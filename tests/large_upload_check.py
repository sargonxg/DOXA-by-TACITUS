#!/usr/bin/env python3
"""Manual 500 MiB resumable-upload acceptance check; not a routine CI test.

The source is a synthetic, valid one-page PDF with an unreferenced padding
stream. This measures transport/persistence/extraction bounds, not OCR or AI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
from doxa.api import create_app  # noqa: E402

SIZE = 500 * 1024 * 1024
RSS_LIMIT = 1024 * 1024 * 1024
MARKER = "DOXA large upload transport fixture. Synthetic source, one actual PDF page."


def sha_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def generate_pdf(path: Path) -> str:
    """Write a valid PDF directly to disk, including explicit xref offsets."""
    offsets = [0]
    with path.open("wb") as output:
        output.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        content = f"BT /F1 12 Tf 50 730 Td ({MARKER}) Tj ET\n".encode()
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        for number, body in enumerate(objects, 1):
            offsets.append(output.tell())
            output.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
        offsets.append(output.tell())
        base = output.tell()
        tail = b"\nendstream\nendobj\n"
        length = SIZE - base - 1000
        for _ in range(12):
            prefix = f"6 0 obj\n<< /Length {length} >>\nstream\n".encode()
            xref_start = base + len(prefix) + length + len(tail)
            suffix = b"xref\n0 7\n0000000000 65535 f \n"
            suffix += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
            suffix += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
            revised = SIZE - base - len(prefix) - len(tail) - len(suffix)
            if revised == length:
                break
            length = revised
        else:
            raise RuntimeError("Could not stabilize PDF byte layout.")
        output.write(prefix)
        block = b"0" * (1024 * 1024)
        remaining = length
        while remaining:
            chunk = block[:min(len(block), remaining)]
            output.write(chunk)
            remaining -= len(chunk)
        output.write(tail)
        if output.tell() != xref_start:
            raise RuntimeError("PDF cross-reference offset mismatch.")
        output.write(suffix)
    if path.stat().st_size != SIZE:
        raise RuntimeError("PDF did not meet the exact 500 MiB fixture size.")
    return sha_file(path)


def rss(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def children(pid: int) -> list[int]:
    found = []
    for candidate in Path("/proc").iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            for line in (candidate / "status").read_text().splitlines():
                if line.startswith("PPid:") and int(line.split()[1]) == pid:
                    found.append(int(candidate.name))
                    break
        except (OSError, ValueError):
            pass
    return found


class MemoryGuard:
    def __init__(self):
        self.stop = threading.Event()
        self.exceeded = threading.Event()
        self.parent_peak = 0
        self.child_peak = 0
        self.combined_peak = 0
        self.thread = threading.Thread(target=self.monitor, daemon=True)

    def monitor(self):
        while not self.stop.wait(0.025):
            parent = rss(os.getpid())
            parser_pids = children(os.getpid())
            child = sum(rss(pid) for pid in parser_pids)
            self.parent_peak = max(self.parent_peak, parent)
            self.child_peak = max(self.child_peak, child)
            self.combined_peak = max(self.combined_peak, parent + child)
            if parent + child > RSS_LIMIT:
                self.exceeded.set()
                for pid in parser_pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def check(self):
        if self.exceeded.is_set():
            raise MemoryError("Parent plus parser RSS exceeded the 1 GiB test guard.")

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=2)
        scale = 1 if sys.platform == "darwin" else 1024
        self.parent_peak = max(self.parent_peak, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale)
        self.child_peak = max(self.child_peak, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale)


def expect(response, code=200):
    if response.status_code != code:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def stream_part(path: Path, offset: int, length: int):
    with path.open("rb") as source:
        source.seek(offset)
        remaining = length
        while remaining:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                raise RuntimeError("Fixture ended inside a declared part.")
            yield chunk
            remaining -= len(chunk)


def send_part(client, upload_id: str, number: int, part_size: int, source: Path, guard: MemoryGuard):
    guard.check()
    offset = (number - 1) * part_size
    length = min(part_size, SIZE - offset)
    expected = hashlib.sha256()
    for chunk in stream_part(source, offset, length):
        expected.update(chunk)
    response = client.put(f"/api/uploads/{upload_id}/parts/{number}",
                          content=stream_part(source, offset, length),
                          headers={"content-type": "application/octet-stream", "content-length": str(length)})
    value = expect(response)
    if value != {"partNumber": number, "etag": expected.hexdigest()}:
        raise RuntimeError("Part acknowledgement did not match the streamed source checksum.")
    del response
    gc.collect()
    guard.check()
    return value


@contextmanager
def client_for(data_dir: Path):
    with TestClient(create_app(data_dir, start_workers=False, use_saved_showcase=False)) as client:
        yield client


def perform(output: Path) -> int:
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_ENABLE_OCR"):
        os.environ.pop(name, None)
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < 4 * SIZE:
        raise RuntimeError("At least 2 GiB of free temporary disk is required; fixture was not created.")
    guard = MemoryGuard()
    guard.thread.start()
    started = time.perf_counter()
    report = {
        "schema_version": 1, "benchmark_kind": "synthetic_large_upload_transport",
        "synthetic": True, "fixture": "Valid one-page PDF with an unreferenced 500 MiB padding stream.",
        "file_bytes": SIZE, "rss_guard_bytes": RSS_LIMIT, "disk_free_before_bytes": free,
        "hardware": {"platform": platform.platform(), "python": platform.python_version(), "logical_cpus": os.cpu_count()},
        "checks": {}, "limitations": [
            "This synthetic padded PDF tests byte transport, persisted parts, assembly, duplicate handling and one selectable-text PDF page.",
            "It does not measure 500 MiB of meaningful document content, OCR, model inference, or semantic quality.",
            "No whole-source download response is buffered in memory. Each request body is at most 8 MiB.",
        ],
    }
    temporary = None
    try:
        with tempfile.TemporaryDirectory(prefix="doxa-large-upload-") as folder:
            temporary = Path(folder)
            source, data_dir = temporary / "large-fixture.pdf", temporary / "state"
            generation = time.perf_counter()
            source_hash = generate_pdf(source)
            report["generation_seconds"] = time.perf_counter() - generation
            report["source_sha256"] = source_hash
            guard.check()
            parts = []
            with client_for(data_dir) as client:
                project = expect(client.post("/api/projects", json={"name": "Synthetic 500 MiB upload acceptance"}), 201)
                project_id = project["id"]
                upload = expect(client.post(f"/api/projects/{project_id}/uploads/init",
                                            json={"filename": source.name, "size": SIZE}))
                upload_id, part_size = upload["id"], upload["part_size"]
                count = (SIZE + part_size - 1) // part_size
                if part_size != 8 * 1024 * 1024 or count != 63:
                    raise RuntimeError("Unexpected multipart transport limits.")
                for number in range(1, 32):
                    parts.append(send_part(client, upload_id, number, part_size, source, guard))
                repeated = send_part(client, upload_id, 17, part_size, source, guard)
                report["checks"]["lost_ack_repeat_same_etag"] = repeated == parts[16]
                report["checks"]["partial_parts_before_restart"] = len(parts) == 31
            # Recreate both app and TestClient, preserving only disk state.
            with client_for(data_dir) as client:
                folder = data_dir / "uploads" / upload_id
                persisted = json.loads((folder / "meta.json").read_text())
                report["checks"]["upload_metadata_survived_restart"] = persisted["project_id"] == project_id and persisted["size"] == SIZE
                report["checks"]["part_etags_survived_restart"] = all(
                    (folder / f"part-{part['partNumber']}").stat().st_size == part_size
                    and (folder / f"etag-{part['partNumber']}").read_text() == part["etag"] for part in parts
                )
                for number in range(32, count + 1):
                    parts.append(send_part(client, upload_id, number, part_size, source, guard))
                completion = time.perf_counter()
                result = expect(client.post(f"/api/uploads/{upload_id}/complete", json={"parts": parts}))
                report["first_completion_seconds"] = time.perf_counter() - completion
                doc = result["document"]
                detail = expect(client.get(f"/api/documents/{doc['id']}"))
                excerpt = "\n".join(block["text"] for block in detail["blocks"])
                report["checks"]["ready_one_real_pdf_page"] = doc["status"] == "ready" and doc["page_count"] == 1
                report["checks"]["real_text_extracted"] = MARKER in excerpt
                report["checks"]["source_digest_matches"] = doc["sha256"] == source_hash
                original = client.app.state.store.object_path(doc["object_key"])
                report["checks"]["persisted_original_digest_matches"] = sha_file(original) == source_hash
                report["checks"]["completed_parts_removed"] = not folder.exists()
                report["extracted_excerpt"] = excerpt[:400]
                if doc["status"] != "ready":
                    raise RuntimeError(f"Large PDF extraction failed: {doc.get('error')}")
                # Re-upload all source bytes through the same bounded part protocol.
                duplicate = expect(client.post(f"/api/projects/{project_id}/uploads/init",
                                               json={"filename": "duplicate.pdf", "size": SIZE}))
                duplicate_parts = [send_part(client, duplicate["id"], number, part_size, source, guard)
                                   for number in range(1, count + 1)]
                repeated_doc = expect(client.post(f"/api/uploads/{duplicate['id']}/complete",
                                                 json={"parts": duplicate_parts}))
                docs = expect(client.get(f"/api/projects/{project_id}/documents"))
                report["checks"]["duplicate_import_reuses_document"] = repeated_doc["duplicate"] is True and repeated_doc["document"]["id"] == doc["id"] and len(docs) == 1
                report["checks"]["duplicate_keeps_original_intact"] = original.exists() and sha_file(original) == source_hash
                report["part_size_bytes"] = part_size
                report["parts_per_upload"] = count
                report["part_requests_total"] = count * 2 + 1
                report["transmitted_source_bytes"] = SIZE * 2 + part_size
                report["resume_after_part"] = 31
            guard.check()
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    finally:
        guard.finish()
        report["wall_seconds"] = time.perf_counter() - started
        report["memory"] = {
            "parent_peak_rss_bytes": guard.parent_peak, "parser_child_peak_rss_bytes": guard.child_peak,
            "sampled_combined_peak_rss_bytes": guard.combined_peak, "sampling_interval_seconds": 0.025,
            "guard_exceeded": guard.exceeded.is_set(),
            "note": "Kernel high-water counters supplement sampled parent/child RSS; unrelated benchmark/model processes are excluded.",
        }
        report["checks"]["rss_under_1_gib"] = not guard.exceeded.is_set() and guard.parent_peak < RSS_LIMIT and guard.child_peak < RSS_LIMIT
        report["checks"]["temporary_artifacts_removed"] = temporary is not None and not temporary.exists()
        report["passed"] = not report.get("error") and all(report["checks"].values())
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report.get(key) for key in ("passed", "wall_seconds", "checks", "memory", "error")}, indent=2), flush=True)
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "large-upload.json")
    args = parser.parse_args()
    try:
        return perform(args.output)
    except Exception as error:
        print(f"Large upload check did not start: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
