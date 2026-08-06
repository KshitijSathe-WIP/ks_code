# OIR Autonomous Agent Platform — Implementation Specification

> **Audience:** GitHub Copilot / implementing engineer
> **Purpose:** Build an autonomous multi-agent system that ingests the daily TD Bank OIR file, detects stale and expiring demands, notifies owners via Microsoft Teams, and writes structured updates back to a Dataverse master.
> **Owner:** Kshitij Sathe — Senior Architect
> **Version:** 1.0

---

## 1. Problem Statement

A daily Excel file (`TD Bank OIR DD-MM-YYYY.xlsx`) is published to SharePoint containing open demand, joiner pipeline, and bench (AXNB) data. Today this file is edited manually by multiple people, producing concurrent conflicting copies, no audit trail, and no way to detect which demands have gone stale.

The system must:

1. Ingest the daily file automatically.
2. Persist an immutable daily snapshot to enable historical comparison.
3. Detect demands whose **Comments** and **Remarks Status** have not changed for **2 days**.
4. Detect demands whose **DEM_End_Date** falls within the **next 2 days**.
5. Notify the responsible **PM**, **TM**, and (for expiry) **EM** through Microsoft Teams.
6. Accept structured replies in Teams and write them back to the master record.
7. Maintain a complete audit trail of every notification and every update.

---

## 2. Architecture Overview

```text
SharePoint (daily OIR .xlsx lands)
          |
          v
[Logic App / Power Automate]  ── file-created trigger
          |
          v
[Azure Function: IngestOIR]   ── parse, hash, upsert
          |
          +──> Dataverse: oir_demand              (current master)
          +──> Dataverse: oir_snapshot_history    (append-only)
          |
          v
[Azure Function: DetectExceptions]  ── scheduled 09:00 IST
          |
          +──> Rule 1: Stale Comments (>= 2 days)
          +──> Rule 2: DEM_End_Date within 2 days
          +──> Rule 3: Escalation ladder
          |
          v
[Foundry Agent: Digest Agent]  ── one message per person
          |
          v
[Teams Adaptive Card]  ──> PM / TM / EM
          |
          v
[Foundry Agent: Reply Interpreter]  ── free-text -> structured JSON
          |
          v
[Azure Function: ApplyUpdate]  ── validate, write, log
          |
          +──> Dataverse: oir_demand (updated)
          +──> Dataverse: oir_interaction_log (audit)
```

### Design principles

| Principle | Rationale |
|---|---|
| Deterministic work in code, never in an LLM | Parsing, hashing, date math, and diffing must be reproducible and cheap. |
| Excel is an input, not the system of record | Concurrent edits make Excel unusable as a source of truth. |
| Append-only snapshot history | Trend analysis and "stale for N days" require persisted history. |
| Content hashing, not timestamps | The file is regenerated daily, so every row *appears* modified. Only a hash of the actual text reveals real change. |
| One digest per person per day | A PM owning 30+ demands must receive one card, not thirty. |
| Confirm before mutating dates or status | Never let a model silently change `DEM_End_Date` or `Status`. |

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Ingestion trigger | Azure Logic App (or Power Automate) |
| Compute | Azure Functions (Python 3.11, isolated worker) |
| Data store | Microsoft Dataverse |
| Agents | Azure AI Foundry Agent Service |
| Model | GPT-4.1 or later, `temperature=0` for interpretation |
| Notification | Microsoft Teams via Bot Framework / Graph API |
| UI | Adaptive Cards v1.5 |
| Analytics | Power BI (DirectQuery over `oir_snapshot_history`) |
| Identity | Microsoft Entra ID |
| Secrets | Azure Key Vault |
| Observability | Application Insights |

---

## 4. Data Model

### 4.1 Table: `oir_demand` — current master

One row per demand. Upserted on every ingestion.

