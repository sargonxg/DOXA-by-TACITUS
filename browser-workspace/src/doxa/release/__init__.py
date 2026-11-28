"""Stage and release the versioned, public DOXA research artifacts."""
import hashlib
import json
import platform
import re
from pathlib import Path
from shutil import copy2

TARGETS = {"dataset": "doxa-pilot", "model": "doxa-linear", "space": "doxa-lab"}
NAMESPACE = "Giuliocc"
COLLECTION = "DOXA | Political Discourse and AI Evaluation"
COLLECTION_DESCRIPTION = (
    "TACITUS open research. Try local discourse review, inspect evidence and provisional labels, "
    "and reproduce the CPU baseline."
)
ORDER = ("dataset", "model", "space")
TABLES = ("cases", "judgments", "behavioral_pairs", "revision_examples")
PUBLIC_FILES = {
    "dataset": {
        "README.md", "NOTICE", "annotation-handbook.md", "counts.json", "data-review-notes.md",
        "original_pilot_audit.json", "queries.sql", "source_registry.json", "split_manifest.json",
        *(f"schema/{table}.json" for table in TABLES),
        *(f"tables/{table}/{split}.{extension}"
          for table in TABLES
          for split in ("train", "validation", "test", "challenge")
          for extension in ("jsonl", "parquet")),
    },
    "model": {
        "README.md", "LICENSE", "EVALUATION.md", "model_manifest.json", "feature_spec.json",
        "vocabulary.json", "idf.f32", "coefficients.f32", "intercepts.f32",
        "training_config.json", "training_support.json", "metrics.json",
        "parity_cases.jsonl", "predictions.jsonl",
    },
    "space": {
        "README.md", "LICENSE", "NOTICE", "source.zip", "screenshot.png", "conflict-desk.png", "HUB_STYLE.md",
        "DOCUMENT_REVIEW.md", "document-desk.png", "dist/pdf/LICENSE", "dist/pdf/pdf.worker.min.mjs",
        "REVIEW_RELEASE.md", "POLITICAL_CONSISTENCY.md", "POLITICAL_CONSISTENCY_RESULTS.json", "POLITICAL_CONSISTENCY_COMPARISON.json", "PROFESSIONAL_VALUE_PROTOCOL.md",
        "PROJECT_FORMAT.md", "PDF_QUALITY.md", "dist/wording/manifest.json",
        "dist/examples/political-bias-study.pdf", "dist/examples/political-influencers.pdf",
        "dist/ocr/worker.min.js", "dist/ocr/TESSERACT-LICENSE.md", "dist/ocr/CORE-LICENSE", "dist/ocr/ENGLISH-DATA.md",
        "dist/ocr/lang/eng.traineddata.gz",
        *(f"dist/ocr/core/{name}" for name in (
            "tesseract-core-lstm.wasm.js", "tesseract-core-lstm.wasm",
            "tesseract-core-simd-lstm.wasm.js", "tesseract-core-simd-lstm.wasm",
        )),
        "CLAIM_CHECK.md", "CLAIM_CHECK_RESULTS.json", "CLAIM_CHECK_PREDICTIONS.json", "claim-check.png",
        *(f"dist/verifier/{name}" for name in (
            "adapter.json", "selector.json", "ORT-LICENSE", "TOKENIZERS-LICENSE",
            "THIRD_PARTY_NOTICES.txt", "ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm",
        )),
        "dist/index.html", "dist/doxa.svg", "dist/release-revisions.json", "dist/brand/doxa-reader.webp", "dist/brand/console.css",
    },
}
SPACE_CARD = """---
title: DOXA — Political Bias Audit
emoji: 🔎
colorFrom: green
colorTo: gray
sdk: static
app_file: dist/index.html
pinned: true
license: apache-2.0
short_description: Audit political framing in documents
tags:
- political-discourse
- evidence-review
- responsible-ai
{links}---

# DOXA Political Bias Audit by TACITUS

[TACITUS](https://tacitus.me/doxa/) · [GitHub](https://github.com/sargonxg/DOXA-by-TACITUS)

[Open the audit](https://giuliocc-doxa-lab.static.hf.space/). Upload PDFs, text
files or Markdown and compare model flags by document or by supplied author
label. Each report gives coverage, academic cues and exact source excerpts.
PDF findings link to the physical page. Record a review decision on a
passage and export a case file with its evidence, source fingerprint and
the reviewer's note. Try the two-paper research corpus to see a real report.

The worked example guides measurement review, causal-claim reading and a
caption false alarm. Stacked distributions show flags, no flag and unresolved
checks on a common denominator, with exact section-level counts. Open a
criterion name for definitions, boundary examples and primary references.
Reviewer-authored mitigation drafts are in development and preserve the
original passage alongside any proposed wording and meaning checks.

Save and reopen complete browser projects, including original files, or
transfer a checked `.doxa` archive. Record exact supporting and countervailing
selections with reviewer attribution. Declare event, topic, genre and period
to form comparison cohorts. Optional English OCR and PDF reading records make
extraction gaps and layout assumptions inspectable. Selected causal claims
include separate Methods, Results and limitations reading candidates.

The optional [contextual wording encoder](https://huggingface.co/Giuliocc/doxa-wording-encoder)
was trained locally on the [human wording benchmark](https://huggingface.co/datasets/Giuliocc/doxa-wording-benchmark).
Its 3,639 cases retain upstream BABE expert-majority labels and individual
votes. Native held-out macro-F1 is 0.789 versus 0.742 for the strongest lexical
control. The 23.2 MB browser export scores 0.784. Its separate improvement
interval includes zero. Academic and prejudice tasks need separate validation.
See [the ten improvements and measured limits](REVIEW_RELEASE.md),
[political consistency failures](POLITICAL_CONSISTENCY.md) and
[the professional reviewer study protocol](PROFESSIONAL_VALUE_PROTOCOL.md).

The guide also searches the [DOXA Research Atlas](https://huggingface.co/datasets/Giuliocc/doxa-research-atlas).
Choose a research question, inspect source and reuse information, and send
an original licensed paragraph to the local audit. Research questions carry
their current capability and evidence requirements. Catalogue topics and
source passages remain candidates for annotation.

![Worked corpus from two published political-science papers](document-desk.png)

The [DOXA Linear model](https://huggingface.co/Giuliocc/doxa-linear) screens
six political language criteria. It was trained on the
[provisional DOXA Pilot dataset](https://huggingface.co/datasets/Giuliocc/doxa-pilot).
The [DOXA Evidence Verifier](https://huggingface.co/Giuliocc/doxa-evidence-verifier)
can check selected causal claims against Methods and Results in the same
document. Its evidence training and evaluation use the
[DOXA Evidence Benchmark](https://huggingface.co/datasets/Giuliocc/doxa-evidence-benchmark).

Model flags need human review. Research cues locate wording for inspection.
The audit uses the supplied document and leaves omission or double-standard
judgments to reviewers with appropriate comparison sources. Files remain in
the browser. See [document audit methods](DOCUMENT_REVIEW.md) and the
[evidence verifier evaluation](CLAIM_CHECK.md).
"""


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def entries(directory):
    allowed = {
        ".json", ".jsonl", ".parquet", ".md", ".csv", ".sql", ".f32",
        ".yaml", ".yml", ".html", ".js", ".mjs", ".css", ".svg", ".png", ".zip", ".webp",
    }
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in release stage: {path}")
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        rel = path.relative_to(directory)
        if any(part.startswith(".") for part in rel.parts):
            raise ValueError(f"Hidden release file: {rel}")
        kind = directory.name
        verifier_asset = kind == "space" and rel.as_posix() in PUBLIC_FILES["space"] and rel.parts[:2] == ("dist", "verifier")
        example_asset = kind == "space" and rel.parts[:2] == ("dist", "examples") and rel.as_posix() in PUBLIC_FILES["space"]
        ocr_asset = kind == "space" and rel.parts[:2] == ("dist", "ocr") and rel.as_posix() in PUBLIC_FILES["space"]
        bundled_wasm = kind == "space" and re.fullmatch(r"dist/assets/[\w.-]+\.wasm",rel.as_posix())
        if path.suffix not in allowed and path.name not in {"LICENSE", "NOTICE"} and not (verifier_asset or example_asset or ocr_asset or bundled_wasm):
            raise ValueError(f"File type outside the release allow-list: {rel}")
        if kind in PUBLIC_FILES:
            name = rel.as_posix()
            space_asset = kind == "space" and (
                re.fullmatch(r"dist/assets/[\w.-]+\.(js|css|wasm)", name)
                or name in {
                    "dist/generated/references.json", "dist/generated/metrics.json",
                    "dist/generated/conflict-desk.json", "dist/generated/contextual-results.json",
                    "dist/generated/predictions.jsonl", "dist/generated/EVALUATION.md",
                    "dist/model/model_manifest.json", "dist/model/feature_spec.json",
                    "dist/model/vocabulary.json", "dist/model/idf.f32",
                    "dist/model/coefficients.f32", "dist/model/intercepts.f32",
                }
            )
            if name not in PUBLIC_FILES[kind] and not space_asset:
                raise ValueError(f"Path outside the {kind} release allow-list: {rel}")
        yield rel, path


