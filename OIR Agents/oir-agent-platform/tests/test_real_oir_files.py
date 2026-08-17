"""Parse the real TD Bank OIR files, not synthetic fixtures.

Every header/sheet assumption in this codebase was originally written from
the spec, and none of it matched the actual reports — the parser could not
read a single real file (wrong sheet-name rule, a required `role` column
that doesn't exist, `status` binding to `Category`). Synthetic fixtures
happily passed throughout, because they encoded the same wrong assumptions.

These tests run against the checked-in samples in Data/ so that drift
between the spec and the real report is caught here rather than in
production. They skip (rather than fail) if the samples aren't present, so
the suite still runs in a clean checkout.
"""
from __future__ import annotations

import glob
import os

import pytest

from functions.ingest_oir.parser import parse_workbook

_DATA_GLOB = os.path.join(os.path.dirname(__file__), "..", "..", "Data", "*.xlsx")
_FILES = sorted(glob.glob(_DATA_GLOB))

pytestmark = pytest.mark.skipif(not _FILES, reason="no sample OIR files in Data/")


@pytest.mark.parametrize("path", _FILES, ids=lambda p: os.path.basename(p))
class TestRealFileParsing:

    def test_file_parses_without_error(self, path):
        rows = list(parse_workbook(path, source_file=os.path.basename(path)))
        assert rows, "parsed zero rows from a real OIR file"

    def test_required_business_fields_populated(self, path):
        """A row the rules engine can actually act on."""
        rows = list(parse_workbook(path, source_file=os.path.basename(path)))
        sample = rows[0]
        assert sample.demand_id
        assert sample.project
        assert sample.remarks_status   # drives staleness/validation
        assert sample.pm_name

    def test_demand_ids_are_unique(self, path):
        """Cosmos uses DemandID as both id and partition key, so duplicates
        inside one file would silently overwrite each other."""
        ids = [r.demand_id for r in parse_workbook(path, source_file=os.path.basename(path))]
        assert len(ids) == len(set(ids)), "duplicate DemandIDs within one file"

    def test_pivot_sheets_are_not_selected(self, path):
        """'OIR Pivot' also starts with 'OIR' — picking it would yield
        summary rows instead of demands."""
        rows = list(parse_workbook(path, source_file=os.path.basename(path)))
        # Only the data sheet carries RLS-style parent requisition ids.
        assert any(r.requisition_id.upper().startswith("RLS") for r in rows)

    def test_demand_id_is_the_position_not_the_requisition(self, path):
        """demand_id must be SR_ID_2 (per position), not RLS_ID (per
        requisition) — several positions share one requisition, so keying on
        RLS_ID silently collapses them in Cosmos."""
        rows = list(parse_workbook(path, source_file=os.path.basename(path)))
        assert len({r.demand_id for r in rows}) > len({r.requisition_id for r in rows}), (
            "demand_id looks like it is keyed on the requisition, not the position"
        )


class TestOwnerEmailAvailability:
    """Tracks the ADR 0008 gap: the OIR file carries owner *names* but no
    owner *emails*, so nobody can be notified yet.

    When the upstream report gains PM_EMAIL/TM_EMAIL/EM_EMAIL columns, the
    xfail below starts passing — which is the signal that notification can
    be switched on. Update this test at that point rather than deleting it.
    """

    @pytest.mark.skipif(not _FILES, reason="no sample OIR files in Data/")
    def test_owner_names_are_present(self):
        rows = list(parse_workbook(_FILES[-1], source_file="latest"))
        assert any(r.pm_name for r in rows), "expected PM names in the real file"

    @pytest.mark.xfail(
        reason="OIR file has no PM/TM/EM email columns yet -- see ADR 0008",
        strict=False,
    )
    @pytest.mark.skipif(not _FILES, reason="no sample OIR files in Data/")
    def test_owner_emails_are_present(self):
        rows = list(parse_workbook(_FILES[-1], source_file="latest"))
        assert any(r.pm_email for r in rows), (
            "no PM_EMAIL column in the OIR file -- demands cannot be notified"
        )
