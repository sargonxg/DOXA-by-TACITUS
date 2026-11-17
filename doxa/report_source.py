"""Bounded, revision-checked report inputs and offline rendering primitives."""
from __future__ import annotations

from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import zipfile

from . import reports
from .recorded_export import AssessmentRows, ReadOnlyStore, compact, file_hash, ids_hash, prepared_human_data, write_snapshot, write_xlsx, recompress_workbook, RECORD_ORDER
from .store import utcnow
from .workspace import Workspace, WorkspaceQuery, unpack_document

STAGES = ("documents", "assessments", "units", "reviews")
PAGE_BYTES = 512 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class SourceChanged(ValueError):
    pass


def revision_guard(store, config):
    if Workspace(store).revision(config["project_id"]) != config["snapshot_version"]:
        raise SourceChanged("The workspace changed before its report inputs were frozen. Refresh the query and create a new report.")


def source_config(store, project_id: str, query_id: str, profile: str) -> dict:
    service = Workspace(store)
    saved = service.require_query(query_id)
    if saved["project_id"] != project_id:
        raise ValueError("Query does not belong to this project")
    scope = WorkspaceQuery.model_validate(saved["scope"]).model_dump()
    clause, params, modern = service._scope(project_id, scope)
    if not modern and saved["aggregates"]["metrics"].get("findings"):
        raise ValueError("Durable report jobs require contextual v3 assessments; use the historical export for legacy results")
    run = saved["aggregates"].get("run")
    if run:
        run = json.loads(json.dumps(run))
        for key in ("provider_preflight", "source_manifest_key"):
            run.get("config", {}).pop(key, None)
    project = store.one("SELECT * FROM projects WHERE id=?", [project_id])
    metadata = {"schema_version": "3.0", "project": project, "runs": [run] if run else [],
                "metrics": saved["aggregates"]["metrics"], "profile": profile, "filters": scope,
                "query_id": query_id, "scope_hash": saved["scope_hash"], "snapshot_id": query_id,
                "snapshot_version": saved["snapshot_version"], "generated_at": utcnow(),
                "export_provenance": {"method": "durable frozen query", "query_persisted": True,
                    "source_content": "Source metadata and exact recorded evidence; originals remain separately stored.",
                    "human_appendix": "Human-readable appendix limits do not reduce the saved data selection.",
                    "inference_requests_made": 0}}
    result = {"project_id": project_id, "query_id": query_id, "scope": scope,
              "scope_hash": saved["scope_hash"], "snapshot_version": saved["snapshot_version"],
              "modern": modern, "metadata": metadata,
              "expected_documents": int(saved["aggregates"]["metrics"].get("documents", 0)),
              "expected_assessments": int(saved["aggregates"]["metrics"].get("findings", 0))}
    revision_guard(store, result)
    return result


@contextmanager
def source_reader(store):
    if store.remote:
        yield store
        return
    database = (store.root / "doxa.sqlite3").resolve()
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    reader = ReadOnlyStore(connection)
    reader.fts = store.fts
    try:
        yield reader
    finally:
        connection.rollback()
        connection.close()


def _spec(reader, config, stage):
    service = Workspace(reader)
    scope, project_id = config["scope"], config["project_id"]
    dw, dp = service._document_scope(project_id, scope)
    dc = " AND ".join(dw)
    clause, params, modern = service._scope(project_id, scope)
    if stage == "documents":
        return "d.*", "documents d", dc, dp, "d.id", "COALESCE(LENGTH(CAST(d.metadata_json AS BLOB)),0)+4096"
    if stage == "assessments":
        return "a.*", service._table(modern), clause, params, "a.id", "COALESCE(LENGTH(CAST(a.payload_json AS BLOB)),0)+COALESCE(LENGTH(CAST(a.review_json AS BLOB)),0)+4096" if modern else "LENGTH(CAST(a.payload_json AS BLOB))+4096"
    if stage == "units":
        return "u.*", "v3_unit_statuses u JOIN documents d ON d.id=u.document_id", "u.run_id=? AND " + dc, [scope.get("run_id"), *dp], "u.document_id||CHAR(31)||u.unit_id", "LENGTH(CAST(u.payload_json AS BLOB))+2048"
    return "r.*", "v3_review_events r JOIN v3_assessments a ON a.id=r.assessment_id JOIN documents d ON d.id=a.document_id", clause, params, "r.id", "LENGTH(CAST(r.corrections_json AS BLOB))+LENGTH(CAST(r.resolved_json AS BLOB))+LENGTH(CAST(r.note AS BLOB))+4096"


