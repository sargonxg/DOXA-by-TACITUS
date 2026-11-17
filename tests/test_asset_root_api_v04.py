"""Installed-package assets resolve independently from parser/runtime paths."""
import json

from fastapi.testclient import TestClient

from doxa.api import ROOT, create_app


def test_demo_assets_and_connected_services_use_explicit_external_root(tmp_path, monkeypatch):
    assets = tmp_path / "external-assets"
    documents = assets / "corpus" / "documents"
    documents.mkdir(parents=True)
    for index in range(20):
        source = {"id": f"fixture-{index}", "title": f"Synthetic asset fixture {index}", "author": "Test fixture",
                  "license": "SYNTHETIC-TEST", "blocks": [{"id": "b1", "text": f"Synthetic external source {index}."}]}
        (documents / f"{index}.json").write_text(json.dumps(source))
    monkeypatch.setenv("DOXA_ASSET_ROOT", str(assets))
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    monkeypatch.delenv("DOXA_INTERNAL_TOKEN", raising=False)
    seen = []
    def imported(_store, _project, root, **_kwargs):
        seen.append(root)
        return {"collection": "news", "documents": 0, "synthetic_test": True}
    monkeypatch.setattr("doxa.news.import_news", imported)
    with TestClient(create_app(tmp_path / "workspace", start_workers=False, use_saved_showcase=False)) as client:
        pid = client.post("/api/projects", json={"name": "External assets"}).json()["id"]
        legacy = client.post(f"/api/projects/{pid}/demo")
        assert legacy.status_code == 200, legacy.text
        assert len(legacy.json()["documents"]) == 20
        assert all(item["title"].startswith("Synthetic asset fixture") for item in legacy.json()["documents"])
        modern = client.post(f"/api/v3/projects/{pid}/demo", json={"collection": "news"})
        assert modern.status_code == 200 and seen == [assets]
    assert ROOT != assets  # extraction cwd and .env lookup stay runtime-owned
