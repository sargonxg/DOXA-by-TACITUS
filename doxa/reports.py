"""Offline, evidence-preserving report artifacts from an immutable snapshot."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import uuid

_COLORS = ["#1f7a70", "#b58945", "#547894", "#9d6f8c", "#718b65", "#a65b50"]
_PROFILES = {
    "research": ("Research dossier", "Evidence, interpretive alternatives and a reproducible audit trail."),
    "briefing": ("Executive briefing", "A concise view of the corpus, the observations and what needs review."),
    "teaching": ("Teaching edition", "Explore how a reading changes with its context, speaker and analytical lens."),
}
_EVIDENCE_LIMITS = {"research": 100, "briefing": 25, "teaching": 50}
_METHOD = (
    "DOXA examines statements in the supplied corpus under an explicit, versioned analytical lens. "
    "Findings are interpretive observations about text, not diagnoses of an author's beliefs or identity. "
    "Quoted speech, endorsement, rebuttal and uncertain attribution must remain distinct. "
    "Document counts and word counts describe this uploaded corpus, not a representative population. "
    "An absence of findings does not establish an absence of relevant discourse. "
    "Model certainty is an uncalibrated assessment, not a probability. Human review status is shown separately."
)


def _string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _obj(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def _number(value) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0
    except (TypeError, ValueError):
        return 0


def _prepare(snapshot: dict) -> dict:
    documents = [dict(d) for d in snapshot.get("documents", [])]
    findings = []
    for finding in snapshot.get("findings", []):
        record = {**_obj(finding.get("payload_json")), **finding}
        findings.append(record)
    runs = []
    for run in snapshot.get("runs", []):
        record = dict(run)
        for name in ("config", "progress", "usage", "model_profile"):
            record[name] = _obj(record.get(name) or record.get(name + "_json"))
        runs.append(record)
    by_id = {_string(d.get("id")): d for d in documents}
    authors: dict[str, dict] = {}
    for document in documents:
        name = _string(document.get("author") or "Unknown")
        entry = authors.setdefault(name, {"documents": 0, "words": 0, "findings": 0, "confirmed": 0})
        entry["documents"] += 1
        entry["words"] += int(_number(document.get("word_count")))
    for finding in findings:
        author = _string(by_id.get(_string(finding.get("document_id")), {}).get("author") or "Unknown")
        entry = authors.setdefault(author, {"documents": 0, "words": 0, "findings": 0, "confirmed": 0})
        entry["findings"] += 1
        entry["confirmed"] += finding.get("review_status") in {"confirmed", "accepted"}
    categories = Counter(_string(f.get("category") or "Uncategorized") for f in findings)
    lenses = Counter(_string(f.get("lens_id") or "Unspecified") for f in findings)
    reviews = Counter(_string(f.get("review_status") or "unreviewed") for f in findings)
    statuses = Counter(_string(d.get("status") or "unknown") for d in documents)
    return {"documents": documents, "findings": findings, "runs": runs,
            "by_id": by_id, "authors": authors, "categories": categories,
            "lenses": lenses, "reviews": reviews, "statuses": statuses,
            "word_count": sum(int(_number(d.get("word_count"))) for d in documents),
            "page_count": sum(int(_number(d.get("page_count"))) for d in documents)}


def _source(finding: dict, data: dict) -> tuple[dict, dict, str]:
    document = data["by_id"].get(_string(finding.get("document_id")), {})
    blocks = document.get("blocks") or []
    block = next((b for b in blocks if _string(b.get("id")) == _string(finding.get("block_id"))), {})
    location = []
    if block.get("page") is not None:
        location.append(f"p. {block['page']}")
    if block.get("paragraph") is not None:
        location.append(f"paragraph {block['paragraph']}")
    if finding.get("block_id"):
        location.append(_string(finding["block_id"]))
    if finding.get("start") is not None and finding.get("end") is not None:
        location.append(f"characters {finding['start']}–{finding['end']}")
    return document, block, " · ".join(location) or "Source location unavailable"


def _bars(counts: Counter, title: str, limit: int = 12) -> str:
    items = counts.most_common(limit)
    width = 800
    row_height = 42
    height = max(100, 30 + len(items) * row_height)
    maximum = max((v for _, v in items), default=1)
    output = [f'<svg class="bars" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title, quote=True)}">',
              f'<title>{escape(title)}</title>']
    if not items:
        output.append('<text x="12" y="48" fill="#647173" font-size="16">No observations in this selection</text>')
    for index, (label, count) in enumerate(items):
        y = 18 + index * row_height
        shown = label.replace("_", " ")
        if len(shown) > 31:
            shown = shown[:28] + "…"
        output.append(f'<text x="0" y="{y+17}" fill="#405354" font-size="14"><title>{escape(label)}</title>{escape(shown)}</text>')
        output.append(f'<rect x="255" y="{y}" rx="5" width="{max(2, int(count/maximum*470))}" height="26" fill="{_COLORS[index % len(_COLORS)]}"/>')
        output.append(f'<text x="{265+int(count/maximum*470)}" y="{y+18}" fill="#243c3b" font-size="14" font-weight="700">{count}</text>')
    output.append("</svg>")
    if len(counts) > limit:
        output.append(f'<p class="muted">Chart shows {limit} of {len(counts)} categories; all counts appear in the workbook.</p>')
    return "".join(output)


def _anchor(value) -> str:
    # Never interpolate an untrusted document ID into an href or HTML attribute.
    import hashlib
    return "source-" + hashlib.sha256(_string(value).encode()).hexdigest()[:16]


def _e(value) -> str:
    return escape(_string(value), quote=True)


def _table_html(rows: list[tuple[str, str]]) -> str:
    return '<table class="facts"><tbody>' + ''.join(f'<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>' for label, value in rows) + '</tbody></table>'


def _lexical(data: dict) -> bool:
    providers = [run["config"].get("provider") or run["model_profile"].get("provider") for run in data["runs"]]
    if any(providers):
        return all(provider == "lexical" for provider in providers)
    return bool(data["findings"]) and all(f.get("evidence_kind") == "lexical_match" for f in data["findings"])


def _coverage_summary(snapshot: dict, data: dict) -> tuple[list[tuple[str, str]], str]:
    """Whitelist public coverage facts; telemetry dictionaries are not prose."""
    metrics = _obj(snapshot.get("metrics"))
    run = data["runs"][0] if data["runs"] else {}
    progress = _obj(metrics.get("coverage_detail")) or _obj(run.get("progress"))
    legacy = _obj(metrics.get("coverage"))
    rows = [("Run status", _string(run.get("status") or "Not supplied").replace("_", " ")),
            ("Analysis method", "Literal word matching; no semantic interpretation" if _lexical(data) else "Model interpretations require contextual review")]
    denominators = []
    def pair(label, done, total):
        if done is None or total is None:
            return
        done, total = int(_number(done)), int(_number(total))
        percentage = f" ({100*done/total:.1f}%)" if total else ""
        rows.append((label, f"{done:,} of {total:,}{percentage}"))
        denominators.append(done == total and total > 0)
    pair("Extracted blocks processed", progress.get("blocks_done", progress.get("processed_blocks")), progress.get("blocks_total", progress.get("total_blocks")))
    pair("Extracted characters processed", progress.get("processed_characters"), progress.get("selected_characters", progress.get("source_characters")))
    pair("Completed chunks", legacy.get("completed_chunks"), legacy.get("total_chunks"))
    if progress.get("tasks_total") is not None:
        pair("Completed tasks", progress.get("tasks_done", 0), progress["tasks_total"])
    if progress.get("tasks_failed") is not None:
        rows.append(("Tasks requiring attention", f'{int(_number(progress["tasks_failed"])):,}'))
    if progress.get("sampled"):
        rows.append(("Selection", "Sampled source passages; this is not a full-corpus analysis"))
    rows.append(("Coverage basis", "Blocks count each source block once; character coverage weights text length. Percentages use their stated denominators and exclude images and unextracted content."))
    explicit_incomplete = any(progress.get(key) is False for key in ("complete", "selected_input_complete", "full_corpus_scan_complete"))
    incomplete = explicit_incomplete or (denominators and not all(denominators)) or run.get("status") in {"partial", "running", "queued", "failed", "cancelled", "paused_budget", "paused_model_changed"}
    if incomplete:
        notice = "Partial coverage. Some selected source content is unprocessed or failed validation. These observations must not be treated as an exhaustive account of the corpus. Review failed tasks or resume analysis before making completeness claims."
    elif not denominators:
        notice = "Coverage counts were not supplied. No completeness claim can be made from this export."
    else:
        notice = "Processing is complete for the stated selection of extracted text. This does not establish interpretive accuracy or cover unextracted images."
    return rows, notice


def _selection_rows(snapshot: dict) -> list[tuple[str, str]]:
    labels = {"author": "Author", "document_id": "Document", "lens_id": "Analytical lens", "category": "Category", "review_status": "Review status", "q": "Search", "run_id": "Selected run"}
    filters = _obj(snapshot.get("filters"))
    return [(label, _string(filters[key])) for key, label in labels.items() if filters.get(key)] or [("Selection", "All sources and observations in this saved export selection")]


def _provenance_rows(snapshot: dict, run: dict) -> list[tuple[str, str]]:
    config, profile, usage = run["config"], run["model_profile"], run["usage"]
    lens_versions = _obj(profile.get("rubric_versions"))
    lenses = "; ".join(f"{name}: {version}" for name, version in lens_versions.items()) or "; ".join(map(str, config.get("lens_ids") or [config.get("lens_id") or "Not supplied"]))
    rows = [("Run", _string(run.get("id") or "Not supplied")),
            ("Inquiry", _string(config.get("question") or "Not supplied")),
            ("Reviewer perspective", _string(config.get("persona") or "Not supplied")),
            ("Provider", _string(profile.get("provider") or config.get("provider") or "Not supplied")),
            ("Model", _string(profile.get("model") or config.get("model") or "Not supplied")),
            ("Model revision", _string(config.get("model_revision") or profile.get("revision") or profile.get("digest") or "Not pinned")),
            ("Quantization", _string(profile.get("quantization") or config.get("quantization") or "Not supplied")),
            ("Prompt version", _string(profile.get("prompt_version") or "Not supplied")),
            ("Response contract", _string(profile.get("response_schema_version") or "Not supplied")),
            ("Lens definitions / versions", lenses),
            ("Report generated", _string(snapshot.get("generated_at") or "Not supplied"))]
    if profile.get("response_model") and profile["response_model"] != profile.get("model"):
        rows.append(("Provider response model", _string(profile["response_model"])))
    if run.get("completed_at"):
        rows.append(("Run completed", _string(run["completed_at"])))
    tokens = [f'{int(_number(usage[key])):,} {label}' for key, label in [("input_tokens", "input tokens"), ("output_tokens", "output tokens"), ("requests", "requests")] if usage.get(key) is not None]
    rows.append(("Recorded usage", "; ".join(tokens) or "Not supplied"))
    if usage.get("cost_usd") is None:
        cost = "Provider cost unavailable; do not interpret missing billing as free inference."
    else:
        cost = f'{"Estimated" if usage.get("estimated") else "Recorded"} inference cost: ${_number(usage["cost_usd"]):.4f}.'
    if _obj(config.get("saved_snapshot")).get("historical_usage"):
        cost += " Historical usage from a saved run; loading the showcase requested no new inference."
    rows.append(("Inference cost", cost))
    budget = []
    for key, label in [("budget_usd", "run cap")]:
        if config.get(key) is not None:
            budget.append(f'${_number(config[key]):.4f} {label}')
    for key, label in [("spent_usd", "accounted spend"), ("reserved_usd", "reserved")]:
        if run.get(key) is not None:
            budget.append(f'${_number(run[key]):.4f} {label}')
    rows.append(("Budget accounting", "; ".join(budget) or "Not supplied"))
    return rows


def _html(snapshot: dict, data: dict) -> str:
    profile = snapshot.get("profile", "research")
    edition, subtitle = _PROFILES.get(profile, _PROFILES["research"])
    project = snapshot.get("project") or {}
    title = _string(project.get("name") or "DOXA corpus analysis")
    generated = _string(snapshot.get("generated_at") or datetime.now(timezone.utc).isoformat())
    confirmed = sum(data["reviews"].get(x, 0) for x in ("confirmed", "accepted"))
    unreviewed = data["reviews"].get("unreviewed", 0)
    evidence_limit = _EVIDENCE_LIMITS.get(profile, 100)
    visible_findings = data["findings"][:evidence_limit]
    lexical = _lexical(data)
    run_status = _string(data["runs"][0].get("status") if data["runs"] else "not supplied").replace("_", " ")
    run_notice = f"Run status: {run_status}" + (" · incomplete source coverage" if run_status in {"partial", "failed", "cancelled", "running", "queued"} or run_status.startswith("paused") else "")
    css = """
    @page{size:A4;margin:18mm 16mm;@bottom-right{content:counter(page);font:10px sans-serif;color:#71827e}}
    *{box-sizing:border-box}body{margin:0;background:#edf1ed;color:#203c39;font-family:Arial,Helvetica,sans-serif;line-height:1.55}
    .wrap{max-width:1160px;margin:auto;padding:40px 40px 64px}.mast{display:flex;justify-content:space-between;align-items:center;font-size:12px;letter-spacing:2px;margin-bottom:28px}
    .brand{font-size:23px;font-weight:800;letter-spacing:5px}.mast span{color:#697a74}.hero{background:#173c39;color:#f6f6ef;border-radius:18px;padding:44px;position:relative;overflow:hidden}
    .hero:after{content:'δ';position:absolute;right:30px;top:-60px;font-family:Georgia,serif;font-size:240px;color:#8fae8b;opacity:.14}
    .eyebrow{text-transform:uppercase;letter-spacing:2px;font-size:11px;font-weight:700;color:#bacbb6}h1{font-family:Georgia,serif;font-size:45px;line-height:1.12;margin:18px 0;max-width:850px;font-weight:400;position:relative;z-index:1}
    .hero p{color:#cddbd1;max-width:650px;margin:10px 0}.dateline{font-size:11px;margin-top:24px;color:#b0c4b6}.nav{display:flex;gap:24px;padding:20px 4px;font-size:12px;text-transform:uppercase;letter-spacing:1px}
    a{color:#1f7a70;text-decoration:none}.nav a{color:#45655c}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:14px 0 24px}.kpi{background:#fffef8;padding:24px;border:1px solid #dce4da;border-radius:12px}.kpi strong{display:block;font-size:35px;letter-spacing:-1px;font-weight:500}.kpi span{font-size:12px;color:#708079}.kpi small{display:block;color:#7a8980;font-size:10px;margin-top:8px}
    .panel{background:#fffef8;border:1px solid #dce4da;padding:28px;border-radius:14px;margin-bottom:20px}h2{font-family:Georgia,serif;font-size:27px;font-weight:400;margin:0 0 12px}h3{font-size:16px;margin:0 0 12px}.muted{color:#728079;font-size:12px}.grid{display:grid;grid-template-columns:1.2fr 1fr;gap:20px}.bars{display:block;width:100%;height:auto}.note{border-left:3px solid #b58945;padding:10px 16px;background:#f5f0df;font-size:12px}.chip{display:inline-block;padding:4px 9px;background:#e7efe7;border:1px solid #d2e0d0;border-radius:12px;font-size:10px;margin:0 5px 6px 0;letter-spacing:.3px}.chip.warn{background:#f7ecd8;border-color:#ead5af;color:#805d2d}
    .finding{border-top:1px solid #dce4da;padding:24px 0;break-inside:avoid}.finding:first-of-type{border-top:0}.finding h3{font-size:19px;margin:8px 0 10px}blockquote{font-family:Georgia,serif;font-size:21px;line-height:1.5;margin:18px 0;padding:0 0 0 18px;border-left:3px solid #4e8c7b;color:#254c43;white-space:pre-wrap;overflow-wrap:anywhere}.citation{font-size:11px;color:#728079}.context{background:#f0f3ed;padding:14px;border-radius:8px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.context mark{background:#e8dcae;color:#213d36;padding:1px}.label{font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;color:#6a7d70}.finding p{font-size:13px;margin:8px 0}.alternative{font-size:12px;border-left:2px solid #bfa976;padding-left:14px;margin-top:16px}
    table{border-collapse:collapse;width:100%;font-size:11px}th{text-align:left;text-transform:uppercase;font-size:9px;letter-spacing:1px;color:#6a7c73;border-bottom:2px solid #bbcbbc;padding:11px 8px}td{border-bottom:1px solid #e2e7dd;padding:12px 8px;vertical-align:top;overflow-wrap:anywhere}pre{font-size:10px;white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f3ed;padding:15px;border-radius:8px}.document{padding:15px 0;border-bottom:1px solid #dce4da;break-inside:avoid}.footer{color:#718279;font-size:10px;margin-top:25px;border-top:1px solid #cbd8c9;padding-top:18px}.teaching{background:#e6eee3;border-radius:8px;padding:18px;font-size:13px}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}
    @media(max-width:760px){.wrap{padding:20px}.hero{padding:28px}h1{font-size:34px}.kpis{grid-template-columns:repeat(2,1fr)}.grid,.two{grid-template-columns:1fr}.panel{padding:20px}.mast{font-size:9px}.nav{gap:14px;font-size:10px}}
    @media print{body{background:white}.wrap{padding:0;max-width:none}.hero{border-radius:0;padding:30px;print-color-adjust:exact;-webkit-print-color-adjust:exact}h1{font-size:34px}.nav{display:none}.panel{border:0;border-top:1px solid #ccd7c9;border-radius:0;padding:20px 0}.kpi{padding:16px}.kpi strong{font-size:28px}.grid,.two{display:block}.bars{max-height:290px}.finding{break-inside:auto}.context{break-inside:auto}.kpis,.hero{break-inside:avoid}a{color:inherit}}
    @media print{body{font-size:11px;line-height:1.4}.panel{padding:16px 0;margin-bottom:12px}h2{font-size:23px;break-after:avoid}h3,.label{break-after:avoid}.finding{padding:14px 0}.finding h3{font-size:16px;margin:6px 0}.finding p{font-size:11px;line-height:1.45;margin:6px 0}blockquote{font-size:15px;line-height:1.4;margin:12px 0;padding-left:12px}.context{font-size:10px;line-height:1.4;padding:10px}.label{font-size:9px}.citation{font-size:9px}.alternative{margin-top:10px}.document{padding:10px 0}.document h3{font-size:12px;margin:0 0 5px}.document p{margin:4px 0}.facts th{width:28%;font-size:9px}.facts td{font-size:10px;padding:7px 6px}.bars{max-height:240px}}
    """
    sections = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src \'none\'"><title>{_e(title)} — DOXA report</title><style>{css}</style></head><body><main class="wrap">',
                '<div class="mast"><div class="brand">DOXA<span style="font-size:10px;letter-spacing:2px"> / TACITUS</span></div><span>READ THE PATTERNS. KEEP THE EVIDENCE.</span></div>',
                f'<header class="hero"><div class="eyebrow">{_e(edition)} · Corpus intelligence</div><h1>{_e(title)}</h1><p>{_e(subtitle)}</p><p>{_e(run_notice)}</p><div class="dateline">Generated {_e(generated)} · {_e(profile)} profile · immutable export snapshot</div></header>',
                '<nav class="nav"><a href="#overview">Overview</a><a href="#evidence">Evidence</a><a href="#sources">Sources</a><a href="#method">Method</a></nav>',
                '<section id="overview" class="kpis">']
    for number, label, detail in [(len(data["documents"]), "source documents", f'{data["page_count"]:,} source pages'),
                                   (data["word_count"], "source words", "Corpus denominator"),
                                   (len(data["findings"]), "candidate observations", "Includes all review statuses"),
                                   (confirmed, "confirmed by a reviewer", f"{unreviewed:,} await review")]:
        sections.append(f'<div class="kpi"><strong>{number:,}</strong><span>{_e(label)}</span><small>{_e(detail)}</small></div>')
    sections.append('</section>')
    if lexical:
        sections.append('<section class="panel"><p class="note"><strong>Literal baseline only.</strong> This report counts exact word mentions. No model performed semantic interpretation, and these mentions do not establish bias, prejudice, endorsement or an author’s beliefs.</p></section>')
    if profile == "briefing":
        largest = data["categories"].most_common(1)
        top = (f'The most frequent candidate category is “{largest[0][0].replace("_", " ")}”, with {largest[0][1]:,} observations.' if largest else 'No candidate observations match this selection.')
        sections.append(f'<section class="panel"><div class="label">At a glance</div><h2>The briefing</h2><p>{_e(top)} {_e(f"{confirmed:,} observations are confirmed by a reviewer; {unreviewed:,} remain unreviewed.")}</p><p class="note">Next review priority: examine unreviewed quotations alongside their surrounding text and speaker attribution. The evidence ledger and method appendix below preserve the complete basis for this briefing.</p></section>')
    sections.append('<div class="grid"><section class="panel"><div class="label">Patterns in this selection</div><h2>What the corpus surfaces</h2>' + _bars(data["categories"], "Observations by category") + '</section><section class="panel"><div class="label">Interpretation & accountability</div><h2>Read with context</h2>')
    sections.append(_bars(data["reviews"], "Observations by review status"))
    sections.append('<p class="note">An observation is a candidate reading of a specific passage. Review its quotation, speaker, lens and alternative before drawing a conclusion.</p></section></div>')
    sections.append('<section class="panel"><div class="label">Corpus coverage</div><h2>Know the denominator</h2><div class="two"><div>')
    sections.append(_bars(data["statuses"], "Documents by ingestion status"))
    coverage_rows, coverage_notice = _coverage_summary(snapshot, data)
    sections.append('</div><div>' + _table_html(coverage_rows) + f'<p class="note">{_e(coverage_notice)}</p></div></div></section>')
    if profile == "teaching":
        sections.append('<section class="panel teaching"><h2>A reading exercise</h2><p>For each passage: identify who is speaking; distinguish a quotation from endorsement; state the lens being applied; write a plausible alternative; then compare with the supplied interpretation. A disagreement can reveal a methodological choice rather than an error.</p></section>')
    sections.append('<section class="panel"><div class="label">Authorship in the supplied corpus</div><h2>Patterns by author</h2><table><thead><tr><th>Author</th><th>Documents</th><th>Words</th><th>Observations</th><th>Confirmed</th></tr></thead><tbody>')
    for author, count in sorted(data["authors"].items(), key=lambda x: (-x[1]["findings"], x[0])):
        sections.append(f'<tr><td>{_e(author)}</td><td>{count["documents"]}</td><td>{count["words"]:,}</td><td>{count["findings"]}</td><td>{count["confirmed"]}</td></tr>')
    sections.append('</tbody></table><p class="muted">Attribution uses supplied document metadata. These counts describe analyzed passages and do not establish an author’s private beliefs.</p></section>')
    sections.append('<section class="panel" id="evidence"><div class="label">The evidence ledger</div><h2>Every observation has a source</h2>')
    if len(data["findings"]) > evidence_limit:
        sections.append(f'<p class="note"><strong>Showing {len(visible_findings):,} of {len(data["findings"]):,} observations.</strong> The {_e(profile)} edition limits its evidence appendix to {evidence_limit:,} entries for readable printing. All charts and totals above describe the full selected snapshot. Export XLSX, CSV or JSONL for complete evidence, or apply a narrower document, lens or review filter before exporting.</p>')
    if not data["findings"]:
        sections.append('<p>No observations match this export selection. This does not establish the absence of relevant discourse.</p>')
    for index, finding in enumerate(visible_findings, 1):
        document, block, location = _source(finding, data)
        status = finding.get("review_status") or "unreviewed"
        sections.append(f'<article class="finding"><div class="label">Observation {index:03d}</div><h3>{_e(finding.get("title") or finding.get("category") or "Passage observation")}</h3>')
        for value in [finding.get("lens_id"), finding.get("category"), finding.get("stance"), status]:
            if value:
                sections.append(f'<span class="chip{" warn" if value == "unreviewed" else ""}">{_e(value)}</span>')
        sections.append(f'<blockquote>{_e(finding.get("quote"))}</blockquote><p class="citation"><a href="#{_anchor(finding.get("document_id"))}">{_e(document.get("title") or document.get("filename") or finding.get("document_id"))}</a> · {_e(document.get("author") or "Unknown author")} · {_e(location)}</p>')
        sections.append(f'<p><span class="label">Interpretation</span><br>{_e(finding.get("explanation") or "No interpretation supplied.")}</p>')
        sections.append(f'<div class="alternative"><span class="label">Alternative reading</span><p>{_e(finding.get("alternative") or "No alternative supplied; consider the broader context during review.")}</p></div>')
        sections.append(f'<p class="muted">Speaker: {_e(finding.get("speaker") or "Unspecified")} · Attribution: {_e(finding.get("attribution") or "Unspecified")} · Model assessment: {_e(finding.get("certainty") or "Not supplied")} (uncalibrated)</p>')
        context = _string(block.get("text") or finding.get("source_context") or finding.get("context"))
        if context:
            start, end = finding.get("start"), finding.get("end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(context) and context[start:end] == _string(finding.get("quote")):
                shown = _e(context[:start]) + '<mark>' + _e(context[start:end]) + '</mark>' + _e(context[end:])
            else:
                shown = _e(context)
            sections.append(f'<p class="label">Source context</p><div class="context">{shown}</div>')
        else:
            sections.append('<p class="muted">Full source context was not supplied in this snapshot. Consult the original document before adjudicating.</p>')
        if finding.get("review_note"):
            sections.append(f'<p><span class="label">Reviewer note</span><br>{_e(finding["review_note"])}</p>')
        if profile == "teaching":
            sections.append('<div class="teaching"><span class="label">Discussion prompt</span><p>Which words support this reading? Whose voice do they represent? What changes when the surrounding passage is included? Compare the interpretation and alternative before proposing your own.</p></div>')
        sections.append('</article>')
    sections.append('</section><section class="panel" id="sources"><div class="label">Source register</div><h2>The corpus behind the reading</h2>')
    for document in data["documents"]:
        metadata = _obj(document.get("metadata") or document.get("metadata_json"))
        warnings = document.get("extraction_warnings") or metadata.get("extraction_warnings") or []
        sections.append(f'<article class="document" id="{_anchor(document.get("id"))}"><h3>{_e(document.get("title") or document.get("filename"))}</h3><p class="muted">{_e(document.get("author") or "Unknown")} · {_e(document.get("document_type") or "document")} · {_e(document.get("published_at") or "Date not supplied")} · {int(_number(document.get("word_count"))):,} words · {_e(document.get("status") or "unknown")}</p>')
        if document.get("source_url"):
            sections.append(f'<p class="muted">Source reference: {_e(document["source_url"])}</p>')
        sections.append(f'<p class="citation">Document {_e(document.get("id"))} · SHA-256 {_e(document.get("sha256") or "not supplied")}<br>Rights / license: {_e(document.get("license") or "Not supplied")}</p>')
        for label, key in [("Attribution", "attribution"), ("License reference", "license_url"), ("Rights and source scope", "rights_notes")]:
            value = document.get(key) or metadata.get(key)
            if value:
                sections.append(f'<p class="citation">{_e(label)}: {_e(value)}</p>')
        if warnings or document.get("error"):
            sections.append(f'<p class="note">{_e(warnings or document.get("error"))}</p>')
        sections.append('</article>')
    sections.append('</section><section class="panel" id="method"><div class="label">Method, limitations & reproducibility</div><h2>How to interpret this report</h2>')
    sections.append(f'<p style="font-size:13px">{_e(_METHOD)}</p><p class="label">Active selection</p>' + _table_html(_selection_rows(snapshot)))
    for run in data["runs"]:
        sections.append(f'<h3>Run provenance · {_e(run.get("status"))}</h3>' + _table_html(_provenance_rows(snapshot, run)))
    sections.append('<p class="muted">The research, briefing and teaching editions use the same evidence snapshot. Presentation choices do not change the underlying findings. Full machine-readable provenance is available in the JSONL export.</p></section>')
    sections.append(f'<footer class="footer">DOXA by TACITUS · {_e(edition)} · {_e(generated)}<br>Evidence before inference. Context before conclusions.</footer></main></body></html>')
    return "".join(sections)


def _spreadsheet(snapshot: dict, data: dict, destination: Path):
    try:
        import xlsxwriter
    except ImportError as exc:
        raise ValueError("XLSX export requires XlsxWriter.") from exc
    with xlsxwriter.Workbook(destination, {"strings_to_formulas": False, "strings_to_urls": False, "constant_memory": False}) as workbook:
        workbook.set_properties({"title": _string((snapshot.get("project") or {}).get("name") or "DOXA corpus analysis"), "author": "DOXA by TACITUS", "comments": "Immutable evidence snapshot; interpretive findings require contextual review."})
        title = workbook.add_format({"font_name": "Aptos Display", "font_size": 24, "bold": True, "font_color": "#173C39"})
        heading = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#173C39", "text_wrap": True, "valign": "top", "border": 0})
        body = workbook.add_format({"font_name": "Aptos", "font_size": 10, "text_wrap": True, "valign": "top", "font_color": "#27453D"})
        alternate = workbook.add_format({"font_name": "Aptos", "font_size": 10, "text_wrap": True, "valign": "top", "bg_color": "#F1F5EF", "font_color": "#27453D"})
        number = workbook.add_format({"num_format": "#,##0", "font_color": "#1F7A70", "font_size": 12})
        money = workbook.add_format({"num_format": "$0.0000", "font_color": "#1F7A70"})
        sheets = {name: workbook.add_worksheet(name) for name in ("Overview", "Documents", "Findings", "Evidence", "Authors", "Lenses", "Runs", "Methodology")}
        for sheet in sheets.values():
            sheet.hide_gridlines(2)
            sheet.set_tab_color("#1F7A70")
            sheet.set_default_row(27)
            sheet.set_landscape()
            sheet.fit_to_pages(1, 0)
            sheet.set_footer("&LDOXA by TACITUS&REvidence snapshot · &P of &N")

        def table(name, headers, rows, widths=None):
            sheet = sheets[name]
            sheet.write_row(0, 0, headers + ["Part"], heading)
            sheet.set_row(0, 32)
            sheet.freeze_panes(1, 1)
            sheet.repeat_rows(0)
            sheet.set_column(0, len(headers), 20, body)
            for column, width in (widths or {}).items():
                sheet.set_column(column, column, width, body)
            row_index = 1
            first_rows = []
            for values in rows:
                first_rows.append(row_index)
                cells = []
                for value in values:
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        cells.append([value])
                    else:
                        text = _string(value)
                        cells.append([text[i:i+32000] for i in range(0, len(text), 32000)] or [""])
                parts = max((len(c) for c in cells), default=1)
                if row_index + parts >= 1_048_576:
                    raise ValueError("Export exceeds Excel's row limit. Apply a narrower export filter or use JSONL.")
                for part in range(parts):
                    fmt = body if len(first_rows) % 2 else alternate
                    for column, chunks in enumerate(cells):
                        value = chunks[part] if part < len(chunks) else ""
                        if isinstance(value, (int, float)):
                            sheet.write_number(row_index, column, value, money if name == "Runs" and column == 8 else number)
                        else:
                            sheet.write_string(row_index, column, value, fmt)
                    sheet.write_string(row_index, len(headers), f"{part+1}/{parts}", fmt)
                    sheet.set_row(row_index, 45 if name in {"Findings", "Evidence", "Methodology"} else 30)
                    row_index += 1
            sheet.autofilter(0, 0, max(1, row_index-1), len(headers))
            return first_rows

        documents = data["documents"]
        doc_headers = ["Document ID", "Title", "Author", "Institution", "Type", "Published", "Status", "Words", "Pages", "Filename", "SHA-256", "Source reference", "License", "Extraction warnings / error", "Attribution", "License reference", "Rights and source scope"]
        doc_rows = []
        for doc in documents:
            meta = _obj(doc.get("metadata") or doc.get("metadata_json"))
            doc_rows.append([doc.get("id"), doc.get("title"), doc.get("author"), doc.get("institution"), doc.get("document_type"), doc.get("published_at"), doc.get("status"), int(_number(doc.get("word_count"))), int(_number(doc.get("page_count"))), doc.get("filename"), doc.get("sha256"), doc.get("source_url"), doc.get("license"), doc.get("extraction_warnings") or meta.get("extraction_warnings") or doc.get("error"), doc.get("attribution") or meta.get("attribution"), doc.get("license_url") or meta.get("license_url"), doc.get("rights_notes") or meta.get("rights_notes")])
        doc_lines = table("Documents", doc_headers, doc_rows, {1: 48, 10: 30, 11: 40, 13: 60})
        doc_link = {_string(doc.get("id")): row for doc, row in zip(documents, doc_lines)}
        fheaders = ["Finding ID", "Document ID", "Source title", "Author", "Observation", "Category", "Lens", "Review status", "Stance", "Speaker", "Attribution", "Model assessment (uncalibrated)", "Exact quote", "Interpretation", "Alternative reading", "Reviewer note", "Block", "Start", "End", "Run ID", "Source row"]
        frows, erows = [], []
        for finding in data["findings"]:
            doc, block, location = _source(finding, data)
            frows.append([finding.get("id"), finding.get("document_id"), doc.get("title"), doc.get("author"), finding.get("title"), finding.get("category"), finding.get("lens_id"), finding.get("review_status"), finding.get("stance"), finding.get("speaker"), finding.get("attribution"), finding.get("certainty"), finding.get("quote"), finding.get("explanation"), finding.get("alternative"), finding.get("review_note"), finding.get("block_id"), finding.get("start"), finding.get("end"), finding.get("run_id"), "Source"])
            erows.append([finding.get("id"), finding.get("document_id"), doc.get("title"), location, finding.get("quote"), block.get("text") or finding.get("source_context") or "Context not supplied in snapshot", finding.get("alternative"), finding.get("lens_id"), finding.get("review_status"), doc.get("sha256")])
        flines = table("Findings", fheaders, frows, {4: 38, 12: 70, 13: 70, 14: 70, 15: 45})
        for finding, row in zip(data["findings"], flines):
            target = doc_link.get(_string(finding.get("document_id")))
            if target is not None:
                sheets["Findings"].write_url(row, 20, f"internal:'Documents'!A{target+1}", body, string="Open source record")
        table("Evidence", ["Finding ID", "Document ID", "Source title", "Location", "Exact quote", "Source context", "Alternative reading", "Lens", "Review status", "Source SHA-256"], erows, {2: 48, 3: 40, 4: 80, 5: 100, 6: 65})
        arows = [[author, counts["documents"], counts["words"], counts["findings"], counts["confirmed"], round(counts["findings"] / counts["words"] * 10000, 3) if counts["words"] else "Not available"] for author, counts in sorted(data["authors"].items(), key=lambda x: (-x[1]["findings"], x[0]))]
        table("Authors", ["Author", "Documents", "Words", "Candidate observations", "Confirmed", "Candidates per 10k source words"], arows, {0: 38, 5: 30})
        lens_rows = [[lens, count, sum(f.get("review_status") in {"confirmed", "accepted"} for f in data["findings"] if _string(f.get("lens_id") or "Unspecified") == lens)] for lens, count in data["lenses"].most_common()]
        table("Lenses", ["Lens ID", "Candidate observations", "Confirmed"], lens_rows, {0: 45})
        rheaders = ["Run ID", "Status", "Created", "Completed", "Model profile", "Configuration", "Coverage / progress", "Token usage", "Recorded spend (USD)", "Errors"]
        table("Runs", rheaders, [[r.get("id"), r.get("status"), r.get("created_at"), r.get("completed_at"), r.get("model_profile"), r.get("config"), r.get("progress"), r.get("usage"), _number(r.get("spent_usd")), r.get("errors") or r.get("errors_json")] for r in data["runs"]], {4: 70, 5: 80, 6: 60, 7: 45, 9: 60})
        method_rows = [["Interpretation", _METHOD], ["Edition", snapshot.get("profile", "research")], ["Generated", snapshot.get("generated_at")], ["Project", snapshot.get("project")], ["Export filters", snapshot.get("filters", {})], ["Metrics / coverage", snapshot.get("metrics", {})], ["Workbook safety", "All source strings are literal cell values, never formulas. Long values continue across rows with the Part column. Source hyperlinks point only to this workbook."], ["Denominators", "Author rates use source words. They include all exported candidate observations regardless of review status, and do not establish ideology or private belief."], ["Comparability", "Compare runs only when corpus selection, extraction, lenses and model profiles are held constant or their differences are disclosed."]]
        table("Methodology", ["Topic", "Details"], method_rows, {0: 30, 1: 120})
        overview = sheets["Overview"]
        overview.set_column("A:A", 38)
        overview.set_column("B:B", 23)
        overview.set_column("D:K", 16)
        overview.merge_range("A1:K2", "DOXA / TACITUS", title)
        overview.merge_range("A4:K4", _string((snapshot.get("project") or {}).get("name") or "Corpus analysis"), title)
        overview.merge_range("A6:K6", _PROFILES.get(snapshot.get("profile"), _PROFILES["research"])[0] + " · " + _string(snapshot.get("generated_at")), body)
        overview.write_row(8, 0, ["Measure", "Value"], heading)
        overview_rows = [("Source documents", len(documents)), ("Source words", data["word_count"]), ("Source pages", data["page_count"]), ("Candidate observations", len(data["findings"])), ("Confirmed by reviewer", sum(data["reviews"].get(x, 0) for x in ("confirmed", "accepted"))), ("Awaiting review", data["reviews"].get("unreviewed", 0))]
        for row, (label, value) in enumerate(overview_rows, 9):
            overview.write_string(row, 0, label, body)
            overview.write_number(row, 1, value, number)
        overview.write_row(17, 0, ["Category", "Candidates"], heading)
        for row, (label, count) in enumerate(data["categories"].most_common(), 18):
            overview.write_string(row, 0, label, body)
            overview.write_number(row, 1, count, number)
        if data["categories"]:
            chart = workbook.add_chart({"type": "bar"})
            chart.add_series({"name": "Candidate observations", "categories": ["Overview", 18, 0, 17+len(data["categories"]), 0], "values": ["Overview", 18, 1, 17+len(data["categories"]), 1], "fill": {"color": "#1F7A70"}, "border": {"none": True}, "data_labels": {"value": True}})
            chart.set_title({"name": "Patterns in this corpus"})
            chart.set_legend({"none": True})
            chart.set_chartarea({"border": {"none": True}, "fill": {"color": "#FFFEF8"}})
            chart.set_x_axis({"major_gridlines": {"visible": False}, "min": 0})
            overview.insert_chart("D9", chart, {"x_scale": 1.4, "y_scale": 1.3})
        review_row = max(20 + len(data["categories"]), 30)
        overview.write_row(review_row, 0, ["Review status", "Candidates"], heading)
        for row, (label, count) in enumerate(data["reviews"].most_common(), review_row+1):
            overview.write_string(row, 0, label, body)
            overview.write_number(row, 1, count, number)
        if data["reviews"]:
            chart = workbook.add_chart({"type": "doughnut"})
            chart.add_series({"name": "Review accountability", "categories": ["Overview", review_row+1, 0, review_row+len(data["reviews"]), 0], "values": ["Overview", review_row+1, 1, review_row+len(data["reviews"]), 1], "points": [{"fill": {"color": color}} for color in _COLORS], "data_labels": {"value": True}})
            chart.set_title({"name": "Review accountability"})
            chart.set_chartarea({"border": {"none": True}})
            overview.insert_chart("D31", chart, {"x_scale": 1.4})
        overview.merge_range(review_row+len(data["reviews"])+3, 0, review_row+len(data["reviews"])+7, 1, "Candidate observations require contextual interpretation. Read Evidence and Methodology before drawing conclusions. All counts are computed from this export's source snapshot.", body)
        overview.freeze_panes(8, 0)


def _csv_safe(value) -> str:
    text = _string(value)
    # CSV readers may interpret source text as a formula, including after spaces.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _pdf_fallback(snapshot: dict, data: dict, destination: Path):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.graphics.shapes import Drawing, Rect, String
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise ValueError("PDF export requires WeasyPrint or ReportLab. Install the report dependencies.") from exc
    font, italic = "Helvetica", "Helvetica-Oblique"
    # Embed a local Unicode font where available; never fetch a font.
    local_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    local_italic = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf")
    if local_font.is_file():
        if "DoxaSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DoxaSans", str(local_font)))
        font = "DoxaSans"
        if local_italic.is_file():
            if "DoxaSansItalic" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DoxaSansItalic", str(local_italic)))
            italic = "DoxaSansItalic"
        else:
            italic = font
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font
    styles.add(ParagraphStyle(name="DoxaTitle", fontName=font, fontSize=27, leading=32, textColor=colors.HexColor("#173C39"), spaceAfter=20))
    styles.add(ParagraphStyle(name="DoxaQuote", fontName=italic, fontSize=11, leading=16, leftIndent=12, borderColor=colors.HexColor("#1F7A70"), borderWidth=2, borderPadding=9, spaceBefore=10, spaceAfter=10))
    styles["BodyText"].fontSize = 9
    styles["BodyText"].leading = 14
    doc = SimpleDocTemplate(str(destination), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=20*mm, bottomMargin=18*mm, title=_string((snapshot.get("project") or {}).get("name")), author="DOXA by TACITUS")
    story = []

    def paragraph(value, style="BodyText"):
        # Splitting enormous text into printable paragraphs prevents a single
        # unsplittable flowable without discarding any source characters.
        text = _string(value)
        for offset in range(0, max(1, len(text)), 5000):
            story.append(Paragraph(escape(text[offset:offset+5000]).replace("\n", "<br/>"), styles[style]))
    paragraph("DOXA / TACITUS", "Heading2")
    paragraph((snapshot.get("project") or {}).get("name") or "Corpus analysis", "DoxaTitle")
    paragraph(_PROFILES.get(snapshot.get("profile"), _PROFILES["research"])[0] + " · " + _string(snapshot.get("generated_at")))
    if _lexical(data):
        paragraph("LITERAL BASELINE ONLY: exact word mentions. No semantic interpretation was performed. Mentions do not establish bias, prejudice, endorsement or an author's beliefs.")
    story.append(Spacer(1, 12))
    stats = [["DOCUMENTS", "SOURCE WORDS", "OBSERVATIONS", "CONFIRMED"], [str(len(data["documents"])), f'{data["word_count"]:,}', str(len(data["findings"])), str(sum(data["reviews"].get(x, 0) for x in ("confirmed", "accepted")))]]
    table = Table(stats, colWidths=[43*mm]*4)
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#173C39")), ("TEXTCOLOR",(0,0),(-1,0),colors.white), ("BACKGROUND",(0,1),(-1,1),colors.HexColor("#EDF2E9")), ("FONTSIZE",(0,0),(-1,0),8), ("FONTSIZE",(0,1),(-1,1),19), ("TOPPADDING",(0,0),(-1,-1),10), ("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    story.append(table)
    paragraph("Patterns in this selection", "Heading2")
    if data["categories"]:
        items = data["categories"].most_common(12)
        chart = Drawing(470, 30 + len(items)*29)
        maximum = max(data["categories"].values())
        for index, (label, count) in enumerate(items):
            y = (len(items)-index-1)*29 + 10
            chart.add(String(0, y+6, label.replace("_", " ")[:32], fontName=font, fontSize=8, fillColor=colors.HexColor("#405354")))
            length = max(2, count/maximum*220)
            chart.add(Rect(190, y, length, 20, rx=3, ry=3, fillColor=colors.HexColor(_COLORS[index % len(_COLORS)]), strokeColor=None))
            chart.add(String(196+length, y+6, str(count), fontName=font, fontSize=9, fillColor=colors.HexColor("#173C39")))
        story.append(chart)
    for label, count in data["categories"].most_common():
        paragraph(f"{label.replace('_', ' ')}: {count} candidate observations")
    paragraph("Coverage and limitations", "Heading2")
    coverage_rows, coverage_notice = _coverage_summary(snapshot, data)
    for label, value in coverage_rows:
        paragraph(f"{label}: {value}")
    paragraph(coverage_notice)
    paragraph("Evidence ledger", "Heading2")
    evidence_limit = _EVIDENCE_LIMITS.get(snapshot.get("profile", "research"), 100)
    visible_findings = data["findings"][:evidence_limit]
    if len(data["findings"]) > evidence_limit:
        paragraph(f"Showing {len(visible_findings):,} of {len(data['findings']):,} observations. This edition limits its evidence appendix for readable printing. All charts and totals describe the full selected snapshot. Export XLSX, CSV or JSONL for complete evidence, or apply a narrower export filter.")
    for index, finding in enumerate(visible_findings, 1):
        document, block, location = _source(finding, data)
        paragraph(f"{index:03d} / {finding.get('title') or finding.get('category') or 'Passage observation'}", "Heading3")
        paragraph(f"Lens: {finding.get('lens_id') or 'Unspecified'} · Review: {finding.get('review_status') or 'unreviewed'} · Stance: {finding.get('stance') or 'unspecified'}")
        paragraph(finding.get("quote"), "DoxaQuote")
        paragraph(f"Source: {document.get('title') or document.get('filename') or finding.get('document_id')} · {document.get('author') or 'Unknown author'} · {location}")
        paragraph("Interpretation: " + _string(finding.get("explanation")))
        paragraph("Alternative reading: " + _string(finding.get("alternative") or "Not supplied; review source context."))
        paragraph(f"Speaker: {finding.get('speaker') or 'Unspecified'} · Attribution: {finding.get('attribution') or 'Unspecified'} · Model assessment (uncalibrated): {finding.get('certainty') or 'Not supplied'}")
        paragraph("Source context: " + _string(block.get("text") or finding.get("source_context") or "Not supplied in snapshot."))
        if finding.get("review_note"):
            paragraph("Reviewer note: " + _string(finding["review_note"]))
    paragraph("Source register", "Heading2")
    for document in data["documents"]:
        metadata = _obj(document.get("metadata") or document.get("metadata_json"))
        paragraph(document.get("title") or document.get("filename"), "Heading3")
        paragraph(f"{document.get('author') or 'Unknown'} · {document.get('document_type') or 'document'} · {document.get('status') or 'unknown'} · {_number(document.get('word_count')):,.0f} words")
        paragraph(f"Document ID: {document.get('id')} · SHA-256: {document.get('sha256') or 'Not supplied'}")
        paragraph(f"Source reference: {document.get('source_url') or 'Not supplied'} · Rights/license: {document.get('license') or 'Not supplied'}")
        for label, key in [("Attribution", "attribution"), ("License reference", "license_url"), ("Rights and source scope", "rights_notes")]:
            value = document.get(key) or metadata.get(key)
            if value:
                paragraph(f"{label}: {_string(value)}")
    paragraph("Method and reproducibility", "Heading2")
    paragraph(_METHOD)
    for label, value in _selection_rows(snapshot):
        paragraph(f"{label}: {value}")
    if snapshot.get("profile") == "teaching":
        paragraph("Discussion: Who is speaking? Is this quotation, endorsement or rebuttal? How does the selected lens affect the reading? What evidence would change your conclusion?")
    for run in data["runs"]:
        paragraph(f"Run {run.get('id')}: {run.get('status')}", "Heading3")
        for label, value in _provenance_rows(snapshot, run):
            paragraph(f"{label}: {value}")
    paragraph("Full telemetry, source manifests and technical diagnostics are retained in the JSONL export and saved report snapshot.")

    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#718279"))
        canvas.drawString(18*mm, 10*mm, "DOXA by TACITUS · Evidence before inference")
        canvas.drawRightString(A4[0]-18*mm, 10*mm, str(document.page))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def build_report(snapshot: dict, format: str, output_dir: Path) -> Path:
    """Create a real downloadable artifact, with no external network resources."""
    format = format.lower().lstrip(".")
    if format not in {"html", "pdf", "xlsx", "csv", "jsonl"}:
        raise ValueError("Unsupported report format. Choose HTML, PDF, XLSX, CSV or JSONL.")
    if snapshot.get("profile", "research") not in _PROFILES:
        raise ValueError("Unknown report profile. Choose research, briefing or teaching.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = _string((snapshot.get("project") or {}).get("name") or "corpus")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:65] or "corpus"
    path = output_dir / f"doxa-{slug}-{snapshot.get('profile', 'research')}-{uuid.uuid4().hex[:10]}.{format}"
    data = _prepare(snapshot)
    try:
        if format == "html":
            path.write_text(_html(snapshot, data), encoding="utf-8")
        elif format == "xlsx":
            _spreadsheet(snapshot, data, path)
        elif format == "pdf":
            try:
                from weasyprint import HTML
                def no_network(url, *args, **kwargs):
                    raise ValueError("External report resources are disabled.")
                HTML(string=_html(snapshot, data), url_fetcher=no_network).write_pdf(path)
            except (ImportError, OSError):
                _pdf_fallback(snapshot, data, path)
        elif format == "csv":
            headers = ["id", "document_id", "source_title", "author", "lens_id", "category", "review_status", "stance", "speaker", "attribution", "certainty", "title", "quote", "explanation", "alternative", "block_id", "start", "end", "run_id", "source_context", "source_sha256", "location", "source_license", "source_attribution", "source_license_url", "source_rights_notes"]
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                for finding in data["findings"]:
                    document, block, location = _source(finding, data)
                    metadata = _obj(document.get("metadata") or document.get("metadata_json"))
                    record = {**finding, "source_title": document.get("title"), "author": document.get("author"), "source_context": block.get("text") or finding.get("source_context"), "source_sha256": document.get("sha256"), "location": location, "source_license": document.get("license"), "source_attribution": document.get("attribution") or metadata.get("attribution"), "source_license_url": document.get("license_url") or metadata.get("license_url"), "source_rights_notes": document.get("rights_notes") or metadata.get("rights_notes")}
                    writer.writerow([_csv_safe(record.get(key)) for key in headers])
        else:
            with path.open("w", encoding="utf-8") as stream:
                def line(kind, value):
                    stream.write(json.dumps({"record_type": kind, "data": value}, ensure_ascii=False, default=str) + "\n")
                line("manifest", {k:v for k,v in snapshot.items() if k not in {"documents", "findings", "runs"}})
                for record in data["documents"]:
                    line("document", record)
                for record in data["findings"]:
                    line("finding", record)
                for record in data["runs"]:
                    line("run", record)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
