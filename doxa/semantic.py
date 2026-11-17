"""Persistent, bounded CPU passage retrieval with exact source anchors.

Vectors are normalized MiniLM embeddings, not prejudice or stance predictions.
The active manifest is published only after all selected document chunks exist.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import heapq
import io
import json
import threading
import time
from urllib.parse import quote

from .cpu_models import CPUModels
from .model_registry import get_model_profile
from .store import Store, utcnow

INDEX_VERSION = "semantic-passages-3.0"
MAX_DOCUMENTS = 10_000  # Allows the 5000-news baseline plus institutional case studies.
MAX_PASSAGES = 250_000
MAX_DOCUMENT_PASSAGES = 20_000
MAX_INDEX_BYTES = 512 * 1024 * 1024
CHUNK_PASSAGES = 256
MAX_CHUNK_BYTES = 8 * 1024 * 1024
_BUILD_LOCK = threading.Lock()


class SemanticIndexError(ValueError):
    pass


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


class SemanticIndex:
    def __init__(self, store: Store, runtime: CPUModels, *, cache_chunks: int = 4):
        if not 0 <= cache_chunks <= 16:
            raise ValueError("In-memory chunk cache must be 0–16 chunks")
        self.store, self.runtime = store, runtime
        self.profile = runtime.model_profile("minilm") if hasattr(runtime, "model_profile") else get_model_profile("minilm")
        self.cache_chunks = cache_chunks
        self._cache = OrderedDict()

    def _prefix(self, project_id: str) -> str:
        return "semantic/" + hashlib.sha256(project_id.encode()).hexdigest()[:32]

    def _optional_json(self, key: str):
        try:
            return json.loads(self._bounded_bytes(key, 16 * 1024 * 1024))
        except FileNotFoundError:
            return None
        except Exception as error:
            # The same artifact interface can use an R2 HTTP bridge.
            if getattr(getattr(error, "response", None), "status_code", None) == 404:
                return None
            raise

    def _bounded_bytes(self, key: str, limit: int) -> bytes:
        if not self.store.remote:
            path = self.store.object_path(key)
            if path.stat().st_size > limit:
                raise SemanticIndexError("Stored semantic artifact exceeds its byte limit")
            return path.read_bytes()
        import httpx
        result = bytearray()
        url = self.store.remote + "/internal/storage/objects/" + quote(key, safe="/")
        with httpx.stream("GET", url, headers=self.store.headers(), timeout=120) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(64 * 1024):
                if len(result) + len(chunk) > limit:
                    raise SemanticIndexError("Stored semantic artifact exceeds its byte limit")
                result.extend(chunk)
        return bytes(result)

    def _documents(self, project_id: str) -> list[dict]:
        if not self.store.one("SELECT id FROM projects WHERE id=?", [project_id]):
            raise SemanticIndexError("Project does not exist")
        rows = self.store.query("SELECT id,title,author,institution,sha256,blocks_key,source_url,license,published_at FROM documents WHERE project_id=? AND status='ready' AND blocks_key IS NOT NULL ORDER BY id LIMIT 10001", [project_id])
        if len(rows) > MAX_DOCUMENTS:
            raise SemanticIndexError("The baseline semantic index supports at most 10000 ready documents per project; split the project")
        return rows

    def _check_manifest(self, manifest: dict, project_id: str):
        if (manifest.get("index_version") != INDEX_VERSION or manifest.get("project_id") != project_id
                or manifest.get("model_revision") != self.profile["revision"]
                or manifest.get("model") != self.profile["model"]):
            raise SemanticIndexError("Index model/version differs from the selected profile; rebuild explicitly")
        if not isinstance(manifest.get("documents"), list) or len(manifest["documents"]) > MAX_DOCUMENTS:
            raise SemanticIndexError("Invalid index document manifest")

    @staticmethod
    def _blocks_digest(blocks: list[dict]) -> str:
        digest = hashlib.sha256()
        for block in blocks:
            digest.update(json.dumps({"id": block["id"], "text": block["text"]},
                                     ensure_ascii=False, sort_keys=True).encode())
            digest.update(b"\n")
        return digest.hexdigest()

    def _write_chunk(self, prefix: str, cache_id: str, number: int, anchors: list[dict], vectors: list[list[float]]) -> dict:
        import numpy as np
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.shape != (len(anchors), 384) or not np.isfinite(matrix).all():
            raise SemanticIndexError("Embedding output has invalid dimensions or nonfinite values")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if (norms == 0).any():
            raise SemanticIndexError("Embedding output contains a zero vector")
        matrix /= norms
        buffer = io.BytesIO()
        np.save(buffer, matrix, allow_pickle=False)
        vector_bytes = buffer.getvalue()
        anchor_bytes = json.dumps(anchors, ensure_ascii=False, separators=(",", ":")).encode()
        if max(len(vector_bytes), len(anchor_bytes)) > MAX_CHUNK_BYTES:
            raise SemanticIndexError("Semantic chunk exceeds its resource limit")
        base = f"{prefix}/{cache_id}/part-{number:05d}"
        vectors_key, anchors_key = base + ".npy", base + ".json"
        self.store.put_bytes(vectors_key, vector_bytes)
        self.store.put_bytes(anchors_key, anchor_bytes, "application/json")
        return {"vectors_key": vectors_key, "anchors_key": anchors_key, "count": len(anchors),
                "vectors_sha256": hashlib.sha256(vector_bytes).hexdigest(),
                "anchors_sha256": hashlib.sha256(anchor_bytes).hexdigest(),
                "bytes": len(vector_bytes) + len(anchor_bytes)}

    def _build_document(self, prefix: str, document: dict, blocks: list[dict], blocks_digest: str) -> tuple[dict, bool]:
        cache_id = _hash({"source_sha256": document["sha256"], "blocks_digest": blocks_digest,
                          "revision": self.profile["revision"], "version": INDEX_VERSION, "overlap": 32})
        doc_prefix = prefix + "/documents/" + hashlib.sha256(document["id"].encode()).hexdigest()[:32]
        cached = self._optional_json(doc_prefix + "/cache.json")
        if (cached and cached.get("cache_id") == cache_id and cached.get("document_id") == document["id"]
                and cached.get("passages", MAX_DOCUMENT_PASSAGES + 1) <= MAX_DOCUMENT_PASSAGES):
            try:
                for chunk in cached["chunks"]:
                    self._read_chunk(chunk, prefix)
                return cached, True
            except (SemanticIndexError, FileNotFoundError):
                # An explicit build repairs a changed/missing cached bundle.
                pass
        chunks, anchors, vectors = [], [], []
        count, total_bytes = 0, 0
        pieces = []
        piece_chars = 0
        nonindexable = []

        def flush_pieces():
            nonlocal anchors, vectors, count, total_bytes, piece_chars
            if not pieces:
                return
            result = self.runtime.embed_windows([piece["text"] for piece in pieces])
            for skipped in result.get("nonindexable_inputs", []):
                piece = pieces[skipped["input_index"]]
                nonindexable.append({"block_id": piece["block"]["id"], "start": piece["start"],
                                     "end": piece["start"] + len(piece["text"]), "reason": skipped["reason"]})
            for window in result["windows"]:
                piece = pieces[window["input_index"]]
                block = piece["block"]
                start, end = piece["start"] + window["start"], piece["start"] + window["end"]
                quote = block["text"][start:end]
                if quote != window["text"]:
                    raise SemanticIndexError("Embedding window does not match its exact source offsets")
                anchors.append({"id": _hash([document["id"], cache_id, block["id"], start, end])[:32],
                                "document_id": document["id"], "block_id": block["id"], "start": start,
                                "end": end, "quote": quote,
                                "locator": {key: block[key] for key in ("page", "paragraph") if key in block}})
                vectors.append(window["embedding"])
                count += 1
                if count > MAX_DOCUMENT_PASSAGES:
                    raise SemanticIndexError("A document exceeds 20000 semantic passages; split the source explicitly")
                if len(anchors) >= CHUNK_PASSAGES:
                    chunk = self._write_chunk(doc_prefix, cache_id, len(chunks), anchors, vectors)
                    chunks.append(chunk)
                    total_bytes += chunk["bytes"]
                    anchors, vectors = [], []
            pieces.clear()
            piece_chars = 0

        for block in blocks:
            text = block["text"]
            for start in range(0, len(text), 100_000):
                piece = text[start:start + 100_000]
                if piece.strip():
                    if piece_chars + len(piece) > 128_000:
                        flush_pieces()
                    pieces.append({"block": block, "start": start, "text": piece})
                    piece_chars += len(piece)
                if len(pieces) >= 64:
                    flush_pieces()
        flush_pieces()
        if anchors:
            chunk = self._write_chunk(doc_prefix, cache_id, len(chunks), anchors, vectors)
            chunks.append(chunk)
            total_bytes += chunk["bytes"]
        entry = {"document_id": document["id"], "source_sha256": document["sha256"],
                 "blocks_key": document["blocks_key"], "blocks_digest": blocks_digest,
                 "cache_id": cache_id, "passages": count, "bytes": total_bytes, "chunks": chunks,
                 "nonindexable_inputs": nonindexable,
                 "embedding_batching_policy": self.profile.get("runtime_settings", {}).get("batching_policy", "source_order")}
        self.store.put_json(doc_prefix + "/cache.json", entry)
        return entry, False

    def build(self, project_id: str, document_ids: list[str] | None = None) -> dict:
        """Explicit indexing; reuse unchanged document bundles after restart."""
        with _BUILD_LOCK:
            started = time.perf_counter()
            documents = self._documents(project_id)
            available = {document["id"]: document for document in documents}
            if document_ids is not None:
                if (not isinstance(document_ids, list) or len(document_ids) > MAX_DOCUMENTS
                        or any(not isinstance(identity, str) for identity in document_ids)):
                    raise SemanticIndexError("document_ids must be an explicit list of at most 10000 source IDs")
                if set(document_ids) - available.keys():
                    raise SemanticIndexError("Selected sources must be ready documents in this project")
                documents = [available[identity] for identity in sorted(set(document_ids))]
            prefix = self._prefix(project_id)
            existing = self._optional_json(prefix + "/manifest.json")
            entries = {}
            if existing and document_ids is not None:
                self._check_manifest(existing, project_id)
                entries = {entry["document_id"]: entry for entry in existing["documents"]
                           if entry["document_id"] in available}
            cached = 0
            for document in documents:
                blocks = self.store.get_json(document["blocks_key"])
                if (not isinstance(blocks, list) or len(blocks) > 100_000
                        or any(not isinstance(block, dict) or not isinstance(block.get("id"), str)
                               or not isinstance(block.get("text"), str) for block in blocks)):
                    raise SemanticIndexError("Source blocks are missing or malformed")
                if sum(len(block["text"]) for block in blocks) > 40_000_000:
                    raise SemanticIndexError("Source text exceeds the 40-million-character extraction bound")
                entry, was_cached = self._build_document(prefix, document, blocks, self._blocks_digest(blocks))
                entries[document["id"]] = entry
                cached += int(was_cached)
                if (sum(entry["passages"] for entry in entries.values()) > MAX_PASSAGES
                        or sum(entry["bytes"] for entry in entries.values()) > MAX_INDEX_BYTES):
                    raise SemanticIndexError("Index exceeds 250000 passages or 512MiB; split the project explicitly")
            sorted_entries = [entries[identity] for identity in sorted(entries)]
            policies = {}
            for entry in sorted_entries:
                policy = entry.get("embedding_batching_policy", "source_order")
                policies[policy] = policies.get(policy, 0) + 1
            manifest = {"index_version": INDEX_VERSION, "project_id": project_id,
                        "model": self.profile["model"], "model_revision": self.profile["revision"],
                        "dimensions": 384, "max_tokens": 256, "overlap_tokens": 32,
                        "built_at": utcnow(), "documents": sorted_entries,
                        "runtime_settings": self.profile.get("runtime_settings", {}),
                        "vector_batching_policies": policies,
                        "passages": sum(entry["passages"] for entry in sorted_entries)}
            manifest["index_id"] = _hash({key: value for key, value in manifest.items() if key != "built_at"})
            if _json_size(manifest) > 16 * 1024 * 1024:
                raise SemanticIndexError("Index manifest exceeds its resource bound")
            self.store.put_json(prefix + "/manifest.json", manifest)
            self._cache.clear()
            return {"documents": len(sorted_entries), "processed_documents": len(documents),
                    "cached_documents": cached, "passages": manifest["passages"],
                    "bytes": sum(entry["bytes"] for entry in sorted_entries),
                    "nonindexable_inputs": sum(len(entry.get("nonindexable_inputs", [])) for entry in sorted_entries),
                    "documents_without_passages": sum(entry["passages"] == 0 for entry in sorted_entries),
                    "model_profile": self.profile, "seconds": round(time.perf_counter() - started, 3),
                    "vector_batching_policies": policies,
                    "index_id": manifest["index_id"], "automatic_downloads": False}

    def _read_chunk(self, chunk: dict, prefix: str):
        import numpy as np
        cache_key = chunk["vectors_sha256"] + chunk["anchors_sha256"]
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        if any(not chunk[key].startswith(prefix + "/documents/") or ".." in chunk[key].split("/")
               for key in ("vectors_key", "anchors_key")):
            raise SemanticIndexError("Index artifact key is outside this project")
        vectors = self._bounded_bytes(chunk["vectors_key"], MAX_CHUNK_BYTES)
        anchors = self._bounded_bytes(chunk["anchors_key"], MAX_CHUNK_BYTES)
        if max(len(vectors), len(anchors)) > MAX_CHUNK_BYTES:
            raise SemanticIndexError("Stored index chunk exceeds its resource limit")
        if (hashlib.sha256(vectors).hexdigest() != chunk["vectors_sha256"]
                or hashlib.sha256(anchors).hexdigest() != chunk["anchors_sha256"]):
            raise SemanticIndexError("Index artifact checksum changed; rebuild explicitly")
        # Inspect the bounded header before NumPy can allocate from its shape.
        header = io.BytesIO(vectors)
        if np.lib.format.read_magic(header) != (1, 0):
            raise SemanticIndexError("Unsupported index array format; rebuild explicitly")
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(header, max_header_size=10000)
        if shape != (chunk["count"], 384) or fortran or dtype != np.float32 or not 1 <= chunk["count"] <= CHUNK_PASSAGES:
            raise SemanticIndexError("Invalid stored embedding dimensions or dtype")
        matrix = np.load(io.BytesIO(vectors), allow_pickle=False)
        metadata = json.loads(anchors)
        if (matrix.dtype != np.float32 or matrix.shape != (chunk["count"], 384)
                or not np.isfinite(matrix).all() or len(metadata) != chunk["count"]
                or chunk["count"] > CHUNK_PASSAGES):
            raise SemanticIndexError("Invalid stored embedding shape or passage metadata")
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=0.001):
            raise SemanticIndexError("Stored embeddings are not normalized cosine vectors")
        result = matrix, metadata
        if self.cache_chunks:
            self._cache[cache_key] = result
            while len(self._cache) > self.cache_chunks:
                self._cache.popitem(last=False)
        return result

    def search(self, project_id: str, text: str, limit: int = 20, *, document_ids: list[str] | None = None) -> dict:
        """Cosine-ranked source windows; scores are not classification confidence."""
        import numpy as np
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SemanticIndexError("Search limit must be between 1 and 100")
        started = time.perf_counter()
        documents = {document["id"]: document for document in self._documents(project_id)}
        selected_ids = None
        if document_ids is not None:
            if (not isinstance(document_ids, list) or len(document_ids) > MAX_DOCUMENTS
                    or any(not isinstance(identity, str) for identity in document_ids)):
                raise SemanticIndexError("Search document_ids must be a bounded source ID list")
            selected_ids = set(document_ids)
            if selected_ids - documents.keys():
                raise SemanticIndexError("Search sources must be ready documents in this project")
            documents = {identity: row for identity, row in documents.items() if identity in selected_ids}
        prefix = self._prefix(project_id)
        manifest = self._optional_json(prefix + "/manifest.json")
        if not manifest:
            raise SemanticIndexError("Build this project's semantic index explicitly before searching")
        self._check_manifest(manifest, project_id)
        query = np.asarray(self.runtime.embed([text])[0], dtype=np.float32)
        if query.shape != (384,) or not np.isfinite(query).all() or np.linalg.norm(query) == 0:
            raise SemanticIndexError("Invalid query embedding")
        query /= np.linalg.norm(query)
        top = []
        sequence, stale, eligible, passages, nonindexable = 0, 0, 0, 0, 0
        indexed_ids = set()
        for entry in manifest["documents"]:
            identity = entry["document_id"]
            if selected_ids is not None and identity not in selected_ids:
                continue
            indexed_ids.add(identity)
            document = documents.get(identity)
            if not document or document["sha256"] != entry["source_sha256"] or document["blocks_key"] != entry["blocks_key"]:
                stale += 1
                continue
            eligible += 1
            passages += entry["passages"]
            nonindexable += len(entry.get("nonindexable_inputs", []))
            for chunk in entry["chunks"]:
                matrix, anchors = self._read_chunk(chunk, prefix)
                scores = matrix @ query
                # Keep only local top-k candidates, then merge into a bounded heap.
                indices = np.argsort(scores, kind="stable")[-limit:]
                for index in indices:
                    anchor = anchors[int(index)]
                    if anchor.get("document_id") != identity:
                        raise SemanticIndexError("Passage attribution differs from its document manifest")
                    hit = {**anchor, "score": float(scores[index]), "title": document["title"],
                           "author": document["author"], "institution": document["institution"],
                           "source_sha256": document["sha256"], "source_url": document["source_url"],
                           "license": document["license"], "published_at": document["published_at"]}
                    item = (hit["score"], sequence, hit)
                    sequence += 1
                    if len(top) < limit:
                        heapq.heappush(top, item)
                    elif item[:2] > top[0][:2]:
                        heapq.heapreplace(top, item)
        hits = [item[2] for item in sorted(top, key=lambda item: (-item[0], item[1]))]
        # Validate returned citations against current immutable source blocks.
        source_blocks = {}
        for hit in hits:
            identity = hit["document_id"]
            if identity not in source_blocks:
                source_blocks[identity] = {block["id"]: block["text"] for block in self.store.get_json(documents[identity]["blocks_key"])}
            original = source_blocks[identity].get(hit["block_id"])
            if (original is None or not isinstance(hit["start"], int) or not isinstance(hit["end"], int)
                    or not 0 <= hit["start"] < hit["end"] <= len(original)
                    or original[hit["start"]:hit["end"]] != hit["quote"]):
                raise SemanticIndexError("Indexed citation differs from current source text; rebuild explicitly")
        unindexed = len(set(documents) - indexed_ids)
        return {"hits": hits, "model_profile": self.profile, "index_id": manifest["index_id"],
                "vector_batching_policies": manifest.get("vector_batching_policies", {"source_order": len(manifest["documents"])}),
                "documents_indexed": eligible, "documents_available": len(documents),
                "passages_indexed": passages, "stale_documents": stale, "unindexed_documents": unindexed,
                "nonindexable_inputs": nonindexable,
                "coverage_notice": "Changed/deleted indexed documents were excluded; new documents require indexing." if stale or unindexed else "All ready project documents are indexed.",
                "score_meaning": "Cosine semantic similarity, not stance, prejudice or confidence.",
                "seconds": round(time.perf_counter() - started, 3), "offset_unit": "unicode_code_points"}
