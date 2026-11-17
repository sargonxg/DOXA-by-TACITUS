#!/usr/bin/env python3
"""Compatibility CLI for the packaged streaming recorded-run exporter."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doxa.recorded_export import (  # noqa: E402
    AssessmentRows as AssessmentRows,
    LimitedWriter as LimitedWriter,
    MAX_FILE_BYTES as MAX_FILE_BYTES,
    MAX_RAW_BYTES as MAX_RAW_BYTES,
    RECORD_ORDER as RECORD_ORDER,
    RESULTS as RESULTS,
    TERMINAL as TERMINAL,
    ReadOnlyStore as ReadOnlyStore,
    compact as compact,
    export_run as export_run,
    file_hash as file_hash,
    ids_hash as ids_hash,
    main as main,
    prepared_human_data as prepared_human_data,
    recompress_workbook as recompress_workbook,
    recorded_snapshot as recorded_snapshot,
    write_snapshot as write_snapshot,
    write_xlsx as write_xlsx,

)

if __name__ == "__main__":
    raise SystemExit(main())
