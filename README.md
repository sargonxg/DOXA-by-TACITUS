# DOXA by TACITUS

**Read what a corpus says, who says it, and how the interpretation was reached.**

DOXA is a browser research workspace for discourse, political framing and contextual prejudice analysis. Import sources, declare an analytical framework, inspect exact passages, compare patterns and download a reproducible research record.

**Bias Detection as a Service** means an open-source toolkit you run on your computer or in your own cloud, with optional [TACITUS methodology and deployment support](https://tacitus.me/doxa/). This repository does not provision a hosted SaaS account. The software is a **research preview**: source anchoring and processing coverage are checked; interpretation accuracy requires independent human reference labels.

![The actual v3 workspace: source collection, linked timeline and criterion matrix, retained observations, and evidence inspector.](docs/images/workspace-v3-news.png)

*Actual interface with 5,000 real news articles. This screenshot records a partial model run; imported sources, completed source checks and correct interpretations are different quantities.*

## Open a real, commented report

<a href="examples/political-framing-commentary.pdf"><img src="docs/images/political-framing-report-preview.png" width="360" alt="Open the political framing PDF: real source wording, recorded model readings, and separately labeled AI-authored commentary."></a> <a href="examples/institutional-context-commentary.pdf"><img src="docs/images/institutional-context-report-preview.png" width="320" alt="Open the institutional context PDF: protective language, reported discrimination, framework boundaries and uncertainty."></a>

**[Read the political-framing PDF](examples/political-framing-commentary.pdf)** · [Standalone HTML](examples/political-framing-commentary.html) · [Excel case workbook](examples/political-framing-commentary.xlsx) · [Frozen snapshot](examples/political-framing-commentary.snapshot.json.gz) · [Checksums and manifest](examples/political-framing-commentary.manifest.json)

This recorded four-source case packet retains **358 of 360 criterion decisions**, **59 of 60 fully assessed source units**, and **3 of 4 fully assessed documents**. **Two criterion decisions remain unresolved.** Its commentary is AI-authored reading guidance, **not independent human adjudication or human gold labels**. Original model output, unknown attribution fields, review state and incomplete coverage remain visible.

The **[institutional-context PDF](examples/institutional-context-commentary.pdf)** follows four illustrative cases from the 20-source run: protective language, reported discrimination, a framework boundary and criterion-specific uncertainty. The run retains **11,701 of 11,704 decisions**, **613 of 616 fully assessed units**, and **18 of 20 fully assessed documents**; **three decisions remain unresolved**. Its reading notes are also AI-authored, not human gold. [HTML](examples/institutional-context-commentary.html) · [XLSX](examples/institutional-context-commentary.xlsx) · [Snapshot](examples/institutional-context-commentary.snapshot.json.gz) · [Manifest](examples/institutional-context-commentary.manifest.json).

The [annotated-example guide](docs/v3-annotated-examples.md) explains the source → model reading → commentary → review distinction. Opening these files makes no new inference requests.

## Start locally

From a clone of this repository, Docker provides the complete application:

```sh
docker compose up --build
```

Open **http://localhost:8000**. In **Import**, load a recorded collection or add your own files. Inspect saved results without an inference key. **Analyze** starts a new inquiry; remote inference requires explicit source-text transfer consent and a budget. The named Docker volume persists your workspace.

For native development, use Python 3.11+ and Node 22+:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m doxa.cli --host 127.0.0.1 --port 8000 --data-dir data
```

On Windows, use `.venv\Scripts\python.exe`. For UI development, `npm --prefix frontend run dev` proxies the API to port 8000. See [installation, persistence, OCR and deployment](docs/deployment.md).

For a fully local inference fallback, install [Ollama](https://ollama.com/download), explicitly download the model, and select it in **Analyze**:

```sh
ollama pull qwen3.5:4b
```

The conservative profile targets a **16 GB computer**, **Q4 weights**, an **8K context** and **one concurrent request**, with CPU-only operation supported. This is a resource target, not a full-corpus speed guarantee. Native Ollama uses Metal on Apple silicon; [Compose also supplies an optional CPU Ollama service](docs/deployment.md#local-models). The app never downloads weights automatically.

## One linked workspace, ten capabilities

| Capability | What you can inspect or do |
| --- | --- |
| **1. Linked workspace** | Sources, observations, timeline, author × criterion matrix and evidence inspector share a frozen server query. Cursor pages and virtual rows keep large collections bounded. |
| **2. Bulk import** | TXT, Markdown, PDF, DOCX, saved HTML and ZIP; resumable 8 MiB parts for large files, grouped imports, source versions and explicit extraction failures. OCR is optional. |
| **3. Authors and metadata** | Filter credited bylines, document type, publication period and import batch. Preserve author/institution, URLs, rights, hashes and translation roles. |
| **4. Explicit frameworks** | Political framing, discourse, IHRA, Jerusalem Declaration, Nexus, anti-Muslim hostility, structural racism and language norms; add a declared custom rubric. |
| **5. Context and evidence** | Exact Unicode source spans, surrounding text, proposition, speaker attribution, speaker/document stance, hypothetical status, alternatives and uncertainty. |
| **6. Temporal monitoring** | Publication trajectories and assessment history use separate clocks. Eligible-source denominators, coverage and comparison gates qualify statistical review candidates. |
| **7. Independent verification** | A separate GPT-OSS 120B job checks indicator-positive/uncertain units plus a 5% target sample of other accepted units, which are not validated negatives. Consent, a $2 cap and literal disagreements stay explicit. |
| **8. Reviewed learning** | Human-approved examples → immutable memory snapshots → paired candidate evaluation → explicit activation or rollback. No automatic weight training. |
| **9. Optional CPU tools** | Persistent MiniLM passage retrieval and independent DeBERTa hypothesis scoring, with pinned artifacts, exact source windows and uncalibrated scores. |
| **10. Downloadable research** | PDF, offline HTML, XLSX, CSV and JSONL; research/civil-society, newsroom/policy and teaching presentations preserve the same evidence and query scope. PDF/HTML appendices disclose their bounds; data exports retain the complete selection. |

Successful manual imports select **Corpus · all imported sources** so new files remain visible. Corpus view makes no model-assessment claims; an analysis selection shows that run's frozen cohort. Analyze the current corpus scope to include newly imported material.

![Source versions, method, exact passages, review and reports remain connected.](docs/images/doxa-process.svg)

## How political analysis works

The `political-framing` rubric asks six descriptive questions: expressed policy position, evaluative language, agency/causal responsibility, foregrounded voices, comparator-dependent asymmetry and presupposition hypotheses. Detecting one of these textual features does not establish prejudice, factual error, unfairness or a private political identity.

| Source wording | An inspectable reading | What still needs evidence |
| --- | --- | --- |
| Executive Order 14149 says the previous administration **“trampled free speech rights.”** | “Trampled” is an appraisal; the surrounding sentence alleges coercion involving online platforms. | Whether the alleged events occurred, whether the characterization is fair, and whether a relevant comparator shows unequal treatment. |
| Executive Order 14188 proposes **“all available and appropriate legal tools”** against unlawful antisemitic harassment and violence. | An expressed enforcement policy, with legal qualifications that must survive extraction and interpretation. | Support for an enforcement policy is not endorsement of antisemitism. The policy's effects or comparative fairness require additional evidence. |

The saved model leaves attribution **unknown** in these examples. The commentary points out the institutional-voice question; it does not silently repair the record or present the note as a human verdict. Read the [research-backed rubric, exact anchors and comparator policy](docs/political-bias-methodology.md).

![Separate the document's position from quoted or reported speakers and their stance.](docs/images/doxa-attribution.svg)

IHRA, Jerusalem and Nexus remain distinct frameworks. A descriptive frame, a prejudice indicator and a boundary/exclusion condition have different meanings. User-specified reviewer roles declare analytical priorities; they do not simulate protected demographic traits. No universal left/right or “politically correct” score is produced. An omission or double-standard audit needs a declared relevant comparator and paired evidence; a single document cannot establish one.

The contextual engine separates compact classification from detailed interpretation:

```mermaid
flowchart LR
  S["Frozen source units + versioned criteria"] --> C["Stage 1: compact criterion decisions"]
  C --> V["Validate explicit decision coverage"]
  V --> N{"Noteworthy or uncertain?"}
  N -->|Yes| D["Stage 2: contextual details"]
  N -->|No| L["Typed assessment ledger"]
  D --> A["Check source sentence IDs and exact spans"]
  A -->|Valid| L
  V -->|Missing or invalid| R["Bounded retry; unresolved stays visible"]
  A -->|Invalid or incomplete| R
  R --> L
  L --> H["Separate human review overlay"]
```

**Exact-source checks are not semantic-accuracy checks.** Missing or rejected decisions stay unresolved rather than becoming negative findings. Framework agreement and independent-model agreement are not accuracy. The [v3 research dossier](docs/research-v3.md) records the rationale, engineering pilot and their limits.

## Real sources, with declared boundaries

| Collection | Frozen material and intended use |
| --- | --- |
| **Global Voices news** | **5,000 articles**, **5,251,119 whitespace-counted words**, **778 distinct credited author names**, **585 translated articles**, **72 publication months across 2020–2025**. A month-stratified split holds **4,000 development / 1,000 held-out article groups** apart. |
| **Public institutions** | **20 UK and US publications**, approximately **357,000 words**: strategies, executive orders, reviews, discrimination guidance and policy responses. A purposive case collection with separate source-specific rights. |

The news file/provenance audit and independent source-character audit both passed **5,000/5,000**. These establish source integrity, not interpretation accuracy. Global Voices is one publisher's collection, not a representative sample of all news or political perspectives. Current text was frozen at collection time; publication-date plots do not reconstruct every original edition.

Per-source records retain authors/translators, URLs, licences, extraction changes and hashes. Global Voices material follows observed CC BY 3.0/publisher conditions and recorded exclusions; institutional sources retain their own notices. This is a documented rights audit, not exhaustive legal clearance. See [news collection and audits](docs/news-corpus.md), [institutional corpus](docs/corpus.md) and [licences](docs/licenses.md).

![Publication-time discourse and assessment/review history are different trajectories.](docs/images/doxa-trajectory.svg)

Rates describe the selected imported corpus. Reporting about hostility, quoted hostility and author-endorsed hostility remain separate. Missing analysis is not absence; unknown dates are not invented. The diagram is schematic, not a measured spike or improvement claim.

## Models and a practical growth path

| Profile | Role and limits |
| --- | --- |
| [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Apache-2.0 CPU retrieval: **384-dimensional** vectors and a **256-wordpiece** sentence-transformer limit. Similarity does not establish shared stance or prejudice. |
| [DeBERTa-v3-base-zeroshot-v2.0-c](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c) | MIT pretrained NLI; each explicit hypothesis is scored independently within its combined token budget. Scores are **uncalibrated**, not probabilities of prejudice; not-entailment is not contradiction. |
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) / Ollama | Conservative local contextual inference; model digest, quantization, context and observed resource use remain recorded. |
| [Gemma 4 26B A4B](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) | Managed cloud primary. Availability, provider-managed revision limits, token use and application budget accounting are recorded. |
| [GPT-OSS 120B](https://huggingface.co/openai/gpt-oss-120b) | Independent model verification through Cloudflare. Its separate comparison exposes paired differences and incomplete selection coverage; it does not adjudicate the primary model. |
| [vLLM](https://docs.vllm.ai/) / [RunPod](https://docs.runpod.io/) | Configured OpenAI-compatible or GPU endpoints for larger-model experiments. Provider adapters are replaceable; larger weights are not presumed improvements. |

DOXA has **not fine-tuned these models**. The current methodology and CPU profiles are English-first; multilingual claims need separate validation. Optional CPU models are installed explicitly and loaded from verified, pinned local artifacts. Start with one question, one source cohort and one declared rubric; add reviewed examples or another provider while preserving the baseline. See [installation and model profiles](docs/models-v3.md).

A full 5,000-source CPU index can take many minutes. The [actual canonical-cohort benchmark](benchmarks/cpu-semantic-5000-summary.json) built **120,464 passages**, verified restart/reuse and exact search quotations, and recorded approximately **49.91 active indexing minutes** across its attempts. This does not measure ranking accuracy or promise another computer's speed. The current browser build action waits on one HTTP request and can hit a proxy timeout; prefer `python scripts/semantic_index.py build PROJECT_ID --data-dir data` for full collections, then search the persisted index in the browser. Run heavy local generation and indexing sequentially when they contend for CPU.

![Reviewed examples require a frozen candidate, evaluation and explicit activation before affecting future runs.](docs/images/doxa-reviewed-learning.svg)

Held-out/story groups remain separate from approved example memory. A passing software check or model-agreement result does not become an independent reference label. [The service-toolkit guide](docs/v3-service-toolkit.md) explains reviewed learning, attribution, source history and rollback.

## Local first; optional cloud infrastructure

The lowest-infrastructure starting point is the local application with SQLite/files and optional metered cloud inference. Keep inference local when appropriate; choose managed inference when its measured throughput and token cost suit the workload. Selecting remote inference sends approved source text to that provider; credentials stay on the server.

The optional Cloudflare deployment supplies Access authentication, a Worker/static UI, D1, R2, durable Queues and a Python Container running the shared engine. It is a shared private workspace for its permitted users; isolate deployments for groups requiring separate data. RunPod/GPU rental adds its own operating and idle costs. Provisioning is explicit: installation and tests do not deploy a service.

```mermaid
flowchart LR
  B["Linked browser workspace"] --> API["Versioned API + frozen queries"]
  API --> ST["SQLite/files or authenticated D1/R2 bridge"]
  API --> J["Durable source-unit jobs"]
  J --> E["Shared Python engine"]
  E --> M["Ollama / Workers AI / vLLM / RunPod"]
  E --> EV["Typed outcomes + source validation"]
  EV --> ST
  ST --> RP["Review history + reproducible exports"]
```

The default **$20/month application variable-spend cap is configurable**. It controls application-accounted usage, **not the whole vendor bill**: fixed plans, storage, containers, GPU time and unreported charges can differ. Unknown provider billing stays unknown. Each run also has its own budget; independent verification is capped at $2. [Deployment and cost guidance](docs/deployment.md) covers configuration, authentication, persistence and cleanup.

## Reproduce and inspect

The [v3 verification record](docs/verification-v3.md) lists the actual software, browser, corpus and runtime checks, their artifacts, and what they establish.

```sh
pytest -q
npm --prefix frontend ci && npm --prefix frontend run build
npm --prefix infra/cloudflare ci && npm --prefix infra/cloudflare test
.venv/bin/python tests/browser_v3.py
.venv/bin/python tests/browser_import_scope.py
.venv/bin/python tests/browser_verification.py
```

Browser contract tests use isolated synthetic fixtures and mocked paid requests; they establish application behavior, not model quality. Separate checks cover actual 11 MiB refresh/reselection recovery, grouped imports and a read-only copy of the real 5,000-source workspace. See [frontend checks](frontend/README.md).

To run a **new** whole-article local pilot after installing Ollama:

```sh
python scripts/benchmark_v3.py --collection news-sample --limit 20 \
  --provider ollama --model qwen3.5:4b --concurrency 1 \
  --data-dir data/my-local-pilot --output benchmarks/my-local-pilot.json
```

For the full 5,000-source selection, configure Cloudflare credentials and deliberately request paid inference with separate state/output paths:

```sh
python scripts/benchmark_v3.py --collection news --provider cloudflare \
  --model @cf/google/gemma-4-26b-a4b-it --concurrency 4 --budget 5 \
  --data-dir data/my-cloud-news --output benchmarks/my-cloud-news.json
```

The budget does not promise completion. **Resume by repeating the identical command, paths and analytical arguments with `--resume`**. Source/model/method changes require a new run. Coverage, unresolved decisions, resource measurements and token/cost accounting—not merely a finished process—determine what a benchmark establishes.

[Recorded benchmark summaries](benchmarks/README.md) distinguish current partial runs, scoped pilots and historical v1 artifacts. Full-corpus model coverage and independently validated interpretation accuracy must not be inferred from source audits, cached demos or the political packet. The earlier [v1 institutional PDF](examples/doxa-public-institutions.pdf) remains a historical example.

## Explore the project

- [V3 research and measured pilot](docs/research-v3.md) · [political-bias methodology](docs/political-bias-methodology.md) · [interpretation contract](docs/v3-interpretation.md)
- [Models and CPU retrieval](docs/models-v3.md) · [architecture](docs/architecture.md) · [deployment](docs/deployment.md) · [benchmarking](docs/benchmarking.md)
- [Annotated real-source examples](docs/v3-annotated-examples.md) · [service toolkit](docs/v3-service-toolkit.md) · [research background](docs/research.md)

<details><summary>Prefer an illustrated introduction? Follow DOXA Quest.</summary>

![An illustrative 8-bit map: import, choose a lens, run an engine, inspect evidence, compare and export.](docs/images/doxa-quest.png)

Read the [updated Quest guide](docs/guide.md). The pixel map explains the research steps; the current application uses one linked workspace and Import, Analyze, Models, Processing and Reports drawers.

</details>

Original application code is Apache 2.0. Dependencies and source materials retain their own licences, including PyMuPDF's separate terms. Keep originals, rights notices, model provenance and human review attached to conclusions.
