"""Editorial annotations refer to exact frozen assessments and source sentences."""

from .cohort import RESULT_LABELS

# One reading per teaching question. The six cases are purposive after selection of the cohort.
NOTES = [
    (
        "784b899f557a471b1b3f82d58d2a4508",
        None,
        "Whose stereotype is this?",
        "A jewellery advertisement showed an interfaith family. The article attributes the accusation to activists and calls it a conspiracy theory. That wording matters when interpreting the flag.",
        "Check who made the allegation and how the reporter introduced it. Assigning the activists’ claim to the journalist would reverse the reading.",
        None,
    ),
    (
        "7e08d3e3122332f76811e3bd9e9cbefb",
        None,
        "One school decision, two different questions",
        "The account identifies a concrete condition for attending class. The next paragraph reports that lower courts backed the principal and that the Supreme Court later affirmed the student’s right to wear the hijab.",
        "A case can support a reading of unequal treatment. Establishing a repeated practice would require records from other cases, dates or schools.",
        "09f824e6a4af44ac297dc8ec25fc4c85",
    ),
    (
        "04abefed865ec931ee95f8a208ea80cd",
        None,
        "What does the author actually support?",
        "The final paragraph calls for freedom to choose whether to wear a veil. The model marked the author as supporting the proposition about the risk of discrimination.",
        "Read the proposition alongside the stance. Support for a woman’s freedom of choice gives this author-attributed flag a very different meaning from support for discrimination.",
        None,
    ),
    (
        "46be81204630ae235741744a93c7f72b",
        None,
        "A flag to challenge",
        "The sentence identifies women as the group barred from care. Neither this sentence nor its paragraph gives Muslim identity as the basis for the restriction. That basis is required by the exclusion criterion.",
        "A reviewer should ask what evidence connects this restriction to religious identity. The published count includes the original flag and keeps this challenge alongside it.",
        None,
    ),
    (
        "23f39c04aea7d023d0e5594826b219cd",
        "s_0878c4ddc5514fc511916aa0",
        "A possible miss in an unflagged article",
        "The paragraph identifies the Uyghurs as a Muslim Turkic people, then reports labels applied to them in Chinese mainstream media. The model recorded that the group-stereotyping criterion was unmet in this unit.",
        "A reviewer should examine whether these reported labels construct the group as inherently suspect and how ethnic and religious targeting intersect. The article attributes the labels to the media.",
        None,
    ),
    (
        "d16abe150d9e8bf60df196c3c0e733db",
        "s_48062527feb0c63a8958537c",
        "A religious reference can be biographical",
        "The article lists milestones in Samia Suluhu Hassan’s presidency. Her religious identity is part of the biographical description. The cited sentence describes no threat or abuse.",
        "The word Muslim brought this article into the sample. A reader still needs a target and hostile expression to support a targeted-hostility flag.",
        None,
    ),
]

REFERENCES = [
    (
        "Defining the construct",
        "Erik Bleich (2011). What Is Islamophobia and How Much Is There?",
        "Different definitions change what a measure can capture. Here each row names the textual criterion and its evidence requirements.",
        "https://doi.org/10.1177/0002764211409387",
    ),
    (
        "Defining the harm",
        "Su Lin Blodgett, Solon Barocas, Hal Daumé III and Hanna Wallach (2020). Language (Technology) is Power.",
        "Specify the harm, the people affected and the interpretation a measure permits. A count of article flags cannot measure a reporter’s private beliefs.",
        "https://aclanthology.org/2020.acl-main.485/",
    ),
    (
        "Reading stance",
        "Saif Mohammad et al. (2016). SemEval-2016 Task 6. Detecting Stance in Tweets.",
        "Stance needs a specific target. For each reading, ask who supports which proposition. The original tweet benchmark supplies no accuracy estimate for these articles.",
        "https://aclanthology.org/S16-1003/",
    ),
    (
        "Independent review",
        "Ron Artstein and Massimo Poesio (2008). Inter-Coder Agreement for Computational Linguistics.",
        "Independent annotations and reported disagreement would be needed to assess how consistently people apply the rubric. This study has no independent reference labels.",
        "https://aclanthology.org/J08-4004/",
    ),
    (
        "Operational framework",
        "UK Government (2026). A Definition of Anti-Muslim Hostility.",
        "The first three criteria adapt England’s non-statutory guidance to textual review. Applying them retrospectively across countries gives a reading under this framework, without deciding legal liability.",
        "https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility",
    ),
    (
        "Operational framework",
        "Runnymede Trust (2025). Islamophobia: towards a working definition.",
        "The second framework prompts separate questions about group generalization, exclusion and institutional practice. A claim about repeated practice needs corroboration beyond a single passage.",
        "https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition",
    ),
]


def cases(study):
    index = {a["id"]: (d, a) for d in study["documents"] for a in d["decisions"]}
    output = []
    for identity, sentence, heading, reading, followup, peer_id in NOTES:
        doc, row = index[identity]
        refs = row["evidence_refs"]
        ref = next(r for r in refs if r["sentence_id"] == sentence) if sentence else refs[0]
        block = next(b for b in doc["blocks"] if b["id"] == ref["block_id"])
        if block["text"][ref["start"] : ref["end"]] != ref["quote"]:
            raise ValueError("Editorial quotation moved away from its frozen source")
        peer = index[peer_id][1] if peer_id else None
        if peer and (peer["document_id"], peer["unit_id"]) != (row["document_id"], row["unit_id"]):
            raise ValueError("Paired readings must refer to the same scored unit")
        output.append(
            {
                "doc": doc,
                "decision": row,
                "ref": ref,
                "context": block["text"],
                "heading": heading,
                "reading": reading,
                "followup": followup,
                "anchor_origin": (
                    "Sentence selected by the model"
                    if row.get("detail_supplied")
                    else "Sentence selected by the editor from software-attached unit references"
                ),
                "result_label": RESULT_LABELS[row["result"]],
                "peer": peer,
            }
        )
    return output
