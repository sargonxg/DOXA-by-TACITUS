#!/usr/bin/env bash
set -euo pipefail
# Explicitly downloads Docling model artifacts; never called by ingestion or startup.
destination="${1:-${DOXA_DOCLING_ARTIFACTS:-models/docling}}"
if ! command -v docling-tools >/dev/null 2>&1; then
  printf '%s\n' "Install the OCR extra first: python -m pip install -e '.[ocr]'" >&2
  exit 1
fi
mkdir -p "$destination"
exec docling-tools models download --output-dir "$destination"
