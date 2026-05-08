#!/usr/bin/env python3
"""
ZCOP Dashboard — Interactive trend & filter analysis for TD Bank ZCOP data.

Run:
    streamlit run zcop_dashboard.py

Requirements:
    pip install streamlit plotly pandas openpyxl
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_CSV  = BASE_DIR / "Zcop Output" / "DATA_combined.csv"
RURD_CSV  = BASE_DIR / "Zcop Output" / "RU-RD_combined.csv"

# ── Page config ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ZCOP Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stMetric"] {
        background: #f0f4fa;
        border-radius: 8px;
        padding: 10px 14px;
    }
    .section-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 4px; }
</style>
""", unsafe_allow_html=True)

st.title("📊 ZCOP Dashboard — TD Bank")
st.caption("Resource utilisation, trend analysis & labour report monitoring")

# ── Data loaders ────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading headcount data…")
def load_data() -> pd.DataFrame:
    if not DATA_CSV.exists():
        st.error(f"DATA CSV not found: {DATA_CSV}")
        st.stop()

    df = pd.read_csv(DATA_CSV, low_memory=False)
    df["LOAD_DATE"] = pd.to_datetime(df["LOAD_DATE"], errors="coerce")
    df = df[df["LOAD_DATE"].notna()].copy()
    df["DATE"] = df["LOAD_DATE"].dt.date

    # Dynamic Labour Report column
    labour_cols = [c for c in df.columns if "labour" in c.lower()]
    df["_LABOUR"] = df[labour_cols[0]] if labour_cols else pd.NA
    df.attrs["labour_col_name"] = labour_cols[0] if labour_cols else "Labour Report"

    for col in ("BILLABILITY_STATUS", "ONS_OFF_FLAG", "SLDU", "CAREER_BAND",
                "TM_NAME", "SLWBS", "SERVICE_LINE", "PM_NAME"):
        if col in df.columns:
            df[col] = df[col].fillna("Unknown").astype(str).str.strip()

    return df


@st.cache_data(show_spinner="Loading RU/RD data…")
def load_rurd() -> pd.DataFrame:
    if not RURD_CSV.exists():
        return pd.DataFrame()

    # Row 0 in the file is a garbage summary row; real header is row 1
    rurd = pd.read_csv(RURD_CSV, low_memory=False, skiprows=1)
    rurd["LOAD_DATE"] = pd.to_datetime(rurd["LOAD_DATE"], errors="coerce")
    rurd = rurd[rurd["LOAD_DATE"].notna()].copy()
    rurd["DATE"] = rurd["LOAD_DATE"].dt.date

    rurd["Count"] = pd.to_numeric(rurd.get("Count", 0), errors="coerce").fillna(0)
    for col in ("RU/RD", "TM_NAME", "Service Line", "Portfolio", "ONS_OFF_FLAG"):
        if col in rurd.columns:
            rurd[col] = rurd[col].fillna("Unknown").astype(str).str.strip()
    if "Remarks" in rurd.columns:
        rurd["Remarks"] = rurd["Remarks"].fillna("").astype(str).str.strip()

    return rurd


df   = load_data()
rurd = load_rurd()
LABOUR_COL_NAME = df.attrs.get("labour_col_name", "Labour Report")

# ── Sidebar filters ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔧 Filters")

    all_dates = sorted(df["DATE"].unique())
    min_d, max_d = all_dates[0], all_dates[-1]

    date_range = st.date_input(
        "Date Range",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
    )
    # Normalise to always (start, end)
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_d, end_d = date_range
    else:
        start_d = end_d = date_range[0] if date_range else min_d

    sel_sldu    = st.multiselect("Service Line / DU",   sorted(df["SLDU"].unique()))
    sel_tm      = st.multiselect("Talent Manager",      sorted(df["TM_NAME"].unique()))
    sel_ons_off = st.multiselect("Onsite / Offshore",   sorted(df["ONS_OFF_FLAG"].unique()))
    sel_bill    = st.multiselect("Billability Status",  sorted(df["BILLABILITY_STATUS"].unique()))
    sel_band    = st.multiselect("Career Band",         sorted(df["CAREER_BAND"].unique()))

    st.divider()
    st.caption(f"Data: {min_d} → {max_d}  |  {len(df):,} rows total")

