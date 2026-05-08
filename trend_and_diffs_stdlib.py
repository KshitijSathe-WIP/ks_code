import csv
from pathlib import Path
from collections import defaultdict, Counter
import sys

zcop_output_folder = Path(r"c:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\Zcop Output")
input_csv = zcop_output_folder / "DATA_combined.csv"

if not input_csv.exists():
    print(f"Input CSV not found: {input_csv}")
    sys.exit(1)

print(f"Reading {input_csv}")

# Read CSV and accumulate counts
slwbs_counts = defaultdict(Counter)   # date -> Counter(SLWBS -> count)
labour_counts = defaultdict(Counter) # date -> Counter(Labour -> count)
codes_by_date = defaultdict(set)     # date -> set(EMP_CODE)
name_by_date = dict()                 # (date, emp_code) -> emp_name
all_slwbs = set()
all_labours = set()

with open(input_csv, 'r', encoding='utf-8', newline='') as f:
    reader = csv.reader(f)
    try:
        header = next(reader)
    except StopIteration:
        print('Empty CSV')
        sys.exit(1)

    # find columns
    hdr_lc = [h.lower() for h in header]
    try:
        idx_load = hdr_lc.index('load_date')
    except ValueError:
        print('LOAD_DATE column not found in header')
        sys.exit(1)

    # SLWBS
    if 'slwbs' in hdr_lc:
        idx_slwbs = hdr_lc.index('slwbs')
    else:
        idx_slwbs = None
    # labour column: first header containing 'labour'
    idx_labour = None
    for i,h in enumerate(hdr_lc):
        if 'labour' in h:
            idx_labour = i
            break
    try:
        idx_emp_code = hdr_lc.index('emp_code')
        idx_emp_name = hdr_lc.index('emp_name')
    except ValueError:
        print('EMP_CODE or EMP_NAME column missing')
        sys.exit(1)

    for row in reader:
        # defensive: skip short rows
        if len(row) <= idx_load:
            continue
        load_val = row[idx_load].strip()
        if not load_val:
            continue
        # extract date portion (assumes ISO-like 'YYYY-MM-DD' at start)
        date_part = load_val.split()[0]

        # SLWBS
        slwbs_val = ''
        if idx_slwbs is not None and idx_slwbs < len(row):
            slwbs_val = row[idx_slwbs].strip()
        all_slwbs.add(slwbs_val)
        slwbs_counts[date_part][slwbs_val] += 1

        # Labour
        if idx_labour is not None and idx_labour < len(row):
            labour_val = row[idx_labour].strip()
            all_labours.add(labour_val)
            labour_counts[date_part][labour_val] += 1

        # EMP
        if idx_emp_code < len(row):
            emp_code = row[idx_emp_code].strip()
        else:
            emp_code = ''
        emp_name = row[idx_emp_name].strip() if idx_emp_name < len(row) else ''
        if emp_code:
            codes_by_date[date_part].add(emp_code)
            # record name if not already recorded for that date+code
            if (date_part, emp_code) not in name_by_date:
                name_by_date[(date_part, emp_code)] = emp_name

# Prepare sorted dates
dates = sorted(slwbs_counts.keys() | labour_counts.keys() | codes_by_date.keys())
if not dates:
    print('No dated records found')
    sys.exit(0)

# Write trend_slwbs.csv
slwbs_list = sorted(all_slwbs)
trend_slwbs_path = zcop_output_folder / 'trend_slwbs.csv'
with open(trend_slwbs_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['DATE'] + slwbs_list)
    for d in dates:
        row = [d] + [slwbs_counts[d].get(s, 0) for s in slwbs_list]
        writer.writerow(row)
print(f'Wrote {trend_slwbs_path}')

# Write trend_labour_report.csv
labour_list = sorted(all_labours)
trend_labour_path = zcop_output_folder / 'trend_labour_report.csv'
with open(trend_labour_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['DATE'] + labour_list)
    for d in dates:
        row = [d] + [labour_counts[d].get(s, 0) for s in labour_list]
        writer.writerow(row)
print(f'Wrote {trend_labour_path}')

# Compute additions/deletions between consecutive dates
add_del_rows = []
for i in range(1, len(dates)):
    prev = dates[i-1]
    curr = dates[i]
    prev_set = codes_by_date.get(prev, set())
    curr_set = codes_by_date.get(curr, set())
    additions = sorted(curr_set - prev_set)
    deletions = sorted(prev_set - curr_set)
    for c in additions:
        name = name_by_date.get((curr, c), '')
        add_del_rows.append({'prev_date': prev, 'curr_date': curr, 'change': 'addition', 'EMP_CODE': c, 'EMP_NAME': name})
    for c in deletions:
        name = name_by_date.get((prev, c), '')
        add_del_rows.append({'prev_date': prev, 'curr_date': curr, 'change': 'deletion', 'EMP_CODE': c, 'EMP_NAME': name})

ad_path = zcop_output_folder / 'additions_deletions_by_date_pairs.csv'
with open(ad_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['prev_date','curr_date','change','EMP_CODE','EMP_NAME'])
    writer.writeheader()
    writer.writerows(add_del_rows)
print(f'Wrote {ad_path}')

# Aggregate additions and deletions
additions_all = [r for r in add_del_rows if r['change'] == 'addition']
deletions_all = [r for r in add_del_rows if r['change'] == 'deletion']
add_all_path = zcop_output_folder / 'additions_all.csv'
del_all_path = zcop_output_folder / 'deletions_all.csv'
with open(add_all_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['DATE','EMP_CODE','EMP_NAME'])
    writer.writeheader()
    for r in additions_all:
        writer.writerow({'DATE': r['curr_date'], 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})
with open(del_all_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['DATE','EMP_CODE','EMP_NAME'])
    writer.writeheader()
    for r in deletions_all:
        writer.writerow({'DATE': r['curr_date'], 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})
print(f'Wrote {add_all_path} and {del_all_path}')

# Summary
print('\nSummary:')
print('  Dates found:', len(dates))
print('  SLWBS categories:', len(slwbs_list))
print('  Labour categories:', len(labour_list))
print('  Additions/deletions rows:', len(add_del_rows))
print('\nDone.')
