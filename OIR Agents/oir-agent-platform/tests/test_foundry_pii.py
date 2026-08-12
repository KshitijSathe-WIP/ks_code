"""Tests for the PII scrubbing that keeps personal data out of Foundry prompts.

The TD-BANK Foundry account's content filter blocks prompts containing
person names and email addresses, so every agent call must be scrubbed
first -- see docs/decisions/0005-no-pii-sent-to-foundry-agents.md.
These tests are the regression guard for that.
"""
from __future__ import annotations

import json

from functions.shared.foundry_client import (
    RECIPIENT_NAME_TOKEN,
    _first_name_from_email,
    restore_pii,
    scrub_recipient,
)


class TestScrubRecipient:

    def _recipient(self, **overrides) -> dict:
        base = {
            "email": "jane.doe@wipro.com",
            "display_name": "Jane Doe",
            "expiring": [{"demand_id": "D1", "project": "Aurora Core", "role": "Data Engineer",
                          "dem_end_date": "2026-08-13", "days_left": 2, "status": "Need Profiles"}],
            "stale": [{"demand_id": "D2", "project": "TDCA", "role": "Mainframe Dev",
                       "stale_days": 4, "status": "Pending CI FB", "escalation_level": 2}],
        }
        base.update(overrides)
        return base

    def test_email_is_removed_entirely(self):
        safe, _ = scrub_recipient(self._recipient())
        assert "email" not in safe

    def test_display_name_replaced_with_token(self):
        safe, _ = scrub_recipient(self._recipient())
        assert safe["display_name"] == RECIPIENT_NAME_TOKEN

    def test_real_name_returned_for_later_substitution(self):
        _, display_name = scrub_recipient(self._recipient())
        assert display_name == "Jane Doe"

    def test_no_pii_anywhere_in_serialised_payload(self):
        """The whole point: nothing identifying survives into the prompt."""
        safe, _ = scrub_recipient(self._recipient())
        blob = json.dumps(safe).lower()
        for forbidden in ("jane", "doe", "wipro.com", "@"):
            assert forbidden not in blob, f"PII leaked into prompt: {forbidden!r}"

    def test_demand_entries_pass_through_untouched(self):
        """Demand data carries no PII, so it must survive scrubbing intact."""
        original = self._recipient()
        safe, _ = scrub_recipient(original)
        assert safe["expiring"] == original["expiring"]
        assert safe["stale"] == original["stale"]

    def test_shadow_mode_marker_is_stripped(self):
        """_shadow_original holds a real email -- must not reach the model."""
        safe, _ = scrub_recipient(self._recipient(_shadow_original="real.person@wipro.com"))
        assert "real.person@wipro.com" not in json.dumps(safe)

    def test_falls_back_to_email_derived_name(self):
        r = self._recipient()
        del r["display_name"]
        _, display_name = scrub_recipient(r)
        assert display_name == "Jane"


class TestRestorePii:

    def test_token_replaced_with_real_name(self):
        generated = f"Hi {RECIPIENT_NAME_TOKEN},\nYou have 2 demands."
        assert restore_pii(generated, "Jane Doe") == "Hi Jane Doe,\nYou have 2 demands."

    def test_multiple_occurrences_all_replaced(self):
        generated = f"{RECIPIENT_NAME_TOKEN} -- please review. Thanks, {RECIPIENT_NAME_TOKEN}."
        assert RECIPIENT_NAME_TOKEN not in restore_pii(generated, "Jane")

    def test_text_without_token_is_unchanged(self):
        assert restore_pii("No greeting here.", "Jane") == "No greeting here."

    def test_roundtrip_scrub_then_restore(self):
        recipient = {"email": "arun.kumar@wipro.com", "expiring": [], "stale": []}
        safe, display_name = scrub_recipient(recipient)
        generated = f"Hi {safe['display_name']}, you have nothing outstanding."
        assert restore_pii(generated, display_name) == "Hi Arun, you have nothing outstanding."


class TestFirstNameFromEmail:

    def test_dotted_local_part(self):
        assert _first_name_from_email("jane.doe@wipro.com") == "Jane"

    def test_underscore_local_part(self):
        assert _first_name_from_email("arun_kumar@wipro.com") == "Arun"

    def test_single_word_local_part(self):
        assert _first_name_from_email("kshitij@wipro.com") == "Kshitij"

    def test_empty_email_falls_back(self):
        assert _first_name_from_email("") == "there"

    def test_malformed_email_falls_back(self):
        assert _first_name_from_email("@wipro.com") == "there"
