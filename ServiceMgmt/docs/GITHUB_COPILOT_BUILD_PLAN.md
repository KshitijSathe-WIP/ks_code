# Incident RCA Agent on Microsoft Foundry and Azure Cosmos DB

## GitHub Copilot Build Plan

**Document purpose:** Provide an implementation plan that GitHub Copilot can follow to build a demo-ready Incident Root Cause Analysis (RCA) solution using Microsoft Foundry Agent Service and Azure Cosmos DB for NoSQL.

**Primary demo input:**

```text
Mobile banking app not working
```

**Primary demo output:** A grounded JSON response containing a probable root cause, root-cause category, confidence score, matched historical incident IDs, related change ID, change-correlation indicator, and evidence.

---

## 1. Executive Build Recommendation

Build the solution incrementally. Do not begin with a full multi-agent implementation or vector search.

### Demo-suitable build

Use the following architecture for the first working demonstration:

```text
User incident description
        |
        v
Microsoft Foundry RCA Prompt Agent
        |
        v
Controlled RCA Evidence Tool
(custom function or HTTP API)
        |
        +-----------------------------+
        |                             |
        v                             v
Cosmos DB                      Deterministic scoring
historical-incidents           and change correlation
        |
        | linkedChangeId
        v
Cosmos DB
change-records
        |
        v
Grounded evidence returned to agent
        |
        v
Strict RCA JSON response
```

### Suitable for the demo

- One Foundry RCA agent
- Azure Cosmos DB for NoSQL
- Two business-data containers
- Approximately 30 historical incidents
- Approximately 15–18 change records
- Deterministic keyword and metadata scoring
- Exact `linkedChangeId` to `changeId` correlation
- One controlled retrieval tool
- Structured JSON response
- Basic traceability and test cases

### Defer until after the demo

- Cosmos DB vector search
- Hybrid BM25/vector retrieval
- Five specialized child agents
- Microsoft Agent Framework orchestration
- Live ServiceNow integration
- Production networking and private endpoints
- Automated embeddings pipeline
- Continuous ingestion and data-quality workflows
- Human approval workflows

---

## 2. Objectives and Success Criteria

### Functional objectives

The system must:

1. Accept a short natural-language incident description.
2. Interpret the likely business service and broad symptom.
3. Query historical incidents in Cosmos DB.
4. Rank the most relevant resolved incidents.
5. retrieve related change records using an exact ID relationship.
6. determine the most probable root cause from grounded evidence.
7. return a consistent JSON object.
8. avoid inventing incident IDs, change IDs, or evidence.

### Demo success criteria

The demo is successful when all of the following are true:

- Input `Mobile banking app not working` returns at least one relevant historical incident.
- The top matches come from the Mobile Banking service.
- A valid related change is returned only if it exists in Cosmos DB.
- Confidence is moderate for vague input and higher for more specific input.
- Every incident and change ID in the response exists in Cosmos DB.
- The response contains no prose outside the JSON object.
- The model calls the retrieval tool before producing an RCA.
- An exact lookup test can retrieve `INC10014` and its related `CHG50014` record.

---

## 3. Repository Structure

GitHub Copilot should create and maintain the following structure:

```text
incident-rca-foundry/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── demo-runbook.md
│   ├── test-scenarios.md
│   └── production-roadmap.md
├── src/
│   ├── api/
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── settings.py
│   │   └── routes/
│   │       ├── health.py
│   │       └── rca.py
│   ├── cosmos/
│   │   ├── client.py
│   │   ├── repositories.py
│   │   └── queries.py
│   ├── retrieval/
│   │   ├── interpreter.py
│   │   ├── normalizer.py
│   │   ├── scorer.py
│   │   └── correlation.py
│   ├── foundry/
│   │   ├── agent_instructions.md
│   │   ├── tool_schema.json
│   │   └── response_schema.json
│   └── common/
│       ├── logging.py
│       └── exceptions.py
├── scripts/
│   ├── create_database.py
│   ├── load_historical_incidents.py
│   ├── load_change_records.py
│   └── validate_seed_data.py
├── data/
│   ├── historical_incidents.json
│   └── change_records.json
├── tests/
│   ├── unit/
│   │   ├── test_normalizer.py
│   │   ├── test_scorer.py
│   │   └── test_correlation.py
│   ├── integration/
│   │   ├── test_cosmos_repository.py
│   │   └── test_rca_endpoint.py
│   └── fixtures/
├── infra/
│   ├── main.bicep
│   └── parameters.demo.json
├── .env.example
├── .gitignore
├── requirements.txt
└── pyproject.toml
```