# ── Filter helpers ───────────────────────────────────────────────────────────────
def filt(data: pd.DataFrame) -> pd.DataFrame:
    m = (data["DATE"] >= start_d) & (data["DATE"] <= end_d)
    if sel_sldu:    m &= data["SLDU"].isin(sel_sldu)
    if sel_tm:      m &= data["TM_NAME"].isin(sel_tm)
    if sel_ons_off: m &= data["ONS_OFF_FLAG"].isin(sel_ons_off)
    if sel_bill:    m &= data["BILLABILITY_STATUS"].isin(sel_bill)
    if sel_band:    m &= data["CAREER_BAND"].isin(sel_band)
    return data[m]

def filt_rurd(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    m = (data["DATE"] >= start_d) & (data["DATE"] <= end_d)
    if sel_sldu and "Service Line" in data.columns:
        m &= data["Service Line"].isin(sel_sldu)
    if sel_tm and "TM_NAME" in data.columns:
        m &= data["TM_NAME"].isin(sel_tm)
    if sel_ons_off and "ONS_OFF_FLAG" in data.columns:
        m &= data["ONS_OFF_FLAG"].isin(sel_ons_off)
    return data[m]


fdf   = filt(df)
frurd = filt_rurd(rurd)

# ── Tabs ─────────────────────────────────────────────────────────────────────────
tab_ov, tab_rurd, tab_sl, tab_lr, tab_ppl = st.tabs([
    "📈 Overview",
    "🔄 RU / RD",
    "📂 Service Lines",
    "📋 Labour Report",
    "🔍 People Details",
])

# ═══════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════
with tab_ov:
    if fdf.empty:
        st.warning("No data for selected filters.")
    else:
        latest_d = fdf["DATE"].max()
        snap = fdf[fdf["DATE"] == latest_d]

        sorted_dates = sorted(fdf["DATE"].unique())
        prev_d = sorted_dates[-2] if len(sorted_dates) >= 2 else None
        prev_snap = fdf[fdf["DATE"] == prev_d] if prev_d else pd.DataFrame()

        total_hc  = len(snap)
        bill_hc   = len(snap[snap["BILLABILITY_STATUS"] == "B"])
        nbill_hc  = total_hc - bill_hc
        bill_pct  = f"{bill_hc / total_hc * 100:.1f}%" if total_hc else "0%"
        delta_hc  = total_hc - len(prev_snap)

        ons_mask  = snap["ONS_OFF_FLAG"].str.upper().str.contains("ONS|ODR", na=False)
        off_mask  = snap["ONS_OFF_FLAG"].str.upper().str.contains("OFF", na=False)
        onsite_hc  = ons_mask.sum()
        offshore_hc = off_mask.sum()

        st.markdown(f"**Latest snapshot: {latest_d}**")
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total HC",       total_hc,  delta=delta_hc)
        c2.metric("Billable HC",    bill_hc)
        c3.metric("Non-Billable",   nbill_hc)
        c4.metric("Billable %",     bill_pct)
        c5.metric("Onsite HC",      onsite_hc)
        c6.metric("Offshore HC",    offshore_hc)

        st.divider()

        # HC trend
        hc_trend = fdf.groupby("DATE").size().reset_index(name="Headcount")
        fig_hc = px.line(
            hc_trend, x="DATE", y="Headcount", markers=True,
            title="Daily Headcount Trend",
            labels={"DATE": "Date", "Headcount": "Headcount"},
        )
        fig_hc.update_traces(line_color="#1f77b4", marker_size=6)
        fig_hc.update_layout(hovermode="x unified")
        st.plotly_chart(fig_hc, use_container_width=True)

        col_a, col_b = st.columns(2)

        with col_a:
            bill_trend = (
                fdf.groupby(["DATE", "BILLABILITY_STATUS"])
                   .size()
                   .reset_index(name="Count")
            )
            fig_bill = px.bar(
                bill_trend, x="DATE", y="Count", color="BILLABILITY_STATUS",
                title="Billable vs Non-Billable by Date",
                barmode="stack",
                labels={"DATE": "Date", "Count": "Headcount"},
                color_discrete_map={"B": "#2ca02c", "NB": "#d62728",
                                    "S": "#ff7f0e", "F": "#9467bd"},
            )
            fig_bill.update_layout(hovermode="x unified", legend_title="Status")
            st.plotly_chart(fig_bill, use_container_width=True)

        with col_b:
            ons_trend = (
                fdf.groupby(["DATE", "ONS_OFF_FLAG"])
                   .size()
                   .reset_index(name="Count")
            )
            fig_ons = px.bar(
                ons_trend, x="DATE", y="Count", color="ONS_OFF_FLAG",
                title="Onsite vs Offshore by Date",
                barmode="stack",
                labels={"DATE": "Date", "Count": "Headcount"},
            )
            fig_ons.update_layout(hovermode="x unified", legend_title="Flag")
            st.plotly_chart(fig_ons, use_container_width=True)

        # Net billed HC trend
        if "NET_BILLED" in fdf.columns:
            nb_trend = (
                fdf.assign(NET_BILLED=pd.to_numeric(fdf["NET_BILLED"], errors="coerce"))
                   .groupby("DATE")["NET_BILLED"]
                   .sum()
                   .reset_index(name="Net Billed Days")
            )
            fig_nb = px.area(
                nb_trend, x="DATE", y="Net Billed Days",
                title="Total Net Billed Days Trend",
            )
            fig_nb.update_layout(hovermode="x unified")
            st.plotly_chart(fig_nb, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════
# TAB 2 — RU / RD
# ═══════════════════════════════════════════════════════════════════
with tab_rurd:
    if frurd.empty:
        st.warning("No RU/RD data found or no data matches filters.")
    else:
        ru_df = frurd[frurd["RU/RD"] == "RU"]
        rd_df = frurd[frurd["RU/RD"] == "RD"]

        total_ru = len(ru_df)
        total_rd = len(rd_df)
        net_chg  = total_ru - total_rd

        m1, m2, m3 = st.columns(3)
        m1.metric("Total RU (Additions)", total_ru)
        m2.metric("Total RD (Removals)",  total_rd)
        m3.metric("Net Change",           f"{net_chg:+d}")

        st.divider()

        col_a, col_b = st.columns(2)

        with col_a:
            daily = (
                frurd.groupby(["DATE", "RU/RD"])["Count"]
                     .sum()
                     .abs()
                     .reset_index()
            )
            fig_rurd = px.bar(
                daily, x="DATE", y="Count", color="RU/RD",
                barmode="group",
                title="Daily RU / RD Count",
                color_discrete_map={"RU": "#2ca02c", "RD": "#d62728"},
                labels={"DATE": "Date"},
            )
            fig_rurd.update_layout(hovermode="x unified")
            st.plotly_chart(fig_rurd, use_container_width=True)

        with col_b:
            net_daily = (
                frurd.groupby("DATE")["Count"]
                     .sum()
                     .reset_index(name="Net")
            )
            fig_net = px.bar(
                net_daily, x="DATE", y="Net",
                title="Net Daily Movement (RU − RD)",
                color="Net",
                color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
                labels={"DATE": "Date"},
            )
            st.plotly_chart(fig_net, use_container_width=True)

        col_c, col_d = st.columns(2)

        with col_c:
            if "Remarks" in rd_df.columns:
                reasons = (
                    rd_df[rd_df["Remarks"] != ""]["Remarks"]
                      .value_counts()
                      .reset_index()
                )
                reasons.columns = ["Reason", "Count"]
                if not reasons.empty:
                    fig_rsn = px.pie(
                        reasons, names="Reason", values="Count",
                        title="RD Reasons Breakdown",
                    )
                    st.plotly_chart(fig_rsn, use_container_width=True)

        with col_d:
            if "Portfolio" in frurd.columns:
                port = (
                    frurd.groupby(["Portfolio", "RU/RD"])["Count"]
                         .sum().abs()
                         .reset_index()
                )
                fig_port = px.bar(
                    port, x="Portfolio", y="Count", color="RU/RD",
                    barmode="group",
                    title="RU/RD by Portfolio",
                    color_discrete_map={"RU": "#2ca02c", "RD": "#d62728"},
                )
                fig_port.update_layout(xaxis_tickangle=-30)
                st.plotly_chart(fig_port, use_container_width=True)

        # Cumulative net change
        cum = (
            frurd.sort_values("DATE")
                 .groupby("DATE")["Count"]
                 .sum()
                 .cumsum()
                 .reset_index(name="Cumulative Net")
        )
        fig_cum = px.area(
            cum, x="DATE", y="Cumulative Net",
            title="Cumulative Net HC Change Over Time",
        )
        fig_cum.update_layout(hovermode="x unified")
        st.plotly_chart(fig_cum, use_container_width=True)

        # RU/RD by TM
        if "TM_NAME" in frurd.columns:
            tm_rurd = (
                frurd.groupby(["TM_NAME", "RU/RD"])["Count"]
                     .sum().abs()
                     .reset_index()
            )
            top_tms = (
                tm_rurd.groupby("TM_NAME")["Count"]
                       .sum()
                       .nlargest(15)
                       .index.tolist()
            )
            tm_rurd_top = tm_rurd[tm_rurd["TM_NAME"].isin(top_tms)]
            fig_tm_rurd = px.bar(
                tm_rurd_top, y="TM_NAME", x="Count", color="RU/RD",
                barmode="group", orientation="h",
                title="Top 15 TMs — RU/RD Breakdown",
                color_discrete_map={"RU": "#2ca02c", "RD": "#d62728"},
            )
            fig_tm_rurd.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_tm_rurd, use_container_width=True)

        with st.expander("📋 RU/RD Detail Table"):
            show_c = [c for c in
                      ["DATE", "EMP_CODE", "EMP_NAME", "RU/RD", "Count",
                       "Service Line", "TM_NAME", "Portfolio", "ONS_OFF_FLAG", "Remarks"]
                      if c in frurd.columns]
            st.dataframe(
                frurd[show_c].sort_values("DATE", ascending=False),
                use_container_width=True, height=400,
            )


# ═══════════════════════════════════════════════════════════════════
# TAB 3 — SERVICE LINES
# ═══════════════════════════════════════════════════════════════════
with tab_sl:
    if fdf.empty:
        st.warning("No data for selected filters.")
    else:
        latest_d = fdf["DATE"].max()
        snap = fdf[fdf["DATE"] == latest_d]

        col_a, col_b = st.columns(2)

        with col_a:
            sldu_c = (
                snap.groupby("SLDU").size()
                    .reset_index(name="HC")
                    .sort_values("HC")
            )
            fig_sldu = px.bar(
                sldu_c, y="SLDU", x="HC", orientation="h",
                title=f"HC by Service Line DU  ({latest_d})", text="HC",
            )
            fig_sldu.update_traces(textposition="outside")
            fig_sldu.update_layout(height=max(350, len(sldu_c) * 22))
            st.plotly_chart(fig_sldu, use_container_width=True)

        with col_b:
            sl_c = snap.groupby("SERVICE_LINE").size().reset_index(name="HC")
            fig_sl = px.pie(
                sl_c, names="SERVICE_LINE", values="HC",
                title=f"Service Line Distribution  ({latest_d})",
            )
            st.plotly_chart(fig_sl, use_container_width=True)

        # SLDU trend
        sldu_trend = fdf.groupby(["DATE", "SLDU"]).size().reset_index(name="HC")
        top_sldu = (
            sldu_trend.groupby("SLDU")["HC"]
                      .sum().nlargest(10).index.tolist()
        )
        fig_sldu_tr = px.line(
            sldu_trend[sldu_trend["SLDU"].isin(top_sldu)],
            x="DATE", y="HC", color="SLDU", markers=True,
            title="Top 10 Service Line DU — HC Trend",
        )
        fig_sldu_tr.update_layout(hovermode="x unified")
        st.plotly_chart(fig_sldu_tr, use_container_width=True)

        # Top SLWBS
        top_n = st.slider("Top N SLWBS to show", 5, 30, 15)
        slwbs_c = (
            snap["SLWBS"].value_counts().head(top_n)
                         .reset_index()
        )
        slwbs_c.columns = ["SLWBS", "Count"]
        fig_slwbs = px.bar(
            slwbs_c, x="Count", y="SLWBS", orientation="h",
            title=f"Top {top_n} SLWBS  ({latest_d})", text="Count",
        )
        fig_slwbs.update_traces(textposition="outside")
        fig_slwbs.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=max(400, top_n * 26),
        )
        st.plotly_chart(fig_slwbs, use_container_width=True)

        col_c, col_d = st.columns(2)

        with col_c:
            band_c = snap["CAREER_BAND"].value_counts().reset_index()
            band_c.columns = ["Band", "Count"]
            fig_band = px.bar(
                band_c, x="Band", y="Count",
                title=f"Career Band Distribution  ({latest_d})", text="Count",
            )
            fig_band.update_traces(textposition="outside")
            fig_band.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_band, use_container_width=True)

        with col_d:
            tm_c = (
                snap.groupby("TM_NAME").size()
                    .reset_index(name="HC")
                    .sort_values("HC", ascending=False)
                    .head(15)
            )
            fig_tm = px.bar(
                tm_c, x="HC", y="TM_NAME", orientation="h",
                title=f"Top 15 TMs by HC  ({latest_d})", text="HC",
            )
            fig_tm.update_traces(textposition="outside")
            fig_tm.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_tm, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════
