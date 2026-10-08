"""Azure Function: UpdateForm

The way a recipient answers a digest. Email is the only channel that reaches
these people (ADR 0010) and nothing reads replies to the ACS mailbox, so the
digest carries a link here instead of inviting a reply into a void.

GET  /api/update?t=<token>   the person's own open demands, as a form
POST /api/update?t=<token>   applies whatever they filled in

The token identifies; it does not authorise. Which demands appear, and which
may be written, is decided per demand by assert_authorised() against the
PM/TM/EM recorded on it -- the same gate the Teams path used. A leaked link
therefore exposes that one person's demands, nothing wider.

Writes go through apply_demand_update(), shared with the ApplyUpdate HTTP
endpoint, so validation, hashing and the audit trail cannot drift between
the two entry points.
"""
from __future__ import annotations

import html
import logging
from datetime import date

import azure.functions as func

from functions.shared.action_token import InvalidActionToken, resolve
from functions.shared.cosmos_client import CosmosDbClient
from functions.shared.models import AuthorisationError, VALID_STATUSES, ValidationError
from functions.shared.telemetry import track_event, track_metric
from functions.apply_update.authz import assert_authorised
from functions.apply_update.core import apply_demand_update

logger = logging.getLogger(__name__)

bp = func.Blueprint()


@bp.route(route="update", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
def update_form(req: func.HttpRequest) -> func.HttpResponse:
    """Anonymous by design: the token in the link is the credential.

    A function key cannot be used here -- it would be identical for every
    recipient, so anyone could act as anyone. The per-recipient token is
    both narrower and revocable.
    """
    token_value = req.params.get("t", "")

    try:
        with CosmosDbClient() as db:
            email = resolve(db, token_value)
            demands = _demands_for(db, email)

            if req.method == "GET":
                return _page(_render_form(email, demands))

            results = _apply_submission(db, req, email, demands)
            return _page(_render_results(email, results))

    except InvalidActionToken as exc:
        logger.warning("Rejected update-form token: %s", exc)
        return _page(_render_error(str(exc)), status=403)
    except Exception:
        logger.exception("Update form failed")
        return _page(_render_error(
            "Something went wrong saving your update. Nothing was changed."),
            status=500)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _demands_for(db, email: str) -> list:
    """The person's own active demands, newest staleness first.

    Filtered by the same authorisation rule that governs writing, so the
    form can never show a demand the submit step would then refuse.
    """
    out = []
    for row in db.list_active_demands():
        try:
            assert_authorised(email, row.get("PMEmail", ""),
                              row.get("TMEmail", ""), row.get("EMEmail", ""))
        except AuthorisationError:
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("StaleDays") or 0, reverse=True)
    return out


def _apply_submission(db, req: func.HttpRequest, email: str, demands: list) -> list[dict]:
    """Apply each demand the user actually filled in.

    Per-demand outcomes rather than all-or-nothing: with a dozen rows on one
    page, failing the whole submission because one status is misspelt would
    silently discard eleven good updates.
    """
    form = req.form
    allowed = {d.get("DemandID") for d in demands}
    results = []
    applied = 0

    for demand_id in form.get_all("demand_id") if hasattr(form, "get_all") else form.getlist("demand_id"):
        comments = (form.get(f"comments__{demand_id}") or "").strip()
        remarks = (form.get(f"remarks__{demand_id}") or "").strip()
        if not comments and not remarks:
            continue        # left blank: not an update, not an error

        if demand_id not in allowed:
            # Should be unreachable via the rendered page; a hand-crafted
            # POST would land here.
            results.append({"demand_id": demand_id, "ok": False,
                            "message": "You are not an owner of this demand."})
            continue

        try:
            apply_demand_update(db, demand_id=demand_id, actor_email=email,
                                action="SUBMIT", comments=comments or None,
                                remarks_status=remarks or None, channel="WEB_FORM")
            applied += 1
            results.append({"demand_id": demand_id, "ok": True,
                            "message": "Updated."})
        except (ValidationError, AuthorisationError) as exc:
            results.append({"demand_id": demand_id, "ok": False, "message": str(exc)})
        except Exception:
            logger.exception("Failed to apply %s from the web form", demand_id)
            results.append({"demand_id": demand_id, "ok": False,
                            "message": "Could not save this one. Please try again."})

    track_metric("updateform.applied_count", applied)
    track_event("UpdateForm.Submitted", {
        "actor": email, "applied": applied, "attempted": len(results),
    })
    return results


