"""Authorisation for the ApplyUpdate function.

An update is authorised if the actor is:
  - the PM, TM, or EM on the demand record, OR
  - a member of the PMO Entra security group

Rejected updates are logged as REJECTED events in oir_interaction_log.
"""
from __future__ import annotations

import logging
import os

import httpx

from functions.shared.models import AuthorisationError

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def assert_authorised(actor_email: str, pm_email: str, tm_email: str, em_email: str) -> None:
    """Raise AuthorisationError if actor_email is not a permitted updater."""
    actor = actor_email.lower().strip()

    authorised_emails = {e.lower().strip() for e in [pm_email, tm_email, em_email] if e}
    if actor in authorised_emails:
        return

    if _is_pmo_member(actor):
        return

    raise AuthorisationError(
        f"'{actor_email}' is not authorised to update this demand. "
        f"Permitted: {sorted(authorised_emails)} + PMO group."
    )


def _is_pmo_member(actor_email: str) -> bool:
    """Whether actor_email may act as PMO (override the demand's own owners).

    Two mechanisms, in order:

    1. `PMO_MEMBER_EMAILS` -- a comma-separated allowlist. Needs no
       permissions at all, which is why it's the default: the Entra group
       lookup below requires admin-consented Graph application permissions
       this tenant has not granted (ADR 0008).
    2. `PMO_GROUP_ID` + Graph -- the original design, kept as the better
       long-term answer since group membership stays current on its own.
       Only attempted when `GRAPH_LOOKUP_ENABLED=true`.

    Returns False (deny the override) when neither is configured. That's the
    safe direction: the demand's own PM/TM/EM can still update it.
    """
    allowlist = {
        e.strip().lower()
        for e in os.environ.get("PMO_MEMBER_EMAILS", "").split(",")
        if e.strip()
    }
    if allowlist:
        return actor_email.lower().strip() in allowlist

    pmo_group_id = os.environ.get("PMO_GROUP_ID", "")
    graph_enabled = os.environ.get("GRAPH_LOOKUP_ENABLED", "false").lower() == "true"
    if not pmo_group_id or not graph_enabled:
        logger.warning(
            "No PMO override configured (set PMO_MEMBER_EMAILS, or PMO_GROUP_ID "
            "with GRAPH_LOOKUP_ENABLED=true); treating '%s' as non-PMO",
            actor_email,
        )
        return False

    token = _graph_token()
    with httpx.Client(timeout=10.0) as http:
        # Use transitiveMemberOf for nested group support
        resp = http.post(
            f"{_GRAPH_BASE}/users/{actor_email}/checkMemberGroups",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"groupIds": [pmo_group_id]},
        )
        if resp.is_success:
            return pmo_group_id in resp.json().get("value", [])
        logger.warning("PMO membership check failed for '%s': %s", actor_email, resp.status_code)
        return False


def _graph_token() -> str:
    """Reuse the shared Graph credential so the PMO membership check runs
    under the same identity (and the same admin-consented permissions) as
    owner-email resolution."""
    from functions.shared.graph_client import _get_credential

    return _get_credential().get_token("https://graph.microsoft.com/.default").token
