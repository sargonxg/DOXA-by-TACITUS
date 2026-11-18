"""Fixed-scope guided-pilot contracts and content-free delivery accounting.

This is not a payment processor or a subscription service. Quotes originate from
an operator authorized by server configuration; acceptance never proves payment.
Provider costs, customer fixed prices and delivery quantities stay distinct.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .access import current_principal, require_project_access
from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pilot_quotes (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, accepted_at TEXT, accepted_by TEXT,
 UNIQUE(project_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS pilot_quotes_project ON pilot_quotes(project_id,created_at);
CREATE TABLE IF NOT EXISTS pilot_usage_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, quote_id TEXT NOT NULL,
 event_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, category TEXT NOT NULL,
 quantity INTEGER NOT NULL, reserved_microusd INTEGER NOT NULL,
 actual_microusd INTEGER, state TEXT NOT NULL, reference_id TEXT,
 created_at TEXT NOT NULL, settled_at TEXT,
 UNIQUE(project_id,event_key)
);
CREATE INDEX IF NOT EXISTS pilot_usage_quote ON pilot_usage_events(project_id,quote_id);
CREATE TABLE IF NOT EXISTS pilot_scope_sources (
 project_id TEXT NOT NULL, quote_id TEXT NOT NULL, document_id TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, first_admitted_at TEXT NOT NULL,
 PRIMARY KEY(project_id,quote_id,document_id,source_sha256)
);
"""
_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_CATEGORIES = frozenset({"inference", "ocr", "compute", "storage", "export", "review", "support"})
_SCOPE_LIMITS = {"documents": 100_000, "characters": 1_000_000_000,
                 "criteria": 1000, "ocr_pages": 100_000}


class CommerceError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def ensure_schema(store):
    if store.remote:
        raise CommerceError("Pilot contracts require a dedicated native installation", 501)
    store.batch([{"sql": part.strip(), "params": []} for part in SCHEMA_SQL.split(";") if part.strip()])


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _key(value, name="Idempotency key"):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise CommerceError(f"{name} must contain 1–160 letters, digits, dots, colons, underscores or hyphens")
    return value


