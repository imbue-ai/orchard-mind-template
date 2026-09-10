"""Call headless ``claude -p`` for a one-shot completion (the keyless AI path).

Adapted from the use-ai-integration skill's ``claude_p.py`` reference for this
app's single need: draft text from a prompt with no tools. This workspace has no
``ANTHROPIC_API_KEY``, so we shell out to ``claude -p``, which reads the shared
Claude settings itself at call time. The call runs from a throwaway directory so
the repo's ``CLAUDE.md`` / ``.claude`` hooks don't bleed into the answer, and
unsets ``MAIN_CLAUDE_SESSION_ID`` so the child isn't mistaken for mngr's managed
main session.

The default model is a top-of-file constant so switching it is a one-line change.
"""

import json
import os
import subprocess
import tempfile
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchard.errors import DraftError

# Voice mimicry is subtle, so this defaults to a mid-tier model rather than the
# smallest; change this one line to trade quality for cost/speed.
DEFAULT_MODEL = "claude-sonnet-5"

_MAIN_CLAUDE_SESSION_ID = "MAIN_CLAUDE_SESSION_ID"


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0


class _ClaudeResult(BaseModel):
    """The ``claude -p --output-format json`` result message, typed and validated."""

    model_config = ConfigDict(extra="ignore")

    subtype: str | None = None
    is_error: bool = False
    result: str | None = None
    total_cost_usd: float | None = None
    usage: _Usage = Field(default_factory=_Usage)
    errors: list[Any] = Field(default_factory=list)


class Completion(BaseModel):
    """The text of one completion plus what the call actually cost (USD)."""

    text: str
    cost_usd: float


def _run_claude(argv: list[str], timeout: int) -> Completion:
    """Run a ``claude -p`` argv from an isolated temp dir; parse or raise DraftError.

    The throwaway cwd keeps the repo's CLAUDE.md / .claude context out of the answer;
    credentials come from the shared Claude settings, not the cwd, so auth is fine."""
    env = dict(os.environ)
    env.pop(_MAIN_CLAUDE_SESSION_ID, None)
    try:
        with tempfile.TemporaryDirectory(prefix="orchard_ai_") as cwd:
            proc = subprocess.run(
                argv, capture_output=True, text=True, env=env, check=False, cwd=cwd, timeout=timeout
            )
    except subprocess.TimeoutExpired as exc:
        raise DraftError("The AI call took too long. Please try again.") from exc
    if proc.returncode != 0:
        raise DraftError(f"The AI call failed (claude -p exit {proc.returncode}).")
    try:
        decoded = json.loads(proc.stdout)
    except ValueError as exc:
        raise DraftError("The AI call returned unreadable output.") from exc
    try:
        parsed = _ClaudeResult.model_validate(decoded)
    except ValidationError as exc:
        raise DraftError("The AI call returned an unexpected shape.") from exc
    if parsed.is_error or parsed.subtype != "success" or parsed.result is None:
        raise DraftError("The AI couldn't produce a result. Please try again in a moment.")
    return Completion(text=parsed.result, cost_usd=parsed.total_cost_usd or 0.0)


def complete(prompt: str, system: str, model: str = DEFAULT_MODEL) -> Completion:
    """One non-agentic completion, no tools. Raises DraftError on failure."""
    argv = [
        "claude", "-p", prompt, "--output-format", "json",
        "--model", model, "--system-prompt", system, "--tools", "",
    ]
    return _run_claude(argv, timeout=90)


def research(prompt: str, system: str, model: str = DEFAULT_MODEL) -> Completion:
    """One agentic call that may read the web (WebFetch/WebSearch only -- no file or
    shell access), for reading dropped links. Raises DraftError on failure."""
    argv = [
        "claude", "-p", prompt, "--output-format", "json",
        "--model", model, "--system-prompt", system,
        "--tools", "WebFetch WebSearch", "--permission-mode", "bypassPermissions",
    ]
    return _run_claude(argv, timeout=180)
