"""Native pilot boundaries using real stores and HTTP; no model requests."""
import base64
import json
import os
import time

from fastapi.testclient import TestClient
import pytest

from doxa.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_ACCESS_MODE", "DOXA_ACCESS_CONFIG_FILE", "DOXA_INTERNAL_TOKEN", "DOXA_CLOUD_STATE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOXA_PRIVACY_VAULT_KEY", base64.b64encode(os.urandom(32)).decode())
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


def project(client, name="Pilot source collection"):
    return client.post("/api/projects", json={"name": name}).json()["id"]


def upload(client, identity, text="Please send this to Alice at alice@example.com before Friday. We will review it."):
    response = client.post(f"/api/projects/{identity}/upload", files={"files": ("message.txt", text.encode(), "text/plain")})
    assert response.status_code == 200, response.text
    return response.json()[0]


@pytest.mark.parametrize("profile", ["company", "public", "personal"])
def test_saved_briefs_freeze_server_intent_in_real_runs(client, profile):
    identity = project(client)
    document = upload(client, identity)
    saved = client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": profile, "expected_revision": 0})
    assert saved.status_code == 200, saved.text
    brief = saved.json()
    response = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "service_profile_id": profile,
        "pilot_brief_revision": brief["revision"], "document_ids": [document["id"]], "question": "Browser changed the approved question"})
    assert response.status_code == 201, response.text
    assert response.json()["config"]["question"] == brief["question"]
    assert response.json()["config"]["service_brief"] == brief
    changed = client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": profile, "expected_revision": 1, "question": "A new question"})
    assert changed.status_code == 200
    stale = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "service_profile_id": profile, "pilot_brief_revision": 1})
    assert stale.status_code == 409


def test_policy_denies_remote_before_any_preflight_and_exports(client, monkeypatch):
    identity = project(client)
    document = upload(client, identity)
    policy = client.get(f"/api/pilot/projects/{identity}/privacy-policy").json()
    assert policy["revision"] == 0
    policy["policy"]["allow_exports"] = False
    saved = client.put(f"/api/pilot/projects/{identity}/privacy-policy", json={"policy": policy["policy"], "expected_revision": 0})
    assert saved.status_code == 200, saved.text
    denied = client.post(f"/api/projects/{identity}/runs", json={"provider": "cloudflare", "allow_remote_text": True, "document_ids": [document["id"]]})
    assert denied.status_code == 422 and "privacy policy" in denied.text
    report = client.post(f"/api/projects/{identity}/reports", json={"format": "html"})
    assert report.status_code == 403, report.text
    stale = client.put(f"/api/pilot/projects/{identity}/privacy-policy", json={"policy": policy["policy"], "expected_revision": 0})
    assert stale.status_code == 409


def test_source_transform_is_explicit_private_idempotent_and_required_by_policy(client):
    identity = project(client)
    original = upload(client, identity)
    preview = client.post(f"/api/pilot/projects/{identity}/privacy-preview", json={"text": "Alice alice@example.com", "custom_entities": [{"value": "Alice"}]})
    assert preview.status_code == 200, preview.text
    assert "alice@example.com" not in preview.json()["transformed_text"]
    assert preview.json()["persisted"] is False
    body = {"document_ids": [original["id"]], "custom_entities": [{"value": "Alice"}]}
    transformed = client.post(f"/api/pilot/projects/{identity}/privacy/transforms", json=body)
    assert transformed.status_code == 201, transformed.text
    record = transformed.json()
    clone = record["created_documents"][0]
    assert clone["id"] != original["id"]
    fetched = client.get(f"/api/documents/{clone['id']}").json()
    assert "alice@example.com" not in json.dumps(fetched)
    assert "Alice" not in json.dumps(fetched)
    assert client.get(f"/api/documents/{original['id']}").json()["sha256"] == original["sha256"]
    duplicate = client.post(f"/api/pilot/projects/{identity}/privacy/transforms", json=body)
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["created_document_ids"] == record["created_document_ids"]
    policy = client.get(f"/api/pilot/projects/{identity}/privacy-policy").json()
    policy["policy"]["identity_mode"] = "pseudonymized"
    assert client.put(f"/api/pilot/projects/{identity}/privacy-policy", json={"policy": policy["policy"], "expected_revision": 0}).status_code == 200
    blocked = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "document_ids": [original["id"]]})
    assert blocked.status_code == 422
    allowed = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "document_ids": [clone["id"]]})
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["config"]["privacy_policy_snapshot"]["policy"]["identity_mode"] == "pseudonymized"
    denied = client.post(f"/api/pilot/projects/{identity}/privacy/transforms/{record['id']}/reveal")
    assert denied.status_code == 403