def checksums(directory, write=False):
    sums = "".join(f"{sha(path)}  {rel.as_posix()}\n" for rel, path in entries(directory))
    manifest = directory / "SHA256SUMS"
    if write:
        manifest.write_text(sums, encoding="utf-8")
    elif manifest.read_text(encoding="utf-8") != sums:
        raise ValueError(f"Changed files in {directory}")
    return len(sums.splitlines())


def stage_space(root):
    from zipfile import ZIP_DEFLATED, ZipFile
    stage_root = root / "release_staging"
    space = stage_root / "space"
    space.mkdir(parents=True, exist_ok=True)
    (space / "README.md").write_text(SPACE_CARD.format(links=""), encoding="utf-8")
    for name in ("LICENSE", "NOTICE"):
        copy2(root / name, space / name)
    copy2(root / "brand" / "HUB_STYLE.md", space / "HUB_STYLE.md")
    copy2(root / "docs" / "document-review.md", space / "DOCUMENT_REVIEW.md")
    copy2(root / "docs" / "claim-check.md", space / "CLAIM_CHECK.md")
    release_docs = {
        "docs/review-release.md": "REVIEW_RELEASE.md",
        "improvement_release/evaluation/POLITICAL_CONSISTENCY.md": "POLITICAL_CONSISTENCY.md",
        "improvement_release/evaluation/political-consistency-results.json": "POLITICAL_CONSISTENCY_RESULTS.json",
        "improvement_release/evaluation/political-consistency-comparison.json": "POLITICAL_CONSISTENCY_COMPARISON.json",
        "improvement_release/evaluation/PROFESSIONAL_VALUE_PROTOCOL.md": "PROFESSIONAL_VALUE_PROTOCOL.md",
        "improvement_release/projects/README.md": "PROJECT_FORMAT.md",
        "improvement_release/pdf/PDF_QUALITY.md": "PDF_QUALITY.md",
    }
    for source, target in release_docs.items():
        copy2(root / source, space / target)
    source_dirs = ("src", "web", "brand", "data/authoring", "data/manifests", "configs", "docs", "tests", "jobs", "expansion/conflict",
                   "claim_review", "evidence_benchmark")
    source_names = ("pyproject.toml", "uv.lock", "README.md", "CITATION.cff", "CHANGELOG.md", "LICENSE", "NOTICE")
    excluded = {"node_modules", "__pycache__", "dist", "generated", "test-results", "playwright-report", ".pytest_cache"}
    with ZipFile(space / "source.zip", "w", ZIP_DEFLATED) as archive:
        for name in source_names:
            if (root / name).is_file():
                archive.write(root / name, name)
        public_checks = (
            "improvement_release/pdf/fixtures/scanned.pdf",
            "improvement_release/pdf/PDF_QUALITY.md",
            "improvement_release/evaluation/cohorts.test.mjs",
            "improvement_release/evaluation/create-consistency-fixtures.mjs",
            "improvement_release/evaluation/run-political-consistency.mjs",
            "improvement_release/evaluation/political-consistency-fixtures.json",
            "improvement_release/evaluation/POLITICAL_CONSISTENCY_PROTOCOL.json",
            "improvement_release/evaluation/POLITICAL_CONSISTENCY.md",
            "improvement_release/evaluation/PROFESSIONAL_VALUE_PROTOCOL.md",
            "improvement_release/explanations/check.mjs",
            "improvement_release/explanations/browser-check.mjs",
            "improvement_release/explanations/README.md",
            "improvement_release/projects/browser-check.mjs",
            "improvement_release/projects/README.md",
        )
        for name in public_checks:
            archive.write(root / name, name)
        for directory in source_dirs:
            for path in sorted((root / directory).rglob("*")):
                rel = path.relative_to(root)
                if (path.is_file() and not path.is_symlink() and
                        not any(p.startswith(".") or p in excluded for p in rel.parts) and
                        not rel.is_relative_to("web/public/model") and
                        not rel.is_relative_to("web/public/pdf") and
                        not rel.is_relative_to("web/public/verifier") and
                        not rel.is_relative_to("web/public/examples") and
                        not rel.is_relative_to("web/public/ocr") and
                        rel.as_posix() != "web/public/release-revisions.json" and
                        path.name not in {"DOXA_CODEX_BUILD.md"}):
                    archive.write(path, rel.as_posix())


