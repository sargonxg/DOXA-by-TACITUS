# DOXA research dossier

Research checked: **16 November 2026**. This document records the conceptual basis, primary sources, technology choices, and validation requirements for DOXA by TACITUS. It distinguishes published documentation from experimental evidence. Model availability, prices, and service limits are a dated snapshot and must be checked again before deployment.

**No DOXA inference-quality experiment, hardware throughput measurement, or independent human annotation study was conducted as part of this literature and product research.** Documentation that a model supports a feature is not evidence that it interprets a document correctly. Product functionality and completed verification are described in the repository's implementation and benchmark records.

## 1. What a defensible DOXA analysis can establish

In social theory, *doxa* concerns assumptions treated as self-evident or taken for granted. It is a useful organizing concept, not an established numeric outcome that an off-the-shelf model measures. A corpus can support observations about recurring propositions, frames, presuppositions, boundaries of acceptable speech, and positions expressed in documents. Inferring what an author privately believes, or what an entire population accepts, requires evidence beyond those texts.

The product should therefore expose several traceable analyses rather than collapse them into a universal ideology, prejudice, or "doxa" score:

| Layer | Useful observation | Required qualification |
| --- | --- | --- |
| Corpus description | Document type, date, supplied author, topics, named entities | Metadata may be absent or wrong; distinguish supplied, extracted, and corrected values. |
| Stance | Support, opposition, mixed position, or insufficient evidence toward a specified proposition | Sentiment about a passage is not stance toward every entity it mentions. |
| Framing | Problem definition, proposed causes, moral evaluation, proposed remedies | A frame is an analytic interpretation; competing frames can coexist. |
| Assumptions | A proposition apparently presupposed or treated as given | Present as a hypothesis with a trigger and counterinterpretation, not privileged access to an author's mind. |
| Rhetoric | Generalization, essentialization, dehumanization, collective blame, threat, or quotation | The speaker and the author's relationship to a quoted statement must be established separately. |
| Definition-based review | A passage potentially meeting a criterion of a named, versioned lens | Definitions differ. A result under one lens is not a universal or legal verdict. |

