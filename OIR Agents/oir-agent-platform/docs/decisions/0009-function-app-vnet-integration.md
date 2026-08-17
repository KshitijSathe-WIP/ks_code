# ADR 0009: VNet-integrate the Function App; defer the Cosmos lockdown

**Status:** Accepted (partially applied — see "Not done yet")
**Date:** 2026-08-17
**Owner:** Kshitij Sathe

## Context

The Cosmos account `td-bank-cosmos` was locked down mid-project (VNet
filter on, four allowlisted IPs, one VNet rule), which broke the OIR
Function App with:

```
Forbidden: Request originated from IP 4.255.23.245 through public internet.
This is blocked by your Cosmos DB account firewall settings.
```

It was subsequently set back to **All networks** in the portal, which
restored access — and, notably, **deleted** the four IP rules and the VNet
rule rather than merely suspending them. If that lockdown is reapplied, it
starts from an empty rule set.

Three apps share this Cosmos account, all owned by the same person:

| App | Plan | Plan's resource group | VNet-integrable |
|---|---|---|---|
| OIR Function App | `plan-oir-dev-…` (B1) | `TD-BANK-POC` | yes |
| `td-rca-api` | `TD-BANK` (B1) | `TD-BANK-POC` | yes |
| `data-tools-app` | `synthetic-data-appbuilder` | `RG_GenAIAppBuilder` | **no — no access** |

None of the three had outbound IPs in the old allowlist, so the lockdown
had almost certainly broken all three, not just this one.

`data-tools-app` is the complication: the *app* lives in `TD-BANK-POC` but
its *plan* lives in `RG_GenAIAppBuilder`, where the project account gets
`AuthorizationFailed` on `Microsoft.Web/serverfarms/read`. VNet integration
can't be configured for a plan you can't read.

## Decision

Do the durable half now, defer the disruptive half.

**Applied:** give the OIR Function App its own VNet path to Cosmos, while
leaving the Cosmos firewall wide open so nothing changes for the other two
apps.

- New subnet `snet-oir-func` (`10.2.3.0/26`) in the `TD-BANK` VNet,
  delegated to `Microsoft.Web/serverFarms`, with the
  `Microsoft.AzureCosmosDB` service endpoint.
- Regional VNet integration enabled on `func-oir-dev-rd5emhxcoejiw`, with
  `vnetRouteAllEnabled=true`.

A *separate* subnet was required rather than reusing the existing
`CosmosDB` subnet (`10.2.2.0/24`): App Service integration needs an
exclusively-delegated subnet, and delegating that one would have been a
change to shared infrastructure. It is in any case empty — no NICs, no
private endpoints — so the pre-existing Cosmos VNet rule pointing at it
granted access to nothing.

`vnetRouteAllEnabled=true` is not optional: without it only RFC1918 traffic
enters the VNet, and Cosmos has a public endpoint, so the service endpoint
would never engage. Before enabling it, the Function App's runtime storage
account (`stooirdevrd5emhxcoejiw`) was checked for a firewall
(`defaultAction: Allow`, no rules) — had it been restricted, routing all
outbound through the VNet would have taken the whole Functions host down,
not just Cosmos access.

**Verified after the change:** the app still returns its own `400`
validation response, and a Cosmos lookup returns `404 Demand not found` —
i.e. Cosmos is reachable with all outbound traffic flowing through the VNet.
Cosmos config confirmed unchanged (`vnetFilter: false`, 0 IP rules, 0 VNet
rules) and both other apps confirmed still un-integrated.

## Not done yet (deliberately)

**No Cosmos VNet rule was added, and the firewall was not re-enabled.**

Adding a VNet rule risks `az cosmosdb network-rule add` implicitly setting
`isVirtualNetworkFilterEnabled=true`, which would switch the account to
"Selected networks" and cut off the other two apps. Since re-locking the
account requires touching Cosmos at that point anyway, adding the rule now
buys nothing and carries real risk — so both steps are deferred to a single
planned change.

When locking down, do it in this order, and **not** before `data-tools-app`
is covered:

```bash
# 1. Rule for this app's subnet
az cosmosdb network-rule add --name td-bank-cosmos --resource-group TD-BANK-POC \
  --virtual-network TD-BANK --subnet snet-oir-func

# 2. Stopgap for data-tools-app until its plan is reachable (IPs are stable
#    only while that plan is unchanged — they rotate on scale/tier change)
az cosmosdb update --name td-bank-cosmos --resource-group TD-BANK-POC \
  --ip-range-filter 20.246.210.180,20.246.210.210,20.246.210.219,20.246.210.222,20.246.211.30,20.246.211.74,40.71.11.143

# 3. td-rca-api: give plan TD-BANK its own delegated subnet and repeat 1
#    (plans cannot share an integration subnet)

# 4. Only then enforce
az cosmosdb update --name td-bank-cosmos --resource-group TD-BANK-POC \
  --enable-virtual-network true
```

Verify all three apps reach Cosmos *between* steps 3 and 4 — while the
account is still open — so a mistake surfaces before anything is enforced,
not after.

## Consequences

- The OIR app no longer depends on App Service outbound IPs for Cosmos
  access. Those IPs were shared with `td-rca-api` (both apps land on the
  same App Service stamp — `4.255.23.245`, the IP in the original error,
  appears in both apps' outbound lists), so the coupling is now removed.
- **All** of this app's outbound traffic now egresses via the VNet,
  including Foundry and Graph. No subnet currently has a route table, so it
  goes straight out. But an Azure Firewall (`TD-BANK-Firewall`) sits in this
  VNet: if a UDR is ever attached forcing `0.0.0.0/0` through it, Foundry
  and Graph calls will start failing unless explicitly allowed there. This
  is the most likely future cause of a sudden, confusing outage.
- Cosmos remains open to all networks, so the security posture is unchanged
  for now — this ADR buys readiness, not protection.
- Two prerequisites before the lockdown is safe: access to
  `RG_GenAIAppBuilder` (or move `data-tools-app` to a plan in this
  subscription's reachable scope), and a delegated subnet for the `TD-BANK`
  plan.
