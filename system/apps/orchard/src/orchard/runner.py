"""Candidate sourcing and templated multi-inbox outreach with open tracking.

Services run from the repo root. Runtime state lives under
``data/.apps/orchard/`` (see store.py). This is a synchronous Flask app
served by the threaded Werkzeug server; the system_interface proxy at
``/service/orchard/`` handles prefixing, so routes are served at ``/``.
"""

import datetime
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request
from werkzeug.serving import run_simple

from orchard import gmail, service, store
from orchard.errors import (
    CandidateOutreachError,
    DraftError,
    EnrichError,
    NotFoundError,
    SendError,
)
from orchard.models import STAGE_LABELS, STAGES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchard")

app = Flask("orchard", static_folder=None)

_ASSETS = Path(__file__).parent / "assets"

# A 1x1 transparent GIF returned by the tracking pixel endpoint.
_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _ui_fields() -> dict[str, Any]:
    """Only the candidate fields the client is allowed to set directly."""
    return {k: v for k, v in _body().items() if k in store.UI_EDITABLE_FIELDS}


def _error(message: str, code: int) -> Response:
    response = jsonify({"error": message})
    response.status_code = code
    return response


@app.route("/")
def index() -> Response:
    # no-store so the single-file app is never served stale after an update.
    response = Response((_ASSETS / "app.html").read_text(), mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.route("/api/bootstrap")
def api_bootstrap() -> Response:
    """Everything the page needs on load: candidates, templates, inboxes, settings."""
    return jsonify(
        {
            "candidates": [c.to_dict() for c in store.list_candidates()],
            "templates": [t.to_dict() for t in store.list_templates()],
            "inboxes": [i.model_dump() for i in store.load_inboxes()],
            "settings": store.load_settings(),
            "contactout_configured": store.has_contactout_token(),
            "stages": [{"key": s, "label": STAGE_LABELS[s]} for s in STAGES],
        }
    )


# ---- candidates -------------------------------------------------------------


def _bad_stage(fields: dict[str, Any]) -> bool:
    """True when the request set a stage that isn't one of the known stages."""
    stage = fields.get("stage")
    return stage is not None and stage != "" and stage not in STAGES


@app.route("/api/candidates", methods=["POST"])
def api_create_candidate() -> Response:
    fields = _ui_fields()
    name = str(fields.get("name", "")).strip()
    if not name:
        return _error("A candidate needs a name.", 400)
    if _bad_stage(fields):
        return _error("Unknown pipeline stage.", 400)
    candidate = store.create_candidate({**fields, "name": name})
    return jsonify(candidate.to_dict())


@app.route("/api/candidates/<candidate_id>", methods=["PATCH"])
def api_update_candidate(candidate_id: str) -> Response:
    fields = _ui_fields()
    if _bad_stage(fields):
        return _error("Unknown pipeline stage.", 400)
    try:
        return jsonify(store.update_candidate(candidate_id, fields).to_dict())
    except NotFoundError as exc:
        return _error(str(exc), 404)


@app.route("/api/candidates/<candidate_id>", methods=["DELETE"])
def api_delete_candidate(candidate_id: str) -> Response:
    try:
        store.delete_candidate(candidate_id)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True})


@app.route("/api/candidates/<candidate_id>/reached-out", methods=["POST"])
def api_reached_out(candidate_id: str) -> Response:
    body = _body()
    inbox_key = str(body.get("inbox_key", ""))
    template_id = str(body.get("template_id", ""))
    try:
        candidate = service.mark_reached_out(candidate_id, inbox_key, template_id, _now_iso())
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify(candidate.to_dict())


@app.route("/api/candidates/<candidate_id>/send", methods=["POST"])
def api_send(candidate_id: str) -> Response:
    body = _body()
    try:
        candidate = store.get_candidate(candidate_id)
        # "Follow up on the same thread" replies onto the most recent send.
        reply_to = candidate.sends[-1] if (body.get("in_thread") and candidate.sends) else None
        inbox_key = reply_to["inbox_key"] if reply_to else str(body.get("inbox_key", ""))
        result = service.send_email(
            candidate_id,
            inbox_key,
            str(body.get("template_id", "")),
            body.get("body"),
            _now_iso(),
            subject_override=body.get("subject"),
            reply_to=reply_to,
        )
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except SendError as exc:
        # A precondition or the offline mail connection -- expected, not a bug.
        return _error(str(exc), 503)
    return jsonify(result)


