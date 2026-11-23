#!/usr/bin/env python3
"""Publish or verify the source-first 60-article bias study without network or inference."""

import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.recorded_starter import canonical_hash  # noqa: E402
from scripts.bias_study.cohort import freeze, summarize, STATE_LABELS  # noqa: E402
from scripts.bias_study.diagnostics import sensitivity  # noqa: E402
from scripts.bias_study.graph import build_graph, case_trace, graph_counts, graph_index  # noqa: E402
from scripts.bias_study.publication import html, reader_data  # noqa: E402
from scripts.bias_study.readings import cases, REFERENCES  # noqa: E402

PUBLIC = ROOT / "frontend/public/studies/muslim-coverage"
MANIFEST = ROOT / "examples/muslim-coverage.manifest.json"


def workbook(study, summary, readings, diagnostics):
    import xlsxwriter

    buff = io.BytesIO()
    book = xlsxwriter.Workbook(
        buff, {"in_memory": True, "strings_to_urls": False, "strings_to_formulas": False}
    )
    book.set_properties(
        {
            "title": "Reading anti-Muslim bias in the news",
            "author": "DOXA by TACITUS",
            "created": datetime(2026, 1, 1),
        }
    )
    header = book.add_format(
        {"bold": True, "font_color": "#ffffff", "bg_color": "#225b4e", "valign": "vcenter"}
    )

    def sheet(name, columns, widths):
        ws = book.add_worksheet(name)
        ws.freeze_panes(1, 1)
        ws.set_row(0, 30)
        ws.write_row(0, 0, columns, header)
        for idx, width in enumerate(widths):
            ws.set_column(idx, idx, width)
        return ws

    metrics = sheet(
        "Findings",
        ["Criterion", "Framework", *STATE_LABELS.values(), "All articles", "Frozen rule"],
        [52, 27, 15, 21, 22, 30, 24, 15, 95],
    )
    for i, (key, row) in enumerate(summary["criteria"].items(), 1):
        metrics.write_row(
            i, 0, [row["label"], row["lens"], *[row["states"][k] for k in STATE_LABELS],
                   summary["articles"], row["definition"]]
        )
    metrics.write(9, 0, "Article counts overlap across criteria.")
    metrics.write(
        10, 0, "An article with a flag and an uncertain unit counts once as flagged for that criterion."
    )
    metrics.write(11, 0, "Uncertainty in every unit is retained in the Decisions sheet.")
    metrics.write(12, 0, "Counts describe this topic-selected cohort. Accuracy has no independent estimate.")
    chart = book.add_chart({"type": "bar"})
    chart.add_series(
        {
            "name": "Articles with a flag",
            "categories": ["Findings", 1, 0, 6, 0],
            "values": ["Findings", 1, 2, 6, 2],
            "fill": {"color": "#22695d"},
            "data_labels": {"value": True},
        }
    )
    chart.set_x_axis({"min": 0, "max": summary["articles"], "name": "Articles (60 in the cohort)"})
    chart.set_legend({"none": True})
    chart.set_size({"width": 710, "height": 300})
    metrics.insert_chart("A16", chart)
    keys = list(summary["criteria"])
    docs = sheet(
        "Articles",
        [
            "ID",
            "Title",
            "Author",
            "Translators",
            "Published",
            "Original article",
            "License",
            "Attribution",
            "Text SHA256",
            "Scored units",
            *keys,
        ],
        [15, 58, 34, 28, 15, 60, 16, 65, 24, 15, *[25] * len(keys)],
    )
    decisions = sheet(
        "Decisions",
        [
            "Assessment",
            "Article",
            "Unit",
            "Framework",
            "Criterion",
            "Recorded outcome",
            "Recorded speaker",
            "Attribution",
            "Speaker stance",
            "Article stance",
            "Proposition",
            "Primary excerpt",
            "Block",
            "Start",
            "End",
            "Grounded detail supplied",
            "Model explanation",
            "Model alternative",
            "Original review status",
        ],
        [32, 15, 30, 25, 30, 23, 30, 22, 18, 18, 65, 85, 25, 10, 10, 23, 85, 85, 20],
    )
    refs = sheet(
        "Evidence spans",
        ["Assessment", "Article", "Sentence", "Block", "Start", "End", "Exact text"],
        [33, 15, 30, 24, 10, 10, 100],
    )
    text = sheet("Source text", ["Article", "Block", "Paragraph text"], [15, 26, 110])
    pos = ref_pos = text_pos = 1
    for i, d in enumerate(study["documents"], 1):
        docs.write_row(
            i,
            0,
            [
                d["id"],
                d["title"],
                d["author"],
                ", ".join(t["name"] for t in d["translators"]),
                d["published_at"][:10],
                d["source_url"],
                d["license"],
                d["attribution"],
                d["txt_sha256"],
                len(d["unit_statuses"]),
                *[STATE_LABELS[summary["article_states"][d["id"]][k]] for k in keys],
            ],
        )
        docs.write_url(i, 5, d["source_url"], string=d["source_url"])
        for b in d["blocks"]:
            text.write_row(text_pos, 0, [d["id"], b["id"], b["text"]])
            text_pos += 1
        for a in d["decisions"]:
            decisions.write_row(
                pos,
                0,
                [
                    a["id"],
                    d["id"],
                    *[
                        a.get(k)
                        for k in (
                            "unit_id",
                            "lens_id",
                            "criterion_id",
                            "result",
                            "speaker",
                            "attribution",
                            "speaker_stance",
                            "document_stance",
                            "proposition",
                            "quote",
                            "block_id",
                            "start",
                            "end",
                            "detail_supplied",
                            "explanation",
                            "alternative",
                            "review_status",
                        )
                    ],
                ],
            )
            pos += 1
            for ref in a["evidence_refs"]:
                refs.write_row(
                    ref_pos,
                    0,
                    [
                        a["id"],
                        d["id"],
                        *[ref[k] for k in ("sentence_id", "block_id", "start", "end", "quote")],
                    ],
                )
                ref_pos += 1
    for ws, end, cols in [
        (docs, len(study["documents"]), 10 + len(keys)),
        (decisions, pos - 1, 19),
        (refs, ref_pos - 1, 7),
        (text, text_pos - 1, 3),
    ]:
        ws.autofilter(0, 0, end, cols - 1)
    notes = sheet(
        "Walkthroughs",
        [
            "Reading",
            "Assessment",
            "Article",
            "Selected passage",
            "Anchor origin",
            "Editorial reading",
            "Review question",
            "Related assessment",
        ],
        [35, 33, 16, 80, 40, 90, 90, 33],
    )
    for i, c in enumerate(readings, 1):
        notes.write_row(
            i,
            0,
            [
                c["heading"],
                c["decision"]["id"],
                c["doc"]["id"],
                c["ref"]["quote"],
                c["anchor_origin"],
                c["reading"],
                c["followup"],
                (c["peer"] or {}).get("id"),
            ],
        )
    research = sheet(
        "Method and sources",
        ["Design choice", "Source", "How it informs the analysis", "Link"],
        [25, 70, 95, 70],
    )
    for i, (kind, title, meaning, url) in enumerate(REFERENCES, 1):
        research.write_row(i, 0, [kind, title, meaning])
        research.write_url(i, 3, url)
    research.write(9, 0, "Selection seed")
    research.write(9, 1, study["selection"]["seed"])
    research.write(10, 0, "Sampling")
    research.write(
        10,
        1,
        "10 distinct stories per year, 2020–2025, selected before reading results. See the report for eligibility.",
    )
    research.write(11, 0, "Model")
    research.write(11, 1, study["parent"]["model"])
    research.write(12, 0, "Source of labels")
    research.write(
        12, 1, "Recorded model readings. The six AI-authored walkthroughs are editorial interpretations."
    )
    coverage = sheet(
        "Sensitivity",
        ["Criterion", "Articles", "Observed flags", "Uncertain without flag", "Incomplete without flag",
         "If all unresolved were flagged"],
        [55, 14, 17, 26, 27, 34],
    )
    omissions = sheet("Year omissions", ["Criterion", "Year omitted", "Flags", "Articles", "Fraction"],
                      [55, 17, 12, 14, 14])
    ratio = book.add_format({"num_format": "0.0%"})
    omission_row = 1
    for i, (key, record) in enumerate(diagnostics.items(), 1):
        label = summary["criteria"][key]["label"]
        coverage.write_row(i, 0, [label, record["articles"], record["observed_flags"],
                                  record["uncertain_without_flag"], record["incomplete_without_flag"],
                                  record["if_all_unresolved_flagged"]])
        for row in record["year_omissions"]:
            omissions.write_row(omission_row, 0, [label, int(row["excluded_year"]), row["flagged"], row["articles"]])
            omissions.write_number(omission_row, 4, row["flagged"] / row["articles"], ratio)
            omission_row += 1
    coverage.write(len(diagnostics) + 2, 0, "Scenarios describe this cohort. They are not accuracy estimates.")
    omissions.autofilter(0, 0, omission_row - 1, 4)
    book.close()
    return buff.getvalue()


