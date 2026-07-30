# Open Gaps Plan — bi-fabrics-audit-agent
# Created: 2026-07-30
# Purpose: Complete, prioritized task list for all open gaps discovered through
#          2026-07-30 live validation. A separate Claude Code session executes this.
#
# Source of truth for gap details: GAPS-AND-ISSUES.md (Sections 1-15)
# Source of truth for in-flight sprint work: tasks/post-phase9-sprint.md
#
# READ BOTH FILES BEFORE TOUCHING ANYTHING.
# This plan references them — it does not duplicate their full detail.

---

## PRIORITY 1 — Critical: breaks or silently corrupts output today

### GAP-1: N26 — Investigate and fix SKU mismatch / stale base_CU
**File:** `fabric_audit_agent/tools.py` → `_resolve_base_cu()`
**Why critical:** If the agent is computing "% of base" against F1024 while the capacity
is actually F512, every single output figure is 2× understated. Concentration alerts,
burndown percentages, throttle verdicts — all wrong by a factor of 2.

**Tasks:**
- [ ] Read `_resolve_base_cu()` in `tools.py` — determine if it fetches live or caches
- [ ] Query the Capacity Overview Events stream for `baseCapacityUnits` on the capacity
      currently configured (check `databricks.yml` for the actual capacity ID/cluster)
- [ ] Compare what the agent reports as base CU vs what the live stream says right now
- [ ] If mismatch confirmed: determine whether it's (A) two different capacities, (B) a
      real resize event, or (C) a stale cached value
- [ ] Fix: `_resolve_base_cu()` must re-fetch from the live stream on every tool call
      (or at minimum on every new conversation/sweep), never use a module-level constant
- [ ] Add a test: if the Capacity Events stream returns a different base_CU than the
      previously used value, the agent uses the new value immediately
- [ ] Document the finding in GAPS-AND-ISSUES.md N26 with the confirmed explanation

---

### GAP-2: N14 — Wire kb/metric_definitions.py into the live pipeline
**Files:** `fabric_audit_agent/kb/__init__.py`, `fabric_audit_agent/kb/metric_definitions.py`,
`fabric_audit_agent/tools.py`
**Why critical:** `METRIC_DEFINITIONS`, `MetricValue`, and all validated formulas exist in the
file but are unreachable. `kb/__init__.py` doesn't export `metric_definitions`. Nothing imports
it. The grounding schema does nothing at runtime.

**Tasks:**
- [ ] Add `metric_definitions` exports to `kb/__init__.py`:
      ```python
      from .metric_definitions import (
          METRIC_DEFINITIONS, MetricValue, get_metric, is_proxy, is_verified,
          DOMINANT_ITEM_SHARE_PCT,
      )
      ```
- [ ] In `tools.py`'s `capacity_peaks_handler` and `capacity_overloads_handler`: wrap CU%
      figures in `MetricValue.from_definition("sku_cu_pct", value, confidence=..., unit="%")`
      before returning in the envelope. This is the first wiring point — it gives every
      capacity-level CU% figure its formula, source, and metric_type automatically.
- [ ] In `tools.py`'s user attribution tools: wrap `sharePct` in
      `MetricValue.from_definition("user_cpu_share_pct", ...)` with `metric_type="proxy_cpu"`.
      This structurally enforces the proxy label on every per-user figure.
- [ ] In `tools.py`'s `render_chart_handler`: use `mv.is_proxy()` and `mv.display_caveat()`
      to set `isProxy` and generate the proxy badge text, rather than hardcoding per-tool logic.
- [ ] Add a test: constructing a `MetricValue` for an unknown metric name raises `KeyError`
      (the "fail loudly" contract from the docstring).
- [ ] Add a test: `MetricValue.is_proxy()` returns True for `proxy_cpu`/`proxy_dur` types,
      False for `true_CU`.

---

### GAP-3: P0 / audit_findings write never called (Phase 6 is permanently a no-op)
**Files:** `fabric_audit_agent/pipeline.py`, `fabric_audit_agent/job.py`
**Why critical:** The `audit_findings` Delta table exists. `context_findings.py` exists.
`query_recent_findings()` exists. But `run_audit()` in `pipeline.py` has no `findings_store`
parameter and `job.py` never constructs one. Phase 6 context injection always returns empty.
Full fix spec: `tasks/post-phase9-sprint.md` Part B, section B1.

**Tasks:**
- [ ] Add `findings_store=None` to `pipeline.py`'s `run_audit()`, call `write()` after
      `store["append"]()`, failure-isolated
