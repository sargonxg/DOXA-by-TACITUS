# Local models: what the bounded v0.4 study actually showed

Both small generative profiles produced useful inspectable results, and both
remained incomplete. **This study does not select a winner or recommend a model
promotion.** It measures a particular CPU workload, validation and retained
coverage; interpretation accuracy has not been measured against independent
human reference labels.

## Scope and operating conditions

The same six **whole articles** were selected as the six shortest articles in
the existing frozen 20-news sample: 2,735 words in total, divided into 17 exact
source units. This purposive sample favours a manageable local trial. It is not
representative of the 5,000-source corpus, long documents or prejudice cases.

Both profiles used the same six existing lenses and 19 criteria, 1,200-character
source units, two neighbouring sentences, an 8,192-token context, four requested
CPU threads and one request at a time. Retries were disabled equally. The models
ran sequentially, with a sampled driver-plus-Ollama RSS stop at 12 GiB.

At 16:37:42 UTC the operator amended the original at-most-90-minute envelope to
a **30-minute operating cap for each profile**, before the 4B phase began. The
2B profile finished attempting the selected inputs before that cap. The 4B
profile was stopped at its cap using an atomic completed-unit checkpoint. Its
in-flight unit observations were not retained; they are not invented as completed
requests, negative outcomes or processed coverage. The model was then unloaded;
the runtime reported no resident models.

The measured runtime was Ollama 0.40.16 on an AMD EPYC 9V74 host, with a shared
cgroup quota of four CPU equivalents and a 16 GiB memory limit. Other development,
test and report workloads shared that cgroup. Environment logs record load,
throttling and shared memory; the initial 2B period before monitoring began is
explicitly unrecorded. These conditions **do not establish a clean causal
comparison of model speed** or predict a particular laptop's performance.

## Actual endpoints

An accepted unit has a valid retained decision for **all 19 requested criteria**.
A partial unit can still contain valid retained peer decisions. An article is
complete only when every selected unit is accepted.

| Observed result | Qwen3.5 2B Q4 | Qwen3.5 4B Q4 |
|---|---:|---:|
| Selected whole articles | 6 | 6 |
| Completely accepted articles | 1 | 1 |
| Selected source units | 17 | 17 |
| Completely accepted units | 12 | 3 |
| Partial units with retained peers | 5 | 3 |
| Unprocessed or unretained units | 0 | 11 |
| Retained decisions, out of 323 requested | 287 | 110 |
| Measured profile interval | 25 min 52.759 s | 30 min 0.185 s |
| Sampled peak driver + Ollama RSS | 6.081 GiB | 6.608 GiB |
| Reported GPU allocation at endpoint | 0 bytes | 0 bytes |
| Retained request inputs reproduced by hash | 56 | 16 |
| Exact sentence references checked | 2,140 | 682 |

The RSS figures cover different amounts of processed work and are sampled peaks,
not guaranteed memory requirements. They do not replace the earlier, larger
local trial's observed 9.238 GiB combined peak. Keep headroom and serialize heavy
local phases on a 16 GB computer.

**Completion is not accuracy.** Negative and non-applicable outcomes can avoid
the second evidence-generation stage. A model can therefore complete more units
while missing substantive indicators, misapplying applicability or offering a
different interpretation. No independent labels determine which model's answers
are better here.

The retained software-validation errors were:

| Error occurrences | 2B | 4B |
|---|---:|---:|
| Missing required evidence | 31 | 3 |
| Invalid source/evidence contract | 3 | 1 |
| Output limit | 2 | 0 |

These are validation events in retained unit results, not semantic error rates.
For 4B, request and token counts exclude unretained work in the interrupted unit.
There were no paid inference requests; local electricity and hardware costs are
not priced as zero.

## The models disagreed strongly

There are 95 jointly retained unit–criterion cells. Their outcome categories
differentiate in **89 cells**. Of those changes, 68 are between
`does_not_meet` and `not_applicable`; these are distinct substantive outcomes,
not interchangeable negatives. Other changes involve criterion satisfaction or
uncertainty. The criteria include indicators, boundaries and descriptive frames,
so a satisfied criterion must not automatically be counted as prejudice.

This pairing is conditioned on both models retaining a valid result and on the
4B stopping point. It is not a representative sample, an accuracy estimate or a
population disagreement rate. Attribution-field differences also need care:
compact negative rows can contain software defaults rather than a separately
elicited speaker analysis. The strong observed disagreement supports **human
review and explicit profile separation**, rather than pooling the outputs.

## The six source articles

Numbers below are completely accepted units / selected units. A dash denotes no
settled unit result retained before the cutoff, not a negative interpretation.

