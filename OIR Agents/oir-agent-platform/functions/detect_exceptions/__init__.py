"""Azure Function: DetectExceptions

Timer-triggered daily at 09:00 IST (04:30 UTC).
Gated: aborts if today's ingestion has not yet completed successfully.

Runs the three detection rules, groups results by recipient, and invokes
the Digest Agent in Azure AI Foundry for each person with exceptions.
Shadow mode: set SHADOW_MODE=true to email the PMO owner only, not live recipients.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import azure.functions as func
import httpx

from functions.shared.telemetry import track_metric, track_event
from functions.shared import foundry_client
from functions.shared.sharepoint_client import SharePointListsClient
from .rules import run_rules

logger = logging.getLogger(__name__)

app = func.FunctionApp()

# 09:00 IST = 03:30 UTC
@app.timer_trigger(schedule="0 30 3 * * *", arg_name="timer", run_on_startup=False)
def detect_exceptions(timer: func.TimerRequest) -> None:
    today = date.today()

    if not _ingestion_completed_today(today):
        msg = f"DetectExceptions aborted: ingestion not confirmed for {today.isoformat()}"
        logger.warning(msg)
        _alert_pmo(msg)
        return

    payload = run_rules(today)
    recipients = payload.get("recipients", [])

    track_metric("detect.stale_count", sum(len(r["stale"]) for r in recipients))
    track_metric("detect.expiring_count", sum(len(r["expiring"]) for r in recipients))

    shadow_mode = os.environ.get("SHADOW_MODE", "false").lower() == "true"

    # NOTE: "sent" here means "digest text generated", not "delivered to Teams" --
    # proactive Teams delivery (stored conversationReference per recipient) is
    # not yet implemented. See docs/runbook.md.
    sent = 0
    for recipient in recipients:
        if not (recipient["stale"] or recipient["expiring"]):
            continue
        try:
            digest_text = _invoke_digest_agent(recipient, shadow_mode=shadow_mode)
            if digest_text:
                sent += 1
        except foundry_client.FoundryAgentError as exc:
            logger.error("Failed to invoke Digest Agent for %s: %s", recipient["email"], exc)

    track_metric("notify.sent_count", sent)
    track_event("DetectExceptions.Complete", {"run_date": today.isoformat(), "recipients_notified": sent})
    logger.info("DetectExceptions complete: %d recipients notified for %s", sent, today.isoformat())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingestion_completed_today(today: date) -> bool:
    """Check the OIR Snapshot History list for a row with today's date (proves ingestion ran)."""
    if not os.environ.get("SHAREPOINT_SITE_URL", "").startswith("http"):
        return True  # local dev: skip gate

    try:
        with SharePointListsClient() as sp:
            return sp.has_snapshot_for_date(today)
    except Exception as exc:
        logger.warning("Cannot verify ingestion gate: %s", exc)
        return False


def _invoke_digest_agent(recipient: dict, shadow_mode: bool) -> str:
    """Call the Foundry Digest Agent for one recipient; return the generated digest text.

    This only generates the message text. Actually delivering it to Teams
    requires proactive messaging (a stored conversationReference per
    recipient, established when they first message the bot), which is not
    yet built -- see docs/runbook.md. For now the generated text is
    returned/logged so the digest content itself can be verified in shadow mode.
    """
    agent_id = os.environ.get("FOUNDRY_DIGEST_AGENT_ID", "")
    if not agent_id:
        logger.warning("FOUNDRY_DIGEST_AGENT_ID not set; skipping agent invocation")
        return ""

    target_email = recipient["email"]
    if shadow_mode:
        pmo_email = os.environ.get("PMO_OWNER_EMAIL", "")
        logger.info("SHADOW MODE: routing %s digest to PMO (%s)", target_email, pmo_email)
        recipient = {**recipient, "email": pmo_email, "_shadow_original": target_email}

    digest_text = foundry_client.invoke_agent(agent_id, json.dumps({"recipient": recipient}))
    logger.info("Digest generated for %s (%d chars) -- Teams delivery not yet implemented",
                recipient["email"], len(digest_text))
    return digest_text


def _alert_pmo(message: str) -> None:
    webhook_url = os.environ.get("PMO_TEAMS_WEBHOOK_URL", "")
    if not webhook_url:
        return
    try:
        with httpx.Client(timeout=10.0) as http:
            http.post(webhook_url, json={"text": f"⚠️ OIR DetectExceptions Alert: {message}"})
    except Exception as exc:
        logger.error("Failed PMO alert: %s", exc)
