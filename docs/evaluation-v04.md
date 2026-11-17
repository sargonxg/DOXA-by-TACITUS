# Reviewed evaluation, provenance and bounded local study

DOXA v0.4 adds an evaluation workflow. It does **not** claim newly measured
interpretation accuracy. The released model outputs remain immutable; new
annotations and reference reviews have separate origins and versions.

## What an explanation establishes

An exact source citation establishes where the text came from. A short model
explanation describes its interpretation; it does not expose or prove the
model's internal reasoning. An independent reviewer must still assess whether
the evidence supports the criterion and whether the speaker, proposition and
document stance are represented correctly. Compact negative decisions may have
no model-written rationale. Their software-attached source-unit anchors must
not be described as a detailed explanation supplied by the model.

The reader-facing layer should show the passage, context, criterion, attributed
voice, explicit proposition/stance, competing reading and review status. Its
technical annex can show source hashes and IDs, method/runtime versions,
selected exemplars and observable classify/detail attempts. No hidden
chain-of-thought or separate provider reasoning content is captured.

## Method, execution and sessions

`doxa.method_config` stores deeply immutable analytical settings separately from
execution policy. The method includes frozen source-metadata identity, requested
model/revision, rubric/unit/context settings and approved memory. Budgets,
concurrency, deadlines and timing history belong to execution policy. Unknown
public configuration settings are conservatively treated as analytical.

New v0.4 contextual runs explicitly opt into the additional method fingerprint
for cache identity. Historical v0.3 hashes, outputs and statuses are not migrated.
The same requested managed-cloud model may have unresolved weights; a method
hash cannot manufacture an unavailable model revision or establish semantic
equivalence across dates. Provider-reported fingerprints remain separate.

Paired learning comparisons exclude execution changes and the intended memory
change. Structural compatibility, complete coverage and model agreement do not
establish quality improvement. Candidate reports explicitly retain
`quality_status: reference_pending` until a separate real-reference evaluation.
Human activation remains explicit and is not an automatic quality endorsement.

## Request capture

New attempts produce metadata-only records: request/schema hashes, source/unit
and criterion IDs, phase, attempt, model profile, output budget, observable
timings, usage and validation outcome. This records actual execution rather than
reconstructing what a historical request probably contained. Older runs without
contemporaneous journals are explicitly `not_recorded`.

Visible prompt/response capture is opt-in and requires an explicit private local
sink (`PrivateJournal` or an operator-supplied equivalent). Direct Python callers
set `request_journal_mode="visible_io"` and `_record_private_io=sink`; without
the sink, analysis refuses that capture mode. The default API records metadata
only. API operators can explicitly set `request_journal_mode: "visible_io"` for
a contextual run on a native local Store after enabling
`DOXA_ENABLE_PRIVATE_JOURNAL=true` on the server. Cloud persistence rejects this
local capture mode. The per-run quota defaults to 64 MiB and is configurable with
`DOXA_PRIVATE_JOURNAL_MAX_BYTES`; a full quota raises an explicit error rather
than silently claiming a response was captured. The local sink uses directory
mode 0700 and file mode 0600, accepts only
visible messages/schema/content and metadata, and does not capture HTTP headers,
credentials or separate reasoning fields. Operators must choose retention and
deletion appropriate to their source material. Source text remains untrusted
data even when it contains instructions.

## Prepared 200-case review pack

Run, without network or model calls:

```sh
.venv/bin/python -B scripts/build_evaluation_v04.py
```

The generator checks frozen news-package/source hashes and prepares 100
development diagnostic cases and 100 locked heldout cases using the existing
source splits. Story groups do not overlap between splits. Selection balances
criterion/outcome buckets and is **not** a representative population sample.
Known 20-news diagnostic stories are excluded from the new heldout selection.
The exposure ledger explicitly records incomplete historical exposure knowledge.

The pack contains no reference labels. Reviewer A/B forms start blank, omit model
outcomes and explanations, and include the complete scored source unit and its
neighbouring sentences. The original parent model outputs are already public:
"locked" means excluded from development and memory, not cryptographically
secret. Independent first-pass reviewers must avoid parent outputs and each
other's annotations. Source metadata in this offline pack comes from the pinned
canonical sidecars, not a reconstructed contemporaneous request capture.
Each case also freezes the full rubric, context rules, version and content hash.
The bundled news cases verify the rubric snapshots embedded in the original run
configuration against their recorded snapshot hash. If a different imported run
has no embedded rubric, the review instructions explicitly identify a later
versioned-registry capture and do not claim verified historical content equality.

For a private imported workspace, create a scoped evaluation using:

```text
POST /api/v3/projects/{project_id}/evaluations
{"run_id":"...","development_count":100,"heldout_count":100,"seed":20261117}
GET /api/v3/projects/{project_id}/evaluations/{evaluation_id}/form?split=development
GET /api/v3/projects/{project_id}/evaluations/{evaluation_id}/form?split=heldout
```

Use that workspace's exported `review_template.rows` when importing its labels;
offline example case IDs are tied to their frozen parent scope and are not
silently remapped to another workspace. The API derives reviewer identity from
trusted operator configuration, ignoring browser claims of reviewer authority.
Distinct declared identities are necessary but do not automatically prove
real-world independence. A private local self-declared operator is not an
independently verified annotator.

