# MASTER INTEGRATION PLAN — Plugin adoption + all open items, mapped to the real architecture

Created: 2026-08-07
Status: AUTHORITATIVE. Supersedes the sequencing notes in tightening.md Parts 21–26 (their
content remains the evidence base; THIS file is the build order). Read tightening.md Parts
0–26 and GAPS-AND-ISSUES.md before starting — this plan references them by ID and does not
restate every detail.

Repo root: `C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\`
- `fabric-audit-agent-app\` — chat app: `fabric_audit_agent/` package + `agent_server/` + `e2e-chatbot-app-next/`
- `fabric-audit-mcp\` — MCP tool server
- GAPS-AND-ISSUES.md: check `fabric-audit-agent-py\GAPS-AND-ISSUES.md` first; if that folder
  no longer exists, search the repo root for the file (`Filesystem glob GAPS-AND-ISSUES.md`).
  It holds ~43 prioritized items across 14 sections — several are duplicated in this plan by
  design (this plan is self-sufficient), but the file must be updated as items close.

THE STANDING RULE from tightening.md applies to EVERY change in this plan without exception:
before any change — identify every caller/importer/consumer (grep the function name, grep the
import, check every reader of the dict keys or return shape being changed), identify every
OTHER place implementing similar logic, identify every test covering the function AND its
callers. After any change — run the full test suite, manually re-verify each caller still
receives the expected shape, grep the codebase for the same bug pattern elsewhere, and record
what was checked (named callers, named sibling files, result). A green test suite alone is
never sufficient evidence.

---

## ARCHITECTURE MAP — where each piece of new work lives (verified against the actual tree)

Confirmed real paths (all verified to exist on 2026-08-07):

```
fabric-audit-agent-app/
  fabric_audit_agent/
    query/kql_guard.py          ← currently ONLY read-only gate (control cmds, tautology,
                                   host allowlist, escape helpers). Audit rules go here.
    query/firewall.py           ← P4 firewall for agent-authored queries (interacts with guard)
    query/envelope.py, redact.py, windows.py, target_classifier.py, deeplinks.py, mine.py
    adapters/collector_log_analytics.py     ← timeout/retry/validation/error-conflation fixes
    adapters/collector_workspace_monitoring.py  ← error-conflation check
    adapters/collector_rest.py              ← error-conflation check
    adapters/collector_capacity_events.py   ← throttle threshold fields extraction (dead gate)
    adapters/collector_merge.py             ← merge_facts_list fragility fix
    adapters/attribution_rollup.py          ← N7 attributionMode hardcode
    detectors/concentration.py, user_concentration.py, system_item_kinds.py
    investigation/gates.py, throttle.py, diagnose.py
    kb/metric_definitions.py    ← EXISTS — verify wired into __init__ exports (known gap)
    tools.py                    ← MCP tool defs; new tools register here
    verdict.py, sla.py, egress.py, config.py, query_library.json
  agent_server/
    agent.py        ← async prod loop (must stay in sync with loop.py)
    loop.py         ← sync eval twin (dedup, budget nudge, wrap_untrusted)
    system_prompt.py← canonical prompt (post ADR-001); wrap_untrusted lives here
    chart_tool.py   ← render_chart direct tool (validate-and-wrap; no I/O)
    chart_stream.py ← fences chart JSON into answer text
    ticket_tool.py, investigator.py, fabric_direct.py
  e2e-chatbot-app-next/client/src/
    components/elements/chart.tsx           ← recharts renderer; Newell tokens go here
    components/elements/code-block.tsx      ← KQL viewer integration point (U4)
    components/elements/confidence-badge.tsx, scope-indicator.tsx, mcp-tool.tsx
    App.tsx                                 ← routes / and /chat/:id only
