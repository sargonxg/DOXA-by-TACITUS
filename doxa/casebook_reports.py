"""Offline publications from immutable, operator-selected source captures."""
from __future__ import annotations

from collections import Counter
import csv
import gzip
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit

from .recorded_export import compact, file_hash
from .report_source import MAX_ARTIFACT_BYTES, _offline_pdf
from .reports import _csv_safe


def _text(value):
    return escape(str("Not recorded" if value is None or value == "" else value))


def _outcomes(case):
    frozen = case["frozen_snapshot"]
    original = frozen.get("assessment") or {}
    reviewed = (frozen.get("review_overlay") or {}).get("resolved") or {}
    return {"original_result": original.get("result"), "review_result": reviewed.get("result"),
            "projected_result": case.get("result"), "original_criterion_kind": original.get("criterion_kind")}


def _link(value):
    """Citations are clickable without admitting active URL schemes."""
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return '<a href="' + escape(str(value), quote=True) + '">' + _text(value) + '</a>'
    except ValueError:
        pass
    return _text(value)


def _case_html(case, index):
    frozen = case["frozen_snapshot"]
    source = frozen["source"]
    credit = {**(source.get("metadata") or {}), **source}
    translations = "; ".join(person.get("name", "") for person in (source.get("metadata") or {}).get("translators", []) if isinstance(person, dict))
    original = frozen.get("assessment") or {}
    criterion = frozen.get("criterion") or {}
    refs = original.get("evidence_refs") or []
    blocks = {block["id"]: block for block in frozen.get("blocks", [])}
    quotes = "".join('<blockquote>' + _text(ref.get("quote") or blocks.get(ref.get("block_id"), {}).get("text", "")[ref.get("start", 0):ref.get("end", 0)])
                     + '</blockquote><p class="small">Exact source anchor: ' + _text(ref.get("block_id")) + ' · characters '
                     + _text(ref.get("start")) + '–' + _text(ref.get("end")) + '</p>' for ref in refs)
    if not refs:
        quotes = "".join('<blockquote>' + _text(block.get("text")) + '</blockquote>' for block in frozen.get("blocks", []))
    outcome = original.get("result") or "Unresolved source unit — no assessment exists"
    details = [("Original model result", outcome), ("Criterion kind", original.get("criterion_kind")),
               ("Source / represented speaker", original.get("speaker")), ("Attribution", original.get("attribution")),
               ("Speech role", original.get("speech_role")), ("Document stance", original.get("document_stance")),
               ("Proposition assessed", original.get("proposition")), ("Model explanation", original.get("explanation")),
               ("Alternative / uncertainty", original.get("alternative"))]
    content = "".join('<dt>' + _text(label) + '</dt><dd>' + _text(value) + '</dd>' for label, value in details)
    condition = '<h3>Frozen framework condition</h3><p>' + _text(criterion.get("lens_name") or criterion.get("lens_id")) + ' / ' + _text(criterion.get("id") or original.get("criterion_id")) + '</p><p>' + _text(criterion.get("description") or criterion.get("definition") or criterion.get("text")) + '</p><p class="small">Framework source: ' + _link(criterion.get("source_url")) + '</p>'
    review = frozen.get("review_overlay") or {}
    if review:
        resolved = review.get("resolved") or {}
        review_text = '<p>Captured review overlay: ' + _text(resolved.get("result")) + '. ' + _text(resolved.get("explanation")) + '</p><p class="small">Review actor: ' + _text(review.get("actor_id")) + '. Original model output above is preserved separately.</p>'
    else:
        review_text = '<p>No captured review overlay. This is not independent human adjudication.</p>'
    unit = frozen.get("unit") or {}
    unresolved = '<p class="notice">Recorded unit status: ' + _text(unit.get("status")) + '. Missing decisions are unresolved, never negative findings. The input archive preserves the actual unit and validation record.</p>' if not original else ''
    return ('<section class="case"><p class="eyebrow">CASE ' + str(index) + ' · ' + _text(case.get("kind")) + '</p><h2>'
            + _text(source.get("title")) + '</h2><p>' + _text(source.get("author")) + ' · ' + _text(source.get("published_at"))
            + '</p>' + unresolved + quotes + '<dl>' + content + '</dl>' + condition + '<h3>Review, separate from the original model</h3>'
            + review_text + '<h3>Operator selection and editorial guidance</h3><p>' + _text(frozen.get("selection_rationale"))
            + '</p><p>' + _text(frozen.get("editorial_note")) + '</p><p class="small">Operator-supplied guidance is self-declared; it is not a human reference label or an accuracy score.</p>'
            + '<h3>Source credit and reproducibility</h3><p class="small">' + _link(source.get("source_url")) + '<br>' + _text(source.get("license"))
            + '<br>Attribution: ' + _text(credit.get("attribution")) + '<br>Rights URL: ' + _link(credit.get("license_url")) + '<br>Translation credit: ' + _text(translations)
            + '<br>Source SHA-256: ' + _text(source.get("sha256")) + '<br>Case capture SHA-256: ' + _text(case.get("capture_sha256")) + '</p></section>')


