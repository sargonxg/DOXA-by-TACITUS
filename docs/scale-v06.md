# Synthetic scale check: 10,000 TXT sources, zero model assessments

The [recorded receipt](../benchmarks/v06-scale-10000.json) passed all 22 infrastructure checks. The [complete XLSX](../examples/v06-scale-10000.xlsx) and its [manifest](../examples/v06-scale-10000.manifest.json) retain 10,000 generated source records, including exact fixture text in source metadata. These are synthetic inventory entries, not real people, model outputs or bias findings.

Document count alone is not workload size. This fixture contains **1,590,000 UTF-8 input bytes and 200,000 counted words**. It does not replace the separate 5,000-real-source replay, whose long texts and retained assessments create a very different storage/export workload.

## What actually ran

The script creates a new temporary native workspace and applies DOXA's shared TXT parser and canonical `save_document` service to each record. This exercises source ownership, extraction manifests, SQLite commits, FTS and object storage rather than inserting only counter rows.

It then replays every source, checks unchanged IDs/source-block fingerprints, reads all 100 HTTP pagination pages and verifies a unique FTS query. A separately declared **128-document lexical scheduler probe** recovers an expired analysis lease and checks terminal redelivery. Lexical processing is literal matching, not contextual or LLM interpretation; the generated fixture intentionally yields no findings.

A complete-source query is frozen independently of that scheduler subset. Its persistent report job recovers an expired lease, commits a frozen checkpoint, closes and reopens the native Store, and finishes the XLSX. Independent ZIP/XML inspection checks all 10,000 source identities in order, zero assessment rows, no formulas and no external workbook connections. Repeated terminal delivery preserves the same artifacts, and source content remains unchanged after reporting.

Networking is blocked by a Python audit hook, only the no-model lexical provider is admitted, operator/cloud settings are removed from the subprocess environment and dotenv loading is disabled. The successful run recorded zero provider requests, model loads, reservations or blocked network attempts. Torch and Transformers were not imported. No existing research workspace was opened; the temporary workspace was actually removed after verification.

## Observed resources

| Phase or artifact | Recorded measurement |
| --- | --- |
| Persist 10,000 parsed TXT records | 42.03 seconds |
| Replay all 10,000 records | 54.85 seconds |
| HTTP pagination | 1.52 seconds |
| 128-document lexical scheduler probe | 0.51 seconds |
| Persistent capture/checkpoint/reopen/render | 13.33 seconds |
| Total, including verification and cleanup | **118.49 seconds** |
| XLSX | **3,267,994 bytes**, 10,000 source rows, zero model-assessment rows |
| Parent lifetime peak RSS | 101,564,416 bytes |
| Child lifetime peak RSS | 160,342,016 bytes |
| Scratch bytes after persistence / before cleanup | 77,926,727 / 88,382,457 bytes |

The run used Linux/Python 3.12.14 with a 16 GiB cgroup limit and four CPU equivalents. Parent and child high-water RSS are separate measurements, not a simultaneous aggregate peak. Scratch sizes are distinct-inode logical file sizes at phase boundaries, not continuous peak-disk measurements. Timings include verification overhead and concurrent host conditions. These measurements do **not** establish 8 GB laptop qualification, GPU performance, production-cloud capacity, PDF/OCR speed or fresh-inference throughput.

## Reproduce

From an installed development checkout:

```sh
python scripts/benchmark_scale_v06.py \
  --documents 10000 --scheduler-documents 128 \
  --output /tmp/doxa-scale-receipt.json \
  --workbook /tmp/doxa-scale-sources.xlsx
```

The script cannot select an existing application data directory. It generates and removes its own temporary workspace; only the explicitly requested receipt/workbook/manifest remain. A small subprocess regression check exercises the same path with deliberately invalid remote/authentication settings and fake credentials. It is a software test, not independent interpretation validation.
