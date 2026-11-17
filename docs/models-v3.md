# DOXA v3 model profiles

DOXA combines source-grounded model interpretation with optional pretrained CPU retrieval and rubric scoring. These models have not been fine-tuned by DOXA. Registry availability and passing runtime smoke tests do not establish accuracy for religious prejudice, political discourse or news.

## Explicit CPU installation

The application never downloads weights on startup, health checks or inference. Install PyTorch from its **CPU index first**; PyPI's ordinary Linux torch distribution can pull large CUDA dependencies. The `cpu` extra deliberately omits torch.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install 'torch==2.9.1+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[cpu]'
python scripts/model_setup.py list
python scripts/model_setup.py download minilm deberta --model-dir ./data/cpu-models
export DOXA_CPU_MODEL_DIR="$PWD/data/cpu-models"
python scripts/model_setup.py verify minilm deberta
python scripts/model_setup.py health
python scripts/model_setup.py smoke
```

The tested Linux x86-64/Python 3.12 combination uses `torch==2.9.1+cpu`, `transformers==4.57.6`, `tokenizers==0.22.2`, `safetensors==0.8.0` and `numpy==2.5.5`. The `+cpu` wheel label is platform-specific; on macOS install the official PyTorch wheel for that platform and use CPU device selection. The runtime always loads onto `cpu`, even if an existing torch installation also supports a GPU. No CUDA packages are required.

The installer reads only the registry's immutable Hugging Face revisions. It checks the upstream declared license and sizes, requests only allowlisted files, verifies upstream LFS SHA256 when provided, and records SHA256 and byte count for every artifact. It accepts safetensors and tokenizer/config/model-card files; it excludes pickle weights, training state and remote code. Downloads use a private staging directory and become visible only when complete. Interrupted stages are cleaned when the installer exits normally; a process kill may leave a dot-prefixed staging directory that can be removed after confirming no installer is running.

Each installed bundle includes `manifest.json` with license, model card, revision, artifact hashes, sizes and installation time. `--max-download-mib` defaults to 1200 per explicitly selected bundle; the registry imposes smaller model-specific caps. A 512MiB disk reserve is required. Existing verified bundles are reused without network access. Damaged installations fail closed rather than being replaced silently: move/remove that bundle explicitly, then rerun the installer. Preserve model-card/license attribution when redistributing artifacts.

Budget at least 4GiB free disk for CPU bundles, dependency installation and working space, beyond the local generator's storage. The pinned CPU artifact bundles downloaded during verification total 471,595,168 bytes; installed runtimes and caches require additional space. For the full local application, 16GB physical RAM, an 8K Q4 generator and one generator request at a time are conservative targets to test. Measure available memory and a representative batch before scheduling 5,000 articles; CPU throughput is hardware- and document-dependent. CPU retrieval/NLI can be used without installing Ollama or a generator.

## Registry

| ID | Model | License | Effective input and role |
|---|---|---|---|
| `minilm` | [sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Apache-2.0 | English short-window retrieval, 256 tokens including specials, 384 normalized dimensions. |
| `deberta` | [MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c) | MIT | Experimental English rubric NLI, 512 tokens **including source, hypothesis and special tokens**. The `-c` variant uses its author's commercially friendly training-data mix. |
| `cloud-gemma` | [Workers AI Gemma 4 26B A4B](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) | Apache-2.0 weights | Cloud primary; app profile bounds input to 12K tokens, output 2K; 32K context profile. |
| `cloud-verifier` | [Workers AI GPT-OSS-120B](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/) | Apache-2.0 weights | Independent-family verifier; disagreements need review. No silent cheaper-model substitution. |
| `local-qwen` | [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), Ollama `qwen3.5:4b` | Apache-2.0 | Q4 local fallback, 8K context, one request, reasoning off. Freeze the actual installed Ollama digest. |

CPU revisions and all profile settings are in `doxa/model_registry.py`. Managed provider model names do not guarantee immutable weights: record provider response identifiers, execution date, prompt/schema/rubric/memory versions, and the revision limitation. Prices in registry entries are estimates verified during research, not billing guarantees. Registry entries describe bounds; provider integrations must explicitly apply supported settings rather than assuming an entry changes a remote runtime automatically.

## Runtime contract

```python
from pathlib import Path
from doxa.cpu_models import CPUModels

