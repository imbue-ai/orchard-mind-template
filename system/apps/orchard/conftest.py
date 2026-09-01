"""Shared fixtures for the orchard tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from orchard.runner import app


@pytest.fixture
def temp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the store's relative ``data/.apps/orchard`` dir into a tmp cwd.

    ``store`` resolves its paths relative to the current working directory at call
    time, so changing the cwd isolates every test's writes.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(temp_state: Path) -> Iterator[FlaskClient]:
    """A Flask test client whose store writes land in the per-test tmp cwd."""
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
