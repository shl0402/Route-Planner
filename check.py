import zipfile
import csv
import io

def analyze_gtfs_routes(zip_path):
    print(f"Scanning {zip_path} for vehicle classifications...\n")
    
    # Official GTFS standard reference
    standard_types = {
        '0': 'Tram / Light Rail',
        '1': 'Subway / Metro',
        '2': 'Rail',
        '3': 'Bus',
        '4': 'Ferry',
        '5': 'Cable Tram (Peak Tram)',
        '6': 'Aerial Lift (Ngong Ping 360)',
        '7': 'Funicular',
        '11': 'Trolleybus',
        '12': 'Monorail'
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            if 'routes.txt' not in z.namelist():
                print("Error: routes.txt not found in the zip archive.")
                return

            with z.open('routes.txt') as f:
                # Read the CSV data directly from the zip stream
                reader = csv.DictReader(io.TextIOWrapper(f, 'utf-8-sig'))
                
                types_found = {}
                
                for row in reader:
                    r_type = row.get('route_type')
                    # Grab whatever name is available
                    r_name = row.get('route_short_name')
                    if not r_name:
                        r_name = row.get('route_long_name', 'Unnamed Route')
                    
                    agency = row.get('agency_id', 'Unknown')
                    
                    if r_type not in types_found:
                        types_found[r_type] = []
                        
                    # Save up to 8 examples per code so we can see what's what
                    if len(types_found[r_type]) < 8:
                        types_found[r_type].append(f"[{agency}] {r_name}")

        # Print the final report
        print("--- HK Government GTFS Route Type Mapping ---")
        for r_type in sorted(types_found.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            description = standard_types.get(r_type, f"Extended/Custom Type")
            print(f"\nRoute Type {r_type} ({description}):")
            for ex in types_found[r_type]:
                print(f"  - {ex}")

    except FileNotFoundError:
        print(f"Could not find the file at {zip_path}. Check the path!")
    except Exception as e:
        print(f"An error occurred: {e}")

# Run the scanner on the zip file in your data folder
analyze_gtfs_routes('./data/gtfs.zip')