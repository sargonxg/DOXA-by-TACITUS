"""Portable publications from one frozen scorecard."""
from __future__ import annotations

import csv
from html import escape
import json
from pathlib import Path

from .recorded_export import file_hash
from .scorecard_graph import to_graph

MEDIA = {"pdf": "application/pdf", "html": "text/html", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "csv": "text/csv", "jsonl": "application/x-ndjson", "jsonld": "application/ld+json"}


def _text(value):
    return escape(str(value or ""))


def _percent(value):
    return "—" if value is None else f"{100 * value:.1f}%"


def _timestamp(milliseconds):
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{fraction:03}"


def location(evidence):
    if evidence["cue_start_ms"] is not None:
        return f'{_timestamp(evidence["cue_start_ms"])}–{_timestamp(evidence["cue_end_ms"])}'
    if evidence["page"] is not None:
        return f'Page {evidence["page"]}'
    return f'Block {evidence["block_id"]}'


def _index_section(index):
    if not index:
        return ""
    heading = f'<h2>{_text(index["name"])}</h2>'
    if index["status"] != "scored":
        return heading + f'<p>{_text(index["status"].replace("_", " ").capitalize())}</p>'
    low, high = index["range"]
    return (heading + f'<p class="big">{index["value"]:.1f}<small> / 100</small></p>'
            f'<p>{low:.1f}–{high:.1f} under alternative outcomes for unresolved sources. '
            f'Equal weights give {index["equal_weight"]:.1f}.</p>')


def _render_criteria(snapshot):
    rows = []
    index = snapshot.get("index") or {}
    for item in snapshot["criteria"]:
        weight = index.get("weights", {}).get(item["key"])
        bounds = item.get("unresolved_range")
        uncertainty = f'<br><small>Unresolved range {_percent(bounds[0])}–{_percent(bounds[1])}</small>' if (
            bounds and (item["uncertain"] or item["incomplete"])) else ""
        rows.append(f'<tr><th scope="row">{_text(item["label"])}</th>'
                    f'<td><div class="track" aria-hidden="true"><i style="width:{100 * (item["rate"] or 0):.1f}%"></i></div>'
                    f'{_percent(item["rate"])} · {item["positive"]}/{item["determinate"]}{uncertainty}</td>'
                    f'<td>{item["uncertain"]} / {item["incomplete"]} / {item["inapplicable"]}</td>'
                    f'<td>{_percent(item["coverage"])}<br>{item["retained"]}/{item["planned"]} decisions</td>'
                    f'<td>{f"{weight}%" if weight else "—"}</td></tr>')
    return "".join(rows)


def _render_comparison(comparison):
    if not comparison:
        return ""
    rows = []
    for row in comparison["criteria"]:
        difference = row["difference_pp"]
        label = f"{difference:+.1f} pp" if difference is not None else "—"
        rows.append(f'<tr><th scope="row">{_text(row["label"])}</th><td>{row["resolved"]}/{row["total"]}</td>'
                    f'<td>{label}</td></tr>')
    return (f'<section class="comparison"><h2>Matched comparison</h2><p>{_text(comparison["question"])}</p>'
            '<table><thead><tr><th>Criterion</th><th>Resolved pairs</th><th>Left minus right</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table><p>{len(comparison["excluded"])} exclusions. '
            f'{_text(comparison["interpretation"])}</p></section>')


def render_html(snapshot):
    sources = {source["id"]: source for source in snapshot["sources"]}
    labels = {row["key"]: row["label"] for row in snapshot["criteria"]}
    witnesses = []
    for row in snapshot["criteria"]:
        candidates = [item for item in snapshot["matrix"] if item["criterion"] == row["key"] and item["evidence"]]
        for status in ("positive", "uncertain"):
            item = next((cell for cell in candidates if cell["status"] == status), None)
            if not item:
                continue
            evidence = item["evidence"]
            witnesses.append(f'<article><h3>{_text(labels[item["criterion"]])} · {status}</h3>'
                             f'<small>{_text(sources[item["document_id"]]["title"])} · {_text(location(evidence))}</small>'
                             f'<blockquote>{_text(evidence["quote"])}</blockquote><p>{_text(evidence["explanation"])}</p>'
                             f'<p>{_text(evidence["alternative"])}</p></article>')
    source_list = "".join(f'<li>{_text(source["title"])} · {_text(source["document_type"])} · '
                          f'{_text(source["published_at"] or "Undated")}'
                          + (f' · Extraction requires review. {_text(" ".join(source["extraction_warnings"]))}' if not source["extraction_complete"] else "")
                          + '</li>' for source in sources.values())
    rules = "".join(f'<li><b>{_text(card["label"])}</b> · {_text(card["operational_rule"])}</li>' for card in snapshot["method_cards"])
    run = snapshot["run"]
    weight_formula = (" The project index sums each frozen weight multiplied by its criterion rate. Its coverage threshold is 80% for each weighted criterion."
                      if snapshot.get("index") else "")
    lead = "The criterion profile shows the observed pattern and how much source material supports each rate."
    if snapshot["comparison"]:
        resolved = [row for row in snapshot["comparison"]["criteria"] if row["resolved"] >= 2]
        if resolved:
            highlight = max(resolved, key=lambda row: abs(row["difference_pp"]))
            lead = (f'The left-minus-right difference for {_text(highlight["label"])} is {highlight["difference_pp"]:+.1f} '
                    f'percentage points across {highlight["resolved"]} resolved pairs.')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DOXA · Research scorecard</title>
