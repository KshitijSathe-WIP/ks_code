# OIR Platform — Deployment Runbook

This is the manual sequence to stand up the dependent systems (Cosmos DB,
Azure infra, Foundry agents, Teams bot) from a clean tenant. Follow it in
order — later steps assume earlier ones are done. Every script here is
idempotent: safe to re-run after a partial failure.

Read [OIR_Autonomous_Agent_Implementation_Spec.md](../../OIR_Autonomous_Agent_Implementation_Spec.md)
first if you haven't — it's the source of truth for *why* each piece exists.
**Note:** that spec (v1.0) specifies Dataverse as the data store; the actual
implementation moved to SharePoint Lists, then to Cosmos DB — see
[docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md](decisions/0002-cosmos-db-instead-of-sharepoint-lists.md)
and [docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md](decisions/0001-sharepoint-lists-instead-of-dataverse.md)
for the full history and why, and treat this runbook (not the spec's §2/§4)
as authoritative on the data layer.

## Confirmed target environment (TD-BANK-POC, as of 2026-08-11)

| Value | Confirmed value |
|---|---|
| `COSMOS_ENDPOINT` | `https://td-bank-cosmos.documents.azure.com:443/` (existing account in this resource group) |
| `COSMOS_DATABASE` | `OIRPlatform` (new, isolated from the account's existing `IncidentRCA`/`lineage` databases) |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://td-bank.services.ai.azure.com/api/projects/TD-BANK` |
| Tenant ID (from the Foundry portal link) | `6efbfbdd-57af-4e28-9f2c-9b75f72a6ffe` — this is `wilmodel3.onmicrosoft.com`, an Azure/AI infrastructure sandbox with **no SharePoint Online or reliable Dataverse access** (see ADRs above) — **verify** with `az account show --query tenantId -o tsv` before creating the service principal in step 1 |

Copy [.env.example](../.env.example) to `.env` — it's pre-filled with the
Foundry and Cosmos endpoint URLs above. `COSMOS_KEY`, the service-principal
(`AZURE_CLIENT_ID`/`SECRET`), and downstream secrets from steps 1–6 below
still need to be added.

> Two abandoned attempts remain in this tenant at no cost if left alone, or
> can be deleted once this Cosmos DB approach is confirmed working:
> the Dataverse environment (`orge0db2320.crm8.dynamics.com`,
> `0687df9e-4e7c-e34e-97b0-eca63c7f61dd`), and nothing was actually created
> for SharePoint (that attempt failed before any resource was provisioned).

## 0. Prerequisites

| Requirement | Notes |
|---|---|
| Azure subscription with Owner/Contributor access | For resource group + service principal creation |
| An existing Cosmos DB account | `td-bank-cosmos` already exists in `TD-BANK-POC`; confirm with `az cosmosdb show --name <account> --resource-group <rg> --query "{kind:kind, publicNetworkAccess:publicNetworkAccess}"` — needs `kind: GlobalDocumentDB` (SQL/Core API) and public network access enabled (or a private endpoint you can reach) |
| Azure AI Foundry project | Existing project with a chat-completion model deployment (TD-BANK project has `gpt-4.1`, used by default) |
| Microsoft Teams admin access | To register the bot channel |
| `az` CLI | `pip install azure-cli` works if `winget`/MSI installs are blocked by a proxy |
| Python 3.11+ | Matches the Function App runtime |

```bash
pip install -r requirements.txt
pip install -r requirements-deploy.txt
```

## 1. Azure infrastructure (Key Vault, App Insights, Storage, Function App)

```powershell
az login
./infra/deploy_infra.ps1 -SubscriptionId <sub-id> -ResourceGroup rg-oir-dev -Location eastus2 -Environment dev
```

This also creates a service principal (`sp-oir-dev`) scoped to the resource
group and prints `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`
**once** — save them to a password manager immediately, then export them for
the remaining steps:

```powershell
$env:AZURE_TENANT_ID = "..."
$env:AZURE_CLIENT_ID = "..."
$env:AZURE_CLIENT_SECRET = "..."
```

Run `-WhatIf` first if you want to preview the deployment without applying it.

> This service principal is used for Graph (owner-email resolution) and
> Foundry (agent invocation) — **not** for Cosmos DB, which uses its own
> key-based auth (step 2). No RBAC role assignment is needed to reach
> Cosmos DB, which is why it sidesteps every wall Dataverse and SharePoint
> hit in this tenant.

## 2. Cosmos DB database and containers

Get the account's key (a plain Contributor-level `listKeys` read — no
`Microsoft.Authorization/roleAssignments/write` needed, unlike everything
else in this tenant):

```powershell
$env:COSMOS_ENDPOINT = "https://td-bank-cosmos.documents.azure.com:443/"
$env:COSMOS_KEY = az cosmosdb keys list --name td-bank-cosmos --resource-group TD-BANK-POC --query primaryMasterKey -o tsv
```

Then provision the `OIRPlatform` database and its four containers
(`Demands`, `SnapshotHistory`, `InteractionLog`, `PersonMap`):

```powershell
python infra/provision_cosmos.py --dry-run   # review planned calls first
python infra/provision_cosmos.py             # apply
```

The script is idempotent — it checks the database and each container by id
before creating anything, so re-running after a partial failure just fills
in what's missing. It never touches the account's other databases
(`IncidentRCA`, `lineage`).

**Verify:** [Azure Portal](https://portal.azure.com) → `td-bank-cosmos` →
**Data Explorer**, and confirm the `OIRPlatform` database and its four
containers exist.

## 3. Foundry agents

Requires the TD-BANK-POC project to have a chat-completion model deployment
— confirmed available: `gpt-4.1`, `gpt-4.1-mini`, `gpt-5.4-nano` (verified via
`az cognitiveservices account deployment list --name TD-BANK --resource-group
TD-BANK-POC`; note `gpt-4o` is **not** deployed here, despite being the
original spec's example). All four `agents/*.yaml` files use `gpt-4.1` by
default — override per-agent via `DIGEST_MODEL`/`REPLY_MODEL`/`TREND_MODEL`/
`ORCHESTRATOR_MODEL` if needed.

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://td-bank.services.ai.azure.com/api/projects/TD-BANK"
python agents/deploy_agents.py --dry-run
python agents/deploy_agents.py
```

This registers `digest-agent`, `reply-interpreter`, `trend-agent`, and
`orchestrator` from their YAML definitions and writes
`agents/.deployed_agents.json` with their agent IDs.

**Invocation model:** `detect_exceptions` and `bot/activity_handler.py` call
these agents directly via `functions/shared/foundry_client.py`, which drives
the Assistants thread/run API in-process (create thread → post message →
`create_and_process` → read reply). No separate wrapper service — fewer
moving parts, one fewer thing to deploy/monitor/secure. Set these app
settings from `agents/.deployed_agents.json`:

```
FOUNDRY_DIGEST_AGENT_ID=<digest-agent id>
FOUNDRY_REPLY_INTERPRETER_AGENT_ID=<reply-interpreter id>
```

**Auth:** `foundry_client.py` authenticates as the Function App's own
system-assigned managed identity when deployed (detected via the
`IDENTITY_ENDPOINT` app setting Azure sets automatically), falling back to
the `sp-oir-dev` service principal for local/CLI dev. Grant **Azure AI
Developer** to whichever identity you're actually using:

```bash
# Deployed Function App -- grant its managed identity (recommended; no
# app secret needed, and no separate request if Contributor/Key Vault
# grants already went to this identity by convention):
az role assignment create --assignee <functionApp principalId> --role "Azure AI Developer" \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account-name>

# Local/CLI dev -- grant sp-oir-dev instead:
az role assignment create --assignee <AZURE_CLIENT_ID> --role "Azure AI Developer" \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account-name>
```

See [docs/decisions/0003-foundry-uses-managed-identity.md](decisions/0003-foundry-uses-managed-identity.md)
for why this differs from `graph_client.py`, which still uses `sp-oir-dev`.

> **Known gap:** `_invoke_digest_agent` only *generates* the digest text —
> it does not deliver it to Teams. Proactive Teams messaging needs a stored
> `conversationReference` per recipient (created when they first message the
> bot) plus a call through the Bot Framework's proactive-message API; that
> piece isn't built yet. Until then, shadow-mode runs will produce and log
> digest text but won't post it anywhere.

## 4. Function App deployment

```bash
cd functions
func azure functionapp publish <functionAppName> --python
```

Then set the remaining app settings that `main.bicep` left blank
(`COSMOS_ENDPOINT`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `PMO_GROUP_ID`,
`PMO_OWNER_EMAIL`, `FOUNDRY_DIGEST_AGENT_ID`/`FOUNDRY_REPLY_INTERPRETER_AGENT_ID`
from step 3, etc.) via:

```bash
az functionapp config appsettings set --name <functionAppName> --resource-group rg-oir-dev \
  --settings COSMOS_ENDPOINT=https://td-bank-cosmos.documents.azure.com:443/ \
             COSMOS_DATABASE=OIRPlatform \
             FOUNDRY_PROJECT_ENDPOINT=https://td-bank.services.ai.azure.com/api/projects/TD-BANK \
             AZURE_TENANT_ID=... PMO_OWNER_EMAIL=...
```

Secrets (`AZURE_CLIENT_SECRET`, `COSMOS_KEY`, `PMO_TEAMS_WEBHOOK_URL`,
`TEAMS_BOT_APP_PASSWORD`) go into the Key Vault created in step 1 — the
Bicep already wires `@Microsoft.KeyVault(...)` references for them:

```bash
az keyvault secret set --vault-name kv-oir-dev-<suffix> --name cosmos-key --value "<value>"
```

## 5. Logic App (SharePoint file-drop trigger)

Import `logicapps/file-trigger.json` via the Azure Portal (Logic App
Designer → code view → paste) or `az logic workflow create`. Fill in the
`SharePointSiteUrl`, `SharePointLibraryId`, `IngestOIRFunctionUrl`, and
`IngestOIRFunctionKey` parameters, and create the SharePoint Online API
connection when prompted.

> **This is unrelated to the data store.** This trigger only watches a
> SharePoint document **library** for the incoming `TD Bank OIR *.xlsx`
> file — the actual OIR data now lives in Cosmos DB (step 2), not
> SharePoint. If the tenant hosting that document library differs from
> `wilmodel3.onmicrosoft.com` (which has no SharePoint at all — see the
> ADRs), this step targets that *other* tenant's SharePoint, which is fine;
> nothing here requires it to match where Cosmos DB lives.

## 6. Teams bot registration

1. Register an app in [dev.botframework.com](https://dev.botframework.com) (or via `az bot create`) using the Function App's messaging endpoint: `https://<functionAppName>.azurewebsites.net/api/messages`.
2. Enable the **Microsoft Teams** channel.
3. Set `TEAMS_BOT_APP_ID` / `TEAMS_BOT_APP_PASSWORD` (Key Vault) to match.
4. Package and sideload the Teams app manifest (not included in this repo yet — needs an `manifest.json` + icons if you want an installable app rather than a raw bot link).

## 7. Shadow mode (do this before any live notification)

Per the spec's Sprint 1 gate — **do not skip this**:

1. Set `SHADOW_MODE=true` and `PMO_OWNER_EMAIL=<you>` on the Function App.
2. Trigger `IngestOIR` manually against 5 consecutive real OIR files (see [Data/](../../Data)).
3. Confirm `DetectExceptions` output routes only to the PMO owner, and that the
   stale/expiring lists match manual inspection of those 5 files.
4. Only then flip `SHADOW_MODE=false`.

## Order-of-operations summary

```
1. az login + deploy_infra.ps1        → resource group, Key Vault, Function App, service principal
2. az cosmosdb keys list + provision_cosmos.py → OIRPlatform database + 4 containers
3. deploy_agents.py                   → 4 Foundry agents
4. func azure functionapp publish     → ship the code
5. Set remaining app settings + Key Vault secrets
6. Import logicapps/file-trigger.json → SharePoint file-drop trigger (separate tenant/site, unrelated to the data store)
7. Register Teams bot                 → dev.botframework.com
8. Shadow mode for 5 business days    → verify, then go live
```
