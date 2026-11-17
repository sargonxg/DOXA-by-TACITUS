#!/usr/bin/env python3
"""Freeze real assessment records and build an annotated, offline reading guide.

This command makes no inference calls and never marks its AI-authored commentary
as independent human review. Read-only DB access leaves application state intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUTHORSHIP = "AI-authored walkthrough guidance; not independent human adjudication or an interpretation-accuracy benchmark."
CONFIG_FIELDS = ("provider", "model", "lens_ids", "question", "persona", "methodology_version", "schema_version",
                 "quantization", "model_revision", "unit_max_chars", "context_sentences", "pipeline_version",
                 "unit_builder_version", "learning_snapshot_id", "verification_of", "verification_selection")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_path(root: Path, key: str) -> Path:
    path = (root / "objects" / key).resolve()
    if not path.is_relative_to((root / "objects").resolve()):
        raise ValueError("Source object escapes its private workspace")
    return path


def unpack(row: dict) -> dict:
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(result.pop(key) or "{}")
    return result


def choose_cases(rows: list[dict], limit: int) -> list[dict]:
    """Select illustrative variety, never a statistical/prevalence sample."""
    selected, seen = [], set()
    order = sorted(rows, key=lambda row: (row["lens_id"] != "political-framing", row["result"] != "meets_criterion",
                                          row["criterion_kind"] != "indicator", row["document_id"], row["id"]))
    for key_fields in (("document_id",), ("criterion_id",), ("id",)):
        groups = {tuple(row[key] for key in key_fields) for row in selected}
        for row in order:
            group = tuple(row[key] for key in key_fields)
            if row["id"] in seen or group in groups:
                continue
            selected.append(row)
            seen.add(row["id"])
            groups.add(group)
            if len(selected) == limit:
                return selected
    return selected


def freeze_run(data_dir: Path, run_id: str, *, limit: int = 4, assessment_ids: list[str] | None = None,
               allow_active: bool = False) -> dict:
    """Read one consistent DB snapshot, then freeze exact immutable source blocks."""
    from doxa.lenses import get_lens
    data_dir = data_dir.resolve()
    database = data_dir / "doxa.sqlite3"
    if not database.is_file():
        raise ValueError("The specified local workspace database does not exist")
    connection = sqlite3.connect("file:" + quote(str(database), safe="/") + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM runs WHERE id=?", [run_id]).fetchone()
        if row is None:
            raise ValueError("Recorded run not found")
        run = unpack(dict(row))
        if str(run.get("config", {}).get("methodology_version")) != "3.0":
            raise ValueError("Use an actual methodology-v3 run for these walkthroughs")
        if run["status"] in {"running", "queued", "importing"} and not allow_active:
            raise ValueError("Wait for work to settle, or explicitly --capture-unfinished with its unfinished status")
        records = [dict(row) for row in connection.execute(
            "SELECT * FROM v3_assessments WHERE run_id=? AND result IN ('meets_criterion','uncertain') ORDER BY id", [run_id])]
        if assessment_ids:
            by_id = {row["id"]: row for row in records}
            if len(set(assessment_ids)) != len(assessment_ids) or any(identity not in by_id for identity in assessment_ids):
                raise ValueError("Every selected ID must be a unique noteworthy assessment from this run")
            selected = [by_id[identity] for identity in assessment_ids]
        else:
            selected = choose_cases(records, limit)
        if not selected:
            raise ValueError("No recorded noteworthy assessments are available; no example has been invented")
        sources = []
        frozen_selection = {item["id"]: item["sha256"] for item in run["config"].get("source_snapshot", [])}
        for identity in dict.fromkeys(item["document_id"] for item in selected):
            row = connection.execute("SELECT * FROM documents WHERE id=? AND project_id=?", [identity, run["project_id"]]).fetchone()
            if row is None or frozen_selection.get(identity) != row["sha256"]:
                raise ValueError("A selected source is unavailable or differs from the run's frozen source version")
            doc = dict(row)
            blocks = json.loads(source_path(data_dir, doc["blocks_key"]).read_text())
            meta = json.loads(doc.pop("metadata_json") or "{}")
            for key in ("object_key", "blocks_key", "error"):
                doc.pop(key, None)
            doc.update({"metadata": meta, "blocks": blocks, "frozen_blocks_sha256": digest(blocks)})
            sources.append(doc)
        unit_rows = [dict(row) for row in connection.execute(
            "SELECT status,COUNT(*) count,COALESCE(SUM(characters),0) characters FROM v3_unit_statuses WHERE run_id=? GROUP BY status", [run_id])]
        decisions = [dict(row) for row in connection.execute(
            "SELECT criterion_kind,result,COUNT(*) count FROM v3_assessments WHERE run_id=? GROUP BY criterion_kind,result", [run_id])]
        project = dict(connection.execute("SELECT * FROM projects WHERE id=?", [run["project_id"]]).fetchone())
        assessments = []
        definitions = {}
        for row in selected:
            value = json.loads(row["payload_json"])
            value.update({key: row[key] for key in ("id", "project_id", "run_id", "document_id", "unit_id", "lens_id", "criterion_id", "criterion_kind", "review_status", "review_note")})
            # Preserve raw output. Existing review overlays remain separately labeled.
            value["recorded_review_overlay"] = json.loads(row.get("review_json") or "{}")
            unit_state = connection.execute("SELECT status FROM v3_unit_statuses WHERE run_id=? AND document_id=? AND unit_id=?",
                                            [run_id, row["document_id"], row["unit_id"]]).fetchone()
            value["recorded_unit_status"] = unit_state["status"] if unit_state else "not supplied"
            assessments.append(value)
            pack = get_lens(row["lens_id"])
            criterion = next(item for item in pack["criteria"] if item["id"] == row["criterion_id"])
            expected = run.get("model_profile", {}).get("rubric_versions", {}).get(pack["id"])
            if expected and expected != pack["version"]:
                raise ValueError("The current lens definition differs from the recorded run; provide a frozen snapshot")
            definitions[row["lens_id"] + "/" + row["criterion_id"]] = {"lens": pack["name"], "version": pack["version"],
                "criterion": criterion, "scope": pack.get("scope", ""), "source_url": pack.get("source_url", "")}
        public_run = {key: value for key, value in run.items() if key not in {"config", "errors"}}
        public_run["config"] = {key: run["config"][key] for key in CONFIG_FIELDS if key in run["config"]}
        public_run["source_selection"] = {"documents": len(frozen_selection), "sha256": digest(run["config"].get("source_snapshot", []))}
        result = {"schema_version": "doxa-annotated-guide-1", "generated_at": datetime.now(timezone.utc).isoformat(),
                  "project": project, "run": public_run, "documents": sources, "assessments": assessments,
                  "criterion_definitions": definitions, "unit_states": unit_rows, "decision_counts": decisions,
                  "selection_notice": "Illustrative recorded cases selected for reading guidance; not a representative sample or a new model run.",
                  "commentary_authorship": AUTHORSHIP, "independent_human_labels": False, "interpretation_accuracy": None}
        if run["config"].get("verification_selection"):
            result["selection_notice"] += " This is a frozen verification-unit subset; coverage refers to that subset, not complete source documents or the whole primary run."
        if any(doc.get("metadata", {}).get("benchmark_selection", {}).get("sampled") for doc in sources):
            result["selection_notice"] += " Source passages were sampled in the recorded run; no full-document completeness claim is made."
        result["source_anchor_audit"] = audit_snapshot(result)
        return result
    finally:
        connection.rollback()
        connection.close()


def audit_snapshot(snapshot: dict) -> dict:
    if snapshot.get("snapshot_sha256") and snapshot["snapshot_sha256"] != digest({key: value for key, value in snapshot.items() if key != "snapshot_sha256"}):
        raise ValueError("Frozen guide failed its immutable snapshot checksum")
    if snapshot.get("schema_version") != "doxa-annotated-guide-1":
        raise ValueError("Unsupported walkthrough snapshot format")
    sources = {doc["id"]: doc for doc in snapshot["documents"]}
    checked = 0
    for source in sources.values():
        if digest(source["blocks"]) != source["frozen_blocks_sha256"]:
            raise ValueError("Frozen source blocks failed their content checksum")
    for assessment in snapshot["assessments"]:
        if assessment["run_id"] != snapshot["run"]["id"] or assessment["document_id"] not in sources:
            raise ValueError("Assessment scope differs from the frozen run/source selection")
        source = sources[assessment["document_id"]]
        if assessment.get("source_hash") not in {None, source["sha256"]}:
            raise ValueError("Assessment source hash differs from the frozen source version")
        blocks = {block["id"]: block["text"] for block in source["blocks"]}
        references = assessment.get("evidence_refs") or []
        if not references:
            raise ValueError("An assessment has no source-anchored evidence")
        for ref in [*references, assessment]:
            if ref.get("document_id") not in {None, assessment["document_id"]}:
                raise ValueError("Evidence references another source document")
            text = blocks.get(ref.get("block_id"))
            start, end = ref.get("start"), ref.get("end")
            if not isinstance(text, str) or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                raise ValueError("Evidence offsets do not identify an exact source span")
            if ref.get("quote") is not None and text[start:end] != ref["quote"]:
                raise ValueError("Recorded quote differs from its exact source span")
            checked += 1
    return {"valid": True, "checked_references_and_primary_spans": checked, "offset_unit": "unicode_code_points",
            "notice": "Source anchoring is verified. Semantic correctness remains unmeasured."}


def commentary(assessment: dict) -> dict:
    """Declared AI-authored guidance; never rewritten as a model finding/review."""
    kind, result = assessment.get("criterion_kind"), assessment.get("result")
    if result in {"does_not_meet", "not_applicable"}:
        capture = "The recorded model did not identify this criterion as met in the assessed passage. This result is limited to that source unit and method; it does not establish absence across a collection."
    elif result == "uncertain":
        capture = "The recorded assessment is unresolved. Keep it as a review question; the available context does not justify forcing a positive or negative verdict."
    elif kind == "boundary":
        capture = "A boundary criterion identifies a limit on classification. Meeting it is not a prejudice signal; it can explain why political expression needs to remain distinct from hostility to people."
    elif kind == "descriptive":
        capture = "This records a textual choice: how a problem is framed, a value is emphasized, a cause is assigned or a response is made plausible. A descriptive match alone does not establish prejudice or hidden intent."
    else:
        capture = "This is a framework-specific indicator for contextual review. Check the criterion's target, conditions and represented speaker before treating it as evidence of author-endorsed hostility."
    if assessment.get("attribution") in {"quoted_speaker", "reported_speaker"}:
        attribution = "The model attributes the proposition to a represented speaker. The publisher's act of quoting or reporting it is separate from that speaker's stance. Read the document's own response and surrounding source."
    elif assessment.get("attribution") == "author":
        attribution = "The model assigns this statement to the document's authorial voice. Check that it is not an embedded quotation, reported allegation or hypothetical example. This does not infer the author's private beliefs."
    else:
        attribution = "Attribution is unresolved. Retain that uncertainty and inspect the source before attaching the proposition to an author."
    if assessment.get("hypothetical"):
        attribution += " This is marked hypothetical; preserve the training/example context."
    return {"authorship": AUTHORSHIP, "what_is_captured": capture, "attribution_check": attribution,
            "bias_commentary": "To argue political bias, state a comparator: alternative framing, omitted relevant evidence, different actors or a matched source collection. The passage supports an inspectable reading, not a universal ideology score.",
            "review_question": "Which exact words support the criterion? Does the full context, attribution or a competing reading change the assessment?"}


def reading_note(snapshot: dict, assessment: dict) -> dict:
    note = commentary(assessment)
    overrides = (snapshot.get("commentary_notes") or {}).get(assessment["id"], {})
    allowed = {"what_is_captured", "attribution_check", "bias_commentary", "review_question"}
    if set(overrides) - allowed or any(not isinstance(value, str) or len(value) > 5000 for value in overrides.values()):
        raise ValueError("Commentary notes must use bounded declared reading-guide fields")
    return {**note, **overrides, "authorship": AUTHORSHIP}


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def inline_diagram(name: str) -> str:
    svg = (ROOT / "docs" / "images" / (name + ".svg")).read_text().replace("<svg ", '<svg class="diagram" ', 1)
    for identity in ("title", "desc", "arrow"):
        svg = svg.replace('id="' + identity + '"', 'id="' + name + '-' + identity + '"')
    return svg.replace('aria-labelledby="title desc"', f'aria-labelledby="{name}-title {name}-desc"').replace('url(#arrow)', 'url(#' + name + '-arrow)')


def source_link(value: str, label: str = "Original source ↗") -> str:
    parts = urlsplit(value or "")
    return f'<a href="{e(value)}">{e(label)}</a>' if parts.scheme in {"http", "https"} and parts.netloc else e(value)


def facts(rows) -> str:
    return '<dl class="facts">' + ''.join(f'<div><dt>{e(label)}</dt><dd>{e(value)}</dd></div>' for label, value in rows if value is not None) + '</dl>'


def coverage(snapshot: dict) -> dict:
    rows = snapshot.get("unit_states", [])
    total = sum(row["count"] for row in rows)
    done = sum(row["count"] for row in rows if row["status"] == "accepted")
    return {"total": total, "done": done, "percent": round(100 * done / total, 1) if total else 0}


def billing_rows(run: dict) -> list[tuple[str, str]]:
    usage = run.get("usage") or {}
    cost = usage.get("cost_usd")
    provider = ("Unknown provider invoice cost; missing billing must not be interpreted as free inference."
                if cost is None else f'{"Estimated" if usage.get("estimated") else "Recorded"} provider inference cost: ${float(cost):.6f}; vendor billing may differ.')
    accounted = run.get("spent_usd")
    budget = ("Not supplied; no application-budget total is inferred." if accounted is None else
              f'${float(accounted):.6f} conservatively accounted by the application; separate from the provider invoice.')
    return [("Provider inference billing", provider), ("Application budget accounted", budget)]


def render_html(snapshot: dict, title: str, kind: str) -> str:
    audit_snapshot(snapshot)
    run, cov = snapshot["run"], coverage(snapshot)
    documents = {doc["id"]: doc for doc in snapshot["documents"]}
    css = '''@page{size:A4;margin:18mm 16mm;@bottom-left{content:"DOXA / TACITUS · AI-authored reading guide";font:8px sans-serif;color:#64776a}@bottom-right{content:counter(page);font:9px sans-serif;color:#64776a}}*{box-sizing:border-box}body{margin:0;color:#204b3b;background:#f6f7f0;font:15px/1.7 Arial,Helvetica,sans-serif}.wrap{max-width:1080px;margin:auto;padding:36px}.brand{font-size:14px;letter-spacing:.18em;font-weight:700}.hero{background:#204b3b;color:#f6f7ee;padding:38px;margin:24px 0}h1{font:400 44px/1.13 Georgia,serif;letter-spacing:-.035em;margin:18px 0 24px}h2{font:400 29px/1.2 Georgia,serif;margin:0 0 20px}h3{font-size:17px}.eyebrow{font-size:10px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}.hero p{max-width:760px;color:#dbe5d6}.notice{border-left:3px solid #ad814b;background:#eee8d5;padding:16px 22px;font-size:13px}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:25px 0}.kpis strong{font:400 34px Georgia,serif;display:block}.kpis span{font-size:12px}.section{padding:30px 0;border-top:1px solid #c7d4c0}.diagram{width:100%;height:auto;display:block;margin:26px 0}.case{padding:28px 0;border-top:2px solid #8fa68d}.quote{font:400 22px/1.5 Georgia,serif;border-left:3px solid #4e788d;padding-left:20px;white-space:pre-wrap}.facts{font-size:12px}.facts>div{display:grid;grid-template-columns:170px 1fr;gap:18px;padding:9px 0;border-top:1px solid #cfdbc8}.facts dt{font-weight:700}.facts dd{margin:0;overflow-wrap:anywhere}.model{background:#edf1e8;padding:18px 22px}.comment{border-left:3px solid #ad814b;padding:15px 23px;background:#faf6e9}.label{font-size:10px;text-transform:uppercase;letter-spacing:.12em;font-weight:700}.small,figcaption{font-size:11px;color:#607464}a{color:#356e5a;text-underline-offset:4px}.hero a{color:#e3eddb}.context{font-size:12px;white-space:pre-wrap;background:#edf1e8;padding:17px}.source{padding:17px 0;border-top:1px solid #c7d4c0}.footer{font-size:11px;border-top:1px solid #c7d4c0;padding-top:20px}.pill{display:inline-block;border:1px solid #90a884;padding:5px 10px;font-size:11px;margin:3px 5px 0 0}@media(max-width:700px){.wrap{padding:20px}.hero{padding:26px}h1{font-size:34px}.kpis{grid-template-columns:1fr}.facts>div{grid-template-columns:1fr;gap:4px}}@media print{body{background:#fff;font-size:11px;line-height:1.5}.wrap{padding:0}.hero{padding:28px;margin-top:20px}h1{font-size:34px}.section{padding:20px 0}.diagram{margin:18px 0}.case{break-before:page;padding-top:20px}.quote{font-size:17px}.facts{font-size:10px}.facts>div{padding:6px 0;grid-template-columns:135px 1fr}.model,.comment{padding:13px 18px}.notice{font-size:11px}.context{font-size:10px}.source{break-inside:avoid}.hero,.kpis{break-inside:avoid}h2,h3,.label{break-after:avoid}}'''
    intro = ("Follow real institutional passages through an explicit political-framing rubric. Read what was captured, whose position it represents and what would be needed to support a stronger bias claim."
             if kind == "political-framing" else "Follow real source-linked assessments into a monitoring workflow. Keep reporting, represented speakers, publication dates, model coverage and human review distinct.")
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src \'none\'"><title>{e(title)} · DOXA</title><style>{css}</style></head><body><main class="wrap"><p class="brand">DOXA / TACITUS</p><header class="hero"><p class="eyebrow">Recorded evidence · Annotated reading guide</p><h1>{e(title)}</h1><p>{e(intro)}</p><p class="small">Frozen {e(snapshot["generated_at"])} · Run {e(run["id"])} · Status: {e(run["status"])}</p></header>',
             f'<p class="notice"><strong>Read the evidence and the authorship labels.</strong> {e(AUTHORSHIP)} Original model readings remain labeled separately. Exact source anchoring has been checked; interpretation accuracy has not been independently measured.</p>',
             f'<section class="kpis"><div><strong>{len(snapshot["assessments"])}</strong><span>illustrative recorded cases</span></div><div><strong>{run["source_selection"]["documents"]:,}</strong><span>sources selected for the recorded run</span></div><div><strong>{cov["done"]:,}/{cov["total"]:,}</strong><span>run units fully assessed ({cov["percent"]}%)</span></div></section>',
             f'<p class="small">{e(snapshot["selection_notice"])} The cases below do not stand for every selected source. Run-wide coverage retains accepted, partial, failed and pending units.</p>',
             '<section class="section"><h2>How to read this guide.</h2><p>Begin with the exact source wording. Check the represented speaker, criterion and proposition. Read the recorded model interpretation, then use the distinctly labeled walkthrough note to question its limits.</p>',
             inline_diagram('doxa-process'), '</section>',
             '<section class="section"><h2>Keep the voices apart.</h2><p>A document may quote, report, reject or invent a speaker for an example. The represented speaker’s stance and the document’s stance are separate assessments.</p>', inline_diagram('doxa-attribution'), '</section>']
    for number, item in enumerate(snapshot["assessments"], 1):
        doc = documents[item["document_id"]]
        definition = snapshot["criterion_definitions"].get(item["lens_id"] + "/" + item["criterion_id"], {})
        note = reading_note(snapshot, item)
        refs = item.get("evidence_refs") or []
        first = refs[0]
        block = next(block for block in doc["blocks"] if block["id"] == item["block_id"])
        context = item.get("context") or block["text"]
        # A short display window keeps the annotated PDF readable. Full text is frozen.
        position = context.find(item.get("quote", ""))
        start, end = max(0, position - 350), min(len(context), max(position, 0) + len(item.get("quote", "")) + 350)
        context_window = ('…' if start else '') + context[start:end] + ('…' if end < len(context) else '')
        location = first.get("locator") or item.get("locator") or {}
        locator = " · ".join(str(value) for value in [f'page {location.get("page", block.get("page"))}' if location.get("page", block.get("page")) is not None else None,
                    f'paragraph {location.get("paragraph", block.get("paragraph"))}' if location.get("paragraph", block.get("paragraph")) is not None else None, item["block_id"], f'characters {item["start"]}–{item["end"]}'] if value)
        parts += [f'<article class="case" id="case-{number}"><p class="eyebrow">Case {number:02d} · Original recorded assessment</p><h2>{e(item.get("title") or definition.get("criterion", {}).get("label") or item["criterion_id"])}</h2>',
                  f'<p class="small">{e(doc["title"])} · {e(doc.get("author"))} · {e(doc.get("published_at"))}<br>{source_link(doc.get("source_url", ""))} · {e(locator)}</p><blockquote class="quote">{e(item.get("quote"))}</blockquote>',
                  facts([("Lens / criterion", definition.get("lens", item["lens_id"]) + " / " + item["criterion_id"]),
                         ("Criterion kind / result", item["criterion_kind"] + " / " + item["result"]),
                         ("Rubric version", definition.get("version")), ("What the criterion asks", definition.get("criterion", {}).get("description")),
                         ("Speaker / speech role", str(item.get("speaker") or "Unknown") + " / " + str(item.get("speech_role") or "unknown")),
                         ("Proposition", item.get("proposition")), ("Speaker stance", item.get("speaker_stance")),
                         ("Document stance", item.get("document_stance")), ("Hypothetical", str(bool(item.get("hypothetical")))),
                         ("Review state", item.get("review_status", "unreviewed")), ("Source unit state", item.get("recorded_unit_status")), ("Assessment ID", item["id"])]),
                  f'<section class="model"><p class="label">Recorded model interpretation</p><p>{e(item.get("explanation") or "No detailed rationale was recorded.")}</p><p><strong>Recorded alternative:</strong> {e(item.get("alternative") or "No alternative was recorded.")}</p></section>',
                  f'<section class="comment"><p class="label">AI-authored walkthrough commentary · separate from the model record</p><p>{e(note["what_is_captured"])}</p><p>{e(note["attribution_check"])}</p><p>{e(note["bias_commentary"])}</p><p><strong>Review question:</strong> {e(note["review_question"])}</p></section>',
                  f'<p class="label">Source context window</p><div class="context">{e(context_window)}</div><p class="small">Context may be excerpted for layout; the frozen snapshot retains the complete selected source blocks and exact offsets. This note changes no review status or model outcome.</p></article>']
    parts += ['<section class="section"><h2>From a reading to monitoring.</h2><p>Discourse trajectory follows source publication dates. Assessment history follows model runs and review actions. An expanded source collection, corrected attribution or different method can change an assessment without establishing a change in published discourse.</p>',
              inline_diagram('doxa-trajectory'), '<p class="small">Schematic diagram only: no measured trend, increase or improvement is implied. A monitoring claim needs compatible scope, methods, time windows and sufficient assessed denominators.</p></section>',
              '<section class="section"><h2>Sources and rights.</h2>']
    for doc in snapshot["documents"]:
        meta = doc.get("metadata") or {}
        translators = "; ".join(str(person.get("name", "")) for person in meta.get("translators", []) if isinstance(person, dict) and person.get("name"))
        translation_credit = f'<p class="small">Translation credited to {e(translators)}.</p>' if translators else ""
        parts += [f'<article class="source"><h3>{e(doc["title"])}</h3>{translation_credit}<p>{e(doc.get("author"))} · {e(doc.get("published_at"))} · {source_link(doc.get("source_url", ""))}</p><p class="small">{e(doc.get("license"))}<br>{e(doc.get("attribution") or meta.get("attribution"))}<br>{e(doc.get("rights_notes") or meta.get("rights_notes"))}<br>Source version SHA-256: {e(doc["sha256"])}<br>Frozen block content SHA-256: {e(doc["frozen_blocks_sha256"])}</p></article>']
    references = list({(value.get("lens"), value.get("version"), value.get("source_url"), value.get("scope")) for value in snapshot["criterion_definitions"].values()})
    if references:
        parts += ['<section class="section"><h2>Applied rubric and reference.</h2>']
        for lens, version, url, scope in sorted(references, key=lambda row: str(row[0])):
            parts += [f'<p><strong>{e(lens)} · {e(version)}</strong> · {source_link(url or "", "Rubric reference ↗")}<br>{e(scope)}</p>']
        parts += ['<p class="small">Criteria are operational research choices. A referenced framework does not establish independent endorsement or interpretation accuracy.</p></section>']
    parts += ['</section><section class="section"><h2>Recorded method and limits.</h2>', facts([("Run ID", run["id"]), ("Run status at capture", run["status"]),
              ("Provider / model", str(run["config"].get("provider")) + " / " + str(run["config"].get("model"))),
              ("Model revision", run.get("model_profile", {}).get("revision", "unresolved")),
              ("Prompt version", run.get("model_profile", {}).get("prompt_version")),
              ("Research question", run["config"].get("question")), ("Reviewer perspective", run["config"].get("persona")),
              ("Source selection hash", run["source_selection"]["sha256"]), ("Walkthrough snapshot hash", snapshot.get("snapshot_sha256")),
              ("Fully assessed units", f'{cov["done"]:,} of {cov["total"]:,} ({cov["percent"]}%)'), ("Commentary authorship", AUTHORSHIP), *billing_rows(run)]),
              '<p>Exact anchors establish where words occur. No independent human labels, calibrated probability or general interpretation-accuracy score is claimed. The documents are a purposive source collection; counts do not represent public opinion. Read source context, methodological alternatives and rights notices before reusing the material.</p></section>',
              f'<footer class="footer">{e(AUTHORSHIP)}<br>DOXA by TACITUS · <a href="https://github.com/sargonxg/DOXA-by-TACITUS">Open-source workbench</a> · <a href="https://tacitus.me/doxa/">Product and methodology</a></footer></main></body></html>']
    return ''.join(parts)


def render_workbook(snapshot: dict, title: str, target: Path) -> None:
    import xlsxwriter
    with xlsxwriter.Workbook(target, {"strings_to_formulas": False, "strings_to_urls": False}) as book:
        book.set_properties({"title": title, "author": "DOXA by TACITUS", "comments": AUTHORSHIP})
        heading = book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#204B3B", "text_wrap": True})
        cell = book.add_format({"text_wrap": True, "valign": "top", "font_color": "#204B3B"})

        def table(name, headers, rows):
            sheet = book.add_worksheet(name)
            sheet.hide_gridlines(2)
            sheet.freeze_panes(1, 1)
            sheet.set_tab_color("#204B3B")
            sheet.set_column(0, len(headers), 30, cell)
            sheet.write_row(0, 0, [*headers, "Part"], heading)
            index = 1
            for values in rows:
                chunks = []
                for value in values:
                    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value if value is not None else "")
                    chunks.append([text[start:start + 32000] for start in range(0, len(text), 32000)] or [""])
                parts = max(map(len, chunks), default=1)
                for part in range(parts):
                    for column, values in enumerate(chunks):
                        sheet.write_string(index, column, values[part] if part < len(values) else "", cell)
                    sheet.write_string(index, len(headers), f"{part + 1}/{parts}", cell)
                    sheet.set_row(index, 58)
                    index += 1
            sheet.autofilter(0, 0, max(1, index - 1), len(headers))
            sheet.repeat_rows(0)
            sheet.set_landscape()
            sheet.fit_to_pages(1, 0)
        cov = coverage(snapshot)
        table("Read me", ["Topic", "Details"], [["Title", title], ["Authorship", AUTHORSHIP], ["Run", snapshot["run"]["id"]],
              ["Run status at capture", snapshot["run"]["status"]], ["Selected run sources", snapshot["run"]["source_selection"]["documents"]],
              ["Run-wide fully assessed units", f'{cov["done"]} / {cov["total"]} ({cov["percent"]}%)'],
              ["Illustrative cases", len(snapshot["assessments"])], ["Selection", snapshot["selection_notice"]],
              ["Snapshot SHA-256", snapshot["snapshot_sha256"]], ["Anchor audit", snapshot["source_anchor_audit"]], *billing_rows(snapshot["run"])])
        rows = []
        for index, item in enumerate(snapshot["assessments"], 1):
            note = reading_note(snapshot, item)
            rows.append([index, item["id"], item["document_id"], item["quote"], item["lens_id"], item["criterion_id"],
                         item["criterion_kind"], item["result"], item.get("speaker"), item.get("attribution"), item.get("speech_role"),
                         item.get("proposition"), item.get("speaker_stance"), item.get("document_stance"), item.get("hypothetical"),
                         item.get("explanation"), item.get("alternative"), note["what_is_captured"], note["attribution_check"],
                         note["bias_commentary"], note["review_question"], AUTHORSHIP, item.get("review_status"), item.get("recorded_review_overlay")])
        table("Annotated cases", ["Case", "Assessment ID", "Source ID", "Exact source quote", "Lens", "Criterion", "Kind", "Recorded model result",
              "Speaker", "Attribution", "Speech role", "Proposition", "Speaker stance", "Document stance", "Hypothetical",
              "Recorded model reading", "Recorded alternative", "AI note: capture", "AI note: attribution", "AI note: bias comparator",
              "AI review question", "Commentary authorship", "Stored review state", "Separate review overlay"], rows)
        table("Sources", ["Source ID", "Title", "Author", "Published", "Original URL", "License", "Source version SHA-256", "Frozen blocks SHA-256", "Complete metadata"],
              [[doc["id"], doc["title"], doc.get("author"), doc.get("published_at"), doc.get("source_url"), doc.get("license"), doc["sha256"],
                doc["frozen_blocks_sha256"], {key: value for key, value in doc.items() if key != "blocks"}] for doc in snapshot["documents"]])
        table("Exact evidence", ["Assessment ID", "Document ID", "Block ID", "Start", "End", "Exact quote", "Locator", "Complete frozen assessment"],
              [[item["id"], item["document_id"], item["block_id"], item["start"], item["end"], item["quote"], item.get("locator"), item] for item in snapshot["assessments"]])
        table("Method and coverage", ["Record", "Frozen details"], [["Run", snapshot["run"]], ["Criteria", snapshot["criterion_definitions"]],
              ["Run-wide unit states", snapshot["unit_states"]], ["Run-wide projected decision counts", snapshot["decision_counts"]],
              ["Independent human labels", False], ["Interpretation accuracy", None], ["Authorship", AUTHORSHIP]])


def build(snapshot: dict, prefix: Path, title: str, kind: str) -> dict:
    if kind == "political-framing" and not any(item["lens_id"] == "political-framing" for item in snapshot["assessments"]):
        raise ValueError("A political-framing guide requires recorded political-framing criterion assessments")
    if snapshot.get("snapshot_sha256") is None:
        snapshot["snapshot_sha256"] = digest(snapshot)
    audit_snapshot(snapshot)
    prefix = prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".walkthrough-", dir=prefix.parent) as working:
        stage = Path(working)
        html = render_html(snapshot, title, kind)
        (stage / "guide.html").write_text(html, encoding="utf-8")
        from weasyprint import HTML
        def no_network(*_args, **_kwargs):
            raise ValueError("External resources are disabled in offline walkthroughs")
        HTML(string=html, url_fetcher=no_network).write_pdf(stage / "guide.pdf")
        render_workbook(snapshot, title, stage / "guide.xlsx")
        with (stage / "guide.snapshot.json.gz").open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as output:
                output.write((json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
        files = []
        for suffix in ("pdf", "html", "xlsx", "snapshot.json.gz"):
            source, target = stage / ("guide." + suffix), prefix.parent / (prefix.name + "." + suffix)
            files.append({"file": target.name, "bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
            shutil.move(source, target)
        manifest = {"schema_version": "doxa-annotated-guide-1", "title": title, "kind": kind, "slug": prefix.name,
                    "generated_at": snapshot["generated_at"], "run_id": snapshot["run"]["id"], "run_status": snapshot["run"]["status"],
                    "snapshot_sha256": snapshot["snapshot_sha256"], "cases": len(snapshot["assessments"]),
                    "selected_run_sources": snapshot["run"]["source_selection"]["documents"], "run_unit_coverage": coverage(snapshot),
                    "commentary_authorship": AUTHORSHIP, "independent_human_labels": False, "interpretation_accuracy": None,
                    "files": files, "source_anchor_audit": snapshot["source_anchor_audit"],
                    "preview": [{"id": item["id"], "title": item.get("title"), "quote": item["quote"], "criterion_kind": item["criterion_kind"],
                                 "result": item["result"], "attribution": item.get("attribution"), "document_stance": item.get("document_stance"),
                                 "source_title": next(doc["title"] for doc in snapshot["documents"] if doc["id"] == item["document_id"]),
                                 "source_url": next(doc.get("source_url") for doc in snapshot["documents"] if doc["id"] == item["document_id"]),
                                 "model_reading": item.get("explanation"), "walkthrough_note": reading_note(snapshot, item)["what_is_captured"]}
                                for item in snapshot["assessments"][:2]]}
        (prefix.parent / (prefix.name + ".manifest.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return manifest


def update_website_index(path: Path, manifest: dict) -> None:
    value = json.loads(path.read_text()) if path.exists() else {"schema_version": "1.0", "guides": []}
    if value.get("schema_version") != "1.0" or not isinstance(value.get("guides"), list):
        raise ValueError("Unsupported prepared website guide index")
    value["guides"] = sorted([row for row in value["guides"] if row["slug"] != manifest["slug"]] + [manifest], key=lambda row: (row["kind"] != "political-framing", row["title"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Previously frozen guide JSON or JSON.gz")
    source.add_argument("--data-dir", type=Path, help="Local recorded workspace; opened SQLite read-only")
    parser.add_argument("--run-id")
    parser.add_argument("--assessment-id", action="append", help="Choose a recorded noteworthy assessment; repeat for each case")
    parser.add_argument("--cases", type=int, default=4)
    parser.add_argument("--notes", type=Path, help="AI-authored commentary JSON keyed by selected assessment ID; never changes model records")
    parser.add_argument("--capture-unfinished", action="store_true", help="Explicitly freeze an unfinished run with its current status")
    parser.add_argument("--kind", choices=["political-framing", "news-monitoring"], required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--website-index", type=Path, help="Update the prepared TACITUS site guide index; does not deploy")
    args = parser.parse_args()
    if not 1 <= args.cases <= 12 or (args.assessment_id and not 1 <= len(args.assessment_id) <= 12):
        parser.error("Choose 1–12 illustrative cases")
    if args.data_dir:
        if not args.run_id:
            parser.error("--data-dir requires --run-id")
        snapshot = freeze_run(args.data_dir, args.run_id, limit=args.cases, assessment_ids=args.assessment_id, allow_active=args.capture_unfinished)
    else:
        opener = gzip.open if args.snapshot.suffix == ".gz" else open
        with opener(args.snapshot, "rt", encoding="utf-8") as stream:
            snapshot = json.load(stream)
    if args.notes:
        notes = json.loads(args.notes.read_text())
        selected_ids = {item["id"] for item in snapshot["assessments"]}
        if not isinstance(notes, dict) or set(notes) - selected_ids:
            parser.error("Commentary keys must name selected frozen assessment IDs")
        snapshot.pop("snapshot_sha256", None)
        snapshot["commentary_notes"] = notes
        snapshot["commentary_created_at"] = datetime.now(timezone.utc).isoformat()
        for item in snapshot["assessments"]:
            reading_note(snapshot, item)
    if args.website_index and snapshot["run"]["status"] in {"running", "queued", "importing"} and not args.capture_unfinished:
        parser.error("Publishing an unfinished capture requires explicit --capture-unfinished")
    result = build(snapshot, args.output_prefix, args.title, args.kind)
    if args.website_index:
        update_website_index(args.website_index, result)
    print(json.dumps({key: result[key] for key in ("title", "run_id", "run_status", "cases", "run_unit_coverage", "files")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
