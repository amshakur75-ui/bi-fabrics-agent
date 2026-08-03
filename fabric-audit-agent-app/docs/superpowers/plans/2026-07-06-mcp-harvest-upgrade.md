# MCP Harvest Upgrade Implementation Plan (v2 — post-audit + post-review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. This v2 folds in three code audits (`research/23` §7–9) + an independent plan review (26 findings). Every corrected item is marked ⟲.

**Goal:** Absorb every permission-free finding from the MCP/skills research (`research/23-mcp-harvest-inventory.md`, `research/24-knowledge-harvest-sources.md`) plus the live-review capability gaps into the EXISTING MCP so the deployed agent can use every feature. The raw-query firewall, verified-query library, and knowledge-content harvest remain **Phase 4**.

**Architecture:** All changes ride existing seams: `adapters/clients.py` (builders), a new `fabric_audit_agent/query/` package (guards + envelopes + windows — pure stdlib, shared later with the P4 firewall), `adapters/collector_events_la.py` / `collector_capacity_events.py` (KQL construction + result meta), `tools.py` (handlers + registration), `mcp_server.py` (arg union). Mock paths keep working offline; every capability is exposed through tool `input_schema` + description so the agent can discover and use it.

**Tech Stack:** Python ≥3.10 stdlib core; `azure-kusto-data`/`msal`/`requests` lazy in `clients.py`; pytest with injected fakes.

