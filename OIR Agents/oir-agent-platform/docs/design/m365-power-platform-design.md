# OIR platform on Microsoft 365: design plan

Status: **Proposed**, 2026-10-08. Nothing here is built. Phase 0 (section 11) is a
set of feasibility spikes that must pass before any of it is.
Decision record: `docs/decisions/0011-move-to-m365-power-platform.md`.

## 1. Intent

The goals do not change: ingest the daily OIR file, find demands that are stale or
about to expire, nudge their owners, escalate anything stale for 5 business days to
the Account Delivery Head (Sharad Jain), accept updates, keep an audit trail, and
stay in a safe redirect mode until told otherwise.

What changes is where it runs. The Azure stack (Functions, Cosmos, Foundry, ACS)
sits in a sandbox tenant with no Teams, no SharePoint licence, and none of the
recipients as directory users (ADR 0002, 0010). The new stack runs in the Wipro
tenant, where Teams, SharePoint and the recipients' identities already exist.

| Concern | Was | Becomes |
|---|---|---|
| State | Cosmos (4 containers) | SharePoint lists |
| Compute | Python Functions | Power Automate flows + Office Scripts |
| Delivery | Email via ACS | Teams adaptive cards (1:1 chat) |
| Updates | ApplyUpdate / web form | Card submit and Copilot Studio agent |
| Reply parsing | Foundry reply-interpreter | Copilot Studio slot-filling + confirmation |
| Digest wording | Foundry digest-agent | Deterministic card (no model) |
| Identity | None (token links) | Teams SSO, real user identity |

Business rules carry over unchanged: 2 / 4 / 5 business-day ladder (all
configurable), recipients PM+TM -> +EM -> +ADH, cap of 10 items per message with the
true total shown, snooze, fail-closed delivery routing.

## 2. Assumptions to verify (the Phase 0 gate)

I have no authenticated session in the Wipro tenant, so none of these is confirmed.
Each has a 15-30 minute spike. The Teams check earlier paid for itself by stopping a
bot that could never have delivered; this is the same discipline.

| # | Assumption | Spike | If false |
|---|---|---|---|
| A1 | You can create a SharePoint site (easiest: create a Team, which makes one) | Create it | Request a site from the SharePoint admin |
| A2 | Power Automate in your licence covers **standard connectors only** (SharePoint, Teams, Excel Online, Office 365). The HTTP connector is premium, so the design avoids it | Check licence in the maker portal | Needs premium licence, or drop the one place it is used (none planned) |
| A3 | The "Workflows"/Power Automate app is allowed in Teams, so a flow can DM a user | Flow: post a card to yourself | Teams admin must allow it; fallback is a channel with @mentions |
| A4 | "Post adaptive card and wait for a response" works for your own chat and returns the submitter's email | Same flow, submit it | Cards become read-only links; updates go through the agent only |
| A5 | Office Scripts are enabled, and **Run script** works from a flow with JSON in and out | Script that echoes a 200-row JSON parameter | Engine moves to flow expressions (much weaker, see risk R3) |
| A6 | A Copilot Studio agent can be built and published to Teams, with SSO exposing the user's email | Hello-world agent returning `User.Email` | Agent is dropped; cards carry everything |
| A7 | A flow can be owned by a service account, or co-owned | Ask | Key-person risk: flows die when the maker leaves (R4) |
| A8 | DLP policy allows SharePoint + Teams + Excel in one flow | Run spike flow | DLP exception request |
| A9 | **Where does the daily OIR file come from?** (mailbox, shared folder, manual save) | Ask | Ingest trigger is undefined until answered |

## 3. Architecture

