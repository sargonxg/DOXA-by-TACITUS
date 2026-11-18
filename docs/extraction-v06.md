# PDF extraction and structural context in v0.6

The default PDF path uses **pdfminer.six** for selectable text and **pypdf** for document inspection. Their upstream [MIT](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE) and [BSD-3-Clause](https://github.com/py-pdf/pypdf/blob/main/LICENSE) notices are retained in the dependency inventory. This choice removes PyMuPDF from the default application dependency; it does not replace a full dependency or data-rights review.

## Exact text, new versions and visible limits

New PDF imports use extractor `pdfminer-six`, extraction version `0.6.0`, pinned engine versions and an explicit layout policy. Blocks retain the exact Unicode strings returned by the parser, page/paragraph locators and text-box coordinates. Text is not normalized to make a quotation pass. The manifest records source bytes, block hashes, settings and an extraction-version identity.

Existing extracted sources, quotation offsets, assessments and reports are **not re-extracted or rewritten**. An explicit re-extraction creates another source version. Different parser output must not be presented as byte-identical historical text.

Encrypted or damaged PDFs fail with actionable errors. Pages without selectable text remain unavailable. A page with a selectable header and a large raster body is marked for review. Side-by-side text boxes trigger a reading-order warning; the signal is a heuristic, not a determination that a page's columns were reconstructed correctly. Its pairwise probe is bounded to the first 256 boxes while retaining every extracted text block. Page/character limits fail rather than returning a complete-looking truncated source.

Images, diagrams, tables' visual relationships and other non-text information are not interpreted by this text path. Ordinary selectable text still has the declared extracted-text scope; a successful parser is not proof that all source meaning has been captured.

OCR remains explicitly enabled, separately installed and dependent on prefetched local artifacts. It never silently downloads models. Recovered text is added without removing selectable source text, and OCR pages retain recognition/reading-order review warnings.

For a deliberately selected legacy workflow, install the `legacy-pdf` extra and set `DOXA_PDF_EXTRACTOR=pymupdf-legacy`. That selects the original parser; missing legacy dependencies cause an error rather than a fallback. Its licensing arrangement is the operator's responsibility. The default suite and runtime do not require that engine.

PDF publication tools inspect generated files with pypdf through `doxa.pdf_tools`. Rebuilding raster preview images additionally needs `pip install '.[publication]'` for PDFium; viewing the application and producing ordinary PDF reports do not require this preview extra.

## An explicit structural-context policy

The existing neighboring-sentence policy remains the default. New runs can opt into `context_policy: "structure-aware-1.0"`. This changes source-unit and prompt identities and is included in analytical configuration. The same source cannot silently reuse a legacy interpretation as a result under the new policy.

The new policy adds bounded, separately labeled exact-source context:

- Explicit heading metadata and Markdown heading markers preserve the nearest heading hierarchy. HTML heading levels and built-in DOCX Heading1–6 styles are retained during new extraction. PDF typography is not silently promoted to verified heading structure.
- For an imported mailbox, references can select at most two ancestor messages already contained in that supplied source. Their actual headers and an authored passage remain attributed to their original message; they do not become the current sender's speech.
- Visible `>` quotation depth is recorded as a marker observation, not a count of authenticated speakers.
- Structural context has a 1,600-character whole-sentence budget. Omitted sentences, absent parent messages and ancestor-limit exclusions are recorded. The system never fetches external mail or invents missing context.

Every opted-in unit has a `context_manifest`, exact `structural_context` anchors, a fixed policy version and `context_sufficiency: review_required`. A model or reviewer must still decide whether enough context exists for the requested interpretation. The analyzer independently rebuilds this context before accepting prebuilt units, rejecting forged text, policy mismatch or relabeled ancestry. Evidence must still include an exact sentence from the scored unit.

Context shown in the request and retained in assessment provenance is observable input. It is not hidden reasoning and does not certify an explanation or interpretation.

## Research and challenge material

[CheckList](https://aclanthology.org/2020.acl-main.442/) motivates behavioral testing beyond aggregate accuracy. [HateCheck](https://aclanthology.org/2021.acl-long.4/) demonstrates functional failures in hate-speech classifiers; its results are not DOXA performance. [ERASER](https://aclanthology.org/2020.acl-main.408/) motivates evaluating evidence separately from labels.

The [twelve additional context challenges](../benchmarks/v06-context-challenges.json) cover headings, nested quotations, explicit condemnation, negation, hypothetical guidance, missing mail parents, ambiguous referents, source instructions, damaged OCR and competing sections. They are **AI-authored synthetic protocol proposals requiring human rubric review**. They contain no reference labels, no executed model results and no accuracy claim. The existing 120-case pack and original model outputs remain unchanged.

Reproduce the additional pack without network or inference:

```sh
python scripts/build_context_challenges_v06.py --output /tmp/doxa-context-challenges.json
```

Software tests exercise real PDF parsing, Unicode/column/mixed-image fixtures, explicit OCR boundaries, frozen-policy context and forged-context rejection. A comparison with frozen outputs generated by the released v0.5 builder verifies that non-opted source units remain byte-identical. Executing and adjudicating actual model challenges is a separate evaluation step.

## Academic report annex

New 1.1 service briefs carry frozen measurement cards. Guided PDF/HTML reports now show each displayed card's construct, operational rule, context requirements, exclusions, permitted wording and citation identifiers. The bibliography states both what each primary source supports and what it does not establish, with clickable source links. This is methodological grounding, not an interpretation-accuracy certificate.

Human reports display up to 12 cards and 12 unique references and disclose shown/total counts. JSON retains the complete frozen contract. XLSX adds readable **Academic methods** and **Academic references** sheets; fields exceeding Excel's cell limit continue in numbered parts. Long reference URLs use short visible link labels in human reports while retaining their complete targets and structured values. New reports also explain privacy and wording-scan coverage in prose.

Legacy 1.0 briefs remain card-free. Their committed JSON/HTML example bytes and publication-manifest hashes are checked unchanged; no old PDFs, workbooks or snapshots were regenerated for this refinement.
