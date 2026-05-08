#!/usr/bin/env python3
"""
ZCOP Dashboard Generator
========================
Reads DATA_combined.csv and RU-RD_combined.csv, then writes a single
self-contained HTML file (Zcop Output/dashboard.html) and opens it.

No third-party packages required — uses only Python built-ins.

Run:
    python generate_dashboard.py
"""

import csv
import io
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_CSV = BASE_DIR / "Zcop Output" / "DATA_combined.csv"
RURD_CSV = BASE_DIR / "Zcop Output" / "RU-RD_combined.csv"
OUT_HTML = BASE_DIR / "Zcop Output" / "dashboard.html"

# ── helpers ────────────────────────────────────────────────────────────────────
def _str(v):
    return str(v).strip() if v else ""

def _num(v):
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0

def _date(v):
    raw = _str(v)[:10]
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
    except Exception:
        return ""

# ── read DATA_combined.csv ─────────────────────────────────────────────────────
def read_data():
    if not DATA_CSV.exists():
        print(f"ERROR: {DATA_CSV} not found"); sys.exit(1)

    rows = []
    labour_col = None

    with open(DATA_CSV, encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            for h in reader.fieldnames:
                if "labour" in h.lower():
                    labour_col = h
                    break

        for row in reader:
            d = _date(row.get("LOAD_DATE", ""))
            if not d:
                continue
            rows.append({
                "date":         d,
                "emp_code":     _str(row.get("EMP_CODE",          "")),
                "emp_name":     _str(row.get("EMP_NAME",          "")),
                "billability":  _str(row.get("BILLABILITY_STATUS","")) or "Unknown",
                "ons_off":      _str(row.get("ONS_OFF_FLAG",      "")) or "Unknown",
                "sldu":         _str(row.get("SLDU",              "")) or "Unknown",
                "band":         _str(row.get("CAREER_BAND",       "")) or "Unknown",
                "tm":           _str(row.get("TM_NAME",           "")) or "Unknown",
                "pm":           _str(row.get("PM_NAME",           "")) or "Unknown",
                "service_line": _str(row.get("SERVICE_LINE",      "")) or "Unknown",
                "slwbs":        _str(row.get("SLWBS",             "")) or "Unknown",
                "city":         _str(row.get("DERIVED_EMP_CITY",  "")),
                "net_billed":   _num(row.get("NET_BILLED",        "0")),
                "labour":       _str(row.get(labour_col, "") if labour_col else "") or "Unknown",
            })

    print(f"DATA rows loaded: {len(rows)}")
    return rows, labour_col or "Labour Report"

# ── read RU-RD_combined.csv ────────────────────────────────────────────────────
def read_rurd():
    if not RURD_CSV.exists():
        print(f"WARNING: {RURD_CSV} not found – RU/RD tab will be empty")
        return []

    with open(RURD_CSV, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    # Row 0 is a garbage summary row – skip it
    reader = csv.DictReader(io.StringIO("".join(lines[1:])))
    rows = []
    for row in reader:
        d = _date(row.get("LOAD_DATE", ""))
        if not d:
            continue
        rows.append({
            "date":         d,
            "emp_code":     _str(row.get("EMP_CODE",      "")),
            "emp_name":     _str(row.get("EMP_NAME",      "")),
            "rurd":         _str(row.get("RU/RD",         "")) or "Unknown",
            "count":        _num(row.get("Count",         "0")),
            "service_line": _str(row.get("Service Line",  "")) or "Unknown",
            "tm":           _str(row.get("TM_NAME",       "")) or "Unknown",
            "portfolio":    _str(row.get("Portfolio",     "")) or "Unknown",
            "ons_off":      _str(row.get("ONS_OFF_FLAG",  "")) or "Unknown",
            "remarks":      _str(row.get("Remarks",       "")),
        })

    print(f"RU/RD rows loaded: {len(rows)}")
    return rows

# ── assemble HTML ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ZCOP Dashboard — TD Bank</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;font-size:13px;background:#f4f6fb;color:#222}
#layout{display:flex;min-height:100vh}

/* ── sidebar ── */
#sidebar{width:230px;min-width:230px;background:#1a2035;color:#cdd5e0;
  display:flex;flex-direction:column;padding:0;overflow-y:auto;flex-shrink:0}
#sidebar-header{padding:16px 14px 10px;background:#131929;font-size:15px;font-weight:700;
  color:#fff;border-bottom:1px solid #283048}
.filter-section{padding:10px 12px 4px}
.filter-label{font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  color:#8899aa;margin-bottom:4px;display:block}
.filter-section select,.filter-section input[type=date]{
  width:100%;background:#253048;border:1px solid #374560;color:#cdd5e0;
  border-radius:4px;padding:4px 6px;font-size:12px;outline:none}
.filter-section select[multiple]{height:80px}
.filter-section select:focus,.filter-section input:focus{border-color:#4a90e2}
#btn-reset{margin:8px 12px 12px;padding:6px;background:#4a90e2;color:#fff;
  border:none;border-radius:4px;cursor:pointer;font-size:12px;width:calc(100% - 24px)}
#btn-reset:hover{background:#357abd}
#sidebar-footer{margin-top:auto;padding:10px 12px;font-size:10px;color:#5a6a80;
  border-top:1px solid #283048}

/* ── main ── */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden}
#topbar{background:#fff;padding:10px 20px;border-bottom:1px solid #dde3ee;
  display:flex;align-items:center;gap:12px}
