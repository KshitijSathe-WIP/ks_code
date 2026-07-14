"""Check sheet names in Excel file."""
import pandas as pd
from pathlib import Path

excel_file = Path(r"C:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\ServiceMgmt\incident-rca-foundry\data\IncidentRCAKnowledge.xlsx")

# Read Excel file and list sheet names
xl = pd.ExcelFile(excel_file)
print("Sheet names in Excel file:")
for sheet in xl.sheet_names:
    print(f"  - {sheet}")
    
# Also show first few rows of each sheet
for sheet in xl.sheet_names:
    print(f"\n--- {sheet} ---")
    df = pd.read_excel(excel_file, sheet_name=sheet, nrows=3)
    print(df.columns.tolist())
