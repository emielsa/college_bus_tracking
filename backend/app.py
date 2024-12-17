from flask import Flask, request, jsonify
from pymongo import MongoClient
from datetime import datetime
import os
import requests
from flask_caching import Cache  # For caching Google Maps API

app = Flask(__name__)

# Configuration
app.config['CACHE_TYPE'] = 'SimpleCache'  # Or any other cache type
app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # seconds - 5 min for caching

cache = Cache(app)


# MongoDB Configuration
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB = os.environ.get("MONGO_DB")
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

# Google Maps API Key
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")


# Helper Functions
def get_bus_location(bus_number):
    bus = db.buses.find_one({"bus_number": bus_number}, projection={"_id": False})
    return bus

def get_route_stops(route_id):
    route = db.routes.find_one({"route_id": route_id}, projection={"_id": False, "stops": True})
    return route['stops'] if route else None



def find_nearest_stop(user_location, stops):
    nearest_stop = None
    min_distance = float('inf')

    for stop in stops:
        distance, _ = calculate_distance_google_maps(user_location, stop['coordinates'])
        if distance is not None and distance < min_distance:
            min_distance = distance
            nearest_stop = stop
    return nearest_stop, min_distance



@cache.memoize() # Cache this function result with given arguments
def calculate_distance_google_maps(origin, destination):
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
        "origins": f"{origin[1]},{origin[0]}",  # Lat, Long
        "destinations": f"{destination[1]},{destination[0]}",  # Lat, Long
        "key": GOOGLE_MAPS_API_KEY
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()

        if data['status'] == "OK":
            distance = data['rows'][0]['elements'][0].get('distance', {}).get('value', 0) / 1000.0  # km
            duration = data['rows'][0]['elements'][0].get('duration', {}).get('value', 0) / 60.0  # minutes
            return distance, duration
        else:
           print(f"Google Maps API error: {data['status']}")
    except requests.exceptions.RequestException as e:
        print(f"Error during google maps request {e}")
    return None, None


def estimate_arrival_time(bus_location, user_location, stops):
    if not bus_location or not user_location or not stops:
        return None, None
    nearest_stop, _ = find_nearest_stop(user_location, stops)
    if not nearest_stop:
        return None, None

    distance_to_bus, travel_time = calculate_distance_google_maps(
        (bus_location['current_location']['coordinates'][1], bus_location['current_location']['coordinates'][0]),
        (nearest_stop['coordinates'][1], nearest_stop['coordinates'][0])
    )

    if distance_to_bus is None or travel_time is None:
        return None, None

    arrival_time = datetime.now() + datetime.timedelta(minutes=travel_time)

    return arrival_time.strftime("%Y-%m-%d %H:%M:%S"), distance_to_bus


# API Endpoints

# -------------------------Bus Routes-----------------------------#
@app.route('/routes', methods=['POST'])
def create_route():
    data = request.get_json()
    if not data or 'route_id' not in data or 'route_name' not in data or 'stops' not in data:
        return jsonify({'message': 'Missing required fields'}), 400

    route_id = data['route_id']
    route_name = data['route_name']
    stops = data['stops']


    new_route = {
        'route_id': route_id,
        'route_name': route_name,
        'stops': stops
    }
    db.routes.insert_one(new_route)
    return jsonify({'message': 'Route created successfully', 'route_id': route_id}), 201


@app.route('/routes/<string:route_id>', methods=['GET'])
def get_route(route_id):
    route = db.routes.find_one({"route_id": route_id}, projection={"_id": False})
    if route:
        return jsonify(route), 200
    return jsonify({'message': 'Route not found'}), 404


# -----------------------------Arrival Time Calculation------------------------------#
@app.route('/estimate', methods=['GET'])
def get_estimated_arrival():
    bus_number = request.args.get('bus_number')
    user_id = request.args.get('user_id')

    if not bus_number or not user_id:
        return jsonify({'message': 'Missing bus_number or user_id'}), 400

    bus = get_bus_location(bus_number)
    if not bus:
        return jsonify({'message': 'Bus not found'}), 404

    user = db.users.find_one({'user_id': user_id}, projection={"_id": False, "location": True})
    if not user:
        return jsonify({'message': 'User not found'}), 404

    route_stops = get_route_stops(bus['route_id'])
    if not route_stops:
        return jsonify({'message': 'Route not found'}), 404

    estimated_time, distance_to_stop = estimate_arrival_time(bus, user['location'], route_stops)

    if estimated_time is None:
        return jsonify({'message': 'Could not calculate arrival time'}), 500

    return jsonify({
        'estimated_arrival_time': estimated_time,
        'distance_to_stop': distance_to_stop,
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
