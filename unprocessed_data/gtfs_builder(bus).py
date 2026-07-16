#!/usr/bin/env python3
"""
GTFS Splitter & Time Adjuster (Bus vs Non-Bus)
Fixed: Strictly isolates all.json to Bus routes to preserve Ferry/MTR schedules.
"""

import zipfile
import csv
import json
import os
from io import TextIOWrapper
from collections import defaultdict

# ================== CONFIG ==================
ORIGINAL_ZIP = "gtfs_merged_multilingual.zip"
ALL_JSON_FILE = "all.json"

OUTPUT_BUS_ZIP = "bus-gtfs.zip"
OUTPUT_NONBUS_ZIP = "nonbus-gtfs.zip"

BUS_ROUTE_TYPE = "3"


# ============================================

def parse_gtfs_time(time_str):
    if not time_str or not time_str.strip():
        return None
    try:
        h, m, s = map(int, time_str.split(':'))
        return h * 3600 + m * 60 + s
    except:
        return None


def seconds_to_gtfs_time(seconds):
    # Fix: Safely wrap negative times (e.g., -60s becomes 23:59:00)
    if seconds < 0:
        seconds = (24 * 3600) + seconds

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def load_journey_times(json_path):
    print(f"[1/5] Loading journey times from {json_path}...")
    if not os.path.exists(json_path):
        print("      File not found. Proceeding with original GTFS times only.")
        return {}

    with open(json_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    lookup = {}
    count = 0
    if isinstance(raw, dict):
        for from_stop, targets in raw.items():
            if isinstance(targets, dict):
                for to_stop, seconds in targets.items():
                    try:
                        lookup[(from_stop, to_stop)] = int(float(seconds))
                        count += 1
                    except:
                        continue
    print(f"      Loaded {count} journey time pairs.")
    return lookup


def adjust_bus_stop_times(stop_times_by_trip, journey_lookup):
    """Applies all.json offsets STRICTLY to bus data."""
    new_stop_times = []
    used_json = 0

    for trip_id, stops in stop_times_by_trip.items():
        if not stops:
            continue

        stops.sort(key=lambda x: int(x.get('stop_sequence', 0)))
        offset = 0

        for i in range(len(stops)):
            stop = stops[i].copy()

            orig_arr = parse_gtfs_time(stop.get('arrival_time'))
            orig_dep = parse_gtfs_time(stop.get('departure_time'))

            if orig_arr is not None:
                stop['arrival_time'] = seconds_to_gtfs_time(orig_arr + offset)
            if orig_dep is not None:
                stop['departure_time'] = seconds_to_gtfs_time(orig_dep + offset)

            new_stop_times.append(stop)

            # Check next stop to update the offset for the rest of the trip
            if i < len(stops) - 1:
                from_stop = stops[i]['stop_id'].strip()
                to_stop = stops[i + 1]['stop_id'].strip()
                travel_sec = journey_lookup.get((from_stop, to_stop))

                if travel_sec is not None and travel_sec > 0:
                    next_orig_arr = parse_gtfs_time(stops[i + 1].get('arrival_time'))
                    curr_orig_dep = orig_dep if orig_dep is not None else orig_arr

                    if next_orig_arr is not None and curr_orig_dep is not None:
                        new_dep = curr_orig_dep + offset
                        new_arr_target = new_dep + travel_sec

                        # Calculate how much we need to shift the next stop to hit our target time
                        offset = new_arr_target - next_orig_arr
                        used_json += 1

    return new_stop_times, used_json


def flatten_stop_times(stop_times_by_trip):
    """Flattens non-bus stop times, perfectly preserving original schedules."""
    flattened = []
    for trip_id, stops in stop_times_by_trip.items():
        stops.sort(key=lambda x: int(x.get('stop_sequence', 0)))
        flattened.extend(stops)
    return flattened


def write_gtfs_zip(output_path, data_dict, fields_dict):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as dst:
        for filename, rows in data_dict.items():
            if not rows:
                continue
            with dst.open(filename, 'w') as f:
                fieldnames = fields_dict.get(filename, list(rows[0].keys()))
                wrapper = TextIOWrapper(f, encoding='utf-8', newline='')
                writer = csv.DictWriter(wrapper, fieldnames=fieldnames, lineterminator='\n', extrasaction='ignore')
                writer.writeheader()
                writer.writerows(rows)
                wrapper.flush()


def process_gtfs(original_zip, journey_lookup):
    print(f"[2/5] Reading and splitting GTFS data from {original_zip}...")

    bus_data, nonbus_data = defaultdict(list), defaultdict(list)
    bus_fields, nonbus_fields = {}, {}

    b_refs = {'agencies': set(), 'routes': set(), 'trips': set(), 'stops': set(), 'shapes': set(), 'services': set()}
    nb_refs = {'agencies': set(), 'routes': set(), 'trips': set(), 'stops': set(), 'shapes': set(), 'services': set()}

    bus_stop_times_by_trip = defaultdict(list)
    nonbus_stop_times_by_trip = defaultdict(list)

    with zipfile.ZipFile(original_zip, 'r') as z:
        files_in_zip = z.namelist()

        default_agency = "AGENCY"
        if 'agency.txt' in files_in_zip:
            with z.open('agency.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                for row in reader:
                    ag_id = row.get('agency_id', '').strip()
                    if ag_id:
                        default_agency = ag_id
                        break

        # --- 1. Routes ---
        if 'routes.txt' in files_in_zip:
            with z.open('routes.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                fields = list(reader.fieldnames) if reader.fieldnames else []
                for col in ['route_short_name', 'agency_id']:
                    if col not in fields: fields.append(col)
                bus_fields['routes.txt'] = nonbus_fields['routes.txt'] = fields

                for row in reader:
                    r_id = row.get('route_id', '').strip()
                    row['route_id'] = r_id

                    if not row.get('route_short_name', '').strip() and not row.get('route_long_name', '').strip():
                        row['route_short_name'] = r_id

                    a_id = row.get('agency_id', '').strip()
                    if not a_id:
                        row['agency_id'] = a_id = default_agency

                    for col in ['route_color', 'route_text_color']:
                        if col in row:
                            val = row[col].replace('#', '').strip()
                            row[col] = val if len(val) == 6 else ''

                    if row.get('route_type', '').strip() == BUS_ROUTE_TYPE:
                        bus_data['routes.txt'].append(row)
                        b_refs['routes'].add(r_id)
                        b_refs['agencies'].add(a_id)
                    else:
                        nonbus_data['routes.txt'].append(row)
                        nb_refs['routes'].add(r_id)
                        nb_refs['agencies'].add(a_id)

        # --- 2. Trips ---
        if 'trips.txt' in files_in_zip:
            with z.open('trips.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['trips.txt'] = nonbus_fields['trips.txt'] = list(reader.fieldnames)
                for row in reader:
                    r_id = row.get('route_id', '').strip()
                    t_id = row.get('trip_id', '').strip()
                    s_id = row.get('shape_id', '').strip()
                    srv_id = row.get('service_id', '').strip()

                    if r_id in b_refs['routes']:
                        bus_data['trips.txt'].append(row)
                        b_refs['trips'].add(t_id)
                        if s_id: b_refs['shapes'].add(s_id)
                        if srv_id: b_refs['services'].add(srv_id)
                    elif r_id in nb_refs['routes']:
                        nonbus_data['trips.txt'].append(row)
                        nb_refs['trips'].add(t_id)
                        if s_id: nb_refs['shapes'].add(s_id)
                        if srv_id: nb_refs['services'].add(srv_id)

        # --- 3. Stop Times ---
        if 'stop_times.txt' in files_in_zip:
            with z.open('stop_times.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['stop_times.txt'] = nonbus_fields['stop_times.txt'] = list(reader.fieldnames)
                for row in reader:
                    t_id = row.get('trip_id', '').strip()
                    s_id = row.get('stop_id', '').strip()

                    if t_id in b_refs['trips']:
                        bus_stop_times_by_trip[t_id].append(row)
                        b_refs['stops'].add(s_id)
                    elif t_id in nb_refs['trips']:
                        nonbus_stop_times_by_trip[t_id].append(row)
                        nb_refs['stops'].add(s_id)

        print("[3/5] Adjusting travel times dynamically for BUSES...")
        bus_data['stop_times.txt'], b_json_used = adjust_bus_stop_times(bus_stop_times_by_trip, journey_lookup)
        print(f"      Bus times adjusted via JSON: {b_json_used} times.")

        print("      Processing NON-BUS times (Strict Schedule Preservation)...")
        nonbus_data['stop_times.txt'] = flatten_stop_times(nonbus_stop_times_by_trip)

        # --- 4. Stops ---
        if 'stops.txt' in files_in_zip:
            with z.open('stops.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['stops.txt'] = nonbus_fields['stops.txt'] = list(reader.fieldnames)
                for row in reader:
                    s_id = row.get('stop_id', '').strip()
                    if s_id in b_refs['stops']: bus_data['stops.txt'].append(row)
                    if s_id in nb_refs['stops']: nonbus_data['stops.txt'].append(row)

        # --- 5. Shapes ---
        if 'shapes.txt' in files_in_zip:
            with z.open('shapes.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['shapes.txt'] = nonbus_fields['shapes.txt'] = list(reader.fieldnames)
                for row in reader:
                    s_id = row.get('shape_id', '').strip()
                    if s_id in b_refs['shapes']: bus_data['shapes.txt'].append(row)
                    if s_id in nb_refs['shapes']: nonbus_data['shapes.txt'].append(row)

        # --- 6. Calendars ---
        for cal_file in ['calendar.txt', 'calendar_dates.txt']:
            if cal_file in files_in_zip:
                with z.open(cal_file) as f:
                    reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                    bus_fields[cal_file] = nonbus_fields[cal_file] = list(reader.fieldnames)
                    for row in reader:
                        srv_id = row.get('service_id', '').strip()
                        if srv_id in b_refs['services']: bus_data[cal_file].append(row)
                        if srv_id in nb_refs['services']: nonbus_data[cal_file].append(row)

        # --- 7. Frequencies ---
        if 'frequencies.txt' in files_in_zip:
            with z.open('frequencies.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['frequencies.txt'] = nonbus_fields['frequencies.txt'] = list(reader.fieldnames)
                for row in reader:
                    t_id = row.get('trip_id', '').strip()
                    if t_id in b_refs['trips']: bus_data['frequencies.txt'].append(row)
                    if t_id in nb_refs['trips']: nonbus_data['frequencies.txt'].append(row)

        # --- 8. Translations ---
        if 'translations.txt' in files_in_zip:
            with z.open('translations.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                bus_fields['translations.txt'] = nonbus_fields['translations.txt'] = list(reader.fieldnames)
                for row in reader:
                    table = row.get('table_name', '').strip()
                    record_id = row.get('record_id', '').strip()

                    if (table == 'routes' and record_id in b_refs['routes']) or \
                            (table == 'stops' and record_id in b_refs['stops']) or \
                            (table == 'trips' and record_id in b_refs['trips']) or \
                            (table == 'agency' and record_id in b_refs['agencies']):
                        bus_data['translations.txt'].append(row)

                    if (table == 'routes' and record_id in nb_refs['routes']) or \
                            (table == 'stops' and record_id in nb_refs['stops']) or \
                            (table == 'trips' and record_id in nb_refs['trips']) or \
                            (table == 'agency' and record_id in nb_refs['agencies']):
                        nonbus_data['translations.txt'].append(row)

        # --- 9. Agency ---
        b_exist_ag, nb_exist_ag = set(), set()
        if 'agency.txt' in files_in_zip:
            with z.open('agency.txt') as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8-sig'))
                fields = list(reader.fieldnames)
                if 'agency_id' not in fields: fields.append('agency_id')
                bus_fields['agency.txt'] = nonbus_fields['agency.txt'] = fields

                for row in reader:
                    ag_id = row.get('agency_id', '').strip()
                    if not ag_id or ag_id in b_refs['agencies']:
                        bus_data['agency.txt'].append(row)
                        b_exist_ag.add(ag_id)
                    if not ag_id or ag_id in nb_refs['agencies']:
                        nonbus_data['agency.txt'].append(row)
                        nb_exist_ag.add(ag_id)

        def patch_agencies(data_dict, valid_agencies, existing_agencies):
            for missing_ag in (valid_agencies - existing_agencies):
                if missing_ag:
                    data_dict['agency.txt'].append({
                        'agency_id': missing_ag,
                        'agency_name': f"Agency {missing_ag}",
                        'agency_url': "http://example.com",
                        'agency_timezone': "Asia/Hong_Kong"
                    })

        patch_agencies(bus_data, b_refs['agencies'], b_exist_ag)
        patch_agencies(nonbus_data, nb_refs['agencies'], nb_exist_ag)

    print("\n[4/5] Writing bus-gtfs.zip...")
    write_gtfs_zip(OUTPUT_BUS_ZIP, bus_data, bus_fields)

    print("[5/5] Writing nonbus-gtfs.zip...")
    write_gtfs_zip(OUTPUT_NONBUS_ZIP, nonbus_data, nonbus_fields)

    print("\n✅ Success! Files have been split and times are correct.")


if __name__ == "__main__":
    print("=== Universal GTFS Splitter & Travel Time Adjuster ===\n")

    if not os.path.exists(ORIGINAL_ZIP):
        print(f"ERROR: {ORIGINAL_ZIP} not found!")
        exit(1)

    journey_lookup = load_journey_times(ALL_JSON_FILE)
    process_gtfs(ORIGINAL_ZIP, journey_lookup)