"""Azure Function: ApplyUpdate

HTTP-triggered by the Teams bot after the Reply Interpretation Agent
produces a validated ParsedReply that has passed the confidence gate.

Validates, authorises, writes to Dataverse, and logs every field change
to oir_interaction_log. Confirm-before-mutate for high-risk fields is
enforced in the bot layer (this function only receives pre-confirmed payloads).

Expected request body:
{
  "demand_id":       "D1234",
  "actor_email":     "pm@wipro.com",
  "action":          "SUBMIT" | "NO_CHANGE" | "SNOOZE",
  "comments":        "...",           // optional
  "remarks_status":  "Pending Offer", // optional
  "dem_end_date":    "2026-09-01",    // optional, ISO 8601
}
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import azure.functions as func

from functions.shared.models import (
    AuthorisationError,
    CONFIG,
    InteractionLog,
    VALID_STATUSES,
    ValidationError,
)
from functions.shared.telemetry import track_event
from functions.ingest_oir.hashing import content_hash
from functions.ingest_oir.dataverse_client import DataverseClient
from .authz import assert_authorised

logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="apply-update", methods=["POST"])
def apply_update(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    demand_id = (body.get("demand_id") or "").strip()
    actor_email = (body.get("actor_email") or "").strip()
    action = (body.get("action") or "SUBMIT").upper()

    if not demand_id or not actor_email:
        return func.HttpResponse("demand_id and actor_email are required", status_code=400)

    with DataverseClient() as dv:
        existing = dv.get_demand(demand_id)
        if existing is None:
            return func.HttpResponse(f"Demand '{demand_id}' not found", status_code=404)

        # -- Authorisation ---------------------------------------------------
        try:
            assert_authorised(actor_email, existing.pm_email, existing.tm_email, existing.em_email)
        except AuthorisationError as exc:
            _log(dv, InteractionLog(
                interaction_id=str(uuid.uuid4()),
                demand_id=demand_id,
                event_type="REJECTED",
                recipient_email=existing.pm_email,
                actor_email=actor_email,
                value_after=str(exc),
            ))
            return func.HttpResponse(str(exc), status_code=403)

        # -- Handle action types ---------------------------------------------
        if action == "NO_CHANGE":
            dv.upsert_demand({
                "oir_demandid": demand_id,
                "oir_last_notified_on": datetime.utcnow().isoformat() + "Z",
            })
            _log(dv, InteractionLog(
                interaction_id=str(uuid.uuid4()),
                demand_id=demand_id,
                event_type="NO_CHANGE",
                recipient_email=existing.pm_email,
                actor_email=actor_email,
            ))
            return func.HttpResponse(
                json.dumps({"status": "recorded", "action": "NO_CHANGE", "demand_id": demand_id}),
                status_code=200, mimetype="application/json",
            )

        if action == "SNOOZE":
            snooze_hours = CONFIG["notification"]["snooze_hours"]
            snooze_until = (datetime.utcnow() + timedelta(hours=snooze_hours)).isoformat() + "Z"
            dv.upsert_demand({
                "oir_demandid": demand_id,
                "oir_snooze_until": snooze_until,
                "oir_last_notified_on": datetime.utcnow().isoformat() + "Z",
            })
            _log(dv, InteractionLog(
                interaction_id=str(uuid.uuid4()),
                demand_id=demand_id,
                event_type="SNOOZED",
                recipient_email=existing.pm_email,
                actor_email=actor_email,
                value_after=snooze_until,
            ))
            return func.HttpResponse(
                json.dumps({"status": "snoozed_until", "snooze_until": snooze_until}),
                status_code=200, mimetype="application/json",
            )

        # -- SUBMIT: validate and apply field changes -------------------------
        updates: dict[str, Any] = {}
        change_logs: list[InteractionLog] = []

        new_comments = body.get("comments")
        new_status = body.get("remarks_status")
        new_end_date = body.get("dem_end_date")

        if new_status is not None:
            try:
                _validate_status(new_status)
            except ValidationError as exc:
                return func.HttpResponse(str(exc), status_code=422)
            if new_status != existing.remarks_status:
                updates["oir_remarks_status"] = new_status
                change_logs.append(_field_log(demand_id, actor_email, existing.pm_email,
                                              "remarks_status", existing.remarks_status, new_status))

        if new_end_date is not None:
            try:
                _validate_future_date(new_end_date)
            except ValidationError as exc:
                return func.HttpResponse(str(exc), status_code=422)
            old_end = existing.dem_end_date.isoformat() if existing.dem_end_date else ""
            if new_end_date != old_end:
                updates["oir_dem_end_date"] = new_end_date
                change_logs.append(_field_log(demand_id, actor_email, existing.pm_email,
                                              "dem_end_date", old_end, new_end_date))

        if new_comments is not None and new_comments != existing.comments:
            updates["oir_comments"] = new_comments
            change_logs.append(_field_log(demand_id, actor_email, existing.pm_email,
                                          "comments", existing.comments, new_comments))

        if not updates:
            return func.HttpResponse(
                json.dumps({"status": "no_change", "demand_id": demand_id}),
                status_code=200, mimetype="application/json",
            )

        effective_comments = updates.get("oir_comments", existing.comments)
        effective_status = updates.get("oir_remarks_status", existing.remarks_status)
        new_hash = content_hash(effective_comments, effective_status)

        updates.update({
            "oir_demandid": demand_id,
            "oir_comments_hash": new_hash,
            "oir_last_content_change_date": date.today().isoformat(),
            "oir_escalation_level": 0,
            "oir_last_notified_on": None,
            "oir_snooze_until": None,
        })

        dv.upsert_demand(updates)
        for entry in change_logs:
            _log(dv, entry)

    track_event("ApplyUpdate.Submit", {"demand_id": demand_id, "fields": list(updates.keys())})

    return func.HttpResponse(
        json.dumps({"status": "applied", "demand_id": demand_id, "fields_changed": list(updates.keys())}),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_status(value: str) -> None:
    if value not in VALID_STATUSES:
        raise ValidationError(
            f"Invalid remarks_status '{value}'. "
            f"Allowed values: {sorted(VALID_STATUSES)}"
        )


def _validate_future_date(iso_str: str) -> None:
    try:
        d = date.fromisoformat(iso_str)
    except ValueError as exc:
        raise ValidationError(f"Invalid date format '{iso_str}': {exc}") from exc
    if d < date.today():
        raise ValidationError(f"dem_end_date '{iso_str}' is in the past and cannot be set.")


def _field_log(demand_id, actor_email, recipient_email, field, before, after) -> InteractionLog:
    return InteractionLog(
        interaction_id=str(uuid.uuid4()),
        demand_id=demand_id,
        event_type="REPLIED",
        recipient_email=recipient_email,
        actor_email=actor_email,
        field_changed=field,
        value_before=str(before or ""),
        value_after=str(after or ""),
    )


def _log(dv: DataverseClient, entry: InteractionLog) -> None:
    try:
        dv.append_log(entry)
    except Exception as exc:
        logger.error("Failed to write interaction log: %s", exc)
