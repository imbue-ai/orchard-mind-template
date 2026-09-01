"""Unit tests for the Gmail send layer (no real subprocess)."""

import json
import subprocess
from typing import Any

import pytest
from orchard import gmail
from orchard.errors import SendError


def _runner(payload: dict[str, Any]):
    def run(_command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(args=_command, returncode=0, stdout=json.dumps(payload), stderr="")

    return run


def test_get_account_email() -> None:
    assert gmail.get_account_email(_runner({"emailAddress": "me@example.com"})) == "me@example.com"


def test_get_account_email_missing_raises() -> None:
    with pytest.raises(SendError):
        gmail.get_account_email(_runner({}))


def test_list_send_as_returns_addresses() -> None:
    payload = {"sendAs": [{"sendAsEmail": "me@example.com"}, {"sendAsEmail": "alex@example.com"}]}
    assert gmail.list_send_as(_runner(payload)) == ["me@example.com", "alex@example.com"]


def test_send_raw_returns_id_and_thread() -> None:
    assert gmail.send_raw("cmF3", runner=_runner({"id": "m123", "threadId": "t9"})) == {"id": "m123", "thread_id": "t9"}


def test_send_raw_surfaces_gmail_error() -> None:
    with pytest.raises(SendError, match="rejected"):
        gmail.send_raw("cmF3", runner=_runner({"error": {"message": "bad"}}))


def test_send_raw_without_id_raises() -> None:
    with pytest.raises(SendError, match="no message id"):
        gmail.send_raw("cmF3", runner=_runner({}))


def test_send_raw_threads_into_existing_thread() -> None:
    seen: dict[str, str] = {}

    def run(command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        if "-d" in command:
            seen["body"] = command[command.index("-d") + 1]
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=json.dumps({"id": "m", "threadId": "T"}), stderr="")

    result = gmail.send_raw("cmF3", thread_id="T", runner=run)
    assert result == {"id": "m", "thread_id": "T"}
    assert '"threadId": "T"' in seen["body"]


def test_get_rfc_message_id_reads_header() -> None:
    payload = {"payload": {"headers": [{"name": "Message-ID", "value": "<abc@mail>"}]}}
    assert gmail.get_rfc_message_id("m1", _runner(payload)) == "<abc@mail>"


def test_get_rfc_message_id_missing_returns_empty() -> None:
    assert gmail.get_rfc_message_id("m1", _runner({"payload": {"headers": []}})) == ""


def test_list_sent_to_returns_metadata() -> None:
    def run(command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        url = command[-1]
        if "format=metadata" in url:
            payload = {
                "id": "g1", "threadId": "T1", "internalDate": "1700000000000", "snippet": "just circling back",
                "payload": {"headers": [{"name": "Subject", "value": "Coffee?"}, {"name": "From", "value": "Sam <sam@example.com>"}]},
            }
        else:
            payload = {"messages": [{"id": "g1"}]}
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=json.dumps(payload), stderr="")

    rows = gmail.list_sent_to("cand@example.com", run)
    assert rows == [{
        "gmail_id": "g1", "thread_id": "T1", "subject": "Coffee?",
        "from_addr": "Sam <sam@example.com>", "snippet": "just circling back", "date_ms": 1700000000000,
    }]


def test_find_latest_from_returns_newest_message() -> None:
    def run(command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        url = command[-1]
        payload = (
            {"messages": [{"id": "m1"}]}
            if "maxResults" in url
            else {"internalDate": "1700000000000", "snippet": "thanks for reaching out"}
        )
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=json.dumps(payload), stderr="")

    result = gmail.find_latest_from("cand@example.com", run)
    assert result == {"date_ms": 1700000000000, "snippet": "thanks for reaching out"}


def test_find_latest_from_none_when_no_messages() -> None:
    assert gmail.find_latest_from("cand@example.com", _runner({"messages": []})) is None


def test_find_latest_from_raises_when_not_permitted() -> None:
    with pytest.raises(SendError, match="read mail"):
        gmail.find_latest_from("cand@example.com", _runner({"error": {"message": "nope"}}))
