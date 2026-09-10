"""Tests for the editable design layer: store, persistence, and image uploads."""

import io
from pathlib import Path

from flask.testing import FlaskClient
from orchard import store


def test_load_design_seeds_default(temp_state: Path) -> None:
    design = store.load_design()
    ids = [c["id"] for c in design["components"]]
    assert ids == ["cmp_apple", "cmp_wordmark", "cmp_heading"]


def test_save_and_load_design_round_trip(temp_state: Path) -> None:
    store.save_design({"components": [{"id": "x", "type": "text", "text": "hi", "x": 5, "y": 6}]})
    design = store.load_design()
    assert [c["id"] for c in design["components"]] == ["x"]
    assert design["components"][0]["text"] == "hi"


def test_empty_save_reseeds_default(temp_state: Path) -> None:
    # Clearing all components falls back to the seeded header rather than an empty one.
    store.save_design({"components": []})
    assert len(store.load_design()["components"]) == 3


def test_save_design_image_and_safe_path(temp_state: Path) -> None:
    name = store.save_design_image("logo.png", b"\x89PNGfake")
    assert name.endswith(".png")
    path = store.design_image_path(name)
    assert path is not None and path.read_bytes() == b"\x89PNGfake"


def test_design_image_path_rejects_traversal(temp_state: Path) -> None:
    assert store.design_image_path("../../etc/passwd") is None
    assert store.design_image_path("nope.png") is None  # missing


# ---- HTTP routes ----


def test_get_design_returns_seeded(client: FlaskClient) -> None:
    body = client.get("/api/design").get_json()
    assert [c["type"] for c in body["components"]] == ["image", "text", "text"]


def test_post_design_persists(client: FlaskClient) -> None:
    payload = {"components": [{"id": "a", "type": "text", "text": "yo", "x": 1, "y": 2}]}
    client.post("/api/design", json=payload)
    assert client.get("/api/design").get_json()["components"][0]["text"] == "yo"


def test_upload_and_serve_design_image(client: FlaskClient) -> None:
    data = {"image": (io.BytesIO(b"GIF89aFAKE"), "pic.gif")}
    res = client.post("/api/design/image", data=data, content_type="multipart/form-data")
    src = res.get_json()["src"]
    assert src.startswith("/design-image/")
    served = client.get(src)
    assert served.status_code == 200 and served.data == b"GIF89aFAKE"


def test_upload_without_file_is_400(client: FlaskClient) -> None:
    assert client.post("/api/design/image", data={}, content_type="multipart/form-data").status_code == 400


def test_serve_unknown_image_is_404(client: FlaskClient) -> None:
    assert client.get("/design-image/does-not-exist.png").status_code == 404
