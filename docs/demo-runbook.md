# Demo Runbook: Incident RCA Agent

## Pre-Demo Setup

### Time Required: 10 minutes

---

## Step 1: Verify Azure Resources (2 minutes)

### Check Cosmos DB

```powershell
cd ServiceMgmt/incident-rca-foundry
python scripts/validate_seed_data.py
```

**Expected Output:**
```
✓ Validation passed! Data integrity confirmed.
Total Incidents: 30
Total Changes: 15
```

### Check Foundry Agent

1. Open Azure AI Foundry Portal: https://ai.azure.com
2. Navigate to project: **TD-BANK**
3. Go to **Agents** → Verify **Incident-RCA-Agent** exists
4. Check tool registration: **search_incident_rca_evidence**

---

## Step 2: Start the Evidence API (1 minute)

```powershell
cd ServiceMgmt/incident-rca-foundry
uvicorn src.api.main:app --reload
```

**Verify:**
- API running at: http://127.0.0.1:8000
- Health check: http://127.0.0.1:8000/health

---

## Demo Flow (7 minutes)

### Scenario 1: Broad Mobile Banking Issue (2 minutes)

**Purpose:** Show how the agent handles vague input with moderate confidence

#### Step 1A: Show the Raw Data
Navigate to Azure Portal → Cosmos DB → Data Explorer
- Database: **IncidentRCA**
- Container: **historical-incidents**
- Filter: `WHERE c.serviceKey = "mobile-banking"`
- Show INC10014 document

#### Step 1B: Agent Query
In Foundry playground, ask:
```
Mobile banking app not working
```

**Expected Response:**
```json
{
  "rootCause": "Mobile banking service disruption due to API node issues or configuration problems",
  "rootCauseCategory": "Application",
  "confidence": 68,
  "matchedIncidentIds": ["INC10014", "INC10015"],
  "relatedChangeId": "",
  "changeCorrelation": false,
  "evidence": [
    "Similarity score: 65/100",
    "Historical incident: INC10014",
    "Matched service: Mobile Banking",
    "Broad symptom match: unavailable"
  ]
}
```

**Key Points:**
✓ Agent called the retrieval tool (check logs)
✓ Moderate confidence (65-79) for broad input
✓ Multiple Mobile Banking matches
✓ No invented evidence

---

### Scenario 2: Specific Load Balancer Symptoms (2 minutes)

**Purpose:** Demonstrate improved accuracy with specific details

#### Step 2: Agent Query
```
Mobile banking is very slow and one API node appears overloaded
```

**Expected Response:**
```json
{
  "rootCause": "Load balancer health-check misconfiguration kept routing traffic to a degraded API node",
  "rootCauseCategory": "Network",
  "confidence": 85,
  "matchedIncidentIds": ["INC10014"],
  "relatedChangeId": "CHG50014",
  "changeCorrelation": true,
  "evidence": [
    "Similarity score: 82/100",
    "Historical incident: INC10014",
    "Matched service: Mobile Banking",
    "Symptom match: slowness, overloaded node, uneven traffic",
    "Root cause category: Network",
    "Related change: CHG50014 - Load Balancer Health Check Update",
    "Change validation: Partially Successful",
    "Post-implementation issues: 2 reported"
  ]
}
```

**Key Points:**
✓ Higher confidence (85 vs 68)
✓ Specific match: INC10014
✓ Change correlation with evidence
✓ Grounded change ID: CHG50014

#### Show Change Record
Navigate to Cosmos DB → change-records
- Filter: `WHERE c.changeId = "CHG50014"`
- Show validation result and post-implementation issues

---

### Scenario 3: Change-Oriented Input (1.5 minutes)

**Purpose:** Show strong change correlation

#### Step 3: Agent Query
```
Mobile banking became slow immediately after the load balancer update
```

**Expected Response:**
- Change correlation: **true**
- Evidence mentions timing and change validation
- Confidence: 85-90

**Key Points:**
✓ Strong temporal correlation
✓ Change evidence drives analysis

---

### Scenario 4: No Evidence (1.5 minutes)

**Purpose:** Show graceful handling of unknown issues

#### Step 4: Agent Query
```
An unrelated service with no historical records is unavailable
```

**Expected Response:**
```json
{
  "rootCause": "No similar historical incidents found",
  "rootCauseCategory": "Unknown",
  "confidence": 0,
  "matchedIncidentIds": [],
  "relatedChangeId": "",
  "changeCorrelation": false,
  "evidence": [
    "No grounded evidence available"
  ]
}
```

**Key Points:**
✓ Confidence: 0 (honest assessment)
✓ Empty matched IDs (no invented evidence)
✓ Clear "Unknown" category

---

## Key Demo Messages

### Technical Highlights
1. **Grounded Evidence**: All IDs, root causes, and details come from Cosmos DB
2. **Transparent Scoring**: 72/100 = 25 (service) + 20 (app) + 12 (symptoms) + 15 (tags)
3. **Change Validation**: Correlation requires supporting evidence, not just existence
4. **Calibrated Confidence**: Broad=68, Specific=85, None=0

### Business Value
1. **Faster MTTR**: Historical patterns guide troubleshooting
2. **Knowledge Retention**: Institutional knowledge persists
3. **Change Correlation**: Links incidents to related changes
4. **Transparency**: Every recommendation is traceable

---

## Demo Troubleshooting

### Issue: Agent not calling tool
**Fix:** Verify tool is registered and enabled in Foundry agent configuration

### Issue: Empty results
**Check:**
1. API is running (http://127.0.0.1:8000/health)
2. Tool endpoint configured correctly
3. Cosmos DB connection successful

### Issue: Invalid IDs returned
**Fix:** This shouldn't happen - it's a bug if IDs don't exist in Cosmos DB

### Issue: Low confidence for good matches
**Check:**
1. Synonym dictionaries in normalizer
2. Scoring weights in scorer.py
3. Historical data quality

---

## Post-Demo: Reset

### If data was modified:
```powershell
cd ServiceMgmt/incident-rca-foundry
python scripts/load_historical_incidents.py
python scripts/load_change_records.py
python scripts/validate_seed_data.py
```

---

## Demo Timing

| Phase | Duration |
|-------|----------|
| Setup Verification | 2 min |
| Scenario 1 (Broad) | 2 min |
| Scenario 2 (Specific) | 2 min |
| Scenario 3 (Change) | 1.5 min |
| Scenario 4 (No Evidence) | 1.5 min |
| Q&A Buffer | 1 min |
| **Total** | **10 min** |

---

## Supporting Materials

### Visual Aids
1. Architecture diagram (docs/architecture.md)
2. Data model diagram (docs/data-model.md)
3. Scoring breakdown table

### Code Samples
- Tool schema: `src/foundry/tool_schema.json`
- Response schema: `src/foundry/response_schema.json`
- Agent instructions: `src/foundry/agent_instructions.md`

---

## Success Criteria

Demo is successful when:
- [x] All 4 scenarios complete in <10 minutes
- [x] Agent calls retrieval tool every time
- [x] No invented evidence (all IDs exist)
- [x] Confidence properly calibrated
- [x] Change correlation is accurate
- [x] Audience understands grounded retrieval value
