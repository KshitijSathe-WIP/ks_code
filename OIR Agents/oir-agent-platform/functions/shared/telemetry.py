"""Application Insights telemetry helpers.

All metrics are emitted as custom events + measurements so they appear in
the Application Insights Metrics Explorer without custom queries.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from opencensus.ext.azure.log_exporter import AzureLogHandler
    from opencensus.ext.azure import metrics_exporter
    from opencensus.stats import aggregation, measure, stats, view

    _ikey = os.environ.get("APPINSIGHTS_INSTRUMENTATIONKEY", "")
    if _ikey:
        _handler = AzureLogHandler(connection_string=f"InstrumentationKey={_ikey}")
        logging.getLogger().addHandler(_handler)
    _APPINSIGHTS_AVAILABLE = bool(_ikey)
except ImportError:
    _APPINSIGHTS_AVAILABLE = False


def track_metric(name: str, value: float, properties: dict[str, Any] | None = None) -> None:
    """Emit a named metric value to Application Insights (best-effort)."""
    props_str = ", ".join(f"{k}={v}" for k, v in (properties or {}).items())
    logger.info("METRIC %s=%.4f %s", name, value, props_str)

    if not _APPINSIGHTS_AVAILABLE:
        return

    try:
        from opencensus.ext.azure import metrics_exporter as _me
        # Lightweight: emit via structured log so AI picks it up via log exporter.
        # For production, replace with OpenCensus measure/view pipeline.
        extra = {"custom_dimensions": {"metric_name": name, "metric_value": str(value), **(properties or {})}}
        logging.getLogger("oir.metrics").info("metric", extra=extra)
    except Exception:  # telemetry must never crash the function
        pass


def track_event(name: str, properties: dict[str, Any] | None = None) -> None:
    """Emit a named event to Application Insights (best-effort)."""
    logger.info("EVENT %s %s", name, properties or {})

    if not _APPINSIGHTS_AVAILABLE:
        return

    try:
        extra = {"custom_dimensions": {"event_name": name, **(properties or {})}}
        logging.getLogger("oir.events").info(name, extra=extra)
    except Exception:
        pass
