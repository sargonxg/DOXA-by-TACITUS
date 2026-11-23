import csv
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.extraction import extract_document
from doxa.laptop import MODEL, PROFILE
from doxa.scorecard_graph import criterion_rate
from doxa.scorecards import _index, _source_outcome, _summary


class LaptopFixture:
    name = "ollama"
    profile = {"provider": "ollama", "model": MODEL, "mode": "local", "revision": "fixture-q4",
               "quantization": "Q4_K_M", "context_limit": 4096, "source_pack_bytes": 2100}

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_args):
        return 0

    async def generate(self, messages, _schema, **_options):
        body = json.loads(messages[-1]["content"])
        decisions, details = [], []
        for unit in body["units"]:
            sentence = unit["scored_sentences"][0]
            target = "custom/risk" if "risk" in sentence["text"] else "custom/remedy"
            decisions.append({"unit_id": unit["unit_id"], "results": [int(key == target) for key in body["criterion_order"]]})
            if body["phase"] != "classify" and target in body["criterion_order"]:
                details.append({"unit_id": unit["unit_id"], "criterion_key": target,
                                "evidence_sentence_ids": [sentence["id"]], "title": "Contextual fixture",
                                "explanation": "The supplied sentence states the criterion.", "alternative": "The surrounding context could narrow the statement.",
                                "target": "demonstrations", "proposition": "consider emergency access",
                                "speaker": "Unknown", "attribution": "unknown", "speaker_stance": "supports",
                                "document_stance": "supports", "hypothetical": False, "certainty": "supported"})
        return {"content": {"decisions": decisions, "details": details}, "finish_reason": "stop",
                "usage": {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0}}


def test_native_caption_scorecard_job_and_review(tmp_path, monkeypatch):
    provider = LaptopFixture()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _config: provider)
    with TestClient(create_app(tmp_path / "app", start_workers=False, use_saved_showcase=False)) as client:
        project = client.post("/api/projects", json={"name": "Timed framing test"}).json()["id"]
        root = f"/api/v3/projects/{project}"
        archive = io.BytesIO()
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("left.srt", "1\n00:00:02,125 --> 00:00:07,250\nThe report cites a documented safety risk.\n")
            bundle.writestr("right.vtt", "WEBVTT\n\n00:05.000 --> 00:08.300\nThe plan proposes a specific remedy.\n")
        session = client.post(root + "/imports", json={"name": "Speeches", "total_files": 2}).json()
        response = client.post(root + f"/imports/{session['id']}/archive", files={"file": ("sources.zip", archive.getvalue())})
        assert response.is_success, response.text
        docs = client.get(f"/api/projects/{project}/documents").json()
        assert {item["document_type"] for item in docs} == {"speech transcript"}
        options = {"execution_profile": PROFILE, "lens_ids": ["custom"],
                   "question": "How is this policy justified?",
                   "custom_lens": {"criteria": [{"id": "risk", "label": "Risk", "description": "Cites a documented safety risk."},
                                                {"id": "remedy", "label": "Remedy", "description": "Proposes a specific remedy."}]},
                   "index_spec": {"name": "Justification", "weights": {"custom/risk": 60, "custom/remedy": 40}}}
        preflight = client.post(root + "/preflight", json=options).json()
        assert preflight["fits"] and preflight["units"] == 2 and preflight["decisions"] == 4
        response = client.post(root + "/runs", json=options)
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["config"]["budget_usd"] == 0 and run["config"]["model_revision"] == "fixture-q4"
        assert run["config"]["index_spec"]["weights"] == options["index_spec"]["weights"]
        run_url = f"/api/runs/{run['id']}"
        client.portal.call(client.app.state.work_once, run["id"])
        assert client.post(run_url + "/pause").json()["status"] == "paused_operator"
        assert not client.portal.call(client.app.state.work_once, run["id"])["pending"]
        assert client.post(run_url + "/resume").is_success
        run = client.portal.call(client.app.state.work_once, run["id"])["run"]
        assert run["status"] == "completed", run["errors"]
        left, right = (next(item["id"] for item in docs if item["filename"].startswith(prefix)) for prefix in ("left", "right"))
        comparison = client.post(root + "/comparisons", json={
            "left_document_ids": [left], "right_document_ids": [right], "run_id": run["id"],
            "per_document": {identity: {"event": "hearing", "topic": "transport", "published_at": "2026-01-01",
                                        "source_family": identity} for identity in (left, right)},
        }).json()

        def snapshot(basis="model", ids=None):
            query = client.post(root + "/query", json={"run_id": run["id"], "document_ids": ids or []}).json()
            response = client.post(root + "/scorecards", json={"query_id": query["query_id"],
                                   "comparison_id": comparison["id"], "basis": basis})
            assert response.status_code == 201, response.text
            return response.json()

        base = snapshot()
        assert base["index"]["value"] == 50 and base["index"]["range"] == [50, 50]
        assert base["comparison"]["criteria"][0]["difference_pp"] == 100
        assert snapshot(ids=[left])["index"]["status"] == "scope_changed"
        cell = next(item for item in base["matrix"] if item["document_id"] == left and item["status"] == "positive")
        assert cell["evidence"]["cue_start_ms"] == 2125
        response = client.patch(f'/api/v3/findings/{cell["evidence"]["assessment_id"]}/review',
                                json={"review_status": "revised", "corrections": {"result": "does_not_meet"}})
        assert response.is_success, response.text
        assert snapshot()["index"]["value"] == 50
        revised = snapshot("review_overlay")
        assert revised["index"]["value"] == 20
        assert client.get(root + "/scorecards/" + base["id"]).json()["index"]["value"] == 50
        other = client.post("/api/projects", json={"name": "Different scope"}).json()["id"]
        assert client.get(f"/api/v3/projects/{other}/scorecards/{base['id']}").status_code == 404
        response = client.post(root + "/report-jobs", json={"scorecard_id": base["id"], "format": "csv"})
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        manager = client.app.state.report_jobs
        assert manager.work_once(job)["status"] == "completed", manager.get(job)
        artifacts = f"/api/v3/report-jobs/{job}/artifacts/"
        rows = list(csv.DictReader(io.StringIO(client.get(artifacts + "csv").text)))
        assert rows[0]["index_value"] == "50.0"
        graph = client.get(artifacts + "jsonld").json()
        assert criterion_rate(graph, "custom/risk")["rate"] == 0.5


