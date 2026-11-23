"""Individual access for one dedicated native pilot installation.

This is deliberately not a multi-tenant identity provider or an OAuth service.
An operator provisions high-entropy credentials and explicit project grants in a
private configuration file. The file is re-read for every request, so removal,
role changes and token rotation also affect existing browser sessions.
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial, wraps
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from starlette.routing import Match

COOKIE = "doxa_pilot_session"
SESSION_SECONDS = 8 * 60 * 60
MAX_CONFIG_BYTES = 128 * 1024
PERMISSIONS = frozenset({"read", "write", "analyze", "review", "export", "admin", "identity_reveal"})
ROLE_PERMISSIONS = {
    "owner": PERMISSIONS - {"identity_reveal"},
    "admin": PERMISSIONS - {"identity_reveal"},
    "analyst": frozenset({"read", "write", "analyze", "export"}),
    "reviewer": frozenset({"read", "review", "export"}),
    "viewer": frozenset({"read"}),
}
_IDENTITY = re.compile(r"[A-Za-z0-9@._:-]{1,128}\Z")


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    project_ids: tuple[str, ...]
    all_projects: bool
    permissions: frozenset[str]
    managed: bool

    def public(self) -> dict:
        return {"id": self.id, "role": self.role, "project_ids": list(self.project_ids),
                "all_projects": self.all_projects, "permissions": sorted(self.permissions)}


LOCAL_OPERATOR = Principal("local-operator (self-declared)", "owner", (), True, PERMISSIONS, False)
_PRINCIPAL: ContextVar[Principal] = ContextVar("doxa_principal", default=LOCAL_OPERATOR)
_ACTIVITY_SCOPE: ContextVar[tuple[object, str] | None] = ContextVar("doxa_project_activity", default=None)
_SYNC_WRAP_LOCK = threading.Lock()


class WorkerAuthorizationError(PermissionError):
    """A queued operation no longer has a currently authorized executor."""


def current_principal() -> Principal:
    return _PRINCIPAL.get()


def require_project_access(project_id: str) -> None:
    principal = current_principal()
    if principal.managed and not principal.all_projects and project_id not in principal.project_ids:
        # Match the response for missing objects to avoid confirming their existence.
        raise HTTPException(404, "Resource not found")


def trusted_actor(request: Request, store) -> dict:
    principal = getattr(request.state, "doxa_principal", current_principal())
    if principal.managed:
        return {"actor_kind": "human", "actor_id": principal.id}
    identity = os.environ.get("DOXA_REVIEWER_ID")
    if store.remote and not identity:
        raise HTTPException(403, "Set a trusted DOXA_REVIEWER_ID on this private workspace before approving reviews")
    return {"actor_kind": "human", "actor_id": identity or LOCAL_OPERATOR.id}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token() -> str:
    return "doxa_" + secrets.token_urlsafe(32)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _loopback(request: Request) -> bool:
    if not request.client:
        return False
    if request.client.host == "testclient" and request.url.hostname == "testserver":
        return True  # TestClient's in-process transport cannot arrive over a network.
    return request.url.hostname in {"localhost", "127.0.0.1", "::1"} and request.client.host in {"127.0.0.1", "::1", "testclient"}


class AccessControl:
    def __init__(self, mode: str = "local", config_file: str | None = None):
        if mode not in {"local", "managed"}:
            raise ValueError("DOXA_ACCESS_MODE must be local or managed")
        self.mode = mode
        self.workspace_kind = os.environ.get("DOXA_PILOT_WORKSPACE_KIND", "organization") if mode == "managed" else "local"
        if mode == "managed" and self.workspace_kind not in {"organization", "personal"}:
            raise ValueError("DOXA_PILOT_WORKSPACE_KIND must be organization or personal")
        self.config_file = Path(config_file) if config_file else None
        if mode == "local" and self.config_file:
            raise ValueError("Remove DOXA_ACCESS_CONFIG_FILE to explicitly disable managed access")
        if self.managed:
            self.configuration()

    @classmethod
    def from_environment(cls):
        path = os.environ.get("DOXA_ACCESS_CONFIG_FILE")
        return cls(os.environ.get("DOXA_ACCESS_MODE", "managed" if path else "local"), path)

    @property
    def managed(self) -> bool:
        return self.mode == "managed"

    def ensure_native(self, store) -> None:
        if self.managed and store.remote:
            raise RuntimeError("Managed pilot access requires a dedicated native workspace; the Cloudflare shared-workspace gateway is not supported")

    def require_worker(self, principal_id: str | None, project_id: str, permission: str = "analyze") -> Principal:
        """Revalidate a durable job independently of the request's old context.

        Historical jobs have no implicit operator authority in managed mode.
        A current authorized member must explicitly resume them. This helper
        never trusts a browser token or a cached principal in the job payload.
        """
        if not self.managed:
            return LOCAL_OPERATOR
        if (not isinstance(principal_id, str) or not principal_id or not isinstance(project_id, str)
                or not project_id or not isinstance(permission, str) or permission not in PERMISSIONS):
            raise WorkerAuthorizationError("This job requires an authorized individual; resume it with a current project member")
        try:
            config = self.configuration()
        except ValueError:
            raise WorkerAuthorizationError("Access configuration is unavailable; execution is paused") from None
        principal = next((item["principal"] for item in config["users"] if item["principal"].id == principal_id), None)
        if (principal is None or permission not in principal.permissions
                or (not principal.all_projects and project_id not in principal.project_ids)):
            raise WorkerAuthorizationError("The job executor no longer has permission for this project; an authorized member must resume it")
        return principal

    def configuration(self) -> dict:
        if not self.config_file or not self.config_file.is_absolute():
            raise ValueError("Managed access requires an absolute DOXA_ACCESS_CONFIG_FILE path")
        try:
            info = self.config_file.stat()
            if not self.config_file.is_file() or info.st_size > MAX_CONFIG_BYTES:
                raise ValueError("Managed access configuration exceeds 128 KiB")
            if os.name == "posix" and info.st_mode & 0o077:
                raise ValueError("Managed access configuration must have private permissions (0600)")
            with self.config_file.open("rb") as source:
                raw = source.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ValueError("Managed access configuration exceeds 128 KiB")
            config = json.loads(raw)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Managed access configuration is unavailable or invalid") from error
        if not isinstance(config, dict) or type(config.get("version")) is not int or config.get("version") != 1:
            raise ValueError("Managed access configuration requires version 1")
        secret = config.get("session_secret")
        users = config.get("users")
        if not isinstance(secret, str) or len(secret) < 32 or not isinstance(users, list) or not 1 <= len(users) <= 256:
            raise ValueError("Managed access requires a strong session secret and one to 256 users")
        identities, digests, parsed = set(), set(), []
        for user in users:
            if not isinstance(user, dict):
                raise ValueError("Invalid managed user")
            identity, role, digest = user.get("id"), user.get("role"), user.get("token_sha256")
            projects, all_projects = user.get("project_ids", []), user.get("all_projects", False)
            extras = user.get("permissions", [])
            if (not isinstance(identity, str) or not _IDENTITY.fullmatch(identity) or identity in identities
                    or not isinstance(role, str) or role not in ROLE_PERMISSIONS or not isinstance(digest, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", digest) or digest in digests
                    or not isinstance(all_projects, bool)
                    or not isinstance(projects, list) or len(projects) > 2000
                    or any(not isinstance(p, str) or not _IDENTITY.fullmatch(p) for p in projects)
                    or not isinstance(extras, list) or any(not isinstance(item, str) or item not in PERMISSIONS for item in extras)
                    or (all_projects and role not in {"owner", "admin"})):
                raise ValueError("Invalid or duplicated managed identity, credential, role or project grant")
            identities.add(identity)
            digests.add(digest)
            parsed.append({"principal": Principal(identity, role, tuple(sorted(set(projects))), all_projects,
                                                ROLE_PERMISSIONS[role] | frozenset(extras), True),
                           "token_sha256": digest})
        if not any(item["principal"].role == "owner" and item["principal"].all_projects for item in parsed):
            raise ValueError("Managed access requires an owner with explicit all_projects access")
        return {"session_secret": secret, "users": parsed}

    def authenticate_token(self, token: str, config: dict) -> dict | None:
        if not isinstance(token, str) or not 32 <= len(token) <= 512:
            return None
        digest = token_hash(token)
        found = None
        for item in config["users"]:
            if hmac.compare_digest(digest, item["token_sha256"]):
                found = item
        return found

    @staticmethod
    def _signature(payload: str, item: dict, config: dict) -> str:
        # Binding to the current credential hash revokes sessions on token rotation.
        value = (payload + "." + item["token_sha256"]).encode()
        return _encode(hmac.new(config["session_secret"].encode(), value, hashlib.sha256).digest())

    def authenticate(self, request: Request, config: dict) -> Principal | None:
        authorization = request.headers.get("authorization", "")
        if authorization:
            match = re.fullmatch(r"Bearer (\S+)", authorization, re.IGNORECASE)
            item = self.authenticate_token(match[1], config) if match else None
            return item["principal"] if item else None
        cookie = request.cookies.get(COOKIE, "")
        if not cookie or len(cookie) > 2048:
            return None
        try:
            payload, signature = cookie.split(".")
            identity, expires = json.loads(_decode(payload))
            if not isinstance(identity, str) or type(expires) is not int or not time.time() < expires <= time.time() + SESSION_SECONDS + 60:
                return None
            item = next((value for value in config["users"] if value["principal"].id == identity), None)
            if item and hmac.compare_digest(signature, self._signature(payload, item, config)):
                return item["principal"]
        except (ValueError, TypeError, UnicodeError):
            return None
        return None

    def session(self, principal: Principal | None) -> dict:
        return {"mode": self.mode, "authenticated": principal is not None,
                "principal": principal.public() if principal else None,
                "deployment": "single_customer_native", "workspace_kind": self.workspace_kind}


# Every API route has an explicit permission. New routes are unavailable in
# managed mode until reviewed; a general /projects/* wildcard would be unsafe.
READ = {
    "/api/system/recorded-example", "/api/system/recorded-example/artifacts/{format}",
    "/api/v3/projects/{project_id}/alert-review-campaigns",
    "/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}",
    "/api/system/readiness", "/api/system/starter-pack", "/api/v3/method-cards",
    "/api/v3/method-cards/{lens_id}/{criterion_id}", "/api/v3/study-protocol",
    "/api/v3/projects/{project_id}/activity", "/api/v3/projects/{project_id}/jobs", "/api/v3/jobs/{job_id}",
    "/api/v3/projects/{project_id}/comparisons", "/api/v3/projects/{project_id}/comparisons/{comparison_id}",
    "/api/v3/projects/{project_id}/scorecards", "/api/v3/projects/{project_id}/scorecards/{scorecard_id}",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/readiness",
    "/api/pilot/projects/{project_id}/quotes", "/api/pilot/projects/{project_id}/usage",
    "/api/lenses", "/api/projects", "/api/projects/{project_id}/documents",
    "/api/documents/{document_id}", "/api/documents/{document_id}/extraction",
    "/api/projects/{project_id}/runs", "/api/runs/{run_id}",
    "/api/projects/{project_id}/findings", "/api/findings/{finding_id}/reviews",
    "/api/projects/{project_id}/batches", "/api/projects/{project_id}/batches/compare",
    "/api/projects/{project_id}/dashboard", "/api/projects/{project_id}/reports",
    "/api/v3/queries/{query_id}", "/api/v3/queries/{query_id}/{kind}",
    "/api/v3/documents/{document_id}/context", "/api/v3/models",
    "/api/v3/projects/{project_id}/runs", "/api/v3/runs/{run_id}/status",
    "/api/v3/projects/{project_id}/verification/{verifier_id}",
    "/api/v3/projects/{project_id}/learning", "/api/v3/projects/{project_id}/learning/snapshots",
    "/api/v3/projects/{project_id}/trajectories", "/api/v3/projects/{project_id}/review-queue",
    "/api/v3/projects/{project_id}/unit-context", "/api/v3/projects/{project_id}/casebooks",
    "/api/v3/casebooks/{identity}", "/api/v3/projects/{project_id}/evaluations",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/form",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/report",
    "/api/v3/projects/{project_id}/assessments/{assessment_id}/overlays",
    "/api/v3/projects/{project_id}/report-jobs", "/api/v3/report-jobs/{job_id}",
    "/api/v3/projects/{project_id}/imports/{import_id}",
    "/api/pilot/profiles", "/api/pilot/projects/{project_id}/brief",
    "/api/pilot/projects/{project_id}/privacy-policy",
    "/api/pilot/projects/{project_id}/privacy/transforms/{transform_id}",
    "/api/pilot/projects/{project_id}/reports",
}
EXPORT = {
    "/api/documents/{document_id}/source", "/api/reports/{report_id}/download",
    "/api/v3/report-jobs/{job_id}/artifacts/{format}",
    "/api/pilot/projects/{project_id}/reports/{report_id}/artifacts/{format}",
}
POST_READ = {
    # Each activity endpoint checks the kind's current write/analyze/export
    # permission inside its manager; the shared envelope alone grants no work.
    "/api/v3/projects/{project_id}/jobs", "/api/v3/jobs/{job_id}/cancel", "/api/v3/jobs/{job_id}/restart",
    "/api/v3/projects/{project_id}/query", "/api/v3/projects/{project_id}/semantic-search",
    "/api/v3/projects/{project_id}/preflight", "/api/pilot/projects/{project_id}/estimate",
    "/api/pilot/projects/{project_id}/privacy-preview",
}
WRITE = {
    "/api/v3/projects/{project_id}/jobs/import-archive",
    "/api/documents/{document_id}", "/api/documents/{document_id}/re-extract",
    "/api/projects/{project_id}/metadata/import", "/api/projects/{project_id}/upload",
    "/api/projects/{project_id}/uploads/init", "/api/uploads/{upload_id}/parts/{part_number}",
    "/api/uploads/{upload_id}/complete", "/api/uploads/{upload_id}",
    "/api/projects/{project_id}/demo", "/api/v3/projects/{project_id}/demo",
    "/api/v3/projects/{project_id}/imports", "/api/v3/projects/{project_id}/imports/{import_id}/archive",
    "/api/pilot/projects/{project_id}/brief",
    "/api/pilot/projects/{project_id}/drafts",
}
ANALYZE = {
    "/api/v3/projects/{project_id}/scorecards",
    "/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}/snapshots",
    "/api/v3/projects/{project_id}/comparisons",
    "/api/projects/{project_id}/runs", "/api/runs/{run_id}/cancel", "/api/runs/{run_id}/pause", "/api/runs/{run_id}/resume",
    "/api/v3/projects/{project_id}/runs", "/api/v3/projects/{project_id}/verification",
    "/api/v3/projects/{project_id}/semantic-index", "/api/v3/projects/{project_id}/rubric-score",
    "/api/pilot/projects/{project_id}/signals", "/api/pilot/projects/{project_id}/privacy/transforms",
}
REVIEW = {
    "/api/v3/projects/{project_id}/alert-review-campaigns",
    "/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}/reviews",
    "/api/v3/projects/{project_id}/evaluation-panels",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/preregistration",
    "/api/findings/{finding_id}/review", "/api/v3/findings/{finding_id}/review",
    "/api/v3/projects/{project_id}/casebooks", "/api/v3/casebooks/{identity}",
    "/api/v3/casebooks/{identity}/cases", "/api/v3/casebooks/{identity}/cases/{case_id}",
    "/api/v3/casebooks/{identity}/order", "/api/v3/projects/{project_id}/evaluations",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/reviews",
    "/api/v3/projects/{project_id}/evaluations/{evaluation_id}/adjudications",
    "/api/v3/projects/{project_id}/assessments/{assessment_id}/overlays",
}
REPORT_WRITE = {
    "/api/projects/{project_id}/reports", "/api/v3/projects/{project_id}/reports",
    "/api/v3/projects/{project_id}/report-jobs", "/api/v3/report-jobs/{job_id}/cancel",
    "/api/v3/report-jobs/{job_id}/restart",
    "/api/pilot/projects/{project_id}/reports",
}
ADMIN = {
    "/api/pilot/projects/{project_id}/quotes/{quote_id}/revoke",
    "/api/pilot/projects/{project_id}/quotes", "/api/pilot/projects/{project_id}/quotes/{quote_id}/accept",
    "/api/settings", "/api/providers/health", "/api/v3/models/health",
    "/api/v3/projects/{project_id}/learning/examples", "/api/v3/projects/{project_id}/learning/snapshots",
    "/api/v3/projects/{project_id}/learning/candidates",
    "/api/v3/projects/{project_id}/learning/candidates/{candidate_id}/evaluate",
    "/api/v3/projects/{project_id}/learning/candidates/{candidate_id}/activate",
    "/api/v3/projects/{project_id}/learning/rollback",
    "/api/pilot/projects/{project_id}/privacy-policy",
    "/api/pilot/projects/{project_id}/erase-jobs", "/api/pilot/erase-jobs/{erase_job_id}",
    "/api/pilot/erase-jobs/{erase_job_id}/resume",
}


def permission_for(path: str, method: str) -> str | None:
    if path == "/api/projects" and method == "POST":
        return "admin"
    if path == "/api/settings" and method in {"GET", "HEAD", "PUT"}:
        return "admin"
    if path == "/api/documents/{document_id}" and method == "DELETE":
        return "admin"
    if path == "/api/pilot/projects/{project_id}/privacy-policy" and method == "PUT":
        return "admin"
    if path in {"/api/pilot/projects/{project_id}/erase-jobs", "/api/pilot/erase-jobs/{erase_job_id}"} and method in {"GET", "HEAD"}:
        return "admin"
    if path in ADMIN - {"/api/settings", "/api/pilot/projects/{project_id}/privacy-policy", "/api/pilot/erase-jobs/{erase_job_id}"} and method == "POST":
        return "admin"
    if method in {"GET", "HEAD"}:
        if path in EXPORT:
            return "export"
        return "read" if path in READ else None
    if path == "/api/pilot/projects/{project_id}/privacy/transforms/{transform_id}/reveal" and method == "POST":
        return "identity_reveal"
    if method == "POST" and path in POST_READ:
        return "read"
    if path in WRITE:
        expected = {
            "/api/documents/{document_id}": {"PATCH"},
            "/api/uploads/{upload_id}/parts/{part_number}": {"PUT"},
            "/api/uploads/{upload_id}": {"DELETE"},
            "/api/pilot/projects/{project_id}/brief": {"PUT"},
        }.get(path, {"POST"})
        return "write" if method in expected else None
    if path in ANALYZE and method == "POST":
        return "analyze"
    if path in REVIEW:
        expected = {
            "/api/findings/{finding_id}/review": {"PATCH"},
            "/api/v3/findings/{finding_id}/review": {"PATCH"},
            "/api/v3/casebooks/{identity}": {"PATCH"},
            "/api/v3/casebooks/{identity}/cases/{case_id}": {"PATCH", "DELETE"},
            "/api/v3/casebooks/{identity}/order": {"PUT"},
        }.get(path, {"POST"})
        return "review" if method in expected else None
    if path in REPORT_WRITE and method == "POST":
        return "export"
    return None


def resolve_project(store, path: str, params: dict, *, strict: bool = True) -> str | None:
    project_id = params.get("project_id")
    resources = {
        "campaign_id": ("v06_alert_campaigns", "id"),
        "comparison_id": ("matched_comparisons", "id"), "quote_id": ("pilot_quotes", "id"),
        "scorecard_id": ("v3_scorecards", "id"),
        "document_id": ("documents", "id"), "run_id": ("runs", "id"),
        "query_id": ("v3_query_snapshots", "id"), "job_id": ("report_jobs", "id"),
        "upload_id": ("upload_sessions", "id"), "report_id": ("reports", "id"),
        "import_id": ("v3_imports", "id"), "evaluation_id": ("v4_evaluations", "id"),
        "assessment_id": ("v3_assessments", "id"), "transform_id": ("pilot_transforms", "id"),
        "erase_job_id": ("privacy_erase_jobs", "id"),
        "candidate_id": ("v3_learning_candidates", "id"), "verifier_id": ("runs", "id"),
    }
    if "identity" in params and "/casebooks/" in path:
        resources["identity"] = ("v4_casebooks", "id")
    if "job_id" in params and path.startswith("/api/v3/jobs/"):
        resources["job_id"] = ("activity_jobs", "id")
    if "report_id" in params and path.startswith("/api/pilot/"):
        resources["report_id"] = ("pilot_reports", "id")
    if "finding_id" in params:
        table = "v3_assessments" if path.startswith("/api/v3/") else "findings"
        resources["finding_id"] = (table, "id")
    for key, (table, column) in resources.items():
        if key not in params:
            continue
        # SQL identifiers are fixed above; only values originate in the request.
        if not store.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table]):
            if not strict:
                return project_id
            raise HTTPException(404, "Resource not found")
        row = store.one(f"SELECT project_id FROM {table} WHERE {column}=?", [params[key]])
        if not row and key == "finding_id" and path.startswith("/api/v3/"):
            row = store.one("SELECT project_id FROM findings WHERE id=?", [params[key]])
        if not row or (project_id is not None and row["project_id"] != project_id):
            if not strict:
                return project_id
            raise HTTPException(404, "Resource not found")
        project_id = row["project_id"]
    return project_id


def _matched_route(application, scope) -> tuple[str | None, dict]:
    for route in application.routes:
        match, child = route.matches(scope)
        if match == Match.FULL:
            _protect_sync_endpoint(route)
            return getattr(route, "path", None), child.get("path_params", {})
    return None, {}


def _erase_route(path: str) -> bool:
    return path.endswith("/erase-jobs") or "/erase-jobs/" in path


def _protect_sync_endpoint(route) -> None:
    """A canceled ASGI awaiter does not necessarily stop its endpoint thread.

    Acquire a second lease *inside* that worker. Its copied ContextVar outlives
    the canceled request, so erasure waits for the real handler to finish. A
    queued worker beginning after the barrier is denied before touching data.
    """
    if not isinstance(route, APIRoute) or _erase_route(route.path):
        return
    with _SYNC_WRAP_LOCK:
        if getattr(route, "_doxa_thread_activity", False):
            return
        original = route.dependant.call
        if (original is None or inspect.iscoroutinefunction(original)
                or inspect.iscoroutinefunction(getattr(original, "__call__", None))):
            return

        @wraps(original)
        def guarded(*args, **kwargs):
            scope = _ACTIVITY_SCOPE.get()
            if scope is None:
                return original(*args, **kwargs)
            from .privacy_lifecycle import ProjectErasingError, run_activity
            store, project_id = scope
            try:
                return run_activity(store, project_id, "api_thread", partial(original, *args, **kwargs))
            except ProjectErasingError:
                raise HTTPException(409, "Project erasure is in progress") from None

        route.dependant.call = guarded
        route._doxa_thread_activity = True


class AccessMiddleware:
    """Authorize before dispatch and hold privacy leases through streamed bodies."""
    def __init__(self, app, *, application, control: AccessControl):
        self.app, self.application, self.control = app, application, control

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path = request.url.path
        original_send = send

        async def private_send(message):
            if message["type"] == "http.response.start" and path.startswith("/api/"):
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() != b"cache-control"]
                headers.append((b"cache-control", b"private, no-store"))
                present = {name.lower() for name, _value in headers}
                for name, value in ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"same-origin")):
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await original_send(message)

        send = private_send
        route, params = _matched_route(self.application, scope)
        principal = LOCAL_OPERATOR
        project_id, activity = None, None
        context, project_context = None, None
        store = getattr(self.application.state, "store", None)
        try:
            if self.control.managed:
                try:
                    config = self.control.configuration()
                except ValueError:
                    await JSONResponse({"detail": "Access configuration unavailable; contact the operator"}, status_code=503)(scope, receive, send)
                    return
                principal = self.control.authenticate(request, config)
                if path.startswith("/api/") and request.url.scheme != "https" and not _loopback(request):
                    raise HTTPException(403, "Managed pilot access requires HTTPS or a loopback connection")
                if route == "/api/access/session" or path == "/api/health" or not path.startswith(("/api", "/internal")):
                    pass
                elif path.startswith("/internal/"):
                    expected = os.environ.get("DOXA_INTERNAL_TOKEN", "")
                    if len(expected) < 32 or not hmac.compare_digest(request.headers.get("x-doxa-service-token", ""), expected):
                        raise HTTPException(401, "Internal service authentication required")
                else:
                    if principal is None:
                        raise HTTPException(401, "Individual pilot authentication required")
                    permission = permission_for(route or "", request.method)
                    if permission is None or permission not in principal.permissions:
                        raise HTTPException(403, "This operation is not permitted")
                    global_admin = route in {"/api/settings", "/api/providers/health", "/api/v3/models/health"} or (route == "/api/projects" and request.method == "POST")
                    if global_admin and not principal.all_projects:
                        raise HTTPException(403, "Installation-wide administration required")
                    project_id = resolve_project(store, route or "", params)
                    if project_id is not None and not principal.all_projects and project_id not in principal.project_ids:
                        raise HTTPException(404, "Resource not found")
            # Local requests retain the same lifecycle barrier even without auth.
            elif store and not store.remote and route and path.startswith("/api/"):
                project_id = resolve_project(store, route, params, strict=False)
            scope.setdefault("state", {})["doxa_principal"] = principal
            scope["state"]["doxa_project_id"] = project_id
            context = _PRINCIPAL.set(principal or LOCAL_OPERATOR)
            if project_id is not None and not _erase_route(route or ""):
                if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
                    raise HTTPException(404, "Resource not found")
                from .privacy_lifecycle import begin_activity
                activity = begin_activity(store, project_id, kind="api")
                project_context = _ACTIVITY_SCOPE.set((store, project_id))
                if permission_for(route or "", request.method) == "export":
                    from .privacy_lifecycle import require_exports
                    try:
                        require_exports(store, project_id)
                    except ValueError:
                        raise HTTPException(403, "Exports are disabled by this project's privacy policy") from None
            await self.app(scope, receive, send)
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status_code)(scope, receive, send)
        except ValueError as error:
            from .privacy_lifecycle import ProjectErasingError
            if not isinstance(error, ProjectErasingError):
                raise
            await JSONResponse({"detail": "Project erasure is in progress"}, status_code=409)(scope, receive, send)
        finally:
            if activity:
                from .privacy_lifecycle import end_activity
                end_activity(store, activity)
            if context is not None:
                _PRINCIPAL.reset(context)
            if project_context is not None:
                _ACTIVITY_SCOPE.reset(project_context)


def install(app, control: AccessControl) -> None:
    app.state.access_control = control

    @app.get("/api/access/session")
    def session(request: Request):
        principal = getattr(request.state, "doxa_principal", None)
        return JSONResponse(control.session(principal), headers={"Cache-Control": "no-store"})

    @app.post("/api/access/session")
    async def login(request: Request):
        if not control.managed:
            return JSONResponse(control.session(LOCAL_OPERATOR), headers={"Cache-Control": "no-store"})
        # A credential is one small string; never buffer an unbounded login body.
        raw = bytearray()
        async for part in request.stream():
            if len(raw) + len(part) > 2048:
                raise HTTPException(413, "Credential request is too large")
            raw.extend(part)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Supply a credential") from None
        config = control.configuration()
        item = control.authenticate_token(body.get("token", ""), config) if isinstance(body, dict) else None
        if item is None:
            raise HTTPException(401, "Invalid pilot credential")
        payload = _encode(json.dumps([item["principal"].id, int(time.time()) + SESSION_SECONDS], separators=(",", ":")).encode())
        cookie = payload + "." + control._signature(payload, item, config)
        response = JSONResponse(control.session(item["principal"]), headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, cookie, max_age=SESSION_SECONDS, httponly=True,
                            secure=not (request.url.scheme == "http" and _loopback(request)), samesite="strict", path="/")
        return response

    @app.delete("/api/access/session")
    def logout():
        response = JSONResponse(control.session(None if control.managed else LOCAL_OPERATOR), headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    app.add_middleware(AccessMiddleware, application=app, control=control)
