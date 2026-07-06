# MCP Harvest Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Absorb every permission-free finding from the MCP/skills research (`research/23-mcp-harvest-inventory.md`, `research/24-knowledge-harvest-sources.md`) plus the live-review capability gaps into the EXISTING MCP — hardened clients, safe KQL construction, result envelopes with caps, token-efficient outputs, real time-window scoping, raw-event access, schema/sample discovery, a firing pattern detector, and query transparency — so the deployed agent can use every feature. This is the pre-Phase-4 upgrade; the raw-query firewall, verified-query library, and knowledge-content harvest remain Phase 4.

**Architecture:** All changes ride the existing seams: `adapters/clients.py` (builders), a new `fabric_audit_agent/query/` package (guarding + envelopes + windows — pure stdlib, later shared with the P4 firewall), `adapters/collector_events_la.py` (KQL construction), `tools.py` (tool handlers + registration), `mcp_server.py` (arg union). Mock paths keep working offline; every new capability is exposed through tool `input_schema` + description so the agent can discover it.

**Tech Stack:** Python ≥3.10 stdlib core; `azure-kusto-data`/`msal`/`requests` stay lazy in `clients.py`; pytest with injected fakes (no live calls).

## Global Constraints
- **Read-only absolute.** New Kusto calls run with `request_readonly` + `request_readonly_hardline`; no mgmt/ingest surface anywhere.
- **camelCase data keys / snake_case identifiers.** Nullish = `x if x is not None else default` — never falsy `or` where 0/"" is valid.
- **stdlib-only core**; prod SDKs lazy-imported only inside `clients.py` builders.
- **Offline tests with injected fakes** — never a live endpoint. Full suite green after every task (`cd fabric-audit-agent-py && python -m pytest -q`; baseline **464 passed, 1 skipped**).
- **MIT attribution**: files adapting `microsoft/fabric-rti-mcp` code carry `Adapted from microsoft/fabric-rti-mcp (MIT)` in the docstring. External code gets line-by-line security review by the implementer before adaptation (treat as untrusted input).
- Mock fixtures label `source: "mock"`; live labels stay accurate (`_has_live_event_source` rules unchanged).

## Explicitly deferred to Phase 4 (do NOT build here)
Raw KQL/SQL firewall tool; pre-execution query-cost estimation (`.show queryplan` — mgmt command, firewall territory); `get_shots`-style verified-query library; knowledge/content harvest (runbooks, capacity-semantics file, BPA rules); hypothesis loop / retry / sanity-gate agent behaviors; OTEL metrics.

---

