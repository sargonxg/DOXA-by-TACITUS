"""Political-framing provenance/contract checks; these are not model gold labels."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from doxa.contextual import analyze_units, build_units
from doxa.lenses import CRITERION_REGISTRY_VERSION, get_lens


ROOT = Path(__file__).resolve().parents[1]

# Captured before adding the new pack. Existing frozen runs must retain these
# normalized definitions, including their original kind/version/context rules.
LEGACY_HASHES = {
    "discourse": "3fec36e6ac6a45b1ecec72caa99689ce7e3a87155c1172eb5234c81471beb1cd",
    "ihra": "2feef47f32f5fdd91c5a826827a88e9f3b01e7ccc44b4e6e8ab06d456eec3fac",
    "jda": "4c6de2019f4741d35a348805e046578dff7a8804d28dd67d6978d95bef4f24e1",
    "nexus": "c95095d8f3bdffb85d633f8a995f0721b99822d0bae4657cb10244b4aec34be6",
    "anti-muslim-hostility": "6ac9353d9e716e7863d482cf4156178fe6729ae77c2d444551ea452da1ff4189",
    "anti-muslim-racism": "082df6dd629a01f58fbc28903d94b73fdbd5ddfbc5c89802e424891105f2bb83",
    "language-norms": "f2f38cf16b0fe774873fa4797d005f302993ea7c7b1e134b75c6c97191e9c841",
}

SOURCE_HASHES = {
    "us01": "8a74e1bd02e4c0baf897e5b4ecacea5163c7d8118e96751905bc8b40ebe7aaac",
    "us03": "ec1701f0a98163e0c6739c24142977998dd41a2f9055b32d90bed535851bdcac",
    "us04": "fc7fe1f1f99c8a8f1b0bc0948c9611307fff901f4c85c4e6f9c62313dfaefa1c",
    "uk07": "c826adb7813babc836a8456fa9fb7c517710bb481b9548efb36710ee087c8f6e",
}

# Exact anchors from the documented purposive teaching packet. They verify
# source fidelity, not the correctness of independently unannotated judgments.
EXAMPLES = [
    ("us03", "us03-b00007", 18, 264,
     "It shall be the policy of the United States to combat anti-Semitism vigorously, using all available and appropriate legal tools, to prosecute, remove, or otherwise hold to account the perpetrators of unlawful anti-Semitic harassment and violence."),
    ("us01", "us01-p0006-b0006", 389, 538,
     "It urges action from all of society—state and local authorities, civil society, community and faith leaders, the private sector, individual citizens."),
    ("us04", "us04-b00005", 268, 374,
     "the previous administration trampled free speech rights by censoring Americans' speech on online platforms"),
    ("us01", "us01-p0002-b0001", 422, 496,
     "The very soul of our Nation was hanging in the balance. It still is today."),
    ("us03", "us03-b00006", 227, 386,
     "The prior administration effectively nullified Executive Order 13899 by failing to give the terms of the order full force and effect throughout the Government."),
    ("us01", "us01-p0002-b0001", 1743, 1913,
     "Our intelligence agencies have determined that domestic terrorism rooted in white supremacy—including antisemitism—is the greatest terrorist threat to our Homeland today."),
    ("uk07", "uk07-p0006-b0002", 17, 251,
     "The Government must work to rebuild trust with Muslim communities by adopting an approach to integration which focuses on how it improves the life chances of disadvantaged communities rather than through the lens of counter-extremism."),
    ("uk07", "uk07-p0007-b0001", 405, 513,
     "The Government does not accept at this stage that there is a need for a plan with specific focus on Muslims."),
    ("us01", "us01-p0011-b0003", 0, 134,
     "To develop this strategy, we held listening sessions with more than 1,000 diverse stakeholders across the Jewish community and beyond."),
    ("us03", "us03-b00006", 986, 1260,
     "A joint report by the House Committees on Education and the Workforce, Energy and Commerce, Judiciary, Oversight and Accountability, Veterans' Affairs, and Ways and Means calls the Federal Government's failure to fight anti-Semitism and protect Jewish students \"astounding.\""),
    ("us04", "us04-b00010", 0, 132,
     "identify and take appropriate action to correct past misconduct by the Federal Government related to censorship of protected speech."),
]


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source(document_id):
    return json.loads((ROOT / "corpus" / "documents" / f"{document_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lens_id,expected", LEGACY_HASHES.items())
def test_existing_lens_definitions_remain_byte_equivalent_after_normalization(lens_id, expected):
    assert CRITERION_REGISTRY_VERSION == "3.0.0"
    assert canonical_hash(get_lens(lens_id)) == expected


def test_political_pack_is_separate_versioned_and_entirely_descriptive():
    pack = get_lens("political-framing")
    assert pack["version"] == "1.0.0"
    assert pack["source_url"] == "https://doi.org/10.1111/j.1460-2466.1993.tb01304.x"
    assert len(pack["criteria"]) == len({criterion["id"] for criterion in pack["criteria"]}) == 6
    assert {criterion["kind"] for criterion in pack["criteria"]} == {"descriptive"}
    pack["criteria"][0]["kind"] = "indicator"
    assert get_lens("political-framing")["criteria"][0]["kind"] == "descriptive"


@pytest.mark.parametrize("document_id,expected", SOURCE_HASHES.items())
def test_worked_packet_keeps_full_frozen_source_not_only_selected_excerpts(document_id, expected):
    document = source(document_id)
    assert canonical_hash(document["blocks"]) == document["content_sha256"] == expected
    assert len(document["blocks"]) > 5
    assert document["word_count"] > 400
    assert document["source_url"].startswith("https://")
    assert document["license"] in {"US-PUBLIC-DOMAIN-GOVERNMENT-WORK", "OGL-UK-3.0"}


@pytest.mark.parametrize("document_id,block_id,start,end,quote", EXAMPLES, ids=[item[1] + ":" + str(item[2]) for item in EXAMPLES])
def test_every_documented_real_quote_has_exact_unicode_evidence(document_id, block_id, start, end, quote):
    document = source(document_id)
    block = next(item for item in document["blocks"] if item["id"] == block_id)
    assert block["text"][start:end] == quote
    explanation = (ROOT / "docs" / "political-bias-methodology.md").read_text(encoding="utf-8")
    assert f"{block_id}[{start}:{end}]" in explanation
    assert quote in explanation


class ScriptedContractProvider:
    """Return deliberate fixture decisions; never an empirical model result."""
    name = "fixture"
    profile = {"provider": "fixture", "model": "scripted-contract-only", "mode": "remote",
               "context_limit": 32768, "source_pack_bytes": 12000}

    def __init__(self):
        self.calls = []

    async def generate(self, messages, schema, **kwargs):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        response = {"decisions": [], "details": []}
        for unit in payload["units"]:
            codes = []
            for key in payload["criterion_order"]:
                code = 1 if key.endswith("/evaluative_language") else 2 if key.endswith("/comparator_dependent_asymmetry") else 3
                codes.append(code)
                if code in {1, 2} and payload["phase"] != "classify":
                    response["details"].append({
                        "unit_id": unit["unit_id"], "criterion_key": key,
                        "evidence_sentence_ids": [unit["scored_sentences"][0]["id"]],
                        "title": "Fixture observation" if code == 1 else "Comparator not supplied",
                        "explanation": "This scripted fixture exercises descriptive contract handling." if code == 1 else "A matched comparator is not supplied, so a comparison cannot be established.",
                        "alternative": "This tests serialization and grounding, not a model's interpretive accuracy.",
                        "target": "Previous administration" if code == 1 else "",
                        "proposition": "The previous administration censored speech." if code == 1 else "",
                        "speaker": "President Donald J. Trump" if code == 1 else "Unknown",
                        "attribution": "author" if code == 1 else "unknown",
                        "speaker_stance": "supports" if code == 1 else "not_applicable",
                        "document_stance": "supports" if code == 1 else "not_applicable",
                        "hypothetical": False,
                        "certainty": "supported" if code == 1 else "insufficient_context",
                    })
            response["decisions"].append({"unit_id": unit["unit_id"], "results": codes})
        if payload["phase"] == "classify":
            response.pop("details")
        return {"content": response, "finish_reason": "stop",
                "usage": {"input_tokens": 20, "output_tokens": 20, "cost_usd": 0, "estimated": False}}


def test_descriptive_detection_and_uncertain_comparator_survive_contextual_contract():
    document = source("us04")
    unit = next(item for item in build_units(document) if "trampled" in item["text"])
    forged = get_lens("political-framing")
    forged["criteria"][1]["kind"] = "indicator"
    provider = ScriptedContractProvider()
    result = asyncio.run(analyze_units(document, [forged], provider, units=[unit]))
    assert result["coverage"]["complete"] and not result["errors"]
    assert len(result["assessments"]) == 6
    detected = next(item for item in result["assessments"] if item["criterion_id"] == "evaluative_language")
    assert detected["result"] == "meets_criterion"
    assert detected["criterion_kind"] == "descriptive" and detected["is_indicator"] is False
    comparison = next(item for item in result["assessments"] if item["criterion_id"] == "comparator_dependent_asymmetry")
    assert comparison["result"] == "uncertain" and comparison["certainty"] == "insufficient_context"
    assert comparison["is_indicator"] is False
    # Registry-owned policy reaches the provider, even for a forged browser pack.
    wire_criterion = next(item for item in provider.calls[0]["criteria"] if item["criterion_id"] == "comparator_dependent_asymmetry")
    assert "Without this comparator, return uncertain" in wire_criterion["description"]
    assert wire_criterion["kind"] == "descriptive"
    for observation in result["findings"]:
        block = next(item for item in document["blocks"] if item["id"] == observation["block_id"])
        assert block["text"][observation["start"]:observation["end"]] == observation["quote"]
