import hashlib
import json
from pathlib import Path

from doxa.lineage import content_sha256

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks" / "evaluation-v04"


def test_real_frozen_scaffold_is_200_unlabelled_cases_with_disjoint_story_groups():
    scaffold = json.loads((BASE / "scaffold.json").read_text())
    assert len(scaffold["cases"]) == 200
    assert scaffold["quality_status"] == "reference_pending"
    assert scaffold["interpretation_accuracy"] is None
    assert content_sha256({key: value for key, value in scaffold.items() if key != "scaffold_sha256"}) == scaffold["scaffold_sha256"]
    groups = {}
    for split in ("development", "heldout"):
        cases = [case for case in scaffold["cases"] if case["split"] == split]
        assert len(cases) == 100
        groups[split] = {case["story_group"] for case in cases}
        assert all(case["reference_labels"] is None and "model_assessment" not in case for case in cases)
    assert groups["development"].isdisjoint(groups["heldout"])
    assert not groups["heldout"].intersection(scaffold["exposure_ledger"]["exposed_story_groups"])


def test_every_real_review_sentence_resolves_to_the_original_licensed_source():
    scaffold = json.loads((BASE / "scaffold.json").read_text())
    for case in scaffold["cases"]:
        source_id = case["parent_locator"]["source_id"]
        source = json.loads((ROOT / "corpus" / "news" / "metadata" / (source_id + ".json")).read_text())
        assert content_sha256(source["blocks"]) == case["source_hash"]
        blocks = {block["id"]: block["text"] for block in source["blocks"]}
        unit = case["scored_unit"]
        assert unit["id"] == case["unit_id"]
        for sentence in [*unit["context_before"], *unit["sentences"], *unit["context_after"]]:
            assert blocks[sentence["block_id"]][sentence["start"]:sentence["end"]] == sentence["text"]


def test_120_functional_fixtures_are_versioned_unexecuted_relational_expectations():
    casebook = json.loads((BASE / "functional-casebook.json").read_text())
    assert len(casebook["fixtures"]) == 120
    assert len({case["base_case_id"] for case in casebook["fixtures"]}) == 24
    for case in casebook["fixtures"]:
        assert case["expectation"]["expected_outcome_label"] is None
        assert case["expectation"]["status"] == "requires_human_rubric_review"
        assert case["execution"] == {"status": "not_run", "model_output": None, "passed": None}
        assert hashlib.sha256(case["text"].encode()).hexdigest() == case["derived_text_sha256"]
    for split in ("development", "heldout"):
        for reviewer in ("A", "B"):
            form = json.loads((BASE / f"reviewer-{reviewer}-{split}.json").read_text())
            assert len(form["rows"]) == 100 and form["model_blinded"]
            assert all(not row["independent_blinded_review"] and all(value is None for value in row["labels"].values()) for row in form["rows"])
