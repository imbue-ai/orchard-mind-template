"""Unit tests for the orchard data models."""

from typing import Any

from orchard.models import Candidate, Template


def _candidate(**overrides: Any) -> Candidate:
    fields: dict[str, Any] = {"id": "cand_1", "name": "Ada Lovelace", "created_at": "2026-08-12T00:00:00+00:00"}
    fields.update(overrides)
    return Candidate(**fields)


def test_status_is_not_contacted_by_default() -> None:
    assert _candidate().status == "not_contacted"


def test_status_is_sent_once_sent_at_is_set() -> None:
    assert _candidate(sent_at="2026-08-12T01:00:00+00:00").status == "sent"


def test_status_is_opened_once_opened_at_is_set() -> None:
    candidate = _candidate(sent_at="2026-08-12T01:00:00+00:00", opened_at="2026-08-12T02:00:00+00:00")
    assert candidate.status == "opened"


def test_opened_wins_even_without_sent_at() -> None:
    # opened_at implies the mail was delivered; status still reflects the strongest signal.
    assert _candidate(opened_at="2026-08-12T02:00:00+00:00").status == "opened"


def test_to_dict_includes_computed_status() -> None:
    data = _candidate(sent_at="2026-08-12T01:00:00+00:00").to_dict()
    assert data["status"] == "sent"
    assert data["name"] == "Ada Lovelace"


def test_template_to_dict_round_trips() -> None:
    template = Template(id="tpl_1", name="Intro", subject="Hi", body="Body", created_at="2026-08-12T00:00:00+00:00")
    assert Template(**template.to_dict()) == template
