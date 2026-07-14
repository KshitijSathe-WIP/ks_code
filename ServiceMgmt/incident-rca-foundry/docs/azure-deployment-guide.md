# Azure Deployment Guide

## Overview

This guide explains how to deploy the Incident RCA system to Azure, including:
1. Cosmos DB database and containers
2. Microsoft Foundry RCA Agent
3. FastAPI Evidence Service (optional Azure deployment)

---

## Prerequisites

✓ Azure subscription with contributor access
✓ Azure Cosmos DB account created
✓ Microsoft Foundry project provisioned
✓ Python 3.11+ with dependencies installed

---

## Part 1: Deploy Cosmos DB (COMPLETED ✓)

### Database: IncidentRCA

**Status:** ✅ Created and populated

### Containers

| Container | Partition Key | Status |
|-----------|---------------|---------|
| historical-incidents | /serviceKey | ✅ Created with 30 records |
| change-records | /serviceKey | ✅ Created with 15 records |

### Connection Details

```
Endpoint: https://td-bank-cosmos.documents.azure.com:443/
Database: IncidentRCA
Authentication: Key-based (stored in .env)
```

### Verification

```powershell
cd ServiceMgmt\incident-rca-foundry
python scripts\validate_seed_data.py
```

**Expected Output:**
```
✓ Validation passed! Data integrity confirmed.
Total Incidents: 30
Total Changes: 15
Valid linkedChangeId relationships: 15
```

---

## Part 2: Deploy Foundry RCA Agent

### Agent Configuration

**Agent Name:** `Incident-RCA-Agent`
**Model:** gpt-4.1-mini (already deployed)
**Project:** TD-BANK
**Endpoint:** https://td-bank.services.ai.azure.com/api/projects/TD-BANK

### Deployment Steps

#### Step 1: Open Azure AI Foundry Portal

1. Navigate to: https://ai.azure.com
2. Sign in with Azure credentials
3. Select project: **TD-BANK**

#### Step 2: Create the Agent

1. Click **Agents** in the left navigation
2. Click **+ New Agent**
3. Configure:
   - **Name:** `Incident-RCA-Agent`
   - **Description:** `Root Cause Analysis agent for banking production incidents`
   - **Model Deployment:** `gpt-4.1-mini`

#### Step 3: Add System Instructions

1. In the agent configuration, find **Instructions**
2. Copy the entire content from:
   ```
   src/foundry/agent_instructions.md
   ```
3. Paste into the Instructions field
4. Click **Save**

#### Step 4: Register the Retrieval Tool

1. In agent configuration, go to **Tools** section
2. Click **+ Add Tool**
3. Select **Custom Function**
4. Configure tool:

**Tool Definition:**
```json
{
  "type": "function",
  "function": {
    "name": "search_incident_rca_evidence",
    "description": "Search historical incident and change evidence to support root cause analysis. Returns grounded matches from Cosmos DB with similarity scores, historical incidents, and related change records.",
    "parameters": {
      "type": "object",
      "properties": {
        "incidentDescription": {
          "type": "string",
          "description": "The user's complete incident description"
        },
        "topIncidentCount": {
          "type": "integer",
          "description": "Maximum number of historical incidents to return",
          "minimum": 1,
          "maximum": 5,
          "default": 3
        }
      },
      "required": ["incidentDescription"]
    }
  }
}
```

**Tool Endpoint:**
- **HTTP Method:** POST
- **URL:** `http://127.0.0.1:8000/api/rca/evidence` (local) or your Azure deployment URL
- **Authentication:** None (for demo) or Managed Identity (for production)

#### Step 5: Configure Tool Mapping

Map function parameters to HTTP request:
- `incidentDescription` → Request Body: `incidentDescription`
- `topIncidentCount` → Request Body: `topIncidentCount`

#### Step 6: Test the Agent

1. Go to agent **Playground**
2. Enter test query: `Mobile banking app not working`
3. Verify:
   - Agent calls the tool
   - Returns valid JSON
   - All incident IDs exist in Cosmos DB

---

## Part 3: Deploy Evidence API (Optional)

### Option A: Local Development (Current Setup)

**Status:** ✅ Working locally

```powershell
cd ServiceMgmt\incident-rca-foundry
uvicorn src.api.main:app --reload
```

**Endpoint:** http://127.0.0.1:8000

### Option B: Azure App Service (Production)

**Coming in Phase 7: Production Readiness**

#### Prerequisites:
- Azure App Service plan
- Managed Identity enabled
- RBAC role assignments for Cosmos DB