fabric-audit-mcp/                           ← tool server; NO agent logic (ADR-001)
```

New modules created by this plan (all inside `fabric_audit_agent/` unless noted):

```
  resolve/__init__.py
  resolve/text_normalize.py     ← shared normalizeForMatching (ONE function, both resolvers)
  resolve/routing_table.py      ← 15-entry table, TABLE_VERSION, LOW-confidence filtering
  resolve/term_resolver.py      ← two-pass matcher + curated ambiguity
  resolve/field_aliases.py      ← ALIAS_MAP (35 entries) + pluralization strip
  resolve/schema_link.py        ← Pass 1c token index (4 guardrails)
  resolve/field_resolver.py     ← 4-pass resolution + disambiguation + AuthoritativeFilter
  resolve/usage_query_builder.py← safe-column builder + provenance + retention warnings
  resolve/catalog.py            ← lazy manifest/search-index/model loader
  resolve/artifact_lookup.py    ← 3-way xlsx inventory lookup
  query/kql_audit_rules.py      ← the ported audit engine (PERF/CORRECT/BEST + custom PBI checks)
  export/__init__.py
  export/html_report.py         ← reverse-engineered html-visualizer (Part E below)
  export/xlsx_report.py         ← reverse-engineered visualizer via openpyxl (Part E below)
  export/html_utils.py          ← esc() + file_timestamp() (from html-utils.ts)
  data/plugin/                  ← extracted zip data: newell-schema.json, catalog/,
                                   ArtifactsMappedtoWorkspace.xlsx, HCMIF0485_IDT_DASHBOARD.xlsx
agent_server/
  export_tool.py                ← direct tools export_html_report / export_xlsx_report
e2e-chatbot-app-next/client/src/components/elements/
  kql-viewer.tsx                ← read-only Monaco/shiki KQL display (editor.ts adaptation)
