# The DOXA study evidence graph

## Scorecard graph

The scorecard export adds one `doxa:SourceCriterion` node for every selected source and criterion. The node records the outcome, retained and planned decisions, and a link to a representative decision when one has exact evidence. Each decision links to its run, criterion, and passage. Passages retain Unicode offsets and caption cue times where available. The graph stores the saved scorecard's checksum. [Example graph](../frontend/public/studies/scorecard/scorecard-evidence.jsonld)

Count `positive` and `negative` source nodes for a criterion to reproduce its denominator. Keep `uncertain`, `incomplete`, and `inapplicable` nodes visible when assessing coverage. For the Civic Square example, this query returns three positives, four determinate sources, and a rate of 0.75.

```sh
python -m doxa.scorecard_graph frontend/public/studies/scorecard/scorecard-evidence.jsonld custom/custom-1
```

To verify the saved checksum, graph nodes and links, and criterion totals, provide the companion frozen JSON file.

```sh
python -m doxa.scorecard_graph frontend/public/studies/scorecard/scorecard-evidence.jsonld --verify frontend/public/studies/scorecard/scorecard.json
```

The verifier works offline. Pair the graph and JSON from the same run or release when checking their provenance. [Example scorecard JSON](../frontend/public/studies/scorecard/scorecard.json)

The published graph connects **60 source versions, 137 scored units, 822 decisions
and six frozen criteria**. The 16,855 decision-to-passage links resolve to 3,064
distinct passages. Six editorial notes lead readers through selected cases.
The standalone HTML, PDF and local reader take their case paths from this graph.
[Download JSON-LD](../frontend/public/studies/muslim-coverage/evidence.jsonld) ·
[Study manifest](../examples/muslim-coverage.manifest.json)

The companion antisemitism case uses the same graph vocabulary for four sources,
nine scored units, and 81 decisions across IHRA, Jerusalem Declaration, and Nexus.
Its published reader resolves the Plzeň excerpt and comparison table directly
from these graph records. [Case graph](../frontend/public/studies/antisemitism/evidence.jsonld) · [Case methods](antisemitism-case.md)

## Follow one case

Run these commands from a clone with Python 3.11 or later.

```sh
python scripts/query_study_graph.py --case school-hijab
python scripts/query_study_graph.py --criterion discrimination_claim
python scripts/query_study_graph.py --summary
```

The first command follows the editorial question to an unequal-treatment
decision, its criterion, the original run and the exact passage in the credited
article. The linked second decision is uncertain about a repeated institutional
practice. Both use the same scored unit. The second command returns every recorded
decision for the criterion, including outcomes summarized as unflagged. [Open the
school case](reading-muslim-coverage.md#review-a-reading)

## Read the vocabulary

| Record | Relationships and fields |
| --- | --- |
| **Study** | Identifies the frozen selection by SHA-256 and records human-label and inference availability. |
| **SourceVersion** | `inStudy` places it in the cohort. Its source URL, credit, license, extracted-content hash and attributed TXT hash travel with the record. |
| **SourceUnit** | `fromSource` identifies the source version that supplied the scored unit. The unit retains its original processing state. |
| **Passage** | `fromSource` points to the same source version. Its block ID, exact quote and zero-based, half-open Unicode code-point offsets identify the slice. |
| **Decision** | `evaluates` a criterion, belongs to a `scoredUnit`, and was `generatedBy` the archival run. `modelPassage` contains detailed references selected by the model. `unitContext` contains references attached by software for a compact decision. |
| **Criterion** | `inFramework` points to the versioned rubric. The original rule, kind and framework snapshot hash are retained. |
| **AnalysisRun** | Identifies the recorded model, provider, run status, configuration hash and archive hash. |
| **EditorialNote** | `commentsOn` the original decision, points to a `selectedPassage`, and may link a `pairedDecision` from the same unit. Authorship and the review question are explicit. |

JSON-LD supplies portable identifiers and typed links. DOXA uses PROV-O’s
`wasGeneratedBy` and `wasDerivedFrom` for run and source lineage. It uses `exact`,
`start` and `end` from the Web Annotation vocabulary. Offsets are relative to
DOXA’s frozen extraction blocks. A position in this text can differ from the
same sentence’s position in the publisher’s current HTML. The export defines its
own passage record and uses these standard terms for parts of that record.
[JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) ·
[PROV-O](https://www.w3.org/TR/prov-o/) ·
[Web Annotation Data Model](https://www.w3.org/TR/annotation-model/)

The graph is named `urn:doxa:graph:muslim-coverage-1:1`. A JSON-LD-capable RDF
store can count the saved outcomes directly.

```sparql
PREFIX doxa: <https://tacitus.me/doxa/terms#>
SELECT ?outcome (COUNT(?decision) AS ?count)
WHERE {
  GRAPH <urn:doxa:graph:muslim-coverage-1:1> {
    ?decision a doxa:Decision ; doxa:outcome ?outcome .
  }
}
GROUP BY ?outcome
```

## Rebuild and verify

```sh
python scripts/build_bias_study.py
```

The builder reconstructs the study from the frozen corpus, archive and rubric.
It verifies every passage slice, deduplicates positions within a source version,
checks typed endpoints and same-source edges, and compares the exported graph and
reader with the reconstructed versions. The manifest binds the published files to
their checksums. New decisions or altered inputs require an explicit new
publication build. [Builder](../scripts/build_bias_study.py) · [Graph code](../scripts/bias_study/graph.py)

The export contains the licensed study cohort. Private projects, provider
credentials and user review records are outside this publication path. A graph
edge records a derivation, method or attributed reading. Establishing that the
interpretation is correct still requires independent review.
