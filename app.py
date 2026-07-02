import requests
from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

OTP_GRAPHQL_URL = "http://localhost:8080/otp/gtfs/v1"

def geocode_hk_address(address_text):
    """Robust geocoder: Extracts the exact matched name for transparency."""
    if not address_text:
        return None
        
    print(f"Geocoding: {address_text}...")
    
    # 1. Try OpenStreetMap (Nominatim) first
    try:
        nom_url = "https://nominatim.openstreetmap.org/search"
        nom_params = {"q": f"{address_text}, Hong Kong", "format": "json", "limit": 1}
        nom_headers = {"User-Agent": "HK-Offline-Routing-App/1.0"}
        nom_resp = requests.get(nom_url, headers=nom_headers, params=nom_params, timeout=5)
        
        if nom_resp.status_code == 200:
            nom_data = nom_resp.json()
            if len(nom_data) > 0:
                resolved_name = nom_data[0].get('name', address_text)
                print(f" -> Found via OpenStreetMap: {resolved_name}")
                return {
                    "lat": float(nom_data[0]['lat']),
                    "lon": float(nom_data[0]['lon']),
                    "name": resolved_name
                }
    except Exception as e:
        print(f"OSM lookup note: {e}")

    # 2. Fallback to Official HK ALS API
    try:
        als_url = "https://www.als.gov.hk/lookup"
        headers = {"Accept": "application/json"}
        params = {"q": address_text, "n": 1}
        
        response = requests.get(als_url, headers=headers, params=params, timeout=5)
        
        if response.status_code == 200:
            if response.text.strip().startswith('{') or response.text.strip().startswith('['):
                data = response.json()
                if 'SuggestedAddress' in data and len(data['SuggestedAddress']) > 0:
                    addr_tree = data['SuggestedAddress'][0]['Address']['PremisesAddress']
                    eng_addr = addr_tree.get('EngPremisesAddress', {})
                    geo = eng_addr.get('GeospatialInformation', {})
                    
                    resolved_name = eng_addr.get('BuildingName') or eng_addr.get('EngStreet', {}).get('StreetName') or address_text
                    
                    if 'Latitude' in geo and 'Longitude' in geo:
                        print(f" -> Found via HK ALS API: {resolved_name}")
                        return {
                            "lat": float(geo['Latitude']),
                            "lon": float(geo['Longitude']),
                            "name": resolved_name
                        }
    except Exception as e:
        print(f"ALS API Error for {address_text}: {e}")

    return None

# --- THIS IS THE ROUTE THAT WENT MISSING ---
@app.route('/')
def index():
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.now().strftime("%H:%M")
    return render_template('index.html', default_date=current_date, default_time=current_time)

@app.route('/get-route', methods=['POST'])
def get_route():
    req_data = request.json
    from_text = req_data.get('from_location')
    to_text = req_data.get('to_location')
    selected_modes = req_data.get('modes', ['WALK', 'TRANSIT'])
    date_str = req_data.get('date')
    time_str = req_data.get('time')

    from_coords = geocode_hk_address(from_text)
    to_coords = geocode_hk_address(to_text)

    if not from_coords or not to_coords:
        return jsonify({"error": "Could not resolve addresses to valid coordinates in Hong Kong."}), 400

    print("\n--- Routing Request Resolved ---")
    print(f"From Input: '{from_text}' -> Matched: {from_coords['name']} ({from_coords['lat']}, {from_coords['lon']})")
    print(f"To Input:   '{to_text}' -> Matched: {to_coords['name']} ({to_coords['lat']}, {to_coords['lon']})")
    print("--------------------------------\n")

    modes_graphql = ", ".join([f"{{mode: {m}}}" for m in selected_modes])

    graphql_query = f"""
    {{
      plan(
        from: {{lat: {from_coords['lat']}, lon: {from_coords['lon']}}}
        to: {{lat: {to_coords['lat']}, lon: {to_coords['lon']}}}
        date: "{date_str}"
        time: "{time_str}"
        transportModes: [{modes_graphql}]
        numItineraries: 15
      ) {{
        itineraries {{
          duration
          walkTime
          legs {{
            mode
            duration
            startTime
            endTime
            from {{ name lat lon }}
            to {{ name lat lon }}
            route {{ shortName }}
            legGeometry {{
              points
            }}
          }}
        }}
      }}
    }}
    """

    try:
        otp_response = requests.post(OTP_GRAPHQL_URL, json={'query': graphql_query}, timeout=50)
        otp_data = otp_response.json()
        
        if 'errors' in otp_data:
            return jsonify({"error": otp_data['errors'][0]['message']}), 500
            
        raw_itineraries = otp_data.get('data', {}).get('plan', {}).get('itineraries', [])
        
        processed_itineraries = []
        for itin in raw_itineraries:
            duration = itin.get('duration', 0)
            walk_time = itin.get('walkTime', 0)
            itin['transitTime'] = max(0, duration - walk_time)
            processed_itineraries.append(itin)

        return jsonify({
            "from": from_coords,
            "to": to_coords,
            "itineraries": processed_itineraries
        })
    except Exception as e:
        return jsonify({"error": f"Failed to connect to local OTP server: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)