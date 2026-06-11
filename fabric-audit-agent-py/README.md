# fabric-audit-agent (Python)

All-Python rebuild of the read-only Microsoft Fabric / Power BI capacity & performance
audit agent, targeting **Databricks** (Python-wheel Job for the sweep + Python MCP server
for the conversational pull surface).

This is a **test-guided port** of the original Node implementation (kept at
`../fabric-audit-agent/` as the reference spec until parity). Every Node test is ported to
`pytest`, so behavior is pinned during the migration.

## Layout
```
fabric_audit_agent/      # the package (pure functional core + adapters)
  config.py              # detection thresholds
  attribution.py         # user-attribution engine (who is driving an item's CU)
  ...                    # detectors/, severity, kb, verdict, importers, diagnosis (porting in progress)
tests/                   # pytest mirror of the Node test suite
```

## Conventions
- **Data dict keys stay camelCase** (e.g. `peakCuPct`, `sharePct`, `topUsers`) to mirror the
  source JSON (`my-estate.json`) and the Microsoft API shapes — keeps the port mechanical and
  JSON round-trips identical.
- **Python identifiers are snake_case** (functions, params, locals).

## Test
```
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]   # Windows  (Linux: .venv/bin/python)
.venv/Scripts/python -m pytest -q
```
or simply `pytest` if it's already on your path. No network or API key needed for the core tests.