| Column | Type | Notes |
|---|---|---|
| `DemandID` | string (PK) | Unique demand key from source file |
| `Project` | string | e.g. Aurora Core, TDCA, QEP |
| `SLDU` | string | Service line delivery unit |
| `Role` | string | Role name |
| `Skill` | string | Essential skill |
| `Status` | choice | See §4.4 |
| `PM_Name` | string | |
| `PM_Email` | string | Resolved via Entra ID |
| `TM_Name` | string | |
| `TM_Email` | string | Resolved via Entra ID |
| `EM_Name` | string | |
| `EM_Email` | string | Resolved via Entra ID |
| `DEM_Start_Date` | date | |
| `DEM_End_Date` | date | Drives expiry rule |
| `Comments` | multiline string | Free text |
| `Remarks_Status` | string | Free text or choice |
| `Comments_Hash` | string(64) | SHA-256 — see §5.2 |
| `Last_Content_Change_Date` | date | Advances **only** when hash changes |
| `Stale_Days` | int (computed) | `TODAY - Last_Content_Change_Date` |
| `Last_Notified_On` | datetime | Spam suppression |
| `Escalation_Level` | int | 0 = none, 1 = PM/TM, 2 = +EM, 3 = +DM |
| `Snooze_Until` | datetime | Nullable |
| `Source_File` | string | Provenance |
| `First_Seen_Date` | date | |
| `Is_Active` | bool | False when absent from latest file |

**Indexes:** `DemandID` (unique), `PM_Email`, `TM_Email`, `Last_Content_Change_Date`, `DEM_End_Date`, `Is_Active`.

### 4.2 Table: `oir_snapshot_history` — append-only

One row per `DemandID` per ingestion date. **Never updated, never deleted.**

| Column | Type |
|---|---|
| `SnapshotID` | guid (PK) |
| `DemandID` | string |
| `Snapshot_Date` | date |
| `Status` | string |
| `Comments` | string |
| `Remarks_Status` | string |
| `Comments_Hash` | string(64) |
| `DEM_End_Date` | date |
| `PM_Email` | string |
| `TM_Email` | string |
| `Source_File` | string |
| `Ingested_At` | datetime |

**Unique constraint:** (`DemandID`, `Snapshot_Date`) — makes re-ingestion idempotent.

### 4.3 Table: `oir_interaction_log` — audit trail

| Column | Type | Notes |
|---|---|---|
| `InteractionID` | guid (PK) | |
| `DemandID` | string | |
| `Event_Type` | choice | `NOTIFIED`, `REPLIED`, `NO_CHANGE`, `SNOOZED`, `ESCALATED`, `AUTO_UPDATED`, `REJECTED` |
| `Recipient_Email` | string | |
| `Actor_Email` | string | Who acted |
| `Channel` | string | `TEAMS` |
| `Rule_Triggered` | string | `STALE_2D`, `EXPIRY_2D`, `ESCALATION_L2` |
| `Message_Sent` | string | Full card payload |
| `Reply_Raw` | string | Verbatim user reply |
| `Reply_Parsed` | json | Structured output |
| `Confidence` | decimal | From interpreter agent |
| `Field_Changed` | string | |
| `Value_Before` | string | |
| `Value_After` | string | |
| `Created_At` | datetime | |

### 4.4 Status vocabulary

Constrain `Remarks_Status` to this closed set (extend only via config, never free-form):

```
Need Profiles
L1 in Progress
Pending CI FB
Pending CI L2
Pending Offer
Pending Joiner
Joined
Project
To be deleted
```

---

## 5. Component: Ingestion

### 5.1 Trigger

Logic App, triggered on **file created** in the OIR SharePoint document library.

Validation before invoking the function:

1. Filename matches regex `^TD Bank OIR \d{2}-\d{2}-\d{4}\.xlsx$`
2. If multiple files exist for the same date, select the one with the **latest** `lastModifiedDateTime` and log the rejected duplicates.
3. Pass `{ fileUrl, fileName, fileDate, lastModifiedBy }` to the function.

### 5.2 Azure Function: `IngestOIR`

**Sheet resolution.** The target sheet is named `OR <date>` where the date varies daily. Resolve by prefix, never by exact string:

```python
def resolve_or_sheet(workbook):
    candidates = [s for s in workbook.sheetnames
                  if s.strip().upper().startswith("OR ")]
    if not candidates:
        raise IngestionError("No sheet matching 'OR <date>' found")
    if len(candidates) > 1:
        # pick the one whose trailing date is latest
        candidates.sort(key=extract_trailing_date, reverse=True)
    return workbook[candidates[0]]
```

