#!/usr/bin/env python3
"""
ZCOP To-Date Report Generator - Create trend reports from accumulated metrics.

Usage:
  python generate_zcop_report.py [--days 7] [--format json|markdown|csv]

Generates:
  - Daily to-date aggregations
  - 7-day trends with day-over-day changes
  - Dimension analysis (DM, business line, client VP trends)
  - Anomaly tracking
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional
import csv


class ZCOPReportGenerator:
    """Generate trend reports from ZCOP metrics snapshots."""
    
    def __init__(self, metrics_file: str = None, days: int = 7):
        if not metrics_file:
            # Default location relative to script
            metrics_file = Path(__file__).parent.parent / 'data' / 'zcop-metrics.json'
        
        self.metrics_file = Path(metrics_file)
        self.days = days
        self.snapshots = []
        self.load_snapshots()
    
    def load_snapshots(self) -> bool:
        """Load snapshots from metrics file."""
        try:
            if not self.metrics_file.exists():
                print(f"ERROR: Metrics file not found: {self.metrics_file}")
                return False
            
            with open(self.metrics_file, 'r') as f:
                data = json.load(f)
                self.snapshots = data.get('snapshots', [])
                # Sort by date ascending for trend calculation
                self.snapshots.sort(key=lambda x: x['date'])
            
            return True
        except Exception as e:
            print(f"ERROR: Failed to load metrics: {e}")
            return False
    
    def get_recent_snapshots(self) -> List[Dict[str, Any]]:
        """Get snapshots from last N days."""
        if not self.snapshots:
            return []
        
        cutoff_date = (datetime.now() - timedelta(days=self.days)).strftime('%Y-%m-%d')
        return [s for s in self.snapshots if s['date'] >= cutoff_date]
    
    def calculate_trend(self, current: float, previous: Optional[float] = None) -> Dict[str, Any]:
        """Calculate change percentage and absolute change."""
        if previous is None or previous == 0:
            return {
                'value': current,
                'change': 0,
                'change_pct': 0,
                'direction': 'stable'
            }
        
        change = current - previous
        change_pct = (change / previous * 100) if previous != 0 else 0
        direction = 'up' if change > 0 else ('down' if change < 0 else 'stable')
        
        return {
            'value': current,
            'change': change,
            'change_pct': round(change_pct, 2),
            'direction': direction
        }
    
    def generate_daily_trends(self) -> List[Dict[str, Any]]:
        """Generate day-by-day trend data."""
        trends = []
        recent = self.get_recent_snapshots()
        
        for idx, snapshot in enumerate(recent):
            prev_snapshot = recent[idx - 1] if idx > 0 else None
            prev_billable = prev_snapshot['metrics']['billable']['hours'] if prev_snapshot else None
            prev_non_billable = prev_snapshot['metrics']['non_billable']['hours'] if prev_snapshot else None
            prev_nga = prev_snapshot['metrics']['nga']['allocation_count'] if prev_snapshot else None
            
            trend_day = {
                'date': snapshot['date'],
                'file': snapshot['file_source'],
                'billable': self.calculate_trend(
                    snapshot['metrics']['billable']['hours'],
                    prev_billable
                ),
                'non_billable': self.calculate_trend(
                    snapshot['metrics']['non_billable']['hours'],
                    prev_non_billable
                ),
                'nga': self.calculate_trend(
                    snapshot['metrics']['nga']['allocation_count'],
                    prev_nga
                ),
                'ramp_up': snapshot['metrics']['ramp_up']['count'],
                'ramp_down': snapshot['metrics']['ramp_down']['count'],
                'anomalies': snapshot.get('anomalies', [])
            }
            trends.append(trend_day)
        
        return trends
    
    def generate_aggregate_stats(self) -> Dict[str, Any]:
        """Generate aggregate statistics across period."""
        recent = self.get_recent_snapshots()
        if not recent:
            return {}
        
        total_billable = sum(s['metrics']['billable']['hours'] for s in recent)
        total_non_billable = sum(s['metrics']['non_billable']['hours'] for s in recent)
        total_nga = sum(s['metrics']['nga']['allocation_count'] for s in recent)
        total_ramp_up = sum(s['metrics']['ramp_up']['count'] for s in recent)
        total_ramp_down = sum(s['metrics']['ramp_down']['count'] for s in recent)
        
        total_hours = total_billable + total_non_billable
        billable_pct = (total_billable / total_hours * 100) if total_hours > 0 else 0
        
        return {
            'period_days': self.days,
            'snapshots_count': len(recent),
            'billable_total_hours': total_billable,
            'non_billable_total_hours': total_non_billable,
            'billable_percentage': round(billable_pct, 2),
            'nga_total_count': total_nga,
            'ramp_up_total': total_ramp_up,
            'ramp_down_total': total_ramp_down,
            'date_range': f"{recent[0]['date']} to {recent[-1]['date']}" if recent else "N/A"
        }
    
    def generate_dimension_analysis(self) -> Dict[str, Any]:
        """Analyze trends by dimension (DM, business line, client VP)."""
        recent = self.get_recent_snapshots()
        if not recent:
            return {}
        
        dm_trend = defaultdict(lambda: {'count': 0, 'billable_hours': 0, 'appearances': 0})
        bl_trend = defaultdict(lambda: {'count': 0, 'billable_hours': 0, 'appearances': 0})
        cv_trend = defaultdict(lambda: {'count': 0, 'billable_hours': 0, 'appearances': 0})
        
        # Aggregate across all snapshots
        for snapshot in recent:
            for dm, stats in snapshot['dimensions'].get('by_dm', {}).items():
                dm_trend[dm]['count'] += stats.get('count', 0)
                dm_trend[dm]['billable_hours'] += stats.get('billable_hours', 0)
                dm_trend[dm]['appearances'] += 1
            
            for bl, stats in snapshot['dimensions'].get('by_business_line', {}).items():
                bl_trend[bl]['count'] += stats.get('count', 0)
                bl_trend[bl]['billable_hours'] += stats.get('billable_hours', 0)
                bl_trend[bl]['appearances'] += 1
            
            for cv, stats in snapshot['dimensions'].get('by_client_vp', {}).items():
                cv_trend[cv]['count'] += stats.get('count', 0)
                cv_trend[cv]['billable_hours'] += stats.get('billable_hours', 0)
                cv_trend[cv]['appearances'] += 1
        
        # Sort by appearance frequency
        top_dms = sorted(dm_trend.items(), key=lambda x: x[1]['appearances'], reverse=True)[:5]
        top_bls = sorted(bl_trend.items(), key=lambda x: x[1]['appearances'], reverse=True)[:5]
        top_cvs = sorted(cv_trend.items(), key=lambda x: x[1]['appearances'], reverse=True)[:5]
        
        return {
            'top_dms': {k: dict(v) for k, v in top_dms},
            'top_business_lines': {k: dict(v) for k, v in top_bls},
            'top_client_vps': {k: dict(v) for k, v in top_cvs}
        }
    
    def generate_markdown_report(self) -> str:
        """Generate formatted markdown report."""
        trends = self.generate_daily_trends()
        stats = self.generate_aggregate_stats()
        dimensions = self.generate_dimension_analysis()
        
        report = f"""# ZCOP To-Date Analysis Report
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary Statistics ({stats.get('period_days', 'N/A')}-Day View)

