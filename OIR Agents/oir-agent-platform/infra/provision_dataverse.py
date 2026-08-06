"""Provision the OIR Dataverse tables from infra/dataverse-schema.json.

Uses the Dataverse Metadata Web API directly (EntityDefinitions / Attributes)
with a service-principal (client-credentials) token — no `pac` CLI required.

Idempotent: existing tables/columns are detected by LogicalName and left
untouched (never drops or renames anything). Safe to re-run after a partial
failure.

Required environment variables:
    DATAVERSE_URL        e.g. https://org.crm.dynamics.com
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET

Usage:
    python infra/provision_dataverse.py [--dry-run]

The service principal must be added as an Application User in the target
Dataverse environment (Power Platform admin center -> Environments -> your
env -> Settings -> Users + permissions -> Application users) with a security
role that grants Create/Read on entities, e.g. "System Customizer".
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from azure.identity import ClientSecretCredential

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("provision_dataverse")

SCHEMA_PATH = Path(__file__).resolve().parent / "dataverse-schema.json"
PUBLISHER_PREFIX = "oir"

# Dataverse AttributeTypeCode-equivalent OData type map for our schema's
# simplified "type" field. See:
# https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/attributemetadata
_TYPE_TO_METADATA = {
    "string": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "multilinetext": "Microsoft.Dynamics.CRM.MemoAttributeMetadata",
    "int": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata",
    "decimal": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
    "boolean": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata",
    "date": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
    "datetime": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
    "picklist": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
    "guid": None,        # primary key column — created implicitly with the entity
}


class DataverseAdmin:
    """Thin client over the Dataverse Metadata Web API for table/column provisioning."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.base_url = os.environ["DATAVERSE_URL"].rstrip("/") + "/api/data/v9.2"
        dv_host = os.environ["DATAVERSE_URL"].rstrip("/").removeprefix("https://")
        self._credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        self._scope = f"https://{dv_host}/.default"
        self._http = httpx.Client(timeout=60.0)

    def _headers(self, extra: dict | None = None) -> dict:
        token = self._credential.get_token(self._scope).token
        headers = {
            "Authorization": f"Bearer {token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def entity_exists(self, logical_name: str) -> bool:
        resp = self._http.get(
            f"{self.base_url}/EntityDefinitions(LogicalName='{logical_name}')",
            headers=self._headers(),
            params={"$select": "LogicalName"},
        )
        return resp.status_code == 200

    def attribute_exists(self, entity_logical_name: str, attr_logical_name: str) -> bool:
        resp = self._http.get(
            f"{self.base_url}/EntityDefinitions(LogicalName='{entity_logical_name}')"
            f"/Attributes(LogicalName='{attr_logical_name}')",
            headers=self._headers(),
            params={"$select": "LogicalName"},
        )
        return resp.status_code == 200

    def create_entity(self, table: dict) -> None:
        logical_name = f"{PUBLISHER_PREFIX}_{table['name'].removeprefix('oir_')}"
        display_name = table["name"].removeprefix("oir_").replace("_", " ").title()
        primary_col = next(c for c in table["columns"] if c.get("isPrimaryKey"))
        primary_logical = primary_col["name"]

        if self.entity_exists(logical_name):
            logger.info("Table '%s' already exists — skipping create", logical_name)
            return

        payload = {
            "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
            "SchemaName": _pascal(logical_name),
            "DisplayName": {
                "@odata.type": "Microsoft.Dynamics.CRM.Label",
                "LocalizedLabels": [
                    {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                     "Label": display_name, "LanguageCode": 1033}
                ],
            },
            "DisplayCollectionName": {
                "@odata.type": "Microsoft.Dynamics.CRM.Label",
                "LocalizedLabels": [
                    {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                     "Label": display_name + "s", "LanguageCode": 1033}
                ],
            },
            "Description": {
                "@odata.type": "Microsoft.Dynamics.CRM.Label",
                "LocalizedLabels": [
                    {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                     "Label": table.get("description", ""), "LanguageCode": 1033}
                ],
            },
            "OwnershipType": "UserOwned",
            "IsActivity": False,
            "HasNotes": False,
            "HasActivities": False,
            "PrimaryNameAttribute": primary_logical,
            "Attributes": [
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
                    "SchemaName": _pascal(primary_logical),
                    "RequiredLevel": {"Value": "ApplicationRequired", "CanBeChanged": True},
                    "MaxLength": primary_col.get("maxLength", 100),
                    "DisplayName": {
                        "@odata.type": "Microsoft.Dynamics.CRM.Label",
                        "LocalizedLabels": [
                            {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                             "Label": display_name + " Key", "LanguageCode": 1033}
                        ],
                    },
                }
            ],
        }

        logger.info("Creating table '%s' (%s)", logical_name, table["name"])
        if self.dry_run:
            logger.info("  [dry-run] POST EntityDefinitions %s", json.dumps(payload)[:200])
            return

        resp = self._http.post(f"{self.base_url}/EntityDefinitions", headers=self._headers(), json=payload)
        if resp.status_code not in (201, 204):
            raise RuntimeError(f"Failed to create entity '{logical_name}': {resp.status_code} {resp.text}")

        # Metadata operations are asynchronous on the server side; give it a moment
        # before attempting to add attributes.
        time.sleep(3)

    def add_attribute(self, table_name: str, column: dict) -> None:
        entity_logical = f"{PUBLISHER_PREFIX}_{table_name.removeprefix('oir_')}"
        col_name = column["name"]
        col_type = column["type"]

        if col_type == "guid" and column.get("isPrimaryKey"):
            return  # primary key handled implicitly by Dataverse (<entity>id)
        if col_name == next(c["name"] for c in _schema_table(table_name)["columns"] if c.get("isPrimaryKey")):
            return  # this is the primary name attribute created with the entity

        if self.attribute_exists(entity_logical, col_name):
            logger.info("  Column '%s.%s' already exists — skipping", entity_logical, col_name)
            return

        odata_type = _TYPE_TO_METADATA.get(col_type)
        if odata_type is None:
            logger.warning("  Unsupported column type '%s' for %s — skipping", col_type, col_name)
            return

        display_name = col_name.removeprefix(f"{PUBLISHER_PREFIX}_").replace("_", " ").title()
        payload: dict[str, Any] = {
            "@odata.type": odata_type,
            "SchemaName": _pascal(col_name),
            "RequiredLevel": {
                "Value": "ApplicationRequired" if column.get("isRequired") else "None",
                "CanBeChanged": True,
            },
            "DisplayName": {
                "@odata.type": "Microsoft.Dynamics.CRM.Label",
                "LocalizedLabels": [
                    {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                     "Label": display_name, "LanguageCode": 1033}
                ],
            },
        }

        if col_type == "string":
            payload["MaxLength"] = column.get("maxLength", 850)
            payload["FormatName"] = {"Value": "Text"}
        elif col_type == "multilinetext":
            payload["MaxLength"] = column.get("maxLength", 4000)
        elif col_type in ("date", "datetime"):
            payload["Format"] = "DateOnly" if col_type == "date" else "DateAndTime"
        elif col_type == "int":
            payload["MinValue"] = -2147483648
            payload["MaxValue"] = 2147483647
        elif col_type == "decimal":
            payload["MinValue"] = -100000000000
            payload["MaxValue"] = 100000000000
            payload["Precision"] = 2
        elif col_type == "picklist":
            options = column.get("options", [])
            payload["OptionSet"] = {
                "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
                "IsGlobal": False,
                "OptionSetType": "Picklist",
                "Options": [
                    {
                        "Value": 100000000 + i,
                        "Label": {
                            "@odata.type": "Microsoft.Dynamics.CRM.Label",
                            "LocalizedLabels": [
                                {"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
                                 "Label": opt, "LanguageCode": 1033}
                            ],
                        },
                    }
                    for i, opt in enumerate(options)
                ],
            }

        logger.info("  Adding column '%s.%s' (%s)", entity_logical, col_name, col_type)
        if self.dry_run:
            logger.info("    [dry-run] POST Attributes %s", json.dumps(payload)[:200])
            return

        resp = self._http.post(
            f"{self.base_url}/EntityDefinitions(LogicalName='{entity_logical}')/Attributes",
            headers=self._headers(),
            json=payload,
        )
        if resp.status_code not in (201, 204):
            raise RuntimeError(f"Failed to create column '{col_name}': {resp.status_code} {resp.text}")

    def close(self) -> None:
        self._http.close()


def _pascal(logical_name: str) -> str:
    """oir_demand_id -> oir_DemandId (Dataverse SchemaName convention: keep prefix lowercase)."""
    parts = logical_name.split("_")
    prefix, rest = parts[0], parts[1:]
    return prefix + "_" + "".join(p.capitalize() for p in rest)


_SCHEMA_CACHE: dict | None = None


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def _schema_table(table_name: str) -> dict:
    return next(t for t in _load_schema()["tables"] if t["name"] == table_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned API calls without executing them")
    args = parser.parse_args()

    missing = [v for v in ("DATAVERSE_URL", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
               if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    schema = _load_schema()
    admin = DataverseAdmin(dry_run=args.dry_run)

    try:
        for table in schema["tables"]:
            admin.create_entity(table)
            for column in table["columns"]:
                admin.add_attribute(table["name"], column)
        logger.info("Dataverse provisioning complete.")
        return 0
    except Exception as exc:
        logger.error("Provisioning failed: %s", exc)
        return 1
    finally:
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