**Header mapping.** Column positions drift between files. Map by normalised header text, not index:

```python
HEADER_ALIASES = {
    "demandid":        ["demand id", "demand_id", "demandid", "sr_id", "rls_id"],
    "project":         ["project", "project name"],
    "role":            ["role_name", "role name", "role"],
    "status":          ["status", "category"],
    "pm_name":         ["pm_name", "pm name", "sl_pm_name", "pm"],
    "tm_name":         ["tm_name", "tm name", "tm"],
    "em_name":         ["em_name", "em name", "em"],
    "dem_end_date":    ["dem_end_date", "dem end date", "demand end date"],
    "comments":        ["comments", "comment"],
    "remarks_status":  ["remarks status", "remarks_status", "remarks"],
}

def normalise(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).strip().lower()).strip()
```

Fail loudly if a required header cannot be resolved — do not silently default to null.

**Hashing.** This is the core of staleness detection:

```python
import hashlib

def content_hash(comments: str, remarks: str) -> str:
    def norm(v):
        return re.sub(r"\s+", " ", (v or "").strip().lower())
    payload = f"{norm(comments)}||{norm(remarks)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Normalisation matters: whitespace and casing changes must **not** count as a real update.

**Upsert logic:**

```python
for row in rows:
    new_hash = content_hash(row.comments, row.remarks_status)
    existing = dataverse.get_demand(row.demand_id)

    if existing is None:
        dataverse.insert_demand(
            **row,
            comments_hash=new_hash,
            last_content_change_date=file_date,
            first_seen_date=file_date,
            escalation_level=0,
            is_active=True,
        )
    else:
        changed = existing.comments_hash != new_hash
        dataverse.update_demand(
            row.demand_id,
            **row,
            comments_hash=new_hash,
            last_content_change_date=(
                file_date if changed else existing.last_content_change_date
            ),
            escalation_level=(0 if changed else existing.escalation_level),
            last_notified_on=(None if changed else existing.last_notified_on),
            is_active=True,
        )

    dataverse.insert_snapshot(row, new_hash, snapshot_date=file_date)

# demands absent from today's file are deactivated, not deleted
dataverse.deactivate_missing(file_date, seen_ids)
```

**Idempotency.** Re-running ingestion for the same `file_date` must produce an identical end state. Enforce via the unique constraint on (`DemandID`, `Snapshot_Date`) and upsert-on-conflict.

**Owner email resolution.** `PM_Name` / `TM_Name` / `EM_Name` arrive as display names. Resolve to UPNs via Microsoft Graph:

```
GET /v1.0/users?$filter=displayName eq '{name}'&$select=id,displayName,mail,userPrincipalName
```

Cache resolutions in a `oir_person_map` table. Unresolved names go to a PMO exception queue — **never guess an address**.

### 5.3 Failure handling

| Condition | Action |
|---|---|
| File missing by 09:30 IST | Alert PMO Teams channel; skip detection run |
| Sheet not found | Fail the run, alert, do not partially ingest |
| Required header missing | Fail the run, alert with the header list found |
| > 20% of rows fail validation | Abort, alert, roll back |
| Owner unresolved | Ingest row, flag `owner_unresolved`, route to PMO queue |

Use a transaction or staging table so a partial ingest never becomes the master state.

---

## 6. Component: Detection Rules

Azure Function `DetectExceptions`, timer-triggered daily at **09:00 IST**, gated on successful ingestion for the current date.

### Rule 1 — Stale Comments

```sql
SELECT * FROM oir_demand
WHERE Is_Active = 1
  AND DATEDIFF(day, Last_Content_Change_Date, @today) >= 2
  AND Status NOT IN ('Joined', 'To be deleted')
  AND (Snooze_Until IS NULL OR Snooze_Until < @now)
  AND (Last_Notified_On IS NULL OR CAST(Last_Notified_On AS date) < @today)
```

Recipients: **PM + TM**. Rule tag: `STALE_2D`.

### Rule 2 — Demand Expiring

```sql
SELECT * FROM oir_demand
WHERE Is_Active = 1
  AND DEM_End_Date BETWEEN @today AND DATEADD(day, 2, @today)
  AND Status <> 'Joined'
