"""Render the teaching case from verified graph records and editorial notes."""

from base64 import b64encode
from html import escape
from pathlib import Path

from scripts.antisemitism_case.evidence import FLAGS, FRAMEWORKS, OUTCOMES, REFERENCES
from scripts.bias_study.graph import case_trace, graph_index
from scripts.bias_study.publication import credit, link

CASE_TITLE = "Reading antisemitism across three frameworks"
PUBLIC_STUDY = "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/docs/reading-muslim-coverage.md"
REPO = "https://github.com/sargonxg/DOXA-by-TACITUS"


def case_card(note, trace, index, printing):
    decision = trace["decision"]
    criterion = trace["criterion"]
    passage = trace["passage"]
    outcome = OUTCOMES[decision["outcome"]]
    if criterion["kind"] == "boundary" and decision["outcome"] == "meets_criterion":
        outcome = "Boundary met"
    origin = (
        "Model-selected evidence." if "modelPassage" in decision
        else "Editorial sentence selected from software-attached unit context."
    )
    excerpts = [passage]
    if "modelPassage" in decision:
        same_block = sorted(
            (index[anchor] for anchor in decision["modelPassage"] if index[anchor]["blockId"] == passage["blockId"]),
            key=lambda anchor: anchor["start"],
        )
        if all(a["end"] == b["start"] for a, b in zip(same_block, same_block[1:])):
            excerpts = same_block
    quote = "".join(anchor["exact"] for anchor in excerpts)
    offsets = ", ".join(f"{anchor['start']}–{anchor['end']}" for anchor in excerpts)
    explanation = decision.get("explanation") or "The archive retains a compact outcome without a written rationale."
    speaker = decision.get("speaker") or "Unrecorded"
    attribution = decision["attribution"].replace("_", " ")
    model = (
        f"<div class='model'><p><b>Archived model reading.</b> "
        f"{escape(trace['framework']['title'])} · {escape(criterion['kind'])} · {escape(outcome)}.</p>"
        f"<p><b>Frozen criterion.</b> {escape(criterion['rule'])}</p>"
        f"<p><b>Recorded speaker.</b> {escape(speaker)} ({escape(attribution)}). "
        f"Article stance toward the proposition · {escape(decision['documentStance'])}.</p>"
        f"<p>{escape(explanation)}</p></div>"
    )
    context = ""
    if not printing:
        context = (
            "<details><summary>Read the surrounding source blocks</summary>"
            + "".join(f"<p><b>{block}</b> {escape(text)}</p>" for block, text in note["nearby"])
            + "</details>"
        )
    body = (
        f"<div class='record'><blockquote>{escape(quote)}</blockquote>"
        f"<p class='note'>{origin} {escape(passage['blockId'])} · characters {offsets}.</p>"
        f"{model}<p><b>Editorial reading.</b> {escape(trace['note']['reading'])}</p>"
        f"<p><b>What to check next.</b> {escape(trace['note']['reviewQuestion'])}</p>{context}"
        f"<p class='note breakable'>Assessment {escape(decision['recordId'])} · "
        f"source {escape(trace['source']['recordId'])} · run {escape(trace['run']['recordId'])}.</p></div>"
    )
    return (
        f"<article class='case' id='{note['slug']}'><h3>{escape(note['heading'])}</h3>"
        f"<p class='credit'>{link(note['doc']['title'], note['doc']['source_url'])}<br>{credit(note['doc'])}</p>"
        f"<p class='prompt'><b>Read before opening.</b> {escape(note['prompt'])}</p>"
        + (body if printing else f"<details><summary>Reveal the passage and archived result</summary>{body}</details>")
        + "</article>"
    )


