# ADR 0008: Source owner emails from the OIR file, not Microsoft Graph

**Status:** Accepted (blocked on an upstream file change)
**Date:** 2026-08-12
**Owner:** Kshitij Sathe
**Follows:** [ADR 0007](0007-single-permission-request-no-secrets.md)

## Context

ADR 0007 reduced the outstanding work to a single privileged request:
admin-consented Graph application permissions (`User.Read.All`,
`GroupMember.Read.All`) so the platform could resolve owner *names* from
the OIR file into *email addresses*. Given this tenant's track record —
Conditional Access blocking Dataverse, no SharePoint licence, no
`Microsoft.Authorization` rights, a PII content filter — that request was
judged unlikely to land quickly, and ADR 0007 recorded a fallback: take
the emails from the OIR file itself and drop the Graph dependency.

This ADR implements that fallback. Doing so required reading the real
files properly for the first time, which surfaced four defects that made
ingestion non-functional regardless of the email question.

## What the real files actually contain

Verified against all four samples in `Data/`:

1. **The parser could not open any of them.** `resolve_or_sheet()` looked
   for a sheet starting `"OR "`; the real sheets are named `OIR 6th Aug `
   (`OIR`, a written-out date, trailing space). Every file raised
   `IngestionError: No sheet matching 'OR <date>'`. The workbooks also
   contain `OIR Pivot` and `AXNB Pivot` tabs that must not be selected.
2. **`role` is a required column and does not exist.** Header mapping
   aborted on it. The closest real equivalent is `ESSENTIAL_SKILL`
   ("Actimize", "AI assisted code generation"), which is what a reader
   recognises the demand by.
3. **`status` bound to the wrong column.** `Category` was an accepted
   alias, but in the real file it holds a business unit (`"WMG"`), not a
   status. `CURRENT_STATUS` is the real column.
4. **`RLS_ID` is not unique** — it is the parent *requisition*. One file
   has 235 rows but only 160 distinct `RLS_ID`s; a single requisition can
   cover nine positions, each with its own `CURRENT_STATUS` and `Remarks`.
   Since Cosmos uses `DemandID` as both document id and partition key,
   keying on `RLS_ID` would have silently collapsed ~35% of demands into
   whichever row was written last. **`SR_ID_2` is unique in all four
   files** (235/235, 252/252, 258/258) and is the per-position id.

And on the actual question: **there are no PM/TM/EM email columns.** The
file carries `PM_NAME`, `TM_NAME`, `EM`, `SL_DM_NAME` — names only. It
*does* carry `Recruit TA EMAIL` and `Contract TA EMAIL`, but those are
talent-acquisition leads, not the owners the platform notifies. `CREATED_ID`
is inconsistent (`BALRAC` vs `AV20272170`) and identifies the creator, not
the owner, so emails cannot be derived from it either.

## Decision

Make the OIR file the authoritative source of owner emails, with Graph
demoted to an optional backstop, and fix the four defects above.

- `demand_id` now maps to `SR_ID_2`; `RLS_ID` is retained as
  `requisition_id` for traceability.
- `role` is no longer required and falls back to `skill`.
- `Category` removed as a `status` alias; `CURRENT_STATUS` added.
- Sheet resolution accepts `OIR `/`OR ` prefixes and excludes
  pivot/summary tabs.
- `pm_email`/`tm_email`/`em_email`/`dm_email` are parsed when present.
  Resolution order is **file column → PersonMap cache → Graph**, and Graph
  is only attempted when `GRAPH_LOOKUP_ENABLED=true` (default off).
- The PMO authorisation override moves to a `PMO_MEMBER_EMAILS` allowlist,
  removing the last Graph dependency. The Entra group path is retained
  behind the same flag as the better long-term answer, since group
  membership maintains itself.

Net effect: **no outstanding permission request of any kind.** The
platform ingests, hashes, persists, snapshots and generates digests with
the permissions it already has.

## Consequences

- **Notification is blocked on an upstream file change, not on IT.** Until
  the report gains owner email columns, every demand ingests correctly but
  has no notifiable owner. `ingest.rows_without_owner` reports this each
  run (currently 235/235) and the function logs a warning naming the
  columns needed. `tests/test_real_oir_files.py` carries an `xfail` that
  flips to passing the moment the columns appear.
- **The ask moves from IT to whoever produces the OIR report:** add
  `PM_EMAIL`, `TM_EMAIL` and `EM_EMAIL` columns. The report already
  contains `Recruit TA EMAIL`, so the data is clearly available upstream.
  No code change is needed when they land.
- Both routes remain open. If Graph consent is granted later, set
  `GRAPH_LOOKUP_ENABLED=true` and names resolve automatically for any row
  missing an email — the two mechanisms compose rather than conflict.
- **Status vocabulary drift is a known, unresolved gap.** Real `Remarks`
  values include `Pending CI`, `L2 in Progress`, `Deleted`, and casing
  variants (`Pending offer`, `Need profiles`) that are absent from
  `VALID_STATUSES`. Ingestion stores them verbatim, but
  `apply_update._validate_status` would reject them on write-back, and
  `rules.py` excludes only `Joined`/`To be deleted` (not `Deleted`).
  Deliberately left alone here: it needs a business decision on the
  canonical vocabulary rather than a guess.

## Lesson recorded

Three sessions of parser, rules and schema work were built and tested
entirely against synthetic fixtures that encoded the spec's assumptions.
Those fixtures passed continuously while the code could not read a single
real file. Sample data was available in `Data/` the whole time. Parse the
real artefact on day one, even before the logic exists.
