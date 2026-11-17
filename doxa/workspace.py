"""Indexed, bounded workspace queries shared by the UI and immutable exports.

No document bodies or complete finding collections are downloaded for a chart.
A cursor is bound to its query and revision; changed data invalidates the cursor.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .store import Store, utcnow


class WorkspaceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = None
    q: str = Field(default="", max_length=1000)
    date_basis: Literal["publication", "ingestion"] = "publication"
    from_date: str = ""
    to_date: str = ""
    lens_ids: list[str] = Field(default_factory=list, max_length=12)
    criterion_ids: list[str] = Field(default_factory=list, max_length=100)
    document_ids: list[str] = Field(default_factory=list, max_length=5000)
    authors: list[str] = Field(default_factory=list, max_length=100)
    document_types: list[str] = Field(default_factory=list, max_length=100)
    batch_ids: list[str] = Field(default_factory=list, max_length=100)
    review_status: str = ""
    speech_roles: list[str] = Field(default_factory=list, max_length=10)
    stance: str = ""
    results: list[str] = Field(default_factory=list, max_length=10)
    metric: str = "indicators"
    bin: Literal["month", "week", "year"] = "month"
    duplicate_policy: Literal["sources", "exact_content"] = "exact_content"


def unpack_document(row: dict) -> dict:
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json", "{}") or "{}")
    return result


def unpack_assessment(row: dict) -> dict:
    original = json.loads(row.get("payload_json") or "{}")
    correction = json.loads(row.get("review_json") or "{}")
    if isinstance(correction.get("resolved"), dict):
        correction = correction["resolved"]
    elif isinstance(correction.get("corrections"), dict):
        correction = correction["corrections"]
    result = {**original, **correction}
    for key, value in row.items():
        if key not in {"payload_json", "review_json"}:
            result[key] = value
    result["criterion"] = result.get("criterion_id", result.get("criterion", ""))
    result["category"] = result.get("criterion_id", result.get("category", ""))
    result["stance"] = result.get("speaker_stance", result.get("stance", "unclear"))
    refs = result.get("evidence_refs") or result.get("evidence") or []
    if refs and isinstance(refs[0], dict):
        for key in ("block_id", "start", "end", "quote", "locator"):
            if key in refs[0] and key not in result:
                result[key] = refs[0][key]
    result.setdefault("title", result.get("criterion_id", "Observation").replace("_", " "))
    result.setdefault("quote", "")
    result["is_indicator"] = result.get("criterion_kind") == "indicator" and result.get("result") == "meets_criterion"
    return result


class Workspace:
    def __init__(self, store: Store):
        self.store = store

    def revision(self, project_id: str) -> str:
        row = self.store.one("SELECT value_json FROM settings WHERE key=?", ["workspace_revision:" + project_id])
        return str(row["value_json"]) if row else "0"

    def touch(self, project_id: str) -> None:
        self.store.execute("INSERT INTO settings(key,value_json) VALUES(?,'1') ON CONFLICT(key) DO UPDATE SET value_json=CAST(CAST(value_json AS INTEGER)+1 AS TEXT)", ["workspace_revision:" + project_id])

    def require_query(self, query_id: str, *, current: bool = True) -> dict:
        row = self.store.one("SELECT * FROM v3_query_snapshots WHERE id=?", [query_id])
        if not row:
            raise HTTPException(404, "Saved workspace query not found")
        row["scope"] = json.loads(row.pop("query_json"))
        row["aggregates"] = json.loads(row.pop("aggregates_json"))
        if current and row["snapshot_version"] != self.revision(row["project_id"]):
            raise HTTPException(409, "Workspace changed. Refresh the query before paging or exporting.")
        return row

    def _document_scope(self, project_id: str, query: dict) -> tuple[list[str], list]:
        where, params = ["d.project_id=?"], [project_id]
        if query.get("document_ids"):
            where.append("d.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(query["document_ids"]))
        if query.get("run_id") and query.get("run_id") != "none":
            where.append("(EXISTS(SELECT 1 FROM v3_run_sources rs WHERE rs.run_id=? AND rs.document_id=d.id) OR EXISTS(SELECT 1 FROM tasks rt WHERE rt.run_id=? AND rt.document_id=d.id) OR EXISTS(SELECT 1 FROM v3_unit_statuses ru WHERE ru.run_id=? AND ru.document_id=d.id))")
            params.extend([query["run_id"]] * 3)
        for key, column in (("authors", "COALESCE(NULLIF(d.author,''),NULLIF(d.institution,''),'Unknown')"), ("document_types", "d.document_type")):
            values = query.get(key) or []
            if values:
                where.append(column + " IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(values))
        if query.get("batch_ids"):
            where.append("EXISTS(SELECT 1 FROM batch_members bm WHERE bm.document_id=d.id AND bm.batch_id IN (SELECT value FROM json_each(?)))")
            params.append(json.dumps(query["batch_ids"]))
        date = "d.created_at" if query.get("date_basis") == "ingestion" else "d.published_at"
        for key, op in (("from_date", ">="), ("to_date", "<=")):
            if query.get(key):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", query[key]):
                    raise HTTPException(422, "Dates must use YYYY-MM-DD")
                where.append(f"SUBSTR({date},1,10){op}?")
                params.append(query[key])
        if query.get("q"):
            terms = re.findall(r"\w+", query["q"])[:20]
            if self.store.fts and terms:
                for term in terms:
                    where.append("d.id IN (SELECT id FROM document_search WHERE project_id=? AND document_search MATCH ?)")
                    params.extend([project_id, '"' + term + '"'])
            else:
                where.append("(d.title LIKE ? OR d.author LIKE ?)")
                params.extend(["%" + query["q"] + "%"] * 2)
        # Keep independent publications/attributions; collapsing content is a view.
        if query.get("duplicate_policy") == "exact_content":
            scoped, scoped_params = self._document_scope(project_id, {**query, "duplicate_policy": "sources"})
            candidate = " AND ".join(scoped).replace("d.", "dd.")
            where.append("d.id=(SELECT MIN(dd.id) FROM documents dd WHERE dd.sha256=d.sha256 AND " + candidate + ")")
            params.extend(scoped_params)
        return where, params

    def _scope(self, project_id: str, query: dict, *, noteworthy: bool = True) -> tuple[str, list, bool]:
        where, params = self._document_scope(project_id, query)
        run = self.store.one("SELECT config_json FROM runs WHERE id=? AND project_id=?", [query.get("run_id"), project_id])
        config = json.loads(run["config_json"]) if run else {}
        modern = str(config.get("methodology_version", config.get("schema_version", "1"))).startswith("3") and config.get("provider") != "lexical"
        where.append("a.run_id=?")
        params.append(query.get("run_id"))
        for key, col in (("lens_ids", "a.lens_id"), ("criterion_ids", "a.criterion_id"), ("speech_roles", "a.speech_role"), ("results", "a.result")):
            values = query.get(key) or []
            if values:
                where.append(col + " IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(values))
        if query.get("review_status"):
            where.append("a.review_status=?")
            params.append(query["review_status"])
        if query.get("stance"):
            where.append("a.speaker_stance=?")
            params.append(query["stance"])
        if modern and noteworthy and not query.get("results"):
            where.append("a.result IN ('meets_criterion','uncertain')")
        return " AND ".join(where), params, modern

    @staticmethod
    def _table(modern: bool) -> str:
        if modern:
            return "v3_assessments a JOIN documents d ON d.id=a.document_id"
        return "(SELECT f.*,f.category AS criterion_id,'legacy' AS criterion_kind,'legacy_unclassified' AS result,'unknown' AS attribution,'unknown' AS speech_role,'unclear' AS speaker_stance,'unclear' AS document_stance,0 AS hypothetical,'{}' AS review_json FROM findings f) a JOIN documents d ON d.id=a.document_id"

    def document_ids(self, snapshot: dict) -> list[str]:
        where, params = self._document_scope(snapshot["project_id"], snapshot["scope"])
        return [r["id"] for r in self.store.query("SELECT d.id FROM documents d WHERE " + " AND ".join(where) + " ORDER BY d.id", params)]

    @staticmethod
    def _cursor(cursor: str | None, query_id: str, kind: str) -> str:
        if not cursor:
            return ""
        try:
            if len(cursor) > 512:
                raise ValueError
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if value["query"] != query_id or value["kind"] != kind:
                raise ValueError
            return str(value["last"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Cursor does not belong to this query") from None

    def page(self, snapshot: dict, kind: str, cursor: str | None = None, limit: int = 50) -> dict:
        limit = max(1, min(int(limit), 200))
        last = self._cursor(cursor, snapshot["id"], kind)
        if kind == "documents":
            where, params = self._document_scope(snapshot["project_id"], snapshot["scope"])
            clause = " AND ".join(where)
            total = self.store.one("SELECT COUNT(*) n FROM documents d WHERE " + clause, params)["n"]
            rows = self.store.query("SELECT d.* FROM documents d WHERE " + clause + " AND d.id>? ORDER BY d.id LIMIT ?", [*params, last, limit + 1])
            items = [unpack_document(r) for r in rows[:limit]]
        else:
            clause, params, modern = self._scope(snapshot["project_id"], snapshot["scope"])
            table = self._table(modern)
            total = self.store.one("SELECT COUNT(*) n FROM " + table + " WHERE " + clause, params)["n"]
            rows = self.store.query("SELECT a.*,d.title AS document_title,d.author,d.institution FROM " + table + " WHERE " + clause + " AND a.id>? ORDER BY a.id LIMIT ?", [*params, last, limit + 1])
            items = [unpack_assessment(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = base64.urlsafe_b64encode(json.dumps({"query": snapshot["id"], "kind": kind, "last": rows[limit - 1]["id"]}).encode()).decode().rstrip("=")
        return {"items": items, "total": total, "next_cursor": next_cursor, "query_hash": snapshot["scope_hash"], "snapshot_version": snapshot["snapshot_version"]}

    def aggregate(self, project_id: str, query: dict) -> dict:
        dw, dp = self._document_scope(project_id, query)
        dc = " AND ".join(dw)
        extent = self.store.one("SELECT COUNT(*) documents,COALESCE(SUM(d.word_count),0) total_words,COALESCE(SUM(d.page_count),0) total_pages FROM documents d WHERE " + dc, dp)
        clause, params, modern = self._scope(project_id, query)
        table = self._table(modern)
        indicator = "a.criterion_kind='indicator' AND a.result='meets_criterion'" if modern else "0"
        endorsed = indicator + " AND a.attribution='author' AND a.speaker_stance='supports' AND a.hypothetical=0"
        reported = indicator + " AND (a.attribution IN ('quoted_speaker','reported_speaker') OR a.hypothetical=1)"
        counts = self.store.one("SELECT COUNT(*) findings,COUNT(DISTINCT CASE WHEN " + indicator + " THEN d.id END) indicators,COUNT(DISTINCT CASE WHEN " + reported + " THEN d.id END) reported_indicators,COUNT(DISTINCT CASE WHEN " + endorsed + " THEN d.id END) endorsed_indicators,COALESCE(SUM(a.result='uncertain'),0) uncertain,COALESCE(SUM(a.review_status IN ('confirmed','revised')),0) reviewed FROM " + table + " WHERE " + clause, params)
        unit = self.store.one("SELECT COUNT(*) total,COALESCE(SUM(u.status='accepted'),0) done,COALESCE(SUM(u.characters),0) characters,COALESCE(SUM(CASE WHEN u.status='accepted' THEN u.characters ELSE 0 END),0) processed FROM v3_unit_statuses u JOIN documents d ON d.id=u.document_id WHERE u.run_id=? AND " + dc, [query.get("run_id"), *dp])
        runrow = self.store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [query.get("run_id"), project_id])
        run = dict(runrow) if runrow else None
        if run:
            for key in ["config_json", "progress_json", "usage_json", "model_profile_json", "errors_json"]:
                run[key[:-5]] = json.loads(run.pop(key) or "{}")
            frozen_sources = run["config"].pop("source_snapshot", [])
            run["source_documents"] = len(frozen_sources)
            run["source_snapshot_sha256"] = hashlib.sha256(json.dumps(frozen_sources, sort_keys=True).encode()).hexdigest()
            run["config"].pop("document_ids", None)
            if run["config"].get("verification_selection"):
                selection = run["config"]["verification_selection"]
                run["config"]["verification_selection"] = {key: value for key, value in selection.items() if key not in {"document_ids", "unit_ids"}}
        coverage = 100 * unit["done"] / unit["total"] if unit["total"] else 0
        detail = {"units_total": unit["total"], "units_done": unit["done"], "characters": unit["characters"], "processed_characters": unit["processed"], "basis": "contextual source units", "semantic_analysis_performed": modern}
        if not modern and run:
            progress = run.get("progress") or {}
            coverage = float(progress.get("percent", progress.get("coverage", 0)) or 0)
            detail = {**detail, "legacy": True, "notice": "Historical observations are unclassified under v3; coverage is not interpretation accuracy."}
        metrics = {**extent, **counts, "coverage": coverage, "coverage_detail": detail, "analysis_kind": "contextual_v3" if modern else ("legacy_unclassified" if run else "unassessed_corpus"), "legacy_findings": counts["findings"] if not modern else 0, "cost_usd": (run or {}).get("spent_usd", 0)}
        date = "d.created_at" if query.get("date_basis") == "ingestion" else "d.published_at"
        expr = f"SUBSTR({date},1,7)" if query.get("bin") != "year" else f"SUBSTR({date},1,4)"
        if query.get("bin") == "week":
            expr = f"strftime('%Y-W%W',{date})"
        from .lenses import get_lens
        from .trajectories import detect_spikes, wilson_interval

        if query.get("bin", "month") == "month":
            expr = "CASE WHEN SUBSTR(" + date + ",1,4) GLOB '[0-9][0-9][0-9][0-9]' AND SUBSTR(" + date + ",5,1)='-' AND CAST(SUBSTR(" + date + ",6,2) AS INTEGER) BETWEEN 1 AND 12 THEN SUBSTR(" + date + ",1,7) ELSE NULL END"

        def timeline_rows(sql, values):
            # Ordered SQLite/D1 placeholders reuse repeated scope parameters,
            # including the same source scope inside exact-content deduplication.
            pieces = sql.split("?")
            if len(pieces) != len(values) + 1:
                raise HTTPException(422, "Timeline parameter scope is invalid")
            unique, slots, output = [], {}, pieces[0]
            for index, value in enumerate(values):
                key = (type(value).__name__, json.dumps(value, sort_keys=True))
                if key not in slots:
                    unique.append(value)
                    slots[key] = len(unique)
                output += "?" + str(slots[key]) + pieces[index + 1]
            if len(unique) > 100:
                raise HTTPException(422, "Timeline scope exceeds the D1 parameter budget")
            return self.store.query(output, unique)

        configured = set()
        for name in (run or {}).get("config", {}).get("lens_ids", []):
            try:
                configured.add(get_lens(name)["id"] if name != "custom" else "custom")
            except ValueError:
                configured.add(name)
        configured.update((run or {}).get("model_profile", {}).get("rubric_versions", {}))
        selected_lenses = set(query.get("lens_ids") or configured)
        not_assessed = sorted(selected_lenses - configured) if modern else sorted(query.get("lens_ids") or [])
        metrics["not_assessed_lenses"] = not_assessed
        metrics["coverage_detail"]["not_assessed_lenses"] = not_assessed

        timeline_docs = timeline_rows("SELECT " + expr + " date,COUNT(*) documents FROM documents d WHERE " + dc + " GROUP BY date ORDER BY date", dp)
        timeline_a = {r["date"]: r for r in timeline_rows("SELECT " + expr + " date,COUNT(*) findings,COUNT(DISTINCT CASE WHEN " + indicator + " THEN d.id END) indicators,COUNT(DISTINCT CASE WHEN " + reported + " THEN d.id END) reported_indicators,COUNT(DISTINCT CASE WHEN " + endorsed + " THEN d.id END) endorsed_indicators,COUNT(DISTINCT CASE WHEN a.result='uncertain' THEN d.id END) unknown FROM " + table + " WHERE " + clause + " GROUP BY date", params)}
        summary, composition = {}, {}
        metric = query.get("metric") or "indicators"
        metric_column = {"indicators": "indicator_pos", "endorsed_indicators": "endorsed_pos", "reported_indicators": "reported_pos"}.get(metric)
        if modern:
            # Coverage considers every explicit selected-criterion outcome.
            # Display-only result/role/stance filters select the numerator.
            base_conditions, base_values = ["a.project_id=?", "a.run_id=?"], [project_id, query.get("run_id")]
            for key, column in (("lens_ids", "a.lens_id"), ("criterion_ids", "a.criterion_id")):
                if query.get(key):
                    base_conditions.append(column + " IN (SELECT value FROM json_each(?))")
                    base_values.append(json.dumps(query[key]))
            review_condition, review_values = "1", []
            if query.get("review_status"):
                review_condition, review_values = "a.review_status=?", [query["review_status"]]
            numerator_conditions, numerator_values = ["1"], []
            for key, column in (("speech_roles", "b.speech_role"), ("results", "b.result")):
                if query.get(key):
                    numerator_conditions.append(column + " IN (SELECT value FROM json_each(?))")
                    numerator_values.append(json.dumps(query[key]))
            if query.get("stance"):
                numerator_conditions.append("b.speaker_stance=?")
                numerator_values.append(query["stance"])
            raw_indicator = "b.criterion_kind='indicator' AND b.result='meets_criterion'"
            raw_endorsed = raw_indicator + " AND b.attribution='author' AND b.speaker_stance='supports' AND b.hypothetical=0"
            raw_reported = raw_indicator + " AND (b.attribution IN ('quoted_speaker','reported_speaker') OR b.hypothetical=1)"
            uncertain = "b.criterion_kind='indicator' AND b.result='uncertain'"
            if metric == "endorsed_indicators":
                uncertain = "b.criterion_kind='indicator' AND (b.result='uncertain' OR (b.result='meets_criterion' AND b.hypothetical=0 AND (b.attribution='unknown' OR (b.attribution='author' AND b.speaker_stance IN ('unclear','mixed','not_applicable')))))"
            elif metric == "reported_indicators":
                uncertain = "b.criterion_kind='indicator' AND (b.result='uncertain' OR (b.result='meets_criterion' AND b.hypothetical=0 AND b.attribution='unknown'))"
            cte = (
                "WITH source_scope AS (SELECT d.id,d.institution,d.source_url,d.document_type,d.word_count," + expr + " AS period FROM documents d WHERE " + dc + "),"
                "unit_doc AS (SELECT u.document_id,COUNT(*) unit_total,SUM(u.status='accepted') unit_done FROM v3_unit_statuses u JOIN source_scope s ON s.id=u.document_id WHERE u.run_id=? GROUP BY u.document_id),"
                "assessment_scope AS (SELECT a.document_id,a.unit_id,a.criterion_kind,a.result,a.attribution,a.speech_role,a.speaker_stance,a.hypothetical,a.review_status FROM v3_assessments a JOIN source_scope s ON s.id=a.document_id WHERE " + " AND ".join(base_conditions) + "),"
                "expected_doc AS (SELECT a.document_id,COUNT(*) expected_decisions FROM assessment_scope a GROUP BY a.document_id),"
                "assessment_base AS (SELECT a.* FROM assessment_scope a WHERE " + review_condition + "),"
                "decision_doc AS (SELECT b.document_id,COUNT(*) selected_decisions,COUNT(DISTINCT b.unit_id) decision_units,MAX(CASE WHEN " + uncertain + " THEN 1 ELSE 0 END) unknown_doc,MAX(b.criterion_kind='indicator') has_indicator FROM assessment_base b GROUP BY b.document_id),"
                "numerator_doc AS (SELECT b.document_id,MAX(CASE WHEN " + raw_indicator + " THEN 1 ELSE 0 END) indicator_pos,MAX(CASE WHEN " + raw_endorsed + " THEN 1 ELSE 0 END) endorsed_pos,MAX(CASE WHEN " + raw_reported + " THEN 1 ELSE 0 END) reported_pos FROM assessment_base b WHERE " + " AND ".join(numerator_conditions) + " GROUP BY b.document_id) "
            )
            values = [*dp, query.get("run_id"), *base_values, *review_values, *numerator_values]
            joins = " FROM source_scope s LEFT JOIN unit_doc u ON u.document_id=s.id LEFT JOIN expected_doc e ON e.document_id=s.id LEFT JOIN decision_doc c ON c.document_id=s.id LEFT JOIN numerator_doc p ON p.document_id=s.id"
            complete = "u.unit_total>0 AND u.unit_done=u.unit_total AND c.decision_units=u.unit_total AND c.selected_decisions=e.expected_decisions" + (" AND 0" if not_assessed else "")
            positive = "COALESCE(p." + (metric_column or "indicator_pos") + ",0)=1"
            known = "(" + complete + ") AND (COALESCE(c.unknown_doc,0)=0 OR (" + positive + "))"
            grouped = (
                "SELECT s.period date,SUM(CASE WHEN " + complete + " THEN 1 ELSE 0 END) complete_doc_n,"
                "SUM(CASE WHEN " + known + " THEN 1 ELSE 0 END) known_doc_n,"
                "SUM(CASE WHEN (" + complete + ") AND (" + positive + ") THEN 1 ELSE 0 END) positive_documents,"
                "SUM(CASE WHEN (" + complete + ") AND c.unknown_doc=1 AND NOT(" + positive + ") THEN 1 ELSE 0 END) uncertain_documents,"
                "SUM(COALESCE(u.unit_total,0)) total_units,SUM(COALESCE(u.unit_done,0)) done_units,MAX(COALESCE(c.has_indicator,0)) has_indicator"
                + joins + " GROUP BY s.period"
            )
            summary = {row["date"]: row for row in timeline_rows(cte + grouped, values)}
            url_tail = "CASE WHEN instr(s.source_url,'://')>0 THEN substr(s.source_url,instr(s.source_url,'://')+3) ELSE s.source_url END"
            host = "CASE WHEN instr((" + url_tail + "),'/')>0 THEN substr((" + url_tail + "),1,instr((" + url_tail + "),'/')-1) ELSE (" + url_tail + ") END"
            origin = "COALESCE(NULLIF(s.institution,''),NULLIF((" + host + "),''),'Unknown source')"
            length = "CASE WHEN s.word_count<=1000 THEN 'short' WHEN s.word_count<=5000 THEN 'medium' ELSE 'long' END"
            mix_sql = "SELECT s.period date,json_array(" + origin + ",s.document_type," + length + ") stratum,COUNT(*) n" + joins + " WHERE " + known + " GROUP BY s.period,stratum"
            for row in timeline_rows(cte + mix_sql, values):
                composition.setdefault(row["date"], {})[row["stratum"]] = row["n"]

        timeline = []
        one_lens = modern and len(selected_lenses) == 1 and not not_assessed
        for period in timeline_docs:
            raw_date = period["date"]
            item = {**period, **timeline_a.get(raw_date, {}), "date": raw_date or "undated"}
            data = summary.get(raw_date, {})
            complete_n, known_n = data.get("complete_doc_n", 0), data.get("known_doc_n", 0)
            done, total = data.get("done_units", 0), data.get("total_units", 0)
            input_coverage = 100 * done / total if total else 0
            complete_coverage = 100 * complete_n / item["documents"] if item["documents"] else 0
            item.update({"complete_doc_n": complete_n, "eligible_documents": known_n, "analyzed_documents": complete_n,
                         "positive_documents": data.get("positive_documents", 0), "uncertain_documents": data.get("uncertain_documents", 0),
                         "coverage": min(input_coverage, complete_coverage), "processing_coverage": input_coverage,
                         "label_coverage": 100 * known_n / complete_n if complete_n else 0,
                         "rate_metric": metric, "composition": composition.get(raw_date, {}), "not_assessed_lenses": not_assessed,
                         "rate_basis": "Distinct completely assessed sources with determinate selected-criterion outcomes; uncertainty remains separate."})
            enabled = one_lens and data.get("has_indicator") and metric_column is not None
            item["rate"] = item["positive_documents"] / known_n if enabled and known_n else None
            item["lower"], item["upper"] = wilson_interval(item["positive_documents"], known_n) if enabled else (None, None)
            item["spike_candidate"] = False
            timeline.append(item)
        if modern:
            fully = sum(row.get("complete_doc_n", 0) for row in summary.values())
            metrics["coverage"] = min(metrics["coverage"], 100 * fully / extent["documents"]) if extent["documents"] else 0
            metrics["coverage_detail"]["complete_documents"] = fully
            metrics["coverage_detail"]["selected_lens_scope_complete"] = not not_assessed
        if not_assessed:
            metrics["coverage"] = 0
        if one_lens and metric_column and query.get("bin", "month") == "month" and query.get("date_basis", "publication") == "publication":
            detection = detect_spikes(timeline)
            tests = {row["date"]: row for row in detection["tests"]}
            for item in timeline:
                item["spike_test"] = tests.get(item["date"])
                item["spike_candidate"] = bool(item["spike_test"] and item["spike_test"]["status"] == "review_candidate")
                item["spike_policy"] = detection["policy"]
        else:
            for item in timeline:
                item["spike_disabled_reason"] = "Choose one assessed indicator lens, a document-rate metric, and publication months."
        matrix = self.store.query("SELECT d.author author,a.lens_id,a.criterion_id,a.criterion_kind,COUNT(*) count,COUNT(DISTINCT d.id) documents FROM " + table + " WHERE " + clause + " GROUP BY d.author,a.lens_id,a.criterion_id,a.criterion_kind ORDER BY count DESC LIMIT 200", params)
        author_units = {row["author"]: row for row in self.store.query("SELECT d.author,COUNT(*) total,SUM(u.status='accepted') done FROM v3_unit_statuses u JOIN documents d ON d.id=u.document_id WHERE u.run_id=? AND " + dc + " GROUP BY d.author", [query.get("run_id"), *dp])}
        author_extent = {row["author"]: row for row in self.store.query("SELECT d.author,SUM(d.word_count) words,COUNT(*) sources FROM documents d WHERE " + dc + " GROUP BY d.author", dp)}
        for cell in matrix:
            units = author_units.get(cell["author"], {})
            cell["coverage"] = 100 * (units.get("done") or 0) / units["total"] if units.get("total") else 0
            if not modern or detail.get("not_assessed_lenses"):
                cell["coverage"] = 0
            extent = author_extent.get(cell["author"], {})
            cell["words"] = extent.get("words", 0)
            cell["sources"] = extent.get("sources", 0)
            cell["coverage_basis"] = "selected author's contextual source units"
            cell["observations_per_10000_words"] = 10000 * cell["count"] / cell["words"] if cell["coverage"] == 100 and cell["words"] else None
        facets = {}
        for name, column in (("authors", "author"), ("document_types", "document_type")):
            facets[name] = self.store.query("SELECT d." + column + " name,COUNT(*) count FROM documents d WHERE " + dc + " GROUP BY d." + column + " ORDER BY count DESC,name LIMIT 200", dp)
        facets["batches"] = self.store.query("SELECT b.id,b.name,COUNT(bm.document_id) count FROM batches b LEFT JOIN batch_members bm ON bm.batch_id=b.id WHERE b.project_id=? GROUP BY b.id ORDER BY b.created_at DESC LIMIT 100", [project_id])
        facets["criteria"] = self.store.query("SELECT a.criterion_id name,a.lens_id,COUNT(*) count FROM " + table + " WHERE " + clause + " GROUP BY a.criterion_id,a.lens_id ORDER BY count DESC LIMIT 100", params)
        facets["speech_roles"] = [{"name": name} for name in ("author_assertion", "attributed_quotation", "reported_speech", "unknown")]
        facets["review_states"] = [{"name": name} for name in ("unreviewed", "confirmed", "revised", "rejected")]
        return {"metrics": metrics, "facets": facets, "timeline": timeline, "matrix": matrix, "run": run}

    def create(self, project_id: str, query: WorkspaceQuery) -> dict:
        if not self.store.one("SELECT id FROM projects WHERE id=?", [project_id]):
            raise HTTPException(404, "Project not found")
        scope = query.model_dump()
        if not scope.get("run_id"):
            latest = self.store.one("SELECT id FROM runs WHERE project_id=? ORDER BY CASE WHEN COALESCE(json_extract(config_json,'$.verification_of'),'')='' THEN 0 ELSE 1 END,created_at DESC LIMIT 1", [project_id])
            scope["run_id"] = latest["id"] if latest else None
        if scope["run_id"] and scope["run_id"] != "none" and not self.store.one("SELECT id FROM runs WHERE id=? AND project_id=?", [scope["run_id"], project_id]):
            raise HTTPException(404, "Run does not belong to this project")
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        # A bounded query cache saves repeated aggregates during long jobs.
        with self.store.lock:
            revision = self.revision(project_id)
            cached = self.store.one("SELECT id FROM v3_query_snapshots WHERE project_id=? AND scope_hash=? AND snapshot_version=? ORDER BY created_at DESC LIMIT 1", [project_id, digest, revision])
            if cached:
                return self.envelope(self.require_query(cached["id"]))
            aggregates = self.aggregate(project_id, scope)
            snapshot = {"id": uuid.uuid4().hex, "project_id": project_id, "scope_hash": digest, "snapshot_version": revision, "scope": scope, "aggregates": aggregates}
            self.store.execute("INSERT INTO v3_query_snapshots(id,project_id,scope_hash,snapshot_version,query_json,aggregates_json,created_at) VALUES(?,?,?,?,?,?,?)", [snapshot["id"], project_id, digest, revision, json.dumps(scope), json.dumps(aggregates), utcnow()])
            return self.envelope(snapshot)

    def envelope(self, snapshot: dict) -> dict:
        return {"query_id": snapshot["id"], "scope_hash": snapshot["scope_hash"], "snapshot_version": snapshot["snapshot_version"], "scope": snapshot["scope"], **snapshot["aggregates"], "documents": self.page(snapshot, "documents"), "findings": self.page(snapshot, "findings")}
