"""Detection rules engine for the OIR platform.

All thresholds are read from config.json — never hard-coded here.
Three rules:
  Rule 1 — STALE_2D:     comments unchanged for >= threshold_days
  Rule 2 — EXPIRY_2D:    DEM_End_Date within lookahead_days
  Rule 3 — Escalation:   tiered recipients based on stale_days

Output is a dict keyed by recipient email with expiring and stale demand lists,
ready to pass to the Digest Agent.

Staleness/expiry filtering happens client-side in Python: list_active_demands()
pushes down IsActive server-side (a cheap Cosmos SQL WHERE clause), but the
staleness/expiry logic itself depends on today's date, which isn't a value
that can be precomputed and indexed, so it stays in Python either way. See
docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md and
docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md for the full
history of this data store's evolution.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from functions.shared.models import CONFIG
from functions.shared.cosmos_client import CosmosDbClient

logger = logging.getLogger(__name__)

_STALENESS_CFG = CONFIG["staleness"]
_EXPIRY_CFG = CONFIG["expiry"]


# ---------------------------------------------------------------------------
# Core rules
# ---------------------------------------------------------------------------

def run_rules(today: date | None = None) -> dict[str, Any]:
    """Execute all three rules and return a grouped-by-recipient payload."""
    today = today or date.today()
    now = datetime.now(timezone.utc)

    excluded = tuple(_STALENESS_CFG["excluded_statuses"])
    stale_threshold = _STALENESS_CFG["threshold_days"]
    l2_threshold = _STALENESS_CFG["escalation_l2_days"]
    l3_threshold = _STALENESS_CFG["escalation_l3_days"]
    lookahead = _EXPIRY_CFG["lookahead_days"]

    with CosmosDbClient() as db:
        active_rows = db.list_active_demands()

    recipient_map: dict[str, dict] = defaultdict(lambda: {"expiring": [], "stale": []})

    for row in active_rows:
        status = row.get("Status", "")

        # ---------------------------------------------------------------
        # Rule 2 — Expiring (snooze intentionally ignored per spec)
        # ---------------------------------------------------------------
        if status != "Joined":
            days_left = _days_left(row.get("DEMEndDate"), today)
            if 0 <= days_left <= lookahead:
                item = {
                    "demand_id": row.get("DemandID", ""),
                    "project": row.get("Project", ""),
                    "role": row.get("Role", ""),
                    "dem_end_date": row.get("DEMEndDate", ""),
                    "days_left": days_left,
                    "status": status,
                    "rule": "EXPIRY_2D",
                }
                for email_field in ("PMEmail", "TMEmail", "EMEmail"):
                    email = row.get(email_field, "")
                    if email:
                        recipient_map[email]["expiring"].append(item)

        # ---------------------------------------------------------------
        # Rule 1 + 3 — Stale (respects snooze and last-notified)
        # ---------------------------------------------------------------
        if status in excluded:
            continue
        if _is_snoozed(row, now):
            continue
        if _notified_today(row, today):
            continue

        stale_days = _stale_days(row.get("LastContentChangeDate"), today, default=0)
        if stale_days < stale_threshold:
            continue

        rule_tag, recipients = _escalation_tier(stale_days, l2_threshold, l3_threshold)
        item = {
            "demand_id": row.get("DemandID", ""),
            "project": row.get("Project", ""),
            "role": row.get("Role", ""),
            "stale_days": stale_days,
            "status": status,
            "escalation_level": int(row.get("EscalationLevel") or 0),
            "rule": rule_tag,
        }

        for field in _recipient_fields(recipients):
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


def _stale_days(last_content_change_date_str: str | None, today: date, default: int) -> int:
    if not last_content_change_date_str:
        return default
    try:
        return (today - date.fromisoformat(last_content_change_date_str[:10])).days
    except ValueError:
        return default


def _is_snoozed(row: dict, now: datetime) -> bool:
    snooze = row.get("SnoozeUntil")
    if not snooze:
        return False
    try:
        snooze_dt = datetime.fromisoformat(snooze.replace("Z", "+00:00"))
        # Compare in UTC; now must also be UTC-aware
        now_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
        return snooze_dt > now_utc
    except (ValueError, AttributeError):
        return False


def _notified_today(row: dict, today: date) -> bool:
    last = row.get("LastNotifiedOn")
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
        "pm": "PMEmail",
        "tm": "TMEmail",
        "em": "EMEmail",
        "dm": "DMEmail",   # optional field; absent rows are skipped silently
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
