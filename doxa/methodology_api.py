"""Read-only academic contracts plus native preregistered review plans."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .method_cards import freeze_method_cards, get_method_card, study_protocol


def register_methodology_api(app, store_factory):
    from .alert_evaluation import register_alert_review_api
    register_alert_review_api(app, store_factory)
    @app.get("/api/v3/method-cards")
    def method_cards():
        return freeze_method_cards()

    @app.get("/api/v3/method-cards/{lens_id}/{criterion_id}")
    def method_card(lens_id: str, criterion_id: str):
        try:
            return get_method_card(lens_id, criterion_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/v3/study-protocol")
    def independent_study_protocol():
        return study_protocol()

    def scoped_store(project_id):
        from .access import require_project_access
        require_project_access(project_id)
        store = store_factory()
        if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
            raise HTTPException(404, "Project not found")
        return store

    @app.post("/api/v3/projects/{project_id}/evaluation-panels", status_code=201)
    def reference_panel(project_id: str, body: dict, request: Request):
        from .access import trusted_actor
        from .reference_panels import import_reference_panel
        store = scoped_store(project_id)
        if store.remote:
            raise HTTPException(501, "Reference panel import requires native pilot storage")
        if set(body) != {"run_id", "manifest"} or not isinstance(body["run_id"], str):
            raise HTTPException(422, "Supply only run_id and an external collection manifest; labels are entered separately")
        try:
            return import_reference_panel(store, project_id, body["run_id"], body["manifest"], actor_id=trusted_actor(request, store)["actor_id"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/preregistration", status_code=201)
    def preregistration(project_id: str, evaluation_id: str, body: dict, request: Request):
        from .access import trusted_actor
        from .evaluation import preregister_protocol
        store = scoped_store(project_id)
        if store.remote:
            raise HTTPException(501, "This preregistration workflow requires native pilot storage")
        # Identity is assigned by authenticated server context. The operator's
        # declared review identity remains supported on an ordinary local app.
        actor_id = trusted_actor(request, store)["actor_id"]
        try:
            return preregister_protocol(store, project_id, evaluation_id, body, actor_id=actor_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/v3/projects/{project_id}/evaluations/{evaluation_id}/readiness")
    def evidence_readiness(project_id: str, evaluation_id: str):
        from .evaluation import reference_report
        from .service_profiles import readiness_from_evaluation
        try:
            return readiness_from_evaluation(reference_report(scoped_store(project_id), project_id, evaluation_id))
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
