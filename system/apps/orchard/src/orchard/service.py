"""Orchestration: token rendering, previews, and recording outreach.

This is the seam the Flask routes call. Personalization and message assembly
are pure functions (no network) so they are fully testable offline; actually
delivering mail and detecting opens is layered on top later, once the user's
mail connection is verified.
"""

import base64
import datetime
import email.utils
import html
import json
import logging
import re
import uuid
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from orchard import ai, contactout, gmail, store
from orchard.errors import DraftError, SendError
from orchard.models import Candidate, Inbox, Notification

logger = logging.getLogger("orchard.service")

_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


def _first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.split() else full_name


def _last_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[-1] if len(parts) > 1 else ""


def token_values(candidate: Candidate, inbox: Inbox) -> dict[str, str]:
    """The values each ``{{token}}`` resolves to for one candidate + sender."""
    return {
        "first_name": _first_name(candidate.name),
        "last_name": _last_name(candidate.name),
        "full_name": candidate.name,
        "company": candidate.company,
        "role": candidate.role,
        "sender_name": inbox.sender_name,
        "personalization": candidate.personalization,
    }


def render_text(text: str, values: dict[str, str]) -> str:
    """Fill known ``{{token}}`` markers; leave unknown ones untouched so a typo
    is visible in the preview rather than silently dropped."""
    return _TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def unresolved_tokens(text: str, values: dict[str, str]) -> list[str]:
    """Tokens that are unknown, or known but empty -- what the user should fix
    before sending."""
    missing: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        name = match.group(1)
        if not values.get(name):
            missing.append(match.group(0))
    return sorted(set(missing))


def build_preview(
    candidate_id: str,
    template_id: str,
    inbox_key: str,
    body_override: str | None,
    subject_override: str | None = None,
) -> dict[str, Any]:
    """Render the email a candidate would receive.

    ``body_override`` / ``subject_override`` let the compose UI preview edits made
    in the box (and the subject line) before they are saved back to the template.
    """
    candidate = store.get_candidate(candidate_id)
    template = store.get_template(template_id) if template_id else None
    inbox = store.get_inbox(inbox_key)
    values = token_values(candidate, inbox)
    body = body_override if body_override is not None else (template.body if template else "")
    subject = subject_override if subject_override is not None else (template.subject if template else "")
    return {
        "subject": render_text(subject, values),
        "body": render_text(body, values),
        "to": candidate.email,
        "to_name": candidate.name,
        "from_name": inbox.name,
        "from_address": inbox.address,
        "unresolved": unresolved_tokens(subject + "\n" + body, values),
    }


