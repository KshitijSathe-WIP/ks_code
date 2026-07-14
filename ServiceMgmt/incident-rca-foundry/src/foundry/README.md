# Microsoft Foundry RCA Agent Configuration Guide

## Overview

This guide explains how to configure and deploy the Microsoft Foundry Root Cause Analysis (RCA) Agent for production incident analysis.

## Agent Configuration

### Agent Identity

- **Name:** `Incident-RCA-Agent`
- **Description:** Analyzes natural-language production incident descriptions using grounded historical incident and change evidence
- **Type:** Prompt Agent (single agent, tool-enabled)

### Required Files

All agent configuration files are located in `src/foundry/`:

1. **agent_instructions.md** - System instructions for the agent
2. **tool_schema.json** - Function definition for the retrieval tool
3. **response_schema.json** - JSON schema for RCA responses
4. **validator.py** - Response validation logic

## Tool Configuration

### Tool: search_incident_rca_evidence

**Purpose:** Search historical incident and change evidence to support root cause analysis.

**Input Parameters:**
```json
{
  "incidentDescription": "string (required)",
  "topIncidentCount": "integer (optional, default: 3, range: 1-5)"
}
```

**Tool Implementation:**
The tool calls the Evidence Retrieval API endpoint:
```
POST /api/rca/evidence
```

The API returns:
- Interpreted context (inferred service, symptoms, keywords)
- Historical incident matches with scores
- Related change records with correlation analysis

## Response Schema

The agent must return a JSON object with this structure:

```json
{
  "rootCause": "string (10-500 chars)",
  "rootCauseCategory": "enum [Application|Database|Network|Infrastructure|Configuration|Security|Data Quality|Integration|Performance|Unknown]",
  "confidence": "integer (0-100)",
  "matchedIncidentIds": ["array of INC##### strings, max 3"],
  "relatedChangeId": "string (CHG##### or empty)",
  "changeCorrelation": "boolean",
  "evidence": ["array of 1-10 evidence statements"]
}
```

### Confidence Calibration

| Range | Interpretation |
|-------|----------------|
| 90-100 | Specific diagnostic input strongly matches historical and change evidence |
| 80-89 | Strong service/application/symptom match with supporting change evidence |
| 65-79 | Broad input; one root cause is better supported than alternatives |
| 40-64 | Multiple causes remain similarly plausible |
| 1-39 | Weak grounded evidence |
| 0 | No meaningful grounded match |

## Agent Instructions Summary

### Critical Requirements

1. **Always call the retrieval tool** - Never answer from general knowledge
2. **Never invent evidence** - All IDs, root causes, and details must come from tool output
3. **Proper change correlation** - Set `changeCorrelation: true` only when change evidence supports the root cause
4. **Calibrated confidence** - Use moderate confidence (65-79) for broad input
5. **JSON only** - Return only the required JSON object with no markdown or surrounding text

### Evidence Construction

Build evidence from:
- Similarity score
- Historical incident IDs
- Matched service/application
- Symptom matches
- Root cause category
- Related change details (if supported)
- Change validation status
- Post-implementation issues

## Deployment Steps

### 1. Create the Agent in Foundry

Using Azure AI Foundry Portal:

1. Navigate to your Foundry project
2. Go to **Agents** → **Create new agent**
3. Enter agent name: `Incident-RCA-Agent`
4. Select a tool-capable model (e.g., GPT-4o, GPT-4 Turbo)

### 2. Configure System Instructions

Copy the contents of `src/foundry/agent_instructions.md` to the agent's system instructions.

### 3. Register the Tool

1. In the agent configuration, select **Add tool** → **Custom function**
2. Paste the contents of `src/foundry/tool_schema.json`
3. Configure tool endpoint:
   - **Endpoint:** Your Evidence API URL (`https://<your-host>/api/rca/evidence`)
   - **Method:** POST
   - **Authentication:** As appropriate (managed identity recommended)

### 4. Configure Tool Mapping

Map tool parameters to API request:
```
incidentDescription → body.incidentDescription
topIncidentCount → body.topIncidentCount
```

### 5. Test the Agent

Use the test scenarios from `tests/integration/test_agent_scenarios.py`:

**Test 1 - Broad Input:**
```
Mobile banking app not working
```
Expected: Moderate confidence (65-79), Mobile Banking matches

**Test 2 - Specific Input:**
```
Mobile banking is very slow and one API node appears overloaded
```
Expected: Higher confidence (80+), INC10014 with CHG50014

**Test 3 - No Evidence:**
```
An unrelated service with no historical records is unavailable
```
Expected: Confidence 0, empty matched IDs

## Response Validation

The `RCAResponseValidator` class provides programmatic validation:

```python
from src.foundry.validator import RCAResponseValidator

validator = RCAResponseValidator()
is_valid, errors = validator.validate(response)

if not is_valid:
    print(f"Validation errors: {errors}")
```

### Validation Rules

**Schema Validation:**
- All required fields present
- Correct data types
- Valid enums and patterns
- Array size constraints

**Business Logic Validation:**
- Confidence 0 → empty matchedIncidentIds
- changeCorrelation true → non-empty relatedChangeId
- All IDs mentioned in evidence
- High confidence → no vague terms in root cause

## Monitoring and Observability

When deployed, monitor:

1. **Tool Call Rate** - Agent should call tool for every RCA request
2. **Response Validation** - Track validation failures
3. **Confidence Distribution** - Ensure appropriate calibration
4. **ID Accuracy** - Verify all returned IDs exist in Cosmos DB
5. **Response Time** - Tool call + agent processing time

## Troubleshooting

### Agent Not Calling Tool

- Verify tool is registered and enabled
- Check system instructions emphasize tool requirement
- Ensure model supports tool calling

### Invalid JSON Responses

- Review system instructions for JSON-only requirement
- Add response format validation in agent configuration
- Check for markdown code fences in output

### Low Confidence for Good Matches

- Review scoring algorithm in Evidence API
- Check synonym dictionaries in normalizer
- Verify historical data quality

### Invented Evidence

- Strengthen "never invent" instructions
- Add post-processing validation
- Log and alert on ungrounded IDs

## Demo Scenarios

Six validated demo scenarios are defined in:
```
tests/integration/test_agent_scenarios.py
```

Each scenario tests specific agent behaviors:
1. Broad input handling
2. Specific symptom ranking
3. Change correlation
4. Service-specific search
5. Batch/ETL issues
6. No evidence graceful handling

## Next Steps

After PHASE 3 completion:
- **PHASE 4:** Demo hardening and observability
- **PHASE 5:** Vector search enhancement
- **PHASE 6:** Multi-agent expansion

## Related Documentation

- [Architecture Overview](../docs/architecture.md)
- [Data Model](../docs/data-model.md)
- [Build Plan](../../docs/GITHUB_COPILOT_BUILD_PLAN.md)
- [API Documentation](../README.md#api-endpoints)
