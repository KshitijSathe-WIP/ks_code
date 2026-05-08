import csv
from pathlib import Path
from collections import OrderedDict

# Set up paths
zcop_output_folder = Path(r"c:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\Zcop Output")

print("Creating updated combined datasets...")
print("=" * 60)

# Read all existing CSV files
all_data_rows = []
all_ru_rd_rows = []

data_csv_path = zcop_output_folder / "DATA_combined.csv"
ru_rd_csv_path = zcop_output_folder / "RU-RD_combined.csv"
data_14apr_path = zcop_output_folder / "DATA_14Apr_2026.csv"
ru_rd_14apr_path = zcop_output_folder / "RU-RD_14Apr_2026.csv"

# Read DATA_combined.csv
if data_csv_path.exists():
    with open(data_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
        if rows:
            all_data_rows = rows
            print(f"✓ Loaded DATA_combined.csv: {len(rows) - 1} records")

# Read DATA_14Apr_2026.csv (skip header since we already have it)
if data_14apr_path.exists():
    with open(data_14apr_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
        if rows and len(rows) > 1:
            # Skip header, add data rows
            all_data_rows.extend(rows[1:])
            print(f"✓ Loaded DATA_14Apr_2026.csv: {len(rows) - 1} records")

# Read RU-RD_combined.csv
if ru_rd_csv_path.exists():
    with open(ru_rd_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
        if rows:
            all_ru_rd_rows = rows
            print(f"✓ Loaded RU-RD_combined.csv: {len(rows) - 1} records")

# Read RU-RD_14Apr_2026.csv (skip header)
if ru_rd_14apr_path.exists():
    with open(ru_rd_14apr_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
        if rows and len(rows) > 1:
            # Skip header, add data rows
            all_ru_rd_rows.extend(rows[1:])
            print(f"✓ Loaded RU-RD_14Apr_2026.csv: {len(rows) - 1} records")

print("=" * 60)

# Remove duplicates while preserving order (using row tuple as key)
def remove_duplicates(rows):
    if not rows:
        return rows
    
    header = rows[0]
    data = rows[1:]
    
    # Convert rows to tuples for deduplication
    seen = set()
    unique_data = []
    for row in data:
        row_tuple = tuple(row)
        if row_tuple not in seen:
            seen.add(row_tuple)
            unique_data.append(row)
    
    return [header] + unique_data

print("\nRemoving duplicates...")
all_data_rows = remove_duplicates(all_data_rows)
all_ru_rd_rows = remove_duplicates(all_ru_rd_rows)

# Write updated DATA_combined.csv
if all_data_rows:
    with open(data_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(all_data_rows)
    total_data = len(all_data_rows) - 1
    print(f"✓ Updated DATA_combined.csv")
    print(f"  Total records: {total_data}")

# Write updated RU-RD_combined.csv
if all_ru_rd_rows:
    with open(ru_rd_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(all_ru_rd_rows)
    total_ru_rd = len(all_ru_rd_rows) - 1
    print(f"✓ Updated RU-RD_combined.csv")
    print(f"  Total records: {total_ru_rd}")

print("\n" + "=" * 60)
print("✓ Updated combined datasets created successfully!")
print("=" * 60)
print(f"\nSummary:")
print(f"  DATA_combined.csv:    {len(all_data_rows) - 1} records")
print(f"  RU-RD_combined.csv:   {len(all_ru_rd_rows) - 1} records")
