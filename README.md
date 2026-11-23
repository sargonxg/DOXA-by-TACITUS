# DOXA by TACITUS

**Analyze political framing and bias in documents and speeches on your laptop.** Import PDFs, documents, transcripts or timed captions. Choose a research question and rubric. DOXA returns a scorecard, a source-by-criterion matrix, a timeline, matched comparisons, and the evidence behind each finding.

[Install the app](#start-locally) · [Worked scorecard](#worked-scorecard) · [Read the PDF](frontend/public/studies/scorecard/doxa-scorecard.pdf) · [Choose a methodology](#what-doxa-measures) · [Download the wheel](https://github.com/sargonxg/DOXA-by-TACITUS/releases/latest)

[![Verify DOXA](https://github.com/sargonxg/DOXA-by-TACITUS/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sargonxg/DOXA-by-TACITUS/actions/workflows/ci.yml)

| Import | Analyze | Compare |
| --- | --- | --- |
| ![Pixel-art documents beside a timed transcript](docs/images/scorecard-intake.png) | ![Pixel-art monitor with three criterion bars](docs/images/scorecard-analysis.png) | ![Pixel-art paired documents with highlighted evidence](docs/images/scorecard-comparison.png) |
| Check extracted text and caption timestamps. | Inspect each rate, its denominator, and unresolved cases. | Open the evidence for each scored criterion and export the result. |

## Start locally

**Install the wheel** in a Python 3.11+ virtual environment. The wheel contains the interface and worked studies. For your own documents, install [Ollama](https://ollama.com/) and ensure its local service is running.

```sh
python -m pip install ./doxa_by_tacitus-0.9.0-py3-none-any.whl
doxa doctor --json --data-dir ./doxa-data
doxa --host 127.0.0.1 --port 8000 --data-dir ./doxa-data
```

Open **http://127.0.0.1:8000**. Select **Open worked example** to inspect the bundled scorecard immediately. To run a fresh analysis, explicitly install the quantized weights once.

```sh
ollama pull qwen3.5:2b-q4_K_M
```

1. **Import** PDF, DOCX, TXT, Markdown, saved HTML, EML, MBOX, SRT, WebVTT, or a ZIP of sources. Check missing PDF text, OCR, and rejected caption cues before scoring.
2. **Analyze** with the bounded laptop preset. Enter a question and choose one or more frameworks within the displayed limit. Check the workload and provider. Jobs can pause between tasks and resume from saved decisions.
3. **Save a scorecard** once results arrive. Inspect the matrix, timeline, and exact passages. Export PDF, offline HTML, XLSX, CSV, or JSONL together with the evidence graph.

The native laptop preset uses a Q4 model, 4,096-token context, two CPU threads and a single request at a time. It starts with a limit of eight sources, 24 text units and six criteria. Run inputs and results are stored in the workspace, with inference sent to Ollama on loopback. The worked examples open without a model. A real Q4 run on a managed Linux VM retained 15 of 16 decisions and exported the scorecard. The physical 8 GB laptop check remains pending. Use `docker compose up --build` from a source checkout for the Docker installation path. [Local requirements and PDF dependencies](docs/deployment.md) · [Measured run and methodology](docs/research-scorecards.md)

## What DOXA measures

A **criterion** defines an observable pattern and the context needed to assess it. A source can meet a criterion in one passage and contain competing evidence elsewhere. Choose the lens that matches the research question and inspect the underlying text when interpreting the scorecard.

| Lens | What to look for | What qualifies the reading |
| --- | --- | --- |
| **Political positions and framing** | Policy stance, appraisal, responsibility, prominence, comparisons, and backgrounded assumptions. | Declare the proposition or issue. A claim of unequal standards needs comparable evidence from both sides. |
| **Discourse and rhetoric** | How language defines problems, assigns causes, appeals to values, and uses devices such as stereotypes, metaphors, and contrasts. | Describe the device in its local context and consider an alternative reading. |
| **Antisemitism · IHRA** | Anti-Jewish hostility or stereotyping, collective blame, and Israel-related examples. | Read the examples in context and examine the specific basis for the criterion. [IHRA working definition](https://holocaustremembrance.com/resources/working-definition-antisemitism) |
| **Antisemitism · Jerusalem Declaration** | Discrimination or hostility toward Jews as Jews, essentializing tropes, collective guilt, and boundaries for political expression. | Apply the declaration's contextual guidelines to the passage. [Jerusalem Declaration](https://jerusalemdeclaration.org/) |
| **Antisemitism · Nexus** | Anti-Jewish tropes, discrimination or collective blame, including in discourse about Israel or Zionism. | Apply the Nexus boundary for political opposition alongside its antisemitism criteria. [Nexus Document](https://nexusproject.us/the-nexus-document/) |
| **Anti-Muslim hostility · England** | Hostile expression directed at Muslims or perceived Muslims, relevant stereotyping, and unequal treatment. | Identify the target and supporting context. Claims about intent require their own evidence. [England guidance](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility) |
| **Anti-Muslim racism · structural lens** | Racialization, exclusion, and repeated institutional practices affecting Muslims or people perceived to be Muslim. | An institutional-pattern finding requires evidence of a practice or recurrence. [Runnymede Trust framework](https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition) |
| **Language norms** | Arguments over acceptable language, labels, norms of respect, and institutional restrictions on expression. | State whose norm applies in the chosen setting and record competing interpretations. |
| **Custom rubrics** | A project-specific construct, such as evidence standards in municipal policy briefings. | Write an observable inclusion rule and a counterexample for each criterion before analysis. |

### Six questions for political analysis

| Question | Evidence to look for |
| --- | --- |
| **What policy does the text support or oppose?** | A stance toward a specific proposal, such as narrowing an exclusion zone. |
| **How does it evaluate the policy or people affected?** | A phrase such as “proportionate safeguard” or “burden on assembly,” with its target. |
| **What cause or responsibility does it assign?** | An explicit claim linking a policy, actor, or condition to an outcome. |
| **Which evidence does it foreground?** | A dispatch log, cost estimate, testimony, or other source given prominence in the text. |
| **Does it apply different standards in comparable cases?** | Evidence on both sides of a declared match. An omission question also needs an inventory of relevant information available at the time. |
| **What assumption does the wording take for granted?** | A phrase such as “restore order,” alongside the premise it may imply and a plausible alternative interpretation. |

The scorecard distinguishes **matches**, **assessed non-matches**, **uncertain readings**, **inapplicable criteria**, and **incomplete sources**. It reports both retained decisions per planned decision and determinate sources per eligible source. Each prejudice framework retains its own results and political-expression boundaries. [Political methodology and real-source walkthrough](docs/political-bias-methodology.md) · [Measurement cards and evaluation](docs/methodology-v06.md)

## Worked scorecard

Two briefings and two captioned speeches discuss the same proposed restriction on demonstrations. Both issuer groups address emergency access, yet they recommend different responses. Three weighted measures give the fictional corpus a **60.0 / 100 public-order justification score**.

| Criterion | Positive / determinate | Weight | Contribution |
| --- | ---: | ---: | ---: |
| Documented safety risk | 3 / 4 | 40% | 30.0 |
| Favorable appraisal of restrictions | 2 / 4 | 25% | 12.5 |
| Restriction as remedy | 2 / 4 | 35% | 17.5 |

The unweighted premise criterion identifies a passage where “restore order” could describe traffic or behavior at the demonstration. The comparison counts two matched pairs and shows a **50 percentage-point** difference in the documented-risk measure. The labels were authored for teaching. Weight choices and the corpus define what the index measures. Each weighted criterion needs at least 80% determinate coverage. [Walkthrough and reproduction](docs/research-scorecards.md) · [Inputs](frontend/public/studies/scorecard/starter.zip) · [PDF](frontend/public/studies/scorecard/doxa-scorecard.pdf) · [Workbook](frontend/public/studies/scorecard/doxa-scorecard.xlsx) · [JSONL](frontend/public/studies/scorecard/doxa-scorecard.jsonl)

## Worked studies

| Research question | Evidence and downloads |
| --- | --- |
| **Antisemitism. What distinguishes a religious accusation, political criticism, and Holocaust remembrance?** Compare three frameworks across the four-source Plzeň teaching set, with 81 retained decisions and nine research red flags. | [Case and red flags](docs/antisemitism-case.md) · [PDF](frontend/public/studies/antisemitism/antisemitism-case.pdf) · [Graph](frontend/public/studies/antisemitism/evidence.jsonld) |
| **Anti-Muslim bias. What evidence supports an unequal-treatment flag or a repeated institutional pattern?** A topic-selected set of 60 Global Voices articles lets you compare these readings and see how results change under different assumptions about unresolved articles. | [Study and sensitivity analysis](docs/reading-muslim-coverage.md) · [PDF](frontend/public/studies/muslim-coverage/report.pdf) · [Evidence workbook](frontend/public/studies/muslim-coverage/evidence.xlsx) |
| **Political framing. How do institutional texts justify remedies, assign responsibility and set boundaries on political expression?** Read four licensed sources with exact anchors and declared comparability limits. | [Real-source walkthrough](docs/political-bias-methodology.md#reproducible-real-source-case-packet) · [Annotated PDF](examples/political-framing-commentary.pdf) |

These collections support worked interpretations under declared rubrics. Independent human labels are pending for the archived model decisions. Population-level and accuracy claims require their own sampling and evaluation. [Evaluation protocol](docs/methodology-v06.md)

## Evidence graph

The scorecard graph links each source-level result to its criterion, run, and representative passage. The passage stores exact text and a source locator. The example graph contains enough information to recompute the documented-risk bar as **3 ÷ 4 = 75%**.

```sh
python -m doxa.scorecard_graph frontend/public/studies/scorecard/scorecard-evidence.jsonld custom/custom-1
```

[Graph data](frontend/public/studies/scorecard/scorecard-evidence.jsonld) · [Vocabulary and offline query](docs/evidence-graph.md)

## Documentation

[Documentation index](docs/README.md) · [Workbench reference](docs/workbench-reference.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Citation](CITATION.cff) · [Apache 2.0 license](LICENSE) · [TACITUS](https://tacitus.me/#doxa)
