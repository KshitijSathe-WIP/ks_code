# OIR Autonomous Agent Platform

Autonomous multi-agent system that ingests the daily TD Bank OIR file, detects stale and expiring demands, notifies owners via Microsoft Teams, and writes structured updates back to Cosmos DB.

> **Data store note:** the original spec (v1.0) specified Dataverse. The
> implementation moved to SharePoint Lists, then to Cosmos DB — see
> [docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md](docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md)
> and [docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md](docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md)
> for the full history and why.

## Architecture

```
SharePoint (daily OIR .xlsx) → Logic App trigger
  → IngestOIR Function (parse, hash, upsert → Demands container in Cosmos DB)
  → DetectExceptions Function (09:00 IST, rules 1-3)
  → Foundry Digest Agent → Teams Adaptive Cards
  → Reply Interpretation Agent → ApplyUpdate Function → Demands container in Cosmos DB
```

## Deployment

See [docs/runbook.md](docs/runbook.md) for the full step-by-step sequence
(infra → Cosmos DB → Foundry agents → Function deploy → Logic App →
Teams bot → shadow mode). Provisioning scripts:

- `infra/deploy_infra.ps1` — Azure resources (Key Vault, App Insights, Storage, Function App) + service principal, via `az`.
- `infra/provision_cosmos.py` — creates the `OIRPlatform` Cosmos DB database and its four containers.
- `agents/deploy_agents.py` — registers the four Foundry agents from their YAML definitions.

## Sprint 1 checklist (run before enabling live notifications)

- [ ] Provision the `OIRPlatform` database and its four containers (`Demands`, `SnapshotHistory`, `InteractionLog`, `PersonMap`)
- [ ] Deploy `IngestOIR` Azure Function
- [ ] Deploy `DetectExceptions` Azure Function
- [ ] Shadow-mode: verify stale list against 5 consecutive real files
- [ ] Confirm zero false-positives before proceeding to Sprint 2

## Repository layout

```
config.json                  ← all thresholds; no hard-coded values in code
functions/
  ingest_oir/                ← Azure Function: parse + upsert OIR file
  detect_exceptions/         ← Azure Function: daily rules engine
  apply_update/              ← Azure Function: write validated reply back
  shared/                    ← models, telemetry, Graph client, Cosmos DB client, Foundry client
agents/                      ← Foundry agent YAML definitions
cards/                       ← Adaptive Card JSON templates
bot/                         ← Teams Bot Framework app
logicapps/                   ← Logic App definition (SharePoint file-drop trigger)
infra/                       ← Bicep IaC + Cosmos DB provisioning
tests/                       ← pytest suite
docs/
  runbook.md
  decisions/                 ← architecture decision records
```

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Environment variables required

| Variable | Description |
|---|---|
| `COSMOS_ENDPOINT` | e.g. `https://td-bank-cosmos.documents.azure.com:443/` |
| `COSMOS_DATABASE` | Defaults to `OIRPlatform` |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `FOUNDRY_DIGEST_AGENT_NAME` / `FOUNDRY_REPLY_INTERPRETER_AGENT_NAME` | Agent *names* — v1 agents aren't `asst_` ids (ADR 0004) |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | Application Insights |
| `PMO_TEAMS_WEBHOOK_URL` | Alert channel webhook; blank disables alerting |
| `PMO_OWNER_EMAIL` / `SHADOW_MODE` | Shadow-mode routing target and switch |
| `PMO_MEMBER_EMAILS` | Comma-separated PMO authorisation allowlist (ADR 0008) |
| `GRAPH_LOOKUP_ENABLED` | Default `false`. Owner emails come from the OIR file; Graph lookup is an optional backstop needing admin consent (ADR 0008) |
| `TEAMS_BOT_APP_ID` | Bot Framework app ID (bot not yet registered) |

**The deployed app stores no secrets.** Cosmos, Foundry and Graph all
authenticate as the Function App's managed identity (ADRs 0003, 0006, 0007),
so there is no `AZURE_CLIENT_SECRET`, `COSMOS_KEY` or
`TEAMS_BOT_APP_PASSWORD` in app settings or in Key Vault. The following are
needed for **local development only**:

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | `sp-oir-dev` fallback used when no managed identity is present |
| `COSMOS_KEY` | Local dev, and `provision_cosmos.py` — which does control-plane work the data-plane role deliberately excludes |