<style>
@page{{size:A4;margin:16mm}}
*{{box-sizing:border-box}}body{{margin:2rem auto;padding:0 1rem;max-width:65rem;background:#fff;color:#172b3a;font:16px/1.45 system-ui,sans-serif}}
header{{border-top:7px solid #195a56;padding:1rem 0 .5rem}}h1{{font-size:2.35rem;line-height:1.1;margin:.4rem 0}}
h2{{font-size:1.35rem;margin:1.45rem 0 .55rem}}h3{{font-size:1rem;margin:.2rem 0}}p{{margin:.55rem 0}}
small{{color:#435b65}}.big{{font-size:2.8rem;font-weight:720;line-height:1.15}}.big small{{font-size:1.2rem}}
.index{{padding:0 1rem .75rem;background:#e8f4ef;border-left:4px solid #195a56;display:flow-root}}
table{{border-collapse:collapse;width:100%;margin:.65rem 0;font-size:.88rem}}th,td{{text-align:left;border-bottom:1px solid #c6d7d8;padding:.46rem;vertical-align:top}}
thead{{background:#e8f4ef}}.track{{height:.35rem;background:#d6e4e4;width:6rem;margin:.25rem 0}}.track i{{background:#195a56;display:block;height:100%}}
article{{border-top:1px solid #cad9db;padding:.65rem 0;break-inside:avoid}}blockquote{{border-left:3px solid #26726a;padding-left:.75rem;margin:.45rem 0}}
footer{{border-top:1px solid #cad9db;margin:1rem 0;padding-top:.55rem;overflow-wrap:anywhere}}li{{margin:.3rem 0}}
@media print{{body{{margin:0;max-width:none;font-size:10pt}}h1{{font-size:26pt}}tr,.comparison{{break-inside:avoid}}h2{{break-after:avoid}}}}
</style></head><body><main>
<header><small>DOXA BY TACITUS · RESEARCH SCORECARD</small><h1>{_text(snapshot["question"])}</h1>
<p>{len(sources)} sources · {_text(snapshot["basis"].replace("_", " ").capitalize())} · {_text(run["status"])}</p><p>{lead}</p></header>
{f'<div class="index">{_index_section(snapshot["index"])}</div>' if snapshot["index"] else ''}
<section><h2>Criterion profile</h2>
<table><thead><tr><th>Criterion</th><th>Observed rate</th><th>Uncertain / incomplete / inapplicable</th><th>Coverage</th><th>Weight</th></tr></thead>
<tbody>{_render_criteria(snapshot)}</tbody></table></section>
{_render_comparison(snapshot["comparison"])}
<section><h2>Evidence and interpretation</h2>{''.join(witnesses) or '<p>This snapshot has no supported or uncertain example passages.</p>'}</section>
<section><h2>Technical appendix</h2><p>Each deduplicated source contributes one outcome per criterion.
The positive rate uses determinate sources. Coverage uses the declared eligible sources.{weight_formula}</p>
<p>{len(snapshot["excluded"])} excluded sources. Run {_text(run["id"])} · {_text(run["model"] or run["provider"])}.</p>
<ul>{source_list}</ul><ul>{rules}</ul></section>
<footer><small>Snapshot {_text(snapshot["id"])} · SHA-256 {_text(snapshot["snapshot_sha256"])}<br>
Use the workbook or JSONL for the full source matrix and the JSON-LD file for evidence links.</small></footer>
</main></body></html>"""


def _tables(snapshot):
    index = snapshot["index"] or {}
    sources = {source["id"]: source for source in snapshot["sources"]}
    cells = []
    for cell in snapshot["matrix"]:
        source = sources[cell["document_id"]]
        evidence = cell["evidence"] or {}
        cells.append({"document_id": cell["document_id"], "title": source["title"], "criterion": cell["criterion"],
                      "status": cell["status"], "planned": cell["planned"], "retained": cell["retained"],
                      "scope_note": cell.get("scope_note"), **evidence})
    components = {item["key"]: item for item in index.get("components", [])}
    criteria = [{**_interval_columns(row), "weight": components.get(row["key"], {}).get("weight"),
                 "contribution": components.get(row["key"], {}).get("contribution")} for row in snapshot["criteria"]]
    comparators, pairs, comparison_exclusions = [], [], []
    if snapshot["comparison"]:
        comparison_exclusions = snapshot["comparison"]["excluded"]
        for row in snapshot["comparison"]["criteria"]:
            comparators.append({key: value for key, value in row.items() if key != "pairs"})
            pairs.extend({"criterion": row["key"], **pair} for pair in row["pairs"])
    return {"Scope": [{"snapshot_id": snapshot["id"], "snapshot_sha256": snapshot["snapshot_sha256"],
                       "question": snapshot["question"], "basis": snapshot["basis"], **snapshot["run"],
                       "index_status": index.get("status"), "index_value": index.get("value"),
                       "index_range": index.get("range"), "equal_weight": index.get("equal_weight")}],
            "Criteria": criteria, "Source matrix": cells, "Sources": snapshot["sources"],
            "Timeline": [_interval_columns(row) for row in snapshot["timeline"]], "Matched comparison": comparators, "Resolved pairs": pairs,
            "Comparison exclusions": comparison_exclusions, "Source exclusions": snapshot["excluded"]}


def _interval_columns(row):
    lower, upper = row.get("unresolved_range") or (None, None)
    return {**{key: value for key, value in row.items() if key != "unresolved_range"},
            "unresolved_lower_rate": lower, "unresolved_upper_rate": upper}


def _scalar(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _worksheet(workbook, name, rows):
    sheet = workbook.add_worksheet(name)
    if not rows:
        sheet.write_string(0, 0, "No records")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    sheet.write_row(0, 0, fields, workbook.add_format({"bold": True, "bg_color": "#E8F4EF"}))
    for index, row in enumerate(rows, 1):
        sheet.write_row(index, 0, [_scalar(row.get(key)) for key in fields])
    sheet.set_column(0, len(fields) - 1, 19)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, len(rows), len(fields) - 1)


def _csv_value(value):
    value = _scalar(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def render_scorecard(snapshot, output: Path, format: str):
    output.mkdir(parents=True, exist_ok=True)
    filename = output / f"doxa-scorecard.{format}"
    if format == "pdf":
        from .report_source import _offline_pdf
        _offline_pdf(render_html(snapshot), filename, tagged=True)
    elif format == "html":
        filename.write_text(render_html(snapshot), encoding="utf-8")
    elif format == "xlsx":
        from xlsxwriter import Workbook
        with Workbook(filename, {"strings_to_formulas": False, "strings_to_urls": False, "constant_memory": True}) as book:
            for name, rows in _tables(snapshot).items():
                _worksheet(book, name, rows)
    elif format == "csv":
        records = [{"record_type": name, **row} for name, rows in _tables(snapshot).items() for row in rows]
        fields = list(dict.fromkeys(key for row in records for key in row))
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in records)
    elif format == "jsonl":
        with filename.open("w", encoding="utf-8") as handle:
            meta = {key: value for key, value in snapshot.items() if key != "matrix"}
            handle.write(json.dumps({"record_type": "scorecard", **meta}, ensure_ascii=False) + "\n")
            for cell in snapshot["matrix"]:
                handle.write(json.dumps({"record_type": "source_criterion", **cell}, ensure_ascii=False) + "\n")
    else:
        raise ValueError("Unsupported scorecard report format")
    graph = output / "scorecard-evidence.jsonld"
    graph.write_text(json.dumps(to_graph(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
    return [{"format": key, "filename": path.name, "path": str(path), "bytes": path.stat().st_size,
             "sha256": file_hash(path), "content_type": MEDIA[key]} for key, path in ((format, filename), ("jsonld", graph))]
