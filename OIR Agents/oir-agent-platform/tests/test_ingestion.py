"""Tests for the core ingestion logic (functions/ingest_oir/ingestion.py).

This is where staleness is actually decided, and the rules are easy to get
backwards in a way nothing else would catch:

  * content UNCHANGED -> LastContentChangeDate must be PRESERVED, so the
    demand keeps ageing and eventually trips the staleness rule
  * content CHANGED   -> date moves forward, escalation resets, and the
    notification clock is cleared so the owner can be chased again

Get the first one wrong and every demand looks freshly updated forever, so
the platform never notifies anyone. Get the second wrong and demands keep
escalating after someone has already responded. Both failures are silent.

Runs entirely against a fake client -- no Cosmos, no network.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from functions.ingest_oir.ingestion import (
    IngestionAborted,
    ingest_rows,
    resolve_email,
)
from functions.ingest_oir.parser import RawRow
from functions.shared.models import OIRDemand


def make_row(**overrides) -> RawRow:
    base = dict(
        demand_id="D1", project="Aurora", sldu="SLDU", role="Dev", skill="Dev",
        status="04. Pending Profile", pm_name="Pat M", tm_name="Tim M",
        em_name="Em M", dem_start_date=None, dem_end_date=date(2026, 12, 31),
        comments="Need profile", remarks_status="Need Profiles",
        source_file="f.xlsx",
    )
    base.update(overrides)
    return RawRow(**base)


def make_existing(**overrides) -> OIRDemand:
    base = dict(
        demand_id="D1", project="Aurora", sldu="SLDU", role="Dev", skill="Dev",
        status="04. Pending Profile", pm_name="Pat M", pm_email="pat@wipro.com",
        tm_name="Tim M", tm_email="tim@wipro.com", em_name="Em M",
        em_email="em@wipro.com", dem_start_date=None,
        dem_end_date=date(2026, 12, 31), comments="Need profile",
        remarks_status="Need Profiles", comments_hash="",
        last_content_change_date=date(2026, 8, 1), stale_days=0,
        last_notified_on=None, escalation_level=0, snooze_until=None,
        source_file="f.xlsx", first_seen_date=date(2026, 8, 1), is_active=True,
    )
    base.update(overrides)
    return OIRDemand(**base)


class FakeDb:
    """Minimal stand-in for CosmosDbClient."""

    def __init__(self, existing: dict[str, OIRDemand] | None = None,
                 emails: dict[str, str] | None = None,
                 fail_on: set[str] | None = None):
        self._existing = existing or {}
        self._emails = emails or {}
        self._fail_on = fail_on or set()
        self.upserts: list[dict] = []
        self.snapshots: list[dict] = []
        self.deactivated_with: set[str] | None = None

    def get_demand(self, demand_id):
        if demand_id in self._fail_on:
            raise RuntimeError("boom")
        return self._existing.get(demand_id)

    def upsert_demand(self, doc):
        self.upserts.append(doc)

    def insert_snapshot(self, doc):
        self.snapshots.append(doc)

    def get_cached_email(self, name):
        return self._emails.get(name)

    def cache_email(self, name, email):
        self._emails[name] = email

    def deactivate_missing(self, seen_ids):
        self.deactivated_with = set(seen_ids)
        return 0

    def last_upsert(self):
        return self.upserts[-1]


TODAY = date(2026, 8, 14)


class TestNewDemand:

    def test_dates_and_counters_initialised(self):
        db = FakeDb()
        stats = ingest_rows([make_row()], TODAY, db)
        doc = db.last_upsert()
        assert doc["LastContentChangeDate"] == TODAY.isoformat()
        assert doc["FirstSeenDate"] == TODAY.isoformat()
        assert doc["EscalationLevel"] == 0
        assert doc["IsActive"] is True
        assert stats.rows_processed == 1
        assert stats.rows_changed == 1

    def test_snapshot_appended(self):
        db = FakeDb()
        ingest_rows([make_row()], TODAY, db)
        assert len(db.snapshots) == 1
        assert db.snapshots[0]["SnapshotDate"] == TODAY.isoformat()
        assert db.snapshots[0]["DemandID"] == "D1"

    def test_requisition_id_preserved(self):
        db = FakeDb()
        ingest_rows([make_row(requisition_id="RLS-123")], TODAY, db)
        assert db.last_upsert()["RequisitionID"] == "RLS-123"


class TestUnchangedContent:
    """The critical case: nothing changed, so the demand must keep ageing."""

    def _ingest_unchanged(self, **existing_overrides):
        row = make_row()
        from functions.ingest_oir.hashing import content_hash
        same_hash = content_hash(row.comments, row.remarks_status)
        existing = make_existing(comments_hash=same_hash, **existing_overrides)
        db = FakeDb(existing={"D1": existing})
        stats = ingest_rows([row], TODAY, db)
        return db.last_upsert(), stats

    def test_last_content_change_date_is_preserved(self):
        doc, _ = self._ingest_unchanged()
        assert doc["LastContentChangeDate"] == "2026-08-01", (
            "unchanged content must keep the old date, or nothing is ever stale"
        )

    def test_not_counted_as_changed(self):
        _, stats = self._ingest_unchanged()
        assert stats.rows_changed == 0

    def test_escalation_level_preserved(self):
        doc, _ = self._ingest_unchanged(escalation_level=2)
        assert doc["EscalationLevel"] == 2

    def test_last_notified_on_preserved(self):
        """Otherwise the owner is re-notified every single day."""
        doc, _ = self._ingest_unchanged(last_notified_on=datetime(2026, 8, 13, 9, 0))
        assert doc["LastNotifiedOn"].startswith("2026-08-13")

    def test_snapshot_still_appended(self):
        """History records every day, changed or not."""
        row = make_row()
        from functions.ingest_oir.hashing import content_hash
        existing = make_existing(comments_hash=content_hash(row.comments, row.remarks_status))
        db = FakeDb(existing={"D1": existing})
        ingest_rows([row], TODAY, db)
        assert len(db.snapshots) == 1


class TestChangedContent:

    def _ingest_changed(self, **existing_overrides):
        existing = make_existing(comments_hash="stale-hash-does-not-match",
                                 **existing_overrides)
        db = FakeDb(existing={"D1": existing})
        stats = ingest_rows([make_row(comments="Profile shared")], TODAY, db)
        return db.last_upsert(), stats

    def test_date_moves_forward(self):
        doc, _ = self._ingest_changed()
        assert doc["LastContentChangeDate"] == TODAY.isoformat()

    def test_escalation_resets(self):
        doc, _ = self._ingest_changed(escalation_level=3)
        assert doc["EscalationLevel"] == 0

    def test_notification_clock_cleared(self):
        doc, _ = self._ingest_changed(last_notified_on=datetime(2026, 8, 13, 9, 0))
        assert doc["LastNotifiedOn"] is None

    def test_counted_as_changed(self):
        _, stats = self._ingest_changed()
        assert stats.rows_changed == 1


class TestEmailResolution:

    def test_file_column_wins(self):
        db = FakeDb(emails={"Pat M": "cached@wipro.com"})
        ingest_rows([make_row(pm_email="fromfile@wipro.com")], TODAY, db)
        assert db.last_upsert()["PMEmail"] == "fromfile@wipro.com"

    def test_person_map_used_when_file_has_none(self):
        db = FakeDb(emails={"Pat M": "cached@wipro.com"})
        ingest_rows([make_row()], TODAY, db)
        assert db.last_upsert()["PMEmail"] == "cached@wipro.com"

    def test_existing_email_not_blanked(self):
        """A resolution failure must not erase an address we already had."""
        existing = make_existing(comments_hash="different", pm_email="known@wipro.com")
        db = FakeDb(existing={"D1": existing})   # no emails available
        ingest_rows([make_row()], TODAY, db)
        assert db.last_upsert()["PMEmail"] == "known@wipro.com"

    def test_unresolvable_owner_counted(self):
        db = FakeDb()
        stats = ingest_rows([make_row()], TODAY, db)
        assert stats.rows_without_owner == 1

    def test_graph_consulted_only_as_last_resort(self):
        class Graph:
            def __init__(self):
                self.calls = []

            def resolve_email(self, name):
                self.calls.append(name)
                return "graph@wipro.com"

        graph = Graph()
        db = FakeDb(emails={"Pat M": "cached@wipro.com"})
        assert resolve_email("Pat M", "file@wipro.com", graph, db) == "file@wipro.com"
        assert resolve_email("Pat M", "", graph, db) == "cached@wipro.com"
        assert graph.calls == [], "Graph must not be called when file/cache suffice"
        assert resolve_email("New Person", "", graph, db) == "graph@wipro.com"
        assert graph.calls == ["New Person"]
        assert db.get_cached_email("New Person") == "graph@wipro.com", "should be cached"


class TestDeactivation:

    def test_seen_ids_passed_through(self):
        db = FakeDb()
        ingest_rows([make_row(demand_id="A"), make_row(demand_id="B")], TODAY, db)
        assert db.deactivated_with == {"A", "B"}

    def test_can_be_disabled_for_backfill(self):
        """Replaying old files must not retire demands added later."""
        db = FakeDb()
        ingest_rows([make_row()], TODAY, db, deactivate_missing=False)
        assert db.deactivated_with is None


class TestFailureHandling:

    def test_empty_input_rejected(self):
        with pytest.raises(ValueError):
            ingest_rows([], TODAY, FakeDb())

    def test_isolated_error_does_not_abort(self):
        rows = [make_row(demand_id=f"D{i}") for i in range(10)]
        db = FakeDb(fail_on={"D3"})
        stats = ingest_rows(rows, TODAY, db)
        assert stats.rows_errored == 1
        assert stats.rows_processed == 9

    def test_aborts_past_error_threshold(self):
        rows = [make_row(demand_id=f"D{i}") for i in range(10)]
        db = FakeDb(fail_on={f"D{i}" for i in range(5)})
        with pytest.raises(IngestionAborted):
            ingest_rows(rows, TODAY, db)


class TestIdempotency:

    def test_reingesting_the_same_file_changes_nothing(self):
        row = make_row()
        from functions.ingest_oir.hashing import content_hash

        db = FakeDb()
        ingest_rows([row], TODAY, db)
        first = db.last_upsert()

        # Second pass: the demand now exists with the same hash
        existing = make_existing(
            comments_hash=content_hash(row.comments, row.remarks_status),
            last_content_change_date=date.fromisoformat(first["LastContentChangeDate"]),
        )
        db2 = FakeDb(existing={"D1": existing})
        stats = ingest_rows([row], TODAY, db2)

        assert stats.rows_changed == 0
        assert db2.last_upsert()["LastContentChangeDate"] == first["LastContentChangeDate"]