#### Deployment:
```powershell
# Using Azure CLI
az webapp up --name incident-rca-api --resource-group YOUR_RG --runtime PYTHON:3.11
```

#### Configuration:
- Enable Managed Identity
- Assign **Cosmos DB Data Contributor** role
- Set environment variables in App Service Configuration

---

## Part 4: Environment Variables

### Local .env (Current)

```env
# Azure Cosmos DB
AZURE_COSMOS_ENDPOINT=https://td-bank-cosmos.documents.azure.com:443/
AZURE_COSMOS_KEY=<REDACTED>
AZURE_COSMOS_DATABASE=IncidentRCA
AZURE_COSMOS_INCIDENT_CONTAINER=historical-incidents
AZURE_COSMOS_CHANGE_CONTAINER=change-records

# Microsoft Foundry
AZURE_AI_PROJECT_ENDPOINT=https://td-bank.services.ai.azure.com/api/projects/TD-BANK
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4.1-mini
AZURE_AI_API_KEY=<REDACTED>

# Application
LOG_LEVEL=INFO
```

### Production (Managed Identity)

Remove keys and use:
```env
AZURE_COSMOS_ENDPOINT=https://td-bank-cosmos.documents.azure.com:443/
# No key - uses Managed Identity
```

---

## Part 5: Verification Checklist

### Cosmos DB ✓
- [x] Database created: IncidentRCA
- [x] Container created: historical-incidents
- [x] Container created: change-records
- [x] 30 incidents loaded
- [x] 15 change records loaded
- [x] All linkedChangeId validated

### Evidence API ⚠️ (Local Only)
- [x] API running locally
- [x] Health check: 200 OK
- [x] Evidence endpoint: POST /api/rca/evidence
- [ ] Deployed to Azure (Phase 7)

### Foundry Agent ⏳ (Next Step)
- [ ] Agent created in portal
- [ ] System instructions configured
- [ ] Tool registered
- [ ] Tool endpoint configured
- [ ] Test query successful

---

## Security Considerations

### Current (Demo)
- ✅ Cosmos DB key authentication
- ✅ Foundry API key authentication
- ⚠️ Local API (no authentication)

### Production (Phase 7)
- Use Managed Identity for Cosmos DB
- Remove keys from .env
- Enable Azure AD authentication for API
- Use Private Endpoints
- Enable Azure Policy compliance

---

## Monitoring and Observability

### Current Logging
```python
from src.common.logging import get_logger
logger = get_logger(__name__)
```

### Future (Phase 7)
- Azure Application Insights
- Cosmos DB metrics
- Foundry agent traces
- Custom dashboards

---

## Cost Estimation

### Cosmos DB
- **Model:** Serverless
- **Expected RU/s:** <100
- **Storage:** <1 GB
- **Monthly Cost:** ~$5-10

### Foundry Agent
- **Model:** gpt-4.1-mini
- **Token Usage:** ~1K tokens/query
- **Queries:** 100/day
- **Monthly Cost:** ~$15-30

### Evidence API (If deployed)
- **App Service:** B1 Basic tier
- **Monthly Cost:** ~$13

**Total Estimated Cost:** ~$30-55/month

---

## Troubleshooting

### Cosmos DB Connection Failed

**Error:** `DefaultAzureCredential failed to retrieve a token`

**Solution:** Ensure `.env` has `AZURE_COSMOS_KEY` set

### Agent Not Calling Tool

**Possible Causes:**
1. Tool not registered
2. Tool endpoint unreachable
3. Tool schema mismatch

**Fix:** Verify tool configuration in Foundry portal

### Empty Query Results

**Check:**
1. Cosmos DB connection
2. Container names match settings
3. Partition key queries working
4. Data loaded successfully

---

## Next Steps

1. **Complete Foundry Agent Setup** (Part 2)
2. **Test All Demo Scenarios** (docs/demo-runbook.md)
3. **Create Phase 4 Documentation** (in progress)
4. **Plan Production Deployment** (Phase 7)

---

## Support Resources

- [Azure AI Foundry Documentation](https://learn.microsoft.com/azure/ai-foundry/)
- [Cosmos DB NoSQL Documentation](https://learn.microsoft.com/azure/cosmos-db/nosql/)
- [FastAPI Deployment Guide](https://fastapi.tiangolo.com/deployment/)
- [GitHub Copilot Build Plan](../docs/GITHUB_COPILOT_BUILD_PLAN.md)