@pytest.mark.parametrize("filename,content", [
    ("broken.srt", "1\n00:00:02,500 --> 00:00:01,000\nDiscarded.\n\n2\n00:00:05,050 --> 00:00:07,250\nCivic debate.\n"),
    ("broken.vtt", "WEBVTT\n\n00:02.500 --> bad\nDiscarded.\n\n00:05.050 --> 00:07.250\nCivic debate.\n"),
])
def test_partial_extraction_requires_review(tmp_path: Path, filename, content):
    source = tmp_path / filename
    source.write_text(content, encoding="utf-8")
    result = extract_document(source)
    assert result["blocks"][0]["text"] == "Civic debate."
    assert result["blocks"][0]["cue_start_ms"] == 5050
    assert result["metadata"]["malformed_cues"] == 1
    assert not result["extraction_manifest"]["source_coverage_complete"]
    assert _source_outcome(1, {"meets_criterion": 1}, False) == "incomplete"


def test_scorecard_sensitivity_and_reliable_coverage():
    cells = [{"status": status, "retained": int(status != "incomplete"), "planned": 1}
             for status in ("positive", "positive", "negative", "negative", "uncertain", "inapplicable")]
    row = _summary(cells)
    assert (row["rate"], row["coverage"], row["eligible"]) == (0.5, 0.8, 5)
    spec = {"weights": {"a": 60, "b": 40}, "minimum_coverage": 0.8}
    value = _index(spec, [{"key": "a", **row}, {"key": "b", **row}], True, True)
    assert (value["value"], value["range"], value["equal_weight"]) == (50, [40, 60], 50)
    incomplete = _summary(cells + [{"status": "incomplete", "retained": 0, "planned": 1}])
    assert _index(spec, [{"key": "a", **incomplete}, {"key": "b", **row}], True, True)["status"] == "insufficient_coverage"