def test_selection_scope_signals_and_estimate_no_silent_broadening(client):
    identity, other = project(client), project(client, "Other company")
    first, second = upload(client, identity), upload(client, other)
    prefix = f"/api/pilot/projects/{identity}"
    assert client.post(prefix + "/signals", json={"document_ids": []}).status_code == 422
    assert client.post(prefix + "/estimate", json={"document_ids": [second["id"]]}).status_code == 404
    signals = client.post(prefix + "/signals", json={"document_ids": [first["id"]]})
    assert signals.status_code == 200, signals.text
    assert signals.json()["model_requests"] == 0
    assert signals.json()["sources"][0]["observations"]
    estimate = client.post(prefix + "/estimate", json={"document_ids": [first["id"]]})
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["modeled_cost"] is None


def test_changed_or_forged_transformation_cannot_bypass_egress_policy(client):
    identity = project(client)
    original = upload(client, identity)
    transform = client.post(f"/api/pilot/projects/{identity}/privacy/transforms", json={"document_ids": [original["id"]]}).json()
    clone = transform["created_documents"][0]
    policy = client.get(f"/api/pilot/projects/{identity}/privacy-policy").json()["policy"]
    policy["identity_mode"] = "pseudonymized"
    assert client.put(f"/api/pilot/projects/{identity}/privacy-policy", json={"policy": policy, "expected_revision": 0}).status_code == 200
    assert client.patch(f"/api/documents/{clone['id']}", json={"author": "An unmasked new identity"}).status_code == 409
    store = client.app.state.store
    store.execute("UPDATE documents SET author=? WHERE id=?", ["An unmasked new identity", clone["id"]])
    denied = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "document_ids": [clone["id"]]})
    assert denied.status_code == 422 and "metadata changed" in denied.text
    metadata = json.loads(store.one("SELECT metadata_json FROM documents WHERE id=?", [original["id"]])["metadata_json"])
    metadata["privacy_transform_id"] = transform["id"]
    store.execute("UPDATE documents SET metadata_json=? WHERE id=?", [json.dumps(metadata), original["id"]])
    forged = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "document_ids": [original["id"]]})
    assert forged.status_code == 422 and "registered output" in forged.text


def wait_for_erasure(client, identity):
    for _ in range(100):
        response = client.get(f"/api/pilot/erase-jobs/{identity}")
        assert response.status_code == 200, response.text
        value = response.json()
        if value["status"] in {"completed", "failed"}:
            return value
        if value["status"] == "waiting":
            client.post(f"/api/pilot/erase-jobs/{identity}/resume")
        time.sleep(0.01)
    pytest.fail("Local erasure failed to settle")


def test_erase_more_than_one_batch_and_idempotent_receipt_after_project_deletion(client):
    identity, other = project(client), project(client, "Keep this workspace")
    original, survivor = upload(client, identity), upload(client, other)
    store = client.app.state.store
    directory = store.object_path(f"originals/{identity}/failure-fixtures")
    directory.mkdir(parents=True)
    for index in range(220):
        (directory / str(index)).write_text("Private temporary source")
    body = {"confirm_project_name": "Pilot source collection", "idempotency_key": "erase-api-test-001"}
    created = client.post(f"/api/pilot/projects/{identity}/erase-jobs", json=body)
    assert created.status_code == 202, created.text
    result = wait_for_erasure(client, created.json()["id"])
    assert result["status"] == "completed", result
    assert not list(directory.glob("*"))
    assert client.get(f"/api/documents/{original['id']}").status_code == 404
    assert client.get(f"/api/documents/{survivor['id']}").status_code == 200
    again = client.post(f"/api/pilot/projects/{identity}/erase-jobs", json=body)
    assert again.status_code == 202 and again.json()["id"] == created.json()["id"]
    assert client.get(f"/api/pilot/projects/{identity}/erase-jobs").json()[0]["status"] == "completed"


def test_failed_clone_transaction_remains_owned_and_erasable(client, monkeypatch):
    identity = project(client)
    original = upload(client, identity)
    store = client.app.state.store
    original_execute = store.execute

    def fail_registry(sql, params=()):
        if sql.startswith("INSERT INTO pilot_transforms"):
            raise RuntimeError("Injected registry failure")
        return original_execute(sql, params)

    monkeypatch.setattr(store, "execute", fail_registry)
    with pytest.raises(RuntimeError, match="Injected registry"):
        client.post(f"/api/pilot/projects/{identity}/privacy/transforms", json={"document_ids": [original["id"]]})
    monkeypatch.setattr(store, "execute", original_execute)
    rows = store.query("SELECT path FROM privacy_artifacts WHERE project_id=? AND kind='transformed_source'", [identity])
    assert rows and any((store.root / row["path"]).exists() for row in rows)
    created = client.post(f"/api/pilot/projects/{identity}/erase-jobs", json={"confirm_project_name": "Pilot source collection", "idempotency_key": "erase-failed-clone"})
    assert created.status_code == 202, created.text
    assert wait_for_erasure(client, created.json()["id"])["status"] == "completed"
    assert not any((store.root / row["path"]).is_dir() and any((store.root / row["path"]).iterdir()) for row in rows)


