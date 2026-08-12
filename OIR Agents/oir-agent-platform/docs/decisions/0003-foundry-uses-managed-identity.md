# ADR 0003: Foundry client authenticates via managed identity, not the service principal

**Status:** Accepted
**Date:** 2026-08-11 (evening)
**Owner:** Kshitij Sathe

## Context

The runbook asked the subscription owner to grant three RBAC roles: Key
Vault Secrets User (for the Function App to resolve its own Key Vault
references), Contributor (optional convenience), and Azure AI Developer
(for `sp-oir-dev`, the service principal `functions/shared/foundry_client.py`
authenticates as, to call the Foundry Agent Service).

Checking the grants in the Azure Portal surfaced a mismatch: all three
roles were granted, but to the **Function App's system-assigned managed
identity** (`func-oir-dev-rd5emhxcoejiw`), not to `sp-oir-dev`. Confirmed
via `az role assignment list --assignee <principal> --include-inherited`
for both identities -- the managed identity has all three at the resource
group scope; `sp-oir-dev` has none.

This is a reasonable grant on the granter's part (the Function App's own
identity is exactly what Azure uses to resolve `@Microsoft.KeyVault(...)`
references, so the Key Vault grant is correct as landed) but it doesn't
help `foundry_client.py`, which explicitly builds a `ClientSecretCredential`
from `AZURE_TENANT_ID`/`CLIENT_ID`/`CLIENT_SECRET` -- i.e. authenticates as
`sp-oir-dev`, an identity with no Foundry access.

## Decision

Change `foundry_client.py` to authenticate as the Function App's own
managed identity when one is available, instead of asking for a duplicate
RBAC grant on `sp-oir-dev`.

Detection: Azure Functions/App Service sets the `IDENTITY_ENDPOINT`
environment variable automatically whenever a managed identity is enabled,
and only then -- it's absent in local/CLI dev. Checking for it is a cheap,
deterministic signal, and avoids the alternative of eagerly requesting a
token just to see if it fails.

```python
if os.environ.get("IDENTITY_ENDPOINT"):
    return ManagedIdentityCredential()
return ClientSecretCredential(...)  # local/CLI dev fallback
```

`DefaultAzureCredential` was considered and rejected for this: it tries
`EnvironmentCredential` (equivalent to `ClientSecretCredential`) *before*
`ManagedIdentityCredential`, and `AZURE_CLIENT_ID`/`SECRET`/`TENANT_ID` are
always present as Function App settings (they're needed for Graph calls),
so `DefaultAzureCredential` would keep picking `sp-oir-dev` regardless of
where it's running -- the exact problem this change is meant to fix.

## Consequences

- No new RBAC request needed -- the grant already on the managed identity
  becomes useful as-is.
- `foundry_client.py`'s auth path now differs from `graph_client.py`'s
  (which still uses `sp-oir-dev` via `ClientSecretCredential`, since Graph
  application permissions are configured against that app registration,
  not the managed identity). This asymmetry is intentional, not an
  oversight: moving Graph to managed identity too would need a fresh
  Graph API permission grant on the managed identity, which is a separate
  piece of work with its own admin-consent step, not addressed here.
- Local/CLI development (running scripts directly, outside a deployed
  Function App) has no `IDENTITY_ENDPOINT`, so it transparently falls back
  to `sp-oir-dev` -- unchanged from before this ADR, and requires
  `AZURE_CLIENT_SECRET` to be set locally as it always did.
- If `sp-oir-dev` is ever granted Foundry access directly in the future
  (e.g. because Graph is also moved to managed identity and the service
  principal is retired), this fallback still works unchanged -- it's not
  a temporary hack, just a lower-priority path.