### GitHub Copilot guardrail

Do not generate the entire repository in one response. Implement one phase at a time, run the associated tests, and update documentation before moving to the next phase.

---

# PHASE 0 — Prerequisites and Technical Decisions

## Goal

Confirm that the required Azure resources and access are available before writing application code.

## Tasks

1. Confirm Cosmos DB API type is **Azure Cosmos DB for NoSQL**.
2. Confirm access to a Microsoft Foundry project.
3. Confirm that a tool-capable model is deployed in the Foundry project.
4. Choose the first tool integration:
   - **Recommended for demo:** a small HTTP API or application-hosted custom function.
   - Alternative: Azure Function.
5. Confirm authentication approach:
   - Local development: `DefaultAzureCredential` or Azure CLI authentication.
   - Azure deployment: managed identity.
6. Define environment settings in `.env.example` without committing secrets.

## Required settings

```text
AZURE_COSMOS_ENDPOINT=
AZURE_COSMOS_DATABASE=IncidentRCA
AZURE_COSMOS_INCIDENT_CONTAINER=historical-incidents
AZURE_COSMOS_CHANGE_CONTAINER=change-records
AZURE_AI_PROJECT_ENDPOINT=
AZURE_AI_MODEL_DEPLOYMENT_NAME=
LOG_LEVEL=INFO
```

## Deliverables

- Repository skeleton
- `.env.example`
- `README.md` prerequisites section
- Health-check endpoint or script

## Exit criteria

- Developer can authenticate locally.
- Cosmos DB endpoint can be reached.
- Foundry project and model deployment are identified.

## Demo suitability

**Required for demo.** Keep this phase lightweight; do not provision vector search or multi-agent infrastructure.

---

# PHASE 1 — Cosmos DB Data Foundation

## Goal

Create a clean, queryable business-data store for historical incidents and change records.

## Database and containers

```text
Database: IncidentRCA

Container 1: historical-incidents
Partition key: /serviceKey

Container 2: change-records
Partition key: /serviceKey
```

Use normalized lowercase partition-key values such as:

```text
mobile-banking
online-banking
payments-platform
regulatory-reporting
```

## Historical incident document

```json
{
  "id": "INC10014",
  "documentType": "historicalIncident",
  "serviceKey": "mobile-banking",
  "incidentId": "INC10014",
  "incidentTitle": "Mobile Banking Slowness Due to Load Balancer Misconfiguration",
  "incidentDescription": "Mobile users experienced intermittent slowness because traffic was routed unevenly to overloaded API nodes.",
  "severity": "P2",
  "businessService": "Mobile Banking",
  "applicationName": "Mobile Banking API",
  "configurationItem": "LB-MOB-PROD-01",
  "symptoms": [
    "Intermittent mobile latency",
    "One API node overloaded",
    "Uneven traffic distribution"
  ],
  "errorCodes": ["LB-MISCONFIG"],
  "rootCause": "Load balancer health-check misconfiguration kept routing traffic to a degraded API node.",
  "rootCauseCategory": "Network",
  "resolutionSummary": "Corrected load balancer algorithm and health probe settings.",
  "linkedChangeId": "CHG50014",
  "tags": [
    "mobile_banking",
    "load_balancer",
    "network_latency",
    "slowness",
    "api_unavailable"
  ],
  "searchText": "Mobile Banking Mobile Banking API app not working unavailable slow latency timeout load balancer health check uneven traffic overloaded API node network issue LB-MISCONFIG",
  "isResolved": true
}
```

## Change record document

