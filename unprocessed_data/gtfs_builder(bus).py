import os
import zipfile
import pandas as pd

def time_to_seconds(t):
    """Converts HH:MM:SS string to total seconds past midnight."""
    try:
        h, m, s = map(int, str(t).split(':'))
        return h * 3600 + m * 60 + s
    except:
        return 0

def sec_to_time(seconds):
    """Converts total seconds back into a clean HH:MM:SS string."""
    if pd.isna(seconds): return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def create_average_frequency_gtfs(input_zip, output_zip):
    print(f"[*] Reading original GTFS data from {input_zip}...")
    
    if not os.path.exists(input_zip):
        print(f"[!] Error: Could not find {input_zip}")
        return

    # 1. Read necessary files
    with zipfile.ZipFile(input_zip, 'r') as zin:
        print("  -> Loading files (this may take a moment)...")
        trips = pd.read_csv(zin.open('trips.txt'), low_memory=False)
        stop_times = pd.read_csv(zin.open('stop_times.txt'), low_memory=False)
        
        try:
            orig_freq = pd.read_csv(zin.open('frequencies.txt'), low_memory=False)
            has_freq = True
        except KeyError:
            has_freq = False

    print("[*] Calculating exact operating windows for all trips...")
    st = stop_times[['trip_id', 'stop_id', 'arrival_time', 'departure_time']].dropna()
    
    # Find start and end times for every trip
    st['dep_sec'] = st['departure_time'].apply(time_to_seconds)
    trip_windows = st.groupby('trip_id').agg(
        start_sec=('dep_sec', 'min'),
        end_sec=('dep_sec', 'max')
    ).reset_index()
    
    print("[*] Calculating historical average wait times per route...")
    # Merge stop times with trips to group by route_id
    st_trips = pd.merge(st, trips[['trip_id', 'route_id']], on='trip_id', how='left')
    st_trips = st_trips.sort_values(by=['stop_id', 'route_id', 'dep_sec'])
    
    # Calculate gap to next bus at the same stop
    st_trips['wait_to_next'] = st_trips.groupby(['stop_id', 'route_id'])['dep_sec'].diff().shift(-1)
    
    # Filter reasonable gaps (between 1 minute and 4 hours) to eliminate end-of-day gaps
    valid_gaps = st_trips[(st_trips['wait_to_next'] >= 60) & (st_trips['wait_to_next'] <= 14400)]
    
    # Calculate the mean headway (wait time) per route in seconds
    route_avg_headway = valid_gaps.groupby('route_id')['wait_to_next'].mean().reset_index()
    route_avg_headway.rename(columns={'wait_to_next': 'headway_secs'}, inplace=True)
    route_avg_headway['headway_secs'] = route_avg_headway['headway_secs'].astype(int)

    # 2. Merge averages back to trips
    trip_windows = pd.merge(trip_windows, trips[['trip_id', 'route_id']], on='trip_id', how='left')
    trip_windows = pd.merge(trip_windows, route_avg_headway, on='route_id', how='left')
    
    # Default to 30 mins (1800s) if a route had no calculable average (e.g., only 1 trip a day)
    trip_windows['headway_secs'] = trip_windows['headway_secs'].fillna(1800).astype(int)
    
    # Format times back to HH:MM:SS
    trip_windows['start_time'] = trip_windows['start_sec'].apply(sec_to_time)
    trip_windows['end_time'] = trip_windows['end_sec'].apply(sec_to_time)

    # 3. Create the final frequencies dataframe
    final_frequencies = trip_windows[['trip_id', 'start_time', 'end_time', 'headway_secs']].copy()
    
    # exact_times=0 tells OTP: "This is a frequency-based route, use average wait penalties for transfers!"
    final_frequencies['exact_times'] = 0 

    # Save to a temporary CSV
    final_frequencies.to_csv('temp_frequencies.txt', index=False)
    
    print(f"[*] Packaging new GTFS into {output_zip}...")
    
    # 4. Create the new zip file
    with zipfile.ZipFile(input_zip, 'r') as zin:
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'frequencies.txt':
                    continue # Skip the old frequency file
                
                content = zin.read(item.filename)
                zout.writestr(item, content)
            
            # Inject our newly calculated average frequency file
            zout.write('temp_frequencies.txt', arcname='frequencies.txt')
            
    # Clean up
    if os.path.exists('temp_frequencies.txt'):
        os.remove('temp_frequencies.txt')
        
    print(f"\n✅ SUCCESS! Created time-accurate, average-frequency file: {output_zip}")
    print("-> OTP will now naturally penalize transfers based on the route's real average wait time!")
    print("-> Remember to delete 'graph.obj' and restart OTP.")

if __name__ == '__main__':
    # Update these paths to match your project folder
    INPUT_FILE = 'unprocessed_data/gtfs_merged_multilingual.zip'
    OUTPUT_FILE = 'unprocessed_data/gtfs_average_wait.zip'
    
    create_average_frequency_gtfs(INPUT_FILE, OUTPUT_FILE)