import csv
import zipfile
import os

# Define the input files and their language codes
BASE_ZIP = 'gtfs (1).zip'  # English (used as the core structure)
TRAD_ZIP = 'gtfs.zip'  # Traditional Chinese
SIMP_ZIP = 'gtfs (2).zip'  # Simplified Chinese
OUTPUT_ZIP = 'gtfs_merged_multilingual.zip'

LANG_MAP = {
    TRAD_ZIP: 'zh-TW',
    SIMP_ZIP: 'zh-CN'
}


def extract_names_from_zip(zip_path, filename, id_col, name_cols):
    """Extracts ID-to-Name mappings from a specific file inside a zip."""
    data = {}
    with zipfile.ZipFile(zip_path, 'r') as z:
        if filename in z.namelist():
            with z.open(filename) as f:
                reader = csv.DictReader(f.read().decode('utf-8-sig').splitlines())
                for row in reader:
                    record_id = row.get(id_col)
                    if record_id:
                        data[record_id] = {col: row.get(col) for col in name_cols if row.get(col)}
    return data


def build_translations():
    print("Extracting translation data...")
    translations = []

    # Define which files and columns need translating
    tables_to_translate = [
        {'table': 'stops', 'file': 'stops.txt', 'id_col': 'stop_id', 'name_cols': ['stop_name']},
        {'table': 'routes', 'file': 'routes.txt', 'id_col': 'route_id',
         'name_cols': ['route_short_name', 'route_long_name']},
        {'table': 'agency', 'file': 'agency.txt', 'id_col': 'agency_id', 'name_cols': ['agency_name']}
    ]

    for table_config in tables_to_translate:
        table = table_config['table']
        filename = table_config['file']
        id_col = table_config['id_col']
        name_cols = table_config['name_cols']

        for zip_file, lang_code in LANG_MAP.items():
            try:
                lang_data = extract_names_from_zip(zip_file, filename, id_col, name_cols)

                for record_id, columns in lang_data.items():
                    for field_name, translated_text in columns.items():
                        if translated_text:  # Only add if a translation actually exists
                            translations.append({
                                'table_name': table,
                                'field_name': field_name,
                                'language': lang_code,
                                'translation': translated_text,
                                'record_id': record_id
                            })
            except FileNotFoundError:
                print(f"Warning: {zip_file} not found. Skipping {lang_code} translation.")

    return translations


def create_merged_gtfs():
    translations = build_translations()

    print(f"Merging files into {OUTPUT_ZIP}...")
    with zipfile.ZipFile(BASE_ZIP, 'r') as base_z:
        with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as out_z:

            # 1. Copy all core structural files from the Base (English) zip
            for item in base_z.infolist():
                content = base_z.read(item.filename)
                out_z.writestr(item, content)

            # 2. Generate and inject the new translations.txt file
            trans_csv = "table_name,field_name,language,translation,record_id\n"
            for t in translations:
                # Sanitize text to avoid CSV breaking on commas
                safe_text = t['translation'].replace('"', '""')
                if ',' in safe_text:
                    safe_text = f'"{safe_text}"'

                trans_csv += f"{t['table_name']},{t['field_name']},{t['language']},{safe_text},{t['record_id']}\n"

            out_z.writestr('translations.txt', trans_csv.encode('utf-8'))

    print(f"SUCCESS! {len(translations)} translation records injected.")
    print("You can now load gtfs_merged_multilingual.zip into OpenTripPlanner.")


if __name__ == "__main__":
    create_merged_gtfs()
