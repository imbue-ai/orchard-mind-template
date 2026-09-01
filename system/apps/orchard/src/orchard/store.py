"""Persistence for candidates, templates, and the sending-inbox config.

Three JSON files under ``data/.apps/orchard/`` (paths are cwd-relative;
services run from the repo root):

- ``candidates.json`` -- the outreach pipeline (one record per person).
- ``templates.json`` -- reusable email templates; seeded with a few defaults on
  first use.
- ``inboxes.json`` -- the three sending identities; seeded with two example
  teammate aliases and an editable "you" identity on first use.
- ``voices.json`` -- per-inbox writing samples; seeded with two neutral samples
  for "you" on first use so the AI voice-drafting feature has something to work
  with out of the box.

Writes are atomic (write-tmp-then-replace) and corrupt files recover to the
seeded default rather than crashing the app.
"""

import datetime
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from orchard.errors import NotFoundError
from orchard.models import Candidate, Inbox, Notification, Template

logger = logging.getLogger("orchard.store")

# The state dir defaults to data/.apps/orchard relative to the repo
# root; ORCHARD_DATA_DIR overrides it so a throwaway instance can run
# against a copy of the store without touching the user's real data.
_STATE_DIR = Path(os.environ.get("ORCHARD_DATA_DIR", "data/.apps/orchard"))
_CANDIDATES_FILE = _STATE_DIR / "candidates.json"
_TEMPLATES_FILE = _STATE_DIR / "templates.json"
_INBOXES_FILE = _STATE_DIR / "inboxes.json"
_SETTINGS_FILE = _STATE_DIR / "settings.json"
_NOTIFICATIONS_FILE = _STATE_DIR / "notifications.json"
# The ContactOut API token lives in its own file and is never returned to the
# client (the bootstrap payload only exposes a boolean "configured" flag).
_CONTACTOUT_FILE = _STATE_DIR / "contactout.json"
# Per-inbox writing samples the recruiter pastes in to shape AI-drafted templates.
_VOICES_FILE = _STATE_DIR / "voices.json"
# Shared bucket of "how to pitch the company" ideas, turned into templates on demand.
_PITCH_IDEAS_FILE = _STATE_DIR / "pitch_ideas.json"
# Append-only queue of tracking-pixel hits written by the public pixel service.
# The main app drains it and records opens, so candidates.json has a single writer
# (avoids a cross-process race between the pixel service and the app).
_PENDING_OPENS_FILE = _STATE_DIR / "pending_opens.jsonl"
# The editable branding layer (draggable text/image components) and uploaded images.
_DESIGN_FILE = _STATE_DIR / "design.json"
_DESIGN_UPLOADS_DIR = _STATE_DIR / "design-uploads"
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# Seeded on first load so the header looks like it does today, then the user edits
# it freely. Positions are pixels within the design canvas; "builtin:apple" renders
# the built-in apple mark until the user uploads their own image.
_DEFAULT_DESIGN: dict[str, Any] = {
    "components": [
        {"id": "cmp_apple", "type": "image", "src": "builtin:apple", "x": 0, "y": 8, "w": 26},
        {
            "id": "cmp_wordmark", "type": "text", "text": "orchard",
            "x": 38, "y": 12, "size": 18, "weight": 700, "color": "#8C8794", "font": "Space Grotesk",
        },
        {
            "id": "cmp_heading", "type": "text", "text": "fruit basket",
            "x": 0, "y": 46, "size": 38, "weight": 600, "color": "#33372B", "font": "Space Grotesk",
        },
    ]
}

_DEFAULT_SETTINGS: dict[str, str] = {"own_email": "", "own_sender_name": "", "public_base_url": ""}

# Seeded on first use; the "you" address is left blank for the recruiter to fill
# in (their own connected mailbox), while the two extra identities ship as generic
# example teammates the adopter renames to their own colleagues.
_DEFAULT_INBOXES: list[dict[str, str]] = [
    {"key": "you", "name": "You", "address": "", "sender_name": ""},
    {"key": "josh", "name": "Alex", "address": "alex@example.com", "sender_name": "Alex"},
    {"key": "kj", "name": "Sam", "address": "sam@example.com", "sender_name": "Sam"},
]

_DEFAULT_TEMPLATES: list[dict[str, str]] = [
    {
        "name": "Cold intro - engineer",
        "subject": "A role you might like",
        "body": (
            "Hi {{first_name}},\n\n"
            "I came across your work at {{company}} - your background as a {{role}} "
            "really stood out. I'm helping grow our engineering team and thought you "
            "might be a great fit for what we're building.\n\n"
            "Would you be open to a quick chat?\n\n"
            "Best,\n{{sender_name}}"
        ),
    },
    {
        "name": "Follow-up - no reply",
        "subject": "Following up",
        "body": (
            "Hi {{first_name}},\n\n"
            "Circling back on my note below. I know {{company}} keeps you busy, but "
            "I'd love 15 minutes to share what we're working on.\n\n{{sender_name}}"
        ),
    },
]

