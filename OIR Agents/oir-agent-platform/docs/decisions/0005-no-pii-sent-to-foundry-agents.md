# ADR 0005: Send no PII to Foundry agents; pseudonymize and re-substitute

**Status:** Accepted
**Date:** 2026-08-11 (evening)
**Owner:** Kshitij Sathe

## Context

The first-ever *invocation* of a Foundry agent with a realistic payload
(previous work had only ever *created* agents, never called one) was
rejected outright:

```json
{"error": {"code": "content_filter", "message":
  "The response was filtered due to the prompt triggering Azure OpenAI's content management policy",
  "content_filter_results": {
    "personally_identifiable_information": {
      "filtered": true, "detected": true,
      "sub_categories": [
        {"sub_category": "Person", "filtered": true, "detected": true},
        {"sub_category": "Email",  "filtered": true, "detected": true}]}}}}
```

The `TD-BANK` Foundry account applies a PII content filter that **blocks
prompts containing person names and email addresses**. The digest agent's
entire purpose is to take one person's name, email, and demand list and
write them a message -- so every production digest call would have failed.

This is independent of ADR 0004's API-surface migration: the same filter
sits in front of both surfaces. It had simply never been hit, because no
agent had ever actually been invoked.

The account's editable RAI policies (`GR-Graph`, `RCAi-Guardrails563`) do
not expose a PII filter to toggle, and the effective policy for our agents
is the built-in `Microsoft.DefaultV2`, which is not retrievable or editable
via ARM. Relaxing the filter would require a tenant/account admin to author
and attach a custom policy.

## Decision

Send **no personal data to any model**. Replace it with placeholder tokens
before the call, and substitute the real values into the generated text
afterwards.

`functions/shared/foundry_client.py` provides:
- `scrub_recipient(recipient) -> (safe_payload, display_name)` -- drops
  `email` entirely (the model never needs it; delivery happens in code) and
  replaces the name with `{{RECIPIENT_NAME}}`.
- `restore_pii(text, display_name)` -- swaps the real name back in.

Verified against the live service: the scrubbed payload passes the filter
and the agent returns exactly the intended output with the token intact,
ready for substitution:

```
Hi {{RECIPIENT_NAME}},
You have open demands needing your attention:
Aurora Core
- Demand D1 (Data Engineer) is expiring in 2 days. ...
```

This was chosen over requesting a filter exemption because it needs no
admin action, and because for a banking client "no personal data ever
reaches the model" is a materially stronger position than "we turned the
PII filter off" -- the constraint pushed us to a better design than the
one originally specified.

## Consequences

- The demand records themselves need no scrubbing: they carry only demand
  ids, project/role names, dates and statuses -- no personal data. Only the
  recipient's own name and email were ever PII in the digest payload.
- **Free text we don't control can still trip the filter.** Excel
  `Comments` and users' own Teams replies may contain colleague names
  ("Rahul is interviewing Thursday"). These cannot be reliably scrubbed
  without named-entity recognition, which would add a dependency and its
  own error modes. Instead `foundry_client` raises a distinct
  `ContentFilteredError`, and the Teams bot degrades gracefully -- it asks
  the user to use the Adaptive Card instead of free text, which routes the
  same update through a structured path that never reaches a model. The
  occurrence is logged so the real-world frequency can be measured during
  shadow mode.
- `_first_name_from_email()` derives a greeting name from the email local
  part (`jane.doe@wipro.com` -> `Jane`) when no display name is available,
  falling back to `there`. This happens entirely in code, after the model
  call.
- If the PII filter is ever relaxed, none of this needs reverting -- it
  remains the more privacy-preserving design, and the graceful degradation
  path becomes dormant rather than wrong.

## Lesson recorded

Creating a resource is not the same as exercising it. Three sessions of
work went into agents that had never once been called; the blocking defect
surfaced within seconds of the first real invocation. Wire up an
end-to-end call against the real service as early as possible, even with
a throwaway payload.
