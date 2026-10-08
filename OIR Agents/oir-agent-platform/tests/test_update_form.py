"""Tests for the update form.

The form is reachable anonymously -- it has to be, since recipients arrive
from an email with no key and no usable Microsoft identity (ADR 0010) -- so
the token in the link is the only thing standing between a stranger and the
demand data. These tests are mostly about that boundary: a token identifies
one person, and that person sees and writes only their own demands.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from functions.shared.action_token import ActionToken
from functions.update_form import _demands_for, _render_form, _render_results

MINE = "pat@wipro.com"
SOMEONE_ELSE = "stranger@wipro.com"


def demand(demand_id, pm=MINE, tm="tim@wipro.com", em="em@wipro.com", stale=5):
    return {
        "DemandID": demand_id, "Project": "Aurora", "Role": "Dev",
        "Status": "04. Pending Profile", "Comments": "old", "RemarksStatus": "",
        "PMEmail": pm, "TMEmail": tm, "EMEmail": em, "StaleDays": stale,
    }


class FakeDb:
    def __init__(self, demands):
        self._demands = demands

    def list_active_demands(self):
        return list(self._demands)


class TestVisibility:

    def test_shows_demands_i_own_as_pm(self):
        db = FakeDb([demand("D1")])
        assert [d["DemandID"] for d in _demands_for(db, MINE)] == ["D1"]

    def test_shows_demands_i_own_as_tm_or_em(self):
        db = FakeDb([demand("D1", pm="other@wipro.com", tm=MINE),
                     demand("D2", pm="other@wipro.com", tm="t@wipro.com", em=MINE)])
        assert {d["DemandID"] for d in _demands_for(db, MINE)} == {"D1", "D2"}

    def test_hides_demands_belonging_to_others(self):
        """The core boundary: one person's link is not a window onto the
        whole account."""
        db = FakeDb([demand("MINE"),
                     demand("THEIRS", pm="a@wipro.com", tm="b@wipro.com", em="c@wipro.com")])
        visible = {d["DemandID"] for d in _demands_for(db, MINE)}
        assert visible == {"MINE"}
        assert "THEIRS" not in visible

    def test_a_stranger_sees_nothing(self):
        db = FakeDb([demand("D1"), demand("D2")])
        assert _demands_for(db, SOMEONE_ELSE) == []

    def test_most_stale_first(self):
        db = FakeDb([demand("D1", stale=2), demand("D9", stale=11), demand("D5", stale=6)])
        assert [d["DemandID"] for d in _demands_for(db, MINE)] == ["D9", "D5", "D1"]


class TestRendering:

    def test_lists_each_demand_with_its_id(self):
        html = _render_form(MINE, [demand("D1"), demand("D2")])
        assert "D1" in html and "D2" in html

    def test_empty_state_is_not_an_error(self):
        html = _render_form(MINE, [])
        assert "Nothing outstanding" in html

    def test_escapes_untrusted_field_content(self):
        """Comments come from a spreadsheet people type into freely."""
        d = demand("D1")
        d["Comments"] = '"><script>alert(1)</script>'
        html = _render_form(MINE, [d])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_project_names_too(self):
        d = demand("D1")
        d["Project"] = "<img src=x onerror=alert(1)>"
        assert "<img src=x" not in _render_form(MINE, [d])

    def test_singular_plural_day_wording(self):
        assert "1 business day without" in _render_form(MINE, [demand("D1", stale=1)])
        assert "2 business days without" in _render_form(MINE, [demand("D1", stale=2)])

    def test_results_page_reports_partial_success(self):
        html = _render_results(MINE, [
            {"demand_id": "D1", "ok": True, "message": "Updated."},
            {"demand_id": "D2", "ok": False, "message": "Bad status"},
        ])
        assert "1 of 2" in html
        assert "D2" in html and "Bad status" in html

    def test_results_escape_messages(self):
        html = _render_results(MINE, [
            {"demand_id": "<b>x</b>", "ok": False, "message": "<i>no</i>"}])
        assert "<b>" not in html and "<i>" not in html


class TestTokenExpiryBoundary:
    """resolve() is covered in test_action_token; this pins the property the
    form depends on -- an expired link must not authenticate anyone."""

    def test_expired_token_is_expired(self):
        t = ActionToken("x", MINE, datetime.now(timezone.utc) - timedelta(seconds=1))
        assert t.is_expired

    def test_live_token_is_not(self):
        t = ActionToken("x", MINE, datetime.now(timezone.utc) + timedelta(days=1))
        assert not t.is_expired