def _html(book, profile):
    counts = Counter((case["frozen_snapshot"].get("assessment") or {}).get("result", "unresolved") for case in book["cases"])
    method = book.get("method_provenance") or {}
    recorded_profile = method.get("profile") or {}
    method_fields = [("Recorded run", method.get("run_id") or book.get("run_id")), ("Recorded run status", method.get("run_status")),
                     ("Provider / model", " / ".join(str(method.get(key) or "Not recorded") for key in ("provider", "model"))),
                     ("Methodology version", method.get("methodology_version")), ("Prompt version", recorded_profile.get("prompt_version")),
                     ("Model revision", method.get("model_revision") or recorded_profile.get("revision"))]
    method_html = '<h2>Frozen analysis provenance</h2><dl>' + ''.join('<dt>' + _text(label) + '</dt><dd>' + _text(value) + '</dd>' for label, value in method_fields) + '</dl>'
    css = """@page{size:A4;margin:18mm}*{box-sizing:border-box}body{font:15px/1.6 Arial,sans-serif;color:#173044;max-width:920px;margin:auto;padding:30px;background:#fff}h1{font-size:40px;line-height:1.12;color:#10334d}h2{font-size:27px;line-height:1.2}h3{margin-bottom:4px;font-size:17px;color:#126a71}.eyebrow{font-size:11px;letter-spacing:2px;color:#126a71;font-weight:bold}.notice{background:#eaf4f2;border-left:4px solid #126a71;padding:15px}.small{font-size:11px;color:#4c6371;overflow-wrap:anywhere}blockquote{margin:20px 0;padding:16px 20px;background:#f0f5f8;border-left:4px solid #ce9f45;font-size:17px}dl{display:grid;grid-template-columns:170px 1fr;gap:8px 16px}dt{font-weight:bold}dd{margin:0;overflow-wrap:anywhere}.case{page-break-before:always;padding-top:20px}a{color:#126a71}@media(max-width:550px){body{padding:18px}h1{font-size:32px}dl{display:block}dd{margin-bottom:12px}}"""
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
            + _text(book["title"]) + '</title><style>' + css + '</style><body><p class="eyebrow">DOXA / TACITUS · CURATED CASEBOOK</p><h1>'
            + _text(book["title"]) + '</h1><p>' + _text(book.get("selection_rationale")) + '</p><p class="notice">Illustrative, operator-selected cases. Selection is not a representative sample. Typed outcomes describe the application of a specified framework; they do not establish author identity, intent, unfairness, or interpretation accuracy. No model calls were made to create this publication.</p><h2>What this publication contains</h2><p>'
            + str(len(book["cases"])) + ' frozen cases · ' + _text(profile) + ' audience · draft revision ' + str(book["revision"])
            + '</p><p>' + ' · '.join(_text(key) + ': ' + str(value) for key, value in sorted(counts.items()))
            + '</p><p class="small">These are selected assessment/workload counts, not bias rates. Missing explanations remain explicitly unrecorded. Full source captures, original model outputs, validation units, review overlays and editorial notes are retained in the downloadable snapshot.</p><p class="small">Query '
            + _text(book["query_id"]) + '<br>Scope ' + _text(book["scope_hash"]) + '<br>Workspace snapshot ' + _text(book["snapshot_version"])
            + '</p>' + method_html + ''.join(_case_html(case, index) for index, case in enumerate(book["cases"], 1)) + '</body></html>')


