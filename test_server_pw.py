import asyncio
import aiohttp
import numpy as np
import json
import datetime
import urllib.parse
from scipy.spatial import cKDTree
from scipy.spatial.distance import pdist, squareform
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import os
from playwright.sync_api import sync_playwright

app = Flask(__name__)
CORS(app)

OTP_GRAPHQL_URL = "http://localhost:8080/otp/gtfs/v1"


def geocode_hk_address(address_text, page):
    """
    Robust geocoder using an existing Playwright JS Fetch environment.
    """
    if not address_text:
        return None

    print(f"\n[+] Starting geocoding for: '{address_text}'")

    # 1. Handle Custom Coordinates bypass
    if ',' in address_text:
        parts = [p.strip() for p in address_text.split(',')]
        if len(parts) >= 2:
            try:
                lat, lon = float(parts[-2]), float(parts[-1])
                name = parts[0] if len(parts) > 2 else "Custom Coordinates"
                return {"lat": lat, "lon": lon, "name": name, "score": "Exact (Coords)"}
            except ValueError:
                pass

    # 2. Try OpenStreetMap (Nominatim) using Playwright JS Fetch
    try:
        nom_url = "https://nominatim.openstreetmap.org/search"
        # THE FIX: Use countrycodes instead of appending ", Hong Kong"
        query_string = urllib.parse.urlencode({
            "q": address_text,
            "format": "json",
            "limit": 1,
            "countrycodes": "hk"
        })
        full_nom_url = f"{nom_url}?{query_string}"

        nom_data = page.evaluate("""async (url) => {
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json', 'User-Agent': 'VRP-Offline-Routing-App/1.0' }
            });
            return await response.json();
        }""", full_nom_url)

        if len(nom_data) > 0:
            resolved_name = nom_data[0].get('name', '')
            is_generic_match = (resolved_name == 'Hong Kong' and address_text.lower() != 'hong kong')

            if not is_generic_match:
                return {
                    "lat": float(nom_data[0]['lat']),
                    "lon": float(nom_data[0]['lon']),
                    "name": resolved_name or address_text,
                    "score": "OSM"
                }
    except Exception as e:
        pass

    # 3. Fallback to Official HK ALS API using Playwright JS Fetch
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
            suggested = data['SuggestedAddress'][0]
            score = suggested.get('ValidationInformation', {}).get('Score', 0)

            addr_tree = suggested.get('Address', {}).get('PremisesAddress', {})

            # THE FIX: Extract BOTH English and Chinese address trees
            eng_addr = addr_tree.get('EngPremisesAddress', {})
            chi_addr = addr_tree.get('ChiPremisesAddress', {})

            eng_name = eng_addr.get('BuildingName') or eng_addr.get('EngEstate', {}).get('EstateName') or eng_addr.get(
                'EngVillage', {}).get('VillageName') or eng_addr.get('EngStreet', {}).get('StreetName')
            chi_name = chi_addr.get('BuildingName') or chi_addr.get('ChiEstate', {}).get('EstateName') or chi_addr.get(
                'ChiVillage', {}).get('VillageName') or chi_addr.get('ChiStreet', {}).get('StreetName')

            eng_dist = eng_addr.get('EngDistrict', {}).get('DcDistrict') or eng_addr.get('EngStreet', {}).get(
                'LocationName')
            chi_dist = chi_addr.get('ChiDistrict', {}).get('DcDistrict') or chi_addr.get('ChiStreet', {}).get(
                'LocationName')

            base_name = eng_name or chi_name or "Unnamed Location"
            district = eng_dist or chi_dist or ""
            resolved_name = f"{base_name}, {district}" if district else base_name

            geo = addr_tree.get('GeospatialInformation', {})

            if 'Latitude' in geo and 'Longitude' in geo:
                return {
                    "lat": float(geo['Latitude']),
                    "lon": float(geo['Longitude']),
                    "name": resolved_name,
                    "score": f"{score}/100"
                }
    except Exception as e:
        pass

    return None


