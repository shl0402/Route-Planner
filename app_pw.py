import requests
import urllib.parse
from flask import Flask, render_template, request, jsonify
from datetime import datetime
from playwright.sync_api import sync_playwright

app = Flask(__name__)

OTP_GRAPHQL_URL = "http://localhost:8080/otp/gtfs/v1"


def geocode_hk_address(address_text, page):
    """Robust geocoder using Playwright JS Fetch to bypass WAF and XML viewers."""
    if not address_text:
        return None

    print(f"Geocoding: {address_text}...")

    # 1. Handle Custom Coordinates bypass (Added this block back)
    if ',' in address_text:
        parts = [p.strip() for p in address_text.split(',')]
        if len(parts) >= 2:
            try:
                lat, lon = float(parts[-2]), float(parts[-1])
                name = parts[0] if len(parts) > 2 else "Custom Coordinates"
                print(f" -> Found via Custom Coordinates: {name}")
                return {"lat": lat, "lon": lon, "name": name}
            except ValueError:
                pass

    # 2. Try OpenStreetMap (Nominatim)
    try:
        nom_url = "https://nominatim.openstreetmap.org/search"
        query_string = urllib.parse.urlencode({"q": f"{address_text}, Hong Kong", "format": "json", "limit": 1})
        full_nom_url = f"{nom_url}?{query_string}"

        nom_data = page.evaluate("""async (url) => {
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json', 'User-Agent': 'HK-Offline-Routing-App/1.0' }
            });
            return await response.json();
        }""", full_nom_url)

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

    # 3. Fallback to Official HK ALS API
    try:
        als_url = "https://www.als.gov.hk/lookup"
        query_string = urllib.parse.urlencode({"q": address_text, "n": 1})
        full_als_url = f"{als_url}?{query_string}"

        data = page.evaluate("""async (url) => {
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json' }
            });
            return await response.json();
        }""", full_als_url)

        if 'SuggestedAddress' in data and len(data['SuggestedAddress']) > 0:
            addr_tree = data['SuggestedAddress'][0]['Address']['PremisesAddress']
            eng_addr = addr_tree.get('EngPremisesAddress', {})

            # THE FIX: Extract geo from addr_tree, not eng_addr
            geo = addr_tree.get('GeospatialInformation', {})

            resolved_name = eng_addr.get('BuildingName') or eng_addr.get('EngStreet', {}).get(
                'StreetName') or address_text

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


# --- FLASK ROUTES ---
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

    # THE FIX: Initialize Playwright once per route request
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--incognito"]
        )
        page = browser.new_page()
        page.goto("about:blank")  # Initialize the JS environment

        # Pass the shared page object to both geocode calls
        from_coords = geocode_hk_address(from_text, page)
        to_coords = geocode_hk_address(to_text, page)

        # Ensure browser closes cleanly
        browser.close()

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