```

Recipients: **PM + TM + EM**. Rule tag: `EXPIRY_2D`.

Expiry alerts **ignore** `Snooze_Until` and `Last_Notified_On` — they are time-critical and must fire every day until resolved.

### Rule 3 — Escalation ladder

| Stale days | Level | Recipients | Tag |
|---|---|---|---|
| 2–3 | 1 | PM + TM | `STALE_2D` |
| 4–5 | 2 | PM + TM + EM | `ESCALATION_L2` |
| ≥ 6 | 3 | PM + TM + EM + DM, plus weekly exception report | `ESCALATION_L3` |

`Escalation_Level` resets to 0 whenever `Comments_Hash` changes.

### Output

A single JSON payload grouped by recipient:

```json
{
  "run_date": "2026-08-06",
  "recipients": [
    {
      "email": "pm.name@wipro.com",
      "display_name": "PM Name",
      "role": "PM",
      "expiring": [
        { "demand_id": "D1234", "project": "Aurora Core",
          "role": "Databricks Data Engineer",
          "dem_end_date": "2026-08-08", "days_left": 2,
          "status": "Need Profiles" }
      ],
      "stale": [
        { "demand_id": "D5678", "project": "TDCA",
          "role": "Mainframe Developer",
          "stale_days": 4, "status": "Pending CI FB",
          "escalation_level": 2 }
      ]
    }
  ]
}
```

---

## 7. Component: Foundry Agents

### 7.1 Agent A — Digest Agent

**Purpose:** convert one recipient's exception list into a single readable Teams message.

**Model settings:** `temperature = 0.2`, `max_tokens = 800`.

**System prompt:**

```
You are the OIR Update Assistant for the TD Bank delivery programme.

You receive a JSON object describing open demands owned by ONE person
that require their attention. Produce a single concise Microsoft Teams
message.

RULES
- Address the person by first name.
- List EXPIRING demands first, then STALE demands.
- Group by project. Sort stale demands by stale_days descending.
- State exactly what is needed: updated Comments and Remarks Status.
- Keep the message under 150 words. Use short lines, no long paragraphs.
- NEVER invent a DemandID, project, role, date, or status.
- NEVER state a fact that is not present in the input JSON.
- Do not apologise, do not add pleasantries beyond a one-line greeting.
- End with a single line telling them to use the cards below to respond.

OUTPUT: plain text only. No markdown headings. No JSON.
```

**Batching is mandatory.** Query the payload grouped by `recipient.email`. One agent invocation per person per day.

### 7.2 Agent B — Reply Interpretation Agent

**Purpose:** convert a free-text Teams reply into a structured update.

**Model settings:** `temperature = 0`, JSON mode / structured output enforced.

**System prompt:**

```
You convert free-text updates about staffing demands into structured JSON.

You are given:
- context: the current record (DemandID, Status, Remarks_Status,
  Comments, DEM_End_Date)
- reply: the user's free-text message

Return ONLY a JSON object matching the schema. Rules:

- Only include a field if the user's reply clearly and explicitly
  provides a new value for it. Omit everything else.
- Remarks_Status MUST be one of the allowed values listed in
  allowed_status. If the reply does not clearly map to one, omit it.
- Dates must be ISO 8601 (YYYY-MM-DD). Resolve relative dates such as
  "next Friday" using today_date from the context.
- confidence is your honest 0.0-1.0 certainty that the parse is correct.
- If the reply is ambiguous, contradictory, or refers to a demand not in
  context, set confidence below 0.5 and populate clarification_needed.
- NEVER guess a date. NEVER guess a status. Omission is always safer
  than a wrong value.
```

**Output schema:**

```json
{
  "type": "object",
  "properties": {
    "demand_id":       { "type": "string" },
    "comments":        { "type": "string" },
    "remarks_status":  { "type": "string" },
    "dem_end_date":    { "type": "string", "format": "date" },
    "no_change":       { "type": "boolean" },
    "confidence":      { "type": "number" },
    "clarification_needed": { "type": "string" }
  },
  "required": ["demand_id", "confidence"]
}
```

**Post-processing gate — implement in code, not in the prompt:**

```python
HIGH_RISK_FIELDS = {"dem_end_date", "remarks_status"}

