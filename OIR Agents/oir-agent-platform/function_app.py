"""Azure Functions v2 entry point for the OIR platform.

The Python v2 programming model expects exactly one FunctionApp, declared
in a `function_app.py` at the deployment root. The three triggers each live
in their own module as a `func.Blueprint` and are registered here:

    ingest_oir        HTTP  POST /api/ingest-oir      (called by the Logic App)
    detect_exceptions timer 03:30 UTC / 09:00 IST daily
    apply_update      HTTP  POST /api/apply-update    (called by the Teams bot)

Both HTTP routes are FUNCTION-auth (require a function key); the Logic App
and bot pass it as `x-functions-key`.
"""
from __future__ import annotations

import azure.functions as func

from functions.apply_update import bp as apply_update_bp
from functions.detect_exceptions import bp as detect_exceptions_bp
from functions.ingest_oir import bp as ingest_oir_bp

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

app.register_functions(ingest_oir_bp)
app.register_functions(detect_exceptions_bp)
app.register_functions(apply_update_bp)
