"""Email delivery for digests, with a redirect safety net.

Teams delivery is not possible from this tenant (ADR 0010): there is no
Teams licence -- POWER_BI_STANDARD is the only SKU -- and the recipients are
@wipro.com users in a different directory entirely. Email is the first
channel that can actually reach them.

THE SAFETY MODEL

Every recipient in this system is a real colleague, and a single bad run
would put 34 test emails into their inboxes. So the gates are arranged so
that the *only* way to reach a real recipient is to say so explicitly, and
every other combination -- including a misconfigured or half-configured one
-- either redirects or refuses:

    EMAIL_ENABLED != true            -> nothing is sent at all (the default)
    EMAIL_REDIRECT_TO set            -> everything goes there instead
    neither redirect nor explicit
    EMAIL_ALLOW_REAL_RECIPIENTS      -> REFUSED, loudly

Note what that last gate buys: clearing EMAIL_REDIRECT_TO does not "turn on"
real delivery. It fails closed. Going live is a deliberate two-flag act, not
something a stray config edit can do by accident.

A redirected message keeps the digest exactly as the recipient would have
seen it -- same greeting, same content -- and prepends a banner naming who
it was really for. That way the redirected copy is a faithful preview rather
than a different message that happens to be similar.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

REDIRECT_BANNER = "=" * 62


class EmailBlocked(RuntimeError):
    """Refused to send because the safety gates were not satisfied."""


@dataclass(frozen=True)
class Delivery:
    """The decision about where a message may go, before any send happens."""

    intended_to: str
    actual_to: str
    redirected: bool
    reason: str

    @property
    def will_send(self) -> bool:
        return bool(self.actual_to)


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


def resolve_delivery(intended_to: str) -> Delivery:
    """Decide where a digest for *intended_to* may actually be sent.

    Pure and side-effect free, so the routing decision can be tested and
    logged without a mail transport anywhere near it.
    """
    intended_to = (intended_to or "").strip()
    if not intended_to:
        return Delivery("", "", False, "no intended recipient")

    if not _flag("EMAIL_ENABLED"):
        return Delivery(intended_to, "", False,
                        "EMAIL_ENABLED is not true (shadow mode)")

    redirect_to = os.environ.get("EMAIL_REDIRECT_TO", "").strip()
    if redirect_to:
        return Delivery(intended_to, redirect_to, True,
                        f"redirected to {redirect_to}")

    if not _flag("EMAIL_ALLOW_REAL_RECIPIENTS"):
        # Fail closed: an empty redirect is treated as a misconfiguration,
        # never as permission to mail real people.
        return Delivery(intended_to, "", False,
                        "EMAIL_REDIRECT_TO is empty and "
                        "EMAIL_ALLOW_REAL_RECIPIENTS is not true")

    return Delivery(intended_to, intended_to, False, "live delivery")


def build_subject(delivery: Delivery, subject: str) -> str:
    """Prefix redirected mail so a test copy is never mistaken for the real thing."""
    if delivery.redirected:
        return f"[TEST -> {delivery.intended_to}] {subject}"
    return subject


def build_body(delivery: Delivery, body: str, display_name: str = "") -> str:
    """The digest, with a banner when this copy is not going to its owner.

    The digest itself is left untouched -- including its greeting to the
    real recipient -- so what you read is what they would have read.
    """
    if not delivery.redirected:
        return body

    who = f"{display_name} <{delivery.intended_to}>" if display_name else delivery.intended_to
    return (
        f"{REDIRECT_BANNER}\n"
        f"TEST COPY -- NOT DELIVERED TO THE RECIPIENT\n"
        f"Intended recipient: {who}\n"
        f"Sent to you instead because EMAIL_REDIRECT_TO is set.\n"
        f"The message below is exactly what they would have received.\n"
        f"{REDIRECT_BANNER}\n\n"
        f"{body}"
    )


def send_email(to: str, subject: str, body: str) -> str:
    """Send one plain-text email via Azure Communication Services.

    Authenticates with the Function App's managed identity where one is
    present, consistent with the no-secrets design (ADR 0007); falls back to
    a connection string for local runs.

    Returns the ACS message id.
    """
    sender = os.environ.get("EMAIL_SENDER_ADDRESS", "").strip()
    endpoint = os.environ.get("EMAIL_ACS_ENDPOINT", "").strip()
    conn_str = os.environ.get("EMAIL_ACS_CONNECTION_STRING", "").strip()
    if not sender:
        raise EmailBlocked("EMAIL_SENDER_ADDRESS is not set")
    if not (endpoint or conn_str):
        raise EmailBlocked("neither EMAIL_ACS_ENDPOINT nor "
                           "EMAIL_ACS_CONNECTION_STRING is set")

    # Imported lazily so the rest of the platform -- and the whole test suite
    # -- does not depend on the ACS SDK being installed.
    from azure.communication.email import EmailClient

    if endpoint:
        from azure.identity import DefaultAzureCredential
        client = EmailClient(endpoint, DefaultAzureCredential())
    else:
        client = EmailClient.from_connection_string(conn_str)

    message = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": to}]},
        "content": {"subject": subject, "plainText": body},
    }
    poller = client.begin_send(message)
    result = poller.result()
    return (result or {}).get("id", "")


def deliver_digest(intended_to: str, subject: str, body: str,
                   display_name: str = "") -> Delivery:
    """Route and send one digest, honouring every safety gate.

    Returns the Delivery decision so the caller can record what happened --
    including the cases where nothing was sent, which are the normal ones in
    shadow mode and must not look like failures.
    """
    delivery = resolve_delivery(intended_to)
    if not delivery.will_send:
        logger.info("Not emailing %s: %s", intended_to or "(unknown)", delivery.reason)
        return delivery

    send_email(
        to=delivery.actual_to,
        subject=build_subject(delivery, subject),
        body=build_body(delivery, body, display_name),
    )
    if delivery.redirected:
        logger.info("Digest for %s redirected to %s", delivery.intended_to,
                    delivery.actual_to)
    else:
        logger.warning("Digest sent to REAL recipient %s", delivery.actual_to)
    return delivery
