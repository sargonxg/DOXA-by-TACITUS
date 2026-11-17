#!/usr/bin/env python3
"""Export a settled v3 run without inference or writes to its workspace.

The frozen selection uses Workspace's scopes, review overlays and aggregates.
JSON and XLSX records stream from one SQLite read transaction. Human editions
reuse the report templates with complete SQL totals and a 25-entry appendix.
This CLI's streaming behavior is separate from the browser export endpoint.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from contextlib import contextmanager
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile

from . import reports
from .store import utcnow
from .workspace import Workspace, WorkspaceQuery, unpack_assessment, unpack_document

RESULTS = ("meets_criterion", "does_not_meet", "uncertain", "not_applicable")
TERMINAL = {"completed", "partial", "failed", "cancelled"}
MAX_FILE_BYTES = 99_000_000
MAX_RAW_BYTES = 2 * 1024 * 1024 * 1024
RECORD_ORDER = ("document_id", "unit_id", "lens_id", "criterion_id", "id")


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class ReadOnlyStore:
    """The subset of Store used by Workspace; deliberately has no write method."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.lock = threading.RLock()
        self.fts = False

    def query(self, sql, params=()):
        if not sql.lstrip().upper().startswith(("SELECT", "WITH", "EXPLAIN")):
            raise ValueError("Recorded export permits read-only queries")
        return [dict(row) for row in self.connection.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


class AssessmentRows(Sequence):
    """SQL-backed records, with the full count and bounded slicing for templates."""

    def __init__(self, connection, select, params, count, *, display=False):
        self.connection, self.select, self.params, self.count = connection, select, params, count
        self.display = display
        priority = ("CASE WHEN a.result='meets_criterion' AND a.criterion_kind='indicator' THEN 0 "
                    "WHEN a.result='uncertain' THEN 1 WHEN a.result='meets_criterion' "
                    "AND a.criterion_kind='boundary' THEN 2 WHEN a.result='meets_criterion' THEN 3 ELSE 4 END,")
        self.order = (priority if display else "") + ",".join("a." + key for key in RECORD_ORDER)

    def __len__(self):
        return self.count

    def __iter__(self):
        for row in self.connection.execute(self.select + " ORDER BY " + self.order, self.params):
            yield unpack_assessment(dict(row))

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, end, step = index.indices(self.count)
            if step != 1 or end - start > 100:
                raise ValueError("Human report slices must be bounded to at most 100 records")
            rows = [unpack_assessment(dict(row)) for row in self.connection.execute(
                self.select + " ORDER BY " + self.order + " LIMIT ? OFFSET ?", [*self.params, end-start, start])]
            if self.display:
                for row in rows:
                    for key in ("context", "source_context"):
                        text, quote = row.get(key) or "", row.get("quote") or ""
                        if len(text) > 1800:
                            position = text.find(quote) if quote else 0
                            start = max(0, position - 300)
                            end = min(len(text), max(start + 1800, max(position, 0) + len(quote) + 300))
                            row[key] = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
            return rows
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        return self[index:index+1][0]


def ids_hash(identities) -> str:
    value = hashlib.sha256()
    for identity in identities:
        value.update((identity + "\n").encode())
    return value.hexdigest()


@contextmanager
def recorded_snapshot(data_dir: Path, run_id: str):
    database = Path(data_dir).resolve() / "doxa.sqlite3"
    if not database.is_file():
        raise ValueError("Recorded workspace database does not exist")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        store = ReadOnlyStore(connection)
        original = store.one("SELECT * FROM runs WHERE id=?", [run_id])
        if not original:
            raise ValueError("Recorded run not found")
        if original["status"] not in TERMINAL:
            raise ValueError("Wait for a terminal run; active or paused runs cannot be exported")
        config = json.loads(original["config_json"])
        if str(config.get("methodology_version")) != "3.0" or config.get("provider") == "lexical":
            raise ValueError("Use a recorded methodology-v3 model run")
        frozen = config.get("source_snapshot") or []
        expected = {row["id"]: row["sha256"] for row in frozen}
        if not expected or len(expected) != len(frozen):
            raise ValueError("The run must retain a unique frozen source selection")
        project_id = original["project_id"]
        project = store.one("SELECT * FROM projects WHERE id=?", [project_id])
        if not project:
            raise ValueError("Recorded project not found")
        scope = WorkspaceQuery(run_id=run_id, results=list(RESULTS), duplicate_policy="sources").model_dump()
        workspace = Workspace(store)
        revision = workspace.revision(project_id)
        aggregates = workspace.aggregate(project_id, scope)
        dw, dp = workspace._document_scope(project_id, scope)
        documents = [unpack_document(row) for row in store.query(
            "SELECT d.* FROM documents d WHERE " + " AND ".join(dw) + " ORDER BY d.id", dp)]
        actual = {row["id"]: row["sha256"] for row in documents}
        if actual != expected:
            raise ValueError("Source selection is missing or differs from the run's frozen source hashes")
        clause, params, modern = workspace._scope(project_id, scope)
        if not modern:
            raise ValueError("Recorded workspace does not use typed v3 assessments")
        table = workspace._table(True)
        select = "SELECT a.* FROM " + table + " WHERE " + clause
        count = store.one("SELECT COUNT(*) n FROM " + table + " WHERE " + clause, params)["n"]
        records = AssessmentRows(connection, select, params, count)
        typed = store.query("SELECT a.criterion_kind,a.result,COUNT(*) count FROM " + table + " WHERE " + clause
                            + " GROUP BY a.criterion_kind,a.result ORDER BY a.criterion_kind,a.result", params)
        if sum(row["count"] for row in typed) != count or any(row["result"] not in RESULTS for row in typed):
            raise ValueError("Typed assessment counts do not match the complete selection")
        metrics = {**aggregates["metrics"],
                   "by_category": store.query("SELECT a.criterion_id name,COUNT(*) count FROM " + table
                                              + " WHERE " + clause + " GROUP BY a.criterion_id ORDER BY count DESC,a.criterion_id", params),
                   "timeline": aggregates["timeline"], "by_author": []}
        run = aggregates["run"]
        # Connectivity probes are operational details, not research provenance.
        omitted = ["provider_preflight", "source_manifest_key"]
        for key in omitted:
            run["config"].pop(key, None)
        query_id = uuid.uuid4().hex
        metadata = {"schema_version": "3.0", "project": project, "runs": [run], "metrics": metrics,
                    "profile": "briefing", "filters": scope, "query_id": query_id,
                    "scope_hash": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
                    "snapshot_id": query_id, "snapshot_version": revision, "generated_at": utcnow(),
                    "export_provenance": {"method": "read-only Workspace snapshot", "query_persisted": False,
                        "source_database_modified": False, "all_result_types": list(RESULTS),
                        "omitted_private_connectivity_fields": omitted,
                        "source_content": "Complete source metadata; full original bodies remain in the separately licensed corpus. Exact assessed quotes and recorded context are retained.",
                        "human_appendix": "25 records, prioritizing indicator matches, uncertainty, boundaries and descriptive matches; totals cover all result types. Context windows are excerpted around the complete primary quote; full context stays in the complete data exports."}}
        reviews_sql = ("SELECT r.* FROM v3_review_events r JOIN v3_assessments a ON a.id=r.assessment_id "
                       "JOIN documents d ON d.id=a.document_id WHERE " + clause + " ORDER BY r.created_at")
        unit_states = store.query("SELECT status,COUNT(*) count,COALESCE(SUM(characters),0) characters "
                                 "FROM v3_unit_statuses WHERE run_id=? GROUP BY status ORDER BY status", [run_id])
        metadata["unit_states"] = unit_states
        packs = config.get("lens_snapshots") or []
        criterion_count = sum(len(pack.get("criteria", [])) for pack in packs) if isinstance(packs, list) else 0
        if criterion_count:
            metadata["requested_criterion_decisions"] = sum(row["count"] for row in unit_states) * criterion_count
        unit_documents = store.query("SELECT document_id,COUNT(*) total,SUM(status='accepted') done FROM v3_unit_statuses "
                                     "WHERE run_id=? GROUP BY document_id", [run_id])
        assessment_ids = ids_hash(row[0] for row in connection.execute(
            "SELECT a.id FROM " + table + " WHERE " + clause + " ORDER BY a.id", params))
        record_order_hash = ids_hash(row[0] for row in connection.execute(
            "SELECT a.id FROM " + table + " WHERE " + clause + " ORDER BY " + ",".join("a." + key for key in RECORD_ORDER), params))
        yield {"connection": connection, "store": store, "metadata": metadata, "documents": documents,
               "records": records, "display_records": AssessmentRows(connection, select, params, count, display=True),
               "typed_counts": typed, "unit_states": unit_states, "complete_documents": sum(row["done"] == row["total"] and row["total"] > 0 for row in unit_documents),
               "reviews_sql": reviews_sql, "params": params, "table": table, "clause": clause,
               "source_ids_sha256": ids_hash(sorted(actual)), "assessment_ids_sha256": assessment_ids,
               "assessment_record_order_sha256": record_order_hash}
    finally:
        connection.rollback()
        connection.close()


def prepared_human_data(frozen: dict) -> dict:
    store, params, table, clause = (frozen[key] for key in ("store", "params", "table", "clause"))
    documents = frozen["documents"]
    from .mail import MAIL_AUTHOR_NOTICE, author_eligible_sql, is_mail_document
    has_mail = any(is_mail_document(document) for document in documents)
    authors = {}
    for document in documents:
        entry = authors.setdefault(document.get("author") or "Unknown", {"documents": 0, "words": 0, "findings": 0, "confirmed": 0})
        entry["documents"] += 1
        entry["words"] += document.get("word_count", 0)
        if is_mail_document(document):
            entry["mail_sources"] = entry.get("mail_sources", 0) + 1
            entry["excluded_context_findings"] = 0
    if has_mail:
        eligible = author_eligible_sql()
        author_query = ("SELECT COALESCE(NULLIF(d.author,''),'Unknown') author,SUM(CASE WHEN " + eligible + " THEN 1 ELSE 0 END) findings,"
                        "SUM(CASE WHEN " + eligible + " AND a.review_status IN ('confirmed','accepted') THEN 1 ELSE 0 END) confirmed,"
                        "SUM(CASE WHEN NOT " + eligible + " THEN 1 ELSE 0 END) excluded_context_findings FROM " + table
                        + " WHERE " + clause + " GROUP BY d.author")
    else:
        author_query = ("SELECT COALESCE(NULLIF(d.author,''),'Unknown') author,COUNT(*) findings,"
                        "SUM(a.review_status IN ('confirmed','accepted')) confirmed FROM " + table
                        + " WHERE " + clause + " GROUP BY d.author")
    for row in store.query(author_query, params):
        authors[row["author"]].update({"findings": row["findings"], "confirmed": row["confirmed"]})
        if has_mail and authors[row["author"]].get("mail_sources"):
            authors[row["author"]]["excluded_context_findings"] = row["excluded_context_findings"]
    def counts(column):
        return Counter({row["label"]: row["count"] for row in store.query(
            "SELECT " + column + " label,COUNT(*) count FROM " + table + " WHERE " + clause + " GROUP BY " + column, params)})
    result = {"documents": documents, "findings": frozen["display_records"], "runs": frozen["metadata"]["runs"],
            "by_id": {row["id"]: row for row in documents}, "authors": authors,
            "categories": counts("a.criterion_id"), "lenses": counts("a.lens_id"), "reviews": counts("a.review_status"),
            "statuses": Counter(row.get("status") or "unknown" for row in documents), "modern": True,
            "typed_counts": Counter({(row["criterion_kind"], row["result"]): row["count"] for row in frozen["typed_counts"]}),
            "word_count": sum(row.get("word_count", 0) for row in documents), "page_count": sum(row.get("page_count", 0) for row in documents)}
    if has_mail:
        result["mail_author_notice"] = MAIL_AUTHOR_NOTICE
    return result


class LimitedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.bytes = stream, limit, 0

    def write(self, data):
        self.bytes += len(data)
        if self.bytes >= self.limit:
            raise ValueError("Export exceeds its per-file byte limit; use the bounded archive parts instead")
        return self.stream.write(data)

    def flush(self):
        return self.stream.flush()


def write_snapshot(frozen: dict, target: Path, limit: int) -> dict:
    raw_hash, raw_bytes = hashlib.sha256(), 0
    source_ids, assessment_ids = hashlib.sha256(), hashlib.sha256()
    typed_counts = Counter()
    document_count = finding_count = 0
    with target.open("wb") as output:
        with gzip.GzipFile(filename="", fileobj=LimitedWriter(output, limit), mode="wb", mtime=0, compresslevel=6) as archive:
            def write(text):
                nonlocal raw_bytes
                data = text.encode()
                raw_bytes += len(data)
                if raw_bytes > MAX_RAW_BYTES:
                    raise ValueError("Snapshot exceeds the bounded uncompressed export size")
                raw_hash.update(data)
                archive.write(data)
            write("{")
            for index, (key, value) in enumerate(frozen["metadata"].items()):
                write(("," if index else "") + compact(key) + ":" + compact(value))
            write(',"documents":[')
            for document_count, row in enumerate(frozen["documents"], 1):
                write(("," if document_count > 1 else "") + compact(row))
                source_ids.update((row["id"] + "\n").encode())
            write('],"findings":[')
            for finding_count, row in enumerate(frozen["records"], 1):
                write(("," if finding_count > 1 else "") + compact(row))
                assessment_ids.update((row["id"] + "\n").encode())
                typed_counts[(row.get("criterion_kind"), row.get("result"))] += 1
            write('],"reviews":[')
            for index, row in enumerate(frozen["connection"].execute(frozen["reviews_sql"], frozen["params"])):
                write(("," if index else "") + compact(dict(row)))
            write("]}")
    if source_ids.hexdigest() != frozen["source_ids_sha256"] or assessment_ids.hexdigest() != frozen["assessment_record_order_sha256"]:
        raise ValueError("Snapshot source/assessment identities differ from the recorded selection")
    expected_typed = Counter({(row["criterion_kind"], row["result"]): row["count"] for row in frozen["typed_counts"]})
    if typed_counts != expected_typed:
        raise ValueError("Snapshot typed outcome counts differ from the recorded selection")
    return {"uncompressed_bytes": raw_bytes, "uncompressed_sha256": raw_hash.hexdigest(),
            "documents": document_count, "assessments": finding_count}


def write_xlsx(frozen: dict, data: dict, target: Path) -> dict:
    import xlsxwriter
    logical_rows, physical_rows = {}, {}
    with xlsxwriter.Workbook(target, {"constant_memory": True, "use_zip64": True, "strings_to_formulas": False,
                                     "strings_to_urls": False, "tmpdir": str(target.parent)}) as workbook:
        workbook.set_properties({"title": "DOXA recorded corpus dashboard", "author": "DOXA by TACITUS"})
        header = workbook.add_format({"bold": True, "bg_color": "#173C39", "font_color": "white", "text_wrap": True})
        body = workbook.add_format({"font_color": "#27453D", "valign": "top", "text_wrap": True})
        title = workbook.add_format({"bold": True, "font_size": 23, "font_color": "#173C39"})
        sheets = {name: workbook.add_worksheet(name) for name in ("Overview", "Documents", "Findings", "Typed results", "Authors", "Lenses", "Units", "Runs", "Methodology")}
        for sheet in sheets.values():
            sheet.hide_gridlines(2)
            sheet.set_tab_color("#1F7A70")
            sheet.set_default_row(27)
            sheet.freeze_panes(1, 1)
        def table(name, headers, rows):
            sheet = sheets[name]
            sheet.write_row(0, 0, [*headers, "Part"], header)
            sheet.set_row(0, 32)
            sheet.set_column(0, len(headers), 24, body)
            sheet.set_column(1, 2, 45, body)
            cursor = count = 0
            for count, row in enumerate(rows, 1):
                cells = []
                for value in row:
                    value = compact(value) if isinstance(value, (dict, list)) else ("" if value is None else value)
                    cells.append([value[i:i+32000] for i in range(0, len(value), 32000)] or [""] if isinstance(value, str) else [value])
                parts = max(map(len, cells), default=1)
                if cursor + parts >= 1_048_575:
                    raise ValueError("Export exceeds Excel's worksheet row limit")
                for part in range(parts):
                    cursor += 1
                    for column, pieces in enumerate(cells):
                        value = pieces[part] if part < len(pieces) else ""
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            sheet.write_number(cursor, column, value, body)
                        else:
                            sheet.write_string(cursor, column, str(value), body)
                    sheet.write_string(cursor, len(headers), f"{part+1}/{parts}", body)
            sheet.autofilter(0, 0, max(cursor, 1), len(headers))
            logical_rows[name], physical_rows[name] = count, cursor
        documents = frozen["documents"]
        def document_rows():
            for row in documents:
                metadata = row.get("metadata") or {}
                translators = "; ".join(person.get("name", "") for person in metadata.get("translators", []) if isinstance(person, dict))
                yield [row["id"], row.get("title"), row.get("author"), row.get("published_at"), row.get("word_count"),
                       row.get("source_url"), row.get("license"), row.get("sha256"),
                       row.get("attribution") or metadata.get("attribution"), translators,
                       row.get("license_url") or metadata.get("license_url"), row]
        table("Documents", ["Document ID", "Title", "Author", "Published", "Words", "Source URL", "License", "Source SHA256", "Attribution", "Translation credit", "License URL", "Complete source metadata JSON"], document_rows())
        finding_fields = ["id", "document_id", "lens_id", "criterion_id", "criterion_kind", "result", "review_status", "attribution", "speech_role", "speaker", "speaker_stance", "document_stance", "proposition", "quote", "explanation", "alternative", "block_id", "start", "end"]
        written_ids, written_types = hashlib.sha256(), Counter()
        def finding_rows():
            for row in frozen["records"]:
                written_ids.update((row["id"] + "\n").encode())
                written_types[(row.get("criterion_kind"), row.get("result"))] += 1
                yield [*[row.get(key) for key in finding_fields], row]
        table("Findings", [*finding_fields, "Complete assessment JSON"], finding_rows())
        if written_ids.hexdigest() != frozen["assessment_record_order_sha256"] or written_types != data["typed_counts"]:
            raise ValueError("Workbook assessment IDs or typed outcomes differ from the frozen selection")
        table("Typed results", ["Criterion kind", "Result", "Assessments", "Label"], ([row["criterion_kind"], row["result"], row["count"], row["criterion_kind"] + " / " + row["result"]] for row in frozen["typed_counts"]))
        if data.get("mail_author_notice"):
            table("Authors", ["Document author / mail metadata", "Source documents", "All source words", "Eligible group assessments", "Confirmed eligible", "Excluded context assessments", "Mail source documents"],
                  ([name, row["documents"], row["words"], row["findings"], row["confirmed"], row.get("excluded_context_findings", 0), row.get("mail_sources", 0)] for name, row in sorted(data["authors"].items())))
        else:
            table("Authors", ["Author", "Source documents", "Source words", "All assessments", "Confirmed assessments"],
                  ([name, row["documents"], row["words"], row["findings"], row["confirmed"]] for name, row in sorted(data["authors"].items())))
        table("Lenses", ["Lens", "All assessments"], sorted(data["lenses"].items()))
        table("Units", ["Status", "Units", "Characters"], ([row["status"], row["count"], row["characters"]] for row in frozen["unit_states"]))
        table("Runs", ["Run ID", "Status", "Full recorded run provenance JSON"], ([row["id"], row["status"], row] for row in frozen["metadata"]["runs"]))
        method_rows = [
            ["Scope", frozen["metadata"]["filters"]], ["Query ID", frozen["metadata"]["query_id"]],
            ["Scope hash", frozen["metadata"]["scope_hash"]], ["Snapshot revision", frozen["metadata"]["snapshot_version"]],
            ["Provenance", frozen["metadata"]["export_provenance"]],
            ["Interpretation", "All four result types and all selected sources are retained. Indicator, boundary and descriptive results are separate; there is no combined prejudice index. Exact source anchoring is not interpretation accuracy."],
            ["Excel safety", "Source strings are literal cell values. Long values continue across physical rows with the Part column; logical record counts are in the manifest. No formulas or external data connections."],
            ["Human edition", "The PDF/HTML briefings display 25 prioritized assessment and source entries, with shown/total disclosure. This XLSX and the compressed snapshot retain the complete selected data."],
            ["Streaming scope", "This command streams JSON/XLSX; it makes no claim that the separate browser export endpoint has the same memory behavior."]]
        if data.get("mail_author_notice"):
            method_rows.append(["Mail author-group limits", data["mail_author_notice"]])
        table("Methodology", ["Topic", "Details"], method_rows)
        overview = sheets["Overview"]
        overview.set_column("A:A", 37)
        overview.set_column("B:H", 24)
        overview.merge_range("A1:H2", "DOXA / TACITUS", title)
        overview.merge_range("A4:H4", frozen["metadata"]["project"].get("name", "Recorded corpus"), body)
        metrics = frozen["metadata"]["metrics"]
        run = (frozen["metadata"]["runs"] or [{"status": "not_assessed", "usage": {}}])[0]
        detail = metrics["coverage_detail"]
        rows = [["Recorded status", run["status"]], ["Selected sources", len(documents)],
                ["Retained assessments · all result types", len(frozen["records"])],
                ["Fully assessed contextual units", f'{detail["units_done"]:,} of {detail["units_total"]:,}'],
                ["Fully assessed documents", f'{frozen["complete_documents"]:,} of {len(documents):,}'],
                ["Interpretation accuracy", "Unmeasured; no independent human quality labels"],
                ["Provider invoice cost", "Unknown" if run.get("usage", {}).get("cost_usd") is None else run["usage"]["cost_usd"]],
                ["Application budget accounted (USD)", run.get("spent_usd")],
                ["Read with context", "Source wording, represented speaker, criterion and review status remain separate. Typed counts are not a prejudice index."]]
        for index, row in enumerate(rows, 5):
            overview.write_row(index, 0, row, body)
        overview.merge_range("A17:H19", "Open Documents and Findings for the complete data, including nonmatches and not-applicable decisions. Review all interpretations in source context; counts do not establish an author's private beliefs.", body)
        if frozen["typed_counts"]:
            chart = workbook.add_chart({"type": "bar"})
            chart.add_series({"name": "Typed assessment counts · all results", "categories": ["Typed results", 1, 3, len(frozen["typed_counts"]), 3], "values": ["Typed results", 1, 2, len(frozen["typed_counts"]), 2], "fill": {"color": "#1F7A70"}})
            chart.set_title({"name": "Different criterion kinds remain separate"})
            chart.set_legend({"none": True})
            chart.set_size({"width": 900, "height": 450})
            overview.insert_chart("A22", chart)
    if logical_rows["Documents"] != len(documents) or logical_rows["Findings"] != len(frozen["records"]):
        raise ValueError("Workbook record counts differ from the frozen selection")
    return {"logical_rows": logical_rows, "physical_rows": physical_rows, "constant_memory": True}


def recompress_workbook(target: Path, *, threshold=95_000_000) -> dict:
    """Reduce ZIP storage, preserving every member's bytes and metadata."""
    before = target.stat().st_size
    if before <= threshold:
        return {"applied": False, "before_bytes": before, "after_bytes": before, "threshold_bytes": threshold}
    packed = target.with_suffix(".zip9.xlsx")
    expected = []
    try:
        with zipfile.ZipFile(target) as source, zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for original in source.infolist():
                info = copy.copy(original)
                if hasattr(info, "compress_level"):
                    info.compress_level = 9
                else:
                    info._compresslevel = 9  # Python 3.11/3.12 ZipInfo compatibility.
                member_hash = hashlib.sha256()
                with source.open(original) as reader, output.open(info, "w", force_zip64=original.file_size >= zipfile.ZIP64_LIMIT) as writer:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        member_hash.update(chunk)
                        writer.write(chunk)
                expected.append((original.filename, original.date_time, original.compress_type, original.file_size, member_hash.hexdigest()))
        actual = []
        with zipfile.ZipFile(packed) as archive:
            for info in archive.infolist():
                member_hash = hashlib.sha256()
                with archive.open(info) as reader:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        member_hash.update(chunk)
                actual.append((info.filename, info.date_time, info.compress_type, info.file_size, member_hash.hexdigest()))
        if actual != expected:
            raise ValueError("Recompressed workbook differs from its complete original members")
        smaller = packed.stat().st_size < before
        after = packed.stat().st_size if smaller else before
        if smaller:
            packed.replace(target)
        return {"applied": smaller, "compression_level": 9, "before_bytes": before, "after_bytes": after,
                "uncompressed_member_sha256_verified": True, "timestamps_and_methods_preserved": True}
    finally:
        packed.unlink(missing_ok=True)


def export_run(data_dir: Path, run_id: str, output_prefix: Path, *, formats=("pdf", "html", "xlsx"), max_file_bytes=MAX_FILE_BYTES) -> dict:
    if not 0 < max_file_bytes < 100_000_000:
        raise ValueError("Choose a positive per-file limit below 100 MB")
    if len(set(formats)) != len(formats) or set(formats) - {"pdf", "html", "xlsx"}:
        raise ValueError("Choose PDF, HTML and/or XLSX formats")
    output_prefix = Path(output_prefix).resolve()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", output_prefix.name):
        raise ValueError("Use a simple output basename")
    with recorded_snapshot(Path(data_dir), run_id) as frozen:
        output_prefix.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".doxa-recorded-", dir=output_prefix.parent) as temporary:
            directory = Path(temporary)
            snapshot_path = directory / (output_prefix.name + ".snapshot.json.gz")
            streamed = write_snapshot(frozen, snapshot_path, max_file_bytes)
            data = prepared_human_data(frozen)
            artifacts = [snapshot_path]
            workbook_info = None
            if set(formats) & {"html", "pdf"}:
                html = reports._html(frozen["metadata"], data)
                html = html.replace("Patterns in this selection", "Assessment workload in this selection")
                html = html.replace("What the corpus surfaces", "Criteria applied in this selection")
                html = html.replace("Observations by category", "Assessment counts by criterion (all outcomes)")
                html = html.replace("The most frequent exported category is", "The criterion with the most retained assessments is")
                detail = frozen["metadata"]["metrics"]["coverage_detail"]
                unresolved_units = detail["units_total"] - detail["units_done"]
                coverage_note = (f'<p class="note"><strong>Recorded processing scope:</strong> {len(frozen["documents"]):,} selected sources; '
                                 f'{detail["units_done"]:,} of {detail["units_total"]:,} contextual units fully assessed; '
                                 f'{unresolved_units:,} units not fully assessed. All four assessment outcomes are retained. '
                                 'Processing coverage and criterion-application counts are not interpretation accuracy, prevalence or bias rates.</p>')
                html = html.replace('</header>', '</header>' + coverage_note, 1)
                note = ('<p class="note">This briefing prioritizes indicator matches, uncertainties, boundaries and descriptive matches in its 25-entry evidence appendix. '
                        'All source, assessment and typed-result totals describe the full selected run. Context windows are excerpted around the complete primary quote for readable printing. Complete records and context, including nonmatches and not-applicable decisions, remain in XLSX and the compressed snapshot.</p>')
                html = html.replace('<section class="panel" id="evidence">', '<section class="panel" id="evidence">' + note, 1)
                if len(html.encode()) >= max_file_bytes:
                    raise ValueError("Offline HTML exceeds its per-file byte limit")
                if "html" in formats:
                    path = directory / (output_prefix.name + ".html")
                    path.write_text(html, encoding="utf-8")
                    artifacts.append(path)
                if "pdf" in formats:
                    path = directory / (output_prefix.name + ".pdf")
                    try:
                        from weasyprint import HTML
                        def no_network(*_args, **_kwargs):
                            raise ValueError("Offline report renderer does not fetch network resources")
                        HTML(string=html, url_fetcher=no_network).write_pdf(path)
                    except ImportError:
                        reports._pdf_fallback(frozen["metadata"], data, path)
                    artifacts.append(path)
            if "xlsx" in formats:
                path = directory / (output_prefix.name + ".xlsx")
                workbook_info = write_xlsx(frozen, data, path)
                workbook_info["zip_recompression"] = recompress_workbook(path)
                artifacts.append(path)
            for path in artifacts:
                if not 0 < path.stat().st_size < max_file_bytes:
                    raise ValueError("Export exceeds its per-file byte limit; use the bounded archive parts instead")
            meta = frozen["metadata"]
            manifest = {"schema_version": "doxa-recorded-export-1", "run_id": run_id,
                        "run_status": meta["runs"][0]["status"], "generated_at": meta["generated_at"],
                        "query_id": meta["query_id"], "scope_hash": meta["scope_hash"], "snapshot_version": meta["snapshot_version"],
                        "filters": meta["filters"], "counts": {"documents": streamed["documents"], "assessments": streamed["assessments"],
                            "fully_assessed_documents": frozen["complete_documents"], **meta["metrics"]["coverage_detail"]},
                        "typed_counts": frozen["typed_counts"], "source_ids_sha256": frozen["source_ids_sha256"],
                        "assessment_ids_sha256": frozen["assessment_ids_sha256"], "snapshot": streamed,
                        "assessment_record_order": list(RECORD_ORDER),
                        "assessment_record_order_sha256": frozen["assessment_record_order_sha256"],
                        "identity_hash_order": "source_ids_sha256 and assessment_ids_sha256 hash sorted IDs followed by newline; assessment_record_order_sha256 hashes the declared source/unit order",
                        "human_appendix": {"profile": "briefing", "assessment_limit": 25, "source_limit": 25,
                            "totals_use_full_selection": True, "order": "indicator matches, uncertainty, boundaries, descriptive matches, other results"},
                        "xlsx": workbook_info, "interpretation_accuracy": None, "independent_human_labels": False,
                        "export_provenance": meta["export_provenance"],
                        "files": [{"file": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)} for path in artifacts]}
            manifest["unit_states"] = frozen["unit_states"]
            if meta.get("requested_criterion_decisions") is not None:
                manifest["counts"].update({"criterion_decisions_requested": meta["requested_criterion_decisions"],
                    "criterion_decisions_retained": streamed["assessments"],
                    "criterion_decisions_unresolved": max(0, meta["requested_criterion_decisions"] - streamed["assessments"])})
            manifest_path = directory / (output_prefix.name + ".manifest.json")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            if manifest_path.stat().st_size >= max_file_bytes:
                raise ValueError("Manifest exceeds its per-file byte limit")
            for path in [*artifacts, manifest_path]:
                shutil.move(str(path), output_prefix.parent / path.name)
            return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=["pdf", "html", "xlsx"], default=["pdf", "html", "xlsx"])
    parser.add_argument("--max-file-mb", type=int, default=99)
    args = parser.parse_args()
    result = export_run(args.data_dir, args.run_id, args.output_prefix, formats=tuple(args.formats), max_file_bytes=args.max_file_mb * 1_000_000)
    print(json.dumps({key: result[key] for key in ("run_id", "run_status", "counts", "files")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