def gate(parsed):
    if parsed["confidence"] < 0.85:
        return "CLARIFY"
    if HIGH_RISK_FIELDS & set(parsed.keys()):
        return "CONFIRM"        # send confirmation Adaptive Card
    return "APPLY"              # safe to write directly
```

### 7.3 Agent C — Trend / MI Agent

**Purpose:** answer leadership questions over `oir_snapshot_history`.

Grounded on the snapshot table via a read-only SQL tool. Restrict to `SELECT`. Never expose write access.

Representative questions to support:

- Which projects have the highest average stale days?
- Which PMs have the lowest reply rate to notifications?
- How has open-position count for Aurora Core trended over 30 days?
- Which demands have been open longest without a status change?
- What is the average time from `Need Profiles` to `Joined`?

### 7.4 Agent D — Orchestrator

Hub-and-spoke router. Classifies the inbound event and dispatches:

| Intent | Route |
|---|---|
| Scheduled exception run | Digest Agent |
| Free-text Teams reply | Reply Interpretation Agent |
| Analytical question | Trend / MI Agent |
| Unrecognised | Fallback help message |

---

## 8. Component: Teams Interaction

### 8.1 Message structure

Per recipient per day: **one** narrative message from the Digest Agent, followed by **one Adaptive Card per demand** (cap at 10 cards; beyond that, link to a Power App list view).

### 8.2 Adaptive Card schema

```json
{
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    {
      "type": "TextBlock",
      "text": "${project} — ${demandId}",
      "weight": "Bolder",
      "size": "Medium",
      "wrap": true
    },
    {
      "type": "FactSet",
      "facts": [
        { "title": "Role",         "value": "${role}" },
        { "title": "Status",       "value": "${status}" },
        { "title": "End Date",     "value": "${demEndDate}" },
        { "title": "Last Updated", "value": "${lastContentChangeDate}" }
      ]
    },
    {
      "type": "TextBlock",
      "text": "${alertText}",
      "color": "Attention",
      "wrap": true,
      "$when": "${alertText != ''}"
    },
    {
      "type": "Input.ChoiceSet",
      "id": "remarks_status",
      "label": "Remarks Status",
      "value": "${status}",
      "choices": [
        { "title": "Need Profiles",  "value": "Need Profiles" },
        { "title": "L1 in Progress", "value": "L1 in Progress" },
        { "title": "Pending CI FB",  "value": "Pending CI FB" },
        { "title": "Pending CI L2",  "value": "Pending CI L2" },
        { "title": "Pending Offer",  "value": "Pending Offer" },
        { "title": "Pending Joiner", "value": "Pending Joiner" },
        { "title": "Joined",         "value": "Joined" },
        { "title": "To be deleted",  "value": "To be deleted" }
      ]
    },
    {
      "type": "Input.Text",
      "id": "comments",
      "label": "Comments",
      "isMultiline": true,
      "placeholder": "What changed since the last update?"
    },
    {
      "type": "Input.Date",
      "id": "dem_end_date",
      "label": "Revised End Date (optional)"
    }
  ],
  "actions": [
    { "type": "Action.Submit", "title": "Submit Update",
      "data": { "action": "SUBMIT", "demandId": "${demandId}" } },
    { "type": "Action.Submit", "title": "No Change",
      "data": { "action": "NO_CHANGE", "demandId": "${demandId}" } },
    { "type": "Action.Submit", "title": "Snooze 24h",
      "data": { "action": "SNOOZE", "demandId": "${demandId}" } }
  ]
}
```

### 8.3 The "No Change" action

This is the highest-value control in the system. It converts silence into an explicit, timestamped signal.

On click:
- Write `NO_CHANGE` to `oir_interaction_log`
- Set `Last_Notified_On = now`
- **Do not** advance `Last_Content_Change_Date` — the demand stays stale and keeps escalating
- Reply in-thread: `"Recorded — no change on D1234. This demand remains flagged."`

This distinguishes *genuinely blocked* from *ignoring the bot*, which is the metric that makes the platform credible to leadership.

### 8.4 Free-text replies

Users will reply conversationally regardless of the cards. Handle it:

1. Bot receives message activity.
2. Resolve `DemandID` from the reply text, or from the thread's parent card context.
3. Invoke Reply Interpretation Agent.
4. Apply the gate from §7.2.
5. `CONFIRM` → send a confirmation card showing before/after; apply only on click.
6. `CLARIFY` → ask one specific question; never guess.

### 8.5 Update application

Azure Function `ApplyUpdate`:

```python
def apply_update(demand_id, parsed, actor_email):
    before = dataverse.get_demand(demand_id)
    validate_status(parsed.get("remarks_status"))
    validate_date_not_in_past(parsed.get("dem_end_date"))

    new_hash = content_hash(
        parsed.get("comments", before.comments),
        parsed.get("remarks_status", before.remarks_status),
    )

    dataverse.update_demand(
        demand_id,
        **parsed,
        comments_hash=new_hash,
        last_content_change_date=today(),
        escalation_level=0,
        last_notified_on=None,
        snooze_until=None,
    )

    for field, new_value in parsed.items():
        log_interaction(
            demand_id, "REPLIED", actor_email,
            field_changed=field,
            value_before=getattr(before, field, None),
            value_after=new_value,
        )
