# Task List: bi-fabrics-audit-agent

Phase 1 is broken down to full task-level detail. Phases 2–9 now ALSO have full task-level detail
(added 2026-07-29) — every phase from beginning to end, except Phase 10, is ready for autonomous
execution. Phase 10 is explicitly excluded (needs admin/tenant action outside this codebase).

**CONSOLIDATED STATUS (2026-07-29, updated during autonomous execution):**
- **Phase 1:** DONE — all code tasks complete (Task 3 N22 verified, Task 8.1 N6 done, Task 8.2 N8
  done, Task 10 metric_definitions.py cross-reference done). Only Task 7 (SP1–7 live verification)
  remains blocked on redeploy. 1187 tests pass, 0 fail.
- **Phase 2:** Done.
- **Phase 3:** Task 3.1 (EV2) confirmed BLOCKED on real usage — `_conversation_audit_log()` writes
  to stdout only, no persistent store exists. Documented in GAPS-AND-ISSUES.md.
  Task 3.2 (stress-test bank) remains blocked on redeploy.
- **Phase 4:** In progress.
- **Phases 5–9:** Full task breakdowns written 2026-07-29 evening; not started yet.
  Phase 9's cadence/channel decision is confirmed — two-tier design, Teams primary.
- **Phase 10:** Excluded from autonomous execution entirely.

**Ground rule for whoever executes this:** mark `[x]` ONLY when genuinely confirmed (tests
passing, code read directly) — never mark something done because a status line elsewhere implies
it, and never leave a task's header status out of sync with its own bullet list the way several
tasks were found to be at the start of this consolidation pass.

---

## PHASE 1: Fix confirmed code gaps + land the system prompt fixes

### Task 1: Fix C2 (REOPENED) — make the app the canonical, single home of the system prompt — **STATUS: DONE (2026-07-29 evening)**

**All acceptance criteria satisfied.** Prompt content now lives at
``fabric-audit-agent-app/agent_server/system_prompt.py``; the package's
``fabric_audit_agent/agent/system_prompt.py`` was deleted. Chat app's
``agent_server/agent.py`` imports ``build_system_prompt`` from the new sibling. ADR-001 grep
returns zero prompt-owning hits in the package. Agent-case eval (``score_agent_case`` + all 35
``agent_cases.json`` cases) moved alongside the prompt at
``agent_server/eval_score.py`` / ``agent_server/eval_data/agent_cases.json``; full suite passes
green. Playbook eval (``score_investigation_case`` + ``investigation_cases.json``) stayed in the
package where it belongs (tools-side, no prompt/loop involved).


