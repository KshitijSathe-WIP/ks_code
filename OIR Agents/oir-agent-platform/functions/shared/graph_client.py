"""Microsoft Graph client for owner display-name → UPN resolution.

Results are cached in the PersonMap container in Cosmos DB to avoid
redundant Graph calls and to survive transient Graph outages.

Security: app-only auth, no delegated tokens. All names from the OIR file
are treated as untrusted input — they are passed as filter values only,
never interpolated into query paths.

AUTH: prefers the Function App's system-assigned managed identity when
running in Azure, falling back to the sp-oir-dev service principal for
local/CLI dev. The Graph *application permissions* (User.Read.All,
GroupMember.Read.All) must be admin-consented onto whichever identity is
in play — see docs/decisions/0007-single-permission-request-no-secrets.md.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


def _get_credential():
    """Managed identity in Azure, service principal locally.

    Mirrors cosmos_client/foundry_client so all three outbound integrations
    use one identity in production and no secret is stored anywhere.
    """
    if os.environ.get("IDENTITY_ENDPOINT"):
        logger.info("Using the Function App's managed identity for Graph auth")
        return ManagedIdentityCredential()
    logger.info("No managed identity available -- falling back to sp-oir-dev service principal")
    return ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )


class GraphClient:
    """Thin wrapper around Microsoft Graph for user resolution."""

    def __init__(self) -> None:
        self._credential = _get_credential()
        self._http = httpx.Client(timeout=10.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_email(self, display_name: str) -> Optional[str]:
        """Return the UPN for *display_name*, or None if unresolvable."""
        display_name = display_name.strip()
        if not display_name:
            return None

        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        # OData $filter — display_name is a value, not a query fragment.
        # httpx encodes query params safely; no injection risk.
        params = {
            "$filter": f"displayName eq '{display_name}'",
            "$select": "id,displayName,mail,userPrincipalName",
            "$top": "5",
        }

        try:
            resp = self._http.get(
                f"{_GRAPH_BASE}/users",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Graph query failed for '%s': %s", display_name, exc)
            return None
        except httpx.RequestError as exc:
            logger.warning("Graph network error for '%s': %s", display_name, exc)
            return None

        users = resp.json().get("value", [])
        if not users:
            logger.warning("No Graph user found for display name '%s'", display_name)
            return None

        if len(users) > 1:
            logger.warning(
                "Ambiguous display name '%s' — %d matches, using first",
                display_name, len(users),
            )

        user = users[0]
        return user.get("mail") or user.get("userPrincipalName")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        token_obj = self._credential.get_token(_SCOPE)
        return token_obj.token
