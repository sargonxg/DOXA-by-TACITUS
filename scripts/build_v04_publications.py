#!/usr/bin/env python3
"""Build additive, source-checked v0.4 publications without inference or DB writes.

Original v0.3 outputs are parents, never output destinations. Publication prose
is AI-authored editorial guidance. It is not independent human adjudication.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.lineage import content_sha256, lineage_record, lineage_status  # noqa: E402
from scripts.build_walkthrough import AUTHORSHIP, audit_snapshot, freeze_run  # noqa: E402

GENERATOR_VERSION = "v04-publications-1"
RESULTS = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}


def sha_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def p(text):
    return {"type": "p", "text": text}


def bullets(*items):
    return {"type": "list", "items": list(items)}


def table(headers, rows):
    return {"type": "table", "headers": headers, "rows": rows}


def page(title, *blocks, eyebrow="RESEARCH PREVIEW / EDITORIAL GUIDE"):
    return {"title": title, "eyebrow": eyebrow, "blocks": list(blocks)}


def citation(identity, title, authors, year, url, consequence, limitation, *, doi=None, venue=None):
    return {"id": identity, "type": "article-journal" if venue in {"Journal of Communication", "Language Resources and Evaluation", "Computational Linguistics", "TACL"} else "paper-conference",
            "title": title, "author": [{"family": name} for name in authors], "issued": {"date-parts": [[year]]},
            "URL": url, "DOI": doi, "container-title": venue or "Original academic publication",
            "review_scope": "Original publisher/anthology text and relevant sections; no claim of an exhaustive systematic review.",
            "app_consequence": consequence, "transfer_limit": limitation,
            "actually_tested_here": "Software/source-integrity tests and recorded operational runs only; no independent human interpretation labels."}


REFERENCES = [
    citation("jacobs2021", "Measurement and Fairness", ["Jacobs", "Wallach"], 2021, "https://arxiv.org/abs/1912.05511", "Declare construct, operationalization, exclusions and population before interpreting counts.", "Does not validate DOXA constructs or model predictions.", doi="10.1145/3442188.3445901", venue="FAccT"),
    citation("entman1993", "Framing: Toward Clarification of a Fractured Paradigm", ["Entman"], 1993, "https://fbaum.unc.edu/teaching/articles/J-Communication-1993-Entman.pdf", "Separate problem definition, causal interpretation, evaluation and remedy.", "A textual frame does not establish unfairness or audience influence.", doi="10.1111/j.1460-2466.1993.tb01304.x", venue="Journal of Communication"),
    citation("mohammad2016", "SemEval-2016 Task 6: Detecting Stance in Tweets", ["Mohammad", "Kiritchenko", "Sobhani", "Zhu", "Cherry"], 2016, "https://aclanthology.org/S16-1003/", "Specify target/proposition; distinguish sentiment, stance and lack of evidence.", "Tweet performance does not transfer to whole policy documents.", doi="10.18653/v1/S16-1003", venue="SemEval"),
    citation("card2015", "The Media Frames Corpus: Annotations of Frames Across Issues", ["Card", "Boydstun", "Gross", "Resnik", "Smith"], 2015, "https://aclanthology.org/P15-2072/", "Use explicit framing dimensions, evidence spans and preserved disagreement.", "Three original policy issues; original article redistribution requires source rights.", doi="10.3115/v1/P15-2072", venue="ACL-IJCNLP"),
    citation("recasens2013", "Linguistic Models for Analyzing and Detecting Biased Language", ["Recasens", "Danescu-Niculescu-Mizil", "Jurafsky"], 2013, "https://aclanthology.org/P13-1162/", "Inspect appraisal and qualified presupposition/entailment cues.", "Wikipedia neutrality edits do not validate a universal political-bias score.", venue="ACL"),
    citation("fan2019", "In Plain Sight: Media Bias Through the Lens of Factual Reporting", ["Fan", "White", "Sharma", "Su", "Choubey", "Huang", "Wang"], 2019, "https://aclanthology.org/D19-1664/", "Informational selection needs declared comparable sources and broader context.", "An arbitrary corpus absence is not deliberate omission.", doi="10.18653/v1/D19-1664", venue="EMNLP-IJCNLP"),
    citation("breck2004", "Playing the Telephone Game: Determining the Hierarchical Structure of Perspective and Speech Expressions", ["Breck", "Cardie"], 2004, "https://aclanthology.org/C04-1018/", "Preserve hierarchical direct/indirect sources of quoted and reported expression.", "Attribution structure is not access to a person's private beliefs.", venue="COLING"),
    citation("sauri2009", "FactBank: a corpus annotated with event factuality", ["Saurí", "Pustejovsky"], 2009, "https://link.springer.com/article/10.1007/s10579-009-9089-9", "Record how a source presents factuality rather than assert independent world truth.", "Publisher abstract/notes reviewed; a factuality label is not external fact-checking.", doi="10.1007/s10579-009-9089-9", venue="Language Resources and Evaluation"),
    citation("plank2022", "The ‘Problem’ of Human Label Variation: On Ground Truth in Data, Modeling and Evaluation", ["Plank"], 2022, "https://aclanthology.org/2022.emnlp-main.731/", "Preserve raw independent labels, plausible disagreement and later adjudication.", "A position paper motivates design, not an accuracy guarantee.", doi="10.18653/v1/2022.emnlp-main.731", venue="EMNLP"),
    citation("artstein2008", "Inter-Coder Agreement for Computational Linguistics", ["Artstein", "Poesio"], 2008, "https://aclanthology.org/J08-4004/", "Choose agreement measures by task and assumptions; report frequencies and uncertainty.", "Agreement is not validity; no pooled coefficient substitutes for field-specific evaluation.", doi="10.1162/coli.07-034-R2", venue="Computational Linguistics"),
    citation("rottger2021", "HateCheck: Functional Tests for Hate Speech Detection Models", ["Röttger", "Vidgen", "Nguyen", "Waseem", "Margetts", "Pierrehumbert"], 2021, "https://aclanthology.org/2021.acl-long.4/", "Test negation, counter-speech, identity references and other specific capabilities.", "Original seven target groups omit Jewish people; short functional cases do not validate long-document antisemitism.", doi="10.18653/v1/2021.acl-long.4", venue="ACL-IJCNLP"),
    citation("ribeiro2020", "Beyond Accuracy: Behavioral Testing of NLP Models with CheckList", ["Ribeiro", "Wu", "Guestrin", "Singh"], 2020, "https://aclanthology.org/2020.acl-main.442/", "Use minimum-functionality, invariance and directional test matrices.", "Passing engineered tests is not population accuracy.", doi="10.18653/v1/2020.acl-main.442", venue="ACL"),
    citation("deyoung2020", "ERASER: A Benchmark to Evaluate Rationalized NLP Models", ["DeYoung", "Jain", "Rajani", "Lehman", "Xiong", "Socher", "Wallace"], 2020, "https://aclanthology.org/2020.acl-main.408/", "Separate rationale plausibility/faithfulness from exact quotation integrity.", "DOXA's explanations are editorially inspectable, not proof of causal model reasoning.", doi="10.18653/v1/2020.acl-main.408", venue="ACL"),
    citation("liu2024", "Lost in the Middle: How Language Models Use Long Contexts", ["Liu", "Lin", "Hewitt", "Paranjape", "Bevilacqua", "Petroni", "Liang"], 2024, "https://transacl.org/index.php/tacl/article/view/5757", "Test position/context packing and preserve supplied-context receipts.", "Long-context QA/retrieval results do not establish the best DOXA context policy.", doi="10.1162/tacl_a_00638", venue="TACL"),
    citation("guo2017", "On Calibration of Modern Neural Networks", ["Guo", "Pleiss", "Sun", "Weinberger"], 2017, "https://proceedings.mlr.press/v70/guo17a.html", "Fit/evaluate confidence calibration on separate labelled data.", "Temperature scaling on classifiers is not a calibration certificate for verbal LLM confidence.", venue="ICML"),
    citation("xiong2024", "Can LLMs Express Their Uncertainty? An Empirical Evaluation of Confidence Elicitation in LLMs", ["Xiong", "Hu", "Lu", "Li", "Fu", "He", "Hooi"], 2024, "https://iclr.cc/virtual/2024/poster/18135", "Evaluate verbal confidence and failure prediction rather than assuming them reliable.", "Reported benchmark findings do not establish DOXA correctness probabilities.", venue="ICLR"),
    citation("tian2023", "Just Ask for Calibration: Strategies for Eliciting Calibrated Confidence Scores from Language Models Fine-Tuned with Human Feedback", ["Tian", "Mitchell", "Zhou", "Sharma", "Rafailov", "Yao", "Finn", "Manning"], 2023, "https://aclanthology.org/2023.emnlp-main.330/", "Treat confidence-elicitation methods as hypotheses to evaluate on the actual task.", "QA improvements do not transfer automatically to prejudice interpretation.", doi="10.18653/v1/2023.emnlp-main.330", venue="EMNLP"),
    citation("bender2018", "Data Statements for Natural Language Processing: Toward Mitigating System Bias and Enabling Better Science", ["Bender", "Friedman"], 2018, "https://aclanthology.org/Q18-1041/", "Publish source, language, population, sampling and intended-use limits.", "A one-publisher corpus is not a representative sample of public discourse.", doi="10.1162/tacl_a_00041", venue="TACL"),
]

FRAMEWORKS = {
    "ihra": {"title": "IHRA Working Definition of Antisemitism", "url": "https://holocaustremembrance.com/resources/working-definition-antisemitism", "year": 2016,
        "audit_status": "official_primary_page_access_blocked_in_current_source_audit", "locator": "Definition, contextual introduction and illustrative examples; current official-page retrieval blocked.",
        "exclusions": "Non-legally binding; examples require overall context; policy criticism similar to other countries is not automatically antisemitic.", "coverage": "DOXA's three criteria are a partial operational summary, not the complete working definition."},
    "jda": {"title": "Jerusalem Declaration on Antisemitism", "url": "https://jerusalemdeclaration.org/", "year": 2021,
        "audit_status": "original_definition_guidelines_and_FAQ_read", "locator": "Definition; General guidelines 1–5; Israel/Palestine guidelines 6–15; FAQ on legal use.",
        "exclusions": "Guidelines must be read together in context; political positions are not inherently antisemitic; not designed as a legal/quasi-legal speech code.", "coverage": "Three operational criteria retain the political-expression boundary but do not replace all fifteen guidelines."},
    "nexus": {"title": "The Nexus Document", "url": "https://nexusproject.us/nexus-resources/the-nexus-document/", "year": 2024,
        "audit_status": "original_definition_and_both_numbered_sections_read", "locator": "What Is Antisemitic? 2–4,7–9; What Is Not Antisemitic? 1,3–5; June 2024 update.",
        "exclusions": "Political opposition and disproportionate attention are not prima facie proof; standards and source context matter.", "coverage": "Three operational criteria; no vote across frameworks and no legal determination."},
    "anti-muslim-hostility": {"title": "A Definition of Anti-Muslim Hostility", "url": "https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility", "year": 2026,
        "audit_status": "original_sections_6_7_8_read", "locator": "Section 6 paragraphs 1–3 and protected-expression list; section 8 notes on use.",
        "exclusions": "England; non-statutory; no change to criminal/equality law; criticism/ridicule of religion protected; required intent/unlawfulness cannot be presumed.", "coverage": "Three textual operational criteria cannot establish every legal/contextual condition from one passage."},
    "anti-muslim-racism": {"title": "Islamophobia: towards a working definition", "url": "https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition", "year": 2025,
        "pdf_url": "https://cdn.prod.website-files.com/61488f992b58e687f1108c7c/6870ebe3af5932d0c3f20824_Islamophobia_Towards%20a%20working%20definition_Runnymede%20July%202025.pdf",
        "pdf_sha256": "ccdb6531b43a1f398fcbfa2a88a3aee6da42c73e339517cb88e64728f8ea86fa", "pdf_pages": 19,
        "audit_status": "original_19_page_briefing_relevant_sections_read", "locator": "PDF p4/printed p3 recommendation; PDF pp5–7 racialization; pp7–10 institutional/structural effects; p12/printed p11 free speech; p14/printed p13 recommendation.",
        "exclusions": "Protect Muslims/perceived Muslims rather than immunize Islam or governments from criticism; structural effects need not require deliberate intent; a paragraph does not establish a population-level pattern.",
        "coverage": "Partial operationalization. Intersectionality, policy evidence and comparable outcomes require additional evidence. Full PDF is linked, not redistributed."},
}


def framework_audit(packs: dict) -> list[dict]:
    rows = []
    # These are scholarly precedents for operational research choices, not
    # claims that the papers prescribe or validate DOXA's exact rubric.
    scholarly_mappings = {
        "discourse/stance": [("mohammad2016", "Task definition: favor/against/neither toward a specified target; stance and sentiment distinction.")],
        "discourse/framing": [("entman1993", "p52: problem definition, causal interpretation, moral evaluation and treatment recommendation."), ("card2015", "Annotation methodology and Table1 framing dimensions; coding disagreement.")],
        "discourse/presupposition": [("recasens2013", "Section2 linguistic indicators; epistemological/framing categories. DOXA's uncertainty requirement is an added operational safeguard.")],
        "discourse/rhetoric": [("recasens2013", "Linguistic-feature taxonomy and framing/epistemological distinction; not a complete rhetoric taxonomy."), ("entman1993", "pp52–53 salience and framing functions.")],
        "political-framing/expressed_policy_position": [("mohammad2016", "Task definition and distinction between target-specific stance and sentiment."), ("entman1993", "p52 treatment recommendation function.")],
        "political-framing/evaluative_language": [("recasens2013", "Section2 categories of linguistic cues; appraisal is evidence to inspect, not proof of objective unfairness."), ("entman1993", "p52 moral evaluation function.")],
        "political-framing/agency_causal_responsibility": [("entman1993", "p52 causal interpretation function."), ("sauri2009", "Original publisher abstract/notes: source-relative event factuality. Full paper not audited here.")],
        "political-framing/source_voice_foregrounding": [("breck2004", "Sections1–3: nested direct/indirect private-state and speech sources; hierarchical source structure."), ("fan2019", "Dataset/annotation design: lexical versus informational bias across three sources covering matched events.")],
        "political-framing/comparator_dependent_asymmetry": [("fan2019", "Sections3–4 matched-event, multiple-outlet informational-bias annotation. Comparator guardrails are DOXA operational constraints, not an automatic fairness metric.")],
        "political-framing/presupposition_hypothesis": [("recasens2013", "Section2 epistemological/framing language cues. The hypothesis and alternative-reading contract is a DOXA operational choice.")],
        "language-norms/norm_claim": [("card2015", "Table1 morality, cultural identity and public-opinion frames provide descriptive precedent; no validated political-correctness construct.")],
        "language-norms/speech_boundary": [("card2015", "Table1 fairness/equality, legality and constitutionality frames; not an official free-expression classification.")],
        "language-norms/institutional_expectation": [("card2015", "Table1 capacity/resources, policy prescription and political frames; institutional-language rule coding is a DOXA research choice.")],
    }
    academic = {reference["id"]: reference for reference in REFERENCES}
    concise = {
        "discourse/stance": "Mohammad2016 task definition: target-specific stance versus sentiment.",
        "discourse/framing": "Entman1993 p52 four functions; Card2015 annotation/Table1.",
        "discourse/presupposition": "Recasens2013 §2.3 epistemological cues; added uncertainty safeguards.",
        "discourse/rhetoric": "Recasens2013 §2.3 framing/epistemological cues; Entman1993 pp52–53.",
        "political-framing/expressed_policy_position": "Mohammad2016 target-specific stance; Entman1993 p52 remedy.",
        "political-framing/evaluative_language": "Recasens2013 §2.3 linguistic cues; Entman1993 p52 evaluation.",
        "political-framing/agency_causal_responsibility": "Entman1993 p52 causal interpretation; FactBank source-relative factuality.",
        "political-framing/source_voice_foregrounding": "Breck2004 nested sources; Fan2019 matched-event informational bias.",
        "political-framing/comparator_dependent_asymmetry": "Fan2019 matched-event design; DOXA adds explicit-comparator safeguards.",
        "political-framing/presupposition_hypothesis": "Recasens2013 §2.3 cues; DOXA adds alternative-reading requirements.",
        "language-norms/norm_claim": "Card2015 Table1 morality/cultural identity/public opinion: precedent only.",
        "language-norms/speech_boundary": "Card2015 Table1 fairness/legality: no validated expression-harm scale.",
        "language-norms/institutional_expectation": "Card2015 Table1 policy/capacity/political frames: research precedent.",
    }
    locators = {
        "ihra/anti_jewish_generalization": "Definition and examples concerning stereotyping/conspiracies/dehumanization (current-page access limitation retained).",
        "ihra/collective_responsibility": "Illustrative example holding Jews collectively responsible for Israel (current-page access limitation retained).",
        "ihra/israel_related_context": "Context introduction and Israel-related examples; no criterion from Israel mention alone (access limitation retained).",
        "jda/jews_as_jews": "Definition; General guidelines 1–5.", "jda/tropes_collective_guilt": "General 2,4; Israel/Palestine 6,7,8.",
        "jda/political_expression_boundary": "Guidelines 11–15 read together with 6–10; FAQ on free debate/legal use.",
        "nexus/anti_jewish_tropes": "What Is Antisemitic? 2,3; definition.", "nexus/collective_guilt": "What Is Antisemitic? 3(c),4.",
        "nexus/opposition_boundary": "What Is Not Antisemitic? 1,3,4,5.",
        "anti-muslim-hostility/targeted_hostility": "Section 6, first definition paragraph; section 8 context and protected-expression conditions.",
        "anti-muslim-hostility/stereotyping_and_intent": "Section 6, second definition paragraph; section 8 discussion of intention and non-criminal conduct.",
        "anti-muslim-hostility/discrimination_claim": "Section 6, third definition paragraph; section 8 deliberately targeted subset of unlawful discrimination.",
        "anti-muslim-racism/racialization": "PDF p4/printed p3 longer definition and examples; PDF pp5–7 racialization discussion.",
        "anti-muslim-racism/exclusion": "PDF p4/printed p3 longer definition; PDF pp7–10 institutional and structural forms.",
        "anti-muslim-racism/institutional_pattern": "PDF pp7–10/printed pp6–9; comparison/evidence must establish a pattern rather than merely assert it.",
    }
    for pack in packs.values():
        framework = FRAMEWORKS.get(pack["id"])
        for criterion in pack["criteria"]:
            key = pack["id"] + "/" + criterion["id"]
            precedents = [{"id": identity, "url": academic[identity]["URL"], "locator": locator,
                           "relation": "scholarly_precedent_not_validation_of_exact_operational_criterion"}
                          for identity, locator in scholarly_mappings.get(key, [])]
            rows.append({"criterion_key": key, "frozen_lens_version": pack["version"], "kind": criterion["kind"],
                "operational_description": criterion["description"], "source_url": framework["url"] if framework else precedents[0]["url"] if precedents else pack.get("source_url"),
                "source_locator": locators.get(key, " ".join(item["id"] + ": " + item["locator"] for item in precedents)),
                "source_summary": concise.get(key, locators.get(key)),
                "academic_precedents": precedents,
                "source_audit_status": framework["audit_status"] if framework else "original_academic_precedent_and_author_operational_rubric",
                "exclusions": framework["exclusions"] if framework else pack.get("context_rules", ""),
                "coverage_limit": framework["coverage"] if framework else "Descriptive research inquiry; no universal ideology, fairness or political-correctness scale.",
                "recorded_processing_coverage_available": pack["id"] != "language-norms",
                "recorded_model_profiles": ["v03-political"] if pack["id"] == "political-framing" else [] if pack["id"] == "language-norms" else ["v03-institutional", "v03-news"],
                "independent_human_interpretation_validation": False,
                "semantic_change_to_v03": False})
    return rows


CASE_SPECS = [
    ("institutional", "32459e910518c074c51980f089823604", "Protective guidance contains an example voice", "The protective rule and the illustrative character are different sources of a claim. A framing match does not mean the EEOC endorses a customer's discriminatory preference.", "Which words state the rule, which belong to Jon's example, and is the hypothetical flag adequate for this mixed unit?"),
    ("institutional", "57bf8bbb0482381bb196ed6068feabc4", "A committee reports discrimination", "A government response reproduces a committee recommendation and reported experiences. Reporting a discrimination claim is distinct from expressing anti-Muslim hostility.", "Trace the government, committee and witnesses separately. Does the model's supportive document stance apply to the right proposition?"),
    ("institutional", "f93394d9d22c28b8127cd3970615b911", "A political-expression boundary is not an indicator", "The JDA boundary is a limit on classification. A boundary match does not turn every surrounding statement into acceptable conduct or settle another framework.", "Which political proposition falls within this boundary, and which separate claims need separate assessment?"),
    ("institutional", "35a1cdcbaace24ccf9582edf493a8cf6", "Reported harm and unestablished intent", "Testimony can report a harmful experience while a criterion-specific intent requirement remains unresolved. Uncertainty does not deny that harm occurred.", "Which element is missing: target, conduct, stereotyping or intent? What additional source evidence could resolve it?"),
    ("political", "d6a4a1bf97d03d50b05186eeba945dd6", "An evaluative metaphor in an executive order", "A metaphor characterizes an opponent's alleged conduct. Detecting appraisal establishes neither factual falsity nor hidden motive.", "What is asserted, who is evaluated, and what comparator would be needed for a claim of unequal standards?"),
    ("political", "2d44e6da2e3f70cb876ef6f1bdc4301b", "An explicit policy remedy", "An executive order supports a named legal-enforcement policy. A clear stance toward that remedy is more defensible than an inferred partisan identity.", "Does this source exclude other remedies, or does the quotation show only one selected instrument?"),
    ("institutional", "57a24379102d33225e54ecc89a0f444e", "A definition versus an instance", "The passage defines racialization. Whether a definition counts as an instance of the criterion depends on the declared measurement scope; this disagreement is not adjudicated here.", "Are we counting defined concepts, represented conduct, or author-endorsed expression?", "verifier", "ca3e1d917bef47607e80e40a68d62bc1"),
    ("institutional", "4e5d1bc843d1e80f689cbc23df298a6f", "A statistical association and an Israel-related criterion", "A recorded hate-crime trend mentions the Israel-Hamas conflict. Mentioning context and satisfying a contextual prejudice criterion are distinct questions.", "What additional anti-Jewish expression or discriminatory standard does this particular criterion require? Does 'since' establish causation?", "verifier", "e5486663af0fb4685a7036b84f05ef36"),
    ("institutional", "7b9c226fb4229c85c248fe974f801493", "The author, a post and a quoted video", "Both models detect a trope, but disagree about the attributed speaker. The source footnote says a post shares a video containing the call; agreement on outcome does not settle attribution.", "Trace author → post → video voice. Is 'author' compatible with the explicit quotation source?", "verifier", "66ef20dd11e36b743f09674304633182"),
    ("news", "87cdc2ef04106000a28088428d071791", "News reporting contains hostile expression", "A news article can report hostile material without endorsing it. Keep the represented speaker's claim and the journalist's stance separate.", "Which voice is responsible for the quoted expression and how does the article respond to it?"),
    ("news", "7df34e61b1b71262af27473c96b579c3", "A news political-expression boundary", "A boundary observation concerns a specified political proposition, not a blanket judgment about a publisher or a person's intentions.", "Does the surrounding source distinguish policy criticism from a claim directed at Jews as Jews?"),
    ("news", "09f824e6a4af44ac297dc8ec25fc4c85", "An institutional-pattern hypothesis stays uncertain", "A story can motivate inquiry into institutions while lacking the comparison needed to establish a repeated structural pattern.", "Which comparison, baseline or additional institutional evidence would make this pattern assessable?"),
    ("news", "00005ffc46023612f8d143b4a3bdc641", "A real explicit negative without a detailed rationale", "The stored decision is does_not_meet. Source-unit anchors are software provenance, not an invented model explanation of why it decided negatively.", "What does this specific negative establish about this unit, and what does it leave unmeasured elsewhere?"),
    ("news", "000211f6208aa907d36a22e3b06239f6", "Not applicable is a distinct recorded outcome", "The recorded applicability judgment is not a measured absence of every possible issue. It should not be merged silently with missing or failed work.", "Is the criterion applicable to the supplied unit? Would a reviewer distinguish a determinate non-match from an out-of-scope question?"),
]


def collect_cases(root: Path = ROOT) -> dict:
    news_state = json.loads((root / "benchmarks/v3-news-final-summary.json.checkpoint.json").read_text())
    configs = {"institutional": (root / "data/v3-institutions-final", "074539f031e64dc28f03384481563a85"),
               "verifier": (root / "data/v3-institutions-final", "ad5046e9849d4392a8160d55de9beacd"),
               "political": (root / "data/v3-political", "8d11a4b04f0c444cab31152c84e7e74e"),
               "news": (Path(news_state["data_dir"]), news_state["run_id"])}
    captures = {}
    for key, (directory, run_id) in configs.items():
        identities = [spec[1] for spec in CASE_SPECS if spec[0] == key] + [spec[6] for spec in CASE_SPECS if len(spec) > 5 and spec[5] == key]
        captures[key] = freeze_run(directory, run_id, assessment_ids=identities, include_all_outcomes=True, include_unresolved=key == "institutional")
    cases = []
    for number, spec in enumerate(CASE_SPECS, 1):
        snapshot = captures[spec[0]]
        assessment = next(item for item in snapshot["assessments"] if item["id"] == spec[1])
        source = next(doc for doc in snapshot["documents"] if doc["id"] == assessment["document_id"])
        value = {"id": f"case-{number:02d}", "title": spec[2], "source": source, "run": snapshot["run"], "assessment": assessment,
                 "framework": snapshot["criterion_definitions"][assessment["criterion_key"]],
                 "editorial": {"authorship": AUTHORSHIP, "reading": spec[3], "question": spec[4], "review_status": "pending_independent_human_review"},
                 "request_capture": lineage_status()}
        if len(spec) > 5:
            second = captures[spec[5]]
            value["comparison"] = {"run": second["run"], "assessment": next(item for item in second["assessments"] if item["id"] == spec[6]),
                                   "notice": "A second recorded model reading, not adjudication or reference truth."}
        cases.append(value)
    primary = captures["institutional"]
    unresolved = next((unit for unit in primary["unresolved_units"] if unit["unit_id"] == "u_581dfa046319abeee367ee1c"), primary["unresolved_units"][0])
    source = next(doc for doc in primary["documents"] if doc["id"] == unresolved["document_id"])
    cases.append({"id": "case-15", "title": "An unresolved decision is not a negative", "source": source, "run": primary["run"],
                  "assessment": None, "unresolved_unit": unresolved,
                  "editorial": {"authorship": AUTHORSHIP, "reading": "The source unit retains some accepted peer criteria, but at least one requested decision is missing. No result, explanation or confidence is invented for the gap.",
                                "question": "Which requested criterion remains unresolved and what supplied context or retry/review would address it?", "review_status": "pending_independent_human_review"},
                  "request_capture": lineage_status()})
    return {"schema_version": "doxa-v04-case-atlas-1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "authorship": AUTHORSHIP, "selection": "Fifteen purposive real-source teaching cases; four outcomes plus a separately represented unresolved unit. No prevalence or accuracy sample.",
            "cases": cases, "captures": captures, "independent_human_labels": False, "interpretation_accuracy": None}


def audit_atlas(atlas: dict) -> dict:
    for capture in atlas["captures"].values():
        audit_snapshot(capture)
    counts = Counter(case["assessment"]["result"] if case["assessment"] else "unresolved" for case in atlas["cases"])
    if len(atlas["cases"]) != 15 or not RESULTS <= counts.keys() or counts["unresolved"] != 1:
        raise ValueError("The real atlas must retain fifteen cases, every explicit outcome and one distinct missing decision")
    checked = 0
    for case in atlas["cases"]:
        blocks = {block["id"]: block["text"] for block in case["source"]["blocks"]}
        for item in [case["assessment"], case.get("comparison", {}).get("assessment")]:
            if item is None:
                continue
            if item["recorded_payload_sha256"] != content_sha256(item["recorded_payload"]):
                raise ValueError("Original assessment payload changed")
            for ref in item["evidence_refs"]:
                if blocks[ref["block_id"]][ref["start"]:ref["end"]] != ref["quote"]:
                    raise ValueError("Atlas evidence failed exact Unicode source validation")
                checked += 1
            if item.get("detail_supplied") is False and item["origin_labels"]["model_interpretation"] != "not_recorded":
                raise ValueError("A compact negative must not acquire an invented model rationale")
        if not case["assessment"] and case["unresolved_unit"]["result"] is not None:
            raise ValueError("Missing work cannot be relabelled as a criterion outcome")
    return {"valid": True, "cases": 15, "outcomes": dict(counts), "checked_evidence_references": checked,
            "independent_human_labels": False, "actual_request_capture": "not_recorded_for_frozen_historical_examples"}


def archive_inputs(root: Path) -> list[Path]:
    files = []
    for name in ("v3-institutions", "v3-news", "v3-political", "v3-local-news", "v3-independent-low-verification"):
        path = root / "benchmarks" / name / "showcase.json"
        manifest = json.loads(path.read_text())
        files.extend([path, *[path.parent / part["file"] for part in manifest["parts"]]])
    return files


def audit_archives(root: Path, *, reuse=True) -> dict:
    """Stream original archives; check every source version and quoted span."""
    receipt_path = root / "examples/v04-corpus-verification.json"
    inputs = {str(path.relative_to(root)): sha_file(path) for path in archive_inputs(root)}
    # Corpus metadata files are small enough to hash one by one; bodies are
    # read per source below rather than accumulating the 1.5GB archive in RAM.
    source_files = {path.stem: path for path in (root / "corpus/documents").glob("*.json")}
    source_files.update({path.stem: path for path in (root / "corpus/news/metadata").glob("*.json")})
    inputs["source_metadata_tree_sha256"] = content_sha256({identity: sha_file(path) for identity, path in sorted(source_files.items())})
    if reuse and receipt_path.exists():
        old = json.loads(receipt_path.read_text())
        if old.get("inputs") == inputs and old.get("status") == "passed":
            return old
    summaries = []
    for name in ("v3-institutions", "v3-news", "v3-political", "v3-local-news", "v3-independent-low-verification"):
        path = root / "benchmarks" / name / "showcase.json"
        manifest = json.loads(path.read_text())
        counts, states, outcomes = Counter(), Counter(), Counter()
        seen = set()
        for part in manifest["parts"]:
            archive = path.parent / part["file"]
            if sha_file(archive) != part["sha256"] or archive.stat().st_size != part["bytes"]:
                raise ValueError("A frozen portable archive part changed")
            documents = raw_size = 0
            with gzip.open(archive, "rb") as stream:
                for line in stream:
                    raw_size += len(line)
                    record = json.loads(line)
                    identity = record["source_id"]
                    if identity in seen or identity not in source_files:
                        raise ValueError("A portable archive has a duplicate or unavailable source")
                    seen.add(identity)
                    source = json.loads(source_files[identity].read_text())
                    blocks = source["blocks"]
                    if record["sha256"] != source["content_sha256"] or content_sha256(blocks) != record.get("blocks_sha256", record["sha256"]):
                        raise ValueError("A portable archive does not match its frozen extracted source")
                    by_id = {block["id"]: block["text"] for block in blocks}
                    for assessment in record["assessments"]:
                        if assessment["result"] not in RESULTS:
                            raise ValueError("An unknown outcome cannot become a publication result")
                        outcomes[assessment["result"]] += 1
                        for ref in assessment["evidence_refs"]:
                            text = by_id.get(ref["block_id"])
                            if text is None or type(ref.get("start")) is not int or type(ref.get("end")) is not int or not 0 <= ref["start"] < ref["end"] <= len(text) or ref.get("quote") != text[ref["start"]:ref["end"]]:
                                raise ValueError("A frozen archive reference does not match source text")
                            counts["audited_references"] += 1
                    states.update(item["status"] for item in record["unit_statuses"])
                    counts.update({"documents": 1, "assessments": len(record["assessments"]),
                                   "source_characters": sum(len(block["text"]) for block in blocks), "words": source["word_count"]})
                    documents += 1
            if documents != part["documents"] or raw_size != part["uncompressed_bytes"]:
                raise ValueError("Archive source count or decompressed length differs from its receipt")
        if dict(counts) != manifest["counts"] or dict(states) != manifest["unit_states"] or dict(outcomes) != manifest["outcomes"]:
            raise ValueError("Recorded corpus summary differs from the streamed original archive")
        run = manifest["run"]
        packs = run["config"].get("lens_snapshots", [])
        criteria = sum(len(pack["criteria"]) for pack in packs)
        if not criteria:
            from doxa.lenses import get_lens
            criteria = sum(len(get_lens(identity)["criteria"]) for identity in run["config"]["lens_ids"])
        total_units = sum(states.values())
        summaries.append({"archive": str(path.relative_to(root)), "archive_sha256": inputs[str(path.relative_to(root))],
            "run_id": run["id"], "status": run["status"], "model": run["config"].get("model"),
            "counts": dict(counts), "unit_states": dict(states), "outcomes": dict(outcomes),
            "selected_units": total_units, "requested_decisions": total_units * criteria,
            "retained_decisions": counts["assessments"], "unresolved_decisions": total_units * criteria - counts["assessments"],
            "fully_assessed_documents": run.get("progress", {}).get("documents_complete"),
            "usage": run.get("usage", {}), "budget_accounted_usd": run.get("spent_usd"),
            "scope_notice": "Verifier coverage is its selected subset, not whole source documents." if name.endswith("verification") else "All selected extracted source text; retained decisions and gaps remain separate."})
        print(f"Verified {name}: {counts['documents']} sources, {counts['assessments']} outcomes, {counts['audited_references']} source references", flush=True)
    result = {"schema_version": "doxa-v04-corpus-audit-1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "status": "passed", "inputs": inputs, "archives": summaries,
              "interpretation_accuracy": None, "independent_human_labels": False,
              "notice": "Every archive part, source hash and reference was checked in a bounded streaming pass. This verifies integrity/counts, not semantic correctness."}
    save_json(receipt_path, result)
    return result


def source_facts(case):
    source = case["source"]
    meta = source.get("metadata", {})
    translators = "; ".join(person["name"] for person in meta.get("translators", []) if person.get("name")) or "None credited in source metadata"
    return [("Title", source["title"]), ("Credited author", source.get("author", "Unknown")),
            ("Translator", translators), ("Publication date", source.get("published_at", "Unknown")),
            ("Source ID / license", str(meta.get("source_id", source["id"])) + " / " + source.get("license", "Unknown")),
            ("Frozen extracted hash", source["sha256"])]


def selected_window(case) -> dict:
    """An explicitly editorial display window; the full evidence stays frozen."""
    assessment = case["assessment"]
    if assessment:
        refs = assessment["evidence_refs"]
        # Negative anchors often cover the complete scored unit. A layout
        # excerpt is selected for readable prose, not offered as model rationale.
        first = next((ref for ref in refs if len(ref["quote"]) >= 80), refs[0])
        if case["id"] == "case-08":
            first = next(ref for ref in case["comparison"]["assessment"]["evidence_refs"] if ref["block_id"] == "uk04-b00011")
    else:
        first = case["unresolved_unit"]["source_unit"]["sentences"][0]
    block = next(block for block in case["source"]["blocks"] if block["id"] == first["block_id"])
    start, end = first["start"], min(first["end"], first["start"] + 650)
    return {"block_id": block["id"], "start": start, "end": end, "quote": block["text"][start:end],
            "context": block["text"][max(0, start - 200):min(len(block["text"]), end + 350)],
            "page": block.get("page"), "paragraph": block.get("paragraph"),
            "origin": "Editorial display excerpt from validated source; not additional model evidence or a model-written rationale."}


def reference_blocks(selected=None):
    references = REFERENCES if selected is None else [reference for reference in REFERENCES if reference["id"] in selected]
    return [{"type": "reference", "reference": reference} for reference in references]


def diagram(name):
    return {"type": "diagram", "name": name}


def corpus_table(audit):
    return table(["Recorded scope", "Sources", "Accepted / selected units", "Retained / requested decisions", "State"],
        [[row["archive"].split("/")[1], row["counts"]["documents"], f"{row['unit_states'].get('accepted', 0):,} / {row['selected_units']:,}",
          f"{row['retained_decisions']:,} / {row['requested_decisions']:,}", row["status"]] for row in audit["archives"]])


def render_block(block):
    kind = block["type"]
    if kind == "p":
        return "<p>" + escape(block["text"]) + "</p>"
    if kind == "quote":
        return '<blockquote>' + escape(block["text"]) + '</blockquote>'
    if kind == "label":
        return '<p class="layer">' + escape(block["text"]) + '</p>'
    if kind == "list":
        return "<ul>" + "".join("<li>" + escape(str(item)) + "</li>" for item in block["items"]) + "</ul>"
    if kind == "table":
        return "<table><thead><tr>" + "".join("<th>" + escape(str(item)) + "</th>" for item in block["headers"]) + "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + escape(str(item if item is not None else "Not recorded")) + "</td>" for item in row) + "</tr>" for row in block["rows"]) + "</tbody></table>"
    if kind == "code":
        return "<pre>" + escape(block["text"]) + "</pre>"
    if kind == "link":
        return '<p class="source-link"><a href="' + escape(block["url"], quote=True) + '">' + escape(block["text"]) + ' ↗</a></p>'
    if kind == "diagram":
        svg = (ROOT / "docs/images" / ("v04-" + block["name"] + ".svg")).read_text()
        return '<figure>' + svg + '<figcaption>Diagram is a methodological schematic, not a plot of measured model performance.</figcaption></figure>'
    if kind == "reference":
        ref = block["reference"]
        return '<article class="reference" id="ref-' + escape(ref["id"]) + '"><p><strong>' + escape("; ".join(author["family"] for author in ref["author"])) + " (" + str(ref["issued"]["date-parts"][0][0]) + "). " + escape(ref["title"]) + '.</strong> <a href="' + escape(ref["URL"], quote=True) + '">Primary source ↗</a></p><p class="small">' + escape(ref["app_consequence"] + " " + ref["transfer_limit"]) + '</p></article>'
    raise ValueError("Unknown publication content block")


CSS = """
@page {size:A4; margin:19mm 18mm 18mm; @top-left{content:'DOXA / TACITUS';font:8pt sans-serif;letter-spacing:2px;color:#476b5b} @bottom-left{content:'v0.4 · AI-authored reading guidance · independent human labels pending';font:7pt sans-serif;color:#4d665c} @bottom-right{content:counter(page);font:9pt sans-serif;color:#224f3e}}
*{box-sizing:border-box}body{margin:0;color:#173e30;background:#f3f4ee;font:11pt/1.48 'DejaVu Sans',sans-serif}main{max-width:970px;margin:auto}.publication-page{background:#fff;padding:35px;margin:30px 0;break-before:page;position:relative}h1{font:normal 30pt/1.12 'DejaVu Serif',serif;letter-spacing:-1px;margin:18px 0 22px;color:#173e30}h2{font:normal 23pt/1.18 'DejaVu Serif',serif;margin:12px 0 20px}.eyebrow{font:700 8pt sans-serif;letter-spacing:1.8px;color:#6b784e;border-top:3px solid #244f3b;padding-top:14px}.layer{font-size:8pt;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:#76532a;background:#f3efdf;padding:9px 12px}.small,figcaption{font-size:8.3pt;color:#496456;line-height:1.4}p{margin:12px 0}blockquote{border-left:3px solid #718e93;padding:10px 17px;background:#f1f4f1;font:12.2pt/1.5 'DejaVu Serif',serif;margin:17px 0;white-space:pre-wrap}table{width:100%;border-collapse:collapse;font-size:8.6pt;margin:15px 0;table-layout:fixed}th{text-align:left;background:#204d3a;color:white;padding:8px}td{vertical-align:top;border-bottom:1px solid #d1dacf;padding:7px;overflow-wrap:anywhere}th:first-child,td:first-child{width:31%}pre{font:8pt/1.4 monospace;background:#eff3ed;padding:13px;white-space:pre-wrap;overflow-wrap:anywhere}a{color:#245d6a;text-decoration:underline;text-underline-offset:2px}li{padding:4px 0}ul{padding-left:22px}figure{margin:20px 0}svg{width:100%;height:auto}.reference p{margin:8px 0}.reference{padding:7px 0;border-bottom:1px solid #d1dacf;break-inside:avoid}.source-link{font-size:9pt}header.cover{background:#204d3a;color:#f5f5eb;padding:35px 30px;margin:25px 0}header.cover h1{color:#f5f5eb}header.cover p{color:#e5ead9}@media print{body{background:white}.publication-page{margin:0;padding:0}.publication-page:first-child{break-before:auto}p,li{orphans:3;widows:3}h1,h2,.layer{break-after:avoid}table,blockquote,pre,figure{break-inside:avoid}}@media(max-width:700px){.publication-page{padding:22px;margin:10px}h1{font-size:25pt}table{font-size:8pt}}
"""


def render_publication(title: str, pages: list[dict], *, style="reader") -> str:
    sections = []
    for number, item in enumerate(pages, 1):
        sections.append('<section class="publication-page" id="page-' + str(number) + '"><p class="eyebrow">' + escape(item["eyebrow"]) + '</p><h1>' + escape(item["title"]) + '</h1>' + "".join(render_block(block) for block in item["blocks"]) + '</section>')
    compact = ("body{font-size:10.1pt;line-height:1.38}h1{font-size:23pt;margin:12px 0 16px}p{margin:8px 0}table{font-size:8pt;margin:11px 0}td,th{padding:5px}.layer{padding:6px 10px;font-size:7.6pt}blockquote{font-size:11.3pt;line-height:1.4;padding:8px 14px;margin:12px 0}"
               if style == "atlas" else "h1{font-size:26pt}p{margin:10px 0}.reference p{font-size:9pt;margin:6px 0}.reference .small{font-size:8pt}.reference{padding:5px 0}" if style == "technical" else "")
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src \'none\'"><title>' + escape(title) + '</title><style>' + CSS + compact + '</style></head><body><main>' + "".join(sections) + '</main></body></html>'


def manuscript(title: str, pages: list[dict]) -> str:
    text = ["# " + title, "", AUTHORSHIP, ""]
    for number, item in enumerate(pages, 1):
        text.extend(["<!-- page -->", "", f"## {number:02d}. {item['title']}", ""])
        for block in item["blocks"]:
            kind = block["type"]
            if kind in {"p", "label"}:
                text.extend([block["text"], ""])
            elif kind == "quote":
                text.extend(["> " + block["text"].replace("\n", "\n> "), ""])
            elif kind == "list":
                text.extend([*["- " + str(item) for item in block["items"]], ""])
            elif kind == "table":
                text.append("| " + " | ".join(block["headers"]) + " |")
                text.append("| " + " | ".join("---" for _ in block["headers"]) + " |")
                text.extend("| " + " | ".join(str(value if value is not None else "Not recorded").replace("|", "\\|").replace("\n", " ") for value in row) + " |" for row in block["rows"])
                text.append("")
            elif kind == "code":
                text.extend(["```text", block["text"], "```", ""])
            elif kind == "link":
                text.extend([f"[{block['text']}]({block['url']})", ""])
            elif kind == "diagram":
                text.extend([f"![Methodological schematic](images/v04-{block['name']}.svg)", ""])
            elif kind == "reference":
                ref = block["reference"]
                text.extend([f"**{ref['id']}** — {ref['title']} ({ref['issued']['date-parts'][0][0]}). {ref['URL']}", ref["app_consequence"] + " " + ref["transfer_limit"], ""])
    return "\n".join(text)


def reader_pages(atlas, audit):
    return [
        page("Reading public discourse with evidence", p("DOXA by TACITUS · v0.4 reader explainer"),
             p("A practical guide to examining policy, news and institutional language without confusing an exact quotation with a correct interpretation. This edition explains what the workbench processes, which questions its lenses ask, and where a human reading is still needed."),
             diagram("measurement-layers"),
             p("The examples are real, attributed public documents and licensed news articles. The analytical readings were produced in recorded model runs. This publication adds AI-authored editorial guidance; it does not turn those readings into independent human judgments or alter their original results."),
             p("Read this guide for the main ideas, the technical annex for the exact contracts and source audit, and the fifteen-case atlas for inspectable passages. All three share a hash-indexed evidence pack. No paid inference was requested to produce these publications.")),
        page("A route through the three publications", p("Begin with the question you want to answer. A researcher may investigate recurring frames; a newsroom may examine attribution and evidence standards; a university may teach the difference between a reported claim and its endorsement. These audiences need different explanations of the same evidence, not silently different definitions."),
             table(["Publication", "Use it to"], [["Reader explainer", "Understand the measurement layers, outcomes, practical workflow and limits."], ["Technical annex", "Inspect criteria, source paragraphs, versions, processing, lineage and evaluation design."], ["Fifteen-case atlas", "Compare source text, framework conditions, model records, review state and editorial questions."], ["Evidence pack", "Verify parents, hashes, source locators, payloads, source audits and missing captures."]]),
             p("The case sequence is purposive. It includes protective guidance, reported discrimination, political boundaries, uncertainty, appraisal, policy remedies, definitions, statistical context, nested quotations, news reporting and explicit non-matches. It also includes missing work. It is a teaching set, not a representative prevalence sample."),
             p("A green design or a polished PDF is not an assurance of accuracy. Each case retains the original result and review state. Where no detailed model explanation exists, the guide says so. Where the original request was not captured, a reconstructed template cannot stand in for it.")),
        page("Name the thing being measured", p("A document may describe discrimination, reproduce a hostile expression, argue against it, or adopt a proposition. A count of passages discussing prejudice measures something different from a count of authorial endorsements. Before comparing authors or institutions, choose and name the observation you intend to count."),
             table(["Measurement view", "What it can describe"], [["Contains or describes", "A text defines, mentions or reports relevant conduct or ideas."], ["Represented expression", "An identified speaker is quoted or reported expressing a proposition."], ["Document relationship", "The document asserts, supports, rejects, defines or leaves unresolved that particular proposition."], ["Private belief or social prevalence", "Not established by an automated passage assessment."]]),
             p("The v0.4 semantic layer is additive. It can record evidence function, proposition type, attribution chains and missing-context reasons. It does not silently change a v0.3 outcome or promote an old interpretation into a new one. An unassessed field remains unassessed until an explicitly recorded analysis or review supplies it."),
             p("This follows measurement-modeling research: a theoretical construct, the evidence selected as its proxy, and the population to which a claim is applied are different choices. Making those choices explicit helps reveal when a model is measuring topic mention rather than the intended phenomenon. [jacobs2021]")),
        page("Keep the voices apart", diagram("attribution-chain"),
             p("A containing document has an author, but not every sentence is that author's assertion. Government responses reproduce committee recommendations. News reports quote witnesses. A review can reproduce a post that shares a video. The source of the innermost words may be several steps away from the byline."),
             p("Record the proposition before recording a stance. A journalist can support the proposition that an incident occurred while rejecting the hostile claim made during that incident. A supportive stance toward a protective policy is not support for the prejudice that the policy opposes. Neither sentiment nor a named group supplies the missing relation."),
             p("Attribution research has long treated direct and indirect sources as a hierarchy. The atlas's nested-video case shows why one flat speaker label can be inadequate even when two models agree on a criterion. An unresolved speaker should remain unresolved; the author is not a safe default. [breck2004; mohammad2016]")),
        page("Lenses ask distinct questions", p("IHRA, the Jerusalem Declaration and Nexus offer distinct definitions and contextual guidance concerning antisemitism. They are not interchangeable classifiers. England's non-statutory anti-Muslim-hostility guidance and Runnymede's structural account also differ in their emphasis on intention, institutions and effects. The source text must be read alongside each framework's conditions and exclusions."),
             table(["Kind", "Meaning of a positive match"], [["Indicator", "A passage meets a named operational criterion under a framework; inspect the target and represented voice."], ["Boundary", "A classification boundary is met. This is not another prejudice indicator."], ["Descriptive", "A stance, frame or language choice is observed. It is not automatically objectionable."]]),
             p("The political-framing rubric describes positions, appraisal, agency, voices and qualified assumptions. It does not assign left/right identities or use a universal political center. An omission or double-standard claim needs a declared comparison set and relevant supplied evidence; unequal conduct may justify unequal treatment."),
             p("The annex maps each operational criterion to a source section and records partial coverage, exclusions and access limits. Referencing a definition does not mean that its authors endorsed this software or that a trained human panel validated DOXA's summaries.")),
        page("Four outcomes, plus missing work", table(["Stored outcome", "Read it as"], [["meets_criterion", "The recorded method returned a match for this named criterion."], ["does_not_meet", "A determinate non-match for this criterion and assessed unit."], ["uncertain", "The method retained a question it could not resolve."], ["not_applicable", "A recorded applicability judgment, distinct from a non-match."], ["Unresolved / missing", "No accepted decision exists. It is not a fifth model outcome or an implicit negative."]]),
             p("Different criteria within a source unit may have different statuses. Valid peer decisions survive a failed request, but that does not make the whole unit complete. A settled task may be partial or failed. A source collection can be fully imported while many interpretation decisions remain unfinished."),
             p("Do not merge an uncertain result with an ordinary negative to make a chart simpler. Do not turn a timeout into evidence of absence. Report both the denominator selected for analysis and the portion accepted successfully. A review can resolve a decision later, but its event belongs to review history rather than the original model record."),
             p("Case 13 is a real explicit negative without a detailed rationale; case 14 is a real not-applicable result; case 15 retains a missing decision. Their different records are part of the explanation, not defects to conceal.")),
        page("From a file to a preserved source", p("Ingestion records what was supplied, extracts text under resource limits and creates stable source blocks. Metadata retains credited author, date, document type, source URL, license and content identity where available. Text extraction and source rights are separate matters: public visibility alone is not a redistribution license."),
             bullets("Government prose, news articles, third-party quotations, images and logos can have different rights within one publication.", "The licensed news edition excludes external media and records credited authors and translators. A text-only result does not analyze omitted pictures or video.", "Source revisions receive new immutable identities when content hashes change. Historical results remain attached to their original version."),
             p("A source audit checks files, hashes, text ranges, dates and attribution receipts. It does not establish the truth of claims in a report, independent interpretation accuracy, or how representative the collection is. The Global Voices cohort is one publisher's selected historical collection. The institutional documents are purposive cases, not a survey of society."),
             p("The evidence pack records the original archives as parents rather than copying a live multi-gigabyte application database. Cases contain only their necessary frozen sources and records. No original database was opened for writing during publication.")),
        page("What the model was asked to do", diagram("processing-flow"),
             p("The preserved text is divided into deterministic source units. Neighboring source sentences provide bounded context. The classification request asks for one explicit code per unit and criterion; positive or uncertain candidates then receive a separate detailed re-evaluation that selects supplied sentence identifiers and records attribution, proposition, stance and an alternative."),
             p("The application resolves identifiers back to original text. The model does not need to manufacture quotation strings. Valid outcomes can be retained independently when another item fails, and the transition between classification and detailed re-evaluation remains visible where recorded."),
             p("For these historical cases, the saved records do not supply a contemporaneous full request/response journal. Embedded rubric snapshots and later rubric captures verified against the historical selected-pack hash are labelled separately. Neither is a full request receipt. A reconstructed prompt is not the actual old request; hidden chain-of-thought is neither exposed nor invented.")),
        page("Reading a negative honestly", p("The Carnival case records does_not_meet for a named criterion. Its compact record has source-unit anchors but no detailed model rationale. The publication preserves that absence. Editorial questions can help a reader inspect the source; they cannot become an explanation that the original model never supplied."),
             p("A useful negative says something bounded: this method did not identify that criterion as met in that assessed unit. It does not show that every issue is absent from the article, that the author is neutral, or that another lens cannot detect a descriptive frame. The same discipline applies to not_applicable, which records a different applicability judgment."),
             bullets("Read the exact outcome and criterion together.", "Check whether displayed evidence was model-selected or attached by software as a source-unit anchor.", "Distinguish no detailed rationale recorded from an explanation of why the decision is correct.", "Keep unassessed source units and failed decisions outside absence claims."),
             p("Evaluation must sample negatives and applicability decisions as well as displayed positive findings. Reviewing only striking outputs can reveal useful failure modes but cannot estimate missed detections or whole-corpus accuracy.")),
        page("Reading nested attribution", p("The Prevent review case contains a footnote describing a screenshot of a post that shares a video. Both recorded models identify an anti-Jewish trope in quoted language. One attributes it to a reported speaker; the other assigns author. The text's explicit nesting is therefore central even though the criterion result agrees."),
             p("The atlas preserves both records and the footnote wording. It asks whether the statement belongs to the review's author, the person sharing the post, or the voice in the video. These are editorial review questions, not an automatic correction or a verdict about a named person's beliefs."),
             p("A robust source chain stores each reporting relation with a textual anchor and can leave the innermost speaker unknown. Quotation marks are useful but insufficient: reported speech may lack them, and a quotation can be endorsed, rejected, questioned or merely cited elsewhere. Wider source context is sometimes necessary."),
             p("A single flat attribution label cannot always express this structure. v0.4's additive layer can hold it while leaving the original result intact. A human correction, when supplied by an actual reviewer, should appear as a separate review event with its reason, evidence and actor.")),
        page("Reading uncertainty without dismissing harm", p("The reported-harm case concerns a criterion that requires stereotyping and intention to encourage hatred. A speaker's account can document what they report experiencing while leaving another actor's specific intention unestablished. An uncertain criterion result does not imply that no incident or harm occurred."),
             p("Uncertainty has several sources: missing context, ambiguous language, unresolved reference, an absent comparator or a contested interpretation. Model disagreement is another reason to review, but it is not identical to uncertainty in the source. A processing error belongs in the processing ledger rather than masquerading as a semantic judgment."),
             bullets("Name the unresolved requirement rather than offer a generic confidence adjective.", "State what evidence would address it and whether that evidence is available.", "Keep genuine competing readings visible even after an adjudicated evaluation label is created.", "Do not substitute a percentage unless calibration has been evaluated on relevant held-out labels."),
             p("Calibration literature motivates testing how confidence relates to correctness. It does not certify these models, and different elicitation methods work differently across tasks. Qualitative uncertainty and review states are more honest than unsupported probability claims. [guo2017; xiong2024; tian2023]")),
        page("Political framing is observable; unfairness needs a comparison", p("An executive order's metaphor or stated remedy is inspectable. The political packet records evaluative language and expressed policy positions using six descriptive criteria. A strong word can accompany a well-supported argument. Detecting it does not establish that an allegation is false or that an author has a particular ideology."),
             p("A comparison needs a question, eligible sources, dates, genres, relevant evidence and matching limits. An executive order and a sixty-page strategy have different purposes and lengths. Their choice of instruments can be described; a raw difference cannot establish a double standard. Equal airtime is not an objective fairness rule."),
             p("The same reasoning applies to omission. To claim that relevant information was unfairly left out, explain why it belonged within the declared scope and was reasonably available. An absence from one selected corpus cannot establish deliberate suppression. The source and comparison policy must remain reviewable."),
             p("Framing research describes selection and salience through problem definitions, causes, moral evaluations and remedies. Informational-bias research also shows why broader comparable reporting matters. These are useful analytical distinctions, not a universal bias meter. [entman1993; card2015; fan2019]")),
        page("Read dashboards through their denominators", corpus_table(audit),
             p("These rows are different recorded scopes and profiles. The independent verifier examines a selected subset; the local Qwen trial was stopped with unfinished work. A numerator without its criterion, source coverage, unit size, kind and review state invites a misleading comparison."),
             p("The frozen archive audit recomputes source counts, outcomes and unit states, checks every part checksum, and resolves every retained reference against its original source. Those are mechanical integrity checks. They do not turn source coverage into understanding or the number of observations into the prevalence of prejudice."),
             p("A useful dashboard labels imported sources, assessable/accepted units, unresolved decisions, represented voices and review state separately. When grouping by author or institution, use credited metadata and scope limits; do not infer a private political or religious identity from patterns of words.")),
        page("Publication time and review time are different histories", p("A publication-date chart concerns when the selected sources were published. A model-run chart concerns when those sources were processed. A review chart concerns when an assessment was changed or approved. A corrected interpretation today is not evidence that a publisher's discourse changed today."),
             p("Historical article text was frozen at collection time and grouped by original publication date. It is not a reconstruction of every first edition. Source revisions, changing collection composition and duplicate stories can alter a trajectory even before a model changes. Keep those facts alongside the chart."),
             diagram("review-loop"),
             p("A spike should be a question with a declared rate, denominator, time window and compatible method. Sparse periods, missing processing and different source mixes need explicit treatment; a zero or a gap must not be silently interpreted as an absence of prejudice. A detected change does not establish a cause.")),
        page("Human review is a separate contribution", p("The current publications have no independent human reference labels. AI-authored notes and a second model can help organize review, but neither is human adjudication. The workflow can be prepared now while actual reviewers and judgments remain pending."),
             p("Independent reviewers should see the relevant original context, rubric and annotation instructions before being shown model outputs. Record their target/proposition, speaker chain, criterion outcome, context sufficiency and evidence. A calibration exercise can refine unclear instructions before the held-out evaluation is frozen."),
             p("Keep both raw labels and any later adjudication. Disagreement may reveal ambiguity, missing evidence or different plausible readings rather than careless annotators. Reliability should be reported by field and criterion alongside label frequencies; it is not a substitute for construct validity. [plank2022; artstein2008]"),
             p("Approved project examples can enter an explicit versioned memory snapshot. A candidate method must be compared with its baseline on separated held-out material before activation. This is reviewed example memory, not autonomous weight training; source/story-group exclusions and rollback remain necessary.")),
        page("Choose the tool for the question", table(["Recorded tool", "Appropriate reading"], [["Cloud generative assessment", "Versioned contextual criterion outcomes; paid remote text transfer, visible gaps and review needed."], ["CPU Qwen generation", "A local alternative whose bounded trial completed only two of twenty articles; no complete local-corpus claim."], ["MiniLM retrieval", "An index of 120,464 passages across 5,000 sources; similarity supports navigation, not endorsement or prejudice inference."], ["CPU DeBERTa NLI", "Twenty articles, 60 windows, 240 independent hypothesis pairs; uncalibrated scores, no winning category or prejudice label."]]),
             p("Local placement changes where inference happens, not the evidentiary standard. Disk, RAM, context length and CPU contention affect practicality. The recorded CPU index combines attempts and reused caches rather than one cold timing. The local generator, retrieval and NLI baselines have different scopes and should not share a single throughput claim."),
             p("Budgets are also scoped. Application-accounted token use and conservative reservations differ from the vendor invoice; fixed infrastructure, electricity and outside requests are separate. Reusing historical outputs in an export makes no new inference call, but it does not erase the original processing cost.")),
        page("What the research supports—and what was tested", p("Academic literature supports explicit constructs, target-specific stance, hierarchical attribution, functional testing and preserved disagreement. It motivates context and calibration experiments. It does not independently validate this implementation's particular operational summaries, model choices or source population."),
             p("The software tests establish behaviors such as exact-source anchoring, explicit outcome handling, failed-peer recovery, immutable source versions and labelled report layers. Actual model runs establish observed coverage, cost/resource receipts and the model readings recorded in those runs. Neither kind supplies independent interpretation accuracy."),
             p("The next evaluation must separate an untouched source/story-group test set from development examples and enriched challenge cases. Publish criterion precision/recall, attribution errors, boundary false positives, abstention, coverage, review effort and uncertainty only when real reference labels justify those quantities. An independent-model difference count remains a difference count."),
             p("Keep model, prompt, rubric, memory and context policies frozen for comparisons. Change one component at a time where feasible and report pipeline differences when not. A fluent explanation or a larger context window is a hypothesis about usefulness, not a result. [ribeiro2020; deyoung2020; liu2024]")),
        page("A practical next reading", p("Open a case, locate the exact words and identify the source of the proposition. Read the framework condition and its exclusions. Then inspect the original model result, the source-unit state, the review status and the separately labelled editorial questions. Ask what evidence would strengthen or change the interpretation."),
             bullets("Use the atlas as a teaching set, not as a scorecard of institutions or public figures.", "Use the annex and evidence pack to verify source/method identity, original payloads and gaps in historical capture.", "Keep genuine uncertainty and incomplete processing visible in reports and dashboards.", "Treat independent human annotation as a future evidence-producing activity, not a label the software can award itself."),
             *reference_blocks(["jacobs2021", "breck2004", "plank2022", "rottger2021"]),
             p("The full research map, BibTeX and CSL-JSON bibliography accompany this publication. They distinguish a paper's finding, its concrete application consequence and the limits of transferring that result to DOXA. All original v0.3 reports remain unchanged parents of this additive edition.")),
    ]


def technical_pages(atlas, audit, packs, criteria_audit):
    pages = [
        page("Technical annex: contracts, sources and receipts", p("DOXA by TACITUS · additive v0.4 documentation of frozen v0.3 evidence"),
             p("This annex specifies the observable processing contract, source audit, recorded model profiles and evidence lineage behind the reader explainer and case atlas. It retains historical outcomes and uncertainty. New semantic annotations or future method experiments must identify their own version rather than overwrite a baseline."),
             diagram("processing-flow"),
             p("The annex is AI-authored technical and editorial documentation. Framework authors have not endorsed DOXA, and independent human interpretation validation remains pending. The source references support particular methodological choices; they do not confer a general accuracy certificate."),
             table(["Object", "Integrity boundary"], [["Original run", "Frozen source selection, method configuration, raw outcomes, usage and failures."], ["Publication capture", "Read-only extraction and exact-source audit; original payload hashes retained."], ["Editorial layer", "Separate AI guidance, questions, research mappings and case selection."], ["Review", "Actual recorded review events only; none invented for publication."]])),
        page("Supported is not the same as tested", p("A primary source can support the conceptual choice of a criterion without establishing its annotation reliability or the performance of a model applying it. An actual run can exercise a criterion without independently validating the interpretation. Keep those levels visible in research claims."),
             table(["Evidence", "Established here", "Still unmeasured"], [["Framework text", "Source identity, applicable passages, exclusions and declared operational gaps", "Official approval or exhaustive implementation"], ["Academic method", "A reason to test constructs, attribution, context and uncertainty", "Transfer of published benchmark accuracy"], ["Software tests", "Specific source, schema, failure, restoration and report behavior", "Population interpretation accuracy"], ["Actual model run", "Observed outcomes, coverage, costs and resources", "Correctness without reference labels"], ["Independent model", "Literal paired differences and profile limits", "Human ground truth or automatic adjudication"]]),
             p("The criterion-source audit is machine readable and covers the selected frozen packs plus an explicitly identified language-norm reference pack. Access restrictions are recorded. A partially summarized definition remains a partial operationalization; a citation is not permission to claim otherwise.")),
        page("The additive measurement contract", p("The immutable assessment keeps its original criterion kind, result, proposition, stance, references and method identity. Additional interpretation belongs in a versioned overlay keyed to the assessment and source version. It should identify whether it was proposed by a model, written editorially or reviewed by an actual person."),
             table(["Semantic field", "Intended distinction"], [["Evidence function", "Definition, incident report, quotation, counter-speech, hypothetical, policy assertion, mixed or unclear"], ["Proposition type", "Hostile claim, report of conduct, definition, remedy, factual context or unclear"], ["Attribution chain", "Anchored relations between containing author, report, quoted source and innermost speaker"], ["Missing context", "Specific absent comparator, quotation origin, intent evidence or unresolved reference"], ["Provenance/status", "Version, actor/model, timestamp, source references, proposed/reviewed state"]]),
             p("Do not infer document adoption simply from a supports value: support for the proposition that discrimination occurred is different from support for discrimination. Unknown legacy overlay fields remain not assessed. A user must be able to inspect both the original record and the additive interpretation."),
             p("The publication itself does not create new semantic reference labels. Its measurement questions are AI-authored prompts for review. Construct-content and discriminant validity are future empirical questions. [jacobs2021]")),
        page("Outcome, kind and process state are orthogonal", table(["Dimension", "Values / meaning"], [["Criterion kind", "indicator, boundary, descriptive; registry-owned"], ["Criterion result", "meets_criterion, does_not_meet, uncertain, not_applicable"], ["Unit processing", "accepted, partial, failed, pending; accepted requires every requested decision"], ["Review", "Unreviewed and actual recorded review events; independent from original model result"], ["Hypothetical", "An independent flag, not a speaker identity or a factuality verdict"]]),
             p("An indicator being met contributes to a criterion-specific indicator count. A boundary or descriptive match does not. A positive represented expression is not automatically author adoption. A missing or invalid output is never expanded into a negative vector to make coverage appear complete."),
             p("Partial units may retain accepted peers after an exception. A conservative failed/pending label can coexist with those retained rows. Coverage must not be promoted from the mere existence of some assessments. The original unit state and missing keys remain in the archive."),
             p("Evaluation should treat the four results as nominal categories unless a separately justified model specifies a different relation. Pooled accuracy dominated by inapplicable questions would hide important boundary and attribution errors.")),
        page("Preserving source identity", p("Source identity, file identity and extracted-content identity answer different questions. Identical text can come from distinct credited sources; changed content from one source is a new immutable version. Hashes are used for integrity and recovery, not as a reason to collapse bibliographic authorship."),
             bullets("Retain source URL, credited author, translator where supplied, date/precision, type, license and retrieval/extraction notices.", "Retain original-source and extracted-block hashes with their distinct meanings.", "Attach assessments to the exact frozen version and retain old versions after revisions.", "Keep cohort membership separate from all historical documents stored in a project."),
             p("The case atlas uses source metadata and original extracted blocks preserved in the recorded workspace. It does not re-fetch current web pages as a substitute for the text actually analyzed. A full source remains available in the frozen snapshot even when the PDF displays a bounded exact excerpt."),
             p("The archive audit compares each record's source identity/hash and extracted blocks to the bundled corpus before checking all references. Rights are attributed per source; the audit is not exhaustive legal clearance.")),
        page("Rights, credits and text-only exclusions", p("The institutional case sources carry US government-work or UK Open Government Licence metadata, with source-specific exceptions. Global Voices articles retain the publisher's CC BY 3.0 attribution evidence, bylines and translation credits. A containing license does not erase rights in separately attributed quotations or media."),
             p("The licensed news edition excludes external images, captions and embeds. Those omissions matter analytically: text-only source context may be insufficient to interpret a reference to a video or image. Missing media must be a context limitation rather than an invented visual finding."),
             p("This publication cites and briefly paraphrases academic papers and the Runnymede briefing. It links the original 19-page briefing, records its checksum and page locators, and does not redistribute the entire copyrighted PDF. An open repository containing an annotation or a paper does not automatically license the underlying news source articles."),
             p("The case source ledger records every included source's title, author, date, URL, license, translator and extracted hash. Reviewer and model claims remain separately attributed. Do not present AI-written commentary as a statement from an original source author or a framework's authors.")),
        page("Deterministic source units", p("The frozen source-units-3.1 builder partitions original source characters and records sentence identifiers and available locators. It does not silently normalize quotation text into a new string for citation. Blocks can contain several voices, and a unit can span multiple source segments."),
             table(["Recorded profile", "Owned source / context"], [["Cloud baseline", "Up to 4,000 source characters per unit; two neighboring sentences on each side"], ["CPU Ollama baseline", "Up to 1,200 source characters per unit; neighboring sentences retained"], ["Offsets", "Zero-based, end-exclusive Unicode code points into the original block"], ["Coverage", "Owned source characters counted once; neighbor context does not inflate coverage"]]),
             p("Source unit, source paragraph, scheduler task, criterion decision and provider request are different denominators. Packing multiple units into a request changes call counts without changing their owned source ranges. Retrying a criterion does not create additional source coverage."),
             p("The publication's unresolved case is rebuilt from the frozen source identity and recorded builder configuration. If that unit cannot be reproduced, capture fails rather than constructing an approximate missing case.")),
        page("Context supplied and context absent", p("Neighbor context can resolve a pronoun or preserve a local rebuttal, but two sentences may not include a recommendation heading, section boundary or distant footnote. The technical question is what original material was supplied, not what a reader can now infer from the whole source."),
             diagram("attribution-chain"),
             p("A future context-expansion experiment should record requested dependencies and selected source passages. A generated summary must not silently replace original evidence. Missing external material should be flagged explicitly. Reading a larger prompt does not prove that a model used its relevant middle passages effectively."),
             p("For historical runs without a request capture, the source-unit builder and frozen settings can reconstruct a likely template and available source units, but this is not the actual transmitted request. The evidence pack labels that distinction. A controlled v0.4 experiment can capture its own visible request receipt from the outset. [liu2024]")),
        page("Classification: explicit and bounded", p("The classifier requests a fixed-length outcome vector in a supplied criterion order. The frozen schema uses codes 0 for does_not_meet, 1 for meets_criterion, 2 for uncertain and 3 for not_applicable. Identifiers and vector lengths are constrained; missing codes are incomplete, not zeros."),
             table(["Validation boundary", "Consequence"], [["Unknown source/unit ID", "Reject that item; never cite an invented unit"], ["Wrong vector length", "Incomplete structured response; recover independently"], ["Length-terminated JSON", "Do not repair it into invented decisions"], ["Positive/uncertain classification", "Requires the bounded detail step before a noteworthy assessment is accepted"]]),
             p("Classifier output is a recorded model decision, not a confidence score. Valid explicit negatives can be committed immediately. Already accepted peers survive failures elsewhere. The two-stage design was measured operationally on a small engineering pilot; that pilot did not have independent interpretation labels."),
             p("Prompt/schema versions and selected rubric snapshots belong to the run. A historical run cannot be resumed under an altered selected method merely because the same model name is still available.")),
        page("Detailed re-evaluation and source identifiers", p("Positive and uncertain candidates receive a separate bounded re-evaluation. The model selects supplied sentence IDs and supplies concise attribution, proposition, stance, explanation and an alternative. The application derives quotations from the source rather than trusting generated quotation strings."),
             p("A detail-step outcome can differ from the initial classifier. Where recorded, the trace preserves both. This is the configured model checking a bounded item, not an independent model or human review. A budget stop between stages leaves the ungrounded candidate incomplete."),
             bullets("At least one cited sentence must belong to the scored unit; neighbor-only evidence cannot score another unit.", "A directional stance requires an explicit proposition.", "An uncertain result cannot simultaneously claim supported certainty.", "Bounded independent retries preserve valid peers and make remaining failures visible."),
             p("Passing these checks verifies structure and anchoring. It does not establish that an explanation follows from the chosen evidence. The technical annex therefore keeps rationale plausibility and model faithfulness as separate future evaluation questions. [deyoung2020]")),
        page("Compact negative and applicability records", p("A determinate negative or not-applicable classification may have no detailed model rationale. The persisted representation can attach source-unit sentence anchors so that its scope is inspectable. These are software-derived anchors; they must not be described as a model selecting a minimal reason for its answer."),
             table(["Origin", "Publication label"], [["detail_supplied=true", "Persisted detail sentence IDs and recorded model detail"], ["detail_supplied=false", "Software-attached unit anchors; model detail not recorded"], ["Flag missing", "Unknown evidence-selection origin; no retrospective certainty"], ["Display excerpt", "Editorial layout excerpt, separately labelled"]]),
             p("The freezer rehydrates absent quote text only by exact slicing of the frozen source. It retains the original payload and both parsed-content and stored-byte hashes. It does not add a rationale to that payload. A displayed excerpt may be chosen for readability, but its selection is editorial, not additional evidence of what the model considered."),
             p("Independent evaluation must include these records. Otherwise recall, missed-context behavior and the difference between does_not_meet and not_applicable remain invisible behind positive-only review.")),
        page("Unresolved work remains a gap", p("The atlas's fifteenth case contains a genuine missing criterion decision from a partial institutional source unit. Accepted peer criteria remain retained. The unresolved entry carries result=null, a list of missing keys, the source unit and its recorded status payload. It has no fabricated assessment ID, model explanation or confidence."),
             p("A retry, a later model experiment or a human review can address the gap, but each must retain its own event and version. Completing a verifier does not retroactively fill a missing primary row. A gap cannot become an implicit negative merely to complete a comparison matrix."),
             table(["Quantity", "Treatment"], [["Selected units", "Include the incomplete source in the declared denominator"], ["Accepted units", "Count only those with every required criterion outcome"], ["Retained decisions", "Include valid peers in their criterion-specific counts"], ["Missing decisions", "Remain unresolved and excluded from absence claims"]]),
             p("The publication exposes only a bounded teaching example, while the corpus audit retains all incomplete states and exact overall counts. The case should lead to a concrete question about what remains missing, not an unsupported explanation of why a request failed.")),
        page("Exact references: necessary, not sufficient", p("Every retained reference is checked against the original source block and Unicode range. A match establishes that the cited words occur there. It does not establish that those words support the explanatory claim, that omitted context is irrelevant, or that a legal condition has been proved."),
             table(["Evidence question", "Required check"], [["Integrity", "Correct source version, block and exact span"], ["Support", "A reviewer assesses whether the cited material supports the stated interpretation"], ["Context sufficiency", "Relevant quotation origin, rebuttal, section and comparison evidence supplied"], ["Faithfulness", "Controlled study of whether the reported rationale is connected to the model's decision"]]),
             p("The bounded streaming audit checks all published archive references and aggregate counts. Case-specific display windows also retain exact source offsets. Long contexts can be excerpted for layout, but the full selected source blocks remain in the case snapshot."),
             p("ERASER distinguishes human-rationale alignment from faithfulness metrics. Applying that lesson means resisting the temptation to equate plausible prose, a matching quote and a trustworthy decision. Any future evidence-removal experiment must be separately specified and measured. [deyoung2020]")),
        page("Nested attribution and factuality", p("A useful representation links a proposition to the source expressing it, the relation through which it is reported, and the containing document's stance toward that proposition. Multiple edges can be needed for a quotation inside a post inside a review."),
             p("FactBank concerns how events are presented as factual, non-occurring or uncertain in text. That descriptive factuality is different from independent verification that an event happened. Similarly, MPQA-related attribution research models textual sources rather than access to inaccessible mental states. [sauri2009; breck2004]"),
             p("In the actual nested-video case, both models agree on the Nexus trope outcome but disagree on attribution. The source footnote explicitly describes a post sharing a video. The editorial question is which speech-source node is responsible for which words. Neither raw record is silently overwritten in this atlas."),
             p("Separate unknown innermost speaker, reported speaker and authorial voice. Do not infer private identities or hidden motives. When an actual reviewer later changes attribution, retain the original raw reading, source evidence, review actor and correction reason.")),
    ]
    framework_order = ["ihra", "jda", "nexus", "anti-muslim-hostility", "anti-muslim-racism", "discourse", "political-framing", "language-norms"]
    extra = {
        "ihra": "The frozen three-criterion pack does not cover every illustrative example as a separate decision. Official primary-page access was bot-blocked during this publication audit. The declared mappings are retained with that limitation rather than presented as a freshly verified full-source audit. This does not invalidate stored outputs, but it limits what this publication claims to have independently rechecked.",
        "jda": "Read the numbered guidelines together. The political-expression boundary cannot make every surrounding statement harmless, and the context of a separate anti-Jewish claim still matters. A framework disagreement is not automatically an annotation error or a reason to vote across definitions.",
        "nexus": "The source distinguishes different standards based solely on Jewish identity from disproportionate attention that is not prima facie proof. A comparison needs relevant supplied evidence and a stated standard. Reporting a trope and endorsing that trope remain separate questions.",
        "anti-muslim-hostility": "Sections 6 and 8 must be read together. The definition is not a new criminal offence and does not rewrite equality law. The source's intent-related conditions require evidence; an automated passage reading cannot pronounce legal unlawfulness or presume an intent to encourage hatred.",
        "anti-muslim-racism": "The original July 2025 briefing has 19 PDF pages. Its discussion of institutional/structural effects explicitly does not require deliberate racist intent, unlike an intent-focused conduct question. PDF p12 protects criticism of doctrine and Muslim-majority governments when not targeting populations. The annex links, rather than redistributes, the copyrighted briefing.",
        "discourse": "The pack combines descriptive stance, framing, rhetorical function and qualified presupposition hypotheses. Its academic precedents do not establish a numeric doxa scale or an author's hidden beliefs. Absence of evidence for support or opposition is not automatically neutrality.",
        "political-framing": "All six criteria are descriptive. Comparison-dependent asymmetry requires declared matched evidence; without it retain uncertainty for a relevant inquiry. A generic single-document route cannot itself complete a paired-source omission audit. There is no universal center, left/right label or automatic author score.",
        "language-norms": "This is a user-defined research inquiry, not an established political-correctness scale. It is documented here as a reference pack and was not selected in the frozen six-lens runs used by this atlas. Source precedent for framing is not validation of this specific rubric."}
    for identity in framework_order:
        pack = packs[identity]
        framework = FRAMEWORKS.get(identity)
        mappings = [row for row in criteria_audit if row["criterion_key"].startswith(identity + "/")]
        pages.append(page("Framework audit: " + pack["name"], p(extra[identity]),
            table(["Frozen operational criterion", "Source mapping / condition"], [[row["criterion_key"].split("/")[1], row["source_summary"]] for row in mappings]),
            p("Scope: " + pack.get("scope", "")),
            p("Exclusions and context: " + (framework["exclusions"] if framework else pack.get("context_rules", ""))),
            p("Audit status: " + (framework["audit_status"] if framework else "Academic precedent with an author-operational descriptive rubric; no independent interpretation validation.")),
            {"type": "link", "text": "Original reference", "url": framework["url"] if framework else pack["source_url"]}))
    pages.extend([
        page("Context and persona sensitivity experiments", p("A useful v0.4 experiment changes one context policy while holding source, criterion, model and other settings fixed. Compare the frozen bounded context with source-linked section/footnote expansion. Record every added passage and why it was eligible; do not use a generated summary as unlabelled original evidence."),
             bullets("Test the same relevant material at beginning, middle and end where a transformation preserves meaning.", "Test quoted versus reported speech, rebuttals crossing boundaries, headings and distant footnote origins.", "Keep presentation persona separate from analytic conditions. A different audience should not silently alter the criterion.", "Report actual pipeline differences when a clean single-component ablation is impractical."),
             p("Liu et al. found position-sensitive performance in long-context QA and retrieval. That motivates a controlled test; it does not prove that more context or retrieval necessarily helps this task. A context change is a new method receipt, not compatible resume of a frozen baseline. [liu2024]")),
        page("Sampling and independence", p("The 5,000-source news cohort is a frozen selection from one publisher, not a sample of all journalism. The twenty institutional documents and fifteen teaching cases are purposive. Neither selection establishes public prevalence or generalizes across outlets by itself."),
             p("A future evaluation should separate a probability-sampled base from enriched challenge cases. Preserve source/story groups across development, calibration and test partitions. Report inclusion rules and weights where prevalence estimation is intended. Do not calculate whole-corpus rates from a deliberately enriched disagreement review set."),
             p("The independent verifier selected indicator-flagged units plus a deterministic document-stratified sample of other accepted units. Those other units are not human-validated negatives. Model selection for review is useful, but independent reviewers should not be led by model outcomes during reference-label creation."),
             p("Text chunks and multiple criteria from one document are correlated. Confidence intervals should respect source/story clustering. A large count of unit-criterion rows is not an equally large number of independent documents. [bender2018; jacobs2021]")),
        page("Recomputed corpus receipts", corpus_table(audit),
             p("The publication audit streams every checksum-listed gzip part. For each source record it verifies source identity, frozen hash, extracted-block checksum, every retained reference and aggregate outcome/unit counts. It reads at most one source record at a time and does not copy or mutate the application database."),
             p("All selected-source words and characters remain in the scope receipt, including documents with gaps. Verifier units belong to its declared subset. A process state of partial remains partial, even when every task is settled. Historical cancelled local work is not installed as a complete demonstration."),
             p("The receipt records input hashes so a repeat build can reuse the audit only when the parent archives and source metadata tree are unchanged. This is an integrity cache, not cached semantic judgment. A changed source must cause a new audit or an explicit refusal.")),
        page("The separate model comparison", p("The recorded institutional Gemma and GPT-oss profiles have 1,500 paired decisions on 79 selected units. They differ on 642 result categories. Of those differences, 459 are switches between does_not_meet and not_applicable. Category differences therefore must not be described as an error rate or all counted as changes to positive indicator flags."),
             p("The verifier emits one additional decision without a primary counterpart. That row remains unpaired; it is not evidence that the primary returned negative. The primary includes a partial unit even where the verifier completed its own selection. The comparison is not complete on all requested primary counterparts."),
             p("The profiles also differ in request packing and output budgets. Their cloud revisions are unresolved. Differences cannot be attributed exclusively to weights or interpreted as a controlled model-ranking experiment. Both readings and the raw provenance remain available."),
             p("The atlas includes a definition, statistical context and nested attribution disagreement. They are selected for instructional contrast, not an independent evaluation sample. Any reviewer conclusions must be separate review events with their own evidence and actor.")),
        page("Model and method identity", table(["Recorded component", "Publication treatment"], [["Model name", "Preserve exact configured identifier and execution profile"], ["Revision", "Use recorded digest when available; unresolved remains unresolved"], ["Rubric", "Use embedded snapshots, or later text verified against the historical selected-pack hash. Label those origins separately; a version string alone is weaker evidence."], ["Prompt/schema", "Preserve recorded versions; reconstructed templates remain separate"], ["Operational settings", "Record concurrency, timeouts, quantization and CPU threads without treating them as quality evidence"]]),
             p("The cloud primary uses Gemma 4 26B A4B IT; the independent profile uses GPT-oss-120B. The local generator is Ollama Qwen3.5 4B with a recorded digest/quantization. Model cards, licenses and advertised context lengths describe interfaces and intended use, not demonstrated performance for this corpus."),
             p("A changed selected method requires a new run or explicit experiment. A compatible unselected-lens extension does not justify modifying selected rubric semantics. Source/version, prompt, memory and context identities must be visible together when interpreting a comparison.")),
        page("CPU retrieval and NLI are different tasks", p("The canonical MiniLM index covers 5,000 articles and 120,464 passages. Its approximately 49.91 active minutes combine attempts, interrupted processing, cached work and a source-version refresh. Restart reuse and exact returned quotations demonstrate persistence/navigation, not ranking accuracy or prejudice inference."),
             p("The CPU DeBERTa baseline processed all twenty original selected news articles, 60 source windows and 240 independent hypothesis pairs in 247.102 seconds including the first model load. Peak process RSS was about 2.038 GiB. Its four auxiliary hypotheses are separate from the nineteen generative criteria, and its scores are uncalibrated."),
             p("The local Qwen trial completed two of twenty articles, with eight accepted units, two partial units and 105 unprocessed. The sampled combined driver/Ollama RSS peak was about 9.238 GiB, can double-count shared pages and excludes browser/OS memory. This is bounded incomplete generation, not a successful full local benchmark."),
             p("These tasks should not share a winner or a combined throughput claim. Run heavy CPU phases sequentially when they contend for resources. Complete source windows and low API cost do not remove the need for attribution and interpretation review.")),
        page("Budgets, retries and operational limits", p("A run budget is an application-accounted total cap, not an incremental allowance on every resume. The workspace's monthly ledger is a separate shared limit. Independent benchmark directories have separate ledgers; their caps do not combine into an account-wide vendor limit."),
             p("Reported token receipts, configured prices, conservative reservations and vendor invoices can differ. Unknown billing remains unknown. A request interrupted after transmission can still be billed; conservative accounting may retain the reserved maximum. Hosting, GPU idle time, storage, electricity and requests outside the workspace are separate costs."),
             p("The isolated v3 CLI supports up to 48 remote scheduler workers and defaults to four; this is not a throughput promise. Ollama requires one. The normal application's remote worker count and the shipped Cloudflare queue's single consumer are distinct execution paths. Local-state cloud inference does not benchmark deployed D1/R2/Queues throughput."),
             p("Timeout or budget exhaustion leaves explicit gaps. A charged failed call does not become a successful empty interpretation. The publication itself reuses saved evidence and requests no additional paid inference.")),
        page("Source revisions, forks and historical usage", p("A changed complete-news cohort cannot be smuggled into an old run by rewriting its checkpoint's source hash. The explicit fork helper verifies the stopped original receipt, preserves that run and creates a separate frozen source selection. Only identical source versions can reuse outcomes."),
             p("A citation-link metadata cleanup changed the canonical extracted hash of one still-unassessed news source. Even though its text and locators were separately checked unchanged, that source received a new immutable version and an audited cohort fork. The changed version acquired no invented reused assessment."),
             p("The fork inherits historical usage and accounted spend as provenance without debiting those requests to the monthly ledger twice. Do not add the original total again to a new total that already includes it. Preserve historical sessions and current sessions separately; active processing duration is not a single uninterrupted wall-clock benchmark."),
             p("A narrow news-sample exception permits recorded parent-manifest digest updates only when every selected source identity, order, content hash, path and setting remains unchanged. It cannot authorize changing selected articles or a complete cohort.")),
        page("A prepared review workflow, not manufactured labels", p("The prepared v0.4 scaffold has 200 unlabelled source cases, split into 100 development and 100 held-out cases with disjoint story groups, plus separate reviewer A/B forms and an exposure ledger. Its functional casebook contains 120 protocol fixtures from 24 real bases across six existing lenses. Expected human labels are null and model execution is not_run. Independent judgments remain pending."),
             {"type": "link", "text": "Prepared evaluation protocol and source forms", "url": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/docs/evaluation-v04.md"},
             p("Use a calibration exercise with two independent trained reviewers to identify unclear instructions before freezing a test. Each reviewer records criterion result, evidence span, source chain, proposition/stance and context sufficiency. Adjudication, where appropriate, follows as a separate event while preserving raw labels."),
             p("Blinding model outputs reduces anchoring during reference-label creation. Source context required for attribution should not be removed merely to hide a byline. Different interpretive expertise can reveal ambiguity; private demographic identities must not be guessed or required without a justified purpose."),
             p("Approved examples enter versioned memory only through an explicit review process. Candidate/baseline comparison, activation and rollback remain inspectable. No automatic weight training or human-quality claim follows from having a review button. [plank2022]")),
        page("Evaluation design and reliability", p("Report criterion-specific precision/recall only after independent labels exist. Also report four-way outcome confusion, context insufficiency, attribution-chain errors, proposition/stance errors, boundary false positives, abstention, accepted coverage and review effort. A single aggregate F1 can conceal important failure types."),
             p("For human annotation, report raw agreement and label frequencies with a task-appropriate nominal coefficient. Separate evidence span selection from category assignment; overlapping spans require their own rule. Do not impose an ordinal distance on the four outcomes without an argument. Agreement is not validity, and an adjudicated label does not erase genuine interpretive variation."),
             p("Separate probability-sampled estimates from challenge-set results and preserve clustered uncertainty by document/story group. A rare criterion can produce deceptively high apparent accuracy through ordinary negatives; publish class-specific sample sizes and uncertainty."),
             p("CheckList and HateCheck motivate targeted behavioral tests alongside held-out measurement. Extend Jewish-target and long-document context cases explicitly rather than assuming the original HateCheck population covers them. [artstein2008; ribeiro2020; rottger2021]")),
        page("Calibration and abstention", p("Qualitative certainty labels, NLI softmax scores and a model's verbal confidence are different quantities. None is a demonstrated probability of prejudice in this application. Independent reference labels are required before fitting or evaluating a calibrator for the actual task."),
             p("Guo et al. studied neural classification calibration and temperature scaling. Tian et al. found useful verbal-confidence methods on particular QA benchmarks. Xiong et al. found remaining overconfidence and task-dependent performance across elicitation strategies. These motivate experiments; they do not settle the best method for contextual prejudice analysis."),
             bullets("Use separate development/calibration/test data and preserve source/story-group separation.", "Report reliability plots and proper scores with criterion-specific sample sizes and uncertainty.", "Show errors versus abstention/coverage where a rejection policy is evaluated.", "Do not promise risk guarantees or causal explanations unsupported by the evaluation design."),
             p("Until then, use named missing-context reasons and review states rather than an authoritative percentage. A second model's agreement can trigger review or comparison, but it is not a calibrated confidence estimate or reference truth. [guo2017; tian2023; xiong2024]")),
        page("Artifact lineage and reproduction", diagram("lineage"),
             p("The evidence pack records source artifacts, frozen rubric parents, persisted assessment payloads, editorial annotations, reconstructed templates and report exports as distinct origins. Every file receipt is content hashed. A parent reference identifies which source and method object a derivative used."),
             p("Historical request capture is not recorded for these cases. The reconstruction is explicitly not the actual request, and a persisted assessment is not a raw provider response. No hidden chain-of-thought, credential or invented historical response is included. A future opted-in visible-I/O journal would produce a different origin record."),
             {"type": "code", "text": "PYTHONPATH=. python scripts/build_v04_publications.py\nPYTHONPATH=. pytest -q tests/test_v04_publications.py"},
             p("The build opens original SQLite with mode=ro, verifies portable archives and source evidence, writes only new v04-prefixed outputs, and checks original parent hashes again before success. Publication regeneration uses saved outputs; no provider/model call is made.")),
        page("Academic source map · 1", *reference_blocks([reference["id"] for reference in REFERENCES[:9]])),
        page("Academic source map · 2", *reference_blocks([reference["id"] for reference in REFERENCES[9:]])),
    ])
    if len(pages) != 36:
        raise ValueError("Technical annex editorial page plan changed")
    return pages


def atlas_pages(atlas, criteria_audit):
    pages = [
        page("Fifteen real cases: an evidence atlas", p("Source → framework → recorded model → review state → editorial questions"),
             p("This atlas preserves real source text and original saved assessments from the institutional, political and news runs. Its fifteen cases include all four explicit outcomes and one separately represented unresolved decision. Three cases show a second recorded model reading."),
             diagram("measurement-layers"),
             p("The cases were purposively selected for teaching and methodological contrast. They are not independent human labels, a prevalence sample or a model-ranking benchmark. Commentary is AI-authored guidance. Original results, source-unit states and missing capture remain visible.")),
        page("Case index and outcome key", table(["Case", "Teaching question", "Original result"],
            [[case["id"], case["title"], case["assessment"]["result"] if case["assessment"] else "Unresolved / no accepted decision"] for case in atlas["cases"]]),
             p("Positive means a named criterion was met under the recorded method. Indicator, boundary and descriptive kinds remain distinct. An uncertain result is not a negative; not_applicable is a separate applicability judgment. A gap has no accepted result to display.")),
        page("How each two-page case is organized", table(["Layer", "What is shown"], [["Source", "Exact bounded quotation, block-relative offsets, context, credits and license"], ["Framework", "Frozen operational criterion, conditions, exclusions and version"], ["Model", "Original result/stance/attribution and recorded explanation, if any"], ["Review", "Actual recorded state; independent judgments remain pending"], ["Editorial", "AI reading note and questions, separately labelled"], ["Lineage", "Original payload hash and explicit historical-request-capture status"]]),
             p("A display excerpt may be shorter than the source unit and is selected editorially for layout. The complete selected source blocks and all recorded references remain in v04-case-atlas.snapshot.json.gz. Exact offsets describe the displayed text, not a generated paraphrase. Source context available to a reader now must not be confused with an actual historical request capture."),
             p("When a compact negative supplied no detailed rationale, the model layer says not recorded. The editorial layer can ask how to assess it but cannot supply a retrospective model explanation. The unresolved case similarly contains no invented criterion result or assessment ID.")),
    ]
    audit_by_key = {row["criterion_key"]: row for row in criteria_audit}
    for number, case in enumerate(atlas["cases"], 1):
        window = selected_window(case)
        item = case["assessment"]
        key = item["criterion_key"] if item else "; ".join(case["unresolved_unit"]["missing_criterion_keys"])
        framework = case.get("framework")
        source_blocks = [p("SOURCE LAYER · original text, not model-authored prose"),
                         {"type": "quote", "text": window["quote"]},
                         p(f"Exact display locator: {window['block_id']}[{window['start']}:{window['end']}]; page {window['page'] if window['page'] is not None else 'not supplied'}, paragraph {window['paragraph'] if window['paragraph'] is not None else 'not supplied'}. Zero-based, end-exclusive Unicode code points."),
                         table(["Bibliographic field", "Frozen value"], source_facts(case)),
                         {"type": "link", "text": "Original source", "url": case["source"]["source_url"]},
                         {"type": "label", "text": "Framework layer · recorded operational criterion"}]
        if framework:
            mapping = audit_by_key[key]
            source_blocks.extend([p(key + " · " + framework["version"] + " · " + framework["criterion"]["kind"]),
                                  p(framework["criterion"]["description"]),
                                  p("Rubric origin: " + ("embedded historical run snapshot" if framework["origin"] == "frozen_run_lens_snapshot" else "later text capture matching the historical selected-pack hash" if framework["origin"] == "verified_compatible_registry_capture" else "later version-checked capture; historical content hash unavailable")),
                                  p("Source mapping: " + mapping["source_locator"]),
                                  p("Scope limitation: " + mapping["coverage_limit"])])
        else:
            source_blocks.extend([p("Missing requested criterion: " + key), p("No accepted decision exists for this key. The source unit and its retained peers remain part of the frozen processing scope.")])
        pages.append(page(f"{number:02d} / Evidence · {case['title']}", *source_blocks, eyebrow="REAL CASE / SOURCE AND FRAMEWORK"))
        model_blocks = [{"type": "label", "text": "Model layer · original persisted reading"}]
        if item:
            model_blocks.append(table(["Recorded field", "Original value"], [["Outcome / kind", item["result"] + " / " + item["criterion_kind"]],
                ["Speaker / attribution", str(item.get("speaker") or "Unknown") + " / " + str(item.get("attribution"))],
                ["Proposition", item.get("proposition") or "Not recorded"], ["Speaker / document stance", str(item.get("speaker_stance")) + " / " + str(item.get("document_stance"))],
                ["Hypothetical / certainty", str(item.get("hypothetical")) + " / " + str(item.get("certainty"))],
                ["Unit / review state", item.get("recorded_unit_status") + " / " + item.get("review_status", "unreviewed")],
                ["Model", case["run"]["config"].get("model")], ["Assessment ID", item["id"]]]))
            model_blocks.extend([p("Recorded explanation: " + (item.get("explanation") or "No detailed model rationale was recorded.")),
                                 p("Recorded alternative: " + (item.get("alternative") or "No model alternative was recorded."))])
            if "comparison" in case:
                other = case["comparison"]["assessment"]
                model_blocks.extend([{ "type": "label", "text": "Second recorded model · not adjudication"},
                    table(["Field", "Verifier value"], [["Result / attribution", other["result"] + " / " + str(other.get("attribution"))],
                         ["Speaker / document stance", str(other.get("speaker_stance")) + " / " + str(other.get("document_stance"))],
                         ["Assessment ID", other["id"]]]),
                    p("Verifier explanation: " + (other.get("explanation") or "No detailed model rationale was recorded."))])
        else:
            unresolved = case["unresolved_unit"]
            model_blocks.extend([p("Result: not recorded. Model explanation: not recorded. Assessment ID: none for the missing decision."),
                                 table(["Recorded processing field", "Value"], [["Unit ID", unresolved["unit_id"]], ["Unit state", unresolved["status"]],
                                      ["Missing keys", "; ".join(unresolved["missing_criterion_keys"])], ["Evidence origin", "Software-rebuilt frozen source unit, not model-selected rationale"]])])
        model_blocks.extend([{ "type": "label", "text": "Editorial layer · AI-authored guidance, not independent human review"},
                             p(case["editorial"]["reading"]), p("Review question: " + case["editorial"]["question"]),
                             p("Human-review layer: independent judgments pending. Historical request capture: not recorded. A reconstructed template is not the actual request."),
                             p("Context excerpt for reading: " + window["context"][:370])])
        pages.append(page(f"{number:02d} / Readings · {case['title']}", *model_blocks, eyebrow="REAL CASE / MODEL, REVIEW AND EDITORIAL"))
    sources = list({case["source"]["id"]: case["source"] for case in atlas["cases"]}.values())
    for part in range(2):
        subset = sources[part * 7:(part + 1) * 7]
        blocks = [p("Credits are frozen bibliographic metadata. A containing document may quote additional speakers, who remain distinct from its byline. The evidence snapshot preserves full URLs, rights notes, source hashes and translator metadata.")]
        for source in subset:
            meta = source.get("metadata", {})
            translators = "; ".join(person["name"] for person in meta.get("translators", []) if person.get("name"))
            blocks.extend([p(source["title"] + " — " + str(source.get("author")) + "; " + str(source.get("published_at")) + "; " + str(source.get("license")) + ("; translation: " + translators if translators else "")),
                           {"type": "link", "text": "Original source · " + str(meta.get("source_id", source["id"])), "url": source["source_url"]}])
        pages.append(page("Source and rights ledger · " + str(part + 1), *blocks))
    pages.append(page("Research behind these questions · 1", *reference_blocks(["jacobs2021", "entman1993", "mohammad2016", "breck2004", "fan2019"])))
    pages.append(page("Research behind these questions · 2", *reference_blocks(["plank2022", "artstein2008", "rottger2021", "deyoung2020", "xiong2024"])))
    pages.append(page("Receipts, gaps and independent review", p("The case snapshot retains the complete selected sources, original payload hashes, frozen criteria, review overlays where recorded, and all reference anchors. The lineage index connects those objects to publication exports and the original portable archives."),
        table(["Publication property", "Claim"], [["Cases", "15 real-source cases: 14 actual assessments and one separate unresolved unit"], ["Outcomes", "All four categories retained; no forced verdict for missing work"], ["Source validity", "Exact Unicode references and source/version hashes checked"], ["Historical requests", "Not recorded for these cases; reconstructions explicitly labelled"], ["Human reference labels", "Not supplied; independent review remains pending"], ["Accuracy / F1", "Unmeasured; not inferred from agreement or source checks"], ["Inference for publication", "None; original saved records reused"]]),
        p("Reproduction uses the new v0.4 publication builder and original parent artifacts. It opens the recorded workspaces read-only, validates sources and writes only new v04-prefixed files. Existing v0.3 reports remain unchanged."),
        p("Use these cases to ask precise questions. A reviewer may later accept, reject or qualify an interpretation, but that action needs its own actor, evidence and review event. AI-authored reading guidance cannot supply that independent judgment.")))
    if len(pages) != 38:
        raise ValueError("Case atlas editorial page plan changed")
    return pages


def write_diagrams(root):
    diagrams = {
        "measurement-layers": [("SOURCE", "Preserved words", "Original author / rights"), ("FRAMEWORK", "Named criterion", "Conditions / exclusions"), ("MODEL", "Recorded outcome", "No accuracy implied"), ("REVIEW", "Actual judgments", "Pending if absent"), ("EDITORIAL", "Reading questions", "AI guidance / separate")],
        "processing-flow": [("INGEST", "Frozen source", "Hash + credits"), ("UNITS", "Exact segments", "Owned + context"), ("CLASSIFY", "Explicit vector", "Four outcomes"), ("DETAIL", "Re-evaluate", "IDs + attribution"), ("VALIDATE", "Resolve anchors", "Retain peers / gaps")],
        "attribution-chain": [("DOCUMENT", "Author of review", "Containing voice"), ("REPORT", "Reported source", "Not the byline"), ("POST", "Shares material", "May relay a video"), ("VIDEO", "Inner expression", "Speaker may be unknown"), ("RELATION", "Proposition / stance", "Adoption ≠ presence")],
        "review-loop": [("PUBLISH", "Source date", "Discourse history"), ("ANALYZE", "Run date", "Frozen method"), ("REVIEW", "Review event", "Human labels pending"), ("COMPARE", "Candidate / baseline", "Held-out scope"), ("ACTIVATE", "Explicit snapshot", "Rollback / provenance")],
        "lineage": [("PARENTS", "Sources + archives", "SHA256 receipts"), ("PERSISTED", "Original readings", "Not raw response"), ("TEMPLATE", "Reconstruction", "Not actual request"), ("EDITORIAL", "Authored guidance", "Separate origin"), ("EXPORT", "PDF / HTML / pack", "Hash-indexed outputs")],
    }
    for name, nodes in diagrams.items():
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 275" role="img" aria-labelledby="title desc"><title id="title">' + escape(name.replace("-", " ")) + '</title><desc id="desc">Methodological schematic. Origins and boundaries remain separate; no measured performance or accuracy is implied.</desc><rect width="1000" height="275" fill="#f1f4ec"/>']
        for index, (label, title, detail) in enumerate(nodes):
            left = 15 + index * 198
            svg.append(f'<rect x="{left}" y="44" width="178" height="173" rx="8" fill="{("#204d3a" if index % 2 == 0 else "#365d68")}"/><text x="{left + 13}" y="75" font-family="sans-serif" font-size="13" fill="#e3dfb5">{escape(label)}</text>')
            for y, text in [(112, title), (151, detail)]:
                # Manual two-line wrapping prevents labels disappearing beyond
                # the box in PDF renderers without foreignObject support.
                words, lines, current = text.split(), [], ""
                for word in words:
                    if len(current + " " + word) > 21:
                        lines.append(current)
                        current = word
                    else:
                        current = (current + " " + word).strip()
                lines.append(current)
                for offset, line in enumerate(lines):
                    svg.append(f'<text x="{left + 13}" y="{y + offset * 19}" font-family="sans-serif" font-size="{15 if y == 112 else 12}" fill="#ffffff">{escape(line)}</text>')
            if index < 4:
                svg.append(f'<path d="M {left + 181} 124 h 13 l -4 -4 m 4 4 l -4 4" stroke="#6d7d4f" fill="none" stroke-width="2"/>')
        svg.append('<text x="18" y="252" font-family="sans-serif" font-size="12" fill="#365744">Schematic only · Source integrity, model interpretation and independent review are different evidence.</text></svg>')
        path = root / "docs/images" / ("v04-" + name + ".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(svg), encoding="utf-8")


def write_research(root, packs):
    csl = [{key: value for key, value in ref.items() if key in {"id", "type", "title", "author", "issued", "URL", "DOI", "container-title"} and value is not None} for ref in REFERENCES]
    organizations = {"ihra": "International Holocaust Remembrance Alliance", "jda": "Jerusalem Declaration on Antisemitism",
                     "nexus": "Nexus Task Force", "anti-muslim-hostility": "Ministry of Housing, Communities and Local Government",
                     "anti-muslim-racism": "Runnymede Trust"}
    csl.extend({"id": "framework-" + identity, "type": "report", "title": item["title"],
                "author": [{"literal": organizations[identity]}], "issued": {"date-parts": [[item["year"]]]}, "URL": item["url"]}
               for identity, item in FRAMEWORKS.items())
    save_json(root / "docs/v04-bibliography.csl.json", csl)
    bib = []
    for ref in csl:
        bib_kind = "article" if ref["type"] == "article-journal" else "techreport" if ref["type"] == "report" else "inproceedings"
        fields = {"title": ref["title"], "author": " and ".join(author.get("family") or author["literal"] for author in ref["author"]),
                  "year": str(ref["issued"]["date-parts"][0][0]), "url": ref["URL"], "doi": ref.get("DOI"),
                  "journal" if bib_kind == "article" else "booktitle": ref.get("container-title")}
        bib.append("@" + bib_kind + "{" + ref["id"] + ",\n" + ",\n".join("  " + key + " = {" + value.replace("{", "\\{").replace("}", "\\}") + "}" for key, value in fields.items() if value) + "\n}")
    (root / "docs/v04-bibliography.bib").write_text("\n\n".join(bib) + "\n", encoding="utf-8")
    save_json(root / "docs/v04-research-map.json", {"schema_version": "doxa-research-map-0.4", "generated_at": datetime.now(timezone.utc).isoformat(),
        "papers": REFERENCES, "frameworks": FRAMEWORKS, "review_scope": "Decision-oriented source review, not a systematic literature review or independent model validation.",
        "rights": "Links and brief original summaries; full copyrighted academic papers and briefing PDFs are not redistributed."})
    audit = framework_audit(packs)
    save_json(root / "docs/v04-criterion-source-audit.json", {"schema_version": "doxa-criterion-source-audit-0.4", "criteria": audit,
        "operationalization_notice": "Original source conditions are distinguished from DOXA's partial operational summaries. Fresh IHRA-page access remains blocked; no completeness or official approval is claimed.",
        "frameworks": FRAMEWORKS, "independent_human_interpretation_validation": False})
    return audit


def template_reconstruction(root):
    from doxa.contextual import PROMPT_VERSION, SYSTEM, UNIT_BUILDER_VERSION
    value = {"schema_version": "doxa-template-reconstruction-0.4", "availability": "reconstructed_not_actual_request",
             "current_template_source": "doxa/contextual.py", "current_template_source_sha256": sha_file(root / "doxa/contextual.py"),
             "template_version": PROMPT_VERSION, "unit_builder_version": UNIT_BUILDER_VERSION,
             "system_template": SYSTEM, "historical_request_capture": lineage_status(),
             "notice": "This is the current source template, not an actual historical request. Per-request messages, packed context, schema, transport and raw response cannot be recovered merely from it."}
    save_json(root / "examples/v04-reconstructed-template.json", value)
    return value


def build_lineage(root, atlas, template, publications):
    records = []
    archive_parents = []
    for path in archive_inputs(root):
        identity = str(path.relative_to(root))
        original = lineage_record("source_artifact", payload_sha256=sha_file(path), source={"path": identity}, metadata={"artifact_role": "frozen_v03_parent"})
        records.append({"id": identity, **original})
        archive_parents.append({"kind": "source_artifact", "id": identity, "sha256": original["lineage_sha256"]})
    snapshot_record = lineage_record("source_artifact", payload_sha256=content_sha256(atlas), parents=archive_parents,
        source={"path": "examples/v04-case-atlas.snapshot.json.gz"}, metadata={"artifact_role": "read_only_case_capture_with_separate_editorial_layer"})
    records.append({"id": "case-atlas-snapshot", **snapshot_record})
    template_record = lineage_record("reconstructed_template", payload_sha256=content_sha256(template),
        origin_template={"id": GENERATOR_VERSION, "source_sha256": sha_file(Path(__file__))}, metadata={"actual_request_capture": "not_recorded"})
    records.append({"id": "reconstructed-template", **template_record})
    for case in atlas["cases"]:
        source = case["source"]
        source_record = lineage_record("source_artifact", payload_sha256=content_sha256(source["blocks"]),
            parents=[{"kind": "source_artifact", "id": "case-atlas-snapshot", "sha256": snapshot_record["lineage_sha256"]}],
            source={"id": source["id"], "source_id": source.get("metadata", {}).get("source_id"), "url": source["source_url"],
                    "content_hash": source["sha256"], "author": source.get("author"), "license": source.get("license")})
        source_record = {"id": case["id"] + ":source", **source_record}
        records.append(source_record)
        parents = [{"kind": source_record["kind"], "id": source_record["id"], "sha256": source_record["lineage_sha256"]}]
        if case["assessment"]:
            assessment = case["assessment"]
            model = case["run"]["model_profile"]
            rubric_record = lineage_record("source_artifact", payload_sha256=content_sha256(case["framework"]),
                parents=[{"kind": "source_artifact", "id": "case-atlas-snapshot", "sha256": snapshot_record["lineage_sha256"]}],
                metadata={"artifact_role": "frozen_rubric", "criterion_key": assessment["criterion_key"], "origin": case["framework"].get("origin")})
            rubric_record = {"id": case["id"] + ":rubric", **rubric_record}
            records.append(rubric_record)
            parents.append({"kind": rubric_record["kind"], "id": rubric_record["id"], "sha256": rubric_record["lineage_sha256"]})
            model_record = lineage_record("persisted_assessment", payload_sha256=assessment["recorded_payload_sha256"], parents=parents,
                origin_model={key: model.get(key) for key in ("provider", "model", "revision", "quantization", "prompt_version", "response_schema_version")},
                origin_template={"recorded_prompt_version": model.get("prompt_version"), "actual_request_available": False},
                metadata={"run_id": case["run"]["id"], "assessment_id": assessment["id"], "origins": assessment["origin_labels"],
                          "stored_payload_bytes_sha256": assessment["recorded_payload_bytes_sha256"], "raw_provider_response": "not_recorded"})
            model_record = {"id": case["id"] + ":assessment", **model_record}
            records.append(model_record)
            parents.append({"kind": model_record["kind"], "id": model_record["id"], "sha256": model_record["lineage_sha256"]})
            if "comparison" in case:
                comparison = case["comparison"]
                other = comparison["assessment"]
                profile = comparison["run"]["model_profile"]
                capture = next(capture for capture in atlas["captures"].values() if capture["run"]["id"] == comparison["run"]["id"])
                other_framework = capture["criterion_definitions"][other["criterion_key"]]
                other_rubric = lineage_record("source_artifact", payload_sha256=content_sha256(other_framework),
                    parents=[{"kind": "source_artifact", "id": "case-atlas-snapshot", "sha256": snapshot_record["lineage_sha256"]}],
                    metadata={"artifact_role": "frozen_comparison_rubric", "criterion_key": other["criterion_key"], "origin": other_framework["origin"]})
                other_rubric = {"id": case["id"] + ":comparison-rubric", **other_rubric}
                records.append(other_rubric)
                other_record = lineage_record("persisted_assessment", payload_sha256=other["recorded_payload_sha256"],
                    parents=[parents[0], {"kind": other_rubric["kind"], "id": other_rubric["id"], "sha256": other_rubric["lineage_sha256"]}],
                    origin_model={key: profile.get(key) for key in ("provider", "model", "revision", "quantization", "prompt_version", "response_schema_version")},
                    metadata={"run_id": comparison["run"]["id"], "assessment_id": other["id"], "origins": other["origin_labels"], "raw_provider_response": "not_recorded"})
                other_record = {"id": case["id"] + ":comparison", **other_record}
                records.append(other_record)
                parents.append({"kind": other_record["kind"], "id": other_record["id"], "sha256": other_record["lineage_sha256"]})
        else:
            missing = lineage_record("source_artifact", payload_sha256=content_sha256(case["unresolved_unit"]), parents=parents,
                metadata={"artifact_role": "unresolved_source_unit_with_recorded_status", "accepted_result": None,
                          "missing_criterion_keys": case["unresolved_unit"]["missing_criterion_keys"]})
            records.append({"id": case["id"] + ":unresolved-unit", **missing})
            parents.append({"kind": missing["kind"], "id": case["id"] + ":unresolved-unit", "sha256": missing["lineage_sha256"]})
        note = lineage_record("editorial_annotation", payload_sha256=content_sha256(case["editorial"]), parents=parents,
            actor={"kind": "ai", "identity": "AI-authored publication guidance; exact authoring model identity not captured"},
            origin_template={"id": GENERATOR_VERSION}, metadata={"independent_human_review": False})
        records.append({"id": case["id"] + ":editorial", **note})
    for publication in publications:
        for artifact in publication["files"]:
            record = lineage_record("report_export", payload_sha256=artifact["sha256"],
                parents=[{"kind": "source_artifact", "id": "case-atlas-snapshot", "sha256": snapshot_record["lineage_sha256"]}],
                origin_template={"id": GENERATOR_VERSION, "source_sha256": sha_file(Path(__file__))},
                metadata={"file": artifact["file"], "publication": publication["title"]})
            records.append({"id": artifact["file"], **record})
    value = {"schema_version": "doxa-v04-lineage-index-1", "records": records, "historical_actual_request_capture": lineage_status(),
             "raw_provider_responses": "not_recorded", "notice": "Reconstructed templates, persisted assessments and editorial notes are distinct. No missing historical capture is fabricated."}
    save_json(root / "examples/v04-lineage-index.json", value)
    return value


def export_publication(root, slug, title, pages, *, page_range):
    from weasyprint import HTML
    import pymupdf
    prefix = root / "examples" / slug
    if not slug.startswith("v04-"):
        raise ValueError("Publication destinations must preserve all original v0.3 artifacts")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    html = render_publication(title, pages, style="atlas" if slug.endswith("case-atlas") else "technical" if slug.endswith("technical-annex") else "reader")
    prefix.with_suffix(".html").write_text(html, encoding="utf-8")
    doc_path = root / "docs" / (slug + ".md")
    doc_path.write_text(manuscript(title, pages), encoding="utf-8")
    def no_network(*_args, **_kwargs):
        raise ValueError("Publication rendering cannot load remote resources")
    HTML(string=html, url_fetcher=no_network).write_pdf(prefix.with_suffix(".pdf"))
    with pymupdf.open(prefix.with_suffix(".pdf")) as pdf:
        count = len(pdf)
        page_text = [page.get_text() for page in pdf]
        if not page_range[0] <= count <= page_range[1]:
            raise ValueError(f"{slug} has {count} pages, outside the declared {page_range} range")
        if any(len(text.strip()) < 100 for text in page_text):
            raise ValueError("A publication contains a blank or contentless PDF page")
        links = sum(len(page.get_links()) for page in pdf)
        for old_preview in (root / "docs/images").glob(slug + "-page-*.png"):
            old_preview.unlink()
        for number in (0, count // 2, count - 1):
            pdf[number].get_pixmap(matrix=pymupdf.Matrix(1.15, 1.15)).save(root / "docs/images" / f"{slug}-page-{number + 1:02d}.png")
    files = [{"file": str(path.relative_to(root)), "sha256": sha_file(path), "bytes": path.stat().st_size}
             for path in (prefix.with_suffix(".html"), prefix.with_suffix(".pdf"), doc_path)]
    manifest = {"schema_version": "doxa-v04-publication-1", "title": title, "slug": slug, "generator_version": GENERATOR_VERSION,
        "editorial_authorship": AUTHORSHIP, "independent_human_labels": False, "interpretation_accuracy": None,
        "planned_sections": len(pages), "pdf_pages": count, "pdf_links": links, "page_range": list(page_range), "files": files}
    save_json(prefix.with_suffix(".manifest.json"), manifest)
    return manifest


def build_publications(root: Path = ROOT, *, refresh_audit=False, refresh_capture=False):
    root = root.resolve()
    # Hash originals before touching any output. This list excludes only the
    # deliberately additive v04 namespace; it is checked again on success.
    original_files = [path for path in (root / "examples").iterdir() if path.is_file() and not path.name.startswith("v04-")]
    original_files.extend(archive_inputs(root))
    original_hashes = {str(path.relative_to(root)): sha_file(path) for path in original_files}
    audit = audit_archives(root, reuse=not refresh_audit)
    snapshot_path = root / "examples/v04-case-atlas.snapshot.json.gz"
    if snapshot_path.exists() and not refresh_capture:
        with gzip.open(snapshot_path, "rt", encoding="utf-8") as stream:
            atlas = json.load(stream)
    else:
        atlas = collect_cases(root)
    atlas["source_anchor_audit"] = audit_atlas(atlas)
    from doxa.lenses import get_lens
    packs = {pack["id"]: pack for capture in atlas["captures"].values() for pack in capture["run"]["config"].get("lens_snapshots", [])}
    for identity in ("discourse", "ihra", "jda", "nexus", "anti-muslim-hostility", "anti-muslim-racism", "political-framing", "language-norms"):
        if identity not in packs:
            packs[identity] = atlas.get("reference_packs", {}).get(identity) or get_lens(identity)
    atlas["reference_packs"] = packs
    write_diagrams(root)
    criterion_audit = write_research(root, packs)
    with (root / "examples/v04-case-atlas.snapshot.json.gz").open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write((json.dumps(atlas, ensure_ascii=False, sort_keys=True) + "\n").encode())
    template = template_reconstruction(root)
    publications = [
        export_publication(root, "v04-reader-explainer", "Reading public discourse with evidence", reader_pages(atlas, audit), page_range=(16, 20)),
        export_publication(root, "v04-technical-annex", "Technical annex: evidence, contracts and research", technical_pages(atlas, audit, packs, criterion_audit), page_range=(30, 40)),
        export_publication(root, "v04-case-atlas", "Fifteen real cases: an evidence atlas", atlas_pages(atlas, criterion_audit), page_range=(35, 45)),
    ]
    lineage = build_lineage(root, atlas, template, publications)
    changed = [name for name, expected in original_hashes.items() if sha_file(root / name) != expected]
    if changed:
        raise ValueError("An original baseline artifact changed during publication")
    detached = {"v04-evidence-pack.manifest.json", "v04-evidence-pack.zip", "v04-evidence-pack.zip.receipt.json"}
    artifacts = [path for path in (root / "examples").glob("v04-*") if path.is_file() and path.name not in detached]
    artifacts.extend(path for path in (root / "docs").glob("v04-*") if path.is_file())
    artifacts.extend(path for path in (root / "docs/images").glob("v04-*") if path.is_file())
    manifest = {"schema_version": "doxa-evidence-pack-0.4", "generated_at": atlas["generated_at"],
        "generator_version": GENERATOR_VERSION, "generator_sha256": sha_file(Path(__file__)),
        "authorship": AUTHORSHIP, "case_audit": atlas["source_anchor_audit"], "corpus_audit": "examples/v04-corpus-verification.json",
        "publications": [{key: value for key, value in item.items() if key != "files"} for item in publications],
        "files": [{"file": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha_file(path)} for path in sorted(artifacts)],
        "parents": [{"file": name, "sha256": value} for name, value in sorted(original_hashes.items())],
        "baseline_preservation": {"verified_unchanged": True, "files": len(original_hashes)},
        "lineage_records": len(lineage["records"]), "historical_actual_request_capture": lineage_status(),
        "independent_human_labels": False, "interpretation_accuracy": None, "paid_inference_calls_for_build": 0}
    save_json(root / "examples/v04-evidence-pack.manifest.json", manifest)
    # The detached download receipt avoids hashing an archive into a manifest
    # contained by that same archive. Internal members keep repo-relative paths.
    download = root / "examples/v04-evidence-pack.zip"
    members = [*manifest["files"], {"file": "examples/v04-evidence-pack.manifest.json"}]
    with zipfile.ZipFile(download, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for member in sorted(members, key=lambda item: item["file"]):
            info = zipfile.ZipInfo(member["file"], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (root / member["file"]).read_bytes())
    save_json(root / "examples/v04-evidence-pack.zip.receipt.json", {"schema_version": "doxa-publication-download-0.4",
        "file": "examples/v04-evidence-pack.zip", "sha256": sha_file(download), "bytes": download.stat().st_size,
        "members": len(members), "internal_manifest": "examples/v04-evidence-pack.manifest.json",
        "manifest_sha256": sha_file(root / "examples/v04-evidence-pack.manifest.json"),
        "notice": "Detached download receipt; archive and receipt intentionally excluded from their internal content manifest to avoid circular hashes."})
    print(json.dumps({"publications": [{"slug": item["slug"], "pdf_pages": item["pdf_pages"]} for item in publications],
                      "case_audit": atlas["source_anchor_audit"], "baseline_files_unchanged": len(original_hashes)}), flush=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--refresh-audit", action="store_true", help="Recheck all archive/source spans instead of an unchanged-input receipt")
    parser.add_argument("--refresh-capture", action="store_true", help="Read original local databases in mode=ro; default rebuilds from the bundled frozen case snapshot")
    args = parser.parse_args(argv)
    if args.audit_only:
        audit_archives(ROOT, reuse=not args.refresh_audit)
    else:
        build_publications(ROOT, refresh_audit=args.refresh_audit, refresh_capture=args.refresh_capture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