```
 Upstream OIR report --(daily .xlsx)--> [SharePoint library: OIR Inbox]
                                               | file created
                                               v
                  F1 Ingest (Power Automate)
                    read Demands + Owners + StatusMap + Config
                    Script A  parseOir      (bound to the new file)  -> rows
                    Script B  engine.plan   (bound to OIR-Engine.xlsx) -> creates / updates / deactivations
                    apply only the deltas; write RunLog
                                               |
                                               v
      [SharePoint lists: Demands, Owners, StatusMap, PlatformConfig, InteractionLog, RunLog]
                    ^                                         ^
                    | read                                    | write (authorised per demand)
  F2 Detect & notify  (weekdays 09:00 IST)                F4 ApplyDemandUpdate  (shared child flow)
    gate on RunLog.Ingest = Succeeded today                  ^                 ^
    Script B  engine.detect -> per-recipient payload + card  |                 |
    loop -> F3 DeliverDigest (child, one per recipient)      |                 |
              ResolveDelivery (Off/Shadow/Redirect/Live)     |                 |
              post card, wait for response ------------------+                 |
                    |                                                          |
                    v                                                          |
        Teams 1:1 chat: adaptive card                    Teams: "OIR Assistant" (Copilot Studio, SSO)
                                                          tools -> agent flows -> F4
```

Design principle: **one implementation of each rule.** The card path and the agent
path both call F4. Ingest and detect both call the same engine script. This is the
same reasoning that produced `apply_update/core.py`, and for the same reason: two
copies of "what an update means" drift.

## 4. Data model

### 4.1 Mapping from Cosmos

| Cosmos container | SharePoint | Notes |
|---|---|---|
| Demands | **Demands** list | Current state only |
| SnapshotHistory | *not replicated* | The archived daily `.xlsx` files are the history. 3,104 rows in 18 days would hit the 5,000-item view threshold within weeks for no benefit |
| InteractionLog | **InteractionLog** list | Archived monthly |
| PersonMap | **Owners** list | Seeded from `infra/person-map-seed.csv` (39 people, 43 with variants) |
| config.json + env vars | **PlatformConfig** list | Editable without a deployment |
| (new) | **StatusMap**, **RunLog** | See below |

### 4.2 Demands

| Column | Type | Notes |
|---|---|---|
| Title | Text, **indexed, unique** | DemandID (`SR_ID_2`, per position, not `RLS_ID`) |
| RequisitionID, Project, Role, Skill, SLDU | Text | Project indexed |
| IFPStatus | Text | System-driven (`04. Pending Profile - IFP`); read-only to owners |
| FileComments, FileRemarksRaw | Text / multiline | Last values seen in the OIR file |
| FileRemarks | Choice | Normalised via StatusMap; `Unmapped` if unknown, never dropped |
| OwnerComments, OwnerRemarks, OwnerEditedAt | Text / Choice / DateTime | **Overlay** written by cards and the agent. Ingest never overwrites these (see 4.3) |
| DEMEndDate | Date | |
| PMName/PMEmail, TMName/TMEmail, EMName/EMEmail | Text | Email resolved from Owners at ingest; kept as text, not Person columns, so ingest does not depend on directory resolution |
| LastContentChangeDate | Date, indexed | `max(last file change, OwnerEditedAt)`; the staleness clock |
| FirstSeenDate, LastSeenDate | Date | |
| IsActive | Yes/No, indexed | |
| EscalationLevel | Number | Highest tier notified since last change |
| LastNotifiedOn, SnoozeUntil | DateTime | |
| SourceFile | Text | |

**Not stored: stale days and content hash.** Stale days change for every unchanged
demand every day, so storing them forces ~200 writes daily. They are computed by the
engine at detect time. The Cosmos hash was an optimisation; ingest now compares the
normalised `(comments, remarks)` pair directly, which also removes any hash-parity
concern with the Python oracle.

### 4.3 The two-writer problem (decision D1)

Today's design has two sources of truth for Comments and Remarks: the upstream OIR
file and, once cards or the agent write, SharePoint. If ingest naively takes the file
as authoritative, tomorrow's file overwrites the owner's update and the stale clock
resets for the wrong reason.

Default design, safe under either answer to D1: keep `File*` and `Owner*` apart.
Effective value = the owner overlay if `OwnerEditedAt` is newer than the last file
change, else the file value. When a later file catches up and matches the overlay,
the overlay clears. The staleness clock is the later of the two edit times.

