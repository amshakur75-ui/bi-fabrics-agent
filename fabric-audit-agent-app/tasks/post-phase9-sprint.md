# Post-Phase-9 Sprint: Final Fixes, Cleanup, and Verification
# Created: 2026-07-30
# Status: Ready for autonomous execution (except Phase 10 items)
#
# This file covers everything discovered after Phase 9 completed:
#   - Delivery infrastructure removal (Teams webhook + Email — both gone; Phase 10 owns delivery)
#   - P0-P7 code fixes from docs/decisions/POST-PHASE9-FIX-PLAN.md
#   - Investigation/alert output enrichment
#   - Databricks table verification
#   - End-to-end agent verification
#   - Full deployment
#
# Ground rule: mark [x] only when confirmed by tests passing, code read, or live verification.
# Never mark done from a status line alone.

---

## PART A: REMOVE ALL DELIVERY INFRASTRUCTURE

**Why:** Teams webhook/Adaptive Card approach uses incompatible infrastructure (Power Automate
Workflows / logic.azure.com) with Phase 10's real Entra bot identity. Email was bolted on as an
interim mechanism without proper design. Both are removed; Phase 10 provides real delivery from
scratch with real Entra auth. No delivery happens until then.

### A1: Delete these files entirely
- [ ] `fabric_audit_agent/adapters/delivery_teams.py` — delete
- [ ] `fabric_audit_agent/adapters/delivery_email.py` — delete
- [ ] `fabric_audit_agent/teams_card.py` — delete

### A2: Remove PlainJsonHttp from clients.py
- [ ] Delete the `PlainJsonHttp` class from `fabric_audit_agent/adapters/clients.py`
      (existed solely for Teams webhook HTTP calls — no other user)
- [ ] Update the module docstring to remove PlainJsonHttp references

### A3: Gut outbound.py — keep only the Phase 10 stub
- [ ] Replace `_ALLOWLIST` with exactly one entry:
      ```python
      _ALLOWLIST = {
          "ado_create_ticket": {"enabled": False, "sink": "ticket"},  # Phase 10 (Entra bot identity)
      }
      ```
