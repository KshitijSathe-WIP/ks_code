"""Tests for the shared update logic behind both write paths.

apply_demand_update() is what the HTTP endpoint and the web form both call.
The behaviours worth pinning are the ones that quietly corrupt the staleness
model if they drift: re-submitting an unchanged value must NOT look like
activity, and a genuine change must reset the escalation ladder.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from functions.apply_update.core import (
    apply_demand_update,
    validate_future_date,
    validate_status,
)
from functions.shared.models import OIRDemand, VALID_STATUSES, ValidationError

A_STATUS = sorted(VALID_STATUSES)[0]
ANOTHER_STATUS = sorted(VALID_STATUSES)[1]


def make_demand(**overrides) -> OIRDemand:
    base = dict(
        demand_id="D1", project="Aurora", sldu="S", role="Dev", skill="Dev",
        status="04. Pending Profile", pm_name="Pat", pm_email="pat@wipro.com",
        tm_name="Tim", tm_email="tim@wipro.com", em_name="Em",
        em_email="em@wipro.com", dem_start_date=None,
        dem_end_date=date(2026, 12, 31), comments="old comment",
        remarks_status=A_STATUS, comments_hash="h",
        last_content_change_date=date(2026, 8, 1), stale_days=9,
        last_notified_on=None, escalation_level=3, snooze_until=None,
        source_file="f.xlsx", first_seen_date=date(2026, 8, 1), is_active=True,
    )
    base.update(overrides)
    return OIRDemand(**base)


class FakeDb:
    def __init__(self, demand=None):
        self.demand = demand if demand is not None else make_demand()
        self.upserts: list[dict] = []
        self.logs: list = []

    def get_demand(self, demand_id):
        return self.demand if self.demand and self.demand.demand_id == demand_id else None

    def upsert_demand(self, doc):
        self.upserts.append(doc)

    def append_log(self, entry):
        self.logs.append(entry)

    def last(self):
        return self.upserts[-1]


class TestNoOpSubmission:
    """Re-saving the same values must not make a stale demand look tended."""

    def test_identical_comment_is_not_an_update(self):
        db = FakeDb()
        result = apply_demand_update(db, "D1", "pat@wipro.com",
                                     comments="old comment")
        assert result["status"] == "no_change"
        assert db.upserts == [], "must not write, or staleness resets"

    def test_identical_status_is_not_an_update(self):
        db = FakeDb()
        result = apply_demand_update(db, "D1", "pat@wipro.com",
                                     remarks_status=A_STATUS)
        assert result["status"] == "no_change"
        assert db.upserts == []

    def test_nothing_supplied_is_not_an_update(self):
        db = FakeDb()
        assert apply_demand_update(db, "D1", "pat@wipro.com")["status"] == "no_change"


class TestRealChange:

    def test_staleness_clock_resets(self):
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com", comments="profile shared")
        assert db.last()["LastContentChangeDate"] == date.today().isoformat()

    def test_escalation_ladder_resets(self):
        """Otherwise the owner keeps being escalated after responding."""
        db = FakeDb(make_demand(escalation_level=3))
        apply_demand_update(db, "D1", "pat@wipro.com", comments="profile shared")
        assert db.last()["EscalationLevel"] == 0

    def test_notification_and_snooze_state_cleared(self):
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com", comments="profile shared")
        assert db.last()["LastNotifiedOn"] is None
        assert db.last()["SnoozeUntil"] is None

    def test_hash_is_recomputed_from_the_new_values(self):
        from functions.ingest_oir.hashing import content_hash
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com", comments="profile shared")
        assert db.last()["CommentsHash"] == content_hash("profile shared", A_STATUS)

    def test_every_field_change_is_logged(self):
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com",
                            comments="new", remarks_status=ANOTHER_STATUS)
        assert {log.field_changed for log in db.logs} == {"comments", "remarks_status"}

    def test_log_records_before_and_after(self):
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com", comments="new")
        log = db.logs[0]
        assert log.value_before == "old comment" and log.value_after == "new"

    def test_channel_is_recorded(self):
        """So a web-form update is distinguishable from an API one in audit."""
        db = FakeDb()
        apply_demand_update(db, "D1", "pat@wipro.com", comments="new",
                            channel="WEB_FORM")
        assert db.logs[0].channel == "WEB_FORM"


class TestValidation:

    def test_unknown_status_rejected(self):
        with pytest.raises(ValidationError):
            validate_status("Definitely Not A Status")

    def test_known_status_accepted(self):
        validate_status(A_STATUS)

    def test_past_end_date_rejected(self):
        with pytest.raises(ValidationError):
            validate_future_date((date.today() - timedelta(days=1)).isoformat())

    def test_malformed_date_rejected(self):
        with pytest.raises(ValidationError):
            validate_future_date("31-12-2026")

    def test_bad_status_does_not_write_anything(self):
        """A rejected submission must leave the demand untouched."""
        db = FakeDb()
        with pytest.raises(ValidationError):
            apply_demand_update(db, "D1", "pat@wipro.com", remarks_status="nope")
        assert db.upserts == []

    def test_missing_demand_rejected(self):
        with pytest.raises(ValidationError, match="not found"):
            apply_demand_update(FakeDb(), "NOPE", "pat@wipro.com", comments="x")
