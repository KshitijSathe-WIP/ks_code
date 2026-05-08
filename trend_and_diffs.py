import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from pathlib import Path
import csv
import sys

sns.set(style='whitegrid')

# Paths
zcop_output_folder = Path(r"c:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\Zcop Output")
input_csv = zcop_output_folder / "DATA_combined.csv"

if not input_csv.exists():
    print(f"Input CSV not found: {input_csv}")
    sys.exit(1)

print(f"Loading: {input_csv}")
df = pd.read_csv(input_csv, encoding='utf-8', low_memory=False)

# Find labour report column (first column that contains 'labour')
labour_cols = [c for c in df.columns if 'labour' in c.lower()]
labour_col = labour_cols[0] if labour_cols else None
if labour_col:
    print(f"Found labour column: {labour_col}")
else:
    print("No 'Labour' column found; labour trends will be skipped.")

# Normalize dates
if 'LOAD_DATE' not in df.columns:
    print("LOAD_DATE column missing. Aborting.")
    sys.exit(1)

df['LOAD_DATE'] = pd.to_datetime(df['LOAD_DATE'], errors='coerce')
# drop rows without date
df = df[~df['LOAD_DATE'].isna()].copy()
# Use date (not time) for trend
df['DATE'] = df['LOAD_DATE'].dt.date

# Ensure EMP_CODE/EMP_NAME exist
if 'EMP_CODE' not in df.columns or 'EMP_NAME' not in df.columns:
    print("EMP_CODE or EMP_NAME missing. Aborting.")
    sys.exit(1)

df['EMP_CODE'] = df['EMP_CODE'].astype(str)
df['EMP_NAME'] = df['EMP_NAME'].astype(str)

outputs = []

# 1) SLWBS trend
if 'SLWBS' in df.columns:
    slwbs_counts = df.groupby(['DATE', 'SLWBS']).size().unstack(fill_value=0)
    slwbs_counts.sort_index(inplace=True)
    slwbs_csv = zcop_output_folder / 'trend_slwbs.csv'
    slwbs_counts.to_csv(slwbs_csv)
    outputs.append(slwbs_csv)

    # Plot top 10 SLWBS
    topN = 10
    sums = slwbs_counts.sum().sort_values(ascending=False)
    top_slwbs = list(sums.head(topN).index)
    if top_slwbs:
        plt.figure(figsize=(12,6))
        x = pd.to_datetime(slwbs_counts.index)
        for col in top_slwbs:
            plt.plot(x, slwbs_counts[col].values, marker='o', label=str(col))
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        plt.xlabel('Date')
        plt.ylabel('Count')
        plt.title('SLWBS Trend (top %d)' % len(top_slwbs))
        plt.legend()
        plt.tight_layout()
        slwbs_png = zcop_output_folder / 'trend_slwbs_top10.png'
        plt.savefig(slwbs_png)
        plt.close()
        outputs.append(slwbs_png)
    else:
        print('No SLWBS categories to plot')
else:
    print('SLWBS column not found; skipping SLWBS trend')

# 2) Labour report trend
if labour_col:
    labour_counts = df.groupby(['DATE', labour_col]).size().unstack(fill_value=0)
    labour_counts.sort_index(inplace=True)
    labour_csv = zcop_output_folder / 'trend_labour_report.csv'
    labour_counts.to_csv(labour_csv)
    outputs.append(labour_csv)

    plt.figure(figsize=(12,6))
    x = pd.to_datetime(labour_counts.index)
    for col in labour_counts.columns:
        plt.plot(x, labour_counts[col].values, marker='o', label=str(col))
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.xticks(rotation=45)
    plt.xlabel('Date')
    plt.ylabel('Count')
    plt.title(f'Labour Report Trend ({labour_col})')
    plt.legend()
    plt.tight_layout()
    labour_png = zcop_output_folder / 'trend_labour_report.png'
    plt.savefig(labour_png)
    plt.close()
    outputs.append(labour_png)
else:
    print('Labour column missing; skipping labour trend')

# 3) Additions / deletions between consecutive dates
dates = sorted(df['DATE'].unique())
add_del_rows = []
for i in range(1, len(dates)):
    prev = dates[i-1]
    curr = dates[i]
    prev_codes = set(df.loc[df['DATE'] == prev, 'EMP_CODE'].unique())
    curr_codes = set(df.loc[df['DATE'] == curr, 'EMP_CODE'].unique())
    additions = curr_codes - prev_codes
    deletions = prev_codes - curr_codes

    # additions: get names from current date
    if additions:
        subs = df.loc[(df['DATE'] == curr) & (df['EMP_CODE'].isin(additions)), ['EMP_CODE','EMP_NAME']].drop_duplicates(subset=['EMP_CODE'])
        for _, r in subs.iterrows():
            add_del_rows.append({'prev_date': str(prev), 'curr_date': str(curr), 'change': 'addition', 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})
    # deletions: get names from previous date
    if deletions:
        subs = df.loc[(df['DATE'] == prev) & (df['EMP_CODE'].isin(deletions)), ['EMP_CODE','EMP_NAME']].drop_duplicates(subset=['EMP_CODE'])
        for _, r in subs.iterrows():
            add_del_rows.append({'prev_date': str(prev), 'curr_date': str(curr), 'change': 'deletion', 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})

# Save additions/deletions
if add_del_rows:
    ad_csv = zcop_output_folder / 'additions_deletions_by_date_pairs.csv'
    with open(ad_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['prev_date','curr_date','change','EMP_CODE','EMP_NAME'])
        writer.writeheader()
        writer.writerows(add_del_rows)
    outputs.append(ad_csv)

    # Also save separate additions and deletions lists (aggregate)
    additions_rows = [r for r in add_del_rows if r['change'] == 'addition']
    deletions_rows = [r for r in add_del_rows if r['change'] == 'deletion']
    additions_csv = zcop_output_folder / 'additions_all.csv'
    deletions_csv = zcop_output_folder / 'deletions_all.csv'
    with open(additions_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['DATE','EMP_CODE','EMP_NAME'])
        w.writeheader()
        for r in additions_rows:
            w.writerow({'DATE': r['curr_date'], 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})
    with open(deletions_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['DATE','EMP_CODE','EMP_NAME'])
        w.writeheader()
        for r in deletions_rows:
            w.writerow({'DATE': r['curr_date'], 'EMP_CODE': r['EMP_CODE'], 'EMP_NAME': r['EMP_NAME']})
    outputs.extend([additions_csv, deletions_csv])
else:
    print('No additions/deletions found between consecutive dates.')

# Print outputs
print('\nOutputs written:')
for p in outputs:
    print(' -', p)

print('\nDone.')