- [ ] Update the module docstring — remove all Teams/email references
- [ ] Fix stale comment on `ado_create_ticket`: change `# -> Phase 7` to `# Phase 10 (Entra bot identity)`
      (Phase 7 in this project's numbering is NL-to-query, not ADO ticketing)
- [ ] `dispatch_outbound` now correctly refuses ALL calls since the only entry is disabled
      — that is the right behavior until Phase 10

### A4: Clean up job.py — remove all delivery code, keep everything else
- [ ] Delete `_default_delivery(env)` function entirely
- [ ] Simplify `_csv_delivery(env)` to: `return {"deliver": lambda envelope: None}`
      (remove the TEAMS_WEBHOOK_URL branch and all imports inside it)
- [ ] Delete `_build_failure_delivery(env)` function entirely
- [ ] Stub `_alert_failure()` to a no-op:
      ```python
      def _alert_failure(exc, env, now_iso=None):
          """Failure alert stub — delivery wired in Phase 10 (Entra bot identity).
          Called from job_main/tier2_main on sweep failure; currently a no-op."""
          return False
      ```
- [ ] Simplify `_maybe_alert()` — run `decide_alert()` for observability, remove ALL delivery:
      ```python
      def _maybe_alert(envelope, prev_history, env):
          """Alert-on-change decision — delivery wired in Phase 10 (Entra bot identity).
          decide_alert() still runs and its result is returned for observability."""
          try:
              from .automation.alerting import decide_alert
              return decide_alert(envelope, prev_history)
          except Exception:
              return None
      ```
- [ ] Simplify `_tier2_delivery_sinks(env)` to: `return {}` (remove all imports inside it)
- [ ] Remove ALL of these imports from any function inside job.py:
      - `from .adapters.clients import PlainJsonHttp`
      - `from .adapters.delivery_teams import create_teams_delivery`
      - `from .adapters.delivery_email import create_email_delivery`
- [ ] Remove ALL `env.get("TEAMS_WEBHOOK_URL")` checks from job.py
- [ ] Update the module-level docstring: change `delivery -> Teams push (incoming webhook)` to
      `delivery -> no-op stub (Phase 10 will wire real Entra bot delivery)`
- [ ] Add a comment at each stripped site:
      ```python
      # Phase 10: Entra bot identity will provide Teams delivery here.
      # Do not add TEAMS_WEBHOOK_URL or SMTP-based delivery — incompatible with Phase 10.
      ```

### A5: Clean up tier2_check.py
- [ ] Remove the Teams dispatch block from `run_tier2_check()` (the `if delivery_sinks.get("teams"):` block)
- [ ] Remove the email dispatch block from `run_tier2_check()` (the `if delivery_sinks.get("email"):` block)
- [ ] Simplify the delivery section to:
      ```python
      # Delivery: Phase 10 (Entra bot identity) will wire the real channel here.
      delivered = {}
      ```
- [ ] Update `run_tier2_check()` docstring: `delivery_sinks` is now reserved/unused —
      document as "reserved for Phase 10; pass None for now"
- [ ] Remove `dispatch_outbound` import from the triggered delivery section

### A6: Update delivery_file.py docstring
- [ ] Remove "real delivery (`delivery_teams` / `ticketing`)" from the docstring
- [ ] Replace with: "real delivery will be via Phase 10's Entra bot identity adapter"

### A7: Clean up databricks.yml
- [ ] Remove ALL `# TEAMS_WEBHOOK_URL: ...` commented-out lines from both job definitions
- [ ] Remove ALL `# SMTP_*` commented-out lines from both job definitions
- [ ] Add a single comment to each job's named_parameters section:
      ```yaml
      # Delivery (Teams): Phase 10 (Entra Agent Identity) — no webhook URL needed here.
      ```

### A8: Update create_delta_tables.sql
- [ ] Update the `delivery_channel` column comment in `concentration_alerts`:
      Change `'Channel used: teams/email/none'` to `'Channel used: none until Phase 10 (Entra bot identity)'`

### A9: Update POST-PHASE9-FIX-PLAN.md
- [ ] Mark P1a and P1b as superseded:
      ```
      ### P1a and P1b: SUPERSEDED (2026-07-30) — delivery infrastructure deleted
      build_teams_card() and build_watch_adaptive_card() were deleted entirely because the
      incoming-webhook Adaptive Card approach is incompatible with Phase 10's real Entra bot
      identity. The size-up suppression and Adaptive Card version issues are moot — there is
      no card to fix. Phase 10 provides all delivery from scratch.
      ```

### A10: Update any tests that reference deleted delivery infrastructure
- [ ] `grep -r "delivery_teams\|delivery_email\|teams_card\|build_teams_card\|create_teams_delivery\|create_watch_delivery\|create_email_delivery\|teams_notify\|email_notify\|TEAMS_WEBHOOK" tests/`
- [ ] Delete tests that ONLY test the deleted infrastructure
- [ ] Update tests that test adjacent behavior (e.g. outbound allowlist) to reflect the new
      single-entry allowlist

### A10 acceptance check (run this grep, must return zero hits):
- [ ] `grep -r "delivery_teams\|delivery_email\|teams_card\|build_teams_card\|create_teams_delivery\|create_watch_delivery\|create_email_delivery\|teams_notify\|email_notify\|TEAMS_WEBHOOK_URL\|PlainJsonHttp\|SMTP_HOST\|SMTP_TO" fabric_audit_agent/`
      returns zero hits

---

## PART B: CODE FIXES (P0, P2–P7 from POST-PHASE9-FIX-PLAN.md)

### B1 (P0): Wire audit_findings into the sweep pipeline — CRITICAL

Phase 6 context injection is permanently non-functional until this lands. The `audit_findings`
Delta table exists, `create_findings_store_delta()` is built, but nothing ever calls `write()`.

**pipeline.py:**
- [ ] Add optional `findings_store=None` parameter to `run_audit()`
- [ ] After the existing `store["append"](...)` call, add (failure-isolated):
      ```python
      if findings_store is not None:
          try:
              write_fn = (findings_store or {}).get("write")
              if write_fn:
                  # Pass the coached/processed findings list so what's stored matches
                  # what the user sees, not the raw detector output
                  write_fn(run_at, resolved_tenant, coached)
          except Exception:
              pass  # findings store write never blocks the sweep
      ```

**job.py:**
- [ ] Add `_default_findings_store(env)` function:
      ```python
      def _default_findings_store(env):
          """Construct the audit_findings store when Delta is configured, else None."""
          catalog = env.get("FABRIC_DELTA_CATALOG")
          schema = env.get("FABRIC_DELTA_SCHEMA")
          if catalog and schema:
              try:
                  from .context_findings import create_findings_store_delta
                  return create_findings_store_delta(catalog, schema)
              except (ImportError, RuntimeError):
                  pass
          return None
      ```
- [ ] Wire `findings_store=_default_findings_store(env)` into `run_unified_job()` and `run_csv_job()`
      (same DI pattern as `store`)

### B2 (P2): Update cadences — 5-minute Tier 2, hourly Tier 1, 20-minute heartbeat

**databricks.yml:**
- [ ] `fabric_audit_tier2` schedule cron: `"0 */15 * * * ?"` → `"0 */5 * * * ?"`
- [ ] `fabric_audit_sweep` schedule cron: `"0 0 6 * * ?"` → `"0 0 * * * ?"` (every hour)
- [ ] Update `schedule_cron` variable description to reflect hourly default

**job.py:**
- [ ] `_check_tier2_heartbeat()` staleness threshold: `60` → `20` minutes
      (4 missed 5-minute checks = 20 minutes)
- [ ] Update the reason string: "threshold: 60 min" → "threshold: 20 min"

**tier2_check.py:**
- [ ] Update module docstring: "every 15 minutes" → "every 5 minutes"

### B3 (P3): Add regression tests — tests/test_regression_wiring.py (new file)

**Test 1: End-to-end Phase 5→6 wiring**
- [ ] Inject fake collector + fake store + fake findings_store into `run_audit()`
- [ ] Assert `findings_store["write"]` was called with the findings list
- [ ] Assert `query_recent_findings()` against a pre-populated findings store returns non-empty context
- [ ] Assert `format_context()` produces a string containing "prior finding"
- NOTE: This test must exercise the ACTUAL call chain — not mock away the connection it's testing

**Test 2: outbound.py refuses all delivery (only ado_create_ticket registered, disabled)**
- [ ] Call `dispatch_outbound("teams_notify", ...)` → assert `dispatched: False`
- [ ] Call `dispatch_outbound("email_notify", ...)` → assert `dispatched: False`
- [ ] Call `dispatch_outbound("ado_create_ticket", ...)` → assert `dispatched: False` (disabled)
- [ ] Call `dispatch_outbound("unknown", ...)` → assert `dispatched: False`
- [ ] Confirm the allowlist has exactly 1 entry

**Test 3: Tier 2 returns empty delivered dict**
- [ ] Run `run_tier2_check()` with triggered data and empty delivery_sinks
- [ ] Assert `result["delivered"] == {}`
- [ ] Run with `delivery_sinks={"teams": mock_sink}` → assert `result["delivered"] == {}`
      (teams sink is now ignored — delivery_sinks is reserved for Phase 10)

### B4 (P4a): Tier 2 live-stream-only collector

- [ ] Add `_build_tier2_collector(env, window="5m")` to `job.py`:
      Builds ONLY Capacity Events KQL collector (the live source with both throttle and
      concentration signal). Never reads CSV (static, doesn't update between 5-min checks).
      Returns a graceful no-op collector (`{"collect": lambda: {"capacity": None, "items": [], "models": []}}`)
      if no live sources are configured — `null_data_gate` handles empty facts cleanly.
- [ ] Replace `build_collector_from_env(env, window="15m")` in `run_tier2_job()` with
      `_build_tier2_collector(env, window="5m")`
- [ ] New test: inject env with only `FABRIC_CSV_PATHS` set; assert `run_tier2_check()`
      returns `triggered: False`, no crash

### B5 (P4b): Fix store_delta.py history() scan direction for performance

- [ ] Change the `.orderBy("run_at").limit(keep)` call to:
      ```python
      rows = (
          s.table(table)
          .orderBy("run_at", ascending=False)
          .limit(keep)
          .collect()
      )
      return [_from_delta_row(r.asDict()) for r in reversed(rows)]
      ```
      (Preserves the oldest-first return contract; lets Spark use the top of the sorted index
      instead of scanning the full table)
- [ ] Existing tests must still pass (return contract unchanged)
- [ ] New test: insert 10 rows, call `history(keep=5)`, assert length 5 and last element is most recent

### B6 (P4c): Parameterize context_findings.py scope injection

- [ ] In `create_findings_store_delta()` → `query()`, escape scope and tenant before f-string:
      ```python
      if scope:
          safe_scope = str(scope).replace("'", "''")
          conditions.append(f"resource = '{safe_scope}'")
      if tenant:
          safe_tenant = str(tenant).replace("'", "''")
          conditions.append(f"tenant = '{safe_tenant}'")
      ```
- [ ] New test: call `query()` with `scope="O'Brien's workspace"`; assert no exception

---

## PART C: INVESTIGATION AND ALERT ENRICHMENT

This is what the agent actually says when it investigates or alerts. The goal: when a user gets
an alert or asks what happened, the agent should tell them:
1. A clear summary of what caused the issue
2. Whether this is recurring (how often, since when)
3. Whether this looks like healthy normal work or a genuine problem
4. How to fix it (if fixable) — specific, not generic
5. Historical context (how does this compare to recent weeks/months)

### C1 (P5b): Make trend.py window larger and human-readable

**trend.py:**
- [ ] Change `window=7` default to `window=24` (24 hourly runs = 1 day of context)
- [ ] Add `firstSeenAt` ISO timestamp to the per-finding annotation:
      ```python
      matching_runs = [run for run in recent
                       if any(rf.get("key") == f["key"] for rf in run.get("findings", []))]
      first_seen_at = matching_runs[0].get("runAt") if matching_runs else None
      out.append({**f, "recurringRuns": prior_hits + 1, "firstSeenAt": first_seen_at})
      ```
- [ ] Update existing trend tests to reflect the new default window

### C2 (P5c): Add healthy-vs-unhealthy framing to Tier 2 trigger payloads

**tier2_check.py — _check_concentration():**
- [ ] Add `normalityHint` field to each concentration trigger based on share percentage:
      - `sharePct >= 50`: `"High share — likely automated/scheduled or runaway process; verify if this is a known batch job"`
      - `30 <= sharePct < 50`: `"Moderate share — may be a large legitimate user run; check if this matches a known scheduled job or report"`

**tier2_check.py — _check_throttle():**
- [ ] Add `normalityHint`: `"Capacity exceeded its throttle threshold — check if this coincides with a scheduled refresh or batch window"`

**tier2_check.py — _check_pressure():**
- [ ] Add `normalityHint`: `"CU exceeded 100% but throttle not yet confirmed — watch for escalation in the next few checks"`

**tier2_check.py — _check_overage():**
- [ ] Add `normalityHint`: `"Overage is accumulating — if this is a one-off large job it will burn down; if it persists across multiple checks it's a pattern"`

### C3 (P5d): Multi-month baseline comparison

**forecast.py — add bucket_monthly_summary():**
- [ ] New function:
      ```python
      def bucket_monthly_summary(history):
          """Monthly bucketed peak CU% for multi-month baseline comparisons.
          Returns list of {"month": "YYYY-MM", "meanPeakCuPct": float,
          "maxPeakCuPct": float, "runCount": int} oldest-first.
          Requires runs with runAt timestamps."""
          from collections import defaultdict
          buckets = defaultdict(list)
          for run in (history or []):
              run_at = run.get("runAt", "")
              peak = (run.get("metrics") or {}).get("peakCuPct")
              if run_at and peak is not None:
                  month = run_at[:7]  # "YYYY-MM"
                  buckets[month].append(peak)
          result = []
          for month in sorted(buckets):
              vals = buckets[month]
              result.append({
                  "month": month,
                  "meanPeakCuPct": round(sum(vals) / len(vals), 1),
                  "maxPeakCuPct": round(max(vals), 1),
                  "runCount": len(vals),
              })
          return result
      ```

**pipeline.py:**
- [ ] Call `bucket_monthly_summary(history)` from `run_audit()` when store exists
- [ ] Only include result in the envelope when history spans > 45 days
      (fewer than 2 distinct months = not useful for comparison):
      ```python
      monthly = bucket_monthly_summary(history)
      if len(monthly) >= 2:
          d["monthlyBaseline"] = monthly
      ```

### C4 (P5e + P6): System prompt additions — investigation quality rules

**In `fabric-audit-agent-app/agent_server/system_prompt.py`:**

- [ ] Add recurrence-surfacing rule (after the existing cadence-vs-causation rule, ~line 130):
      ```
      - For every finding you report: always state whether it is new (first occurrence this
        sweep) or recurring. Use the finding's recurringRuns and firstSeenAt fields.
        - recurringRuns == 1: "first detected this check — not yet confirmed as recurring."
        - recurringRuns 2–4: "appeared in [N] of the last [N] checks — emerging pattern."
        - recurringRuns >= 5: "present in [N] consecutive checks since [firstSeen date] —
          confirmed recurring pattern."
        - accountability-flagged (openRuns >= 3): "unresolved for [N] consecutive checks
          since [firstSeen] — flagged as a standing open issue."
        This is not optional: a user receiving an alert deserves to know whether this is a
        fresh surprise or something that has been ongoing for days.
      ```

- [ ] Add monthly-baseline rule (after the forecast/trend rules):
      ```
      - When monthlyBaseline is available in the data (history spans multiple months): use it
        to make comparisons, e.g. "July's average peak CU is 87% vs April's 61% — a 43%
        increase month-over-month." Always state the comparison period explicitly. Do not
        compare months with fewer than 3 runs — say "insufficient data for [month]" instead.
        Never fabricate a comparison when monthlyBaseline is absent.
      ```

- [ ] Add render_chart awareness rule (in the tool-usage section):
      ```
      - When you have tabular or time-series data that would be clearer as a chart, call
        render_chart. Rules:
        - sourceScope: "capacity" for capacity-level CU data (true CU, no proxy caveat).
          "item" for per-item attribution. "user" for per-user attribution.
        - isProxy: true for any per-user or per-item data from CpuTimeMs/DurationMs
          (Workspace Monitoring or Log Analytics). false for capacity-level data.
        - Never blend scopes in one chart call — if the question mixes capacity-level and
          per-user data, offer two separate charts or explain why they can't be compared.
        - Always describe the chart in one sentence of text BEFORE calling render_chart,
          so the response is readable even if the chart fails to render.
        - A chart is a tool, not a substitute for the verbal finding. Always state the
          conclusion in text; the chart is supporting evidence.
      ```

- [ ] Add investigation quality rule (in the "what to always include" section):
      ```
      - When investigating or reporting any finding, always include all four of:
        (1) What caused it — the specific source, user, item, or pattern, not just "capacity was high"
        (2) Whether it is recurring — use recurringRuns/firstSeenAt, state in plain calendar terms
        (3) Whether this looks like healthy expected behavior or a problem — use the cadence-vs-
            causation distinction, normalityHint from Tier 2 triggers, and your own judgment about
            whether the pattern matches a known legitimate scheduled workload
        (4) What to do about it — specific fix steps if actionable, or honest "no actionable fix
            exists" if not (e.g. a user doing legitimate large work that legitimately needs capacity)
        A finding without all four is incomplete.
      ```

---

## PART D: DATABRICKS VERIFICATION

The Delta tables are confirmed in `shakur-main.bi-fabrics-audit` — a dedicated schema, fully
isolated from all other Databricks workspace data. Three-part fully qualified names (`catalog.schema.table`),
`CREATE TABLE IF NOT EXISTS`, no shared schemas. You have write permission to this schema.

### D1: Rebuild, redeploy, run sweep after all code changes
- [ ] `python -m build` (in fabric-audit-agent-py)
- [ ] `databricks bundle deploy -t dev`
- [ ] `databricks bundle run fabric_audit_sweep -t dev`
- [ ] `databricks bundle run fabric_audit_tier2 -t dev`

### D2: Verify audit_findings has rows (critical — confirms P0 fix works end-to-end)
- [ ] Query: `SELECT COUNT(*) FROM shakur-main.bi-fabrics-audit.audit_findings`
- [ ] If zero rows: debug. Check whether findings_store was constructed and passed correctly.
      Add temporary print/logging if needed. Fix before continuing.
- [ ] If rows exist: query a sample — `SELECT * FROM shakur-main.bi-fabrics-audit.audit_findings LIMIT 5`
      and confirm the finding_key, level, what_text fields are populated (not all NULL)

### D3: Verify run_history from previous sweep
- [ ] `SELECT * FROM shakur-main.bi-fabrics-audit.run_history ORDER BY run_at DESC LIMIT 5`
- [ ] Confirm previous sweep run appears. Confirm peak_cu_pct, verdict_decision are populated.

### D4: Verify capacity_reporting and concentration_alerts are queryable
- [ ] `SELECT COUNT(*) FROM shakur-main.bi-fabrics-audit.capacity_reporting`
- [ ] `SELECT COUNT(*) FROM shakur-main.bi-fabrics-audit.concentration_alerts`
- [ ] Both must exist and be queryable without error (may be empty — that's fine)

### D5: Verify Tier 2 heartbeat
- [ ] After Tier 2 run: confirm `tier2_heartbeat.json` exists in the Volume
      (`/Volumes/shakur-main/bi-fabrics-audit/reports/tier2_heartbeat.json`)
- [ ] Confirm it contains `{"lastRun": "...", "tier": "tier2"}` with a recent timestamp

### D6: Verify Tier 2 runs clean at 5-minute cadence
- [ ] Check the Databricks job run history for `fabric-audit-tier2`
- [ ] Confirm it completes without error on each run
- [ ] Confirm `triggered: False` on runs where no threshold is crossed (the common case)

---

## PART E: END-TO-END AGENT VERIFICATION

This is not optional — verify real behavior, not just that tests pass. If anything fails, fix it
before moving on. The definition of done is the agent running correctly, not "tests pass locally."

### E1: Full test suites
- [ ] `pytest tests/ -x` in fabric-audit-agent-py — all must pass
- [ ] `pytest tests/ -x` in fabric-audit-agent-app — all must pass
- [ ] Agent-case eval suite (35 cases) — all must pass
- [ ] If any test fails: fix the root cause, not the test

### E2: Specific live agent verification (ask these questions against the deployed chat app)
- [ ] "What's the current capacity health?" — should produce a lean status answer, NOT mention
      size-up unless the verdict is optimize/healthy (size-up verdict → suppress, say "throttling
      detected, ask me for analysis")
- [ ] "Have there been any throttling events this week?" — should surface findings with recurrence
      context ("first detected" vs "present in N of last N checks"), NOT fabricate data if nothing found
- [ ] "Who's using the most capacity?" — should surface concentration data with a proxy caveat
      on per-user figures, NOT claim these are true billed CU
- [ ] "Show me a chart of CU% over time" — should call render_chart with `sourceScope="capacity"`,
      `isProxy=false` (capacity CU is true, not proxy)
- [ ] If any of these fail: diagnose the root cause (wrong tool output? missing prompt rule?
      wrong data?) and fix it. Don't just note it as a gap.

### E3: Redeploy-dependent verification (do now since we're redeploying)
- [ ] Task 7: SP1–SP7 live verification — run the Section 14 stress-test bank Categories 1, 2, 4, 5
- [ ] Task 3.2: Section 14 stress-test bank — run all 20 questions, record pass/fail in GAPS-AND-ISSUES.md
- [ ] Task 4.2: N4 — grep for `# VERIFY AT DEPLOY` markers, confirm all 3 against current deployment
- [ ] Update all three of these tasks to [x] in tasks/todo.md after completion

---

## PART F: FINAL DEPLOYMENT (do this last, after all verification passes)

- [ ] Rebuild MCP package wheel: `python -m build` in fabric-audit-agent-py
- [ ] Deploy MCP bundle (jobs + wheel): `databricks bundle deploy -t dev`
- [ ] Deploy MCP Databricks App (mcp-bi-fabrics-auditor):
      `databricks apps deploy` in fabric-audit-agent-py (confirm app name in app.yaml)
- [ ] Deploy Chat App (fabric-audit-agent):
      `databricks apps deploy` in fabric-audit-agent-app (confirm app name in app.yaml)
- [ ] Verify both apps show as Running in the Databricks Apps console
- [ ] Run one final manual sweep: `databricks bundle run fabric_audit_sweep -t dev`
- [ ] Confirm the final sweep completes clean, writes to run_history AND audit_findings
- [ ] Both jobs (fabric_audit_sweep + fabric_audit_tier2) remain UNPAUSED after deploy

---

## PART G: UPDATE TRACKING DOCS

- [ ] Update `tasks/todo.md` Phase 9 section status note to reflect delivery removal:
      add a note that Teams/email delivery was removed in the post-Phase-9 sprint (2026-07-30)
      and Phase 10 owns delivery
- [ ] Update `GAPS-AND-ISSUES.md` with a session note covering everything completed
- [ ] Mark Task 7, Task 3.2, Task 4.2 as complete in tasks/todo.md after E3 verification
- [ ] Confirm `docs/decisions/POST-PHASE9-FIX-PLAN.md` P1a/P1b are marked superseded

---

## Acceptance check — run this before calling done

```bash
# 1. No delivery infrastructure remains
grep -r "delivery_teams\|delivery_email\|teams_card\|build_teams_card\|create_teams_delivery\|create_email_delivery\|teams_notify\|email_notify\|TEAMS_WEBHOOK_URL\|PlainJsonHttp\|SMTP_HOST\|SMTP_TO" fabric_audit_agent/
# → must return zero hits

# 2. Outbound allowlist has exactly one entry
grep -A5 "_ALLOWLIST" fabric_audit_agent/outbound.py
# → must show only ado_create_ticket (disabled)

# 3. Both test suites pass
cd fabric-audit-agent-py && pytest tests/ -q
cd fabric-audit-agent-app && pytest tests/ -q

# 4. audit_findings has rows
# → run the Databricks SQL query from D2

# 5. Both apps are live
# → check Databricks Apps console
```