def capture_page(reader, config, stage: str, cursor: str = "") -> tuple[list[dict], str]:
    if stage not in STAGES:
        raise ValueError("Unknown report input stage")
    revision_guard(reader, config)
    columns, table, clause, params, key, size = _spec(reader, config, stage)
    candidates = reader.query("SELECT " + key + " _key,(" + size + ") _bytes FROM " + table
                              + " WHERE " + clause + " AND " + key + ">? ORDER BY " + key + " LIMIT 100", [*params, cursor])
    selected, budget = [], 0
    for row in candidates:
        estimated = max(1024, int(row["_bytes"]) * 2)
        if selected and budget + estimated > PAGE_BYTES:
            break
        selected.append(row["_key"])
        budget += estimated
    if not selected:
        revision_guard(reader, config)
        return [], cursor
    rows = reader.query("SELECT " + columns + " FROM " + table + " WHERE " + clause
                        + " AND " + key + " IN(SELECT value FROM json_each(?)) ORDER BY " + key, [*params, compact(selected)])
    if len(rows) != len(selected):
        raise SourceChanged("Report input membership changed while freezing")
    if any(len(compact(row).encode()) > MAX_RECORD_BYTES for row in rows):
        raise ValueError("A report input record exceeds the bounded capture size")
    revision_guard(reader, config)
    return rows, selected[-1]


def encode_part(stage, rows):
    raw = compact({"stage": stage, "rows": rows}).encode()
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _download_part(store, part, folder):
    target = folder / (part["stage"] + "-" + str(part["ordinal"]) + ".json.gz")
    if not target.is_file() or file_hash(target) != part["sha256"]:
        store.download(part["object_key"], target)
    if target.stat().st_size != part["bytes"] or file_hash(target) != part["sha256"]:
        raise ValueError("Frozen report input checksum failed")
    return part, target


