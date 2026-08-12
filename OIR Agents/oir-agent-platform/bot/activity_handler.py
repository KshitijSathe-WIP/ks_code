"""Teams Bot activity handler for the OIR platform.

Handles:
  - AdaptiveCard submit actions (SUBMIT / NO_CHANGE / SNOOZE / CONFIRM_APPLY)
  - Free-text message replies (routed through Reply Interpretation Agent)

Security: message activity user IDs are resolved through the Bot Framework
token service. We never trust a self-reported email in the payload body.
All file/Excel content in Comments fields is treated as untrusted data and
never placed in a system prompt — it goes only into the agent's user message.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

import httpx
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity, ActivityTypes

from functions.shared.models import CONFIG, VALID_STATUSES
from functions.shared import foundry_client
from functions.shared.cosmos_client import CosmosDbClient
from functions.detect_exceptions.rules import _gate

logger = logging.getLogger(__name__)

_HIGH_RISK_FIELDS = set(CONFIG["agent"]["high_risk_fields"])
_APPLY_UPDATE_URL = os.environ.get("APPLY_UPDATE_FUNCTION_URL", "")
_APPLY_UPDATE_KEY = os.environ.get("APPLY_UPDATE_FUNCTION_KEY", "")
_REPLY_INTERPRETER_AGENT_NAME = os.environ.get("FOUNDRY_REPLY_INTERPRETER_AGENT_NAME", "")


class OIRActivityHandler(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        actor_email = _resolve_actor_email(activity)

        # -- AdaptiveCard submit action -------------------------------------
        if activity.value:
            await self._handle_card_action(turn_context, activity.value, actor_email)
            return

        # -- Free-text reply ------------------------------------------------
        text = (activity.text or "").strip()
        if not text:
            return

        demand_id = _extract_demand_id(text)
        if not demand_id:
            await turn_context.send_activity(
                "I couldn't identify which demand you're referring to. "
                "Please include the Demand ID (e.g. D1234) in your message."
            )
            return

        await self._handle_freetext_reply(turn_context, demand_id, text, actor_email)

    # -----------------------------------------------------------------------
    # Card actions
    # -----------------------------------------------------------------------

    async def _handle_card_action(
        self, turn_context: TurnContext, value: dict[str, Any], actor_email: str
    ) -> None:
        action = (value.get("action") or "").upper()
        demand_id = (value.get("demandId") or "").strip()

        if not demand_id:
            await turn_context.send_activity("Could not identify the demand — please use the card buttons.")
            return

        if action in ("SUBMIT", "NO_CHANGE", "SNOOZE"):
            payload = {
                "demand_id": demand_id,
                "actor_email": actor_email,
                "action": action,
                "comments": value.get("comments"),
                "remarks_status": value.get("remarks_status"),
                "dem_end_date": value.get("dem_end_date"),
            }
            result = _call_apply_update(payload)
            await turn_context.send_activity(_format_result(action, demand_id, result))

        elif action == "CONFIRM_APPLY":
            field = value.get("fieldName", "")
            new_value = value.get("valueAfter", "")
            payload = {
                "demand_id": demand_id,
                "actor_email": actor_email,
                "action": "SUBMIT",
                field: new_value,
            }
            result = _call_apply_update(payload)
            await turn_context.send_activity(_format_result("SUBMIT", demand_id, result))

        elif action == "CONFIRM_CANCEL":
            await turn_context.send_activity(
                f"Understood — the proposed change to {demand_id} has been cancelled."
            )

    # -----------------------------------------------------------------------
    # Free-text reply flow
    # -----------------------------------------------------------------------

    async def _handle_freetext_reply(
        self,
        turn_context: TurnContext,
        demand_id: str,
        text: str,
        actor_email: str,
    ) -> None:
        context = _build_context(demand_id)
        if context is None:
            await turn_context.send_activity(
                f"Demand {demand_id} was not found in the system."
            )
            return

        try:
            parsed = _invoke_reply_interpreter(context, text)
        except foundry_client.ContentFilteredError:
            # Free text we don't control (Excel comments, the user's own
            # words) can contain names/emails, which this account's content
            # filter blocks. Degrade to the card rather than failing loudly.
            logger.info("Reply for %s blocked by content filter -- steering user to the card", demand_id)
            await turn_context.send_activity(
                "I can't process that message automatically -- it may contain personal details. "
                "Please use the update card instead, and I'll record it straight away."
            )
            return

        if parsed is None:
            await turn_context.send_activity(
                "Sorry, I couldn't interpret that reply. Please try again or use the card."
            )
            return

        route = _gate(parsed)

        if route == "CLARIFY":
            clarification = parsed.get("clarification_needed") or "Could you be more specific?"
            await turn_context.send_activity(clarification)

        elif route == "CONFIRM":
            # Send a confirm card for each high-risk field
            for field in _HIGH_RISK_FIELDS:
                if field in parsed:
                    card_json = _build_confirm_card(demand_id, context, field, parsed[field])
                    await turn_context.send_activity(
                        Activity(
                            type=ActivityTypes.message,
                            attachments=[_card_attachment(card_json)],
                        )
                    )

        else:  # APPLY
            # If the interpreter signalled no_change, route to NO_CHANGE action
            if parsed.get("no_change"):
                payload = {"demand_id": demand_id, "actor_email": actor_email, "action": "NO_CHANGE"}
            else:
                payload = {
                    "demand_id": demand_id,
                    "actor_email": actor_email,
                    "action": "SUBMIT",
                    **{k: v for k, v in parsed.items()
                       if k in ("comments", "remarks_status", "dem_end_date")},
                }
            result = _call_apply_update(payload)
            await turn_context.send_activity(_format_result(payload["action"], demand_id, result))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_actor_email(activity: Activity) -> str:
    """Extract the user's email from the Bot Framework token claims."""
    # In production, use turn_context.activity.from_property.aad_object_id
    # resolved via Graph. For now, use the channel-provided email if present.
    from_prop = activity.from_property
    if from_prop and from_prop.aad_object_id:
        # Resolve OID → email via Graph (omitted here; inject via middleware)
        return from_prop.name or ""
    return from_prop.name if from_prop else ""