@app.route("/api/candidates/<candidate_id>/draft-email", methods=["POST"])
def api_draft_email(candidate_id: str) -> Response:
    """Draft a reach-out or follow-up email in the sender's voice for this candidate."""
    body = _body()
    try:
        draft = service.draft_outreach(
            candidate_id,
            str(body.get("inbox_key", "you")),
            follow_up=bool(body.get("follow_up")),
        )
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except DraftError as exc:
        return _error(str(exc), 502)
    return jsonify(draft)


@app.route("/api/candidates/<candidate_id>/find-email", methods=["POST"])
def api_find_email(candidate_id: str) -> Response:
    """Look up the candidate's email from their LinkedIn URL via ContactOut."""
    try:
        result = service.find_candidate_email(candidate_id)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except EnrichError as exc:
        # Missing token / no credits / no profile / connection down -- expected.
        return _error(str(exc), 502)
    return jsonify(result)


# ---- templates --------------------------------------------------------------


@app.route("/api/templates", methods=["POST"])
def api_create_template() -> Response:
    body = _body()
    name = str(body.get("name", "")).strip()
    if not name:
        return _error("A template needs a name.", 400)
    template = store.create_template(name, str(body.get("subject", "")), str(body.get("body", "")))
    return jsonify(template.to_dict())


@app.route("/api/templates/<template_id>", methods=["PATCH"])
def api_update_template(template_id: str) -> Response:
    try:
        return jsonify(store.update_template(template_id, _body()).to_dict())
    except NotFoundError as exc:
        return _error(str(exc), 404)


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def api_delete_template(template_id: str) -> Response:
    try:
        store.delete_template(template_id)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True})


# ---- compose ----------------------------------------------------------------


@app.route("/api/preview", methods=["POST"])
def api_preview() -> Response:
    body = _body()
    try:
        preview = service.build_preview(
            str(body.get("candidate_id", "")),
            str(body.get("template_id", "")),
            str(body.get("inbox_key", "")),
            body.get("body"),
            subject_override=body.get("subject"),
        )
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except CandidateOutreachError as exc:
        return _error(str(exc), 400)
    return jsonify(preview)


# ---- tracking pixel ---------------------------------------------------------


@app.route("/pixel/<token>")
def pixel(token: str) -> Response:
    """Invisible 1x1 image embedded in sent mail; a load records an open.

    Always returns the GIF (even for an unknown token) so a broken image never
    shows in the recipient's client.
    """
    # Queue the hit; the main app drains and records it (single-writer) on the next
    # notifications poll. Keeps candidates.json race-free across the two processes.
    store.append_pending_open(token.removesuffix(".gif"), _now_iso())
    response = Response(_PIXEL_GIF, mimetype="image/gif")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


# ---- notifications ----------------------------------------------------------


@app.route("/api/notifications")
def api_notifications() -> Response:
    # Apply any pixel hits queued by the public pixel service before reporting.
    service.ingest_pending_opens()
    notifications = store.list_notifications()
    return jsonify(
        {
            "notifications": [n.to_dict() for n in notifications],
            "unseen": sum(1 for n in notifications if not n.seen),
        }
    )


@app.route("/api/notifications/seen", methods=["POST"])
def api_notifications_seen() -> Response:
    store.mark_notifications_seen()
    return jsonify({"ok": True})


# ---- settings & mail status -------------------------------------------------


@app.route("/api/settings", methods=["POST"])
def api_settings() -> Response:
    return jsonify(store.save_settings(_body()))


@app.route("/api/contactout-token", methods=["POST"])
def api_contactout_token() -> Response:
    """Store (or, with an empty value, clear) the ContactOut API token.

    The token is write-only from the client's perspective -- it is never returned;
    callers learn only whether one is configured.
    """
    store.save_contactout_token(str(_body().get("token", "")))
    return jsonify({"configured": store.has_contactout_token()})


@app.route("/api/mail-status")
def api_mail_status() -> Response:
    """Best-effort check of whether the mail connection is up and which inbox
    addresses the connected account can actually send as."""
    try:
        account_email = gmail.get_account_email()
        sendable = gmail.list_send_as()
    except SendError as exc:
        return jsonify({"online": False, "error": str(exc), "account_email": None, "sendable": []})
    return jsonify({"online": True, "error": None, "account_email": account_email, "sendable": sendable})


# ---- founder voices ---------------------------------------------------------


@app.route("/api/voices")
def api_voices() -> Response:
    """All inboxes' voice samples, plus the inbox names to label the tabs."""
    return jsonify(
        {
            "voices": store.load_all_voices(),
            "inboxes": [{"key": i.key, "name": i.name} for i in store.load_inboxes()],
            "ideas": store.load_pitch_ideas(),
        }
    )


