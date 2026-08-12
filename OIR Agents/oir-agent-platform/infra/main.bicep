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
        { name: 'AZURE_TENANT_ID', value: '' }       // set in Key Vault reference
        { name: 'AZURE_CLIENT_ID', value: '' }
        { name: 'AZURE_CLIENT_SECRET', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.keyVaultUri}secrets/azure-client-secret/)' }
        { name: 'COSMOS_ENDPOINT', value: '' }
        { name: 'COSMOS_KEY', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.keyVaultUri}secrets/cosmos-key/)' }
        { name: 'COSMOS_DATABASE', value: 'OIRPlatform' }
        { name: 'PMO_TEAMS_WEBHOOK_URL', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.keyVaultUri}secrets/pmo-teams-webhook/)' }
        { name: 'SHADOW_MODE', value: 'true' }
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: '' }
        { name: 'FOUNDRY_DIGEST_AGENT_NAME', value: '' }
        { name: 'FOUNDRY_REPLY_INTERPRETER_AGENT_NAME', value: '' }
        { name: 'PMO_GROUP_ID', value: '' }
        { name: 'PMO_OWNER_EMAIL', value: '' }
        { name: 'TEAMS_BOT_APP_ID', value: '' }
        { name: 'TEAMS_BOT_APP_PASSWORD', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.keyVaultUri}secrets/teams-bot-password/)' }
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
