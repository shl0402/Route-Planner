#!/usr/bin/env python3
"""
MTR & Light Rail GTFS Builder (Accurate 3D Routing & Exits)
"""

import csv
import json
import os
import time
import zipfile
import requests
import math
from collections import defaultdict
from datetime import datetime, timedelta

# --- CONFIGURATION ---
MTR_CSV = 'mtr_lines_and_stations.csv'
LRT_CSV = 'light_rail_routes_and_stops.csv'
EXITS_JSON_FILE = 'exits.mtr.json'
OUTPUT_ZIP = 'mtr-gtfs-accurate.zip'

# Cache Files
TIME_CACHE_FILE = 'travel_time_cache.json'
GEO_CACHE_FILE = 'geo_coords_cache.json'
EXITS_CACHE_FILE = 'station_exits_cache.json'

HEADERS = {
    'User-Agent': 'HK-MTR-Routing-App/1.0 (Student Project)',
    'Accept': 'application/json'
}

MTR_API_URL = "https://www.mtr.com.hk/share/customer/jp/api/CompleteRoutes/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "http://overpass-api.de/api/interpreter"

# Global flag to instantly skip API calls if the internet is down
OFFLINE_MODE = False


# --- HELPER FUNCTIONS ---
def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on the Earth surface in meters."""
    R = 6371000  # Radius of Earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_cache(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache, filepath):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)


def load_local_exits(filepath):
    """Loads exact exit coordinates from exits.mtr.json."""
    if not os.path.exists(filepath):
        print(f"  [Info] {filepath} not found. Will rely entirely on Overpass API fallback.")
        return {}

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    exits_by_station = defaultdict(list)
    for item in data:
        exits_by_station[item['name_en']].append({
            "id": str(item.get('exit', 'M')),
            "lat": str(item['lat']),
            "lon": str(item['lng']),
            "name_zh": item.get('name_zh', '')
        })
    return exits_by_station


# --- DATA FETCHING FUNCTIONS ---
def geocode_station(station_name, cache):
    """Fetches real GPS coordinates for the station center."""
    global OFFLINE_MODE

    if station_name in cache:
        return cache[station_name]

    if OFFLINE_MODE:
        return {"lat": "22.3", "lon": "114.1"}  # Fallback

    print(f"  [Geo] Locating: {station_name} Station...")
    params = {"q": f"{station_name} Station, Hong Kong", "format": "json", "limit": 1}

    try:
        response = requests.get(NOMINATIM_URL, headers=HEADERS, params=params, timeout=5)
        if response.status_code == 200 and len(response.json()) > 0:
            data = response.json()[0]
            coords = {"lat": data['lat'], "lon": data['lon']}
            cache[station_name] = coords
            save_cache(cache, GEO_CACHE_FILE)
            time.sleep(1.2)  # Polite rate limiting
            return coords
    except requests.exceptions.RequestException:
        print("  [!] Network down. Switching to OFFLINE MODE.")
        OFFLINE_MODE = True

    return {"lat": "22.3", "lon": "114.1"}


def fetch_station_exits(lat, lon, station_id, cache):
    """Fallback: Uses Overpass API to find actual street-level subway entrances."""
    global OFFLINE_MODE

    if station_id in cache:
        return cache[station_id]

    if OFFLINE_MODE or lat == "22.3":
        return []

    print(f"  [Exits] Scanning street level for {station_id} (Overpass API fallback)...")
    query = f"""
    [out:json];
    node(around:250, {lat}, {lon})["railway"="subway_entrance"];
    out body;
    """

    try:
        response = requests.post(OVERPASS_URL, data={'data': query}, timeout=8)
        if response.status_code == 200:
            nodes = response.json().get('elements', [])
            # Map to same structure as exits.mtr.json
            exits = [{"id": str(n['id'])[-4:], "lat": str(n['lat']), "lon": str(n['lon']), "name_zh": ""} for n in
                     nodes]

            cache[station_id] = exits
            save_cache(cache, EXITS_CACHE_FILE)
            time.sleep(1.5)
            return exits
    except requests.exceptions.RequestException:
        OFFLINE_MODE = True

    return []


def fetch_mtr_time(origin_id, dest_id, stops_dict, cache):
    """Fetches real travel time between two stations via MTR API."""
    global OFFLINE_MODE
    cache_key = f"{origin_id}_{dest_id}"

    if cache_key in cache:
        return cache[cache_key]

    fallback_time = 3 if len(str(origin_id)) < 3 else 1  # 3 min MTR, 1 min LRT
    origin_type = stops_dict.get(origin_id, {}).get('route_type', '1')

    if origin_type == "0":  # Skip API for Light Rail
        cache[cache_key] = 2
        save_cache(cache, TIME_CACHE_FILE)
        return 2

    if OFFLINE_MODE:
        return fallback_time

    origin_name = stops_dict.get(origin_id, {}).get('name_raw', 'Unknown')
    dest_name = stops_dict.get(dest_id, {}).get('name_raw', 'Unknown')
    print(f"  [Time] Fetching: {origin_name} to {dest_name}...")

    payload = {
        'lang': 'E', 'oLabel': origin_name, 'oType': 'HRStation', 'oValue': origin_id,
        'dLabel': dest_name, 'dType': 'HRStation', 'dValue': dest_id
    }

    try:
        headers = HEADERS.copy()
        headers['Referer'] = 'https://www.mtr.com.hk/en/customer/jp/index.php'
        response = requests.get(MTR_API_URL, headers=headers, params=payload, timeout=6)

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('errorCode') == '0' and len(data.get('routes', [])) > 0:
                    travel_time = int(data['routes'][0]['time'])
                    cache[cache_key] = travel_time
                    save_cache(cache, TIME_CACHE_FILE)
                    time.sleep(1.2)
                    return travel_time
            except json.JSONDecodeError:
                pass
    except requests.exceptions.RequestException:
        print("  [!] Network error. Switching to OFFLINE MODE for times.")
        OFFLINE_MODE = True

    cache[cache_key] = fallback_time
    save_cache(cache, TIME_CACHE_FILE)
    return fallback_time


# --- NETWORK MAPPING ---
def load_network():
    network = defaultdict(list)
    stops = {}

    geo_cache = load_cache(GEO_CACHE_FILE)
    exits_cache = load_cache(EXITS_CACHE_FILE)
    local_exits_data = load_local_exits(EXITS_JSON_FILE)

    for filename, is_lrt in [(MTR_CSV, False), (LRT_CSV, True)]:
        if not os.path.exists(filename):
            print(f"CRITICAL ERROR: '{filename}' not found.")
            continue

        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get('Sequence') or not row.get('Line Code'): continue

                line = row['Line Code']
                direction = row['Direction']
                stop_id = row.get('Station ID', row.get('Stop ID'))
                name = row['English Name']
                seq = int(float(row['Sequence']))

                # Support varying column names for Chinese
                name_zh_hk = row.get('Traditional Chinese Name', row.get('Chinese Name', ''))
                name_zh_cn = row.get('Simplified Chinese Name', '')

                trip_id = f"{line}_{'LRT' if is_lrt else 'MTR'}_{direction}"

                if stop_id not in stops:
                    coords = geocode_station(name, geo_cache)

                    # 1. Prefer local JSON exits. 2. Fallback to Overpass API
                    if name in local_exits_data:
                        exits = local_exits_data[name]
                    else:
                        exits = fetch_station_exits(coords['lat'], coords['lon'], stop_id, exits_cache)

                    stops[stop_id] = {
                        "stop_id": stop_id,
                        "stop_name": f"{name} {'Stop' if is_lrt else 'Station'}",
                        "lat": coords['lat'],
                        "lon": coords['lon'],
                        "name_raw": name,
                        "name_zh_hk": name_zh_hk,
                        "name_zh_cn": name_zh_cn,
                        "route_type": "0" if is_lrt else "1",
                        "exits": exits
                    }

                network[trip_id].append({"stop_id": stop_id, "name": name, "seq": seq, "line": line})

    # Sort sequences correctly
    for trip in network:
        network[trip].sort(key=lambda x: x['seq'])

    return network, stops


# --- GTFS BUILDER ---
def build_gtfs_files():
    network, stops = load_network()
    time_cache = load_cache(TIME_CACHE_FILE)

    trips_csv = "route_id,service_id,trip_id,direction_id\n"
    routes_csv = "route_id,agency_id,route_short_name,route_type\n"
    stop_times_csv = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    transfers_csv = "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
    freq_csv = "trip_id,start_time,end_time,headway_secs\n"

    # Advanced 3D Stops structure
    stops_csv = "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
    pathways_csv = "pathway_id,from_stop_id,to_stop_id,pathway_mode,is_bidirectional,traversal_time\n"
    translations_csv = "table_name,field_name,language,translation,record_id\n"

    seen_routes = set()
    interchange_groups = defaultdict(list)

    print("\nBuilding Routes, Frequencies, and Stop Times...")
    for trip_id, stations in network.items():
        route_id = trip_id.rsplit('_', 1)[0]
        direction = "0" if trip_id.endswith("DT") or trip_id.endswith("1") else "1"
        route_type = stops[stations[0]['stop_id']]['route_type']

        if route_id not in seen_routes:
            routes_csv += f"{route_id},MTR,{route_id.split('_')[0]},{route_type}\n"
            seen_routes.add(route_id)

        trips_csv += f"{route_id},ALL_DAYS,{trip_id},{direction}\n"
        freq_csv += f"{trip_id},06:00:00,23:59:59,180\n"

        current_time = datetime.strptime("06:00:00", "%H:%M:%S")

        for i in range(len(stations)):
            st = stations[i]
            interchange_groups[st['name']].append({'stop_id': st['stop_id'], 'trip_id': trip_id, 'index': i})

            arr_str = current_time.strftime("%H:%M:%S")
            current_time += timedelta(seconds=30)  # 30 sec dwell time
            dep_str = current_time.strftime("%H:%M:%S")

            stop_times_csv += f"{trip_id},{arr_str},{dep_str},{st['stop_id']},{st['seq']}\n"

            if i < len(stations) - 1:
                next_st = stations[i + 1]
                travel_mins = fetch_mtr_time(st['stop_id'], next_st['stop_id'], stops, time_cache)
                current_time += timedelta(minutes=travel_mins)

    print("\nBuilding 3D Station Hierarchies, Pathways & Translations...")
    for s_id, data in stops.items():
        parent_id = f"ST_{s_id}"

        # 1. Parent Station (Hub)
        stops_csv += f"{parent_id},{data['stop_name']},{data['lat']},{data['lon']},1,\n"

        # 2. Platform (Where the train actually stops)
        stops_csv += f"{s_id},{data['stop_name']} Platform,{data['lat']},{data['lon']},0,{parent_id}\n"

        # Translations for Station and Platform
        if data['name_zh_hk']:
            translations_csv += f"stops,stop_name,zh-TW,{data['name_zh_hk']},{parent_id}\n"
            translations_csv += f"stops,stop_name,zh-TW,{data['name_zh_hk']} 月台,{s_id}\n"
        if data['name_zh_cn']:
            translations_csv += f"stops,stop_name,zh-CN,{data['name_zh_cn']},{parent_id}\n"
            translations_csv += f"stops,stop_name,zh-CN,{data['name_zh_cn']} 月台,{s_id}\n"

        # 3. Street Exits & Internal Walking Pathways
        for exit_node in data['exits']:
            ext_id = f"EXT_{s_id}_{exit_node['id']}"
            ext_name = f"{data['stop_name']} Exit {exit_node['id']}"

            stops_csv += f"{ext_id},{ext_name},{exit_node['lat']},{exit_node['lon']},2,{parent_id}\n"

            # Translation for Exit
            if exit_node.get('name_zh'):
                translations_csv += f"stops,stop_name,zh-TW,{exit_node['name_zh']} 出口 {exit_node['id']},{ext_id}\n"
                translations_csv += f"stops,stop_name,zh-CN,{exit_node['name_zh']} 出口 {exit_node['id']},{ext_id}\n"

            # Calculate precise walking time based on distance (Average walking speed 1.2 m/s)
            try:
                dist_meters = haversine(float(data['lat']), float(data['lon']), float(exit_node['lat']),
                                        float(exit_node['lon']))
                walk_time_secs = max(60, int(dist_meters / 1.2))  # Minimum 1 minute to exit
            except:
                walk_time_secs = 180  # Fallback 3 mins

            pathways_csv += f"pw_{ext_id},{ext_id},{s_id},1,1,{walk_time_secs}\n"

    print("Calculating Internal Transfer Penalties...")
    for name, occurrences in interchange_groups.items():
        unique_lines = list({o['trip_id'].split('_')[0] for o in occurrences})
        if len(unique_lines) > 1:
            unique_stops = list({o['stop_id'] for o in occurrences})
            for i in range(len(unique_stops)):
                for j in range(len(unique_stops)):
                    if i != j:
                        from_id, to_id = unique_stops[i], unique_stops[j]
                        try:
                            # Subtraction Trick Logic
                            line1_data = next(o for o in occurrences if o['stop_id'] == from_id)
                            line2_data = next(o for o in occurrences if o['stop_id'] == to_id)
                            idx1, idx2 = line1_data['index'], line2_data['index']

                            if idx1 > 0 and idx2 < len(network[line2_data['trip_id']]) - 1:
                                stat_A = network[line1_data['trip_id']][idx1 - 1]['stop_id']
                                stat_B = network[line2_data['trip_id']][idx2 + 1]['stop_id']

                                time_A_to_X = fetch_mtr_time(stat_A, from_id, stops, time_cache)
                                time_X_to_B = fetch_mtr_time(to_id, stat_B, stops, time_cache)
                                time_A_to_B = fetch_mtr_time(stat_A, stat_B, stops, time_cache)

                                penalty_mins = time_A_to_B - (time_A_to_X + time_X_to_B)
                                penalty_secs = max(60, penalty_mins * 60)
                                transfers_csv += f"{from_id},{to_id},2,{penalty_secs}\n"
                        except IndexError:
                            transfers_csv += f"{from_id},{to_id},2,180\n"

    print(f"\nZipping {OUTPUT_ZIP}...")
    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zf:
        agency_csv = "agency_id,agency_name,agency_url,agency_timezone\nMTR,MTR Corporation,http://www.mtr.com.hk,Asia/Hong_Kong\n"
        calendar_csv = "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nALL_DAYS,1,1,1,1,1,1,1,20240101,20301231\n"

        zf.writestr('agency.txt', agency_csv)
        zf.writestr('calendar.txt', calendar_csv)
        zf.writestr('stops.txt', stops_csv)
        zf.writestr('routes.txt', routes_csv)
        zf.writestr('trips.txt', trips_csv)
        zf.writestr('stop_times.txt', stop_times_csv)
        zf.writestr('frequencies.txt', freq_csv)
        zf.writestr('transfers.txt', transfers_csv)
        zf.writestr('pathways.txt', pathways_csv)
        zf.writestr('translations.txt', translations_csv)

    print("SUCCESS: Accurate 3D GTFS Package with Translations Generated!")


if __name__ == "__main__":
    build_gtfs_files()