@app.route("/api/voices/<key>", methods=["POST"])
def api_add_voice_sample(key: str) -> Response:
    text = str(_body().get("text", "")).strip()
    if not text:
        return _error("A voice sample can't be empty.", 400)
    try:
        sample = store.add_voice_sample(key, text)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify(sample)


@app.route("/api/voices/<key>/<sample_id>", methods=["DELETE"])
def api_delete_voice_sample(key: str, sample_id: str) -> Response:
    try:
        store.delete_voice_sample(key, sample_id)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True})


@app.route("/api/personalize", methods=["POST"])
def api_personalize() -> Response:
    """Given dropped links / notes about a candidate, return a one-sentence rationale
    for why to reach out (reads any URLs via the web)."""
    try:
        result = service.generate_personalization(str(_body().get("material", "")))
    except DraftError as exc:
        return _error(str(exc), 502)
    return jsonify(result)


@app.route("/api/voices/<key>/draft", methods=["POST"])
def api_draft_from_voice(key: str) -> Response:
    """Draft a template in this inbox's voice from its stored samples (calls Claude)."""
    try:
        draft = service.draft_template_from_voice(key)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except DraftError as exc:
        # No samples, or the model output couldn't be used -- expected, not a bug.
        return _error(str(exc), 502)
    return jsonify(draft)


# ---- pitch ideas ------------------------------------------------------------


@app.route("/api/pitch-ideas", methods=["POST"])
def api_add_pitch_idea() -> Response:
    text = str(_body().get("text", "")).strip()
    if not text:
        return _error("A pitch idea can't be empty.", 400)
    return jsonify(store.add_pitch_idea(text))


@app.route("/api/pitch-ideas/<idea_id>", methods=["DELETE"])
def api_delete_pitch_idea(idea_id: str) -> Response:
    try:
        store.delete_pitch_idea(idea_id)
    except NotFoundError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True})


@app.route("/api/pitch-ideas/draft", methods=["POST"])
def api_draft_from_ideas() -> Response:
    """Draft a fresh template, or revise an existing one, from stored pitch ideas."""
    body = _body()
    raw_ids = body.get("idea_ids")
    idea_ids = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else []
    try:
        draft = service.draft_template_from_ideas(idea_ids, str(body.get("template_id", "")))
    except NotFoundError as exc:
        return _error(str(exc), 404)
    except DraftError as exc:
        # No ideas, or the model output couldn't be used -- expected, not a bug.
        return _error(str(exc), 502)
    return jsonify(draft)


@app.route("/api/sync-sent", methods=["POST"])
def api_sync_sent() -> Response:
    """Fold in follow-ups the recruiter sent from their own email client."""
    try:
        recorded = service.sync_sent_mail()
    except SendError as exc:
        return jsonify({"online": False, "error": str(exc), "recorded": 0})
    return jsonify({"online": True, "error": None, "recorded": recorded})


@app.route("/api/check-replies", methods=["POST"])
def api_check_replies() -> Response:
    """Scan the connected inbox for candidate replies; flip any repliers to 'replied'."""
    try:
        replied = service.scan_for_replies()
    except SendError as exc:
        # Mail connection down or read not permitted -- expected, not a bug.
        return jsonify({"online": False, "error": str(exc), "replied": []})
    return jsonify({"online": True, "error": None, "replied": replied})


# ---- editable design layer --------------------------------------------------


@app.route("/api/design")
def api_get_design() -> Response:
    return jsonify(store.load_design())


@app.route("/api/design", methods=["POST"])
def api_save_design() -> Response:
    return jsonify(store.save_design(_body()))


@app.route("/api/design/image", methods=["POST"])
def api_upload_design_image() -> Response:
    """Store an uploaded image for an image component; returns its served path."""
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return _error("No image uploaded.", 400)
    raw = uploaded.read()
    if len(raw) > 5_000_000:
        return _error("Image is too large (max 5 MB).", 400)
    name = store.save_design_image(uploaded.filename, raw)
    return jsonify({"src": f"/design-image/{name}"})


@app.route("/design-image/<name>")
def serve_design_image(name: str) -> Response:
    path = store.design_image_path(name)
    if path is None:
        return _error("Not found.", 404)
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    response = Response(path.read_bytes(), mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    # Port defaults to 8082; ORCHARD_PORT overrides it so a throwaway
    # instance can run on a spare port beside the live one during verification.
    port = int(os.environ.get("ORCHARD_PORT", "8082"))
    run_simple("127.0.0.1", port, app, threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
