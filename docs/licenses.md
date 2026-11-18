# Source and dependency licenses

The original DOXA application code is distributed under Apache License 2.0; dependencies and source documents retain their own terms. The repository license does not relicense them.

| Component | Terms / reference |
|---|---|
| pdfminer.six (default PDF text) | [MIT](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE); retain notices and record the exact locked version. |
| pypdf (default PDF inspection) | [BSD-3-Clause](https://github.com/py-pdf/pypdf/blob/main/LICENSE); retain notices. |
| PyMuPDF (explicit `legacy-pdf` extra only) | [AGPL or commercial license](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright). Absent from the default v0.6 runtime lock/image. Installing the legacy extra requires compliance with its applicable terms. |
| PDFium previews (optional `publication` extra) | [pypdfium2 license and third-party notices](https://github.com/pypdfium2-team/pypdfium2/blob/main/LICENSE); inventory the chosen binary distribution and its bundled notices separately. |
| Docling (optional) | [MIT code](https://github.com/docling-project/docling); model artifacts have their own licenses. |
| Qwen3.5 model family | [Apache 2.0 model card](https://huggingface.co/Qwen/Qwen3.5-4B). Record the exact downloaded package and revision. |
| Gemma 4 | [Apache 2.0](https://ai.google.dev/gemma/docs/gemma_4_license), unlike earlier Gemma generations. |
| GPT-OSS | [Apache 2.0 model card](https://huggingface.co/openai/gpt-oss-20b). |
| Bundled interface fonts | SIL Open Font License; notices are included alongside the font files. |
| UK source prose | Open Government Licence v3 where specified; attribution, exceptions and source links are in each corpus record. |
| US federal source prose | Public-domain federal-authored material under 17 USC 105; separately credited third-party material retains its rights. |

Images, logos, government seals and linked external publications are not bundled in the text snapshots. Short contextual quotations and citations retain attribution and original rights. The corpus documentation records the scope of the rights review; it does not claim exhaustive legal clearance.

Other Python and JavaScript dependencies retain the license metadata shipped in their distributions and lockfiles. Regenerate dependency and model inventories when upgrading or creating a commercial distribution.

The [v0.6 runtime inventory](runtime-sbom-v06.spdx.json) lists locked default Python dependencies and frontend runtime packages. Regenerate it with `python scripts/build_runtime_sbom.py`. It is a partial SPDX inventory, not a vulnerability scan or legal clearance: `NOASSERTION` entries, operating-system/container libraries, optional extras, model weights and corpus permissions need their own review. Moving the default extractor does not alter historical source text or relicense any earlier package.