```

---

## PHASE 0 — Pre-flight (nothing changes yet)

- [x] 0.1 Locate and read `GAPS-AND-ISSUES.md` in full. Build a checklist of its open items;
      cross off items this plan covers; any item it has that this plan lacks gets APPENDED to
      the relevant phase below before Phase 1 starts. (This is the completeness backstop.)
- [x] 0.2 Extract the plugin zip data into `fabric_audit_agent/data/plugin/`:
      `newell-schema.json`, the full `catalog/` directory (manifest.json, search-index.json,
      models/*.json — all 14), `ArtifactsMappedtoWorkspace.xlsx`, `HCMIF0485_IDT_DASHBOARD.xlsx`.
      Source zip: `C:\Users\am08570\Downloads\kql-mcp-server-v5` (folder) or the uploaded zip.
      Do NOT rerun the .cjs build scripts — their source Excel/CSV inputs are NOT in the zip
      (tightening.md 25b). The pre-built outputs are authoritative as-is.
- [x] 0.3 Read these files in full before any edit (they are the blast-radius core):
      `tools.py`, `agent_server/agent.py`, `agent_server/loop.py`, `agent_server/system_prompt.py`,
      `query/kql_guard.py`, `query/firewall.py`, `adapters/collector_log_analytics.py`,
      `adapters/collector_merge.py`, `job.py`, `config.py`, `investigation/gates.py`.
- [x] 0.4 Run the FULL existing test suite once and record the baseline pass count. Every
      later phase re-runs it; any regression against this baseline blocks progress.

---

## PHASE 1 — Collector hardening (tightening.md 26e/f/g/h + 25e + conversation items)

All changes in `adapters/`. Blast radius: `collector_merge.py`, `job.py`'s
`build_collector_from_env`, `tools.py`'s independent collector assembly, tests in `tests/`.

- [x] 1.1 `collector_log_analytics.py`: set explicit query timeout ~55s (26e) with a specific
      timeout error message; confirm LA token scope is `https://api.loganalytics.io/.default`
      NOT the ARM scope (July 22 conversation correction — check the actual code, don't assume).
- [x] 1.2 Add bounded retry (2 retries, 1s/2s backoff, transient-only `429|503|504|throttled`)
      to the LA HTTP layer (26g). Non-transient errors propagate immediately.
- [x] 1.3 Add response-shape validation (26h): before accessing `tables[0].rows/columns`,
      validate the structure; on mismatch raise a specific "unexpected API response shape"
      error, never a bare KeyError.
- [x] 1.4 Error-conflation fix (25e) in ALL THREE collectors (log_analytics, workspace_monitoring,
      rest): only the Kusto-semantic-error regex (`sem0100|semantic error|failed to resolve
      table|entitynotfound|badargument`) may map to "no data"; everything else re-raises.
      Per the STANDING RULE, also grep collector_capacity_events.py, collector_events_la.py,
      collector_activity*.py for the same conflation pattern.
- [x] 1.5 If ANY code converts `ago(Xh/Xm)` to an ISO 8601 timespan: verify floor-days /
      ceil-remaining-hours (26f — the ceil bug made ago(4h) scan a full day). Grep for
      `timespan`, `P1D`, `PT`, `ago(` across adapters/ and query/.
- [x] 1.6 `collector_merge.py`: per-collector try/except so one failing collector yields empty
      facts + a recorded health event instead of killing the whole run (July 22 finding). The
      failure MUST be surfaced in the run's health output (ties into tightening Part 4), never
      only printed.
- [x] 1.7 Reconcile `tools.py` collector assembly vs `job.py`'s `build_collector_from_env` —
      the Playground "0 findings / healthy / peakCuPct: null" bug came from tools.py building
      collectors independently so job.py's LA branch never executed on the App path. Make ONE
      shared builder both call. Blast radius: every tool in tools.py that triggers collection,
      job.py, any env-var docs.
- [x] 1.8 `collector_capacity_events.py`: extract the three throttle threshold fields from the
      events stream so the throttle signal gate is no longer structurally dead (July 29
      finding + GAPS A-items). Remember: the raw threshold fields are constant=1 BOOLEAN flags,
      NOT the CU limit — the limit measure is base×30 (verified formulas, userMemories). Also
      apply the ×100 scaling fix where the raw API returns 0–1 fractions but `throttle.py`
      compares against `>100.0` (GAP A1).
- [x] 1.9 Real-Time Hub Summary events: dedup by 30-second window (best-effort delivery can
      duplicate). Verify whether the collector already dedups; add if absent.

## PHASE 2 — KQL guard upgrade (tightening 24a/24c/24h + 26r + 25a)

New file `query/kql_audit_rules.py`; `query/kql_guard.py` keeps its current read-only gate
untouched (its callers rely on `assert_read_only_kql` semantics — do not change signatures).
Blast radius: `query/firewall.py` (the P4 firewall for agent-authored KQL calls the guard),
`tools.py` run_kql path, `agent_server` dispatch of run_kql, all query/ tests.

- [x] 2.1 Port as named checks: PERF001 (contains→has, WITH the EventText bracket exemption —
      operand containing `[` or `]` is exempt), PERF003-style time-filter check via a custom
      `POWER_BI_TABLES = {"PowerBIDatasetsWorkspace"}` set (25a — it is NOT in the plugin's
      HIGH_VOLUME_TABLES), CORRECT001 (`== null` → isnull()), CORRECT007 (hand-authored
      EventText filter detection). Each check: rule id, severity, message, suggestion,
      corrected form where deterministic.
- [x] 2.2 Severity contract per 26r: error-severity blocks execution; warnings surface but
      never block. Wire this into the firewall path that executes agent-authored KQL.
- [x] 2.3 Retention check: timespan > 60d on PowerBIDatasetsWorkspace → WARNING (not error)
      with the retention text (25a). Constant `WORKSPACE_RETENTION_DAYS = 60` in one place.
- [x] 2.4 Produce a `QueryOutline` dataclass (24h): sourceTable, pipelineSteps, hasTimeFilter,
      hasRowLimit, hasSummarize, hasJoin — returned by the audit entry point so investigation/
      code can stop re-parsing query strings ad hoc.
- [x] 2.5 Port the 8 `kql_query_limits` pre-flight checks (24f + Pass-1 detail): no take/
      summarize=HIGH truncation risk; expected>400k rows=HIGH; no project=MEDIUM; no time
      filter=HIGH timeout; join w/o time filter=HIGH E_RUNAWAY; make-series=LOW; >5 unions=
      MEDIUM; >10 lets=MEDIUM; `set notruncation` w/o take=HIGH. Expose as
      `preflight_limits(kql) -> list[Risk]` used by the collectors and firewall.
- [x] 2.6 Port `parseKustoError` fix-suggestion mapping (E_RUNAWAY_QUERY /
      E_QUERY_RESULT_SET_TOO_LARGE / timeout / 401-403) into the collector error path so users
      see actionable fixes, not raw Kusto errors.

## PHASE 3 — Resolution layer (the big port: routing table, field resolver, catalog)

All new code under `fabric_audit_agent/resolve/`. Nothing existing changes until 3.8.
Data dependency: Phase 0.2 extraction complete.

- [x] 3.1 `text_normalize.py`: ONE `normalize_for_matching()` — lowercase → non-alphanumeric
      runs → single space → trim (25c). Property test: idempotent, matches the TS behavior on
      the documented examples ("Z.Sales" == "z sales" == "Z-SALES").
- [x] 3.2 `routing_table.py`: all 15 entries transcribed from routing-table.ts including
      `catalogModelName`, `connectionPath` (8 entries — 26b), `confidence`, per-variant
      `verified`/`matchMode`/`ambiguousWith`, `TABLE_VERSION="2.1.0"`, `LAST_REVIEWED`.
      LOW entries (CMMS, OEE Monthly Reports) stay in the file but are excluded from the
      match index AND from the known-models message (26a).
- [x] 3.3 `term_resolver.py`: two-pass (exact; then whole-word containment excluding
      matchMode="exact" variants), curated ambiguity first (DTC↔Ecomm), generic collision
      safety net with distinguishable reason text, connectionPath included in resolved result
      (26q). Port the invariant tests from term-resolver.test.ts: no duplicate normalized
      variants per entry, no uncurated cross-entry collisions, every ambiguousWith target real.
- [x] 3.4 `field_aliases.py`: ALIAS_MAP (35 entries per 25c) + trailing-s strip (>3 chars,
      not `ss`).
- [x] 3.5 `schema_link.py`: token index from catalog search-index.json with all 4 guardrails
      (26c): single-token ≥4 chars, multi-token AND-intersection, 500 ceiling, alias variants
      tried first.
- [x] 3.6 `field_resolver.py`: 4 passes (exact → alias → schema-link → containment),
      disambiguation order (model_hint→HIGH, sole-measure→MEDIUM, else ambiguous +
      combinedKqlFilter from FULL candidate list), HR special case (routing entry exists, no
      field schema → no_match with a redirect message to HR enrichment), DTC special case
      (catalog only, not newell-schema.json) (25f). `AuthoritativeFilter` as a Python wrapper
      class — the usage builder REJECTS plain strings (branded-type pattern, runtime-enforced).
- [x] 3.7 `usage_query_builder.py`: SAFE_USAGE_COLUMNS (25d — NO DatasetName), escape `\`
      then `"` in that order, provenance per clause + `format_provenance()` (26t), retention
      warn-never-clamp, compare-periods doubles the window and gates on the doubled span (26i),
      single-window always includes DistinctUsers + LastUsed, title newline-stripping
      (comment-injection guard). `catalog.py`: lazy manifest+index load, per-model memoized,
      degraded-mode on any missing file (never crash startup). `artifact_lookup.py`: 3-way
      lookup, first-encountered dedup with logged conflicts, exactly-one-param guard.
      resolveFieldUsage returns 5 statuses incl. invalid_request (26s).
- [x] 3.8 Register new tools in `tools.py` AND in the agent's direct toolset (whichever path
      agent_server actually serves — check how render_chart was registered in chart_tool.py
      and follow the same dual-registration pattern): `resolve_term`, `resolve_field`,
      `field_usage_query`, `workspace_usage_query`, `field_search`, `field_detail`,
      `artifact_lookup`. Tool descriptions must carry the self-contained guidance (Part 23):
      "you never see, write, edit, or verify the EventText filter yourself", the xmSQL
      never-search rule (24e), and the kql_execute-style display rules where results include
      ExecutingUser.
