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
import re
import time
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

logger = logging.getLogger(__name__)

# A bare identifier, deliberately not name-shaped and not mustache-wrapped.
# Measured over 12 identical calls with only this value changed:
#   "Alex" (a real-looking name) -> 12/12 refusals
#   "{{RECIPIENT_NAME}}"         ->  2/12
#   "NAME_PLACEHOLDER"           ->  0/12
# A real name refuses every time -- the account's PII filter, which is the
# whole reason for ADR 0005 -- and the mustache braces read as "fill in this
# template from data you do not have", which the model also sometimes
# declines. Anything substituted here must stay unique enough for
# restore_pii() to swap back safely.
RECIPIENT_NAME_TOKEN = "NAME_PLACEHOLDER"

# Refusals arrive as ordinary text, not errors, so they have to be recognised
# by content. A digest containing one must never be emailed.
_REFUSAL_RE = re.compile(
    r"\b(i'm sorry|i am sorry|cannot assist|can't assist|unable to assist|"
    r"can(?:not|'t) help with (?:that|this))\b", re.IGNORECASE)

DIGEST_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2

_project_client: Optional[AIProjectClient] = None
_openai_clients: dict[str, object] = {}


class FoundryAgentError(RuntimeError):
    """Raised when a Foundry agent call fails or returns no usable reply."""


class AgentRefusedError(FoundryAgentError):
    """The agent declined the request on every attempt.

    Distinct from ContentFilteredError: nothing was blocked at the API
    level, the model simply answered with a refusal. Callers should skip
    the message entirely -- a refusal must never be delivered to a user.
    """


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

    Locally, a service principal is used only if one is fully configured;
    otherwise this falls back to the signed-in az CLI user. That ordering
    mirrors cosmos_client._get_credential() and matters for shadow runs:
    the no-secrets design (ADR 0007) means AZURE_CLIENT_SECRET is normally
    empty, and demanding it would make every local digest run impossible.
    """
    if os.environ.get("IDENTITY_ENDPOINT"):
        logger.info("Using the Function App's managed identity for Foundry auth")
        return ManagedIdentityCredential()

    sp = (os.environ.get("AZURE_TENANT_ID", ""),
          os.environ.get("AZURE_CLIENT_ID", ""),
          os.environ.get("AZURE_CLIENT_SECRET", ""))
    if all(sp):
        logger.info("Using the sp-oir-dev service principal for Foundry auth")
        return ClientSecretCredential(tenant_id=sp[0], client_id=sp[1], client_secret=sp[2])

    logger.info("No managed identity or service principal -- using the az CLI login")
    from azure.identity import AzureCliCredential
    return AzureCliCredential()


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


def _is_transient_error(exc: Exception) -> bool:
    """Server-side blips worth another attempt, as opposed to a bad request.

    A 400 means the prompt is wrong and will be wrong again; a 500, 429 or
    504 usually clears on retry. Observed in practice: an
    InternalServerError mid-run that succeeded immediately afterwards.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429, 500, 502, 503, 504)
    return any(s in type(exc).__name__ for s in
               ("InternalServerError", "RateLimit", "APITimeout", "APIConnection"))


def _is_content_filter_error(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("error", body).get("code") == "content_filter":
        return True
    return "content_filter" in str(exc)


def looks_like_refusal(text: str) -> bool:
    """Is this reply a refusal rather than a usable answer?

    Refusals also arrive *appended to a half-finished answer*: the response
    carries two output items and output_text concatenates them, producing
    text like "...(13 daysI'm sorry, but I cannot assist with that request."
    So this checks anywhere in the string, not just the start.
    """
    return bool(_REFUSAL_RE.search(text or ""))


def invoke_agent(agent_name: str, message: str,
                 max_attempts: int = DIGEST_MAX_ATTEMPTS) -> str:
    """Send *message* to the named v1 agent and return its reply text.

    The caller is responsible for ensuring *message* contains no PII --
    see scrub_recipient().

    Retries on refusals. Even with the PII-safe placeholder and an explicit
    instruction, roughly one call in ten still comes back as "I'm sorry, but
    I cannot assist with that request" -- sometimes glued onto a partial
    answer. Retrying is effective because the failure is non-deterministic;
    three attempts take the residual rate to well under 1%. If every attempt
    refuses we raise, so the caller skips delivery rather than emailing a
    half-written message.
    """
    client = _get_openai_client(agent_name)
    last_reply = ""

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.responses.create(input=message)
        except Exception as exc:
            if _is_content_filter_error(exc):
                raise ContentFilteredError(
                    f"Agent '{agent_name}' prompt was blocked by the content filter "
                    f"(most likely PII in free text): {exc}"
                ) from exc
            # A transient 500/429 is retried on the same footing as a refusal.
            # Without this a single blip drops that person's digest for the
            # day, which is indistinguishable from having nothing to say.
            if _is_transient_error(exc) and attempt < max_attempts:
                logger.warning("Agent '%s' transient error on attempt %d/%d: %s",
                               agent_name, attempt, max_attempts, exc)
                time.sleep(_BACKOFF_SECONDS * attempt)
                continue
            raise FoundryAgentError(f"Agent '{agent_name}' call failed: {exc}") from exc

        reply = (response.output_text or "").strip()
        if reply and not looks_like_refusal(reply):
            if attempt > 1:
                logger.info("Agent '%s' succeeded on attempt %d", agent_name, attempt)
            return reply

        last_reply = reply
        logger.warning("Agent '%s' %s on attempt %d/%d", agent_name,
                       "refused" if reply else "returned an empty reply",
                       attempt, max_attempts)

    raise AgentRefusedError(
        f"Agent '{agent_name}' refused or returned nothing on all "
        f"{max_attempts} attempts; last reply: {last_reply[:200]!r}"
    )


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
