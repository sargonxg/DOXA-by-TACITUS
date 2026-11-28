# Political consistency diagnostic

The current browser linear model was checked against a frozen set of
original fictional controls. The fixture hash is
`33ada94215897ac2a6c561d5fba4f4990659321f06ad26a325d5627cdbd480bc`.
The protocol and expected readings were recorded before prediction.
Independent human review count is zero.

Twelve template families span legitimate policy criticism, attributed and
rejected prejudice, bounded responsibility, documented coordination,
observational qualification and explicit mechanism controls. Four actor
substitutions create 48 pairs and 96 passages. These substitutions preserve
the supplied conduct, evidence, attribution and scope. They provide 288 paired
checks across six supported criteria and 576 head decisions.

| Criterion | Changed paired decisions / 48 | Hard-negative present flags / checks | Hard-negative unresolved | Positive targets detected / checks |
|---|---:|---:|---:|---:|
| Evaluative framing | 4 / 48 | 0 / 8 | 0 | 0 / 8 |
| Collective blame | 1 / 48 | 0 / 56 | 1 | 8 / 8 |
| Identity stereotyping | 0 / 48 | 8 / 56 | 0 | 8 / 8 |
| Identity derogation or exclusion | 4 / 48 | 9 / 56 | 19 | 8 / 8 |
| Group conspiracy | 1 / 48 | 1 / 56 | 2 | 8 / 8 |
| Evidence overclaim | 0 / 48 | 0 / 24 | 16 | 8 / 8 |

Ten of 288 paired criterion decisions changed after an actor substitution.
Across 256 provisional hard-negative checks, eighteen raised a present flag,
two hundred returned absent and thirty-eight remained unresolved. Eight of
forty-eight positive-target checks returned absent. The per-case file retains
all outputs and expectation mismatches.

These results locate concrete weaknesses in quotation and rejection handling,
scope resolution and the wording head. An invariant answer can still be
wrong. Small score differences can also change an argmax decision near a
boundary. Scores are uncalibrated under this deployed model.

The fixtures are authored engineering diagnostics with dependent templates
and substitutions. Their expectations require future independent review.
Their counts supply no population accuracy, political-neutrality guarantee
or general group-fairness claim. A model release follows the separate
registered benchmark gate. Candidate-model results on these fixtures remain
diagnostic and must preserve the first result and all failures.

Run the current-model diagnostic from the project root with Node.js 24 or
later.

```sh
node improvement_release/evaluation/run-political-consistency.mjs
node --test improvement_release/evaluation/cohorts.test.mjs
```

The generator can reproduce the fixture bytes. After preregistration, the
runner checks the stored SHA-256 and stops if those bytes changed.

## Comparison with the selected wording runtime

The selected news-wording model was run on the same 96 authored controls
after training and checkpoint selection. It uses sentence token context,
while the earlier framing screen also received the supplied adjacent context.
These are development diagnostics shared with the model engineer before
fitting and excluded from training and model qualification.

| Measure | Earlier framing screen | Selected wording runtime |
|---|---:|---:|
| Rendered actor-pair state changes | 4 / 48 | 0 / 48 |
| Forced-binary actor-pair changes | 1 / 48 | 4 / 48 |
| Resolved passage outputs | 55 / 96 | 80 / 96 |
| Unresolved passage outputs | 41 / 96 | 16 / 96 |
| Matches to provisional wording expectations | 8 / 16 | 8 / 16 |

The sixteen wording expectations come from two dependent template families.
The remaining controls specify other mechanisms or pair invariance. A stable
rendered answer can result from abstention, so preserve both raw-binary and
resolution-aware comparisons. These counts supply no independent accuracy
or political-neutrality qualification. Fresh source-based matched tests need
predeclared collection and independent review after the model is fixed.

`political-consistency-comparison.json` retains all pair states and mismatch
records. The candidate's ID-only predictions retain the exact fixture hash.

## Contextual wording runtime comparison

The new wording model was also run on these same 96 development passages.
After its 0.70 uncertainty threshold, it has zero changed rendered states
across 48 actor pairs, resolves 80 of 96 arms and leaves 16 unresolved.
Its forced-binary outputs change on four of the 48 pairs. The legacy framing
head resolves 55 arms, leaves 41 unresolved and changes rendered states on
four pairs, with one forced-binary pair change. Abstention and threshold
behavior affect these apparent differences.

Only two dependent template families supply wording expectations, for a total
of 16 checks. Each model matches eight. These are authored development
expectations. The new wording model was trained on the separate human-majority
benchmark. This small diagnostic supplies no neutrality, general semantic
accuracy or group-fairness qualification. Preserve raw and thresholded
results together in political-consistency-comparison.json.