# Seeded for the "you" inbox on first use so the AI voice-drafting feature can
# demonstrate itself out of the box; the user replaces these with their own
# writing. Two neutral samples in different registers -- one warm/casual, one
# crisp/professional -- with no real people or companies.
_DEFAULT_VOICES: dict[str, list[str]] = {
    "you": [
        (
            "hey - saw the project you shipped recently and thought it was really "
            "cool. would love to grab coffee sometime and hear more about what "
            "you're working on. no agenda, just always like meeting people doing "
            "interesting work."
        ),
        (
            "Hi there - I wanted to reach out because your background caught my eye. "
            "We're growing the team and I think there could be a strong fit. Would "
            "you have 15 minutes this week for a quick call to explore it?"
        ),
    ]
}


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("Corrupt JSON at %s; starting fresh.", path)
        return default


def _write(path: Path, data: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


# ---- settings ---------------------------------------------------------------


def load_settings() -> dict[str, str]:
    return {**_DEFAULT_SETTINGS, **_read(_SETTINGS_FILE, {})}


def save_settings(fields: dict[str, str]) -> dict[str, str]:
    settings = load_settings()
    for key in _DEFAULT_SETTINGS:
        if key in fields:
            settings[key] = str(fields[key]).strip()
    _write(_SETTINGS_FILE, settings)
    return settings


# ---- ContactOut API token ---------------------------------------------------


def load_contactout_token() -> str:
    """The stored ContactOut API token, or empty string if none is set."""
    return str(_read(_CONTACTOUT_FILE, {}).get("token", ""))


def save_contactout_token(token: str) -> None:
    """Store (or, with an empty string, clear) the ContactOut API token."""
    _write(_CONTACTOUT_FILE, {"token": token.strip()})


def has_contactout_token() -> bool:
    return bool(load_contactout_token())


# ---- voice samples ----------------------------------------------------------
# Per-inbox writing samples the recruiter pastes in to shape AI-drafted templates.
# Keyed by inbox key ("you", "josh", "kj"); each sample is {id, text, created_at}.


def _seed_voices() -> dict[str, list[dict[str, str]]]:
    """Write the default voice samples on first use and return them (keyed by inbox)."""
    seeded = {
        key: [{"id": _new_id("vs"), "text": text, "created_at": _now_iso()} for text in texts]
        for key, texts in _DEFAULT_VOICES.items()
    }
    _save_all_voices(seeded)
    return seeded


def load_all_voices() -> dict[str, list[dict[str, str]]]:
    """Every inbox's voice samples, keyed by inbox key (missing keys -> []).

    Seeds a couple of neutral default samples for the 'you' inbox on first use
    (mirroring template seeding) so the AI voice-drafting feature works out of the
    box; the user then replaces them with their own writing."""
    if not _VOICES_FILE.exists():
        voices = _seed_voices()
    else:
        voices = _read(_VOICES_FILE, {"voices": {}}).get("voices", {})
    return {inbox.key: list(voices.get(inbox.key, [])) for inbox in load_inboxes()}


def load_voice_samples(key: str) -> list[dict[str, str]]:
    return load_all_voices().get(key, [])


def _save_all_voices(voices: dict[str, list[dict[str, str]]]) -> None:
    _write(_VOICES_FILE, {"voices": voices})


def add_voice_sample(key: str, text: str) -> dict[str, str]:
    get_inbox(key)  # validate the inbox exists
    sample = {"id": _new_id("vs"), "text": text.strip(), "created_at": _now_iso()}
    voices = load_all_voices()
    voices.setdefault(key, []).append(sample)
    _save_all_voices(voices)
    return sample


def delete_voice_sample(key: str, sample_id: str) -> None:
    voices = load_all_voices()
    samples = voices.get(key, [])
    remaining = [s for s in samples if s.get("id") != sample_id]
    if len(remaining) == len(samples):
        raise NotFoundError(f"No such voice sample: {sample_id}")
    voices[key] = remaining
    _save_all_voices(voices)


# ---- pitch ideas ------------------------------------------------------------
# A shared bucket of ideas for how to pitch the company, dropped in freely and
# later turned into a template (fresh or a revision of an existing one). Not tied
# to an inbox; each idea is {id, text, created_at}.


def load_pitch_ideas() -> list[dict[str, str]]:
    """Every stored pitch idea, oldest first."""
    ideas = _read(_PITCH_IDEAS_FILE, {"ideas": []}).get("ideas", [])
    return list(ideas) if isinstance(ideas, list) else []


def _save_pitch_ideas(ideas: list[dict[str, str]]) -> None:
    _write(_PITCH_IDEAS_FILE, {"ideas": ideas})


def add_pitch_idea(text: str) -> dict[str, str]:
    idea = {"id": _new_id("pi"), "text": text.strip(), "created_at": _now_iso()}
    ideas = load_pitch_ideas()
    ideas.append(idea)
    _save_pitch_ideas(ideas)
    return idea


def delete_pitch_idea(idea_id: str) -> None:
    ideas = load_pitch_ideas()
    remaining = [i for i in ideas if i.get("id") != idea_id]
    if len(remaining) == len(ideas):
        raise NotFoundError(f"No such pitch idea: {idea_id}")
    _save_pitch_ideas(remaining)


# ---- inboxes ----------------------------------------------------------------


def load_inboxes() -> list[Inbox]:
    """The three sending identities, with the recruiter's own address/name
    overlaid from settings so 'you' reflects the connected mailbox."""
    data = _read(_INBOXES_FILE, {"inboxes": _DEFAULT_INBOXES})
    settings = load_settings()
    inboxes: list[Inbox] = []
    for row in data.get("inboxes", _DEFAULT_INBOXES):
        merged = dict(row)
        if merged["key"] == "you":
            merged["address"] = settings["own_email"] or merged.get("address", "")
            merged["sender_name"] = settings["own_sender_name"] or merged.get("sender_name", "")
        inboxes.append(Inbox(**merged))
    return inboxes


def get_inbox(key: str) -> Inbox:
    for inbox in load_inboxes():
        if inbox.key == key:
            return inbox
    raise NotFoundError(f"No such inbox: {key}")


def save_inboxes(inboxes: list[Inbox]) -> None:
    _write(_INBOXES_FILE, {"inboxes": [i.model_dump() for i in inboxes]})


# ---- templates --------------------------------------------------------------


def _seed_templates() -> list[Template]:
    seeded = [
        Template(id=_new_id("tpl"), created_at=_now_iso(), **row) for row in _DEFAULT_TEMPLATES
    ]
    _write(_TEMPLATES_FILE, {"templates": [t.to_dict() for t in seeded]})
    return seeded


def list_templates() -> list[Template]:
    if not _TEMPLATES_FILE.exists():
        return _seed_templates()
    data = _read(_TEMPLATES_FILE, {"templates": []})
    return [Template(**row) for row in data.get("templates", [])]


def get_template(template_id: str) -> Template:
    for template in list_templates():
        if template.id == template_id:
            return template
    raise NotFoundError(f"No such template: {template_id}")


def _save_templates(templates: list[Template]) -> None:
    _write(_TEMPLATES_FILE, {"templates": [t.to_dict() for t in templates]})


def create_template(name: str, subject: str, body: str) -> Template:
    template = Template(
        id=_new_id("tpl"), name=name, subject=subject, body=body, created_at=_now_iso()
    )
    templates = list_templates()
    templates.append(template)
    _save_templates(templates)
    return template


def update_template(template_id: str, fields: dict[str, str]) -> Template:
    templates = list_templates()
    for index, template in enumerate(templates):
        if template.id == template_id:
            updated = template.model_copy(
                update={k: v for k, v in fields.items() if k in {"name", "subject", "body"}}
            )
            templates[index] = updated
            _save_templates(templates)
            return updated
    raise NotFoundError(f"No such template: {template_id}")


def delete_template(template_id: str) -> None:
    templates = list_templates()
    remaining = [t for t in templates if t.id != template_id]
    if len(remaining) == len(templates):
        raise NotFoundError(f"No such template: {template_id}")
    _save_templates(remaining)


# ---- candidates -------------------------------------------------------------


def list_candidates() -> list[Candidate]:
    data = _read(_CANDIDATES_FILE, {"candidates": []})
    return [Candidate(**row) for row in data.get("candidates", [])]


def get_candidate(candidate_id: str) -> Candidate:
    for candidate in list_candidates():
        if candidate.id == candidate_id:
            return candidate
    raise NotFoundError(f"No such candidate: {candidate_id}")


def find_by_token(token: str) -> Candidate | None:
    """The candidate whose current outreach carries this tracking token, if any."""
    if not token:
        return None
    for candidate in list_candidates():
        if candidate.track_token == token:
            return candidate
    return None


def _save_candidates(candidates: list[Candidate]) -> None:
    _write(_CANDIDATES_FILE, {"candidates": [c.model_dump() for c in candidates]})


_EDITABLE_FIELDS = {
    "name",
    "company",
    "role",
    "email",
    "linkedin",
    "notes",
    "personalization",
    "stage",
    "channel",
    "sent_at",
    "opened_at",
    "last_open_at",
    "replied_at",
    "from_inbox",
    "last_template_id",
    "track_token",
    "open_count",
    "raw",
    "sends",
}

# Fields the web UI is allowed to set directly. Tracking internals (token,
# counts, timestamps) are managed by the service, never posted by the client.
UI_EDITABLE_FIELDS = {
    "name", "company", "role", "email", "linkedin", "notes", "personalization", "stage", "channel"
}


def _stamp_reached_out(candidate: Candidate) -> Candidate:
    """When a candidate is moved to the 'reached_out' stage but has no sent date yet
    (e.g. reached via LinkedIn, not the app), stamp one so the timeline shows a date."""
    if candidate.stage == "reached_out" and not candidate.sent_at:
        return candidate.model_copy(update={"sent_at": _now_iso()})
    return candidate


def create_candidate(fields: dict[str, Any]) -> Candidate:
    payload = {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS}
    candidate = _stamp_reached_out(Candidate(id=_new_id("cand"), created_at=_now_iso(), **payload))
    candidates = list_candidates()
    candidates.insert(0, candidate)
    _save_candidates(candidates)
    return candidate


def update_candidate(candidate_id: str, fields: dict[str, Any]) -> Candidate:
    candidates = list_candidates()
    for index, candidate in enumerate(candidates):
        if candidate.id == candidate_id:
            updated = _stamp_reached_out(
                candidate.model_copy(update={k: v for k, v in fields.items() if k in _EDITABLE_FIELDS})
            )
            candidates[index] = updated
            _save_candidates(candidates)
            return updated
    raise NotFoundError(f"No such candidate: {candidate_id}")


def delete_candidate(candidate_id: str) -> None:
    candidates = list_candidates()
    remaining = [c for c in candidates if c.id != candidate_id]
    if len(remaining) == len(candidates):
        raise NotFoundError(f"No such candidate: {candidate_id}")
    _save_candidates(remaining)


# ---- notifications ----------------------------------------------------------


def list_notifications() -> list[Notification]:
    data = _read(_NOTIFICATIONS_FILE, {"notifications": []})
    return [Notification(**row) for row in data.get("notifications", [])]


def _save_notifications(notifications: list[Notification]) -> None:
    _write(_NOTIFICATIONS_FILE, {"notifications": [n.to_dict() for n in notifications]})


def add_notification(candidate: Candidate, opened_at: str) -> Notification:
    notification = Notification(
        id=_new_id("ntf"),
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        inbox=candidate.from_inbox,
        opened_at=opened_at,
        open_count=candidate.open_count,
    )
    notifications = list_notifications()
    notifications.insert(0, notification)
    _save_notifications(notifications)
    return notification


def mark_notifications_seen() -> None:
    notifications = list_notifications()
    for notification in notifications:
        notification.seen = True
    _save_notifications(notifications)


# ---- pending open events (written by the public pixel service) --------------


def append_pending_open(token: str, at: str) -> None:
    """Record a tracking-pixel hit to the queue (append-only, no candidate write)."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PENDING_OPENS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"token": token, "at": at}) + "\n")


def load_design() -> dict[str, Any]:
    """The editable branding layer, seeded with the default header on first load."""
    data = _read(_DESIGN_FILE, {})
    components = data.get("components")
    if not isinstance(components, list) or not components:
        return {"components": [dict(c) for c in _DEFAULT_DESIGN["components"]]}
    return {"components": components}


def save_design(design: dict[str, Any]) -> dict[str, Any]:
    """Persist the whole component list (the client sends the full design)."""
    components = design.get("components")
    if not isinstance(components, list):
        components = []
    _write(_DESIGN_FILE, {"components": components})
    return {"components": components}


def save_design_image(filename: str, raw: bytes) -> str:
    """Store an uploaded image and return the safe name it is served under."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        ext = ".png"
    name = _new_id("img") + ext
    _DESIGN_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (_DESIGN_UPLOADS_DIR / name).write_bytes(raw)
    return name


def design_image_path(name: str) -> Path | None:
    """The on-disk path for an uploaded design image, or None if the name is unsafe
    or missing (guards against path traversal in the served name)."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = _DESIGN_UPLOADS_DIR / name
    return path if path.exists() else None


def drain_pending_opens() -> list[dict[str, str]]:
    """Atomically take everything queued so far and return it, clearing the queue.

    Renames the file first so pixel hits arriving mid-drain land in a fresh queue
    rather than being lost or double-counted."""
    if not _PENDING_OPENS_FILE.exists():
        return []
    draining = _PENDING_OPENS_FILE.with_suffix(".draining")
    _PENDING_OPENS_FILE.replace(draining)
    events: list[dict[str, str]] = []
    for line in draining.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping unreadable pending-open line: %s", line[:120])
    draining.unlink()
    return events
