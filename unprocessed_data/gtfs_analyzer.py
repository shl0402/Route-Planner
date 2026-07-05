import os
import zipfile
import pandas as pd

def load_gtfs_csv(zip_ref, filename):
    """Helper to safely load a CSV from inside a zip file."""
    try:
        with zip_ref.open(filename) as f:
            return pd.read_csv(f, low_memory=False)
    except KeyError:
        return None

def time_to_seconds(t):
    """Converts HH:MM:SS string to total seconds past midnight."""
    try:
        h, m, s = map(int, str(t).split(':'))
        return h * 3600 + m * 60 + s
    except:
        return None

def sec_to_time(seconds):
    """Converts total seconds back into a clean HH:MM:SS string for the CSV."""
    if pd.isna(seconds) or seconds is None:
        return ""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def export_all_wait_times(input_zip):
    print(f"[*] Analyzing all waiting times and service windows in {input_zip}...")
    
    if not os.path.exists(input_zip):
        print(f"[!] Error: Could not find {input_zip}")
        return

    with zipfile.ZipFile(input_zip, 'r') as zip_ref:
        routes = load_gtfs_csv(zip_ref, 'routes.txt')
        trips = load_gtfs_csv(zip_ref, 'trips.txt')
        frequencies = load_gtfs_csv(zip_ref, 'frequencies.txt')
        stop_times = load_gtfs_csv(zip_ref, 'stop_times.txt')

        # Safely extract route names
        route_cols = ['route_id']
        if 'route_short_name' in routes.columns: route_cols.append('route_short_name')
        if 'route_long_name' in routes.columns: route_cols.append('route_long_name')
        
        trip_routes = pd.merge(trips, routes[route_cols], on='route_id', how='left')
        
        # Create a unified display name
        if 'route_short_name' in trip_routes.columns:
            trip_routes['display_name'] = trip_routes['route_short_name'].fillna(trip_routes['route_id'])
        elif 'route_long_name' in trip_routes.columns:
            trip_routes['display_name'] = trip_routes['route_long_name'].fillna(trip_routes['route_id'])
        else:
            trip_routes['display_name'] = trip_routes['route_id'].astype(str)
            
        results = []
        all_windows = []

        # 1. Analyze Frequencies (Hardcoded waits)
        if frequencies is not None:
            freq_merged = pd.merge(frequencies, trip_routes, on='trip_id', how='left')
            freq_merged['wait_mins'] = freq_merged['headway_secs'] / 60
            
            # Extract time windows
            freq_merged['start_sec'] = freq_merged['start_time'].apply(time_to_seconds)
            freq_merged['end_sec'] = freq_merged['end_time'].apply(time_to_seconds)
            
            f_win = freq_merged.groupby('display_name').agg(
                start_sec=('start_sec', 'min'),
                end_sec=('end_sec', 'max')
            ).reset_index()
            all_windows.append(f_win)
            
            freq_summary = freq_merged.groupby('display_name').agg(
                avg_wait_mins=('wait_mins', 'mean'),
                min_wait_mins=('wait_mins', 'min'),
                max_wait_mins=('wait_mins', 'max'),
                source=pd.NamedAgg(column='trip_id', aggfunc=lambda x: 'frequencies.txt')
            ).reset_index()
            results.append(freq_summary)

        # 2. Analyze Stop Times (Timetable gaps)
        if stop_times is not None:
            print("  -> Crunching timetable gaps and operating hours. This takes a few seconds...")
            st = stop_times[['trip_id', 'stop_id', 'departure_time']].dropna()
            st['dep_sec'] = st['departure_time'].apply(time_to_seconds)
            
            st_merged = pd.merge(st, trip_routes[['trip_id', 'display_name']], on='trip_id', how='left')
            
            # Extract time windows BEFORE filtering out gaps
            st_win = st_merged.groupby('display_name').agg(
                start_sec=('dep_sec', 'min'),
                end_sec=('dep_sec', 'max')
            ).reset_index()
            all_windows.append(st_win)
            
            st_merged = st_merged.sort_values(by=['stop_id', 'display_name', 'dep_sec'])
            
            # Calculate time difference to the NEXT bus
            st_merged['wait_to_next'] = st_merged.groupby(['stop_id', 'display_name'])['dep_sec'].diff().shift(-1)
            st_merged['wait_mins'] = st_merged['wait_to_next'] / 60
            
            # Filter out end-of-day gaps (e.g. gaps over 4 hours between the last bus at night and first bus next day)
            valid_gaps = st_merged[(st_merged['wait_mins'] > 0) & (st_merged['wait_mins'] <= 240)]
            
            st_summary = valid_gaps.groupby('display_name').agg(
                avg_wait_mins=('wait_mins', 'mean'),
                min_wait_mins=('wait_mins', 'min'),
                max_wait_mins=('wait_mins', 'max'),
                source=pd.NamedAgg(column='trip_id', aggfunc=lambda x: 'stop_times.txt')
            ).reset_index()
            results.append(st_summary)

        # 3. Combine, format, and output
        if results:
            final_df = pd.concat(results, ignore_index=True)
            
            # If a route exists in both, group it again and take the average
            final_df = final_df.groupby(['display_name', 'source']).mean().reset_index()
            
            # Merge in the operational time windows
            if all_windows:
                windows_df = pd.concat(all_windows, ignore_index=True)
                windows_df = windows_df.groupby('display_name').agg(
                    start_sec=('start_sec', 'min'),
                    end_sec=('end_sec', 'max')
                ).reset_index()
                
                final_df = pd.merge(final_df, windows_df, on='display_name', how='left')
                final_df['service_start'] = final_df['start_sec'].apply(sec_to_time)
                final_df['service_end'] = final_df['end_sec'].apply(sec_to_time)
                final_df.drop(columns=['start_sec', 'end_sec'], inplace=True)
            
            # Sort by wait times and round numbers
            final_df = final_df.sort_values('avg_wait_mins').round(1)
            
            print("\n" + "="*80)
            print(f"🚌 WAITING TIMES & SERVICE PERIODS FOR ALL ROUTES IN {input_zip}")
            print("="*80)
            
            # Force Pandas to print EVERY row to the terminal without truncating
            with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', 1000):
                print(final_df.to_string(index=False))
            
            # Export to CSV for manual review
            csv_filename = input_zip.replace('.zip', '_all_waits.csv')
            final_df.to_csv(csv_filename, index=False)
            print(f"\n✅ COMPLETE! Data with service periods saved to '{csv_filename}'")

if __name__ == "__main__":
    export_all_wait_times("unprocessed_data/gtfs_merged_multilingual.zip")