| Metric | Value | Percentage |
|--------|-------|-----------|
| Billable Hours | {stats.get('billable_total_hours', 0):.1f} | {stats.get('billable_percentage', 0):.1f}% |
| Non-Billable Hours | {stats.get('non_billable_total_hours', 0):.1f} | {100 - stats.get('billable_percentage', 0):.1f}% |
| NGA Allocations | {stats.get('nga_total_count', 0)} | - |
| RD Total | {stats.get('rds_total_count', 0)} | - |
| Period | {stats.get('date_range', 'N/A')} | - |

## Daily Trends

"""
        for trend in trends:
            report += f"### {trend['date']}\n"
            report += f"- **Billable**: {trend['billable']['value']:.1f}h "
            if trend['billable']['change'] != 0:
                report += f"({trend['billable']['direction']} {abs(trend['billable']['change_pct']):.1f}%)"
            report += "\n"
            report += f"- **Non-Billable**: {trend['non_billable']['value']:.1f}h "
            if trend['non_billable']['change'] != 0:
                report += f"({trend['non_billable']['direction']} {abs(trend['non_billable']['change_pct']):.1f}%)"
            report += "\n"
            report += f"- **NGA**: {trend['nga']['value']} allocations\n"
            report += f"- **RDs**: {trend['rds']}\n"
            if trend['anomalies']:
                report += f"- **⚠️ Anomalies**: {', '.join(trend['anomalies'])}\n"
            report += "\n"
        
        # Dimension analysis
        report += "## Top Dimensions\n\n"
        
        report += "### Delivery Managers (Top 5)\n"
        for dm, stats in dimensions.get('top_dms', {}).items():
            report += f"- **{dm}**: {stats['count']} people, {stats['billable_hours']:.1f} billable hours (in {stats['appearances']} snapshots)\n"
        
        report += "\n### Business Lines (Top 5)\n"
        for bl, stats in dimensions.get('top_business_lines', {}).items():
            report += f"- **{bl}**: {stats['count']} people, {stats['billable_hours']:.1f} billable hours (in {stats['appearances']} snapshots)\n"
        
        report += "\n### Client VPs (Top 5)\n"
        for cv, stats in dimensions.get('top_client_vps', {}).items():
            report += f"- **{cv}**: {stats['count']} people, {stats['billable_hours']:.1f} billable hours (in {stats['appearances']} snapshots)\n"
        
        return report
    
    def generate_json_report(self) -> Dict[str, Any]:
        """Generate report as JSON."""
        return {
            'generated': datetime.now().isoformat(),
            'period_days': self.days,
            'aggregate_stats': self.generate_aggregate_stats(),
            'daily_trends': self.generate_daily_trends(),
            'dimension_analysis': self.generate_dimension_analysis()
        }
    
    def generate_csv_report(self) -> str:
        """Generate CSV report of daily trends."""
        trends = self.generate_daily_trends()
        
        output = []
        output.append(['Date', 'Billable Hours', 'Billable Change %', 'Non-Billable Hours', 'NGA Count', 'RDs', 'Anomalies'])
        
        for trend in trends:
            output.append([
                trend['date'],
                f"{trend['billable']['value']:.1f}",
                f"{trend['billable']['change_pct']:.2f}",
                f"{trend['non_billable']['value']:.1f}",
                str(trend['nga']['value']),
                str(trend['rds']),
                '; '.join(trend['anomalies']) if trend['anomalies'] else ''
            ])
        
        # Convert to CSV string
        from io import StringIO
        output_buffer = StringIO()
        writer = csv.writer(output_buffer)
        writer.writerows(output)
        return output_buffer.getvalue()


def main():
    days = 7
    format_type = 'markdown'
    save_output = False
    
    # Parse arguments
    for arg in sys.argv[1:]:
        if arg.startswith('--days'):
            try:
                days = int(arg.split('=')[1])
            except:
                pass
        elif arg.startswith('--format'):
            format_type = arg.split('=')[1].lower()
        elif arg == '--save':
            save_output = True
    
    generator = ZCOPReportGenerator(days=days)
    
    if not generator.snapshots:
        print("No snapshots available yet. Run the ZCOP Analysis Agent to collect data.")
        sys.exit(0)
    
    # Generate report based on format
    if format_type == 'json':
        report_data = generator.generate_json_report()
        output = json.dumps(report_data, indent=2)
    elif format_type == 'csv':
        output = generator.generate_csv_report()
    else:  # markdown (default)
        output = generator.generate_markdown_report()
    
    # Print to console
    print(output)
    
    # Save to file if requested
    if save_output:
        output_dir = Path(__file__).parent.parent.parent / 'Zcop Output'
        output_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if format_type == 'json':
            output_file = output_dir / f'zcop_report_{timestamp}.json'
        elif format_type == 'csv':
            output_file = output_dir / f'zcop_report_{timestamp}.csv'
        else:
            output_file = output_dir / f'zcop_report_{timestamp}.md'
        
        try:
            with open(output_file, 'w') as f:
                f.write(output)
            print(f"\n✓ Report saved to: {output_file}")
        except Exception as e:
            print(f"ERROR: Failed to save report: {e}")


if __name__ == '__main__':
    main()