def matrix(index, trace):
    rows = []
    for decision in index.values():
        if decision.get("scoredUnit") != trace["decision"]["scoredUnit"]:
            continue
        criterion = index[decision["evaluates"]]
        framework = index[criterion["inFramework"]]
        outcome = decision["outcome"]
        css_class = " class='comparison-met'" if outcome == "meets_criterion" else ""
        rows.append(
            f"<tr><td>{escape(FRAMEWORKS[framework['recordId']])}</td>"
            f"<th scope='row'>{escape(criterion['title'])}</th>"
            f"<td>{escape(criterion['kind'].title())}</td>"
            f"<td{css_class}>{escape(OUTCOMES[outcome])}</td></tr>"
        )
    return (
        "<div class='matrix' role='region' aria-label='Plzeň criteria comparison' tabindex='0'>"
        "<table><thead><tr><th scope='col'>Framework</th><th scope='col'>Frozen criterion</th>"
        "<th scope='col'>Kind</th><th scope='col'>Archived outcome</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render(study, graph, notes, counts, graphics, *, printing=False):
    css = Path(__file__).with_name("reader.css").read_text(encoding="utf-8")
    index = graph_index(graph)
    traces = {node["recordId"]: case_trace(node, index)
              for node in index.values() if "doxa:EditorialNote" in node["@type"]}
    first = traces[notes[0]["slug"]]
    cards = [case_card(note, traces[note["slug"]], index, printing) for note in notes]

    def art(name, alt, width, height):
        url = "data:image/png;base64," + b64encode(graphics[name]).decode() if printing else name
        return f"<img src='{url}' width='{width}' height='{height}' alt='{escape(alt, quote=True)}'>"

    steps = "".join(
        f"<figure>{art(name, alt, 370, 260)}<figcaption><b>{title}</b>{action}</figcaption></figure>"
        for name, alt, title, action in (
            ("source.png", "Pixel illustration of a magnifier over a highlighted source.",
             "Find the wording", "Start with the exact words and the sentence around them."),
            ("voice.png", "Pixel illustration of two distinct quotation bubbles.",
             "Assign the voice", "Identify who spoke and who reported the statement."),
            ("criterion.png", "Pixel illustration of three separate interpretation cards.",
             "Compare the criteria", "Check the archived outcome under each rule."),
        )
    )
    flags = "".join(
        f"<article><h3>{escape(title)}</h3><p>{escape(signal)}</p><p>{escape(probe)}</p>"
        f"{link(*REFERENCES[source])}</article>" for title, signal, probe, source in FLAGS
    )
    framework_notes = "".join(
        f"<li><b>{escape(FRAMEWORKS[lens['id']])}.</b> {escape(lens['context_rules'])} "
        f"{link(*REFERENCES[lens['id']])}</li>" for lens in study["lenses"]
    )
    title = CASE_TITLE + " An antisemitism case study"
    return f"""<!doctype html><html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="author" content="DOXA by TACITUS">
<meta name="description" content="Read an archived antisemitism case with exact passages, three frozen frameworks, and questions for independent review.">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'">
<title>{title} | DOXA by TACITUS</title><style>{css}</style></head>
<body><header class="site-head"><a href="https://github.com/sargonxg/DOXA-by-TACITUS">DOXA BY TACITUS</a>
<nav class="screen-only" aria-label="Case sections"><a href="#plzen-poster">Plzeň</a><a href="#three-frameworks">Compare frameworks</a>
<a href="#red-flags">Red flags</a><a href="#method">Method</a></nav></header>
<main>
<section class="hero"><div><p class="kicker">Antisemitism case study</p><h1>{CASE_TITLE}</h1>
<p>A news report quotes an accusation against Jews on a shop poster in Plzeň. Follow the exact passage, compare three archived readings, then test your interpretation on three other articles.</p>
<div class="actions screen-only"><a class="action" href="#plzen-poster">Read the Plzeň case</a>
<a href="antisemitism-case.pdf" download>Download PDF</a></div></div>
<figure>{art("evidence-desk.png", "An 8-bit archive desk connects a highlighted source to a quotation and three criterion cards.", 600, 320)}</figure>
</section>
<dl class="facts" aria-label="Teaching set coverage"><div><dt>Selected articles</dt><dd>{counts["articles"]}</dd></div>
<div><dt>Scored units</dt><dd>{counts["units"]}</dd></div>
<div><dt>Retained decisions</dt><dd>{counts["retained"]}/{counts["expected"]}</dd></div></dl>
<section class="chapter" aria-labelledby="reading-steps"><h2 id="reading-steps">Read the passage before the model</h2>
<div class="steps">{steps}</div>
<div class="print-only"><h3>Before you turn the page</h3>
<p>Write down the proposition, the group it targets, and the words that attribute it to a speaker. Keep the reporter’s own language and the reported legal disposition in separate notes.</p>
<p>Then open the Plzeň record and compare the three frameworks. The articles that follow test political expression, historical analogy, and remembrance.</p></div>
{cards[0]}</section>
<section class="chapter new-page" id="three-frameworks"><h2>One Plzeň unit, nine archived decisions</h2>
<p>IHRA’s hostility or stereotyping criterion was the sole indicator met on this unit. The model recorded criterion unmet for the Jerusalem Declaration’s tropes and collective guilt rule and Nexus’s anti-Jewish tropes rule.</p>
{matrix(index, first)}
<p>The compact outcomes supply no model explanation. Read the three rules, decide how each handles reported speech, and write a reading before comparing the results. The divergence is a prompt for review. The table records one model applying distinct criteria.</p>
<details open><summary>How the frozen frameworks guide review</summary><ul class="sources">{framework_notes}</ul></details></section>
<section class="chapter new-page" id="contrast"><h2>Three contrasts to test your reading</h2>
<p>Try an objection to a policy, a dispute over a Holocaust analogy, and a survivor’s remembrance. Open the archived results once you have identified the target, speaker, and evidence.</p>
{''.join(cards[1:])}</section>
<section class="chapter new-page" id="red-flags"><h2>Research red flags and what to check</h2>
<p>Use these cues to decide what evidence to collect. Several cues extend beyond the recorded teaching set. Each needs its own passage, target, and context.</p>
<div class="flags">{flags}</div></section>
<section class="chapter new-page" id="method"><h2>What the record can support</h2>
<p>Four licensed English-edition Global Voices articles were chosen after inspection of the archive. They are from distinct development story groups. The set retains {counts["retained"]} of {counts["expected"]} expected decisions, including {counts["indicators_met"]} indicator met and {counts["boundaries_met"]} boundary met. The 5,000-article parent analysis remains partial. These cases support close reading of the saved results. Prevalence and model performance require separate sampling and independent labels.</p>
<p>The graph binds each decision to its versioned source, frozen criterion, and archived {escape(study["parent"]["model"])} run. It distinguishes model-selected excerpts from software-attached context. The four teaching notes are AI-authored. Independent human labels are unavailable.</p>
<ol class="protocol"><li>Write down the exact proposition, speaker, target, and stance. Record which parts are quoted, reported, or the article’s own language.</li>
<li>Apply a specified criterion and record the supporting span. Describe missing context and plausible alternatives. Keep differing readings visible.</li>
<li>For independent evaluation, collect model-blind judgments and adjudicate differences in a declared protocol. Keep passage-level outcomes, legal findings, and a person’s beliefs separate.</li></ol>
<div class="actions screen-only"><a href="evidence.jsonld" download>Download the JSON-LD graph</a>
<a href="decisions.csv" download>Download all {counts["retained"]} decision rows</a></div>
<p>{link("Case notes, source credits, and reproduction", REPO + "/blob/main/docs/antisemitism-case.md")}</p>
</section>
</main><footer>Published by <a href="https://tacitus.me/#doxa">TACITUS</a> ·
<a href="{PUBLIC_STUDY}">Companion study on anti-Muslim bias</a></footer></body></html>
"""