def render_casebook(book: dict, output: Path, format: str, profile: str) -> list[dict]:
    """No live database/model access; every output uses the submitted draft copy."""
    if not book.get("cases"):
        raise ValueError("Pin at least one recorded case before preparing a casebook")
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "casebook.snapshot.json.gz"
    with gzip.open(snapshot, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(book, stream, ensure_ascii=False, separators=(",", ":"))
    artifacts = [("snapshot", snapshot, "application/gzip")]
    if format in {"html", "pdf"}:
        path = output / ("casebook." + format)
        html = _html(book, profile)
        if format == "pdf":
            _offline_pdf(html, path)
        else:
            path.write_text(html, encoding="utf-8")
        artifacts.append((format, path, "application/pdf" if format == "pdf" else "text/html; charset=utf-8"))
    elif format == "xlsx":
        import xlsxwriter
        path = output / "casebook.xlsx"
        workbook = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
        positions = {}
        def sheet(name):
            value = workbook.add_worksheet(name)
            positions[name] = 0
            return value
        def append(target, values):
            index = positions[target.name]
            for column, value in enumerate(values):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    target.write_number(index, column, value)
                else:
                    target.write_string(index, column, str("" if value is None else value))
            positions[target.name] += 1
        overview = sheet("Overview")
        for key in ("title", "selection_rationale", "query_id", "scope_hash", "snapshot_version", "revision"):
            append(overview, [key, book.get(key)])
        method = book.get("method_provenance") or {}
        for key in ("run_id", "run_status", "methodology_version", "provider", "model", "model_revision", "method_fingerprint"):
            append(overview, [key, method.get(key)])
        append(overview, ["prompt_version", (method.get("profile") or {}).get("prompt_version")])
        append(overview, ["Interpretation accuracy", "Not established; illustrative operator selection"])
        cases = sheet("Cases")
        fields = ("id", "kind", "document_id", "unit_id", "assessment_id", "original_result", "review_result", "projected_result", "original_criterion_kind", "capture_sha256", "selection_rationale", "editorial_note")
        append(cases, fields)
        raw = sheet("Full frozen captures")
        append(raw, ["Case ID", "Part", "JSON text (concatenate parts in order)"])
        sources = sheet("Source credits")
        source_fields = ("id", "title", "author", "published_at", "source_url", "license", "sha256", "attribution", "license_url", "translation_credit")
        append(sources, source_fields)
        seen = set()
        for case in book["cases"]:
            values = {**case, **_outcomes(case)}
            append(cases, [values.get(key) if key not in {"selection_rationale", "editorial_note"} else case["frozen_snapshot"].get(key) for key in fields])
            value = compact(case["frozen_snapshot"])
            for offset in range(0, len(value), 30000):
                append(raw, [case["id"], offset // 30000 + 1, value[offset:offset + 30000]])
            source = case["frozen_snapshot"]["source"]
            if source["id"] not in seen:
                metadata = source.get("metadata") or {}
                credited = {**metadata, **source, "translation_credit": "; ".join(person.get("name", "") for person in metadata.get("translators", []) if isinstance(person, dict))}
                append(sources, [credited.get(key) for key in source_fields])
                seen.add(source["id"])
        workbook.close()
        artifacts.append((format, path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    else:
        path = output / ("casebook." + format + ".gz")
        with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as stream:
            if format == "jsonl":
                stream.write(compact({"record_type": "manifest", **{key: value for key, value in book.items() if key != "cases"}}) + "\n")
                for case in book["cases"]:
                    stream.write(compact({"record_type": "case", **case}) + "\n")
            elif format == "csv":
                writer = csv.writer(stream)
                fields = ("id", "kind", "document_id", "assessment_id", "original_result", "review_result", "projected_result", "original_criterion_kind")
                writer.writerow([*fields, "record_json"])
                for case in book["cases"]:
                    values = {**case, **_outcomes(case)}
                    writer.writerow([_csv_safe(values.get(key)) for key in fields] + [_csv_safe(compact(case))])
            else:
                raise ValueError("Unsupported casebook format")
        artifacts.append((format, path, "application/gzip"))
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "doxa-casebook-report-1", "casebook_id": book["id"], "casebook_revision": book["revision"],
                        "query_id": book["query_id"], "scope_hash": book["scope_hash"], "snapshot_version": book["snapshot_version"],
                        "method_provenance": book.get("method_provenance"),
                        "cases": len(book["cases"]), "original_outcome_counts": dict(Counter(_outcomes(case)["original_result"] or "unresolved" for case in book["cases"])),
                        "interpretation_accuracy": None, "inference_requests_made": 0,
                        "files": [{"format": kind, "filename": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)} for kind, path, _ in artifacts]}, indent=2), encoding="utf-8")
    artifacts.append(("manifest", manifest, "application/json"))
    result = []
    for kind, path, content_type in artifacts:
        if not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Casebook artifact exceeds its bounded size")
        result.append({"format": kind, "filename": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path), "content_type": content_type, "path": str(path)})
    return result
