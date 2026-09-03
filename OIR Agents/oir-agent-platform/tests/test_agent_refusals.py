"""Tests for refusal detection and retry in foundry_client.

The digest agent intermittently answers "I'm sorry, but I cannot assist with
that request" instead of writing the digest. Measured against the live agent
over 15 identical calls: 10/15 refusals with a bare JSON prompt, 1/15 once an
explicit instruction and a non-name placeholder are used. Non-zero either
way, so the guard exists to make sure the residual case is "nobody is
emailed" rather than "somebody is emailed a refusal".

The nastiest variant is a refusal glued onto a half-written digest -- the
response carries two output items and output_text concatenates them -- which
is why detection scans the whole reply rather than just its beginning.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from functions.shared import foundry_client
from functions.shared.foundry_client import (
    AgentRefusedError,
    RECIPIENT_NAME_TOKEN,
    looks_like_refusal,
)

GOOD = "Hi Nisarg,\n\n- D123 is 5 days stale.\n\nPlease reply with an update."
PLAIN_REFUSAL = "I'm sorry, but I cannot assist with that request."
# Observed verbatim in a real run: truncated mid-word, refusal appended.
GLUED_REFUSAL = (
    "Hi Sharad,\n\nQEP:\n- 193405 (13 days"
    "I'm sorry, but I cannot assist with that request."
)


class TestRefusalDetection:

    def test_plain_refusal(self):
        assert looks_like_refusal(PLAIN_REFUSAL)

    def test_refusal_appended_to_a_partial_digest(self):
        """The dangerous one: mostly-valid text with a refusal welded on."""
        assert looks_like_refusal(GLUED_REFUSAL)

    def test_good_digest_is_not_flagged(self):
        assert not looks_like_refusal(GOOD)

    def test_empty_is_not_flagged_as_refusal(self):
        assert not looks_like_refusal("")

    @pytest.mark.parametrize("text", [
        "I am sorry, I cannot assist with that request.",
        "Unable to assist with this.",
        "I can't help with that.",
    ])
    def test_common_phrasings(self, text):
        assert looks_like_refusal(text)

    def test_ordinary_apology_in_a_digest_is_not_a_refusal(self):
        """A digest may legitimately contain the word 'sorry' in prose;
        only the refusal formulations should trip the guard."""
        assert not looks_like_refusal(
            "Hi Amit,\n\nSorry for the duplicate: D123 is 4 days stale.")


class TestRetry:

    def _client(self, replies):
        client = MagicMock()
        client.responses.create.side_effect = [
            MagicMock(output_text=r) for r in replies
        ]
        return client

    def test_retries_past_a_refusal(self):
        with patch.object(foundry_client, "_get_openai_client",
                          return_value=self._client([PLAIN_REFUSAL, GOOD])):
            assert foundry_client.invoke_agent("digest-agent", "p") == GOOD

    def test_retries_past_a_glued_refusal(self):
        with patch.object(foundry_client, "_get_openai_client",
                          return_value=self._client([GLUED_REFUSAL, GOOD])):
            assert foundry_client.invoke_agent("digest-agent", "p") == GOOD

    def test_gives_up_and_raises_rather_than_returning_a_refusal(self):
        client = self._client([PLAIN_REFUSAL] * 3)
        with patch.object(foundry_client, "_get_openai_client", return_value=client):
            with pytest.raises(AgentRefusedError):
                foundry_client.invoke_agent("digest-agent", "p")
        assert client.responses.create.call_count == 3

    def test_no_retry_when_the_first_reply_is_good(self):
        client = self._client([GOOD, GOOD, GOOD])
        with patch.object(foundry_client, "_get_openai_client", return_value=client):
            foundry_client.invoke_agent("digest-agent", "p")
        assert client.responses.create.call_count == 1

    def test_empty_replies_are_retried_then_raise(self):
        client = self._client(["", "", ""])
        with patch.object(foundry_client, "_get_openai_client", return_value=client):
            with pytest.raises(AgentRefusedError):
                foundry_client.invoke_agent("digest-agent", "p")
        assert client.responses.create.call_count == 3

    def test_attempt_count_is_configurable(self):
        client = self._client([PLAIN_REFUSAL] * 5)
        with patch.object(foundry_client, "_get_openai_client", return_value=client):
            with pytest.raises(AgentRefusedError):
                foundry_client.invoke_agent("digest-agent", "p", max_attempts=5)
        assert client.responses.create.call_count == 5


class TestPlaceholderToken:
    """The token is load-bearing: a real-looking name refused 12/12 times."""

    def test_token_is_not_name_shaped(self):
        assert RECIPIENT_NAME_TOKEN.isupper() or "_" in RECIPIENT_NAME_TOKEN

    def test_token_has_no_mustache_braces(self):
        assert "{" not in RECIPIENT_NAME_TOKEN and "}" not in RECIPIENT_NAME_TOKEN

    def test_round_trip_still_restores_the_real_name(self):
        recipient = {"email": "nisarg.shah4@wipro.com", "stale": [], "expiring": []}
        safe, display_name = foundry_client.scrub_recipient(recipient)
        assert safe["display_name"] == RECIPIENT_NAME_TOKEN
        assert "nisarg.shah4@wipro.com" not in str(safe), "email must not reach the model"
        restored = foundry_client.restore_pii(f"Hi {RECIPIENT_NAME_TOKEN}, ...", display_name)
        assert restored == f"Hi {display_name}, ..."