@app.route('/geocode', methods=['POST'])
def geocode_batch():
    data = request.json
    addresses = data.get('addresses', [])
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--incognito"])
        page = browser.new_page()
        page.goto("about:blank")

        for idx, addr in enumerate(addresses):
            coords = geocode_hk_address(addr, page)
            if coords:
                results.append({
                    "originalAddress": addr, "status": "ok",
                    "lat": str(coords['lat']), "lon": str(coords['lon']), "name": coords['name'],
                    "score": coords.get('score', 'N/A')  # Pass score to frontend
                })
            else:
                results.append({
                    "originalAddress": addr, "status": "error",
                    "lat": "", "lon": "", "name": "", "score": "0/100"
                })

        browser.close()

    return jsonify({"results": results})

@app.route('/')
def index():
    return jsonify({
        "status": "online",
        "message": "VRP Routing API is running.",
        "available_endpoints": ["POST /geocode", "POST /build-matrix", "POST /optimize"]
    })


async def fetch_otp_route(session, from_node, to_node, params):
    date_str = params.get('date', datetime.datetime.now().strftime("%Y-%m-%d"))
    time_str = params.get('time', "08:00:00")

    graphql_query = f"""
    {{
      plan(
        from: {{lat: {from_node['lat']}, lon: {from_node['lon']}}}
        to: {{lat: {to_node['lat']}, lon: {to_node['lon']}}}
        date: "{date_str}"
        time: "{time_str}"
        transportModes: [{{mode: WALK}}, {{mode: TRANSIT}}]
        searchWindow: 3600
        numItineraries: 15
      ) {{
        itineraries {{
          duration
          legs {{
            mode
            startTime
            endTime
          }}
        }}
      }}
    }}
    """
    try:
        # OTP is local, so this uses the standard aiohttp request safely bypassing the proxy
        async with session.post(OTP_GRAPHQL_URL, json={'query': graphql_query}, timeout=15) as response:
            if response.status == 200:
                otp_data = await response.json()

                if 'errors' in otp_data:
                    return {"from_id": from_node['id'], "to_id": to_node['id'], "time_seconds": 999999}

                itineraries = otp_data.get('data', {}).get('plan', {}).get('itineraries', [])
                if not itineraries:
                    return {"from_id": from_node['id'], "to_id": to_node['id'], "time_seconds": 999999}

                valid_options = []
                for itin in itineraries:
                    duration = itin.get('duration', 0)
                    legs = itin.get('legs', [])
                    initial_wait_time = 0

                    for idx, leg in enumerate(legs):
                        if leg.get('mode') != 'WALK':
                            if idx > 0 and legs[idx - 1].get('mode') == 'WALK':
                                arrival_at_stop = legs[idx - 1].get('endTime')
                                transit_departure = leg.get('startTime')
                                wait_ms = transit_departure - arrival_at_stop
                                if wait_ms > 0:
                                    initial_wait_time = int(wait_ms / 1000)
                            break

                    adjusted_duration = max(0, duration - initial_wait_time)
                    valid_options.append(adjusted_duration)

                best_time = min(valid_options) if valid_options else 999999
                return {"from_id": from_node['id'], "to_id": to_node['id'], "time_seconds": best_time}
    except Exception as e:
        pass

    return {"from_id": from_node['id'], "to_id": to_node['id'], "time_seconds": 999999}


async def process_matrix_chunk(locations, edge_chunk, params):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_otp_route(session, locations[i], locations[j], params) for i, j in edge_chunk]
        return await asyncio.gather(*tasks)