def stage(root):
    stage_space(root)
    stage_root = root / "release_staging"
    receipt_path = root / "release_receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        slug = receipt.get("collection_slug")
        app_parent = receipt.get("repos", {}).get("space", {}).get("url")
        if slug and app_parent:
            for kind in ("dataset", "model"):
                card = stage_root / kind / "README.md"
                text = card.read_text(encoding="utf-8")
                base = re.split(r"\n<!-- doxa-(?:discovery|release-provenance) -->", text, maxsplit=1)[0]
                card.write_text(
                    base.rstrip() + "\n\n<!-- doxa-discovery -->\n"
                    "## Explore the rest of DOXA\n\n"
                    f"- [Open DOXA Evidence Lab]({app_parent}) to inspect examples and run browser-local review.\n"
                    f"- [DOXA collection](https://huggingface.co/collections/{slug}) groups the demo, data, and model.\n"
                    "- [TACITUS.me](https://tacitus.me) develops the broader research and tools.\n",
                    encoding="utf-8",
                )
    for kind in ORDER:
        checksums(stage_root / kind, write=True)


def check(root):
    stage_root = root / "release_staging"
    required = {
        "dataset": ("README.md",),
        "model": ("README.md", "model_manifest.json", "coefficients.f32", "metrics.json"),
        "space": ("README.md", "dist/index.html", "source.zip"),
    }
    manifest = {}
    for kind in ORDER:
        directory = stage_root / kind
        for filename in required[kind]:
            if not (directory / filename).is_file():
                raise FileNotFoundError(directory / filename)
        files = checksums(directory)
        manifest[kind] = {"repo": f"{NAMESPACE}/{TARGETS[kind]}", "visibility": "public",
                          "files": files + 1, "bytes": sum(p.stat().st_size for _, p in entries(directory)) + (directory / "SHA256SUMS").stat().st_size,
                          "manifest_sha256": sha(directory / "SHA256SUMS")}
    model_directory = stage_root / "model"
    provenance = json.loads((model_directory / "model_manifest.json").read_text(encoding="utf-8"))
    if not provenance["heads"]:
        raise ValueError("The trained release requires supported model heads")
    for filename, digest in provenance["training_data_files"].items():
        if sha(stage_root / "dataset" / filename) != digest:
            raise ValueError(f"Training data hash mismatch: {filename}")
    for filename in ("feature_spec.json", "vocabulary.json", "idf.f32", "coefficients.f32", "intercepts.f32"):
        if sha(model_directory / filename) != sha(stage_root / "space" / "dist" / "model" / filename):
            raise ValueError(f"Browser model asset differs from staged model: {filename}")
    return {"budget_usd": 0, "targets": manifest}