def evidence_zip(study, readings):
    buffer = io.BytesIO()

    def store(archive, name, content):
        info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, content)

    notes = [
        {k: c[k] for k in ("slug", "heading", "reading", "followup", "anchor_origin")}
        | {
            "document": c["doc"]["id"],
            "assessment": c["decision"]["id"],
            "reference": c["ref"],
            "peer_assessment": (c["peer"] or {}).get("id"),
        }
        for c in readings
    ]
    with ZipFile(buffer, "w") as archive:
        for d in study["documents"]:
            raw = (ROOT / "corpus/news/articles" / (d["id"] + ".txt")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != d["txt_sha256"]:
                raise ValueError("Published article changed")
            store(archive, "articles/" + d["id"] + ".txt", raw)
        store(
            archive,
            "study.json",
            json.dumps(
                {**study, "editorial_cases": notes, "references": REFERENCES},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
        )
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the published artifacts. Default verifies the checked-in study.",
    )
    parser.add_argument("--offline-zip", type=Path, help="Package the verified study for offline reading.")
    args = parser.parse_args()
    study = freeze(ROOT)
    summary = summarize(study)
    readings = cases(study)
    diagnostics = sensitivity(study, summary)
    graph = build_graph(study, readings)
    index = graph_index(graph)
    traces = {
        node["recordId"]: case_trace(node, index)
        for node in index.values() if "doxa:EditorialNote" in node["@type"]
    }
    counts = graph_counts(graph)
    if (counts["Decision"], counts["SourceVersion"], counts["EvidenceReference"]) != (
        summary["decisions"], summary["articles"], summary["references"],
    ):
        raise ValueError("Evidence graph does not account for the frozen record counts")
    screen = html(study, summary, readings, traces, diagnostics).encode()
    evidence = evidence_zip(study, readings)
    reader = json.dumps(
        reader_data(study, summary, readings, traces, diagnostics), ensure_ascii=False, separators=(",", ":")
    ).encode()
    graph_bytes = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    generated = {"reader.json": reader, "index.html": screen, "evidence.jsonld": graph_bytes}
    public_names = ["index.html", "reader.js", "reader.json", "report.pdf", "evidence.xlsx", "sources.zip", "evidence.jsonld"]
    if args.write:
        from weasyprint import HTML

        PUBLIC.mkdir(parents=True, exist_ok=True)
        print_html = html(study, summary, readings, traces, diagnostics, printing=True)
        pdf = HTML(string=print_html).write_pdf(pdf_tags=True)
        sheets = workbook(study, summary, readings, diagnostics)
        for name, data in {**generated, "report.pdf": pdf, "evidence.xlsx": sheets, "sources.zip": evidence}.items():
            (PUBLIC / name).write_bytes(data)
    else:
        for name, expected in generated.items():
            if (PUBLIC / name).read_bytes() != expected:
                raise ValueError(f"{name} no longer matches the frozen corpus and commentary")
        # Decompressed member equality is portable across zlib versions.
        with ZipFile(io.BytesIO(evidence)) as expected, ZipFile(PUBLIC / "sources.zip") as actual:
            if actual.namelist() != expected.namelist() or any(
                actual.read(n) != expected.read(n) for n in expected.namelist()
            ):
                raise ValueError("Published source archive differs from the frozen cohort")
    manifest = {
        "study_version": study["study_version"],
        "selection": study["selection"],
        "parent": study["parent"],
        "counts": {k: v for k, v in summary.items() if k != "article_states"},
        "article_states_sha256": canonical_hash(summary["article_states"]),
        "graph_counts": counts,
        "sensitivity": diagnostics,
        "case_ids": [c["decision"]["id"] for c in readings],
        "fresh_inference_requests": 0,
        "independent_human_labels": False,
        "files": [],
    }
    for name in public_names:
        path = PUBLIC / name
        raw = path.read_bytes()
        manifest["files"].append(
            {
                "file": path.relative_to(ROOT).as_posix(),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if args.write:
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    elif json.loads(MANIFEST.read_text()) != manifest:
        raise ValueError("Published artifact checksums or metrics differ from the checked-in manifest")
    if args.offline_zip:
        with ZipFile(args.offline_zip, "w", ZIP_DEFLATED) as package:
            package.writestr(
                "START-HERE.txt",
                "Open index.html in a browser with all sibling files in the same folder.\n"
                "Choose Follow the evidence to inspect a worked example. The report, workbook,\n"
                "source ZIP and JSON-LD graph can be opened without a DOXA installation.\n"
                "The archive contains recorded model readings and six AI-authored notes.\n"
                "Independent reference labels are unavailable.\n",
            )
            package.writestr(
                "SHA256SUMS.txt",
                "".join(f"{row['sha256']}  {Path(row['file']).name}\n" for row in manifest["files"]),
            )
            for name in public_names:
                package.write(PUBLIC / name, name)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "articles",
                    "flagged_articles",
                    "units",
                    "decisions",
                    "missing",
                    "references",
                    "flag_voices",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