def build_raw_message(to_address: str, from_address: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC-822 message for the Gmail send API.

    Pure and testable offline; the actual send call (which needs the live mail
    connection) is layered on in the sending step.
    """
    message = EmailMessage()
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _advanced_stage(current: str) -> str:
    """The stage after an outreach: at least 'reached_out', but never regress a
    later manual stage the recruiter already set (replied / not a fit)."""
    return current if current in ("replied", "not_interested") else "reached_out"


def mark_reached_out(candidate_id: str, inbox_key: str, template_id: str, sent_at: str) -> Candidate:
    """Record that the recruiter reached out to a candidate by hand.

    A manual fallback (e.g. while the mail connection is down) -- it records the
    outreach but sets no tracking pixel, so opens can't be detected for it.
    """
    candidate = store.get_candidate(candidate_id)
    store.get_inbox(inbox_key)  # validate the inbox exists
    return store.update_candidate(
        candidate_id,
        {
            "sent_at": sent_at,
            "from_inbox": inbox_key,
            "last_template_id": template_id,
            "stage": _advanced_stage(candidate.stage),
        },
    )


def pixel_url(base_url: str, token: str) -> str:
    """The public URL of the tracking pixel for one outreach."""
    return f"{base_url.rstrip('/')}/pixel/{token}.gif"


def _re_subject(subject: str) -> str:
    """Prefix ``Re:`` for a reply, without doubling it."""
    trimmed = subject.strip()
    return trimmed if trimmed[:3].lower() == "re:" else f"Re: {trimmed}"


# Markdown-style links [text](url) that the template/voice editors insert. Only
# http(s) and mailto URLs are turned into real anchors; anything else is left as
# literal text so a crafted scheme (e.g. javascript:) can never reach a recipient.
_MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_LINK_SCHEMES: tuple[str, ...] = ("http://", "https://", "mailto:")


def _is_safe_link(url: str) -> bool:
    return url.lower().startswith(_SAFE_LINK_SCHEMES)


def render_body_html(body: str) -> str:
    """Escape a message body for an HTML email, turning [text](url) links into anchors."""
    parts: list[str] = []
    last = 0
    for match in _MARKDOWN_LINK_RE.finditer(body):
        parts.append(html.escape(body[last : match.start()]))
        text, url = match.group(1), match.group(2)
        if _is_safe_link(url):
            parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>')
        else:
            parts.append(html.escape(match.group(0)))
        last = match.end()
    parts.append(html.escape(body[last:]))
    return "".join(parts)


def render_body_plain(body: str) -> str:
    """Flatten [text](url) links to 'text (url)' for the plain-text part of the email."""

    def _flatten(match: re.Match[str]) -> str:
        text, url = match.group(1), match.group(2)
        if not _is_safe_link(url):
            return match.group(0)
        return text if text == url else f"{text} ({url})"

    return _MARKDOWN_LINK_RE.sub(_flatten, body)


def build_html_message(
    to_address: str, from_address: str, subject: str, body: str,
    pixel_src: str | None = None, in_reply_to: str | None = None,
) -> str:
    """Build a base64url RFC-822 message (plain + HTML).

    When ``pixel_src`` is given, an invisible tracking pixel is appended to the
    HTML part; when it is ``None`` the mail is sent without tracking. When
    ``in_reply_to`` (a prior message's RFC Message-ID) is given, threading headers
    are set so the recipient's client shows it as a reply in the same conversation.
    """
    message = EmailMessage()
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(render_body_plain(body))
    pixel = (
        f'<img src="{html.escape(pixel_src, quote=True)}" width="1" height="1" alt="" '
        'style="display:none;max-height:0;overflow:hidden">'
        if pixel_src
        else ""
    )
    html_body = f'<div style="white-space:pre-wrap;font-family:sans-serif">{render_body_html(body)}</div>{pixel}'
    message.add_alternative(html_body, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


SendFn = Callable[..., dict[str, str]]
FetchMessageIdFn = Callable[[str], str]


def send_email(
    candidate_id: str,
    inbox_key: str,
    template_id: str,
    body_override: str | None,
    now: str,
    send_fn: SendFn = gmail.send_raw,
    subject_override: str | None = None,
    reply_to: dict[str, Any] | None = None,
    fetch_message_id: FetchMessageIdFn = gmail.get_rfc_message_id,
) -> dict[str, Any]:
    """Send a personalized, open-tracked email and record the outreach.

    ``body_override`` / ``subject_override`` carry edits from the compose box; each
    falls back to the template when None. When ``reply_to`` (a prior send record with
    ``thread_id``/``message_id``/``subject``) is given, the email is threaded onto that
    conversation with a ``Re:`` subject. Every send is appended to the candidate's
    ``sends`` history. Raises ``SendError`` when a precondition is missing."""
    candidate = store.get_candidate(candidate_id)
    inbox = store.get_inbox(inbox_key)
    # ``template_id`` is empty when the recruiter picked "write from scratch"; the
    # compose box always supplies body/subject overrides in that case, so there is
    # no template to fall back to and we must not try to load one.
    template = store.get_template(template_id) if template_id else None
    settings = store.load_settings()

    if not inbox.address:
        raise SendError(f"The {inbox.name} inbox has no address set yet -- add it in settings.")
    if not candidate.email:
        raise SendError(f"{candidate.name} has no email address yet.")

    values = token_values(candidate, inbox)
    body = body_override if body_override is not None else (template.body if template else "")
    subject_template = subject_override if subject_override is not None else (template.subject if template else "")
    subject = render_text(subject_template, values)
    rendered_body = render_text(body, values)
    if not rendered_body.strip():
        raise SendError("Write a message first -- the email body is empty.")
    if reply_to:
        # A reply keeps the original conversation's subject, prefixed with Re:.
        subject = _re_subject(reply_to.get("subject") or subject)

    # Open tracking is added only when a public link is configured; without it we
    # still send, just without the pixel (so sending is never blocked on setup).
    base_url = settings["public_base_url"].strip()
    token = uuid.uuid4().hex if base_url else None
    pixel = pixel_url(base_url, token) if token else None
    in_reply_to = reply_to.get("message_id") if reply_to else None
    thread_id_in = reply_to.get("thread_id") if reply_to else None
    raw = build_html_message(candidate.email, inbox.address, subject, rendered_body, pixel, in_reply_to=in_reply_to)
    sent = send_fn(raw, thread_id_in)

    thread_id = sent.get("thread_id") or thread_id_in or ""
    try:
        rfc_id = fetch_message_id(sent["id"]) if sent.get("id") else ""
    except SendError:
        rfc_id = ""  # threading still works via thread_id; the header is a nicety
    record = {
        "inbox_key": inbox_key, "subject": subject, "body": rendered_body,
        "sent_at": now, "thread_id": thread_id, "message_id": rfc_id,
        "gmail_id": sent.get("id", ""), "source": "orchard",
    }
    updated = store.update_candidate(
        candidate_id,
        {
            "sent_at": now,
            "from_inbox": inbox_key,
            "last_template_id": template_id,
            "track_token": token,
            "opened_at": None,
            "open_count": 0,
            "stage": _advanced_stage(candidate.stage),
            "sends": [*candidate.sends, record],
        },
    )
    return {
        "candidate": updated.to_dict(), "message_id": sent.get("id", ""),
        "tracked": bool(token), "threaded": bool(reply_to),
    }


LookupFn = Callable[[str, str], dict[str, Any]]


def find_candidate_email(candidate_id: str, lookup: LookupFn = contactout.lookup_emails) -> dict[str, Any]:
    """Look up a candidate's email from their LinkedIn URL via ContactOut.

    Stores the raw ContactOut payload on the candidate (so a later change needs no
    refetch) and fills ``email`` when it was blank -- it never overwrites an address
    the recruiter already has; the found addresses are returned for them to choose.
    Raises ``EnrichError`` (surfaced to the user) on any precondition or API failure.
    """
    candidate = store.get_candidate(candidate_id)
    token = store.load_contactout_token()
    result = lookup(candidate.linkedin, token)
    everything = result["all"]
    chosen = everything[0] if everything else ""
    raw = dict(candidate.raw)
    raw["contactout"] = result["profile"]
    fields: dict[str, Any] = {"raw": raw}
    applied = ""
    if chosen and not candidate.email:
        fields["email"] = chosen
        applied = chosen
    updated = store.update_candidate(candidate_id, fields)
    return {
        "candidate": updated.to_dict(),
        "emails": everything,
        "work": result["work"],
        "personal": result["personal"],
        "applied": applied,
    }


# ---- voice-drafted templates ------------------------------------------------

_VOICE_SYSTEM = (
    "You help a recruiter write cold-outreach email templates that sound like they "
    "were written by a specific person -- reproducing their real, unpolished email "
    "voice, not a clean marketing version of it. You are given writing samples from "
    "that person. Write ONE short recruiting outreach email template in their voice.\n\n"
    "Mirror the samples exactly, and do NOT clean them up:\n"
    "- Match their capitalization habits. If they write in lowercase or skip "
    "capital letters, do the same -- do not tidy it up.\n"
    "- Match their punctuation, contractions, dashes, fragments, and any slang or "
    "filler they use. Keep sentences as short and rough as theirs.\n"
    "- Match their length. Most casual senders write 2-4 short sentences; if the "
    "samples are terse, be terse.\n"
    "- Avoid recruiter/marketing cliches and polish that are NOT in the samples "
    "(e.g. 'I'd love to', 'reach out', 'excited about', 'really stood out', "
    "'production-grade', 'passionate') -- these read as generated. Only use phrasing "
    "the person actually uses.\n"
    "- Match their register precisely, in BOTH directions: don't polish them up, "
    "but don't exaggerate their casualness either. If the samples are relaxed but "
    "still real emails, write a relaxed real email -- not a caricature of slang, "
    "abbreviations, or terseness. Aim for something the person would actually send "
    "and recognize as their own.\n\n"
    "The email is FROM the sender TO a candidate they want to recruit. The ask is "
    "a low-key invitation to connect -- a quick coffee or lunch -- worked in "
    "naturally in the sender's own voice; do not make it sound like a formal event. "
    "Never use the word 'pitch' (or 'pitching') anywhere in the subject or body.\n\n"
    "Use these placeholder tokens, filled per candidate at send time; each means a "
    "specific thing, so only use them for that meaning:\n"
    "- {{first_name}}: the candidate's first name\n"
    "- {{company}}: the candidate's own current or recent employer (NOT the sender's company)\n"
    "- {{role}}: the candidate's own job title (NOT the role being hired for)\n"
    "- {{sender_name}}: the sender's name, for the sign-off\n"
    "- {{personalization}}: a bespoke line the recruiter writes for each candidate "
    "(e.g. a specific detail about their work). Optional -- include it near the "
    "opening as its own sentence only if a personalized touch fits the voice; if you "
    "use it, place the {{personalization}} token on its own, do not write the detail "
    "yourself.\n"
    "Do not invent tokens beyond these.\n\n"
    "Respond with ONLY a JSON object (no prose, no code fences) of the form "
    '{"subject": "...", "body": "..."}, using \\n for line breaks in the body.'
)

CompleteFn = Callable[..., ai.Completion]


def _voice_prompt(name: str, samples: list[str]) -> str:
    joined = "\n\n---\n\n".join(samples)
    return (
        f"The sender's name is {name}. Here are writing samples from {name}:\n\n"
        f"{joined}\n\n"
        f"Now write one recruiting outreach email template in {name}'s voice."
    )


def _extract_json(text: str) -> str:
    """Pull the first {...} object out of a model response (tolerating fences/prose)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise DraftError("The AI draft wasn't in the expected format. Please try again.")
    return text[start : end + 1]


def draft_template_from_voice(key: str, complete: CompleteFn = ai.complete) -> dict[str, Any]:
    """Draft a template in an inbox's voice from its stored writing samples.

    Returns ``{name, subject, body, cost_usd}`` -- a draft the UI shows for the user
    to edit and save; it is not persisted as a template here. Raises ``DraftError``
    when there are no samples or the model output can't be used.
    """
    inbox = store.get_inbox(key)  # validates the key (NotFoundError -> 404)
    samples = [s["text"] for s in store.load_voice_samples(key) if s.get("text")]
    if not samples:
        raise DraftError(f"Add at least one writing sample for {inbox.name} first.")
    result = complete(_voice_prompt(inbox.name, samples), _VOICE_SYSTEM)
    try:
        parsed = json.loads(_extract_json(result.text))
    except (ValueError, TypeError) as exc:
        raise DraftError("The AI draft wasn't in the expected format. Please try again.") from exc
    subject = str(parsed.get("subject", "")).strip()
    body = str(parsed.get("body", "")).strip()
    if not subject or not body:
        raise DraftError("The AI draft came back missing a subject or body. Please try again.")
    return {"name": f"{inbox.name} — voice draft", "subject": subject, "body": body, "cost_usd": result.cost_usd}


# ---- pitch ideas -> template ------------------------------------------------

_IDEAS_SYSTEM = (
    "You help a recruiter turn rough ideas about how to pitch their company into a short "
    "cold-outreach email template they can reuse. You are given a list of ideas (angles, "
    "selling points, things to emphasize about the company) and, optionally, an existing "
    "template to revise.\n\n"
    "If an existing template is given, REVISE it: keep its overall shape, tone, and any "
    "sign-off, and work the ideas in naturally -- do not rewrite it from scratch. If no "
    "template is given, write ONE fresh template built around the ideas.\n\n"
    "Write like a real person: warm, natural, casual but not sloppy -- not marketing copy. "
    "Keep it short, like a real cold email. The email is FROM the sender TO a candidate they "
    "want to recruit; where it fits, offer a low-key invitation to connect (a quick coffee "
    "or lunch). Rules: no buzzwords or hype; never the word 'pitch' (or 'pitching') anywhere "
    "in the subject or body; no long dashes (em/en).\n\n"
    "Use these placeholder tokens, filled per candidate at send time; each means a specific "
    "thing, so only use them for that meaning:\n"
    "- {{first_name}}: the candidate's first name\n"
    "- {{company}}: the candidate's own current or recent employer (NOT the sender's company)\n"
    "- {{role}}: the candidate's own job title (NOT the role being hired for)\n"
    "- {{sender_name}}: the sender's name, for the sign-off\n"
    "- {{personalization}}: an optional bespoke line the recruiter writes per candidate; if "
    "you use it, place the {{personalization}} token on its own and do not write the detail "
    "yourself.\n"
    "Do not invent tokens beyond these.\n\n"
    "Respond with ONLY a JSON object (no prose, no code fences) of the form "
    '{"subject": "...", "body": "..."}, using \\n for line breaks in the body.'
)


def _ideas_prompt(ideas: list[str], base: Any = None) -> str:
    joined = "\n".join(f"- {idea}" for idea in ideas)
    parts = [f"Ideas for how to pitch the company:\n{joined}"]
    if base is not None:
        parts.append(f"Existing template to revise:\nSubject: {base.subject}\n\n{base.body}")
        parts.append("Revise the template above, working these ideas in naturally.")
    else:
        parts.append("Write one fresh recruiting outreach email template built around these ideas.")
    return "\n\n".join(parts)


def draft_template_from_ideas(
    idea_ids: list[str], template_id: str = "", complete: CompleteFn = ai.complete
) -> dict[str, Any]:
    """Draft (or revise) a template from stored pitch ideas.

    ``idea_ids`` picks which stored ideas to use (empty -> all of them). When
    ``template_id`` is given, the draft revises that template -- its name is reused and
    ``target_template_id`` echoes it back so the UI can update it in place; otherwise the
    draft is a fresh template. Returns ``{name, subject, body, cost_usd,
    target_template_id}``. Raises ``DraftError`` when there are no ideas to work from,
    or ``NotFoundError`` when ``template_id`` is unknown.
    """
    wanted = set(idea_ids)
    all_ideas = store.load_pitch_ideas()
    chosen = [i for i in all_ideas if i.get("id") in wanted] if wanted else all_ideas
    texts = [i["text"] for i in chosen if i.get("text")]
    if not texts:
        raise DraftError("Add at least one pitch idea first.")
    base = store.get_template(template_id) if template_id else None  # NotFoundError -> 404
    result = complete(_ideas_prompt(texts, base), _IDEAS_SYSTEM)
    try:
        parsed = json.loads(_extract_json(result.text))
    except (ValueError, TypeError) as exc:
        raise DraftError("The AI draft wasn't in the expected format. Please try again.") from exc
    subject = str(parsed.get("subject", "")).strip()
    body = str(parsed.get("body", "")).strip()
    if not subject or not body:
        raise DraftError("The AI draft came back missing a subject or body. Please try again.")
    return {
        "name": base.name if base else "Pitch idea draft",
        "subject": subject,
        "body": body,
        "cost_usd": result.cost_usd,
        "target_template_id": template_id,
    }


# ---- auto-draft an outreach / follow-up email -------------------------------

_DRAFT_SYSTEM = (
    "You draft a recruiting email for a recruiter, in the sender's own voice: human, warm, "
    "and casual but not sloppy -- like a real person wrote it, not marketing.\n\n"
    "You are given: the sender's name; optional writing samples that define their voice "
    "(mimic their tone, rhythm, capitalization, and sign-off if present); the candidate's "
    "details; an optional personalized line about the candidate; and whether this is a "
    "first outreach or a follow-up (with the previous email, if any).\n\n"
    "Write the email. For a first outreach: a short, genuine note that opens with the "
    "personalized detail if given, connects lightly to the sender's company, and offers a "
    "low-key invitation to connect (a quick coffee or lunch). For a follow-up: a brief, "
    "friendly nudge that references the earlier note without repeating it, and gently "
    "re-invites. Rules: keep it short (a real cold email); no buzzwords or hype; never the "
    "word 'pitch'; no long dashes (em/en); sign off with the sender's name.\n\n"
    "Respond with ONLY a JSON object: {\"subject\": \"...\", \"body\": \"...\"}, using \\n for "
    "line breaks in the body. For a follow-up, the subject may be empty (it will reuse the "
    "original)."
)


def draft_outreach(
    candidate_id: str,
    inbox_key: str,
    follow_up: bool = False,
    complete: CompleteFn = ai.complete,
) -> dict[str, Any]:
    """Draft a reach-out (or follow-up) email in the sender's voice for one candidate."""
    candidate = store.get_candidate(candidate_id)
    inbox = store.get_inbox(inbox_key)
    samples = [s["text"] for s in store.load_voice_samples(inbox_key) if s.get("text")]
    prior = candidate.sends[-1] if (follow_up and candidate.sends) else None

    parts = [f"Sender: {inbox.sender_name or inbox.name}"]
    if samples:
        parts.append("Writing samples that define the sender's voice:\n" + "\n\n---\n\n".join(samples))
    else:
        parts.append("(No writing samples provided -- use a warm, natural, lightly casual voice.)")
    detail = ", ".join(p for p in [candidate.name, candidate.role, candidate.company] if p)
    parts.append(f"Candidate: {detail or candidate.name}")
    if candidate.personalization:
        parts.append(f"Personalized detail about them: {candidate.personalization}")
    if follow_up:
        prev = f"Subject: {prior['subject']}\n\n{prior['body']}" if prior else "(the earlier note isn't recorded)"
        parts.append(f"This is a FOLLOW-UP. They haven't replied. The earlier email was:\n{prev}")
    else:
        parts.append("This is a FIRST outreach.")

    result = complete("\n\n".join(parts), _DRAFT_SYSTEM)
    try:
        parsed = json.loads(_extract_json(result.text))
    except (ValueError, TypeError) as exc:
        raise DraftError("The draft came back in an unexpected format. Please try again.") from exc
    body = re.sub(r"\s*[—–]\s*", ", ", str(parsed.get("body", "")).strip())
    subject = re.sub(r"\s*[—–]\s*", ", ", str(parsed.get("subject", "")).strip())
    if not body:
        raise DraftError("The draft came back empty. Please try again.")
    return {"subject": subject, "body": body, "cost_usd": result.cost_usd}


# ---- personalization: why reach out --------------------------------------

_PERSONALIZE_SYSTEM = (
    "You write a short, personalized line a recruiter can paste straight into "
    "a cold outreach email to a candidate -- the bit that shows you actually looked at "
    "their work and says why you're reaching out to them specifically.\n\n"
    "You are given links (a personal site, GitHub, a profile) and/or notes about ONE "
    "candidate. Use the WebFetch tool to read every URL provided, and WebSearch only if "
    "you're given a bare name or handle. Find a specific, real detail about their work "
    "or interests.\n\n"
    "Then write a SHORT fragment, in the first person addressed to the candidate, that "
    "names that concrete detail and hints at why it fits the sender's company -- warm, "
    "natural, and human, the way a real person types, not marketing copy. Rules: count "
    "the words and "
    "use AT MOST 9 (aim for 6-8); specific, never generic flattery; no buzzwords; never "
    "the word 'pitch'; NO long dashes (no em or en dashes) -- an ordinary hyphen inside a "
    "word like 'open-source' is fine; it should read naturally right after an email "
    "greeting. Respond with ONLY the fragment -- no preamble, no quotes, no markdown."
)


def generate_personalization(material: str, research: Any = ai.research) -> dict[str, Any]:
    """Given dropped links / notes about a candidate, return a one-sentence rationale
    for why to reach out. Raises DraftError if there's nothing to work with."""
    material = (material or "").strip()
    if not material:
        raise DraftError("Add a link or some notes about the person first.")
    result = research(f"Here are the links and/or notes about the candidate:\n\n{material}", _PERSONALIZE_SYSTEM)
    # Belt-and-suspenders: strip any long dashes the model still slips in, and
    # collapse whitespace so the line is clean to paste.
    cleaned = re.sub(r"\s*[—–]\s*", ", ", result.text.strip())
    sentence = " ".join(cleaned.split()).strip(" ,")
    if not sentence:
        raise DraftError("The AI couldn't find enough to go on. Add a bit more about the person.")
    return {"sentence": sentence, "cost_usd": result.cost_usd}


# ---- reply detection --------------------------------------------------------

ReplyFinder = Callable[[str], dict[str, Any] | None]


def _iso_to_ms(iso: str) -> int:
    return int(datetime.datetime.fromisoformat(iso).timestamp() * 1000)


def _ms_to_iso(date_ms: int) -> str:
    return datetime.datetime.fromtimestamp(date_ms / 1000, tz=datetime.timezone.utc).isoformat()


def scan_for_replies(find_from: ReplyFinder = gmail.find_latest_from) -> list[dict[str, Any]]:
    """Detect candidates who replied, by looking for a message from them in the
    connected inbox that arrived after we reached out. Flips each to the 'replied'
    stage with a ``replied_at`` date and returns the newly-replied candidates.

    Only scans candidates currently at 'reached_out' with an email and a sent date;
    a manual 'replied' / 'not a fit' is left alone. Replies to founder-alias sends
    land in those founders' own inboxes, so they aren't visible here.
    """
    newly_replied: list[dict[str, Any]] = []
    for candidate in store.list_candidates():
        if (
            candidate.pipeline_stage != "reached_out"
            or candidate.replied_at
            or not candidate.email
            or not candidate.sent_at
        ):
            continue
        latest = find_from(candidate.email)
        if latest and latest["date_ms"] > _iso_to_ms(candidate.sent_at):
            updated = store.update_candidate(
                candidate.id, {"stage": "replied", "replied_at": _ms_to_iso(latest["date_ms"])}
            )
            newly_replied.append(updated.to_dict())
    return newly_replied


# A repeat open within this window of the previous one is treated as the same
# view (mail clients often prefetch/re-load the pixel in bursts), so it counts but
# does not fire another notification.
_REOPEN_NOTIFY_GAP_SECONDS = 300


def _seconds_between(earlier_iso: str | None, later_iso: str) -> float:
    """Seconds from ``earlier_iso`` to ``later_iso``; a huge number if unknown."""
    if not earlier_iso:
        return float("inf")
    try:
        earlier = datetime.datetime.fromisoformat(earlier_iso)
        later = datetime.datetime.fromisoformat(later_iso)
    except ValueError:
        return float("inf")
    return (later - earlier).total_seconds()


SentListFn = Callable[[str], list[dict[str, Any]]]


def sync_sent_mail(list_sent: SentListFn = gmail.list_sent_to) -> int:
    """Fold emails the recruiter sent from their own client into each engaged
    candidate's send history, so follow-ups sent outside Orchard still show up.

    Scans candidates already in the pipeline (not 'not_contacted') for messages the
    connected inbox sent to them, and records any not already known. Only the
    connected inbox is visible, so mail sent from a founder's own account is not
    seen. Returns how many sends were newly recorded."""
    inboxes = store.load_inboxes()
    addr_to_key = {i.address.lower(): i.key for i in inboxes if i.address}
    recorded = 0
    for candidate in store.list_candidates():
        if not candidate.email or candidate.pipeline_stage == "not_contacted":
            continue
        try:
            messages = list_sent(candidate.email)
        except SendError:
            break  # mailbox unreadable / offline -- stop the whole sync
        seen_ids = {s.get("gmail_id") for s in candidate.sends if s.get("gmail_id")}
        seen_thread_subj = {(s.get("thread_id"), s.get("subject")) for s in candidate.sends}
        new_records = []
        for m in messages:
            if m.get("gmail_id") and m["gmail_id"] in seen_ids:
                continue
            if (m.get("thread_id"), m.get("subject")) in seen_thread_subj:
                continue
            new_records.append({
                "inbox_key": addr_to_key.get(email.utils.parseaddr(m.get("from_addr", ""))[1].lower(), "you"),
                "subject": m.get("subject", ""), "body": m.get("snippet", ""),
                "sent_at": _ms_to_iso(int(m.get("date_ms", 0))), "thread_id": m.get("thread_id", ""),
                "message_id": "", "gmail_id": m.get("gmail_id", ""), "source": "gmail",
            })
        if not new_records:
            continue
        merged = sorted([*candidate.sends, *new_records], key=lambda s: s.get("sent_at", ""))
        store.update_candidate(candidate.id, {"sends": merged})
        recorded += len(new_records)
    return recorded


def record_open(token: str, now: str) -> tuple[Candidate | None, Notification | None]:
    """Record a tracking-pixel hit. Returns the affected candidate (if the token is
    known) and a notification.

    Notifies on the first open and on later re-opens, but debounces repeats that
    arrive within ``_REOPEN_NOTIFY_GAP_SECONDS`` of the previous open (automated
    image prefetches load the pixel in bursts and shouldn't each ping you). Every
    hit still increments ``open_count``; ``opened_at`` stays the first open."""
    candidate = store.find_by_token(token)
    if candidate is None:
        return None, None
    first_open = candidate.opened_at is None
    notify = first_open or _seconds_between(candidate.last_open_at, now) >= _REOPEN_NOTIFY_GAP_SECONDS
    fields: dict[str, Any] = {"open_count": candidate.open_count + 1, "last_open_at": now}
    if first_open:
        fields["opened_at"] = now
    updated = store.update_candidate(candidate.id, fields)
    notification = store.add_notification(updated, now) if notify else None
    return updated, notification


def ingest_pending_opens() -> int:
    """Apply queued pixel hits (written by the public pixel service) to candidates.

    Runs in the single-writer main process, so recording opens here never races the
    user's own edits. Returns how many hits were applied."""
    events = store.drain_pending_opens()
    for event in events:
        token = str(event.get("token", "")).removesuffix(".gif")
        record_open(token, str(event.get("at", "")) or _now_iso_fallback())
    return len(events)


def _now_iso_fallback() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
