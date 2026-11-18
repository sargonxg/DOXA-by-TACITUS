#!/usr/bin/env python3
"""Exercise real Docker HTTP workflows under a 4 GiB / 2 CPU boundary.

This is a software/resource smoke test, not a physical 8 GB laptop usability,
interpretation-quality, inference-throughput or production-cloud benchmark.
Only this invocation's labelled disposable resources are removed. No existing
workspace, volume or container can be selected. Model weights are not installed.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MEMORY_BYTES = 4 * 1024**3
LABEL = "io.tacitus.doxa.verification"
HTTP = build_opener(ProxyHandler({}))


def run(*arguments, timeout=60):
    return subprocess.run(list(arguments), cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_identity():
    files = run("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    selected = []
    for name in sorted(set(files)):
        if (name in {"Dockerfile", ".dockerignore", "pyproject.toml", "setup.py", "infra/requirements-runtime.lock"}
                or name.startswith(("doxa/", "frontend/"))):
            path = ROOT / name
            if path.is_file() and not any(part in {"node_modules", "dist", "__pycache__"} for part in path.parts):
                selected.append({"file": name, "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
    return {"git_head": run("git", "rev-parse", "HEAD"),
            "working_tree_fingerprint_sha256": sha(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()),
            "files_checked": len(selected),
            "fingerprint_scope": "Runtime Python, packaged data, frontend source/assets, Docker recipes and locked Python dependencies; excludes documentation, benchmark receipts and generated caches"}


def docker_json(*arguments):
    return json.loads(run("docker", *arguments))


def inspect_resource(kind, identity):
    result = docker_json(kind, "inspect", identity)
    return result[0]


def request(base, path, *, body=None, headers=None, timeout=30):
    headers = dict(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with HTTP.open(Request(base + path, data=body, headers=headers), timeout=timeout) as response:
        raw = response.read(24 * 1024 * 1024 + 1)
        if len(raw) > 24 * 1024 * 1024:
            raise AssertionError("Verification HTTP response exceeded its bound")
        return response.status, {key.lower(): value for key, value in response.headers.items()}, raw


def json_request(base, path, **options):
    status, _headers, raw = request(base, path, **options)
    return status, json.loads(raw)


def multipart(files):
    boundary = "doxa-verification-" + uuid.uuid4().hex
    output = bytearray()
    for name, filename, content_type, raw in files:
        output.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode())
        output.extend(raw)
        output.extend(b"\r\n")
    output.extend(f"--{boundary}--\r\n".encode())
    return bytes(output), {"Content-Type": "multipart/form-data; boundary=" + boundary}


def fixture_pdf():
    """Make a tiny valid PDF without adding host-side dependencies."""
    stream = (b"BT /F1 12 Tf 60 760 Td (Fictional practice document for software verification.) Tj "
              b"0 -25 Td (Please review this draft before Friday.) Tj "
              b"0 -25 Td (Perhaps we can clarify the proposed deadline.) Tj ET")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    output, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(output)


def pdf_pages(container, raw):
    result = subprocess.run(["docker", "exec", "--interactive", container, "python", "-c",
        "import sys; from io import BytesIO; from pypdf import PdfReader; print(len(PdfReader(BytesIO(sys.stdin.buffer.read())).pages))"],
        cwd=ROOT, input=raw, capture_output=True, check=True, timeout=30)
    return int(result.stdout)


METRICS_CODE = r'''
import importlib.util,json,os,platform,shutil,sqlite3
from pathlib import Path

def read(path):
 try: return Path(path).read_text().strip()
 except OSError: return None

def pairs(path):
 raw=read(path)
 return {line.split()[0]:int(line.split()[1]) for line in raw.splitlines()} if raw else None

def count(table):
 exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
 return db.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0] if exists else 0

root=Path('/data'); seen=set(); size=0
for folder,dirs,files in os.walk(root):
 for name in files:
  p=Path(folder)/name
  if p.is_symlink(): continue
  info=p.stat(); key=(info.st_dev,info.st_ino)
  if key not in seen: seen.add(key); size+=info.st_size
with sqlite3.connect('file:/data/doxa.sqlite3?mode=ro',uri=True) as db:
 counts={table:count(table) for table in ('runs','v3_assessments','request_attempt_events','budget_reservations','report_jobs','activity_jobs')}
 usage=db.execute('SELECT COALESCE(SUM(spent_usd),0),COALESCE(SUM(reserved_usd),0) FROM budget_ledger').fetchone()
 print(json.dumps({'memory_max':read('/sys/fs/cgroup/memory.max'), 'memory_peak':read('/sys/fs/cgroup/memory.peak'),
  'memory_events':pairs('/sys/fs/cgroup/memory.events'), 'cpu_max':read('/sys/fs/cgroup/cpu.max'),'cpu_stat':pairs('/sys/fs/cgroup/cpu.stat'),
  'v1_memory_limit':read('/sys/fs/cgroup/memory/memory.limit_in_bytes'),'v1_memory_peak':read('/sys/fs/cgroup/memory/memory.max_usage_in_bytes'),
  'v1_memory_failcnt':read('/sys/fs/cgroup/memory/memory.failcnt'),
  'torch_installed':importlib.util.find_spec('torch') is not None,
  'pymupdf_installed':importlib.util.find_spec('pymupdf') is not None or importlib.util.find_spec('fitz') is not None,
  'data_logical_file_bytes_distinct_inodes':size,'data_filesystem_free_bytes':shutil.disk_usage(root).free,
  'database_counts':counts,'inference_spent_usd':usage[0],'inference_reserved_usd':usage[1],
  'container_python':platform.python_version(),'container_platform':platform.platform()}))
'''


def cleanup(kind, identity, nonce):
    try:
        info = inspect_resource(kind, identity)
    except subprocess.CalledProcessError:
        return
    labels = info.get("Config", {}).get("Labels", {}) if kind == "container" else info.get("Labels", {})
    if labels.get(LABEL) != nonce:
        raise RuntimeError("Refusing cleanup of a resource without this verification's ownership label")
    arguments = ["docker", kind, "rm"]
    if kind == "container":
        arguments.append("--force")
    run(*arguments, identity)


def execute(args):
    receipt = Path(args.receipt)
    if receipt.exists() and not args.replace_receipt:
        raise ValueError("Receipt already exists; use a new path or explicitly --replace-receipt")
    source = source_identity()
    nonce = uuid.uuid4().hex
    prefix = "doxa-v06-verify-" + nonce[:12]
    created = []
    result = None
    started = time.monotonic()
    host_free_before = shutil.disk_usage(ROOT).free
    if host_free_before < 3 * 1024**3:
        raise ValueError("At least 3 GiB free is required before this bounded Docker build/smoke; no resources created")
    build = {"requested": args.build, "ca_secret_used": False}
    try:
        if args.build:
            descriptor, log_name = tempfile.mkstemp(prefix=prefix + "-build-", suffix=".log")
            os.close(descriptor)
            command = ["docker", "build", "--progress=plain", "--tag", args.image,
                       "--label", "io.tacitus.doxa.source-sha256=" + source["working_tree_fingerprint_sha256"]]
            ca = args.build_ca or os.environ.get("SSL_CERT_FILE")
            if ca:
                if not Path(ca).is_file():
                    raise ValueError("The selected BuildKit CA bundle does not exist")
                command += ["--secret", "id=doxa_build_ca,src=" + str(Path(ca).resolve())]
                build["ca_secret_used"] = True
            command.append(".")
            print(json.dumps({"stage": "building", "image": args.image, "log": log_name}), flush=True)
            build_started = time.monotonic()
            with open(log_name, "w") as log:
                subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
            build.update(seconds=round(time.monotonic() - build_started, 6), log_sha256=sha(Path(log_name).read_bytes()))
        image = inspect_resource("image", args.image)
        if source_identity()["working_tree_fingerprint_sha256"] != source["working_tree_fingerprint_sha256"]:
            raise RuntimeError("Runtime source changed during the image build; rerun against a frozen tree")
        run("docker", "network", "create", "--internal", "--label", f"{LABEL}={nonce}", prefix)
        created.append(("network", prefix))
        run("docker", "volume", "create", "--label", f"{LABEL}={nonce}", prefix)
        created.append(("volume", prefix))
        # Record ownership before starting: even a failed docker run may leave
        # a labelled stopped container that must be cleaned up.
        created.append(("container", prefix))
        container = run("docker", "run", "--detach", "--name", prefix, "--label", f"{LABEL}={nonce}",
            "--network", prefix, "--memory", "4g", "--memory-swap", "4g", "--cpus", "2", "--pids-limit", "256",
            "--publish", "127.0.0.1::8000", "--volume", prefix + ":/data",
            "--env", "DOXA_LOCAL_PROFILE=low-memory", "--env", "DOXA_ENABLE_OCR=0",
            "--env", "DOXA_REQUIRE_ACCEPTED_QUOTE=false", "--env", "DOXA_AUTOMATIC_RETENTION=false",
            "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1", "--env", "PYTHON_DOTENV_DISABLED=1", args.image)
        endpoint = run("docker", "port", container, "8000/tcp").splitlines()[0]
        base = "http://" + endpoint
        print(json.dumps({"stage": "http_smoke", "memory_bytes": MEMORY_BYTES, "cpus": 2}), flush=True)
        for _attempt in range(120):
            try:
                _status, health = json_request(base, "/api/health", timeout=2)
                break
            except (OSError, URLError):
                state = inspect_resource("container", container)["State"]
                if not state["Running"]:
                    raise RuntimeError("Verification container stopped before HTTP health became ready") from None
                time.sleep(0.5)
        else:
            raise RuntimeError("Verification HTTP health did not become ready")
        _status, readiness = json_request(base, "/api/system/readiness")
        assert readiness["ok"] and readiness["api_ui_version_match"] is True
        assert readiness["profile"]["application_budget_mib"] == 4096
        assert readiness["effective_execution"]["parser_slots"] == 1
        assert not readiness["cpu_dependencies"]["torch"]
        _status, example = json_request(base, "/api/system/recorded-example")
        from doxa.recorded_starter import validate_example
        example_counts = validate_example(example)
        assert example_counts["documents"] == 20 and example_counts["assessments"] == 645
        assert set(example_counts["outcomes"]) == {"meets_criterion", "does_not_meet", "not_applicable", "uncertain"}
        assert example["fresh_inference_requests"] == 0 and example["interpretation_accuracy"] is None
        _status, _headers, recorded_pdf = request(base, "/api/system/recorded-example/artifacts/pdf")
        assert recorded_pdf.startswith(b"%PDF")
        assert pdf_pages(container, recorded_pdf) >= 2
        _status, project = json_request(base, "/api/projects", body={"name": "Disposable low-memory software verification"})
        project_id = project["id"]
        text = b"Fictional practice writing for software verification.\nPlease review this by Friday. Perhaps we can clarify the deadline.\n"
        pdf = fixture_pdf()
        upload, headers = multipart([("files", "practice.txt", "text/plain", text), ("files", "practice.pdf", "application/pdf", pdf)])
        _status, documents = json_request(base, f"/api/projects/{project_id}/upload", body=upload, headers=headers)
        assert len(documents) == 2 and all(doc["status"] == "ready" for doc in documents)
        sources = []
        for doc in documents:
            _status, context = json_request(base, f"/api/v3/documents/{doc['id']}/context")
            assert any("Please review" in block["text"] for block in context["blocks"])
            sources.append({"id": doc["id"], "filename": doc["filename"], "sha256": doc["sha256"],
                            "extractor": doc.get("metadata", {}).get("extractor"), "word_count": doc["word_count"]})
        pdf_source = next(doc for doc in sources if doc["filename"].endswith(".pdf"))
        assert "pdfminer" in str(pdf_source["extractor"]) and "pymupdf" not in str(pdf_source["extractor"])
        _status, brief = json_request(base, f"/api/pilot/projects/{project_id}/brief",
            body=None)
        # The explicit PUT retains the same API used by the browser.
        body = json.dumps({"profile_id": "personal", "expected_revision": brief["revision"]}).encode()
        req = Request(base + f"/api/pilot/projects/{project_id}/brief", data=body,
                      headers={"Content-Type": "application/json"}, method="PUT")
        with HTTP.open(req, timeout=30) as response:
            assert response.status == 200
        body = {"kind": "guided_report", "document_ids": [doc["id"] for doc in documents],
                "formats": ["pdf", "html", "xlsx", "json"], "expert_commentary": [], "review_ack": False}
        headers = {"Idempotency-Key": nonce}
        status, job = json_request(base, f"/api/v3/projects/{project_id}/jobs", body=body, headers=headers)
        assert status == 202
        _status, repeated = json_request(base, f"/api/v3/projects/{project_id}/jobs", body=body, headers=headers)
        assert repeated["id"] == job["id"]
        minimum_free = shutil.disk_usage(ROOT).free
        for _attempt in range(240):
            _status, job = json_request(base, f"/api/v3/jobs/{job['id']}")
            minimum_free = min(minimum_free, shutil.disk_usage(ROOT).free)
            if job["status"] == "completed":
                break
            if job["status"] in {"failed", "partial", "cancelled", "paused_access"}:
                raise RuntimeError("Guided report did not complete: " + json.dumps(job.get("error")))
            time.sleep(0.5)
        else:
            raise RuntimeError("Guided report did not finish within this smoke test's 120-second window")
        downloads = []
        assert {item["format"] for item in job["result"]["artifacts"]} == {"pdf", "html", "xlsx", "json"}
        for item in job["result"]["artifacts"]:
            _status, artifact_headers, raw = request(base, item["download_url"])
            assert len(raw) == item["bytes"] and sha(raw) == item["sha256"]
            assert artifact_headers.get("etag") == '"' + item["sha256"] + '"'
            if item["format"] == "pdf":
                assert raw.startswith(b"%PDF") and pdf_pages(container, raw) >= 2
            elif item["format"] == "xlsx":
                with zipfile.ZipFile(BytesIO(raw)) as archive:
                    assert archive.testzip() is None and "xl/workbook.xml" in archive.namelist()
            elif item["format"] == "json":
                report = json.loads(raw)
                assert report["report_brief"]["brief"]["profile_id"] == "personal" and len(report["documents"]) == 2
            downloads.append({"format": item["format"], "bytes": len(raw), "sha256": sha(raw)})
        metrics = json.loads(run("docker", "exec", container, "python", "-c", METRICS_CODE))
        info = inspect_resource("container", container)
        memory_max = int(metrics["memory_max"] or metrics["v1_memory_limit"])
        memory_peak = int(metrics["memory_peak"] or metrics["v1_memory_peak"])
        assert memory_max == MEMORY_BYTES and memory_peak < MEMORY_BYTES
        if metrics["memory_events"]:
            assert metrics["memory_events"].get("oom", 0) == metrics["memory_events"].get("oom_kill", 0) == 0
        else:
            assert int(metrics["v1_memory_failcnt"]) == 0
        assert not info["State"]["OOMKilled"]
        assert not metrics["torch_installed"] and not metrics["pymupdf_installed"]
        assert all(metrics["database_counts"][name] == 0 for name in ("runs", "v3_assessments", "request_attempt_events", "budget_reservations"))
        assert metrics["database_counts"]["activity_jobs"] == 1 and metrics["database_counts"]["report_jobs"] == 4
        assert metrics["inference_spent_usd"] == metrics["inference_reserved_usd"] == 0
        after = source_identity()
        assert after["working_tree_fingerprint_sha256"] == source["working_tree_fingerprint_sha256"], "Runtime source changed during verification"
        result = {"schema_version": "doxa-low-memory-smoke-1", "status": "pass", "source": source,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scope": "Real HTTP software workflow in a 4 GiB / 2 CPU Docker container; not a physical 8 GB laptop test",
            "physical_8gb_laptop_validated": False, "interpretation_accuracy": None,
            "fresh_inference_requests": 0, "independent_human_reference_labels": False,
            "image": {"tag": args.image, "id": image["Id"], "bytes": image["Size"]}, "build": build,
            "container": {"memory_bytes": info["HostConfig"]["Memory"], "memory_swap_bytes": info["HostConfig"]["MemorySwap"],
                          "nano_cpus": info["HostConfig"]["NanoCpus"], "pids_limit": info["HostConfig"]["PidsLimit"],
                          "user": info["Config"]["User"], "internal_network": True, "host_loopback_http": True},
            "host": {"platform": platform.platform(), "python": platform.python_version(), "logical_cpus": os.cpu_count(),
                     "physical_memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")},
            "verification_script_sha256": sha(Path(__file__).read_bytes()),
            "checks": ["Compiled browser UI and matching API version", "Actual HTTP readiness without models or downloads",
                "Twenty real recorded sources and all four retained outcomes validated", "Packaged recorded PDF downloaded and parsed",
                "Own synthetic TXT and PDF extracted through shared intake", "Four-format durable guided report completed",
                "Every artifact downloaded with matching size/SHA256/ETag", "Repeated submission retained one activity and four child jobs",
                "No PyTorch or PyMuPDF installed; no fresh run/provider attempt/reservation/spend", "Cgroup memory peak below 4 GiB and zero OOM events"],
            "readiness": {"version": readiness["version"], "frontend_version": readiness["frontend_version"],
                          "profile": readiness["profile"], "effective_execution": readiness["effective_execution"]},
            "recorded_example": {"counts": example_counts, "pdf_bytes": len(recorded_pdf), "pdf_sha256": sha(recorded_pdf)},
            "practice_sources": sources, "downloads": downloads, "metrics": metrics,
            "storage": {"host_free_before_build_bytes": host_free_before, "minimum_host_free_during_http_smoke_bytes": minimum_free,
                        "measurement": "Filesystem free-space observations, separately from distinct-inode logical /data file sizes"},
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "notes": ["Network is isolated from external egress; the browser/API is reached through its loopback published port.",
                      "Real-source examples contain historical model output, not new inference or independent human labels.",
                      "Practice report uses fictional inputs and wording observations; it does not validate bias judgments.",
                      "No OCR, local contextual model, GPU or full-corpus workload is qualified by this small smoke test."]}
    finally:
        errors = []
        for kind, identity in reversed(created):
            try:
                cleanup(kind, identity, nonce)
            except Exception as error:
                errors.append(type(error).__name__)
        if errors:
            raise RuntimeError("Owned-resource cleanup failed: " + ",".join(errors))
    result["cleanup"] = {"own_container_removed": True, "own_volume_removed": True,
                         "own_network_removed": True, "image_retained": True,
                         "other_resources_modified": False}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_name(receipt.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(receipt)
    print(json.dumps({"status": "pass", "receipt": str(receipt), "elapsed_seconds": result["elapsed_seconds"],
                      "memory_peak_bytes": result["metrics"]["memory_peak"] or result["metrics"]["v1_memory_peak"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Create only labelled disposable local Docker resources")
    image_mode = parser.add_mutually_exclusive_group()
    image_mode.add_argument("--build", action="store_true", help="Build the selected local image before verification")
    image_mode.add_argument("--skip-build", dest="build", action="store_false", help="Verify an image already built from this checkout, as in CI")
    parser.set_defaults(build=False)
    parser.add_argument("--build-ca", help="Optional organizational CA bundle passed only as a temporary BuildKit secret")
    parser.add_argument("--image", default="doxa:v06")
    parser.add_argument("--receipt", default="benchmarks/v06-low-memory-smoke.json")
    parser.add_argument("--replace-receipt", action="store_true", help="Explicitly replace a previous receipt after a new successful verification")
    parser.add_argument("--fingerprint-only", action="store_true")
    args = parser.parse_args()
    if args.fingerprint_only:
        print(json.dumps(source_identity(), indent=2))
    elif not args.execute:
        print(json.dumps({"status": "not_executed", "image": args.image, "memory_bytes": MEMORY_BYTES,
                          "cpus": 2, "network": "disposable internal bridge; no external egress",
                          "scope": "Small HTTP software smoke; no model inference or physical 8 GB laptop qualification"}, indent=2))
    else:
        execute(args)


if __name__ == "__main__":
    main()
