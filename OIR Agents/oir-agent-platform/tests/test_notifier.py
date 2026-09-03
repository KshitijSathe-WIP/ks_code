"""Tests for the email redirect safety net.

Every address in this system belongs to a real colleague, so the question
these tests answer is not "does mail get sent" but "can mail reach a real
person by accident". Each gate is checked on its own, and then the
combinations are enumerated exhaustively, because the dangerous case is not
one wrong flag -- it is a plausible-looking pair of them.

No transport is touched: resolve_delivery() is pure, and deliver_digest()
is exercised with send_email patched out.
"""
from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from functions.shared.notifier import (
    Delivery,
    EmailBlocked,
    build_body,
    build_subject,
    deliver_digest,
    resolve_delivery,
    send_email,
)

REAL = "hardik.sanghavi1@wipro.com"
ME = "kshitij.sathe@wipro.com"

# Every env var that participates in a routing decision.
GATES = ("EMAIL_ENABLED", "EMAIL_REDIRECT_TO", "EMAIL_ALLOW_REAL_RECIPIENTS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from "nothing configured", so a passing test can
    never be an artefact of a variable left set by another one."""
    for name in GATES + ("EMAIL_SENDER_ADDRESS", "EMAIL_ACS_ENDPOINT",
                         "EMAIL_ACS_CONNECTION_STRING"):
        monkeypatch.delenv(name, raising=False)


class TestDefaultIsSilent:

    def test_nothing_configured_sends_nothing(self):
        d = resolve_delivery(REAL)
        assert d.will_send is False
        assert d.actual_to == ""

    def test_reason_is_recorded_not_swallowed(self):
        assert "EMAIL_ENABLED" in resolve_delivery(REAL).reason

    def test_redirect_alone_does_not_enable_sending(self, monkeypatch):
        """A redirect address is not permission to start emailing."""
        monkeypatch.setenv("EMAIL_REDIRECT_TO", ME)
        assert resolve_delivery(REAL).will_send is False


class TestRedirect:

    @pytest.fixture(autouse=True)
    def enabled(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "true")

    def test_goes_to_the_redirect_address(self, monkeypatch):
        monkeypatch.setenv("EMAIL_REDIRECT_TO", ME)
        d = resolve_delivery(REAL)
        assert d.actual_to == ME
        assert d.redirected is True

    def test_intended_recipient_is_preserved(self, monkeypatch):
        """Needed to show who the copy was for -- and to log it truthfully."""
        monkeypatch.setenv("EMAIL_REDIRECT_TO", ME)
        assert resolve_delivery(REAL).intended_to == REAL

    def test_redirect_wins_even_when_real_delivery_is_allowed(self, monkeypatch):
        """If both are set, the safer one must win."""
        monkeypatch.setenv("EMAIL_REDIRECT_TO", ME)
        monkeypatch.setenv("EMAIL_ALLOW_REAL_RECIPIENTS", "true")
        d = resolve_delivery(REAL)
        assert d.actual_to == ME, "redirect must take precedence over live delivery"
        assert d.redirected is True

    def test_whitespace_only_redirect_is_not_a_redirect(self, monkeypatch):
        """...and must fail closed rather than fall through to the real address."""
        monkeypatch.setenv("EMAIL_REDIRECT_TO", "   ")
        d = resolve_delivery(REAL)
        assert d.will_send is False
        assert d.actual_to != REAL


class TestFailClosed:
    """Removing the redirect must not silently start mailing real people."""

    def test_enabled_without_redirect_refuses(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "true")
        d = resolve_delivery(REAL)
        assert d.will_send is False
        assert d.actual_to != REAL
        assert "EMAIL_ALLOW_REAL_RECIPIENTS" in d.reason

    def test_live_delivery_requires_the_explicit_flag(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "true")
        monkeypatch.setenv("EMAIL_ALLOW_REAL_RECIPIENTS", "true")
        d = resolve_delivery(REAL)
        assert d.actual_to == REAL
        assert d.redirected is False

    def test_exhaustive_only_one_combination_reaches_a_real_person(self, monkeypatch):
        """The whole safety argument, enumerated.

        Across every combination of the three gates, a real address may be
        produced by exactly one: enabled + no redirect + explicit consent.
        """
        reaching = []
        for enabled, redirect, allow in itertools.product(
                ("true", "false"), ("", ME), ("true", "false")):
            for name, value in zip(GATES, (enabled, redirect, allow)):
                monkeypatch.setenv(name, value)
            if resolve_delivery(REAL).actual_to == REAL:
                reaching.append((enabled, redirect, allow))

        assert reaching == [("true", "", "true")], (
            f"real recipients reachable via unexpected config(s): {reaching}"
        )


class TestMessageFraming:

    def test_redirected_subject_names_the_intended_recipient(self):
        d = Delivery(REAL, ME, True, "")
        assert build_subject(d, "OIR: 10 demands") == f"[TEST -> {REAL}] OIR: 10 demands"

    def test_live_subject_is_untouched(self):
        d = Delivery(REAL, REAL, False, "")
        assert build_subject(d, "OIR: 10 demands") == "OIR: 10 demands"

    def test_banner_states_it_was_not_delivered(self):
        body = build_body(Delivery(REAL, ME, True, ""), "Hi Hardik, ...", "Hardik Sanghavi")
        assert "NOT DELIVERED" in body
        assert REAL in body
        assert "Hardik Sanghavi" in body

    def test_original_digest_is_preserved_verbatim(self):
        """A redirected copy is a preview, so it must not alter the message."""
        original = "Hi Hardik,\n\n- D123 stale 5 days\n\nPlease update Comments."
        body = build_body(Delivery(REAL, ME, True, ""), original)
        assert body.endswith(original)

    def test_live_body_has_no_banner(self):
        body = build_body(Delivery(REAL, REAL, False, ""), "Hi Hardik, ...")
        assert "TEST COPY" not in body


class TestDeliverDigest:

    def test_shadow_mode_never_calls_the_transport(self):
        with patch("functions.shared.notifier.send_email") as sender:
            d = deliver_digest(REAL, "subj", "body")
        sender.assert_not_called()
        assert d.will_send is False

    def test_redirected_send_uses_the_redirect_address(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "true")
        monkeypatch.setenv("EMAIL_REDIRECT_TO", ME)
        with patch("functions.shared.notifier.send_email") as sender:
            deliver_digest(REAL, "subj", "body", display_name="Hardik Sanghavi")
        kwargs = sender.call_args.kwargs
        assert kwargs["to"] == ME
        assert REAL not in kwargs["to"]
        assert REAL in kwargs["subject"], "the copy should say who it was for"

    def test_empty_recipient_is_not_sent(self):
        with patch("functions.shared.notifier.send_email") as sender:
            assert deliver_digest("", "subj", "body").will_send is False
        sender.assert_not_called()


class TestTransportConfig:
    """send_email refuses rather than guessing when half-configured."""

    def test_missing_sender_is_refused(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ACS_ENDPOINT", "https://acs.example.com")
        with pytest.raises(EmailBlocked, match="EMAIL_SENDER_ADDRESS"):
            send_email(ME, "s", "b")

    def test_missing_transport_is_refused(self, monkeypatch):
        monkeypatch.setenv("EMAIL_SENDER_ADDRESS", "noreply@example.com")
        with pytest.raises(EmailBlocked, match="EMAIL_ACS"):
            send_email(ME, "s", "b")
