"""Direct Azure AI Foundry Agent Service client (thread/run/poll), used in-process.

Both DetectExceptions and the Teams bot invoke Foundry agents through this
module instead of an intermediate HTTP wrapper service: no extra deployed
component, no extra network hop, no extra auth surface -- fewer independent
failure points. Uses the same service-principal credentials
(AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET) already used for SharePoint/Graph.

Each call creates a fresh thread. Callers here are single-turn (one digest
per person per day; one reply per Teams message), so there is no benefit to
thread reuse and it avoids unbounded thread accumulation in the project.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from azure.ai.agents.models import MessageRole, RunStatus
from azure.ai.projects import AIProjectClient
from azure.identity import ClientSecretCredential

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1

_client: Optional[AIProjectClient] = None


class FoundryAgentError(RuntimeError):
    """Raised when a Foundry agent run fails, expires, cancels, or returns no reply."""


def _get_client() -> AIProjectClient:
    global _client
    if _client is None:
        credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        _client = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=credential,
        )
    return _client


def invoke_agent(agent_id: str, message: str) -> str:
    """Send *message* to *agent_id* on a fresh thread; block until the run
    finishes and return the agent's reply text.
    """
    client = _get_client()
    agents = client.agents

    thread = agents.threads.create()
    agents.messages.create(thread_id=thread.id, role=MessageRole.USER, content=message)

    run = agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent_id,
        polling_interval=_POLL_INTERVAL_SECONDS,
    )

    if run.status != RunStatus.COMPLETED:
        raise FoundryAgentError(
            f"Agent '{agent_id}' run {run.id} ended with status '{run.status}': {run.last_error}"
        )

    reply = agents.messages.get_last_message_text_by_role(thread_id=thread.id, role=MessageRole.AGENT)
    if reply is None:
        raise FoundryAgentError(f"Agent '{agent_id}' run {run.id} completed but produced no reply message")

    return reply.text.value.strip()


def invoke_agent_json(agent_id: str, message: str) -> dict:
    """Like invoke_agent, but parses the reply as JSON (for JSON-mode agents)."""
    raw = invoke_agent(agent_id, message)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FoundryAgentError(f"Agent '{agent_id}' reply was not valid JSON: {raw[:500]}") from exc
