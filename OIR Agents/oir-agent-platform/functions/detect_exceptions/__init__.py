"""Azure Function: DetectExceptions

Timer-triggered daily at 09:00 IST (04:30 UTC).
Gated: aborts if today's ingestion has not yet completed successfully.

Runs the three detection rules, groups results by recipient, and invokes
the Digest Agent in Azure AI Foundry for each person with exceptions.

Generating a digest and delivering it are separate steps. Digests are always
generated for, and addressed to, the real recipient, then written to
InteractionLog whether or not anything is sent -- so shadow mode leaves a
reviewable record of what the agents would have said. Where a message
actually goes is decided by functions/shared/notifier.py, which defaults to
sending nothing at all. Teams is not an option from this tenant; see
docs/decisions/0010-email-first-channel-teams-not-possible.md.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import azure.functions as func
import httpx

from functions.shared.telemetry import track_metric, track_event
from functions.shared import foundry_client, notifier
from functions.shared.cosmos_client import CosmosDbClient
from functions.shared.models import InteractionLog
from .rules import run_rules

logger = logging.getLogger(__name__)

bp = func.Blueprint()

DIGEST_INSTRUCTION = (
    "Write the daily OIR digest message for the recipient described by the "
    "JSON below, following your system instructions.\n\n"
)

# 09:00 IST = 03:30 UTC
@bp.timer_trigger(schedule="0 30 3 * * *", arg_name="timer", run_on_startup=False)
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

    generated = delivered = refused = 0
    with CosmosDbClient() as db:
        for recipient in recipients:
            if not (recipient["stale"] or recipient["expiring"]):
                continue
            try:
                digest_text, display_name = _invoke_digest_agent(recipient)
            except foundry_client.AgentRefusedError as exc:
                # Skip rather than deliver: a refusal reaching an inbox is
                # worse than that person hearing nothing today.
                refused += 1
                logger.error("Digest for %s abandoned after repeated refusals: %s",
                             recipient["email"], exc)
                continue
            except foundry_client.FoundryAgentError as exc:
                logger.error("Failed to invoke Digest Agent for %s: %s",
                             recipient["email"], exc)
                continue
            if not digest_text:
                continue
            generated += 1

            # Delivery is gated separately (notifier.py): in shadow mode this
            # sends nothing and simply reports why.
            try:
                delivery = notifier.deliver_digest(
                    intended_to=recipient["email"],
                    subject=_digest_subject(recipient),
                    body=digest_text,
                    display_name=display_name,
                )
                if delivery.will_send:
                    delivered += 1
            except Exception as exc:
                logger.error("Digest for %s generated but not delivered: %s",
                             recipient["email"], exc)
                delivery = notifier.Delivery(recipient["email"], "", False,
                                             f"send failed: {exc}")

            # Recorded even when nothing was sent -- in shadow mode the stored
            # text is the only way to review what would have gone out.
            try:
                _record_digest(db, recipient, digest_text, delivery)
            except Exception as exc:
                logger.error("Failed to record digest for %s: %s",
                             recipient["email"], exc)

    track_metric("notify.generated_count", generated)
    track_metric("notify.delivered_count", delivered)
    track_metric("notify.refused_count", refused)
    track_event("DetectExceptions.Complete", {
        "run_date": today.isoformat(),
        "digests_generated": generated,
        "digests_delivered": delivered,
        "digests_refused": refused,
    })
    logger.info("DetectExceptions complete for %s: %d generated, %d delivered, %d refused",
                today.isoformat(), generated, delivered, refused)
    if refused:
        _alert_pmo(f"{refused} digest(s) abandoned after repeated agent refusals "
                   f"on {today.isoformat()} -- those owners were not contacted.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingestion_completed_today(today: date) -> bool:
    """Check the SnapshotHistory container for a row with today's date (proves ingestion ran)."""
    if not os.environ.get("COSMOS_ENDPOINT", "").startswith("http"):
        return True  # local dev: skip gate

    try:
        with CosmosDbClient() as db:
            return db.has_snapshot_for_date(today)
    except Exception as exc:
        logger.warning("Cannot verify ingestion gate: %s", exc)
        return False