models = CPUModels(Path("data/cpu-models"), threads=4, batch_size=8)
print(models.health())  # presence/dependency check; no model load/network
vectors = models.embed(["A short source passage."])  # raises if >256 tokens
windows = models.embed_windows([long_source_text])   # full token coverage and exact offsets
scores = models.score_rubrics(source_text, [
    {"id": "equal-rights", "text": "The speaker supports equal rights for Muslim residents."},
    {"id": "condemn-threats", "text": "The speaker condemns threats against Jewish people."},
])
```

`health(verify_artifacts=True)` checks every file hash. Actual model loading always verifies the full manifest, loads safetensors with `local_files_only=True`, disables remote code, uses the pinned fast tokenizer and selects CPU. Missing dependencies/bundles produce explicit errors. `CPUModelError.code` distinguishes absent models, changed artifacts, invalid inputs and runtime failures. Threads and inference batches are bounded; CPU torch thread count is a process-level setting.

MiniLM uses the official model's attention-mask mean pooling and L2 normalization. Its sentence-transformer limit is 256 wordpieces even though the underlying encoder configuration has 512 positions. `embed` rejects oversize text. `embed_windows` returns each exact source substring, Unicode start/end offsets and an embedding; use source IDs/hashes supplied by the application to identify windows durably. Similarity does not establish matching stance or prejudice.

Formatting-only extracted inputs can be nonblank yet produce no tokenizer tokens. `embed_windows` discloses these as `nonindexable_inputs` with source input indices/reasons; persistent index counts and per-document manifests preserve that exclusion. Direct query embedding and NLI scoring reject tokenless inputs. No arbitrary substitute vector or semantic judgment is generated.

The DeBERTa model has two logits: `0=entailment`, `1=not_entailment`. For each source/hypothesis pair, the runtime calculates `softmax(logits)[0]`. Hypotheses are scored **independently**, so several can score highly. It never softmaxes competing prejudice categories against one another. `not_entailment` is not contradiction. Scores are **uncalibrated model scores, not probabilities of prejudice**, and no automatic threshold, document aggregation, author judgment or finding is produced.

The runtime budgets the longest hypothesis and all pair special tokens before constructing overlapping source windows. It checks re-tokenized windows and actual combined batch length; it never silently truncates. Results preserve the hypothesis, raw logits, score, exact window text/offsets, model profile and pair count. Default output aggregation is `none`; a caller choosing max/mean aggregation must document that choice and validate it separately. Source-window scores are not precise evidence-span extraction: downstream source anchoring, attribution and review remain necessary.

## Persistent passage retrieval

```bash
python scripts/semantic_index.py build PROJECT_ID --data-dir ./data
python scripts/semantic_index.py search PROJECT_ID --data-dir ./data --text 'religious discrimination and equal rights'
```

`SemanticIndex(store, CPUModels(...)).build(project_id, document_ids=None)` indexes ready document source blocks using MiniLM windows and returns document/passage counts, model profile, index ID, bytes and elapsed time. An explicit subset updates those documents and preserves previously indexed project documents. Unchanged document bundles are reused across restarts, keyed by source/block content, embedding revision and segmentation version. A corrupt cache is repaired by an explicit build. The active project manifest is published only after the requested scope has completed; interrupted builds can reuse completed document bundles.

`search(project_id, text, limit=20)` streams cosine comparisons over bounded NumPy chunks and returns exact block-relative Unicode citations, current bibliographic metadata and similarity scores. Each returned quote is checked against the current source. Changed/deleted documents are excluded and disclosed; new sources remain visibly unindexed until a build. Query inputs use the same explicit 256-token bound as other direct MiniLM embeddings. The default four-chunk in-memory cache is bounded; vectors use float32 and load with `allow_pickle=False`. Artifact hashes and model revision are checked. Source bodies are never supplied by untrusted `.npy` pickle payloads.

CPU embedding inference groups existing windows by token length, then restores every vector to its original source-window order. This reduces padding; text, source ranges, model revision, attention-mask mean pooling and L2 normalization remain unchanged. `CPUModels(..., embedding_batching="source_order")` retains the original control policy. Runtime profiles record thread/batch settings and policy, while index manifests count the policies used by persisted document bundles; older bundles without an explicit policy were generated in source order.

A warmed, alternating comparison on 32 actual frozen-news source windows measured maximum absolute vector difference `1.12e-7` and pairwise cosine-score difference `3.58e-7`, below the declared `1e-5` acceptance tolerance. All 32 vectors remained in their original order. Median observed latency was 4.215s in source order and 1.974s with length grouping on this shared host. This microbenchmark does not promise a 2.13x full-corpus speedup or establish interpretation accuracy. Source-order cached vectors are retained with their provenance after this parity check; the machine-readable result is `benchmarks/cpu-embedding-batching-parity.json`.

Baseline resource caps are 10,000 ready documents (to accommodate 5,000 news articles plus institutional cases), 250,000 passages and 512MiB of active index data per project. Each document is capped at 20,000 passages, source extraction remains bounded at 40 million characters, and vector/anchor chunks hold at most 256 passages. Oversized scopes fail explicitly. Historical content versions can leave inactive artifact bundles; disk lifecycle/garbage collection is a separate operational task, not silent deletion during live search.

Verification performed offline with both runtimes loaded: a generated 9,294-character functional input produced three NLI windows/six pairs and seven normalized 384D embedding windows, ending at the exact final source character. The cold run took 10.896s and observed peak RSS 1,804,916KiB in this environment. This is a functional/resource observation, not a news quality evaluation or a portable throughput promise.

The actual complete 20-document institutional corpus also indexed successfully: 9,249 passages, 18,112,424 index bytes and 99 explicitly nonindexable formatting-only inputs (PDF private-use decorative glyphs), with zero documents lacking passages. A resumed build reused ten completed document bundles and took 85.665s; this is **not a cold 20-document timing**. Three unreviewed example searches returned five exact-source-validated hits each in 0.076s, 0.152s and 0.083s. Observed process peak RSS was 599,844KiB. These results demonstrate functioning persistence/retrieval and resource use, not ranking accuracy or prejudice-classification accuracy.

## Evaluation and reviewed learning

Compare the pretrained NLI scorer, retrieval-assisted prompted model and cloud primary/verifier on independently reviewed news and the existing institutional regression cases. Split by document, publisher/syndication cluster and time; holdout material must not enter training, retrieved exemplars or prompt optimization. Measure criterion precision/recall, boundary false positives, abstention, target/speaker/proposition stance accuracy, anchor integrity, processing coverage, review workload, time, memory and tokens/cost. Runtime smoke inputs are not an accuracy benchmark.

Reviewed exemplars can improve prompts without weight training; store exemplar IDs/version in every run. Disagreement selects cases for review rather than establishing truth. Start trained challengers with [SetFit](https://arxiv.org/abs/2209.11055) only after reviewed data and source-separated learning curves justify it. Promote a challenger explicitly after frozen evaluation and preserve rollback. If introducing ONNX/INT8, first verify output and quality parity; generic ONNX support does not imply every custom model is supported.

No specialist is silently substituted. Several antisemitism/anti-Muslim checkpoints are tweet-domain, lack sufficient license documentation or restrict product use; the MIT NLI engine is a transparent experimental alternative, not a newly validated specialist. The [NLI model's paper](https://arxiv.org/abs/2312.17543) describes the general zero-shot approach, not DOXA-specific accuracy.