- [ ] Add `_default_findings_store(env)` to `job.py`
- [ ] Wire into `run_unified_job()` and `run_csv_job()`
- [ ] New end-to-end wiring test: run `run_audit()` with fake findings_store, assert
      `write()` was called, assert `query_recent_findings()` returns data
- [ ] After fixing: run a real sweep in Databricks and query
      `SELECT COUNT(*) FROM shakur-main.bi-fabrics-audit.audit_findings` — must be > 0

---

## PRIORITY 2 — High: wrong output reaching users

### GAP-4: N24 — Strengthen proxy labeling for user attribution
**Files:** `fabric-audit-agent-app/agent_server/system_prompt.py`,
`fabric_audit_agent/detectors/concentration.py`
**Why high:** Live validation confirmed 23.5× gap between CpuTimeMs and true billed CU for
XMLA Read Operations. The current proxy caveat exists but is not strong enough — it doesn't
tell users what to do about it or how unreliable the ranking can be for specific operation types.

**Tasks:**
- [ ] Add to `system_prompt.py` (find the existing proxy-caveat section, strengthen it):
      ```
      - When reporting per-user attribution: always include this exact phrase in the response
        (not buried in a footnote): "These are CPU-time rankings from monitored telemetry —
        not billed CU. For XMLA or composite model operations the gap can exceed 10×. Use
        the Capacity Metrics app Timepoint Item Detail page to confirm which users are the
        true heaviest consumers before taking any action."
      - Never present a per-user CU-sec figure and a capacity-level CU% figure in the same
        table row without a clear label distinguishing them. They are not the same quantity.
      ```
- [ ] In `concentration.py`: when a concentration alert fires, add a `proxyWarning` field
      to the evidence: `"proxyWarning": "User attribution is based on CpuTimeMs proxy. XMLA
      Read Operations can undercount true CU by 10-25×. Validate in the Metrics app."`
- [ ] New eval case: ask "who's using the most capacity?" — expected response must include
      the proxy validation instruction, not just a caveat footnote

---

### GAP-5: N25 — Fix per-user "% of base" and "Lifetime %" column labeling
**Files:** `fabric_audit_agent/tools.py` (capacity_peaks_handler / capacity_overloads_handler),
`fabric-audit-agent-app/agent_server/system_prompt.py`
**Why high:** "Lifetime % 129.1%" on a per-user operation looks like throttling but means
"this single operation's total lifetime CU cost equals 129% of one 30-second window budget."
Very easy to misread as the user caused 129% utilization.

**Tasks:**
- [ ] In `tools.py`: rename the column headers in the peaks/overloads tool output:
      - `"pctOfBase"` → `"opSizePctOfWindow"` (operation size as % of one 30-sec budget)
      - `"lifetimePct"` → `"lifetimeBudgetPct"` (lifetime cost as % of one window budget)
- [ ] Add to `system_prompt.py` (near the existing % of base rules):
      ```
      - When Lifetime % > 100% on a per-user or per-operation table: always state explicitly
        that this means "the operation's total CU cost exceeded one full 30-second window
        budget over its lifetime" — NOT that the user or operation caused 100%+ capacity
        utilization. These are very different claims. Never present the two (per-user
        lifetime % and capacity CU%) in the same table without making this distinction clear.
      ```
- [ ] New eval case: response with a per-user Lifetime % > 100% must include the above
      clarification inline, not just in end caveats

---

### GAP-6: N27 — Fix chart component not rendering (npm install + rebuild)
**Files:** `fabric-audit-agent-app/e2e-chatbot-app-next/client/`
**Why high:** Phase 8 built the render_chart tool and chart.tsx but the live app can't
render charts. The agent gracefully falls back to text but the feature is effectively absent.

**Tasks:**
- [ ] In the chat app frontend directory: run `npm install` (recharts was added to
      `package.json` but never installed in the deployed environment)
- [ ] Rebuild the frontend bundle: `npm run build`
- [ ] Redeploy the chat app: `databricks apps deploy` for fabric-audit-agent
- [ ] Verify: ask the live agent "show me a chart of CU% over time" — chart must visually
      render, not fall back to text
- [ ] If chart still doesn't render after npm install: read `databricks-message-part-transformers.ts`
      and confirm the chart component is actually registered; check browser console for errors

---

