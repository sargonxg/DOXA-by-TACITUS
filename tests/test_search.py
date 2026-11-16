import json

from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.store import Store


def test_long_document_index_requests_are_bounded_even_with_unicode(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.init()
    original = store.execute
    sizes = []

    def measured(sql, params=()):
        sizes.append(len(json.dumps({"statements": [{"sql": sql, "params": list(params)}]}, ensure_ascii=True).encode()))
        return original(sql, params)

    monkeypatch.setattr(store, "execute", measured)
    text = ("安全 evidence \x00 " * 10000)
    store.index_document("doc", "project", "Long source", [{"id": "b1", "text": text}])
    rows = store.query("SELECT LENGTH(body) AS size FROM document_search WHERE id='doc'")
    assert len(rows) > 10
    assert max(row["size"] for row in rows) <= 6000
    assert max(sizes) < 256 * 1024
    store.close()


def test_search_intersects_document_ids_across_different_index_fragments(tmp_path):
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as client:
        project = client.get("/api/projects").json()[0]["id"]
        body = "firstneedle " + "ordinary words " * 10000 + " lastneedle"
        response = client.post(f"/api/projects/{project}/upload", files={"files": ("long.txt", body.encode(), "text/plain")})
        assert response.status_code == 200
        document = response.json()[0]
        assert document["status"] == "ready"
        rows = client.get(f"/api/projects/{project}/documents", params={"q": "firstneedle lastneedle"}).json()
        assert len(rows) == 1 and rows[0]["id"] == document["id"]
        assert client.get(f"/api/projects/{project}/documents", params={"q": "nonexistent lastneedle"}).json() == []
        client.patch(f"/api/documents/{document['id']}", json={"title": "Correctedtitle"})
        assert len(client.get(f"/api/projects/{project}/documents", params={"q": "Correctedtitle lastneedle"}).json()) == 1