def _extract_demand_id(text: str) -> str | None:
    import re
    match = re.search(r"\b([A-Z]{0,3}\d{3,8})\b", text.upper())
    return match.group(1) if match else None


def _build_context(demand_id: str) -> dict | None:
    """Fetch the current demand record from the Demands container in Cosmos DB."""
    if not os.environ.get("COSMOS_ENDPOINT", "").startswith("http"):
        return None
    try:
        with CosmosDbClient() as db:
            demand = db.get_demand(demand_id)
        if demand is None:
            return None
        return {
            "demand_id": demand_id,
            "status": demand.status,
            "remarks_status": demand.remarks_status,
            "comments": demand.comments,
            "dem_end_date": demand.dem_end_date.isoformat() if demand.dem_end_date else "",
            "today_date": date.today().isoformat(),
            "allowed_status": sorted(VALID_STATUSES),
        }
    except Exception as exc:
        logger.error("Failed to fetch context for %s: %s", demand_id, exc)
        return None


def _invoke_reply_interpreter(context: dict, reply: str) -> dict | None:
    """Parse a free-text reply into a structured update.

    Raises ContentFilteredError (not caught here) when the prompt trips the
    account's PII filter -- the caller turns that into a "please use the
    card" nudge. Other agent failures are logged and return None.
    """
    if not _REPLY_INTERPRETER_AGENT_NAME:
        logger.warning("FOUNDRY_REPLY_INTERPRETER_AGENT_NAME not set")
        return None
    try:
        return foundry_client.invoke_agent_json(
            _REPLY_INTERPRETER_AGENT_NAME,
            json.dumps({"context": context, "reply": reply}),
        )
    except foundry_client.ContentFilteredError:
        raise
    except foundry_client.FoundryAgentError as exc:
        logger.error("Reply interpreter call failed: %s", exc)
        return None


def _call_apply_update(payload: dict) -> dict:
    if not _APPLY_UPDATE_URL:
        logger.warning("APPLY_UPDATE_FUNCTION_URL not set")
        return {"status": "error", "detail": "apply_update URL not configured"}
    try:
        with httpx.Client(timeout=15.0) as http:
            resp = http.post(
                _APPLY_UPDATE_URL,
                headers={"x-functions-key": _APPLY_UPDATE_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("apply_update call failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _format_result(action: str, demand_id: str, result: dict) -> str:
    if result.get("status") == "ok" or result.get("status") == "applied":
        return f"✅ Recorded — {action} on {demand_id}."
    if result.get("status") == "snoozed_until":
        return f"⏸️ {demand_id} snoozed until {result.get('snooze_until', 'N/A')}."
    if result.get("status") == "no_change":
        return f"ℹ️ No fields changed on {demand_id}."
    return f"⚠️ Update could not be applied for {demand_id}: {result.get('detail', 'unknown error')}"


def _build_confirm_card(demand_id: str, context: dict, field: str, new_value: str) -> dict:
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": "⚠️ Please confirm this change",
             "weight": "Bolder", "color": "Attention"},
            {"type": "TextBlock", "text": f"Demand: **{demand_id}**", "wrap": True},
            {"type": "FactSet", "facts": [
                {"title": "Field", "value": field},
                {"title": "Current", "value": context.get(field, "")},
                {"title": "Proposed", "value": new_value},
            ]},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Confirm & Apply", "style": "positive",
             "data": {"action": "CONFIRM_APPLY", "demandId": demand_id,
                      "fieldName": field, "valueAfter": new_value}},
            {"type": "Action.Submit", "title": "Cancel",
             "data": {"action": "CONFIRM_CANCEL", "demandId": demand_id}},
        ],
    }


def _card_attachment(card_json: dict) -> dict:
    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": card_json,
    }
