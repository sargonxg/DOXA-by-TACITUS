# How DOXA measures bias signals

Start with a question that can be answered from the selected material. “Which accounts characterize the same protest as a threat, and what evidence do they cite?” gives a reviewer a target, a comparison, and a criterion they can dispute. “Is this publisher biased?” leaves the standard and the population undefined.

## From a research question to a recorded decision

1. **Freeze the inquiry.** State the outcome of interest, the collection rule, the unit of analysis, and an example that should fail each criterion. DOXA saves the question, rubric and method-card hashes, model digest, and source versions with the run. [Method cards](methodology-v06.md)
2. **Inspect extraction.** Read the PDF text or transcript preview against its original. DOXA preserves the extracted blocks, stable identifiers, and Unicode character positions. For SRT and WebVTT, it also records the start and end of each cue. OCR gaps or rejected cues can leave a source incomplete. Audio and video use supplied transcripts or captions. [Extraction and source versions](extraction-v06.md)
3. **Classify each unit against each criterion.** The contextual model receives a scored text unit, bounded neighboring context and the named rubric. It returns an explicit decision vector. Matches and uncertain readings require a concise explanation, an alternative, and sentence IDs. DOXA checks that the cited sentence exists in the supplied context and that at least one citation comes from the scored unit. It retrieves the actual quote from the source and checks its offsets before adding it to a scorecard. [Political framing implementation](political-bias-methodology.md)
4. **Reconcile the source outcomes.** A complete source contributes one result per criterion. A supported match takes priority. With no match, an uncertain unit keeps the source uncertain. An applicable non-match can support a negative outcome when no unit remains uncertain. A source that lacks a required decision or has unresolved extraction is incomplete. If all units are inapplicable, the source is inapplicable unless the rubric declares the source eligible.
5. **Review and report.** Inspect the quoted passage and surrounding wording. Check the target of the statement, qualifying language, and whether an alternative interpretation changes the result. Reuse the saved scorecard to produce the report. A review overlay uses explicit adjudicated corrections and preserves its own recorded basis. [Scorecards and export](research-scorecards.md)

## Political positions and framing

Use each measure to answer a distinct analytical question. The examples below are fictional.

| Criterion | Evidence that supports a reading | What to check before counting |
| --- | --- | --- |
| Expressed policy position | “We recommend limiting the closure to two streets.” The proposition and direction are explicit. | A summary of proposals can name a policy without taking a position. Record the target proposition in its own words. |
| Evaluative language | “A proportionate safeguard” evaluates a specified restriction favorably. | Identify the target and local context. Preserve negation, such as “hardly a safeguard.” |
| Agency and causal responsibility | “The blocked lane delayed two ambulances” supplies a stated causal explanation. | Preserve the claim as stated and check its support separately. A chronology by itself leaves responsibility unresolved. |
| Foregrounded evidence | A briefing opens with the ambulance dispatch log and foregrounds emergency access. | Identify the concrete passage and its location. A prominence finding describes evidence selection within the source. |
| Comparative asymmetry | Matched briefings use different standards for similar claims, such as quoting a number as settled on one side and requiring underlying records on the other. | Declare the issue and comparison set first. Supply evidence from both sides. The scorecard treats single-source findings of a match or non-match on this criterion as uncertain. |
| Presupposition | “Restore order” may take previous disorder for granted. | Identify the phrase and offer a plausible competing reading, such as restoring traffic flow. Preserve uncertainty when context leaves both readings open. |

