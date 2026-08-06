"""Create or update the four OIR agents in an Azure AI Foundry project.

Reads each *.yaml definition in this directory and registers it as a Foundry
Agent (Assistants API) via the azure-ai-projects SDK. Idempotent: an agent
whose `name` already exists in the project is updated in place, never
duplicated.

Required environment variables:
    FOUNDRY_PROJECT_ENDPOINT   e.g. https://<project>.services.ai.azure.com/api/projects/<project-name>
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET

Optional:
    DIGEST_MODEL / REPLY_MODEL / TREND_MODEL / ORCHESTRATOR_MODEL
        override the `model.deployment` value from each YAML file.

Usage:
    pip install -r requirements-deploy.txt
    python agents/deploy_agents.py [--dry-run]

Writes agents/.deployed_agents.json mapping agent name -> Foundry agent ID.
Put the digest-agent and reply-interpreter IDs into the Function App's
FOUNDRY_DIGEST_AGENT_ID / FOUNDRY_REPLY_INTERPRETER_AGENT_ID settings --
functions/shared/foundry_client.py drives the thread/run API directly using
those IDs (see docs/runbook.md). The service principal used by the Functions
at runtime needs its own "Azure AI Developer" role grant on this Foundry
account/project; it's separate from whatever identity runs this script.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from azure.identity import ClientSecretCredential

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("deploy_agents")

AGENTS_DIR = Path(__file__).resolve().parent
DEPLOYED_MAP_PATH = AGENTS_DIR / ".deployed_agents.json"

AGENT_FILES = ["digest_agent.yaml", "reply_interpreter.yaml", "trend_agent.yaml", "orchestrator.yaml"]

_MODEL_OVERRIDE_ENV = {
    "digest-agent": "DIGEST_MODEL",
    "reply-interpreter": "REPLY_MODEL",
    "trend-agent": "TREND_MODEL",
    "orchestrator": "ORCHESTRATOR_MODEL",
}


def _load_agent_def(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_tools(agent_def: dict) -> list[dict]:
    tools = []
    for tool in agent_def.get("tools") or []:
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return tools


def deploy_one(client, agent_def: dict, existing_by_name: dict, dry_run: bool) -> str:
    name = agent_def["name"]
    model = os.environ.get(_MODEL_OVERRIDE_ENV.get(name, ""), "") or agent_def["model"]["deployment"]
    instructions = agent_def["system_prompt"]
    tools = _build_tools(agent_def)

    existing = existing_by_name.get(name)
    if dry_run:
        action = "update" if existing else "create"
        logger.info("[dry-run] would %s agent '%s' (model=%s, tools=%d)", action, name, model, len(tools))
        return existing.id if existing else "dry-run-id"

    if existing:
        logger.info("Updating existing agent '%s' (id=%s)", name, existing.id)
        client.agents.update_agent(
            existing.id,
            model=model,
            instructions=instructions,
            tools=tools,
        )
        return existing.id

    logger.info("Creating agent '%s' (model=%s)", name, model)
    created = client.agents.create_agent(
        model=model,
        name=name,
        instructions=instructions,
        tools=tools,
    )
    return created.id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        logger.error("Missing required environment variable: FOUNDRY_PROJECT_ENDPOINT")
        return 1

    try:
        from azure.ai.projects import AIProjectClient
    except ImportError:
        logger.error(
            "azure-ai-projects is not installed. Run: pip install -r requirements-deploy.txt"
        )
        return 1

    # Prefer an explicit service principal (used by the deployed Functions at
    # runtime); fall back to the caller's own `az login` session for
    # interactive/admin use like this script.
    if os.environ.get("AZURE_CLIENT_SECRET"):
        credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
    else:
        from azure.identity import AzureCliCredential
        logger.info("AZURE_CLIENT_SECRET not set -- using the current 'az login' session")
        credential = AzureCliCredential()

    client = AIProjectClient(endpoint=endpoint, credential=credential)

    existing_agents = list(client.agents.list_agents())
    existing_by_name = {a.name: a for a in existing_agents}

    deployed: dict[str, str] = {}
    for filename in AGENT_FILES:
        agent_def = _load_agent_def(AGENTS_DIR / filename)
        agent_id = deploy_one(client, agent_def, existing_by_name, args.dry_run)
        deployed[agent_def["name"]] = agent_id

    if not args.dry_run:
        with open(DEPLOYED_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(deployed, f, indent=2)
        logger.info("Wrote agent ID map to %s", DEPLOYED_MAP_PATH)

    logger.info("Deployment complete: %s", deployed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
