"""Applying a demand update, independent of how it arrived.

Two entry points now write demands -- the ApplyUpdate HTTP endpoint and the
web form (functions/update_form) -- and a third, inbound email, is plausible
later. Each of them has to validate the status, skip no-op writes, recompute
the content hash, reset the staleness clock and the escalation ladder, and
log every field change. Duplicating that per channel is how the two paths
quietly stop agreeing about what an update means.

So the rules live here, and each channel only handles its own transport.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional

from functions.ingest_oir.hashing import content_hash
from functions.shared.models import (
    InteractionLog,
    VALID_STATUSES,
    ValidationError,
)

logger = logging.getLogger(__name__)


def validate_status(value: str) -> None:
    if value not in VALID_STATUSES:
        raise ValidationError(
            f"'{value}' is not a recognised Remarks Status. "
            f"Expected one of: {', '.join(sorted(VALID_STATUSES))}"
        )


def validate_future_date(iso_str: str) -> None:
    try:
        parsed = date.fromisoformat(iso_str)
    except (TypeError, ValueError):
        raise ValidationError(f"'{iso_str}' is not a valid date (expected YYYY-MM-DD)")
    if parsed < date.today():
        raise ValidationError(f"End date {iso_str} is in the past")


def apply_demand_update(
    db,
    demand_id: str,
    actor_email: str,
    action: str = "SUBMIT",
    comments: Optional[str] = None,
    remarks_status: Optional[str] = None,
    dem_end_date: Optional[str] = None,
    channel: str = "API",
) -> dict[str, Any]:
    """Apply one update and return a summary of what changed.

    Raises ValidationError for bad input. Callers are responsible for
    authorisation *before* calling this -- it is deliberately not repeated
    here, so there is no chance of a caller assuming the other one did it.

    Returns {"status": "applied"|"no_change"|..., "fields_changed": [...]}.
    """
    existing = db.get_demand(demand_id)
    if existing is None:
        raise ValidationError(f"Demand '{demand_id}' not found")

    now = datetime.utcnow().isoformat() + "Z"

    if action == "NO_CHANGE":
        db.upsert_demand({"DemandID": demand_id, "LastNotifiedOn": now})
        _log(db, demand_id, "NO_CHANGE", existing.pm_email, actor_email, channel)
        return {"status": "recorded", "action": "NO_CHANGE", "fields_changed": []}

    updates: dict[str, Any] = {}
    changes: list[tuple[str, str, str]] = []

    if remarks_status is not None:
        validate_status(remarks_status)
        if remarks_status != existing.remarks_status:
            updates["RemarksStatus"] = remarks_status
            changes.append(("remarks_status", existing.remarks_status, remarks_status))

    if dem_end_date is not None:
        validate_future_date(dem_end_date)
        old_end = existing.dem_end_date.isoformat() if existing.dem_end_date else ""
        if dem_end_date != old_end:
            updates["DEMEndDate"] = dem_end_date
            changes.append(("dem_end_date", old_end, dem_end_date))

    if comments is not None and comments != existing.comments:
        updates["Comments"] = comments
        changes.append(("comments", existing.comments, comments))

    if not updates:
        # Submitting the value already on record is not an update, and must
        # not reset the staleness clock -- otherwise re-saving an unchanged
        # form would make a stale demand look freshly attended to.
        return {"status": "no_change", "fields_changed": []}

    effective_comments = updates.get("Comments", existing.comments)
    effective_status = updates.get("RemarksStatus", existing.remarks_status)

    updates.update({
        "DemandID": demand_id,
        "CommentsHash": content_hash(effective_comments, effective_status),
        "LastContentChangeDate": date.today().isoformat(),
        # The owner has acted, so the ladder starts again and the demand
        # becomes eligible to be chased afresh rather than re-escalated.
        "EscalationLevel": 0,
        "LastNotifiedOn": None,
        "SnoozeUntil": None,
    })

    db.upsert_demand(updates)
    for field, before, after in changes:
        _log(db, demand_id, "AUTO_UPDATED", existing.pm_email, actor_email,
             channel, field=field, before=before, after=after)

    return {"status": "applied", "fields_changed": [c[0] for c in changes]}


def _log(db, demand_id, event_type, recipient_email, actor_email, channel,
         field="", before="", after="") -> None:
    db.append_log(InteractionLog(
        interaction_id=str(uuid.uuid4()),
        demand_id=demand_id,
        event_type=event_type,
        recipient_email=recipient_email,
        actor_email=actor_email,
        channel=channel,
        field_changed=field,
        value_before=before or "",
        value_after=after or "",
    ))