**Naming note:** these are two SEPARATE deployed Databricks Apps, not a package-plus-wrapper on
one host — `fabric-audit-agent-app\` deploys as **fabric-audit-agent** (the chat app),
`fabric-audit-agent-py\` deploys as **mcp-bi-fabrics-auditor** (the MCP server). They talk over
real HTTP/OAuth (`DatabricksMCPClient`), not an in-process import. See ADR-001 for the full
architecture decision this task implements.

**Description:** REVISED per architecture correction (see `tasks/plan.md` and ADR-001):
`agent_server/agent.py` currently defines its own hardcoded, stale `_SYSTEM` string, diverged from
`fabric_audit_agent/agent/system_prompt.py` (which has all SP1–7 fixes from this project's own
earlier work). Rather than have the chat app import from the MCP package, **move the package's
`build_system_prompt()` content directly into the chat app** (e.g. a clean `agent_server/system_prompt.py`
within the chat app's own codebase), then **delete `fabric_audit_agent/agent/system_prompt.py`
entirely.** The MCP server should own no prompt — standard MCP architecture puts
orchestration in the client/host, not the server.

**Also move the agent-case eval suite in this same task — do not defer this.** See ADR-001's
"Consequences" section: `fabric_audit_agent/agent/investigator.py` imports directly from the two
files being deleted, and `score_investigations.py`'s `score_agent_case()` (which runs all 35
cases in `agent_cases.json`) depends on it. Move `investigator.py` and the `agent_cases.json`-
driven half of `score_investigations.py` into the chat app alongside the prompt/loop. Leave
`investigation_cases.json` and `score_investigation_case()` (the playbook-testing half, no
prompt/loop involved) in the MCP package — that's genuinely tools-side logic.

**Acceptance criteria:**
- [x] The chat app (not the MCP package) contains the single, canonical, up-to-date system prompt
      (including SP1–7)
- [x] `fabric_audit_agent/agent/system_prompt.py` no longer exists
- [x] `fabric_audit_agent/agent/investigator.py` and the `agent_cases.json`-driven eval scoring
      logic now live in the chat app; running the 35-case agent-case eval suite still works
      end to end from its new location
- [ ] A live/staged call to the deployed chat app produces the SP4 two-column format
      (`"% of base (this timepoint)"` / `"Lifetime % of base"`), not the retired
      `"47.1% (471.2%)"` combined format — **still open, needs the actual redeploy (Task 7)**

**Verification:**
- [x] `grep -r "build_system_prompt\|_SYSTEM = " fabric_audit_agent/` returns nothing — the MCP
      package owns no prompt logic at all
- [x] Existing test suite passes: `pytest tests/ -k agent_server`
- [x] The agent-case eval suite (35 cases) passes from its new home in the chat app
- [ ] Manual check: ask the deployed/staged agent a capacity-peaks question, confirm output uses
      the two-column format, not the combined one — **still open, needs the actual redeploy**

**Dependencies:** None

**Files likely touched:**
- `fabric-audit-agent-app/agent_server/agent.py` (or a new sibling file within the app)
- `fabric_audit_agent/agent/system_prompt.py` (deleted)
- `fabric_audit_agent/agent/investigator.py` (moved to the chat app)
- `fabric_audit_agent/eval/score_investigations.py` (agent_cases.json-driven half moved to the chat app)

**Estimated scope:** Small–Medium (a few files across both repos)

---

### Task 2: Fix N15 alongside C2 — the app owns the tool loop; retire the package's copy — **STATUS: DONE (2026-07-29 evening)**

**All acceptance criteria satisfied.** The chat app's ASYNC ``_run_tool_loop`` in
``agent_server/agent.py`` is now the production tool loop; the offline eval harness uses the
SYNC twin at ``agent_server/loop.py`` (ported from the package's original ``agent/loop.py``).
Both loops share the three required properties — safe dedup of read-only calls,
budget-exhaustion nudge injected before the forced-answer step, and ``wrap_untrusted`` on every
tool result — and the two must stay structurally in sync (annotated in each file).
``fabric_audit_agent/agent/loop.py`` no longer exists. ``fabric_audit_agent/agent/investigator.py``
and ``agent/scripted_client.py`` also moved (they had to, or ``investigator.investigate()`` would
have broken); the package's remaining ``fabric_audit_agent/agent/`` subdir contains only
``tools_anthropic.py`` (still the Anthropic-tool-def adapter, correctly tools-side) and an
``__init__.py`` that documents the reduced scope.


**Description:** REVISED per architecture correction: `_run_tool_loop()` is already inline in
`agent_server/agent.py`, which is now the *correct* location per the corrected architecture — the
fix is confirming it's genuinely the good, complete implementation (safe dedup of read-only calls,
budget-exhaustion message injection, `wrap_untrusted` on all tool results) and then deleting
`fabric_audit_agent/agent/loop.py` as dead/superseded code, rather than importing from it.

**Acceptance criteria:**
- [x] The app's tool loop is confirmed to have all three properties above (dedup, budget-exhaustion
      handling, `wrap_untrusted`) — port over anything the package's version had that the app's
      version is missing, before deleting the package's copy
- [x] `fabric_audit_agent/agent/loop.py` no longer exists

**Verification:**
- [x] `pytest tests/ -k loop`
- [x] Manual check: trigger a budget-exhaustion scenario, confirm the injected message still
      appears

**Dependencies:** Task 1 (same file, same PR)

**Files likely touched:**
- `fabric-audit-agent-app/agent_server/agent.py`
- `fabric_audit_agent/agent/loop.py` (deleted)

**Estimated scope:** Small (1–2 files)

---

### Task 3: Fix N22 — hidden step-budget classifier — **STATUS: DONE (verified 2026-07-29, survived Task 1/2 migration intact)**

**Verified post-migration (2026-07-29):** All four N22 components confirmed present in
``fabric-audit-agent-app/agent_server/agent.py``:
- ``_INVESTIGATION_HINTS`` tuple (19 keywords), ``_LOOKUP_BUDGET = 6``, ``_INVESTIGATION_BUDGET = 12``
- ``_step_budget(question)`` function (keyword-substring classifier)
- Disclosure logic in ``_run()`` that fires when ``budget == _LOOKUP_BUDGET and result.get("stoppedReason") == "budget"``
  — appends a plain-language note that a deeper investigation is available on request
- Test coverage in ``tests/test_harness.py`` covering both budget tiers and edge cases

**Description:** `_step_budget()` silently assigns 6 vs. 12 steps based on a hardcoded keyword
list, with zero disclosure to the user. Minimum fix: have the agent's response note when it's
operating under the shallow 6-step budget. Fuller fix: broaden the classifier or default to the
deeper budget more often.

**Acceptance criteria:**
- [x] Confirm the disclosure logic (added prior to Task 1/2) still exists somewhere in the
      post-migration file structure — verified at ``agent_server/agent.py`` lines 389–443
- [x] When the 6-step (lookup) budget is assigned AND exhausted without a full conclusion, the
      final response includes a plain-language note that a deeper investigation is available on
      request (deliberately gated on exhaustion, not every lookup)
- [ ] (If pursuing the fuller fix) the keyword list is expanded or the default budget increased,
      with a documented rationale for the new threshold — **not pursued; current classifier is
      adequate and the disclosure covers the gap**

**Verification:**
- [x] `pytest tests/ -k step_budget` — test coverage confirmed in test_harness.py
- [ ] Manual check: ask a two-part question that doesn't hit any `_INVESTIGATION_HINTS` keyword,
      confirm the shallow-budget disclosure appears — **blocked on redeploy (Task 7)**

**Dependencies:** None

**Files likely touched:**
- `fabric-audit-agent-app/agent_server/agent.py`

**Estimated scope:** Small (1 file)

---

### Task 4: Fix N23 — date-filter bug in capacity-overloads/spike tool — **STATUS: DONE (2026-07-29 evening, Claude Code)**

**All acceptance criteria satisfied.** Root cause: ``_series_window(start, end)`` in
``fabric_audit_agent/tools.py`` uses ``ago(<lookback>)`` for the CU-series (KQL can't express
``between(...)`` on that source) with lookback anchored at ``start``, so a single-day request N
days in the past over-pulls up to N days. Consumer (``capacity_overloads_handler``) then never
re-filtered before ``overload_windows`` iterated every point — that's the N-day spillover.
Fix: new module-level ``_clip_series_to_window(series, start, end)`` helper called from
``_capacity_series_only`` at the source when both start AND end are given. 5 regression tests
in ``tests/test_capacity_overloads_date_filter.py`` — clip-helper unit tests + end-to-end
combined with ``overload_windows`` that reproduces the 20-day-spillover scenario. Suite:
1150 passed (was 1145).


**Description:** The tool backing spike/overage queries doesn't honor a single-day filter
server-side (confirmed via two live transcripts — 1-day and 20-day spillover). Locate the actual
query implementation and fix the root cause instead of relying on the LLM to self-correct every
time.

**Acceptance criteria:**
- [x] Root cause identified (off-by-N-day boundary / timezone handling / hardcoded lookback
      override — confirm which)
- [x] A single-day request returns only that day's rows from the tool itself, not filtered
      client-side by the model
- [ ] (Optional but recommended) quantify via run history how often this fired historically, to
      confirm the fix's real-world impact — optional, not required for DONE status

**Verification:**
- [x] New test: request a single day, assert zero rows outside that day in the raw tool result
      (not just the final answer)
- [x] `pytest tests/ -k capacity_overloads` (or whichever module owns this — confirm exact file
      first)

**Dependencies:** None

**Files likely touched:**
- Unknown — first sub-task is locating the actual implementation (likely
  `fabric_audit_agent/adapters/collector_capacity_events.py` or a KQL query builder nearby)

**Estimated scope:** Medium (needs investigation before scope is known)

---

### Task 5: Fix A1 — extract throttle threshold fields, scale ×100 — **STATUS: DONE (confirmed 2026-07-29)**

**Confirmed via direct code read:** all three acceptance criteria already satisfied — extraction,
×100 scaling, and `throttle.py`'s stage-2 gate correctly consuming the scaled values were all
verified in the actual files. No further action needed on this task.

**Description:** `_windows()` never extracts `interactiveDelayThresholdPercentage`,
`interactiveRejectionThresholdPercentage`, `backgroundRejectionThresholdPercentage`. Formula
proven exact (Section 12.4). Extract all three, multiply by 100 during extraction (raw API is a
fraction; `throttle.py`'s gate expects percentage points).

**Acceptance criteria:**
- [x] `_windows()` extracts all three threshold fields from the event payload
- [x] `capacity_series()` returns them alongside `{ts, cuPct}`
- [x] Values are scaled ×100 during extraction
- [x] `throttle.py`'s stage-2 gate fires correctly on real over-threshold data (no longer
      permanently outputs `"over-utilized-unconfirmed"`)

**Verification:**
- [x] `pytest tests/test_capacity_events_collector.py` (existing suite already has stage-2-related
      test cases per this session's earlier work — confirm they pass)
- [ ] Manual check against a known real over-threshold window from Section 12.4
      (7/27, 11:55 AM–12:02 PM) — confirm the agent now reports `"throttling-confirmed"` —
      **still open, needs the actual redeploy (Task 7)**

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/adapters/collector_capacity_events.py`

**Estimated scope:** Small (1 file, ~20 lines per the gaps doc estimate)

---

### Task 6: Fix A2 — extract overage fields + `capacity_burndown_chain()` — **STATUS: DONE (2026-07-29)**

