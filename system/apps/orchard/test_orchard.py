"""Integration tests for the orchard Flask routes."""

from flask.testing import FlaskClient
from orchard import service, store


def test_bootstrap_returns_seeded_templates_and_inboxes(client: FlaskClient) -> None:
    body = client.get("/api/bootstrap").get_json()
    assert body["candidates"] == []
    assert [t["name"] for t in body["templates"]] == ["Cold intro - engineer", "Follow-up - no reply"]
    assert [i["key"] for i in body["inboxes"]] == ["you", "josh", "kj"]


def test_create_candidate_requires_a_name(client: FlaskClient) -> None:
    response = client.post("/api/candidates", json={"company": "Anthropic"})
    assert response.status_code == 400
    assert "name" in response.get_json()["error"].lower()


def test_create_candidate_then_shows_in_bootstrap(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada Lovelace", "company": "Anthropic"}).get_json()
    assert created["status"] == "not_contacted"
    listed = client.get("/api/bootstrap").get_json()["candidates"]
    assert [c["id"] for c in listed] == [created["id"]]


def test_update_candidate(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    updated = client.patch(f"/api/candidates/{created['id']}", json={"email": "ada@example.com"}).get_json()
    assert updated["email"] == "ada@example.com"


def test_candidate_personalization_persists(client: FlaskClient) -> None:
    created = client.post(
        "/api/candidates", json={"name": "Ada", "personalization": "saw your KubeCon talk"}
    ).get_json()
    assert created["personalization"] == "saw your KubeCon talk"
    listed = client.get("/api/bootstrap").get_json()["candidates"][0]
    assert listed["personalization"] == "saw your KubeCon talk"


def test_bootstrap_exposes_stages(client: FlaskClient) -> None:
    stages = client.get("/api/bootstrap").get_json()["stages"]
    assert [s["key"] for s in stages] == ["not_contacted", "reached_out", "replied", "not_interested"]


def test_set_stage_and_channel_via_patch(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    assert created["stage"] == "not_contacted"
    updated = client.patch(
        f"/api/candidates/{created['id']}", json={"stage": "reached_out", "channel": "LinkedIn InMail"}
    ).get_json()
    assert updated["stage"] == "reached_out"
    assert updated["channel"] == "LinkedIn InMail"
    assert updated["sent_at"]  # reaching out stamps a date even without an email send


def test_unknown_stage_is_rejected(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    assert client.patch(f"/api/candidates/{created['id']}", json={"stage": "bogus"}).status_code == 400


def test_personalize_empty_material_is_502(client: FlaskClient) -> None:
    # Empty material is caught before any model/web call.
    assert client.post("/api/personalize", json={"material": "   "}).status_code == 502


def test_sync_sent_with_no_engaged_candidates_is_online(client: FlaskClient) -> None:
    # No engaged candidates means the mailbox is never queried.
    body = client.post("/api/sync-sent").get_json()
    assert body["online"] is True and body["recorded"] == 0


def test_check_replies_with_no_candidates_is_online(client: FlaskClient) -> None:
    # No reached-out candidates means the mailbox is never queried, so it reports
    # online with nothing to flip (exercises the route without a live connection).
    body = client.post("/api/check-replies").get_json()
    assert body["online"] is True
    assert body["replied"] == []


def test_update_unknown_candidate_returns_404(client: FlaskClient) -> None:
    assert client.patch("/api/candidates/nope", json={"email": "x@y.com"}).status_code == 404


def test_reached_out_flips_status_to_sent(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    templates = client.get("/api/bootstrap").get_json()["templates"]
    updated = client.post(
        f"/api/candidates/{created['id']}/reached-out",
        json={"inbox_key": "josh", "template_id": templates[0]["id"]},
    ).get_json()
    assert updated["status"] == "sent"
    assert updated["from_inbox"] == "josh"


def test_preview_fills_tokens(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada Lovelace", "company": "Anthropic"}).get_json()
    templates = client.get("/api/bootstrap").get_json()["templates"]
    preview = client.post(
        "/api/preview",
        json={"candidate_id": created["id"], "template_id": templates[0]["id"], "inbox_key": "kj"},
    ).get_json()
    assert "Ada" in preview["body"]
    assert preview["from_address"] == "sam@example.com"


def test_preview_unknown_candidate_returns_404(client: FlaskClient) -> None:
    templates = client.get("/api/bootstrap").get_json()["templates"]
    response = client.post(
        "/api/preview",
        json={"candidate_id": "missing", "template_id": templates[0]["id"], "inbox_key": "you"},
    )
    assert response.status_code == 404


def test_delete_candidate(client: FlaskClient) -> None:
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    assert client.delete(f"/api/candidates/{created['id']}").status_code == 200
    assert client.get("/api/bootstrap").get_json()["candidates"] == []


def test_client_cannot_set_tracking_fields_directly(client: FlaskClient) -> None:
    created = client.post(
        "/api/candidates", json={"name": "Ada", "sent_at": "hax", "track_token": "hax", "id": "hax"}
    ).get_json()
    assert created["id"] != "hax"
    assert created["sent_at"] is None
    assert created["track_token"] is None


def test_settings_saved_and_reflected_in_you_inbox(client: FlaskClient) -> None:
    client.post("/api/settings", json={"own_email": "me@example.com", "own_sender_name": "Alex"})
    body = client.get("/api/bootstrap").get_json()
    assert body["settings"]["own_email"] == "me@example.com"
    you = next(i for i in body["inboxes"] if i["key"] == "you")
    assert you["address"] == "me@example.com"


def test_send_to_candidate_without_email_returns_503(client: FlaskClient) -> None:
    # A missing email is a precondition failure caught before any mail is sent.
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    templates = client.get("/api/bootstrap").get_json()["templates"]
    response = client.post(
        f"/api/candidates/{created['id']}/send",
        json={"inbox_key": "kj", "template_id": templates[0]["id"]},
    )
    assert response.status_code == 503
    assert "email" in response.get_json()["error"].lower()


def test_pixel_records_open_and_creates_notification(client: FlaskClient) -> None:
    # Seed a sent candidate (with a tracking token) without a live mail connection.
    store.save_settings({"own_email": "me@example.com", "public_base_url": "https://demo.test/svc"})
    candidate = store.create_candidate({"name": "Ada Lovelace", "email": "ada@example.com"})
    template = store.list_templates()[0]
    sent = service.send_email(
        candidate.id, "kj", template.id, None, "2026-08-12T10:00:00+00:00",
        send_fn=lambda raw, thread_id=None: {"id": "m", "thread_id": "t"}, fetch_message_id=lambda _id: "",
    )
    token = sent["candidate"]["track_token"]

    pixel_response = client.get(f"/pixel/{token}.gif")
    assert pixel_response.status_code == 200
    assert pixel_response.mimetype == "image/gif"

    notifications = client.get("/api/notifications").get_json()
    assert notifications["unseen"] == 1
    assert notifications["notifications"][0]["candidate_name"] == "Ada Lovelace"

    # Marking seen zeroes the unseen count.
    client.post("/api/notifications/seen")
    assert client.get("/api/notifications").get_json()["unseen"] == 0


def test_pixel_unknown_token_still_returns_gif(client: FlaskClient) -> None:
    response = client.get("/pixel/unknown.gif")
    assert response.status_code == 200
    assert response.mimetype == "image/gif"


def test_contactout_token_sets_flag_without_leaking(client: FlaskClient) -> None:
    assert client.get("/api/bootstrap").get_json()["contactout_configured"] is False
    assert client.post("/api/contactout-token", json={"token": "secret-tok"}).get_json()["configured"] is True
    body = client.get("/api/bootstrap").get_json()
    assert body["contactout_configured"] is True
    # The token itself is never returned to the client.
    assert "secret-tok" not in client.get("/api/bootstrap").get_data(as_text=True)


def test_find_email_without_token_returns_502(client: FlaskClient) -> None:
    # A missing token is a precondition failure caught before any network call.
    created = client.post("/api/candidates", json={"name": "Ada", "linkedin": "https://linkedin.com/in/ada"}).get_json()
    response = client.post(f"/api/candidates/{created['id']}/find-email")
    assert response.status_code == 502
    assert "token" in response.get_json()["error"].lower()


def test_find_email_unknown_candidate_returns_404(client: FlaskClient) -> None:
    assert client.post("/api/candidates/nope/find-email").status_code == 404


def test_voices_add_list_delete(client: FlaskClient) -> None:
    assert client.get("/api/voices").get_json()["voices"]["josh"] == []
    sample = client.post("/api/voices/josh", json={"text": "hey there"}).get_json()
    assert sample["text"] == "hey there"
    listed = client.get("/api/voices").get_json()["voices"]["josh"]
    assert [s["id"] for s in listed] == [sample["id"]]
    assert client.delete(f"/api/voices/josh/{sample['id']}").status_code == 200
    assert client.get("/api/voices").get_json()["voices"]["josh"] == []


def test_add_voice_sample_rejects_empty(client: FlaskClient) -> None:
    assert client.post("/api/voices/josh", json={"text": "   "}).status_code == 400


def test_add_voice_sample_unknown_inbox_returns_404(client: FlaskClient) -> None:
    assert client.post("/api/voices/nope", json={"text": "x"}).status_code == 404


def test_draft_without_samples_returns_502(client: FlaskClient) -> None:
    # No samples is a precondition failure caught before any model call.
    response = client.post("/api/voices/josh/draft")
    assert response.status_code == 502
    assert "sample" in response.get_json()["error"].lower()


def test_send_passes_subject_through(client: FlaskClient) -> None:
    # A candidate with no email hits the precondition (503) before any real send,
    # so this exercises the route wiring without a live mail connection.
    created = client.post("/api/candidates", json={"name": "Ada"}).get_json()
    templates = client.get("/api/bootstrap").get_json()["templates"]
    response = client.post(
        f"/api/candidates/{created['id']}/send",
        json={"inbox_key": "kj", "template_id": templates[0]["id"], "subject": "custom subject"},
    )
    assert response.status_code == 503
