# DOXA model and system card

**Version:** application 0.4.0; each frozen analytical run records its own methodology, rubric snapshots, model/provider identity and source selection. This application is an orchestration and evidence system, not a newly trained foundation model.

## Intended use

Research, civil-society monitoring, newsroom/policy analysis and teaching with declared frameworks and inspectable evidence. Use source-specific review to distinguish an author's position from quotation, reported allegations, counterspeech, hypothetical examples and descriptive framing. Rates describe a selected imported corpus.

The engine emits typed criterion outcomes, attribution and stance fields, exact source references and explicit unresolved work. Frameworks retain distinct definitions and boundary conditions. Political framing is descriptive; an observed frame alone establishes neither prejudice nor factual error. No demographic-persona simulation or private political-identity inference is needed.

## Model roles and provenance

| Component | Role | Recorded limitations |
| --- | --- | --- |
| Qwen3.5 / Ollama | Optional local contextual generation | CPU throughput and strict structured-output reliability vary; model digest, quantization and context matter. |
| Cloudflare Gemma | Recorded managed primary inference | Provider-managed revision limits; consent and application-accounted budget; vendor invoice is separate. |
| GPT-OSS 120B | Independent-model comparison | Paired disagreement is a review aid, not human adjudication. |
| MiniLM-L6-v2 | Optional CPU retrieval | Similarity is not stance or prejudice. |
| DeBERTa NLI | Optional independent hypothesis scores | Uncalibrated entailment scores, not probabilities of prejudice. |

See [installation, pinned artifacts and actual model benchmarks](models-v3.md), the [bounded local study and limitations](local-study-v04.md), and the [technical annex](v04-technical-annex.md). No model weights are downloaded during normal installation or software tests. DOXA has not fine-tuned these models.

## Evaluation status

The 5,000-source cloud run retained 204,342 of 204,421 explicit decisions and 4,927 complete source checks; 79 decisions remain unresolved. Exact-source audits measure anchoring and integrity. They do not measure semantic validity. Independent-model comparisons expose literal differences but do not establish which interpretation is correct.

The [independent evaluation scaffold](evaluation-v04.md) contains 200 cases with no fabricated human labels. Held-out cases require fresh, model-blind independent review before metrics are unlocked. The 120 functional fixtures are an authored protocol; their expected behaviors require rubric review and actual model execution. Interpretation accuracy, calibration and subgroup performance remain unmeasured.

## Review and adaptation

Review records are appended separately from original model output. Approved examples can enter immutable memory snapshots; a candidate requires paired evaluation and explicit activation or rollback. Optional semantic overlays retain actor/version labels. The current system does not train weights or autonomously promote itself.

## Constraints

English-first methods and CPU profiles need separate multilingual validation. Missing context, extraction/OCR errors, ambiguous attribution, framework differences, source-selection bias and provider model changes can affect results. An omission or double-standard claim needs a declared relevant comparator. Keep source rights, reviewer authorship, incomplete coverage and plausible alternative readings with any downstream conclusion.

Deploy private workspaces with the authentication and data-transfer controls described in [deployment](deployment.md). Default attempt records contain metadata; historical visible prompts are not reconstructed as if they were actual captured requests.
