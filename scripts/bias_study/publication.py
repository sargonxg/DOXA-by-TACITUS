"""Render the example web dashboard and print briefing from the same checked data."""

from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit

from .cohort import LENSES, RESULT_LABELS, STATE_LABELS, TITLES
from .readings import REFERENCES

HERE = Path(__file__).parent
TITLE = "Reading anti-Muslim bias in the news"
VOICE = {
    "reported_speaker": "Reported speaker",
    "quoted_speaker": "Quoted speaker",
    "author": "Article author",
    "unknown": "Voice unresolved",
    "hypothetical": "Hypothetical speech",
}
FRAME_NAMES = {LENSES[0]: "Hostility and discrimination", LENSES[1]: "Racialization and exclusion"}
REPO = "https://github.com/sargonxg/DOXA-by-TACITUS"
LICENSE = "https://creativecommons.org/licenses/by/3.0/"


def link(label, url):
    if urlsplit(url).scheme != "https":
        raise ValueError("Source and reference links must use HTTPS")
    return (
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'
    )


def credit(doc):
    translators = ", ".join(row["name"] for row in doc["translators"])
    translation = f" · Translation by {escape(translators)}" if translators else ""
    return f"{escape(doc['author'])}{translation} · Global Voices · {doc['published_at'][:10]} · {link('CC BY 3.0', doc['license_url'])}"


def trace_data(trace):
    node, rule, source, passage = (
        trace["decision"], trace["criterion"], trace["source"], trace["passage"],
    )
    return {
        "decision": node["recordId"],
        "criterion": rule["recordId"],
        "rule": rule["rule"],
        "framework": trace["framework"]["title"],
        "version": trace["framework"]["version"],
        "source": source["recordId"],
        "source_hash": source["contentSha256"],
        "run": trace["run"]["recordId"],
        "model": trace["run"]["model"],
        "passage": passage["@id"],
    }


def case_html(case, graph_trace, printing):
    row, ref, doc = case["decision"], case["ref"], case["doc"]
    trace = trace_data(graph_trace)
    extra = ""
    if peer := case["peer"]:
        extra = f"<p><b>Second decision for this unit.</b> {escape(TITLES[peer['criterion_id']])} · {escape(RESULT_LABELS[peer['result']].lower())}.</p>"
    rule = escape(trace["rule"])
    position = f"{escape(ref['block_id'])} · characters {ref['start']}–{ref['end']}"
    technical = (
        f"<p class='case-provenance'><b>Frozen criterion.</b> {rule}<br>"
        f"<small>{escape(trace['framework'])} {escape(trace['version'])} · {position}</small></p>"
    )
    if not printing:
        rationale = (
            row.get("explanation")
            or "The original model supplied an outcome code without a written rationale."
        )
        alternative = row.get("alternative")
        technical = (
            "<details><summary>Evidence path and original model record</summary>"
            + technical
            + f"<p class='meta'>Decision {escape(trace['decision'])} · run {escape(trace['run'])} · "
            f"{escape(trace['model'])}. Source version SHA-256 {escape(trace['source_hash'])}.</p>"
            f"<blockquote>{escape(case['context'])}</blockquote><p><b>Saved explanation.</b> {escape(rationale)}</p>"
            + (f"<p><b>Model’s alternative.</b> {escape(alternative)}</p>" if alternative else "")
            + "</details>"
        )
    return (
        f"<article class='case' id='case-{case['slug']}'><div class='case-label'>Recorded model outcome · "
        f"{escape(case['result_label'])} · {escape(TITLES[row['criterion_id']])}</div>"
        f"<h3>{escape(case['heading'])}</h3><p>{link(doc['title'], doc['source_url'])}<br><small>{credit(doc)}</small></p>"
        f"<blockquote>{escape(graph_trace['passage']['exact'])}</blockquote><small>{escape(case['anchor_origin'])}. "
        f"Recorded voice · {escape(VOICE.get(row['attribution'], row['attribution']))}.</small>"
        f"<p><b>Editorial reading.</b> {escape(case['reading'])}</p>{extra}"
        f"<p><b>Review question.</b> {escape(case['followup'])}</p>{technical}</article>"
    )


def chart(summary, printing):
    parts = []
    for lens in LENSES:
        cls = "structural" if lens == LENSES[1] else ""
        parts.append(f"<div class='chart-group {cls}'><h3>{FRAME_NAMES[lens]}</h3>")
        for key, item in summary["criteria"].items():
            if item["lens"] != lens:
                continue
            n = item["states"]["flagged"]
            label = escape(item["label"])
            control = (
                label
                if printing
                else f"<button class='screen-action' data-open-criterion='{key}'>{label}</button>"
            )
            parts.append(
                f"<div class='bar-row'><div class='bar-name'><span>{control}</span><strong>{n} / {summary['articles']}</strong></div>"
                f"<div class='track' aria-hidden='true'><div class='fill' style='width:{100 * n / summary['articles']:.3f}%'></div></div></div>"
            )
        parts.append("</div>")
    return "".join(parts)


