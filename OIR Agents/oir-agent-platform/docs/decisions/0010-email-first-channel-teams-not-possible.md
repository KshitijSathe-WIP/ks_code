# 0010 - Email as the first delivery channel; Teams is not possible from this tenant

Date: 2026-09-03
Status: Accepted

## Context

The platform can detect stale demands and generate a digest for each owner,
but had no way to put that digest in front of a human. The original design
assumed Microsoft Teams: a registered bot, an app manifest, and proactive
messages to each owner.

Before building any of it we checked whether Teams delivery was achievable
at all. It is not.

**1. The tenant has no Teams licence.** `subscribedSkus` returns exactly one
SKU for the whole tenant:

    POWER_BI_STANDARD   consumed=2

No Teams, no M365, no Exchange. This is the same root cause that killed
SharePoint Lists in ADR 0002: `wilmodel3` is an Azure/AI sandbox, not a
collaboration tenant. There is no Teams here to post into.

**2. The recipients are in a different directory.** Every owner we notify is
`@wipro.com` (tenant `258ac4e4-...`); our infrastructure is in
`6efbfbdd-...`. Directory lookups for them return nothing:

    amul.tyagi@wipro.com    -> matches: 0
    sharad.jain4@wipro.com  -> matches: 0

Proactive Teams messaging needs either a prior conversation with the bot or
an app installation for that user. Both require the user to exist in the
directory the bot is registered in. They do not.

**3. Cross-tenant publishing needs Wipro's Teams administrator.** Even with a
licence, an app that messages Wipro employees must be approved in the Wipro
tenant -- a larger ask than anything granted so far, in the tenant that
already blocked Dataverse via Conditional Access (ADR 0001) and declined
Graph application permissions (ADR 0008).

`Microsoft.BotService` *is* registered, so a bot resource could be created.
That is necessary but nowhere near sufficient, and creating one would have
produced a resource that could never deliver a message.

## Decision

Email is the first delivery channel. Teams is deferred until the platform
lives in a tenant that has it.

Delivery is implemented in `functions/shared/notifier.py`, deliberately
separate from digest generation, and defaults to sending nothing.

During testing all mail is redirected to a single mailbox
(`EMAIL_REDIRECT_TO`) while remaining *addressed* to the real owner, so the
content can be reviewed without putting test messages in colleagues'
inboxes. The redirected copy carries a banner naming the intended recipient
and is otherwise byte-identical to what that person would have received.

The safety gates fail closed:

    EMAIL_ENABLED != true          -> nothing sent (the default)
    EMAIL_REDIRECT_TO set          -> everything goes there
    no redirect, and no explicit
    EMAIL_ALLOW_REAL_RECIPIENTS    -> REFUSED

Clearing the redirect does not start live delivery; it stops delivery. Going
live requires deliberately setting a second flag.
`tests/test_notifier.py` enumerates all eight combinations of the three
gates and asserts exactly one reaches a real address.

Transport is Azure Communication Services, provisioned in TD-BANK-POC:

    emailServices/oir-email-dev
      domains/AzureManagedDomain -> b782f25f-...-224b27159679.azurecomm.net
    communicationServices/oir-acs-dev
      sender: DoNotReply@b782f25f-...-224b27159679.azurecomm.net

The `az communication` CLI extension could not be installed -- the extension
index fails TLS verification behind the corporate proxy -- so all three
resources were created through the ARM REST API instead.

This is the one place the no-secrets design (ADR 0007) could not be held.
Authenticating with the Function App's managed identity needs a role
assignment on the ACS resource, and `roleAssignments/write` is denied to
this account, exactly as it was for Key Vault:

    AuthorizationFailed ... does not have authorization to perform action
    'Microsoft.Authorization/roleAssignments/write'

So `EMAIL_ACS_CONNECTION_STRING` is stored as a Function App setting. It is
never committed, and `notifier.send_email()` prefers `EMAIL_ACS_ENDPOINT`
with a managed identity whenever one is granted -- switching over later
means setting one variable and clearing the other, with no code change.

## Consequences

- Digests reach real people without depending on tenant capabilities we do
  not have, and without a cross-tenant admin approval.
- Shadow mode became genuinely reviewable: every digest is written to
  `InteractionLog` with the delivery decision attached, sent or not.
- Fixed a bug found while wiring this up. The old shadow path swapped
  `recipient["email"]` to the PMO address *before* invoking the agent, and
  `display_name` is derived from that email -- so a shadow digest would have
  opened "Hi Kshitij" instead of showing what the real owner would read.
  Redirection now happens only at delivery.
- Deliverability from an ACS-managed domain (`*.azurecomm.net`) to
  `wipro.com` is unproven and may be filtered by Wipro's gateway. Testing to
  our own mailbox first surfaces this immediately; a custom verified domain
  is the fallback.
- Replies are out of scope for this channel. The reply-interpreter agent
  assumed a Teams conversation; inbound email parsing is a separate problem
  and is not solved here.