What this does **not** answer is whether the upstream report ever sees owner updates.
Until someone writes back or the process changes, people reading the upstream OIR see
stale content while the platform shows fresh. That is a business question, not a
technical one, and it is D1 below. (The unimplemented `reconciliation` block in
`config.json` was a placeholder for it.)

### 4.4 Remarks vocabulary (StatusMap)

`Remark` is free text. Latest file, 19 spellings of 12 real values; 30 spellings
across all 14 files. The code's 9-value `VALID_STATUSES` would reject `Pending CI`
alone (35 demands), `L2 in Progress`, `L1 Reject`, `Pending L1` and
`Pending Allocation`: a latent bug in the current `apply_update`.

| Canonical | Raw variants seen (latest file) | Demands | NudgeOwner | Terminal |
|---|---|---|---|---|
| Need Profiles | Need Profiles, Need profiles | 80 | yes | no |
| L1 in Progress | | 44 | yes | no |
| Pending CI | Pending CI, pending CI, Pending Ci, `Pending CI, awaiting customer confirmation...` | 41 | **decide** | no |
| Pending Offer | Pending Offer, Pending offer | 16 | yes | no |
| Pending Joiner | Pending Joiner, Pending joiner | 14 | yes | no |
| To be deleted | To be Deleted, To be deleted | 5 | no | **yes** |
| Pending CI FB | | 2 | yes | no |
| L2 in Progress | | 2 | yes | no |
| Deleted | | 2 | no | **yes** |
| L1 Reject, Pending L1, Pending Allocation | | 1 each | yes | no |

`NudgeOwner` is the knob for the volume problem (106 of 209 demands stale, 61
escalating to the ADH on day one). Statuses waiting on the customer or TA can be set
to not nudge, by editing a list row. The long `Pending CI, awaiting customer...`
variant keeps its tail in `FileRemarksRaw`.

### 4.5 Other lists

- **Owners**: DisplayName (Title), Email, Aliases (multiline), Role (PM/TM/EM/PMO), IsActive.
- **PlatformConfig** (key/value): `Mode`, `RedirectTo`, `AllowRealRecipients`,
  `PilotAllowList`, `StaleThresholdDays`=2, `L2Days`=4, `ADHDays`=5,
  `UseBusinessDays`, `MaxItemsPerCard`=10, `SnoozeHours`=24, `ADHEmail`, `PMOEmails`,
  `IngestDeadlineIST`, `Holidays`.
- **InteractionLog**: Title (guid), DemandID (indexed), EventType (GENERATED,
  NOT_SENT, NOTIFIED, SUBMITTED, FIELD_UPDATED, NO_CHANGE, SNOOZED, REJECTED,
  EXPIRED), RecipientEmail, ActorEmail, Channel (CARD/AGENT/SHADOW), RuleTriggered,
  MessageSent (text rendering, not the 28 KB card JSON), FieldChanged,
  ValueBefore/After, Delivery (actual-to + reason), RunId (indexed).
  Version history on; monthly archive-and-prune flow.
- **RunLog**: RunId, Kind (INGEST/DETECT), AsOfDate, Status, counts (processed,
  changed, errored, unmapped), Message. F2 reads this as its ingestion gate.
- **Libraries**: *OIR Inbox* (trigger), *OIR Archive* (processed files = snapshot
  history), plus `OIR-Engine.xlsx` (the host workbook for Script B).

Scale: Demands is ~350 rows including inactive. InteractionLog will pass 5,000 in
roughly 2-3 months, hence the indexes and the archive flow.

## 5. Flows

### Engine (Office Scripts, TypeScript)

Power Automate is poor at 200-row diffing, business-day arithmetic and building JSON.
So the logic lives in two scripts and the flows only move data and apply deltas.

- **Script A `parseOir`** (bound to the incoming file): picks the data sheet by the
  rules in `parser.resolve_or_sheet`, applies the alias map from `header_map.py`,
  returns canonical rows. Required because the sheet is not an Excel table, so
  "List rows present in a table" cannot read it.
