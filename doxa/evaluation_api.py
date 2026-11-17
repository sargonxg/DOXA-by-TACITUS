"""HTTP hooks for independent reference review and optional semantic overlays."""
from __future__ import annotations

from fastapi import Request

from . import evaluation, semantic_overlays


def register(app, services):
    def db():
        return services["db"]()

    def call(function, *args, **kwargs):
        return services["call"](function, *args, **kwargs)

    @app.get("/api/v3/projects/{project_id}/evaluations")
    def overview(project_id: str):
        services["require_project"](project_id)
        return call(evaluation.evaluation_overview, db(), project_id)

    @app.post("/api/v3/projects/{project_id}/evaluations")
    def create(project_id: str, body: dict, request: Request):
        services["require_project"](project_id)
        services["require_run"](str(body.get("run_id", "")), project_id)
        actor = services["actor"](request)
        return call(evaluation.create_scaffold, db(), project_id, body["run_id"],
                    development_count=body.get("development_count", 100), heldout_count=body.get("heldout_count", 100),
                    seed=body.get("seed", 20261117), actor_id=actor["actor_id"])

    @app.get("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/form")
    def form(project_id: str, evaluation_id: str, split: str = "development", include_model: bool = False):
        services["require_project"](project_id)
        return call(evaluation.export_review_form, db(), project_id, evaluation_id, split=split, include_model=include_model)

    @app.post("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/reviews")
    def reviews(project_id: str, evaluation_id: str, body: dict, request: Request):
        services["require_project"](project_id)
        actor = services["actor"](request)
        return call(evaluation.import_reviews, db(), project_id, evaluation_id, body.get("rows"),
                    reviewer_id=actor["actor_id"], actor_kind=actor["actor_kind"])

    @app.post("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/adjudications")
    def adjudication(project_id: str, evaluation_id: str, body: dict, request: Request):
        services["require_project"](project_id)
        return call(evaluation.adjudicate_case, db(), project_id, evaluation_id, str(body.get("case_id", "")),
                    body.get("labels"), note=str(body.get("note", "")), **services["actor"](request))

    @app.get("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/report")
    def report(project_id: str, evaluation_id: str):
        services["require_project"](project_id)
        return call(evaluation.reference_report, db(), project_id, evaluation_id)

    @app.get("/api/v3/projects/{project_id}/assessments/{assessment_id}/overlays")
    def overlays(project_id: str, assessment_id: str):
        services["require_project"](project_id)
        return call(semantic_overlays.assessment_overlays, db(), project_id, assessment_id)

    @app.post("/api/v3/projects/{project_id}/assessments/{assessment_id}/overlays")
    def overlay(project_id: str, assessment_id: str, body: dict, request: Request):
        services["require_project"](project_id)
        return call(semantic_overlays.record_overlay, db(), project_id, assessment_id,
                    body.get("fields"), version=str(body.get("version", "0.4")), **services["actor"](request))
