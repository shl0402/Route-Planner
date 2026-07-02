import csv
import json
import os
import time
import zipfile
import requests
from collections import defaultdict
from datetime import datetime, timedelta

MTR_CSV = 'mtr_lines_and_stations.csv'
LRT_CSV = 'light_rail_routes_and_stops.csv'
OUTPUT_ZIP = 'mtr-gtfs-accurate.zip'
CACHE_FILE = 'travel_time_cache.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': 'https://www.mtr.com.hk/en/customer/jp/index.php'
}

# The true backend endpoint you discovered
MTR_API_URL = "https://www.mtr.com.hk/share/customer/jp/api/CompleteRoutes/" 

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)

def fetch_mtr_time(origin_id, dest_id, stops_dict, cache):
    cache_key = f"{origin_id}_{dest_id}"
    
    if cache_key in cache:
        return cache[cache_key]

    # Look up names for the required API parameters
    origin_name = stops_dict.get(origin_id, {}).get('name_raw', 'Unknown')
    dest_name = stops_dict.get(dest_id, {}).get('name_raw', 'Unknown')

    print(f"    -> Fetching API: {origin_name} to {dest_name}...")
    
    payload = {
        'lang': 'E',
        'oLabel': origin_name,
        'oType': 'HRStation',
        'oValue': origin_id,
        'dLabel': dest_name,
        'dType': 'HRStation',
        'dValue': dest_id
    }
    
    try:
        response = requests.get(MTR_API_URL, headers=HEADERS, params=payload, timeout=10)
        
        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('errorCode') == '0' and len(data.get('routes', [])) > 0:
                    travel_time = int(data['routes'][0]['time'])
                    
                    cache[cache_key] = travel_time
                    save_cache(cache)
                    
                    time.sleep(1.2) # Polite delay
                    return travel_time
            except requests.exceptions.JSONDecodeError:
                print(f"       [!] Server returned HTML instead of JSON. Check endpoint.")
                time.sleep(2)
                return 3
        else:
            print(f"       [!] Server returned HTTP Status {response.status_code}")
            time.sleep(2)
            
    except Exception as e:
        print(f"       [!] Network Error: {e}")
    
    # Fallback if API fails
    return 3 if len(str(origin_id)) < 3 else 1 

def load_network():
    network = defaultdict(list)
    stops = {}
    
    for filename, is_lrt in [(MTR_CSV, False), (LRT_CSV, True)]:
        if not os.path.exists(filename):
            print(f"CRITICAL ERROR: Cannot find '{filename}'. Ensure it is in the exact same folder as the script.")
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
                
                route_id = f"{line}_{'LRT' if is_lrt else 'MTR'}"
                trip_id = f"{route_id}_{direction}"
                
                if stop_id not in stops:
                    stops[stop_id] = {
                        "stop_id": stop_id,
                        "stop_name": f"{name} {'Stop' if is_lrt else 'Station'}",
                        "stop_lat": "22.3", 
                        "stop_lon": "114.1",
                        "name_raw": name,
                        "route_type": "0" if is_lrt else "1"
                    }
                
                network[trip_id].append({
                    "stop_id": stop_id,
                    "name": name,
                    "seq": seq,
                    "line": line
                })
            
    for trip in network:
        network[trip].sort(key=lambda x: x['seq'])
        
    return network, stops

