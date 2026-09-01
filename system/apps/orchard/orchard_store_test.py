"""Unit tests for orchard persistence."""

from pathlib import Path

import pytest
from orchard import store
from orchard.errors import NotFoundError


def test_list_templates_seeds_defaults_on_first_use(temp_state: Path) -> None:
    templates = store.list_templates()
    assert [t.name for t in templates] == ["Cold intro - engineer", "Follow-up - no reply"]
    # Seeding persists, so a second call returns the same ids rather than reseeding.
    assert [t.id for t in store.list_templates()] == [t.id for t in templates]


def test_load_inboxes_defaults_to_three_identities(temp_state: Path) -> None:
    inboxes = store.load_inboxes()
    assert [i.key for i in inboxes] == ["you", "josh", "kj"]
    assert store.get_inbox("josh").address == "alex@example.com"


def test_get_inbox_rejects_unknown_key(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.get_inbox("nope")


def test_create_and_get_candidate_round_trip(temp_state: Path) -> None:
    created = store.create_candidate({"name": "Ada Lovelace", "company": "Anthropic"})
    fetched = store.get_candidate(created.id)
    assert fetched.name == "Ada Lovelace"
    assert fetched.company == "Anthropic"
    assert fetched.status == "not_contacted"


def test_create_candidate_ignores_unknown_fields(temp_state: Path) -> None:
    created = store.create_candidate({"name": "Ada", "bogus": "x", "id": "attacker"})
    assert created.id != "attacker"
    assert not hasattr(created, "bogus")


def test_create_candidate_prepends_newest_first(temp_state: Path) -> None:
    store.create_candidate({"name": "First"})
    store.create_candidate({"name": "Second"})
    assert [c.name for c in store.list_candidates()] == ["Second", "First"]


def test_update_candidate_persists(temp_state: Path) -> None:
    created = store.create_candidate({"name": "Ada"})
    store.update_candidate(created.id, {"email": "ada@example.com", "role": "Engineer"})
    reloaded = store.get_candidate(created.id)
    assert reloaded.email == "ada@example.com"
    assert reloaded.role == "Engineer"


def test_update_candidate_rejects_unknown_id(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.update_candidate("cand_missing", {"email": "x@y.com"})


def test_delete_candidate_removes_it(temp_state: Path) -> None:
    created = store.create_candidate({"name": "Ada"})
    store.delete_candidate(created.id)
    assert store.list_candidates() == []


def test_delete_candidate_rejects_unknown_id(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        store.delete_candidate("cand_missing")


def test_create_update_delete_template(temp_state: Path) -> None:
    created = store.create_template("Custom", "Subj", "Body {{first_name}}")
    assert store.get_template(created.id).subject == "Subj"
    store.update_template(created.id, {"subject": "New subject"})
    assert store.get_template(created.id).subject == "New subject"
    store.delete_template(created.id)
    with pytest.raises(NotFoundError):
        store.get_template(created.id)


def test_list_candidates_recovers_from_corrupt_json(temp_state: Path) -> None:
    path = temp_state / "runtime" / "orchard" / "candidates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")
    assert store.list_candidates() == []


def test_settings_default_then_save(temp_state: Path) -> None:
    assert store.load_settings() == {"own_email": "", "own_sender_name": "", "public_base_url": ""}
    store.save_settings({"own_email": "me@example.com", "public_base_url": "https://x/"})
    settings = store.load_settings()
    assert settings["own_email"] == "me@example.com"
    assert settings["public_base_url"] == "https://x/"


def test_you_inbox_reflects_saved_settings(temp_state: Path) -> None:
    store.save_settings({"own_email": "me@example.com", "own_sender_name": "Alex"})
    you = store.get_inbox("you")
    assert you.address == "me@example.com"
    assert you.sender_name == "Alex"


def test_find_by_token(temp_state: Path) -> None:
    created = store.create_candidate({"name": "Ada"})
    store.update_candidate(created.id, {"track_token": "tok123"})
    found = store.find_by_token("tok123")
    assert found is not None
    assert found.id == created.id
    assert store.find_by_token("missing") is None
    assert store.find_by_token("") is None


def test_notifications_add_and_mark_seen(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada", "from_inbox": "kj"})
    store.add_notification(candidate, "2026-08-12T10:00:00+00:00")
    notifications = store.list_notifications()
    assert len(notifications) == 1
    assert notifications[0].candidate_name == "Ada"
    assert notifications[0].seen is False
    store.mark_notifications_seen()
    assert all(n.seen for n in store.list_notifications())