For a difference in rates, match on the same event, topic, genre, and time window. DOXA builds the largest available set of one-to-one pairs within the chosen constraints, preferring shorter date gaps during its search. Among eligible inputs, identical copies have one representative per side, and content that appears on both sides is excluded. Each reported difference uses the same resolved pairs on both sides and includes the pair count. Review date gaps, source dependence, and case composition before generalizing. An omission inquiry also needs a list of relevant details reasonably available at the time. [Framing functions](https://doi.org/10.1111/j.1460-2466.1993.tb01304.x) · [Lexical and informational bias](https://aclanthology.org/D19-1664/) · [Matched scorecards](research-scorecards.md#read-the-measurements)

## Prejudice lenses and boundary checks

These lenses apply separate definitions. Detection starts with the group or institution targeted, the conduct or claim described, and the context that makes the criterion applicable.

| Lens | How a reviewer tests a signal | Useful boundary check |
| --- | --- | --- |
| IHRA antisemitism | Look for hostility toward Jews as Jews, anti-Jewish stereotypes, collective responsibility, or a relevant Israel-related example. Cite the exact wording and context supporting the named rule. | Review whether a passage criticizes a government policy using standards applied to other governments. [IHRA](https://holocaustremembrance.com/resources/working-definition-antisemitism) |
| Jerusalem Declaration | Check hostility, prejudice, or discrimination against Jews as Jews, including the use of tropes or collective guilt. | Evaluate political criticism, advocacy, and historical analogies under the Declaration's own guidelines. A satisfied political-expression boundary describes that criterion. [Jerusalem Declaration](https://jerusalemdeclaration.org/) |
| Nexus | Look for anti-Jewish tropes, accusations of collective guilt, or discrimination in the context of discussion about Israel or Zionism. | Assess opposition to a policy or to Zionism separately from evidence of anti-Jewish prejudice. [Nexus](https://nexusproject.us/the-nexus-document/) |
| Anti-Muslim hostility · England | Look for hostility, relevant stereotyping or unequal treatment directed at Muslims or people perceived to be Muslim. If the chosen test requires intent, identify the contextual evidence supporting it. | Inspect whether the passage debates a doctrine, critiques a policy, or directs hostility at people. [England guidance](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility) |
| Anti-Muslim racism · structural lens | Look for racialization, exclusion, or an institutional practice. For the pattern criterion, scope the claimed practice and locate corroborating decisions or evidence of recurrence. | A removal request concerning a hijab can support an unequal-treatment inquiry. The institutional-pattern question still depends on evidence about how the practice is applied. [Runnymede Trust](https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition) |

For example, the Plzeň case uses the same excerpt to ask how three antisemitism frameworks treat a collective religious accusation. Archived model outcomes differ, which gives reviewers a chance to examine rule application and possible errors. The anti-Muslim bias study follows the same event through unequal treatment and an unresolved institutional-pattern inquiry. [Antisemitism case](antisemitism-case.md) · [Anti-Muslim bias study](reading-muslim-coverage.md)

## Denominators, uncertainty, and the weighted index

- **Rate** divides positive sources by determinate sources. **Coverage** divides determinate sources by all eligible sources, including incomplete and uncertain sources. The scorecard also displays retained versus planned unit decisions. Record each denominator when citing a rate.
- For a custom rubric covering every source in the collection, select **Apply each custom criterion to every selected source** or set `applicability: "source_scope"` on named criteria. If a source has only inapplicable unit decisions, the scorecard records it as uncertain and keeps it in the eligible denominator. Inspect that source for an overly narrow criterion or a model error.
- Each criterion's **unresolved range** assigns its uncertain and incomplete eligible sources to all negatives, then all positives. If three of four eligible sources have determinate readings and two are positive, the observed rate is 2/3, coverage is 3/4, and the range is 2/4 to 3/4. These are two missing-data scenarios. Sampling uncertainty calls for a suitable sample and a separate analysis.
- A weighted index uses predeclared weights for a specific descriptive question. Each weighted criterion needs at least 80% determinate coverage. The Civic Square example measures public-order justification in four fictional sources. A score of 60.0 follows from the selected signals and weights. [Worked calculations and sensitivity](research-scorecards.md)

## Check whether the method works for a new corpus

Sample positive, negative, and unresolved readings, including full texts and matched pairs. Mix counterexamples, such as explicit endorsement beside quotation or criticism, and reserve a held-out set before tuning. Keep related stories in the same split. Have independent reviewers apply the rubric and record their disagreements before adjudication. Report precision, recall, agreement, and coverage with counts and the evaluation scope. Use the authored Civic Square labels to test the pipeline and understand a discrepancy. Use the held-out review to test interpretation accuracy. [Evaluation protocol](methodology-v06.md) · [Measurement and Fairness](https://arxiv.org/abs/1912.05511)

The saved scorecard supplies the summary, source-level cells, and evidence for every exported format. The JSON-LD projection identifies the run, criterion and representative text for each cell. Use the offline verifier to check both the saved checksum and the graph against the frozen observations. [Graph verification command](evidence-graph.md#scorecard-graph)
