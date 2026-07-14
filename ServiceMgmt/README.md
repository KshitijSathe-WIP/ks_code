# TD RCA API — Incident Root Cause Analysis Tool API

This branch contains the **Incident RCA Evidence API** — a FastAPI service that serves as a tool for Azure AI Foundry agents. It searches historical incident and change evidence from Cosmos DB to support root cause analysis.

## Architecture

```
ServiceMgmt/
├── .deployment              ← Azure deployment routing (points to incident-rca-foundry/)
├── .gitignore
├── incident-rca-foundry/    ← Main application
│   ├── src/
│   │   ├── api/             ← FastAPI endpoints (/health, /api/rca/evidence)
│   │   ├── cosmos/          ← Azure Cosmos DB client and repositories
│   │   ├── retrieval/       ← Evidence retrieval, scoring, correlation
│   │   ├── foundry/         ← OpenAPI schema, agent instructions, tool schema
│   │   └── common/          ← Logging, exceptions
│   ├── data/                ← Seed data (historical incidents, change records)
│   ├── scripts/             ← Database setup and data loading scripts
│   ├── tests/               ← Unit and integration tests
│   ├── requirements.txt     ← Production dependencies (pinned)
│   ├── startup.sh           ← Azure App Service startup script
│   └── Procfile             ← Process definition for gunicorn
│   └── docs/                ← Deployment guides and architecture docs
│
└── docs/                    ← Build plan documentation
```

## Deployment to Azure App Service

### Prerequisites
- Azure App Service (Python 3.12, Linux)
- Azure Cosmos DB with `IncidentRCA` database
- Environment variables configured (see `.env.example`)

### Quick Deploy

1. **Set Startup Command** in Azure Portal → App Service → Configuration → General settings:
   ```
   gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120 src.api.main:app
   ```

2. **Configure Environment Variables** in App Service → Configuration → Application settings:
   - `AZURE_COSMOS_ENDPOINT`
   - `AZURE_COSMOS_KEY`
   - `AZURE_COSMOS_DATABASE` (default: IncidentRCA)
   - `AZURE_COSMOS_INCIDENT_CONTAINER` (default: historical-incidents)
   - `AZURE_COSMOS_CHANGE_CONTAINER` (default: change-records)

3. **Deploy via GitHub** — Connect App Service Deployment Center to this branch (`td-rca-api`), set root to `ServiceMgmt/incident-rca-foundry`.

   **Or deploy via ZIP:**
   ```powershell
   cd ServiceMgmt/incident-rca-foundry
   .\create-deploy-package.ps1
   az webapp deploy --resource-group <RG> --name td-rca-api --src-path deploy.zip --type zip
   ```

## API Endpoints

| Method | Path                | Description                          |
|--------|---------------------|--------------------------------------|
| GET    | `/health`           | Health check with connectivity status|
| POST   | `/api/rca/evidence` | Search RCA evidence                  |

## Foundry Agent Integration

The OpenAPI schema at `src/foundry/openapi_schema.json` is used to register this API as a tool in Azure AI Foundry agents. The agent calls `/api/rca/evidence` with an incident description and receives grounded historical matches.