- **Script B `engine`** (bound to `OIR-Engine.xlsx`, inputs passed as JSON):
  - `plan(rows, existing, owners, statusMap, config, asOf)` returns creates, updates
    (with SharePoint IDs), deactivations, stats, and the list of unmapped values.
  - `detect(existing, config, statusMap, asOf)` returns the same recipient payload as
    Python `run_rules`, **plus the rendered card JSON per recipient**, so no flow has
    to assemble JSON by hand and card size can be asserted in a test.

Writes per day are the changed rows only (observed 9-117), not the portfolio.

### F1 Ingest
Trigger: file created in *OIR Inbox*. Gets Demands/Owners/StatusMap/Config, runs A
then B, applies the plan, moves the file to *OIR Archive*, writes RunLog. Aborts if
the error rate passes 20% (existing rule). Idempotent: same file twice, no change.

### F2 Detect and notify
Weekdays 09:00 IST. Gate: RunLog has an INGEST row for today with Status=Succeeded;
else message the PMO and stop. Runs `detect`, loops recipients (concurrency ~5),
calling F3 per recipient. Nothing in F2 touches Demands.

### F3 DeliverDigest (child, one per recipient)
`ResolveDelivery` -> post the card -> **wait for the response** (24 h timeout;
unanswered cards are updated to "expired" and logged) -> for each submitted demand,
call F4 -> log. One child per recipient so a slow responder never blocks the others.

### F4 ApplyDemandUpdate (shared child; the single write path)
Inputs: demandId, actorEmail, comments?, remarks?, endDate?, channel.
1. Load the item. 2. **Authorise**: actor is the PM, TM or EM of that demand, or in
`PMOEmails`. 3. Validate: remarks in StatusMap canonical list, end date not in the
past. 4. No-op if nothing differs (re-saving unchanged text must not reset the
clock). 5. Write the `Owner*` overlay, `LastContentChangeDate`=today,
`EscalationLevel`=0, clear `LastNotifiedOn`/`SnoozeUntil`. 6. Log every field.

### ResolveDelivery (one place, fail closed)
A single `Mode` replaces the three booleans in `notifier.py`, which makes the table
smaller to reason about.

| Mode | Result |
|---|---|
| Off, blank, or any unrecognised value | Nothing sent, logged `NOT_SENT` |
| Shadow | Nothing sent; the rendered card is logged for review |
| Redirect | Goes to `RedirectTo` with a banner naming the intended owner; if `RedirectTo` is blank, **not sent** |
| Live | Real recipient only if `AllowRealRecipients=true` **and** (`PilotAllowList` is empty or contains them); otherwise not sent |

Only `Live` + `AllowRealRecipients` reaches a real person, and an empty allow-list is
the only configuration where that means everyone. The Python harness enumerated all
8 gate combinations; flows cannot be unit-tested, so a **self-test flow** runs the
same matrix against synthetic config rows and asserts the outcomes.

### F5 Housekeeping
Expire stale cards, archive/prune InteractionLog monthly. Optional later: a holiday
list so Indian public holidays do not count as business days.

## 6. Adaptive cards

Target schema 1.4 (safest through the Flow bot). Posted by flow, so the sender shows
as the Workflows app, not the agent. Each card carries an "Ask the OIR Assistant"
link to keep the experience coherent.

- **Owner card**: banner (tier, redirect notice if any), then up to 10 demand blocks:
  project / role / id, IFP status, "N business days without an update",
  `Input.Text` for comments (prefilled with the effective value) and
  `Input.ChoiceSet` for remarks (canonical list). Footer: "Showing the 10 most overdue
  of N" (never imply completeness). Actions: **Submit**, **Snooze 24h**, **Nothing
  changed**. Blank fields mean "leave alone".
- **ADH card**: read-only and **rolled up**, not demand-by-demand: counts by owner and
  project, the 10 oldest, and a link to a filtered Demands view. 61 individual lines
  to one executive was never a usable shape.
