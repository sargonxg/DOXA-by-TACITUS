# PDF extraction and local OCR

The parser preserves the uploaded PDF and physical page number. It now records
page-level extraction provenance and positions for the returned text lines.
Coordinates use PDF.js viewport points at scale one, measured from the top left.
Glyph boxes approximate font extents. The original page supplies the layout
and image evidence for a final review.

## Reading order and source locations

Positioned text fragments form geometric rows. Wide synthetic spaces from
PDF.js are treated as potential gutters. A two-column heuristic requires
several overlapping substantive lines on both sides. Full-width lines break
the page into reading bands. The method records the inference and keeps the
source fragment IDs with each line.

Rotated text and complex layouts with at least three detected text columns
retain native content-stream order and carry an explicit issue. Table cues
look for repeated aligned short cells. Small bottom text supplies a possible
footnote cue. These indicators require source inspection and can miss unusual
tables, sidebars and footnote styles.

The first eight readable pages supply repeated-margin candidates. A short
line must recur in the same top or bottom margin on at least three pages and
sixty percent of the preview. Page-number digits normalize for this matching.
Suppressed lines remain empty placeholders in the analyzed line array, and
the quality record preserves their original text, coordinates and line number.
This keeps the remaining line ordinals traceable. Headers that change later
in a document can remain in the scan.

## OCR scope

OCR is opt-in and supports English. It applies to pages with little native
text and an image or drawing content. Tesseract.js runs in a same-origin
browser worker. The worker, WebAssembly core and language data are served as
static assets. The PDF and rendered page image travel through local browser
memory and worker messages.

The parser renders at up to scale two and 3.5 million pixels per page. The
default budget is two hundred attempted OCR pages per file. Later pages keep
their native text or textless status and an OCR-limit issue. A cancel stops
the PDF render and terminates the OCR worker. OCR output is marked as derived
text, with page coordinates and the engine's raw score. The score supplies
no calibrated word-accuracy probability. Low scores and all OCR quotations
need visual verification in the original scan.

The tested packages are Tesseract.js 6.0.1, its installed core 6.1.2 and the
English data package 1.0.0. The compact `4.0.0_best_int` language data recovered
the same controlled scan text as the larger `4.0.0` variant. Its compressed
SHA-256 is `45b4cb346724ac1774f1c36f42f182b887bcdb28ebe63e6fff90ac41f3fcff91`.

## Additive parser API

Existing consumers retain `start`, `page`, `active` and `cancelWith` callbacks.
They can add `quality(PdfPageQuality)` and `status(text)` callbacks and call
`extractPdf(file, consumer, {ocr: true, maxOcrPages: 200})`. Each quality event
precedes the corresponding page callback. The report can keep page summaries
for coverage and retain geometry needed by selected source findings.

`PdfPageQuality` reports source type, native and final character counts,
reading order, detected columns, table and small-text cues, image operators,
excluded margin lines, issues, source line geometry and optional OCR score.
Each geometry record's line number matches the page callback's array index
plus one. A page image with no native text remains distinguishable after OCR
adds text through its native-character and image fields.

## Checks run

```sh
node --test web/tests/pdf-layout.test.mjs
cd web
node scripts/pdf-quality-fixtures.mjs
node scripts/pdf-quality-fixtures.mjs --compact
```

The six layout checks cover two columns, PDF.js's synthetic gutter space,
table rows, repeated margins with line placeholders, small text and complex
three-column fallback. Browser checks use a generated actual image-only PDF,
a generated PDF with interleaved text-stream columns, an exhausted OCR
budget and both published example papers.

The controlled scan's five lines were recovered, including its explicit causal
limitation. Both language variants returned an engine score of 95. Initial
timings were approximately three seconds on the current machine. These
fixtures establish workflow behavior and provide no general OCR accuracy
estimate for historical scans, handwriting, low-resolution images or other
languages.

The actual examples returned all 23 and 18 pages with zero extraction errors.
The measured retained character counts were 69,587 and 66,774, with 91 and
36 repeated margin lines suppressed. A table cue appeared on page 10 of the
news-outlet study. The browser observed 33 requests, all same-origin GETs,
with no source payload sent. `results.json` and `results-compact.json` retain
the exact outputs. `fixtures/scanned.pdf` is an original fictional test image.