- [x] 3.9 System prompt additions (`agent_server/system_prompt.py`): tool-sequencing rules
      (resolve term FIRST on informal names; never hand-author EventText filters with the
      wrong-pattern examples; xmSQL rule; identity display rule). Keep prompt single-sourced
      per ADR-001 — do NOT add a copy in the MCP package.
- [x] 3.10 Loop hooks (24b — THREE, not one) in BOTH `agent_server/agent.py` (async) and
      `agent_server/loop.py` (sync twin — they must stay structurally in sync, this is an
      explicit repo invariant): (a) after run_kql-style execution, auto-analysis nudge;
      (b) after execution with ExecutingUser column, identity normalization applied at the
      data layer (the three-point normalizeExecutingUserDisplay pattern — display, export,
      structured rows); (c) after any general KQL generation, PBI-usage redirect check
      (26l's three patterns) that discards the generated query and routes to the builder.
      Hook (c) is the critical one.

## PHASE 4 — Statistics + detector reconciliation (Parts 21 HARDEN + 26 + conversation items)

Blast radius: `forecast.py`, `anomaly.py`, `automation/trend.py`, `investigation/*`,
`detectors/*`, `investigation/gates.py`, `config.py`, digest + tier2 consumers.

- [x] 4.1 Trend discipline: n≥6 minimum, OLS with R², R²<0.3 caveated, ±15% direction bands.
- [x] 4.2 Spike detection: median+4×MAD (MAD=0 → 3×median fallback, value>10 floor), z-band
      severity (≥3σ or ≥100% severe; ≥2σ moderate).
- [x] 4.3 Minimum-volume floor: suppress %-change when prior <10 (26 analysis.ts + period-delta.sh).
- [x] 4.4 Same-hour/day-of-week baseline comparison in anomaly.py (Part 21 ADOPT METHOD).
- [ ] 4.5 Concentration threshold cross-check: our 30%/40% vs plugin's externally-validated
      60% top-1 — after the metric() formula fix lands, re-evaluate and either raise or
      document why ours differs. Route BOTH concentration detectors through the shared
      `concentration_gate()` (FIX 2) and exclude system item kinds (N5/N6 —
      `detectors/system_item_kinds.py` exists; verify both detectors actually use it).
- [x] 4.6 Unify the ≥3 independent 30% threshold definitions (N8) into config.py constants;
      document `DOMINANT_ITEM_SHARE_PCT=40` (N9) where it governs verdict logic.
- [x] 4.7 N7: `attribution_rollup.py` — stop hardcoding `attributionMode="cost"` when the
      underlying number is DurationMs not CpuTimeMs.
- [x] 4.8 Verify `kb/metric_definitions.py` is exported/wired (known dead-KB gap) and add the
      verified formulas as grounding constants: CU% = TimepointCU_s/(base×30); burndown
      recursion Cumulative[T]=Cumulative[T-1]+Add[T-1]+Burndown[T-1] (negative, one-window
      lag); expected_burndown_minutes = Cumulative%/200; threshold fields are boolean flags.
- [x] 4.9 Math-consistency check (B4 in GAPS file): any inline arithmetic in agent responses
      verified against tool numbers before final answer (the 17%-computed-as-0.5% bug).
- [x] 4.10 Burndown auto-trigger: >100% findings automatically invoke the burndown chain.
- [ ] 4.11 SKU mismatch (HIGHEST RISK open item): add a startup/collection cross-check that
      the base CU value used by the agent matches the SKU reported by the capacity API; on
      mismatch, flag loudly in every percentage output rather than silently computing.

## PHASE 5 — Reverse-engineered visuals (the previously-skipped files, now integrated)

Design principle: the plugin wrote local files to ~/Downloads on a desktop. Our app is a
hosted multi-user Databricks app — the SAME capabilities become server-generated downloadable
artifacts + an in-chat viewer. Nothing writes to a user's local disk.

- [x] 5.1 `export/html_utils.py` — port esc() (5-char HTML escape) and file_timestamp().
- [x] 5.2 `export/html_report.py` — reverse-engineered from html-visualizer.ts (722 lines,
      fully read): self-contained Newell-branded HTML string builder. Port EXACTLY: the
      column classifier (datetime+numeric→line ≤5 series; categorical+numeric→vertical bar
      ≤20 uniques else horizontal, ≤4 value cols; else table-only), the brand tokens
      (#288FC2/#01405C/#696158 + 7-color palette, Arial), the layout (navy header
      "…INFORMATION DELIVERY · NEWELL BRANDS", 4px gradient bar, KPI meta cards, ECharts 5.5.1
      CDN chart, sticky-header table capped at 2,000 rows, timestamp footer), ExecutingUser
      normalization on every displayed cell, esc() on ALL interpolated values. Returns HTML
      text — the caller decides where it goes.
- [x] 5.3 `export/xlsx_report.py` — reverse-engineered from visualizer.ts. The TS version
      hand-builds OOXML chart XML via adm-zip ONLY because SheetJS can't embed charts. In
      Python, openpyxl has native chart support — the entire OOXML injection machinery
      collapses to `openpyxl.chart.LineChart/BarChart` + typed cells. Port the CONTRACT, not
      the mechanism: typed cells (dates as real datetimes with a date numFmt — the
      inspect-xlsx.mjs Phase-4 gate checks t='n'+numFmt, which openpyxl produces natively;
      numbers as numbers; headers as strings), auto-filter + frozen header row on the
      QueryResults sheet, chart auto-selection (datetime+numeric→LineChart ≤3 series;
      categorical+numeric→BarChart, ≥3 numeric cols→column variant; else no chart),
      empty-rowset guard (no chart on 0 rows), ExecutingUser normalization in the export
      column, non-fatal chart failure (data table still valid).
- [x] 5.4 `agent_server/export_tool.py` — two direct tools `export_html_report` and
      `export_xlsx_report`, following chart_tool.py's exact pattern (pure validate + handler,
      dual registration, tolerant point/row coercion). Input: columns+rows from a prior tool
      result (NEVER re-execute — 26p: reuse data already in context; put that rule in the
      tool description). Output: writes to a server temp/volume path the app can serve and
      returns a download link + summary. Add the download endpoint to the app server
      (follow however chart_stream/agent.py currently exposes artifacts; if no file-serving
      exists, add a minimal `/api/exports/{id}` route with content-disposition and an
      allowlist of the export directory — no path traversal).
