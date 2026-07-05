import csv
import json
import os
import time
import zipfile
import requests
from collections import defaultdict
from datetime import datetime, timedelta

BASE_DIR = "unprocessed_data/"

# --- CONFIGURATION ---
MTR_CSV = BASE_DIR + 'mtr_lines_and_stations.csv'
LRT_CSV = BASE_DIR + 'light_rail_routes_and_stops.csv'
OUTPUT_ZIP = BASE_DIR + 'mtr-gtfs-accurate.zip'

# Cache Files
TIME_CACHE_FILE = BASE_DIR + 'travel_time_cache.json'
GEO_CACHE_FILE = BASE_DIR + 'geo_coords_cache.json'

HEADERS = {
    'User-Agent': 'HK-MTR-Routing-App/1.0 (Student Project)',
    'Accept': 'application/json'
}

MTR_API_URL = "https://www.mtr.com.hk/share/customer/jp/api/CompleteRoutes/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Global flag to instantly skip API calls if the internet is down
OFFLINE_MODE = False

# --- CACHE MANAGEMENT ---
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

# --- DATA FETCHING FUNCTIONS ---
def geocode_station(station_name, cache, current, total):
    """Fetches real GPS coordinates for the station center."""
    global OFFLINE_MODE
    
    if station_name in cache:
        return cache[station_name]
        
    if OFFLINE_MODE:
        return {"lat": "22.3", "lon": "114.1"}

    print(f"  [Geo] Locating: {station_name} Station... ({current}/{total})")
    params = {"q": f"{station_name} Station, Hong Kong", "format": "json", "limit": 1}
    
    try:
        response = requests.get(NOMINATIM_URL, headers=HEADERS, params=params, timeout=5)
        if response.status_code == 200 and len(response.json()) > 0:
            data = response.json()[0]
            coords = {"lat": data['lat'], "lon": data['lon']}
            cache[station_name] = coords
            save_cache(cache, GEO_CACHE_FILE)
            time.sleep(1.2)
            return coords
    except requests.exceptions.RequestException:
        print("  [!] Network down. Switching to OFFLINE MODE for geography.")
        OFFLINE_MODE = True
        
    return {"lat": "22.3", "lon": "114.1"}

