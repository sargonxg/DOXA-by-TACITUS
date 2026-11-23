# DOXA by TACITUS

**Read what a corpus says, who says it, and how the interpretation was reached.**

DOXA is an open-source research workbench for antisemitism, anti-Muslim bias, and political framing. Import documents, choose a framework, and trace model readings to exact passages and frozen criteria. Compare readings and export the evidence for review.

[Antisemitism case](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/) · [Anti-Muslim bias study](https://doxa-public-study.giuliocatanzariti.workers.dev/) · [Run DOXA locally](#start-locally) · [TACITUS](https://tacitus.me/doxa/) · [Download v0.8.0](https://github.com/sargonxg/DOXA-by-TACITUS/releases/tag/v0.8.0)

[![Verify DOXA](https://github.com/sargonxg/DOXA-by-TACITUS/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sargonxg/DOXA-by-TACITUS/actions/workflows/ci.yml)

## Antisemitism case · Whose accusation is it?

A Global Voices report quotes a poster in Plzeň that directed a religious accusation at Jews. The archived model flagged an IHRA hostility criterion. It returned compact outcomes of criterion unmet for the Jerusalem Declaration and Nexus trope criteria on the same unit. Read the passage, assign the voice, and compare the rules before opening the saved result.

[![Pixel-art evidence desk with an open folio, quote bubble, and three criterion cards.](frontend/public/studies/antisemitism/evidence-desk.png)](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/)

The four-article set retains **81 decisions**. Three contrasts cover political expression, a Holocaust analogy, and survivor remembrance. Nine research red flags each come with a follow-up question and a source. [Read the case and methods](docs/antisemitism-case.md) · [PDF](frontend/public/studies/antisemitism/antisemitism-case.pdf) · [JSON-LD graph](frontend/public/studies/antisemitism/evidence.jsonld) · [Publication plan](docs/antisemitism-case-plan.md)

## Anti-Muslim bias study

In a selection of **60 Global Voices articles** published from 2020 to 2025, the recorded model flagged passages in **14 articles**. It attributed **45 of 49 flags** to people the articles quoted or reported. The six walkthroughs let you assess how speaker attribution, context and criterion wording affect a reading. Independent reviewers have yet to assess those readings.

[![The field study presents source counts, the school case and six criterion bars.](docs/images/muslim-coverage-dashboard.png)](https://doxa-public-study.giuliocatanzariti.workers.dev/)

| Read or reproduce | Start here |
| --- | --- |
| **Follow one flag** | A student was reportedly pressured to remove her hijab. Compare the unequal-treatment flag with the uncertain reading about repeated institutional practice. [Open the case](https://doxa-public-study.giuliocatanzariti.workers.dev/#case-school-hijab) |
| **Challenge a reading** | Check a possible false flag and a possible miss against the text and the frozen criteria. [Flag to challenge](https://doxa-public-study.giuliocatanzariti.workers.dev/#case-challenge-a-flag) · [Possible miss](https://doxa-public-study.giuliocatanzariti.workers.dev/#case-possible-miss) |
| **Read or share the study** | Open the briefing and take the results with you. [PDF](frontend/public/studies/muslim-coverage/report.pdf) · [Workbook](frontend/public/studies/muslim-coverage/evidence.xlsx) · [Attributed source texts and decisions](frontend/public/studies/muslim-coverage/sources.zip) |
| **Check the methodology** | See the selection rule, decision denominators, unresolved-reading scenarios and what happens when each year is omitted. [Study methods](docs/reading-muslim-coverage.md) · [Sensitivity table](https://doxa-public-study.giuliocatanzariti.workers.dev/#sensitivity) |
| **Explore a different topic** | Read how a four-source political framing example treats appraisal, attribution and two unresolved decisions. [Annotated PDF](examples/political-framing-commentary.pdf) · [More examples](docs/v3-annotated-examples.md) |

## Follow the evidence graph

The study graph connects every retained decision to a frozen criterion, the archived analysis run and exact passages in versioned sources. The standalone and local readers use that graph to show the evidence path for each walkthrough. The graph distinguishes model-selected evidence, software-attached context and editorial review questions.

```mermaid
flowchart LR
  N[Editorial question] --> D[Recorded decision]
  D --> C[Frozen criterion]
  D --> R[Archived run]
  D --> P[Exact passage]
  P --> S[Versioned source]
```

From a clone, follow the school case or list decisions under one criterion with Python 3.11+.

```sh
python scripts/query_study_graph.py --case school-hijab
python scripts/query_study_graph.py --criterion discrimination_claim
```

[Graph vocabulary and examples](docs/evidence-graph.md) · [JSON-LD export](frontend/public/studies/muslim-coverage/evidence.jsonld) · [Artifact checksums](examples/muslim-coverage.manifest.json)

## Start locally

Use Docker from a clone of this repository.

```sh
docker compose up --build
```

Open **http://localhost:8000** and choose either published study. You can inspect the existing results and practice with fictional texts without a model, key or upload. To use your own documents, open **Import**, review extraction quality, then open **Analyze** to choose a question, framework and processing destination.

To run without Docker, download the application wheel from the release and install it in a Python 3.11+ environment.

```sh
python -m pip install ./doxa_by_tacitus-0.8.0-py3-none-any.whl
doxa doctor --json --data-dir ./doxa-data
doxa --host 127.0.0.1 --port 8000 --data-dir ./doxa-data
```

The wheel includes the browser interface and recorded readers. New analyses use a configured local or remote inference provider. Remote processing requires explicit source-text transfer consent and a budget. [Release files](https://github.com/sargonxg/DOXA-by-TACITUS/releases/tag/v0.8.0) · [System dependencies and deployment](docs/deployment.md)

## Use DOXA in a review

1. **Declare what counts.** Choose the research question, unit of analysis, framework and exclusions. The same passage may support different readings under different criteria. [Measurement cards and independent evaluation](docs/methodology-v06.md)
2. **Read the source in context.** Inspect the passage, speaker, target and stance. A source anchor verifies where the wording occurs. Assessing the interpretation requires judgment. [A worked review protocol](docs/reading-muslim-coverage.md#review-a-reading)
3. **Report the coverage.** Publish the cohort and retained decisions with their denominators. Keep uncertainty, missing decisions and human corrections visible in the review record. [Casebooks and reports](docs/report-jobs.md)

DOXA is a research preview. The two publications use different selection designs. The antisemitism case is a purposive teaching set and the anti-Muslim bias study uses a topic-selected cohort. Claims about prevalence, cross-publisher differences or model accuracy require an appropriate collection and model-blind human review.

## Documentation

- [Documentation index](docs/README.md) · [Workbench and release reference](docs/workbench-reference.md) · [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) · [0.7 delivery plan](docs/release-plan-v07.md) · [Citation](CITATION.cff) · [Apache 2.0 software license](LICENSE)
