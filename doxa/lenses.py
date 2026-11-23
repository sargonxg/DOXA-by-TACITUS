"""Operational lens packs; a rubric is an interpretation, never a legal verdict.

These concise criteria are original operational summaries. Linked sources remain
authoritative. Different definitions are intentionally not merged or voted on.
"""
from __future__ import annotations

from copy import deepcopy
import re

LENSES = [
    {
        "id": "discourse", "name": "Doxa & discourse", "version": "1.0.0",
        "description": "Inspect expressed positions, framing, values and reviewable assumptions.",
        "source_url": "https://aclanthology.org/P15-2072/",
        "scope": "Descriptive discourse analysis; implicit assumptions are hypotheses, not private beliefs.",
        "criteria": [
            {"id": "stance", "label": "Expressed stance", "description": "Identify a position toward a specified proposition, with the speaker and target explicit. Sentiment alone is not stance."},
            {"id": "framing", "label": "Problem framing", "description": "Identify how a passage defines a problem, assigns causes or responsibility, invokes a value, or proposes a remedy."},
            {"id": "presupposition", "label": "Presupposition hypothesis", "description": "Identify an assumption taken for granted by the wording. Mark it ambiguous where alternative readings exist; do not infer an author's hidden beliefs."},
            {"id": "rhetoric", "label": "Rhetorical device", "description": "Describe an evidenced generalization, contrast, euphemism, moral boundary, metaphor, or appeal to authority; explain its textual function."},
        ],
        "context_rules": "Distinguish the document author from quoted or reported speakers. Absence of a viewpoint is meaningful only against a declared comparator. Do not infer religious or political identities.",
    },
    {
        "id": "ihra", "name": "Antisemitism · IHRA", "version": "2026-11-operational-1",
        "description": "Apply a context-sensitive operational summary of the IHRA working definition.",
        "source_url": "https://holocaustremembrance.com/resources/working-definition-antisemitism",
        "scope": "Non-legally-binding framework. Findings concern passages, not whether a person is antisemitic.",
        "criteria": [
            {"id": "anti_jewish_generalization", "label": "Anti-Jewish hostility or stereotyping", "description": "Assess hostility, dehumanization, conspiracies, or collective allegations directed at Jews as Jews."},
            {"id": "collective_responsibility", "label": "Collective responsibility", "description": "Assess holding Jewish people collectively responsible for real or alleged acts of another person, group, or Israel."},
            {"id": "israel_related_context", "label": "Israel-related expression in context", "description": "Assess relevant IHRA examples in overall context, including anti-Jewish tropes, discriminatory standards or denial of Jewish self-determination. Do not assume that policy criticism alone satisfies a criterion."},
        ],
        "context_rules": "The examples require overall context. Criticism of Israel similar to criticism of other countries is not antisemitic under the source text. Distinguish quotation, reporting, opposition to hate, criticism of a government, and endorsement. Indicate insufficient context when evidence does not establish the criterion.",
    },
    {
        "id": "jda", "name": "Antisemitism · Jerusalem Declaration", "version": "2021-operational-1",
        "description": "Read passages through the Jerusalem Declaration's definition and contextual guidelines.",
        "source_url": "https://jerusalemdeclaration.org/",
        "scope": "Alternative framework with its own protections for political expression; no legal determination.",
        "criteria": [
            {"id": "jews_as_jews", "label": "Discrimination against Jews as Jews", "description": "Assess discrimination, prejudice, hostility, or violence against Jews as Jews or Jewish institutions as Jewish."},
            {"id": "tropes_collective_guilt", "label": "Tropes and collective guilt", "description": "Assess anti-Jewish conspiracies, essentializing stereotypes, and responsibility assigned to Jews for Israel's conduct."},
            {"id": "political_expression_boundary", "label": "Political-expression boundary", "description": "Differentiate anti-Jewish expression from political criticism. BDS, anti-Zionism, constitutional alternatives securing equality, and disproportionate criticism are not inherently antisemitic under this framework."},
        ],
        "context_rules": "Read all relevant contextual guidelines. Political position by itself does not establish anti-Jewish prejudice. Preserve genuine ambiguity and distinguish a speaker's statements from text quoting or opposing them.",
    },
    {
        "id": "nexus", "name": "Antisemitism · Nexus", "version": "2024-06-operational-1",
        "description": "Apply the Nexus distinction between anti-Jewish prejudice and political opposition.",
        "source_url": "https://nexusproject.us/nexus-resources/the-nexus-document/",
        "scope": "Contextual framework; findings do not establish a person's intentions or legal liability.",
        "criteria": [
            {"id": "anti_jewish_tropes", "label": "Anti-Jewish tropes", "description": "Assess stereotypical assertions, conspiracies, demonization, or unequal treatment directed at Jews as Jews."},
            {"id": "collective_guilt", "label": "Collective guilt", "description": "Assess blaming Jews collectively for Israel or presuming Jewish individuals are agents of Israel."},
            {"id": "opposition_boundary", "label": "Political-opposition boundary", "description": "Distinguish hostility to Jews from criticism of Israel or Zionism. Disproportionate attention to Israel is not prima facie proof of antisemitism."},
        ],
        "context_rules": "Do not classify political opposition from keywords alone. Identify the target, source speaker and supporting context. Describe systemic claims only where the supplied corpus supports them.",
    },
    {
        "id": "anti-muslim-hostility", "name": "Anti-Muslim hostility · England 2026", "version": "2026-03-operational-1",
        "description": "A contextual reading of England's non-statutory Anti-Muslim Hostility definition.",
        "source_url": "https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility",
        "scope": "England-specific non-statutory definition. Text alone may not establish intention or unlawful discrimination.",
        "criteria": [
            {"id": "targeted_hostility", "label": "Hostility toward Muslims", "description": "Identify evidenced threatening, abusive, or hostile expression targeting people because they are Muslim or perceived to be Muslim."},
            {"id": "stereotyping_and_intent", "label": "Stereotyping and intent", "description": "Assess stereotyping intended to encourage hatred of Muslims. If intent is not established by context, explicitly mark insufficient_context rather than claiming it."},
            {"id": "discrimination_claim", "label": "Targeted discrimination indicators", "description": "Identify stated exclusion or unequal treatment directed at Muslims; do not pronounce legal unlawfulness from a passage."},
        ],
        "context_rules": "The guidance protects criticism, ridicule, and discussion of Islam. Differentiate criticism of doctrines, religious institutions or policy from hostility to people. Do not presume intent from an isolated word or a demographic label.",
    },
    {
        "id": "anti-muslim-racism", "name": "Islamophobia · structural racism", "version": "2025-operational-1",
        "description": "Inspect anti-Muslim racialization and exclusion, including evidenced institutional patterns.",
        "source_url": "https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition",
        "scope": "Structural analytical lens, distinct from the England conduct-and-intent framework.",
        "criteria": [
            {"id": "racialization", "label": "Racialization and essentializing", "description": "Assess treating Muslims or perceived Muslims as a homogeneous, inferior, threatening, or inherently suspect population."},
            {"id": "exclusion", "label": "Exclusion and unequal treatment", "description": "Identify expressed exclusion, discriminatory expectations, or restrictions attached to Muslim identity."},
            {"id": "institutional_pattern", "label": "Institutional pattern hypothesis", "description": "Identify an evidenced institutional practice or repeated pattern. One passage cannot by itself establish a systemic pattern; mark insufficient context when required."},
        ],
        "context_rules": "Separate critique of beliefs or institutions from racialization of people. A structural claim needs corpus context and an explicit comparator; preserve its evidential limits.",
    },
    {
        "id": "language-norms", "name": "Language norms & political correctness", "version": "1.0.0",
        "description": "Explore contested norms, censorship claims and institutional language expectations.",
        "source_url": "https://aclanthology.org/P15-2072/",
        "scope": "A user-defined inquiry, not an objective political-correctness score.",
        "criteria": [
            {"id": "norm_claim", "label": "Language-norm claim", "description": "Identify an explicit claim about acceptable, respectful, inclusive, offensive, or prohibited language and whose norm it represents."},
            {"id": "speech_boundary", "label": "Speech boundary and censorship framing", "description": "Describe a text's stated boundary between expression and harm, or its claim of censorship, without assuming the claim is factually proven."},
            {"id": "institutional_expectation", "label": "Institutional expectation", "description": "Identify an expressed institutional language rule, enforcement mechanism, or competing value such as inclusion and freedom of expression."},
        ],
        "context_rules": "State the user-selected inquiry and preserve competing interpretations. Never assign a universal PC score or assume a demographic group shares one view.",
    },
    {
        "id": "political-framing", "name": "Political positions & framing", "version": "1.0.0",
        "description": "Describe evidenced policy positions, evaluative wording, responsibility and source voices; qualify comparative claims.",
        "source_url": "https://doi.org/10.1111/j.1460-2466.1993.tb01304.x",
        "scope": "Descriptive textual analysis, not a universal political-bias, truth, fairness or left/right score. A detected pattern is not a verdict about a person or audience influence.",
        "criteria": [
            {"id": "expressed_policy_position", "label": "Expressed policy position", "description": "Identify an explicit position toward a named policy proposition, proposed action or remedy. State whose position it is and distinguish the attributed speaker's stance from the document's stance. Sentiment and topic mention alone do not establish stance; missing evidence of support or opposition does not imply neutrality. Do not infer a party, political identity or ideology from the position."},
            {"id": "evaluative_language", "label": "Evaluative language", "description": "Identify evidenced appraisal, metaphor or legitimizing/delegitimizing wording, its target and textual function. Preserve quotation, negation, counter-speech and the responsible speaker. Apply the same descriptive rule across authors. Strong or emotional wording can be justified: it does not by itself establish falsity, unfairness, deceptive intent or equivalent conduct by compared actors."},
            {"id": "agency_causal_responsibility", "label": "Agency and causal responsibility", "description": "Describe how the passage names, backgrounds or leaves unspecified an actor, cause, beneficiary or bearer of responsibility, and whether the source asserts or attributes the relationship. A causal allegation is not verified causation. Do not turn temporal association into causation, passive wording into deliberate concealment, or a framing observation into a finding about hidden motives."},
            {"id": "source_voice_foregrounding", "label": "Source and voice foregrounding", "description": "Identify whose claim, testimony, evidence or institutional authority is explicitly foregrounded and how it is attributed. Distinguish the containing document's author from quoted speakers, committee recommendations and reported allegations. A locally prominent voice is observable; claims that other voices were unfairly excluded require a declared comparator and relevant supplied evidence. Equal quotation counts are not a fairness standard."},
            {"id": "comparator_dependent_asymmetry", "label": "Comparator-dependent asymmetry", "description": "Assess a difference in evidential standards, appraisal, attribution or relevant coverage only against an explicitly declared comparator with supplied passages, inclusion rules and a shared comparison question. State topic, genre, date and available-evidence matching limits; unequal conduct or evidence may justify unequal treatment. Without this comparator, return uncertain for a relevant comparison inquiry or not_applicable when no comparison is attempted, never does_not_meet as a proxy for no bias. Mere corpus absence, different policy instruments or raw voice counts cannot establish omission, a double standard or deliberate selection."},
            {"id": "presupposition_hypothesis", "label": "Qualified presupposition hypothesis", "description": "Identify a specific premise backgrounded or taken for granted by a textual trigger, with the exact proposition, responsible speaker and a plausible alternative reading. Check surrounding explicit assertions: a repeated allegation may be backgrounded without being an uncovered unstated belief. Do not strengthen the wording into an exclusive or universal claim, infer private beliefs, or treat the premise as fact. Preserve uncertainty where readings differ."},
        ],
        "context_rules": "Ground each observation in supplied source evidence and context. Separate document and speaker stance, reporting, attributed quotation, hypothetical examples, negation and counter-speech. No automatic left/right label, universal political center, private identity inference, author-character judgment or aggregate bias score. Frame presence does not demonstrate audience effects. Comparative absence, omission, imbalance and evidential asymmetry require a declared matched comparator and supplied relevant evidence; otherwise preserve uncertainty. These are reviewable descriptive hypotheses, not validated accuracy or factual-verification claims.",
    },
]

