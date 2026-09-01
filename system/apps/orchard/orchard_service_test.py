"""Unit tests for token rendering, previews, and message construction."""

import base64
import email
import email.policy
from pathlib import Path
from typing import Any

import pytest
from orchard import ai, service, store
from orchard.errors import NotFoundError, SendError
from orchard.models import Candidate, Inbox

_INBOX = Inbox(key="kj", name="Sam", address="sam@example.com", sender_name="Sam")


def _fake_send(raw: str, thread_id: str | None = None) -> dict[str, str]:
    return {"id": "m", "thread_id": thread_id or "t"}


def _no_msgid(_gmail_id: str) -> str:
    return ""


def _candidate(**overrides: Any) -> Candidate:
    fields: dict[str, Any] = {
        "id": "cand_1",
        "name": "Ada Lovelace",
        "company": "Anthropic",
        "role": "Research Engineer",
        "email": "ada@example.com",
        "created_at": "2026-08-12T00:00:00+00:00",
    }
    fields.update(overrides)
    return Candidate(**fields)


def test_token_values_derives_names_and_sender() -> None:
    values = service.token_values(_candidate(), _INBOX)
    assert values["first_name"] == "Ada"
    assert values["last_name"] == "Lovelace"
    assert values["company"] == "Anthropic"
    assert values["sender_name"] == "Sam"


def test_render_text_fills_known_tokens() -> None:
    values = service.token_values(_candidate(), _INBOX)
    rendered = service.render_text("Hi {{first_name}} at {{company}}", values)
    assert rendered == "Hi Ada at Anthropic"


def test_personalization_token_fills_from_candidate() -> None:
    values = service.token_values(_candidate(personalization="loved your CRDT talk"), _INBOX)
    assert values["personalization"] == "loved your CRDT talk"
    assert service.render_text("Hey — {{personalization}}.", values) == "Hey — loved your CRDT talk."


def test_render_text_leaves_unknown_tokens_visible() -> None:
    values = service.token_values(_candidate(), _INBOX)
    assert service.render_text("Ref {{unknown_token}}", values) == "Ref {{unknown_token}}"


def test_unresolved_reports_unknown_and_empty_tokens() -> None:
    values = service.token_values(_candidate(company=""), _INBOX)
    unresolved = service.unresolved_tokens("{{company}} {{first_name}} {{mystery}}", values)
    assert unresolved == ["{{company}}", "{{mystery}}"]


