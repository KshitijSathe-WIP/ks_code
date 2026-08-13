# ADR 0007: Eliminate all stored secrets; one outstanding permission request

**Status:** Accepted
**Date:** 2026-08-12
**Owner:** Kshitij Sathe

## Context

Three Key Vault secrets were still outstanding and un-writable by the
project account (`ForbiddenByRbac` — the vault is RBAC-mode and Contributor
does not grant data-plane access; ADR 0006 covers the same discovery for
the Cosmos key):

- `azure-client-secret` — `sp-oir-dev`'s client secret, used by
  `graph_client.py` for owner display-name → email resolution
- `pmo-teams-webhook` — Teams channel webhook for PMO failure alerts
- `teams-bot-password` — Bot Framework app password

Unblocking these as designed would have needed *at least three* separate
privileged requests: a Key Vault data-plane role, a vault firewall IP rule,
and — critically — Graph admin consent, because checking
`servicePrincipals/{sp-oir-dev}/appRoleAssignments` returned **`[]`**.
`sp-oir-dev` has no Graph application permissions at all, so
`azure-client-secret` would have been useless even once stored: every Graph
call would have failed on insufficient privileges.

That reframed the problem. The secret was never the blocker; the *Graph
consent* was. And consent has to be granted to whichever identity actually
calls Graph — which need not be a service principal with a password.

## Decision

Move Graph to the Function App's managed identity, matching what ADR 0003
(Foundry) and ADR 0006 (Cosmos) already did, and remove all three Key Vault
references from `main.bicep`.

| Secret | Resolution |
|---|---|
| `azure-client-secret` | **Eliminated.** `graph_client.py` and `apply_update/authz.py` now authenticate as the managed identity when `IDENTITY_ENDPOINT` is present. `authz.py` reuses `graph_client._get_credential()` so both Graph callers run under one identity and one consent grant. |
| `pmo-teams-webhook` | **Downgraded to a plain app setting.** It's a channel webhook URL, alerting is optional (`_alert_pmo` no-ops when unset), and routing it through Key Vault would reintroduce a privileged request for negligible security gain at POC scope. |
| `teams-bot-password` | **Deferred and expected to be eliminated.** The bot isn't registered yet; when it is, use a managed-identity bot type (`MicrosoftAppType=SystemAssignedMSI`), which has no password. Revisit only if password-based registration proves unavoidable. |

Net effect: **no secret is stored anywhere** for the deployed application,
and the outstanding ask collapses from three privileged requests to one.

## The single remaining request

Grant the Function App's managed identity two read-only Graph application
permissions, with admin consent:

- `User.Read.All` — resolve a display name to an email
  (`GET /users?$filter=displayName eq '...'`)
- `GroupMember.Read.All` — PMO group membership check in `authz.py`
  (`POST /users/{id}/checkMemberGroups`)

`Directory.Read.All` would cover both if the admin prefers a single
broader grant, but the two above are narrower and read-only.

## Consequences

- All three outbound integrations (Cosmos, Foundry, Graph) now use one
  identity in production. Consistent, and nothing to rotate or leak.
- The local-dev fallback still uses `sp-oir-dev` via
  `AZURE_CLIENT_ID`/`SECRET`, so `.env` keeps those. That path also needs
  the Graph grant if Graph is exercised locally — but the deployed app
  doesn't depend on it.
- **The Key Vault is now unused.** It's left deployed rather than removed:
  it has purge protection enabled (so deletion isn't clean for 90 days
  anyway), and it's plausibly useful later. The
  `grantFunctionAppKeyVaultAccess` Bicep parameter and its role assignment
  are likewise retained but currently inert.
- Until the Graph consent lands, ingestion cannot populate owner emails,
  so digests have nobody to address. Everything else — parsing, hashing,
  Cosmos persistence, snapshotting, agent generation — works without it.
- If the tenant refuses Graph application permissions outright, the
  fallback is to source emails from the OIR file itself (adding email
  columns upstream) rather than resolving them from names, which would
  remove the Graph dependency entirely. Not needed unless consent is
  refused.
