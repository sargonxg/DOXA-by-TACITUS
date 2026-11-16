"""Compare imported source snapshots without interpreting absence as deletion."""
from __future__ import annotations

import json


def _document(row: dict) -> dict:
    result = dict(row)
    raw = result.pop("metadata_json", "{}")
    try:
        metadata = json.loads(raw or "{}") if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        metadata = {}
        result["metadata_warning"] = "Stored metadata was not valid JSON."
    result["metadata"] = metadata if isinstance(metadata, dict) else {}
    return result


def _source_identity(document: dict) -> str:
    source_url = str(document.get("source_url") or "").strip()
    if source_url:
        # URL paths may be case-sensitive. Do not lowercase, drop queries, or
        # guess that two independently supplied URLs represent one source.
        return "url:" + source_url
    filename = str(document.get("filename") or "").strip().casefold()
    if not filename:
        raise ValueError("A batch document needs a source URL or filename to compare")
    return "filename:" + filename


def _latest_sources(store, project_id: str, batch: dict | None) -> dict[str, dict]:
    if batch is None:
        return {}
    rows = store.query("SELECT d.* FROM batch_members m JOIN documents d ON d.id=m.document_id WHERE m.batch_id=? ORDER BY d.created_at,d.id", [batch["id"]])
    sources = {}
    for row in rows:
        if row["project_id"] != project_id:
            raise ValueError("Batch membership must reference documents in the same project")
        document = _document(row)
        identity = _source_identity(document)
        previous = sources.get(identity)
        # Import time, then stable ID, resolves multiple versions consistently
        # even when timestamps tie or a source appears repeatedly in a batch.
        if previous is None or (str(document.get("created_at") or ""), document["id"]) > (str(previous.get("created_at") or ""), previous["id"]):
            sources[identity] = document
    return sources


def compare_batches(store, project_id: str, before_batch: str | None = None,
                    after_batch: str | None = None) -> dict:
    """Return content-hash deltas between two authorized project batches.

Defaults use the latest two batches. Selecting only an ``after`` batch uses
its immediately preceding project batch, or an empty baseline for the first.
An explicit ``before`` with no ``after`` compares it with the latest batch.
The same content may belong to both batches through one deduplicated document.
"""
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise LookupError("Project not found")
    batches = store.query("SELECT * FROM batches WHERE project_id=? ORDER BY created_at,id", [project_id])
    by_id = {batch["id"]: batch for batch in batches}
    for requested in [before_batch, after_batch]:
        if requested is not None and requested not in by_id:
            raise LookupError("Batch not found in this project")
    after = by_id[after_batch] if after_batch is not None else (batches[-1] if batches else None)
    if before_batch is not None:
        before = by_id[before_batch]
    elif after is not None:
        index = next(index for index, batch in enumerate(batches) if batch["id"] == after["id"])
        before = batches[index - 1] if index else None
    else:
        before = None
    earlier = _latest_sources(store, project_id, before)
    later = _latest_sources(store, project_id, after)
    added, changed, unchanged, not_in_later = [], [], [], []
    for identity in sorted(later):
        current = later[identity]
        previous = earlier.get(identity)
        if previous is None:
            added.append(current)
        elif previous.get("sha256") != current.get("sha256"):
            changed.append({"before": previous, "after": current})
        else:
            unchanged.append(current)
    for identity in sorted(set(earlier) - set(later)):
        not_in_later.append(earlier[identity])
    return {"before_batch": before, "after_batch": after, "added": added, "changed": changed,
            "unchanged": unchanged, "not_in_later_batch": not_in_later,
            "totals": {"added": len(added), "changed": len(changed), "unchanged": len(unchanged),
                       "not_in_later_batch": len(not_in_later)},
            "note": "Incremental imports: absence does not imply deletion."}
