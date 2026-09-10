"""Unit tests for the pitch-ideas bucket: storage, drafting, and its routes."""

from pathlib import Path

import pytest
from flask.testing import FlaskClient
from orchard import ai, service, store
from orchard.errors import DraftError, NotFoundError


def _completion(text: str) -> ai.Completion:
    return ai.Completion(text=text, cost_usd=0.002)


# ---- store: pitch ideas ----


def test_pitch_idea_round_trip(temp_state: Path) -> None:
    assert store.load_pitch_ideas() == []
    idea = store.add_pitch_idea("  lead with the code quality angle  ")
    assert idea["text"] == "lead with the code quality angle" and idea["id"]
    assert [i["text"] for i in store.load_pitch_ideas()] == ["lead with the code quality angle"]
    store.delete_pitch_idea(idea["id"])
    assert store.load_pitch_ideas() == []


def test_pitch_ideas_keep_insertion_order(temp_state: Path) -> None:
    store.add_pitch_idea("first")
    store.add_pitch_idea("second")
    assert [i["text"] for i in store.load_pitch_ideas()] == ["first", "second"]


def test_delete_unknown_pitch_idea_raises(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.delete_pitch_idea("missing")


# ---- service: draft_template_from_ideas ----


def test_draft_from_ideas_requires_ideas(temp_state: Path) -> None:
    with pytest.raises(DraftError, match="idea"):
        service.draft_template_from_ideas([], "", complete=lambda _p, _s: _completion("{}"))


def test_draft_from_ideas_fresh_template(temp_state: Path) -> None:
    store.add_pitch_idea("emphasize amplifying human agency")
    draft = service.draft_template_from_ideas(
        [], "",
        complete=lambda _p, _s: _completion('{"subject":"hi {{first_name}}","body":"hey {{first_name}}"}'),
    )
    assert draft["subject"] == "hi {{first_name}}"
    assert draft["body"] == "hey {{first_name}}"
    assert draft["name"] == "Pitch idea draft"
    assert draft["target_template_id"] == ""
    assert draft["cost_usd"] == 0.002


def test_draft_from_ideas_revises_existing_template(temp_state: Path) -> None:
    template = store.create_template("Cold intro", "Subject", "Body {{sender_name}}")
    store.add_pitch_idea("mention the team lunch")
    captured: dict[str, str] = {}

    def fake(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        return _completion('{"subject":"s","body":"b"}')

    draft = service.draft_template_from_ideas([], template.id, complete=fake)
    assert draft["name"] == "Cold intro"  # keeps the revised template's name
    assert draft["target_template_id"] == template.id
    # The base template's body is fed to the model so it revises rather than rewrites.
    assert "Body {{sender_name}}" in captured["prompt"]
    assert "mention the team lunch" in captured["prompt"]


def test_draft_from_ideas_uses_only_selected_ideas(temp_state: Path) -> None:
    keep = store.add_pitch_idea("keep-this-idea")
    store.add_pitch_idea("drop-this-idea")
    captured: dict[str, str] = {}

    def fake(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        return _completion('{"subject":"s","body":"b"}')

    service.draft_template_from_ideas([keep["id"]], "", complete=fake)
    assert "keep-this-idea" in captured["prompt"]
    assert "drop-this-idea" not in captured["prompt"]


def test_draft_from_ideas_unknown_template_raises(temp_state: Path) -> None:
    store.add_pitch_idea("some idea")
    with pytest.raises(NotFoundError):
        service.draft_template_from_ideas([], "nope", complete=lambda _p, _s: _completion('{"subject":"s","body":"b"}'))


def test_draft_from_ideas_bad_output_raises(temp_state: Path) -> None:
    store.add_pitch_idea("idea")
    with pytest.raises(DraftError):
        service.draft_template_from_ideas([], "", complete=lambda _p, _s: _completion("no json"))


# ---- routes ----


def test_add_and_list_pitch_idea_route(client: FlaskClient) -> None:
    created = client.post("/api/pitch-ideas", json={"text": "a fresh angle"})
    assert created.status_code == 200 and created.get_json()["text"] == "a fresh angle"
    voices = client.get("/api/voices").get_json()
    assert [i["text"] for i in voices["ideas"]] == ["a fresh angle"]


def test_add_empty_pitch_idea_is_rejected(client: FlaskClient) -> None:
    assert client.post("/api/pitch-ideas", json={"text": "   "}).status_code == 400


def test_delete_pitch_idea_route(client: FlaskClient) -> None:
    idea = client.post("/api/pitch-ideas", json={"text": "temp"}).get_json()
    assert client.delete(f"/api/pitch-ideas/{idea['id']}").status_code == 200
    assert client.get("/api/voices").get_json()["ideas"] == []


def test_delete_unknown_pitch_idea_route_404(client: FlaskClient) -> None:
    assert client.delete("/api/pitch-ideas/missing").status_code == 404