def sensitivity_table(diagnostics):
    rows = []
    for criterion, record in diagnostics.items():
        omissions = record["year_omissions"]
        leave_out = "—"
        if omissions:
            low, high = omissions[0], omissions[-1]
            leave_out = f"{low['flagged']}/{low['articles']} to {high['flagged']}/{high['articles']}"
        rows.append(
            f"<tr><th scope='row'>{escape(TITLES[criterion])}</th>"
            f"<td>{record['observed_flags']}/{record['articles']}</td>"
            f"<td>{record['if_all_unresolved_flagged']}/{record['articles']}</td>"
            f"<td>{leave_out}</td></tr>"
        )
    return (
        "<h2 id='sensitivity-title'>How much do unresolved readings matter?</h2>"
        "<p>The maximum counts each as-yet unflagged article with missing or uncertain decisions as a flag. "
        "The last column drops one publication year at a time and compares the observed fractions.</p>"
        "<div class='table-scroll' tabindex='0' role='region' aria-labelledby='sensitivity-title'><table>"
        "<thead><tr><th scope='col'>Criterion</th><th scope='col'>Observed flags</th>"
        "<th scope='col'>All unresolved flagged</th><th scope='col'>One year omitted</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        "<p class='caption'>These calculations describe the selected articles and keep the recorded readings fixed. "
        "They give no error rate or interval for a broader population.</p>"
    )


def methods(study, summary):
    sel = study["selection"]
    n = sum(sel["candidates_by_year"].values())
    source_scope = (
        f"The archive contains 5,000 Global Voices articles. In its licensed English-language development set, "
        f"{n} articles contained Muslim, Muslims, Islamophobia or Islamophobic in the extracted body after holdout exclusions. "
        "A fixed hash order selected ten per year, with distinct story groups and content hashes. Selection used the text before opening model results. "
        "This search can include a personal name or miss an article that uses other terms."
    )
    counts = summary["outcomes"]
    return (
        f"<h2>How to read these numbers</h2><ol><li><b>Which articles?</b> {escape(source_scope)} "
        "Equal numbers per year give each year the same space. They do not measure how often the topic appeared in the news.</li>"
        f"<li><b>What was checked?</b> Six frozen criteria applied to {summary['units']} source units in {summary['articles']} full extracted articles. "
        f"The original Gemma model run retained {summary['decisions']} decisions here, including "
        f"{counts.get('meets_criterion', 0)} flags, {counts.get('uncertain', 0)} uncertain, {counts.get('does_not_meet', 0)} criterion unmet and "
        f"{counts.get('not_applicable', 0)} outside the criterion. {summary['missing']} selected decisions are missing. The wider parent run is partial.</li>"
        "<li><b>How are articles counted?</b> For each criterion, one or more flags count the article once. Without a flag, missing decisions take precedence, "
        "then uncertainty, then a checked unit without a flag, then all units outside the criterion. One article can appear in multiple criterion rows. "
        "Original unit-level outcomes, including uncertainty alongside a flag, are available in the workbook.</li>"
        "<li><b>Who interpreted the text?</b> The original model assigned the outcomes, speaker and stance. Positive and uncertain readings have grounded detail when supplied. "
        "For a compact decision, DOXA attached the scored unit’s sentences in software. The six teaching examples were chosen afterwards. Their commentary is AI-authored. "
        "The original outputs have no independent human reference labels.</li></ol>"
        "<p class='frame-note'>These figures describe one selected publisher and the model’s readings. Estimating how common prejudice is, comparing outlets, or measuring missed and incorrect flags needs another sampling design and independent review.</p>"
        "<h3>Where the criteria come from</h3><ul class='refs'>"
        + "".join(
            f"<li><b>{escape(kind)}.</b> {escape(why)} {link(title, url)}</li>"
            for kind, title, why, url in REFERENCES
        )
        + "</ul>"
    )


