# OIR Autonomous Agent Platform

Autonomous multi-agent system that ingests the daily TD Bank OIR file, detects stale and expiring demands, notifies owners via Microsoft Teams, and writes structured updates back to Dataverse.

## Architecture

```
SharePoint (daily OIR .xlsx) → Logic App trigger
  → IngestOIR Function (parse, hash, upsert → Dataverse)
  → DetectExceptions Function (09:00 IST, rules 1-3)
  → Foundry Digest Agent → Teams Adaptive Cards
  → Reply Interpretation Agent → ApplyUpdate Function → Dataverse
```

## Deployment

See [docs/runbook.md](docs/runbook.md) for the full step-by-step sequence
(infra → Dataverse → Foundry agents → Function deploy → Logic App → Teams
bot → shadow mode). Provisioning scripts:

- `infra/deploy_infra.ps1` — Azure resources (Key Vault, App Insights, Storage, Function App) + service principal, via `az`.
- `infra/provision_dataverse.py` — creates the four `oir_*` Dataverse tables directly via the Web API (no `pac` CLI needed).
- `agents/deploy_agents.py` — registers the four Foundry agents from their YAML definitions.

## Sprint 1 checklist (run before enabling live notifications)

- [ ] Provision Dataverse + create four tables (`oir_demand`, `oir_snapshot_history`, `oir_interaction_log`, `oir_person_map`)
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
  shared/                    ← models, telemetry, Graph client
agents/                      ← Foundry agent YAML definitions
cards/                       ← Adaptive Card JSON templates
bot/                         ← Teams Bot Framework app
logicapps/                   ← Logic App definition (SharePoint trigger)
infra/                       ← Bicep IaC
tests/                       ← pytest suite
docs/
  runbook.md
```

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Environment variables required

| Variable | Description |
|---|---|
| `DATAVERSE_URL` | e.g. `https://org.crm.dynamics.com` |
| `AZURE_CLIENT_ID` | Service principal |
| `AZURE_TENANT_ID` | Entra ID tenant |
| `AZURE_CLIENT_SECRET` | Stored in Key Vault; injected at runtime |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | Application Insights |
| `TEAMS_BOT_APP_ID` | Bot Framework app ID |
| `TEAMS_BOT_APP_PASSWORD` | Bot Framework secret |
| `PMO_TEAMS_WEBHOOK_URL` | Alert channel webhook |