### Task 1: KQL guard module (escaping + single-statement)
**Files:** Create `fabric_audit_agent/query/__init__.py` (empty), `fabric_audit_agent/query/kql_guard.py`; Test `tests/test_kql_guard.py`
**Interfaces:** Produces `escape_string(value) -> str` (safe for embedding inside a double-quoted KQL string literal: strips `\x00`, escapes `\` and `"`), `escape_entity(name) -> str` (KQL entity-name escaping: wraps in `['...']` bracket form, rejects control chars/newlines via `ValueError`), `first_statement(text) -> str` (returns text up to the first top-level `;` that is not inside a string literal — single-statement enforcement). Adapted from fabric-rti-mcp's `kql_escape_string`/`kql_escape_entity_name`/`_find_first_statement` (MIT — attribute in docstring).

- [ ] **Step 1: failing test**
```python
# tests/test_kql_guard.py
import pytest
from fabric_audit_agent.query.kql_guard import escape_string, escape_entity, first_statement

def test_escape_string_neutralizes_quote_breakout():
    assert escape_string('a"; PowerBIDatasetsWorkspace | take 999 //') == 'a\\"; PowerBIDatasetsWorkspace | take 999 //'
    assert escape_string("back\\slash") == "back\\\\slash"
    assert escape_string("nul\x00byte") == "nulbyte"

def test_escape_entity_brackets_and_rejects_control_chars():
    assert escape_entity("My Table") == "['My Table']"
    assert escape_entity("T'able") == "['T\\'able']"
    with pytest.raises(ValueError):
        escape_entity("bad\nname")

def test_first_statement_cuts_stacked_statements():
    assert first_statement("T | take 5; T2 | take 9") == "T | take 5"
    # a ; inside a string literal must NOT cut
    assert first_statement('T | where x == "a;b" | take 5') == 'T | where x == "a;b" | take 5'

def test_first_statement_passthrough_single():
    assert first_statement("T | take 5") == "T | take 5"
```
- [ ] **Step 2:** run `pytest tests/test_kql_guard.py -v` → FAIL (ModuleNotFound). **Step 3: implement**
```python
# fabric_audit_agent/query/kql_guard.py
"""KQL construction guards: string/entity escaping + single-statement enforcement.
Adapted from microsoft/fabric-rti-mcp (MIT). Pure stdlib; shared later by the P4 firewall."""

def escape_string(value):
    s = str(value).replace("\x00", "")
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return s

def escape_entity(name):
    s = str(name)
    if any(c in s for c in ("\n", "\r", "\t", "\x00")):
        raise ValueError(f"invalid control character in entity name: {s!r}")
    return "['" + s.replace("\\", "\\\\").replace("'", "\\'") + "']"

def first_statement(text):
    in_str = None   # current string delimiter (' or ") or None
    prev = ""
    for i, ch in enumerate(str(text)):
        if in_str:
            if ch == in_str and prev != "\\":
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == ";":
            return text[:i].rstrip()
        prev = ch
    return str(text).rstrip()
```
- [ ] **Step 4:** run → PASS. **Step 5:** `git add … && git commit -m "feat(query): KQL guard — escaping + single-statement (adapted fabric-rti-mcp, MIT)"`

### Task 2: Wire the guard into KQL builders
**Files:** Modify `fabric_audit_agent/adapters/collector_events_la.py` (`_kql`: use `escape_string` for user/item instead of naive `.replace('"','')`; run the final KQL through `first_statement`), `fabric_audit_agent/adapters/collector_log_analytics.py` (`_build_default_kql`: `escape_string` for workspace-filter names). Test: extend `tests/test_collector_events_la.py` + `tests/test_log_analytics_collector.py`.
**Interfaces:** No signature changes — construction hardening only.
- [ ] TDD: new tests — a user value of `a"; drop | take 1` produces `ExecutingUser =~ "a\"; drop | take 1"` (quote escaped, content preserved as data, no statement breakout); a `kql` override containing `; second` is truncated to the first statement; existing tests still green (the old strip-quotes test is REPLACED by escape semantics — update assertions). Commit `fix(collectors): guard KQL interpolation (escape, single-statement)`.

### Task 3: Kusto/LA client hardening (readonly-hardline + timeout + request id)
**Files:** Modify `fabric_audit_agent/adapters/clients.py`; Test `tests/test_clients_kusto.py` (new)
**Interfaces:** `build_kusto_query(cluster_uri, database, tenant_id, client_id, client_secret, *, timeout_seconds=240, action="query", client=None)` — same returned `query(kql) -> list[dict]`. Every execute uses `ClientRequestProperties` with `request_readonly=True`, `request_readonly_hardline=True`, `servertimeout=timedelta(seconds=timeout_seconds)`, and `client_request_id=f"FAA.{action}:{uuid4()}"` (audit-traceable in `.show queries`). `client=None` lazy-builds the real `KustoClient`; tests inject a fake. `build_log_analytics_query(...)` gains header `Prefer: wait={timeout_seconds}` (LA API server-side wait cap; API is read-only by design).
- [ ] **Key test (fake client captures properties):**
```python
# tests/test_clients_kusto.py
from fabric_audit_agent.adapters.clients import build_kusto_query

class _FakeTable:
    columns = []
    rows = []

class _FakeResp:
    primary_results = [_FakeTable()]

class _FakeKusto:
    def __init__(self):
        self.calls = []
    def execute(self, db, kql, properties=None):
        self.calls.append((db, kql, properties))
        return _FakeResp()

def test_kusto_query_sets_readonly_hardline_and_request_id():
    fake = _FakeKusto()
    q = build_kusto_query("https://c", "db", "t", "cid", "sec", client=fake)
    q("T | take 1")
    _, _, props = fake.calls[0]
    opts = props.to_json() if hasattr(props, "to_json") else props  # CRP or dict-shaped fake
    s = str(opts)
    assert "request_readonly" in s and "request_readonly_hardline" in s
    assert "FAA.query:" in s
```
  Implementation note: import `ClientRequestProperties` lazily inside the builder; when `client` is injected (tests) and the azure SDK is absent, fall back to a plain dict `{"Options": {...}, "ClientRequestId": ...}` passed as `properties` — the fake only needs shape, the real client gets a real CRP. Keep the SDK import inside the `client is None` branch so offline tests never import it.
- [ ] Full suite green. Commit `feat(clients): Kusto readonly-hardline + servertimeout + FAA request-id; LA Prefer-wait`.

### Task 4: Result envelope + row caps (every event tool)
**Files:** Create `fabric_audit_agent/query/envelope.py`; Modify `tools.py` handlers (`spike_events`, `user_spike_history`, `capacity_patterns`, and Task-7's `raw_events`); Test `tests/test_envelope.py` + extend `tests/test_mcp_tools.py`.
**Interfaces:** `finish(payload, *, rows_key, cap=None, kql=None) -> dict` — adds `rowCount` (len of `payload[rows_key]`), `truncated` (True when cap applied), `capApplied` (the cap), and `queryKql` (the exact KQL run, when provided — query transparency: every figure traceable to its query). Pure; no clock (determinism).
- [ ] TDD: `finish({"events":[1,2,3]}, rows_key="events", cap=2)` → events len 2, `rowCount=2`, `truncated=True`; without cap → `truncated=False`, no data change; `queryKql` passthrough. Wire into the three existing event handlers (cap from input `topN`/tool default; `queryKql` only on the LIVE path — the mock path sets `queryKql=None`). Update affected `test_mcp_tools.py` assertions. Commit `feat(mcp): result envelopes — rowCount/truncated/queryKql on event tools`.

### Task 5: Token-efficient columnar output option
**Files:** Modify `fabric_audit_agent/query/envelope.py` (+`to_columnar`), `tools.py` (a `format` input on `spike_events` + `raw_events`); Tests alongside.
**Interfaces:** `to_columnar(records) -> {"columns": {name: [values...]}}` (column-major; keys appear once instead of per-row — the fabric-rti-mcp KustoFormatter insight) + `from_columnar` (round-trip, for tests). Tool input `format: "records"|"columnar"` (default `records`).
- [ ] TDD: round-trip equality on a 3-row heterogenous list (missing keys → None); `spike_events {format:"columnar"}` returns `events` as the columnar dict and `rowCount` still correct. Commit `feat(mcp): columnar output option (token-efficient large results)`.

### Task 6: Real time-window scoping (hours / start–end / "right now")
**Files:** Create `fabric_audit_agent/query/windows.py`; Modify `tools.py::_events_or_mock` + the three event handlers + `adapters/collector_events_la.py` (`_kql` accepts a window *clause*), `mcp_server.py` (`_make_with_args` union adds `hours: float = None, start: str = None, end: str = None, format: str = None, order: str = None`); Tests `tests/test_windows.py` + extensions.
**Interfaces:** `resolve_window(days=None, hours=None, start=None, end=None) -> {"clause": str, "label": str}` — precedence: explicit `start`/`end` (ISO-8601, validated) → `| where TimeGenerated between (datetime(<start>) .. datetime(<end>))`; else `hours` → `ago(<n>h)` filter; else `days` (existing) → `ago(<n>d)`; default 30d. ISO strings validated with `datetime.fromisoformat` (Z-tolerant) and re-emitted — never interpolated raw. Also: thread env `FABRIC_EVENT_OPERATIONS` (comma list) through `_events_or_mock` → the collector's `operations` allowlist (exposes the PR-#7 sub-op filter to deployment).
- [ ] TDD: each precedence tier emits the right clause; `start="2026-07-05T12:45:00Z", end="...13:00:00Z"` produces a `between(datetime(2026-07-05T12:45:00+00:00) .. …)` clause; malformed ISO → `ValueError` with a clear message (the tool returns it as an error string, not a crash); `hours=0.25` works ("last 15 min" = the "right now" question); mock path ignores the window but ECHOES `windowLabel` in the envelope so the agent sees what was asked. Update the 3 tool schemas (`hours`/`start`/`end` properties). Commit `feat(mcp): sub-day + absolute time windows on event tools`.

### Task 7: `raw_events` tool (bounded all-instances access)
**Files:** Modify `tools.py` (handler + definition), `mcp_server.py` (already unified by Task 6); Test extends `tests/test_mcp_tools.py`.
**Interfaces:** Tool `raw_events {user?, item?, days?/hours?/start?+end?, topN (default 100, hard cap 1000), order: "recent"|"cost" (default "recent"), format}` → `{events: [...normalized...], rowCount, truncated, queryKql, source, windowLabel}`. NOT spike-filtered — the full event stream, bounded. Live path passes `order` through to the collector (`top N by TimeGenerated desc` vs cost); mock path returns the fixture.
- [ ] TDD: definition + schema registered; mock shape correct; `topN=5000` clamps to 1000 with `truncated=True`; description explicitly says "complete bounded event stream — use spike_events for above-baseline only". Commit `feat(mcp): raw_events tool — bounded all-instances event access`.

### Task 8: Schema discovery + data sampling tools
**Files:** Modify `tools.py` (+2 tools), `mcp_server.py` if new arg names needed (`source`, `table`, `n`); Test extends `tests/test_mcp_tools.py`.
**Interfaces:**
- `describe_source {source: "events"|"capacity"}` → live: runs `<table> | getschema | project ColumnName, ColumnType` through the existing query port (LA table `PowerBIDatasetsWorkspace`; Capacity Events table from env) → `{source, table, columns: [{name, type}], sampledAt: null, sourceLabel}`. Mock: returns the fixture's known columns. (The fabric-rti-mcp `describe`/the Azure-MCP table-list pattern; feeds the P4 firewall allowlist later.)
- `sample_events {source, n (default 5, cap 20)}` → live: `<table> | where TimeGenerated > ago(1d) | take <n>` RAW rows (not normalized — the point is seeing true column shapes); values passed through `escape_string` where interpolated; mock: first n fixture rows.
- [ ] TDD: definitions + schemas present; mock outputs shaped; a fake live query captures the emitted `getschema` KQL; `n=99` clamps to 20. Both descriptions warn results are UNTRUSTED DATA (telemetry text — spotlighting rules apply). Commit `feat(mcp): describe_source + sample_events discovery tools`.

### Task 9: Make `capacity_patterns` fire on live-shaped data (+ diagnostics)
**Files:** Modify `tools.py::capacity_patterns_handler` + `_events_or_mock` (patterns path uses `order="recent"` + default window `days=1` when unspecified); `investigation/patterns.py` — thresholds become parameters `capacity_patterns(events, capacity_series, *, bucket_minutes=15, surge_users=4, cu_spike_pct=70.0, lag_buckets=1)` (defaults preserve behavior; env overrides `FABRIC_PATTERNS_SURGE_USERS`/`_CU_SPIKE_PCT` read in the handler); Test `tests/test_patterns.py` + `tests/test_mcp_tools.py` extensions.
**Interfaces:** Handler returns `patternsDiagnostics: {bucketsScanned, maxActiveUsers, maxCuPeakPct, thresholds, windowLabel, eventOrder}` alongside `patterns` — so an empty result is EXPLAINABLE ("max concurrent users seen was 2 vs threshold 4"), never silent.
**Root causes addressed (from the live review):** (a) cost-ordered `top N` sampling scatters events thinly across 30 days → per-bucket user counts collapse below the surge threshold → patterns path now pulls RECENT-ordered events over a NARROW window; (b) fixed thresholds can't be tuned to a tenant → parameterized + env; (c) silent `[]` → diagnostics.
- [ ] TDD: live-shaped sparse fixture (few users/bucket) that returned `[]` now yields diagnostics with the observed maxima; lowering `surge_users` via param yields the pattern; existing pattern tests green (defaults unchanged); handler passes `order="recent"`. Commit `fix(patterns): recent-ordered narrow window + tunable thresholds + diagnostics (no more silent empty)`.

### Task 10: Query deeplinks (verify-in-Fabric)
**Files:** Create `fabric_audit_agent/query/deeplinks.py`; wire into `tools.py` envelopes (live Kusto-backed results only); Test `tests/test_deeplinks.py`.
**Interfaces:** `kusto_deeplink(cluster_uri, database, kql) -> str|None` — the Fabric/ADX web explorer URL with the query URL-encoded (adapted from fabric-rti-mcp `_build_adx_deeplink`/`_build_fabric_deeplink`; MIT attribution). Returns None for non-Kusto/unknown hosts. Envelope gains `verifyUrl` when the capacity-events cluster URI is configured. (LA has no clean equivalent — `queryKql` from Task 4 covers transparency there.)
- [ ] TDD: known Fabric host → URL contains encoded query + database; unknown host → None; envelope carries `verifyUrl` on the live capacity path, absent on mock. Commit `feat(mcp): verify-in-Fabric deeplinks on Kusto-backed results`.

### Task 11: Honesty labels — units + denominators
**Files:** Modify `tools.py` (`user_activity`, `user_spike_history`, `spike_events` envelopes + descriptions); Test extends `tests/test_mcp_tools.py`.
**Interfaces:** Event/attribution envelopes gain `cuUnit: "cuSeconds (CPU-time proxy; not authoritative capacity CU)"` and `denominator: "monitored user-attributable activity"` (user_activity) — machine-readable versions of the honesty rules, so the agent stops re-deriving them; `user_activity` description explains why its share can differ from `run_audit`'s estimator (different denominators).
- [ ] TDD: fields present + exact strings locked; descriptions updated. Commit `feat(mcp): machine-readable unit/denominator labels`.

### Task 12: Tool-description overhaul + docs + eval + final sweep
**Files:** Modify `tools.py` (descriptions teach: window params incl. "right now"=hours, `format:"columnar"` for big pulls, raw_events vs spike_events, describe/sample for grounding, diagnostics fields, verifyUrl/queryKql); `MCP-AGENT.md` + `CLAUDE.md`/`STATUS.md` (tool list + counts refresh); add ONE eval golden case (`eval/agent_cases.json`): a windowed raw_events question ("what ran between 12:45 and 13:00") that must ground on the tool result; Test: `tests/test_eval_agent.py` + `python -m fabric_audit_agent eval-agent` all-pass.
- [ ] Final verification: full suite green; `eval-investigations` + `eval-agent` green; `git status` clean; every new input property appears in `_make_with_args`' union and every tool's `input_schema`. Commit `docs+eval: MCP harvest upgrade — agent-facing descriptions, golden case, counts`.

---

## Coverage traceability (research → task)
| Finding | Task |
|---|---|
| rti-mcp `_crp` readonly-hardline + request-id + servertimeout | 3 |
| rti-mcp escaping + `_find_first_statement` | 1, 2 |
| rti-mcp KustoFormatter columnar (token efficiency) | 5 |
| rti-mcp describe/sample discovery; Azure-MCP table-list pattern | 8 |
| rti-mcp deeplinks | 10 |
| kusto-mcp result limiting + size metadata | 4, 7 |
| mcp-kql-server schema-grounding | 8 (cache = P4) |
| grafana investigation-tool shape (findings not rows) | 9 (diagnostics), existing tools |
| Live-review: time windows / "right now" | 6 |
| Live-review: all-instances access | 7 |
| Live-review: capacity_patterns silent `[]` | 9 |
| Live-review: estimator drift + units clarity | 11 |
| PR-#7 `operations` sub-op filter exposure | 6 (env passthrough) |
| Deferred: queryplan cost, get_shots, firewall, knowledge content, OTEL | Phase 4 (listed above) |

## Self-Review
1. **Spec coverage:** every permission-free item in research/23 + the live-review gaps maps to a task (table above); deferred items are named, not dropped. ✓
2. **Placeholders:** none — every task carries interfaces + test code or exact assertions. ✓
3. **Type consistency:** `resolve_window` output consumed as a KQL *clause* by `_kql` (Task 6 modifies `_kql` to accept a clause instead of only `ago()` strings — collector tests updated in the same task); envelope fields camelCase everywhere; `format`/`order`/`hours`/`start`/`end` added once to the `_make_with_args` union. ✓
4. **Order:** 1→2→3 are independent foundations; 4→5 build the envelope; 6 before 7 (raw_events needs windows); 8–11 independent; 12 last. ✓
