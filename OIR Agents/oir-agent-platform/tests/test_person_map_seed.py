"""Guard: every owner in every real OIR file must have an email.

Owner emails are supplied manually via infra/person-map-seed.csv (the OIR
report has no email columns, and Graph consent was never granted -- ADR
0008). That makes the mapping a static artefact that silently rots: a new
PM/TM/EM appearing in next week's file has no email, so their demands are
ingested but nobody is ever told about them.

This test fails when that happens. It is offline -- it reads the CSV and the
checked-in samples, and never touches Cosmos.
"""
from __future__ import annotations

import csv
import glob
import os

import pytest

from functions.ingest_oir.parser import parse_workbook

_HERE = os.path.dirname(__file__)
_SEED_CSV = os.path.join(_HERE, "..", "infra", "person-map-seed.csv")
_FILES = sorted(glob.glob(os.path.join(_HERE, "..", "..", "Data", "*.xlsx")))

# Must stay in step with infra/seed_person_map.py::NAME_VARIANTS
NAME_VARIANTS = {
    "Nivetha G .": "Nivetha G",
    "Nivetha G.": "Nivetha G",
    "Hardik Sanghavi (Aurora)": "Hardik Sanghavi",
    "Balram": "Balram Choudhary",
}

# Owners that appear ONLY in superseded files and have no email. Accepted
# rather than chased: the platform ingests the newest file, these names are
# absent from it, and inventing an address for an ambiguous first-name-only
# entry is worse than leaving it unresolved.
#   Kaustubh Lele — real name, last seen 6-Aug
#   Santosh       — first name only, no unambiguous match
KNOWN_HISTORICAL_GAPS = {"Kaustubh Lele", "Santosh"}


def _load_seed() -> dict[str, str]:
    with open(_SEED_CSV, encoding="utf-8-sig", newline="") as f:
        return {
            (r["Name"] or "").strip(): (r["Email"] or "").strip()
            for r in csv.DictReader(f)
            if (r.get("Name") or "").strip()
        }


pytestmark = pytest.mark.skipif(
    not os.path.exists(_SEED_CSV) or not _FILES,
    reason="person-map-seed.csv or Data/*.xlsx not present",
)


def _resolvable(name: str, seed: dict[str, str]) -> bool:
    n = (name or "").strip()
    return not n or NAME_VARIANTS.get(n, n) in seed


class TestSeedIntegrity:

    def test_every_row_has_a_plausible_email(self):
        for name, email in _load_seed().items():
            assert "@" in email and email == email.strip().lower(), (
                f"{name}: implausible or unnormalised email {email!r}"
            )

    def test_no_duplicate_emails(self):
        """Two people sharing an address means one of them is wrong."""
        seed = _load_seed()
        by_email: dict[str, list[str]] = {}
        for name, email in seed.items():
            by_email.setdefault(email, []).append(name)
        dupes = {e: n for e, n in by_email.items() if len(n) > 1}
        assert not dupes, f"same email for different people: {dupes}"

    def test_variants_point_at_real_entries(self):
        seed = _load_seed()
        for variant, canonical in NAME_VARIANTS.items():
            assert canonical in seed, f"variant {variant!r} -> missing {canonical!r}"


def _missing_owners(path: str) -> list[str]:
    seed = _load_seed()
    rows = list(parse_workbook(path, source_file=os.path.basename(path)))
    return sorted({
        n.strip()
        for r in rows
        for n in (r.pm_name, r.tm_name, r.em_name, r.dm_name)
        if n and n.strip() and not _resolvable(n, seed)
    })


class TestOwnerCoverage:

    def test_latest_file_is_fully_covered(self):
        """Strict: this is the file the platform actually ingests. Any gap
        here is a demand that gets stored and then silently never chased."""
        missing = _missing_owners(_FILES[-1])
        assert not missing, (
            f"{len(missing)} owner(s) in {os.path.basename(_FILES[-1])} have no email — "
            f"their demands would be ingested but never notified: {missing}. "
            f"Add them to infra/person-map-seed.csv and re-run infra/seed_person_map.py."
        )

    @pytest.mark.parametrize("path", _FILES[:-1], ids=lambda p: os.path.basename(p))
    def test_older_files_have_only_known_gaps(self, path):
        """Looser for superseded files, but still catches anything new."""
        unexpected = [n for n in _missing_owners(path) if n not in KNOWN_HISTORICAL_GAPS]
        assert not unexpected, (
            f"unexpected uncovered owner(s) in {os.path.basename(path)}: {unexpected}. "
            f"Either add them to person-map-seed.csv or, if they have left, to "
            f"KNOWN_HISTORICAL_GAPS with a note."
        )
