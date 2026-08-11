<#
.SYNOPSIS
    Deploys the OIR platform's Azure infrastructure (Key Vault, App Insights,
    Storage, Function App) from infra/main.bicep, and creates the service
    principal used by the Functions to call Graph / Foundry (Cosmos DB uses
    its own key-based auth, independent of this service principal).

.DESCRIPTION
    Wraps `az` CLI calls only - does not touch Cosmos DB or Foundry (see
    provision_cosmos.py and deploy_agents.py for those). Safe to re-run:
    resource group and deployment are idempotent; the service principal step
    is skipped if -SkipServicePrincipal is passed or one already exists with
    the given display name.

.PARAMETER SubscriptionId
    Target Azure subscription ID.

.PARAMETER ResourceGroup
    Resource group to create/use, e.g. rg-oir-dev.

.PARAMETER Location
    Azure region, e.g. eastus2.

.PARAMETER Environment
    dev | uat | prod - passed through to main.bicep.

.EXAMPLE
    ./deploy_infra.ps1 -SubscriptionId <sub-id> -ResourceGroup rg-oir-dev -Location eastus2 -Environment dev
#>
param(
    [Parameter(Mandatory = $true)][string]$SubscriptionId,
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [Parameter(Mandatory = $true)][string]$Location,
    [ValidateSet('dev', 'uat', 'prod')][string]$Environment = 'dev',
    [string]$ServicePrincipalName = "sp-oir-$Environment",
    [switch]$SkipServicePrincipal,
    [switch]$SkipKeyVaultRbac,
    [string]$PlanSkuName = 'B1',
    [string]$PlanSkuTier = 'Basic',
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

function Assert-AzCli {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI ('az') not found on PATH. Install it, then run 'az login' before this script."
    }
}

Assert-AzCli

Write-Host "==> Setting subscription $SubscriptionId" -ForegroundColor Cyan
az account set --subscription $SubscriptionId

Write-Host "==> Ensuring resource group '$ResourceGroup' in $Location" -ForegroundColor Cyan
az group create --name $ResourceGroup --location $Location --output none

$bicepFile = Join-Path $PSScriptRoot 'main.bicep'
$grantKvRbac = if ($SkipKeyVaultRbac) { 'false' } else { 'true' }

if ($SkipKeyVaultRbac) {
    Write-Host "==> Skipping Key Vault RBAC grant (requires Owner/User Access Administrator)." -ForegroundColor Yellow
    Write-Host "    Someone with that role must run this afterward so the Function App can read secrets:" -ForegroundColor Yellow
    Write-Host "    az role assignment create --assignee <functionApp principalId> --role 'Key Vault Secrets User' --scope <keyVault resource id>" -ForegroundColor Yellow
}

if ($WhatIf) {
    Write-Host "==> What-if: main.bicep against $ResourceGroup" -ForegroundColor Cyan
    az deployment group what-if `
        --resource-group $ResourceGroup `
        --template-file $bicepFile `
        --parameters environment=$Environment location=$Location grantFunctionAppKeyVaultAccess=$grantKvRbac planSkuName=$PlanSkuName planSkuTier=$PlanSkuTier
    exit 0
}

Write-Host "==> Deploying main.bicep to '$ResourceGroup' (environment=$Environment)" -ForegroundColor Cyan
$deployment = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file $bicepFile `
    --parameters environment=$Environment location=$Location grantFunctionAppKeyVaultAccess=$grantKvRbac planSkuName=$PlanSkuName planSkuTier=$PlanSkuTier `
    --query 'properties.outputs' `
    --output json | ConvertFrom-Json

Write-Host "Function App: $($deployment.functionAppName.value)" -ForegroundColor Green
Write-Host "Function App URL: $($deployment.functionAppUrl.value)" -ForegroundColor Green

if (-not $SkipServicePrincipal) {
    Write-Host "==> Checking for existing service principal '$ServicePrincipalName'" -ForegroundColor Cyan
    $existing = az ad sp list --display-name $ServicePrincipalName --query '[0]' --output json | ConvertFrom-Json

    if ($existing) {
        Write-Host "Service principal '$ServicePrincipalName' already exists (appId=$($existing.appId))." -ForegroundColor Yellow
        Write-Host "Skipping creation. Rotate its secret manually via 'az ad app credential reset' if needed."
    }
    else {
        Write-Host "==> Creating service principal '$ServicePrincipalName' (Contributor scoped to $ResourceGroup)" -ForegroundColor Cyan
        # NOTE: this itself performs a role assignment (Microsoft.Authorization/roleAssignments/write),
        # so it needs Owner/User Access Administrator on $ResourceGroup -- same requirement as the
        # Key Vault RBAC grant above. If that's missing, fall back to an app registration with no
        # role assignment: Graph access for this app is granted separately anyway (Graph API
        # application permissions with admin consent), and Cosmos DB uses key-based auth
        # independent of this service principal entirely, so Contributor on the RG is a
        # convenience, not a hard requirement for the Functions to work once deployed.
        try {
            $sp = az ad sp create-for-rbac `
                --name $ServicePrincipalName `
                --role Contributor `
                --scopes "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup" `
                --output json 2>$null | ConvertFrom-Json
            if (-not $sp) { throw "az ad sp create-for-rbac returned no output" }
        }
        catch {
            Write-Host "Role-scoped creation failed (likely missing Owner/User Access Administrator)." -ForegroundColor Yellow
            Write-Host "Falling back to an app registration with no RBAC role assignment:" -ForegroundColor Yellow
            $sp = az ad sp create-for-rbac --name $ServicePrincipalName --skip-assignment --output json | ConvertFrom-Json
            Write-Host "Ask someone with Owner/User Access Administrator to grant it Contributor on $ResourceGroup if needed:" -ForegroundColor Yellow
            Write-Host "  az role assignment create --assignee $($sp.appId) --role Contributor --scope /subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup" -ForegroundColor Yellow
        }

        Write-Host ""
        Write-Host "=====================================================================" -ForegroundColor Yellow
        Write-Host " SAVE THESE NOW - the secret is shown only once."          -ForegroundColor Yellow
        Write-Host "   AZURE_TENANT_ID     = $($sp.tenant)"
        Write-Host "   AZURE_CLIENT_ID     = $($sp.appId)"
        Write-Host "   AZURE_CLIENT_SECRET = $($sp.password)"
        Write-Host "=====================================================================" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Next: run 'az cosmosdb keys list' for the target Cosmos DB account and" -ForegroundColor Cyan
        Write-Host "then run provision_cosmos.py to create the OIRPlatform database + containers." -ForegroundColor Cyan
    }
}

Write-Host "==> Done. See docs/runbook.md for the remaining Cosmos DB/Foundry/Teams steps." -ForegroundColor Green