@app.route('/build-matrix', methods=['POST'])
def build_matrix():
    data = request.json
    locations = data.get('locations', [])
    params = data.get('params', {})

    if len(locations) < 3:
        return jsonify({"error": "Not enough locations"}), 400

    def generate_stream():
        yield json.dumps(
            {"type": "progress", "percent": 5, "message": "Initializing spatial index and KDTree..."}) + "\n"

        edges = set()

        # Node 0 is Start, Node 1 is End. Jobs start at index 2.
        job_nodes = [i for i, loc in enumerate(locations) if loc.get('status') != 'dummy' and i >= 2]

        # Ensure Real Start connects to all jobs
        if locations[0].get('status') != 'dummy':
            for j in job_nodes:
                edges.add(tuple(sorted((0, j))))

        # Ensure all jobs connect to Real End
        if locations[1].get('status') != 'dummy':
            for j in job_nodes:
                edges.add(tuple(sorted((j, 1))))

        # Unused vehicle failsafe: Start to End
        if locations[0].get('status') != 'dummy' and locations[1].get('status') != 'dummy':
            edges.add((0, 1))

        # Spatial connectivity for jobs
        if len(job_nodes) >= 2:
            coords = np.array([[float(locations[i]['lat']), float(locations[i]['lon'])] for i in job_nodes])
            k_neighbors = min(8, len(coords))
            tree = cKDTree(coords)
            _, indices = tree.query(coords, k=k_neighbors)

            for i, row in enumerate(indices):
                for j in row[1:]:
                    edges.add(tuple(sorted((job_nodes[i], job_nodes[int(j)]))))

            dense_dist = squareform(pdist(coords))
            mst = minimum_spanning_tree(csr_matrix(dense_dist))
            mst_coo = mst.tocoo()

            for i, j in zip(mst_coo.row, mst_coo.col):
                edges.add(tuple(sorted((job_nodes[int(i)], job_nodes[int(j)]))))

        edge_list = list(edges)
        total_edges = len(edge_list)

        yield json.dumps({"type": "progress", "percent": 15,
                          "message": f"Graph built. Querying {total_edges} routes from OTP..."}) + "\n"

        chunk_size = 10
        otp_matrix_results = []

        for i in range(0, total_edges, chunk_size):
            chunk = edge_list[i:i + chunk_size]
            chunk_results = asyncio.run(process_matrix_chunk(locations, chunk, params))
            otp_matrix_results.extend(chunk_results)

            completed = min(i + chunk_size, total_edges)
            percent = 15 + int(80 * (completed / total_edges))
            yield json.dumps({"type": "progress", "percent": percent,
                              "message": f"Processing OTP Routes: {completed} / {total_edges}"}) + "\n"

        formatted_edges = [{"source": res['from_id'], "target": res['to_id'], "time_seconds": res['time_seconds']} for
                           res in otp_matrix_results]

        yield json.dumps({
            "type": "complete",
            "status": "success",
            "total_locations": len(locations),
            "total_edges_calculated": total_edges,
            "matrix": otp_matrix_results,
            "edges": formatted_edges
        }) + "\n"

    return Response(stream_with_context(generate_stream()), mimetype='application/x-ndjson')


from math import radians, sin, cos, sqrt, atan2


