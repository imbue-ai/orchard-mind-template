"""Unit tests for reply detection (no real mailbox)."""

from pathlib import Path
from typing import Any

from orchard import service, store


def _reached_out(email: str = "cand@example.com") -> Any:
    # Creating at the reached_out stage stamps a sent_at date.
    return store.create_candidate({"name": "Ada", "email": email, "stage": "reached_out"})


def test_scan_marks_reply_after_send(temp_state: Path) -> None:
    candidate = _reached_out()
    after = service._iso_to_ms(store.get_candidate(candidate.id).sent_at) + 60_000
    replied = service.scan_for_replies(find_from=lambda _email: {"date_ms": after, "snippet": "hi"})
    assert len(replied) == 1 and replied[0]["stage"] == "replied" and replied[0]["replied_at"]
    assert store.get_candidate(candidate.id).pipeline_stage == "replied"


def test_scan_ignores_message_before_send(temp_state: Path) -> None:
    candidate = _reached_out()
    before = service._iso_to_ms(store.get_candidate(candidate.id).sent_at) - 60_000
    replied = service.scan_for_replies(find_from=lambda _email: {"date_ms": before, "snippet": "old thread"})
    assert replied == []
    assert store.get_candidate(candidate.id).pipeline_stage == "reached_out"


def test_scan_no_message_leaves_candidate(temp_state: Path) -> None:
    candidate = _reached_out()
    assert service.scan_for_replies(find_from=lambda _email: None) == []
    assert store.get_candidate(candidate.id).pipeline_stage == "reached_out"


def test_scan_skips_already_replied(temp_state: Path) -> None:
    store.create_candidate({"name": "Ada", "email": "a@x.com", "stage": "replied"})
    calls: list[str] = []

    def find(email: str) -> dict[str, Any]:
        calls.append(email)
        return {"date_ms": 9_999_999_999_999, "snippet": ""}

    assert service.scan_for_replies(find_from=find) == []
    assert calls == []  # a candidate already past reached_out isn't scanned


def test_scan_skips_not_contacted(temp_state: Path) -> None:
    store.create_candidate({"name": "Ada", "email": "a@x.com"})  # not_contacted, no send
    assert service.scan_for_replies(find_from=lambda _email: {"date_ms": 9_999_999_999_999}) == []


# ---- syncing off-platform sends ----


def _sent(**over: Any) -> dict[str, Any]:
    base = {"gmail_id": "g9", "thread_id": "T9", "subject": "Following up",
            "from_addr": "Me <me@example.com>", "snippet": "just circling back", "date_ms": 1_700_000_000_000}
    base.update(over)
    return base


def test_sync_records_offplatform_send(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com"})  # the 'you' inbox
    candidate = store.create_candidate({"name": "Ada", "email": "ada@x.com", "stage": "reached_out"})
    recorded = service.sync_sent_mail(list_sent=lambda _e: [_sent()])
    assert recorded == 1
    rec = store.get_candidate(candidate.id).sends[-1]
    assert rec["gmail_id"] == "g9" and rec["source"] == "gmail"
    assert rec["inbox_key"] == "you" and rec["subject"] == "Following up"


def test_sync_skips_not_contacted(temp_state: Path) -> None:
    store.create_candidate({"name": "Ada", "email": "ada@x.com"})  # not_contacted
    calls: list[str] = []
    assert service.sync_sent_mail(list_sent=lambda e: calls.append(e) or []) == 0
    assert calls == []


def test_sync_dedups_known_message(temp_state: Path) -> None:
    store.create_candidate({
        "name": "Ada", "email": "ada@x.com", "stage": "reached_out",
        "sends": [{"inbox_key": "you", "subject": "x", "body": "y", "sent_at": "2026-01-01T00:00:00+00:00",
                   "thread_id": "T", "message_id": "", "gmail_id": "g9"}],
    })
    assert service.sync_sent_mail(list_sent=lambda _e: [_sent()]) == 0
