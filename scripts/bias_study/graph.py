"""Project the frozen public study into a small, verifiable evidence graph."""

from collections import Counter
from urllib.parse import quote

from doxa.recorded_starter import canonical_hash


def _link(term):
    return {"@id": term, "@type": "@id"}


CONTEXT = {
    "@vocab": "https://tacitus.me/doxa/terms#",
    "doxa": "https://tacitus.me/doxa/terms#",
    "dct": "http://purl.org/dc/terms/",
    "prov": "http://www.w3.org/ns/prov#",
    "oa": "http://www.w3.org/ns/oa#",
    "title": "dct:title",
    "exact": "oa:exact",
    "start": "oa:start",
    "end": "oa:end",
    "sourceUrl": _link("dct:source"),
    "license": _link("dct:license"),
    "generatedBy": _link("prov:wasGeneratedBy"),
    "fromSource": _link("prov:wasDerivedFrom"),
    **{field: _link("doxa:" + field) for field in (
        "inStudy", "inFramework", "evaluates", "scoredUnit", "modelPassage",
        "unitContext", "commentsOn", "selectedPassage", "pairedDecision",
    )},
}
EDGE_TYPES = {
    "inStudy": "Study",
    "inFramework": "Framework",
    "evaluates": "Criterion",
    "scoredUnit": "SourceUnit",
    "generatedBy": "AnalysisRun",
    "fromSource": "SourceVersion",
    "modelPassage": "Passage",
    "unitContext": "Passage",
    "commentsOn": "Decision",
    "selectedPassage": "Passage",
    "pairedDecision": "Decision",
}


def iri(kind, *parts):
    return "urn:doxa:" + kind + ":" + ":".join(quote(str(item), safe="") for item in parts)


def _node(identity, node_type, **fields):
    prov_type = "prov:Activity" if node_type == "AnalysisRun" else "prov:Entity"
    return {"@id": identity, "@type": [f"doxa:{node_type}", prov_type], **fields}


def _passage(source_id, blocks, ref):
    block = ref["block_id"]
    start, end, exact = ref["start"], ref["end"], ref["quote"]
    text = blocks[block]
    if not 0 <= start < end <= len(text) or text[start:end] != exact:
        raise ValueError("Evidence graph requires a valid slice of the frozen source block")
    identity = iri("passage", canonical_hash([source_id, block, start, end, exact]))
    return _node(
        identity, "Passage", fromSource=source_id, blockId=block, start=start, end=end, exact=exact,
    )