def doctor():
    from huggingface_hub import HfApi, get_token
    data = {"python": platform.python_version(), "platform": platform.platform(), "sdk_authenticated": False}
    if get_token():
        try:
            data["sdk_account"] = HfApi().whoami()["name"]
            data["sdk_authenticated"] = True
        except Exception as error:
            data["sdk_error"] = type(error).__name__
    return data


def remote_file(repo, kind, filename, revision):
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo, filename, repo_type=kind, revision=revision, token=False))


def publish(root, namespace, public, budget):
    from huggingface_hub import CommitOperationAdd, HfApi, get_token
    from huggingface_hub.errors import RepositoryNotFoundError
    if namespace != NAMESPACE or not public or budget != 0:
        raise ValueError("Expected --namespace Giuliocc --public --budget-usd 0")
    if not get_token() or HfApi().whoami()["name"] != NAMESPACE:
        raise RuntimeError("Sign into the Hugging Face CLI locally as Giuliocc with write access.")
    check(root)
    api = HfApi()
    receipt_path = root / "release_receipt.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {"repos": {}}
    for kind in ORDER:
        repo = f"{namespace}/{TARGETS[kind]}"
        directory = root / "release_staging" / kind
        state = receipt["repos"].get(kind, {})
        try:
            info = api.repo_info(repo, repo_type=kind)
            remote_sha = info.sha
            if not state or remote_sha not in {state.get("sha"), state.get("initial_sha")}:
                raise RuntimeError(f"Unexpected existing repository or parent revision for {repo}; preserve it.")
        except RepositoryNotFoundError:
            api.create_repo(repo, repo_type=kind, private=True, space_sdk="static" if kind == "space" else None)
            info = api.repo_info(repo, repo_type=kind)
            remote_sha = info.sha
            state = {"initial_sha": remote_sha}
            receipt["repos"][kind] = state
            receipt_path.write_text(json.dumps(receipt, indent=2))
        if kind == "model":
            path = directory / "model_manifest.json"
            manifest = json.loads(path.read_text())
            dataset_path = root / "release_staging" / "dataset"
            for filename, digest in manifest["training_data_files"].items():
                if sha(dataset_path / filename) != digest:
                    raise ValueError(f"Training data hash mismatch: {filename}")
            manifest["hub_dataset_commit"] = receipt["repos"]["dataset"]["sha"]
            manifest["hub_dataset_repo"] = receipt["repos"]["dataset"]["url"]
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            dataset_link = f'{manifest["hub_dataset_repo"]}/tree/{manifest["hub_dataset_commit"]}'
            card = directory / "README.md"
            contents = card.read_text(encoding="utf-8").split("\n<!-- doxa-release-provenance -->")[0]
            card.write_text(contents.rstrip() + f"\n\n<!-- doxa-release-provenance -->\n[Frozen dataset revision]({dataset_link})\n",
                            encoding="utf-8")
        if kind == "space":
            links = f"models:\n- {namespace}/{TARGETS['model']}\ndatasets:\n- {namespace}/{TARGETS['dataset']}\n"
            conflict = {}
            if (root / "conflict_release_receipt.json").is_file():
                conflict = json.loads((root / "conflict_release_receipt.json").read_text(encoding="utf-8"))
                if conflict.get("tag"):
                    links += f"- {namespace}/doxa-conflict-context\n"
            (directory / "README.md").write_text(SPACE_CARD.format(links=links), encoding="utf-8")
            # Add remote references adjacent to runtime assets without altering the frozen weights.
            references = {name: {key: receipt["repos"][name][key] for key in ("url", "sha")}
                          for name in ("dataset", "model")}
            if receipt.get("collection_slug"):
                references["collection"] = {
                    "url": f"https://huggingface.co/collections/{receipt['collection_slug']}"
                }
            if conflict.get("tag"):
                references["conflict_context"] = {key: conflict[key] for key in ("url", "sha")}
            (directory / "dist" / "release-revisions.json").write_text(json.dumps(references, indent=2), encoding="utf-8")
            for model_manifest in (directory / "dist").rglob("model_manifest.json"):
                copy2(root / "release_staging" / "model" / "model_manifest.json", model_manifest)
        checksums(directory, write=True)
        digest = sha(directory / "SHA256SUMS")
        if state.get("manifest_sha256") == digest and state.get("sha") == remote_sha:
            if info.private:
                api.update_repo_settings(repo, repo_type=kind, private=False)
            continue
        operations = [CommitOperationAdd(path_in_repo=rel.as_posix(), path_or_fileobj=path)
                      for rel, path in entries(directory)]
        operations.append(CommitOperationAdd(path_in_repo="SHA256SUMS", path_or_fileobj=directory / "SHA256SUMS"))
        commit = api.create_commit(repo, operations=operations, repo_type=kind, parent_commit=remote_sha,
                                   commit_message="DOXA v0.1.0 experimental research pilot")
        state.update(sha=commit.oid, manifest_sha256=digest, url={
            "dataset": f"https://huggingface.co/datasets/{repo}",
            "model": f"https://huggingface.co/{repo}",
            "space": f"https://huggingface.co/spaces/{repo}",
        }[kind])
        state.pop("tag", None)
        receipt_path.write_text(json.dumps(receipt, indent=2))
        if info.private:
            api.update_repo_settings(repo, repo_type=kind, private=False)
    return receipt