- **Limits to design around**: Teams card payload ~28 KB (10 blocks with a 12-value
  choice list repeated per block is the stress case; the engine asserts size in a
  test); one waiting run per card; 30-day run ceiling (we use 24 h).

## 7. Copilot Studio agent: "OIR Assistant"

Teams only, **sign-in required** (SSO).

- **Tools** (agent flows): `GetMyDemands`, `GetDemandDetail`, `UpdateDemand` (calls
  F4), `SnoozeDemand`, and a PMO-only `PortfolioSummary`.
- **Identity is bound, not asked.** `actorEmail` is filled from the authenticated user
  variable, never from a model-supplied input. Otherwise anyone could ask the agent to
  act as someone else. F4 authorises independently regardless.
- **Writes live in explicit topics**, not free generative orchestration. Every write,
  comments included, ends with a confirmation card (current value -> new value). This
  replaces the reply-interpreter's confidence threshold with something strictly
  safer: a human confirms every change. The old rules carry over as topic
  instructions: omit rather than guess, resolve relative dates against today, status
  from the closed list only.
- **Read/Q&A** ("what's pending for me", "why was I notified", escalation FAQ) can use
  generative orchestration, with the runbook as knowledge.
- **Treat Comments as untrusted data.** They are free text typed into a spreadsheet and
  will reach the model; the agent is instructed never to act on instructions inside
  them.
- Cannot message proactively. That is the flows' job.

## 8. Security and governance

- Site membership: PMO group and the service account only. Owners never need site
  access; they use cards and the agent.
- Flow connections should belong to a **service account** (A7). Per-user connections
  die with the person and also run with that person's permissions, which is why F4
  does its own authorisation instead of trusting SharePoint permissions.
- Redirect/shadow carried over, fail closed, with the pilot allow-list as an extra step
  before everyone.
- InteractionLog holds names and emails: retention policy, 90 days online then
  archived.
- DLP and environment: build in a dedicated environment/solution if available so
  dev -> prod promotion is possible (A8).

## 9. What happens to the existing assets

| Asset | Disposition |
|---|---|
| `functions/` Python (rules, ingestion, parser, header_map, core) and its ~230 committed tests | **Kept as the specification and test oracle**, moved under `reference/` once the engine passes parity |
| Cosmos `OIRPlatform` database, Function App + plan, ACS email, Key Vault, App Insights, Log Analytics, storage, `snet-oir-func`, the four OIR Foundry agents, `sp-oir-dev` | **Removed 2026-10-08 at the owner's direction**, ahead of the pilot, so there is **no live rollback**. Rebuilding is possible but not free: `infra/main.bicep` re-provisions the resources and `infra/backfill_history.py` replays the 14 committed files into Cosmos. Anything shared was left alone (see ADR 0011) |
| `digest-agent` | **Retired.** A card renders structured data directly. The model added wording only, caused 10-50% refusals, and needed a retry and refusal guard. Removing it removes that whole failure class |
| `reply-interpreter` | Superseded by Copilot Studio slot-filling + confirmation |
| `trend-agent`, `orchestrator` | Parked, not part of this scope |
| `update_form/`, `action_token.py`, `apply_update/core.py`, digest-link code and their tests | Superseded (cards give a real identity, which the token links existed to fake). **Done 2026-10-08:** archived on branch `archive/web-form-token-links` (`cf823e0`) and removed from `experiments` |
| Deployed leftovers from that work | Gone with the Function App and the `OIRPlatform` database (the `ActionTokens` container held 0 rows) |

Correction to my own earlier work: the form's results page says changes "appear in
tomorrow's OIR file". I never verified that, and it is the same open question as D1.
That string is in code I am proposing to archive.

## 10. Testing

Flows cannot be unit-tested, so the approach is to push as much logic as possible
into the scripts and prove them against the Python that already runs on real data.

- **Parity oracle**: replay the 14 real files through Python, record per day the
  active set, changed flags, `LastContentChangeDate`, business-day staleness at the
  as-of date, and the detect payload. The TypeScript engine must reproduce all of it.
  Compare *decisions*, not hash values.