def directory(study, summary, printing):
    if printing:
        return (
            "<h2>Source register</h2><p>All 60 texts were analysed. Each title opens its original article. Source paragraphs were extracted from the English edition. "
            "The workbook and source archive retain the complete credits and article-level results.</p>"
            + "".join(
                f"<div class='print-reg'>{link(d['title'], d['source_url'])}<small>{credit(d)} · {d['id']}</small></div>"
                for d in study["documents"]
            )
        )
    state_options = "".join(f"<option value='{k}'>{escape(v)}</option>" for k, v in STATE_LABELS.items())
    crit_options = "".join(f"<option value='{k}'>{escape(v)}</option>" for k, v in TITLES.items())
    parts = [
        f"<h2>Explore all {summary['articles']} articles</h2><p>Open a title to see how the model read each criterion. The source archive includes all the attributed texts.</p>",
        "<div class='controls js-only'><label>Find a title or credit<input id='search' type='search'></label>"
        f"<label>Criterion<select id='criterion'><option value=''>Any criterion</option>{crit_options}</select></label>"
        f"<label>Article reading<select id='outcome'><option value=''>Any reading</option>{state_options}</select></label>"
        "<label>Publication year<select id='year'><option value=''>Any year</option>"
        + "".join(f"<option>{year}</option>" for year in sorted({d["published_at"][:4] for d in study["documents"]}))
        + "</select></label>"
        "<button id='reset' class='more' type='button'>Clear filters</button></div><p class='meta' id='shown' role='status'></p>",
    ]
    for d in study["documents"]:
        states = summary["article_states"][d["id"]]
        flags = sum(v == "flagged" for v in states.values())
        search = " ".join([d["title"], d["author"], *[t["name"] for t in d["translators"]]])
        parts.append(
            f"<details class='register-item' data-states='{escape(json.dumps(states), quote=True)}' "
            f"data-year='{d['published_at'][:4]}' data-search='{escape(search, quote=True)}'>"
            f"<summary>{escape(d['title'])}</summary>"
            f"<small>{credit(d)}</small><p>{flags} of six criteria flagged at least once in this article. {link('Read the original article', d['source_url'])}.</p>"
        )
        for k, state in states.items():
            parts.append(f"<div class='record-result'><b>{escape(TITLES[k])}</b> · {STATE_LABELS[state]}")
            for row in d["decisions"]:
                if row["criterion_id"] == k and row["result"] in {"meets_criterion", "uncertain"}:
                    origin = (
                        "Model-selected reference"
                        if row.get("detail_supplied")
                        else "Software-attached reference"
                    )
                    parts.append(
                        f"<blockquote>{escape(row['quote'])}</blockquote><small>{RESULT_LABELS[row['result']]} · "
                        f"{escape(VOICE.get(row['attribution'], row['attribution']))} · {origin}. "
                        f"{escape(row['block_id'])}, characters {row['start']}–{row['end']}.</small>"
                    )
            parts.append("</div>")
        parts.append("</details>")
    parts.append("<button id='more' class='more js-only' type='button'>Show more articles</button>")
    return "".join(parts)


