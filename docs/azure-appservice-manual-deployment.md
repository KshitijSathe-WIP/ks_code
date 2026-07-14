# Azure App Service Deployment Guide - Manual Steps

## Prerequisites
- Azure Portal access to TD-BANK subscription
- Project files ready for deployment

## Step 1: Create App Service

1. **Go to Azure Portal**: https://portal.azure.com
2. **Navigate to**: App Services → **Create**
3. **Configure Basic Settings**:
   - **Subscription**: TD-BANK
   - **Resource Group**: Create new → `rg-incident-rca` or use existing
   - **Name**: `incident-rca-api` (must be globally unique, try adding random numbers if taken)
   - **Publish**: Code
   - **Runtime stack**: Python 3.11
   - **Operating System**: Linux
   - **Region**: East US (same as Cosmos DB)
   - **Pricing Plan**: B1 (Basic) - ~$13/month

4. **Click**: Review + Create → **Create**
5. **Wait**: 2-3 minutes for deployment

## Step 2: Configure Application Settings

1. **Go to**: Your App Service → **Configuration** (left menu)
2. **Add Application Settings** (click **+ New application setting** for each):

   ```
   Name: AZURE_COSMOS_ENDPOINT
   Value: https://<YOUR_COSMOS_ACCOUNT>.documents.azure.com:443/
   
   Name: AZURE_COSMOS_KEY
   Value: <YOUR_COSMOS_PRIMARY_KEY>
   
   Name: AZURE_COSMOS_DATABASE
   Value: IncidentRCA
   
   Name: AZURE_COSMOS_INCIDENT_CONTAINER
   Value: historical-incidents
   
   Name: AZURE_COSMOS_CHANGE_CONTAINER
   Value: change-records
   
   Name: AZURE_AI_PROJECT_ENDPOINT
   Value: https://td-bank.services.ai.azure.com/api/projects/TD-BANK
   
   Name: AZURE_AI_MODEL_DEPLOYMENT_NAME
   Value: gpt-4.1-mini
   
   Name: AZURE_AI_API_KEY
   Value: 3fEc2gOfX3YzVSNszK9bCnd1HpZdjxu8mDhH2DiBRWl7bizTV5JwJQQJ99CDACYeBjFXJ3w3AAAAACOGnICA
   ```

3. **Click**: **Save** (top of page) → **Continue**

## Step 3: Configure Startup Command

1. **Still in Configuration** → **General settings** tab
2. **Startup Command**: Enter this exactly:
   ```
   gunicorn -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 src.api.main:app
   ```
3. **Click**: **Save** → **Continue**

## Step 4: Deploy Code (ZIP Deploy Method)

### 4a: Create Deployment ZIP

**In PowerShell** (from project root):
```powershell
cd "C:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\ServiceMgmt\incident-rca-foundry"

# Create deployment package (excluding unnecessary files)
$files = @(
    "src",
    "requirements-azure.txt"
)

Compress-Archive -Path $files -DestinationPath "deploy.zip" -Force

Write-Host "✅ Created deploy.zip"
```

### 4b: Deploy via Azure Portal

1. **Go to**: Your App Service → **Deployment Center** (left menu)
2. **Click**: **FTPS credentials** tab
3. **Copy** the **Application scope** username and password (you'll need these)
4. **OR use the easier method**: 
   - Click **Browse** → **Upload .zip file**
   - Select `deploy.zip` from your project folder
   - Click **Upload**
   - Wait 2-3 minutes

5. **Alternative - Kudu Console Method**:
   - Go to: `https://incident-rca-api.scm.azurewebsites.net/ZipDeployUI`
   - Drag and drop `deploy.zip` to the browser window
   - Wait for "Deployment successful"

## Step 5: Verify Deployment

1. **Go to**: Your App Service → **Overview**
2. **Find**: **Default domain** (looks like `incident-rca-api.azurewebsites.net`)
3. **Test Health Endpoint** in browser or PowerShell:
   ```powershell
   $url = "https://incident-rca-api.azurewebsites.net/health"
   Invoke-RestMethod -Uri $url
   ```
   
   **Expected**: `{"status": "healthy"}`

4. **Test Evidence Endpoint**:
   ```powershell
   $url = "https://incident-rca-api.azurewebsites.net/api/rca/evidence"
   $body = @{
       incident_description = "Mobile banking app not working"
       top_incident_count = 3
   } | ConvertTo-Json
   
   Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType "application/json"
   ```

## Step 6: Update OpenAPI Schema

1. **Get your App Service URL** from portal (e.g., `https://incident-rca-api.azurewebsites.net`)
2. **Open**: `src/foundry/openapi_schema.json`
3. **Replace** the servers section:
   ```json
   "servers": [
     {
       "url": "https://incident-rca-api.azurewebsites.net",
       "description": "Production Azure App Service"
     }
   ]
   ```
4. **Save the file**

## Step 7: Create Foundry Agent

1. **Go to**: https://ai.azure.com
2. **Navigate**: TD-BANK project → **Agents** → **New Agent**
3. **Configure**:
   - **Name**: Incident-RCA-Agent
   - **Model**: gpt-4.1-mini
4. **Copy** entire content from `src/foundry/agent_instructions.md` to **Instructions** field
5. **Add Tool**:
   - Click **+ Add tool** → **Upload OpenAPI spec**
   - Upload `src/foundry/openapi_schema.json` (the updated one with Azure URL)
   - **Name**: search_incident_rca_evidence
   - **Authentication**: None
6. **Test** in playground:
   - Input: "Mobile banking app not working"
   - Verify agent calls the tool and gets evidence

## Troubleshooting

### App Service won't start:
- Check **Deployment Center → Logs** for errors
- Common issue: Missing `requirements-azure.txt` → Redeploy with correct file name
- Check **Log stream** (App Service → Log stream in left menu)

### 500 errors:
- Check **Monitoring → Log stream**
- Verify Cosmos DB credentials in **Configuration → Application settings**
- Test Cosmos connection directly from portal console

### Can't reach API from Foundry:
- Verify App Service URL is correct and HTTPS
- Check App Service is **Running** (Overview page)
- Verify no firewall restrictions on App Service

## Cost Estimate

- **App Service B1**: ~$13/month
- **Cosmos DB** (existing): ~$25/month  
- **Foundry Agent** (existing): Included in AI Services
- **Total New Cost**: ~$13/month

## Next Steps

After successful deployment:
1. ✅ Verify `/health` endpoint returns 200
2. ✅ Verify `/api/rca/evidence` returns data
3. ✅ Update OpenAPI schema with production URL
4. ✅ Create Foundry agent with updated schema
5. ✅ Test with demo scenarios from `docs/demo-runbook.md`
