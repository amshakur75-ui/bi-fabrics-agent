# plugin-source/

Raw build-input files for the KQL plugin catalog + schema (kept in git for build
reproducibility per tightening.md 22 Step 4), **outside the Databricks App source path**.

Databricks Apps caps individual source files at **10 MB per file**; the raw
`enriched-field-catalog.json` is 14.2 MB, so shipping it inside
`fabric-audit-agent-app/fabric_audit_agent/data/plugin/` fails the app deploy with
`BAD_REQUEST: File size imported ... exceeded max size (10485760 bytes)`.

## What's here

- `enriched-field-catalog.json` — raw catalog (20,283 records × 13 short-named models).
  Source-of-truth input for the pre-built `catalog/` directory that IS shipped inside
  the app at `fabric-audit-agent-app/fabric_audit_agent/data/plugin/catalog/`.
- `scripts/build-field-catalog.cjs` — Node build script that reads the raw catalog and the
  Dim Catalog CSVs (not in this repo — see tightening.md 25b) to produce
  `data/plugin/catalog/{manifest.json,search-index.json,models/*.json}`.
- `scripts/generate-schema.cjs` — Node build script that reads the "Grant" DAX Queries
  XLSX files (not in this repo) to produce `data/plugin/newell-schema.json`.

## Runtime consumption

The Python resolver reads ONLY the pre-built outputs
(`fabric_audit_agent/data/plugin/catalog/*` + `newell-schema.json`) — see
`fabric_audit_agent/resolve/catalog.py` and `resolve/schema_link.py`. The raw catalog here
is used by nothing at runtime; `SchemaLinkIndex`'s "legacy fallback" branch requires an
explicit `legacy_catalog_path=` argument that no caller currently passes.

## Rebuild (if the Dim Catalog CSVs / Grant XLSX ever land)

1. Place `enriched-field-catalog.json` (or its source CSVs) here.
2. Run `node plugin-source/scripts/build-field-catalog.cjs` (from a machine with the
   original Dim Catalog CSVs at the paths the script expects).
3. The refreshed `data/plugin/catalog/` goes back inside the app source path.
4. Verify `python -m pytest tests/test_catalog.py tests/test_schema_link.py -q` stays green.
