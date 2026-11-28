# Source reading and mitigation checks

The pure browser module `web/src/finding-explanation.ts` connects a recorded
finding to its preserved source text and a bounded review task. It supplies
exact quotations, source locations, source-linked reading cues, unresolved
speaker and target questions, evidence requirements and contextual boundaries.

`explainFinding(finding, report?)` returns `doxa-source-reading-v1`. The source
passage and adjacent text remain unchanged. A valid sentence focus adds a
selected quotation and its recorded causal wording and citation marker.
Invalid offsets fall back to the full preserved source with a limitation.

For model flags, the module states that the class score supplies no learned
evidence span or source-grounded rationale. Source-linked reporting words,
quotation boundaries and scope terms ask questions about the actual wording.
They supply reading cues. Their presence establishes neither the model's
reason nor the criterion's final interpretation. Speaker endorsement, target
scope and evidential support require a reviewer decision.

The questions cover the six deployed language screens, all twelve research
cues, the causal-sentence index and contextual comparison tasks. They retain
supported political criticism, warranted allegations, attributed quotation
and explicit study limitations. Framework-sensitive cases require the named
framework, version and contextual rationale.

`checkMitigation(original, proposed)` returns `doxa-meaning-check-v1` with
`status: review_required`. It compares numbers, name-like capitalized terms,
negation wording, quotations and uncertainty wording. Each check records its
literal values before and after the edit and a question about their scope.
An empty draft receives `not_assessed` checks. A matching extracted term list
receives `no_change_detected`.

These checks direct reviewer attention. A reversal of actor roles can retain
the same names. A moved negation can retain the same token. A revision can
alter a political position through syntax or implication while preserving
all five extracted lists. Review the original and revision together for
responsibility, scope, political meaning, evidence and attribution.

The name detector uses capitalization candidates and requires manual reading
for lowercase names, roles, coreference and many languages. Numeric strings
require their units and denominator context. Quotation and uncertainty rules
can miss nested, implicit or unusual expressions. Model-score changes supply
no independent evidence that a revision preserves meaning.

## Engineering fixtures

`fixtures.json` contains sixteen finding cases and twelve revision cases.
All are fictional, authored for engineering checks and labeled AI-provisional.
Independent human review count is zero. The finding cases test source fidelity,
offset anchoring, recorded cues, attribution questions, invalid focus and
coverage limitations. The revision cases include supported criticism,
quantities, actor changes, negation, quotation, uncertainty, Unicode names,
line wrapping and semantic changes with identical extracted terms.

Run the checked fixture set with Node.js 24 or later.

```sh
node improvement_release/explanations/check.mjs
```

The script writes `evaluation.json`. Its result measures engineering behavior
on authored regression cases. Task accuracy, political neutrality, semantic
faithfulness and the usefulness of the reviewer questions need independent
evaluation on representative source material.

## Reading component integration

`createFindingReading(finding, existingAnnotations?)` in
`web/src/finding-reading.ts` returns `{ element, readAnnotations }`.
Append the element inside the existing finding view and call
`readAnnotations()` when the reviewer saves a decision. An empty editor
returns `undefined`. Entered data uses the exported `FindingAnnotations`
type with `provenance: reviewer_entered`, optional-content speaker, target
and scope strings, and exact supporting or countervailing evidence spans.
This provenance describes entry through the editor. It establishes no
authenticated reviewer identity or independent human validation.

The editor stays collapsed and keeps the full source in a readonly selector.
It records UTF-16 offsets against the preserved source, including CRLF and
Unicode text. Empty selections and boundaries inside surrogate pairs are
rejected. Prior entries must match the current source text and offsets.
The editor identifies invalid prior entries visibly and preserves valid
ones as separate copies. Speaker and target fields start empty and require
actual reviewer input. Source strings and recorded cues render as text nodes.

`renderMitigationReview(original, proposed)` returns a section showing
changed term categories, exact before/after terms and the remaining meaning
questions. Its status stays `review_required`, including unchanged term lists
and an empty draft.

The component exposes local selectors for the application’s existing styles.
Useful selectors are `.finding-reading`, `.reading-questions`, `.reading-cues`,
`.evidence-annotations`, `.reviewer-fields`, `.source-selector`,
`.evidence-actions`, `.recorded-evidence`, `.meaning-review` and
`.meaning-changes`. Input and textarea widths should follow their containing
finding. Action buttons can wrap at small widths. The source selector should
keep readable line spacing and visible keyboard focus.

Run the isolated Chromium checks with the installed web dependencies.

```sh
node improvement_release/explanations/browser-check.mjs
```

The fixture compiles the two modules into a temporary directory and removes
it afterward. Six browser flows check empty entries, unique labels, exact
cues, keyboard selection, Unicode/CRLF offsets, duplicate and invalid spans,
restoration, removal, HTML-text safety and meaning-review status. The fixtures
remain authored engineering checks with zero independent human reviews.
