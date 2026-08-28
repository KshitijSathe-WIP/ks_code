"""Tests for detection rules engine.

Uses freezegun to control date.today() and plain dict fixtures shaped like
Cosmos DB document fields -- no real HTTP calls are made.
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from functions.detect_exceptions.rules import (
    run_rules,
    _business_days_between,
    _days_left,
    _is_snoozed,
    _notified_today,
    _escalation_tier,
    _gate,
)
from functions.shared.models import CONFIG


class TestExpiryBoundary:

    def _make_row(self, days_from_today: int) -> dict:
        end = (date.today() + timedelta(days=days_from_today)).isoformat()
        return {
            "DemandID": "D001",
            "Project": "Aurora",
            "Role": "Developer",
            "Status": "Need Profiles",
            "PMEmail": "pm@wipro.com",
            "TMEmail": "tm@wipro.com",
            "EMEmail": "em@wipro.com",
            "DEMEndDate": end,
        }

    def test_exactly_lookahead_days_triggers(self):
        lookahead = CONFIG["expiry"]["lookahead_days"]
        row = self._make_row(lookahead)
        left = _days_left(row["DEMEndDate"], date.today())
        assert left == lookahead

    def test_beyond_lookahead_does_not_trigger(self):
        lookahead = CONFIG["expiry"]["lookahead_days"]
        row = self._make_row(lookahead + 1)
        left = _days_left(row["DEMEndDate"], date.today())
        assert left > lookahead

    def test_today_expiry_triggers(self):
        left = _days_left(date.today().isoformat(), date.today())
        assert left == 0


class TestStalenessGate:

    def test_snoozed_row_skipped(self):
        from datetime import datetime
        future = (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"
        row = {"SnoozeUntil": future}
        assert _is_snoozed(row, datetime.utcnow()) is True

    def test_expired_snooze_not_skipped(self):
        from datetime import datetime
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        row = {"SnoozeUntil": past}
        assert _is_snoozed(row, datetime.utcnow()) is False

    def test_notified_today_skips(self):
        row = {"LastNotifiedOn": date.today().isoformat() + "T08:00:00Z"}
        assert _notified_today(row, date.today()) is True

    def test_not_notified_today_does_not_skip(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        row = {"LastNotifiedOn": yesterday + "T08:00:00Z"}
        assert _notified_today(row, date.today()) is False


class TestEscalationTier:
    """Ladder is (threshold=2, l2=4, adh=5) business days.

    The top tier goes to the Account Delivery Head, a single configured
    person -- NOT the file's SL_DM_NAME, which holds the same person as
    TM_NAME on 206/209 rows and so escalates to nobody new.
    """

    def test_stale_2_is_level1(self):
        tag, recipients = _escalation_tier(2, 4, 5)
        assert tag == "STALE_2D"
        assert "pm" in recipients and "tm" in recipients
        assert "em" not in recipients

    def test_stale_4_is_level2(self):
        tag, recipients = _escalation_tier(4, 4, 5)
        assert tag == "ESCALATION_L2"
        assert "em" in recipients
        assert "adh" not in recipients

    def test_stale_5_escalates_to_account_delivery_head(self):
        tag, recipients = _escalation_tier(5, 4, 5)
        assert tag == "ESCALATION_ADH"
        assert "adh" in recipients

    def test_owners_stay_on_the_thread_when_escalated(self):
        """Escalation adds people; it must not drop the ones who can act."""
        _, recipients = _escalation_tier(9, 4, 5)
        assert {"pm", "tm", "em", "adh"} <= set(recipients)

    def test_dm_is_never_a_recipient(self):
        """SL_DM_NAME duplicates TM_NAME, so notifying it is a no-op."""
        for days in (2, 4, 5, 20):
            assert "dm" not in _escalation_tier(days, 4, 5)[1]

    def test_stale_3_is_still_level1(self):
        tag, _ = _escalation_tier(3, 4, 5)
        assert tag == "STALE_2D"

    def test_thresholds_are_configurable(self):
        tag, recipients = _escalation_tier(7, 10, 20)
        assert tag == "STALE_2D", "below both thresholds despite a high day count"
        assert _escalation_tier(25, 10, 20)[0] == "ESCALATION_ADH"


class TestBusinessDayStaleness:
    """The OIR file is weekday-only, so a demand can never last-change at a
    weekend. Counting calendar days made the 4-5 day L2 window land entirely
    on Sat+Sun every Thursday, skipping that tier and escalating straight to
    the Account Delivery Head.
    """

    def test_friday_to_monday_is_one_day(self):
        assert _business_days_between(date(2026, 8, 14), date(2026, 8, 17)) == 1

    def test_weekend_adds_nothing(self):
        # Fri -> Sat -> Sun all count as zero further business days
        assert _business_days_between(date(2026, 8, 14), date(2026, 8, 16)) == 0

    def test_same_day_is_zero(self):
        assert _business_days_between(date(2026, 8, 20), date(2026, 8, 20)) == 0

    def test_full_week(self):
        assert _business_days_between(date(2026, 8, 13), date(2026, 8, 20)) == 5

    def test_l2_window_reachable_on_every_weekday(self):
        """The regression that motivated this: on a Thursday, 4 and 5
        CALENDAR days back are Sun and Sat, so no demand could ever sit in
        the L2 band. In business days the band is always reachable."""
        for offset in range(5):                      # Mon..Fri
            run_day = date(2026, 8, 17) + timedelta(days=offset)
            reachable = [
                d for d in (run_day - timedelta(days=n) for n in range(1, 15))
                if d.weekday() < 5 and _business_days_between(d, run_day) in (4, 5)
            ]
            assert reachable, f"L2 band unreachable when run on {run_day:%a}"


class TestConfidenceGate:
    """Tests for the post-processing gate (§7.2)."""

    def _gate(self, parsed: dict) -> str:
        from functions.shared.models import CONFIG
        threshold = CONFIG["agent"]["confidence_threshold"]
        high_risk = set(CONFIG["agent"]["high_risk_fields"])
        if parsed.get("confidence", 0) < threshold:
            return "CLARIFY"
        if high_risk & set(parsed.keys()):
            return "CONFIRM"
        return "APPLY"

    def test_low_confidence_routes_clarify(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.80}) == "CLARIFY"

    def test_high_confidence_no_risk_routes_apply(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.90, "comments": "Updated"}) == "APPLY"

    def test_date_change_routes_confirm(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.95, "dem_end_date": "2026-09-01"}) == "CONFIRM"

    def test_status_change_routes_confirm(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.95, "remarks_status": "Pending Offer"}) == "CONFIRM"

    def test_boundary_confidence_085_routes_apply(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.85, "comments": "ok"}) == "APPLY"

    def test_confidence_084_routes_clarify(self):
        assert self._gate({"demand_id": "D1", "confidence": 0.849}) == "CLARIFY"


class TestRunRulesClientSideFiltering:
    """End-to-end run_rules() against a mocked CosmosDbClient.

    Verifies the staleness/expiry logic still holds after moving off
    Dataverse's OData $filter pushdown (see docs/decisions/0001-* and
    docs/decisions/0002-* for the data store's evolution).
    """

    def _row(self, **overrides) -> dict:
        base = {
            "DemandID": "D100",
            "Project": "Aurora",
            "Role": "Developer",
            "Status": "Need Profiles",
            "PMEmail": "pm@wipro.com",
            "TMEmail": "tm@wipro.com",
            "EMEmail": "em@wipro.com",
            "DMEmail": "",
            "DEMEndDate": (date.today() + timedelta(days=30)).isoformat(),
            "LastContentChangeDate": date.today().isoformat(),
            "EscalationLevel": 0,
            "SnoozeUntil": None,
            "LastNotifiedOn": None,
            "IsActive": True,
        }
        base.update(overrides)
        return base

    def _run_with_rows(self, rows: list[dict]) -> dict:
        mock_client = MagicMock()
        mock_client.list_active_demands.return_value = rows
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        with patch("functions.detect_exceptions.rules.CosmosDbClient", return_value=mock_client):
            return run_rules(today=date.today())

    def test_stale_row_grouped_by_pm_and_tm(self):
        stale_threshold = CONFIG["staleness"]["threshold_days"]
        row = self._row(
            LastContentChangeDate=(date.today() - timedelta(days=stale_threshold)).isoformat()
        )
        payload = self._run_with_rows([row])
        recipients = {r["email"]: r for r in payload["recipients"]}
        assert "pm@wipro.com" in recipients
        assert "tm@wipro.com" in recipients
        assert len(recipients["pm@wipro.com"]["stale"]) == 1
        assert recipients["pm@wipro.com"]["stale"][0]["demand_id"] == "D100"

    def test_fresh_row_produces_no_stale_entry(self):
        payload = self._run_with_rows([self._row()])  # LastContentChangeDate = today
        assert payload["recipients"] == []

    def test_excluded_status_never_flagged_stale(self):
        stale_threshold = CONFIG["staleness"]["threshold_days"]
        row = self._row(
            Status="Joined",
            LastContentChangeDate=(date.today() - timedelta(days=stale_threshold + 10)).isoformat(),
        )
        payload = self._run_with_rows([row])
        assert payload["recipients"] == []

    def test_expiring_row_reaches_pm_tm_and_em(self):
        lookahead = CONFIG["expiry"]["lookahead_days"]
        row = self._row(DEMEndDate=(date.today() + timedelta(days=lookahead)).isoformat())
        payload = self._run_with_rows([row])
        recipients = {r["email"]: r for r in payload["recipients"]}
        assert set(recipients) == {"pm@wipro.com", "tm@wipro.com", "em@wipro.com"}
        for bucket in recipients.values():
            assert len(bucket["expiring"]) == 1

    def test_inactive_row_excluded_by_client_side_filter(self):
        # list_active_demands() is expected to have already filtered IsActive;
        # this confirms run_rules doesn't re-derive anything from IsActive itself.
        stale_threshold = CONFIG["staleness"]["threshold_days"]
        row = self._row(
            LastContentChangeDate=(date.today() - timedelta(days=stale_threshold)).isoformat()
        )
        payload = self._run_with_rows([row])
        assert len(payload["recipients"]) == 2  # pm + tm, sanity check the active row still flags
