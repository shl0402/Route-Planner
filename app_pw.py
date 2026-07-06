import requests
import urllib.parse
from flask import Flask, render_template, request, jsonify
from datetime import datetime
from playwright.sync_api import sync_playwright
import json

app = Flask(__name__)

OTP_GRAPHQL_URL = "http://localhost:8080/otp/gtfs/v1"


def geocode_hk_address(address_text, page):
    """Robust geocoder using Playwright JS Fetch to bypass WAF and XML viewers."""
    if not address_text:
        print("DEBUG: address_text is empty!")
        return None

    print(f"\n--- Geocoding: '{address_text}' ---")

    # 1. Handle Custom Coordinates bypass
    if ',' in address_text:
        parts = [p.strip() for p in address_text.split(',')]
        if len(parts) >= 2:
            try:
                lat, lon = float(parts[-2]), float(parts[-1])
                name = parts[0] if len(parts) > 2 else "Custom Coordinates"
                print(f" [SUCCESS] Found via Custom Coordinates: {name}")
                return {"lat": lat, "lon": lon, "name": name, "score": "Exact (Coords)"}
            except ValueError:
                print(" DEBUG: Comma found, but could not parse as float coordinates.")

    # 2. Try OpenStreetMap (Nominatim)
    print(" [1/2] Trying OpenStreetMap (Nominatim)...")
    try:
        nom_url = "https://nominatim.openstreetmap.org/search"
        query_string = urllib.parse.urlencode({
            "q": address_text,
            "format": "json",
            "limit": 1,
            "countrycodes": "hk"
        })
        full_nom_url = f"{nom_url}?{query_string}"

        nom_data = page.evaluate("""async (url) => {
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json', 'User-Agent': 'HK-Offline-Routing-App/1.0' }
            });
            return await response.json();
        }""", full_nom_url)

        print(f"  -> Raw OSM Response: {json.dumps(nom_data, ensure_ascii=False)[:200]}...")

        if len(nom_data) > 0:
            resolved_name = nom_data[0].get('name', '')
            is_generic_match = (resolved_name == 'Hong Kong' and address_text.lower() != 'hong kong')

            if not is_generic_match:
                print(f" [SUCCESS] Found via OpenStreetMap: {resolved_name}")
                return {
                    "lat": float(nom_data[0]['lat']),
                    "lon": float(nom_data[0]['lon']),
                    "name": resolved_name or address_text,
                    "score": "OSM"
                }
            else:
                print("  -> OSM returned generic 'Hong Kong' match. Rejecting and moving to ALS.")
        else:
            print("  -> OSM returned empty array []. No match found.")
    except Exception as e:
        print(f"  [ERROR] OSM lookup failed: {e}")

    # 3. Fallback to Official HK ALS API
    print(" [2/2] Trying Official HK ALS API...")
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

        # Print the first 400 characters of the raw JSON to see what we are actually getting
        print(f"  -> Raw ALS Response: {json.dumps(data, ensure_ascii=False)[:400]}...")

        if 'SuggestedAddress' in data and len(data['SuggestedAddress']) > 0:
            suggested = data['SuggestedAddress'][0]
            score = suggested.get('ValidationInformation', {}).get('Score', 0)
            print(f"  -> ALS Match Found! Score is {score}/100")

            addr_tree = suggested.get('Address', {}).get('PremisesAddress', {})

            # THE FIX: Extract BOTH English and Chinese address trees
            eng_addr = addr_tree.get('EngPremisesAddress', {})
            chi_addr = addr_tree.get('ChiPremisesAddress', {})

            # 1. Try to find the most descriptive name in English
            eng_name = eng_addr.get('BuildingName') or \
                       eng_addr.get('EngEstate', {}).get('EstateName') or \
                       eng_addr.get('EngVillage', {}).get('VillageName') or \
                       eng_addr.get('EngStreet', {}).get('StreetName')

            # 2. If English is missing, try Chinese
            chi_name = chi_addr.get('BuildingName') or \
                       chi_addr.get('ChiEstate', {}).get('EstateName') or \
                       chi_addr.get('ChiVillage', {}).get('VillageName') or \
                       chi_addr.get('ChiStreet', {}).get('StreetName')

            # 3. Grab the District/Location to provide geographic context
            eng_dist = eng_addr.get('EngDistrict', {}).get('DcDistrict') or eng_addr.get('EngStreet', {}).get(
                'LocationName')
            chi_dist = chi_addr.get('ChiDistrict', {}).get('DcDistrict') or chi_addr.get('ChiStreet', {}).get(
                'LocationName')

            # Assemble the final resolved string
            base_name = eng_name or chi_name or "Unnamed Street/Lot"
            district = eng_dist or chi_dist or ""

            resolved_name = f"{base_name}, {district}" if district else base_name

            geo = addr_tree.get('GeospatialInformation', {})

            if 'Latitude' in geo and 'Longitude' in geo:
                print(f" [SUCCESS] Found via HK ALS API: {resolved_name} (Score: {score})")
                return {
                    "lat": float(geo['Latitude']),
                    "lon": float(geo['Longitude']),
                    "name": resolved_name,
                    "score": f"{score}/100"
                }
            else:
                print(f"  -> FAIL: Missing 'Latitude' or 'Longitude' in ALS. Keys: {list(geo.keys())}")

    except Exception as e:
        print(f"  [ERROR] ALS API Error: {e}")

    print(f" [FAIL] All geocoding methods exhausted for '{address_text}'. Returning None.\n")
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
            headless=True,
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

        # ADD THESE TWO LINES: Attach the original user input to the payload
    from_coords['originalInput'] = from_text
    to_coords['originalInput'] = to_text

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
