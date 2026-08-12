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
Foundry and Cosmos endpoint URLs above. The service principal
(`AZURE_CLIENT_ID`/`SECRET`) and downstream secrets from steps 1–6 below
still need to be added. `COSMOS_KEY` is only needed for local dev and for
`provision_cosmos.py` — the deployed app uses managed identity instead
(ADR 0006).

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

> This service principal is used for Graph (owner-email resolution) only.
> The **deployed** app reaches Cosmos DB and Foundry through the Function
> App's own managed identity — see ADR 0003 and ADR 0006. Cosmos needs no
> `Microsoft.Authorization` role assignment at all, which is why it
> sidesteps every wall Dataverse and SharePoint hit in this tenant.

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

This publishes a new **version** of `digest-agent`, `reply-interpreter`,
`trend-agent`, and `orchestrator` on the v1 `/agents` surface (the one the
Foundry portal displays) and writes `agents/.deployed_agents.json` with the
latest version id per agent.

> **Verify in the portal, not just via the API.** An earlier iteration used
> SDK 1.x, which writes to a legacy `/assistants` endpoint the portal does
> not show — and listing agents through that same SDK "confirmed" they
> existed, which proved nothing. After deploying, open the Foundry portal's
> **Agents** list and confirm all four appear. See
> [docs/decisions/0004-foundry-v1-agents-api.md](decisions/0004-foundry-v1-agents-api.md).

**Invocation model:** `detect_exceptions` and `bot/activity_handler.py` call
these agents directly via `functions/shared/foundry_client.py`, using the
OpenAI **Responses API** against each agent's scoped endpoint
(`get_openai_client(agent_name=...)`, which needs `allow_preview=True`).
No separate wrapper service — fewer moving parts, one fewer thing to
deploy/monitor/secure. v1 agents are addressed by **name**:

```
FOUNDRY_DIGEST_AGENT_NAME=digest-agent
FOUNDRY_REPLY_INTERPRETER_AGENT_NAME=reply-interpreter
```

> **No PII reaches the model.** This account's content filter blocks
> prompts containing person names or email addresses, which would have
> failed every digest call. `foundry_client.scrub_recipient()` strips the
> email and swaps the name for a `{{RECIPIENT_NAME}}` placeholder before
> the call; `restore_pii()` substitutes it back into the generated text.
> Free text we don't control (Excel comments, Teams replies) can still trip
> the filter — that raises `ContentFilteredError` and the bot asks the user
> to use the Adaptive Card instead. See
> [docs/decisions/0005-no-pii-sent-to-foundry-agents.md](decisions/0005-no-pii-sent-to-foundry-agents.md).

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

The v2 Python model needs a single entry point at the **deployment root**:
`function_app.py` (which registers the three `func.Blueprint`s from
`functions/`), plus `host.json`, `requirements.txt` and `config.json`.
Package exactly those, then zip-deploy with a remote build:

```bash
# Build the package (root-level files + the functions/ package, no __pycache__)
mkdir pkg && cp function_app.py host.json requirements.txt config.json pkg/ && cp -r functions pkg/
find pkg -name __pycache__ -type d -exec rm -rf {} +
cd pkg && zip -r ../oir-deploy.zip . && cd ..

az functionapp deployment source config-zip \
  --name <functionAppName> --resource-group <rg> \
  --src oir-deploy.zip --build-remote true --timeout 1800
```

> `--build-remote true` is **required**. Without it (and despite
> `SCM_DO_BUILD_DURING_DEPLOYMENT=true`) the zip is only extracted, no
> `pip install` runs, and the app silently indexes zero functions.
>
> `requirements.txt` is what Oryx installs, so it must contain only real,
> pip-installable runtime packages. Sanity-check it before deploying —
> `pip install --dry-run -r requirements.txt` — since a bad entry fails the
> build. Test/bot-only packages live in `requirements-dev.txt`.
>
> `func azure functionapp publish` also works if you have the Core Tools
> CLI installed; the zip route above avoids that dependency.

**Verifying the deploy — don't trust `az functionapp function list`.** On
this app it returns an empty list even when everything is working (the ARM
listing doesn't reliably reflect blueprint-registered v2 functions). Probe
the endpoints instead:

```bash
# 401 (not 404) already proves the route is registered and key-protected
curl -s -o /dev/null -w "%{http_code}" -X POST https://<app>.azurewebsites.net/api/ingest-oir

KEY=$(az functionapp keys list --name <app> --resource-group <rg> --query functionKeys.default -o tsv)
# Expect our own 400 validation message -- proves imports + code executed
curl -X POST "https://<app>.azurewebsites.net/api/ingest-oir?code=$KEY" \
  -H 'Content-Type: application/json' -d '{"fileName":"bad.xlsx","fileDate":"2026-08-12","fileUrl":"https://x"}'
# Expect 404 "Demand '...' not found" -- proves Cosmos was reached via managed identity
curl -X POST "https://<app>.azurewebsites.net/api/apply-update?code=$KEY" \
  -H 'Content-Type: application/json' \
  -d '{"demand_id":"PROBE-404","actor_email":"<you>","action":"NO_CHANGE"}'
```

Then set the remaining app settings that `main.bicep` left blank
(`COSMOS_ENDPOINT`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `PMO_GROUP_ID`,
`PMO_OWNER_EMAIL`, `FOUNDRY_DIGEST_AGENT_NAME`/`FOUNDRY_REPLY_INTERPRETER_AGENT_NAME`
from step 3, etc.) via:

```bash
az functionapp config appsettings set --name <functionAppName> --resource-group rg-oir-dev \
  --settings COSMOS_ENDPOINT=https://td-bank-cosmos.documents.azure.com:443/ \
             COSMOS_DATABASE=OIRPlatform \
             FOUNDRY_PROJECT_ENDPOINT=https://td-bank.services.ai.azure.com/api/projects/TD-BANK \
             AZURE_TENANT_ID=... PMO_OWNER_EMAIL=...
```

Secrets (`AZURE_CLIENT_SECRET`, `PMO_TEAMS_WEBHOOK_URL`,
`TEAMS_BOT_APP_PASSWORD`) go into the Key Vault created in step 1 — the
Bicep already wires `@Microsoft.KeyVault(...)` references for them:

```bash
az keyvault secret set --vault-name kv-oir-dev-<suffix> --name azure-client-secret --value "<value>"
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