```

**Authorisation:** reject the update if `actor_email` is not the `PM_Email`, `TM_Email`, `EM_Email`, or a member of the PMO security group. Log the rejection as `REJECTED`.

---

## 9. Component: Excel Reconciliation

Nightly job at 22:00 IST regenerates `OIR_Master_Updated.xlsx` from Dataverse into SharePoint.

- Preserves the original column layout so PMO tooling continues to work.
- Adds four columns: `Last_Content_Change_Date`, `Stale_Days`, `Escalation_Level`, `Last_Updated_By`.
- Writes to a **separate folder** from the ingestion folder to prevent a feedback loop.

This makes Excel a rendered output rather than an edited source, giving a clean cut-over path.

---

## 10. Guardrails

| Risk | Control |
|---|---|
| Concurrent duplicate files | Ingest only the latest by `lastModifiedDateTime`; log rejects |
| Notification fatigue | One digest per person per day; 24h snooze; expiry alerts exempt |
| False-positive staleness | Shadow mode for 5 business days before enabling notifications |
| Wrong owner email | Entra ID resolution + cached person map; unresolved → PMO queue |
| Model hallucinating an update | Confidence gate + confirmation card for date/status changes |
| Unauthorised update | Actor must be PM/TM/EM on the record or PMO group member |
| Silent pipeline failure | Alert PMO channel if ingestion has not succeeded by 09:30 IST |
| Data loss | `oir_snapshot_history` is append-only; no hard deletes anywhere |
| Prompt injection via Comments field | Treat all file content as untrusted data; never place it in a system prompt |

---

## 11. Observability

Emit to Application Insights on every run:

| Metric | Purpose |
|---|---|
| `ingest.rows_processed` | Volume |
| `ingest.rows_changed` | Real update rate |
| `ingest.duration_ms` | Performance |
| `ingest.owner_unresolved_count` | Data quality |
| `detect.stale_count` | Exception volume |
| `detect.expiring_count` | Exception volume |
| `notify.sent_count` | Delivery |
| `notify.reply_count` | Engagement |
| `notify.no_change_count` | Genuine blockage signal |
| `agent.interpret.confidence_avg` | Model health |
| `agent.interpret.clarify_rate` | Prompt quality |

**Primary success metric:** `reply_count / sent_count` (target ≥ 70% within 24h).

---

## 12. Build Sequence

### Sprint 1 — Foundation (no notifications)

- [ ] Provision Dataverse environment, create the three tables
- [ ] Build `IngestOIR` with sheet resolution, header mapping, hashing
- [ ] Implement idempotent upsert and snapshot append
- [ ] Build Entra ID person resolution with caching
- [ ] Build `DetectExceptions` with Rules 1 and 2
- [ ] **Shadow mode:** email the exception list to the programme owner only
- [ ] Validate the stale list manually against 5 consecutive real files

> **Do not proceed to Sprint 2 until shadow-mode output is verified correct.** If the hashing logic is wrong, the first live run notifies 250+ people with false positives and adoption is lost permanently.

### Sprint 2 — Notify and capture

- [ ] Register the Teams bot; deploy the messaging endpoint
- [ ] Build the Digest Agent in Foundry
- [ ] Build the Adaptive Card renderer with Submit / No Change / Snooze
- [ ] Build `ApplyUpdate` with authorisation and audit logging
- [ ] Pilot with 2–3 PMs on a single high-volume project

### Sprint 3 — Autonomy and MI

- [ ] Implement Rule 3 escalation ladder
- [ ] Build the Reply Interpretation Agent with the confidence gate
- [ ] Build the Trend / MI Agent over snapshot history
- [ ] Build the Power BI model and executive dashboard
- [ ] Enable nightly Excel regeneration
- [ ] Roll out to all projects

---

## 13. Repository Structure

```text
oir-agent-platform/
├── README.md
├── infra/
│   ├── main.bicep
│   ├── dataverse-schema.json
│   └── keyvault.bicep
├── functions/
│   ├── ingest_oir/
│   │   ├── __init__.py
│   │   ├── parser.py
│   │   ├── hashing.py
│   │   ├── header_map.py
│   │   └── dataverse_client.py
│   ├── detect_exceptions/
│   │   ├── __init__.py
│   │   └── rules.py
│   ├── apply_update/
│   │   ├── __init__.py
│   │   └── authz.py
│   └── shared/
│       ├── graph_client.py
│       ├── models.py
│       └── telemetry.py
├── agents/
│   ├── digest_agent.yaml
│   ├── reply_interpreter.yaml
│   ├── trend_agent.yaml
│   └── orchestrator.yaml
├── cards/
│   ├── demand_update.json
│   ├── confirm_change.json
│   └── digest_header.json
├── bot/
│   ├── app.py
│   └── activity_handler.py
├── logicapps/
│   └── file-trigger.json
├── tests/
│   ├── test_hashing.py
│   ├── test_parser.py
│   ├── test_rules.py
│   ├── test_idempotency.py
│   └── fixtures/
└── docs/
    └── runbook.md
