#!/usr/bin/env python3
"""Check a release wheel in an empty directory with no source checkout assets."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="doxa-installed-wheel-") as temporary:
        target = Path(temporary) / "installed"
        empty = Path(temporary) / "empty"
        empty.mkdir()
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(args.wheel.resolve())], check=True)
        code = '''import os
from pathlib import Path
import doxa
from doxa.api import create_app
from fastapi.testclient import TestClient
assert Path(doxa.__file__).resolve().is_relative_to(Path(os.environ["DOXA_WHEEL_TARGET"]).resolve())
with TestClient(create_app(os.environ["DOXA_DATA_DIR"],start_workers=False,use_saved_showcase=False)) as client:
    health=client.get("/api/health")
    assert health.status_code==200
    page=client.get("/")
    assert page.status_code==200 and '<div id="root">' in page.text
    from doxa.resources import frontend_distribution
    assets=list((frontend_distribution()/"assets").glob("*.js"))
    assert assets and client.get("/assets/"+assets[0].name).status_code==200
    project=client.post("/api/projects",json={"name":"Installed wheel smoke"}).json()["id"]
    assert client.get("/api/projects/"+project+"/documents").status_code==200
    assert client.get("/api/v3/projects/"+project+"/report-jobs").status_code==200
    uploaded=client.post("/api/projects/"+project+"/upload",files=[("files",("wheel-source.txt",b"A local installed-wheel extraction fixture.","text/plain"))])
    assert uploaded.status_code==200, uploaded.text
    documents=client.get("/api/projects/"+project+"/documents").json()
    assert len(documents)==1 and documents[0]["status"]=="ready", documents
    detail=client.get("/api/documents/"+documents[0]["id"]).json()
    assert any(block["text"]=="A local installed-wheel extraction fixture." for block in detail["blocks"])
    query=client.post("/api/v3/projects/"+project+"/query",json={}).json()
    response=client.post("/api/v3/projects/"+project+"/report-jobs",json={"query_id":query["query_id"],"format":"xlsx","publication_type":"archive"})
    assert response.status_code==202, response.text
    identity=response.json()["id"]
    manager=client.app.state.report_jobs
    for _ in range(8):
        manager.work_once(identity,deadline_seconds=420)
        job=manager.get(identity)
        if job["status"] in {"completed","failed","cancelled"}:
            break
    assert job["status"]=="completed", job
    artifact=client.get("/api/v3/report-jobs/"+identity+"/artifacts/xlsx")
    assert artifact.status_code==200 and artifact.content.startswith(b"PK")
print("Installed wheel API, compiled UI, TXT extraction subprocess and actual XLSX report worker passed without checkout assets or model calls.")
'''
        env = {key: value for key, value in os.environ.items() if not key.startswith(("DOXA_", "CLOUDFLARE_", "OPENAI_", "RUNPOD_"))}
        env.update({"PYTHONPATH": str(target), "DOXA_WHEEL_TARGET": str(target), "DOXA_DATA_DIR": str(Path(temporary) / "data")})
        subprocess.run([sys.executable, "-c", code], cwd=empty, env=env, check=True)


if __name__ == "__main__":
    main()
