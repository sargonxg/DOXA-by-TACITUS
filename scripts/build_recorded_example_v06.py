#!/usr/bin/env python3
"""Publish readable real-source examples from the frozen offline starter.

No inference, mutable Store, new review or rewritten historical result occurs.
PDF/HTML show a declared case appendix; XLSX/JSON retain every selected record.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import html
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.recorded_starter import canonical_hash, load_recorded_example  # noqa: E402

LABELS = {"meets_criterion": "Criterion met", "does_not_meet": "Criterion not met",
          "not_applicable": "Not applicable", "uncertain": "Uncertain"}
ORDER = ("meets_criterion", "does_not_meet", "not_applicable", "uncertain")
GUIDE = {
    "meets_criterion": "Apply the named criterion at its stated level. Descriptive framing is not a prejudice verdict. Check the attributed voice, relevant context and framework boundaries before drawing a conclusion.",
    "does_not_meet": "This retained criterion decision is scoped to its source unit. It does not establish that the whole article, author or organization is unbiased.",
    "not_applicable": "The model recorded this criterion as not applicable here. This is distinct from a negative decision and from missing processing.",
    "uncertain": "Uncertainty remains an unresolved interpretation requiring review. It must not be silently counted as either a positive finding or completed negative evidence.",
}


def selected_cases(payload):
    choices = []
    for outcome in ORDER:
        rows = [item for record in payload["records"] for item in record["assessments"] if item["result"] == outcome]
        rows.sort(key=lambda item: (item["criterion_kind"] == "descriptive", not item.get("detail_supplied"), item["id"]))
        choices.extend(rows[:2])
    return choices


def render_html(payload, cases):
    escape = html.escape
    counts = payload["selection"]["counts"]
    documents = {item["id"]: item for item in payload["documents"]}
    maximum = max(counts["outcomes"].values())
    result = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>DOXA · Twenty real sources, recorded readings</title>",
              "<style>body{max-width:1040px;margin:auto;padding:36px;font:16px/1.6 system-ui,sans-serif;color:#173c39;background:#fafbf7}h1{font-size:42px;line-height:1.1}h2{margin-top:40px}.eyebrow{letter-spacing:.15em;text-transform:uppercase;color:#607569}.notice{padding:18px;background:#fff1d6;border-left:5px solid #ad7b28}.stats{display:flex;flex-wrap:wrap;gap:12px}.stat{background:#e8f0e9;padding:18px;min-width:140px}.stat strong{font-size:28px;display:block}.bar{height:15px;background:#357d79;display:inline-block;margin:0 10px}.case{background:white;border:1px solid #d6dfd5;padding:22px;margin:22px 0;break-inside:avoid}blockquote{margin:15px 0;border-left:4px solid #357d79;padding:12px;background:#f0f4ee;white-space:pre-wrap}small{color:#546a60}a{color:#156b66}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #d6dfd5}code{overflow-wrap:anywhere}@media(max-width:500px){body{padding:18px}h1{font-size:32px}}</style>",
              "<p class='eyebrow'>DOXA by TACITUS · Recorded evidence reader</p><h1>Twenty real sources.<br>Inspectable model readings.</h1>",
              f"<p class='notice'>{escape(payload['notice'])}</p><div class='stats'>",
              *[f"<div class='stat'><strong>{counts[key]:,}</strong>{label}</div>" for key, label in (("documents", "Real source articles"), ("assessments", "Retained decisions"), ("exact_references", "Checked exact references"))],
              "</div><h2>What this example actually shows</h2>",
              f"<p>{counts['unit_states'].get('accepted', 0)} accepted source units and {sum(v for k,v in counts['unit_states'].items() if k!='accepted')} incomplete unit remain separate. This is a deliberately selected development packet across 2020–2025, not a representative sample. Criterion outcomes are correlated within articles and differ in meaning across frameworks. They must not become a universal bias percentage.</p>",
              "<h2>All four recorded outcomes</h2>"]
    for outcome in ORDER:
        number = counts["outcomes"][outcome]
        result.append(f"<p>{LABELS[outcome]} <span class='bar' style='width:{max(2,number*65/maximum):.2f}%'></span> {number:,}</p>")
    result.extend(["<h2>Method and provenance</h2>", f"<p>Original provider: {escape(payload['parent']['provider'])}; model: <strong>{escape(payload['parent']['model'])}</strong>. The managed model does not establish immutable weight identity. The full parent run is <strong>partial</strong>: 204,342 of 204,421 expected decisions were retained; 79 remain missing. No inference or human review was added by this publication.</p>",
                   f"<p>Parent run <code>{escape(payload['parent']['run_id'])}</code><br>Parent package SHA-256 <code>{payload['parent']['package_sha256']}</code></p>",
                   "<p>The complete frozen rubric and actual recorded fields are in the JSON snapshot and XLSX. Exact references establish provenance, not whether the interpretation is correct. The reading guidance below is application/AI-authored editorial explanation, separately labeled from original model text.</p>",
                   f"<h2>Case appendix · {len(cases)} of {counts['assessments']} decisions</h2>"])
    for index, assessment in enumerate(cases, 1):
        document = documents[assessment["document_id"]]
        ref = assessment["evidence_refs"][0]
        block = next(block for block in document["blocks"] if block["id"] == ref["block_id"])
        rationale = assessment.get("explanation") or "No detailed model-written rationale was retained for this compact decision. Its original source-unit references remain available."
        result.extend([f"<article class='case'><small>Case {index} · {LABELS[assessment['result']]} · {escape(assessment['criterion_kind'])}</small><h3>{escape(document['title'])}</h3>",
                       f"<p><strong>{escape(assessment['criterion_key'])}</strong> · {escape(document['author'])} · {escape(str(document['published_at']))}</p>",
                       f"<p>Recorded voice: {escape(str(assessment.get('attribution','unknown')))}; document stance: {escape(str(assessment.get('document_stance','unknown')))}.</p>",
                       f"<blockquote>{escape(ref['quote'])}</blockquote><small>Exact source reference: {escape(ref['block_id'])}, characters {ref['start']}–{ref['end']}.</small>",
                       f"<h4>Original recorded model reading</h4><p>{escape(rationale)}</p>",
                       f"<h4>Source paragraph context</h4><blockquote>{escape(block['text'])}</blockquote>",
                       f"<h4>Reading guide · application/AI-authored, not human adjudication</h4><p>{GUIDE[assessment['result']]}</p>",
                       f"<p><small>{escape(document['attribution'])} <a href='{escape(document['license_url'],quote=True)}'>CC BY 3.0</a>. See the original <a href='{escape(document['source_url'],quote=True)}'>article</a>. Assessment <code>{escape(assessment['id'])}</code>.</small></p></article>"])
    result.append("<h2>Source register and credits</h2><table><tr><th>Source</th><th>Attribution</th><th>Selected unit state</th></tr>")
    by_record = {row["document_id"]: row for row in payload["records"]}
    for document in payload["documents"]:
        states = Counter(row["status"] for row in by_record[document["id"]]["unit_statuses"])
        result.append(f"<tr><td><a href='{escape(document['source_url'],quote=True)}'>{escape(document['title'])}</a><br><small>{escape(document['source_id'])}</small></td><td>{escape(document['attribution'])}<br><small>Translators: {escape(json.dumps(document['translators'],ensure_ascii=False))}; CC BY 3.0.</small></td><td>{escape(str(dict(states)))}</td></tr>")
    result.append("</table><p>The JSON/XLSX preserve all selected source text, credits, unit statuses, decisions and original reference arrays. This reader's eight cases are a bounded explanatory appendix. Missing decisions and absent independent reference labels remain visible.</p></html>")
    return "\n".join(result).encode()


def render_pdf(payload, cases):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.pdfbase import pdfmetrics
    from doxa.pilot_reports import _font
    font, font_file = _font()
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font
        if hasattr(style, "fontSize"):
            style.leading = style.fontSize * 1.45
    styles["Title"].fontSize = 30
    styles["Title"].leading = 36
    styles["Title"].alignment = TA_LEFT
    styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 13
    styles.add(ParagraphStyle("QuoteReader", parent=styles["BodyText"], backColor=colors.HexColor("#eef4ee"),
                              borderPadding=10, spaceBefore=10, spaceAfter=12))
    story = []
    def text(value, style="BodyText"):
        story.append(Paragraph(html.escape(str(value)).replace("\n", "<br/>"), styles[style]))
        story.append(Spacer(1, 6))
    counts = payload["selection"]["counts"]
    text("DOXA BY TACITUS · RECORDED EVIDENCE READER", "Heading3")
    text("Twenty real sources.\nInspectable model readings.", "Title")
    text(payload["notice"], "QuoteReader")
    text(f"20 articles · {counts['assessments']:,} retained decisions · {counts['exact_references']:,} exact references", "Heading2")
    text("A deliberately selected development packet across 2020–2025. Counts describe retained criterion outcomes, not the prevalence of prejudice. Multiple correlated criteria and descriptive frames cannot be collapsed into a universal bias score.")
    chart = Drawing(460, 125)
    maximum = max(counts["outcomes"].values())
    for index, outcome in enumerate(ORDER):
        y = 100 - index * 27
        number = counts["outcomes"][outcome]
        chart.add(String(0, y, LABELS[outcome], fontName=font, fontSize=9, fillColor=colors.HexColor("#173c39")))
        chart.add(Rect(140, y-3, max(2,number*255/maximum), 12, fillColor=colors.HexColor("#357d79"), strokeColor=None))
        chart.add(String(412, y, str(number), fontName=font, fontSize=9))
    story.append(chart)
    text(f"Unit coverage: {counts['unit_states'].get('accepted',0)} accepted of {sum(counts['unit_states'].values())} selected source units; {sum(value for key,value in counts['unit_states'].items() if key!='accepted')} incomplete. Full parent: 204,342 of 204,421 expected decisions; 79 missing. No new inference or human adjudication was performed.")
    text("Method and evidence boundaries", "Heading2")
    text(f"Original provider: {payload['parent']['provider']}; requested model: {payload['parent']['model']}. Managed weights are not immutably pinned by that identifier. Original question: {payload['method']['question']}")
    text("The complete frozen frameworks and recorded fields are in JSON/XLSX. Exact quotations establish provenance, not interpretation correctness. The reading guides are application/AI-authored editorial explanation, separately attributed from original model output.")
    text("Parent run: " + payload["parent"]["run_id"])
    text("Parent package SHA-256: " + payload["parent"]["package_sha256"])
    documents = {item["id"]: item for item in payload["documents"]}
    rendered_text = []
    for index, assessment in enumerate(cases, 1):
        story.append(PageBreak())
        document = documents[assessment["document_id"]]
        ref = assessment["evidence_refs"][0]
        block = next(item for item in document["blocks"] if item["id"] == ref["block_id"])
        text(f"CASE {index}/{len(cases)} · {LABELS[assessment['result']]} · {assessment['criterion_kind']}", "Heading3")
        text(document["title"], "Heading1")
        text(f"{document['author']} · {document['published_at']} · {assessment['criterion_key']}")
        text(f"Recorded attributed voice: {assessment.get('attribution','unknown')}; document stance: {assessment.get('document_stance','unknown')}.")
        text(ref["quote"], "QuoteReader")
        text(f"Exact reference: {ref['block_id']}, Unicode characters {ref['start']}–{ref['end']}.")
        text("Original recorded model reading", "Heading2")
        text(assessment.get("explanation") or "No detailed model-written rationale was retained for this compact decision. Its original source-unit references remain available.")
        text("Source paragraph context", "Heading2")
        text(block["text"], "QuoteReader")
        text("Reading guide · application/AI-authored", "Heading2")
        text(GUIDE[assessment["result"]])
        text(document["attribution"] + " CC BY 3.0. " + document["license_url"])
        story.append(Paragraph(f'<link href="{html.escape(document["source_url"], quote=True)}" color="#156b66">Open original source article</link>', styles["BodyText"]))
        text("Original assessment: " + assessment["id"])
        rendered_text.extend([document["title"], document["author"], ref["quote"], block["text"]])
    story.append(PageBreak())
    text("Twenty-source register and credits", "Heading1")
    text(f"The case appendix shows {len(cases)} of {counts['assessments']} retained decisions. XLSX/JSON preserve every selected record and all source paragraphs; no positive, negative, uncertain or not-applicable category is dropped.")
    for document in payload["documents"]:
        text(document["title"], "Heading3")
        text(document["attribution"] + " CC BY 3.0.")
        text("Translators: " + json.dumps(document["translators"], ensure_ascii=False))
        text(f"Source {document['source_id']} · content SHA-256 {document['sha256']}")
        rendered_text.extend([document["title"], document["attribution"], json.dumps(document["translators"],ensure_ascii=False)])
    glyphs = pdfmetrics.getFont(font).face.charToGlyph
    missing = sorted({ord(character) for value in rendered_text for character in value if ord(character)>32 and ord(character) not in glyphs})
    if missing:
        text("Font coverage: some source characters may not render in this local PDF font. Original Unicode remains in HTML/XLSX/JSON. Missing: " + ", ".join(f"U+{value:04X}" for value in missing))
    buffer = io.BytesIO()
    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#607569"))
        canvas.drawString(42, 27, "DOXA · Recorded model readings · Independent interpretation validation pending")
        canvas.drawRightString(A4[0]-42, 27, str(document.page))
    pdf = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=40, bottomMargin=45, title="DOXA · Twenty real sources, recorded readings",
                            author="DOXA by TACITUS", invariant=1)
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue(), {"font_file": Path(font_file).name, "missing_code_points": [f"U+{value:04X}" for value in missing]}


def render_xlsx(payload):
    import xlsxwriter
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    workbook.set_properties({"title": payload["title"], "author": "DOXA by TACITUS", "created": datetime(2026, 11, 18)})
    header = workbook.add_format({"bold": True, "bg_color": "#173C39", "font_color": "white", "text_wrap": True})
    wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
    def sheet(name, titles):
        worksheet = workbook.add_worksheet(name)
        worksheet.freeze_panes(1,0)
        worksheet.set_column(0,len(titles)-1,28,wrap)
        worksheet.write_row(0,0,titles,header)
        return worksheet
    method = sheet("Method", ["Field", "Recorded value"])
    entries = [("Notice", payload["notice"]), ("Origin", payload["mode"]), ("Fresh inference", 0), ("Independent human labels", False), ("Interpretation accuracy", "not measured"), ("Parent run", payload["parent"]["run_id"]), ("Parent status", "partial"), ("Parent missing decisions", 79), ("Selection", payload["selection"]["purpose"]), ("Counts", json.dumps(payload["selection"]["counts"])), ("Case appendix", f"8 of {payload['selection']['counts']['assessments']} decisions in PDF/HTML; all retained here")]
    for row, values in enumerate(entries,1):
        method.write_row(row,0,values)
    sources = sheet("Sources", ["Document ID", "Canonical source", "Title", "Author", "Published", "URL", "License", "Attribution", "Translators", "Content SHA256"])
    paragraphs = sheet("Source paragraphs", ["Document ID", "Block ID", "Paragraph", "Text part", "Exact text"])
    paragraph_row = 1
    for row, document in enumerate(payload["documents"],1):
        sources.write_row(row,0,[document["id"], document["source_id"], document["title"], document["author"], document["published_at"], document["source_url"], document["license"], document["attribution"], json.dumps(document["translators"],ensure_ascii=False), document["sha256"]])
        for block in document["blocks"]:
            for ordinal,start in enumerate(range(0,len(block["text"]),15000),1):
                paragraphs.write_row(paragraph_row,0,[document["id"],block["id"],block.get("paragraph"),ordinal,block["text"][start:start+15000]])
                paragraph_row += 1
    assessments = sheet("Assessments", ["Assessment ID", "Document ID", "Unit ID", "Criterion", "Kind", "Outcome", "Attributed voice", "Document stance", "Exact quote", "Original explanation", "Raw JSON parts key"])
    units = sheet("Units", ["Document ID", "Unit ID", "Original status", "Accepted criteria", "Failed criteria", "Pending criteria", "Raw JSON parts key"])
    raw_sheet = sheet("Full JSON parts", ["Record type", "Record ID", "Part", "JSON characters; concatenate by part"])
    raw_row = 1
    def raw_parts(kind, identity, value):
        nonlocal raw_row
        serialized = json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        for ordinal,start in enumerate(range(0,len(serialized),15000),1):
            raw_sheet.write_row(raw_row,0,[kind,identity,ordinal,serialized[start:start+15000]])
            raw_row += 1
    row, unit_row = 1,1
    for record in payload["records"]:
        raw_parts("archive_record",record["document_id"],record)
        for assessment in record["assessments"]:
            assessments.write_row(row,0,[assessment["id"],record["document_id"],assessment["unit_id"],assessment["criterion_key"],assessment["criterion_kind"],assessment["result"],assessment.get("attribution"),assessment.get("document_stance"),assessment["quote"],assessment.get("explanation","No detailed rationale recorded"),assessment["id"]])
            raw_parts("assessment",assessment["id"],assessment)
            row += 1
        for status in record["unit_statuses"]:
            key=record["document_id"]+":"+status["unit_id"]
            units.write_row(unit_row,0,[record["document_id"],status["unit_id"],status["status"],json.dumps(status.get("accepted_criteria",[])),json.dumps(status.get("failed_criteria",[])),json.dumps(status.get("pending_criteria",[])),key])
            raw_parts("unit_status",key,status)
            unit_row += 1
    for key in ("parent","method","selection"):
        raw_parts(key,key,payload[key])
    for document in payload["documents"]:
        raw_parts("source_snapshot",document["id"],{**document["metadata"], "blocks": document["blocks"]})
    workbook.close()
    return output.getvalue()


def build(root=ROOT):
    root = Path(root)
    data = root / "doxa/data"
    payload = load_recorded_example(data)
    cases = selected_cases(payload)
    pdf, font = render_pdf(payload,cases)
    artifacts = {"html": render_html(payload,cases), "pdf": pdf, "xlsx": render_xlsx(payload),
                 "json.gz": (data / "recorded-starter.json.gz").read_bytes()}
    files=[]
    for extension, raw in artifacts.items():
        output = root / "examples" / ("v06-recorded-example."+extension)
        output.write_bytes(raw)
        packaged = "recorded-starter.json.gz" if extension=="json.gz" else "recorded-example."+extension
        if extension != "json.gz":
            (data / packaged).write_bytes(raw)
        files.append({"file":output.name,"packaged_file":packaged,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
    manifest = {"schema_version":"doxa-recorded-example-publication-1","files":files,
                "snapshot_sha256":canonical_hash(payload),"parent_package_sha256":payload["parent"]["package_sha256"],
                "counts":payload["selection"]["counts"],"pdf_html_case_appendix":len(cases),
                "shown_assessment_ids":[item["id"] for item in cases],"pdf_font":font,
                "model_requests":0,"independent_human_labels":False,"interpretation_accuracy":None,
                "commentary_authorship":"application/AI-authored reading guidance; not human adjudication"}
    raw=(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n").encode()
    (root / "examples/v06-recorded-example.manifest.json").write_bytes(raw)
    (data / "recorded-example.manifest.json").write_bytes(raw)
    return manifest


if __name__=="__main__":
    print(json.dumps(build(),indent=2))