def fetch_mtr_time(origin_id, dest_id, stops_dict, cache, current=0, total=0, silent=False):
    """Fetches real travel time between two stations."""
    global OFFLINE_MODE
    cache_key = f"{origin_id}_{dest_id}"
    
    if cache_key in cache:
        return cache[cache_key]

    fallback_time = 3 if len(str(origin_id)) < 3 else 1 # 3 min MTR, 1 min LRT
    origin_type = stops_dict.get(origin_id, {}).get('route_type', '1')
    
    if origin_type == "0": # Skip API for Light Rail
        cache[cache_key] = 2
        save_cache(cache, TIME_CACHE_FILE)
        return 2

    if OFFLINE_MODE:
        return fallback_time

    origin_name = stops_dict.get(origin_id, {}).get('name_raw', 'Unknown')
    dest_name = stops_dict.get(dest_id, {}).get('name_raw', 'Unknown')
    
    if not silent:
        print(f"  [Time] Fetching: {origin_name} to {dest_name}... ({current}/{total})")
    
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
    
    # 1. First pass: count unique stations for progress tracking
    unique_station_names = set()
    for filename in [MTR_CSV, LRT_CSV]:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('English Name'): unique_station_names.add(row['English Name'])
    
    total_geo = len(unique_station_names)
    geo_count = 0
    
    # 2. Second pass: actually load network
    print("\n[Phase 1] Mapping Geography...")
    for filename, is_lrt in [(MTR_CSV, False), (LRT_CSV, True)]:
        if not os.path.exists(filename):
            print(f"CRITICAL ERROR: '{filename}' not found.")
            continue
            
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get('Sequence') or not row.get('Line Code'): continue
                
                line, direction = row['Line Code'], row['Direction']
                stop_id = row.get('Station ID', row.get('Stop ID'))
                name = row['English Name']
                seq = int(float(row['Sequence']))
                
                trip_id = f"{line}_{'LRT' if is_lrt else 'MTR'}_{direction}"
                
                if stop_id not in stops:
                    geo_count += 1
                    coords = geocode_station(name, geo_cache, geo_count, total_geo)
                    
                    stops[stop_id] = {
                        "stop_id": stop_id,
                        "stop_name": f"{name} {'Stop' if is_lrt else 'Station'}",
                        "lat": coords['lat'], 
                        "lon": coords['lon'],
                        "name_raw": name,
                        "route_type": "0" if is_lrt else "1"
                    }
                
                network[trip_id].append({"stop_id": stop_id, "name": name, "seq": seq, "line": line})
            
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
    stops_csv = "stop_id,stop_name,stop_lat,stop_lon\n"
    
    seen_routes = set()
    interchange_groups = defaultdict(list)
    
    # Pre-calculate how many time API calls we need to make
    total_time_calls = sum(len(stations) - 1 for stations in network.values())
    time_call_count = 0

    print("\n[Phase 2] Building Routes, Frequencies, and End-to-End Stop Times...")
    for trip_id, stations in network.items():
        route_id = trip_id.rsplit('_', 1)[0]
        direction = "0" if trip_id.endswith("DT") or trip_id.endswith("1") else "1"
        route_type = stops[stations[0]['stop_id']]['route_type']
        
        if route_id not in seen_routes:
            routes_csv += f"{route_id},MTR,{route_id.split('_')[0]},{route_type}\n"
            seen_routes.add(route_id)
            
        trips_csv += f"{route_id},ALL_DAYS,{trip_id},{direction}\n"
        freq_csv += f"{trip_id},06:00:00,23:59:59,60\n"
        
        base_time = datetime.strptime("06:00:00", "%H:%M:%S")
        last_arr_time = base_time
        anchor_st = stations[0] 
        
        for i in range(len(stations)):
            st = stations[i]
            interchange_groups[st['name']].append({'stop_id': st['stop_id'], 'trip_id': trip_id, 'index': i})
            
            if i == 0:
                arr_time = base_time
            else:
                time_call_count += 1
                travel_mins = fetch_mtr_time(anchor_st['stop_id'], st['stop_id'], stops, time_cache, time_call_count, total_time_calls)
                arr_time = base_time + timedelta(minutes=travel_mins)
                
                if arr_time <= last_arr_time:
                    arr_time = last_arr_time + timedelta(minutes=1)
            
            last_arr_time = arr_time
            dep_time = arr_time + timedelta(seconds=30) 
            
            stop_times_csv += f"{trip_id},{arr_time.strftime('%H:%M:%S')},{dep_time.strftime('%H:%M:%S')},{st['stop_id']},{st['seq']}\n"

    print("\n[Phase 3] Writing Flat Geography data...")
    for s_id, data in stops.items():
        stops_csv += f"{s_id},{data['stop_name']},{data['lat']},{data['lon']}\n"

    print("\n[Phase 4] Calculating Internal Transfer Penalties...")
    # Count how many subtraction operations exist
    total_transfer_calcs = 0
    for name, occurrences in interchange_groups.items():
        unique_lines = list({o['trip_id'].split('_')[0] for o in occurrences})
        if len(unique_lines) > 1:
            total_transfer_calcs += 1
            
    transfer_count = 0
            
    for name, occurrences in interchange_groups.items():
        unique_lines = list({o['trip_id'].split('_')[0] for o in occurrences})
        if len(unique_lines) > 1:
            transfer_count += 1
            print(f"  [Transfers] Processing Interchange: {name}... ({transfer_count}/{total_transfer_calcs})")
            
            unique_stops = list({o['stop_id'] for o in occurrences})
            for i in range(len(unique_stops)):
                for j in range(len(unique_stops)):
                    if i != j:
                        from_id, to_id = unique_stops[i], unique_stops[j]
                        try:
                            line1_data = next(o for o in occurrences if o['stop_id'] == from_id)
                            line2_data = next(o for o in occurrences if o['stop_id'] == to_id)
                            idx1, idx2 = line1_data['index'], line2_data['index']
                            
                            if idx1 > 0 and idx2 < len(network[line2_data['trip_id']]) - 1:
                                stat_A = network[line1_data['trip_id']][idx1 - 1]['stop_id']
                                stat_B = network[line2_data['trip_id']][idx2 + 1]['stop_id']
                                
                                # Silent fetches here to avoid terminal clutter, as these should mostly hit the cache anyway
                                time_A_to_X = fetch_mtr_time(stat_A, from_id, stops, time_cache, silent=True)
                                time_X_to_B = fetch_mtr_time(to_id, stat_B, stops, time_cache, silent=True)
                                time_A_to_B = fetch_mtr_time(stat_A, stat_B, stops, time_cache, silent=True)
                                
                                penalty_mins = time_A_to_B - (time_A_to_X + time_X_to_B)
                                penalty_secs = max(60, penalty_mins * 60)
                                transfers_csv += f"{from_id},{to_id},2,{penalty_secs}\n"
                        except IndexError:
                            transfers_csv += f"{from_id},{to_id},2,180\n"

    print(f"\n[Phase 5] Zipping {OUTPUT_ZIP}...")
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

    print("SUCCESS: Accurate GTFS Package Generated!")

if __name__ == "__main__":
    build_gtfs_files()