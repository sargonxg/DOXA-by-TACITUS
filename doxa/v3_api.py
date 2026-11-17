"""Version-three workspace, provenance, learning and model HTTP contracts."""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from contextlib import nullcontext

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool

from .store import utcnow
from .workspace import Workspace, WorkspaceQuery, unpack_assessment, unpack_document


class VerificationInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    allow_remote_text: StrictBool = False
    budget_usd: float = Field(default=2, gt=0, le=2, allow_inf_nan=False, strict=True)


def register(app: FastAPI, services: dict) -> None:
    async def validation_error(_request: Request, error: RequestValidationError):
        # JSON permits exponents whose parsed float overflows. Keep the 422
        # response serializable without accepting the invalid budget or request.
        details = jsonable_encoder(error.errors(), custom_encoder={
            float: lambda value: value if math.isfinite(value) else str(value),
        })
        return JSONResponse(status_code=422, content={"detail": details})

    app.add_exception_handler(RequestValidationError, validation_error)

    def db():
        return services["db"]()

    async def project_thread(project_id, kind, function, *args, **kwargs):
        from .privacy_lifecycle import run_activity
        return await asyncio.to_thread(run_activity, db(), project_id, kind, function, *args, **kwargs)

    def workspace():
        return Workspace(db())

    def project(identity):
        return services["require_project"](identity)

    def actor(request: Request) -> dict:
        from .access import trusted_actor
        return trusted_actor(request, db())

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    from .report_jobs import register as register_report_jobs
    from .casebooks import register_casebooks
    from .review_queue import register_review_queue
    from .evaluation_api import register as register_evaluation
    register_report_jobs(app, services)
    register_casebooks(app, services)
    register_review_queue(app, services)
    register_evaluation(app, {**services, "actor": actor, "call": call})

    @app.post("/api/v3/projects/{project_id}/query")
    def query(project_id: str, body: WorkspaceQuery):
        return workspace().create(project_id, body)

    @app.get("/api/v3/queries/{query_id}")
    def restore_query(query_id: str):
        saved = workspace().require_query(query_id, current=False)
        return workspace().create(saved["project_id"], WorkspaceQuery.model_validate(saved["scope"]))

    @app.get("/api/v3/queries/{query_id}/{kind}")
    def cursor_page(query_id: str, kind: str, cursor: str | None = None, limit: int = 50):
        if kind not in {"documents", "findings"}:
            raise HTTPException(404, "Unknown query resource")
        service = workspace()
        return service.page(service.require_query(query_id), kind, cursor, limit)

    @app.get("/api/v3/documents/{document_id}/context")
    def context(document_id: str, finding_id: str = "", block_id: str = "", radius: int = 2, cursor: int = 0):
        document = services["require_document"](document_id, True)
        finding = None
        if finding_id:
            row = db().one("SELECT * FROM v3_assessments WHERE id=? AND document_id=?", [finding_id, document_id])
            if row:
                finding = unpack_assessment(row)
            else:
                row = db().one("SELECT * FROM findings WHERE id=? AND document_id=?", [finding_id, document_id])
                if not row:
                    raise HTTPException(404, "Evidence does not belong to this source")
                finding = json.loads(row["payload_json"])
                finding["result"] = "legacy_unclassified"
            block_id = finding.get("block_id") or (finding.get("evidence_refs") or [{}])[0].get("block_id", block_id)
        blocks = document.pop("blocks", [])
        radius = max(0, min(radius, 5))
        center = next((i for i, b in enumerate(blocks) if b["id"] == block_id), max(0, min(cursor, len(blocks))))
        start, end = max(0, center - radius), min(len(blocks), center + radius + 1)
        return {"document": document, "blocks": blocks[start:end], "finding": finding, "total_blocks": len(blocks), "start": start, "next_cursor": end if end < len(blocks) else None}

    @app.get("/api/v3/models")
    def models():
        from .model_registry import list_model_profiles
        return {"profiles": list_model_profiles(), "automatic_downloads": False}

    @app.post("/api/v3/models/health")
    async def model_health(body: dict):
        if body.get("model_key") in {"minilm", "deberta"} or body.get("provider") == "cpu":
            from .cpu_models import CPUModels
            return await asyncio.to_thread(CPUModels().health, body.get("model_key"))
        return await services["provider_health"](body)

    def cpu_runtime():
        if not hasattr(app.state, "cpu_runtime"):
            from .cpu_models import CPUModels
            app.state.cpu_runtime = CPUModels(threads=2)
        return app.state.cpu_runtime

    @app.post("/api/v3/projects/{project_id}/semantic-index")
    async def semantic_index(project_id: str, body: dict):
        from .semantic import SemanticIndex
        project(project_id)
        document_ids = body.get("document_ids")
        if body.get("query_id"):
            saved = workspace().require_query(body["query_id"])
            if saved["project_id"] != project_id:
                raise HTTPException(404, "Query does not belong to this project")
            document_ids = workspace().document_ids(saved)
        try:
            return await project_thread(project_id, "semantic_index", SemanticIndex(db(), cpu_runtime()).build, project_id, document_ids)
        except (ValueError, RuntimeError, ImportError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/semantic-search")
    async def semantic_search(project_id: str, body: dict):
        from .semantic import SemanticIndex
        project(project_id)
        text = str(body.get("q") or body.get("text") or "")
        if not 1 <= len(text) <= 2000:
            raise HTTPException(422, "Enter a search query of at most 2,000 characters")
        document_ids = None
        if body.get("query_id"):
            saved = workspace().require_query(body["query_id"])
            if saved["project_id"] != project_id:
                raise HTTPException(404, "Query does not belong to this project")
            document_ids = workspace().document_ids(saved)
        try:
            return await project_thread(project_id, "semantic_search", SemanticIndex(db(), cpu_runtime()).search, project_id, text,
                                           limit=max(1, min(int(body.get("limit", 20)), 50)), document_ids=document_ids)
        except (ValueError, RuntimeError, ImportError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/rubric-score")
    async def rubric_score(project_id: str, body: dict):
        project(project_id)
        document = services["require_document"](str(body.get("document_id", "")), True)
        if document["project_id"] != project_id:
            raise HTTPException(404, "Source does not belong to this project")
        hypotheses = body.get("hypotheses") or []
        if not isinstance(hypotheses, list) or not 1 <= len(hypotheses) <= 12:
            raise HTTPException(422, "Supply one to twelve explicit rubric hypotheses")
        blocks = document["blocks"]
        if body.get("block_id"):
            blocks = [block for block in blocks if block["id"] == body["block_id"]]
        if not blocks or sum(len(block["text"]) for block in blocks) > 30000:
            raise HTTPException(422, "Select a source block when scoring more than 30,000 characters")
        try:
            results = []
            for block in blocks:
                value = await project_thread(project_id, "rubric_score", cpu_runtime().score_rubrics, block["text"], hypotheses)
                results.append({"block_id": block["id"], **value})
            return {"document_id": document["id"], "results": results, "semantic_coverage": "selected blocks only",
                    "notice": "Experimental uncalibrated entailment scores, not prejudice findings or probabilities."}
        except (ValueError, RuntimeError, ImportError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/runs", status_code=201)
    async def new_run(project_id: str, body: dict):
        from .api import RunInput
        project(project_id)
        options = {**body, "methodology_version": "3.0"}
        query_id = options.pop("query_id", None)
        if query_id:
            saved = workspace().require_query(query_id)
            if saved["project_id"] != project_id:
                raise HTTPException(404, "Query does not belong to this project")
            options["document_ids"] = workspace().document_ids(saved)
        try:
            parsed = RunInput.model_validate(options)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return await services["create_run"](project_id, parsed)

    @app.get("/api/v3/projects/{project_id}/runs")
    def runs(project_id: str, limit: int = 30):
        project(project_id)
        rows = db().query("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", [project_id, max(1, min(limit, 100))])
        output = []
        for row in rows:
            value = dict(row)
            for key in ("config_json", "progress_json", "usage_json", "model_profile_json", "errors_json"):
                value[key[:-5]] = json.loads(value.pop(key) or "{}")
            value["source_documents"] = len(value["config"].pop("source_snapshot", []))
            value["config"].pop("document_ids", None)
            if value["config"].get("verification_selection"):
                value["config"]["verification_selection"] = {key: item for key, item in value["config"]["verification_selection"].items() if key not in {"document_ids", "unit_ids"}}
            output.append(value)
        return output

    @app.get("/api/v3/runs/{run_id}/status")
    def run_status(run_id: str):
        row = db().one("SELECT id,project_id,status,created_at,started_at,completed_at,progress_json,usage_json,cancel_requested,spent_usd,reserved_usd FROM runs WHERE id=?", [run_id])
        if not row:
            raise HTTPException(404, "Run not found")
        value = dict(row)
        value["progress"] = json.loads(value.pop("progress_json"))
        value["usage"] = json.loads(value.pop("usage_json"))
        value["units"] = db().query("SELECT status,COUNT(*) count FROM v3_unit_statuses WHERE run_id=? GROUP BY status", [run_id])
        return value

    @app.post("/api/v3/projects/{project_id}/verification", status_code=201)
    async def verify_run(project_id: str, body: VerificationInput):
        from .api import RunInput
        project(project_id)
        return await services["create_run"](project_id, RunInput(provider="cloudflare", model="@cf/openai/gpt-oss-120b",
                         methodology_version="3.0", verification_of=body.run_id,
                         reasoning_effort="low", reasoning_token_allowance=2048,
                         allow_remote_text=body.allow_remote_text, budget_usd=body.budget_usd))

    @app.get("/api/v3/projects/{project_id}/verification/{verifier_id}")
    def verification_report(project_id: str, verifier_id: str):
        from .verification import compare_runs
        run = services["require_run"](verifier_id, project_id)
        primary_id = run["config"].get("verification_of")
        if not primary_id:
            raise HTTPException(422, "Choose a run created for independent verification")
        return call(compare_runs, db(), project_id, primary_id, verifier_id)

    @app.post("/api/v3/projects/{project_id}/preflight")
    def preflight(project_id: str, body: dict):
        project(project_id)
        service = workspace()
        if body.get("query_id"):
            saved = service.require_query(body["query_id"])
            if saved["project_id"] != project_id:
                raise HTTPException(404, "Query does not belong to this project")
            metrics = saved["aggregates"]["metrics"]
        else:
            metrics = service.create(project_id, WorkspaceQuery())["metrics"]
        words, documents = metrics["total_words"], metrics["documents"]
        # Declared conservative planning assumptions, superseded by measured usage.
        calls = max(documents, (words + 1199) // 1200)
        input_tokens = int(words * 2.1 + calls * 2200)
        output_tokens = calls * 2000
        provider = body.get("provider", "cloudflare")
        local = provider in {"ollama", "lexical"}
        estimate = 0 if local else (input_tokens * 0.10 + output_tokens * 0.30) / 1_000_000
        return {"documents": documents, "words": words, "estimated_requests": calls,
                "estimated_input_tokens": input_tokens, "maximum_output_tokens": output_tokens,
                "estimated_upper_usd": round(estimate * 1.3, 4), "retry_allowance": 0.30,
                "verification_allowance_usd": 0 if local else 2, "monthly_cap_usd": 20,
                "source_text_destination": "local Ollama" if local else body.get("model") or "configured cloud model",
                "basis": "Planning estimate at Gemma rates with 2,000 output tokens/request; not an invoice or speed guarantee.",
                "full_scope": True, "automatic_sampling": False}

    @app.patch("/api/v3/findings/{finding_id}/review")
    def review(finding_id: str, body: dict, request: Request):
        from .learning import record_review
        record = db().one("SELECT * FROM v3_assessments WHERE id=?", [finding_id])
        if not record:
            raise HTTPException(409, "Historical observations remain under legacy review; start a v3 run for structured corrections")
        identity = actor(request)
        status = body.get("review_status", "confirmed")
        if status not in {"unreviewed", "confirmed", "rejected", "revised"}:
            raise HTTPException(422, "Unknown review state")
        corrections = body.get("corrections") or {}
        if not isinstance(corrections, dict):
            raise HTTPException(422, "Corrections must be structured fields")
        with nullcontext() if db().remote else db().transaction(write=True):
            event = call(record_review, db(), record["project_id"], finding_id, corrections, **identity,
                         note=str(body.get("review_note", ""))[:12000], adjudicated=status in {"confirmed", "revised"})
            db().batch([
                {"sql": "UPDATE v3_assessments SET review_status=?,review_note=? WHERE id=?", "params": [status, str(body.get("review_note", ""))[:12000], finding_id]},
                workspace().touch_statement(record["project_id"]),
            ])
        result = unpack_assessment(db().one("SELECT * FROM v3_assessments WHERE id=?", [finding_id]))
        return {**result, "review_event": event}

    @app.get("/api/v3/projects/{project_id}/learning")
    def learning(project_id: str):
        from .learning import learning_overview
        project(project_id)
        overview = learning_overview(db(), project_id)
        overview["examples"] = overview["approved_examples"]
        overview["review_count"] = db().one("SELECT COUNT(*) n FROM v3_review_events WHERE project_id=?", [project_id])["n"]
        overview["active_snapshot_id"] = (overview.get("active") or {}).get("snapshot_id")
        return overview

    @app.post("/api/v3/projects/{project_id}/learning/examples")
    def approve_example(project_id: str, body: dict, request: Request):
        from .learning import approve_example as approve
        project(project_id)
        return call(approve, db(), project_id, body.get("review_id"), **actor(request))

    @app.get("/api/v3/projects/{project_id}/learning/snapshots")
    def snapshots(project_id: str):
        return learning(project_id)["snapshots"]

    @app.post("/api/v3/projects/{project_id}/learning/snapshots")
    def create_snapshot(project_id: str, request: Request):
        from .learning import create_snapshot
        project(project_id)
        return call(create_snapshot, db(), project_id, **actor(request))

    @app.post("/api/v3/projects/{project_id}/learning/candidates")
    def create_candidate(project_id: str, body: dict, request: Request):
        from .learning import create_candidate
        project(project_id)
        return call(create_candidate, db(), project_id, body.get("snapshot_id"), **actor(request))

    @app.post("/api/v3/projects/{project_id}/learning/candidates/{candidate_id}/evaluate")
    def evaluate_candidate(project_id: str, candidate_id: str, body: dict):
        from .learning import candidate_report
        project(project_id)
        return call(candidate_report, db(), project_id, candidate_id, body.get("baseline_run_id"), body.get("candidate_run_id"))

    @app.post("/api/v3/projects/{project_id}/learning/candidates/{candidate_id}/activate")
    def activate_candidate(project_id: str, candidate_id: str, body: dict, request: Request):
        from .learning import activate_candidate
        project(project_id)
        return call(activate_candidate, db(), project_id, candidate_id, body.get("report_id"), **actor(request), note=str(body.get("note", ""))[:2000])

    @app.post("/api/v3/projects/{project_id}/learning/rollback")
    def rollback(project_id: str, body: dict, request: Request):
        from .learning import rollback_candidate
        project(project_id)
        return call(rollback_candidate, db(), project_id, body.get("target_candidate_id"), **actor(request), note=str(body.get("note", ""))[:2000])

    @app.get("/api/v3/projects/{project_id}/trajectories")
    def trajectories(project_id: str, run_id: str | None = None, mode: str = "publication", lens_id: str | None = None, criterion_id: str | None = None, review_scope: str = "provisional", query_id: str | None = None):
        from .trajectories import monthly_trajectories
        document_ids, speech_roles = None, None
        scope_hash = None
        if query_id:
            saved = workspace().require_query(query_id)
            if saved["project_id"] != project_id or (run_id and saved["scope"]["run_id"] != run_id):
                raise HTTPException(404, "Query and run do not belong to this trajectory")
            run_id = saved["scope"]["run_id"]
            document_ids = workspace().document_ids(saved)
            speech_roles = saved["scope"].get("speech_roles") or None
            lenses = saved["scope"].get("lens_ids") or []
            criteria = saved["scope"].get("criterion_ids") or []
            lens_id = lens_id or (lenses[0] if len(lenses) == 1 else None)
            criterion_id = criterion_id or (criteria[0] if len(criteria) == 1 else None)
            if saved["scope"].get("review_status") in {"confirmed", "revised"}:
                review_scope = "reviewed"
            scope_hash = saved["scope_hash"]
        if not run_id:
            raise HTTPException(422, "Choose a run for a trajectory")
        services["require_run"](run_id, project_id)
        result = call(monthly_trajectories, db(), project_id, run_id, lens_id=lens_id, criterion_id=criterion_id, mode=mode, review_scope=review_scope, document_ids=document_ids, speech_roles=speech_roles)
        result["scope_hash"] = scope_hash
        return result

    @app.post("/api/v3/projects/{project_id}/reports")
    async def report(project_id: str, body: dict):
        from .reports import build_report
        project_data = project(project_id)
        service = workspace()
        saved = service.require_query(str(body.get("query_id", "")))
        if saved["project_id"] != project_id:
            raise HTTPException(404, "Query does not belong to this project")
        fmt, profile = body.get("format", "pdf"), body.get("profile", "research")
        if fmt not in {"pdf", "html", "xlsx", "csv", "jsonl"} or profile not in {"research", "briefing", "teaching"}:
            raise HTTPException(422, "Unsupported report format or profile")
        with db().lock:
            saved = service.require_query(saved["id"])
            clause, params, modern = service._scope(project_id, saved["scope"])
            rows = db().query("SELECT a.* FROM " + service._table(modern) + " WHERE " + clause + " ORDER BY a.id", params)
            findings = [unpack_assessment(row) for row in rows]
            dw, dp = service._document_scope(project_id, saved["scope"])
            documents = [unpack_document(row) for row in db().query("SELECT d.* FROM documents d WHERE " + " AND ".join(dw) + " ORDER BY d.id", dp)]
            metrics = saved["aggregates"]["metrics"]
            metrics = {**metrics, "by_category": [{"name": cell["criterion_id"], "count": cell["count"]} for cell in saved["aggregates"]["matrix"]], "timeline": saved["aggregates"]["timeline"], "by_author": []}
            reviews = db().query("SELECT r.* FROM v3_review_events r JOIN v3_assessments a ON a.id=r.assessment_id JOIN documents d ON d.id=a.document_id WHERE " + clause + " ORDER BY r.created_at", params) if modern else []
            snapshot = {"schema_version": "3.0", "project": project_data, "documents": documents, "findings": findings,
                        "runs": [saved["aggregates"]["run"]] if saved["aggregates"]["run"] else [], "metrics": metrics,
                        "profile": profile, "filters": saved["scope"], "query_id": saved["id"], "scope_hash": saved["scope_hash"],
                        "snapshot_version": saved["snapshot_version"], "generated_at": utcnow(), "reviews": reviews}
        identity = uuid.uuid4().hex
        output = db().root / "exports" / identity
        if not db().remote:
            from .privacy_lifecycle import track_artifact
            track_artifact(db(), project_id, f"exports/{identity}", "report_work")
            track_artifact(db(), project_id, f"objects/reports/{identity}", "report_artifact")
        output.mkdir(parents=True)
        path = await project_thread(project_id, "legacy_v3_report_render", build_report, snapshot, fmt, output)
        key, snapshot_key = f"reports/{identity}/{path.name}", f"reports/{identity}/snapshot.json"
        db().put_path(key, path)
        db().put_json(snapshot_key, snapshot)
        db().execute("INSERT INTO reports(id,project_id,format,profile,object_key,snapshot_key,created_at) VALUES(?,?,?,?,?,?,?)", [identity, project_id, fmt, profile, key, snapshot_key, utcnow()])
        return FileResponse(path, filename=path.name, headers={"X-DOXA-Scope": saved["scope_hash"], "X-DOXA-Snapshot": saved["snapshot_version"]})

    @app.post("/api/v3/projects/{project_id}/demo")
    async def demo(project_id: str, body: dict):
        project(project_id)
        collection = body.get("collection", "news")
        if collection == "institutions":
            from .showcase_v3 import ShowcaseNotFinished, import_institution_sources, read_manifest, restore_showcase_v3
            package = services["root"] / "benchmarks" / "v3-institutions" / "showcase.json"
            skipped = ""
            if services.get("use_saved_showcase", True) and package.exists():
                try:
                    read_manifest(package)
                    documents, batch = await project_thread(project_id, "institutional_import", import_institution_sources, db(), project_id, services["root"])
                    run_id = await project_thread(project_id, "recorded_import", restore_showcase_v3, db(), project_id, documents, package)
                    workspace().touch(project_id)
                    return {"documents": len(documents), "run": {"id": run_id}, "batch": batch, "collection": "institutions", "saved_results": True,
                            "notice": "Real public documents with previously computed contextual results. Exact source anchors were checked again. No inference was requested; inspect coverage and review state."}
                except ShowcaseNotFinished as error:
                    skipped = str(error) + ". "
                except (ValueError, OSError, KeyError, TypeError) as error:
                    raise HTTPException(503, f"Saved contextual showcase integrity check failed: {error}") from error
            result = await services["load_demo"](project_id)
            result["collection"] = "institutions"
            result["notice"] = skipped + "Historical institutional demonstration. " + result.get("notice", "")
            workspace().touch(project_id)
            return result
        if collection != "news":
            raise HTTPException(422, "Choose news or institutions")
        from .news import import_news
        try:
            result = await project_thread(project_id, "news_import", import_news, db(), project_id, services["root"], use_saved_showcase=services.get("use_saved_showcase", True))
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise HTTPException(503, str(error)) from error
        workspace().touch(project_id)
        return result
