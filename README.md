# Corporate

Private home for agents built to deploy inside the company's own infrastructure
(Microsoft Fabric / Power BI / Azure / Databricks). Each agent is built and tested
standalone — no external build-system dependencies.

## Agents

### `fabric-audit-agent/`
Read-only Microsoft **Fabric / Power BI capacity & performance audit agent**. Detects issues across
7 domains (capacity, semantic models, reports, pipelines, lineage, security, cost), explains root
cause, prioritizes fixes, coaches report authors, and gives an evidence-backed capacity verdict.
Self-contained Node package, 557 tests.

**Quick start**
```
cd fabric-audit-agent
npm install
npm test          # 557 tests, no env required
npm run audit     # sample run on mock data
```

- Overview: `fabric-audit-agent/README.md`
- Permissions + deployment: `fabric-audit-agent/DEPLOYMENT.md`

**Validate on real data (local, nothing leaves the machine):**

_Easiest — feed your actual export(s); no hand-typing:_
```
cd fabric-audit-agent
node import.js "Capacity Metrics export.csv"   # also reads .vpax; pass several files to merge
```
It auto-maps your columns (and prints exactly which column fed which field), writes
`my-estate.json`, then prints the diagnosis. Excel? do **File → Save As → CSV** first.

_Or fill the template by hand:_
```
cp my-estate.example.json my-estate.json   # your private copy
# edit my-estate.json, then:
node mytest.js
```
`my-estate.json` is gitignored, so your real company numbers are **never** pushed —
only the blank `my-estate.example.json` template is tracked.
