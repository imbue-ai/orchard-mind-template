"""Unit tests for sender voice samples and AI-drafted templates (no real model call)."""

from pathlib import Path

import pytest
from orchard import ai, service, store
from orchard.errors import DraftError, NotFoundError


def _completion(text: str) -> ai.Completion:
    return ai.Completion(text=text, cost_usd=0.001)


# ---- store: voice samples ----


def test_voice_sample_round_trip(temp_state: Path) -> None:
    assert store.load_voice_samples("josh") == []
    sample = store.add_voice_sample("josh", "  hey there  ")
    assert sample["text"] == "hey there" and sample["id"]
    assert [s["text"] for s in store.load_voice_samples("josh")] == ["hey there"]
    store.delete_voice_sample("josh", sample["id"])
    assert store.load_voice_samples("josh") == []


def test_voice_samples_are_per_inbox(temp_state: Path) -> None:
    store.add_voice_sample("josh", "josh writes this")
    store.add_voice_sample("kj", "kj writes that")
    assert [s["text"] for s in store.load_voice_samples("josh")] == ["josh writes this"]
    assert [s["text"] for s in store.load_voice_samples("kj")] == ["kj writes that"]


def test_add_voice_sample_rejects_unknown_inbox(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.add_voice_sample("nope", "x")


def test_delete_unknown_voice_sample_raises(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.delete_voice_sample("josh", "missing")


# ---- service: draft_template_from_voice ----


def test_draft_requires_samples(temp_state: Path) -> None:
    with pytest.raises(DraftError, match="sample"):
        service.draft_template_from_voice("josh", complete=lambda _p, _s: _completion("{}"))


def test_draft_parses_subject_and_body(temp_state: Path) -> None:
    store.add_voice_sample("josh", "hey, quick note")
    draft = service.draft_template_from_voice(
        "josh",
        complete=lambda _p, _s: _completion('{"subject":"hi {{first_name}}","body":"hey {{first_name}}"}'),
    )
    assert draft["subject"] == "hi {{first_name}}"
    assert draft["body"] == "hey {{first_name}}"
    assert draft["name"] == "Alex — voice draft"
    assert draft["cost_usd"] == 0.001


def test_draft_tolerates_code_fences_and_prose(temp_state: Path) -> None:
    store.add_voice_sample("josh", "sample")
    draft = service.draft_template_from_voice(
        "josh",
        complete=lambda _p, _s: _completion('Here you go:\n```json\n{"subject":"s","body":"b"}\n```'),
    )
    assert draft["subject"] == "s" and draft["body"] == "b"


def test_draft_bad_output_raises(temp_state: Path) -> None:
    store.add_voice_sample("josh", "sample")
    with pytest.raises(DraftError):
        service.draft_template_from_voice("josh", complete=lambda _p, _s: _completion("no json here"))


def test_draft_missing_body_raises(temp_state: Path) -> None:
    store.add_voice_sample("josh", "sample")
    with pytest.raises(DraftError):
        service.draft_template_from_voice("josh", complete=lambda _p, _s: _completion('{"subject":"only subject"}'))


def test_draft_feeds_samples_and_token_guidance_to_the_model(temp_state: Path) -> None:
    store.add_voice_sample("josh", "unique-sample-text-xyz")
    captured: dict[str, str] = {}

    def fake(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        captured["system"] = system
        return _completion('{"subject":"s","body":"b"}')

    service.draft_template_from_voice("josh", complete=fake)
    assert "unique-sample-text-xyz" in captured["prompt"]
    assert "{{first_name}}" in captured["system"]
