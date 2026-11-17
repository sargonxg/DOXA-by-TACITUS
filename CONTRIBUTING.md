# Contributing to DOXA

Start with the [documentation map](docs/README.md), [architecture](docs/architecture.md) and [interpretation contract](docs/v3-interpretation.md). Use an isolated `--data-dir` for development; retain published corpora, snapshots and benchmark receipts as historical evidence.

## Develop and check

Install Python 3.11+ with `python -m pip install -e '.[dev]'`, then build the UI with `npm --prefix frontend ci` and `npm --prefix frontend run build`. Start the API with `doxa --data-dir data/development` and open localhost:8000.

Run the checks appropriate to your change. The [verification guide](docs/verification-v04.md) gives exact commands and explains which tests are mocked, use actual API/report workers, or replay all recorded sources. Normal tests make no paid inference requests and do not download weights. Full replay uses bounded temporary storage and requires an explicit manifest hash.

## Preserve analytical meaning

- Keep original source text, exact Unicode offsets and model payloads immutable. A new extraction is a distinct source version.
- Separate descriptive frames, prejudice indicators and framework boundaries. Keep alternative interpretations, missing context and unresolved decisions visible.
- Freeze analytical changes under a distinct method identity; operational budgets/concurrency belong to execution policy. Preserve historical identities.
- Label human review, model output and editorial commentary by their actual origin. Do not manufacture reference labels or infer accuracy from source anchoring/model agreement.
- Keep source rights, model provenance and the relevant run/query scope attached to publications.

For a model or prompt proposal, use a small declared cohort first and retain failures. Follow the [independent-evaluation protocol](docs/evaluation-v04.md); do not expose held-out labels through memory or tune on them. Proposed performance claims need the corresponding measured receipt and its limitations.

## Submit a change

Explain the concrete problem, resulting behavior, relevant validation and any remaining limits. A minimal reproducer is useful for a bug. Update the public guide when a user-visible workflow changes. The [refinement roadmap](docs/refinement-roadmap.md) lists prioritized experiments and acceptance evidence; planned work is not a completed capability.
