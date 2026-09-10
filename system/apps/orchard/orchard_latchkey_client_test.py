"""Unit tests for the latchkey curl wrapper (no real subprocess)."""

import subprocess
from typing import Any

import pytest
from orchard import latchkey_client
from orchard.errors import SendError


def _runner(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def run(_command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(args=_command, returncode=returncode, stdout=stdout, stderr=stderr)

    return run


def test_get_json_parses_object() -> None:
    result = latchkey_client.get_json("http://x", _runner(stdout='{"a": 1}'))
    assert result == {"a": 1}


def test_post_json_parses_object() -> None:
    result = latchkey_client.post_json("http://x", {"b": 2}, _runner(stdout='{"id": "m1"}'))
    assert result == {"id": "m1"}


def test_empty_response_is_empty_dict() -> None:
    assert latchkey_client.get_json("http://x", _runner(stdout="")) == {}


def test_nonzero_exit_raises_send_error() -> None:
    with pytest.raises(SendError, match="offline"):
        latchkey_client.get_json("http://x", _runner(returncode=7, stderr="boom"))


def test_non_json_response_raises_send_error() -> None:
    with pytest.raises(SendError, match="Non-JSON"):
        latchkey_client.get_json("http://x", _runner(stdout="<html>nope</html>"))


def test_non_object_json_raises_send_error() -> None:
    with pytest.raises(SendError, match="non-object"):
        latchkey_client.get_json("http://x", _runner(stdout="[1, 2, 3]"))


def test_timeout_raises_send_error() -> None:
    def run(command: list[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        raise subprocess.TimeoutExpired(cmd=command, timeout=30)

    with pytest.raises(SendError, match="timed out"):
        latchkey_client.get_json("http://x", run)
