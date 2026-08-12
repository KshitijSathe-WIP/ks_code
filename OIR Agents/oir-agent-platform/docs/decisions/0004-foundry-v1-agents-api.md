# ADR 0004: Use the v1 Foundry Agents API, not the legacy Assistants API

**Status:** Accepted
**Date:** 2026-08-11 (evening)
**Owner:** Kshitij Sathe

## Context

The four OIR agents were originally created with `azure-ai-projects==1.0.0`,
whose `client.agents.create_agent()` writes to the legacy `/assistants`
endpoint (OpenAI Assistants-compatible, `asst_...` ids, threads/runs
invocation model).

They were reported as not visible in the Azure AI Foundry portal. Probing
the project's REST surface directly showed why -- there are two distinct
agent stores on the same project:

| Surface | Contents |
|---|---|
| `GET /assistants?api-version=v1` | `digest-agent`, `reply-interpreter`, `trend-agent`, `orchestrator` (ours) |
| `GET /agents?api-version=v1` | `Incident-RCA-Agent`, `lineage-agent`, `TestMyAPI` (the org's other agents) |

The nextgen Foundry portal reads `/agents`. Our four were on `/assistants`,
so they were genuinely invisible in the UI -- and notably, every other
agent in this project already lived on the v1 surface, so ours were the
outlier.

The original verification missed this because it was circular: the agents
were confirmed to exist by listing them through the same SDK/endpoint that
had created them, which can never detect writing to the wrong surface.

## Decision

Upgrade to `azure-ai-projects>=2.4` and manage the agents on the v1
`/agents` surface via `AgentsOperations.create_version()` with
`PromptAgentDefinition`.

Both capabilities that could have made this a dead end were verified
against the live service before migrating:
- **Function tools** (`trend-agent`'s read-only SQL tool) -> supported via
  `models.FunctionTool`.
- **JSON-mode output** (`reply-interpreter`, `orchestrator`) -> supported
  via `PromptAgentDefinitionTextOptions(format=TextResponseFormatJsonObject())`.

## Consequences

- **Agents are addressed by name, not id.** v1 agents have human-readable
  ids equal to their name (`digest-agent`), versioned as
  `digest-agent:1`. The app settings therefore change:
  `FOUNDRY_DIGEST_AGENT_ID` -> `FOUNDRY_DIGEST_AGENT_NAME`, same for the
  reply interpreter. `agents/.deployed_agents.json` now records the latest
  version id per agent rather than an opaque `asst_...` id.
- **Invocation model changes.** `/assistants` used create-thread ->
  add-message -> create-run -> poll. v1 agents are called through the
  OpenAI **Responses API** against an agent-scoped endpoint obtained from
  `AIProjectClient.get_openai_client(agent_name=...)`, which requires
  `allow_preview=True` on the client. `foundry_client.py` was rewritten
  accordingly; it is materially simpler (one call, no polling loop).
  Note the agent-scoped endpoint rejects the `agent` / `agent_reference`
  request property -- the agent is already implied by the endpoint.
- **Deployments are versioned.** Re-running `deploy_agents.py` publishes a
  new version (`digest-agent:2`, etc.) rather than mutating in place, so
  the script stays idempotent in the sense that matters (never duplicates
  an agent) while giving a rollback trail.
- The four legacy `/assistants` entries were deleted after the migration,
  so there is exactly one live definition per agent.
- `requirements.txt` / `requirements-deploy.txt` pin `azure-ai-projects>=2.4`.
  Note `azure-cli` (installed via pip in the dev environment) declares a
  conflicting `azure-ai-projects~=1.0.0` dependency; pip warns, but both
  the CLI and this SDK were verified working side by side afterwards.

## Lesson recorded

Verifying a write by reading through the same client that performed it
proves only self-consistency, not correctness. Where a resource is meant
to be visible or usable by another system (a portal, another service),
the check has to go through that other system's view -- or at minimum a
different API surface.