```json
{
  "id": "CHG50014",
  "documentType": "changeRecord",
  "serviceKey": "mobile-banking",
  "changeId": "CHG50014",
  "changeTitle": "Load Balancer Health Check Update",
  "changeDescription": "Updated health probe configuration for the Mobile Banking API cluster.",
  "changeType": "Normal",
  "changeCategory": "Network",
  "changeStatus": "Completed",
  "businessService": "Mobile Banking",
  "applicationName": "Mobile Banking API",
  "configurationItem": "LB-MOB-PROD-01",
  "implementationSummary": "Updated load balancer health probes and routing configuration.",
  "rollbackPerformed": false,
  "validationResult": "Partially Successful",
  "postImplementationIssues": [
    "Uneven traffic routing observed",
    "One Mobile Banking API node received excessive traffic"
  ],
  "relatedIncidentIds": ["INC10014"],
  "changeCorrelationNotes": "The incident started shortly after the load balancer health-check update.",
  "tags": [
    "load_balancer",
    "health_check",
    "network",
    "mobile_banking",
    "uneven_routing"
  ],
  "searchText": "Mobile Banking Mobile Banking API load balancer health check network change uneven traffic routing degraded API node"
}
```

## Relationship rule

The application must perform this logical join:

```text
historical-incidents.linkedChangeId = change-records.changeId
```

There is no need for a physical relational constraint.

## Tasks

1. Create database and containers if they do not exist.
2. Convert the existing demo records to JSON documents.
3. Add normalized `serviceKey`, token arrays, and `searchText` fields.
4. Load 30 historical incidents.
5. Load at least 15 related change records.
6. Validate uniqueness of all `id`, `incidentId`, and `changeId` values.
7. Validate every populated `linkedChangeId` against `change-records`.
8. Create a seed-data validation report.

## Deliverables

- Container-creation script
- Incident and change seed files
- Data loaders
- Validation script
- Data model documentation

## Exit criteria

- `INC10014` can be retrieved by ID.
- `CHG50014` can be retrieved by ID.
- `INC10014.linkedChangeId` resolves to `CHG50014`.
- Mobile Banking records can be queried using `serviceKey = mobile-banking`.
- No orphaned change references exist, except explicitly documented test cases.

## Demo suitability

**Required for demo.** The two-container design and 30/15-record dataset are sufficient.

---

# PHASE 2 — Deterministic RCA Evidence Service

## Goal

Build a controlled service that searches Cosmos DB, ranks historical incidents, and correlates related changes.

## Recommended demo API

```text
POST /api/rca/evidence
```

### Request

```json
{
  "incidentDescription": "Mobile banking app not working",
  "topIncidentCount": 3
}
```

### Response

```json
{
  "interpretedContext": {
    "businessService": "Mobile Banking",
    "serviceKey": "mobile-banking",
    "probableApplication": "Mobile Banking API",
    "symptoms": ["unavailable"],
    "keywords": ["mobile", "banking", "app", "not working", "unavailable"]
  },
  "historicalMatches": [],
  "relatedChanges": []
}
```

## 2.1 Natural-language normalization

Implement a small, transparent synonym dictionary for the demo.

Examples:

```text
"mobile banking app" -> serviceKey: mobile-banking
"online banking" -> serviceKey: online-banking
"payments" -> serviceKey: payments-platform
"regulatory report" -> serviceKey: regulatory-reporting

"not working" -> unavailable, failed, outage, error
"slow" -> latency, timeout, degradation
"cannot log in" -> login failure, authentication failure, MFA, OAuth, LDAP
```

Rules:

- Do not infer a technical root cause at this stage.
- Infer only broad service, application family, and symptom category.
- Preserve the original user text.

## 2.2 Candidate retrieval

For the demo:

1. Query by `serviceKey` when it can be inferred.
2. Otherwise query a limited set of resolved incidents.
3. Filter to `isResolved = true`.
4. Do not scan unrestricted production containers.
5. Retrieve enough candidates for scoring, such as the top 25 within the service partition.

## 2.3 Weighted scoring

Use transparent deterministic scoring:

```text
Same BusinessService or serviceKey     +25
Same or close ApplicationName          +20
Symptom keyword overlap                +25
Tag or searchText overlap              +15
Matching error code                    +10
Matching ConfigurationItem              +5
Maximum                                 100
```

Implementation requirements:

- Case-insensitive comparisons
- Trim whitespace
- Normalize underscores, hyphens, and punctuation
- Deduplicate keywords
- Return a score breakdown for diagnostics
- Return no more than three matches to the agent
- Apply a minimum meaningful-match threshold

## 2.4 Change correlation

For each selected historical incident:

