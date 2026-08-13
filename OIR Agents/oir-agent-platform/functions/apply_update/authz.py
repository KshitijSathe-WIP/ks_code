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
    """Check whether actor_email is a member of the PMO Entra group."""
    pmo_group_id = os.environ.get("PMO_GROUP_ID", "")
    if not pmo_group_id:
        logger.warning("PMO_GROUP_ID not configured; PMO membership check skipped")
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
