"""Tests for content hashing — the core of staleness detection."""
import pytest
from functions.ingest_oir.hashing import content_hash


class TestHashStability:
    """Whitespace and casing changes must produce the same hash."""

    def test_leading_trailing_whitespace_ignored(self):
        h1 = content_hash("  L1 screens ongoing  ", "Pending CI FB")
        h2 = content_hash("L1 screens ongoing", "Pending CI FB")
        assert h1 == h2

    def test_internal_whitespace_collapsed(self):
        h1 = content_hash("L1  screens   ongoing", "Pending CI FB")
        h2 = content_hash("L1 screens ongoing", "Pending CI FB")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = content_hash("L1 SCREENS ONGOING", "PENDING CI FB")
        h2 = content_hash("l1 screens ongoing", "pending ci fb")
        assert h1 == h2

    def test_none_treated_as_empty(self):
        h1 = content_hash(None, None)
        h2 = content_hash("", "")
        assert h1 == h2

    def test_none_comments_empty_remarks(self):
        h1 = content_hash(None, "Need Profiles")
        h2 = content_hash("", "Need Profiles")
        assert h1 == h2


class TestHashSensitivity:
    """Any real text change must produce a different hash."""

    def test_different_comments(self):
        h1 = content_hash("L1 screens ongoing", "Pending CI FB")
        h2 = content_hash("L1 screens complete", "Pending CI FB")
        assert h1 != h2

    def test_different_remarks(self):
        h1 = content_hash("L1 screens ongoing", "Pending CI FB")
        h2 = content_hash("L1 screens ongoing", "Pending Offer")
        assert h1 != h2

    def test_both_changed(self):
        h1 = content_hash("old comment", "old status")
        h2 = content_hash("new comment", "new status")
        assert h1 != h2

    def test_empty_vs_non_empty(self):
        h1 = content_hash("", "")
        h2 = content_hash("something", "")
        assert h1 != h2

    def test_hash_is_64_chars(self):
        h = content_hash("test", "test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