1. Read `linkedChangeId`.
2. If empty, do not query a change.
3. Retrieve the exact change document.
4. Validate that the service, application, or configuration item is related.
5. Inspect validation result, rollback status, post-implementation issues, and correlation notes.

Set `changeSupported` to true only if:

- the exact change exists; and
- the service, application, or CI aligns; and
- the change evidence supports the recorded root cause.

A populated `linkedChangeId` alone is not enough.

## 2.5 Evidence service output

```json
{
  "interpretedContext": {
    "businessService": "Mobile Banking",
    "serviceKey": "mobile-banking",
    "probableApplication": "Mobile Banking API",
    "symptoms": ["unavailable"],
    "keywords": ["mobile", "banking", "app", "not working", "unavailable"]
  },
  "historicalMatches": [
    {
      "incidentId": "INC10014",
      "similarityScore": 72,
      "scoreBreakdown": {
        "service": 25,
        "application": 20,
        "symptoms": 12,
        "tags": 15,
        "errorCode": 0,
        "configurationItem": 0
      },
      "incidentTitle": "Mobile Banking Slowness Due to Load Balancer Misconfiguration",
      "rootCause": "Load balancer health-check misconfiguration",
      "rootCauseCategory": "Network",
      "linkedChangeId": "CHG50014"
    }
  ],
  "relatedChanges": [
    {
      "changeId": "CHG50014",
      "changeTitle": "Load Balancer Health Check Update",
      "validationResult": "Partially Successful",
      "rollbackPerformed": false,
      "postImplementationIssues": ["Uneven traffic routing observed"],
      "changeSupported": true
    }
  ]
}
```

## Deliverables

- Cosmos repositories
- Input interpreter
- Normalizer
- Deterministic scorer
- Change correlation module
- Evidence API
- Unit tests
- Integration tests

## Exit criteria

- Vague Mobile Banking input returns relevant Mobile Banking candidates.
- Specific input ranks the correct incident higher than vague input.
- Exact change lookup works.
- No nonexistent IDs are returned.
- Score breakdown totals are correct.
- Empty or irrelevant input returns no grounded matches.

## Demo suitability

**Core demo phase.** This is the most important phase because it makes retrieval deterministic and debuggable.

---

# PHASE 3 — Microsoft Foundry RCA Agent

## Goal

Create one Foundry prompt agent that always uses the controlled retrieval tool and returns strict RCA JSON.

## Agent identity

```text
Name: Incident-RCA-Agent
Description: Analyzes natural-language production incident descriptions using grounded historical incident and change evidence.
```

## Tool

Expose one high-level tool:

```text
search_incident_rca_evidence
```

### Tool input schema

```json
{
  "type": "object",
  "properties": {
    "incidentDescription": {
      "type": "string",
      "description": "The user's complete incident description."
    },
    "topIncidentCount": {
      "type": "integer",
      "minimum": 1,
      "maximum": 3,
      "default": 3
    }
  },
  "required": ["incidentDescription"],
  "additionalProperties": false
}
```

## Agent instructions

Store the final instruction in `src/foundry/agent_instructions.md`:

```text
You are a Root Cause Analysis Specialist for banking production incidents.

The user can provide a short and nontechnical report, such as:
"Mobile banking app not working."

You must call search_incident_rca_evidence before determining a root cause.
Do not answer from general knowledge.

Use only the historical incidents and change records returned by the tool.
Never invent an incident ID, change ID, error code, root cause, or evidence.

Select the root cause supported by the strongest combination of:
- historical similarity score;
- same business service;
- same application;
- similar symptoms;
- confirmed historical root cause; and
- supporting change evidence.

Set changeCorrelation to true only when the tool returns a related change
whose service/application/CI and implementation evidence support the root cause.

For vague input, use moderate confidence and describe the result as probable.
If the tool returns no meaningful match, return confidence 0.

Do not provide recommendations or resolution steps.
Return only the required JSON object with no markdown or surrounding text.
```

## Response schema

Store in `src/foundry/response_schema.json`:

