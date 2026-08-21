"""Azure Function: IngestOIR

HTTP-triggered (called by Logic App on SharePoint file-created event).
Parses the OIR Excel file, hashes content, upserts the Demands container in
Cosmos DB, and appends a snapshot row for every demand. Designed to be
fully idempotent.

Expected request body:
{
  "fileUrl":          "https://...",
  "fileName":         "TD Bank OIR 06-08-2026.xlsx",
  "fileDate":         "2026-08-06",
  "lastModifiedBy":   "user@example.com"
}
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime

import azure.functions as func
import httpx
from azure.identity import ClientSecretCredential

from functions.shared.models import CONFIG, IngestionError
from functions.shared.graph_client import GraphClient
from functions.shared.cosmos_client import CosmosDbClient
from functions.shared.telemetry import track_metric, track_event
from .parser import parse_workbook
from .ingestion import IngestionAborted, ingest_rows

logger = logging.getLogger(__name__)

_FILE_PATTERN = re.compile(r"^TD Bank OIR \d{2}-\d{2}-\d{4}\.xlsx$")
_ERROR_RATE_ABORT_THRESHOLD = CONFIG["ingestion"]["error_rate_abort_threshold"]


bp = func.Blueprint()


@bp.route(route="ingest-oir", methods=["POST"])
def ingest_oir(req: func.HttpRequest) -> func.HttpResponse:
    start = datetime.utcnow()
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    file_url = body.get("fileUrl", "")
    file_name = body.get("fileName", "")
    file_date_str = body.get("fileDate", "")
    last_modified_by = body.get("lastModifiedBy", "")

    # -- Validate filename format -----------------------------------------
    if not _FILE_PATTERN.match(file_name):
        msg = f"Filename '{file_name}' does not match expected pattern."
        logger.error(msg)
        _alert_pmo(msg)
        return func.HttpResponse(msg, status_code=400)

    try:
        file_date = date.fromisoformat(file_date_str)
    except ValueError:
        return func.HttpResponse(f"Invalid fileDate '{file_date_str}'", status_code=400)

    # -- Download file -------------------------------------------------------
    try:
        token = _graph_token_for_file_download()
        with httpx.Client(timeout=60.0) as http:
            resp = http.get(file_url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            raw_bytes = resp.content
    except Exception as exc:
        msg = f"Failed to download OIR file: {exc}"
        logger.error(msg)
        _alert_pmo(msg)
        return func.HttpResponse(msg, status_code=502)

    # -- Parse ---------------------------------------------------------------
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        rows = list(parse_workbook(tmp_path, source_file=file_name))
    except IngestionError as exc:
        msg = str(exc)
        logger.error("IngestionError: %s", msg)
        _alert_pmo(msg)
        return func.HttpResponse(msg, status_code=422)
    finally:
        os.unlink(tmp_path)

    total_rows = len(rows)
    if total_rows == 0:
        msg = "No data rows found in workbook."
        _alert_pmo(msg)
        return func.HttpResponse(msg, status_code=422)

    # -- Upsert (shared with infra/backfill_history.py -- see ingestion.py) ---
    # Graph is optional: it only helps when the OIR file carries no email
    # columns, and it needs application permissions the tenant hasn't
    # granted. Set GRAPH_LOOKUP_ENABLED=true once that consent lands.
    graph_enabled = os.environ.get("GRAPH_LOOKUP_ENABLED", "false").lower() == "true"
    graph = GraphClient() if graph_enabled else None

    try:
        with CosmosDbClient() as db:
            stats = ingest_rows(rows, file_date, db, graph=graph)
    except IngestionAborted as exc:
        logger.error(str(exc))
        _alert_pmo(str(exc))
        return func.HttpResponse(str(exc), status_code=500)
    finally:
        if graph is not None:
            graph.close()

    duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    track_metric("ingest.rows_processed", stats.rows_processed, {"file_date": file_date_str})
    track_metric("ingest.rows_changed", stats.rows_changed, {"file_date": file_date_str})
    track_metric("ingest.rows_without_owner", stats.rows_without_owner, {"file_date": file_date_str})
    track_metric("ingest.duration_ms", duration_ms)
    track_event("IngestOIR.Complete", {"file": file_name, "rows": stats.rows_processed})

    return func.HttpResponse(
        json.dumps({"status": "ok", "file_date": file_date_str,
                    "duration_ms": duration_ms, **stats.as_dict()}),
        status_code=200,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _graph_token_for_file_download() -> str:
    """Token to download the source .xlsx from its SharePoint document library.

    Distinct from CosmosDbClient's own token acquisition -- this one
    is scoped just to the file download step, before the workbook has even
    been parsed.
    """
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )
    return credential.get_token("https://graph.microsoft.com/.default").token


def _alert_pmo(message: str) -> None:
    webhook_url = os.environ.get("PMO_TEAMS_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("PMO_TEAMS_WEBHOOK_URL not set; cannot send alert: %s", message)
        return
    try:
        with httpx.Client(timeout=10.0) as http:
            http.post(webhook_url, json={"text": f"⚠️ OIR Ingest Alert: {message}"})
    except Exception as exc:
        logger.error("Failed to send PMO alert: %s", exc)