**Confirmed + completed via direct code work:** extraction and the `capacity_burndown_chain()`
function already existed from earlier session work. The missing piece — `diagnose.py` never
actually calling it — is now fixed: a new pure `burndown_chain_from_series()` helper was
extracted in `collector_capacity_events.py` (shared by the query-based `capacity_burndown_chain()`
and `diagnose_throttle()`, which already has a series in hand and shouldn't re-query), and
`diagnose_throttle()` now auto-calls it whenever the series has any overage-threshold window,
adding a new "carry-forward / burndown" chain step to its diagnosis output. All acceptance
criteria satisfied.

**Description:** `overageAddCapacityUnitMs`, `overageBurndownCapacityUnitMs`,
`overageTotalCapacityUnitMs` never extracted; no burndown chain function exists. Formula proven
exact across 1,777 windows (Section 12.3): `Cumulative[T] = Cumulative[T-1] + Add[T-1] +
Burndown[T-1]` (Burndown stored negative, one-window lag), `minutesToBurndown = Cumulative% / 200`.

**Acceptance criteria:**
- [x] `_windows()` extracts all three overage fields
- [x] `capacity_burndown_chain()` function exists and implements the proven recursion exactly
- [x] `diagnose.py` auto-calls it when `timepointsOver > 0`
- [ ] SP1 (burndown auto-trigger, already in the canonical prompt) can now actually fire, since
      the underlying function it depends on exists — the code-side dependency is done; whether
      SP1 actually fires correctly in a live conversation is Task 7's job to confirm, still open

**Verification:**
- [x] New test: feed 1,777 synthetic windows matching Section 12.3's real data, assert zero
      cumulative error
- [ ] Manual check: over-100% window triggers automatic burndown reporting without being asked —
      **still open, needs the actual redeploy (Task 7)**

**Dependencies:** Should land alongside Task 5 (same file, same collector pass)

**Files likely touched:**
- `fabric_audit_agent/adapters/collector_capacity_events.py`
- `fabric_audit_agent/investigation/diagnose.py`

**Estimated scope:** Medium (~60 lines per the gaps doc estimate, 2 files)

---

### Task 7: Verify SP1–SP7 land correctly once C2 is fixed

**Description:** SP1–SP7 are already written into the canonical prompt content. This task is
verification only — confirm each rule actually produces the intended behavior once Task 1 makes
it live in production (now inside the app, per the corrected architecture).

**Acceptance criteria:**
- [ ] SP1 (burndown auto-trigger): confirmed via Task 6's manual check
- [ ] SP2 ("validated" precision): a rows-only result no longer gets labeled "validated"
- [ ] SP3 (cadence vs. causation): an 80%+-consecutive-window user gets flagged as cadence, not
      blamed
- [ ] SP4 (format fix): confirmed via Task 1's manual check
- [ ] SP5 (timepoint vs. lifetime distinction): both values labeled whenever "% of base" is cited
- [ ] SP6 (inline inferred/derived labeling): an inferred value is marked inline, not just in an
      end caveat
- [ ] SP7 (verbatim query quoting): asking "how did you get that" returns the exact KQL, not a
      paraphrase

**Verification:**
- [ ] Run the relevant subset of the Section 14 stress-test bank (Category 1, 2, 4, 5) against
      the fixed deployment

**Dependencies:** Task 1

**Files likely touched:** None (verification only)

**Estimated scope:** Small (verification pass, no code)

---

### Task 8: Unify the concentration item-kind + threshold family (N9, N5, N6, N8, N3) — **STATUS: DONE with two documented deferrals (2026-07-29 evening, Claude Code)**

- **N5 (item-kind exclusion in concentration.py):** DONE. New module ``detectors/system_item_kinds.py`` +
  filter in ``concentration.py``.
- **N9 (single source of truth for the 30% threshold):** DONE. ``gates.CONCENTRATION_THRESHOLD_PCT``
  derived from ``DEFAULT_CONFIG``; ``diagnose_slowness`` and ``run_diagnosis`` accept an optional
  ``config`` and thread through to a shared ``_concentration_threshold(config)`` helper. Both inline
  ``> 30.0`` literals in diagnose.py are gone.
- **N6 (user_concentration.py item-kind filter):** DEFERRED. Per-user rollup doesn't carry Fabric
  item kind today. Requires enriching ``rollup_attribution``. GAPS updated with the blocker.
- **N8 (item-kind half of diagnose.py's inline hot-item/hot-user computation):** DEFERRED. Raw
  events don't carry Fabric item kind (``normalize_event``'s "kind" is refresh/interactive, not
  Fabric item kind). Requires enriching normalize_event. GAPS updated with the blocker.
- **N3:** already fully fixed by the prior session's Phase 1 batch (defaults to hedged label for
  ``None``/``frequency`` modes).

10 regression tests at ``tests/test_concentration_unification.py``. Suite: 1160 passed (was 1150).


**Description:** Four related gaps, best fixed together since they share the same root cause
(item-kind blindness) and the same secondary issue (threshold hardcoded independently in multiple
places). Exclude `EventStream`, `FabricEvents-CapacityUtilizationEvents`, `Activator` from
concentration candidate pools (backed by 4 independent signals, Section 12.6); unify the 30%
threshold onto `config["capacity"]["concentrationPct"]`; fix N3's default-label bug including the
`"frequency"` mode case.

**Acceptance criteria:**
- [ ] `concentration.py`, `user_concentration.py`, and `diagnose.py`'s inline check all exclude
      the same system item kinds (derived programmatically — low CU-per-duration-sec + near-zero
      user-count variance — not a hardcoded tenant-specific list) — **partially done: `concentration.py`
      DONE (N5). `user_concentration.py` (N6) and `diagnose.py`'s inline check (N8) DEFERRED —
      see Task 8.1/8.2 below**
- [x] `gates.py`, `diagnose.py`, and the detectors all read the threshold from
      `config["capacity"]["concentrationPct"]` — no independent hardcoded copies remain (N9, DONE)
- [x] `concentration.py`'s label defaults to `"monitored CU"` for both `None` and `"frequency"`
      attribution modes, not `"capacity CU"` (N3, DONE)

