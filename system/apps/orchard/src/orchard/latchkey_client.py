"""Thin wrapper around the `latchkey curl` CLI for authenticated HTTP calls.

Sending mail reaches Gmail through latchkey, which injects the user's stored
credentials into curl requests. There is no Python SDK that performs the
credential injection, so we shell out to `latchkey curl` and parse the JSON
response. Kept deliberately small and injectable so tests never shell out.
"""

import json
import logging
import subprocess
from collections.abc import Callable
from typing import Any

from orchard.errors import SendError

logger = logging.getLogger("orchard.latchkey")

_TIMEOUT_SECONDS = 30

# The subprocess runner is injectable so tests drive the send/error paths without
# a live mail connection; production always uses subprocess.run.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(args: list[str], runner: Runner) -> str:
    command = ["latchkey", "curl", "-s", *args]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise SendError(f"Mail connection timed out after {_TIMEOUT_SECONDS}s") from exc
    except FileNotFoundError as exc:
        raise SendError(f"latchkey binary not found: {exc}") from exc
    if completed.returncode != 0:
        raise SendError(
            "Your mail connection is offline "
            f"(latchkey exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def get_json(url: str, runner: Runner = subprocess.run) -> dict[str, Any]:
    """GET a URL through latchkey and return the parsed JSON object."""
    return _parse(_run([url], runner), url)


def post_json(url: str, body: dict[str, Any], runner: Runner = subprocess.run) -> dict[str, Any]:
    """POST a JSON body to a URL through latchkey and return the parsed JSON object."""
    args = ["-X", "POST", url, "-H", "Content-Type: application/json", "-d", json.dumps(body)]
    return _parse(_run(args, runner), url)


def _parse(raw: str, url: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SendError(f"Non-JSON response from {url}: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise SendError(f"Unexpected non-object response from {url}: {raw[:200]}")
    return parsed