# ---------------------------------------------------------------------------
# Rendering -- server-side, no JS, no external assets
# ---------------------------------------------------------------------------

def _page(body: str, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Links land here from an email, so keep the page out of history/indexes. -->
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>OIR demand update</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; padding: 1.5rem;
        background: #f3f2f1; color: #201f1e; }}
 main {{ max-width: 60rem; margin: 0 auto; }}
 h1 {{ font-size: 1.3rem; }}
 .demand {{ background: #fff; border: 1px solid #e1dfdd; border-radius: 4px;
            padding: 1rem; margin-bottom: 1rem; }}
 .meta {{ color: #605e5c; font-size: .85rem; margin: .25rem 0 .75rem; }}
 .stale {{ color: #a4262c; font-weight: 600; }}
 label {{ display: block; font-size: .85rem; font-weight: 600; margin: .5rem 0 .2rem; }}
 input[type=text], select {{ width: 100%; padding: .5rem; font-size: .95rem;
        border: 1px solid #8a8886; border-radius: 2px; box-sizing: border-box; }}
 button {{ background: #0078d4; color: #fff; border: 0; border-radius: 2px;
           padding: .7rem 1.4rem; font-size: 1rem; cursor: pointer; }}
 .ok {{ color: #107c10; }} .bad {{ color: #a4262c; }}
 .note {{ color: #605e5c; font-size: .85rem; }}
</style></head><body><main>{body}</main></body></html>""",
        status_code=status, mimetype="text/html",
    )


def _render_error(message: str) -> str:
    return (f"<h1>This link cannot be used</h1><p>{html.escape(message)}</p>"
            "<p class='note'>Links expire, and each digest carries a fresh one. "
            "Use the most recent OIR email, or update the OIR tracker directly.</p>")


def _render_form(email: str, demands: list) -> str:
    if not demands:
        return ("<h1>Nothing outstanding</h1>"
                "<p>You have no open OIR demands needing an update. Thank you.</p>")

    rows = []
    for d in demands:
        did = html.escape(d.get("DemandID", ""))
        stale = d.get("StaleDays") or 0
        rows.append(f"""
<div class="demand">
  <strong>{html.escape(d.get('Project',''))} &mdash; {html.escape(str(d.get('Role','')))}</strong>
  <div class="meta">{did} &middot; status {html.escape(d.get('Status',''))}
    &middot; <span class="stale">{stale} business day{'s' if stale != 1 else ''} without an update</span></div>
  <input type="hidden" name="demand_id" value="{did}">
  <label for="c__{did}">Comments</label>
  <input type="text" id="c__{did}" name="comments__{did}"
         value="{html.escape(d.get('Comments','') or '')}" maxlength="2000">
  <label for="r__{did}">Remarks status</label>
  {_status_select(f"r__{did}", f"remarks__{did}", d.get('RemarksStatus',''))}
</div>""")

    return (f"<h1>Your open OIR demands</h1>"
            f"<p class='note'>Signed in as {html.escape(email)} from your digest link. "
            f"Leave anything you are not changing blank.</p>"
            f"<form method='post'>{''.join(rows)}"
            f"<button type='submit'>Save updates</button></form>")


def _status_select(dom_id: str, name: str, current: str) -> str:
    opts = ["<option value=''>-- leave unchanged --</option>"]
    for status in sorted(VALID_STATUSES):
        sel = " selected" if status == current else ""
        opts.append(f"<option value='{html.escape(status)}'{sel}>{html.escape(status)}</option>")
    return f"<select id='{dom_id}' name='{name}'>{''.join(opts)}</select>"


def _render_results(email: str, results: list[dict]) -> str:
    if not results:
        return ("<h1>Nothing submitted</h1><p>No fields were filled in, so "
                "nothing was changed.</p>")
    ok = sum(1 for r in results if r["ok"])
    items = "".join(
        f"<li class='{'ok' if r['ok'] else 'bad'}'>{html.escape(r['demand_id'])}: "
        f"{html.escape(r['message'])}</li>" for r in results)
    return (f"<h1>{ok} of {len(results)} demand{'s' if len(results) != 1 else ''} updated</h1>"
            f"<ul>{items}</ul>"
            f"<p class='note'>Changes are recorded against {html.escape(email)} "
            f"and appear in tomorrow's OIR file.</p>")