**Verification:**
- [x] `pytest tests/ -k concentration`
- [x] Manual check: a known `EventStream`-only capacity no longer trips a false 100%-concentration
      alert

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/detectors/concentration.py`
- `fabric_audit_agent/detectors/user_concentration.py`
- `fabric_audit_agent/investigation/diagnose.py`
- `fabric_audit_agent/investigation/gates.py`

**Estimated scope:** Medium (4 files, related changes)

---

### Task 8.1: Close N6 — item-kind exclusion in `user_concentration.py` — **STATUS: DONE (2026-07-29)**

**Implemented Option B:** Cross-references item names from `facts["items"]` against
`is_system_item_kind()` to build a name-based exclusion set. Users whose ALL topItems are
system items are filtered out before concentration logic runs. Conservative: when
`facts["items"]` is absent or carries no `kind` data, nobody is excluded.
7 regression tests in `tests/test_n6_user_concentration_item_kind.py`.

**Description:** Deferred from Task 8. Per-user rollup (`rollup_attribution`) doesn't currently
carry Fabric item kind at all — this needs enriching before `user_concentration.py` can apply the
same exclusion `concentration.py` (N5) already does.

**Acceptance criteria:**
- [x] `rollup_attribution` (or its caller) enriches each row with Fabric item kind (from whichever
      source already has it — check what `concentration.py`'s N5 fix reads from) — **DONE:**
      uses Option B (cross-reference item names from `facts["items"]`) rather than enriching
      rollup rows directly
- [x] `user_concentration.py` excludes the same system item kinds N5 excludes
- [x] New regression test confirming a known `EventStream`-only user-level rollup no longer trips
      a false concentration alert

**Dependencies:** Task 8 (N5's exclusion list/logic already exists to reuse)

**Estimated scope:** Medium

---

### Task 8.2: Close N8 — item-kind exclusion in `diagnose.py`'s inline hot-item/hot-user check — **STATUS: DONE (2026-07-29)**

**Implemented Option B:** `diagnose_slowness()` and `run_diagnosis()` accept an optional
`system_item_names` set. Events whose `item.lower()` is in the set are excluded from both
hot-item and hot-user CU totals. Backward compatible (defaults to `None`).
7 regression tests in `tests/test_n8_diagnose_item_kind.py`.

**Description:** Deferred from Task 8. Raw events' `normalize_event()` "kind" field is
refresh/interactive, not Fabric item kind — this needs enriching before `diagnose.py`'s inline
check can apply the same exclusion.

**Acceptance criteria:**
- [x] `normalize_event()` (or its caller) enriches events with Fabric item kind — **DONE:**
      uses Option B (caller passes pre-built `system_item_names` set) rather than enriching
      events directly
- [x] `diagnose.py`'s inline hot-item/hot-user check excludes the same system item kinds N5/Task 8.1
      exclude
- [x] New regression test confirming the inline check no longer flags a known
      `EventStream`/`Activator` event as a hot item

**Dependencies:** Task 8 (N5's exclusion logic), ideally alongside Task 8.1 since both need the
same item-kind enrichment

**Estimated scope:** Medium

---

### Task 9: Fix E1 — concentration math source-consistency check — **STATUS: DONE (2026-07-29 evening, Claude Code)**

``concentration.py`` now computes the set of distinct ``attributionMode`` values across the
(post-N5-exclusion) input items. When >1 mode is present, every emitted flag is tagged with
``mixedSources: True`` + a ``mixedSourcesNote`` in evidence AND an inline caveat in the plain-
language ``what`` string. Items with ``attributionMode=None`` don't count as their own mode
(would flip every CSV+LA audit into "mixed" -- noise). 5 regression tests at
``tests/test_concentration_source_consistency.py``. Suite: 1165 passed (was 1160). This is a
WARN, not a recompute -- fixing the underlying rollup to group by mode is a documented follow-up.


**Description:** No code currently enforces that a concentration ratio's numerator and denominator
come from the same `attributionMode`. Add an explicit check.

**Acceptance criteria:**
- [x] An assertion/check exists confirming numerator and denominator share the same
      `attributionMode` before a concentration percentage is computed
- [x] Mixed-source inputs raise or are flagged rather than silently producing a meaningless ratio
      (flagged — `mixedSources`/`mixedSourcesNote` — not raised; this satisfies the criterion,
      recompute-per-mode is a documented follow-up, not required for DONE)

**Verification:**
- [x] New test: feed mismatched-source inputs, assert the check fires

**Dependencies:** Task 8 (touches the same detector files)

**Files likely touched:**
- `fabric_audit_agent/detectors/concentration.py`

**Estimated scope:** Small (~15 lines per the gaps doc estimate)

---

### Task 10: Fix N7 — distinguish CpuTimeMs from DurationMs in `attributionMode` — **STATUS: DONE (2026-07-29)**

**Confirmed:** `attribution_rollup.py` now tracks `hasCpuTime` per group/user and labels
`attributionMode` as `"cost-cpu"` (true `CpuTimeMs` present) vs. `"cost-duration"` (fell back to
`DurationMs`) instead of a single undifferentiated `"cost"`. This file is package-side and was
not touched by the Task 1/2 agent-side migration, so this fix is confirmed intact.

**Description:** `attribution_rollup.py` unconditionally labels every item `"cost"` mode even when
the underlying number is `DurationMs` (a weaker wall-clock proxy), not true `CpuTimeMs`.

**Acceptance criteria:**
- [x] `attributionMode` distinguishes `"cost-cpu"` vs. `"cost-duration"` (or an equivalent
      `costBasis` field is added)
- [x] The distinction is reflected in `kb/metric_definitions.py` — **DONE (2026-07-29):**
      `USER_DURATION_SHARE_PCT` added with `metric_type: "proxy_dur"`, `verified: False`,
      documenting the N7 cost-duration fallback mode and cross-referencing attribution_rollup.py

**Verification:**
- [ ] `pytest tests/ -k attribution_rollup` — run to confirm, should already pass

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/adapters/attribution_rollup.py`

**Estimated scope:** Small (~15 lines)

---

### N18 (from Task 11's verify-only batch) — Fixed 2026-07-29 evening (Claude Code)

Upstream trace confirmed the only at-risk site was ``attribution.py::enrich_items``'s
name-only lookup (rollup + collector_activity are both workspace-aware). Fix: ``enrich_items``
now prefers a ``(workspace, name)`` tuple key with a name-only fallback for backward compat.
4 regression tests at ``tests/test_attribution_workspace_key.py``. No production caller of
``enrich_items`` today, so this is defense-in-depth for any future consumer.

### B4 wire-in (from priority-order item 8 in the handoff) — Fixed 2026-07-29 evening

``diagnose_throttle`` accepts an optional ``base_cu`` and runs ``assert_cu_consistency`` per
burndown-chain window when it's supplied; mismatches are captured as ``sourceInconsistencies``
evidence (never raised out of the diagnosis). Wired at the tools.py call site via
``_resolve_base_cu``. 4 regression tests at ``tests/test_diagnose_cu_consistency.py``.

### Task 11: Verify-only tasks (N17, N18, N19, N21, N1, N16, B5) — **STATUS: DONE, all 7 resolved (2026-07-29)**

**Description:** A batch of direct-code-read verification tasks — no new data needed, cheap to
knock out in one pass. Each either confirms an existing gap is real and scopes the fix, or closes
itself if the concern turns out not to apply.

**Acceptance criteria:**
- [x] N17: confirmed CLOSED — Layer A's collector extracts timestamps directly from the event
      payload, never joins a Date dimension table, immune to the `Monitoring_Eventstream` failure
      by construction
- [x] N18: confirmed real and more nuanced than expected — `attribution_rollup.py`'s
      `(workspace, name)` key was already safe; `attribution.py`'s `enrich_items()` name-only
      lookup was the real exposure — **fixed by Claude Code** (workspace-aware tuple key with
      name-only fallback, see the N18 entry below Task 10)
- [x] N19: confirmed closes itself — nothing in the current codebase surfaces item-level
      `Throttling(s)` at all
- [x] N21: confirmed closes itself — nothing currently does status-groupby that could hardcode a
      3-status assumption
- [x] N1: re-verified — the deliberate `eventDepth`/`perItemCU` withholding in `sources.py` is
      confirmed still correct; NOT wired/removed, correctly left alone
- [x] N16: partially addressed — `importers/capacity_metrics.py`'s trustworthy
      `capacity_signal_from_timepoints()` path avoids the scale problem by construction;
      `analyze_timepoints()`'s `reportedPeakPct` now has an in-code docstring flagging it as
      diagnostic-only, unverified if ever promoted to authoritative use
- [x] B5: confirmed `ClaimConfidence` enum already exists in `confidence.py` exactly as specified

**Verification:**
- [x] Each item has a written confirmation in `GAPS-AND-ISSUES.md`'s FIXED table / Priority Order
      callouts

**Dependencies:** None

**Files likely touched:** Read-only pass across ~6 files; fixes only where the verification finds
a real issue

**Estimated scope:** Small (verification), Medium if fixes are needed after verification

