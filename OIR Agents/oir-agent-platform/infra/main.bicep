targetScope = 'resourceGroup'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Environment tag: dev, uat, prod')
@allowed(['dev', 'uat', 'prod'])
param environment string = 'dev'

@description('Unique suffix to avoid naming collisions')
param suffix string = uniqueString(resourceGroup().id)

@description('Grant the Function App identity Key Vault Secrets User via RBAC. Requires Owner/User Access Administrator on the target scope -- set to false and have someone with that role assign it afterward if the deploying principal only has Contributor.')
param grantFunctionAppKeyVaultAccess bool = true

@description('App Service Plan SKU name. Default is Basic (B1) because Linux Consumption ("Y1"/Dynamic) is not enabled in every subscription/resource-group -- switch to Y1 (with planSkuTier=Dynamic) once that feature is available, for lower idle cost.')
param planSkuName string = 'B1'

@description('App Service Plan SKU tier, matching planSkuName (e.g. Basic for B1, Dynamic for Y1, ElasticPremium for EP1).')
param planSkuTier string = 'Basic'

// ---------------------------------------------------------------------------
// Key Vault
// ---------------------------------------------------------------------------
var keyVaultName = 'kv-oir-${environment}-${suffix}'

module kv './keyvault.bicep' = {
  name: 'kv-deploy'
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

// Resolved via the deterministic name above (not the module output) so its
// .id can be used in a role-assignment "name" expression, which Bicep
// requires to be calculable before deployment starts (BCP120).
resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// ---------------------------------------------------------------------------
// Application Insights + Log Analytics Workspace
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-oir-${environment}-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'ai-oir-${environment}-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Storage Account (Azure Functions host)
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'stooir${environment}${suffix}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

// ---------------------------------------------------------------------------
// App Service Plan
// ---------------------------------------------------------------------------
resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'plan-oir-${environment}-${suffix}'
  location: location
  sku: { name: planSkuName, tier: planSkuTier }
  kind: 'functionapp'
  properties: { reserved: true }   // Linux
}

// ---------------------------------------------------------------------------
// Function App
// ---------------------------------------------------------------------------
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: 'func-oir-${environment}-${suffix}'
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    siteConfig: {
      pythonVersion: '3.11'
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value}' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        // No AZURE_CLIENT_SECRET: all three outbound integrations (Cosmos,
        // Foundry, Graph) authenticate as this app's managed identity when
        // deployed, so no client secret exists to store. AZURE_TENANT_ID /
        // AZURE_CLIENT_ID remain only for the local-dev fallback path.
        // See docs/decisions/0007-single-permission-request-no-secrets.md.
        { name: 'AZURE_TENANT_ID', value: '' }
        { name: 'AZURE_CLIENT_ID', value: '' }
        // No COSMOS_KEY: the Function App's managed identity holds the Cosmos
        // "Built-in Data Contributor" data-plane role, so cosmos_client.py
        // authenticates via Entra ID and no account key is stored anywhere.
        // See docs/decisions/0006-cosmos-managed-identity-auth.md.
        { name: 'COSMOS_ENDPOINT', value: '' }
        { name: 'COSMOS_DATABASE', value: 'OIRPlatform' }
        // Plain setting, not a Key Vault reference: it's a channel webhook
        // URL, alerting is optional (code no-ops when unset), and routing it
        // through Key Vault would reintroduce a permission request for no
        // real security gain at POC scope.
        { name: 'PMO_TEAMS_WEBHOOK_URL', value: '' }
        { name: 'SHADOW_MODE', value: 'true' }
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: '' }
        { name: 'FOUNDRY_DIGEST_AGENT_NAME', value: '' }
        { name: 'FOUNDRY_REPLY_INTERPRETER_AGENT_NAME', value: '' }
        // Owner emails come from the OIR file (ADR 0008); Graph lookup is an
        // optional backstop that needs admin-consented app permissions.
        { name: 'GRAPH_LOOKUP_ENABLED', value: 'false' }
        // Single escalation contact for demands stale past
        // staleness.escalation_adh_days. Not derivable from the OIR file.
        { name: 'ACCOUNT_DELIVERY_HEAD_EMAIL', value: '' }
        { name: 'PMO_MEMBER_EMAILS', value: '' }
        { name: 'PMO_GROUP_ID', value: '' }
        { name: 'PMO_OWNER_EMAIL', value: '' }
        // No TEAMS_BOT_APP_PASSWORD: when the bot is registered it should use
        // a managed-identity bot type (MicrosoftAppType=SystemAssignedMSI),
        // which has no password at all. Revisit only if a password-based bot
        // registration turns out to be unavoidable.
        { name: 'TEAMS_BOT_APP_ID', value: '' }
      ]
    }
    httpsOnly: true
  }
}

// Grant Function App identity read access to Key Vault (scoped to the vault
// itself, not the whole resource group, per least privilege). Conditional:
// creating a role assignment needs Owner/User Access Administrator, which
// the deploying principal may not have even with Contributor on the RG.
resource kvAccessPolicy 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantFunctionAppKeyVaultAccess) {
  name: guid(keyVaultRef.id, functionApp.id, 'KeyVaultSecretsUser')
  scope: keyVaultRef
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')  // Key Vault Secrets User
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output functionAppName string = functionApp.name
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
