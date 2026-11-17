"""Explicit re-extraction preserves every historical source and analysis unit."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from test_api import project, upload
from doxa.api import create_app
from doxa.contextual import build_units


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    monkeypatch.delenv("DOXA_INTERNAL_TOKEN", raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as result:
        yield result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_reextraction_creates_an_immutable_derived_version_and_replay_is_idempotent(client):
    pid = project(client)
    original = upload(client, pid, "Original paragraph. A precise source quotation.")
    original_detail = client.get(f"/api/documents/{original['id']}").json()
    original_blocks = original_detail["blocks"]
    result = client.post(f"/api/documents/{original['id']}/re-extract")
    assert result.status_code == 201, result.text
    derived = result.json()["document"]
    assert derived["id"] != original["id"] and derived["sha256"] != original["sha256"]
    assert derived["metadata"]["raw_sha256"] == original["sha256"]
    assert derived["metadata"]["derived_from_document_id"] == original["id"]
    assert derived["batch_id"] != original["batch_id"]
    assert client.app.state.store.one("SELECT COUNT(*) n FROM batch_members WHERE batch_id=?", [original["batch_id"]])["n"] == 1
    assert result.json()["original_preserved"] is True
    assert client.get(f"/api/documents/{original['id']}").json() == original_detail
    assert client.get(f"/api/documents/{derived['id']}").json()["blocks"] == original_blocks
    replay = client.post(f"/api/documents/{original['id']}/re-extract").json()
    assert replay["duplicate"] and replay["document"]["id"] == derived["id"]
    assert client.post(f"/api/documents/{derived['id']}/re-extract").json()["document"]["id"] == derived["id"]
    new_detail = client.get(f"/api/documents/{derived['id']}").json()
    assert {unit["id"] for unit in build_units(original_detail)}.isdisjoint(unit["id"] for unit in build_units(new_detail))
    # Shared raw originals outlive deletion of an individual derived version.
    assert client.delete(f"/api/documents/{derived['id']}").status_code == 200
    assert client.get(f"/api/documents/{original['id']}/source").status_code == 200


def test_new_extractor_profile_does_not_change_old_run_sources_or_anchors(client, monkeypatch):
    import doxa.api as api
    from test_v3_api import finish, start_mock
    pid = project(client)
    original = upload(client, pid, "COUNTER. A writer rejects collective blame.")
    before = client.get(f"/api/documents/{original['id']}").json()
    run, _ = start_mock(client, pid, monkeypatch)
    extractor = api.extract_path
    def revised(path, filename):
        result = extractor(path, filename)
        result["blocks"][0]["text"] += " Additional text recognized by a revised extractor."
        manifest = result["extraction_manifest"]
        manifest["blocks_sha256"] = digest(result["blocks"])
        manifest["extractor_version"] = "synthetic-new-extraction-profile"
        manifest["extraction_version_id"] = digest({"profile": "v2", "blocks": manifest["blocks_sha256"]})
        result["metadata"]["extraction_manifest"] = manifest
        return result
    monkeypatch.setattr(api, "extract_path", revised)
    response = client.post(f"/api/documents/{original['id']}/re-extract")
    assert response.status_code == 201, response.text
    derived = response.json()["document"]
    assert derived["sha256"] != original["sha256"]
    assert finish(client, run["id"])["status"] == "completed"
    assert client.get(f"/api/documents/{original['id']}").json() == before
    current = client.get(f"/api/runs/{run['id']}").json()
    assert current["config"]["source_snapshot"] == [{"id": original["id"], "sha256": original["sha256"]}]
    assert client.app.state.store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=? AND document_id=?", [run["id"], derived["id"]])["n"] == 0


def test_reextraction_refuses_a_changed_original_and_preserves_all_versions(client):
    pid = project(client)
    original = upload(client, pid, "Retain the original hash.")
    store = client.app.state.store
    store.put_bytes(original["object_key"], b"tampered bytes")
    result = client.post(f"/api/documents/{original['id']}/re-extract")
    assert result.status_code == 409
    assert store.one("SELECT COUNT(*) n FROM documents WHERE project_id=?", [pid])["n"] == 1


def test_large_page_ledger_is_stored_once_and_returned_in_bounded_pages(client, monkeypatch):
    import doxa.api as api
    pid = project(client)
    extractor = api.extract_path
    def many_pages(path, filename):
        result = extractor(path, filename)
        result["extraction_manifest"]["pages"] = [{"page": index, "method": "text", "characters": 80, "warnings": ["Synthetic pagination for storage-bound testing only"], "needs_review": False} for index in range(10000)]
        return result
    monkeypatch.setattr(api, "extract_path", many_pages)
    original = upload(client, pid, "A synthetic ledger storage test.")
    assert original["metadata"]["extraction_manifest"]["pages_count"] == 10000
    assert len(json.dumps(original).encode()) < 20000
    response = client.get(f"/api/documents/{original['id']}/extraction", params={"offset": 199, "limit": 200})
    assert response.status_code == 200
    result = response.json()
    assert result["total_pages"] == 10000 and len(result["pages"]) == 200
    assert result["pages"][0]["page"] == 199 and result["next_offset"] == 399
    assert client.get(f"/api/documents/{original['id']}/extraction", params={"limit": 201}).status_code == 422