Each reviewer independently labels result, attribution, speaker/document stance,
evidence adequacy, criterion application, context sufficiency and acceptable
alternative outcomes, with a note. They then declare that their first pass was
independent and model-blinded. An adjudicator records a separate decision only
after two distinct initial reviews. Initial reviews are retained and immutable;
conflicting adjudications require a new versioned evaluation.

```text
POST .../evaluations/{evaluation_id}/reviews
{"rows":[{"case_id":"...","case_sha256":"...","source_hash":"...",
  "independent_blinded_review":true,"labels":{...real human labels...}}]}
POST .../evaluations/{evaluation_id}/adjudications
{"case_id":"...","labels":{...real adjudication...},"note":"..."}
GET .../evaluations/{evaluation_id}/report
```

Hash/scope mismatches are rejected. Heldout agreement metrics remain locked until
every heldout case is adjudicated. The report distinguishes review progress and
selected-case diagnostic agreement from population accuracy. Keep initial
disagreement and legitimate framework-dependent alternatives visible. A small
200-case scaffold cannot support precise claims for every criterion or group.
Do not tune prompts repeatedly against the locked labels.

## Functional expectations: 24 bases, five variants

The prepared casebook has 120 derived fixtures, not 120 observed model successes.
It uses only development cases and existing lenses. Each base has an original
control, irrelevant-formatting candidate, reported/condemned framing,
definition/example context and a source-instruction variant. Expectations are
AI-authored protocol suggestions requiring human rubric review; all execution
results start `not_run` and all expected outcome labels remain unset.

Quoted hostility can satisfy an utterance-level indicator while the document
rejects it. A definition/example need not enact the pattern it describes. Adding
untrusted instructions must not execute those instructions, but can itself add
discourse content that affects a legitimate criterion. Identity substitutions
are not assumed invariant across target-specific frameworks. Derived fixtures
have their own hashes and never reuse original offsets as if the text were
unchanged.

## Optional semantic overlays

Evidence function, proposition type, attribution chain and missing-context notes
default to `NOT_ASSESSED`. They are optional editorial annotations with actor,
kind, version, parent-assessment hash and lineage. New annotations append;
original model payloads are unchanged. Agent annotations are identified as agent
annotations. These fields do not infer private beliefs or automatically revise
criterion outcomes.

## Bounded local feasibility comparison

The explicit command is:

```sh
.venv/bin/python -B scripts/study_local_v04.py --minutes 30
```

It never downloads weights. Both Q4 packages must already be installed with the
manifest digests pinned in the script. The generic Ollama `qwen3.5:2b` tag was
observed to resolve to Q8_0; the study instead requires
`qwen3.5:2b-q4_K_M`. The download receipt records the approved disk checks and
removal of that unused new Q8 download; existing 4B/CPU artifacts were retained.

The two profiles examine the same six shortest **whole articles** from the
existing 20-news sample, with 1,200-character units, two neighbouring sentences,
the same six lenses, four requested CPU threads, one request at a time and an
8,192-token context. They run sequentially, with an amended 30-minute operating cap per profile
(within the original at-most-90-minute envelope)
and a sampled driver-plus-Ollama RSS stop at 12 GiB. Only one model is loaded at
a time. These are requested runtime policies, not reserved exclusive CPU cores;
other development workloads can affect elapsed times.
The amendment was recorded before the 4B phase. Shared-environment observations
began after the 2B phase started; the preceding load is explicitly unrecorded.

Receipts preserve every selected article/unit denominator, accepted/partial/
unprocessed work, validation errors, actual Ollama digest/quantization,
upstream-model reference revision, timings, sampled combined RSS and GPU-use
observations. The upstream HF reference does not prove conversion parity with
an Ollama quantized package. A timeout, validation failure or RSS stop remains
visible. This is feasibility evidence, not an accuracy comparison, and never
automatically promotes a model. There are no paid inference calls.

## Research basis and later gates

- [ERASER](https://aclanthology.org/2020.acl-main.408/) and
  [faithful interpretability](https://aclanthology.org/2020.acl-main.386/): source
  alignment, explanation plausibility and causal faithfulness are different.
- [Unfaithful explanations](https://arxiv.org/abs/2305.04388): explanation text is
  not proof of the actual causal decision process.
- [CheckList](https://aclanthology.org/2020.acl-main.442/) and
  [HateCheck](https://aclanthology.org/2021.acl-long.4/): use functionality and
  contrast tests, with domain-specific limits.
- [BIPIA](https://arxiv.org/abs/2312.14197): untrusted-source prompt injection
  needs real-model testing; schema validity is not semantic resistance.
- [Selective prediction](https://proceedings.mlr.press/v97/geifman19a.html) and
  [calibration](https://proceedings.mlr.press/v70/guo17a.html): measure risk versus
  accepted coverage only after independent reference labels exist.
- [Human label variation](https://aclanthology.org/2022.emnlp-main.731/): preserve
  legitimate disagreement rather than manufacturing unanimous gold.

Further paid experiments require a verified remaining shared monthly allowance.
Start with a paired 20-source experiment bounded to at most 80 frozen units and
the smaller of $1.50 and that remaining allowance, changing one variable at a
time. No new full-5,000-source model run is part of this release. Classifiers,
SetFit training, calibrated thresholds, semantic memory and context-skipping
cascades require reviewed data and heldout evaluation before quality claims.
