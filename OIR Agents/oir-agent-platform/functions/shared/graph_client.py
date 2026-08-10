"""Microsoft Graph client for owner display-name → UPN resolution.

Results are cached in the OIR Person Map SharePoint list to avoid
redundant Graph calls and to survive transient Graph outages.

Security: uses client-credentials flow (app-only); no delegated tokens.
All names from the OIR file are treated as untrusted input — they are
passed as filter values only, never interpolated into query paths.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from azure.identity import ClientSecretCredential

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


class GraphClient:
    """Thin wrapper around Microsoft Graph for user resolution."""

    def __init__(self) -> None:
        tenant_id = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        client_secret = os.environ["AZURE_CLIENT_SECRET"]

        self._credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
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
