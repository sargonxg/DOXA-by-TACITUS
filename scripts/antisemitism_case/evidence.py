"""Frozen inputs and editorial teaching notes for the antisemitism case."""

from collections import Counter
import json

from doxa.recorded_starter import canonical_hash
from scripts.bias_study.cohort import freeze_selected
from scripts.build_recorded_starter_v06 import EXPECTED_CORPUS_SHA256, file_hash


SOURCES = {
    "gv-801447": "assessments-part-0035.jsonl.gz",
    "gv-839816": "assessments-part-0048.jsonl.gz",
    "gv-780962": "assessments-part-0028.jsonl.gz",
    "gv-774621": "assessments-part-0026.jsonl.gz",
}
LENSES = ("ihra", "jda", "nexus")
FRAMEWORKS = {"ihra": "IHRA", "jda": "Jerusalem Declaration", "nexus": "Nexus"}
OUTCOMES = {
    "meets_criterion": "Criterion met",
    "does_not_meet": "Criterion not met",
    "not_applicable": "Outside criterion",
    "uncertain": "Uncertain",
}

CASES = (
    {
        "slug": "plzen-poster",
        "heading": "The accusation on the poster",
        "decision": "95f0ecd347b4a8348d80017aa7a32a29",
        "sentence": "s_5180d2fe8292da5f7610fccf",
        "peer": "3ca92a332451004c4a4d75d0c0413dc6",
        "prompt": "Underline the words that name the target. Then find the words that tell you whose accusation this is.",
        "reading": (
            "The article attributes to a shop owner a religious accusation against Jews as a group. "
            "The accusation assigns collective guilt and invokes a divine curse. The journalist introduces it as an "
            "example of reported antisemitism. The next sentence says the prosecutor canceled the prosecution. "
            "That reported disposition answers a different question from the textual reading."
        ),
        "followup": (
            "What would the original poster, a verified translation, or a fuller report add? "
            "Record separately what the article quotes, what it attributes, and what you can corroborate."
        ),
        "context": ("b00017", "b00018", "b00019"),
    },
    {
        "slug": "australia-policy",
        "heading": "A plan, an objection, and a boundary",
        "decision": "a520caaf0bb90677eda4fdfc27278d76",
        "sentence": "s_58ba24ca18ce7512a0f9ba9a",
        "peer": "0dc40aeabe410922edccb116d459f592",
        "prompt": "Locate the object of the disagreement. Write down what the observers fear the plan would do.",
        "reading": (
            "The passage reports concerns over the plan’s effect on criticism of Israel. "
            "The archived model marked the Jerusalem Declaration political-expression boundary as met. "
            "This is a boundary result. It records a distinction about political expression."
        ),
        "followup": (
            "What wording would move the target from a government or policy to Jews as Jews? "
            "Find the envoy’s quoted distinction in the surrounding article before answering."
        ),
        "context": ("b00003", "b00007", "b00013"),
    },
    {
        "slug": "holocaust-analogy",
        "heading": "An allegation about a Holocaust analogy",
        "decision": "3fb5c699c85c3fa16d618e555a3f8ffb",
        "sentence": "s_3d2fbfdec7a358ecbc13a028",
        "peer": None,
        "prompt": "Separate the reported accusation, the historical analogy, and the words the article quotes from the speech.",
        "reading": (
            "The article describes a dispute over a wartime analogy. A reviewer needs the speech to assess "
            "what historical facts the speaker acknowledged, minimized, or denied. The same article "
            "describes the murder of Jews and Roma at Babyn Yar. The saved result is a compact outcome."
        ),
        "followup": (
            "What wording in the original speech would substantiate a claim of denial or distortion? "
            "Keep that evidence separate from the fact that other speakers criticized the analogy."
        ),
        "context": ("b00003", "b00008", "b00015"),
    },
    {
        "slug": "survivor-remembrance",
        "heading": "A survivor’s name in a remembrance",
        "decision": "d03c68bcca7ed9899c97c1bfefd7097a",
        "sentence": "s_2733f1697d75af893d0f77ad",
        "peer": "eaac38d71ca74037b3cbafe8335b1cd8",
        "prompt": "Ask what the article is doing when it refers to the Holocaust and names the survivor.",
        "reading": (
            "The obituary names a survivor in a report of his death. The saved model marks the selected "
            "hostility criterion unmet and the Jerusalem Declaration political-expression boundary "
            "outside scope. This is a useful control for keyword-based reading."
        ),
        "followup": "If a search retrieves this passage on the word Holocaust, what additional evidence would justify a flag?",
        "context": ("b00001", "b00002"),
    },
)

REFERENCES = {
    "ihra": ("IHRA working definition and contextual examples",
             "https://holocaustremembrance.com/resources/working-definition-antisemitism"),
    "jda": ("Jerusalem Declaration on Antisemitism, especially guidelines 1–5 and 6–15",
            "https://jerusalemdeclaration.org/"),
    "nexus": ("The Nexus Document on antisemitism, Israel, and Zionism",
              "https://nexusproject.us/nexus-resources/the-nexus-document/"),
    "blood": ("US Holocaust Memorial Museum, Blood Libel",
              "https://encyclopedia.ushmm.org/content/en/article/blood-libel"),
    "denial": ("US Holocaust Memorial Museum, Holocaust Denial key dates",
               "https://encyclopedia.ushmm.org/content/en/article/holocaust-denial-key-dates"),
    "online": ("Zannettou and colleagues, A Quantitative Approach to Understanding Online Antisemitism",
               "https://ojs.aaai.org/index.php/ICWSM/article/view/7343"),
    "hatecheck": ("Röttger and colleagues, HateCheck functional tests", "https://aclanthology.org/2021.acl-long.4/"),
}

