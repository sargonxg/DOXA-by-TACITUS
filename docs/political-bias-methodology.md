# Political positions and framing: what the engine can establish

DOXA's `political-framing` lens, version `1.0.0`, describes how a supplied text expresses a policy position, evaluates actors, assigns responsibility, foregrounds voices and backgrounds premises. Each observation cites source-derived evidence and retains the attributed speaker, the document's stance and an alternative reading. It does not assign an author a political identity or place a publication on a universal left/right scale.

This is an operational research rubric. Its six criteria are **descriptive**, so a `meets_criterion` outcome means that the requested textual pattern was detected. It does not mean prejudice, factual error, unfairness or manipulation. A policy document can legitimately advocate a policy. A reporter can quote an evaluative accusation without adopting it. A difference between two texts can reflect their subject matter or genre.

The new lens is separate from the existing discourse, antisemitism, anti-Muslim and language-norm packs. Their criteria and versions, the global criterion registry version, the assessment contract and existing engine prompts are unchanged. Previously saved six-lens assessments do not retrospectively become political-framing assessments.

## Scientific basis and operational limits

| Primary research | Supported design choice | Limit of the inference |
| --- | --- | --- |
| [Entman, 1993](https://doi.org/10.1111/j.1460-2466.1993.tb01304.x), [original paper](https://fbaum.unc.edu/teaching/articles/J-Communication-1993-Entman.pdf) | Identify selection and salience through problem definitions, causal interpretations, moral evaluations and proposed remedies. | A frame detected in text does not guarantee an audience effect; Entman makes this distinction explicitly on page 53. A frame is not automatically unjustified. |
| [Mohammad et al., 2016, SemEval Task 6](https://aclanthology.org/S16-1003/) | Stance is toward a specified target and differs from sentiment. Preserve the proposition and separate speaker/document stance. | Section 2.1 distinguishes lack of evidence for favor/against from neutrality. Performance on the tweet benchmark does not establish performance on these documents. |
| [Card et al., 2015, Media Frames Corpus](https://aclanthology.org/P15-2072/) | Use explicit, cross-issue frame dimensions and evidence spans; retain interpretive disagreement. | The original study concerns three policy issues and does not validate an objective partisan score. Full source articles have separate redistribution restrictions. |
| [Recasens, Danescu-Niculescu-Mizil and Jurafsky, 2013](https://aclanthology.org/P13-1162/) | Examine appraisal and perspective-specific wording, and qualified presupposition/entailment cues. | Their empirical setting is Wikipedia neutrality edits and locating bias-inducing words in selected biased sentences. It does not establish the falsity of political advocacy or an author's ideology. |
| [Fan et al., 2019, BASIL](https://aclanthology.org/D19-1664/), [paper](https://aclanthology.org/D19-1664.pdf) | Distinguish lexical choices from informational selection and organization. Comparisons can require other reports of the same event and broader article context. | The study uses 300 articles and 1,727 annotated spans. Its design does not make missing words in an arbitrary corpus proof of deliberate omission. DOXA does not bundle those copyrighted news articles. |
| [Spinde et al., 2021, BABE](https://aclanthology.org/2021.findings-emnlp.101/) | Treat word-choice judgments as context dependent and obtain trained human review before reporting interpretive accuracy. | The paper's dataset/model results are its own. They are not an accuracy estimate for this lens or the selected inference model. |

These studies motivate the rubric; they do not certify the model applying it. DOXA's exact quotation checks verify provenance and offsets, not the truth or completeness of an interpretation. Without independent human reference labels, political interpretation accuracy, recall and F1 remain unmeasured.

## Six separate questions

| Criterion ID | Useful observation | Required restraint |
| --- | --- | --- |
| `expressed_policy_position` | The identified speaker supports or opposes a named action, proposition or remedy. | Topic mention and sentiment do not suffice. No evidence of a stance does not imply a neutral stance. |
| `evaluative_language` | A cited metaphor or appraisal characterizes a stated target in a particular way. | Strong language alone establishes neither factual falsity nor deceptive intent. Apply the same descriptive rule across authors. |
| `agency_causal_responsibility` | The source asserts or attributes who acts, causes, benefits or bears responsibility; an actor may be grammatically unspecified. | Describe a causal allegation as an allegation. Do not turn temporal succession into cause or passive voice into intentional concealment. |
| `source_voice_foregrounding` | A specified voice or authority is locally quoted, cited or made prominent. | The containing author is not automatically the quoted speaker. A voice's presence alone cannot establish unfair exclusion of others. |
| `comparator_dependent_asymmetry` | A declared comparison shows a qualified difference in evidence standards, attribution, appraisal or relevant coverage. | Requires supplied matched evidence and a declared comparison question. Different conduct or evidence may justify different treatment; equal airtime is not an objective fairness benchmark. |
| `presupposition_hypothesis` | A textual trigger backgrounds a stated premise, with an alternative interpretation. | A backgrounded premise may already have been asserted. Do not invent exclusivity, hidden beliefs or motives, or treat the premise as independently proved. |

Every displayed finding should retain lens/criterion versions, source version/hash, document and block IDs, Unicode offsets, source quote, immediate context, attributed speaker, speech role, proposition, speaker stance, document stance, certainty, explanation, alternative reading and review state. These fields make an observation inspectable; they do not make it a legal or factual verdict.

## Comparator policy

A comparison packet must declare its question and included sources **before** drawing a comparative conclusion. Record the packet ID/version, source versions, evidence anchors, selection rules, time window, genres, relevant actors and matching limitations. Distinguish a bounded difference in supplied passages from a population-wide claim about an outlet, an administration or all coverage.

For a relevant asymmetry inquiry without a supplied declared comparator, use `uncertain` with `insufficient_context`. Use `not_applicable` where no comparative inquiry is being attempted. A missing comparator is not `does_not_meet`, neutrality or proof of no bias. With a comparator, a reviewer can accept a bounded observation or still find the question uncertain. No automatic equal-airtime requirement, universal political center or left/right label is used.

The generic contextual assessment route supplies the scored document and local context. Adding this lens alone does **not** provide a second document or enforce a cross-document factual check. Its comparator requirement is an explicit rubric instruction; a paired-source reporting tool must supply and validate its own comparison packet and preserve uncertainty when relevant evidence is absent. Do not present a single-document assessment as a completed omission audit.

Omission claims require more than an observed absence. The user must explain why the absent information was relevant and reasonably available within the declared coverage scope, and provide evidence for that explanation. Cross-document differences are not automatically a double standard. Deliberate suppression, hidden intentions and audience effects are outside this textual analysis.

## Reproducible real-source case packet

Packet `political-framing-institutional-v1` uses four existing full institutional documents. The passages below are **curated worked explanations, not model outputs, independent gold labels or a representative political sample**. Their selection illustrates six analytical questions and known attribution/comparator failure modes. It is not selected to estimate prevalence or score political actors.

| ID | Original publication | Credited author | Source |
| --- | --- | --- | --- |
| `us01` | 2023-05-25 | White House, Biden administration | [The U.S. National Strategy to Counter Antisemitism](https://bidenwhitehouse.archives.gov/wp-content/uploads/2023/05/U.S.-National-Strategy-to-Counter-Antisemitism.pdf) |
| `us03` | 2025-01-29 | President Donald J. Trump | [Executive Order 14188: Additional Measures To Combat Anti-Semitism](https://www.govinfo.gov/content/pkg/DCPD-202500196/html/DCPD-202500196.htm) |
| `us04` | 2025-01-20 | President Donald J. Trump | [Executive Order 14149: Restoring Freedom of Speech and Ending Federal Censorship](https://www.govinfo.gov/content/pkg/DCPD-202500116/html/DCPD-202500116.htm) |
| `uk07` | 2016-12-20 | Government Equalities Office | [Response to the Women and Equalities Committee Report on Employment Opportunities for Muslims in the UK](https://assets.publishing.service.gov.uk/media/5a800a2b40f0b62305b88cee/58077_Cm9371_WEB.pdf) |

The US federal prose is recorded in the institutional corpus as a US government work; the UK publication is recorded as OGL-UK-3.0, subject to its notices and third-party exceptions. Preserve the corpus attribution and rights metadata. These examples do not expand the source licenses or supply legal clearance for separately attributed materials. No new source download is required.

The packet freezes these existing extraction hashes (`content_sha256`):

```json
{
  "packet_id": "political-framing-institutional-v1",
  "lens_id": "political-framing",
  "lens_version": "1.0.0",
  "selection": "Purposive teaching examples; four full institutional sources, with exact evidence anchors below.",
  "comparison_question": "How do these supplied passages express remedies, appraisals, agency and source voices?",
  "comparison_scope": "Illustrative passage differences only; not a fairness, factual-accuracy or outlet-prevalence comparison.",
  "matching_limits": ["Different dates", "Different genres and document lengths", "Different policy questions", "No exhaustive external evidence inventory"],
  "independent_human_gold": false,
  "sources": {
    "us01": "8a74e1bd02e4c0baf897e5b4ecacea5163c7d8118e96751905bc8b40ebe7aaac",
    "us03": "ec1701f0a98163e0c6739c24142977998dd41a2f9055b32d90bed535851bdcac",
    "us04": "fc7fe1f1f99c8a8f1b0bc0948c9611307fff901f4c85c4e6f9c62313dfaefa1c",
    "uk07": "c826adb7813babc836a8456fa9fb7c517710bb481b9548efb36710ee087c8f6e"
  }
}
```

All locators below are zero-based, end-exclusive **Unicode code-point offsets into that block's `text`**, not PDF byte offsets or document-wide offsets. A PDF page is the extracted source page. The tests verify every quoted span against the bundled original extraction. Keep the full source available for review; the selected quotes are not full-document semantic coverage.

### 1. A policy position can be clear without a partisan label

`us03-b00007[18:264]`:

> It shall be the policy of the United States to combat anti-Semitism vigorously, using all available and appropriate legal tools, to prosecute, remove, or otherwise hold to account the perpetrators of unlawful anti-Semitic harassment and violence.

`us01-p0006-b0006[389:538]`, page 6:

> It urges action from all of society—state and local authorities, civil society, community and faith leaders, the private sector, individual citizens.

**Comment:** The order emphasizes legal enforcement; the strategy extends responsibility to nongovernmental actors. Both passages support combating antisemitism through stated instruments.

**Alternative and limit:** Neither passage shows that its document excludes the other approach. A short order and a 60-page strategy serve different purposes. These quotes do not establish a left/right position, authoritarianism or the absence of earlier action. The named policy proposition is the appropriate target of a stance observation.

### 2. Apply the same descriptive rule to strong wording across authors

`us04-b00005[268:374]`:

> the previous administration trampled free speech rights by censoring Americans' speech on online platforms

`us01-p0002-b0001[422:496]`, page 2:

> The very soul of our Nation was hanging in the balance. It still is today.

**Comment:** “Trampled” evaluates an opponent's conduct through a metaphor. “The very soul” presents the stakes through moral and existential imagery. The target and function differ, but the rule for describing metaphor applies to both.

**Alternative and limit:** Emotional language can accompany well-supported arguments. The observation establishes neither factual falsity nor equivalent accuracy, severity or motives. An advocacy genre can legitimately use appraisal.

### 3. An attributed cause is not independently verified causation

`us03-b00006[227:386]`:

> The prior administration effectively nullified Executive Order 13899 by failing to give the terms of the order full force and effect throughout the Government.

`us01-p0002-b0001[1743:1913]`, page 2:

> Our intelligence agencies have determined that domestic terrorism rooted in white supremacy—including antisemitism—is the greatest terrorist threat to our Homeland today.

**Comment:** The first assigns implementation failure to the prior administration. The second attributes a threat assessment to intelligence agencies and describes a source of domestic terrorism. Preserve the source's attribution when explaining either assertion.

**Alternative and limit:** These excerpts do not verify implementation history, the underlying intelligence assessment or comparative threat rankings. They concern different propositions and are not a matched factual dispute.

### 4. A containing document can contain opposing institutional voices

`uk07-p0006-b0002[17:251]`, page 6:

> The Government must work to rebuild trust with Muslim communities by adopting an approach to integration which focuses on how it improves the life chances of disadvantaged communities rather than through the lens of counter-extremism.

`uk07-p0007-b0001[405:513]`, page 7:

> The Government does not accept at this stage that there is a need for a plan with specific focus on Muslims.

**Comment:** The first is Women and Equalities Select Committee **Recommendation 1**, reproduced in the government response. It foregrounds life chances rather than counter-extremism. The second is the government's own response to **Recommendations 2 and 3**, declining a Muslim-specific plan at that stage. A government byline does not turn the first into a government assertion.

**Alternative and limit:** The statements address related but different recommendations; they are not a direct contradiction without that qualification. The PDF extraction contains multiple voices in a single block, so speaker attribution needs the recommendation/response structure and surrounding pages.

### 5. A good comparative result can be “asymmetry not established”

`us01-p0011-b0003[0:134]`, page 11:

> To develop this strategy, we held listening sessions with more than 1,000 diverse stakeholders across the Jewish community and beyond.

`us03-b00006[986:1260]`:

> A joint report by the House Committees on Education and the Workforce, Energy and Commerce, Judiciary, Oversight and Accountability, Veterans' Affairs, and Ways and Means calls the Federal Government's failure to fight anti-Semitism and protect Jewish students "astounding."

**Comment:** One passage foregrounds consultation breadth; the other foregrounds a congressional report. “Astounding” is an appraisal attributed to that report within the order.

**Comparator result:** Evidential asymmetry is **not established** by these passages. Consultation for a strategy and a report supporting a particular accusation are different activities. The number consulted or presence of a citation does not prove inconsistent evidential standards. For an asymmetry inquiry this packet lacks a sufficiently matched comparator and should preserve `uncertain`, not return a completed finding of fairness or unfairness.

**Needed evidence:** Declare similar claims, comparable scope/genres/time periods, inclusion rules, relevant available evidence and standards for assessing evidence and counterevidence. Merely observing different sources or equal/unequal quotation counts does not complete that task.

### 6. A presupposition hypothesis must not become a hidden-belief claim

`us04-b00010[0:132]`:

> identify and take appropriate action to correct past misconduct by the Federal Government related to censorship of protected speech.

**Comment:** “Correct past misconduct” backgrounds the proposition that the government committed misconduct involving censorship of protected speech.

**Alternative and limit:** The earlier purpose paragraph already explicitly alleges misconduct. This can be the backgrounding of a previously asserted premise rather than an uncovered unstated belief. The quote independently establishes neither misconduct nor intentional deception. Preserve the source's claim and the hypothesis's qualification.

To reproduce the provenance checks, run:

```sh
.venv/bin/pytest -q tests/test_political_framing.py
```

The tests also protect existing lens definitions and demonstrate that a detected political-framing observation remains descriptive in the contextual assessment contract. A scripted provider tests that contract; it is explicitly not a model-quality benchmark. Actual model outputs need a separately identified inference run, its coverage, failures, source versions and review status.

## Useful dashboards and review

A useful dashboard keeps the six questions separate: named policy propositions and stances; evidence-backed evaluative patterns by target; asserted versus attributed responsibility; locally foregrounded source voices; and qualified comparison questions. It can group by the credited author, institution, document type and publication period recorded in metadata. It must expose source volume, assessable coverage, uncertain outcomes, duplicates and review state alongside any rate.

Do not sum these observations into an ideological or prejudice score. A count of metaphors measures detected metaphors under this rubric, not dishonesty. A single publisher cohort cannot establish the balance of all journalism. Source-publication trajectories and later reviewer/model changes are different histories and should remain distinct.

Reviewers should inspect source context, confirm speaker/proposition attribution, state whether they accept the observation, record an alternative and flag missing comparator evidence. Preserve disagreement rather than forcing a consensus label. Before publishing interpretive accuracy, define a held-out evaluation set, independent annotation instructions, disagreement handling and criterion-specific metrics with uncertainty. Exact-span validation and attractive reports cannot substitute for that evaluation.