# TAB 4 — LABOUR REPORT
# ═══════════════════════════════════════════════════════════════════
with tab_lr:
    st.caption(f"Labour Report column: **{LABOUR_COL_NAME}**")

    if fdf.empty or fdf["_LABOUR"].isna().all():
        st.warning("Labour Report column not found in data.")
    else:
        lr_trend = (
            fdf.groupby(["DATE", "_LABOUR"])
               .size()
               .reset_index(name="Count")
               .rename(columns={"_LABOUR": "Labour Status"})
        )

        col_a, col_b = st.columns(2)

        with col_a:
            fig_lr = px.bar(
                lr_trend, x="DATE", y="Count", color="Labour Status",
                title="Labour Report Status — Daily Trend",
                barmode="stack",
                color_discrete_map={
                    "Present in LR": "#2ca02c",
                    "Not in LR":     "#d62728",
                    "Delivery Team": "#ff7f0e",
                },
                labels={"DATE": "Date", "Count": "Headcount"},
            )
            fig_lr.update_layout(hovermode="x unified")
            st.plotly_chart(fig_lr, use_container_width=True)

        with col_b:
            latest_d = fdf["DATE"].max()
            lr_pie = (
                fdf[fdf["DATE"] == latest_d]["_LABOUR"]
                   .value_counts()
                   .reset_index()
            )
            lr_pie.columns = ["Status", "Count"]
            fig_lr_pie = px.pie(
                lr_pie, names="Status", values="Count",
                title=f"Labour Report Status  ({latest_d})",
                color="Status",
                color_discrete_map={
                    "Present in LR": "#2ca02c",
                    "Not in LR":     "#d62728",
                    "Delivery Team": "#ff7f0e",
                },
            )
            st.plotly_chart(fig_lr_pie, use_container_width=True)

        # "Not in LR" trend
        nilr_trend = (
            fdf[fdf["_LABOUR"] == "Not in LR"]
               .groupby("DATE").size()
               .reset_index(name="Not in LR")
        )
        fig_nilr = px.line(
            nilr_trend, x="DATE", y="Not in LR", markers=True,
            title='"Not in LR" Headcount Trend',
        )
        fig_nilr.update_traces(line_color="#d62728", marker_size=7)
        fig_nilr.update_layout(hovermode="x unified")
        st.plotly_chart(fig_nilr, use_container_width=True)

        # Breakdown of "Not in LR" on latest snapshot
        not_lr = fdf[(fdf["DATE"] == latest_d) & (fdf["_LABOUR"] == "Not in LR")]
        if not not_lr.empty:
            st.subheader(f"'Not in LR' Detail — {latest_d}  ({len(not_lr)} people)")
            col_c, col_d = st.columns(2)

            with col_c:
                sldu_nilr = (
                    not_lr.groupby("SLDU").size()
                          .reset_index(name="Count")
                          .sort_values("Count")
                )
                fig_sldu_nilr = px.bar(
                    sldu_nilr, y="SLDU", x="Count", orientation="h",
                    title='"Not in LR" by Service Line DU', text="Count",
                )
                fig_sldu_nilr.update_traces(textposition="outside")
                st.plotly_chart(fig_sldu_nilr, use_container_width=True)

            with col_d:
                tm_nilr = (
                    not_lr.groupby("TM_NAME").size()
                          .reset_index(name="Count")
                          .sort_values("Count", ascending=False)
                          .head(10)
                )
                fig_tm_nilr = px.bar(
                    tm_nilr, x="TM_NAME", y="Count",
                    title='Top 10 TMs with "Not in LR"', text="Count",
                )
                fig_tm_nilr.update_traces(textposition="outside")
                fig_tm_nilr.update_layout(xaxis_tickangle=-30)
                st.plotly_chart(fig_tm_nilr, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════
# TAB 5 — PEOPLE DETAILS
# ═══════════════════════════════════════════════════════════════════
with tab_ppl:
    if fdf.empty:
        st.warning("No data for selected filters.")
    else:
        latest_d = fdf["DATE"].max()

        col_a, col_b = st.columns([3, 1])
        with col_a:
            search = st.text_input("🔍 Search by Name or EMP Code", "")
        with col_b:
            show_all = st.checkbox("Show all dates", value=False)

        view = fdf if show_all else fdf[fdf["DATE"] == latest_d]

        if search:
            name_mask = view["EMP_NAME"].str.contains(search, case=False, na=False)
            code_mask = view["EMP_CODE"].astype(str).str.contains(search, na=False)
            view = view[name_mask | code_mask]

        display_cols = [
            "DATE", "EMP_CODE", "EMP_NAME", "CAREER_BAND", "SLDU",
            "TM_NAME", "PM_NAME", "BILLABILITY_STATUS", "ONS_OFF_FLAG",
            "DERIVED_EMP_CITY", "SERVICE_LINE", "_LABOUR",
        ]
        display_cols = [c for c in display_cols if c in view.columns]

        st.caption(f"Showing **{len(view):,}** records")
        st.dataframe(
            view[display_cols]
                .rename(columns={"_LABOUR": LABOUR_COL_NAME})
                .sort_values("DATE", ascending=False)
                .reset_index(drop=True),
            use_container_width=True,
            height=500,
        )

        csv_out = (
            view[display_cols]
                .rename(columns={"_LABOUR": LABOUR_COL_NAME})
                .to_csv(index=False)
        )
        st.download_button(
            "⬇️ Download Filtered Data",
            data=csv_out,
            file_name="zcop_filtered.csv",
            mime="text/csv",
        )
