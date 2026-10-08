"""Azure Functions v2 entry point for the OIR platform.

The Python v2 programming model expects exactly one FunctionApp, declared
in a `function_app.py` at the deployment root. The three triggers each live
in their own module as a `func.Blueprint` and are registered here:

    ingest_oir        HTTP  POST     /api/ingest-oir    (called by the Logic App)
    detect_exceptions timer 03:30 UTC / 09:00 IST daily
    apply_update      HTTP  POST     /api/apply-update  (machine callers)
    update_form       HTTP  GET/POST /api/update        (recipients, from a digest link)

The default is FUNCTION auth, so ingest_oir and apply_update require a
function key passed as `x-functions-key`.

update_form is the deliberate exception and declares ANONYMOUS on its own
route. It is opened by a person clicking a link in their email, who has no
key and -- in this tenant -- no usable Microsoft identity either (ADR 0010).
A function key would be no use as a credential regardless: it is the same
value for every recipient, so holding one would let anyone act as anyone.
Instead each link carries a per-recipient opaque token
(functions/shared/action_token.py) that identifies who opened it, and every
write is still authorised per demand against its PM/TM/EM.
"""
from __future__ import annotations

import azure.functions as func

from functions.apply_update import bp as apply_update_bp
from functions.detect_exceptions import bp as detect_exceptions_bp
from functions.ingest_oir import bp as ingest_oir_bp
from functions.update_form import bp as update_form_bp

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

app.register_functions(ingest_oir_bp)
app.register_functions(detect_exceptions_bp)
app.register_functions(apply_update_bp)
app.register_functions(update_form_bp)