| Licensed source | Words | 2B | 4B |
|---|---:|---:|---:|
| [gv-740730](../corpus/news/metadata/gv-740730.json) — Controversy over an explosion in the Caspian sea | 255 | 1/2, partial | 2/2, complete |
| [gv-706526](../corpus/news/metadata/gv-706526.json) — COVID-19 in the Caribbean: ‘Looking out, not going out’ | 313 | 1/2, partial | 0/2, partial peers retained |
| [gv-755578](../corpus/news/metadata/gv-755578.json) — Nigerian citizen is prevented from entering Mozambique, reigniting debate on mobility in Africa | 326 | 1/2, partial | 1/2, partial |
| [gv-770760](../corpus/news/metadata/gv-770760.json) — LIVE on September 22: Techno-authoritarianism in India | 508 | 2/3, partial | —, interrupted in flight |
| [gv-791816](../corpus/news/metadata/gv-791816.json) — China rolls out new patriotic education law to consolidate its single-party regime | 640 | 4/4, complete | — |
| [gv-814580](../corpus/news/metadata/gv-814580.json) — New Caledonia cries, ‘Everything is negotiable, except independence’ | 693 | 3/4, partial | — |

The frozen results retain source IDs, content hashes, exact anchors, errors,
model profiles and visible attempt metadata. They contain no private
chain-of-thought. Checking a source anchor establishes integrity, not that the
passage supports the model's interpretation.

## Exact model identities and reproduction

Both runtime packages reported `Q4_K_M`. The manifest digests are the actual
Ollama runtime pins:

```text
qwen3.5:2b-q4_K_M
1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7

qwen3.5:4b
d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04
```

The upstream references, whose model cards declare Apache-2.0, were:

- [Qwen/Qwen3.5-2B, revision 15852e8c16360a2fea060d615a32b45270f8a8fc](https://huggingface.co/Qwen/Qwen3.5-2B/tree/15852e8c16360a2fea060d615a32b45270f8a8fc).
- [Qwen/Qwen3.5-4B, revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a).

These upstream references do not prove conversion parity with the quantized
Ollama packages. The generic `qwen3.5:2b` tag resolved to Q8_0 during setup; the
study used the explicit Q4 tag instead. The unused new Q8 package was removed
before inference. After the 2B phase, its new Q4 package was also removed to free
disk for report proofs; the completed result artifacts and existing 4B/CPU model
weights were retained. Cleanup and freed bytes are recorded in the receipt.

Explicit installation commands are:

```sh
ollama pull qwen3.5:2b-q4_K_M
ollama pull qwen3.5:4b
```

Check free disk before downloading: require twice the official package size plus
at least 1 GB of headroom. The observed 2B Q4 package was 1,945,781,181 bytes,
including its projector. Verify the full installed digest through `/api/tags`;
the study refuses changed runtime pins. Installing a model does not force CPU
execution on every platform. Use an appropriate CPU-only runtime and check GPU
allocation; this study used the provided Linux CPU runtime with one loaded model
and one parallel request.

Once the frozen corpus and both pinned packages are available, reproduce into a
separate output directory:

```sh
.venv/bin/python -B scripts/study_local_v04.py --minutes 30 \
  --output data/v04-cpu-reproduction/summary.json
```

The command never downloads weights or silently substitutes a model. It keeps
all six-source/17-unit denominators on failure or interruption. The original
retained study can be checked without inference:

```sh
.venv/bin/python -B scripts/audit_local_study_v04.py
gzip -t benchmarks/v04-cpu*.gz
```

## Inspectable evidence and practical use

- [Final summary](../benchmarks/v04-cpu-study.json), [offline input/anchor audit](../benchmarks/v04-cpu-study-audit.json) and [30-minute amendment](../benchmarks/v04-cpu-study-policy-amendment.json).
- [2B retained results](../benchmarks/v04-cpu-2b-results.json.gz), [4B retained results](../benchmarks/v04-cpu-4b-results.json.gz) and [cutoff checkpoint](../benchmarks/v04-cpu-study-at-cap.json.gz).
- [Closed-stream/CRC and environment summary](../benchmarks/v04-cpu-study-environment-summary.json), [shared-environment log](../benchmarks/v04-cpu-study-environment.jsonl.gz), [4B environment log](../benchmarks/v04-cpu-study-environment-4b.jsonl.gz) and [download/cleanup receipt](../benchmarks/v04-cpu-download-receipt.json).
- [Reviewed evaluation workflow](evaluation-v04.md): 200 prepared cases and blank independent-review forms; reference labels remain pending.

For a modest computer, start with the already demonstrated MiniLM retrieval and
DeBERTa proposition-scoring tools, inspect source context and use generative
analysis on a bounded selection. Their own scores are uncalibrated and their
tasks differ from the 19-criterion generative analysis. The separate retrieval
proof covers 5,000 articles; the NLI proof covers 20 whole articles. Neither is a
5,000-article local generative benchmark. The cloud profile remains the default
candidate for complete contextual analysis; this trial makes no automatic
change to that selection.
