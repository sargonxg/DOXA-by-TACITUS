# Whose accusation is it? An antisemitism case study

An article about Czech coverage of Israel’s war in Gaza quotes a poster in
Plzeň that accused Jews collectively. The model flagged the reported wording
under IHRA’s hostility or stereotyping criterion. The same scored unit has
compact outcomes of criterion unmet under the Jerusalem Declaration and Nexus
trope criteria. The case invites a reader to explain that divergence with the
source and the three frozen rules in view. [Open the worked case](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/)

[Tagged PDF](../frontend/public/studies/antisemitism/antisemitism-case.pdf) ·
[81-row decision ledger](../frontend/public/studies/antisemitism/decisions.csv) ·
[JSON-LD evidence graph](../frontend/public/studies/antisemitism/evidence.jsonld) ·
[Checksums and archive positions](../examples/antisemitism-case.manifest.json)

![An 8-bit folio, quotation bubble, and three criterion cards connect the evidence path.](../frontend/public/studies/antisemitism/evidence-desk.png)

## Read the evidence in layers

1. **The wording.** Lyapina’s article quotes an accusation of killing prophets
   and a collective curse. For this reading, the textual cue is an accusation
   directed at Jews as a group. The Global Voices extraction establishes what the
   article says. Verify the original poster and translation before making claims
   about the original item. [Plzeň reading](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/#plzen-poster)
2. **The voice.** The article attributes the poster to a shop owner. The journalistic
   frame, the quoted accusation, and the prosecutor’s reported disposition each
   have their own speaker and evidentiary status. Model metadata records the
   stance of the article toward the proposition as neutral. That field concerns
   the proposition in this decision. [Original article](https://globalvoices.org/2023/12/05/is-selective-coverage-of-israels-war-on-gaza-in-the-czech-republic-following-or-creating-anti-semitism/)
3. **The criterion.** The nine-row comparison uses a single scored unit and run.
   The IHRA hostility decision contains a saved rationale. The compact Jerusalem
   Declaration and Nexus trope decisions have no written rationale. A reviewer
   should read each rule, state their interpretation, and investigate the model
   divergence before drawing conclusions about the framework or the classifier.
   [Compare all nine decisions](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/#three-frameworks)

The report says the prosecutor canceled the prosecution. Legal disposition,
original wording, and interpretation are separate questions. Record precisely
which proposition each piece of evidence supports.

## Research red flags

The reader covers nine cues. It asks what would substantiate inherited blame,
collective responsibility for Israel, a conspiracy, presumed dual loyalty,
dehumanization or threats, blood libel, Holocaust denial or distortion, a shift
from a political target to Jews as Jews, and a misassigned speaker. The cue
about blood libel has a specific historical meaning. It refers to a false
ritual-murder allegation. The quoted Plzeň wording contains a different
religious accusation. [Red flags and follow-up questions](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/#red-flags) · [US Holocaust Memorial Museum on blood libel](https://encyclopedia.ushmm.org/content/en/article/blood-libel)

The three frameworks call for context, with different operational criteria
for Israel-related speech. The IHRA working definition uses contextual
examples and expressly addresses criticism similar to that of other countries.
The Jerusalem Declaration includes guidelines on evidence-based political
criticism and the transfer of classic tropes. Nexus distinguishes a Jewish
target from political criticism and says disproportionate attention alone is
insufficient. The published comparison uses DOXA’s **frozen summaries** of those
frameworks. [IHRA](https://holocaustremembrance.com/resources/working-definition-antisemitism) · [Jerusalem Declaration](https://jerusalemdeclaration.org/) · [Nexus](https://nexusproject.us/nexus-resources/the-nexus-document/)

## Three contrast readings

| Reading | Question to answer |
| --- | --- |
| **Australian policy debate** | Who is raising the concern, and what policy is being criticized? The archived Jerusalem Declaration result meets a political-expression boundary. Read that result separately from the hostility indicators. |
| **Holocaust analogy in Ukrainian diplomacy** | Does the text quote a denial, describe an analogy, or report that someone objected to it? Identify what the original speech would need to show before coding denial or distortion. |
| **Remembering Moshe Ha Elion** | What is the function of the word *Holocaust* in this obituary? The reference identifies a survivor. A keyword retrieves the passage while the surrounding language determines the reading. |

[Open the three contrast cases](https://doxa-public-study.giuliocatanzariti.workers.dev/antisemitism/#contrast)

## Selection and reproduction

The four Global Voices articles are licensed English-edition development cases
from distinct story groups, selected after reviewing the archive for teaching
value. The 5,000-article parent analysis is partial. For the selected four
articles, the archive retains **81 of 81 decisions across nine scored units**.
One indicator and one boundary were met. The other outcomes are 75 criterion
unmet and four outside criterion. The set supports inspection of those recorded
readings. Estimating prevalence, comparative framework performance, or accuracy
requires a separate collection and independent human labels.

The reader, ledger, and graph share the original archived run. The graph
distinguishes directly selected evidence from software-attached unit context.
The four notes are AI-authored editorial readings. The graphics illustrate
research steps and use an original pixel palette based on the DOXA Quest.
[Graph vocabulary](evidence-graph.md) · [DOXA Quest](guide.md) ·
[Implementation plan](antisemitism-case-plan.md)

```sh
python scripts/build_antisemitism_case.py
python scripts/query_study_graph.py \
  --graph frontend/public/studies/antisemitism/evidence.jsonld --case plzen-poster
```

Use `--offline-zip /tmp/doxa-antisemitism-case.zip` to package the same reader,
images, PDF, ledger, and graph. Use `--write` to regenerate the artifacts from
the frozen input. PDF pagination depends on the installed render stack and fonts.
[Companion anti-Muslim bias study](reading-muslim-coverage.md)
