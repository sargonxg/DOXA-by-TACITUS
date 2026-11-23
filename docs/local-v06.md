# DOXA on an 8 GB computer

The low-memory profile is the default for a native v0.6 installation and the supplied local Docker Compose app. It is an **operating target for bounded work**, not a promise that a large generative model or the complete research corpus fits in 8 GB. No model, API account or GPU is needed to inspect sources, use observable-wording tools, explore a saved result or generate an appropriate report.

The one-page workbench keeps source text, a declared inquiry, results, review and publication together. **Explore recorded evidence** opens twenty real Global Voices development articles with 645 retained model decisions and 11,280 checked exact source references. All four outcomes remain present, alongside 33 accepted source units and one genuinely partial unit. The deliberate diagnostic selection covers 2020–2025 and excludes the canonical and prepared evaluation holdouts. Its full 5,000-source parent remains partial with 79 missing decisions; no new inference or independent human labels are supplied. This is read-only exploration, not installation of a fabricated new analysis. Each source retains its authors, translation credits, URL and CC BY 3.0 notice.

Open the [real-source PDF reader](../examples/v06-recorded-example.pdf), [HTML](../examples/v06-recorded-example.html), [complete selected workbook](../examples/v06-recorded-example.xlsx) or [full frozen JSON snapshot](../examples/v06-recorded-example.json.gz). PDF/HTML explain eight selected cases; XLSX/JSON preserve all 645 original decisions and every selected source. The charts are deliberately selected criterion counts, not estimated bias prevalence or interpretation accuracy. Reading guidance is application/AI-authored and remains separate from original model text.

**Try practice sources** is a different workflow: five explicitly synthetic practice texts duplicated byte-for-byte from the [published v0.5 inputs](../examples/pilot-v05-inputs), for a user's own intake and report generation. They supply no recorded model interpretations or reference labels. The larger [20-document institutional showcase and 5,000-source recorded runs](../benchmarks/README.md) remain separate optional collections. A demonstration is not a fresh inference benchmark.

## Install and check

The published wheel contains the compiled interface and the Python engine. From an empty directory, install the wheel you downloaded:

```sh
python -m pip install ./doxa_by_tacitus-0.6.1-py3-none-any.whl
doxa doctor --json --data-dir ./doxa-data
doxa --local-profile low-memory --data-dir ./doxa-data
```

Open <http://127.0.0.1:8000>. The wheel requires no Node build. A source checkout still needs the frontend build described in the [deployment guide](deployment.md). The installed Python dependencies and native rendering libraries need to be present; Docker provides the documented system environment.

For the reference container profile:

```sh
docker compose up --build -d
docker compose exec app doxa doctor --json --data-dir /data
```

Compose enforces a **4 GiB limit over the application container and its worker children**. The default parser process has a 1,536 MiB address-space ceiling, with one parser and one heavy operation at a time; CPU retrieval defaults to two threads and batches of two. Native mode applies worker and parser controls but does **not** impose an aggregate operating-system memory limit on the Python process tree. Use the container profile when that aggregate boundary is required. A separately running Ollama server and the browser are outside the application-container budget.

The local profile can be selected with `DOXA_LOCAL_PROFILE=low-memory|standard|server` or the explicit CLI flag. Native standard mode targets a larger 16 GB environment. Compose's explicit environment variables and memory limit remain authoritative: changing its profile label alone does not raise the container limit or parser settings. Resource overrides are reported by the doctor and need a service restart. They are not qualified as the default 8 GB profile.

Native macOS and Windows use the parser subprocess timeout, concurrency limits and format-specific extraction limits. The Linux worker also applies the parser address-space ceiling. Windows users can use Docker Desktop with WSL2 for the documented container memory boundary. Private managed-pilot provisioning and hardened erasure require POSIX. Hardware qualification receipts identify the actual machine and workload before making speed or whole-host memory claims.

## What the doctor establishes

`doxa doctor` shows readable recovery instructions. `doxa doctor --json` returns the same checks in the `doxa-readiness-1` contract. The command inspects the nearest existing data-directory parent without creating it or writing a probe file. It reports:

- Data-directory permissions, free disk and available/cgroup memory where observable.
- Selected profile, effective worker/parser controls and explicit operator overrides.
- API/distribution versions, compiled-interface presence, and UI build compatibility when the build manifest is available.
- PDF dependency availability, optional native library discovery and OCR configuration.
- Local CPU model identities and artifact-size/manifest state, without loading model weights or verifying expensive full-file hashes.

The authenticated `GET /api/system/readiness` supplies content-free diagnostics for the browser. It contains no customer source text, credential values, configured endpoint URLs or private filesystem paths. Readiness does not make provider calls or inference requests. An available model or dependency is not a measured interpretation-quality result. The diagnostic does not render a PDF or perform OCR and cannot certify future extraction success.

