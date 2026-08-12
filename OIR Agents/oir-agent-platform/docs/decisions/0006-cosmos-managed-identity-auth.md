# ADR 0006: Cosmos DB auth via managed identity, no account key stored

**Status:** Accepted
**Date:** 2026-08-11 (evening)
**Owner:** Kshitij Sathe

## Context

`main.bicep` originally passed the Cosmos account key to the Function App
as a Key Vault reference:
`COSMOS_KEY = @Microsoft.KeyVault(SecretUri=.../secrets/cosmos-key/)`.

Deploying revealed that secret could never be populated. The Key Vault has
`enableRbacAuthorization: true`, and writing a secret returned:

```
Inner error: { "code": "ForbiddenByRbac" }
```

Contributor on the resource group does not grant Key Vault *data-plane*
access under RBAC mode, and granting it needs
`Microsoft.Authorization/roleAssignments/write` -- the same Owner/User
Access Administrator permission that has been unavailable throughout this
project. (The vault also has `networkAcls.defaultAction: Deny` with no IP
rules, which would have blocked writes from a developer machine even with
the role.)

So the deployed app would have started with an unresolvable `COSMOS_KEY`
app setting.

## Decision

Drop the account key entirely. Grant the Function App's system-assigned
managed identity the Cosmos **Built-in Data Contributor** data-plane role,
and have `cosmos_client.py` authenticate via Entra ID.

Crucially, Cosmos data-plane role assignments are a *control-plane*
operation under the `Microsoft.DocumentDB` provider
(`sqlRoleAssignments/write`), not under `Microsoft.Authorization` -- so
plain Contributor is sufficient, and this succeeded where every previous
RBAC request in this tenant had to be escalated:

```bash
az cosmosdb sql role assignment create \
  --account-name td-bank-cosmos --resource-group TD-BANK-POC \
  --role-definition-id 00000000-0000-0000-0000-000000000002 \
  --principal-id <functionApp principalId> --scope "/"
```

Credential selection mirrors `foundry_client.py`: `IDENTITY_ENDPOINT`
present (deployed in Azure) -> `ManagedIdentityCredential`; else
`COSMOS_KEY` if set (local dev); else `AzureCliCredential`.

Verified end-to-end with `COSMOS_KEY` unset: write, read and delete all
succeeded against the live `OIRPlatform` database using only an Entra ID
token.

## Consequences

- **No Cosmos secret exists to leak, rotate, or store.** This removes the
  `COSMOS_KEY` app setting, its Key Vault reference, and the previously
  noted rotation chore. It's a better security posture than the design it
  replaces, arrived at because the weaker option was blocked.
- The Key Vault is now referenced only for `azure-client-secret`,
  `pmo-teams-webhook` and `teams-bot-password`. **Those secrets are still
  unpopulated and still un-writable by this account** -- the same
  `ForbiddenByRbac` applies. They are only needed for Graph owner-email
  resolution and Teams delivery, neither of which is exercised yet, but
  they must be resolved (by someone with Key Vault Secrets Officer, or by
  moving Graph to managed identity too) before those paths can work.
- `COSMOS_KEY` remains supported for local dev and is still required by
  `infra/provision_cosmos.py`, which performs control-plane work (creating
  databases and containers) that the data-plane role deliberately does not
  grant.
- The developer account was granted the same data-plane role to validate
  the code path; harmless in a sandbox, but worth removing if this pattern
  is promoted to a shared or production environment.