@pytest.mark.parametrize("profile", ["company", "public", "personal"])
def test_every_pilot_package_generates_authenticated_real_report_files(client, profile):
    identity = project(client)
    source = upload(client, identity)
    assert client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": profile, "expected_revision": 0}).status_code == 200
    report = client.post(f"/api/pilot/projects/{identity}/reports", json={"document_ids": [source["id"]]})
    assert report.status_code == 201, report.text
    value = report.json()
    assert value["reference_status"] == "reference_pending" and value["review_status"] == "provisional"
    assert {item["format"] for item in value["artifacts"]} == {"pdf", "html", "xlsx", "json"}
    for artifact in value["artifacts"]:
        downloaded = client.get(artifact["download_url"])
        assert downloaded.status_code == 200
        assert len(downloaded.content) == artifact["bytes"]
        assert downloaded.headers["cache-control"] == "private, no-store"
    rejected = client.post(f"/api/pilot/projects/{identity}/reports", json={"document_ids": [source["id"]], "expert_commentary": [{"document_id": source["id"], "actor_id": "a-fake-expert"}]})
    assert rejected.status_code == 422


def test_pasted_writing_is_explicitly_stored_and_quotes_remain_context_after_masking(client):
    identity = project(client)
    text = "Please review this, Alice.\n> Could you email alice@example.com?\n"
    created = client.post(f"/api/pilot/projects/{identity}/drafts", json={"text": text, "title": "Selected thread"})
    assert created.status_code == 201, created.text
    source = client.get(f"/api/documents/{created.json()['id']}").json()
    assert "".join(block["text"] for block in source["blocks"]) == text
    assert source["metadata"]["retention"] == "saved_source_not_transient"
    assert any(block["mail_role"] == "quoted" and not block["eligible_for_author_counts"] for block in source["blocks"])
    assert client.get(f"/api/documents/{source['id']}/source").content.decode() == text
    masked = client.post(f"/api/pilot/projects/{identity}/privacy/transforms", json={"document_ids": [source["id"]], "custom_entities": [{"value": "Alice"}]})
    assert masked.status_code == 201, masked.text
    clone = client.get(f"/api/documents/{masked.json()['created_document_ids'][0]}").json()
    assert clone["metadata"]["mail_context_version"] == "pseudonymized-mail-1"
    assert any(block["mail_role"] == "quoted" and not block["eligible_for_author_counts"] for block in clone["blocks"])
    assert "alice@example.com" not in json.dumps(clone)
    signals = client.post(f"/api/pilot/projects/{identity}/signals", json={"document_ids": [clone["id"]]}).json()
    quoted = [item for item in signals["sources"][0]["observations"] if not item["count_eligible"]]
    assert quoted


def test_report_download_rejects_bytes_changed_under_a_saved_digest(client):
    identity = project(client)
    source = upload(client, identity)
    report = client.post(f"/api/pilot/projects/{identity}/reports", json={"document_ids": [source["id"]], "formats": ["json"]}).json()
    artifact = report["artifacts"][0]
    path = client.app.state.store.object_path(artifact["object_key"])
    body = path.read_bytes()
    path.write_bytes(b"X" + body[1:])
    assert path.stat().st_size == artifact["bytes"]
    denied = client.get(artifact["download_url"])
    assert denied.status_code == 409


def test_profile_default_policy_blocks_remote_before_saved_policy(client):
    identity = project(client)
    source = upload(client, identity)
    brief = client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": "company", "expected_revision": 0}).json()
    denied = client.post(f"/api/projects/{identity}/runs", json={"provider": "cloudflare", "allow_remote_text": True,
        "service_profile_id": "company", "pilot_brief_revision": brief["revision"], "document_ids": [source["id"]]})
    assert denied.status_code == 422 and "privacy policy" in denied.text


def test_report_keeps_actual_run_provenance_and_rejects_relabelled_history(client):
    identity = project(client)
    source = upload(client, identity)
    brief = client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": "public", "expected_revision": 0}).json()
    run = client.post(f"/api/projects/{identity}/runs", json={"provider": "lexical", "service_profile_id": "public",
        "pilot_brief_revision": brief["revision"], "document_ids": [source["id"]]}).json()
    body = {"document_ids": [source["id"]], "formats": ["json"], "run_id": run["id"]}
    created = client.post(f"/api/pilot/projects/{identity}/reports", json=body)
    assert created.status_code == 201, created.text
    saved = client.get(created.json()["artifacts"][0]["download_url"]).json()
    assert saved["run_provenance"]["run_id"] == run["id"]
    assert saved["run_provenance"]["coverage"]["selected_sources"][0]["document_id"] == source["id"]
    assert saved["run_provenance"]["question"] == brief["question"]
    assert client.patch(f"/api/documents/{source['id']}", json={"author": "A changed credit"}).status_code == 200
    changed_source = client.post(f"/api/pilot/projects/{identity}/reports", json=body)
    assert changed_source.status_code == 409 and "Source metadata changed" in changed_source.text
    assert client.put(f"/api/pilot/projects/{identity}/brief", json={"profile_id": "public", "expected_revision": 1, "question": "A different political question"}).status_code == 200
    changed_brief = client.post(f"/api/pilot/projects/{identity}/reports", json=body)
    assert changed_brief.status_code == 409 and "inquiry brief" in changed_brief.text
