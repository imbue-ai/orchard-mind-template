"""Tests for the public pixel service, the open queue, and ingestion."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from orchard import service, store
from orchard.pixel_runner import app as pixel_app


@pytest.fixture
def pixel_client(temp_state: Path) -> Iterator[FlaskClient]:
    pixel_app.config.update(TESTING=True)
    with pixel_app.test_client() as client:
        yield client


def test_pixel_returns_gif_and_queues_the_hit(pixel_client: FlaskClient, temp_state: Path) -> None:
    response = pixel_client.get("/pixel/tok123.gif")
    assert response.status_code == 200 and response.mimetype == "image/gif"
    events = store.drain_pending_opens()
    assert [e["token"] for e in events] == ["tok123"]


def test_pixel_service_health(pixel_client: FlaskClient) -> None:
    assert pixel_client.get("/health").status_code == 200


def test_append_and_drain_round_trip(temp_state: Path) -> None:
    store.append_pending_open("a", "2026-01-01T00:00:00+00:00")
    store.append_pending_open("b", "2026-01-01T00:01:00+00:00")
    assert [e["token"] for e in store.drain_pending_opens()] == ["a", "b"]
    assert store.drain_pending_opens() == []  # queue cleared after draining


def test_ingest_records_open_for_known_token(temp_state: Path) -> None:
    candidate = store.create_candidate({"name": "Ada", "email": "a@x.com", "track_token": "tk1"})
    store.append_pending_open("tk1", "2026-08-18T00:00:00+00:00")
    assert service.ingest_pending_opens() == 1
    updated = store.get_candidate(candidate.id)
    assert updated.opened_at and updated.open_count == 1


def test_ingest_ignores_unknown_token_without_error(temp_state: Path) -> None:
    store.append_pending_open("no-such-token", "2026-08-18T00:00:00+00:00")
    assert service.ingest_pending_opens() == 1  # drained, but matched no candidate
    assert store.drain_pending_opens() == []