def html(study, summary, cases, traces, diagnostics, *, printing=False):
    css = (HERE / "reader.css").read_text()
    n = summary["articles"]
    voice = summary["flag_voices"]
    attributed = voice.get("reported_speaker", 0) + voice.get("quoted_speaker", 0)
    positives = summary["outcomes"].get("meets_criterion", 0)
    intro = (
        f"The model flagged passages in <strong>{summary['flagged_articles']} of {n} articles</strong>. "
        f"It attributed <strong>{attributed} of its {positives} flagged readings</strong> to people the articles quoted or reported. "
        "A story documenting discrimination can therefore appear in the count."
    )
    downloads = (
        "<div class='downloads'><a href='report.pdf' download>Download PDF</a>"
        "<a href='evidence.xlsx' download>Download workbook</a><a href='sources.zip' download>Download 60 texts and decisions</a>"
        "<a href='evidence.jsonld' download>Download evidence graph</a></div>"
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; base-uri 'none'; form-action 'none'\">",
        f"<title>{TITLE} | DOXA by TACITUS</title><meta name='author' content='DOXA by TACITUS'><style>{css}</style></head><body>",
        "<header><div class='brand'>DOXA by TACITUS <span>Field study · Global Voices · 2020–2025</span></div>",
        "<nav aria-label='Study sections'><a href='#signals'>Findings</a><a href='#cases'>Read the passages</a>"
        "<a href='#sensitivity'>Sensitivity</a><a href='#articles'>All articles</a>"
        "<a href='#method'>Method &amp; references</a></nav></header><main>",
        f"<h1>{TITLE}</h1><p class='lead'>{intro}</p>",
        "<div class='measure'><strong>What counted as unequal treatment?</strong> In one Czech report, a student faced a choice between removing her headscarf and risking expulsion. "
        "The model flagged that account of treatment. It recorded uncertainty about whether the case established an institutional pattern. "
        "<a href='#case-school-hijab'>Follow the evidence</a>.</div>",
        "<dl class='scope-metrics' aria-label='Study coverage'>"
        f"<div><dt>Selected articles</dt><dd>{n}</dd></div>"
        f"<div><dt>Source units</dt><dd>{summary['units']}</dd></div>"
        f"<div><dt>Retained decisions</dt><dd>{summary['decisions']}/{summary['decisions'] + summary['missing']}</dd></div></dl>",
        "" if printing else downloads,
        "<section class='chapter' id='signals'><h2>What the model flagged</h2>",
        f"<p>Each bar counts distinct articles with at least one flagged passage under the named criterion. The full width is {n} articles. A single article can contribute to several rows.</p>",
        f"<div class='chart-grid'>{chart(summary, printing)}</div>",
        f"<p class='caption'>Of {positives} flagged decisions, {voice.get('reported_speaker', 0)} were attributed to reported speakers, {voice.get('quoted_speaker', 0)} to quoted speakers and {voice.get('author', 0)} to article authors. "
        f"The model also recorded {summary['outcomes'].get('uncertain', 0)} uncertain decisions in {summary['uncertain_articles']} articles. Speaker labels are provisional.</p></section>",
    ]
    for i in range(0, len(cases), 2):
        parts.append("<section class='chapter print-page'" + (" id='cases'" if i == 0 else "") + ">")
        if i == 0:
            parts.append(
                "<h2>Read the passages</h2><p>Six examples trace a count back to the text. Each pairs a recorded model outcome with an editorial reading and a question for review.</p>"
            )
        parts.extend(case_html(c, traces[c["slug"]], printing) for c in cases[i : i + 2])
        parts.append("</section>")
    parts += [
        "<section class='chapter print-page' id='sensitivity'>",
        sensitivity_table(diagnostics),
        "</section>",
        "<section class='methods chapter print-page' id='method'>",
        methods(study, summary),
        "</section>",
        "<section class='chapter print-page' id='articles'>",
        directory(study, summary, printing),
        "</section></main>",
        "<footer>Original texts and excerpts are credited to their authors and translators at Global Voices under CC BY 3.0. DOXA added counts, formatting and editorial commentary. "
        + link("Study files and reproduction guide", REPO + "/blob/main/docs/reading-muslim-coverage.md")
        + ("." if printing else " · " + link(
            "Read the companion antisemitism case",
            "https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/",
        ))
        + "</footer>",
    ]
    if not printing:
        parts.append('<script src="reader.js"></script>')
    return "".join(parts) + "</body></html>"


def reader_data(study, summary, readings, traces, diagnostics):
    """Lean, escaped-by-React display model. Full source and raw decisions live in the ZIP."""
    evidence = []
    for c in readings:
        d, a = c["doc"], c["decision"]
        evidence.append(
            {
                "slug": c["slug"],
                "trace": trace_data(traces[c["slug"]]),
                "heading": c["heading"],
                "reading": c["reading"],
                "followup": c["followup"],
                "ref": c["ref"],
                "context": c["context"],
                "origin": c["anchor_origin"],
                "document": d["id"],
                "criterion": TITLES[a["criterion_id"]],
                "result": RESULT_LABELS[a["result"]],
                "voice": VOICE[a["attribution"]],
                "explanation": a.get("explanation"),
                "peer": (TITLES[c["peer"]["criterion_id"]] + " · " + RESULT_LABELS[c["peer"]["result"]])
                if c["peer"]
                else None,
            }
        )
    articles = []
    for doc in study["documents"]:
        excerpts = [
            {
                "criterion": a["criterion_id"],
                "result": RESULT_LABELS[a["result"]],
                "voice": VOICE[a["attribution"]],
                "ref": a["evidence_refs"][0],
                "origin": "Model-selected reference"
                if a.get("detail_supplied")
                else "Software-attached reference",
            }
            for a in doc["decisions"]
            if a["result"] in {"meets_criterion", "uncertain"}
        ]
        articles.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "author": doc["author"],
                "translators": [t["name"] for t in doc["translators"]],
                "url": doc["source_url"],
                "date": doc["published_at"][:10],
                "states": summary["article_states"][doc["id"]],
                "excerpts": excerpts,
            }
        )
    return {
        "title": TITLE,
        "article_count": summary["articles"],
        "flagged_articles": summary["flagged_articles"],
        "flags": summary["outcomes"].get("meets_criterion", 0),
        "voices": summary["flag_voices"],
        "uncertain_decisions": summary["outcomes"].get("uncertain", 0),
        "uncertain_articles": summary["uncertain_articles"],
        "units": summary["units"],
        "decisions": summary["decisions"],
        "missing": summary["missing"],
        "candidates": sum(study["selection"]["candidates_by_year"].values()),
        "criteria": summary["criteria"],
        "sensitivity": diagnostics,
        "frameworks": FRAME_NAMES,
        "state_names": STATE_LABELS,
        "cases": evidence,
        "articles": articles,
        "references": REFERENCES,
    }