def collection(root):
    from huggingface_hub import HfApi
    api = HfApi()
    if api.whoami()["name"] != NAMESPACE:
        raise RuntimeError("Expected authenticated account Giuliocc")
    path = root / "release_receipt.json"
    receipt = json.loads(path.read_text())
    if any("sha" not in receipt["repos"].get(kind, {}) for kind in ORDER):
        raise RuntimeError("Publish the artifacts before creating the collection")
    result = api.create_collection(COLLECTION, namespace=NAMESPACE,
                                   description=COLLECTION_DESCRIPTION, exists_ok=True)
    if result.description != COLLECTION_DESCRIPTION:
        api.update_collection_metadata(result.slug, description=COLLECTION_DESCRIPTION)
    receipt["collection_slug"] = result.slug
    path.write_text(json.dumps(receipt, indent=2))
    notes = {
        "space": "Try the local passage screening and corpus-review workflow.",
        "dataset": "Inspect provisional annotations, evidence and provenance.",
        "model": "Download the trained linear baseline and reproduce its evaluation.",
    }
    for kind in ("space", "dataset", "model"):
        api.add_collection_item(result.slug, f"{NAMESPACE}/{TARGETS[kind]}", kind,
                                note=notes[kind], exists_ok=True)
    return f"https://huggingface.co/collections/{result.slug}"