### GAP-7: render_chart not in system prompt
**File:** `fabric-audit-agent-app/agent_server/system_prompt.py`
**Why high:** The render_chart tool is registered and working (per backend logs) but the
agent has no instructions telling it when to use it, what sourceScope/isProxy mean, or the
proxy/true-CU boundary rule for charts. It will use the tool inconsistently.
Full rule text: `tasks/post-phase9-sprint.md` Part C, section C4.

**Tasks:**
- [ ] Add render_chart awareness rules to `system_prompt.py` (full text in post-phase9-sprint.md C4):
      - When to call render_chart vs answer in text
      - sourceScope values and what they mean
      - isProxy rules (true for user/item data from proxy sources)
      - Never blend scopes in one chart
      - Always describe in text first, then call render_chart
- [ ] New eval case: "show me a chart" → expects render_chart called with correct sourceScope
      and isProxy, AND a one-sentence text description before the tool call

---

## PRIORITY 3 — Operational: agent not actually running autonomously

### GAP-8: Both jobs still PAUSED — nothing monitoring autonomously
**File:** `databricks.yml`
**Why operational:** `fabric_audit_sweep` and `fabric_audit_tier2` are both
`pause_status: PAUSED`. No autonomous monitoring is happening. The agent only responds
to manual chat questions.