# These are prompts for research review. The archived rubric and its outcomes remain separate.
FLAGS = (
    ("Inherited collective blame", "A sweeping accusation moves from an alleged act to Jews as a group.",
     "In Plzeň, inspect the group label and the collective curse. Record the reported speaker before coding.", "jda"),
    ("Collective responsibility for Israel", "A statement transfers the acts of a state or individual to Jewish people.",
     "Identify who did the alleged act and who is blamed. Look for an explicit transfer of responsibility.", "nexus"),
    ("Conspiracy and coded proxies", "A claim describes a hidden Jewish network as controlling public affairs.",
     "Ask how the wording makes the Jewish target and claim of coordinated control identifiable. Test plausible alternative readings of a code or name.", "online"),
    ("Dual loyalty or presumed agency", "A person is presumed to work for a foreign government because they are Jewish.",
     "Separate evidence about this individual’s actions from an assumption attached to identity.", "nexus"),
    ("Dehumanization, exclusion, or threats", "A text denies equal standing, depicts Jews as subhuman, or targets people or institutions as Jewish.",
     "Locate the target, the proposed treatment, and whether the speaker endorses, describes, or condemns it.", "ihra"),
    ("Blood libel", "A specific false accusation alleges that Jews murder non-Jews for ritual purposes.",
     "Check whether the ritual allegation is present. The reported Plzeň wording makes a different religious accusation.", "blood"),
    ("Holocaust denial or distortion", "A text denies established events, radically minimizes them, or uses misleading historical claims.",
     "Preserve the asserted historical proposition and source. A reference to the Holocaust can also report an allegation or commemorate a survivor.", "denial"),
    ("Political target and surrounding context", "Language about Israel or Zionism can cross into anti-Jewish hostility through its target or tropes.",
     "Identify the policy, institution, or people being targeted. Apply the selected framework’s context rules and keep its boundary result separate.", "jda"),
    ("Misassigned voice", "A quote, denunciation, translation, or reported claim is treated as the reporter’s view.",
     "Track the attribution verb, quote boundary, surrounding paragraph, and stance toward the exact proposition.", "hatecheck"),
)


def freeze(root):
    corpus = root / "corpus/news"
    manifest = corpus / "manifest.jsonl"
    if file_hash(manifest) != EXPECTED_CORPUS_SHA256:
        raise ValueError("Canonical source manifest changed")
    holdout = corpus / "holdout.jsonl"
    scaffold = root / "benchmarks/evaluation-v04/scaffold.json"
    excluded = {row["story_group_id"] for row in map(json.loads, holdout.read_text().splitlines())}
    heldout = [row for row in json.loads(scaffold.read_text())["cases"] if row["split"] == "heldout"]
    excluded.update(row["story_group"] for row in heldout)
    excluded_ids = {row["source"]["source_id"] for row in heldout}
    rows = {row["id"]: row for row in map(json.loads, manifest.read_text().splitlines()) if row["id"] in SOURCES}
    if set(rows) != set(SOURCES):
        raise ValueError("Teaching source is absent from the canonical manifest")
    for row in rows.values():
        if (row["split"] != "development" or row["license"] != "CC-BY-3.0" or row["synthetic"]
                or row["language"] != "en" or row["story_group_id"] in excluded or row["id"] in excluded_ids):
            raise ValueError(f"Ineligible case source {row['id']}")
    if len({row["story_group_id"] for row in rows.values()}) != len(rows):
        raise ValueError("Teaching sources must be from distinct story groups")
    selection = {
        "design": "purposive teaching cases selected after reading archived outcomes",
        "uses_model_outcomes": True,
        "sources": sorted(rows),
        "manifest_sha256": EXPECTED_CORPUS_SHA256,
        "holdout_sha256": file_hash(holdout),
        "scaffold_sha256": file_hash(scaffold),
        "selection_sha256": canonical_hash(SOURCES),
    }
    study = freeze_selected(root, rows, selection, LENSES, "antisemitism-case-1", wanted_parts=set(SOURCES.values()))
    if any(doc["archive_locator"]["part"] != SOURCES[doc["id"]] for doc in study["documents"]):
        raise ValueError("Frozen archive locations changed")
    return study


def readings(study):
    index = {row["id"]: (doc, row) for doc in study["documents"] for row in doc["decisions"]}
    result = []
    for note in CASES:
        doc, decision = index[note["decision"]]
        ref = next(r for r in decision["evidence_refs"] if r["sentence_id"] == note["sentence"])
        blocks = {block["id"]: block["text"] for block in doc["blocks"]}
        result.append({
            **note,
            "doc": doc,
            "decision": decision,
            "ref": ref,
            "context": blocks[ref["block_id"]],
            "nearby": [(block, blocks[f"{doc['id']}-{block}"]) for block in note["context"]],
            "peer": index[note["peer"]][1] if note["peer"] else None,
        })
    return result


def coverage(study):
    counts = Counter((row["criterion_kind"], row["result"])
                     for doc in study["documents"] for row in doc["decisions"])
    units = sum(len(doc["unit_statuses"]) for doc in study["documents"])
    expected = units * sum(len(lens["criteria"]) for lens in study["lenses"])
    return {
        "articles": len(study["documents"]),
        "units": units,
        "retained": sum(counts.values()),
        "expected": expected,
        "indicators_met": counts["indicator", "meets_criterion"],
        "boundaries_met": counts["boundary", "meets_criterion"],
        "outcomes": {result: sum(n for (_, outcome), n in counts.items() if outcome == result) for result in OUTCOMES},
    }
