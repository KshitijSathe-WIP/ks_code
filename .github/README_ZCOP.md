# ZCOP Analysis Framework

Automated analysis and trend tracking for ZCOP (Zerobase Operational Cost and Profit) data.

## Overview

This framework provides:
1. **Custom Agent** (`zcop-analysis.agent.md`) - Specialized AI analysis of daily ZCOP files
2. **Parser Script** (`parse_zcop.py`) - Extract metrics from Excel files
3. **Report Generator** (`generate_zcop_report.py`) - Create trend reports from accumulated data
4. **Automated Hooks** - Auto-update metrics file on analysis completion

## Quick Start

### Using the ZCOP Analysis Agent (Recommended)

1. Open VS Code chat and select the **ZCOP Analysis** agent from the agent picker
2. Request analysis: *"Analyze the latest ZCOP file and show trends on billable, non-billable, NGA, and RDs across DMs, business lines, and client VPs"*
3. The agent will:
   - Find the latest ZCOP Excel file
   - Parse and extract key metrics
   - Compare against historical data
   - Update `.github/data/zcop-metrics.json` with new snapshot
   - Generate a formatted report

### Manual Usage

#### Parse a single ZCOP file:
```bash
python .github/scripts/parse_zcop.py "Zcop Analysis/TD Bank ZCOP 5-Feb-2026.xlsx"
```

#### Parse and automatically save to metrics file:
```bash
python .github/scripts/parse_zcop.py "Zcop Analysis/TD Bank ZCOP 5-Feb-2026.xlsx" --save-snapshot
```

#### Generate a trend report (last 7 days):
```bash
# Print to console
python .github/scripts/generate_zcop_report.py --days=7 --format=markdown

# Save to Zcop Output folder
python .github/scripts/generate_zcop_report.py --days=7 --format=json --save
```

Supported formats:
- `markdown` - Formatted report (default)
- `json` - Structured data for programmatic use
- `csv` - Spreadsheet-compatible format
- Use `--save` flag to automatically save to `Zcop Output/` folder

#### Export to Executive Excel Report:
```bash
python .github/scripts/export_zcop_excel.py --days=7
```

Outputs to `Zcop Output/` folder automatically.

Options:
- `--days=N` - Include last N days of data (default: 7)
- `--output=filename.xlsx` - Custom filename (saved to Zcop Output folder)

This creates a formatted Excel workbook with:
- **Executive Summary** - KPIs, trends, and top dimensions
- **Daily Trends** - Day-by-day breakdown with change indicators
- **Dimension Analysis** - Detailed view by DMs, business lines, client VPs
- **Raw Data** - Complete metrics for detailed review

## File Structure

```
.github/
├── agents/
│   └── zcop-analysis.agent.md       # Custom AI agent
├── data/
│   └── zcop-metrics.json            # Historical metrics snapshots
├── hooks/
│   └── zcop-auto-snapshot.json      # Auto-update hook
├── scripts/
│   ├── parse_zcop.py                # Extract metrics from Excel
│   ├── generate_zcop_report.py      # Create trend reports
│   └── export_zcop_excel.py         # Export to executive Excel
└── README_ZCOP.md                   # This file

Zcop Output/
├── zcop_report_20260209_143022.xlsx # Executive Excel reports
├── zcop_report_20260209_143045.json # JSON trend reports
├── zcop_report_20260209_143102.md   # Markdown reports
└── zcop_report_20260209_143118.csv  # CSV data exports
```

## Metrics Tracked

### Core Metrics
- **Billable Hours/Costs** - Revenue-generating work
- **Non-Billable Hours/Costs** - Internal, training, overhead
- **NGA Allocations** - Nearshore Global Academy resource assignments
- **RU (Ramp Up)** - Count of resources being increased/onboarded (billable growth)
- **RD (Ramp Down)** - Count of resources being decreased/offboarded (billable reduction)

### Dimensions
- **By DM** (Delivery Managers) - Headcount and utilization
- **By Business Line** - Allocation and hours distribution
- **By Client VP** - Account-level resource assignment

### Tracking
- **Daily Snapshots** - One entry per ZCOP file analyzed
- **Aggregations** - Running totals across period
- **Anomalies** - Automated flagging of unusual values
- **Trends** - Day-over-day change calculations

## Stored Data Format

`.github/data/zcop-metrics.json` stores:
```json
{
  "date": "2026-02-05",
  "file_source": "TD Bank ZCOP 5-Feb-2026.xlsx",
  "metrics": {
    "billable": {"hours": 1234.5, "cost": 987654},
    "non_billable": {"hours": 456.7, "cost": 234567},
    "nga": {"allocation_count": 12, "percentage_of_total": 3.4},
    "ramp_up": {"count": 15, "percentage_of_billable": 4.2},
    "ramp_down": {"count": 8, "percentage_of_billable": 2.1}
  },
  "dimensions": {
    "by_dm": {"DM_Name": {"count": 5, "billable_hours": 200}...},
    "by_business_line": {...},
    "by_client_vp": {...}
  },
  "anomalies": [],
  "notes": ""
}
```

## Workflow Example

### Day 1: Initial Analysis
```
$ # Upload new ZCOP file: TD Bank ZCOP 6-Feb-2026.xlsx
$ # Chat: "Analyze ZCOP for Feb 6"
→ Agent extracts metrics → Saves to zcop-metrics.json
→ Report: Shows initial baseline data
```

### Day 7: Trend View
```
$ # After 7 days of analysis
$ python .github/scripts/generate_zcop_report.py --days=7 --save
→ Creates: Zcop Output/zcop_report_20260209_143022.md
→ Shows trends, anomalies, top dimensions across 7-day period
→ Identifies patterns in billable, NGA, RD allocations
```

