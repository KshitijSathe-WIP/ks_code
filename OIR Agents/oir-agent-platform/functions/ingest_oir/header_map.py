"""Header normalisation and mapping for the OIR Excel file.

Column positions drift between files; we map by normalised header text,
never by index. If a required header cannot be resolved, we raise so the
caller can abort the run — we never silently default to null.

Aliases were reconciled against the real TD Bank OIR files (see
docs/decisions/0008-owner-emails-from-the-oir-file.md). The original set
was written from the spec and matched none of them, so ingestion could
never have run against production data.
"""
from __future__ import annotations

import re
from typing import Dict

# Mapping: canonical field name → list of accepted header aliases (normalised).
# Real TD Bank column names are marked (real); the others are kept as
# tolerated variants in case the upstream report is renamed.
HEADER_ALIASES: Dict[str, list[str]] = {
    # SR_ID_2 is the per-POSITION id and the only column that is actually
    # unique: RLS_ID is the parent *requisition* and repeats (235 rows ->
    # 160 ids in one real file), and SR_ID_1 collides once per file. Keying
    # on RLS_ID would have silently collapsed ~35% of demands into whichever
    # row happened to be written last, each carrying a different status.
    "demand_id":      ["sr id 2", "sr id 1", "sr id", "demand id", "demandid", "rls id"],
    "requisition_id": ["rls id"],   # parent requisition, kept for traceability
    "project":        ["project", "project name"],                             # real: Project / Project Name
    "sldu":           ["sldu", "service line delivery unit", "sld unit"],      # real: SLDU
    "role":           ["role name", "role", "jobcode 2", "demand type"],       # NOT present in real files
    "skill":          ["essential skill", "skill"],                            # real: ESSENTIAL_SKILL
    "status":         ["current status", "status"],                            # real: CURRENT_STATUS
    "pm_name":        ["pm name", "sl pm name", "pm"],                         # real: PM_NAME
    "tm_name":        ["tm name", "tm"],                                       # real: TM_NAME
    "em_name":        ["em name", "em"],                                       # real: EM
    "dm_name":        ["sl dm name", "dm name", "dm"],                         # real: SL_DM_NAME
    "dem_start_date": ["dem st date", "dem start date", "demand start date"],  # real: DEM_ST_DATE
    "dem_end_date":   ["dem end date", "demand end date"],                     # real: DEM_END_DATE
    "comments":       ["comments", "comment"],                                 # real: Comments
    # real: "Remarks" up to 11-Aug-2026, renamed to "Remark" from 12-Aug.
    # Both accepted -- this field is required, so the rename broke ingestion
    # outright until it was caught by tests/test_real_oir_files.py.
    "remarks_status": ["remarks", "remark", "remarks status"],

    # Owner email columns. NOT present in the OIR file today -- these exist so
    # that the moment the upstream report adds them, ingestion picks them up
    # with no code change. Until then owner emails stay blank and those
    # demands cannot be notified. See ADR 0008.
    "pm_email":       ["pm email", "pm mail", "pm email id", "pm e mail"],
    "tm_email":       ["tm email", "tm mail", "tm email id", "tm e mail"],
    "em_email":       ["em email", "em mail", "em email id", "em e mail"],
    "dm_email":       ["dm email", "sl dm email", "dm mail", "dm email id"],
}

# Fields that MUST be present; absence aborts ingestion.
#
# `role` is deliberately NOT required: the real OIR file has no role column
# at all. ingest falls back to `skill` (ESSENTIAL_SKILL), which is what a
# reader would actually recognise the demand by.
REQUIRED_FIELDS = {
    "demand_id",
    "project",
    "status",
    "pm_name",
    "dem_end_date",
    "comments",
    "remarks_status",
}


def _normalise(header: str) -> str:
    """Lower-case, collapse non-alphanumeric runs to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", str(header).strip().lower()).strip()


def build_column_map(raw_headers: list[str]) -> Dict[str, int]:
    """Return {canonical_field: column_index} for all resolvable headers.

    Where several headers match the same canonical field, the alias listed
    first wins — so the real column name is preferred over a tolerated
    variant regardless of column order.

    Raises ValueError listing any required field that could not be mapped.
    """
    normalised_headers = [(_normalise(h), i) for i, h in enumerate(raw_headers)]
    by_name: Dict[str, int] = {}
    for norm_h, idx in normalised_headers:
        by_name.setdefault(norm_h, idx)   # first occurrence wins

    col_map: Dict[str, int] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:             # alias precedence, not column order
            if alias in by_name:
                col_map[canonical] = by_name[alias]
                break

    missing = REQUIRED_FIELDS - set(col_map.keys())
    if missing:
        raise ValueError(
            f"Required header(s) not found: {sorted(missing)}. "
            f"Headers seen in file: {[h for h, _ in normalised_headers]}"
        )

    return col_map