def build_graph(study, readings):
    edition, parent = study["study_version"], study["parent"]
    cohort_id = iri("study", edition)
    run_id = iri("run", parent["id"])
    nodes = {}

    def insert(node):
        identity = node["@id"]
        if nodes.setdefault(identity, node) != node:
            raise ValueError(f"Conflicting evidence graph node {identity}")
        return identity

    insert(_node(
        cohort_id, "Study", recordId=edition, selectionSha256=canonical_hash(study["selection"]),
        independentHumanLabels=study["independent_human_labels"],
        freshInferenceRequests=study["fresh_inference_requests"],
    ))
    insert(_node(
        run_id, "AnalysisRun", recordId=parent["id"], model=parent["model"], provider=parent["provider"],
        status=parent["status"], configSha256=parent["config_sha256"], archiveSha256=parent["archive_sha256"],
    ))
    for lens in study["lenses"]:
        framework_id = insert(_node(
            iri("framework", lens["id"], lens["version"], canonical_hash(lens)),
            "Framework", recordId=lens["id"], title=lens["name"], version=lens["version"],
            sourceUrl=lens["source_url"], snapshotSha256=canonical_hash(lens),
        ))
        for criterion in lens["criteria"]:
            insert(_node(
                iri("criterion", lens["id"], criterion["id"], lens["version"], canonical_hash(criterion)),
                "Criterion", recordId=criterion["id"], title=criterion["label"],
                rule=criterion["description"], kind=criterion["kind"], inFramework=framework_id,
            ))
    criteria = {
        (nodes[node["inFramework"]]["recordId"], node["recordId"]): node["@id"]
        for node in nodes.values() if "doxa:Criterion" in node["@type"]
    }
    for doc in study["documents"]:
        source_id = insert(_node(
            iri("source", doc["source_version_id"]), "SourceVersion", recordId=doc["id"],
            title=doc["title"], author=doc["author"], translator=[t["name"] for t in doc["translators"]],
            publishedOn=doc["published_at"][:10], sourceUrl=doc["source_url"], license=doc["license_url"],
            contentSha256=doc["content_sha256"], textSha256=doc["txt_sha256"],
            archivedRecordSha256=doc["parent_record_sha256"], inStudy=cohort_id,
        ))
        blocks = {block["id"]: block["text"] for block in doc["blocks"]}
        for unit in doc["unit_statuses"]:
            insert(_node(
                iri("unit", doc["source_version_id"], unit["unit_id"]), "SourceUnit",
                recordId=unit["unit_id"], status=unit["status"], fromSource=source_id,
            ))
        for row in doc["decisions"]:
            anchors = list(dict.fromkeys(insert(_passage(source_id, blocks, ref)) for ref in row["evidence_refs"]))
            anchor_type = "modelPassage" if row.get("detail_supplied") else "unitContext"
            insert(_node(
                iri("decision", row["id"]), "Decision", recordId=row["id"], outcome=row["result"],
                generatedBy=run_id, evaluates=criteria[row["lens_id"], row["criterion_id"]],
                scoredUnit=iri("unit", doc["source_version_id"], row["unit_id"]),
                attribution=row["attribution"], speaker=row["speaker"], speakerStance=row["speaker_stance"],
                documentStance=row["document_stance"], proposition=row["proposition"],
                hypothetical=row["hypothetical"], reviewStatus=row["review_status"],
                **({"explanation": row["explanation"]} if row.get("explanation") else {}),
                **{anchor_type: anchors},
            ))
    for case in readings:
        doc, row = case["doc"], case["decision"]
        anchor = _passage(
            iri("source", doc["source_version_id"]), {case["ref"]["block_id"]: case["context"]}, case["ref"],
        )
        insert(_node(
            iri("note", edition, case["slug"]), "EditorialNote", recordId=case["slug"],
            title=case["heading"], authorship="AI-authored editorial commentary", reading=case["reading"],
            reviewQuestion=case["followup"], commentsOn=iri("decision", row["id"]),
            selectedPassage=anchor["@id"],
            **({"pairedDecision": iri("decision", case["peer"]["id"])} if case["peer"] else {}),
        ))
    graph = {"@context": CONTEXT, "@id": iri("graph", edition, 1), "@graph": list(nodes.values())}
    graph_index(graph)
    return graph


def graph_index(graph):
    """Check typed links and the source version behind each anchor."""
    index = {node["@id"]: node for node in graph["@graph"]}
    if len(index) != len(graph["@graph"]):
        raise ValueError("Duplicate evidence graph identifiers")
    for node in index.values():
        for edge, kind in EDGE_TYPES.items():
            targets = node.get(edge, [])
            if isinstance(targets, str):
                targets = [targets]
            if any(f"doxa:{kind}" not in index.get(target, {}).get("@type", []) for target in targets):
                raise ValueError(f"Invalid {edge} on {node['@id']}")
        if "doxa:Decision" in node["@type"]:
            if ("modelPassage" in node) == ("unitContext" in node):
                raise ValueError("Decision requires one explicit evidence selection origin")
            source_id = index[node["scoredUnit"]]["fromSource"]
            if any(
                index[anchor]["fromSource"] != source_id
                for anchor in node.get("modelPassage", node.get("unitContext", []))
            ):
                raise ValueError("Decision evidence points to a different source version")
        if "doxa:EditorialNote" in node["@type"]:
            decision = index[node["commentsOn"]]
            if node["selectedPassage"] not in decision.get("modelPassage", decision.get("unitContext", [])):
                raise ValueError("Editorial excerpt must be one of the decision's recorded references")
            if node.get("pairedDecision") and index[node["pairedDecision"]]["scoredUnit"] != decision["scoredUnit"]:
                raise ValueError("A paired reading must use the same source unit")
    return index


def case_trace(note, index):
    decision = index[note["commentsOn"]]
    criterion = index[decision["evaluates"]]
    unit = index[decision["scoredUnit"]]
    return {
        "note": note,
        "decision": decision,
        "criterion": criterion,
        "framework": index[criterion["inFramework"]],
        "run": index[decision["generatedBy"]],
        "unit": unit,
        "source": index[unit["fromSource"]],
        "passage": index[note["selectedPassage"]],
        "peer": index[note["pairedDecision"]] if "pairedDecision" in note else None,
    }


def graph_counts(graph):
    counts = Counter(node["@type"][0].removeprefix("doxa:") for node in graph["@graph"])
    counts["EvidenceReference"] = sum(
        len(node.get("modelPassage", node.get("unitContext", []))) for node in graph["@graph"]
    )
    return dict(sorted(counts.items()))