def materialize(store, config, parts: list[dict], folder: Path, check=lambda: None) -> tuple[Path, Path]:
    """Rebuild a private renderer database from immutable, bounded parts."""
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / "frozen.sqlite3"
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(Path(__file__).with_name("schema.sql").read_text() + "\n" + Path(__file__).with_name("v3_schema.sql").read_text())
        from .learning import SCHEMA_SQL
        connection.executescript(SCHEMA_SQL)
        tables = {"documents": "documents", "assessments": "v3_assessments", "units": "v3_unit_statuses", "reviews": "v3_review_events"}
        schemas = {name: {row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")} for name, table in tables.items()}
        ordered = sorted(parts, key=lambda item: (STAGES.index(item["stage"]), item["ordinal"]))
        with ThreadPoolExecutor(max_workers=4) as executor:
            for offset in range(0, len(ordered), 8):
                check()
                batch = list(executor.map(lambda part: _download_part(store, part, folder), ordered[offset:offset+8]))
                for part, path in batch:
                    with gzip.open(path, "rb") as stream:
                        raw = stream.read(MAX_RECORD_BYTES * 4 + 1)
                    if len(raw) > MAX_RECORD_BYTES * 4:
                        raise ValueError("Frozen report part exceeds its raw byte limit")
                    payload = json.loads(raw)
                    if payload["stage"] != part["stage"] or len(payload["rows"]) != part["row_count"]:
                        raise ValueError("Frozen report part membership differs")
                    table, columns = tables[part["stage"]], schemas[part["stage"]]
                    for row in payload["rows"]:
                        for name in set(row) - columns:
                            if not name.replace("_", "").isalnum():
                                raise ValueError("Invalid captured field name")
                            connection.execute('ALTER TABLE ' + table + ' ADD COLUMN "' + name + '"')
                            columns.add(name)
                        names = list(row)
                        connection.execute("INSERT INTO " + table + "(" + ",".join('"'+name+'"' for name in names) + ") VALUES(" + ",".join("?" for _ in names) + ")", [row[name] for name in names])
                connection.commit()
        connection.execute("CREATE INDEX IF NOT EXISTS report_order ON v3_assessments(document_id,unit_id,lens_id,criterion_id,id)")
        connection.commit()
    finally:
        connection.close()
    metadata_path = folder / "metadata.json"
    metadata_path.write_text(compact(config["metadata"]), encoding="utf-8")
    return database, metadata_path


@contextmanager
def frozen_view(database: Path, metadata: dict):
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    store = ReadOnlyStore(connection)
    documents = [unpack_document(row) for row in store.query("SELECT * FROM documents ORDER BY id")]
    clause, table, params = "1", "v3_assessments a JOIN documents d ON d.id=a.document_id", []
    count = store.one("SELECT COUNT(*) n FROM v3_assessments")["n"]
    typed = store.query("SELECT criterion_kind,result,COUNT(*) count FROM v3_assessments GROUP BY criterion_kind,result ORDER BY criterion_kind,result")
    units = store.query("SELECT status,COUNT(*) count,COALESCE(SUM(characters),0) characters FROM v3_unit_statuses GROUP BY status")
    completed = store.one("SELECT COUNT(*) n FROM(SELECT document_id FROM v3_unit_statuses GROUP BY document_id HAVING SUM(status='accepted')=COUNT(*))")["n"]
    metadata["unit_states"] = units
    metadata["metrics"]["by_category"] = store.query("SELECT criterion_id name,COUNT(*) count FROM v3_assessments GROUP BY criterion_id")
    order = ",".join("a." + key for key in RECORD_ORDER)
    try:
        yield {"connection": connection, "store": store, "metadata": metadata, "documents": documents,
               "records": AssessmentRows(connection, "SELECT a.* FROM " + table, [], count),
               "display_records": AssessmentRows(connection, "SELECT a.* FROM " + table, [], count, display=True),
               "typed_counts": typed, "unit_states": units, "complete_documents": completed,
               "reviews_sql": "SELECT * FROM v3_review_events ORDER BY created_at,id", "params": params, "table": table, "clause": clause,
               "source_ids_sha256": ids_hash(row["id"] for row in documents),
               "assessment_ids_sha256": ids_hash(row[0] for row in connection.execute("SELECT id FROM v3_assessments ORDER BY id")),
               "assessment_record_order_sha256": ids_hash(row[0] for row in connection.execute("SELECT a.id FROM v3_assessments a ORDER BY " + order))}
    finally:
        connection.close()


def _offline_pdf(html: str, path: Path):
    from weasyprint import HTML
    def no_network(*_args, **_kwargs):
        raise ValueError("Report rendering never fetches external resources")
    HTML(string=html, url_fetcher=no_network).write_pdf(path)


def render(database: Path, metadata_path: Path, output: Path, format: str) -> list[dict]:
    """Called in an isolated child process; consumes only frozen local inputs."""
    metadata = json.loads(metadata_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    workbook_info = None
    with frozen_view(database, metadata) as frozen:
        snapshot = output / "snapshot.json.gz"
        snapshot_info = write_snapshot(frozen, snapshot, MAX_ARTIFACT_BYTES)
        artifacts.append(("snapshot", snapshot, "application/gzip"))
        data = prepared_human_data(frozen)
        if format in {"pdf", "html"}:
            path = output / ("report." + format)
            html = reports._html(metadata, data)
            if format == "pdf":
                _offline_pdf(html, path)
            else:
                path.write_text(html, encoding="utf-8")
            artifacts.append((format, path, "application/pdf" if format == "pdf" else "text/html; charset=utf-8"))
        elif format == "xlsx":
            path = output / "report.xlsx"
            workbook_info = write_xlsx(frozen, data, path)
            recompress_workbook(path)
            artifacts.append((format, path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        elif format == "jsonl":
            path = output / "report.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as stream:
                stream.write(compact({"record_type": "manifest", **metadata}) + "\n")
                for row in frozen["documents"]:
                    stream.write(compact({"record_type": "document", **row}) + "\n")
                for row in frozen["records"]:
                    stream.write(compact({"record_type": "assessment", **row}) + "\n")
            artifacts.append((format, path, "application/gzip"))
        else:
            import csv
            path = output / "report.csv.gz"
            fields = ("record_type", "id", "document_id", "title", "author", "criterion_kind", "result", "lens_id", "criterion_id", "quote", "explanation", "review_status", "source_url", "license", "record_json")
            with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                for kind, rows in (("document", frozen["documents"]), ("assessment", frozen["records"])):
                    for row in rows:
                        values = {**row, "record_type": kind, "record_json": compact(row)}
                        writer.writerow([reports._csv_safe(values.get(field)) for field in fields])
            artifacts.append((format, path, "application/gzip"))
        manifest = {"schema_version": "doxa-report-job-1", "query_id": metadata["query_id"], "scope_hash": metadata["scope_hash"],
                    "snapshot_version": metadata["snapshot_version"], "generated_at": metadata["generated_at"], "profile": metadata["profile"],
                    "counts": {"documents": len(frozen["documents"]), "assessments": len(frozen["records"]), "complete_documents": frozen["complete_documents"]},
                    "typed_counts": frozen["typed_counts"], "unit_states": frozen["unit_states"], "snapshot": snapshot_info,
                    "source_ids_sha256": frozen["source_ids_sha256"], "assessment_ids_sha256": frozen["assessment_ids_sha256"],
                    "assessment_record_order": list(RECORD_ORDER), "assessment_record_order_sha256": frozen["assessment_record_order_sha256"],
                    "workbook": workbook_info,
                    "interpretation_accuracy": None, "inference_requests_made": 0,
                    "files": [{"format": kind, "filename": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)} for kind, path, _ in artifacts]}
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(("manifest", manifest_path, "application/json"))
    result = []
    for kind, path, content_type in artifacts:
        if not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Report artifact exceeds its bounded size; choose a smaller query")
        result.append({"format": kind, "filename": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path), "content_type": content_type, "path": str(path)})
    return result


def input_zip(config, parts, folder: Path, target: Path):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", compact({"schema_version": "doxa-frozen-report-inputs-1", "metadata": config["metadata"], "parts": [{key: value for key, value in part.items() if key != "object_key"} for part in parts]}))
        for part in parts:
            name = part["stage"] + "-" + str(part["ordinal"]) + ".json.gz"
            archive.write(folder / name, "parts/" + name)
    if target.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Frozen input archive exceeds the artifact size limit")