```json
{
  "type": "object",
  "properties": {
    "rootCause": {"type": "string"},
    "rootCauseCategory": {"type": "string"},
    "confidence": {
      "type": "integer",
      "minimum": 0,
      "maximum": 100
    },
    "matchedIncidentIds": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 3
    },
    "relatedChangeId": {"type": "string"},
    "changeCorrelation": {"type": "boolean"},
    "evidence": {
      "type": "array",
      "items": {"type": "string"}
    }
  },
  "required": [
    "rootCause",
    "rootCauseCategory",
    "confidence",
    "matchedIncidentIds",
    "relatedChangeId",
    "changeCorrelation",
    "evidence"
  ],
  "additionalProperties": false
}
```

## Confidence guidance

```text
90–100: Specific diagnostic input strongly matches historical and change evidence.
80–89: Strong service/application/symptom match with supporting change evidence.
65–79: Broad input; one root cause is better supported than alternatives.
40–64: Multiple causes remain similarly plausible.
1–39: Weak grounded evidence.
0: No meaningful grounded match.
```

## Deliverables

- Foundry prompt agent
- Registered retrieval tool
- Agent instructions
- Strict response schema or application-level validation
- Tool-call trace verification
- Agent integration tests

## Exit criteria

- Agent calls the retrieval tool for every RCA request.
- Agent returns valid JSON.
- Agent returns only IDs present in tool output.
- Broad input receives moderate confidence.
- Specific input produces improved ranking/confidence.
- No recommendations are returned.

## Demo suitability

**Required for demo.** Use one agent only. Do not add child agents until this stage is stable.

---

# PHASE 4 — Demo Hardening and Presentation

## Goal

Make the solution predictable, observable, and executive-demo ready.

## Required demo scenarios

### Scenario 1 — Broad Mobile Banking issue

```text
Mobile banking app not working
```

Expected behavior:

- Identifies Mobile Banking.
- Returns relevant Mobile Banking historical incidents.
- Uses moderate confidence.
- Does not claim a confirmed technical diagnosis.

### Scenario 2 — More specific load-balancer symptom

```text
Mobile banking is very slow and one API node appears overloaded.
```

Expected behavior:

- Ranks `INC10014` first.
- Returns `CHG50014` if the change supports the root cause.
- Returns higher confidence than Scenario 1.

### Scenario 3 — Change-oriented input

```text
Mobile banking became slow immediately after the load balancer update.
```

Expected behavior:

- Strong change correlation.
- Uses change validation and post-implementation issues as evidence.

### Scenario 4 — Payment failure

```text
Payments started failing after the API release.
```

Expected behavior:

- Searches the Payments Platform partition.
- Returns payment-related incidents and relevant changes.

### Scenario 5 — Regulatory batch issue

```text
The regulatory reporting batch did not complete overnight.
```

Expected behavior:

- Searches Regulatory Reporting records.
- Ranks ETL/schema/database/batch incidents.

### Scenario 6 — No evidence

```text
An unrelated service with no historical records is unavailable.
```

Expected behavior:

- Does not invent evidence.
- Returns confidence 0 and empty matched IDs.

## Demo runbook

The demo should show:

1. Cosmos DB records for one incident and its related change.
2. The natural-language user query.
3. The Foundry agent tool call.
4. The grounded tool payload.
5. The final RCA JSON.
6. A second, more specific query demonstrating improved confidence.

## Observability

Capture:

- Correlation ID
- Request timestamp
- Tool invocation duration
- Cosmos query duration and RU charge
- Candidate count
- Selected incident IDs
- Selected change IDs
- Final confidence
- Validation failures

Do not log secrets or confidential incident descriptions in production.

## Deliverables

- Demo runbook
- Six tested scenarios
- Expected outputs
- Screenshots or trace IDs
- Known limitations section
- Troubleshooting guide

## Exit criteria

- All demo scenarios pass consistently.
- The solution can be reset and reseeded.
- A failed tool call produces a controlled error response.
- The final demo takes less than ten minutes.

## Demo suitability

**Required for a polished demo.** This phase is about reliability and presentation, not adding new architecture.

---

# PHASE 5 — Post-Demo Retrieval Enhancement

## Goal

Improve semantic retrieval when the data volume and vocabulary outgrow deterministic scoring.

## Add after the demo

1. Generate an embedding from each document's `searchText`.
2. Store the embedding alongside the incident document.
3. Create a vector-enabled version of the incident container.
4. Enable full-text search over selected fields.
5. Combine vector and full-text ranking using hybrid search.
6. Retain metadata filters for `serviceKey`, application, severity, and status.
7. Compare hybrid retrieval against the deterministic baseline.

