# Architecture Overview

## System Architecture

The Incident RCA system uses a controlled retrieval pattern to ensure grounded evidence:

```
┌─────────────────────────────────────────────────────────────┐
│                     User / Client                            │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            Microsoft Foundry RCA Prompt Agent                │
│  • Interprets incident description                           │
│  • Calls retrieval tool (mandatory)                          │
│  • Reasons over grounded evidence                            │
│  • Returns strict JSON response                              │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│         Controlled RCA Evidence API (FastAPI)                │
│  • Normalizes natural language input                         │
│  • Queries Cosmos DB by partition key                        │
│  • Scores candidates deterministically                       │
│  • Correlates exact change relationships                     │
│  • Returns only grounded evidence                            │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│           Azure Cosmos DB for NoSQL                          │
│                                                               │
│  Container: historical-incidents (partition: serviceKey)     │
│  Container: change-records (partition: serviceKey)           │
│                                                               │
│  Relationship: incidents.linkedChangeId → changes.changeId   │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Principles

### 1. Grounded Evidence Only
- Never return incident/change IDs not retrieved from Cosmos DB
- All evidence must be traceable to source documents
- Agent must call retrieval tool before producing RCA

### 2. Deterministic Retrieval
- Transparent weighted scoring (not black-box)
- Explicit service/symptom/tag matching
- Debuggable score breakdowns
- Vector search deferred to Phase 5

### 3. Partition-Aware Queries
- All queries use `serviceKey` partition key when possible
- Normalized service identifiers (e.g., "mobile-banking")
- Efficient RU consumption

### 4. Exact Change Correlation
- Changes linked via explicit `linkedChangeId` field
- Validation of change evidence (rollback, issues, validation result)
- `changeSupported` flag requires evidence alignment

### 5. Controlled Tool Access
- Cosmos DB accessed only through Evidence API
- No direct database access by agent
- Rate limiting and validation at API layer

## Component Responsibilities

### Evidence API (`src/api/`)
- FastAPI endpoints for health and evidence retrieval
- Request validation and response formatting
- Correlation ID tracking

### Cosmos DB Layer (`src/cosmos/`)
- Authenticated client initialization
- Repository pattern for incidents and changes
- Parameterized queries

### Retrieval Engine (`src/retrieval/`)
- Natural language normalization
- Synonym dictionary for services/symptoms
- Weighted scoring algorithm
- Change correlation logic

### Foundry Agent (`src/foundry/`)
- Agent instructions and system prompt
- Tool schema definitions
- Response schema validation

### Common Utilities (`src/common/`)
- Centralized logging
- Custom exceptions
- Shared models

## Authentication Flow

```
Local Development:
  DefaultAzureCredential → Azure CLI login → Token

Production:
  Managed Identity → Azure AD → Token

Cosmos DB:
  Token → Data Plane RBAC → Query
```

## Data Flow Example

**Input:** "Mobile banking app not working"

1. **Agent receives request**
   - Understands broad intent
   - Calls `search_incident_rca_evidence` tool

2. **Evidence API normalizes**
   - `businessService`: "Mobile Banking"
   - `serviceKey`: "mobile-banking"
   - `symptoms`: ["unavailable", "failed", "outage"]
   - `keywords`: ["mobile", "banking", "app", "not working"]

3. **Cosmos DB query**
   ```sql
   SELECT * FROM c 
   WHERE c.serviceKey = "mobile-banking" 
   AND c.isResolved = true
   ```

4. **Scoring**
   - Service match: +25
   - Application match: +20
   - Symptom overlap: +15
   - Tag overlap: +10
   - **Total: 70**

5. **Change correlation**
   - Read `linkedChangeId`: "CHG50014"
   - Retrieve change document
   - Validate evidence alignment
   - Set `changeSupported`: true

6. **Evidence response**
   ```json
   {
     "historicalMatches": [{
       "incidentId": "INC10014",
       "similarityScore": 70,
       "rootCause": "Load balancer misconfiguration",
       "linkedChangeId": "CHG50014"
     }],
     "relatedChanges": [{
       "changeId": "CHG50014",
       "changeSupported": true
     }]
   }
   ```

7. **Agent reasoning**
   - Evaluates evidence strength
   - Determines most probable root cause
   - Assigns confidence based on match quality
   - Returns strict JSON

8. **Final response**
   ```json
   {
     "rootCause": "Load balancer health-check misconfiguration",
     "rootCauseCategory": "Network",
     "confidence": 72,
     "matchedIncidentIds": ["INC10014"],
     "relatedChangeId": "CHG50014",
     "changeCorrelation": true,
     "evidence": [...]
   }
   ```

## Future Architecture (Phase 6)

Multi-agent orchestration with Microsoft Agent Framework:

```
Orchestrator
  ├── Intake Agent (classify & normalize)
  ├── RCA Agent (current implementation)
  ├── Change Impact Agent (assess blast radius)
  ├── Knowledge Agent (retrieve runbooks)
  └── Resolution Agent (suggest actions)
```

Each agent maintains strict contracts and controlled data access.
