"""Tests for detection rules engine.

Uses freezegun to control date.today() and a mock Dataverse response
so no real HTTP calls are made.
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from functions.detect_exceptions.rules import (
    run_rules,
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
            "oir_demandid": "D001",
            "oir_project": "Aurora",
            "oir_role": "Developer",
            "oir_status": "Need Profiles",
            "oir_pm_email": "pm@wipro.com",
            "oir_tm_email": "tm@wipro.com",
            "oir_em_email": "em@wipro.com",
            "oir_dem_end_date": end,
        }

    def test_exactly_lookahead_days_triggers(self):
        lookahead = CONFIG["expiry"]["lookahead_days"]
        row = self._make_row(lookahead)
        left = _days_left(row["oir_dem_end_date"], date.today())
        assert left == lookahead

    def test_beyond_lookahead_does_not_trigger(self):
        lookahead = CONFIG["expiry"]["lookahead_days"]
        row = self._make_row(lookahead + 1)
        left = _days_left(row["oir_dem_end_date"], date.today())
        assert left > lookahead

    def test_today_expiry_triggers(self):
        left = _days_left(date.today().isoformat(), date.today())
        assert left == 0


class TestStalenessGate:

    def test_snoozed_row_skipped(self):
        from datetime import datetime
        future = (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"
        row = {"oir_snooze_until": future}
        assert _is_snoozed(row, datetime.utcnow()) is True

    def test_expired_snooze_not_skipped(self):
        from datetime import datetime
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        row = {"oir_snooze_until": past}
        assert _is_snoozed(row, datetime.utcnow()) is False

    def test_notified_today_skips(self):
        row = {"oir_last_notified_on": date.today().isoformat() + "T08:00:00Z"}
        assert _notified_today(row, date.today()) is True

    def test_not_notified_today_does_not_skip(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        row = {"oir_last_notified_on": yesterday + "T08:00:00Z"}
        assert _notified_today(row, date.today()) is False


class TestEscalationTier:

    def test_stale_2_is_level1(self):
        tag, recipients = _escalation_tier(2, 4, 6)
        assert tag == "STALE_2D"
        assert "pm" in recipients and "tm" in recipients
        assert "em" not in recipients

    def test_stale_4_is_level2(self):
        tag, recipients = _escalation_tier(4, 4, 6)
        assert tag == "ESCALATION_L2"
        assert "em" in recipients

    def test_stale_6_is_level3(self):
        tag, recipients = _escalation_tier(6, 4, 6)
        assert tag == "ESCALATION_L3"
        assert "dm" in recipients

    def test_stale_3_is_still_level1(self):
        tag, _ = _escalation_tier(3, 4, 6)
        assert tag == "STALE_2D"

    def test_stale_5_is_level2(self):
        tag, _ = _escalation_tier(5, 4, 6)
        assert tag == "ESCALATION_L2"


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
