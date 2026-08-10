"""Provision the OIR SharePoint Lists from infra/sharepoint-lists-schema.json.

Uses Microsoft Graph (site + list + column APIs) with an application
(client-credentials) token -- the same credentials already used for owner
email resolution in functions/shared/graph_client.py. See
docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md for why this
replaced the earlier Dataverse-based approach.

Idempotent: existing lists/columns are detected by name and left untouched.
Safe to re-run after a partial failure.

Required environment variables:
    SHAREPOINT_SITE_URL   e.g. https://contoso.sharepoint.com/sites/OIR
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET

The app registration needs an application permission granting write access
to this site -- either Sites.Selected (scoped to just this site via a
one-time Graph admin call) or the broader Sites.ReadWrite.All -- with admin
consent granted.

Usage:
    python infra/provision_sharepoint_lists.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from azure.identity import ClientSecretCredential

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("provision_sharepoint_lists")

SCHEMA_PATH = Path(__file__).resolve().parent / "sharepoint-lists-schema.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"

_COLUMN_TYPE_BUILDERS = {
    "text": lambda col: {"text": {}},
    "multilineText": lambda col: {"text": {"allowMultipleLines": True}},
    "number": lambda col: {"number": {"decimalPlaces": "none"}},
    "boolean": lambda col: {"boolean": {}},
    "dateOnly": lambda col: {"dateTime": {"format": "dateOnly", "displayAs": "default"}},
    "dateTime": lambda col: {"dateTime": {"format": "dateTime", "displayAs": "default"}},
    "choice": lambda col: {"choice": {"choices": col["choices"], "displayAs": "dropDownMenu", "allowTextEntry": False}},
}


def _parse_site_url(site_url: str) -> tuple[str, str]:
    without_scheme = site_url.split("://", 1)[-1]
    hostname, _, path = without_scheme.partition("/")
    return hostname, "/" + path.rstrip("/")


class SharePointAdmin:
    def __init__(self, site_url: str, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        self._http = httpx.Client(timeout=30.0)
        hostname, path = _parse_site_url(site_url)
        self.site_id = self._resolve_site_id(hostname, path)

    def _headers(self) -> dict:
        token = self._credential.get_token(SCOPE).token
        return {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}

    def _resolve_site_id(self, hostname: str, path: str) -> str:
        resp = self._http.get(f"{GRAPH_BASE}/sites/{hostname}:{path}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()["id"]

    def find_list(self, display_name: str) -> dict | None:
        resp = self._http.get(
            f"{GRAPH_BASE}/sites/{self.site_id}/lists",
            headers=self._headers(),
            params={"$filter": f"displayName eq '{display_name}'"},
        )
        resp.raise_for_status()
        items = resp.json().get("value", [])
        return items[0] if items else None

    def create_list(self, display_name: str, description: str) -> str:
        payload = {
            "displayName": display_name,
            "description": description,
            "list": {"template": "genericList"},
        }
        logger.info("Creating list '%s'", display_name)
        if self.dry_run:
            logger.info("  [dry-run] POST lists %s", json.dumps(payload))
            return "dry-run-list-id"
        resp = self._http.post(f"{GRAPH_BASE}/sites/{self.site_id}/lists", headers=self._headers(), json=payload)
        resp.raise_for_status()
        return resp.json()["id"]

    def find_column(self, list_id: str, name: str) -> dict | None:
        resp = self._http.get(f"{GRAPH_BASE}/sites/{self.site_id}/lists/{list_id}/columns", headers=self._headers())
        resp.raise_for_status()
        for col in resp.json().get("value", []):
            if col.get("name") == name:
                return col
        return None

    def add_column(self, list_id: str, column: dict) -> None:
        name = column["name"]
        col_type = column["type"]
        builder = _COLUMN_TYPE_BUILDERS.get(col_type)
        if builder is None:
            logger.warning("  Unsupported column type '%s' for %s -- skipping", col_type, name)
            return

        payload = {"name": name, "indexed": bool(column.get("indexed", False)), **builder(column)}
        if column.get("required"):
            payload["required"] = True

        logger.info("  Adding column '%s' (%s)", name, col_type)
        if self.dry_run:
            logger.info("    [dry-run] POST columns %s", json.dumps(payload))
            return
        resp = self._http.post(
            f"{GRAPH_BASE}/sites/{self.site_id}/lists/{list_id}/columns", headers=self._headers(), json=payload
        )
        if resp.status_code not in (201, 204):
            raise RuntimeError(f"Failed to create column '{name}': {resp.status_code} {resp.text}")

    def close(self) -> None:
        self._http.close()


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    missing = [v for v in ("SHAREPOINT_SITE_URL", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
               if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    schema = _load_schema()
    admin = SharePointAdmin(os.environ["SHAREPOINT_SITE_URL"], dry_run=args.dry_run)

    try:
        for list_def in schema["lists"]:
            display_name = list_def["displayName"]
            existing = admin.find_list(display_name)
            if existing:
                logger.info("List '%s' already exists -- skipping create", display_name)
                list_id = existing["id"]
            else:
                list_id = admin.create_list(display_name, list_def.get("description", ""))

            if args.dry_run and not existing:
                for column in list_def["columns"]:
                    admin.add_column(list_id, column)
                continue

            for column in list_def["columns"]:
                if admin.find_column(list_id, column["name"]):
                    logger.info("  Column '%s.%s' already exists -- skipping", display_name, column["name"])
                    continue
                admin.add_column(list_id, column)

        logger.info("SharePoint list provisioning complete.")
        return 0
    except Exception as exc:
        logger.error("Provisioning failed: %s", exc)
        return 1
    finally:
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
