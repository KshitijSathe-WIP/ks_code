# ADR 0001: Use SharePoint Lists instead of Dataverse for the OIR data store

**Status:** Accepted
**Date:** 2026-08-06
**Owner:** Kshitij Sathe

## Context

The original [implementation spec](../../OIR_Autonomous_Agent_Implementation_Spec.md)
(v1.0) specified Microsoft Dataverse as the system of record for
`oir_demand`, `oir_snapshot_history`, `oir_interaction_log`, and
`oir_person_map`. During implementation against the real TD-BANK-POC
tenant, we hit a sequence of access problems specific to Dataverse:

1. **RBAC role-assignment gap.** The deploying account had Contributor on
   the target resource group but not Owner/User Access Administrator, so
   Azure-side role assignments (Key Vault access, service-principal role
   grants) needed a separate admin to complete. This was worked around, but
   was the first sign of tighter-than-expected access boundaries.
2. **Conditional Access blocking Dataverse specifically.** A validated,
   correctly-scoped Azure AD token (right tenant, right audience, right
   user) was rejected with `401 Unauthorized` on every Dataverse Web API
   call, including the trivial `WhoAmI` endpoint -- while the *same login
   session* worked fine for Azure Resource Manager and Microsoft Graph
   (Foundry agent creation succeeded via the identical credential). This
   points to a Conditional Access policy scoped to the Dynamics
   CRM/Power Platform resource specifically, common in regulated-industry
   tenants, that a non-interactive CLI session doesn't satisfy.
3. **Application User picker not surfacing a healthy app registration.**
   The service principal (`sp-oir-dev`, confirmed healthy: correct tenant,
   enabled, single-tenant sign-in audience) did not appear in the
   Power Platform "+ Add an app" picker by ID or by name, in either the
   classic Advanced Settings UI or the modern admin center. Root cause
   unconfirmed (directory propagation delay vs. a tenant-level Enterprise
   Application visibility restriction) -- but combined with (2), it points
   to the same underlying pattern: this tenant has Dataverse/Power Platform
   access locked down more tightly than plain Azure resource access.

None of these are code defects; they're tenant governance boundaries that
would require a separate administrator (Power Platform tenant admin) to
resolve, and that admin's availability/timeline was unknown.

## Decision

Replace Dataverse with **SharePoint Lists** as the data store, accessed via
Microsoft Graph using the same application (client-credentials) credentials
already used for owner-email resolution (`functions/shared/graph_client.py`).

This was chosen over the other alternative considered (Azure SQL Database)
because:
- The user already has full administrative control over the SharePoint
  site in question -- it's the same site the source OIR Excel file already
  lands on, so there is no new access boundary to negotiate.
- Microsoft Graph (`graph.microsoft.com`) is a different resource/audience
  than Dataverse (`*.crm.dynamics.com`); the Conditional Access block
  observed in (2) is specific to the Dynamics CRM audience and does not
  apply to Graph, which the existing `graph_client.py` already exercises
  successfully in this codebase's design.
- It avoids introducing an entirely new Azure resource (Azure SQL would
  need its own provisioning, firewall rules, and connection secrets) at a
  point where the team is trying to reduce the number of pending
  administrative asks, not add to them.

## Consequences

- **Four SharePoint Lists** replace the four Dataverse tables (see
  `infra/sharepoint-lists-schema.json`): `OIR Demands`,
  `OIR Snapshot History`, `OIR Interaction Log`, `OIR Person Map`.
- `functions/shared/sharepoint_client.py` replaces
  `functions/ingest_oir/dataverse_client.py`. The public shape callers see
  (`get_demand` returning an `OIRDemand`, `upsert_demand`, `insert_snapshot`,
  `append_log`, `get_cached_email`/`cache_email`) is preserved as closely as
  possible to minimize churn in `ingest_oir`, `detect_exceptions`,
  `apply_update`, and the Teams bot.
- **No server-side computed columns or composite unique constraints.**
  Dataverse's `oir_stale_days` computed column and the
  `(DemandID, Snapshot_Date)` unique constraint on snapshot history are both
  enforced in application code instead (`rules.py` computes stale days in
  Python; `sharepoint_client.insert_snapshot` does a get-then-skip check
  before inserting). This was already partially true even under Dataverse
  (computed columns aren't filterable via OData), so the practical
  difference is smaller than it sounds.
- **Filtering moves client-side.** `detect_exceptions/rules.py` previously
  pushed staleness/expiry filters down via OData `$filter`. SharePoint list
  filtering via Graph is less reliable on non-indexed columns and has a
  5,000-item list-view threshold; at this project's scale (one team's daily
  demand list), fetching all active rows and filtering in Python is simpler
  and more robust than fighting Graph's filter quirks. `DemandID`,
  `SnapshotDate`, and `DisplayName` are still marked as indexed columns in
  the provisioning script so the narrow single-record lookups
  (`get_demand`, `get_cached_email`) stay reliable.
- **Revisit if this scales up significantly** (multiple teams, >5,000
  historical snapshot rows, or a need for true Power BI DirectQuery over
  the history table) -- Azure SQL Database remains the documented fallback
  in this ADR's alternatives analysis if that point is reached.
- The original Dataverse-specific implementation (`dataverse_client.py`,
  `infra/dataverse-schema.json`, `infra/provision_dataverse.py`,
  `infra/register_dataverse_app_user.py`) is removed from the working tree.
  It remains in git history (see the `experiments` branch commit history
  prior to this change) as the record of what was built and why it was
  abandoned, per this ADR.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Fix Dataverse access | Blocked on a Power Platform tenant admin whose availability/timeline was unknown; two independent access failures (CA policy, app picker) suggested a systemic tenant restriction, not a one-off fixable setting. |
| New Dataverse environment (self-created, auto-admin) | Created (`orge0db2320.crm8.dynamics.com`) and confirmed the creator is System Administrator, but the *same* Conditional Access and app-visibility issues applied to this environment too, since they're tenant-wide policies, not environment-specific. |
| Azure SQL Database | Closest architectural match (real constraints, computed columns, Power BI DirectQuery) and remains the fallback if SharePoint Lists' scale limits are hit -- but introduces a new Azure resource and its own provisioning/firewall/secrets work, which the team preferred to avoid while multiple other administrative requests were already pending. |
| Azure Cosmos DB (already provisioned in-subscription) | Zero new provisioning, but weaker fit for the spec's relational assumptions and Power BI DirectQuery requirement than either Dataverse or Azure SQL. |