## Important design constraint

Treat the vector-enabled container as a new version, for example:

```text
historical-incidents-v2
```

Do not disrupt the working demo container while experimenting with vector policies.

## Evaluation metrics

- Recall@3
- Precision@3
- Mean reciprocal rank
- Correct-service rate
- Correct-change correlation rate
- Grounded-ID accuracy
- Average RU charge
- P95 retrieval latency

## Exit criteria

- Hybrid search outperforms deterministic scoring on an approved test set.
- Existing exact-ID and metadata filters still work.
- No reduction in grounding accuracy.

## Demo suitability

**Not required for the first demo.** Present as the planned scalability and accuracy enhancement.

---

# PHASE 6 — Post-Demo Multi-Agent Expansion

## Goal

Expand the proven RCA component into an orchestrated incident-management solution.

## Proposed agents

```text
Incident Management Orchestrator
        |
        +-- Incident Intake Agent
        +-- RCA Agent
        +-- Change Impact Agent
        +-- Knowledge Agent
        +-- Resolution Agent
```

## Recommended sequence

```text
User request
   ↓
Incident Intake Agent
   ↓ normalized incident JSON
RCA Agent
   ↓ grounded RCA JSON
Change Impact Agent
   ↓ change impact JSON
Knowledge Agent
   ↓ relevant KB/runbook JSON
Resolution Agent
   ↓ resolution options JSON
Orchestrator
   ↓ final response
```

## Orchestration guidance

- Use Microsoft Agent Framework for the new implementation.
- Use explicit typed contracts between agents.
- Keep Cosmos DB access behind controlled tools.
- Do not let every agent perform unrestricted searches.
- Store trace and execution state separately from business source containers.
- Add timeout, retry, and fallback behavior for each agent call.
- Add human approval before any production action.

## Demo suitability

**Do not include in the first RCA feasibility demo.** Mention as the target architecture after the RCA component is validated.

---

# PHASE 7 — Production Readiness

## Goal

Replace the demo assumptions with enterprise controls and live integrations.

## Production workstreams

### Data ingestion

- Integrate with ServiceNow or the enterprise ITSM platform.
- Incrementally load incidents, changes, problems, and knowledge articles.
- Add schema validation and data-quality checks.
- Remove or tokenize sensitive data.
- Track ingestion timestamps and source-system IDs.

### Security

- Use managed identity.
- Apply least-privilege Cosmos DB data-plane roles.
- Use private endpoints where required.
- Separate read-only RCA identity from data-loader identity.
- Use Key Vault for secrets that cannot use managed identity.
- Enable audit logging and threat protection.

### Reliability

- Add retries with bounded exponential backoff.
- Add circuit breakers and tool timeouts.
- Add health and readiness endpoints.
- Add dead-letter handling for failed ingestion.
- Establish backup and recovery procedures.

### Governance

- Add prompt and tool versioning.
- Maintain an approved evaluation dataset.
- Capture grounding and hallucination metrics.
- Add change control for scoring and prompt modifications.
- Define retention policies for incidents, traces, and conversations.

### User experience

- Allow analysts to inspect matched evidence.
- Clearly label RCA as probable versus confirmed.
- Collect analyst feedback on matches and root causes.
- Support escalation when confidence is low.

## Demo suitability

**Not required for demo implementation.** Include these controls in the roadmap to demonstrate production awareness.

---

## 4. Testing Strategy

### Unit tests

Test:

- Text normalization
- Service classification
- Symptom expansion
- Scoring calculations
- Score caps
- Exact change joins
- Change-support rules
- Empty-input behavior
- JSON schema validation

### Integration tests

Test:

- Cosmos DB authentication
- Incident lookup by ID
- Change lookup by ID
- Search by service partition
- Evidence endpoint
- Orphaned change handling
- Tool timeout handling

### Agent tests

Test:

- Tool is always invoked
- IDs are restricted to tool output
- Confidence is within 0–100
- Vague input is not overconfident
- No evidence produces confidence 0
- Response contains no markdown or recommendations

### Regression dataset

Maintain at least 20 approved test prompts with expected:

