# Incident RCA Agent on Microsoft Foundry and Azure Cosmos DB

## Overview

A demonstration system that performs root cause analysis (RCA) on production incidents using:
- **Microsoft Foundry Agent Service** for AI-powered reasoning
- **Azure Cosmos DB for NoSQL** for historical incident and change data
- **Deterministic retrieval** with transparent scoring
- **Grounded evidence** to prevent hallucination

## Demo Input/Output

**Input:**
```
Mobile banking app not working
```

**Output:**
```json
{
  "rootCause": "Load balancer health-check misconfiguration",
  "rootCauseCategory": "Network",
  "confidence": 72,
  "matchedIncidentIds": ["INC10014"],
  "relatedChangeId": "CHG50014",
  "changeCorrelation": true,
  "evidence": [
    "Historical incident INC10014 matched with 72% similarity",
    "Same Mobile Banking service and application",
    "Related change CHG50014 updated load balancer health checks",
    "Post-implementation issues reported uneven traffic routing"
  ]
}
```

## Prerequisites

### Azure Resources
1. **Azure Cosmos DB for NoSQL** account with contributor access
2. **Microsoft Foundry** project with tool-capable model deployment (GPT-4 recommended)

### Local Development
1. **Python 3.11+** installed
2. **Azure CLI** installed and authenticated: `az login`
3. **Git** for version control

### Permissions
- `Cosmos DB Data Contributor` role on the Cosmos DB account
- Access to the Foundry project and model deployment

## Quick Start

### 1. Clone and Setup

```powershell
cd ServiceMgmt\incident-rca-foundry
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and update with your values:

```powershell
cp .env.example .env
```

Edit `.env` with your actual Azure endpoints and deployment names.

### 3. Verify Prerequisites

```powershell
python scripts/verify_prerequisites.py
```

This script checks:
- Azure CLI authentication
- Cosmos DB connectivity
- Foundry project access
- Required Python packages

### 4. Initialize Database

```powershell
python scripts/create_database.py
python scripts/load_historical_incidents.py
python scripts/load_change_records.py
python scripts/validate_seed_data.py
```

### 5. Run API Server

```powershell
uvicorn src.api.main:app --reload
```

### 6. Test the System

```powershell
# Run all tests
pytest

# Run specific test suites
pytest tests/unit
pytest tests/integration
```

## Project Structure

```
incident-rca-foundry/
├── src/
│   ├── api/              # FastAPI application
│   ├── cosmos/           # Cosmos DB client and repositories
│   ├── retrieval/        # Evidence retrieval and scoring
│   ├── foundry/          # Foundry agent configuration
│   └── common/           # Shared utilities
├── scripts/              # Setup and maintenance scripts
├── data/                 # Seed data files
├── tests/                # Unit and integration tests
├── docs/                 # Documentation
└── infra/                # Azure infrastructure (Bicep)
```

## Architecture

```
User Incident Description
        ↓
Microsoft Foundry RCA Agent
        ↓
Controlled RCA Evidence Tool (FastAPI)
        ↓
Cosmos DB Query (by serviceKey)
        ↓
Deterministic Scoring Engine
        ↓
Change Correlation (exact linkedChangeId)
        ↓
Grounded Evidence → Agent
        ↓
Strict JSON Response
```

## Key Design Decisions

- **Deterministic retrieval first**: Transparent scoring before vector search
- **Single agent**: One RCA agent for demo; multi-agent is Phase 6
- **Grounded IDs only**: Never return incident/change IDs not in Cosmos DB
- **Controlled tools**: Cosmos DB access through explicit API, not direct
- **Partition keys**: All queries use `serviceKey` partition key
- **Change validation**: Related changes require supporting evidence

## Demo Scenarios

See [docs/demo-runbook.md](docs/demo-runbook.md) for six validated test scenarios.

## Testing Strategy

- **Unit tests**: Scoring, normalization, correlation logic
- **Integration tests**: Cosmos DB queries, API endpoints
- **Agent tests**: Tool invocation, response validation
- **Regression dataset**: 20+ approved test prompts

## Known Limitations (Demo Phase)

- Small curated dataset (30 incidents, 15 changes)
- Deterministic scoring with manual synonym dictionary
- No vector/semantic search (planned for Phase 5)
- No multi-agent orchestration (planned for Phase 6)
- No live ServiceNow integration (planned for Phase 7)

## Roadmap

- **Phase 5**: Vector and hybrid retrieval for improved semantic matching
- **Phase 6**: Multi-agent orchestration with Microsoft Agent Framework
- **Phase 7**: Production readiness (security, governance, live integration)

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Data Model](docs/data-model.md)
- [Demo Runbook](docs/demo-runbook.md)
- [Test Scenarios](docs/test-scenarios.md)
- [Production Roadmap](docs/production-roadmap.md)

## Support

For issues or questions, refer to the [GitHub Copilot Build Plan](docs/GITHUB_COPILOT_BUILD_PLAN.md).
