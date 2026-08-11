# ADR 0002: Use Cosmos DB instead of SharePoint Lists for the OIR data store

**Status:** Accepted
**Date:** 2026-08-11
**Owner:** Kshitij Sathe
**Supersedes:** [ADR 0001](0001-sharepoint-lists-instead-of-dataverse.md) (Dataverse -> SharePoint Lists)

## Context

ADR 0001 moved the data store from Dataverse to SharePoint Lists to avoid a
Conditional Access policy that was blocking Dataverse access in the
`wilmodel3.onmicrosoft.com` tenant (the "TD-BANK-POC" sandbox where this
platform is actually being built and will run -- confirmed with the user
this is not a throwaway prototyping tenant; it's where the Azure stack for
this POC actually lives).

Attempting to provision SharePoint Lists in that same tenant surfaced a
more fundamental blocker: Microsoft Graph rejected every SharePoint call
(`/sites/root`, `/sites?search=*`) with

```json
{"error": {"code": "BadRequest", "message": "Tenant does not have a SPO license."}}
```

This is not a permissions gap -- it's a licensing gap. The tenant has no
SharePoint Online license at all, so no amount of admin consent or role
assignment would make SharePoint Lists work here. This also explains, in
hindsight, why the user's account presents as a guest
(`...#EXT#@wilmodel3.onmicrosoft.com`): this tenant is provisioned for
Azure/AI infrastructure (Cognitive Services, Cosmos DB, Neo4j, Azure
Virtual Desktop, networking), not Microsoft 365 collaboration.

## Decision

Replace SharePoint Lists with **Azure Cosmos DB** (SQL/Core API), using the
`td-bank-cosmos` account that already exists in the `TD-BANK-POC` resource
group -- alongside `IncidentRCA` and `lineage`, this platform gets its own
isolated database, `OIRPlatform`.

Verified end-to-end before committing to this rewrite (unlike the previous
two attempts, which each hit their blocking issue only after a partial
implementation):
- `az cosmosdb show` confirms `kind: GlobalDocumentDB` (plain SQL/Core API,
  not Gremlin/Mongo/Cassandra -- a natural fit for the existing document
  shape already used for `oir_demand` etc.), `publicNetworkAccess: Enabled`,
  no VNet/IP restrictions, and `disableLocalAuth: false` (key-based auth
  permitted).
- `az cosmosdb keys list` succeeded -- reading keys is a control-plane
  action covered by Contributor, unlike the `Microsoft.Authorization/
  roleAssignments/write` action that blocked us at every other turn in
  this tenant.
- A live connection with the retrieved key, via the Cosmos Python SDK,
  successfully listed the account's existing databases.

## Consequences

- **Minimal caller-side churn**, unlike the Dataverse -> SharePoint move.
  Cosmos DB stores schemaless JSON documents, so the exact same PascalCase
  field names introduced in ADR 0001 (`DemandID`, `PMEmail`, `IsActive`,
  etc.) carry over unchanged. Only the client class and its construction
  change in `ingest_oir`, `detect_exceptions`, `apply_update`, and the bot.
- **Four containers replace four SharePoint lists**, all partitioned by
  `/DemandID` except `PersonMap` (partitioned by `/DisplayName`, its own
  natural key): `Demands`, `SnapshotHistory`, `InteractionLog`, `PersonMap`.
- **Snapshot idempotency gets simpler, not harder.** SharePoint needed an
  explicit get-then-skip check before every snapshot insert. Cosmos
  documents have a natural unique key (`id`, scoped to partition); giving
  each snapshot a deterministic `id` of `{DemandID}::{SnapshotDate}` makes
  a plain `upsert_item` call idempotent by construction -- re-ingesting the
  same file twice just overwrites an identical document. No separate
  existence check needed.
- **Partial-update semantics need a read-merge-write**, unlike SharePoint's
  `PATCH .../items/{id}/fields`, which updates only the fields it's given.
  Cosmos's `upsert_item` replaces the whole document, so `upsert_demand`
  reads the existing document first (if any), merges in the caller's
  partial field dict, and writes the merged result back.
- **Filtering stays client-side where it already was** (`detect_exceptions/
  rules.py` scans all active demands and filters in Python, per ADR 0001) --
  Cosmos SQL *can* push down `WHERE IsActive = true` server-side cheaply,
  which `list_active_demands()` does, but the staleness/expiry logic itself
  still runs in Python since it depends on today's date, not a value that
  can be precomputed and indexed.
- **Shared account, isolated database.** `OIRPlatform` is a new database
  inside the existing `td-bank-cosmos` account, provisioned with shared
  throughput (400 RU/s) across its four containers -- cheap at this
  project's scale, and doesn't touch `IncidentRCA` or `lineage`.
- The SharePoint-specific implementation
  (`sharepoint_client.py`, `provision_sharepoint_lists.py`,
  `sharepoint-lists-schema.json`) is removed from the working tree, same
  pattern as ADR 0001's removal of the Dataverse implementation -- it
  remains in git history as the record of what was attempted.

## Alternatives considered

Unchanged from ADR 0001's analysis except: Dataverse and SharePoint Lists
are now *disqualified* (Conditional Access block; no SPO license), not
merely deprioritized. Azure SQL Database remains a documented fallback if
Cosmos DB's schemaless model or this project's scale ever becomes a real
constraint (see ADR 0001's alternatives table).
