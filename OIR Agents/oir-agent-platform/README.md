# OIR Autonomous Agent Platform

Autonomous multi-agent system that ingests the daily TD Bank OIR file, detects stale and expiring demands, notifies owners via Microsoft Teams, and writes structured updates back to SharePoint Lists.

> **Data store note:** the original spec (v1.0) specified Dataverse. This
> implementation uses SharePoint Lists instead — see
> [docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md](docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md)
> for why.

## Architecture

```
SharePoint (daily OIR .xlsx) → Logic App trigger
  → IngestOIR Function (parse, hash, upsert → OIR Demands SharePoint list)
  → DetectExceptions Function (09:00 IST, rules 1-3)
  → Foundry Digest Agent → Teams Adaptive Cards
  → Reply Interpretation Agent → ApplyUpdate Function → OIR Demands SharePoint list
```

## Deployment

See [docs/runbook.md](docs/runbook.md) for the full step-by-step sequence
(infra → SharePoint Lists → Foundry agents → Function deploy → Logic App →
Teams bot → shadow mode). Provisioning scripts:

- `infra/deploy_infra.ps1` — Azure resources (Key Vault, App Insights, Storage, Function App) + service principal, via `az`.
- `infra/provision_sharepoint_lists.py` — creates the four OIR SharePoint lists directly via Microsoft Graph.
- `agents/deploy_agents.py` — registers the four Foundry agents from their YAML definitions.

## Sprint 1 checklist (run before enabling live notifications)

- [ ] Provision the four SharePoint lists (`OIR Demands`, `OIR Snapshot History`, `OIR Interaction Log`, `OIR Person Map`)
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
  shared/                    ← models, telemetry, Graph client, SharePoint Lists client, Foundry client
agents/                      ← Foundry agent YAML definitions
cards/                       ← Adaptive Card JSON templates
bot/                         ← Teams Bot Framework app
logicapps/                   ← Logic App definition (SharePoint file-drop trigger)
infra/                       ← Bicep IaC + SharePoint Lists provisioning
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
| `SHAREPOINT_SITE_URL` | e.g. `https://<tenant>.sharepoint.com/sites/<site-name>` |
| `AZURE_CLIENT_ID` | Service principal |
| `AZURE_TENANT_ID` | Entra ID tenant |
| `AZURE_CLIENT_SECRET` | Stored in Key Vault; injected at runtime |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | Application Insights |
| `TEAMS_BOT_APP_ID` | Bot Framework app ID |
| `TEAMS_BOT_APP_PASSWORD` | Bot Framework secret |
| `PMO_TEAMS_WEBHOOK_URL` | Alert channel webhook |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `FOUNDRY_DIGEST_AGENT_ID` / `FOUNDRY_REPLY_INTERPRETER_AGENT_ID` | From `agents/.deployed_agents.json` |
