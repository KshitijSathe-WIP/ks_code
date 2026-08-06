"""Content hashing for staleness detection.

The hash is computed over normalised (comments, remarks_status) so that
whitespace and casing changes do NOT count as real updates.
"""
from __future__ import annotations

import hashlib
import re


def _normalise(value: str | None) -> str:
    """Collapse whitespace and lower-case; treat None as empty."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def content_hash(comments: str | None, remarks_status: str | None) -> str:
    """Return a 64-char hex SHA-256 hash of the normalised content pair."""
    payload = f"{_normalise(comments)}||{_normalise(remarks_status)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
