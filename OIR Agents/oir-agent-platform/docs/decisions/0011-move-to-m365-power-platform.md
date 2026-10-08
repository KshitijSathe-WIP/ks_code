# 0011 - Move to Power Automate, SharePoint, Teams cards and Copilot Studio

Date: 2026-10-08
Status: **Proposed** (not accepted until Phase 0 of the design passes)

Full design: `docs/design/m365-power-platform-design.md`.

## Context

The Azure build works end to end: 14 real files ingested, business-day staleness,
ADH escalation, Foundry-generated digests, and email delivered through ACS to a test
mailbox. What it cannot do is reach people the way they work, or know who they are.

- Teams is impossible from the sandbox tenant: no Teams licence, recipients are not
  directory users there (ADR 0010).
- Email works but is one-way. Nothing reads replies, so a "reply to this email"
  instruction sends updates into a void; the web-form workaround needed token links
  to fake an identity the platform otherwise has no way to establish.
- Every attempt to get closer to the recipients' own tenant from the sandbox hit
  access or domain restrictions: Dataverse behind Conditional Access (ADR 0001),
  no SharePoint licence (ADR 0002), Graph consent refused (ADR 0008), role
  assignments denied (ADR 0007, 0010).

## Decision (proposed)

Rebuild on the Microsoft 365 services that already exist in the Wipro tenant:

- SharePoint lists for state; the archived daily files are the snapshot history.
- Power Automate for orchestration, with the rules in Office Scripts so they can be
  tested locally against the existing Python.
- Teams adaptive cards for proactive notification and updates.
- A Copilot Studio agent in Teams for conversational updates, using Teams SSO for
  identity.

The business rules, the fail-closed redirect model and the audit trail carry over.
The Foundry digest agent is retired: a card renders structured data directly, and
the model added only wording while causing the refusal failures measured earlier.

## Consequences

- Real identity (Teams SSO) replaces token links, which removes the reason the web
  form existed.
- Updates become two-way, which exposes a problem the email design hid: Comments and
  Remarks have two possible writers, the upstream file and the platform. The design
  keeps them separate (overlay columns) but whether the upstream ever receives owner
  updates is an open business decision (design D1).
- Logic that was ordinary tested Python becomes Office Script plus flow actions, which
  are harder to test. The Python code is kept as the oracle and the engine must
  reproduce its results on all 14 files before anything is trusted.
- Several assumptions about the Wipro tenant are unverified (design section 2), among
  them premium-connector licensing, Teams policy for the Workflows bot, and Office
  Scripts. This ADR should not move to Accepted until those spikes pass.
- The Azure resources stay as the rollback until a pilot succeeds, then are
  decommissioned. Uncommitted web-form work is archived, not deleted.
