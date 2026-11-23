"""JSON-LD projection of the scorecard ledger, with an offline rate query."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from urllib.parse import quote

from .method_config import content_hash

CONTEXT = {"@vocab": "https://tacitus.me/doxa/terms#", "doxa": "https://tacitus.me/doxa/terms#",
           "prov": "http://www.w3.org/ns/prov#", "oa": "http://www.w3.org/ns/oa#",
           "criterion": {"@type": "@id"}, "source": {"@id": "prov:wasDerivedFrom", "@type": "@id"},
           "run": {"@id": "prov:wasGeneratedBy", "@type": "@id"}, "passage": {"@type": "@id"},
           "member": {"@type": "@id"}, "decision": {"@type": "@id"},
           "exact": "oa:exact", "start": "oa:start", "end": "oa:end"}


def _id(kind, identity):
    return f"urn:doxa:{kind}:{quote(str(identity), safe='')}"


def to_graph(snapshot):
    scorecard_id = _id("scorecard", snapshot["id"])
    run_id = _id("run", snapshot["run"]["id"])
    cards = {card["id"]: _id("method-card", card["card_sha256"]) for card in snapshot["method_cards"]}
    sources = {source["id"]: _id("source-version", content_hash([source["id"], source["sha256"], source["extraction_version"]]))
               for source in snapshot["sources"]}
    nodes = {run_id: {"@id": run_id, "@type": ["doxa:AnalysisRun", "prov:Activity"], **snapshot["run"]}}
    for card in snapshot["method_cards"]:
        identity = cards[card["id"]]
        nodes[identity] = {"@id": identity, "@type": "doxa:Criterion", "key": card["id"],
                           "version": card["lens_version"], "label": card["label"], "sha256": card["card_sha256"]}
    for source in snapshot["sources"]:
        identity = sources[source["id"]]
        nodes[identity] = {"@id": identity, "@type": "doxa:SourceVersion", **source}
    members = []
    for cell in snapshot["matrix"]:
        identity = _id("scorecard-cell", content_hash([snapshot["id"], cell["document_id"], cell["criterion"]]))
        source_id = sources[cell["document_id"]]
        criterion = cards[cell["criterion"]]
        node = {"@id": identity, "@type": "doxa:SourceCriterion", "source": source_id, "criterion": criterion,
                "status": cell["status"], "planned": cell["planned"], "retained": cell["retained"]}
        if cell.get("scope_note"):
            node["scopeNote"] = cell["scope_note"]
        evidence = cell.get("evidence")
        if evidence:
            passage = _id("passage", content_hash([source_id, evidence["block_id"], evidence["start"], evidence["end"]]))
            nodes[passage] = {"@id": passage, "@type": "doxa:Passage", "source": source_id, "exact": evidence["quote"],
                              **{key: evidence[key] for key in ("block_id", "start", "end", "cue_start_ms", "cue_end_ms")}}
            decision = _id("decision", content_hash([run_id, evidence["assessment_id"], evidence.get("review_event_id")]))
            nodes[decision] = {"@id": decision, "@type": "doxa:Decision", "criterion": criterion,
                               "passage": passage, "run": run_id, "assessmentId": evidence["assessment_id"]}
            node["decision"] = decision
        nodes[identity] = node
        members.append(identity)
    nodes[scorecard_id] = {"@id": scorecard_id, "@type": "doxa:Scorecard", "run": run_id, "member": members,
                           "question": snapshot["question"], "sha256": snapshot["snapshot_sha256"],
                           "basis": snapshot["basis"]}
    return {"@context": CONTEXT, "@graph": list(nodes.values())}


def criterion_rate(graph, key):
    nodes = graph["@graph"]
    criterion = next(node["@id"] for node in nodes if node["@type"] == "doxa:Criterion" and node["key"] == key)
    member_ids = next(node["member"] for node in nodes if node["@type"] == "doxa:Scorecard")
    members = set(member_ids)
    statuses = [node["status"] for node in nodes if node["@id"] in members and node["criterion"] == criterion]
    positive = statuses.count("positive")
    determinate = positive + statuses.count("negative")
    return {"criterion": key, "positive": positive, "determinate": determinate,
            "rate": positive / determinate if determinate else None}


def verify_projection(graph, snapshot):
    """Check a graph against its frozen scorecard and recalculate the chart totals."""
    expected_hash = content_hash({field: value for field, value in snapshot.items() if field != "snapshot_sha256"})
    if snapshot.get("snapshot_sha256") != expected_hash:
        raise ValueError("Scorecard checksum mismatch")
    expected = to_graph(snapshot)
    nodes = {node["@id"]: node for node in graph["@graph"]}
    if (len(nodes) != len(graph["@graph"]) or graph.get("@context") != expected["@context"]
            or nodes != {node["@id"]: node for node in expected["@graph"]}):
        raise ValueError("Graph nodes or links differ from the frozen scorecard")
    from .scorecards import _summary
    cells = defaultdict(list)
    for cell in snapshot["matrix"]:
        cells[cell["criterion"]].append(cell)
    keys = [row["key"] for row in snapshot["criteria"]]
    if len(keys) != len(set(keys)) or set(keys) != set(cells):
        raise ValueError("Scorecard criteria differ from the frozen source matrix")
    for criterion in snapshot["criteria"]:
        key = criterion["key"]
        computed = _summary(cells[key])
        if any(field not in criterion or criterion[field] != value
               for field, value in computed.items() if field != "unresolved_range" or field in criterion):
            raise ValueError(f"Scorecard totals differ from the frozen observations for {key}")
    return {"verified": True, "snapshot_sha256": expected_hash, "nodes": len(nodes),
            "criteria_checked": len(snapshot["criteria"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("criterion", nargs="?")
    parser.add_argument("--verify", type=Path, metavar="SCORECARD", help="Check the graph against the saved scorecard")
    args = parser.parse_args()
    if not args.criterion and not args.verify:
        parser.error("Supply a criterion ID or --verify SCORECARD")
    try:
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
        result = {}
        if args.verify:
            result.update(verify_projection(graph, json.loads(args.verify.read_text(encoding="utf-8"))))
        if args.criterion:
            result.update(criterion_rate(graph, args.criterion))
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    except (StopIteration, KeyError):
        parser.error("The graph does not contain this scorecard criterion.")


if __name__ == "__main__":
    main()
