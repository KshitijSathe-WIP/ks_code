"""Tests for the update-form link tokens.

These links are the only credential a recipient has -- they arrive by email
with no session and no Microsoft identity behind them (ADR 0010) -- so the
properties worth pinning down are: the token is unguessable, it carries no
personal data in the URL, it stops working when it should, and it identifies
without granting anything on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from functions.shared.action_token import (
    ActionToken,
    DEFAULT_TTL_DAYS,
    InvalidActionToken,
    build_url,
    issue,
    new_token_value,
    resolve,
)

EMAIL = "hardik.sanghavi1@wipro.com"
BASE = "https://func-oir-dev.azurewebsites.net"


class FakeDb:
    def __init__(self):
        self.tokens: dict[str, ActionToken] = {}
        self.ttls: dict[str, int] = {}

    def put_action_token(self, token, ttl_seconds):
        self.tokens[token.token] = token
        self.ttls[token.token] = ttl_seconds

    def get_action_token(self, value):
        return self.tokens.get(value)

    def revoke_action_token(self, value):
        self.tokens.pop(value, None)


class TestTokenValue:

    def test_is_long_enough_to_be_unguessable(self):
        # 32 random bytes -> >=43 urlsafe-base64 chars
        assert len(new_token_value()) >= 43

    def test_values_do_not_repeat(self):
        assert len({new_token_value() for _ in range(500)}) == 500

    def test_is_url_safe(self):
        import re
        assert re.fullmatch(r"[A-Za-z0-9_-]+", new_token_value())


class TestUrl:

    def test_contains_no_personal_data(self):
        """The whole reason for an opaque token: URLs end up in browser
        history, proxy logs and forwarded messages."""
        url = build_url(BASE, "abc123")
        assert EMAIL not in url
        assert "@" not in url
        assert "hardik" not in url.lower()

    def test_points_at_the_update_route(self):
        assert build_url(BASE, "abc123") == f"{BASE}/api/update?t=abc123"

    def test_tolerates_a_trailing_slash(self):
        assert build_url(BASE + "/", "x") == f"{BASE}/api/update?t=x"


class TestIssueAndResolve:

    def test_round_trip(self):
        db = FakeDb()
        token = issue(db, EMAIL)
        assert resolve(db, token.token) == EMAIL

    def test_email_is_never_in_the_token_itself(self):
        db = FakeDb()
        token = issue(db, EMAIL)
        assert EMAIL not in token.token
        assert "hardik" not in token.token.lower()

    def test_default_expiry_is_applied(self):
        db = FakeDb()
        token = issue(db, EMAIL)
        expected = datetime.now(timezone.utc) + timedelta(days=DEFAULT_TTL_DAYS)
        assert abs((token.expires_at - expected).total_seconds()) < 60

    def test_cosmos_ttl_matches_the_expiry(self):
        """Belt and braces: the row also self-deletes, so a forgotten token
        does not sit in the store indefinitely."""
        db = FakeDb()
        token = issue(db, EMAIL, ttl_days=3)
        assert db.ttls[token.token] == 3 * 86400

    def test_each_issue_is_a_fresh_token(self):
        db = FakeDb()
        assert issue(db, EMAIL).token != issue(db, EMAIL).token

    def test_blank_email_is_rejected(self):
        with pytest.raises(ValueError):
            issue(FakeDb(), "  ")


class TestRejection:

    def test_unknown_token(self):
        with pytest.raises(InvalidActionToken):
            resolve(FakeDb(), "not-a-real-token")

    def test_empty_token(self):
        with pytest.raises(InvalidActionToken):
            resolve(FakeDb(), "")

    def test_expired_token_is_refused_even_if_the_row_survives(self):
        """Cosmos TTL sweeps lazily, so expiry cannot be inferred from the
        row simply being gone."""
        db = FakeDb()
        stale = ActionToken("tok", EMAIL,
                            datetime.now(timezone.utc) - timedelta(seconds=1))
        db.tokens["tok"] = stale
        with pytest.raises(InvalidActionToken):
            resolve(db, "tok")

    def test_revoked_token_stops_working(self):
        db = FakeDb()
        token = issue(db, EMAIL)
        db.revoke_action_token(token.token)
        with pytest.raises(InvalidActionToken):
            resolve(db, token.token)

    def test_error_does_not_reveal_whether_the_token_ever_existed(self):
        """Distinguishing "expired" from "never existed" would confirm a
        guess for a probing caller."""
        db = FakeDb()
        with pytest.raises(InvalidActionToken) as unknown:
            resolve(db, "never-existed")
        assert "expired" not in str(unknown.value).lower()
