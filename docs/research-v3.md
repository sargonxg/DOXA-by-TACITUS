# Research and verification for contextual DOXA v3

This note connects the v3 implementation to its research basis and the measurements actually obtained. It supplements the broader [research review](research.md), [methodology](methodology.md), [model profiles](models-v3.md), and [source collection audit](news-corpus.md). Sources and prices were checked during 16–17 November 2026. A model card, a functioning pipeline, exact citations, and correct interpretation are separate kinds of evidence.

## What is being measured

DOXA records **criterion assessments of source units under a named, versioned analytical lens**. It does not assign an author a universal prejudice score. The [IHRA working definition](https://holocaustremembrance.com/resources/working-definition-antisemitism), [Jerusalem Declaration](https://jerusalemdeclaration.org/), and [Nexus Document](https://nexusproject.us/nexus-resources/the-nexus-document/) remain distinct frameworks. Their political-expression boundaries and contextual conditions must survive operationalization. Framework agreement is not a substitute for an independently justified interpretation.

The [England anti-Muslim-hostility guidance](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility) and [Runnymede's structural-racism perspective](https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition) address different questions. A passage alone may not establish intention, unlawful conduct, or an institutional pattern. The Runnymede landing-page summary was read in the original research; its linked full briefing was not independently reviewed. DOXA's concise operational criteria are its own summaries, not an officially validated annotation scheme.

The [Media Frames Corpus](https://aclanthology.org/P15-2072/) provides a precedent for explicit framing annotation across policy issues. A frame or expressed stance is descriptive evidence; it need not be objectionable. Political correctness and language norms require a stated inquiry and perspective. Comparisons about missing voices, imbalance, or unequal treatment need a declared comparator. Source selection and topic prevalence can explain a pattern without proving a writer's beliefs or intentions.

The v3 contract keeps these dimensions separate:

- `Criterion.kind`: `indicator`, `boundary`, or `descriptive`, fixed by the registry.
- `Assessment.result`: `meets_criterion`, `does_not_meet`, `uncertain`, or `not_applicable`.
- Attributed speaker and speech role; an explicit proposition; speaker stance and document stance toward that proposition; an independent hypothetical flag.
- Source provenance, processing status, model profile, alternative reading, and subsequent human review.

Only an **indicator** meeting its criterion contributes to an indicator count. A political-expression boundary being met is not a prejudice flag. A descriptive frame being found is not a prejudice flag. Quoted hostility is not automatically the author's endorsement. Missing, malformed, failed, and pending decisions are not negative decisions. Historical v1 findings remain `legacy_unclassified`; they are not retrospectively converted into explicit v3 outcomes.

## Source-grounded, two-stage analysis

`doxa/contextual.py` builds immutable units from the full extracted document. Source-derived sentence identifiers carry document/version identity, source hash, block-relative Unicode offsets, and available page/paragraph/line locators. Adjacent sentences may span paragraphs; each segment retains its original block and offsets. Owned source characters are counted once, without inventing separator characters. The remote profile owns at most 4,000 characters per unit; the Ollama profile uses 1,200. Two neighboring sentences on each side supply bounded context. Whole-document unit construction precedes task slicing, preserving neighbors at task boundaries.

The model selects supplied identifiers. Application code extracts quotations from the persisted source and validates every reference, including at least one sentence belonging to the scored unit. Neighboring material can supplement that evidence. An exact citation proves where an observation came from; it does not prove that its explanation is correct. Text-only extraction also cannot establish what images, layout, omitted media, or an unavailable external source mean.

The frozen `contextual-decisions-3.2.1` pipeline has two stages:

1. **Classification:** return only a fixed-length decision vector for each requested unit, in an explicit criterion order. Codes are `0=does_not_meet`, `1=meets_criterion`, `2=uncertain`, `3=not_applicable`. All outcomes are explicit; an omitted entry is incomplete. Valid negative/not-applicable decisions can be persisted immediately.
2. **Grounded details:** provisional positive/uncertain pairs receive a separate re-evaluation with source-sentence identifiers, attribution, proposition, stance, explanation, and alternative. Requests contain at most three criteria remotely or two locally. Current output caps are 1,600 remote / 1,000 local tokens. Titles, explanations, and alternatives have character bounds. No provisional positive becomes an accepted indicator without valid grounded details.

The detail stage uses the configured model; it is not an independent human verification or independent-model consensus. If its explicit re-evaluation differs from the provisional classification, both decisions remain in a trace. Already accepted negative peers are excluded from retry. Independent retries preserve valid peers rather than discarding an entire source pack. A budget stop between stages leaves ungrounded positives pending. Durable reservations, cancellation guards, and accepted-outcome caches belong to the surrounding job API.

The unit builder is `source-units-3.1`, assessment contract `3.0`, criterion registry `3.0.0`, response schema `source-vectors-3.2`, and wire-budget profile `3.2.2`. Run profiles freeze rubric versions and analysis hashes. Resuming a run under a changed prompt is rejected. Managed model revision remains `unresolved` when the provider supplies no immutable weight revision; execution dates and returned model identifiers are retained.

## Model and runtime choices

The cloud baseline is [Workers AI Gemma 4 26B A4B IT](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/). Its [official model card](https://huggingface.co/google/gemma-4-26B-A4B-it) and [Gemma 4 licence](https://ai.google.dev/gemma/docs/gemma_4_license) identify Apache 2.0. Earlier Gemma-generation terms must not be assumed to apply. The advertised context is larger than DOXA's deliberately bounded 32,768-token application profile. The provider's published input schema supports JSON Schema output and an explicit thinking control; the benchmark requests thinking disabled. Generic OpenAI-compatible endpoints are transport adapters, not proof that an exact backend supports every schema keyword or thinking parameter.

The local fallback is [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), whose official repository declares Apache 2.0, served through Ollama with the actual installed digest/quantization recorded. [Ollama structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs) and [vLLM structured-output documentation](https://docs.vllm.ai/en/latest/features/structured_outputs/) establish supported interfaces, while backend/model combinations still require runtime checks. A 16GB CPU-only machine is a supported design target, not a completed 5,000-document local throughput measurement. The separately documented [CPU embedding/NLI profiles](models-v3.md) support retrieval and rubric exploration; their uncalibrated scores are not prejudice probabilities or substitutes for contextual assessments.

The checked Gemma token rates were **$0.10 per million input tokens and $0.30 per million output tokens**. Displayed costs multiply reported tokens by configured rates. They are estimates, not invoices, and exclude hosting, storage, free allowances, extraction, and operator time. Cloud inference is explicit: source import and the workspace stay local by default, while selected source text goes to the configured remote provider with transfer consent. A local fallback changes inference location, not the evidentiary standard. Model availability is checked before use; retired models are not silently replaced.

On 17 November both phase schemas passed JSON Schema Draft 2020-12 meta-schema validation. Every object has all its properties required and forbids additional properties; identifier enums and exact vector lengths are bounded. A valid classifier sample passed; short vectors, invalid unit IDs, and an extra classifier `details` property failed. These checks establish schema validity, not universal constrained-decoder portability. The final classifier and detail path were actually exercised against the hosted Gemma runtime. Other deployments need an exact-model capability smoke test.

## Paired operational pilot: what improved

The [sanitized machine-readable trace](../benchmarks/v3-pilot-comparison.json) compares two adjacent midpoint units from the real [U.S. National Strategy to Counter Antisemitism](../corpus/documents/us01.json), source `us01`. They contain **7,701 scored Unicode characters** and request **38 decisions**: 19 criteria from six lenses for each of two units. This is a small source-selected engineering pilot, not a complete-document or news-quality benchmark. The raw PDF checksum and extracted-text checksum are retained; public artifacts contain metrics/provenance, not source passages or credentials.

| Operational measure | One-pass 3.1 | Two-stage 3.2.1 |
| --- | ---: | ---: |
| Accepted explicit decisions | 32 / 38 | 38 / 38 |
| Fully accepted units | 1 / 2 | 2 / 2 |
| Fully covered source characters | 3,856 / 7,701 | 7,701 / 7,701 |
| Requests, including recovery | 10 | 6 |
| Reported input tokens | 40,919 | 25,206 |
| Reported output tokens | 16,011 | 3,740 |
| Estimated token cost | $0.0088952 | $0.0036426 |
| Sum of request durations | 253.80 s | 65.46 s |
| Length-terminated requests | 3 | 1 |
| Final unresolved decisions | 6 | 0 |

Both initial one-pass responses hit their output cap; smaller criterion retries recovered 32 decisions. In the final profile both classification calls stopped normally at 98 and 96 output tokens. One three-criterion detail call still hit its cap; its independent retry recovered all three decisions. Output tokens fell **76.64%** and estimated token cost **59.05%** in this paired observation. Summed request time fell **74.21%**. Timing is the sum of rounded per-request durations, not end-to-end elapsed time or a service-level promise.

There are **no independent reference labels** for these units. Noteworthy outcomes changed from eight to seven; neither count is a known truth. Prompt, schema, decomposition, and caps changed together, so this is not an isolated causal experiment. It demonstrates successful structured recovery and lower observed token use in one case. It establishes neither interpretation accuracy, calibration, representative news performance, a final 99% acceptance rate, nor the eventual cost of the complete 5,000-article run.

### Narrow empty-array diagnostic

An intermediate classifier required `details: []`, constrained by `maxItems=0`. In a captured response the 19-code vector completed, then an opened `details` array was followed by repeated whitespace until the 396-token cap. The returned content was 588 characters, including 447 trailing whitespace characters, and was incomplete JSON. The message had no reasoning-content field. This observation concerns that request/schema and hosted runtime; it is not evidence that Gemma generally fails, that every Cloudflare endpoint fails, or that empty arrays are universally unsupported.

The targeted workaround removes `details` entirely from classification properties/required and removes its unused definition. Detail-phase requests retain their own schema. No parser repair invents missing decisions. Separate sandbox connection hangs were diagnosed as local networking/tooling behavior: the same health request succeeded outside that restriction. Those waits are excluded from inference timings.

## News provenance and rights

The separate [news collection](news-corpus.md) contains **5,000 real Global Voices articles**, 2020–2025, **5,251,119 whitespace-counted words**, **778 distinct credited author names**, and **585 translated articles**. File/provenance and independent source-character audits both passed **5,000/5,000**. The frozen split is 4,000 development / 1,000 heldout article groups. This is one publisher's collection, not a representative sample of all news or of political perspectives. Selection did not use predicted bias labels. A source-integrity audit and completed inference coverage are separate results.

The [Global Voices republishing policy](https://globalvoices.org/about/global-voices-attribution-policy/) was read alongside article licence links to [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). Per-article receipts retain bylines, translator roles, source URLs, licence statements, extraction changes, and hashes. Specific restrictions override a general footer. Unresolved partner republications are excluded; external images, captions, embeds, scripts, and audiovisual material are omitted. Ordinary attributed quotations retain their original rights. This is a documented publisher-licence/source check, not exhaustive legal clearance. Distinct source aliases preserve credited authors even where identical content shares storage or inference.

The twenty institutional case studies have separate source-specific rights. The pilot's federal-authored U.S. prose is treated as public domain under 17 USC 105; third-party material retains its rights, and images/seals/logos are excluded. Source links, rights notices, exclusions, and exact text hashes remain available to exports and saved snapshots. Restoring a packaged result requires matching source hashes and validated anchors; historic usage is labelled as saved-run provenance rather than newly billed work.

## Reviewed learning, trajectories, and evaluation limits

The implemented learning mechanism is **versioned, human-approved example memory**, not autonomous weight training. Model proposals and human adjudication remain distinct. Original outcomes survive structured review events. Approved examples enter immutable snapshots and explicit candidate/baseline comparisons, activation, and rollback. Retrieval is bounded to four examples and 4,800 UTF-8 JSON bytes, retaining original evidence rather than generated paraphrases. Same-document, content-hash/story-group, and heldout exclusions reduce leakage. Snapshot IDs and example IDs are recorded in run profiles. Agent agreement does not establish truth or authorize its own promotion.

Publication-time discourse and assessment-time review history are different axes. A changed model/rubric/memory run must not be connected as if an author's underlying discourse changed. Trajectories carry criterion/kind, attribution/role, source scope, method versions, time-bucket date precision, and eligible/covered/reviewed denominators. Missing or incomplete periods are gaps, not zero prejudice. Spikes describe changes in a declared rate under coverage and baseline gates; they do not establish cause. Quoted and reported expression remain separable from author assertions. Every point must lead back to scoped evidence.

[HateCheck](https://aclanthology.org/2021.acl-long.4/) motivates functional tests for negation, counter-speech, quotations, targets, and other failure modes. Its original seven target groups do not include Jewish people, and its short cases cannot validate long-document antisemitism analysis. [Plank (2022)](https://aclanthology.org/2022.emnlp-main.731/) explains why human label variation can be meaningful information rather than annotation noise. At least two trained independent reviewers should label source spans, target/speaker, proposition stance, criterion outcome, context sufficiency, and competing readings; preserve disagreement and adjudication separately.

Current functional checks cover exact anchors, cross-paragraph units, explicit outcomes, malformed/length-limited output, independent recovery, budget stops, caches, and leakage exclusions. They establish software behavior under those cases. They do not provide F1, fairness, calibration, or independent interpretation accuracy. Future quality evaluation must freeze source/story/time-separated splits, rubrics, prompts, model revisions, and memory snapshots before measurement; report precision/recall, boundary false positives, abstention, attribution errors, coverage, review effort, cost, and uncertainty by relevant slice.

Fine-tuning is a later experiment after sufficient rights-cleared adjudicated data. [Hugging Face PEFT quantization guidance](https://huggingface.co/docs/peft/en/developer_guides/quantization) documents possible LoRA/quantized-adapter workflows; it does not establish their accuracy or hardware feasibility for this task. Weight updates require separate frozen evaluation, explicit promotion, provenance, and rollback. Neither the twenty institutional sources nor the unreviewed news output is a ready-made gold training set.
