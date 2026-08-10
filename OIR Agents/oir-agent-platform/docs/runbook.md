# OIR Platform — Deployment Runbook

This is the manual sequence to stand up the dependent systems (SharePoint
Lists, Azure infra, Foundry agents, Teams bot) from a clean tenant. Follow
it in order — later steps assume earlier ones are done. Every script here
is idempotent: safe to re-run after a partial failure.

Read [OIR_Autonomous_Agent_Implementation_Spec.md](../../OIR_Autonomous_Agent_Implementation_Spec.md)
first if you haven't — it's the source of truth for *why* each piece exists.
**Note:** that spec (v1.0) specifies Dataverse as the data store; the actual
implementation uses SharePoint Lists instead — see
[docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md](decisions/0001-sharepoint-lists-instead-of-dataverse.md)
for why, and treat this runbook (not the spec's §2/§4) as authoritative on
the data layer.

## Confirmed target environment (TD-BANK-POC, as of 2026-08-06)

| Value | Confirmed value |
|---|---|
| `SHAREPOINT_SITE_URL` | *(pending — set once the target site is chosen; see step 2)* |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://td-bank.services.ai.azure.com/api/projects/TD-BANK` |
| Tenant ID (from the Foundry portal link) | `6efbfbdd-57af-4e28-9f2c-9b75f72a6ffe` — **verify** with `az account show --query tenantId -o tsv` before creating the service principal in step 1; if it doesn't match, the SP won't be able to reach this Foundry tenant |

Copy [.env.example](../.env.example) to `.env` — it's pre-filled with the
Foundry URL above. The SharePoint site URL, service-principal
(`AZURE_CLIENT_ID`/`SECRET`), and downstream secrets from steps 1–6 below
still need to be added.

> The abandoned Dataverse environment (`orge0db2320.crm8.dynamics.com`,
> `0687df9e-4e7c-e34e-97b0-eca63c7f61dd`) can be deleted once this migration
> is confirmed working, or left alone at no cost if unused.

## 0. Prerequisites

| Requirement | Notes |
|---|---|
| Azure subscription with Owner/Contributor access | For resource group + service principal creation |
| A SharePoint Online site | Existing site you have owner/full-control access to (the same one the source OIR Excel file lands on works well — no new site required), or create a new one in [admin.microsoft.com](https://admin.microsoft.com) → SharePoint |
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

## 2. SharePoint Lists

The service principal from step 1 needs a Microsoft Graph **application**
permission granting write access to the target SharePoint site before it
can create lists or data. Two options:

- **Sites.Selected** (recommended — narrowest scope): grant admin consent
  for the `Sites.Selected` application permission on the app registration,
  then a tenant admin runs one Graph call to grant this specific app
  `write` access to just the target site (see
  [Microsoft's Sites.Selected guide](https://learn.microsoft.com/sharepoint/dev/embedded/development/sites-selected-authorization)) —
  no delegated user consent needed.
- **Sites.ReadWrite.All** (simpler, broader): grant admin consent for this
  application permission on the app registration; the app can then write to
  any SharePoint site in the tenant.

In [entra.microsoft.com](https://entra.microsoft.com) → **App registrations**
→ `sp-oir-dev` → **API permissions** → **Add a permission** → **Microsoft
Graph** → **Application permissions** → pick one of the above → **Grant
admin consent**.

Then provision the four lists (`OIR Demands`, `OIR Snapshot History`,
`OIR Interaction Log`, `OIR Person Map`) via Microsoft Graph — no separate
CLI needed:

```powershell
$env:SHAREPOINT_SITE_URL = "https://<tenant>.sharepoint.com/sites/<site-name>"
python infra/provision_sharepoint_lists.py --dry-run   # review planned calls first
python infra/provision_sharepoint_lists.py             # apply
```

The script is idempotent — it checks each list/column by name before
creating anything, so re-running after a partial failure just fills in
what's missing. It never drops or alters existing columns.

**Verify:** open the site, click the gear icon → **Site contents**, and
confirm all four lists and their columns exist.

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
`create_and_process` → read reply) using the same service-principal
credentials as SharePoint/Graph. No separate wrapper service — fewer moving
parts, one fewer thing to deploy/monitor/secure. Set these app settings from
`agents/.deployed_agents.json`:

```
FOUNDRY_DIGEST_AGENT_ID=<digest-agent id>
FOUNDRY_REPLY_INTERPRETER_AGENT_ID=<reply-interpreter id>
```

The service principal used by the Functions (from step 1) needs its own
grant to call the Foundry project — separate from whatever identity ran
`deploy_agents.py` above:

```bash
az role assignment create --assignee <AZURE_CLIENT_ID> --role "Azure AI Developer" \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account-name>
```

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
(`SHAREPOINT_SITE_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `PMO_GROUP_ID`,
`PMO_OWNER_EMAIL`, `FOUNDRY_DIGEST_AGENT_ID`/`FOUNDRY_REPLY_INTERPRETER_AGENT_ID`
from step 3, etc.) via:

```bash
az functionapp config appsettings set --name <functionAppName> --resource-group rg-oir-dev \
  --settings SHAREPOINT_SITE_URL=https://<tenant>.sharepoint.com/sites/<site-name> \
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

> **Not the same site/library as step 2.** This trigger watches a document
> **library** for the incoming `TD Bank OIR *.xlsx` file — a different
> SharePoint artifact from the `OIR Demands`/etc. **lists** created in step 2.
> They can live on the same site or different sites; nothing here requires
> them to match.

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
2. Grant SP Graph Sites.Selected/ReadWrite.All + admin consent (manual, Entra admin center)
3. provision_sharepoint_lists.py      → 4 SharePoint lists
4. deploy_agents.py                   → 4 Foundry agents
5. func azure functionapp publish     → ship the code
6. Set remaining app settings + Key Vault secrets
7. Import logicapps/file-trigger.json → SharePoint file-drop trigger
8. Register Teams bot                 → dev.botframework.com
9. Shadow mode for 5 business days    → verify, then go live
```