def _invoke_digest_agent(recipient: dict) -> tuple[str, str]:
    """Call the Foundry Digest Agent for one recipient.

    Returns (digest_text, display_name).

    No PII is sent to the model: scrub_recipient() drops the email and
    replaces the name with a placeholder, which restore_pii() swaps back
    into the generated text afterwards. This both satisfies the account's
    PII content filter and keeps personal data out of the model entirely --
    see docs/decisions/0005-no-pii-sent-to-foundry-agents.md.

    The digest is always generated for the REAL recipient, whatever the
    delivery settings say. Redirecting here instead would rewrite the
    greeting -- display_name is derived from the email -- so a test copy
    would open "Hi Kshitij" rather than showing what the actual owner would
    have read. Where a message goes is a delivery concern, and lives in
    functions/shared/notifier.py.
    """
    agent_name = os.environ.get("FOUNDRY_DIGEST_AGENT_NAME", "")
    if not agent_name:
        logger.warning("FOUNDRY_DIGEST_AGENT_NAME not set; skipping agent invocation")
        return "", ""

    safe_payload, display_name = foundry_client.scrub_recipient(recipient)
    # The JSON is prefixed with an explicit request rather than sent bare.
    # A lone JSON blob carries no instruction, and the model intermittently
    # answers "I cannot assist with that request": measured over 15 calls,
    # 10/15 refusals bare versus 1/15 with this prefix.
    prompt = DIGEST_INSTRUCTION + json.dumps({"recipient": safe_payload})
    raw = foundry_client.invoke_agent(agent_name, prompt)
    return foundry_client.restore_pii(raw, display_name), display_name


def _digest_subject(recipient: dict) -> str:
    stale, expiring = len(recipient["stale"]), len(recipient["expiring"])
    total = (recipient.get("truncated") or {}).get("stale_total", stale)
    parts = []
    if stale:
        parts.append(f"{total} demand needs an update" if total == 1
                     else f"{total} demands need an update")
    if expiring:
        parts.append(f"{expiring} expiring")
    return "OIR: " + ", ".join(parts) if parts else "OIR daily digest"


def _record_digest(db, recipient: dict, digest_text: str, delivery) -> None:
    """Append the generated digest to the audit trail.

    Written whether or not it was delivered: in shadow mode nothing is sent,
    and the stored text is the only way to review what the agents would have
    said before any of it reaches a colleague. Delivery.reason is kept so a
    digest that was never sent is distinguishable from one that failed.
    """
    rules = sorted({i["rule"] for i in recipient["stale"]})
    for demand_id in sorted({i["demand_id"] for i in
                             recipient["stale"] + recipient["expiring"]})[:1] or [""]:
        db.append_log(InteractionLog(
            interaction_id=f"digest::{recipient['email']}::{datetime.now(timezone.utc).date().isoformat()}",
            demand_id=demand_id,
            event_type="NOTIFIED" if delivery.will_send else "GENERATED",
            recipient_email=recipient["email"],
            actor_email="system",
            channel="EMAIL" if delivery.will_send else "SHADOW",
            rule_triggered=",".join(rules),
            message_sent=digest_text,
            reply_parsed={
                "delivered_to": delivery.actual_to,
                "redirected": delivery.redirected,
                "reason": delivery.reason,
                "stale_count": len(recipient["stale"]),
                "expiring_count": len(recipient["expiring"]),
            },
        ))


def _alert_pmo(message: str) -> None:
    webhook_url = os.environ.get("PMO_TEAMS_WEBHOOK_URL", "")
    if not webhook_url:
        return
    try:
        with httpx.Client(timeout=10.0) as http:
            http.post(webhook_url, json={"text": f"⚠️ OIR DetectExceptions Alert: {message}"})
    except Exception as exc:
        logger.error("Failed PMO alert: %s", exc)
