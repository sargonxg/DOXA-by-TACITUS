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
from doxa.scorecard_graph import verify_projection
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
    study="/studies/muslim-coverage/"
    assert "What the model flagged" in client.get(study+"index.html").text
    for name, magic in (("report.pdf",b"%PDF"),("evidence.xlsx",b"PK"),("sources.zip",b"PK")):
        assert client.get(study+name).content.startswith(magic)
    graph=client.get(study+"evidence.jsonld")
    assert graph.headers["content-type"]=="application/ld+json"
    assert sum("doxa:Decision" in row["@type"] for row in graph.json()["@graph"])==822
    assert client.head(study+"evidence.jsonld").status_code==200
    assert client.get(study+"missing.pdf").status_code==404
    case="/studies/antisemitism/"
    assert "Reading antisemitism across three frameworks" in client.get(case+"index.html").text
    assert client.get(case+"evidence-desk.png").content.startswith(bytes.fromhex("89504e47"))
    assert client.get(case+"antisemitism-case.pdf").content.startswith(b"%PDF")
    assert sum("doxa:Decision" in row["@type"] for row in client.get(case+"evidence.jsonld").json()["@graph"])==81
    scorecard="/studies/scorecard/"
    example=client.get(scorecard+"scorecard.json").json()
    assert example["index"]["value"] == 60
    assert verify_projection(client.get(scorecard+"scorecard-evidence.jsonld").json(),example)["criteria_checked"]==4
    assert client.get(scorecard+"doxa-scorecard.pdf").content.startswith(b"%PDF")
    assert client.get(scorecard+"starter.zip").content.startswith(b"PK")
    project=client.post("/api/projects",json={"name":"Installed wheel smoke"}).json()["id"]
    assert client.get("/api/projects/"+project+"/documents").status_code==200
    assert client.get("/api/v3/projects/"+project+"/report-jobs").status_code==200
    root="/api/v3/projects/"+project
    archive=client.get(scorecard+"civic-square-sources.zip")
    assert archive.status_code==200 and archive.content.startswith(b"PK")
    intake=client.post(root+"/imports",json={"name":"Civic Square","total_files":4})
    assert intake.status_code in {200,201}, intake.text
    uploaded=client.post(root+"/imports/"+intake.json()["id"]+"/archive",
                         files={"file":("civic-square-sources.zip",archive.content,"application/zip")})
    assert uploaded.is_success, uploaded.text
    documents=client.get("/api/projects/"+project+"/documents").json()
    assert len(documents)==4 and all(row["status"]=="ready" for row in documents), documents
    transcript=next(row for row in documents if row["filename"]=="committee-speech.srt")
    detail=client.get("/api/documents/"+transcript["id"]).json()
    assert detail["blocks"][0]["cue_start_ms"]==5000
    caption="The councillor said, “la reunión seguirá.”"
    cue=("WEBVTT\\n\\n00:01.000 --> 00:03.000\\n"+caption+"\\n").encode("utf-8")
    uploaded=client.post("/api/projects/"+project+"/upload",
                         files={"files":("unicode.vtt",cue,"text/vtt")})
    assert uploaded.is_success, uploaded.text
    document=uploaded.json()[0]
    assert document["status"]=="ready", document
    assert client.get("/api/documents/"+document["id"]).json()["blocks"][0]["text"]==caption
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
print("Installed wheel UI, evidence-graph verification, ZIP and caption intake, and XLSX report worker passed without checkout assets or model calls.")
'''
        env = {key: value for key, value in os.environ.items() if not key.startswith(("DOXA_", "CLOUDFLARE_", "OPENAI_", "RUNPOD_"))}
        env.update({"PYTHONPATH": str(target), "DOXA_WHEEL_TARGET": str(target), "DOXA_DATA_DIR": str(Path(temporary) / "data")})
        subprocess.run([sys.executable, "-c", code], cwd=empty, env=env, check=True)


if __name__ == "__main__":
    main()
