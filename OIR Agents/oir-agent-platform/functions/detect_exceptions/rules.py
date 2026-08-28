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
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from functions.shared.models import CONFIG
from functions.shared.cosmos_client import CosmosDbClient

logger = logging.getLogger(__name__)

_STALENESS_CFG = CONFIG["staleness"]
_EXPIRY_CFG = CONFIG["expiry"]
_NOTIFICATION_CFG = CONFIG["notification"]


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
    adh_threshold = _STALENESS_CFG["escalation_adh_days"]
    lookahead = _EXPIRY_CFG["lookahead_days"]

    adh_email = account_delivery_head_email()
    if not adh_email:
        logger.warning(
            "ACCOUNT_DELIVERY_HEAD_EMAIL is not set; demands stale for >=%s days "
            "will still notify PM/TM/EM but the escalation to the Account "
            "Delivery Head will not be sent.", adh_threshold,
        )

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

        rule_tag, recipients = _escalation_tier(stale_days, l2_threshold, adh_threshold)
        item = {
            "demand_id": row.get("DemandID", ""),
            "project": row.get("Project", ""),
            "role": row.get("Role", ""),
            "stale_days": stale_days,
            "status": status,
            "escalation_level": int(row.get("EscalationLevel") or 0),
            "rule": rule_tag,
        }

        targets = [row.get(f, "") for f in _recipient_fields(recipients)]
        if "adh" in recipients and adh_email:
            targets.append(adh_email)
        for email in targets:
            if email:
                recipient_map[email]["stale"].append(item)

    # -----------------------------------------------------------------------
    # Build final payload
    # -----------------------------------------------------------------------
    max_items = _NOTIFICATION_CFG["max_items_per_digest"]
    recipients_list = []
    for email, buckets in recipient_map.items():
        # Most urgent first, since anything past the cap is only summarised.
        buckets["stale"].sort(key=lambda x: x["stale_days"], reverse=True)
        buckets["expiring"].sort(key=lambda x: x["days_left"])

        stale, expiring = buckets["stale"], buckets["expiring"]
        entry = {
            "email": email,
            "expiring": expiring[:max_items],
            "stale": stale[:max_items],
        }
        # Never drop demands silently: tell the reader what was left out, so a
        # capped digest reads as "10 of 61" rather than looking complete.
        if len(stale) > max_items or len(expiring) > max_items:
            entry["truncated"] = {
                "stale_shown": len(entry["stale"]),
                "stale_total": len(stale),
                "expiring_shown": len(entry["expiring"]),
                "expiring_total": len(expiring),
            }
        recipients_list.append(entry)

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


def _business_days_between(start: date, end: date) -> int:
    """Weekdays strictly after *start*, up to and including *end*.

    Mon->Tue is 1; Fri->Mon is also 1, because no OIR file is produced at
    the weekend so no update could have happened then.
    """
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:      # Mon-Fri
            days += 1
    return days


def _stale_days(last_content_change_date_str: str | None, today: date, default: int) -> int:
    """Age of a demand's last content change.

    Counted in BUSINESS days by default. The OIR file is weekday-only, so a
    demand can never last-change on a weekend; with calendar days the 4-5 day
    L2 window falls entirely on Sat+Sun every Thursday, making that tier
    unreachable and escalating L1 straight to the Account Delivery Head.
    """
    if not last_content_change_date_str:
        return default
    try:
        changed = date.fromisoformat(last_content_change_date_str[:10])
    except ValueError:
        return default
    if _STALENESS_CFG.get("use_business_days", True):
        return _business_days_between(changed, today)
    return (today - changed).days


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


def _escalation_tier(stale_days: int, l2: int, adh: int) -> tuple[str, list[str]]:
    """Who gets told, and under which rule tag.

    The ladder adds people rather than replacing them, so the owner stays on
    the thread as it escalates above them.

    The top tier goes to the Account Delivery Head, NOT to the file's
    SL_DM_NAME: that column holds the same person as TM_NAME on 206 of 209
    rows, so "escalating" to it merely re-sends to whoever already had the
    first nudge.
    """
    if stale_days >= adh:
        return "ESCALATION_ADH", ["pm", "tm", "em", "adh"]
    if stale_days >= l2:
        return "ESCALATION_L2", ["pm", "tm", "em"]
    return "STALE_2D", ["pm", "tm"]


def _recipient_fields(roles: list[str]) -> list[str]:
    """Map roles to the demand fields holding their address.

    'adh' is deliberately absent: it is one configured person for the whole
    account, not a per-demand column, and is added in run_rules().
    """
    mapping = {
        "pm": "PMEmail",
        "tm": "TMEmail",
        "em": "EMEmail",
    }
    return [mapping[r] for r in roles if r in mapping]


def account_delivery_head_email() -> str:
    """The single escalation contact for the account, from configuration.

    Returns "" when unset, in which case ADH-tier demands still notify the
    PM/TM/EM and a warning is logged -- better than dropping the escalation
    silently.
    """
    return os.environ.get("ACCOUNT_DELIVERY_HEAD_EMAIL", "").strip()


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
