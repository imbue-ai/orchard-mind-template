"""Unit tests for manual pipeline stages and the outreach channel."""

from pathlib import Path
from typing import Any

from orchard import store
from orchard.models import Candidate


def _cand(**overrides: Any) -> Candidate:
    fields: dict[str, Any] = {"id": "c1", "name": "Ada", "created_at": "2026-08-12T00:00:00+00:00"}
    fields.update(overrides)
    return Candidate(**fields)


# ---- pipeline_stage derivation ----


def test_stage_defaults_to_not_contacted() -> None:
    assert _cand().pipeline_stage == "not_contacted"


def test_stage_derives_reached_out_from_sent_at() -> None:
    assert _cand(sent_at="2026-08-12T01:00:00+00:00").pipeline_stage == "reached_out"


def test_manual_stage_overrides_derivation() -> None:
    assert _cand(stage="replied", sent_at="2026-08-12T01:00:00+00:00").pipeline_stage == "replied"


def test_to_dict_exposes_effective_stage() -> None:
    assert _cand(sent_at="2026-08-12T01:00:00+00:00").to_dict()["stage"] == "reached_out"


# ---- reached_out stamps a date ----


def test_create_reached_out_stamps_sent_at(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada", "stage": "reached_out", "channel": "LinkedIn InMail"})
    assert candidate.stage == "reached_out"
    assert candidate.sent_at
    assert candidate.channel == "LinkedIn InMail"


def test_update_to_reached_out_stamps_sent_at(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada"})
    assert candidate.sent_at is None
    updated = store.update_candidate(candidate.id, {"stage": "reached_out"})
    assert updated.stage == "reached_out" and updated.sent_at


def test_replied_without_a_send_gets_no_sent_at(temp_state: Path) -> None:
    # Only 'reached_out' stamps a date; other manual stages don't invent one.
    candidate = store.create_candidate({"name": "Ada", "stage": "replied"})
    assert candidate.stage == "replied" and candidate.sent_at is None
