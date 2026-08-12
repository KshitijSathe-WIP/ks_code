"""Azure AI Foundry v1 Agents client, used in-process.

Both DetectExceptions and the Teams bot invoke Foundry agents through this
module instead of an intermediate HTTP wrapper service: no extra deployed
component, no extra network hop, no extra auth surface -- fewer independent
failure points.

API SURFACE: agents live on the v1 `/agents` surface and are addressed by
NAME (e.g. "digest-agent"), not by an `asst_...` id. Invocation goes
through the OpenAI Responses API against the agent's own scoped endpoint,
via AIProjectClient.get_openai_client(agent_name=...). See
docs/decisions/0004-foundry-v1-agents-api.md.

AUTH: authenticates as the Function App's own system-assigned managed
identity when running in Azure, falling back to the sp-oir-dev service
principal for local/CLI dev. See
docs/decisions/0003-foundry-uses-managed-identity.md.

PII: this Foundry account's content filter blocks prompts containing
person names and email addresses, so no PII is sent to a model. Callers
pass placeholder tokens and substitute real values into the returned text
afterwards -- see scrub_recipient()/restore_pii() and
docs/decisions/0005-no-pii-sent-to-foundry-agents.md. Free text we don't
control (Excel comments, Teams replies) can still trip the filter; that
surfaces as ContentFilteredError so callers can degrade gracefully rather
than crash.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

logger = logging.getLogger(__name__)

RECIPIENT_NAME_TOKEN = "{{RECIPIENT_NAME}}"

_project_client: Optional[AIProjectClient] = None
_openai_clients: dict[str, object] = {}


class FoundryAgentError(RuntimeError):
    """Raised when a Foundry agent call fails or returns no usable reply."""


class ContentFilteredError(FoundryAgentError):
    """Raised when Azure's content filter blocks the prompt.

    Most often PII (a person name or email address) in free text we don't
    control. Callers should degrade gracefully -- e.g. ask the user to use
    the Adaptive Card instead of free text -- rather than treat this as an
    outage.
    """


def _get_credential():
    """Prefer the Function App's managed identity (it holds Azure AI
    Developer on this Foundry account). IDENTITY_ENDPOINT is set by Azure
    Functions/App Service only when a managed identity is available, so it
    reliably distinguishes deployed-in-Azure from local/CLI dev.
    """
    if os.environ.get("IDENTITY_ENDPOINT"):
        logger.info("Using the Function App's managed identity for Foundry auth")
        return ManagedIdentityCredential()
    logger.info("No managed identity available -- falling back to sp-oir-dev service principal")
    return ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )


def _get_project_client() -> AIProjectClient:
    global _project_client
    if _project_client is None:
        _project_client = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=_get_credential(),
            allow_preview=True,   # required for agent-scoped endpoints
        )
    return _project_client


def _get_openai_client(agent_name: str):
    if agent_name not in _openai_clients:
        _openai_clients[agent_name] = _get_project_client().get_openai_client(agent_name=agent_name)
    return _openai_clients[agent_name]


def _is_content_filter_error(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("error", body).get("code") == "content_filter":
        return True
    return "content_filter" in str(exc)


def invoke_agent(agent_name: str, message: str) -> str:
    """Send *message* to the named v1 agent and return its reply text.

    The caller is responsible for ensuring *message* contains no PII --
    see scrub_recipient().
    """
    client = _get_openai_client(agent_name)
    try:
        response = client.responses.create(input=message)
    except Exception as exc:
        if _is_content_filter_error(exc):
            raise ContentFilteredError(
                f"Agent '{agent_name}' prompt was blocked by the content filter "
                f"(most likely PII in free text): {exc}"
            ) from exc
        raise FoundryAgentError(f"Agent '{agent_name}' call failed: {exc}") from exc

    reply = (response.output_text or "").strip()
    if not reply:
        raise FoundryAgentError(f"Agent '{agent_name}' returned an empty reply")
    return reply


def invoke_agent_json(agent_name: str, message: str) -> dict:
    """Like invoke_agent, but parses the reply as JSON (for JSON-mode agents)."""
    import json

    raw = invoke_agent(agent_name, message)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FoundryAgentError(f"Agent '{agent_name}' reply was not valid JSON: {raw[:500]}") from exc


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------

def scrub_recipient(recipient: dict) -> tuple[dict, str]:
    """Strip PII from a DetectExceptions recipient payload before it goes to a model.

    Returns (safe_payload, display_name). The recipient's email is dropped
    entirely (the model never needs it -- delivery is handled in code) and
    their name is replaced with RECIPIENT_NAME_TOKEN, which restore_pii()
    swaps back afterwards.

    The demand entries themselves carry no personal data -- only demand ids,
    project/role names, dates, and statuses -- so they pass through as-is.
    """
    email = recipient.get("email", "") or ""
    display_name = recipient.get("display_name") or _first_name_from_email(email)

    safe = {k: v for k, v in recipient.items() if k not in ("email", "display_name", "_shadow_original")}
    safe["display_name"] = RECIPIENT_NAME_TOKEN
    return safe, display_name


def restore_pii(text: str, display_name: str) -> str:
    """Substitute the real name back into an agent's generated text."""
    return text.replace(RECIPIENT_NAME_TOKEN, display_name)


def _first_name_from_email(email: str) -> str:
    """Best-effort human-ish first name from an email local part.

    'jane.doe@wipro.com' -> 'Jane'. Falls back to 'there' so a digest can
    still open with a natural greeting when nothing better is available.
    """
    local = (email.split("@", 1)[0] or "").strip()
    if not local:
        return "there"
    first = local.replace("_", ".").split(".")[0]
    return first.capitalize() if first else "there"
