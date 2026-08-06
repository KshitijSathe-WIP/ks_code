"""Tests for the Dataverse metadata provisioning script.

Only exercises pure logic (schema loading, naming conversion, payload shape).
No network calls — DataverseAdmin's HTTP methods are exercised via dry-run
with a stubbed HTTP client where a call would otherwise occur.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.provision_dataverse import DataverseAdmin, _load_schema, _pascal, _schema_table


class TestNamingConvention:

    def test_simple_name(self):
        assert _pascal("oir_demandid") == "oir_Demandid"

    def test_multi_word_name(self):
        assert _pascal("oir_last_content_change_date") == "oir_LastContentChangeDate"

    def test_prefix_preserved_lowercase(self):
        assert _pascal("oir_status").startswith("oir_")


class TestSchemaLoading:

    def test_all_expected_tables_present(self):
        schema = _load_schema()
        names = {t["name"] for t in schema["tables"]}
        assert names == {"oir_demand", "oir_snapshot_history", "oir_interaction_log", "oir_person_map"}

    def test_schema_table_lookup(self):
        table = _schema_table("oir_demand")
        assert any(c["name"] == "oir_demandid" and c.get("isPrimaryKey") for c in table["columns"])

    def test_unknown_table_raises(self):
        with pytest.raises(StopIteration):
            _schema_table("does_not_exist")


class TestDataverseAdminDryRun:

    def _admin(self) -> DataverseAdmin:
        admin = DataverseAdmin(dry_run=True)
        admin.entity_exists = MagicMock(return_value=False)
        admin.attribute_exists = MagicMock(return_value=False)
        return admin

    def test_create_entity_dry_run_makes_no_post(self):
        admin = self._admin()
        admin._http.post = MagicMock(side_effect=AssertionError("should not POST in dry-run"))
        table = _schema_table("oir_interaction_log")
        admin.create_entity(table)  # must not raise

    def test_add_attribute_dry_run_makes_no_post(self):
        admin = self._admin()
        admin._http.post = MagicMock(side_effect=AssertionError("should not POST in dry-run"))
        admin.add_attribute("oir_interaction_log", {"name": "oir_channel", "type": "string", "default": "TEAMS"})

    def test_primary_key_column_skipped(self):
        admin = self._admin()
        admin._http.post = MagicMock(side_effect=AssertionError("primary key must not be re-created"))
        admin.add_attribute("oir_demand", {"name": "oir_demandid", "type": "string", "isPrimaryKey": True})

    def test_unsupported_type_skipped(self):
        admin = self._admin()
        admin._http.post = MagicMock(side_effect=AssertionError("unsupported type must not POST"))
        admin.add_attribute("oir_snapshot_history", {"name": "oir_snapshotid", "type": "guid", "isPrimaryKey": True})