CRITERION_REGISTRY_VERSION = "3.0.0"
SOURCE_SCOPE = "source_scope"
_BOUNDARIES = {("jda", "political_expression_boundary"), ("nexus", "opposition_boundary")}
for _lens in LENSES:
    _lens["criterion_registry_version"] = CRITERION_REGISTRY_VERSION
    for _criterion in _lens["criteria"]:
        _criterion["kind"] = ("descriptive" if _lens["id"] in {"discourse", "language-norms", "political-framing"}
                              else "boundary" if (_lens["id"], _criterion["id"]) in _BOUNDARIES else "indicator")

_ALIASES = {"core": "discourse", "doxa": "discourse", "core-discourse": "discourse", "discourse-core": "discourse", "antisemitism": "ihra", "antisemitism-ihra": "ihra", "antisemitism-jda": "jda", "antisemitism-nexus": "nexus", "islamophobia": "anti-muslim-racism", "pc": "language-norms", "political-correctness": "language-norms"}


def get_lens(lens_id: str) -> dict:
    lens_id = _ALIASES.get(lens_id, lens_id)
    for lens in LENSES:
        if lens["id"] == lens_id:
            return deepcopy(lens)
    raise ValueError(f"Unknown analytical lens: {lens_id}")


def validate_custom_lens(value: dict) -> dict:
    """Validate editable criteria before they become part of a frozen run."""
    if not isinstance(value, dict):
        raise ValueError("custom_lens must be an object")
    name = str(value.get("name", "Custom analytical lens")).strip()
    description = str(value.get("description", "User-specified analytical inquiry")).strip()
    criteria = value.get("criteria")
    if not name or len(name) > 160 or len(description) > 2000:
        raise ValueError("Custom lens needs a name up to 160 characters and a description up to 2000")
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
        raise ValueError("Custom lens needs between 1 and 12 explicit criteria")
    normalized = []
    used = set()
    for index, criterion in enumerate(criteria):
        if isinstance(criterion, str):
            criterion = {"id": f"criterion-{index + 1}", "label": criterion[:100], "description": criterion}
        if not isinstance(criterion, dict):
            raise ValueError("Each custom criterion must be text or an object")
        cid = str(criterion.get("id", f"criterion-{index + 1}"))
        label = str(criterion.get("label", criterion.get("name", ""))).strip()
        text = str(criterion.get("description", criterion.get("text", ""))).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", cid) or cid in used:
            raise ValueError("Custom criterion IDs must be unique short alphanumeric identifiers")
        if not label or len(label) > 160 or not text or len(text) > 1500:
            raise ValueError("Each custom criterion needs a label and an explicit description")
        used.add(cid)
        kind = criterion.get("kind", "descriptive")
        if kind not in {"indicator", "boundary", "descriptive"}:
            raise ValueError("Criterion kind must be indicator, boundary, or descriptive")
        applicability = criterion.get("applicability")
        if applicability not in (None, SOURCE_SCOPE):
            raise ValueError("Use source_scope applicability or leave applicability unspecified")
        entry = {"id": cid, "label": label, "description": text, "kind": kind}
        if applicability:
            entry["applicability"] = applicability
        normalized.append(entry)
    return {"id": "custom", "name": name, "description": description, "version": str(value.get("version", "1.0"))[:80], "criterion_registry_version": CRITERION_REGISTRY_VERSION, "source_url": "", "scope": "User-authored analytical rubric, not an established or validated scientific definition.", "criteria": normalized, "context_rules": "Ground every finding in exact source evidence. Preserve quotation, attribution, counter-speech and alternative interpretations."}
