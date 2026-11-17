#!/usr/bin/env python3
"""Export frozen NLI scores to an inspectable workbook, without model inference."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import xlsxwriter

ROOT = Path(__file__).resolve().parents[1]
VERSION = "doxa-cpu-nli-workbook-1"
FOREST, SAGE, IVORY, INK, MUTED, LINE, GOLD = "#163E35", "#DDEAE3", "#F7F5EC", "#253C36", "#63766C", "#D6DFD7", "#B88D46"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def read_frozen(summary_path: Path) -> tuple[dict, dict, list[dict], dict]:
    """Reconstruct and hash original blocks from the portable score archive."""
    summary_bytes = summary_path.read_bytes()
    summary = json.loads(summary_bytes)
    score_name = summary["complete_scores"]["path"]
    if Path(score_name).name != score_name:
        raise ValueError("The score archive must be beside the frozen summary")
    archive_path = summary_path.parent / score_name
    archive_bytes = archive_path.read_bytes()
    if digest(archive_bytes) != summary["complete_scores"]["sha256"]:
        raise ValueError("Frozen score archive checksum mismatch")
    documents, windows, header, expanded = {}, [], None, 0
    with gzip.open(archive_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            expanded += len(line.encode())
            if expanded > 32 * 1024 * 1024:
                raise ValueError("The twenty-article score archive exceeded its resource bound")
            row = json.loads(line)
            kind = row.get("record_type")
            if kind == "benchmark" and header is None:
                header = row
            elif kind == "document" and row["source_id"] not in documents:
                documents[row["source_id"]] = row
            elif kind == "window_scores":
                windows.append(row)
            else:
                raise ValueError("Unexpected or duplicate frozen archive record")
    expected = summary["collection"]["sources"]
    if (summary.get("status") != "completed" or len(expected) != 20 or len(documents) != 20
            or summary.get("documents_complete") != 20 or len(windows) != 60 or summary.get("pairs_evaluated") != 240):
        raise ValueError("This workbook requires the complete twenty-source, sixty-window, 240-pair baseline")
    if not header or header["hypotheses"] != summary["hypotheses"] or header["model_profile"] != summary["model_profile"]:
        raise ValueError("Frozen model or hypotheses differ from the summary")
    hypotheses = {row["id"]: row["text"] for row in summary["hypotheses"]}
    if len(hypotheses) != 4:
        raise ValueError("Four distinct literal hypotheses are required")
    by_source, unique_pairs, references = {}, set(), 0
    for window in windows:
        by_source.setdefault(window["source_id"], []).append(window)
        for score in window["scores"]:
            identity = (window["source_id"], window["window_index"], score["id"])
            if identity in unique_pairs or hypotheses.get(score["id"]) != score["hypothesis"]:
                raise ValueError("Duplicate or changed source/window/hypothesis pair")
            unique_pairs.add(identity)
            a, b = score["logits"]["entailment"], score["logits"]["not_entailment"]
            value = score["entailment_score"]
            if not all(math.isfinite(x) for x in (a, b, value)) or not 0 <= value <= 1:
                raise ValueError("NLI scores and raw logits must be finite")
            maximum = max(a, b)
            wanted = math.exp(a - maximum) / (math.exp(a - maximum) + math.exp(b - maximum))
            if abs(value - wanted) > 1e-12:
                raise ValueError("Score differs from the declared two-logit softmax")
        if {row["id"] for row in window["scores"]} != set(hypotheses):
            raise ValueError("A source window lacks one of the four separate hypothesis scores")
    if len(unique_pairs) != 240 or set(by_source) != set(documents):
        raise ValueError("Incomplete source/window/hypothesis scope")
    for source in expected:
        identity = source["source_id"]
        document = documents[identity]
        if document["source_content_sha256"] != source["content_sha256"]:
            raise ValueError("Original source checksum differs from frozen selection")
        mapping = document["block_offset_map"]
        buffers = {item["block_id"]: [None] * (item["end"] - item["start"]) for item in mapping}
        for window in by_source[identity]:
            if window["source_content_sha256"] != source["content_sha256"] or window["joined_text_sha256"] != document["joined_text_sha256"]:
                raise ValueError("A window changed its source identity/hash")
            for ref in window["source_block_refs"]:
                target = buffers[ref["block_id"]]
                if not 0 <= ref["start"] < ref["end"] <= len(target) or len(ref["text"]) != ref["end"] - ref["start"]:
                    raise ValueError("Original block reference has invalid offsets")
                for index, character in enumerate(ref["text"], ref["start"]):
                    if target[index] not in (None, character):
                        raise ValueError("Overlapping source windows disagree on original text")
                    target[index] = character
                references += 1
        original_blocks = []
        for metadata in document["source_blocks_metadata"]:
            characters = buffers[metadata["id"]]
            if any(value is None for value in characters):
                raise ValueError("A frozen source block is not completely represented")
            original_blocks.append({**metadata, "text": "".join(characters)})
        if digest(canonical(original_blocks)) != source["content_sha256"]:
            raise ValueError("Reconstructed original source block checksum mismatch")
        joined = "\n\n".join(row["text"] for row in original_blocks)
        if digest(joined.encode()) != document["joined_text_sha256"]:
            raise ValueError("Reconstructed joined source checksum mismatch")
        for window in by_source[identity]:
            if joined[window["start"]:window["end"]] != window["text"]:
                raise ValueError("Window text differs from exact original source offsets")
        document["verified_blocks"] = original_blocks
    order = {row["source_id"]: index for index, row in enumerate(expected)}
    windows.sort(key=lambda row: (order[row["source_id"]], row["window_index"]))
    checks = {"source_hashes_verified": 20, "exact_windows_verified": 60,
              "score_logit_pairs_verified": 240, "original_block_references_verified": references,
              "summary_sha256": digest(summary_bytes), "scores_sha256": digest(archive_bytes)}
    return summary, documents, windows, checks


def literal(value) -> str:
    if value is None:
        return ""
    result = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    if len(result) > 32767:
        raise ValueError("A workbook cell exceeds Excel's text limit; nothing was silently truncated")
    return result


def export_workbook(summary_path: Path, output: Path, *, overwrite: bool = False) -> dict:
    summary_path, output = Path(summary_path).resolve(), Path(output).resolve()
    receipt_path = output.with_suffix(".receipt.json")
    if not overwrite and (output.exists() or receipt_path.exists()):
        raise ValueError("Generated workbook exists; use --overwrite explicitly to regenerate it")
    summary, documents, windows, checks = read_frozen(summary_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".xlsx.tmp")
    book = xlsxwriter.Workbook(temporary, {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False})
    created = datetime.fromisoformat(summary["generated_at"]).astimezone(timezone.utc).replace(tzinfo=None)
    book.set_properties({"title": "DOXA · Local CPU NLI · 20 news articles", "author": "DOXA by TACITUS",
                         "subject": "Uncalibrated textual entailment, exact sources and model provenance",
                         "comments": "Software integrity checks only; no independent interpretation accuracy labels.",
                         "company": "TACITUS", "created": created})
    formats = {
        "title": book.add_format({"font_name": "Aptos Display", "font_size": 25, "bold": True, "font_color": FOREST, "bg_color": IVORY, "valign": "vcenter"}),
        "subtitle": book.add_format({"font_name": "Aptos", "font_size": 11, "font_color": MUTED, "bg_color": IVORY, "text_wrap": True, "valign": "top"}),
        "note": book.add_format({"font_name": "Aptos", "font_size": 10, "font_color": INK, "bg_color": SAGE, "text_wrap": True, "valign": "vcenter"}),
        "header": book.add_format({"font_name": "Aptos", "font_size": 10, "bold": True, "font_color": "white", "bg_color": FOREST, "text_wrap": True, "valign": "vcenter"}),
        "section": book.add_format({"font_name": "Aptos", "font_size": 12, "bold": True, "font_color": FOREST, "bg_color": SAGE, "bottom": 1, "bottom_color": LINE}),
        "cardlabel": book.add_format({"font_name": "Aptos", "font_size": 9, "bold": True, "font_color": MUTED, "bg_color": IVORY, "text_wrap": True}),
        "cardvalue": book.add_format({"font_name": "Aptos Display", "font_size": 25, "bold": True, "font_color": FOREST, "bg_color": IVORY, "valign": "vcenter"}),
    }
    for band, background in ((0, "#FFFFFF"), (1, IVORY)):
        base = {"font_name": "Aptos", "font_size": 10, "font_color": INK, "bg_color": background,
                "text_wrap": True, "valign": "top", "bottom": 1, "bottom_color": LINE}
        for kind, extra in (("text", {}), ("link", {"font_color": "#216C59", "underline": 1}),
                            ("integer", {"num_format": "#,##0"}), ("decimal", {"num_format": "0.000000"}),
                            ("score", {"num_format": "0.0000", "bold": True, "font_color": FOREST})):
            formats[f"{kind}{band}"] = book.add_format({**base, **extra})

    def sheet(name, title, subtitle, widths, note):
        ws = book.add_worksheet(name)
        ws.hide_gridlines(2)
        ws.set_tab_color(FOREST if name in {"Method", "Scores"} else GOLD)
        ws.set_zoom(85)
        for column, width in enumerate(widths):
            ws.set_column(column, column, width)
        ws.merge_range(0, 0, 0, len(widths) - 1, title, formats["title"])
        ws.set_row(0, 42)
        ws.merge_range(1, 0, 1, len(widths) - 1, subtitle, formats["subtitle"])
        ws.set_row(1, 28)
        ws.merge_range(2, 0, 2, len(widths) - 1, note, formats["note"])
        ws.set_row(2, 45)
        ws.set_row(3, 10)
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.set_margins(.35, .35, .45, .45)
        ws.set_header("&LDOXA by TACITUS&RLocal CPU NLI · research data", {"margin": .2})
        ws.set_footer(f"&L{name} · frozen source evidence&R&P / &N", {"margin": .2})
        return ws

    def table(name, title, subtitle, headers, widths, rows, *, note, height=62, numeric=None, links=None):
        ws = sheet(name, title, subtitle, widths, note)
        ws.write_row(4, 0, headers, formats["header"])
        ws.set_row(4, 35)
        ws.freeze_panes(5, 2 if name == "Sources" else 1)
        ws.repeat_rows(0, 4)
        for index, row in enumerate(rows, 5):
            band = (index - 5) % 2
            ws.set_row(index, height(row) if callable(height) else height)
            for column, value in enumerate(row):
                kind = (numeric or {}).get(column, "text")
                fmt = formats[f"{kind}{band}"]
                link = (links or {}).get((index - 5, column))
                if link:
                    ws.write_url(index, column, link, formats[f"link{band}"], string=literal(value))
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    ws.write_number(index, column, value, fmt)
                else:
                    ws.write_string(index, column, literal(value), fmt)
        ws.autofilter(4, 0, 4 + len(rows), len(headers) - 1)
        ws.print_area(0, 0, 4 + len(rows), len(headers) - 1)
        return ws

    note = "Auxiliary English NLI scores · uncalibrated · no automatic threshold or prejudice probability · original source text and credits preserved."
    method = sheet("Method", "DOXA / Local CPU NLI", "A complete, inspectable twenty-article resource baseline · frozen scores, no new inference", [14] * 12, note)
    method.activate()
    metrics = [("ORIGINAL ARTICLES", 20), ("SOURCE WINDOWS", 60), ("SCORING PAIRS", 240),
               ("EXPLICIT HYPOTHESES", 4), ("SECONDS · FIRST LOAD INCLUDED", round(summary["seconds_including_first_model_load"], 3)),
               ("PEAK PROCESS RSS · GiB", round(summary["peak_process_rss_bytes"] / 1073741824, 3))]
    for index, (label, value) in enumerate(metrics):
        left = index * 2
        method.merge_range(4, left, 4, left + 1, label, formats["cardlabel"])
        method.merge_range(5, left, 6, left + 1, value, formats["cardvalue"])
    method.set_row(4, 30)
    method.set_row(5, 27)
    method.set_row(6, 17)
    method.merge_range(8, 0, 8, 11, "Read a score beside its hypothesis and exact source window", formats["section"])
    method.merge_range(9, 0, 11, 11,
        "Each hypothesis is scored separately with softmax(entailment, not_entailment)[entailment]. The four scores do not compete for a single winning category. Scores are uncalibrated; they are not probabilities of prejudice or judgments of an author's private beliefs. Not-entailment is not contradiction. Windows overlap by 32 tokens: 240 scoring rows are not 240 separate incidents or statistically independent observations. There are no independent human accuracy labels.", formats["note"])
    for row in (9, 10, 11):
        method.set_row(row, 24)
    method.merge_range(13, 0, 13, 11, "Evidence, scope and reproducibility", formats["section"])
    details = [
        ("Source scope", "All original blocks of the same 20 frozen Global Voices news articles; no passage sampling. The publisher cohort is not a representative sample of all news."),
        ("Window construction", "Original blocks joined with literal double newlines. Offsets count Unicode code points, start inclusive/end exclusive. Windows retain exact original-block locators and full text."),
        ("Model limit", "512 combined source + hypothesis + special tokens; 32-token source overlap. The pinned model is a general English NLI scorer, not a DOXA-fine-tuned prejudice specialist."),
        ("Runtime and timing", f"CPU; {summary['model_profile']['runtime_settings']['threads']} threads; batches of {summary['model_profile']['runtime_settings']['batch_size']}. {summary['seconds_including_first_model_load']:.6f} seconds including the first model load in this process. Later articles reuse loaded weights. Filesystem caches were uncontrolled."),
        ("Memory and cost", f"Peak process RSS {summary['peak_process_rss_bytes']:,} bytes; browser, OS and other services excluded. No GPU or paid inference. CPU hardware, electricity and installation excluded."),
        ("Software integrity", f"Verified {checks['source_hashes_verified']} reconstructed original-source hashes, {checks['exact_windows_verified']} exact windows, {checks['original_block_references_verified']} original-block references and all {checks['score_logit_pairs_verified']} score/logit conversions. These checks do not establish interpretation accuracy."),
        ("Workbook guide", "Sources: authors, license notices and hashes. Windows: complete exact text and locators. Scores: all 240 separate results and both raw logits. Hypotheses: the four literal propositions. Models: pinned runtime provenance."),
        ("Viewing long text", "Exact text is stored in full, without generated ellipses. Select a cell to inspect its complete value in Excel's formula bar; row height and zoom can be adjusted. Filter Scores by source and hypothesis, then follow its internal window link."),
        ("Frozen summary SHA-256", checks["summary_sha256"]),
        ("Frozen score archive SHA-256", checks["scores_sha256"]),
        ("Reproduce this workbook", "python scripts/export_cpu_nli_workbook.py --summary benchmarks/cpu-nli-20-news-summary.json --output examples/local-cpu-nli-20.xlsx --overwrite"),
    ]
    for index, (label, value) in enumerate(details, 14):
        band = (index - 14) % 2
        method.merge_range(index, 0, index, 2, label, formats[f"text{band}"])
        method.merge_range(index, 3, index, 11, value, formats[f"text{band}"])
        method.set_row(index, 48 if index < 22 else 36)
    method.freeze_panes(4, 0)
    method.print_area(0, 0, 24, 11)

    profile = summary["model_profile"]
    model_rows = [[key, value] for key, value in profile.items()]
    model_rows += [["Score definition", summary["score_meaning"]], ["Software versions", summary["software"]],
                   ["Hardware observation", summary["hardware"]], ["Offline flags", summary["offline_environment_flags"]],
                   ["Artifact hashes verified before model load", summary["model_artifacts_hashes_verified_before_load"]],
                   ["Score cache", summary["model_cache"]]]
    table("Models", "The pinned model", "One pretrained MIT-licensed CPU model · actual revision, limits and runtime recorded", ["Field", "Recorded value"], [35, 130], model_rows,
          note="General NLI, experimental for rubric exploration. DOXA did not fine-tune these weights. Labels: 0=entailment, 1=not_entailment.", height=62)

    source_order = [row["source_id"] for row in summary["collection"]["sources"]]
    measured = {row["source_id"]: row for row in summary["documents"]}
    source_rows, source_links = [], {}
    for index, identity in enumerate(source_order):
        document, observation = documents[identity], measured[identity]
        credit = document["credits_and_provenance"]
        source_rows.append([identity, credit.get("title"), credit.get("author"), credit.get("published_at"), credit.get("license"),
                            credit.get("attribution"), credit.get("source_url"), credit.get("license_url"), observation["words"],
                            len(document["verified_blocks"]), observation["characters"], observation["windows"], observation["pairs_evaluated"],
                            document["source_content_sha256"], document["joined_text_sha256"], credit.get("rights_review", {}).get("scope"), credit])
        for column, key in ((6, "source_url"), (7, "license_url")):
            target = credit.get(key) or ""
            if urlsplit(target).scheme in {"https", "http"} and urlsplit(target).netloc:
                source_links[index, column] = target
    table("Sources", "20 original news articles", "Original selection order · author credit, rights, frozen hashes and source coverage", ["Source ID", "Title", "Author", "Published at", "License", "Attribution", "Original URL", "License URL", "Words", "Blocks", "Joined characters", "Windows", "Scoring pairs", "Original block content SHA-256", "Joined text SHA-256", "Rights qualification", "Complete credits/provenance JSON"],
          [18, 58, 28, 24, 17, 80, 60, 55, 12, 12, 18, 12, 15, 70, 70, 95, 120], source_rows,
          note="All 20 original articles are represented. Original journalistic quotations retain their attribution/rights qualifications; external media was excluded by the frozen collector.",
          numeric={8: "integer", 9: "integer", 10: "integer", 11: "integer", 12: "integer"}, links=source_links, height=110)

    source_row = {identity: index + 6 for index, identity in enumerate(source_order)}
    window_rows, window_links, window_row = [], {}, {}
    for index, window in enumerate(windows):
        identity = window["source_id"]
        locators = "\n".join(f"{ref['block_id']}[{ref['start']}:{ref['end']}]" for ref in window["source_block_refs"])
        window_row[identity, window["window_index"]] = index + 6
        window_rows.append([identity, window["window_index"], window["text"], locators, window["start"], window["end"], window["token_count"],
                            window["source_block_refs"], window["source_content_sha256"], window["joined_text_sha256"]])
        window_links[index, 0] = f"internal:'Sources'!A{source_row[identity]}"
    table("Windows", "60 exact source windows", "Complete original text · precise block locators · Unicode code-point offsets", ["Source ID", "Window index (zero-based)", "Exact original source window", "Exact original-block locators", "Start code point", "End code point", "Source tokens", "Complete original-block references JSON", "Original block content SHA-256", "Joined text SHA-256"],
          [18, 14, 100, 70, 15, 15, 15, 120, 70, 70], window_rows,
          note="Text cells contain the entire exact source window. Select a cell to inspect all text. Offsets are start-inclusive/end-exclusive Unicode code points; windows overlap and are not separate incidents.",
          numeric={1: "integer", 4: "integer", 5: "integer", 6: "integer"}, links=window_links,
          height=lambda row: min(409, max(120, (len(row[2]) / 95 + row[2].count('\n') + 2) * 12)))

    score_rows, score_links, hypothesis_counts = [], {}, Counter()
    for window in windows:
        for score in window["scores"]:
            index = len(score_rows)
            hypothesis_counts[score["id"]] += 1
            locator = f"{window['source_id']} · window {window['window_index']} · [{window['start']}:{window['end']}] code points"
            score_rows.append([window["source_id"], window["window_index"], score["id"], score["entailment_score"],
                               score["logits"]["entailment"], score["logits"]["not_entailment"], "Read exact source window", score["hypothesis"], locator, window["source_content_sha256"]])
            score_links[index, 0] = f"internal:'Sources'!A{source_row[window['source_id']]}"
            score_links[index, 6] = f"internal:'Windows'!C{window_row[window['source_id'], window['window_index']]}"
    table("Scores", "240 separately scored pairs", "Read each score with its literal hypothesis and linked original source window", ["Source ID", "Window index (zero-based)", "Hypothesis ID", "Uncalibrated entailment score", "Raw logit · entailment", "Raw logit · not_entailment", "Exact source text", "Literal hypothesis", "Exact joined-source locator", "Original block content SHA-256"],
          [18, 14, 24, 25, 24, 26, 30, 100, 75, 70], score_rows,
          note="Decimals are uncalibrated model scores, not prejudice probabilities or accuracy/confidence estimates. Not-entailment is not contradiction. No ranking, threshold or document-level prejudice score is produced.",
          numeric={1: "integer", 3: "score", 4: "decimal", 5: "decimal"}, links=score_links, height=56)
    table("Hypotheses", "4 explicit research propositions", "Literal text sent to the model · each hypothesis evaluated separately for every window", ["Hypothesis ID", "Literal hypothesis", "Window scores", "Interpretation"], [27, 105, 20, 100],
          [[row["id"], row["text"], hypothesis_counts[row["id"]], "Auxiliary proposition entailment only. Several hypotheses can score highly together; no winning category or private-belief inference."] for row in summary["hypotheses"]],
          note="The four propositions are research inputs. Scores support exploration and review; they are not gold labels or a validated prejudice classifier.", numeric={2: "integer"}, height=90)
    book.close()
    temporary.replace(output)
    receipt = {"schema_version": "3.0", "exporter_version": VERSION,
               "generated_at": datetime.now(timezone.utc).isoformat(), "input_summary": str(summary_path.relative_to(ROOT)) if summary_path.is_relative_to(ROOT) else summary_path.name,
               "input_summary_sha256": checks["summary_sha256"], "input_scores_sha256": checks["scores_sha256"],
               "workbook": str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else output.name,
               "workbook_sha256": digest(output.read_bytes()), "workbook_bytes": output.stat().st_size,
               "sheet_data_rows": {"Sources": 20, "Windows": 60, "Scores": 240, "Hypotheses": 4},
               "models": 1, "source_integrity_checks": checks, "interpretation_accuracy": None,
               "model_inference_performed": False, "paid_calls": 0, "xlsxwriter_version": version("XlsxWriter"),
               "exporter_sha256": digest(Path(__file__).read_bytes()),
               "reproduce": "python scripts/export_cpu_nli_workbook.py --summary benchmarks/cpu-nli-20-news-summary.json --output examples/local-cpu-nli-20.xlsx --overwrite"}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=ROOT / "benchmarks/cpu-nli-20-news-summary.json")
    parser.add_argument("--output", type=Path, default=ROOT / "examples/local-cpu-nli-20.xlsx")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(export_workbook(args.summary, args.output, overwrite=args.overwrite), indent=2))


if __name__ == "__main__":
    main()