**Tasks:**
- [ ] Confirm Delta tables are populated (audit_findings has rows after GAP-3 fix)
- [ ] Confirm both jobs pass a manual test run without errors
- [ ] Set `pause_status: RUNNING` for both jobs in `databricks.yml`
- [ ] Deploy: `databricks bundle deploy -t dev`
- [ ] Verify both jobs appear as active/scheduled in the Databricks Jobs console
- [ ] Verify the Tier 2 job runs every 5 minutes (after GAP-9's cadence fix lands)

**Dependencies:** GAP-3 (audit_findings must be wired before unpausing), GAP-9 (cadence)

---

### GAP-9: Cadences wrong — Tier 2 15min, Tier 1 daily, heartbeat 60min
**File:** `databricks.yml`, `fabric_audit_agent/job.py`, `fabric_audit_agent/automation/tier2_check.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part B, section B2.

**Tasks:**
- [ ] `databricks.yml`: Tier 2 cron `"0 */15 * * * ?"` → `"0 */5 * * * ?"`
- [ ] `databricks.yml`: Tier 1 cron `"0 0 6 * * ?"` → `"0 0 * * * ?"` (hourly)
- [ ] `job.py` `_check_tier2_heartbeat()`: staleness threshold 60 → 20 minutes
- [ ] `tier2_check.py` docstring: "every 15 minutes" → "every 5 minutes"
- [ ] Deploy after: `databricks bundle deploy -t dev`

---

### GAP-10: Delivery infrastructure still in codebase (Phase 10 conflict risk)
**Files:** `delivery_teams.py`, `delivery_email.py`, `teams_card.py`, `clients.py`,
`outbound.py`, `job.py`, `tier2_check.py`, `databricks.yml`
Full removal spec: `tasks/post-phase9-sprint.md` Part A (A1–A10).

**Tasks:** Execute Part A of `tasks/post-phase9-sprint.md` in full.
Acceptance check: the grep at the bottom of Part A must return zero hits.

---

## PRIORITY 4 — Code quality: wiring and correctness

### GAP-11: N6/N8 — Item-kind exclusion still partial
**Files:** `fabric_audit_agent/detectors/user_concentration.py`,
`fabric_audit_agent/investigation/diagnose.py`
**Details:** Task 8.1 and 8.2 from `tasks/todo.md` are marked DONE but this was the
"Option B cross-reference" workaround. Confirm by reading the actual files — does
`user_concentration.py` actually exclude EventStream/Activator users? Does `diagnose.py`'s
inline hot-item/hot-user check exclude them? If either is still open, fix it.

**Tasks:**
- [ ] Read `user_concentration.py` directly — confirm EventStream/Activator users are excluded
- [ ] Read `diagnose.py`'s inline concentration check — confirm same exclusion applies
- [ ] If either is still open: implement using the same `system_item_kinds.py` exclusion set
      that `concentration.py` already uses
- [ ] Regression test: a known EventStream-only user does not appear in concentration alerts

---

### GAP-12: P4b — store_delta.py history() full table scan
**File:** `fabric_audit_agent/adapters/store_delta.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part B, section B5.

**Tasks:**
- [ ] Change `.orderBy("run_at").limit(keep)` to DESC order + reverse in Python
- [ ] Existing tests must still pass (return contract: oldest-first, `[-1]` = most recent)
- [ ] New test: 10 rows inserted, `history(keep=5)` returns 5 rows, last element is most recent

---

### GAP-13: P4a — Tier 2 reads static CSV sources every 5 minutes
**File:** `fabric_audit_agent/job.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part B, section B4.

**Tasks:**
- [ ] Add `_build_tier2_collector(env, window="5m")` — live-stream sources only (Capacity Events KQL)
- [ ] Replace `build_collector_from_env()` call in `run_tier2_job()` with the new function
- [ ] Test: env with only CSV configured → `triggered: False`, no crash

---

### GAP-14: P4c — context_findings.py scope injection unsanitized
**File:** `fabric_audit_agent/context_findings.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part B, section B6.

**Tasks:**
- [ ] Single-quote-escape scope and tenant before f-string injection
- [ ] Test: `query(scope="O'Brien's workspace")` — no exception, no SQL injection

---

### GAP-15: P3 — Missing regression tests
**File:** `tests/test_regression_wiring.py` (new file)
Full spec: `tasks/post-phase9-sprint.md` Part B, section B3.

**Tasks:**
- [ ] Test 1: end-to-end Phase 5→6 wiring (run_audit with fake findings_store, assert write called)
- [ ] Test 2: outbound refuses all delivery (only ado_create_ticket registered, disabled)
- [ ] Test 3: Tier 2 returns empty delivered dict

---

## PRIORITY 5 — Enrichment: makes output better

### GAP-16: Investigation quality rules missing from system prompt
**File:** `fabric-audit-agent-app/agent_server/system_prompt.py`
Full rule text: `tasks/post-phase9-sprint.md` Part C, section C4.

**Tasks:**
- [ ] Add recurrence-surfacing rule (recurringRuns + firstSeenAt always stated)
- [ ] Add monthly-baseline comparison rule (when monthlyBaseline available)
- [ ] Add investigation quality rule (4 required elements: cause, recurrence, healthy-vs-problem, fix)
**Note:** render_chart rule covered by GAP-7 above.

---

### GAP-17: C1 — trend.py window too small, no firstSeenAt
**File:** `fabric_audit_agent/automation/trend.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part C, section C1.

**Tasks:**
- [ ] Change `window=7` default to `window=24` (1 day of hourly sweeps)
- [ ] Add `firstSeenAt` field to per-finding annotation
- [ ] Update existing trend tests

---

### GAP-18: C2 — Tier 2 triggers have no healthy-vs-unhealthy framing
**File:** `fabric_audit_agent/automation/tier2_check.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part C, section C2.

**Tasks:**
- [ ] Add `normalityHint` to concentration triggers (≥50%: likely automated; 30-50%: may be legitimate)
- [ ] Add `normalityHint` to throttle trigger (check for refresh window coincidence)
- [ ] Add `normalityHint` to pressure and overage triggers

---

### GAP-19: C3 — No monthly baseline bucketing
**Files:** `fabric_audit_agent/forecast.py`, `fabric_audit_agent/pipeline.py`
Full fix spec: `tasks/post-phase9-sprint.md` Part C, section C3.

**Tasks:**
- [ ] Add `bucket_monthly_summary(history)` to `forecast.py`
- [ ] Call from `pipeline.py`'s `run_audit()` when history spans > 45 days
- [ ] Include result in envelope as `monthlyBaseline`

---

## PRIORITY 6 — Validation: confirm agent is correct

### GAP-20: N26 — base_CU staleness confirmed or cleared
Covered by GAP-1. After the investigation, document the finding either way in GAPS N26.

### GAP-21: Section 14 stress-test bank — never run
**File:** `GAPS-AND-ISSUES.md` Section 14 (20 questions, 7 categories)
**Dependency:** Needs live deployment (GAP-8 jobs running, both apps deployed)

**Tasks:**
- [ ] Run all 20 questions against the live chat app after everything above is deployed
- [ ] Record pass/fail for each in GAPS-AND-ISSUES.md
- [ ] Any failure becomes a new gap entry, not silently dropped

---

### GAP-22: EV2 — mine_evals still blocked on real conversation logs
**File:** `fabric_audit_agent/eval/mine_evals.py`
**Status:** `_conversation_audit_log()` writes to stdout only. No persistent log store exists.

**Tasks:**
- [ ] Once real user traffic exists: export Databricks App logs, pass to `mine_evals.py`
- [ ] Alternatively: add a persistent conversation log writer (Volume or Delta table) so
      `mine_evals.py` has a reliable input source going forward
- [ ] This is low priority until the agent has sustained real usage

---

## PRIORITY 7 — Phase 10 (needs admin action, not code)

### GAP-23: Entra Agent Identity provisioning
**Status:** Not started. Requires org-level action.

**Tasks (not for Claude Code — for the project owner):**
- [ ] Confirm M365 E5 + Microsoft Agent 365 add-on licensing is in place
- [ ] Get Client ID / Tenant ID / Application ID URI from the Entra admin
- [ ] Provide these to Claude Code once confirmed — then Claude Code can wire the sidecar
- [ ] Once identity is provisioned: stand up the Microsoft.Identity.Web.Sidecar at localhost:5000
- [ ] Swap token acquisition code in `job.py` from client-credentials to sidecar pattern
- [ ] Re-grant the new Entra Agent Identity the same Fabric permissions the current SP has

---

## Execution sequence for Claude Code

Execute in this order to minimize rework:

```
1. GAP-1  (N26: investigate SKU mismatch — determines if output is currently trustworthy)
2. GAP-10 (Part A: remove delivery infrastructure — clears the deck)
3. GAP-3  (P0: wire audit_findings — Phase 6 depends on this)
4. GAP-9  (cadences — set correctly before unpausing)
5. GAP-2  (N14: wire metric_definitions — affects all tool output labeling)
6. GAP-4  (N24: strengthen proxy labeling)
7. GAP-5  (N25: fix % of base column labeling)
8. GAP-6  (N27: npm install + rebuild frontend for charts)
9. GAP-7  (render_chart in system prompt)
10. GAP-11 (N6/N8: confirm/fix item-kind exclusion in user_concentration + diagnose)
11. GAP-12 (store_delta scan direction)
12. GAP-13 (Tier 2 live-stream collector)
13. GAP-14 (scope injection)
14. GAP-15 (regression tests)
15. GAP-8  (unpause both jobs — only after 1-14 complete and tested)
16. GAP-16 (system prompt investigation quality rules)
17. GAP-17 (trend.py window + firstSeenAt)
18. GAP-18 (Tier 2 normalityHint)
19. GAP-19 (monthly baseline)
20. GAP-21 (stress-test bank — after full deployment)
21. GAP-22 (mine_evals — low priority, after real usage)
22. GAP-23 (Phase 10 — owner action required, not Claude Code)
```

---

## Files touched (for reference)

**fabric-audit-agent-py:**
- `fabric_audit_agent/kb/__init__.py` (GAP-2)
- `fabric_audit_agent/kb/metric_definitions.py` (GAP-2, no changes needed to file itself)
- `fabric_audit_agent/tools.py` (GAP-1, GAP-2, GAP-5)
- `fabric_audit_agent/pipeline.py` (GAP-3, GAP-19)
- `fabric_audit_agent/job.py` (GAP-3, GAP-9, GAP-10, GAP-13)
- `fabric_audit_agent/adapters/store_delta.py` (GAP-12)
- `fabric_audit_agent/context_findings.py` (GAP-14)
- `fabric_audit_agent/automation/tier2_check.py` (GAP-9, GAP-10, GAP-18)
- `fabric_audit_agent/automation/trend.py` (GAP-17)
- `fabric_audit_agent/forecast.py` (GAP-19)
- `fabric_audit_agent/detectors/user_concentration.py` (GAP-11)
- `fabric_audit_agent/investigation/diagnose.py` (GAP-11)
- `fabric_audit_agent/detectors/concentration.py` (GAP-4)
- `fabric_audit_agent/outbound.py` (GAP-10)
- `fabric_audit_agent/adapters/delivery_teams.py` (GAP-10, DELETE)
- `fabric_audit_agent/adapters/delivery_email.py` (GAP-10, DELETE)
- `fabric_audit_agent/teams_card.py` (GAP-10, DELETE)
- `fabric_audit_agent/adapters/clients.py` (GAP-10)
- `databricks.yml` (GAP-8, GAP-9, GAP-10)
- `tests/test_regression_wiring.py` (GAP-15, NEW)
- `GAPS-AND-ISSUES.md` (update after each gap is resolved)

**fabric-audit-agent-app:**
- `agent_server/system_prompt.py` (GAP-4, GAP-5, GAP-7, GAP-16)
- `e2e-chatbot-app-next/client/` (GAP-6, npm install + rebuild)
