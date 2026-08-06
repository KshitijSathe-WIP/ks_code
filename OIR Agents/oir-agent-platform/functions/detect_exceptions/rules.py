"""Detection rules engine for the OIR platform.

All thresholds are read from config.json — never hard-coded here.
Three rules:
  Rule 1 — STALE_2D:     comments unchanged for >= threshold_days
  Rule 2 — EXPIRY_2D:    DEM_End_Date within lookahead_days
  Rule 3 — Escalation:   tiered recipients based on stale_days

Output is a dict keyed by recipient email with expiring and stale demand lists,
ready to pass to the Digest Agent.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from azure.identity import ClientSecretCredential

from functions.shared.models import CONFIG

logger = logging.getLogger(__name__)

_STALENESS_CFG = CONFIG["staleness"]
_EXPIRY_CFG = CONFIG["expiry"]


# ---------------------------------------------------------------------------
# Data fetch helpers
# ---------------------------------------------------------------------------

def _token() -> str:
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )
    dv_host = os.environ["DATAVERSE_URL"].rstrip("/").removeprefix("https://")
    return credential.get_token(f"https://{dv_host}/.default").token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Accept": "application/json",
    }


def _dv_get(path: str, params: dict | None = None) -> list[dict]:
    base = os.environ["DATAVERSE_URL"].rstrip("/") + "/api/data/v9.2"
    with httpx.Client(timeout=30.0) as http:
        resp = http.get(f"{base}/{path}", headers=_headers(), params=params or {})
        resp.raise_for_status()
        return resp.json().get("value", [])


# ---------------------------------------------------------------------------
# Core rules
# ---------------------------------------------------------------------------

def run_rules(today: date | None = None) -> dict[str, Any]:
    """Execute all three rules and return a grouped-by-recipient payload."""
    from datetime import timezone as _tz
    today = today or date.today()
    now = datetime.now(_tz.utc)

    excluded = tuple(_STALENESS_CFG["excluded_statuses"])
    stale_threshold = _STALENESS_CFG["threshold_days"]
    l2_threshold = _STALENESS_CFG["escalation_l2_days"]
    l3_threshold = _STALENESS_CFG["escalation_l3_days"]
    lookahead = _EXPIRY_CFG["lookahead_days"]

    # -----------------------------------------------------------------------
    # Rule 1 + 3 — Stale demands (respects snooze and last-notified)
    # Filter by last_content_change_date (persisted, filterable) instead of
    # oir_stale_days (computed column — not filterable in Dataverse OData).
    # -----------------------------------------------------------------------
    stale_cutoff = (today - timedelta(days=stale_threshold)).isoformat()
    excluded_filter = "".join(f"and oir_status ne '{s}' " for s in excluded)
    stale_params = {
        "$filter": (
            f"oir_is_active eq true "
            f"and oir_last_content_change_date le {stale_cutoff} "
            + excluded_filter
        ),
        "$select": (
            "oir_demandid,oir_project,oir_role,oir_status,"
            "oir_pm_email,oir_tm_email,oir_em_email,"
            "oir_dem_end_date,oir_last_content_change_date,oir_escalation_level,"
            "oir_snooze_until,oir_last_notified_on"
        ),
    }
    raw_stale = _dv_get("oir_demands", stale_params)
    # Compute stale_days in code and attach to each row
    stale_rows = []
    for r in raw_stale:
        lcd = r.get("oir_last_content_change_date", "")
        try:
            stale_days_val = (today - date.fromisoformat(lcd[:10])).days
        except (ValueError, TypeError):
            stale_days_val = stale_threshold
        stale_rows.append({**r, "oir_stale_days": stale_days_val})

    # -----------------------------------------------------------------------
    # Rule 2 — Expiring demands (snooze intentionally ignored per spec)
    # -----------------------------------------------------------------------
    expiry_end = (today + timedelta(days=lookahead)).isoformat()
    expiry_params = {
        "$filter": (
            f"oir_is_active eq true "
            f"and oir_dem_end_date ge {today.isoformat()} "
            f"and oir_dem_end_date le {expiry_end} "
            f"and oir_status ne 'Joined'"
        ),
        "$select": (
            "oir_demandid,oir_project,oir_role,oir_status,"
            "oir_pm_email,oir_tm_email,oir_em_email,oir_dem_end_date"
        ),
    }
    expiry_rows = _dv_get("oir_demands", expiry_params)

    # -----------------------------------------------------------------------
    # Group by recipient
    # -----------------------------------------------------------------------
    recipient_map: dict[str, dict] = defaultdict(lambda: {"expiring": [], "stale": []})

    for row in expiry_rows:
        item = {
            "demand_id": row["oir_demandid"],
            "project": row.get("oir_project", ""),
            "role": row.get("oir_role", ""),
            "dem_end_date": row.get("oir_dem_end_date", ""),
            "days_left": _days_left(row.get("oir_dem_end_date"), today),
            "status": row.get("oir_status", ""),
            "rule": "EXPIRY_2D",
        }
        for email_key in ("oir_pm_email", "oir_tm_email", "oir_em_email"):
            email = row.get(email_key, "")
            if email:
                recipient_map[email]["expiring"].append(item)

    for row in stale_rows:
        if _is_snoozed(row, now):
            continue
        if _notified_today(row, today):
            continue

        stale_days = int(row.get("oir_stale_days", 0))
        rule_tag, recipients = _escalation_tier(stale_days, l2_threshold, l3_threshold)
        item = {
            "demand_id": row["oir_demandid"],
            "project": row.get("oir_project", ""),
            "role": row.get("oir_role", ""),
            "stale_days": stale_days,
            "status": row.get("oir_status", ""),
            "escalation_level": int(row.get("oir_escalation_level", 0)),
            "rule": rule_tag,
        }

        email_fields = _recipient_fields(recipients)
        for field in email_fields:
            email = row.get(field, "")
            if email:
                recipient_map[email]["stale"].append(item)

    # -----------------------------------------------------------------------
    # Build final payload
    # -----------------------------------------------------------------------
    recipients_list = []
    for email, buckets in recipient_map.items():
        # Sort stale by stale_days desc; expiring by days_left asc
        buckets["stale"].sort(key=lambda x: x["stale_days"], reverse=True)
        buckets["expiring"].sort(key=lambda x: x["days_left"])
        recipients_list.append({
            "email": email,
            "expiring": buckets["expiring"],
            "stale": buckets["stale"],
        })

    return {
        "run_date": today.isoformat(),
        "recipients": recipients_list,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_left(dem_end_date_str: str | None, today: date) -> int:
    if not dem_end_date_str:
        return 999
    try:
        end = date.fromisoformat(dem_end_date_str[:10])
        return (end - today).days
    except ValueError:
        return 999


def _is_snoozed(row: dict, now: datetime) -> bool:
    snooze = row.get("oir_snooze_until")
    if not snooze:
        return False
    try:
        from datetime import timezone as _tz
        snooze_dt = datetime.fromisoformat(snooze.replace("Z", "+00:00"))
        # Compare in UTC; now must also be UTC-aware
        now_utc = now.replace(tzinfo=_tz.utc) if now.tzinfo is None else now
        return snooze_dt > now_utc
    except (ValueError, AttributeError):
        return False


def _notified_today(row: dict, today: date) -> bool:
    last = row.get("oir_last_notified_on")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return last_dt.date() >= today
    except (ValueError, AttributeError):
        return False


def _escalation_tier(stale_days: int, l2: int, l3: int) -> tuple[str, list[str]]:
    if stale_days >= l3:
        return "ESCALATION_L3", ["pm", "tm", "em", "dm"]
    if stale_days >= l2:
        return "ESCALATION_L2", ["pm", "tm", "em"]
    return "STALE_2D", ["pm", "tm"]


def _recipient_fields(roles: list[str]) -> list[str]:
    mapping = {
        "pm": "oir_pm_email",
        "tm": "oir_tm_email",
        "em": "oir_em_email",
        "dm": "oir_dm_email",   # optional field; absent rows are skipped silently
    }
    return [mapping[r] for r in roles if r in mapping]


def _gate(parsed: dict) -> str:
    """Post-processing confidence gate for Reply Interpretation output (spec §7.2).

    Low confidence -> CLARIFY. High-risk field present -> CONFIRM.
    Otherwise safe to write directly -> APPLY.
    """
    threshold = CONFIG["agent"]["confidence_threshold"]
    high_risk = set(CONFIG["agent"]["high_risk_fields"])
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0   # treat malformed confidence as low -> CLARIFY
    if confidence < threshold:
        return "CLARIFY"
    if high_risk & set(parsed.keys()):
        return "CONFIRM"
    return "APPLY"
