"""One-per-recipient links for the update form.

A digest is email, so the recipient arrives at the form with no session and
nothing to sign in with -- this tenant has no Teams and no M365 identity we
can use (ADR 0010). The link itself has to carry the proof of who they are.

WHY AN OPAQUE TOKEN RATHER THAN A SIGNED ONE

A signed token (HMAC over the email plus an expiry) would need no storage,
but it puts the person's address in a URL, and URLs leak: browser history,
corporate proxy logs, referrer headers, the "share this link" reflex. So the
token is instead 32 random bytes that mean nothing on their own, and the
email lives server-side in the ActionTokens container.

That also buys revocation -- deleting the row kills the link -- and Cosmos's
native TTL expires rows without a cleanup job.

WHAT THE TOKEN DOES NOT DO

It identifies; it does not authorise. Which demands the holder may change is
decided at submit time by assert_authorised() against the PM/TM/EM on each
demand, exactly as for the Teams path. So a leaked link exposes that
person's own demands, not everyone's, and a stale one grants nothing that
person did not already have.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CONTAINER_ACTION_TOKENS = "ActionTokens"

# Long enough to survive a weekend plus a holiday; short enough that a link
# found in an old mailbox is dead. Digests are daily, so a fresh link
# arrives long before this one lapses.
DEFAULT_TTL_DAYS = 7

_TOKEN_BYTES = 32   # 256 bits, URL-safe base64


class InvalidActionToken(Exception):
    """The token is unknown, malformed, revoked or expired."""


@dataclass(frozen=True)
class ActionToken:
    token: str
    email: str
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


def new_token_value() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def issue(db, email: str, ttl_days: int = DEFAULT_TTL_DAYS) -> ActionToken:
    """Mint a link token for *email* and persist it.

    A fresh token per digest rather than a reusable one: it keeps each link's
    lifetime bounded, and revoking a single day's mail does not lock the
    person out of tomorrow's.
    """
    email = (email or "").strip()
    if not email:
        raise ValueError("cannot issue an action token without an email")

    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    token = ActionToken(new_token_value(), email, expires_at)
    db.put_action_token(token, ttl_seconds=ttl_days * 86400)
    return token


def resolve(db, token_value: str) -> str:
    """Return the email a token was issued to, or raise InvalidActionToken.

    Cosmos TTL removes expired rows eventually rather than instantly, so the
    expiry is re-checked here instead of trusting their absence.
    """
    token_value = (token_value or "").strip()
    if not token_value:
        raise InvalidActionToken("no token supplied")

    record: Optional[ActionToken] = db.get_action_token(token_value)
    if record is None:
        # Deliberately vague: a precise "expired" vs "never existed" tells a
        # probing caller which of their guesses was once real.
        raise InvalidActionToken("this link is not valid")
    if record.is_expired:
        raise InvalidActionToken("this link has expired")
    return record.email


def build_url(base_url: str, token_value: str) -> str:
    """The link that goes in the digest.

    Only the token appears in the URL -- no email, no demand id -- so the
    address is not exposed in history, proxy logs or a forwarded link.
    """
    return f"{base_url.rstrip('/')}/api/update?t={token_value}"
