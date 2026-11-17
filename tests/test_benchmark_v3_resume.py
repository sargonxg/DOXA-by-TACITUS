"""Resume compatibility gates stop before inference or application creation."""
import importlib.util
import json
from pathlib import Path

import pytest

from doxa.lenses import get_lens

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("benchmark_v3_resume_fixture", ROOT / "scripts" / "benchmark_v3.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.fixture
async def frozen(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    source_root = tmp_path / "source"
    for name in ("contextual.py", "models.py", "lenses.py"):
        path = source_root / "doxa" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Synthetic frozen file: {name}\n")
    monkeypatch.setattr(benchmark, "ROOT", source_root)
    selection = {"collection": "institutions", "documents": 1, "manifest_sha256": "frozen-source-manifest",
                 "sources": [{"source_id": "synthetic-source", "content_sha256": "frozen-source-hash"}]}
    monkeypatch.setattr(benchmark, "source_selection", lambda *_args: selection)

    class UnavailableProvider:
        health_calls = 0

        async def health(self):
            self.health_calls += 1
            return {"ok": False, "revision": "synthetic-fixed-model", "details": "Offline test: no inference"}

        async def generate(self, *_args, **_kwargs):
            pytest.fail("Resume-signature tests must never call inference")

    provider = UnavailableProvider()

    async def get_provider(_config):
        return provider

    monkeypatch.setattr("doxa.providers.get_provider", get_provider)
    options = benchmark.parse_args([
        "--collection", "institutions", "--provider", "cloudflare", "--lens-ids", "discourse",
        "--data-dir", str(tmp_path / "data"), "--output", str(tmp_path / "result.json"),
    ])
    assert await benchmark.run_benchmark(options) == 2
    checkpoint = options.output.with_suffix(options.output.suffix + ".checkpoint.json")
    baseline = json.loads(checkpoint.read_text())
    options.resume = True
    return options, source_root, selection, provider, checkpoint, baseline


@pytest.mark.asyncio
async def test_unselected_lens_extension_keeps_frozen_signature_and_reaches_preflight(frozen):
    options, source_root, _selection, provider, checkpoint, baseline = frozen
    # Runtime selected packs stay identical; only the overall lens-file digest changes.
    (source_root / "doxa" / "lenses.py").write_text("# Synthetic additive, unselected lens extension\n")
    options.budget_usd = 20
    options.concurrency = 8
    assert await benchmark.run_benchmark(options) == 2
    resumed = json.loads(checkpoint.read_text())
    assert provider.health_calls == 2
    assert resumed["signature"] == baseline["signature"]
    assert resumed["engine_fingerprint"] == baseline["engine_fingerprint"]
    assert len(resumed["compatible_extensions"]) == 1
    assert resumed["run_id"] is None
    assert not (options.data_dir / "doxa.sqlite3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["engine", "schema", "selected_rubric", "question", "source", "compatibility_receipt"])
async def test_incompatible_change_cannot_use_unselected_lens_exception(frozen, monkeypatch, changed):
    options, source_root, selection, provider, checkpoint, baseline = frozen
    (source_root / "doxa" / "lenses.py").write_text("# Synthetic new lens-file version\n")
    if changed == "engine":
        (source_root / "doxa" / "contextual.py").write_text("# Changed core analysis\n")
    elif changed == "schema":
        (source_root / "doxa" / "models.py").write_text("# Changed response schema\n")
    elif changed == "selected_rubric":
        def changed_lens(identity):
            pack = get_lens(identity)
            pack["criteria"][0]["description"] += " Synthetic changed selected criterion."
            return pack
        monkeypatch.setattr("doxa.lenses.get_lens", changed_lens)
    elif changed == "question":
        options.question += " Synthetic changed analytical inquiry."
    elif changed == "source":
        selection["sources"][0]["content_sha256"] = "different-source-hash"
    else:
        receipt_path = options.data_dir / "frozen-engine-compatibility.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["engine_fingerprint"] = "unrelated-previous-engine"
        receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="Analytical configuration, source manifest, engine version or data-dir changed"):
        await benchmark.run_benchmark(options)
    assert provider.health_calls == 1, "Reject changed analysis before even a provider preflight"
    assert json.loads(checkpoint.read_text())["signature"] == baseline["signature"]
    assert not (options.data_dir / "doxa.sqlite3").exists()