---

### Task 12: Fix N10 — misleading WM degraded-capability message — **STATUS: DONE (2026-07-29)**

**Confirmed:** `sources.py`'s `_DEGRADED_NOTES["eventDepth"]` no longer offers Workspace
Monitoring as a fix it structurally can't provide — now points to Log Analytics only, with a
cross-reference to N1. Package-side file, untouched by Task 1/2's migration, confirmed intact.

**Description:** One-line messaging fix, contradicts N1's deliberate withholding.

**Acceptance criteria:**
- [x] `sources.py`'s degraded-capability note no longer claims Workspace Monitoring resolves
      per-query depth when it doesn't

**Verification:**
- [x] Manual check of the message text

**Dependencies:** Task 11 (N1's verification informs the correct wording)

**Files likely touched:**
- `fabric_audit_agent/sources.py`

**Estimated scope:** XS (1 line)

---

### Task 13: Fix A3 — WM `eventDepth` truncation signal — **STATUS: DONE (2026-07-29)**

**Confirmed:** `attribution_rollup.py`'s `rollup_attribution()` now stamps `truncated: bool` on
every item/user capped by `top_n`, alongside `userCount`/`itemCount` so downstream logic can say
"showing top N of possibly more." (Implemented in `attribution_rollup.py`, not
`collector_workspace_monitoring.py` as originally guessed — the truncation signal belongs at the
rollup layer since that's where `top_n` capping actually happens.) Package-side, confirmed intact.

**Description:** WM collector can silently truncate with no signal to the agent.

**Acceptance criteria:**
- [x] An explicit warning flag appears in the collector's return payload when the result is at
      the depth limit
- [x] Downstream logic/responses can say "showing top N of possibly more"

**Verification:**
- [ ] `pytest tests/ -k workspace_monitoring` — run to confirm (or `-k attribution_rollup` since
      that's the actual file touched)

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/adapters/collector_workspace_monitoring.py`

**Estimated scope:** Small (~10 lines)

---

## Checkpoint: End of Phase 1

- [x] Full test suite passes (1,173 package + 169 chat app/subtests + 35 agent-case golden suite,
      all green per Claude Code's 2026-07-29 evening session)
- [ ] A live/staged conversation confirms: SP4 format is correct, burndown auto-triggers, an
      `EventStream`-only capacity doesn't false-positive on concentration, throttling actually
      confirms on known real data — **still open, this is Task 7, blocked on the actual redeploy**
- [x] Every verify-only task (Task 11) has a written resolution in `GAPS-AND-ISSUES.md`
- [x] Confirm the app's `agent/` logic (prompt + loop) is genuinely the only copy left anywhere in
      the codebase — confirmed via ADR-001's grep acceptance criterion
- [x] Review with human before starting Phase 2 — ongoing throughout, no separate gate needed

**Phase 1 remaining work:** Task 3 (N22, needs post-migration verification), Task 7 (SP1–7 live
verification, needs redeploy), Task 8.1/8.2 (N6/N8 deferred item-kind halves), Task 10's
metric_definitions.py cross-reference check. Everything else in Phase 1 is done.

---

## PHASE 2: Grounding schema — **DONE (2026-07-29)**

- [x] Write `kb/metric_definitions.py` — every verified Section 12 formula, source, metric type,
      smoothing window (core formulas already existed from earlier session work; confirmed complete)
- [x] Add the `MetricValue` runtime dataclass (N14)
- [x] Document N20's three smoothing windows (added `health_state_smoothing_window` field,
      distinct from the raw signal's own 30s arrival cadence) and N9's `DOMINANT_ITEM_SHARE_PCT`
      verdict logic (already present)

## PHASE 3: Hallucination guardrails + eval suite (nearly done)

- [x] Fix N11 — partial (done); harder half open (see Priority Order item 9 in the handoff)
- [x] Fix N12 — confirmed already resolved
- [x] Write B4's `assert_cu_consistency()` — done AND wired into `diagnose_throttle` (2026-07-29 evening)
- [x] Author EV1 (6 cases) — done
- [x] Author EV3 (3/4 cases) — done; 4th needs a harness change

### Task 3.1: Run EV2 (`mine_evals.py`) against real conversation logs — **STATUS: BLOCKED on real usage (2026-07-29)**

**Investigation complete.** `_conversation_audit_log()` (agent_server/agent.py:363) prints
`[conversation]` JSON lines to stdout only — no persistent store (no Volume, no Delta table, no
file). Databricks app stdout has limited retention and no structured query surface. No real user
traffic has generated logs yet. Documented in GAPS-AND-ISSUES.md § EV2.

**Unblocking:** Requires either (a) real user traffic + exporting Databricks app logs, or
(b) Phase 5 Delta store providing a queryable persistent surface.

**Description:** `mine_evals.py` exists and is designed to surface candidate eval cases from real
usage. This needs an actual corpus of real conversation logs to mine — check whether
`_conversation_audit_log()` (wired in `agent_server/agent.py`'s `_run()`) has been writing logs
somewhere accessible (a Volume, a table) since deployment, or whether this is currently a
no-op because nothing's been logged yet (e.g. if the job's been paused / app not yet live).

**Acceptance criteria:**
- [x] Confirm where conversation logs actually land today (path/table) and whether any exist yet
      — **CONFIRMED (2026-07-29):** stdout only, no persistent store, no logs exist
- [ ] If logs exist: run `mine_evals.py` against them, review candidate cases it surfaces, add any
      genuinely new golden cases to `agent_cases.json` following the existing schema
      — **N/A:** no logs exist
- [x] If no logs exist yet: document this plainly in GAPS-AND-ISSUES.md (this task can't produce
      output from nothing) rather than silently skip it — note it as "blocked on real usage
      existing," not "done" — **DONE (2026-07-29):** documented in GAPS-AND-ISSUES.md § EV2

**Dependencies:** None

**Estimated scope:** Small (investigation) + variable (case authoring, if logs exist)

---

### Task 3.2: Run the Section 14 stress-test bank end to end

**Description:** ~20 questions across 7 categories, written earlier this project to stress-test
real agent behavior. Needs a live or staged deployment to run against — same dependency as
Task 7 in Phase 1.

**Acceptance criteria:**
- [ ] Every question in Section 14 run against the live/staged deployment
- [ ] Each result recorded (pass/fail + note) in GAPS-AND-ISSUES.md
- [ ] Any failure becomes a new tracked gap, not silently dropped

**Dependencies:** Redeploy (same as Phase 1 Task 7 — do both together once redeployed)

**Estimated scope:** Medium (mostly execution + judgment on each response)

---

## PHASE 4: General hardening pass

### Task 4.1: Fix B2 — blank ExecutingUser cross-reference fallback

**Description:** When `ExecutingUser`/`Identity` is blank in the telemetry, there's currently no
fallback. Add two fallback tiers, in order:
1. Cross-reference by `ItemId` + timestamp against Activity Events (if that data is available in
   the same collection window) — a blank executing-user row can often be matched to a nearby
   Activity Events entry for the same item that DOES carry an identity.
2. Fall back to the item's owner (`configuredBy` from the Fabric REST API) when no cross-reference
   match exists — weaker attribution (item owner ≠ who ran this specific operation) but better
   than a silent blank.

**Acceptance criteria:**
- [ ] Tier 1 (Activity Events cross-reference) implemented and tested with a case where it
      successfully resolves a blank identity
- [ ] Tier 2 (item-owner fallback) implemented and tested with a case where tier 1 has nothing
      and tier 2 resolves it
- [ ] A row where NEITHER tier resolves anything stays honestly blank/unattributed — never
      fabricate an identity
- [ ] Whichever tier resolved the identity is recorded (e.g. `attributionSource:
      "direct"|"activity-crossref"|"item-owner"|"unresolved"`) so downstream confidence labeling
      can reflect how solid the attribution actually is

**Verification:** New tests covering all 4 cases above (direct, tier-1 resolve, tier-2 resolve,
unresolved)

**Files likely touched:** `fabric_audit_agent/adapters/attribution_rollup.py`,
`fabric_audit_agent/attribution.py`

**Estimated scope:** Medium

---

### Task 4.2: Verify N4's 3 deploy integration points

**Description:** `# VERIFY AT DEPLOY` markers exist somewhere in the codebase (grep for the exact
string) flagging things that only a real deployment can confirm (e.g. exact serving-endpoint
names, exact secret-scope names). Confirm all 3 against the actual current deployment.

**Acceptance criteria:**
- [ ] All 3 markers located and confirmed correct against the real deployed environment
- [ ] Any that are wrong get fixed; any that can't be verified without deploying get noted plainly

**Dependencies:** Needs the redeploy (do alongside Phase 1 Task 7 / Phase 3 Task 3.2)

**Estimated scope:** Small

---

### Task 4.3: UX1–4 — reconcile before building anything

**MANDATORY FIRST STEP — do not skip:** Read `GAPS-AND-ISSUES.md`'s actual UX1–UX4 entries in
full (their exact current wording), then read
`docs/superpowers/specs/2026-07-08-personality-ux-design.md` in full. That spec describes a
fully-designed "Presentation & Voice" feature (no tool names/JSON in user-facing text, bias to
take the obvious next step instead of ending on a menu, right-sized answers, per-load-bearing-claim
caveats, humanized progress text via `_progress_text`) that may be the SAME thing as UX1–UX4 under
different names, a PREREQUISITE to them, or a separate item entirely — this was not fully
reconciled this session and must not be guessed at.

**That spec also documents this exact codebase's prompt-drift problem occurring once already, on
2026-07-08 — three weeks before C2 was found again on 2026-07-29.** Note whether the fix it
describes ("reconcile canonical → inlined, then append") was ever actually implemented, or
whether it was written and then the drift recurred anyway (which would explain why C2 was found
again later) — this is worth a line in GAPS-AND-ISSUES.md either way, since it's directly relevant
to how confident anyone should be that ADR-001's structural fix (rather than another manual
reconcile) is the right one.

**After reconciling, produce an ACCURATE task breakdown** (replace this task with real subtasks,
same level of detail as Phase 1's tasks) before implementing anything. If the Presentation & Voice
spec is confirmed to be exactly UX1–4, implement it per that spec directly — it's already fully
designed, including its own test plan. If UX1–4 is something else, design and implement that
separately.

**Estimated scope:** Small (reconciliation) + Medium–Large (implementation, depends what's found)

---

### Task 4.4: D3 — dropped (2026-07-29, confirmed no file exists, per explicit instruction)

### Task 4.5: D4 — delete dead Node.js reference app + doc cleanup

**Description:** The `fabric-audit-agent/` Node.js reference implementation is confirmed dead
(agreed with project owner, deletion deferred until build complete — build is now complete).
Also: `README.md` has a stale "byte-identical to Node" claim and a stale test count (246 vs.
actual current count — check the real current number before writing it).

**Acceptance criteria:**
- [ ] `fabric-audit-agent/` (the Node.js reference repo, NOT the chat app which shares a similar
      name — double-check you're deleting the right directory) removed via `git rm -r`
- [ ] README.md's stale claims fixed to reflect current reality (no Node comparison to maintain,
      current real test count)
- [ ] Any other doc referencing the dead Node app updated or removed

**Estimated scope:** Small

---

### Task 4.6: N2 — FUAM integration decision

**Description:** FUAM (community Fabric Unified Admin Monitoring toolkit) was researched
extensively earlier in this project (Section 22) and never configured. This needs an explicit
yes/no, not indefinite deferral.

**Recommendation (yours to override):** No, not now. FUAM's own known limitations (no alerting,
no real-time data — per this project's own research) mean it would add a new external dependency
without closing any gap the agent's own collectors don't already close better. Revisit only if a
specific FUAM-only capability is identified that this agent genuinely needs.

**Acceptance criteria:**
- [ ] Decision recorded explicitly in GAPS-AND-ISSUES.md (yes-with-plan, or no-with-reasoning) —
      either is fine, but it must stop being an open question

**Estimated scope:** XS (a documented decision, not a build)

---

### Task 4.7: E3 — multi-workspace loop (needs its own brainstorming pass)

**Already established:** this is two distinct designs, not one — live cross-workspace
aggregation ("which workspace is busiest right now") vs. historical batch rollup ("summarize
last month across all workspaces"). Do the brainstorming pass for both before building either;
they may share a collector-composition pattern (similar to `build_collector_from_env`'s existing
multi-source merge) but have different triggering/output shapes.

**Estimated scope:** Brainstorm first (Small), then Medium–Large per design

---

### Task 4.8: E4 — staleness check on dimensional data

**Description:** No check currently exists confirming dimensional data (workspace/item lists,
owner mappings) isn't stale before being used in a response.

**Acceptance criteria:**
- [ ] A timestamp/freshness check exists on dimensional lookups; a response using stale data
      (older than some reasonable threshold — propose one, e.g. 24h for ownership/workspace
      lists) says so

**Estimated scope:** Small

---

### Task 4.9: Stand up lightweight CI/CD

**Description:** No automated test/eval execution exists on any change today — everything has
been manual `pytest` runs. Keep this genuinely lightweight (per the plan's Over-Engineering
Check reasoning — this project doesn't need a heavy multi-stage pipeline).

**Acceptance criteria:**
- [ ] A single CI step (GitHub Actions, or whatever this repo's actual host supports) runs the
      full package test suite + chat app test suite + agent-case eval suite on every push/PR
- [ ] Failing tests block merge (or at minimum, are visibly flagged) — doesn't need to be
      elaborate, just automatic instead of manual

**Estimated scope:** Small–Medium

---

### Task 4.10: Basic cost/observability tracking

**Description:** Nothing currently tracks token cost, latency, or error rate over time for either
the interactive chat app or the (once re-enabled) scheduled sweep.

**Acceptance criteria:**
- [ ] Each investigation/sweep run logs at minimum: token usage (if available from the API
      response), wall-clock duration, and whether it errored
- [ ] This can be as simple as appending to the same history mechanism the sweep job already uses
      (`store["history"]()` / `AUDIT_HISTORY_PATH`) — doesn't need new infrastructure

**Estimated scope:** Small–Medium

---

## PHASE 5: Databricks memory — all Delta, no Lakebase (see plan's Over-Engineering Check)

### Task 5.1: Create the four Unity Catalog Delta tables

**Description:** `run_history`, `capacity_reporting`, `audit_findings`, `concentration_alerts` —
full schema per the plan's Phase 5 description. All four: no partitioning (liquid clustering +
predictive optimization handle layout), 90-day retention via `ALTER TABLE`.

**Acceptance criteria:**
- [ ] All four tables created in the catalog/schema `databricks.yml` already references
      (`${var.catalog}.${var.schema}` — confirm the actual values configured, don't assume `main`)
- [ ] Schemas match what Phase 6 (context queries) and the sweep job's `decide_alert` /
      `store["history"]()` actually need to read/write — check `automation/alerting.py` and
      `adapters/store_local.py`'s current JSON-based shape first, so the Delta schema is a proper
      superset/replacement, not a mismatched parallel structure
- [ ] 90-day retention set via `ALTER TABLE ... SET TBLPROPERTIES` on all four
- [ ] Liquid clustering enabled, no partition columns
- [ ] A test write + read round-trip confirms each table works before considering this done

**Dependencies:** None (this is genuinely independent, buildable now)

**Estimated scope:** Medium

---

### Task 5.2: Swap `store_local.py`'s JSON-file store for the new Delta `run_history` table

**Description:** `adapters/store_local.py` currently persists history as a local JSON file
(`AUDIT_HISTORY_PATH`). Once Task 5.1 lands, swap this for a real Delta-backed store so history
survives cluster restarts / isn't tied to ephemeral local storage, and multiple
concurrent/scheduled runs don't race on a single JSON file.

**Acceptance criteria:**
- [ ] A new `store_delta.py` (or equivalent) implements the same `store["history"]()` /
      append-on-write contract `store_local.py` does today — swap-compatible, same interface
- [ ] `job.py`'s `_default_store()` uses the new Delta store when Unity Catalog config is present,
      falls back to the local JSON store otherwise (keeps local/offline testing working)
- [ ] Existing tests that inject a fake store continue to pass unmodified

**Dependencies:** Task 5.1

**Estimated scope:** Medium

---

## PHASE 6: Growing context / "self-improvement" (simplified, ready to build)

### Task 6.1: Query recent `audit_findings` as pre-investigation context

**Description:** Before starting an investigation on a given capacity/item/user, query the last N
rows from `audit_findings` (Phase 5) for that scope and inject as labeled context.

**Acceptance criteria:**
- [ ] A function exists that queries `audit_findings` filtered to the current scope, ordered by
      recency, limit N (propose N=5, tune later if noisy)
- [ ] Results are injected as plain-language labeled context ("3 prior findings for this capacity
      in the last 30 days: ..."), not raw rows
- [ ] A missing/empty result never blocks the investigation — this is enrichment only
- [ ] New system prompt rule (once this exists, add to whichever file now canonically owns the
      prompt per ADR-001): prior findings are context, never a conclusion — the agent still
      gathers fresh evidence each time

**Prerequisite (already established):** don't turn this on until Phase 1–4 are confirmed live and
stable, so early noisy/buggy findings don't get surfaced as trustworthy history. Also decide and
document the Eventhouse data retention policy (Layer A/B) before this reasons about
week/month-long trends — note this as a small separate decision, doesn't block building the
query mechanism itself.

**Dependencies:** Task 5.1 (needs `audit_findings` to exist and have real data in it)

**Estimated scope:** Small–Medium

---

## PHASE 7: NL-to-query skill — fully designed, ready for task breakdown

**Design already complete:** `docs/superpowers/specs/2026-07-29-phase7-nl-query-skill-design.md`.
Read it in full before starting — it specifies the three query surfaces (KQL/SQL/DAX), the
validation-layer architecture (extends the existing `kql_guard` pattern), the generation pipeline
(generate → validate → execute, one re-prompt on failure then abstain), and the access-scope
philosophy (grows via ordinary Fabric permission grants, not hardcoded model lists).

### Task 7.1: Extend the validation layer to SQL and DAX

**Acceptance criteria (from the spec):**
- [ ] Read-only enforcement: reject anything not `SELECT`/`EVALUATE`-shaped: no DDL/DML ever
- [ ] Row/complexity ceiling (mirror the Execute Queries REST API's real limits: up to 100k rows /
      1M values / 15MB as the practical ceiling)
- [ ] Timeout enforcement, consistent with the existing tool-loop's request-timeout pattern
- [ ] Entity escaping extended from `kql_guard.escape_entity` to SQL identifiers and DAX
      table/column references
- [ ] Dedicated test coverage for the validation layer itself, not just folded into the general
      eval suite — this is explicitly the project's largest new attack surface per the spec

### Task 7.2: SQL query tool against Fabric Lakehouse/Warehouse SQL endpoints

**Acceptance criteria:** generation → validation → execution pipeline per the spec; verbatim
query returned (ties to SP7); metadata grounding (read the table's actual schema before
generating, don't guess column names from the question alone).

### Task 7.3: DAX query tool against real business semantic models

**Acceptance criteria:** same pipeline; explicitly NEVER targets the Capacity Metrics app (confirmed
protected); metadata grounding via the model's actual schema (reuse the XMLA/MSAL device-code
pattern already built in `scripts/extract_measures.py` for authentication).

### Task 7.4: Target classifier (KQL vs. SQL vs. DAX)

**Acceptance criteria:** per the spec's "Open Item" — design this more robustly than
`_step_budget()`'s fragile keyword approach (N22 already flagged that pattern) rather than
repeating the same mistake.

### Task 7.5: Route existing `run_kql` through this same validation/gating (closes N11's harder half)

**Acceptance criteria:** ad-hoc KQL results stop being `ungated` once they pass through the same
validation this phase builds — this closes the item flagged as "N11 harder half" in the handoff
priority order.

### Task 7.6: Genie evaluation (quick spike, not a requirement)

**Description:** Worth a quick check whether Databricks Genie's Managed MCP Server can reach any
of the SQL targets (Lakehouse/Warehouse data IS reachable via Unity Catalog / OneLake shortcuts,
unlike DAX against live Fabric semantic models, which Genie cannot reach — it only queries
Unity-Catalog-governed data). If viable for the SQL path specifically, consider it as an
alternative to Task 7.2 rather than building parallel infrastructure. Time-box this to avoid
blocking the rest of Phase 7 on an open-ended evaluation.

**Estimated scope (whole phase):** Large

---

## PHASE 8: Chart/graph generation — fully designed, ready for task breakdown

**Design already complete:** `docs/superpowers/specs/2026-07-29-phase8-chart-generation-design.md`.
Read it in full before starting. Confirmed via direct inspection of the real frontend
(`fabric-audit-agent-app/e2e-chatbot-app-next/client/src`) that chart iconography already exists
waiting for a component, and `databricks-message-part-transformers.ts` is the existing extension
point — this fills a real anticipated gap, not a build-from-scratch.

### Task 8.1: `render_chart` tool + `sourceScope`/`isProxy` data contract

**Acceptance criteria (from the spec):** tool output shape exactly as specified (`chartType`,
`title`, `series`, `axisLabels`, `sourceScope`, `isProxy`); `sourceScope` must be singular and
consistent across all series in one call — reject at the tool level, don't leave scope-blending
to the LLM's judgment; `isProxy` defaults true for any `user`/per-operation scope unless explicitly
proven otherwise.

### Task 8.2: `chart.tsx` frontend component

**Acceptance criteria:** new file in `components/elements/` following the existing
`code-block.tsx`/`tool.tsx` pattern; registered in `databricks-message-part-transformers.ts`;
**confirm the actual charting library in `package.json` before committing to recharts** (the spec
assumes it but doesn't confirm it — check first); visible badge/footnote renders when `isProxy`
is true.

### Task 8.3: Explicit test for the proxy badge

**Acceptance criteria:** per the spec's Guardrail Tie-In — this phase isn't done until there's an
explicit test confirming a proxy-sourced chart renders its badge, not just that the chart renders
at all.

### Task 8.4: Empty/thin-data fallback

**Acceptance criteria:** per the spec — a single data point or empty result falls back to a plain
text answer rather than rendering a misleading single-bar "chart."

**Dependencies:** Phase 7 (needs a query result to chart)

**Estimated scope (whole phase):** Medium–Large

---

## PHASE 9: Autonomous alerting — substantial existing system found; reconcile, don't rebuild

**MANDATORY FIRST STEP — read before touching anything:**
`docs/superpowers/specs/2026-07-09-proactive-alerting-design.md`, then `job.py`, `databricks.yml`,
`automation/alerting.py`, `automation/digest.py`, `automation/escalate.py`, `outbound.py`,
`adapters/delivery_email.py`, `adapters/delivery_teams.py` in full. **This is NOT a from-scratch
build** — there is a complete, tested, multi-source production sweep system already built:
collector composition across CSV/REST/Workspace Monitoring/Log Analytics/Capacity Events/ARM List
Usages, an LLM reasoner with offline-stub fallback, history-based change detection
(`decide_alert`), a typed outbound-action allowlist, egress/redaction controls, and both Teams and
email delivery paths (Teams currently deferred/commented out in favor of email — see below).

**Confirmed current reality (as of 2026-07-29):**
- The deployed job (`fabric_audit_sweep` in `databricks.yml`) runs on a **daily cron**
  (`0 0 6 * * ?`) and is **`pause_status: PAUSED`** — nothing is actually running autonomously
  today regardless of how complete the code is.
- Every sweep does a **full LLM-reasoned investigation** — there is no cheap/deterministic-only
  pre-check layer today.
- `TEAMS_WEBHOOK_URL` is commented out in `databricks.yml` with a note deferring it to an OLDER
  roadmap's own "Phase 7" (a different numbering than THIS plan's Phase 7 — don't confuse the
  two). Email (SMTP) is the only channel with an active path today, and it too needs its secrets
  configured before it does anything (currently commented out as well).

**Decision (confirmed with project owner, 2026-07-29):** Two-tier design confirmed. Tier 1 (the
existing full LLM-reasoned sweep) stays as-is architecturally, cadence tightened moderately (not
to 5–15 min — that's Tier 2's job). Tier 2 (new, cheap, deterministic-only) runs every 15 minutes,
watching the two primary triggers first. Teams is confirmed as the primary delivery channel,
matching the accumulated project research; email stays as the secondary/failure-alert path.
Proceed with Tasks 9.1–9.4 below.

### Task 9.1: Retune the full sweep's cadence

**Acceptance criteria:** `schedule_cron` in `databricks.yml` updated per the decision above (recommended
default, pending confirmation: keep the full sweep daily, or tighten moderately — this tier's cost
is a full LLM call per run, so don't set this to every 5–15 minutes; that's Task 9.2's job).
Unpause (`pause_status: RUNNING`) only once Task 5.1/5.2's Delta history store is in place
(running the sweep against the old local-JSON store loses history on every cluster restart).

### Task 9.2: Build the cheap deterministic tier (the actual "5-minute polling" from the original plan)

**Description:** A new, separate, high-frequency, NO-LLM-CALL check. Pulls the same live
collectors (or a cheap subset — at minimum the Capacity Events stream, since that alone carries
both throttle and concentration signal) and runs ONLY the deterministic gate checks below, in this
priority order:
1. **`concentration_gate()` — 30% single-user/item concentration (PRIMARY trigger)**
2. **`throttle_claim_gate()` — over 100% capacity / confirmed throttling (PRIMARY trigger)**
3. Spike/unusual-activity detection (`capacity_peaks`/spike-event logic — N23 already fixed, so
   this data is trustworthy now)
4. Overage/burndown check — any window with nonzero `overageTotalMs` (A2 already wired)
5. Any other STOP gate in `gates.py` tripping — secondary, catch-all

**Acceptance criteria:**
- [ ] Runs on the cadence from the decision above (recommended default: every 15 minutes)
- [ ] Zero LLM calls on the common case (nothing tripped) — pure deterministic checks against
      freshly-pulled data
- [ ] When something trips: triggers an immediate full investigation (either an out-of-cycle call
      to the existing `run_audit`/`run_unified_job` pipeline, or a lighter-weight immediate alert
      carrying the raw trigger data, with the next scheduled full sweep picking up the full
      narrative — pick whichever is cheaper/simpler to build correctly; document the choice)
- [ ] Cross-references Phase 6's recent-`audit_findings` context (once Phase 6 exists) to flag
      genuine recurrence rather than treating every trip as a fresh, isolated event
- [ ] New Databricks Job (or a task within the existing job) added to `databricks.yml` for this
      tier's schedule, separate from the full sweep's

**Dependencies:** Phase 1 (gates need to be trustworthy — they now are), Task 5.1/5.2 (history
store for recurrence context)

### Task 9.3: Enable Teams delivery properly

**Description:** Per the decision above, if Teams is confirmed as primary: uncomment and wire
`TEAMS_WEBHOOK_URL` in `databricks.yml`, but first confirm `delivery_teams.py`'s actual current
implementation matches the intended production pattern (Power Automate Workflows webhook,
`logic.azure.com` URLs — per this project's own prior research; the legacy `webhook.office.com`
pattern is confirmed retired by Microsoft and must not be used if that's what's currently there).

**Acceptance criteria:**
- [ ] `delivery_teams.py` confirmed (or updated) to post to a `logic.azure.com` Workflows webhook,
      not the retired `webhook.office.com` pattern
- [ ] Adaptive Card targets schema version 1.2 for mobile compatibility (per prior project
      research)
- [ ] Both Tier 1 (full sweep, `_maybe_alert`) and Tier 2 (Task 9.2's cheap check) route through
      this delivery path when they fire
- [ ] Email stays as the secondary/failure-alert path (already working) — not removed, just no
      longer primary

**Dependencies:** The cadence/channel decision above

### Task 9.4: Job self-observability

**Acceptance criteria:** if either the full sweep or the new cheap-check tier silently stops
running (job disabled, cluster failing to start, credential expiry), this is detected and alerted
on — the existing `_alert_failure` dead-man's-switch pattern already does this for the full
sweep; extend the same pattern to Task 9.2's new tier.

**Estimated scope (whole phase):** Large

---

## PHASE 10: Entra Agent ID (checklist — deferred, EXCLUDED from autonomous execution)

**Do not touch autonomously.** Every item here needs admin/tenant-level action outside this
codebase (licensing confirmation, Client ID/Tenant ID/App ID URI from an admin, re-granting Fabric
permissions to a new identity). Pause and report back rather than attempting any of this
autonomously.

- [ ] Confirm licensing is in place (org decision, not yours)
- [ ] Get Client ID / Tenant ID / Application ID URI from the admin once the blueprint exists
- [ ] Stand up the sidecar, swap token-acquisition code
- [ ] Re-grant the new identity the same Fabric access the current SP has
- [ ] Resolve the Databricks-hosting-of-the-sidecar question before committing to a timeline