```

---

## 14. Test Requirements

Minimum coverage before Sprint 2:

| Test | Assertion |
|---|---|
| `test_hash_stable` | Whitespace and case changes produce the same hash |
| `test_hash_sensitive` | Any real text change produces a different hash |
| `test_staleness_no_change` | Ingesting an identical file twice does **not** advance `Last_Content_Change_Date` |
| `test_staleness_on_change` | A changed comment **does** advance the date and reset escalation |
| `test_idempotent_ingest` | Re-running the same file yields an identical database state |
| `test_sheet_resolution` | `OR 04-08-2026` resolves correctly among many sheets |
| `test_header_drift` | Reordered or renamed columns still map correctly |
| `test_missing_header` | Missing required header raises, does not silently null |
| `test_expiry_boundary` | `DEM_End_Date` exactly `TODAY+2` triggers; `TODAY+3` does not |
| `test_authz_reject` | A non-owner update is rejected and logged |
| `test_confidence_gate` | Confidence 0.80 routes to `CLARIFY`, not `APPLY` |
| `test_date_change_confirms` | Any `dem_end_date` change routes to `CONFIRM` |

---

## 15. Configuration

All thresholds must be configuration-driven, never hard-coded.

```json
{
  "staleness": {
    "threshold_days": 2,
    "escalation_l2_days": 4,
    "escalation_l3_days": 6,
    "excluded_statuses": ["Joined", "To be deleted"]
  },
  "expiry": {
    "lookahead_days": 2,
    "ignore_snooze": true
  },
  "notification": {
    "max_cards_per_message": 10,
    "snooze_hours": 24,
    "digest_time_ist": "09:00",
    "ingest_deadline_ist": "09:30"
  },
  "agent": {
    "confidence_threshold": 0.85,
    "high_risk_fields": ["dem_end_date", "remarks_status"]
  },
  "reconciliation": {
    "enabled": true,
    "run_time_ist": "22:00"
  }
}
```
