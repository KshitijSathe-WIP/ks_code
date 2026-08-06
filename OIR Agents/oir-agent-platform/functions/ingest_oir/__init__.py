"""Azure Function: IngestOIR

HTTP-triggered (called by Logic App on SharePoint file-created event).
Parses the OIR Excel file, hashes content, upserts Dataverse, and appends
a snapshot row for every demand. Designed to be fully idempotent.

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
from functions.shared.telemetry import track_metric, track_event
from .parser import parse_workbook
from .hashing import content_hash
from .dataverse_client import DataverseClient

logger = logging.getLogger(__name__)

_FILE_PATTERN = re.compile(r"^TD Bank OIR \d{2}-\d{2}-\d{4}\.xlsx$")
_ERROR_RATE_ABORT_THRESHOLD = CONFIG["ingestion"]["error_rate_abort_threshold"]


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="ingest-oir", methods=["POST"])
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
        token = _sharepoint_token()
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

    # -- Upsert into Dataverse -----------------------------------------------
    rows_processed = 0
    rows_changed = 0
    rows_errored = 0
    seen_ids: set[str] = set()

    with DataverseClient() as dv, GraphClient() as graph:
        for row in rows:
            seen_ids.add(row.demand_id)
            new_hash = content_hash(row.comments, row.remarks_status)

            # Resolve owner emails via Graph (with Dataverse cache)
            pm_email = _resolve_email(row.pm_name, graph, dv)
            tm_email = _resolve_email(row.tm_name, graph, dv)
            em_email = _resolve_email(row.em_name, graph, dv)

            try:
                existing = dv.get_demand(row.demand_id)

                if existing is None:
                    dv.upsert_demand({
                        "oir_demandid": row.demand_id,
                        "oir_project": row.project,
                        "oir_sldu": row.sldu,
                        "oir_role": row.role,
                        "oir_skill": row.skill,
                        "oir_status": row.status,
                        "oir_pm_name": row.pm_name,
                        "oir_pm_email": pm_email or "",
                        "oir_tm_name": row.tm_name,
                        "oir_tm_email": tm_email or "",
                        "oir_em_name": row.em_name,
                        "oir_em_email": em_email or "",
                        "oir_dem_start_date": row.dem_start_date.isoformat() if row.dem_start_date else None,
                        "oir_dem_end_date": row.dem_end_date.isoformat() if row.dem_end_date else None,
                        "oir_comments": row.comments,
                        "oir_remarks_status": row.remarks_status,
                        "oir_comments_hash": new_hash,
                        "oir_last_content_change_date": file_date.isoformat(),
                        "oir_first_seen_date": file_date.isoformat(),
                        "oir_escalation_level": 0,
                        "oir_is_active": True,
                        "oir_source_file": row.source_file,
                    })
                    rows_changed += 1
                else:
                    content_changed = existing.comments_hash != new_hash
                    if content_changed:
                        rows_changed += 1

                    dv.upsert_demand({
                        "oir_demandid": row.demand_id,
                        "oir_project": row.project,
                        "oir_sldu": row.sldu,
                        "oir_role": row.role,
                        "oir_skill": row.skill,
                        "oir_status": row.status,
                        "oir_pm_name": row.pm_name,
                        "oir_pm_email": pm_email or existing.pm_email,
                        "oir_tm_name": row.tm_name,
                        "oir_tm_email": tm_email or existing.tm_email,
                        "oir_em_name": row.em_name,
                        "oir_em_email": em_email or existing.em_email,
                        "oir_dem_start_date": row.dem_start_date.isoformat() if row.dem_start_date else None,
                        "oir_dem_end_date": row.dem_end_date.isoformat() if row.dem_end_date else None,
                        "oir_comments": row.comments,
                        "oir_remarks_status": row.remarks_status,
                        "oir_comments_hash": new_hash,
                        "oir_last_content_change_date": (
                            file_date.isoformat() if content_changed
                            else existing.last_content_change_date.isoformat()
                        ),
                        "oir_escalation_level": 0 if content_changed else existing.escalation_level,
                        "oir_last_notified_on": None if content_changed else (
                            existing.last_notified_on.isoformat() + "Z"
                            if existing.last_notified_on else None
                        ),
                        "oir_is_active": True,
                        "oir_source_file": row.source_file,
                    })

                dv.insert_snapshot(row, new_hash, snapshot_date=file_date)
                rows_processed += 1

            except Exception as exc:
                rows_errored += 1
                logger.error("Error processing demand '%s': %s", row.demand_id, exc)
                if rows_errored / total_rows > _ERROR_RATE_ABORT_THRESHOLD:
                    msg = (
                        f"Abort: error rate {rows_errored}/{total_rows} "
                        f"exceeded {int(_ERROR_RATE_ABORT_THRESHOLD*100)}% threshold."
                    )
                    logger.error(msg)
                    _alert_pmo(msg)
                    return func.HttpResponse(msg, status_code=500)

        dv.deactivate_missing(file_date, seen_ids)

    duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    track_metric("ingest.rows_processed", rows_processed, {"file_date": file_date_str})
    track_metric("ingest.rows_changed", rows_changed, {"file_date": file_date_str})
    track_metric("ingest.duration_ms", duration_ms)
    track_event("IngestOIR.Complete", {"file": file_name, "rows": rows_processed})

    return func.HttpResponse(
        json.dumps({
            "status": "ok",
            "file_date": file_date_str,
            "rows_processed": rows_processed,
            "rows_changed": rows_changed,
            "rows_errored": rows_errored,
            "duration_ms": duration_ms,
        }),
        status_code=200,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_email(display_name: str, graph: GraphClient, dv: DataverseClient) -> str | None:
    if not display_name:
        return None
    cached = dv.get_cached_email(display_name)
    if cached:
        return cached
    email = graph.resolve_email(display_name)
    if email:
        dv.cache_email(display_name, email)
    return email


def _sharepoint_token() -> str:
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