The [Media Frames Corpus paper](https://aclanthology.org/P15-2072/) supplies a practical precedent for annotating framing across policy issues. [Atwell, Simons, and Alikhani (2025)](https://aclanthology.org/2025.findings-acl.107/) studied human/model agreement on presupposition judgments and found discrepancies associated with linguistic factors and possible gender and political-ideology biases. This supports a reviewable hypothesis interface, not automatic acceptance of inferred assumptions.

Each finding should resolve to an exact span of extracted source text and retain surrounding context. Record the document, page where meaningful, paragraph or character offsets, target proposition, quoted speaker, document author, observation, lens criterion, alternative interpretation, uncertainty, human review state, model identity, prompt version, and run version. Validate quotes against the extracted source before displaying them. A source quote being exact proves provenance; it does not prove the interpretation is correct.

Keep unknown authors and speakers unknown. Merge author identities only from explicit metadata or an analyst's correction. Distinguish reporting, endorsement, rebuttal, historical description, hypothetical examples, and ambiguous attribution. Do not infer an author's religion or ethnicity from a name or expressed viewpoint. Aggregate what documents express and expose the denominator. Missing extraction or unsupported languages must not become "no issue found."

"Missing voices" is a comparison question: it needs a declared reference corpus, sampling frame, or stakeholder list. A single document cannot establish that a viewpoint has been suppressed. Similarly, an increase in matching passages may reflect more documents, changed source mix, duplicate coverage, or a new rubric rather than a change in underlying beliefs.

## 2. Definitions and analyst perspectives

### Antisemitism: preserve differences between frameworks

Three influential frameworks are relevant and must remain identifiable:

| Framework | Verified scope and distinctions | Product implication |
| --- | --- | --- |
| [IHRA working definition](https://holocaustremembrance.com/resources/working-definition-antisemitism), adopted 2016 | A non-legally-binding definition with illustrative examples applied in overall context. It expressly excludes criticism of Israel similar to criticism of other countries. | Store the framework/version and criterion. Include contextual qualifications rather than use an isolated keyword or example as a categorical rule. |
| [Jerusalem Declaration on Antisemitism](https://jerusalemdeclaration.org/), launched 25 March 2021 | Definition plus 15 contextual guidelines. Its examples distinguish anti-Jewish prejudice from, in themselves, BDS, opposition to Zionism, equal-rights constitutional alternatives, and disproportionate political criticism. Its FAQ rejects use as a legal or quasi-legal speech code. | Preserve its explicit exclusions and review Israel/Palestine guidelines together. Do not equate political disagreement with prejudice. |
| [Nexus Document](https://nexusproject.us/nexus-resources/the-nexus-document/), February 2021; page identifies June 2024 update | Covers anti-Jewish beliefs, attitudes, actions, and systemic conditions. Distinguishes anti-Jewish tropes and collective blame from political opposition. Disproportionate attention to Israel is not prima facie proof of antisemitism. | Keep systemic observations separate from explicit threats or stereotypes. Require evidence for a claimed anti-Jewish connection. |

The IHRA primary page was discovered in live search but blocked automated content extraction during this research. Its wording was cross-checked against the [archived US State Department reproduction](https://2017-2021.state.gov/defining-anti-semitism). This does not establish a redistribution licence for the underlying IHRA text. Linking to or referencing a framework is different from bundling its complete text.

Offer side-by-side results on the same evidence, including disagreement and insufficient context. Do not average the three definitions or count agreement between them as statistical proof. Changing a lens or its operationalization creates a new analysis run. Any locally written criterion paraphrase must be labelled as DOXA's operationalization and linked to the original framework; it must not imply endorsement by its authors.

### Islamophobia and anti-Muslim hostility

[Runnymede's 2025 briefing](https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition) describes Islamophobia as racism toward Muslims that can be institutional and systemic. Its public landing-page summary was read; the linked full briefing was not independently reviewed in this research. The summary is sufficient to establish a structural-racism perspective, but not to invent a detailed official annotation taxonomy.

England's [non-statutory definition of anti-Muslim hostility](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility), published 9 March 2026, is a distinct framework. It covers intentional criminal conduct, prejudicial stereotyping intended to encourage hatred, and deliberately targeted unlawful discrimination. It says the definition and accompanying context must be read together. Its explicit protected-expression examples include criticism and ridicule of Islam, its practices, and beliefs, as well as academic and political debate.

The England framework is jurisdiction-specific, non-statutory, and sensitive to intention. A document may express stereotyping without supplying enough evidence to establish intent; text analysis also cannot itself determine whether conduct is unlawful. Keep observable language, inferred intent, and legal status separate. Do not relabel every criticism of religion as hostility toward its adherents.

### Political correctness and personas

"Political correctness" is a contested expression, not a single ground-truth measurement. Support a declared question and rubric: inclusive-language norms, claims of censorship, institutional expectations, exclusion, euphemism, acceptable disagreement, or how speakers construct those disputes. Record whose standards are being used and which period and institution they concern.

The three report audiences are researchers/civil society, newsrooms/policy analysts, and universities/teaching. These should change explanatory emphasis and presentation, while keeping the source evidence and frozen run visible. A researcher needs sampling and error detail; a newsroom needs concise, attributable findings; teaching needs definitions, contrasting readings, and discussion prompts.

Personas are analyst roles and declared priorities. A prompt asking a model to speak as a demographic group is not evidence of that group's opinions. Do not present generated persona disagreement as a survey, actual expert review, or measured public opinion.

## 3. Model and inference landscape

### Why start with prompting and evaluation

An instruction model can apply a supplied rubric and return structured findings without a bespoke fine-tune. [Patel, Mehta, and Blackburn (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.1792/) evaluate open models using in-context antisemitism definitions. Their research motivates testing policy-conditioned prompting; its results are not measurements of DOXA, the models below, or long PDF corpora.

Models trained for generic toxicity or short social posts answer a different question from contextual discourse analysis. A fluent explanation, generic benchmark score, large context window, or safety-training label does not establish source attribution, understanding of political context, or calibrated confidence.

### Current cloud candidates

The following prices and capabilities were checked in Cloudflare's model documentation on 16 November 2026. Prices are USD per million billed tokens. These are **candidates for comparison**, not a semantic quality ranking.

| Model | Advertised context | Input / output price | Proposed experimental role |
| --- | ---: | ---: | --- |
| [Gemma 4 26B A4B IT](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) | 256,000 | $0.10 / $0.30 | Budget cloud candidate; benchmark with thinking explicitly disabled, then separately enabled. |
| [GPT-oss 20B](https://developers.cloudflare.com/workers-ai/models/gpt-oss-20b/) | 128,000 | $0.20 / $0.30 | Medium-size comparison and potential local/cloud comparison. |
| [GPT-oss 120B](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/) | 128,000 | $0.35 / $0.75 | Larger-model comparison for difficult contextual passages. |
| [Llama 3.3 70B FP8 fast](https://developers.cloudflare.com/workers-ai/models/llama-3.3-70b-instruct-fp8-fast/) | 24,000 | $0.293 / $2.253 | Additional baseline; do not assume a larger family context than the hosted variant advertises. |
| [Qwen3.8 27B](https://developers.cloudflare.com/workers-ai/models/qwen3.8-27b/) | 262,144 | $0.45 / $3.20 | Optional quality comparison with explicit reasoning/output budget. |

Cloudflare currently advertises batch support and zero data retention for these model pages. Those statements do not remove the need to review current account terms, logging, retention elsewhere in the application, and data-processing requirements. Batch capability alone does not establish a price discount. Check the [pricing table](https://developers.cloudflare.com/workers-ai/platform/pricing/) before relying on this snapshot.

Gemma 4's [published input schema](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/sync-input.json) supports `response_format` with `type: "json_schema"`, a named schema and `strict: true`; `chat_template_kwargs.enable_thinking`; `max_completion_tokens`; and `store: false`. Thinking defaults to enabled in that schema. Explicitly disable it for a budget baseline and record the choice. [Gemma 4's licence page](https://ai.google.dev/gemma/docs/gemma_4_license) and [official model card](https://huggingface.co/google/gemma-4-26B-A4B-it) identify Apache 2.0; do not assume earlier Gemma-generation terms apply. GPT-oss is also distributed under Apache 2.0; verify each exact model/version's licence before redistribution.

The GPT-oss provider schemas use a different structured-output shape from Gemma's current schema. Provider adapters must translate capabilities and normalize usage and refusal responses rather than assume every endpoint is identically OpenAI-compatible. Valid JSON is only syntactic correctness. The application must still validate required fields, allowed labels, source spans, and truncation. Keep refusals, schema failures, missing context, empty findings, and successful negative results distinct.

### Retirement is a runtime concern

Cloudflare's [5 November 2026 announcement](https://developers.cloudflare.com/changelog/post/2026-11-05-planned-model-deprecations/) says `@cf/qwen/qwen3-30b-a3b-fp8` and other listed models stop responding on **20 November 2026**, returning HTTP **410** and code **5028**. Exclude those models from new defaults. The announcement recommends Gemma 4 and Qwen3.8 for smaller-model replacement needs.

Pin the chosen profile, retain the provider-reported identity/fingerprint where available, and check availability before a run. A retired or unavailable model should pause incomplete work while preserving committed progress. A replacement is a new run/profile; it must not silently mix models in one longitudinal result. Catalog failure is an unverified readiness state, not evidence of retirement. Hosted aliases may change without exposing an immutable weight revision, and the provenance record must say so.

### Local inference

Use native Ollama or an explicitly configured compatible local endpoint. Conservative entry candidates are Qwen3.5 4B Q4 and, after resource preflight, 9B Q4. The official [4B](https://huggingface.co/Qwen/Qwen3.5-4B) and [9B](https://huggingface.co/Qwen/Qwen3.5-9B) model cards are the sources for model identity and licence, not community filenames alone.

The explicit Ollama tag [`qwen3.5:9b-q4_K_M`](https://ollama.com/library/qwen3.5:9b-q4_K_M) was reported at approximately 6.6 GB packaged size, comprising roughly 5.6 GB quantized language weights and a 922 MB vision projector. Disk size is not runtime memory. KV cache, context, runtime overhead, extraction/OCR, operating system use, and concurrent models add to the requirement. A 16 GB machine may run some configurations but should not be promised a comfortable 9B workflow; 4B is the entry profile, while 24–32 GB is a more conservative target for 9B plus document processing.

Set context and concurrency deliberately: an 8,192-token context, one parallel inference request, and one resident model are conservative starting settings, not measured optimal values. Source chunk, adjacent context, rubric, instructions, and output allowance must fit together. A model's maximum advertised context is not an instruction to use it on a small laptop. Run extraction/OCR and model inference sequentially when memory is constrained.

Capture the full installed SHA256 from Ollama's [`GET /api/tags`](https://docs.ollama.com/api/tags.md). The short digest displayed by a website or a mutable tag is not an immutable model pin. Inspect [`/api/show`](https://docs.ollama.com/api/show.md) capabilities and [`/api/ps`](https://docs.ollama.com/api/ps.md) runtime memory/context; run a bounded structured-extraction pilot before estimating throughput.

Apple silicon should use native Ollama for Metal acceleration; do not promise Docker Desktop GPU passthrough on macOS. CPU inference remains a supported deployment possibility with measured pilot timing and a clear slower-processing state. There is no defensible fixed pages/hour promise before benchmarking on the actual hardware and corpus. A refused or unavailable local request must never trigger an undeclared cloud fallback.

### Smaller classifiers, embeddings, and GPU serving

A lexical baseline is inexpensive and explainable, but cannot reliably separate quoted attacks from endorsement. A small NLI model such as [`MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c`](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c) supplies a CPU/GPU zero-shot classification comparison. Its card discusses commercially friendly training-data choices; review the exact card, training-data terms, and revision rather than copy the foundation licence alone. Neither lexical nor NLI screening should drop passages from a supposedly complete analysis unless measured recall and disclosed coverage justify it.

Embeddings are useful for searching related passages, organizing themes, and finding similar evidence. Retrieval of a few passages cannot support a claim that every document was checked. Start with full-text search and complete bounded passage processing; introduce embeddings when they improve exploration. Benchmark deduplication separately so repeated press coverage does not inflate counts.

[vLLM's server documentation](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/) establishes a growth path for authenticated, separately operated GPU inference. It supports several OpenAI-compatible interfaces while explicitly documenting limitations. Model chat templates, tokenizer revisions, structured-output capabilities, and reasoning controls still need per-profile tests. Never expose its development controls or unauthenticated GPU endpoint to the public internet.

[Runpod Serverless](https://docs.runpod.io/serverless/overview) can scale workers to zero and offers queued and synchronous invocation patterns. Its documentation says cold starts and idle timeout intervals are billed, so "serverless" does not mean paying only for generated tokens. An always-warm GPU may suit sustained batches; intermittent workloads may favor managed token billing. [Hugging Face Inference Endpoints](https://huggingface.co/docs/inference-endpoints/en/guides/autoscaling) offer another dedicated-model path with scale-to-zero/cold-start tradeoffs. No comparative Runpod/Hugging Face throughput or total-cost experiment has yet established a cheapest GPU provider for this corpus.

## 4. Architecture and operating-cost rationale

### Reference deployment

The reference local topology is a browser frontend, FastAPI, a shared Python analysis package, SQLite/FTS5 metadata and job state, filesystem artifacts, and host Ollama. "Runs locally in your browser" describes the interface; reliable ingestion/OCR and substantial local models also require a local service/runtime. Browser-only WebGPU inference is a possible later mode, not the reference large-corpus promise.

The selected cloud direction is Cloudflare Static Assets and Worker API, D1 for metadata/job truth, R2 for artifacts, Queues for scheduling, and a bounded Python Container for extraction/analysis orchestration. Inference remains replaceable: Workers AI first for a low-operations cloud candidate, with an authenticated vLLM/Runpod/Hugging Face endpoint as an extension. A CPU container is not being proposed as a GPU host.

One Python package should implement chunking, rubrics, evidence checks, coverage accounting, and aggregation for both deployments. Avoid a second independently evolving semantic pipeline in the Worker. The storage/job transport can differ while the document, work, finding, and export contracts remain versioned.

The [Cloudflare Containers FAQ](https://developers.cloudflare.com/containers/faq/) documents ephemeral lifecycle behavior and currently says automatic stateless scaling is not provided. Begin with a named container and concurrency one, then grow to a deliberately bounded pool. The [Queues limits](https://developers.cloudflare.com/queues/platform/limits/) give a 15-minute consumer wall-clock limit. Use work slices bounded below that limit, for example ten minutes, with persisted page/chunk checkpoints. A consumer must commit the durable result/checkpoint before acknowledging a message; accepting a `202` from background Python work is not durable completion.

Keep immutable/content-addressed artifacts in R2, not container disk. Idempotent committed results and leases prevent duplicate dashboard counts after retries. They do not guarantee an interrupted network request cannot incur a duplicate provider charge. Track attempted calls, committed results, and usage separately. Store large text and source objects in artifacts rather than queue payloads.

### Alternatives and why they remain options

| Deployment | Appropriate use | Tradeoff to measure |
| --- | --- | --- |
| Local service + Ollama | Confidential/offline analysis and predictable local ownership | Hardware setup, memory, CPU/GPU speed, and model downloads. |
| Cloudflare application + managed inference | Small private deployment with one operational ecosystem and token-metered inference | Container lifecycle, bounded jobs, model catalog changes, and per-service costs. |
| Google Cloud Run + storage/database + managed or GPU inference | Teams already using GCP, regional controls, or an established GCP operations stack | Additional services, GPU quotas/regions, instance billing, and cold starts. |
| Runpod/vLLM GPU endpoint + application host | Sustained batches, exact open-weight revisions, or custom adapters | GPU utilization, billed cold/idle intervals, endpoint security, and model maintenance. |
| Hugging Face dedicated endpoint | Managed deployment of a chosen Hub model | Instance pricing, scale-to-zero behavior, model support, and endpoint readiness. |

Cloud Run is viable; it is not omitted because Python or GPUs cannot run there. Google's [GPU service documentation](https://docs.cloud.google.com/run/docs/configuring/services/gpu), [request-timeout documentation](https://docs.cloud.google.com/run/docs/configuring/request-timeout), and [GPU jobs documentation](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu) describe capabilities and billing constraints that need a deployment-specific comparison. Cloudflare is the selected initial integration to limit operational components; no research result proves it is globally cheapest for every workload.

### Cost arithmetic, not benchmark results

For one illustrative workload, assume 20 documents × 10 pages × 500 source tokens/page = 100,000 source tokens. Suppose repeated instructions/context make billed input three times source volume and output equals 20% of source volume: **300,000 input + 20,000 output tokens**. These are accounting assumptions, not measurements of the real showcase documents.

| Model | Illustrative 20-document token cost | 1,000 documents under the same assumptions |
| --- | ---: | ---: |
| Gemma 4 26B A4B | $0.036 | $1.80 |
| GPT-oss 20B | $0.066 | $3.30 |
| GPT-oss 120B | $0.120 | $6.00 |
| Llama 3.3 70B fast | $0.133 | $6.65 |
| Qwen3.8 27B | $0.199 | $9.95 |

These figures exclude infrastructure subscription, extraction/OCR, storage operations, retries, reasoning expansion, multiple lenses/passes, report generation, and larger real documents. For Gemma, another transparent example is 20M billed input + 2M billed output = **$2.60** at the listed rate. Neither calculation is a guaranteed documents-to-dollar conversion. Free daily allowances may reduce actual bills but should not be required for correctness.

A $25/month cloud target is an operating envelope, not a flat-price guarantee. Consult current [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), [Containers pricing](https://developers.cloudflare.com/containers/pricing/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), and [Queues pricing](https://developers.cloudflare.com/queues/platform/pricing/). Reserve infrastructure spend before inference budgets; estimate permitted request output and repeated context, meter actual usage, and pause at an explicit budget threshold.

## 5. Evaluation: what the 20-document showcase proves

The showcase must use **20 real public source documents**, English first, with source URLs, dates, institutional authors, rights evidence, source/extracted hashes, retrieval date, and any exclusions. It is an end-to-end product and reproducibility demonstration. Public availability is not a copyright licence, and a licence is not a gold annotation.

Include real policy/report/guidance texts with different lengths, dates, institutions, and perspectives. Government strategies, civil-rights guidance, parliamentary/government reports, and executive speech policies provide useful contrasts and repeated institutional authors. They do not represent a random sample of society or an unbiased picture of online hate. Authors of reports that quote prejudice must not be counted as endorsing those quotations.

The researched US source set includes the White House's [2023 antisemitism strategy](https://bidenwhitehouse.archives.gov/wp-content/uploads/2023/05/U.S.-National-Strategy-to-Counter-Antisemitism.pdf), [2024 Islamophobia/anti-Arab hate strategy](https://bidenwhitehouse.archives.gov/wp-content/uploads/2024/12/National-Strategy-Doc.pdf), [EO 14188](https://www.govinfo.gov/content/pkg/DCPD-202500196/html/DCPD-202500196.htm), [EO 14149](https://www.govinfo.gov/content/pkg/DCPD-202500116/html/DCPD-202500116.htm), EEOC religious-discrimination guidance, and DOJ reports. These illustrate different institutional frames; document dates and later caveats matter. For example, several EEOC pages preserve original issue dates but explicitly say the 2023 *Groff* ruling supersedes contrary accommodation guidance. Preserve those notices in snapshots and reports.

Rights evidence includes the [White House archive policy](https://bidenwhitehouse.archives.gov/copyright/), [DOJ policy](https://www.justice.gov/legalpolicies), and [GovInfo policy](https://www.govinfo.gov/about/policies). The latter explicitly warns that third-party content inside a government publication is not automatically public domain. Bundle only rights-cleared material, excluding protected seals/images and uncleared credited content. For UK sources, retain the relevant [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) attribution and respect its exclusions. Store deterministic exclusions and coverage; do not silently edit a source or label the edited object as a complete original PDF.

### External evaluation resources

| Resource | Verified usefulness | Limit |
| --- | --- | --- |
| [HateCheck](https://aclanthology.org/2021.acl-long.4/) and [data](https://github.com/paul-rottger/hatecheck-data) | CC BY 4.0; 3,728 cases, 29 functional tests; counterspeech, negation, slurs, spelling variation, and other targeted failure modes | Its seven target groups do **not** include Jewish people. It cannot validate antisemitism performance by itself and is not a long-document benchmark. |
| [Measuring Hate Speech](https://huggingface.co/datasets/ucberkeley-dlab/measuring-hate-speech/blob/main/README.md) | CC BY 4.0; 39,565 unique comments, 135,556 annotator rows; Jewish/Muslim targets and constituent measures, with annotator variation | Rows are not independent unique comments. Social-media sampling and measurement do not establish performance on reports or another lens. |
| [ISCA Antisemitism on Twitter](https://huggingface.co/datasets/ISCA-IUB/AntisemitismOnTwitter) | 6,941 English tweets, IHRA-based trained annotation; card describes natural-context review and 1,250 antisemitic labels | Annotators saw threads, links, images, and video that a text-only system may lack. Verify redistribution/platform terms separately; do not assume a Hub dataset listing grants all underlying content rights. |
| [Media Frames Corpus](https://github.com/dallascard/media_frames_corpus) | Framing annotations and span-location precedent | Full articles require Lexis-Nexis access/rights. Repository code licence does not license article redistribution. |

Synthetic minimal pairs are appropriate as **separate functional tests**, not as replacements for the real 20-document showcase. Use them for quotation versus endorsement, negation, historical quotation, neutral religious references, criticism of states/religions, ambiguity, OCR damage, and instructions embedded in source documents.

### Independent human validation protocol

Freeze source passages, document splits, rubrics, model settings, and prompts before a comparison. Separate exploratory tuning from untouched testing by document/source and, when possible, author. Closely related documents and duplicate passages should stay in the same split. Do not tune a model against the published showcase and then report showcase scores as an unbiased estimate.

At least two independent trained annotators should mark target, speaker, stance, criterion, evidence span, context sufficiency, and alternative interpretation. Use adjudication to produce an evaluation decision where appropriate while preserving original labels and disagreement. Domain expertise and a representative variety of interpretive perspectives matter more than pretending disagreement never exists. [Plank (2022)](https://aclanthology.org/2022.emnlp-main.731/) explains why human label variation can be meaningful information rather than noise.

Publish these outcomes separately:

- Extraction success, unsupported/failed pages, and passage coverage.
- Exact evidence-span validity, speaker attribution, and endorsement-versus-reporting errors.
- Per-criterion precision, recall, macro-F1, confusion matrices, and sample sizes.
- Hard-negative false positives; abstention rate; human review workload; model stability.
- Results by lens, target, document type, and supported language, with limitations from sparse strata.
- Human agreement and disagreement distributions; adjudication policy.
- Hardware/provider, model revision, latency, memory, tokens, retries, and actual cost.

Confidence intervals should account for passages clustered within documents; hundreds of passages from twenty related documents are not hundreds of independent documents. A small showcase cannot establish population-wide fairness, narrow error bounds, or general scientific validity. A model's self-reported confidence is not a calibrated probability. If calibration has not been evaluated on held-out labels, display uncertainty/review states rather than an authoritative percentage.

[dela Cruz, Hendrickx, and Larson (2025)](https://aclanthology.org/2025.gem-1.49/) found that extractive rationales can improve confidence estimates on a disaster-tweet task. This supports investigating extractive evidence, while its domain and findings do not establish DOXA's calibration. Similarly, a second model agreeing with the first is an additional signal, not independent human validation.

## 6. Growth path and research gates

1. **Reproducible first release:** source ingestion, metadata corrections, complete bounded analysis, explicit lens selection, evidence review, deterministic aggregation, and real PDF/HTML/XLSX exports. Keep demo data and live provider results visibly distinct. Never populate charts with invented measurements.
2. **Methodology refinement:** collect consented review corrections, extend real test corpora, analyze disagreements, and revise criteria with domain reviewers. Version rubric changes and rerun comparisons. More connectors do not by themselves improve validity.
3. **Classifier experiments:** compare lexical/NLI and small supervised classifiers against the same frozen test set. Introduce high-recall triage only if its measured omission rate and coverage accounting fit the use case.
4. **Fine-tuning:** use rights-cleared, independently labelled examples and held-out sources. [Hugging Face TRL SFT](https://huggingface.co/docs/trl/en/sft_trainer), [PEFT integration](https://huggingface.co/docs/trl/en/peft_integration), and [quantization guidance](https://huggingface.co/docs/peft/en/developer_guides/quantization) establish LoRA/quantized-adapter routes. Hardware, compatible architectures, licence, context handling, and evaluation still require experiments. Do not fine-tune on twenty documents and claim a validated specialist model.
5. **Scale and multilingual work:** move sustained inference to a measured GPU service, add source-specific connectors and permission handling, and extend languages only with extraction and interpretation tests in each language. A multilingual model card does not establish equal quality across languages or political contexts.

Fine-tuning should earn its complexity: require demonstrated gains in evidence validity, attribution, context handling, review workload, or cost at a fixed quality target. Distillation of model-generated labels is not independent expert annotation. Save training-data lineage, base revision, adapter revision, operationalized lens, and evaluation results. No model is an authoritative antisemitism, Islamophobia, or DOXA oracle.

## 7. Trust, privacy, and failure behavior

Documents are untrusted data. Their instructions must not become system instructions or gain tools. Analysis should not browse linked URLs, execute macros, invoke shell commands, or fetch remote HTML resources. Parse supported files with bounded resources; show damaged, encrypted, scanned, and unsupported states. Retain page/paragraph provenance without inventing page numbers for formats that lack them.

Local mode should keep source data local unless the analyst explicitly selects a cloud endpoint. Provider secrets belong in server configuration, not browser storage or downloads. Cloud deployment should begin as a private single-tenant application with access control; multi-tenant SaaS identity, authorization, retention, and billing are a distinct expansion.

Schema failure, refusal, timeout, cancellation, budget exhaustion, missing context, and zero matching findings are different outcomes. Checkpoint progress, retry bounded failures, retain incomplete coverage, and never fabricate completion. Pin prompts and lens versions; keep raw provider usage/error metadata while avoiding secret or unnecessary source-text logging.

Reports and dashboards should use the same persisted run and aggregates, including filters, sample sizes, and review status. Escape source text in HTML; write untrusted spreadsheet strings as literal cells to prevent formula execution. A downloadable report should include methodology, evidence links/locators, uncertainty, coverage, provenance, and the actual verification state.

The [NIST Generative AI Profile, AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) identifies confabulation and related risks. For this product, the concrete response is source validation, honest coverage, independent evaluation, explicit interpretive lenses, human review, and accurate statements about what has actually been run.