#topbar h1{font-size:17px;font-weight:700;color:#1a2035}
#topbar small{color:#888;font-size:11px}

/* ── tabs ── */
#tab-bar{display:flex;background:#fff;border-bottom:2px solid #dde3ee;padding:0 16px}
.tab-btn{padding:9px 16px;border:none;background:none;cursor:pointer;
  font-size:13px;color:#666;border-bottom:2px solid transparent;margin-bottom:-2px;
  transition:color .15s,border-color .15s}
.tab-btn:hover{color:#1a2035}
.tab-btn.active{color:#1a2035;font-weight:600;border-bottom-color:#4a90e2}

/* ── content ── */
#content{flex:1;overflow-y:auto;padding:16px}
.tab-pane{display:none}.tab-pane.active{display:block}

/* ── KPI cards ── */
.kpi-row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
.kpi-card{background:#fff;border-radius:8px;padding:12px 16px;min-width:120px;
  flex:1;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.kpi-card .kpi-val{font-size:22px;font-weight:700;color:#1a2035}
.kpi-card .kpi-lbl{font-size:11px;color:#888;margin-top:2px}
.kpi-card .kpi-delta{font-size:11px;font-weight:600}
.kpi-card .kpi-delta.up{color:#2ca02c}.kpi-card .kpi-delta.dn{color:#d62728}

/* ── chart grid ── */
.chart-row{display:grid;gap:14px;margin-bottom:14px}
.chart-row.cols-1{grid-template-columns:1fr}
.chart-row.cols-2{grid-template-columns:1fr 1fr}
.chart-row.cols-3{grid-template-columns:1fr 1fr 1fr}
.chart-card{background:#fff;border-radius:8px;padding:14px;
  box-shadow:0 1px 4px rgba(0,0,0,.08);position:relative}
.chart-card canvas{max-height:280px}

/* ── table ── */
.tbl-wrap{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.08);
  overflow:hidden;margin-bottom:12px}
.tbl-toolbar{padding:10px 14px;display:flex;align-items:center;gap:10px;
  border-bottom:1px solid #eee;background:#fafbfc}
.tbl-toolbar input{padding:5px 10px;border:1px solid #ddd;border-radius:4px;
  font-size:12px;outline:none;width:240px}
.tbl-toolbar label{font-size:12px;display:flex;align-items:center;gap:5px;cursor:pointer}
#people-count{margin-left:auto;font-size:11px;color:#888}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{background:#f0f4fa;padding:7px 10px;text-align:left;
  font-weight:600;color:#445;border-bottom:2px solid #dde3ee;
  position:sticky;top:0}
tbody tr:hover{background:#f8f9fe}
tbody td{padding:6px 10px;border-bottom:1px solid #f0f0f0}
.badge-green{color:#2a7a2a;font-weight:600}
.badge-red{color:#c0392b;font-weight:600}
.badge-orange{color:#b65a00;font-weight:600}
.tbl-pager{padding:8px 14px;font-size:11px;color:#888;
  display:flex;align-items:center;gap:8px}
.tbl-pager button{padding:3px 10px;border:1px solid #ddd;border-radius:3px;
  background:#fff;cursor:pointer;font-size:11px}
.tbl-pager button:hover{background:#f0f4fa}

/* download btn */
#btn-download{padding:6px 14px;background:#2ca02c;color:#fff;border:none;
  border-radius:4px;cursor:pointer;font-size:12px}
#btn-download:hover{background:#1e7b1e}

@media(max-width:900px){
  .chart-row.cols-2,.chart-row.cols-3{grid-template-columns:1fr}
  #sidebar{display:none}
}
</style>
</head>
<body>
<div id="layout">

<!-- ── SIDEBAR ── -->
<div id="sidebar">
  <div id="sidebar-header">📊 ZCOP Filters</div>

  <div class="filter-section">
    <label class="filter-label">Date From</label>
    <input type="date" id="date-start" class="filter-control"/>
  </div>
  <div class="filter-section">
    <label class="filter-label">Date To</label>
    <input type="date" id="date-end" class="filter-control"/>
  </div>
  <div class="filter-section">
    <label class="filter-label">Service Line / DU</label>
    <select id="filter-sldu" multiple class="filter-control"></select>
  </div>
  <div class="filter-section">
    <label class="filter-label">Talent Manager</label>
    <select id="filter-tm" multiple class="filter-control"></select>
  </div>
  <div class="filter-section">
    <label class="filter-label">Onsite / Offshore</label>
    <select id="filter-ons-off" multiple class="filter-control"></select>
  </div>
  <div class="filter-section">
    <label class="filter-label">Billability Status</label>
    <select id="filter-billability" multiple class="filter-control"></select>
  </div>
  <div class="filter-section">
    <label class="filter-label">Career Band</label>
    <select id="filter-band" multiple class="filter-control"></select>
  </div>

  <button id="btn-reset" onclick="resetFilters()">⟳ Reset All Filters</button>
  <div id="sidebar-footer">Hold Ctrl / ⌘ to multi-select</div>
</div>

<!-- ── MAIN ── -->
<div id="main">
  <div id="topbar">
    <h1>ZCOP Dashboard — TD Bank</h1>
    <small id="gen-time"></small>
    <div style="margin-left:auto">
      <button id="btn-download" onclick="downloadCSV()">⬇ Export Filtered</button>
    </div>
  </div>

  <div id="tab-bar">
    <button class="tab-btn active" id="tab-btn-overview" onclick="switchTab('overview')">📈 Overview</button>
    <button class="tab-btn" id="tab-btn-rurd"     onclick="switchTab('rurd')">🔄 RU / RD</button>
    <button class="tab-btn" id="tab-btn-sl"       onclick="switchTab('sl')">📂 Service Lines</button>
    <button class="tab-btn" id="tab-btn-lr"       onclick="switchTab('lr')">📋 Labour Report</button>
    <button class="tab-btn" id="tab-btn-people"   onclick="switchTab('people')">🔍 People</button>
  </div>

  <div id="content">

    <!-- OVERVIEW TAB -->
    <div class="tab-pane active" id="tab-overview">
      <div class="kpi-row">
        <div class="kpi-card"><div class="kpi-val" id="kpi-total">—</div><div class="kpi-lbl">Total HC <span id="kpi-date" style="color:#aaa"></span></div><div class="kpi-delta" id="kpi-delta"></div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-billable">—</div><div class="kpi-lbl">Billable HC</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-nonbill">—</div><div class="kpi-lbl">Non-Billable HC</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-billpct">—</div><div class="kpi-lbl">Billable %</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-onsite">—</div><div class="kpi-lbl">Onsite HC</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-offshore">—</div><div class="kpi-lbl">Offshore HC</div></div>
      </div>
      <div class="chart-row cols-1">
        <div class="chart-card"><canvas id="chart-hc-trend"></canvas></div>
      </div>
      <div class="chart-row cols-2">
        <div class="chart-card"><canvas id="chart-billability"></canvas></div>
        <div class="chart-card"><canvas id="chart-ons-off"></canvas></div>
      </div>
    </div>

    <!-- RU/RD TAB -->
    <div class="tab-pane" id="tab-rurd">
      <div class="kpi-row">
        <div class="kpi-card"><div class="kpi-val" id="kpi-ru" style="color:#2ca02c">—</div><div class="kpi-lbl">Total RU (Additions)</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-rd" style="color:#d62728">—</div><div class="kpi-lbl">Total RD (Removals)</div></div>
        <div class="kpi-card"><div class="kpi-val" id="kpi-net">—</div><div class="kpi-lbl">Net Change</div></div>
      </div>
      <div class="chart-row cols-2">
        <div class="chart-card"><canvas id="chart-rurd-daily"></canvas></div>
        <div class="chart-card"><canvas id="chart-rurd-cum"></canvas></div>
      </div>
      <div class="chart-row cols-2">
        <div class="chart-card"><canvas id="chart-rd-reasons"></canvas></div>
        <div class="chart-card"><canvas id="chart-rurd-portfolio"></canvas></div>
      </div>
      <div class="chart-row cols-1">
        <div class="chart-card"><canvas id="chart-rurd-tm"></canvas></div>
      </div>
      <div class="tbl-wrap">
        <div class="tbl-toolbar"><b>RU/RD Detail</b></div>
        <div style="overflow-x:auto;max-height:300px;overflow-y:auto">
          <table><thead><tr><th>Date</th><th>Emp Code</th><th>Name</th><th>RU/RD</th><th>Service Line</th><th>TM</th><th>Portfolio</th><th>Ons/Off</th><th>Remarks</th></tr></thead>
          <tbody id="rurd-tbody"></tbody></table>
        </div>
      </div>
    </div>

    <!-- SERVICE LINES TAB -->
    <div class="tab-pane" id="tab-sl">
      <div class="chart-row cols-2">
        <div class="chart-card" style="min-height:320px"><canvas id="chart-sldu"></canvas></div>
        <div class="chart-card"><canvas id="chart-sl-pie"></canvas></div>
      </div>
      <div class="chart-row cols-1">
        <div class="chart-card"><canvas id="chart-sldu-trend"></canvas></div>
      </div>
      <div class="chart-row cols-1">
        <div class="chart-card" style="min-height:380px"><canvas id="chart-slwbs"></canvas></div>
      </div>
      <div class="chart-row cols-2">
        <div class="chart-card"><canvas id="chart-band"></canvas></div>
        <div class="chart-card" style="min-height:320px"><canvas id="chart-tm"></canvas></div>
      </div>
    </div>

    <!-- LABOUR REPORT TAB -->
    <div class="tab-pane" id="tab-lr">
      <div id="lr-col-name" style="font-size:11px;color:#888;margin-bottom:8px"></div>
      <div class="chart-row cols-2">
        <div class="chart-card"><canvas id="chart-lr-trend"></canvas></div>
        <div class="chart-card"><canvas id="chart-lr-pie"></canvas></div>
      </div>
      <div class="chart-row cols-1">
        <div class="chart-card"><canvas id="chart-nilr-trend"></canvas></div>
      </div>
      <div class="chart-row cols-2">
        <div class="chart-card" style="min-height:320px"><canvas id="chart-nilr-sldu"></canvas></div>
        <div class="chart-card"><canvas id="chart-nilr-tm"></canvas></div>
      </div>
    </div>

    <!-- PEOPLE TAB -->
    <div class="tab-pane" id="tab-people">
      <div class="tbl-wrap">
        <div class="tbl-toolbar">
          <input type="text" id="people-search" placeholder="🔍  Search name or EMP code…" oninput="renderPeople()"/>
          <label><input type="checkbox" id="people-show-all" onchange="renderPeople()"/> Show all dates</label>
          <span id="people-count" style="margin-left:auto;font-size:11px;color:#888"></span>
        </div>
        <div style="overflow-x:auto;max-height:500px;overflow-y:auto">
          <table>
            <thead><tr>
              <th>Date</th><th>EMP Code</th><th>Name</th><th>Band</th>
              <th>SLDU</th><th>TM</th><th>PM</th><th>Billability</th>
              <th>Ons/Off</th><th>City</th><th>Labour Report</th>
            </tr></thead>
            <tbody id="people-tbody"></tbody>
          </table>
        </div>
        <div class="tbl-pager">
          <button onclick="changePage(-1)">◀ Prev</button>
          <span id="people-pagination"></span>
          <button onclick="changePage(1)">Next ▶</button>
          <button id="btn-download-people" onclick="downloadCSV()" style="margin-left:auto;padding:4px 12px;background:#2ca02c;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px">⬇ Download</button>
        </div>
      </div>
    </div>

  </div><!-- /#content -->
</div><!-- /#main -->
</div><!-- /#layout -->

<!-- Chart.js from CDN -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>

<script>
// ── EMBEDDED DATA ───────────────────────────────────────────────────────────
const DATA = __DATA_JSON__;
const RURD = __RURD_JSON__;
const LABOUR_COL = __LABOUR_COL_JSON__;
const GEN_TIME   = "__GEN_TIME__";

// ── STATE ───────────────────────────────────────────────────────────────────
const charts = {};
let activeTab = 'overview';
let peoplePage = 1;
const PAGE_SIZE = 200;

// ── PALETTE ─────────────────────────────────────────────────────────────────
const PALETTE = [
  '#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
  '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf',
  '#aec7e8','#ffbb78','#98df8a','#ff9896','#c5b0d5',
];

// ── HELPERS ─────────────────────────────────────────────────────────────────
function uniq(arr){return [...new Set(arr)].filter(Boolean).sort()}
function countBy(arr,key){
  return arr.reduce((a,r)=>{const k=r[key]||'Unknown';a[k]=(a[k]||0)+1;return a},{})
}
function getDates(arr){return uniq(arr.map(r=>r.date))}

function mkChart(id, cfg){
  if(charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if(!el) return;
  charts[id] = new Chart(el, cfg);
}

// ── FILTERS ─────────────────────────────────────────────────────────────────
function getFilters(){
  return {
    ds: document.getElementById('date-start').value,
    de: document.getElementById('date-end').value,
    sldu: msVal('filter-sldu'),
    tm:   msVal('filter-tm'),
    oo:   msVal('filter-ons-off'),
    bl:   msVal('filter-billability'),
    bd:   msVal('filter-band'),
  }
}
function msVal(id){
  const el=document.getElementById(id);
  if(!el) return [];
  return Array.from(el.selectedOptions).map(o=>o.value);
}

function filterData(){
  const f=getFilters();
  return DATA.filter(r=>{
    if(f.ds && r.date<f.ds) return false;
    if(f.de && r.date>f.de) return false;
    if(f.sldu.length && !f.sldu.includes(r.sldu)) return false;
    if(f.tm.length   && !f.tm.includes(r.tm))   return false;
    if(f.oo.length   && !f.oo.includes(r.ons_off)) return false;
    if(f.bl.length   && !f.bl.includes(r.billability)) return false;
    if(f.bd.length   && !f.bd.includes(r.band))  return false;
    return true;
  });
}
function filterRURD(){
  const f=getFilters();
  return RURD.filter(r=>{
    if(f.ds && r.date<f.ds) return false;
    if(f.de && r.date>f.de) return false;
    if(f.sldu.length && !f.sldu.includes(r.service_line)) return false;
    if(f.tm.length   && !f.tm.includes(r.tm))   return false;
    if(f.oo.length   && !f.oo.includes(r.ons_off)) return false;
    return true;
  });
}

// ── OVERVIEW ─────────────────────────────────────────────────────────────────
function renderOverview(){
  const fd=filterData(), dates=getDates(fd);
  const ld=dates[dates.length-1]||'';
  const snap=fd.filter(r=>r.date===ld);
  const prev=dates.length>=2?fd.filter(r=>r.date===dates[dates.length-2]):[];

  const tot=snap.length, bill=snap.filter(r=>r.billability==='B').length;
  const nb=tot-bill, pct=tot?(bill/tot*100).toFixed(1)+'%':'0%';
  const dlt=tot-prev.length;
  const ons=snap.filter(r=>/ONS|ODR/i.test(r.ons_off)).length;
  const off=snap.filter(r=>/OFF/i.test(r.ons_off)).length;

  document.getElementById('kpi-total').textContent=tot;
  document.getElementById('kpi-date').textContent=ld?'('+ld+')':'';
  const deltaEl=document.getElementById('kpi-delta');
  deltaEl.textContent=dlt===0?'':(dlt>0?'+':'')+dlt+' vs prev';
  deltaEl.className='kpi-delta '+(dlt>0?'up':dlt<0?'dn':'');
  document.getElementById('kpi-billable').textContent=bill;
  document.getElementById('kpi-nonbill').textContent=nb;
  document.getElementById('kpi-billpct').textContent=pct;
  document.getElementById('kpi-onsite').textContent=ons;
  document.getElementById('kpi-offshore').textContent=off;

  // HC trend
  mkChart('chart-hc-trend',{type:'line',data:{
    labels:dates,
    datasets:[{label:'Headcount',data:dates.map(d=>fd.filter(r=>r.date===d).length),
      borderColor:'#1f77b4',backgroundColor:'rgba(31,119,180,.1)',fill:true,
      tension:.3,pointRadius:5}]
  },options:{responsive:true,plugins:{title:{display:true,text:'Daily Headcount Trend'}},
    scales:{y:{beginAtZero:false}},interaction:{mode:'index'}}});

  // Billable stacked
  const bSts=uniq(fd.map(r=>r.billability));
  const bMap={'B':'#2ca02c','NB':'#d62728','S':'#ff7f0e','F':'#9467bd'};
  mkChart('chart-billability',{type:'bar',data:{labels:dates,
    datasets:bSts.map((s,i)=>({label:s,backgroundColor:bMap[s]||PALETTE[i],
      data:dates.map(d=>fd.filter(r=>r.date===d&&r.billability===s).length)}))
  },options:{responsive:true,plugins:{title:{display:true,text:'Billable vs Non-Billable'},
    legend:{position:'top'}},scales:{x:{stacked:true},y:{stacked:true}},interaction:{mode:'index'}}});

  // Ons/Off stacked
  const ooVals=uniq(fd.map(r=>r.ons_off));
  mkChart('chart-ons-off',{type:'bar',data:{labels:dates,
    datasets:ooVals.map((s,i)=>({label:s,backgroundColor:PALETTE[i],
      data:dates.map(d=>fd.filter(r=>r.date===d&&r.ons_off===s).length)}))
  },options:{responsive:true,plugins:{title:{display:true,text:'Onsite vs Offshore'},
    legend:{position:'top'}},scales:{x:{stacked:true},y:{stacked:true}},interaction:{mode:'index'}}});
}

// ── RU/RD ─────────────────────────────────────────────────────────────────────
function renderRURD(){
  const fr=filterRURD(), dates=getDates(fr);
  const ru=fr.filter(r=>r.rurd==='RU').length;
  const rd=fr.filter(r=>r.rurd==='RD').length;
  const net=ru-rd;
  document.getElementById('kpi-ru').textContent=ru;
  document.getElementById('kpi-rd').textContent=rd;
  const netEl=document.getElementById('kpi-net');
  netEl.textContent=(net>=0?'+':'')+net;
  netEl.style.color=net>0?'#2ca02c':net<0?'#d62728':'#444';

  // Daily bar
  mkChart('chart-rurd-daily',{type:'bar',data:{labels:dates,datasets:[
    {label:'RU',backgroundColor:'#2ca02c',data:dates.map(d=>fr.filter(r=>r.date===d&&r.rurd==='RU').length)},
    {label:'RD',backgroundColor:'#d62728',data:dates.map(d=>fr.filter(r=>r.date===d&&r.rurd==='RD').length)},
  ]},options:{responsive:true,plugins:{title:{display:true,text:'Daily RU / RD Count'}},interaction:{mode:'index'}}});

  // Cumulative
  let cum=0;
  const cumD=dates.map(d=>{
    cum+=fr.filter(r=>r.date===d).reduce((s,r)=>s+r.count,0); return cum;
  });
  mkChart('chart-rurd-cum',{type:'line',data:{labels:dates,datasets:[{
    label:'Cumulative Net',data:cumD,borderColor:'#9467bd',
    backgroundColor:'rgba(148,103,189,.15)',fill:true,tension:.3,pointRadius:5
  }]},options:{responsive:true,plugins:{title:{display:true,text:'Cumulative Net HC Change'}}}});

  // RD Reasons pie
  const rdArr=fr.filter(r=>r.rurd==='RD'&&r.remarks);
  const rsnMap=countBy(rdArr,'remarks');
  const rsnL=Object.keys(rsnMap).filter(k=>k!=='');
  if(rsnL.length){
    mkChart('chart-rd-reasons',{type:'pie',data:{labels:rsnL,
      datasets:[{data:rsnL.map(k=>rsnMap[k]),backgroundColor:PALETTE}]
    },options:{responsive:true,plugins:{title:{display:true,text:'RD Reasons Breakdown'},
      legend:{position:'right'}}}});
  }

  // Portfolio bar
  const ports=uniq(fr.map(r=>r.portfolio));
  mkChart('chart-rurd-portfolio',{type:'bar',data:{labels:ports,datasets:[
    {label:'RU',backgroundColor:'#2ca02c',data:ports.map(p=>fr.filter(r=>r.portfolio===p&&r.rurd==='RU').length)},
    {label:'RD',backgroundColor:'#d62728',data:ports.map(p=>fr.filter(r=>r.portfolio===p&&r.rurd==='RD').length)},
  ]},options:{responsive:true,plugins:{title:{display:true,text:'RU/RD by Portfolio'}},
    scales:{x:{ticks:{maxRotation:45}}}}});

  // TM bar (top 15)
  const tmMap=countBy(fr,'tm');
  const top15=Object.entries(tmMap).sort((a,b)=>b[1]-a[1]).slice(0,15).reverse();
  mkChart('chart-rurd-tm',{type:'bar',data:{labels:top15.map(e=>e[0]),
    datasets:[{label:'Total Movements',backgroundColor:'#1f77b4',data:top15.map(e=>e[1])}]
  },options:{indexAxis:'y',responsive:true,
    plugins:{title:{display:true,text:'Top 15 TMs — Total RU+RD Movements'}}}});

  // Table
  const tbody=document.getElementById('rurd-tbody');
  tbody.innerHTML='';
  const show=fr.slice().sort((a,b)=>b.date.localeCompare(a.date)).slice(0,500);
  show.forEach(r=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${r.date}</td><td>${r.emp_code}</td><td>${r.emp_name}</td>
      <td class="${r.rurd==='RU'?'badge-green':'badge-red'}">${r.rurd}</td>
      <td>${r.service_line}</td><td>${r.tm}</td><td>${r.portfolio}</td>
      <td>${r.ons_off}</td><td>${r.remarks}</td>`;
    tbody.appendChild(tr);
  });
}

// ── SERVICE LINES ──────────────────────────────────────────────────────────────
function renderServiceLines(){
  const fd=filterData(), dates=getDates(fd);
  const ld=dates[dates.length-1]||'';
  const snap=fd.filter(r=>r.date===ld);

  // SLDU bar (horizontal)
  const slduMap=countBy(snap,'sldu');
  const slduE=Object.entries(slduMap).sort((a,b)=>a[1]-b[1]);
  mkChart('chart-sldu',{type:'bar',data:{labels:slduE.map(e=>e[0]),
    datasets:[{label:'HC',backgroundColor:'#1f77b4',data:slduE.map(e=>e[1])}]
  },options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{title:{display:true,text:`HC by Service Line DU (${ld})`}}}});

  // SL pie
  const slMap=countBy(snap,'service_line');
  const slL=Object.keys(slMap);
  mkChart('chart-sl-pie',{type:'pie',data:{labels:slL,
    datasets:[{data:slL.map(k=>slMap[k]),backgroundColor:PALETTE}]
  },options:{responsive:true,plugins:{title:{display:true,text:`Service Line Distribution (${ld})`},
    legend:{position:'right'}}}});

  // SLDU trend (top 10)
  const all=countBy(fd,'sldu');
  const top10=Object.entries(all).sort((a,b)=>b[1]-a[1]).slice(0,10).map(e=>e[0]);
  mkChart('chart-sldu-trend',{type:'line',data:{labels:dates,
    datasets:top10.map((s,i)=>({label:s,borderColor:PALETTE[i],backgroundColor:'transparent',
      data:dates.map(d=>fd.filter(r=>r.date===d&&r.sldu===s).length),
      tension:.3,pointRadius:4}))
  },options:{responsive:true,plugins:{title:{display:true,text:'Top 10 Service Line DU — HC Trend'},
    legend:{position:'top'}},interaction:{mode:'index'}}});

  // SLWBS bar (top 15)
  const wbsMap=countBy(snap,'slwbs');
  const top15wbs=Object.entries(wbsMap).sort((a,b)=>b[1]-a[1]).slice(0,15).reverse();
  mkChart('chart-slwbs',{type:'bar',data:{labels:top15wbs.map(e=>e[0]),
    datasets:[{label:'HC',backgroundColor:'#17becf',data:top15wbs.map(e=>e[1])}]
  },options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{title:{display:true,text:`Top 15 SLWBS (${ld})`}}}});

  // Career Band
  const bandMap=countBy(snap,'band');
  const bandL=Object.keys(bandMap).sort((a,b)=>bandMap[b]-bandMap[a]);
  mkChart('chart-band',{type:'bar',data:{labels:bandL,
    datasets:[{label:'HC',backgroundColor:'#ff7f0e',data:bandL.map(k=>bandMap[k])}]
  },options:{responsive:true,plugins:{title:{display:true,text:`Career Band Distribution (${ld})`}},
    scales:{x:{ticks:{maxRotation:30}}}}});

  // TM HC (top 15)
  const tmMap=countBy(snap,'tm');
  const top15tm=Object.entries(tmMap).sort((a,b)=>b[1]-a[1]).slice(0,15).reverse();
  mkChart('chart-tm',{type:'bar',data:{labels:top15tm.map(e=>e[0]),
    datasets:[{label:'HC',backgroundColor:'#2ca02c',data:top15tm.map(e=>e[1])}]
  },options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{title:{display:true,text:`Top 15 TMs by HC (${ld})`}}}});
}

// ── LABOUR REPORT ──────────────────────────────────────────────────────────────
function renderLabourReport(){
  const fd=filterData(), dates=getDates(fd);
  const ld=dates[dates.length-1]||'';
  const snap=fd.filter(r=>r.date===ld);
  document.getElementById('lr-col-name').textContent='Labour column: '+LABOUR_COL;

  const lrMap={'Present in LR':'#2ca02c','Not in LR':'#d62728','Delivery Team':'#ff7f0e'};
  const lSts=uniq(fd.map(r=>r.labour));

  // Trend stacked bar
  mkChart('chart-lr-trend',{type:'bar',data:{labels:dates,
    datasets:lSts.map((s,i)=>({label:s,backgroundColor:lrMap[s]||PALETTE[i],
      data:dates.map(d=>fd.filter(r=>r.date===d&&r.labour===s).length)}))
  },options:{responsive:true,plugins:{title:{display:true,text:'Labour Report Status — Daily Trend'}},
    scales:{x:{stacked:true},y:{stacked:true}},interaction:{mode:'index'}}});

  // Pie latest
  const lMap=countBy(snap,'labour');
  const lL=Object.keys(lMap);
  mkChart('chart-lr-pie',{type:'pie',data:{labels:lL,
    datasets:[{data:lL.map(k=>lMap[k]),backgroundColor:lL.map(k=>lrMap[k]||'#9467bd')}]
  },options:{responsive:true,plugins:{title:{display:true,text:`Labour Report Status (${ld})`},
    legend:{position:'right'}}}});

  // Not in LR trend
  mkChart('chart-nilr-trend',{type:'line',data:{labels:dates,datasets:[{
    label:'"Not in LR" Count',
    data:dates.map(d=>fd.filter(r=>r.date===d&&r.labour==='Not in LR').length),
    borderColor:'#d62728',backgroundColor:'rgba(214,39,40,.1)',
    fill:true,tension:.3,pointRadius:5
  }]},options:{responsive:true,plugins:{title:{display:true,text:'"Not in LR" Headcount Trend'}}}});

  // Not in LR SLDU (latest)
  const nilr=snap.filter(r=>r.labour==='Not in LR');
  const nSldu=countBy(nilr,'sldu');
  const nSlduE=Object.entries(nSldu).sort((a,b)=>a[1]-b[1]);
  mkChart('chart-nilr-sldu',{type:'bar',data:{labels:nSlduE.map(e=>e[0]),
    datasets:[{label:'Not in LR',backgroundColor:'#d62728',data:nSlduE.map(e=>e[1])}]
  },options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{title:{display:true,text:`"Not in LR" by SLDU (${ld})`}}}});

  // Not in LR TM top 10
  const nTm=countBy(nilr,'tm');
  const top10tm=Object.entries(nTm).sort((a,b)=>b[1]-a[1]).slice(0,10);
  mkChart('chart-nilr-tm',{type:'bar',data:{labels:top10tm.map(e=>e[0]),
    datasets:[{label:'Not in LR',backgroundColor:'#d62728',data:top10tm.map(e=>e[1])}]
  },options:{responsive:true,plugins:{title:{display:true,text:'Top TMs with "Not in LR"'}},
    scales:{x:{ticks:{maxRotation:30}}}}});
}

// ── PEOPLE ────────────────────────────────────────────────────────────────────
let _peopleCache = null;

function getFilteredPeople(){
  const fd=filterData(), dates=getDates(fd);
  const ld=dates[dates.length-1]||'';
  const showAll=document.getElementById('people-show-all').checked;
  let view=showAll?fd:fd.filter(r=>r.date===ld);
  const q=(document.getElementById('people-search').value||'').toLowerCase();
  if(q) view=view.filter(r=>r.emp_name.toLowerCase().includes(q)||r.emp_code.toLowerCase().includes(q));
  return view.slice().sort((a,b)=>b.date.localeCompare(a.date));
}

function renderPeople(){
  _peopleCache=getFilteredPeople();
  const total=_peopleCache.length;
  const pages=Math.ceil(total/PAGE_SIZE)||1;
  if(peoplePage>pages) peoplePage=1;
  const start=(peoplePage-1)*PAGE_SIZE;
  const page=_peopleCache.slice(start,start+PAGE_SIZE);

  document.getElementById('people-count').textContent=total.toLocaleString()+' records';
  document.getElementById('people-pagination').textContent=`Page ${peoplePage} of ${pages}`;

  const tb=document.getElementById('people-tbody');
  tb.innerHTML='';
  const bCls={'B':'badge-green','NB':'badge-red','S':'badge-orange','F':'badge-orange'};
  const lCls={'Present in LR':'badge-green','Not in LR':'badge-red'};

  page.forEach(r=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${r.date}</td><td>${r.emp_code}</td><td>${r.emp_name}</td>
      <td>${r.band}</td><td>${r.sldu}</td><td>${r.tm}</td><td>${r.pm}</td>
      <td class="${bCls[r.billability]||''}">${r.billability}</td>
      <td>${r.ons_off}</td><td>${r.city}</td>
      <td class="${lCls[r.labour]||''}">${r.labour}</td>`;
    tb.appendChild(tr);
  });
}

function changePage(dir){
  _peopleCache=_peopleCache||getFilteredPeople();
  const pages=Math.ceil((_peopleCache.length||1)/PAGE_SIZE);
  peoplePage=Math.max(1,Math.min(pages,peoplePage+dir));
  renderPeople();
}

// ── DOWNLOAD ─────────────────────────────────────────────────────────────────
function downloadCSV(){
  const rows=getFilteredPeople();
  const cols=['date','emp_code','emp_name','band','sldu','tm','pm',
              'billability','ons_off','city','service_line','slwbs','labour'];
  const hdr=cols.join(',');
  const body=rows.map(r=>cols.map(c=>{
    const v=String(r[c]||'').replace(/"/g,'""');
    return v.includes(',')||v.includes('"')?`"${v}"`:v;
  }).join(','));
  const blob=new Blob([hdr+'\n'+body.join('\n')],{type:'text/csv'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='zcop_filtered.csv'; a.click();
}

// ── INIT / ROUTING ────────────────────────────────────────────────────────────
function populateFilters(){
  const fill=(id,vals)=>{
    const el=document.getElementById(id); if(!el) return;
    uniq(vals).forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;el.appendChild(o)});
  };
  fill('filter-sldu',        DATA.map(r=>r.sldu));
  fill('filter-tm',          DATA.map(r=>r.tm));
  fill('filter-ons-off',     DATA.map(r=>r.ons_off));
  fill('filter-billability', DATA.map(r=>r.billability));
  fill('filter-band',        DATA.map(r=>r.band));
  const dates=uniq(DATA.map(r=>r.date));
  if(dates.length){
    document.getElementById('date-start').value=dates[0];
    document.getElementById('date-end').value=dates[dates.length-1];
  }
}

function switchTab(tab){
  activeTab=tab;
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
  document.getElementById('tab-btn-'+tab).classList.add('active');
  document.getElementById('tab-'+tab).classList.add('active');
  renderActiveTab();
}

function renderActiveTab(){
  _peopleCache=null;
  if(activeTab==='overview') renderOverview();
  else if(activeTab==='rurd') renderRURD();
  else if(activeTab==='sl') renderServiceLines();
  else if(activeTab==='lr') renderLabourReport();
  else if(activeTab==='people') renderPeople();
}

function resetFilters(){
  document.querySelectorAll('.filter-control').forEach(el=>{
    if(el.tagName==='SELECT') Array.from(el.options).forEach(o=>o.selected=false);
  });
  const dates=uniq(DATA.map(r=>r.date));
  if(dates.length){
    document.getElementById('date-start').value=dates[0];
    document.getElementById('date-end').value=dates[dates.length-1];
  }
  renderActiveTab();
}

window.onload=function(){
  document.getElementById('gen-time').textContent='Generated: '+GEN_TIME;
  populateFilters();
  document.querySelectorAll('.filter-control').forEach(el=>{
    el.addEventListener('change',()=>renderActiveTab());
    if(el.type==='date') el.addEventListener('input',()=>renderActiveTab());
  });
  renderOverview();
};
</script>
</body>
</html>
"""

def build_html(data_records, rurd_records, labour_col):
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = HTML_TEMPLATE
    html = html.replace("__DATA_JSON__",  json.dumps(data_records,  ensure_ascii=False))
    html = html.replace("__RURD_JSON__",  json.dumps(rurd_records,  ensure_ascii=False))
    html = html.replace("__LABOUR_COL_JSON__", json.dumps(labour_col))
    html = html.replace("__GEN_TIME__",   gen_time)
    return html

# ── main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    data_records, labour_col = read_data()
    rurd_records = read_rurd()

    html = build_html(data_records, rurd_records, labour_col)

    OUT_HTML.parent.mkdir(exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)

    size_kb = OUT_HTML.stat().st_size // 1024
    print(f"\n✓ Dashboard written → {OUT_HTML}  ({size_kb} KB)")
    print("  Opening in browser…")
    webbrowser.open(OUT_HTML.as_uri())
