# DOXA 0.7 delivery plan

The release should let a first-time reader open a real study, follow a recorded
decision to the exact passage and frozen criterion, and reproduce that path
offline. A separate local workbench remains available for authorized material.

## First pass · Evidence and reliability

| # | Improvement | Acceptance check |
| --- | --- | --- |
| 1 | Reconcile all original branch heads | All eight heads are ancestors of main. The history-only merge has the same tree as the previous main. |
| 2 | Run the existing suite on the starting tree | Establish the build and test baseline before changing behavior. |
| 3 | Build a small, versioned JSON-LD evidence graph | Export every retained decision, with identifiers stable across rebuilds. |
| 4 | Connect decisions to the archived run and frozen criteria | A reader can recover the model, config hash, framework version and criterion wording. |
| 5 | Keep exact passage identity across repeated references | Deduplicate source spans and verify each half-open Unicode slice against its frozen block. |
| 6 | Distinguish model-selected and software-attached anchors | Expose the difference on both graph edges and reader cards. |
| 7 | Keep editorial questions separate from recorded decisions | Link each of the six walkthroughs and any paired reading without changing the original outcome. |
| 8 | Verify the graph and package its checksum | Reject dangling links or conflicting records and detect changes in the offline build. |
| 9 | Provide a small offline graph query | Trace a case and list the decisions that use a named criterion with standard Python. |
| 10 | Document the graph vocabulary and scope | Explain the PROV-O and Web Annotation terms, offset basis and privacy boundary. |
| 11 | Report the article and decision denominators together | Preserve complete decision coverage and distinguish an unresolved article-criterion pair. |
| 12 | Quantify sensitivity to unresolved rows | Show the observed flags and the maximum if every unresolved, unflagged article were flagged. |
| 13 | Check dependence on any one publication year | Report the leave-one-year-out range with its denominator and scope. |
| 14 | Improve criteria guidance | Make the frozen definition and question available beside the result. |
| 15 | Cross-check the new methods against the frozen study | Recompute diagnostics, compare across formats and cover bounds with a fixture that contains missing decisions. |

## Second pass · Reading, distribution and launch

| # | Improvement | Acceptance check |
| --- | --- | --- |
| 16 | Give GitHub a focused front door | Show the study, installation and graph paths above implementation history. |
| 17 | Preserve the research and operations reference | Move detailed prior material to a linked guide and check relative links. |
| 18 | Make the public HTML trace navigable | Provide deep links to cases and a readable source-to-method path without loading the full export. |
| 19 | Bring the same graph path into the local study reader | Match the public case IDs and show the frozen criterion and anchor origin. |
| 20 | Make article filtering easier to use | Expose filter state, reset and a clear empty result. |
| 21 | Rebuild the PDF as a structured briefing | Use navigable headings, clearer case labels, typed evidence and a compact source register. |
| 22 | Make the workbook serve as a review instrument | Add a coverage and sensitivity sheet with denominators and preserve full original decisions. |
| 23 | Package an offline edition | Include the HTML, graph, PDF, workbook, source ZIP and a reading guide. |
| 24 | Surface missing study assets as missing | Return HTTP 404 instead of the workbench SPA for nonexistent study downloads. |
| 25 | Ship and verify a clean application wheel | Check the analysis API, built UI and published graph from an isolated install. |
| 26 | Harden and verify the public study routes | Test the graph content type, supported methods, unknown paths and upstream failure handling. |
| 27 | Publish a GitHub release with usable assets | Publish the checksummed wheel and offline study against the verified main commit. |
| 28 | Add the small DOXA entry after Windtunnel | Verify immediate section order on tacitus.me and the links to project details and the study. |
| 29 | Update the TACITUS DOXA project page | Present the study, the methods, local setup and the versioned graph from one place. |
| 30 | Verify the published user journey | Check fresh HTTP downloads, desktop and mobile use, the repository and the final branch ancestry. |

The two repositories have separate release gates. DOXA checks include the Python
suite, frontend build, browser workflow, Worker contract, installed wheel and a
rebuild against the frozen study. The homepage check uses the public TACITUS
build and verifies the live routes after deployment. Interpretation validation
requires model-blind human labels and a new collection design for any
population-level claim.
