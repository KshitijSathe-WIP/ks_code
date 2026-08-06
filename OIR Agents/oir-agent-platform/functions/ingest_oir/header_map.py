"""Header normalisation and mapping for the OIR Excel file.

Column positions drift between files; we map by normalised header text,
never by index. If a required header cannot be resolved, we raise so the
caller can abort the run — we never silently default to null.
"""
from __future__ import annotations

import re
from typing import Dict

# Mapping: canonical field name → list of accepted header aliases (normalised)
HEADER_ALIASES: Dict[str, list[str]] = {
    "demand_id":      ["demand id", "demand id", "demandid", "sr id", "rls id"],
    "project":        ["project", "project name"],
    "sldu":           ["sldu", "service line delivery unit", "sld unit"],
    "role":           ["role name", "role name", "role"],
    "skill":          ["skill", "essential skill"],
    "status":         ["status", "category"],
    "pm_name":        ["pm name", "pm name", "sl pm name", "pm"],
    "tm_name":        ["tm name", "tm name", "tm"],
    "em_name":        ["em name", "em name", "em"],
    "dem_start_date": ["dem start date", "dem start date", "demand start date"],
    "dem_end_date":   ["dem end date", "dem end date", "demand end date"],
    "comments":       ["comments", "comment"],
    "remarks_status": ["remarks status", "remarks status", "remarks"],
}

# Fields that MUST be present; absence aborts ingestion
REQUIRED_FIELDS = {"demand_id", "project", "role", "status", "pm_name", "dem_end_date", "comments", "remarks_status"}


def _normalise(header: str) -> str:
    """Lower-case, collapse non-alphanumeric runs to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", str(header).strip().lower()).strip()


def build_column_map(raw_headers: list[str]) -> Dict[str, int]:
    """Return {canonical_field: column_index} for all resolvable headers.

    Raises ValueError listing any required field that could not be mapped.
    """
    normalised_headers = [(_normalise(h), i) for i, h in enumerate(raw_headers)]

    col_map: Dict[str, int] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for norm_h, idx in normalised_headers:
            if norm_h in aliases:
                col_map[canonical] = idx
                break

    missing = REQUIRED_FIELDS - set(col_map.keys())
    if missing:
        found_headers = [h for h, _ in normalised_headers]
        raise ValueError(
            f"Required header(s) not found: {sorted(missing)}. "
            f"Headers seen in file: {found_headers}"
        )

    return col_map