### Executive Reporting
```
$ # Generate formatted Excel for exec team
$ python .github/scripts/export_zcop_excel.py --days=7
→ Creates: Zcop Output/zcop_report_20260209_143022.xlsx
→ Professional Excel workbook with:
  • Executive Summary (KPIs, trends, top dimensions)
  • Daily Trends (day-by-day changes with color coding)
  • Dimension Analysis (top DMs, business lines, client VPs)
  • Raw Data (detailed metrics for deep dive)
```

## Customization

### Adding New Metrics
Edit `parse_zcop.py` to add patterns in `PATTERNS` dict:
```python
PATTERNS = {
    'your_metric': r'column_name_pattern',
    ...
}
```

### Adjusting Column Detection
The parser uses regex patterns to find columns by name. Customize in `parse_zcop.py`:
```python
billable_col = self.find_column_by_pattern(self.PATTERNS['billable'])
```

### Changing Anomaly Thresholds
Edit detection logic in `parse_zcop.py` `parse_metrics()` method.

## Troubleshooting

### "No openpyxl" error
```bash
pip install openpyxl
```

### Metrics file not updating
Ensure `.github/data/zcop-metrics.json` exists and is writable
Check that `--save-snapshot` flag is used with parse script

### Agent not finding ZCOP files
Verify file naming matches pattern: `TD Bank ZCOP [DATE].xlsx`
Check `Zcop Analysis/` directory exists and is accessible

### No trend data in reports
Reports require at least 2 snapshots to show trends
Run agent on multiple days to build history

## Integration with Git

Optional: Track metrics file in version control:
```bash
git add .github/data/zcop-metrics.json
git add .github/agents/zcop-analysis.agent.md
git add .github/scripts/
git commit -m "ZCOP analysis: Daily snapshot"
```

This creates an audit trail of metric changes over time.

## Executive Excel Reports

The `export_zcop_excel.py` script generates professional Excel workbooks for sharing with leadership.

### Sheets Included

1. **Executive Summary**
   - Key Performance Indicators (KPIs) with trend indicators
   - Billable %, NGA allocation, RD count
   - Top 3 Delivery Managers, Business Lines, Client VPs
   - Color-coded trends (green ↑, red ↓, neutral →)

2. **Daily Trends**
   - Day-by-day breakdown of all metrics
   - Change % with color coding (positive = green, negative = red)
   - Anomaly flagging
   - Source file tracking

3. **Dimension Analysis**
   - Detailed metrics by Delivery Manager (people, billable hours, appearance count)
   - Business Line distribution and allocation
   - Client VP assignments and resource distribution
   - Sorted by frequency for quick impact identification

4. **Raw Data**
   - Complete dataset for detailed analysis
   - Includes costs, percentages, RD breakdown
   - All anomalies highlighted
   - Suitable for drilling deeper if needed

### Usage Options

Generate standard 7-day executive report:
```bash
python .github/scripts/export_zcop_excel.py
```

Generate 30-day rolling analysis:
```bash
python .github/scripts/export_zcop_excel.py --days=30 --output=zcop_report_monthly.xlsx
```

Generate and save with custom filename:
```bash
python .github/scripts/export_zcop_excel.py --output="ZCOP_Report_Q1_2026.xlsx"
```

### Sharing Workflow

1. Collect daily data for desired period (run ZCOP Analysis Agent daily)
2. Generate Excel: `python .github/scripts/export_zcop_excel.py --days=7`
3. Open the generated `.xlsx` file in `Zcop Output/` folder (e.g., `zcop_report_20260209_143022.xlsx`)
4. Review Executive Summary sheet for at-a-glance insights
5. Use Daily Trends for pattern analysis
6. Reference Dimension Analysis for resource allocation review
7. Send to stakeholders via email or share drive

## Output Folder Organization

All generated reports are automatically saved to the `Zcop Output/` folder with timestamped filenames for easy tracking and archiving:

### File Types Generated

- **Excel Reports** (`zcop_report_*.xlsx`)
  - Generated by: `export_zcop_excel.py`
  - Purpose: Executive presentations and reviews
  - Sheets: Executive Summary, Daily Trends, Dimension Analysis, Raw Data
  - Share directly with leadership

- **JSON Reports** (`zcop_report_*.json`)
  - Generated by: `generate_zcop_report.py --format=json --save`
  - Purpose: Data analysis, dashboarding, programmatic access
  - Contains: Aggregate stats, daily trends, dimension analysis
  - Ideal for: Power BI, Tableau, custom analysis tools

- **Markdown Reports** (`zcop_report_*.md`)
  - Generated by: `generate_zcop_report.py --format=markdown --save`
  - Purpose: Documentation, email distribution
  - Contains: Formatted tables and trend narratives
  - Ideal for: Slack, email, wiki documentation

- **CSV Exports** (`zcop_report_*.csv`)
  - Generated by: `generate_zcop_report.py --format=csv --save`
  - Purpose: Spreadsheet import, data analysis
  - Contains: Day-by-day breakdown
  - Ideal for: Additional pivot tables, historical tracking

### File Naming Convention

Files use ISO timestamp format for sorting: `zcop_report_YYYYMMDD_HHMMSS.ext`

Example: `zcop_report_20260209_143022.xlsx`
- Date: 2026-02-09
- Time: 14:30:22
- Naturally sorts in chronological order

### Storage & Retention

- Files persist in `Zcop Output/` for historical reference
- Organize by date if needed (e.g., move old reports to archive folder)
- Optional: Add `.gitignore` if tracking only metrics, not outputs