def _integer(value, name, *, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CommerceError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _project(store, project_id):
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise CommerceError("Resource not found", 404)


def _active_project(store, project_id):
    from .privacy_lifecycle import ProjectErasingError, require_active_project
    try:
        require_active_project(store, project_id)
    except ProjectErasingError:
        raise CommerceError("Project erasure prevents further contract or delivery mutations", 409) from None
    _project(store, project_id)


def _public(row):
    payload = json.loads(row["payload_json"])
    return {**payload, "id": row["id"], "project_id": row["project_id"], "status": row["status"],
            "created_at": row["created_at"], "accepted_at": row["accepted_at"],
            "accepted_by": row["accepted_by"], "payment_status": "not_recorded",
            "notice": "Fixed-scope service agreement; acceptance does not record payment or establish analytical accuracy."}


def get_quote(store, project_id, quote_id):
    ensure_schema(store)
    row = store.one("SELECT * FROM pilot_quotes WHERE id=? AND project_id=?", [quote_id, project_id])
    if not row:
        raise CommerceError("Resource not found", 404)
    return _public(row)


def create_quote(store, project_id, body, *, operator_id):
    """Server/operator entry point. HTTP callers cannot choose operator_id."""
    ensure_schema(store)
    _project(store, project_id)
    if not isinstance(body, dict):
        raise CommerceError("Quote must be an object")
    allowed = {"idempotency_key", "profile_id", "fixed_price_cents", "processing_cap_microusd", "scope", "valid_days"}
    if set(body) - allowed:
        raise CommerceError("Quote contains unsupported fields; client estimates and actor identities are not accepted")
    key = _key(body.get("idempotency_key"))
    profile = body.get("profile_id")
    if profile not in {"company", "public", "personal"}:
        raise CommerceError("Choose a Company, Public or Personal service profile")
    price = _integer(body.get("fixed_price_cents"), "Fixed USD price in cents", maximum=100_000_000, minimum=1)
    cap = _integer(body.get("processing_cap_microusd"), "Internal processing cap in micro-USD", maximum=10**12, minimum=1)
    days = _integer(body.get("valid_days", 14), "Quote validity in days", maximum=90, minimum=1)
    supplied = body.get("scope")
    if not isinstance(supplied, dict) or set(supplied) != set(_SCOPE_LIMITS):
        raise CommerceError("Scope must explicitly set documents, characters, criteria and ocr_pages")
    scope = {name: _integer(supplied[name], name, maximum=maximum,
                             minimum=0 if name == "ocr_pages" else 1)
             for name, maximum in _SCOPE_LIMITS.items()}
    request = {"profile_id": profile, "fixed_price_cents": price, "processing_cap_microusd": cap,
               "scope": scope, "valid_days": days}
    request_hash = _digest(request)
    with store.transaction(write=True):
        _active_project(store, project_id)
        existing = store.one("SELECT * FROM pilot_quotes WHERE project_id=? AND idempotency_key=?", [project_id, key])
        if existing:
            if existing["request_sha256"] != request_hash:
                raise CommerceError("Idempotency key already belongs to a different quote", 409)
            return {**_public(existing), "duplicate": True}
        created = utcnow()
        expires = (datetime.fromisoformat(created) + timedelta(days=days)).isoformat()
        payload = {"contract_version": "doxa-guided-quote-1", "profile_id": profile, "currency": "USD",
                   "fixed_price_cents": price, "processing_cap_microusd": cap, "scope": scope,
                   "scope_sha256": _digest(scope), "operator_id": operator_id, "expires_at": expires,
                   "billing_mode": "manual_invoice", "expert_review_required": True,
                   "scope_basis": "per_operation_limits", "processing_cap_basis": "aggregate_across_quote",
                   "provider_costs_are_customer_price": False}
        identity = uuid.uuid4().hex
        store.execute("INSERT INTO pilot_quotes(id,project_id,idempotency_key,request_sha256,status,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                      [identity, project_id, key, request_hash, "offered", _canonical(payload), created])
    return {**get_quote(store, project_id, identity), "duplicate": False}


def accept_quote(store, project_id, quote_id, *, actor_id, scope_sha256):
    ensure_schema(store)
    with store.transaction(write=True):
        _active_project(store, project_id)
        quote = get_quote(store, project_id, quote_id)
        if not isinstance(scope_sha256, str) or scope_sha256 != quote["scope_sha256"]:
            raise CommerceError("Accept the exact offered scope hash", 409)
        if quote["status"] == "accepted":
            return {**quote, "duplicate": True}
        if quote["status"] != "offered":
            raise CommerceError("This quote cannot be accepted", 409)
        if datetime.fromisoformat(quote["expires_at"]) <= datetime.now(timezone.utc):
            raise CommerceError("Quote has expired; request a new operator-issued quote", 409)
        store.execute("UPDATE pilot_quotes SET status='accepted',accepted_at=?,accepted_by=? WHERE id=? AND project_id=? AND status='offered'",
                      [utcnow(), actor_id, quote_id, project_id])
    return {**get_quote(store, project_id, quote_id), "duplicate": False}


def revoke_quote(store, project_id, quote_id, *, actor_id, reason_code):
    if reason_code not in {"customer_cancelled", "scope_changed", "security_review"}:
        raise CommerceError("Choose an explicit contract revocation reason code")
    ensure_schema(store)
    with store.transaction(write=True):
        _active_project(store, project_id)
        quote = get_quote(store, project_id, quote_id)
        if quote["status"] == "revoked":
            return {**quote, "duplicate": True}
        row = store.one("SELECT payload_json FROM pilot_quotes WHERE id=? AND project_id=?", [quote_id, project_id])
        payload = {**json.loads(row["payload_json"]), "revoked_at": utcnow(),
                   "revoked_by": actor_id, "revocation_reason": reason_code}
        store.execute("UPDATE pilot_quotes SET status='revoked',payload_json=? WHERE id=? AND project_id=?",
                      [_canonical(payload), quote_id, project_id])
    return {**get_quote(store, project_id, quote_id), "duplicate": False}


def quote_required():
    return os.environ.get("DOXA_REQUIRE_ACCEPTED_QUOTE", "").lower() in {"1", "true", "yes"}


def usd_to_microusd(amount):
    """Round a nonnegative USD amount upward, never under-reserving a fraction."""
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0:
            raise ValueError
        return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise CommerceError("Cost must be a finite nonnegative USD amount") from None


def apply_server_pricing(config, *, submitted_fields=None):
    """Bind quoted work to operator-owned rates before health or inference.

    Local unquoted research retains its existing illustrative-rate contract.
    Worker calls pass no submitted_fields: frozen rates must still match current
    server configuration; a changed price requires a newly authorized run.
    """
    selected = dict(config)
    if not (selected.get("quote_id") or quote_required()):
        return selected
    from .providers import CLOUDFLARE_MODEL, OLLAMA_MODEL, configured_endpoint
    provider = str(selected.get("provider") or "lexical").lower()
    if provider in {"lexical", "lexical-baseline"}:
        return {**selected, "commercial_pricing": {"kind": "no_provider_inference", "customer_price": False}}
    try:
        endpoint = configured_endpoint(selected)
    except ValueError:
        raise CommerceError("Unsupported or invalid quoted inference destination", 422) from None
    parsed = urlsplit(endpoint)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost", "ollama", "host.docker.internal"}
    local = provider == "ollama" and loopback
    if provider in {"openai", "openai-compatible", "vllm"}:
        local = loopback and os.environ.get("DOXA_OPENAI_MODE") == "local"
        selected["inference_mode"] = "local" if local else "remote"
    elif provider in {"cloudflare", "runpod"}:
        selected["inference_mode"] = "remote"
    if local:
        if provider == "ollama" and not selected.get("model"):
            selected["model"] = OLLAMA_MODEL
        return {**selected, "input_price_per_million": 0.0, "output_price_per_million": 0.0,
                "commercial_pricing": {"kind": "local_compute_excluded", "customer_price": False}}
    if provider == "ollama":
        raise CommerceError("Quoted remote Ollama requires a metered compatible gateway; choose an explicitly priced supported provider", 409)
    if parsed.scheme != "https":
        raise CommerceError("Quoted remote inference requires an HTTPS provider endpoint", 409)
    price_fields = {"input_price_per_million", "output_price_per_million"}
    if submitted_fields is not None and any(key in submitted_fields and selected.get(key) is not None for key in price_fields):
        raise CommerceError("Hosted pilot token prices are server-configured; omit browser-supplied token rates", 422)
    model = str(selected.get("model") or (CLOUDFLARE_MODEL if provider == "cloudflare" else ""))
    if not model:
        raise CommerceError("Quoted paid inference requires an explicitly priced model", 409)
    path = Path(os.environ.get("DOXA_COMMERCE_RATES_FILE", ""))
    try:
        if not path.is_absolute() or path != path.resolve():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 131072
                    or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
                raise ValueError
            raw = source.read(131073)
        if len(raw) > 131072:
            raise ValueError
        rates = json.loads(raw)
        if (not isinstance(rates, dict) or set(rates) != {"version", "revision", "rates"}
                or type(rates["version"]) is not int or rates["version"] != 1
                or not isinstance(rates["revision"], str) or not 1 <= len(rates["revision"]) <= 128
                or not isinstance(rates["rates"], list) or len(rates["rates"]) > 128):
            raise ValueError
        entries, identities = [], set()
        for row in rates["rates"]:
            if not isinstance(row, dict) or set(row) != {"provider", "model", "endpoint", *price_fields}:
                raise ValueError
            identity = (row["provider"], row["model"], row["endpoint"])
            if any(not isinstance(value, str) or not value or len(value) > 2048 for value in identity) or identity in identities:
                raise ValueError
            identities.add(identity)
            for field in price_fields:
                value = row[field]
                if type(value) not in {int, float} or not Decimal(str(value)).is_finite() or not 0 < value <= 1_000_000:
                    raise ValueError
            entries.append(row)
        matching = [row for row in entries if (row["provider"], row["model"], row["endpoint"]) == (provider, model, endpoint)]
        if len(matching) != 1:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        raise CommerceError("No valid private server pricing is configured for this exact provider, model and endpoint", 409) from None
    row = matching[0]
    if submitted_fields is None and any(selected.get(field) != row[field] for field in price_fields):
        raise CommerceError("Server token prices changed or the run has no trusted pricing; authorize a new run before paid execution", 409)
    selected.update({"model": model, **{field: row[field] for field in price_fields},
                     "commercial_pricing": {"kind": "server_token_rates", "revision": rates["revision"],
                         "config_sha256": hashlib.sha256(raw).hexdigest(), "provider": provider,
                         "model": model, "endpoint": endpoint, "customer_price": False}})
    return selected


def admit_quote_work(store, project_id, quote_id, *, profile_id, documents, characters, criteria, ocr_pages=0,
                     source_ids=None):
    """Authorize declared scope. Call again at execution before paid reservations.

    This does not reserve costs; reserve_usage performs the atomic cap check.
    Unquoted local research remains available unless the operator requires quotes.
    """
    if quote_id is None and not quote_required():
        return None
    if not isinstance(quote_id, str) or not quote_id:
        raise CommerceError("This installation requires an accepted operator-issued pilot quote", 409)
    quote = get_quote(store, project_id, quote_id)
    if quote["status"] != "accepted":
        raise CommerceError("An accepted quote with remaining processing authority is required", 409)
    if quote["profile_id"] != profile_id:
        raise CommerceError("Quote belongs to a different service profile", 409)
    for name, value in {"documents": documents, "characters": characters, "criteria": criteria, "ocr_pages": ocr_pages}.items():
        _integer(value, name, maximum=_SCOPE_LIMITS[name])
        if value > quote["scope"][name]:
            raise CommerceError(f"Selected {name} exceed the accepted pilot scope", 409)
    if source_ids is not None:
        if (not isinstance(source_ids, list) or len(source_ids) != documents
                or any(not isinstance(value, str) for value in source_ids) or len(set(source_ids)) != len(source_ids)):
            raise CommerceError("Source scope must list every distinct admitted source identity")
        with store.transaction(write=True):
            _active_project(store, project_id)
            if get_quote(store, project_id, quote_id)["status"] != "accepted":
                raise CommerceError("Quote is no longer accepted", 409)
            for offset in range(0, len(source_ids), 200):
                part = source_ids[offset:offset + 200]
                rows = store.query("SELECT id,sha256 FROM documents WHERE project_id=? AND id IN (" + ','.join('?' for _ in part) + ")", [project_id, *part])
                if len(rows) != len(part):
                    raise CommerceError("Source scope contains an unavailable source", 404)
                store.batch([{"sql": "INSERT INTO pilot_scope_sources(project_id,quote_id,document_id,source_sha256,first_admitted_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id,quote_id,document_id,source_sha256) DO NOTHING",
                              "params": [project_id, quote_id, row["id"], row["sha256"], utcnow()]} for row in rows])
    return quote


def usage_summary(store, project_id, quote_id=None):
    ensure_schema(store)
    params = [project_id]
    condition = "project_id=?"
    if quote_id is not None:
        get_quote(store, project_id, quote_id)
        condition += " AND quote_id=?"
        params.append(quote_id)
    rows = store.query("SELECT category,state,COUNT(*) event_count,SUM(quantity) quantity,SUM(CASE WHEN state='reserved' THEN reserved_microusd ELSE 0 END) reserved_microusd,SUM(COALESCE(actual_microusd,0)) actual_microusd FROM pilot_usage_events WHERE " + condition + " GROUP BY category,state ORDER BY category,state", params)
    source_count = store.one("SELECT COUNT(*) count FROM pilot_scope_sources WHERE " + condition, params)["count"]
    return {"project_id": project_id, "quote_id": quote_id, "categories": rows,
            "admitted_unique_source_versions": source_count, "source_count_basis": "registered_admissions_only",
            "reserved_microusd": sum(row["reserved_microusd"] for row in rows),
            "actual_microusd": sum(row["actual_microusd"] for row in rows),
            "currency": "USD", "provider_invoice": False, "customer_charge": False,
            "notice": "Internal delivery accounting; fixed customer prices and external invoices are separate. Scope limits apply per operation; processing cost caps accumulate across each quote. Source-version counts cover registered admissions, not a cumulative document allowance or completed analysis. Quantities have category-specific units and are not summed across categories."}


def reserve_usage(store, project_id, quote_id, *, event_key, category, reserved_microusd, quantity=1, reference_id=None):
    """Trusted-server reservation. Duplicate delivery never reserves twice."""
    ensure_schema(store)
    _key(event_key, "Usage event key")
    if category not in _CATEGORIES:
        raise CommerceError("Unsupported delivery cost category")
    _integer(reserved_microusd, "Reserved cost", maximum=10**12)
    _integer(quantity, "Quantity", maximum=10**12)
    if reference_id is not None:
        _key(reference_id, "Task/reference identity")
    request = {"quote_id": quote_id, "category": category, "reserved_microusd": reserved_microusd,
               "quantity": quantity, "reference_id": reference_id}
    request_hash = _digest(request)
    with store.transaction(write=True):
        _active_project(store, project_id)
        existing = store.one("SELECT * FROM pilot_usage_events WHERE project_id=? AND event_key=?", [project_id, event_key])
        if existing:
            if existing["request_sha256"] != request_hash:
                raise CommerceError("Usage event key belongs to different delivery work", 409)
            return {**existing, "duplicate": True}
        quote = get_quote(store, project_id, quote_id)
        if quote["status"] != "accepted":
            raise CommerceError("Processing requires an accepted quote", 409)
        usage = usage_summary(store, project_id, quote_id)
        if usage["actual_microusd"] + usage["reserved_microusd"] + reserved_microusd > quote["processing_cap_microusd"]:
            raise CommerceError("Accepted processing cap would be exceeded", 409)
        identity = uuid.uuid4().hex
        store.execute("INSERT INTO pilot_usage_events(id,project_id,quote_id,event_key,request_sha256,category,quantity,reserved_microusd,state,reference_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [identity, project_id, quote_id, event_key, request_hash, category, quantity, reserved_microusd, "reserved", reference_id, utcnow()])
        return {**store.one("SELECT * FROM pilot_usage_events WHERE id=?", [identity]), "duplicate": False}


def settle_usage(store, project_id, event_key, *, actual_microusd):
    """Record real/conservative cost even if an upstream estimate was too low.

    An overrun cannot be undone; it is retained and blocks further reservations.
    Unknown vendor usage must use a declared conservative value, not zero.
    """
    ensure_schema(store)
    _integer(actual_microusd, "Actual/conservatively charged cost", maximum=10**12)
    with store.transaction(write=True):
        _active_project(store, project_id)
        event = store.one("SELECT * FROM pilot_usage_events WHERE project_id=? AND event_key=?", [project_id, event_key])
        if not event:
            raise CommerceError("Usage reservation not found", 404)
        if event["state"] == "settled":
            if event["actual_microusd"] != actual_microusd:
                raise CommerceError("Settled usage is immutable; record a separately identified adjustment", 409)
            return {**event, "duplicate": True}
        store.execute("UPDATE pilot_usage_events SET actual_microusd=?,state='settled',settled_at=? WHERE id=? AND state='reserved'",
                      [actual_microusd, utcnow(), event["id"]])
        quote = get_quote(store, project_id, event["quote_id"])
        usage = usage_summary(store, project_id, event["quote_id"])
        if usage["actual_microusd"] + usage["reserved_microusd"] > quote["processing_cap_microusd"]:
            store.execute("UPDATE pilot_quotes SET status='cap_exceeded' WHERE id=? AND project_id=? AND status='accepted'", [event["quote_id"], project_id])
        return {**store.one("SELECT * FROM pilot_usage_events WHERE id=?", [event["id"]]), "duplicate": False}


def register_commerce_api(app, store_factory):
    """Register before access.install(), so its explicit route allowlist applies."""
    def store_for(project_id):
        require_project_access(project_id)
        store = store_factory()
        ensure_schema(store)
        _project(store, project_id)
        return store

    def permission(request, name, *, operator=False):
        principal = getattr(request.state, "doxa_principal", current_principal())
        if name not in principal.permissions:
            raise HTTPException(403, "This operation requires " + name + " permission")
        if operator and principal.managed:
            operators = {value.strip() for value in os.environ.get("DOXA_COMMERCE_OPERATOR_IDS", "").split(",") if value.strip()}
            if principal.id not in operators:
                raise HTTPException(403, "Quote issuance requires a server-configured service operator")
        return principal

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CommerceError as error:
            raise HTTPException(error.status_code, str(error)) from error

    @app.get("/api/pilot/projects/{project_id}/quotes")
    def quotes(project_id: str, request: Request):
        permission(request, "read")
        store = call(store_for, project_id)
        return {"quotes": [_public(row) for row in store.query("SELECT * FROM pilot_quotes WHERE project_id=? ORDER BY created_at DESC,id", [project_id])],
                "quote_required": quote_required(),
                "billing_mode": "manual_invoice", "payment_processing": False}

    @app.post("/api/pilot/projects/{project_id}/quotes", status_code=201)
    def issue_quote(project_id: str, body: dict, request: Request):
        principal = permission(request, "admin", operator=True)
        return call(create_quote, call(store_for, project_id), project_id, body, operator_id=principal.id)

    @app.post("/api/pilot/projects/{project_id}/quotes/{quote_id}/accept")
    def accept(project_id: str, quote_id: str, body: dict, request: Request):
        principal = permission(request, "admin")
        if set(body) != {"scope_sha256"}:
            raise HTTPException(422, "Only the offered scope_sha256 is accepted")
        return call(accept_quote, call(store_for, project_id), project_id, quote_id,
                    actor_id=principal.id, scope_sha256=body["scope_sha256"])

    @app.get("/api/pilot/projects/{project_id}/usage")
    def usage(project_id: str, request: Request, quote_id: str | None = None):
        permission(request, "read")
        return call(usage_summary, call(store_for, project_id), project_id, quote_id)

    @app.post("/api/pilot/projects/{project_id}/quotes/{quote_id}/revoke")
    def revoke(project_id: str, quote_id: str, body: dict, request: Request):
        principal = permission(request, "admin")
        if set(body) != {"reason_code"}:
            raise HTTPException(422, "Only reason_code is accepted")
        return call(revoke_quote, call(store_for, project_id), project_id, quote_id,
                    actor_id=principal.id, reason_code=body["reason_code"])