## Global Constraints
- **Read-only absolute.** Kusto calls set `request_readonly` + `request_readonly_hardline` (blocked from override); no mgmt/ingest/callout surface. RBAC Viewer role is the real boundary; these are layer 2.
- **Uniform error envelope (NEW, applies to EVERY new/modified handler):** wrap the body in `try/except Exception as e: return {"error": str(e), "errorType": type(e).__name__, "source": <label>}`. Live HTTP/Kusto failures, unset-env (`describe_source` with no capacity cluster), and malformed windows must return an error dict — never raise into `agent/loop.py` (which calls `handler(input)` with no guard). *(review #17)*
- **camelCase data keys / snake_case identifiers.** Nullish = `x if x is not None else default` — never falsy `or` where 0/"" is valid.
- **stdlib-only core**; prod SDKs lazy-imported only inside `clients.py`.
- **Offline tests with injected fakes** — never a live endpoint. Full suite green after every task (`cd fabric-audit-agent-py && python -m pytest -q`; baseline **464 passed, 1 skipped**).
- **MIT attribution**: files adapting `microsoft/fabric-rti-mcp`, `4R9UN/mcp-kql-server`, or `johnib/kusto-mcp` carry `Adapted from <repo> (MIT)` in the docstring; implementer does a line-by-line security read first (untrusted input).

## Explicitly deferred to Phase 4 (do NOT build here) ⟲ expanded (review #7–9)
Raw KQL/SQL firewall tool; **`.show queryplan` cost *estimation*** (mgmt command; the free per-query *actuals* land here in Task 5); **`powerbi-modeling-mcp` DAX validate-without-execute** (firewall DAX arm); **SQLite schema cache + `difflib` "did-you-mean" repair** (mcp-kql-server — the durable cache is firewall territory; Task 8 uses live `getschema`); **UC functions-as-tools** allowlist face; hosted Power BI MCP schema-priming; grafana `find_slow_query_patterns`-style summarized tools; `AzurePricingMCP`/finops ($ verdict, P5); verified-query library (`get_shots`); knowledge-content harvest incl. **BPA-rule-descriptions → system-prompt** and awesome-copilot skill absorption (research/24); hypothesis loop / retry / sanity-gate agent behaviors; OTEL metrics.

---

### Task 1: KQL guard module (escaping + single-statement + read-only validator) ⟲⟲ +Azure-MCP validator
**Files:** Create `fabric_audit_agent/query/__init__.py` (empty), `query/kql_guard.py`; Test `tests/test_kql_guard.py`
**Interfaces:** `escape_string(value)` (double-quoted-literal safe: strip `\x00`, escape `\` then `"`), `escape_entity(name)` (`['...']` bracket form, doubles `'`, rejects control chars via `ValueError`), `first_statement(text)` (text up to the first top-level `;` not inside a string literal). Adapted from fabric-rti-mcp `kql_escape_string`/`kql_escape_entity_name`/`_find_first_statement` (MIT).
⟲⟲ **ALSO** `assert_read_only_kql(kql)` — the Azure-MCP `KqlQueryValidator` gate (research/23 §11), STRONGER than `first_statement`: (a) length ≤ 10_000 → `ValueError`; (b) strip string literals, then reject **control commands** (`.drop .alter .create .delete .set .append .set-or-append .set-or-replace .ingest .purge .execute`) appearing at the **start OR immediately after a `|` or `;`** (catches post-pipe/post-semicolon injection `first_statement` misses); (c) reject boolean tautologies (`or 1==1`, `or true`, `or '1'=='1'`) on the literal-stripped text. Returns the kql unchanged if clean. This is the read-only gate the collectors and (Task 9) command path call before executing built KQL. MIT-attributed to microsoft/mcp.

- [ ] **Step 1: failing test** (note the escaped-backslash-at-boundary case — review #1)
```python
import pytest
from fabric_audit_agent.query.kql_guard import escape_string, escape_entity, first_statement

def test_escape_string_neutralizes_quote_breakout():
    assert escape_string('a"; T | take 999 //') == 'a\\"; T | take 999 //'
    assert escape_string("back\\slash") == "back\\\\slash"
    assert escape_string("nul\x00byte") == "nulbyte"

def test_escape_entity_brackets_and_rejects_control_chars():
    assert escape_entity("My Table") == "['My Table']"
    assert escape_entity("T'able") == "['T\\'able']"
    with pytest.raises(ValueError):
        escape_entity("bad\nname")

def test_first_statement_cuts_stacked_statements():
    assert first_statement("T | take 5; T2 | take 9") == "T | take 5"
    assert first_statement('T | where x == "a;b" | take 5') == 'T | where x == "a;b" | take 5'
    # escaped-backslash at the string boundary must NOT keep us "in string" forever:
    assert first_statement('T | where x == "a\\\\"; T2 | take 9') == 'T | where x == "a\\\\"'
    assert first_statement("T | take 5") == "T | take 5"
```
- [ ] **Step 3: implement** (boolean `escaped` state machine, not a `prev` char)
```python
"""KQL construction guards. Adapted from microsoft/fabric-rti-mcp (MIT). Pure stdlib.
NOTE: handles standard single/double-quoted KQL string literals with backslash escaping;
KQL @"verbatim" strings ("" doubling) are NOT modeled — acceptable because we only guard
KQL we build ourselves, never arbitrary agent-authored KQL (that is the P4 firewall)."""

def escape_string(value):
    s = str(value).replace("\x00", "")
    return s.replace("\\", "\\\\").replace('"', '\\"')

def escape_entity(name):
    s = str(name)
    if any(c in s for c in ("\n", "\r", "\t", "\x00")):
        raise ValueError(f"invalid control character in entity name: {s!r}")
    return "['" + s.replace("\\", "\\\\").replace("'", "\\'") + "']"

def first_statement(text):
    s = str(text)
    in_str = None
    escaped = False
    for i, ch in enumerate(s):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == ";":
            return s[:i].rstrip()
    return s.rstrip()
```
- [ ] Run → PASS. Commit `feat(query): KQL guard — escaping + single-statement (adapted fabric-rti-mcp, MIT)`.

### Task 2: Wire the guard into KQL builders ⟲ guard at the override seam; exempt trusted env
**Files:** Modify `collector_events_la.py`, `collector_log_analytics.py`; extend their tests.
**Interfaces:** In `create_event_collector`/`capacity_series`, the seam is `kql = cfg.get("kql") or _kql(...)`. Use `escape_string` for interpolated `user`/`item` inside `_kql` (replaces naive `.replace('"','')`). Apply `first_statement` **only to `_kql`-BUILT queries, not to a `cfg["kql"]` override** — the deployed `FABRIC_CAPACITY_EVENTS_KQL` is a trusted multi-line/`let` flatten and must pass unmodified (review #6). So: `built = _kql(...); kql = cfg.get("kql") or first_statement(built)`.
- [ ] TDD: `user='a"; drop | take 1'` → `ExecutingUser =~ "a\"; drop | take 1"` (escaped, preserved, no breakout); a `_kql`-built query with an injected `; second` is truncated; a `cfg["kql"]` override containing `let x=…; x | …` is passed through untouched. Replace the old strip-quotes test with escape semantics. Commit `fix(collectors): guard built KQL (escape + single-statement); exempt trusted override`.

### Task 3: Kusto/LA client hardening — exact `_crp` shape ⟲ drop inspect; LA Prefer header
**Files:** Modify `clients.py`; Test `tests/test_clients_kusto.py` (new).
**Interfaces:** `build_kusto_query(cluster_uri, database, tenant_id, client_id, client_secret, *, timeout_seconds=240, action="query", client=None)`. Every execute passes a `ClientRequestProperties` (or, when a fake `client` is injected and the SDK is absent, a shape-compatible dict) setting — the EXACT rti-mcp shape (research/23 §9):
- `set_option("request_readonly", True)`, `set_option("request_readonly_hardline", True)` — never overridable (there is no caller-CRP path here, so no `_BLOCKED_CRP_KEYS` needed yet; it arrives with the P4 firewall).
- `set_option(ClientRequestProperties.request_timeout_option_name, timedelta(seconds=timeout_seconds))` — **timedelta, not int**.
- `client_request_id = f"FAA.{action}:{uuid4()}"`.
- **DROP** rti-mcp's `inspect.currentframe()` destructive-classification entirely — we are always read-only.
`build_log_analytics_query(...)`: extend `EntraHttp` to accept per-request `headers=` and pass `Prefer: wait=<timeout_seconds>` (LA server-side wait cap; API is read-only by design). *(review "LA Prefer" note)*
- [ ] **Key test** (fake client captures properties; assert distinct token — review #11):
```python
class _FakeTable: columns=[]; rows=[]
class _FakeResp: primary_results=[_FakeTable()]
class _FakeKusto:
    def __init__(self): self.calls=[]
    def execute(self, db, kql, properties=None): self.calls.append((db,kql,properties)); return _FakeResp()

def test_kusto_sets_readonly_hardline_timeout_and_request_id():
    from fabric_audit_agent.adapters.clients import build_kusto_query
    fake=_FakeKusto(); build_kusto_query("https://c","db","t","cid","sec",client=fake)("T | take 1")
    _,_,props = fake.calls[0]; s=str(props.to_json() if hasattr(props,"to_json") else props)
    assert '"request_readonly": true' in s.lower() and '"request_readonly_hardline": true' in s.lower()
    assert "FAA.query:" in s
```
  Impl note: lazy-import `ClientRequestProperties` + `timedelta` **inside the `client is None` (real) branch**; when a fake is injected, build a plain dict `{"Options":{"request_readonly":True,"request_readonly_hardline":True,...},"ClientRequestId":...}` passed as `properties=` (fake only needs shape; the real-CRP branch stays untested, matching repo convention).
- [ ] Commit `feat(clients): Kusto readonly-hardline + timedelta servertimeout + FAA request-id; LA Prefer-wait`.

### Task 4: Result envelope + char-budget limiter ⟲ binary-search cap (johnib), applied broadly
**Files:** Create `query/envelope.py`; Modify `tools.py` (`spike_events`, `raw_events`[T7], `user_spike_history`, `list_workspaces`); Test `tests/test_envelope.py` + `test_mcp_tools.py`.
**Interfaces:**
- `finish(payload, *, rows_key, kql=None, extra=None) -> dict` — adds `rowCount` (len of rows), `queryKql` (exact KQL, live only; None on mock), merges `extra` (e.g. `windowLabel`, `queryStats`). No clock.
- `cap_rows(records, *, max_chars=12000, min_rows=1) -> (rows, meta)` — the johnib char-budget limiter: if `json.dumps(records)` ≤ `max_chars`, return unchanged (`{"truncated":False,"rowCount":n}`); else **binary-search** the largest row count whose serialized length ≤ `max_chars`; returns `meta={truncated, rowCount, originalRowCount, responseChars, capMode:"charBudget"}`. Pure stdlib.
- [ ] TDD: `cap_rows` on 100 fat rows @ small budget → fewer rows, `truncated=True`, `originalRowCount=100`; on 3 small rows → unchanged; `finish` merges keys + `queryKql` passthrough. Wire: `spike_events` passes the **unsliced** spike list to `cap_rows` (not the pre-`_top_expensive`-sliced one — review #10) and reports `originalRowCount`; `list_workspaces` capped (items+workspaces — review #18); `user_spike_history` capped (review #19). Commit `feat(mcp): result envelopes + char-budget row limiter on all list tools`.

### Task 5: Columnar output + free per-query cost metadata ⟲ QueryCompletionInformation (johnib)
**Files:** `query/envelope.py` (+`to_columnar`/`from_columnar`), `clients.py` (query() also returns stats), `tools.py` (`format` input); Tests alongore.
**Interfaces:**
- `to_columnar(records) -> {"columns": {name:[...]}}` (keys once, not per row); `from_columnar` round-trip. Tool input `format: "records"|"columnar"` (default records) on `spike_events`/`raw_events`.
- **Cost metadata:** the Kusto `query()` in `clients.py` also parses the `QueryCompletionInformation` secondary table (documented `KustoResponseDataSet` tables — NOT `_rows` hacks) into `{cpuTime, executionTimeMs, extentsScanned}` when present, exposed via a companion `query_with_stats(kql) -> (rows, stats)`; the event/capacity tools surface it as `queryStats` in the envelope. On-mission: "this query cost X CPU / scanned Y extents." No `.show queryplan` needed.
- [ ] TDD: columnar round-trip (missing keys→None); a fake Kusto resp with a `QueryCompletionInformation` table yields `queryStats.cpuTime`; absent → `queryStats=None`. Commit `feat(mcp): columnar output + per-query cost metadata (CPU/extents)`.

### Task 6: Real time-window scoping ⟲ py3.10 Z-normalize, emit `...Z`, flip union defaults, collector meta
**Files:** Create `query/windows.py`; Modify `_events_or_mock` + 3 event handlers + `collector_events_la._kql` (accept a window *clause*) + `collector_capacity_events` (series window) ; `mcp_server.py` (`_make_with_args`); Tests.
**Interfaces:**
- `resolve_window(days=None, hours=None, start=None, end=None) -> {"clause": str, "label": str}`. Precedence: `start`+`end` → validate each with `datetime.fromisoformat(v.replace("Z","+00:00"))` **(py3.10 is NOT Z-tolerant — normalize first, review #4)**, convert to UTC, **emit `| where TimeGenerated between (datetime(<ISO>Z) .. datetime(<ISO>Z))`** (Z suffix, not `+00:00` — review #5); else `hours` → `ago(<n>h)`; else `days` → `ago(<n>d)`; default 30d. Malformed ISO → `ValueError` (handler returns it via the error envelope). `hours=0.25` = "right now"/last-15-min.
- ⟲ **`_make_with_args` (review #2):** flip `days`/`topN` defaults to `None` and add `hours: float=None, start: str=None, end: str=None, format: str=None, order: str=None, surgeUsers: int=None, cuSpikePct: float=None, source: str=None, table: str=None, n: int=None`; forward only non-None so handlers own real defaults (else `raw_events topN=100` / `capacity_patterns days=1` never trigger).
- ⟲ **Collector contract (review #14):** `create_event_collector` return gains a `"kql"` key (the built query); `_events_or_mock(...)` returns **`(events, series, meta)`** where `meta = {eventKql, windowLabel, seriesWindowLabel}` so handlers can populate `queryKql`/`windowLabel`. Mock path sets `eventKql=None` but still echoes the requested `windowLabel`.
- Env `FABRIC_EVENT_OPERATIONS` (comma list) → collector `operations` allowlist (exposes the PR-#7 sub-op filter to deploy).
- [ ] TDD: each precedence tier's clause; `start/end` emits `between (datetime(2026-07-05T12:45:00Z) .. datetime(2026-07-05T13:00:00Z))`; `"...12:45:00Z"` parses on 3.10; malformed → ValueError; `hours=0.25` works; 3 tool schemas gain the props; `_make_with_args` forwards new args; mock echoes `windowLabel`. Commit `feat(mcp): sub-day + absolute time windows; collector kql/window meta; union defaults fixed`.

### Task 7: `raw_events` tool (bounded all-instances) ⟲ push cap server-side
**Files:** `tools.py`, `mcp_server.py` (unified in T6); Test `test_mcp_tools.py`.
**Interfaces:** `raw_events {user?, item?, days?/hours?/start?+end?, topN (default 100, hard cap 1000), order:"recent"|"cost" (default recent), format}` → `finish(..., rows_key="events")` + `windowLabel`, `queryStats`, `source`. NOT spike-filtered. ⟲ push the effective `topN` into `event_cfg["cap"]` so the KQL `top N` bounds server-side (review #21); also run through `cap_rows` as the char backstop.
- [ ] TDD: registered + schema; mock shape; `topN=5000` → clamped 1000, `capApplied`/`truncated` reflect it (review #3: assert `capApplied==1000`, and `truncated` only if char-budget or clamp trims — specify clamp sets `truncated=True`); description says "complete bounded stream — use spike_events for above-baseline". Commit `feat(mcp): raw_events — bounded all-instances access`.

### Task 8: Schema discovery + sampling (+ optional dry-run) ⟲ escape_entity for tables; int-guard; dry-run
**Files:** `tools.py` (+2 tools + a helper), `mcp_server.py` (args `source`,`table`,`n` — unconditional, review #22); Test.
**Interfaces:**
- `describe_source {source:"events"|"capacity"}` → live: for KQL/Kusto (capacity) use the Azure-MCP grounding primitive **`.show table ['<t>'] cslschema`** (research/23 §11); for LA use `<table> | getschema | project ColumnName, ColumnType` (LA has no `.show table`); table name via **`escape_entity`** (review #12). Return `{source, table, columns:[{name,type}], sourceLabel}`. Unset capacity env → error envelope (not KeyError). Mock: fixture columns. ⟲⟲ Kusto host must pass the **cluster-URI allowlist** check (HTTPS + host suffix in the Kusto/Fabric/ADX-monitor set) before any live call — add `assert_kusto_host(uri)` to `query/kql_guard.py` (anti-SSRF, Azure-MCP `ValidateAndNormalizeClusterUri`).
- `sample_events {source, n (default 5, cap 20)}` → `<table> | where TimeGenerated > ago(1d) | take <n>` RAW rows; **`n` cast to `int`, clamped [1,20]** before interpolation (rti-mcp footgun, research/23 §9). Description warns results are UNTRUSTED telemetry (spotlighting applies).
- `dry_run(kql) -> {valid, error}` helper (adapted mcp-kql-server): wrap as `<kql>\n| take 0`, execute; empty success = valid, else the bind error. Exposed as an internal helper used by tools before a heavy live query (not yet an agent tool — full validation UX is P4).
- [ ] TDD: definitions/schemas; mock outputs; a fake live query captures the emitted `getschema` KQL with a bracketed table; `n=99`→20; unset-env → `{"error":...}`. Commit `feat(mcp): describe_source + sample_events (+ take-0 dry-run helper)`.

### Task 9: `capacity_diagnostics` tool — the rti-mcp `.show` goldmine ⟲ NEW (research/23 §9)
**Files:** `tools.py` (+1 tool), `clients.py` (a `command(kql)` read path via the same client, `.show`-prefix-guarded); Test.
**Interfaces:** `capacity_diagnostics {}` → runs a fixed dict of read-only `.show` commands against the Capacity Events cluster, each error-caught per section: `capacity` (`.show capacity | project Resource, Total, Consumed, Remaining`), `cluster` (`.show cluster`), `workloadGroups` (`.show workload_groups`), `diagnostics` (`.show diagnostics`). Returns `{sections:{capacity:[...],cluster:[...],...}, source, errors:{...}}`. `clients.py` gains a guarded command path: reject any command not starting `.show ` (`ValueError`); reuse the readonly-hardline CRP. **Only runs live when the capacity cluster is configured; else `{source:"none", note:...}`.** No interpolation, no injection surface.
- [ ] TDD: mock returns `source:"none"` + note; a fake client returns fixture rows per section and a per-section error is isolated (other sections still return); a non-`.show` command raises. Commit `feat(mcp): capacity_diagnostics — read-only .show capacity/cluster suite`.

### Task 10: `capacity_patterns` live-fix ⟲ (patterns,diagnostics) API + tool-tunable + seriesWindowLabel
**Files:** `investigation/patterns.py` (params + diagnostics return), `tools.py` (handler), `_events_or_mock`; Test `test_patterns.py` + `test_mcp_tools.py`.
**Interfaces:**
- `capacity_patterns(events, capacity_series, *, bucket_minutes=15, surge_users=4, cu_spike_pct=70.0, lag_buckets=1, return_diagnostics=False)` — defaults preserve behavior; when `return_diagnostics=True` returns `(patterns, {bucketsScanned, maxActiveUsers, maxCuPeakPct, thresholds})` so the handler needn't re-bucket (review #15).
- Handler: pulls **recent-ordered** events over a **narrow default window (`days=1` when unspecified)** — root cause of the live `[]` (cost-ordered 30-day sampling scattered events thin); reads `surgeUsers`/`cuSpikePct` from **tool inputs** (review #16) → else env `FABRIC_PATTERNS_SURGE_USERS`/`_CU_SPIKE_PCT` → else defaults; returns `patterns` + `patternsDiagnostics` (incl. `windowLabel`, `seriesWindowLabel` — the CU series may span a different window than the events, review #20). An empty result is now EXPLAINABLE ("max concurrent users 2 vs threshold 4"), never silent.
- [ ] TDD: a sparse live-shaped fixture that returned `[]` now yields diagnostics with observed maxima; a `surgeUsers=2` tool input yields the pattern; existing pattern tests green (defaults unchanged); handler passes `order="recent"`; `seriesWindowLabel` present. Commit `fix(patterns): recent narrow window + tunable thresholds + diagnostics (no silent empty)`.

### Task 11: Verify-in-Fabric deeplinks ⟲ lock percent-encoding
**Files:** Create `query/deeplinks.py`; wire into live Kusto envelopes; Test.
**Interfaces:** `kusto_deeplink(cluster_uri, database, kql) -> str|None` (Fabric/ADX web-explorer URL, query `urllib.parse.quote(kql, safe="")` — review #24; adapted rti-mcp `_build_*_deeplink`, MIT). None for unknown hosts. Envelope gains `verifyUrl` when the capacity cluster URI is configured (LA has no clean equivalent — `queryKql` covers transparency there).
- [ ] TDD: known Fabric host → URL contains the fully percent-encoded multi-line query (`%0A` for newline, `%20`/`%7C` present) + database; unknown host → None; envelope carries `verifyUrl` live, absent on mock. Commit `feat(mcp): verify-in-Fabric deeplinks on Kusto-backed results`.

### Task 12: Honesty labels + log redaction ⟲
**Files:** `tools.py` (`user_activity`/`user_spike_history`/`spike_events` envelopes + descriptions); Create `query/redact.py` (adapted mcp-kql-server `redact_secrets`, 3 regexes) used wherever we log a URL/query; Test.
**Interfaces:** envelopes gain `cuUnit:"cuSeconds (CPU-time proxy; not authoritative capacity CU)"` + (user_activity) `denominator:"monitored user-attributable activity"`; `user_activity` description explains why its share differs from `run_audit`'s estimator (different denominators). `redact_secrets(text)` masks `user:pass@host`, `bearer <tok>`, `key=value` before logging.
- [ ] TDD: fields present + exact strings; `redact_secrets("bearer abc.def")` masks the token. Commit `feat(mcp): machine-readable unit/denominator labels + log redaction`.

### Task 13: Descriptions + docs + eval + final sweep ⟲ correct path, stronger golden case
**Files:** `tools.py` (descriptions teach: `hours`/`start`/`end` incl. "right now"=`hours`; `format:"columnar"` for big pulls; `raw_events` vs `spike_events`; `describe_source`/`sample_events` grounding; `capacity_diagnostics`; `patternsDiagnostics`; `queryStats`/`verifyUrl`/`queryKql`; tunable `surgeUsers`); `mcp_server.py` `build_mcp_server` docstring tool list (review #26); `MCP-AGENT.md` + `CLAUDE.md`/`STATUS.md` (tool list + counts); add ONE golden case to **`fabric_audit_agent/eval/agent_cases.json`** (review #13): a windowed `raw_events` question ("what ran between 12:45 and 13:00") that must ground on the tool result AND whose **trajectory tool input carries `start`/`end`** (review #25 — not just the echoed digits); Test `test_eval_agent.py` + `eval-agent`.
- [ ] Final verification: full suite green; `eval-investigations` + `eval-agent` green; `git status` clean; **every new input property appears in BOTH `_make_with_args`' union AND the tool's `input_schema`**; every new/modified handler returns the error envelope on failure. Commit `docs+eval: MCP harvest upgrade — agent-facing descriptions, golden case, counts`.

---

## Coverage traceability (research → task)
| Finding | Task |
|---|---|
| rti-mcp `_crp` exact (readonly+hardline+blocked+timedelta+request-id); DROP inspect | 3 |
| rti-mcp escaping + `_find_first_statement` (escaped-boundary fix) | 1, 2 |
| rti-mcp `KustoFormatter` columnar | 5 |
| **rti-mcp `kusto_diagnostics` `.show capacity` suite** | **9** |
| rti-mcp `kusto_show_command` guarded `.show` | 9 (command path) |
| rti-mcp describe/sample; sample_size int-guard footgun | 8 |
| rti-mcp deeplinks (percent-encode) | 11 |
| rti-mcp DO-NOT-PORT (kusto_command, ingest, get_shots AOAI egress, interactive-cred, open unknown-services) | Global Constraints + deferral |
| johnib char-budget limiter + partial metadata | 4 |
| **johnib per-query cost (QueryCompletionInformation)** | **5** |
| johnib own-timeouts | 3 |
| mcp-kql-server `| take 0` dry-run; redact_secrets; (SQLite cache+difflib → P4) | 8, 12 (cache deferred) |
| grafana findings-not-rows shape | 9/10 diagnostics |
| Live-review: time windows / "right now" | 6 |
| Live-review: all-instances | 7 |
| Live-review: capacity_patterns silent `[]` | 10 |
| Live-review: estimator drift + units | 12 |
| Live-review: numbers-sanity (error envelope) | Global Constraints + 13 verify |
| PR-#7 `operations` sub-op filter exposure | 6 |
| Deferred: firewall, queryplan estimation, DAX validate, get_shots library, schema-cache, knowledge content, OTEL | Phase 4 (deferral list) |

## Self-Review
1. **Coverage:** every permission-free item in research/23 (incl. §7–9 audits) + live-review gaps maps to a task; deferred items are NAMED (review #7–9 fixed). ✓
2. **Placeholders:** none — critical snippets (first_statement, resolve_window, _crp, cap_rows) are concrete; the rest carry exact interfaces + assertions. ✓
3. **Type consistency:** `resolve_window.clause` consumed by `_kql`; `_events_or_mock` → `(events, series, meta)` everywhere (T6 changes all callers in-task); every new input added ONCE to the `_make_with_args` union AND the per-tool `input_schema`; envelope fields camelCase. ✓
4. **Order:** 1→2→3 independent foundations; 4→5 envelope/limiter/stats; 6 before 7 (raw_events needs windows); 8,9 independent tools; 10 needs 6; 11,12 independent; 13 last. ✓
5. **Read-only integrity:** CLEAN (reviewer-confirmed) — all additions are query-side/`.show`/inert; `request_readonly_hardline` strengthens posture; no mgmt/ingest/callout ported. ✓