def verify(root):
    from math import isclose
    from tempfile import TemporaryDirectory
    from urllib.request import urlopen
    from huggingface_hub import HfApi
    import pyarrow.parquet as pq
    from doxa.models import PortableModel
    api = HfApi(token=False)
    receipt = json.loads((root / "release_receipt.json").read_text())
    downloaded = {}
    for kind in ORDER:
        state = receipt["repos"][kind]
        repo = f"{NAMESPACE}/{TARGETS[kind]}"
        remote_info = api.repo_info(repo, repo_type=kind, revision=state["sha"], token=False)
        if remote_info.private or remote_info.sha != state["sha"]:
            raise RuntimeError(f"Revision is not publicly available: {repo}")
        sums = remote_file(repo, kind, "SHA256SUMS", state["sha"])
        if sha(sums) != state["manifest_sha256"]:
            raise ValueError(f"Remote manifest mismatch: {repo}")
        downloaded[kind] = {}
        for line in sums.read_text().splitlines():
            digest, name = line.split("  ", 1)
            path = remote_file(repo, kind, name, state["sha"])
            if sha(path) != digest:
                raise ValueError(f"Remote checksum mismatch: {repo}/{name}")
            downloaded[kind][name] = path
    parquet_rows = {name: pq.ParquetFile(path).metadata.num_rows
                    for name, path in downloaded["dataset"].items() if name.endswith(".parquet")}
    if not parquet_rows or not sum(parquet_rows.values()):
        raise RuntimeError("No readable dataset Parquet records")
    manifest = json.loads(downloaded["model"]["model_manifest.json"].read_text())
    if manifest.get("hub_dataset_commit") != receipt["repos"]["dataset"]["sha"]:
        raise RuntimeError("Model provenance differs from the published dataset")
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        for name, path in downloaded["model"].items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(path, target)
        model = PortableModel(directory)
        fixtures = [json.loads(line) for line in (directory / "parity_cases.jsonl").read_text(encoding="utf-8").splitlines()]
        for fixture in fixtures:
            actual = model.predict(fixture["text"], fixture["context"])
            expected = fixture["output"]
            if len(actual["criteria"]) != len(expected["criteria"]):
                raise RuntimeError("Remote model criteria differ from parity fixtures")
            for current, reference in zip(actual["criteria"], expected["criteria"], strict=True):
                for field in ("criterion_id", "prediction_status", "suggested_decision"):
                    if current.get(field) != reference.get(field):
                        raise RuntimeError(f"Remote inference differs from parity fixture at {field}")
                if (current["scores"] is None) != (reference["scores"] is None):
                    raise RuntimeError("Remote inference score status changed")
                if reference["scores"] and any(
                    not isclose(current["scores"][name], value, abs_tol=1e-5)
                    for name, value in reference["scores"].items()
                ):
                    raise RuntimeError("Remote inference scores changed")
    runtime = api.get_space_runtime(f"{NAMESPACE}/{TARGETS['space']}", token=False)
    ready_domains = [item["domain"] for item in runtime.raw.get("domains", [])
                     if item.get("stage") == "READY" and item.get("domain", "").endswith(".hf.space")]
    app_url = f"https://{ready_domains[0]}" if ready_domains else api.space_info(
        f"{NAMESPACE}/{TARGETS['space']}", token=False
    ).host
    if app_url:
        with urlopen(app_url, timeout=20) as response:
            if "DOXA" not in response.read().decode("utf-8"):
                raise RuntimeError("Public Space response did not contain the DOXA app")
    return {"repos": receipt["repos"], "space_runtime": str(runtime.stage), "space_app_url": app_url,
            "remote_parquet_rows": parquet_rows, "remote_parity_fixtures": len(fixtures),
            "verification_scope": "Public hashes, downloaded model inference and Space HTTP response. Interactive browser checks are separate."}