def build_gtfs_files():
    network, stops = load_network()
    cache = load_cache()
    
    print(f"Loaded {len(cache)} pre-calculated routes from cache.")
    
    trips_csv = "route_id,service_id,trip_id,direction_id\n"
    routes_csv = "route_id,agency_id,route_short_name,route_type\n"
    stop_times_csv = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    transfers_csv = "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
    
    seen_routes = set()
    interchange_groups = defaultdict(list)

    print("\nBuilding Routes and Stop Times...")
    for trip_id, stations in network.items():
        route_id = trip_id.rsplit('_', 1)[0]
        direction = "0" if trip_id.endswith("DT") or trip_id.endswith("1") else "1"
        route_type = stops[stations[0]['stop_id']]['route_type']
        
        if route_id not in seen_routes:
            routes_csv += f"{route_id},MTR,{route_id.split('_')[0]},{route_type}\n"
            seen_routes.add(route_id)
            
        trips_csv += f"{route_id},ALL_DAYS,{trip_id},{direction}\n"
        current_time = datetime.strptime("06:00:00", "%H:%M:%S")
        
        for i in range(len(stations)):
            st = stations[i]
            interchange_groups[st['name']].append({'stop_id': st['stop_id'], 'trip_id': trip_id, 'index': i})
            
            arr_str = current_time.strftime("%H:%M:%S")
            current_time += timedelta(seconds=30) 
            dep_str = current_time.strftime("%H:%M:%S")
            
            stop_times_csv += f"{trip_id},{arr_str},{dep_str},{st['stop_id']},{st['seq']}\n"
            
            if i < len(stations) - 1:
                next_st = stations[i+1]
                travel_mins = fetch_mtr_time(st['stop_id'], next_st['stop_id'], stops, cache)
                current_time += timedelta(minutes=travel_mins)

    print("\nCalculating Transfer Penalties...")
    for name, occurrences in interchange_groups.items():
        unique_lines = list({o['trip_id'].split('_')[0] for o in occurrences})
        
        if len(unique_lines) > 1:
            unique_stops = list({o['stop_id'] for o in occurrences})
            
            for i in range(len(unique_stops)):
                for j in range(len(unique_stops)):
                    if i != j:
                        from_id = unique_stops[i]
                        to_id = unique_stops[j]
                        
                        try:
                            line1_data = next(o for o in occurrences if o['stop_id'] == from_id)
                            line2_data = next(o for o in occurrences if o['stop_id'] == to_id)
                            
                            idx1 = line1_data['index']
                            idx2 = line2_data['index']
                            
                            if idx1 > 0 and idx2 < len(network[line2_data['trip_id']]) - 1:
                                stat_A = network[line1_data['trip_id']][idx1 - 1]['stop_id']
                                stat_B = network[line2_data['trip_id']][idx2 + 1]['stop_id']
                                
                                time_A_to_X = fetch_mtr_time(stat_A, from_id, stops, cache)
                                time_X_to_B = fetch_mtr_time(to_id, stat_B, stops, cache)
                                time_A_to_B = fetch_mtr_time(stat_A, stat_B, stops, cache)
                                
                                penalty_mins = time_A_to_B - (time_A_to_X + time_X_to_B)
                                penalty_secs = max(60, penalty_mins * 60)
                                transfers_csv += f"{from_id},{to_id},2,{penalty_secs}\n"
                        except IndexError:
                            transfers_csv += f"{from_id},{to_id},2,180\n"

    print("\nZipping output files...")
    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zf:
        stops_csv = "stop_id,stop_name,stop_lat,stop_lon\n"
        for s in stops.values():
            stops_csv += f"{s['stop_id']},{s['stop_name']},{s['stop_lat']},{s['stop_lon']}\n"
            
        # ADD THESE TWO LINES:
        agency_csv = "agency_id,agency_name,agency_url,agency_timezone\nMTR,MTR Corporation,http://www.mtr.com.hk,Asia/Hong_Kong\n"
        zf.writestr('agency.txt', agency_csv)
        
        zf.writestr('stops.txt', stops_csv)
        zf.writestr('routes.txt', routes_csv)
        zf.writestr('trips.txt', trips_csv)
        zf.writestr('stop_times.txt', stop_times_csv)
        zf.writestr('transfers.txt', transfers_csv)
        zf.writestr('calendar.txt', "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nALL_DAYS,1,1,1,1,1,1,1,20240101,20301231\n")

    print(f"SUCCESS: {OUTPUT_ZIP} generated.")

if __name__ == "__main__":
    build_gtfs_files()