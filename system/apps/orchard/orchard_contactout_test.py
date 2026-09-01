"""Unit tests for the ContactOut email-lookup layer (no real network)."""

from pathlib import Path
from typing import Any

import pytest
from orchard import contactout, service, store
from orchard.errors import EnrichError, NotFoundError


def _fetch(payload: dict[str, Any]):
    """A ContactOut fetcher stub that records its call and returns a fixed payload."""
    calls: dict[str, str] = {}

    def fetch(url: str, token: str) -> dict[str, Any]:
        calls["url"] = url
        calls["token"] = token
        return payload

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


# ---- contactout.lookup_emails ----


def test_lookup_splits_and_combines_emails() -> None:
    fetch = _fetch(
        {"profile": {"email": ["a@x.com"], "work_email": ["a@work.com"], "personal_email": ["a@home.com"]}}
    )
    result = contactout.lookup_emails("https://linkedin.com/in/a", "tok", fetch)
    assert result["all"] == ["a@x.com"]
    assert result["work"] == ["a@work.com"]
    assert result["personal"] == ["a@home.com"]


def test_lookup_falls_back_to_work_and_personal_when_no_combined() -> None:
    fetch = _fetch({"profile": {"work_email": ["w@x.com"], "personal_email": ["p@x.com"]}})
    result = contactout.lookup_emails("https://linkedin.com/in/a", "tok", fetch)
    assert result["all"] == ["w@x.com", "p@x.com"]


def test_lookup_passes_profile_url_and_token_to_fetch() -> None:
    fetch = _fetch({"profile": {"email": ["a@x.com"]}})
    contactout.lookup_emails("https://linkedin.com/in/ada", "secret-tok", fetch)
    assert "profile=https" in fetch.calls["url"] and "ada" in fetch.calls["url"]
    assert fetch.calls["token"] == "secret-tok"


def test_lookup_empty_profile_yields_no_emails() -> None:
    result = contactout.lookup_emails("https://linkedin.com/in/a", "tok", _fetch({"profile": {}}))
    assert result["all"] == [] and result["work"] == [] and result["personal"] == []


def test_lookup_without_token_raises() -> None:
    with pytest.raises(EnrichError, match="token"):
        contactout.lookup_emails("https://linkedin.com/in/a", "", _fetch({}))


def test_lookup_without_linkedin_raises() -> None:
    with pytest.raises(EnrichError, match="LinkedIn"):
        contactout.lookup_emails("", "tok", _fetch({}))


def test_http_error_messages_are_actionable() -> None:
    assert "token" in contactout._http_error_message(401, "")
    assert "credit" in contactout._http_error_message(403, "").lower()
    assert "profile" in contactout._http_error_message(404, "").lower()
    assert "521" in contactout._http_error_message(521, "boom")


# ---- store token round-trip ----


def test_contactout_token_round_trip(temp_state: Path) -> None:
    assert store.has_contactout_token() is False
    store.save_contactout_token("  my-token  ")
    assert store.load_contactout_token() == "my-token"
    assert store.has_contactout_token() is True
    store.save_contactout_token("")
    assert store.has_contactout_token() is False


# ---- service.find_candidate_email ----


def test_find_email_fills_blank_email_and_stores_raw(temp_state: Path) -> None:
    store.save_contactout_token("tok")
    candidate = store.create_candidate({"name": "Ada", "linkedin": "https://linkedin.com/in/ada"})
    profile = {"email": ["ada@found.com"], "headline": "Engineer"}
    result = service.find_candidate_email(
        candidate.id, lookup=lambda _url, _tok: {"all": ["ada@found.com"], "work": [], "personal": [], "profile": profile}
    )
    assert result["applied"] == "ada@found.com"
    assert result["candidate"]["email"] == "ada@found.com"
    # Raw ContactOut payload is preserved on the candidate.
    assert store.get_candidate(candidate.id).raw["contactout"] == profile


def test_find_email_does_not_overwrite_existing_email(temp_state: Path) -> None:
    store.save_contactout_token("tok")
    candidate = store.create_candidate(
        {"name": "Ada", "email": "keep@me.com", "linkedin": "https://linkedin.com/in/ada"}
    )
    result = service.find_candidate_email(
        candidate.id, lookup=lambda _url, _tok: {"all": ["other@x.com"], "work": [], "personal": [], "profile": {}}
    )
    assert result["applied"] == ""
    assert result["emails"] == ["other@x.com"]
    assert store.get_candidate(candidate.id).email == "keep@me.com"


def test_find_email_no_result_leaves_candidate_unchanged(temp_state: Path) -> None:
    store.save_contactout_token("tok")
    candidate = store.create_candidate({"name": "Ada", "linkedin": "https://linkedin.com/in/ada"})
    result = service.find_candidate_email(
        candidate.id, lookup=lambda _url, _tok: {"all": [], "work": [], "personal": [], "profile": {}}
    )
    assert result["applied"] == "" and result["emails"] == []
    assert store.get_candidate(candidate.id).email == ""


def test_find_email_unknown_candidate_raises(temp_state: Path) -> None:
    with pytest.raises(NotFoundError):
        service.find_candidate_email("nope", lookup=lambda _url, _tok: {"all": [], "work": [], "personal": [], "profile": {}})
