#!/usr/bin/env python3
"""Analyze the Civic Square sources through a running native DOXA workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

INPUTS = Path(__file__).resolve().parents[1] / "examples/scorecard-inputs"
TERMINAL = {"completed", "partial", "failed", "cancelled"}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text[:800]}")
    return response


def run_example(client, output, *, pause_once, deadline):
    started = time.monotonic()
    health = request(client, "POST", "/api/v3/models/health",
                     json={"execution_profile": "laptop-8gb-v1"}).json()
    if not health["ok"]:
        raise RuntimeError(health["details"])
    project = request(client, "POST", "/api/projects", json={"name": "Civic Square · laptop example"}).json()["id"]
    root = f"/api/v3/projects/{project}"
    legacy = f"/api/projects/{project}"
    inputs = [("files", (path.name, path.read_bytes(), "text/plain"))
              for path in sorted(INPUTS.iterdir()) if path.suffix in {".txt", ".srt", ".vtt"}]
    documents = request(client, "POST", legacy + "/upload", files=inputs).json()
    if len(documents) != 4 or any(row["status"] != "ready" for row in documents):
        raise RuntimeError("One or more example sources could not be extracted")
    metadata = INPUTS / "metadata.csv"
    request(client, "POST", legacy + "/metadata/import", files={"file": (metadata.name, metadata.read_bytes(), "text/csv")})
    inquiry = json.loads((INPUTS / "inquiry.json").read_text(encoding="utf-8"))
    options = {**inquiry, "execution_profile": "laptop-8gb-v1", "document_ids": [row["id"] for row in documents],
               "persona": "Apply the stated definition to each source. Check the exact evidence and preserve ambiguity."}
    workload = request(client, "POST", root + "/preflight", json=options).json()
    if not workload["fits"]:
        raise RuntimeError("The example does not fit the laptop preset")
    run = request(client, "POST", root + "/runs", json=options).json()
    run_path = f"/api/runs/{run['id']}"
    interrupted = False
    while run["status"] not in TERMINAL:
        if time.monotonic() - started > deadline:
            raise RuntimeError(f"Analysis exceeded the verification deadline. Run {run['id']} is available in Jobs.")
        if run["status"].startswith("paused_"):
            raise RuntimeError(f"Analysis paused with status {run['status']}")
        if pause_once and not interrupted and run["progress"].get("documents_settled"):
            paused = request(client, "POST", run_path + "/pause")
            assert paused.json()["status"] == "paused_operator"
            run = request(client, "POST", run_path + "/resume").json()
            interrupted = True
        time.sleep(1)
        run = request(client, "GET", run_path).json()
    annotations = {row["id"]: {"event": "Civic Square hearing", "topic": "exclusion zone"}
                   for row in documents}
    comparison = request(client, "POST", root + "/comparisons", json={
        "run_id": run["id"], "question": "How do the two issuer groups frame the same proposal?",
        "left_document_ids": [row["id"] for row in documents if row["filename"].startswith("committee")],
        "right_document_ids": [row["id"] for row in documents if row["filename"].startswith("rights")],
        "per_document": annotations,
    }).json()
    query = request(client, "POST", root + "/query", json={"run_id": run["id"]}).json()
    scorecard = request(client, "POST", root + "/scorecards", json={"query_id": query["query_id"], "comparison_id": comparison["id"]}).json()
    output.mkdir(parents=True, exist_ok=True)
    (output / "scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    jobs = [request(client, "POST", root + "/report-jobs", json={"format": format, "scorecard_id": scorecard["id"]}).json()
            for format in ("pdf", "xlsx")]
    for job in jobs:
        while job["status"] not in {"completed", "failed", "cancelled"}:
            if time.monotonic() - started > deadline:
                raise RuntimeError("Report job exceeded the verification deadline")
            time.sleep(0.5)
            job = request(client, "GET", f"/api/v3/report-jobs/{job['id']}").json()
        if job["status"] != "completed":
            raise RuntimeError(f"Report job failed: {job.get('error')}")
        for artifact in job["artifacts"]:
            response = request(client, "GET", artifact["download_url"])
            (output / Path(artifact["filename"]).name).write_bytes(response.content)
    return {"profile": run["config"]["execution_profile"], "model": health["model"], "model_digest": health["revision"],
            "quantization": health["quantization"], "project_id": project, "run_id": run["id"], "status": run["status"],
            "source_units": workload["units"], "planned_decisions": workload["decisions"],
            "retained_decisions": sum(row["retained"] for row in scorecard["criteria"]),
            "index": scorecard["index"], "matched_pairs": len(comparison["pairs"]), "paused_and_resumed": interrupted,
            "seconds": round(time.monotonic() - started, 2), "errors": run.get("errors", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("doxa-civic-square"))
    parser.add_argument("--pause-once", action="store_true")
    parser.add_argument("--deadline", type=int, default=1200)
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1", "localhost"} or url.username or url.path not in {"", "/"}:
        parser.error("Choose a running native DOXA service on HTTP loopback.")
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30) as client:
        result = run_example(client, args.output, pause_once=args.pause_once, deadline=args.deadline)
    (args.output / "run-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