- **Engine unit tests** run locally under Node. Node is not currently available on
  this machine (`node`, `npm`, `pac` all absent), and earlier installs were blocked by
  proxy TLS. `pip` worked before, so a pip-distributed Node is the likely route;
  untested.
- **Card tests**: size under 28 KB at 10 blocks, structure valid, "N of M" present
  whenever truncated.
- **Flows**: scripted manual checklist per flow, run history as evidence, and the
  ResolveDelivery self-test flow.
- **Shadow first**: every phase runs in `Shadow` or `Redirect` before anything is live.

## 11. Phases

| Phase | Scope | Exit criteria | Rough size |
|---|---|---|---|
| **0 Feasibility** | A1-A9 spikes in the Wipro tenant | All pass, or each failure has an agreed fallback. **Go/no-go** | 0.5-1 day |
| **1 Data + ingest** | Site, lists, seeds, engine `parseOir`+`plan`, F1, backfill the 14 files | Engine matches the Python oracle on all 14 days; 209 active demands; unmapped statuses counted | 3-4 days |
| **2 Detect + cards, Redirect** | `detect`, F2/F3/F4, owner + ADH cards, ResolveDelivery | All digests land with you, addressed to real owners; card size test green; tiers match the Python run | 2-3 days |
| **3 Agent** | Read-only tools, then writes with confirmation | Authorisation tests: a user cannot read or write another's demand | 3-4 days |
| **4 Pilot** | `Live` + allow-list of 2-3 owners, then everyone, then ADH rollup | Real submissions land in the list; no unintended recipient | 2 days + soak |

Sizes are rough and assume Phase 0 passes.

**Who does what.** I can write and test in this repo: the engine scripts and parity
oracle, card JSON and size tests, the list schemas, seed CSVs, StatusMap and Config
rows, topic YAML, and exact flow specs with expressions. I **cannot** click through
Power Automate, SharePoint or Copilot Studio from here: my CLI sessions are
authenticated to the sandbox tenant, and the Dataverse call from this machine was
already blocked by Conditional Access. So the build steps are done by you in the
browser, from my specs. The Claude-in-Chrome tools could drive those portals in your
signed-in browser; that is possible but a bigger trust decision, and I would only
do it with your explicit go-ahead, one phase at a time.

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Two writers** to Comments/Remarks (4.3, D1) | Overlay design; business decision on write-back |
| R2 | Premium licensing creeps in (HTTP connector, Copilot Studio capacity) | Standard connectors only; A2/A6 spikes; cost check before Phase 3 |
| R3 | Logic in flows is untestable and rots | Logic lives in scripts under test; flows only move data |
| R4 | Flows owned by an individual stop working or run with their rights | Service account or co-owners (A7); F4 authorises independently |
| R5 | Card limits: 28 KB, one waiting run per card, `Action.Submit` only | 10-block cap, size test, 24 h timeout |
| R6 | 5,000-item view threshold on InteractionLog | Indexes + monthly archive |
| R7 | Notifications come from "Workflows", not the agent | Deep link from card to agent; set expectations |
| R8 | Status drift continues because the source is free text | StatusMap + Unmapped counter in RunLog; owner edits go through a closed list |
| R9 | Office Script parameter/return size or runtime limits | A5 spike with a 200-row payload; fall back to batching |
| R10 | Business-day count ignores Indian public holidays | `Holidays` config; F5 later |

## 13. Decisions needed from you

1. **D1, source of truth.** After an owner updates Comments/Remarks through a card or
   the agent, does anything write that back to the upstream OIR report? If not, is
   the platform meant to become where these two fields are maintained? This decides
   whether staleness is measured on the file, the platform, or both, and it is the
   one question that can invalidate the design.
2. **D2, environment.** Which Wipro tenant site/environment, who owns the flows, and
   can a service account be provided?
3. **D3, vocabulary and volume.** Confirm the 12 canonical remarks values, and for
   each whether `NudgeOwner` is yes or no (especially `Pending CI`, 41 demands).
4. **D4, source file.** Where does the daily OIR file land (A9)?
