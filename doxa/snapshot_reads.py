"""Coherent local reads and explicit revision guards for remote compound reads."""
from contextlib import contextmanager
import hashlib
import json

from fastapi import HTTPException

from .workspace import Workspace


def validate_source_blocks(document, blocks):
    """Check a recorded extraction digest when available; never invent a legacy digest."""
    expected = (document.get("metadata") or {}).get("extraction_manifest", {}).get("blocks_sha256")
    if not expected:
        return {"status": "not_recorded", "notice": "This legacy source has no frozen extraction-block digest; exact retained anchors are checked separately."}
    actual = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if actual != expected:
        raise HTTPException(409, "Source blocks differ from their recorded extraction digest")
    return {"status": "verified", "blocks_sha256": actual}


@contextmanager
def scope_snapshot(store, project_id):
    if not getattr(store, "remote", False) and hasattr(store, "transaction"):
        with store.transaction(write=False):
            yield
        return
    before = Workspace(store).revision(project_id)
    yield
    if Workspace(store).revision(project_id) != before:
        raise HTTPException(409, "Workspace changed during capture. Refresh the source scope and retry.")