- [ ] 5.5 `kql-viewer.tsx` — editor.ts adapted to our world: our agent IS the query interface,
      so a standalone offline editor makes no sense; what transfers is the read-only KQL
      display with syntax highlighting for U4 ("show me the query"). Implement as a
      code-block.tsx extension or sibling: KQL token highlighting (port editor.ts's Monaco
      KQL tokenizer keyword list into a lightweight highlighter — shiki/prism grammar — do
      NOT pull full Monaco unless the bundle already has it), copy button, used wherever
      generated/retrieved KQL or DAX is shown. Blast radius: response.tsx / message renderers
      that currently show code fences.
- [ ] 5.6 chart.tsx parity check: Newell brand tokens applied to the recharts palette
      (verify against #288FC2/#01405C series colors); confirm our auto-selected chart types
      remain the render_chart contract (line/bar/grouped/stacked/pie/donut) — the plugin's
      html classifier is for EXPORTS, not for the in-chat chart; do not merge the two
      selection systems (26o documents that even the plugin keeps them separate).

## PHASE 6 — Delivery, memory, and platform items from the conversation sweep

- [ ] 6.1 Teams delivery (Phase-10-owned; do NOT rebuild webhook infra removed earlier):
      when Phase 10 lands, the constraints are already decided — Power Automate Workflows
      `logic.azure.com` URLs (webhook.office.com is retired), Adaptive Cards v1.2, 4 req/s
      (batch all findings into ONE card per run), co-owners on flows, `is_proxy` labeling in
      card subtitles. Record these as requirements in Phase 10's task doc; nothing to build now.
- [ ] 6.2 Delta memory tables: verify the four tables (run_history, capacity_reporting,
      audit_findings, concentration_alerts) have NO partitioning, liquid clustering on, and
      explicit 90-day retention set via ALTER TABLE (time-travel trend analysis). Delta-only
      is correct — the Lakebase split was considered and reverted.
- [ ] 6.3 HR enrichment (optional, only if attribution enrichment is wanted this round):
      local-file pattern from `data/plugin/HCMIF0485_IDT_DASHBOARD.xlsx` — dynamic header
      index detection, Email Address required, lowercase both sides of the join (file is
      UPPERCASE), 75% coverage gate before any percentage claims, Function/Sub-Function
      cohort flags <5, at most 5 unmatched named. Graph API (AADSTS65002) and M365 MCP
      (HTTP 406) are confirmed dead paths — never attempt them.
- [ ] 6.4 EXTERNALMEASURE thread: `scripts/extract_measures.py` (MSAL device-code, client id
      a672d62c) stays as-is pending Jiao/Vegasina/Srikanth; when real formulas arrive they go
      into kb/metric_definitions.py. Section 12.9 loose thread ("SKU CU by timepoint
      basecore" two hypotheses) remains OPEN — do not guess; the SKU cross-check in 4.11
      covers the operational risk meanwhile.
- [x] 6.5 N1 reminder: WM eventDepth withholding is DELIBERATE (prevents mock events being
      mislabeled as real perQuery data) — a "fix" that reinstates it reintroduces that bug.
      Leave unless the underlying mock-labeling issue is solved first.
- [x] 6.6 N15: verify whether tool-loop duplication between `agent/tools_anthropic.py` /
      `agent_server/agent.py` / `agent_server/loop.py` still exists beyond the sanctioned
      sync/async twin pair; consolidate anything OUTSIDE that pair.
- [x] 6.7 D4: the dead Node.js reference app — delete after this plan's build completes;
      fix README stale claims (byte-identical-to-Node, test count 246 vs 841) NOW since
      they're documentation lies regardless of the deletion timing.

## PHASE 7 — Final verification loop

- [x] 7.1 Full test suite green vs the Phase-0 baseline; new modules each have tests
      including the ported invariant tests (term resolver table invariants, builder
      provenance completeness, filter-brand rejection of plain strings, guard severity gate).
- [ ] 7.2 Live checks against the deployed app: the five standing questions (capacity health;
      top users — WHO/WHAT/WHY framing, no capacity-% blend; problems today; CU% chart;
      yesterday's throttling) PLUS three new ones: "who used Invoice Quantity last month"
      (exercises resolve→build→execute→provenance), "export that as an Excel report"
      (exercises 26p reuse + xlsx export), "show me a Newell-branded HTML report of that"
      (html export). Verify the exports download and open.
- [ ] 7.3 Update GAPS-AND-ISSUES.md: close every item this plan landed, with the phase ID.
- [ ] 7.4 Re-read tightening.md Parts 0–26 top to bottom one final time; anything unlanded
      and not explicitly deferred gets a written one-line disposition (done / deferred-why /
      superseded-by-what). No silent drops.

---

## COMPLETENESS CROSS-CHECK (why this plan is believed to cover everything)

Sources swept and folded in: tightening.md Parts 0–26 (all items either phased above or
explicitly owned by their original Part numbers, which remain in force for Parts 0–19's
already-scoped work); GAPS-AND-ISSUES.md via the Phase 0.1 backstop; the last 15 conversations
(EXTERNALMEASURE/extract_measures, throttle gate dead, burndown trigger, C2 prompt dedup
(done, ADR-001), math check, METRIC_DEFINITIONS, proxy-caveat placement, Teams/Power-Automate
constraints, 4-table Delta memory rules, LA token scope, merge fragility, tools-vs-job
collector divergence, SP/Tenant.Read.All note, WM↔LA exclusivity, RTH dedup, SKU mismatch,
FUAM (no action — known limitations), Sowmya permissions brief (doc-only, no code),
Obsidian/second-brain (out of scope for the agent codebase)); the plugin via three full
passes (Parts 24–26) plus this session's final re-check which found the remaining unported
items now explicitly placed: the 5-point in-tool display rules (3.8), startup degraded-mode
discipline (3.7 catalog + collectors' non-fatal loaders), request-id tracing (fold into 1.2's
HTTP layer), display caps (MAX_DISPLAY_ROWS analog only matters for exports — 5.2's 2,000-row
table cap), `union withsource=T * | distinct T` discovery fallback (available to Phase 2.6's
error path if table listing is ever needed), the SSL/CA note (local-dev only —
extract_measures.py; Databricks runtime unaffected), and the four visual files reverse-
engineered in Phase 5 instead of skipped.

Known deliberately-NOT-ported: Azure CLI delegated auth (we use SP client-credentials),
single-workspace pin (we go multi-workspace), nl-generator's 16 App-Insights patterns
(different domain — only the structural redirect transfers, Phase 3.10c), kql-debugging
(ADX-only evaluate python()), kql-sdk-integration (external-app embedding), Monaco full
editor as a standalone file (adapted to kql-viewer.tsx instead), MCP prompt/slash-command
registration (our app has no slash-command surface; the commands' CONTENT became system-prompt
rules and tool descriptions in 3.8/3.9).
