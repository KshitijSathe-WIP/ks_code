# Azure App Service Deployment Guide

## Quick Deploy via Azure Portal (15 minutes)

### Step 1: Create App Service

1. Go to [Azure Portal](https://portal.azure.com)
2. Search "App Services" → Click **+ Create**
3. Configure:
   - **Subscription:** TD-BANK subscription
   - **Resource Group:** `td-bank-rg` (or create new)
   - **Name:** `td-bank-rca-api` (must be globally unique)
   - **Publish:** Code
   - **Runtime stack:** Python 3.11
   - **Region:** Same as your Cosmos DB (e.g., East US)
   - **Pricing Plan:** B1 Basic ($13/month) or F1 Free (for testing)
4. Click **Review + Create** → **Create**

### Step 2: Configure Environment Variables

1. Go to your App Service → **Configuration** → **Application settings**
2. Click **+ New application setting** for each:

```
AZURE_COSMOS_ENDPOINT=https://<YOUR_COSMOS_ACCOUNT>.documents.azure.com:443/
AZURE_COSMOS_KEY=<YOUR_COSMOS_PRIMARY_KEY>
AZURE_COSMOS_DATABASE=IncidentRCA
AZURE_COSMOS_INCIDENT_CONTAINER=historical-incidents
AZURE_COSMOS_CHANGE_CONTAINER=change-records
```

3. Click **Save** → **Continue**

### Step 3: Deploy Code

#### Option A: Deploy from Local Git (Recommended)

1. In App Service → **Deployment Center**
2. Select **Local Git** → **Save**
3. Go to **Deployment Center** → Copy **Git Clone Uri**
4. In PowerShell:

```powershell
cd "C:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\ServiceMgmt\incident-rca-foundry"

# Initialize git if not already
git init
git add .
git commit -m "Initial deployment"

# Add Azure remote (replace with your Git Clone Uri)
git remote add azure https://td-bank-rca-api.scm.azurewebsites.net:443/td-bank-rca-api.git

# Deploy (you'll be prompted for deployment credentials)
git push azure main
```

#### Option B: Deploy via ZIP

1. Create deployment package:

```powershell
cd "C:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\ServiceMgmt\incident-rca-foundry"
Compress-Archive -Path * -DestinationPath deploy.zip -Force
```

2. Deploy via Azure CLI:

```powershell
az webapp deployment source config-zip `
  --resource-group td-bank-rg `
  --name td-bank-rca-api `
  --src deploy.zip
```

#### Option C: Deploy via VS Code (Easiest)

1. Install [Azure App Service extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-azureappservice)
2. Open folder in VS Code
3. Right-click folder → **Deploy to Web App...**
4. Select your App Service

### Step 4: Configure Startup Command

1. Go to App Service → **Configuration** → **General settings**
2. **Startup Command:** `bash /home/site/wwwroot/startup.sh`
3. Click **Save**

### Step 5: Verify Deployment

1. Wait 2-3 minutes for deployment to complete
2. Visit: `https://td-bank-rca-api.azurewebsites.net/health`
3. Should return: `{"status": "healthy"}`

### Step 6: Update OpenAPI Schema

1. Open [openapi_schema.json](./src/foundry/openapi_schema.json)
2. Update the server URL:

```json
"servers": [
  {
    "url": "https://td-bank-rca-api.azurewebsites.net",
    "description": "Azure App Service"
  }
]
```

3. Re-upload to Foundry portal
4. Test the agent!

---

## Troubleshooting

### Check Logs
```powershell
az webapp log tail --resource-group td-bank-rg --name td-bank-rca-api
```

### Common Issues

**"Application Error"**
- Check logs for Python errors
- Verify environment variables are set
- Ensure `startup.sh` is executable

**"502 Bad Gateway"**
- Check that port matches `$PORT` environment variable
- Verify gunicorn is starting correctly

**"Cosmos DB connection failed"**
- Verify `AZURE_COSMOS_ENDPOINT` and `AZURE_COSMOS_KEY` are correct
- Check firewall settings on Cosmos DB (allow Azure services)

---

## Cost Estimate

- **B1 Basic:** $13/month (1 core, 1.75 GB RAM)
- **F1 Free:** $0/month (60 minutes/day compute limit - good for testing)
- **Cosmos DB:** ~$30/month (existing, already paid)

**Total:** $13-43/month depending on tier selected