def test_build_preview_fills_from_stored_records(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada Lovelace", "company": "Anthropic", "role": "Engineer", "email": "ada@example.com"})
    template = store.list_templates()[0]
    preview = service.build_preview(candidate.id, template.id, "kj", None)
    assert "Ada" in preview["body"]
    assert preview["to"] == "ada@example.com"
    assert preview["from_address"] == "sam@example.com"
    assert preview["unresolved"] == []


def test_build_preview_uses_body_override(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada Lovelace"})
    template = store.list_templates()[0]
    preview = service.build_preview(candidate.id, template.id, "you", "Custom {{first_name}}")
    assert preview["body"] == "Custom Ada"


def test_build_raw_message_encodes_rfc822_headers() -> None:
    raw = service.build_raw_message("ada@example.com", "sam@example.com", "Hello", "Body text")
    decoded = base64.urlsafe_b64decode(raw).decode()
    assert "To: ada@example.com" in decoded
    assert "From: sam@example.com" in decoded
    assert "Subject: Hello" in decoded
    assert "Body text" in decoded


def test_mark_reached_out_records_inbox_and_timestamp(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada"})
    template = store.list_templates()[0]
    updated = service.mark_reached_out(candidate.id, "kj", template.id, "2026-08-12T03:00:00+00:00")
    assert updated.status == "sent"
    assert updated.from_inbox == "kj"
    assert updated.sent_at == "2026-08-12T03:00:00+00:00"
    assert updated.last_template_id == template.id


def test_mark_reached_out_rejects_unknown_inbox(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada"})
    with pytest.raises(NotFoundError):
        service.mark_reached_out(candidate.id, "not_an_inbox", "tpl", "2026-08-12T03:00:00+00:00")


# ---- sending & open tracking ----


def _ready_candidate() -> str:
    store.save_settings({"own_email": "me@example.com", "public_base_url": "https://demo.test/svc"})
    return store.create_candidate({"name": "Ada Lovelace", "company": "Anthropic", "email": "ada@example.com"}).id


def test_pixel_url_is_built_from_base() -> None:
    assert service.pixel_url("https://demo.test/svc/", "abc") == "https://demo.test/svc/pixel/abc.gif"


def test_build_html_message_has_alternatives_and_pixel() -> None:
    raw = service.build_html_message("a@x.com", "b@y.com", "Hi", "Body", "https://demo.test/pixel/t.gif")
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
    assert message.get_content_type() == "multipart/alternative"
    html_part = next(p for p in message.walk() if p.get_content_type() == "text/html")
    body = html_part.get_content()
    assert "https://demo.test/pixel/t.gif" in body
    assert 'width="1"' in body


def test_render_body_html_turns_markdown_links_into_anchors() -> None:
    out = service.render_body_html("see [our deck](https://x.test/d?a=1&b=2) now")
    assert '<a href="https://x.test/d?a=1&amp;b=2">our deck</a>' in out


def test_render_body_html_escapes_surrounding_text_and_link_text() -> None:
    out = service.render_body_html("a < b [<x>](https://x.test)")
    assert "a &lt; b " in out
    assert ">&lt;x&gt;</a>" in out


def test_render_body_html_leaves_unsafe_scheme_as_literal_text() -> None:
    out = service.render_body_html("[click](javascript:alert(1))")
    assert "<a " not in out
    assert "javascript:alert(1)" in out


def test_render_body_plain_flattens_links_to_text_and_url() -> None:
    assert service.render_body_plain("see [deck](https://x.test)") == "see deck (https://x.test)"


def test_render_body_plain_leaves_unsafe_scheme_untouched() -> None:
    assert service.render_body_plain("[x](ftp://h/y)") == "[x](ftp://h/y)"


def test_build_html_message_links_in_html_part_and_flattens_plain() -> None:
    raw = service.build_html_message(
        "a@x.com", "b@y.com", "Hi", "book [a call](https://cal.test/me)"
    )
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
    html_part = next(p for p in message.walk() if p.get_content_type() == "text/html")
    text_part = next(p for p in message.walk() if p.get_content_type() == "text/plain")
    assert '<a href="https://cal.test/me">a call</a>' in html_part.get_content()
    assert "a call (https://cal.test/me)" in text_part.get_content()


def test_send_email_sets_token_and_status(temp_state: Path) -> None:
    candidate_id = _ready_candidate()
    template = store.list_templates()[0]
    result = service.send_email(candidate_id, "kj", template.id, None, "2026-08-12T10:00:00+00:00", send_fn=lambda raw, thread_id=None: {"id": "msg1", "thread_id": "t"}, fetch_message_id=_no_msgid)
    assert result["message_id"] == "msg1"
    assert result["candidate"]["status"] == "sent"
    assert result["candidate"]["track_token"]
    assert result["candidate"]["from_inbox"] == "kj"


def test_send_email_advances_stage_to_reached_out(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com"})
    candidate = store.create_candidate({"name": "Ada", "email": "a@x.com", "stage": "not_contacted"})
    template = store.list_templates()[0]
    result = service.send_email(candidate.id, "kj", template.id, None, "now", send_fn=_fake_send, fetch_message_id=_no_msgid)
    assert result["candidate"]["stage"] == "reached_out"


def test_send_email_does_not_regress_a_replied_candidate(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com"})
    candidate = store.create_candidate({"name": "Ada", "email": "a@x.com", "stage": "replied"})
    template = store.list_templates()[0]
    result = service.send_email(candidate.id, "kj", template.id, None, "now", send_fn=_fake_send, fetch_message_id=_no_msgid)
    assert result["candidate"]["stage"] == "replied"


def test_send_email_records_a_send(temp_state: Path) -> None:
    candidate_id = _ready_candidate()
    template = store.list_templates()[0]
    service.send_email(
        candidate_id, "kj", template.id, None, "2026-08-24T00:00:00+00:00",
        send_fn=lambda raw, thread_id=None: {"id": "m", "thread_id": "THREADX"},
        fetch_message_id=lambda _id: "<mid@x>",
    )
    record = store.get_candidate(candidate_id).sends[-1]
    assert record["inbox_key"] == "kj"
    assert record["thread_id"] == "THREADX" and record["message_id"] == "<mid@x>"
    assert record["subject"] and record["body"]


def test_reply_to_threads_the_followup(temp_state: Path) -> None:
    candidate_id = _ready_candidate()
    template = store.list_templates()[0]
    captured: dict[str, Any] = {}

    def cap_send(raw: str, thread_id: str | None = None) -> dict[str, str]:
        captured["raw"] = raw
        captured["thread_id"] = thread_id
        return {"id": "m2", "thread_id": thread_id or "t"}

    reply_to = {"thread_id": "THREAD1", "message_id": "<orig@mail>", "subject": "Coffee?"}
    result = service.send_email(
        candidate_id, "kj", template.id, None, "now",
        send_fn=cap_send, fetch_message_id=_no_msgid, reply_to=reply_to,
    )
    assert captured["thread_id"] == "THREAD1"
    decoded = base64.urlsafe_b64decode(captured["raw"]).decode()
    assert "In-Reply-To: <orig@mail>" in decoded
    assert "References: <orig@mail>" in decoded
    assert "Subject: Re: Coffee?" in decoded
    assert result["threaded"] is True


def test_mark_reached_out_advances_stage(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada", "stage": "not_contacted"})
    template = store.list_templates()[0]
    updated = service.mark_reached_out(candidate.id, "kj", template.id, "2026-08-17T00:00:00+00:00")
    assert updated.stage == "reached_out"


def test_draft_outreach_returns_subject_and_body(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com", "own_sender_name": "Ming"})
    candidate = store.create_candidate({"name": "Ada", "company": "Stripe", "role": "engineer"})
    draft = service.draft_outreach(
        candidate.id, "you", follow_up=False,
        complete=lambda _p, _s: ai.Completion(text='{"subject":"lunch?","body":"Hi Ada, loved your work."}', cost_usd=0.01),
    )
    assert draft["subject"] == "lunch?" and "Ada" in draft["body"]


def test_draft_outreach_feeds_voice_samples_and_personalization(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com", "own_sender_name": "Ming"})
    store.add_voice_sample("you", "hey! quick note, keeping it short")
    candidate = store.create_candidate({"name": "Ada", "personalization": "loved your evals work"})
    captured: dict[str, str] = {}

    def cap(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        return ai.Completion(text='{"subject":"s","body":"b"}', cost_usd=0.0)

    service.draft_outreach(candidate.id, "you", follow_up=False, complete=cap)
    assert "keeping it short" in captured["prompt"]         # voice sample fed in
    assert "loved your evals work" in captured["prompt"]     # personalization fed in
    assert "FIRST outreach" in captured["prompt"]


def test_draft_outreach_followup_includes_prior_email(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com"})
    candidate = store.create_candidate({
        "name": "Ada", "stage": "reached_out",
        "sends": [{"inbox_key": "you", "subject": "Coffee?", "body": "first note body", "sent_at": "x", "thread_id": "T", "message_id": ""}],
    })
    captured: dict[str, str] = {}

    def cap(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        return ai.Completion(text='{"subject":"","body":"nudge"}', cost_usd=0.0)

    service.draft_outreach(candidate.id, "you", follow_up=True, complete=cap)
    assert "FOLLOW-UP" in captured["prompt"] and "first note body" in captured["prompt"]


def test_send_email_uses_subject_override(temp_state: Path) -> None:
    candidate_id = _ready_candidate()
    template = store.list_templates()[0]
    sent: dict[str, str] = {}
    service.send_email(
        candidate_id, "kj", template.id, None, "now",
        send_fn=lambda raw, thread_id=None: (sent.setdefault("raw", raw), {"id": "m", "thread_id": "t"})[1], fetch_message_id=_no_msgid,
        subject_override="Custom {{first_name}} subject",
    )
    decoded = base64.urlsafe_b64decode(sent["raw"]).decode()
    assert "Subject: Custom Ada subject" in decoded


def test_send_email_without_public_link_sends_without_tracking(temp_state: Path) -> None:
    # No public link set: sending still works, just with no pixel / token.
    candidate = store.create_candidate({"name": "Ada", "email": "ada@example.com"})
    template = store.list_templates()[0]
    sent_raw: dict[str, str] = {}
    result = service.send_email(
        candidate.id, "kj", template.id, None, "now", send_fn=lambda raw, thread_id=None: (sent_raw.setdefault("raw", raw), {"id": "m", "thread_id": "t"})[1], fetch_message_id=_no_msgid
    )
    assert result["tracked"] is False
    assert result["candidate"]["track_token"] is None
    decoded = base64.urlsafe_b64decode(sent_raw["raw"]).decode()
    assert "/pixel/" not in decoded


def test_send_email_from_scratch_needs_no_template(temp_state: Path) -> None:
    # "Write from scratch" sends template_id="" with the body typed in the compose box.
    candidate_id = _ready_candidate()
    sent_raw: dict[str, str] = {}
    result = service.send_email(
        candidate_id, "kj", "", "Hi {{first_name}}, let's talk.", "now",
        send_fn=lambda raw, thread_id=None: (sent_raw.setdefault("raw", raw), {"id": "m", "thread_id": "t"})[1],
        fetch_message_id=_no_msgid, subject_override="A note for you",
    )
    assert result["candidate"]["status"] == "sent"
    decoded = base64.urlsafe_b64decode(sent_raw["raw"]).decode()
    assert "Hi Ada, let's talk." in decoded
    assert "Subject: A note for you" in decoded


def test_send_email_rejects_empty_body(temp_state: Path) -> None:
    candidate_id = _ready_candidate()
    with pytest.raises(SendError, match="body is empty"):
        service.send_email(
            candidate_id, "kj", "", "   ", "now", send_fn=_fake_send, fetch_message_id=_no_msgid,
            subject_override="Subject only",
        )


def test_send_email_requires_candidate_email(temp_state: Path) -> None:
    store.save_settings({"public_base_url": "https://demo.test/svc"})
    candidate = store.create_candidate({"name": "Ada"})  # no email
    template = store.list_templates()[0]
    with pytest.raises(SendError, match="no email"):
        service.send_email(candidate.id, "kj", template.id, None, "now", send_fn=_fake_send, fetch_message_id=_no_msgid)


def test_send_email_requires_inbox_address(temp_state: Path) -> None:
    # 'you' has no address until settings.own_email is set.
    store.save_settings({"public_base_url": "https://demo.test/svc"})
    candidate = store.create_candidate({"name": "Ada", "email": "ada@example.com"})
    template = store.list_templates()[0]
    with pytest.raises(SendError, match="no address"):
        service.send_email(candidate.id, "you", template.id, None, "now", send_fn=_fake_send, fetch_message_id=_no_msgid)


def _sent_token(when: str = "2026-08-12T10:00:00+00:00") -> str:
    candidate_id = _ready_candidate()
    template = store.list_templates()[0]
    sent = service.send_email(candidate_id, "kj", template.id, None, when, send_fn=_fake_send, fetch_message_id=_no_msgid)
    return sent["candidate"]["track_token"]


def test_first_open_notifies_and_sets_opened_at(temp_state: Path) -> None:
    token = _sent_token()
    first, notification = service.record_open(token, "2026-08-12T11:00:00+00:00")
    assert first is not None and first.status == "opened" and first.open_count == 1
    assert notification is not None and notification.candidate_name == "Ada Lovelace"
    assert notification.open_count == 1


def test_reopen_after_gap_notifies_with_count(temp_state: Path) -> None:
    token = _sent_token()
    service.record_open(token, "2026-08-12T11:00:00+00:00")
    again, notification = service.record_open(token, "2026-08-12T12:00:00+00:00")  # 1h later
    assert again is not None and again.open_count == 2
    assert notification is not None and notification.open_count == 2  # re-opens now notify


def test_rapid_reopen_counts_but_does_not_notify(temp_state: Path) -> None:
    token = _sent_token()
    service.record_open(token, "2026-08-12T11:00:00+00:00")
    # a burst re-load 30s later (prefetch): counted, but debounced (no notification)
    again, notification = service.record_open(token, "2026-08-12T11:00:30+00:00")
    assert again is not None and again.open_count == 2
    assert notification is None
    # opened_at stays the first open
    assert again.opened_at == "2026-08-12T11:00:00+00:00"


def test_record_open_unknown_token_is_noop(temp_state: Path) -> None:
    assert service.record_open("nope", "now") == (None, None)