@app.route('/optimize', methods=['POST'])
def optimize_routes():
    data = request.json
    locations = data.get('locations', [])
    matrix = data.get('matrix', [])
    params = data.get('params', {})

    daily_hours = int(params.get('dailyHours', 8))
    stay_time = int(params.get('stayTimeMins', 15)) * 60
    max_time_seconds = daily_hours * 3600

    lunch_break = params.get('lunchBreak', False)
    lunch_duration = int(params.get('lunchDuration', 45)) * 60
    lunch_start_min = 3 * 3600
    lunch_start_max = 5 * 3600

    if len(locations) < 3:
        return jsonify({"error": "Not enough locations to route."}), 400

    sparse_matrix = {}
    for edge in matrix:
        u, v = edge['from_id'], edge['to_id']
        cost = edge['time_seconds'] + stay_time
        if u not in sparse_matrix: sparse_matrix[u] = {}
        if v not in sparse_matrix: sparse_matrix[v] = {}
        sparse_matrix[u][v] = cost
        sparse_matrix[v][u] = cost

    # Calculate max possible vehicles (1 worker per valid job location)
    job_count = len([loc for loc in locations if loc.get('status') != 'dummy']) - 2
    num_vehicles = max(1, job_count)

    starts = [0] * num_vehicles
    ends = [1] * num_vehicles
    manager = pywrapcp.RoutingIndexManager(len(locations), num_vehicles, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def get_haversine_time(node1_idx, node2_idx):
        n1 = locations[node1_idx]
        n2 = locations[node2_idx]
        if n1.get('status') == 'dummy' or n2.get('status') == 'dummy': return 0
        try:
            lat1, lon1 = float(n1['lat']), float(n1['lon'])
            lat2, lon2 = float(n2['lat']), float(n2['lon'])
            R = 6371000
            phi1, phi2 = radians(lat1), radians(lat2)
            d_phi = radians(lat2 - lat1)
            d_lon = radians(lon2 - lon1)
            a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lon / 2) ** 2
            c = 2 * atan2(sqrt(a), sqrt(1 - a))
            return int((R * c) / 4.0) + 1800 + stay_time
        except:
            return 14400

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)

        if from_node == to_node: return 0

        # Zero cost for injected Dummy Nodes
        if from_node == 0 and locations[0].get('status') == 'dummy': return 0
        if to_node == 1 and locations[1].get('status') == 'dummy': return 0

        # Unused vehicle bypassing
        if from_node == 0 and to_node == 1:
            if locations[0].get('status') == 'dummy' or locations[1].get('status') == 'dummy':
                return 0

        if from_node in sparse_matrix and to_node in sparse_matrix[from_node]:
            if sparse_matrix[from_node][to_node] >= 999999:
                return get_haversine_time(from_node, to_node) + 3600
            return sparse_matrix[from_node][to_node]

        return get_haversine_time(from_node, to_node) + 5400

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    routing.AddDimension(
        transit_callback_index, 0, max_time_seconds, True, "Time"
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Disjunctions: Dropping a job is extremely expensive (10 million penalty)
    for node in range(2, len(locations)):
        routing.AddDisjunction([manager.NodeToIndex(node)], 10000000)

    # Fixed Cost: Activating a new worker is moderately expensive (100k penalty)
    routing.SetFixedCostOfAllVehicles(100000)

    if lunch_break:
        node_visit_transit = [0] * routing.Size()
        for vehicle_id in range(num_vehicles):
            break_intervals = [
                routing.solver().FixedDurationIntervalVar(
                    lunch_start_min, lunch_start_max, lunch_duration, False, f'Lunch_{vehicle_id}'
                )
            ]
            time_dimension.SetBreakIntervalsOfVehicle(break_intervals, vehicle_id, node_visit_transit)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 10

    solution = routing.SolveWithParameters(search_parameters)

    if not solution:
        return jsonify({"status": "error", "message": "Configuration failure."}), 400

    routes = []
    visited_nodes = set()

    # We assign worker ID sequentially based on who actually gets routes
    active_worker_count = 0

    for vehicle_id in range(num_vehicles):
        index = routing.Start(vehicle_id)
        route_edges = []
        route_time = 0

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node >= 2: visited_nodes.add(node)
            previous_index = index
            index = solution.Value(routing.NextVar(index))
            next_node = manager.IndexToNode(index)

            real_seconds = distance_callback(previous_index, index)
            travel_only = max(0, real_seconds - (stay_time if next_node >= 2 else 0))
            route_time += real_seconds

            route_edges.append({
                "from_node": node,
                "to_node": next_node,
                "cost_seconds": travel_only
            })

        # Only keep routes that do actual work (stop at least 1 job)
        if len(route_edges) > 1 and not (len(route_edges) == 1 and route_edges[0]['to_node'] == 1):
            routes.append({
                "worker_id": active_worker_count,
                "edges": route_edges,
                "time_seconds": route_time,
                "stops": len([e for e in route_edges if e['to_node'] >= 2])
            })
            active_worker_count += 1

    all_nodes = set(range(2, len(locations)))
    skipped_nodes = list(all_nodes - visited_nodes)

    return jsonify({
        "status": "success",
        "active_workers": len(routes),
        "routes": routes,
        "skipped_nodes": skipped_nodes
    })


if __name__ == '__main__':
    app.run(port=5001, debug=True)
