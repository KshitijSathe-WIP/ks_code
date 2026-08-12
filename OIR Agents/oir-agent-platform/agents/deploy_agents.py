"""Create or update the four OIR agents in an Azure AI Foundry project.

Reads each *.yaml definition in this directory and registers it as a v1
Foundry Agent (`/agents` surface) via azure-ai-projects 2.x. Idempotent:
each run publishes a new *version* of the named agent -- Foundry keeps the
version history and serves the latest, so re-running never duplicates an
agent.

NOTE ON API SURFACE: an earlier version of this script used
azure-ai-projects 1.x, whose create_agent() writes to the legacy
`/assistants` endpoint. Agents created there do not appear in the Foundry
portal, which reads the v1 `/agents` surface. See
docs/decisions/0004-foundry-v1-agents-api.md.

Required environment variables:
    FOUNDRY_PROJECT_ENDPOINT   e.g. https://<account>.services.ai.azure.com/api/projects/<project-name>

Auth: uses AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET when AZURE_CLIENT_SECRET
is set, otherwise falls back to the caller's `az login` session.

Optional:
    DIGEST_MODEL / REPLY_MODEL / TREND_MODEL / ORCHESTRATOR_MODEL
        override the `model.deployment` value from each YAML file.

Usage:
    pip install -r requirements-deploy.txt
    python agents/deploy_agents.py [--dry-run]

Writes agents/.deployed_agents.json mapping agent name -> latest version id.
v1 agents are addressed by NAME (not an `asst_...` id), so the Function App
settings are FOUNDRY_DIGEST_AGENT_NAME / FOUNDRY_REPLY_INTERPRETER_AGENT_NAME.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    FunctionTool,
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonObject,
)
from azure.identity import AzureCliCredential, ClientSecretCredential

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


def _build_tools(agent_def: dict) -> list[FunctionTool]:
    """Map the YAML `tools:` list onto v1 FunctionTool objects."""
    tools = []
    for tool in agent_def.get("tools") or []:
        tools.append(
            FunctionTool(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=tool.get("parameters", {"type": "object", "properties": {}}),
                strict=False,
            )
        )
    return tools


def _build_definition(agent_def: dict) -> PromptAgentDefinition:
    name = agent_def["name"]
    model = os.environ.get(_MODEL_OVERRIDE_ENV.get(name, ""), "") or agent_def["model"]["deployment"]
    params = agent_def["model"].get("parameters", {}) or {}

    kwargs: dict = {
        "model": model,
        "instructions": agent_def["system_prompt"],
    }

    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]

    # JSON-mode agents (reply-interpreter, orchestrator) declare
    # response_format: {type: json_object} in their YAML.
    response_format = params.get("response_format") or {}
    if response_format.get("type") == "json_object":
        kwargs["text"] = PromptAgentDefinitionTextOptions(format=TextResponseFormatJsonObject())

    tools = _build_tools(agent_def)
    if tools:
        kwargs["tools"] = tools

    return PromptAgentDefinition(**kwargs)


def deploy_one(client: AIProjectClient, agent_def: dict, dry_run: bool) -> str:
    name = agent_def["name"]
    definition = _build_definition(agent_def)
    description = " ".join((agent_def.get("description") or "").split())[:1000]

    if dry_run:
        logger.info(
            "[dry-run] would publish agent '%s' (model=%s, tools=%d, json_mode=%s)",
            name, definition.model, len(getattr(definition, "tools", None) or []),
            getattr(definition, "text", None) is not None,
        )
        return "dry-run-version"

    logger.info("Publishing agent '%s' (model=%s)", name, definition.model)
    version = client.agents.create_version(
        agent_name=name,
        definition=definition,
        description=description,
    )
    logger.info("  -> version id=%s", version.id)
    return version.id


def _get_credential():
    if os.environ.get("AZURE_CLIENT_SECRET"):
        return ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
    logger.info("AZURE_CLIENT_SECRET not set -- using the current 'az login' session")
    return AzureCliCredential()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        logger.error("Missing required environment variable: FOUNDRY_PROJECT_ENDPOINT")
        return 1

    client = AIProjectClient(endpoint=endpoint, credential=_get_credential(), allow_preview=True)

    deployed: dict[str, str] = {}
    for filename in AGENT_FILES:
        agent_def = _load_agent_def(AGENTS_DIR / filename)
        deployed[agent_def["name"]] = deploy_one(client, agent_def, args.dry_run)

    if not args.dry_run:
        with open(DEPLOYED_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(deployed, f, indent=2)
        logger.info("Wrote agent version map to %s", DEPLOYED_MAP_PATH)

    logger.info("Deployment complete: %s", deployed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
