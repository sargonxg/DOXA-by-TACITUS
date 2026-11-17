#!/usr/bin/env python3
"""Explicit bounded CPU feasibility study, never a startup model download.

Uses six whole articles from the existing frozen 20-news sample. Two explicit
Q4 profiles run sequentially with identical source units/rubrics. No accuracy
claim or automatic model promotion is made. No original Store is opened.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doxa.contextual import analyze_units, build_units, PROMPT_VERSION  # noqa: E402
from doxa.lineage import content_sha256  # noqa: E402
from doxa.method_config import method_config, new_run_session  # noqa: E402
from doxa.providers import OllamaProvider  # noqa: E402
from scripts.benchmark_v3 import source_selection  # noqa: E402

LENSES = ["discourse", "ihra", "jda", "nexus", "anti-muslim-hostility", "anti-muslim-racism"]
MODELS = ["qwen3.5:2b-q4_K_M", "qwen3.5:4b"]
PINS = {"qwen3.5:2b-q4_K_M": "1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7",
        "qwen3.5:4b": "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04"}


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def resource_sample():
    pids, rss = [], 0
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = (path / "cmdline").read_bytes()
            own = int(path.name) == os.getpid()
            ollama = args.startswith(b"/tmp/doxa-ollama/bin/ollama\0") or b"/tmp/doxa-ollama/lib/ollama/" in args.split(b"\0")[0]
            if not (own or ollama):
                continue
            fields = (path / "statm").read_text().split()
            amount = int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
            rss += amount
            pids.append({"pid": int(path.name), "role": "driver" if own else "ollama", "rss_bytes": amount})
        except (OSError, ValueError, IndexError):
            pass
    environment = {"load_average": list(os.getloadavg()), "exclusive_cpu_reservation": False}
    for name in ("cpu.max", "cpu.stat", "cpuset.cpus.effective", "memory.current", "memory.max"):
        try:
            environment[name] = (Path("/sys/fs/cgroup") / name).read_text().strip()
        except OSError:
            environment[name] = "not_recorded"
    return {"combined_rss_bytes": rss, "processes": pids, "shared_environment": environment}


async def main(args):
    import httpx
    if args.minutes <= 0 or args.minutes > 90:
        raise ValueError("Each profile must have a positive deadline no greater than 90 minutes")
    os.environ["DOXA_OLLAMA_NUM_THREADS"] = "4"
    selection = source_selection("news-sample")
    sources = [json.loads((ROOT / member["path"]).read_text()) for member in selection["sources"]]
    sources.sort(key=lambda source: (source["word_count"], source["id"]))
    sources = sources[:6]
    for source in sources:
        if content_sha256(source["blocks"]) != source["content_sha256"]:
            raise ValueError("Frozen source hash mismatch")
    documents = [{**source, "sha256": source["content_sha256"],
        "source_version_id": hashlib.sha256(f"{source['id']}:{source['content_sha256']}".encode()).hexdigest()[:32]} for source in sources]
    units = {document["id"]: build_units(document, max_unit_chars=1200, context_sentences=2) for document in documents}
    summary = {"schema_version": "0.4", "kind": "bounded_cpu_profile_feasibility", "status": "running",
        "selection_policy": "Six shortest whole articles from the frozen equal-position 20-news sample; purposive runtime feasibility, not representative interpretation quality.",
        "parent_selection_sha256": content_sha256(selection), "passage_sampling": False,
        "selected_articles": 6, "source_words": sum(source["word_count"] for source in sources),
        "selected_source_units": sum(map(len, units.values())), "sources": [
            {"source_id": source["id"], "source_hash": source["content_sha256"], "title": source["title"],
             "words": source["word_count"], "units": len(units[source["id"]])} for source in sources],
        "policy": {"threads": 4, "concurrent_requests": 1, "context_tokens": 8192,
                   "max_minutes_per_model": args.minutes, "combined_rss_stop_bytes": 12 * 1024 ** 3,
                   "sample_interval_seconds": 2, "sequential_models": True, "unit_max_chars": 1200,
                   "context_sentences": 2, "max_unit_retries": 0, "lens_ids": LENSES},
        "interpretation_accuracy": None, "quality_status": "reference_pending", "auto_promotion": False,
        "profiles": [], "started_at": time.time()}
    if args.resume:
        prior = json.loads(args.output.read_text())
        for key in ("sources", "parent_selection_sha256", "selected_source_units"):
            if prior.get(key) != summary[key]:
                raise ValueError("Frozen study cohort changed; refusing resume")
        summary = prior
        summary["status"] = "running"
        summary.setdefault("execution_amendments", []).append({"at": time.time(),
            "max_minutes_per_model": args.minutes, "scope": "operating cap only; source units, lenses, prompts and weights unchanged"})
    atomic(args.output, summary)
    async with httpx.AsyncClient(timeout=30) as client:
        runtime = await client.get("http://127.0.0.1:11434/api/version")
        summary["ollama_version"] = runtime.json().get("version")
        upstream = {}
        for size in ("2B", "4B"):
            response = await client.get(f"https://huggingface.co/api/models/Qwen/Qwen3.5-{size}")
            response.raise_for_status()
            card = response.json()
            upstream[size] = {"repository": card["id"], "revision": card["sha"],
                              "license": (card.get("cardData") or {}).get("license", "unresolved")}
        for model in args.models:
            if any(profile["model"] == model for profile in summary["profiles"]):
                raise ValueError("This model already has a receipt; no implicit repeat or replacement")
            config = {"provider": "ollama", "model": model, "method_config_version": "0.4",
                "lens_ids": LENSES, "unit_max_chars": 1200, "context_sentences": 2,
                "question": "Apply the selected criteria in context, distinguishing attribution, counter-speech, uncertainty and competing frameworks.",
                "persona": "research", "budget_usd": 0, "max_unit_retries": 0,
                "request_journal_mode": "metadata", "source_metadata_hash": content_sha256([{key: source.get(key) for key in ("id", "title", "author", "institution", "document_type", "published_at")} for source in sources])}
            provider = OllamaProvider(config)
            health = await provider.health()
            if not health.get("ok") or health.get("quantization") != "Q4_K_M" or health.get("revision") != PINS[model]:
                raise ValueError(f"Selected Q4 model unavailable: {model}")
            config["model_revision"] = health["revision"]
            receipt = {"model": model, "model_profile": health, "method": method_config(config).as_dict(),
                "method_fingerprint": method_config(config).fingerprint, "session": new_run_session(config).as_dict(),
                "prompt_version": PROMPT_VERSION, "status": "running", "articles": [], "accepted_units": 0,
                "operating_cap_minutes": args.minutes,
                "selected_units": summary["selected_source_units"], "complete_articles": 0,
                "requests": 0, "started_at": time.time(), "peak_sampled_combined_rss_bytes": 0,
                "upstream_reference": upstream["2B" if ":2b" in model else "4B"],
                "upstream_model_card": f"https://huggingface.co/Qwen/Qwen3.5-{'2B' if ':2b' in model else '4B'}",
                "conversion_relation": "Ollama quantized package; manifest digest is the runtime pin, not a proof of upstream conversion parity."}
            summary["profiles"].append(receipt)
            deadline = time.monotonic() + args.minutes * 60
            stop, stop_reason = asyncio.Event(), []
            loop = asyncio.get_running_loop()

            def interrupt():
                stop_reason.append("operator_interrupt")
                stop.set()

            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, interrupt)

            async def guard():
                while not stop.is_set():
                    sample = resource_sample()
                    if sample["combined_rss_bytes"] > receipt["peak_sampled_combined_rss_bytes"]:
                        receipt["peak_sampled_combined_rss_bytes"] = sample["combined_rss_bytes"]
                        receipt["peak_sample"] = sample
                    if sample["combined_rss_bytes"] >= 12 * 1024 ** 3:
                        stop_reason.append("combined_rss_limit")
                        stop.set()
                    if time.monotonic() >= deadline:
                        stop_reason.append("profile_wall_deadline")
                        stop.set()
                    atomic(args.output, summary)
                    await asyncio.sleep(2)

            watcher = asyncio.create_task(guard())
            try:
                for document in documents:
                    if stop.is_set():
                        break
                    article_started = time.monotonic()
                    article = {"source_id": document["id"], "selected_units": len(units[document["id"]]),
                               "accepted_units": 0, "status": "running", "errors": [], "unit_results": []}
                    receipt["articles"].append(article)
                    for unit in units[document["id"]]:
                        if stop.is_set():
                            break
                        task = asyncio.create_task(analyze_units(document, LENSES, provider, units=[unit], config=config))
                        halted = asyncio.create_task(stop.wait())
                        done, _ = await asyncio.wait([task, halted], return_when=asyncio.FIRST_COMPLETED)
                        if halted in done and not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            break
                        halted.cancel()
                        result = await task
                        article["unit_results"].append(result)
                        receipt["requests"] += result["usage"]["requests"]
                        article["accepted_units"] += result["coverage"]["accepted_units"]
                        article["errors"].extend(result["errors"])
                        receipt["accepted_units"] += result["coverage"]["accepted_units"]
                        article["elapsed_seconds"] = round(time.monotonic() - article_started, 3)
                        atomic(args.output, summary)
                    article["status"] = "completed" if article["accepted_units"] == article["selected_units"] else "partial"
                    article["elapsed_seconds"] = round(time.monotonic() - article_started, 3)
                    receipt["complete_articles"] += article["status"] == "completed"
                    atomic(args.output, summary)
                receipt["status"] = "completed" if receipt["accepted_units"] == receipt["selected_units"] else "partial"
                receipt["stop_reason"] = stop_reason[0] if stop_reason else "selected_work_finished_with_validation_failures" if receipt["status"] == "partial" else "selected_work_finished"
            finally:
                stop.set()
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                receipt["elapsed_seconds"] = round(time.time() - receipt["started_at"], 3)
                ps = (await client.get("http://127.0.0.1:11434/api/ps")).json()
                receipt["runtime_ps"] = ps
                if any(item.get("size_vram", 0) for item in ps.get("models", [])):
                    receipt["gpu_guard_failed"] = True
                await client.post("http://127.0.0.1:11434/api/generate", json={"model": model, "keep_alive": 0})
                # Persist complete results separately; summary remains inspectable.
                details = args.output.parent / ("v04-cpu-" + ("2b" if ":2b" in model else "4b") + "-results.json.gz")
                with gzip.open(details, "wt", encoding="utf-8") as stream:
                    json.dump(receipt, stream, ensure_ascii=False, allow_nan=False)
                receipt["results_path"] = str(details.relative_to(ROOT))
                receipt["results_sha256"] = hashlib.sha256(details.read_bytes()).hexdigest()
                for article in receipt["articles"]:
                    results = article.pop("unit_results")
                    article["accepted_assessments"] = sum(len(result["assessments"]) for result in results)
                    article["attempt_journal_entries"] = sum(len(result["attempt_journal"]) for result in results)
                    article["error_codes"] = sorted({error["code"] for error in article.pop("errors")})
                atomic(args.output, summary)
    summary["status"] = "completed" if all(profile["status"] == "completed" for profile in summary["profiles"]) else "partial"
    summary["finished_at"] = time.time()
    atomic(args.output, summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--models", nargs="+", choices=MODELS, default=MODELS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "v04-cpu-study.json")
    asyncio.run(main(parser.parse_args()))