The authenticated `GET /api/system/recorded-example` reads only the bounded, checksummed packaged twenty-source snapshot; it never initializes customer storage, changes historical IDs or makes model calls. Its fixed PDF/XLSX/HTML/JSON artifact routes verify release checksums before download. The included generator streams the original archive one source at a time and preserves frozen records, rights metadata and exact quotations. Rebuild/check with `scripts/build_recorded_starter_v06.py --write` and `scripts/build_recorded_example_v06.py`; running the starter builder without `--write` checks reproducibility without changing artifacts.

An `error` produces command exit status 1. Optional capabilities, unknown telemetry and warnings remain distinguishable from a missing core requirement. A different installed-package version or browser build calls for installing a matching release or rebuilding the source interface.

## Choose the work deliberately

| Capability | What it does | Requirements and limits |
| --- | --- | --- |
| Starter sources and saved results | Practice intake/reporting or inspect an explicitly recorded analysis | No fresh model calls; provenance and original coverage remain visible |
| Observable wording | Locate requests, qualification and time expressions with exact spans | Rules are not calibrated judgments of bias, politeness, intent or emotion |
| CPU retrieval | Find related source passages | Optional installed model/runtime; similarity does not establish stance or prejudice |
| CPU NLI | Score explicit source/hypothesis pairs | Optional research capability; uncalibrated scores and separate resource needs |
| Contextual generation | Apply a declared contextual rubric | Explicit installed local model or authorized remote provider; start with a small selected test, preserve partial coverage and inspect evidence |
| OCR | Extract scanned page text | Explicit OCR installation, local model artifacts and enablement; heavier work, separately qualified |

No action automatically pulls weights, silently substitutes a smaller model, changes the requested criteria or sends text to a cloud provider. The [bounded local study](local-study-v04.md) found incomplete CPU generation and substantial model disagreement. It does not justify recommending a small generative model as an effective universal bias detector. More demanding analysis remains an explicit, independently evaluated addition.

For an Ollama startup or model problem, run `doxa doctor --laptop` to check the expected model through loopback metadata. The default doctor works offline. For a scanned document, either configure OCR deliberately or supply text-readable material. For low storage, preserve source and checkpoint data, inspect the scoped workload and check free space before resuming. The full corpus, model weights, indexes, retained histories, builds and complete exports can need **30 GB or more** of disk headroom. The starter workflow uses a smaller selection.

## Optional ONNX candidate, not a promoted default

The `cpu-lite` extra adds ONNX Runtime, Tokenizers and NumPy. It does not install or convert weights. `doxa.cpu_lite.ONNXEmbeddings` accepts only an explicitly prepared local MiniLM-derived candidate bundle with a pinned declared base revision and checksummed `model.onnx` and `tokenizer.json` files. The bundle is operator-trusted input, not a public upload format. Use a self-contained ONNX graph with no external-weight paths and retain all upstream notices and conversion records.

The manifest contract is `doxa-onnx-embedding-1`, with `base_model`, `base_revision`, `license`, declared `precision` (`int8` or `fp32`) and an `artifacts` list containing each file's `path`, byte count and SHA-256. The base must match the installed registry's pinned MiniLM identity. The bundle ceiling is 160 MiB. These checks establish local identity and integrity; they do not prove conversion fidelity, actual graph precision, training rights or semantic parity.

The candidate runtime uses only `CPUExecutionProvider`, bounded source-order batches, explicit token-length rejection, attention-mask mean pooling and L2 normalization. It never truncates oversize text silently. A different runtime/quantization has a distinct method identity and remains `unqualified_candidate`; it is not automatically selected by semantic search.

From a source checkout, inspect a prepared bundle without executing it:

```sh
.venv/bin/python scripts/benchmark_cpu_lite.py \
  --bundle /your/prepared/minilm-onnx \
  --input /your/authorized-inputs.json
```

`--execute` explicitly requests fresh local embedding inference. `--baseline-model-dir /your/already-installed-model-root` additionally compares the same inputs with the existing pinned MiniLM runtime. The benchmark never downloads missing artifacts. An optional `--receipt` creates a new private file and refuses to overwrite an existing one. Receipts contain hashes, counts, actual elapsed times and available numerical comparison, without source text or vectors. A minimum-cosine check of 0.99 is a declared numerical diagnostic, **not** retrieval-relevance validation or interpretation accuracy.

No actual ONNX-model benchmark or hardware performance claim is supplied merely by shipping this adapter. Its contract tests use synthetic tensors. Model promotion requires recorded real-artifact numerical tests, retrieval evaluation and the applicable methodology gates.