- Service classification
- Top-three candidate IDs
- Expected root-cause category
- Expected change ID, if any
- Confidence range

---

## 5. Non-Functional Requirements

### Performance targets for demo

```text
Evidence retrieval P95: under 3 seconds
End-to-end agent response P95: under 10 seconds
Maximum returned historical matches: 3
Maximum evidence items: 8
```

### Reliability

- Tool response must include a version field.
- Every request must have a correlation ID.
- Every returned ID must be validated.
- Model output must be validated against the response schema.
- Invalid model output should be repaired once or rejected safely.

### Cost controls

- Query by partition key whenever possible.
- Limit candidate retrieval.
- Return only fields required by the agent.
- Keep evidence concise.
- Do not call the model to perform deterministic joins.
- Do not enable vector search until evaluation shows a benefit.

---

## 6. Definition of Done by Phase

### Demo MVP is done after Phase 4

The demo MVP includes:

- Phase 0: prerequisites
- Phase 1: Cosmos DB data foundation
- Phase 2: deterministic evidence service
- Phase 3: one Foundry RCA agent
- Phase 4: demo hardening

### Phase 5 and beyond are roadmap items

- Phase 5: vector and hybrid retrieval
- Phase 6: multi-agent orchestration
- Phase 7: production readiness

---

## 7. GitHub Copilot Execution Instructions

GitHub Copilot should follow these operating rules:

1. Work only on the current phase.
2. Before coding, restate the phase goal, assumptions, files to modify, and acceptance criteria.
3. Prefer small modules with clear responsibilities.
4. Use typed request and response models.
5. Do not hard-code secrets, endpoints, database names, or credentials.
6. Use managed-identity-compatible authentication.
7. Add tests with every implementation increment.
8. Run tests and report failures before proceeding.
9. Keep Cosmos DB queries parameterized.
10. Query by partition key whenever available.
11. Never return a historical incident or change ID that was not retrieved from Cosmos DB.
12. Keep deterministic retrieval separate from LLM reasoning.
13. Do not add vector search during the demo phases.
14. Do not add more agents until the single RCA agent passes all demo tests.
15. Update the relevant documentation and README after every phase.
16. Stop at each phase exit criterion and produce a completion summary.

### Suggested Copilot prompt for each phase

```text
Implement only PHASE <number> from docs/GITHUB_COPILOT_BUILD_PLAN.md.

Before making changes:
1. Summarize the phase goal.
2. List assumptions.
3. List files you will create or modify.
4. Restate the acceptance criteria.

During implementation:
- Keep modules small and typed.
- Add unit and integration tests.
- Do not implement work assigned to later phases.
- Do not hard-code secrets.
- Use managed-identity-compatible Azure authentication.

After implementation:
1. Run the relevant tests.
2. Report test results.
3. Update README and phase documentation.
4. List remaining issues.
5. Stop and wait before starting the next phase.
```

---

## 8. Known Demo Limitations

Document these honestly during the presentation:

- A vague incident statement cannot prove a definitive root cause.
- The demo returns the most historically supported probable cause.
- The seed dataset is intentionally small and curated.
- Deterministic scoring uses a controlled synonym dictionary.
- Historical correlation does not replace engineering validation.
- Change correlation requires supporting evidence; a linked ID alone is insufficient.
- Live monitoring, log analysis, CMDB dependency traversal, and ServiceNow integration are roadmap items.

---

## 9. Reference Architecture Decisions

- Microsoft Foundry Agent Service provides prompt and hosted agents, tools, managed runtime, and observability.
- Cosmos DB business data is accessed through a controlled function/API tool; Cosmos thread storage is a separate concern.
- Cosmos DB for NoSQL can later support vector, full-text, and hybrid search.
- Foundry function calling supports controlled custom capabilities.
- Microsoft Agent Framework is the preferred post-demo orchestration approach.

---

## 10. Final Recommendation

For the feasibility demo, stop at **Phase 4**.

The strongest demo story is:

```text
Natural-language incident
→ controlled Cosmos DB retrieval
→ transparent historical ranking
→ exact incident/change correlation
→ grounded Foundry reasoning
→ strict RCA JSON
```

This demonstrates feasibility, traceability, and enterprise extensibility without introducing premature vector-search or multi-agent complexity.
