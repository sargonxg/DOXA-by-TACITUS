# Refinement and evaluation roadmap

[v0.6](refinement-v06.md) now implements local diagnostics, resource profiles, extraction/context refinements, matched-cohort and evaluation interfaces, recoverable activities and dedicated-pilot tooling. The table below remains a research and qualification agenda: shipping the interfaces does not complete human annotation or establish model quality.

v0.4 implements the evidence and publication workflow. The next improvements should be decided by recorded failures and independent evaluation. The gates below are proposals, not completed experiments or promised accuracy.

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| P0 · interpretation | Two model-blind reviewers and adjudicator complete the 200-case protocol | Signed scope/exposure records; locked reference labels; missingness and agreement reported; metrics unlocked only for eligible cases. |
| P0 · functional behavior | Review and execute the 120 transformation fixtures | Quote/counterspeech/negation/hypothetical/attribution behavior measured by rubric; failures retained by model, prompt and source version. |
| P0 · extraction | A licensed scanned/mixed PDF and multilingual extraction reference set | Per-page coverage, exact text differences, OCR errors, tables/headings and excluded material checked independently. |
| P1 · local inference | Repeat the bounded study on a quiet 16 GB machine and a declared GPU profile | Fixed selected sources/rubrics; explicit quantization; strict-output failures; full-unit/document denominators; wall time, RSS and energy/cost if measurable. |
| P1 · comparator research | Paired source cohorts for asymmetry/omission questions | Relevance rationale, comparable opportunities, source selection and exact paired evidence; alternative explanations retained. |
| P1 · trajectories | Prospectively evaluate event/spike review candidates | Publication-time denominators, incomplete-source rates, date uncertainty, minimum exposure and multiplicity controls; human relevance review. No causal attribution from a plot alone. |
| P1 · repeated reporting | Scheduled frozen-cohort checks and reviewed source updates | Idempotent connector imports, immutable prior editions, consent and spend caps, extraction-change alerts and revision-consistent reports. |
| P1 · cloud operations | Production load and failure-injection study | Queue redelivery, cold starts, D1 limits, R2 failures, cancellation latency, auth boundaries, storage retention and full report resource measurements in the target deployment. |
| P2 · model adaptation | Compare reviewed example memory, prompt changes and task-specific fine-tuning | Separate candidate method, approved/licensed training data, held-out story groups, independent labels, paired results and explicit rollback; model size alone does not justify promotion. |
| P2 · multilingual scope | Separate language/translation validation | Native-language reviewers, source/translation provenance and aligned-case disagreements; no English-only performance extrapolation. |
| P2 · suggestions | Optional evidence-linked suggested questions or revisions | User explicitly requests suggestions; original wording preserved; every suggestion tied to an actual passage, declared goal and alternative reading. Detection and quantification remain independently usable. |

## Streamline before expanding

Measure time to import, find an exact passage, identify its speaker, understand an incomplete result, curate a mixed-outcome casebook and verify a downloaded report. Fix repeated confusion before adding another dashboard. Keep keyboard and small-screen workflows in the same acceptance run as desktop behavior.

Use a small declared cohort for a new hypothesis, then expand only after extraction, structured-output reliability, interpretation review and cost are understood. Retain all attempted sources and failure reasons. A narrow successful example should remain a narrow claim.

## Failure-injection checklist

The automated suite exercises interrupted imports, conflicting part writers, database transactions, future-schema refusal, changed source versions, queue redelivery, expired leases, cancellation checkpoints, artifact upload failures, source revisions, range downloads and evaluator locks. Extend the tests with a minimal reproducer when an actual new failure appears. Production infrastructure and interpretation-quality studies require their own measured evidence; a local mock cannot substitute for either.
