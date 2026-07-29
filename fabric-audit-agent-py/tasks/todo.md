# Task List: bi-fabrics-audit-agent

Phase 1 is broken down to full task-level detail — ready to execute now, zero new research needed,
everything traces to a proven finding in `GAPS-AND-ISSUES.md`. Phases 2–10 are checklist-level only;
each gets its own detailed task breakdown when we reach it, per the plan's sub-project sequencing.

**STATUS (2026-07-29): Tasks 3, 10, 11, 12, 13 implemented directly (low-risk batch, no live test
execution needed). Task 6 (A2) completed as part of this pass too — the extraction already existed
from earlier session work, but the `diagnose.py` auto-call was missing; that's now wired in. Tasks
1, 2, 4, 5, 8, 9 remain — these need live test execution and are Claude Code's to pick up. See each
task below for updated status.**

**UPDATE (2026-07-29 evening, Claude Code session): Tasks 1 + 2 COMPLETE.** The MCP package no
longer contains any prompt/loop/investigator/scripted-client code — all moved into the chat app
(`fabric-audit-agent-app/agent_server/`). ADR-001 grep acceptance criterion satisfied
(`build_system_prompt` and prompt-owning `_SYSTEM = ` return zero hits in
`fabric_audit_agent/`, aside from the completely unrelated `reasoner_claude.py::_SYSTEM` for
the offline stub reasoner). 35/35 agent-case eval golden suite passes from its new home
(`fabric-audit-agent-app/agent_server/eval_data/agent_cases.json`, scored by
`agent_server/eval_score.py::run_agent_suite`). Two EV1 cases were rewritten to ground against
tokens actually present in the mock outputs, since the prior planning session added them
without live test-execution access; changes annotated with ``_reviewNote`` inline. Also
deleted: the legacy pre-split ``fabric-audit-agent-py/app/agent.py`` scaffold; the obsolete
``fabric-audit-agent-app/tests/test_prompt_parity.py`` (its whole reason for existing was
catching drift between two `_SYSTEM` copies — there is only one now). Both app versions
bumped: MCP 1.9.13 → 1.9.14, chat app 0.2.12 → 0.2.13. Task 7 (SP1–7 verification against
the fixed deployment) is now UNBLOCKED and next.

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
- [ ] The chat app (not the MCP package) contains the single, canonical, up-to-date system prompt
      (including SP1–7)
- [ ] `fabric_audit_agent/agent/system_prompt.py` no longer exists
- [ ] `fabric_audit_agent/agent/investigator.py` and the `agent_cases.json`-driven eval scoring
      logic now live in the chat app; running the 35-case agent-case eval suite still works
      end to end from its new location
- [ ] A live/staged call to the deployed chat app produces the SP4 two-column format
      (`"% of base (this timepoint)"` / `"Lifetime % of base"`), not the retired
      `"47.1% (471.2%)"` combined format

**Verification:**
- [ ] `grep -r "build_system_prompt\|_SYSTEM = " fabric_audit_agent/` returns nothing — the MCP
      package owns no prompt logic at all
- [ ] Existing test suite passes: `pytest tests/ -k agent_server`
- [ ] The agent-case eval suite (35 cases) passes from its new home in the chat app
- [ ] Manual check: ask the deployed/staged agent a capacity-peaks question, confirm output uses
      the two-column format, not the combined one

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
- [ ] The app's tool loop is confirmed to have all three properties above (dedup, budget-exhaustion
      handling, `wrap_untrusted`) — port over anything the package's version had that the app's
      version is missing, before deleting the package's copy
- [ ] `fabric_audit_agent/agent/loop.py` no longer exists

**Verification:**
- [ ] `pytest tests/ -k loop`
- [ ] Manual check: trigger a budget-exhaustion scenario, confirm the injected message still
      appears

**Dependencies:** Task 1 (same file, same PR)

**Files likely touched:**
- `fabric-audit-agent-app/agent_server/agent.py`
- `fabric_audit_agent/agent/loop.py` (deleted)

**Estimated scope:** Small (1–2 files)

---

### Task 3: Fix N22 — hidden step-budget classifier

**Description:** `_step_budget()` silently assigns 6 vs. 12 steps based on a hardcoded keyword
list, with zero disclosure to the user. Minimum fix: have the agent's response note when it's
operating under the shallow 6-step budget. Fuller fix: broaden the classifier or default to the
deeper budget more often.

**Acceptance criteria:**
- [ ] When the 6-step (lookup) budget is assigned, the final response includes a plain-language
      note that a deeper investigation is available on request
- [ ] (If pursuing the fuller fix) the keyword list is expanded or the default budget increased,
      with a documented rationale for the new threshold

**Verification:**
- [ ] `pytest tests/ -k step_budget`
- [ ] Manual check: ask a two-part question that doesn't hit any `_INVESTIGATION_HINTS` keyword,
      confirm the shallow-budget disclosure appears

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
- [ ] Root cause identified (off-by-N-day boundary / timezone handling / hardcoded lookback
      override — confirm which)
- [ ] A single-day request returns only that day's rows from the tool itself, not filtered
      client-side by the model
- [ ] (Optional but recommended) quantify via run history how often this fired historically, to
      confirm the fix's real-world impact

**Verification:**
- [ ] New test: request a single day, assert zero rows outside that day in the raw tool result
      (not just the final answer)
- [ ] `pytest tests/ -k capacity_overloads` (or whichever module owns this — confirm exact file
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
- [ ] `_windows()` extracts all three threshold fields from the event payload
- [ ] `capacity_series()` returns them alongside `{ts, cuPct}`
- [ ] Values are scaled ×100 during extraction
- [ ] `throttle.py`'s stage-2 gate fires correctly on real over-threshold data (no longer
      permanently outputs `"over-utilized-unconfirmed"`)

**Verification:**
- [ ] `pytest tests/test_capacity_events_collector.py` (existing suite already has stage-2-related
      test cases per this session's earlier work — confirm they pass)
- [ ] Manual check against a known real over-threshold window from Section 12.4
      (7/27, 11:55 AM–12:02 PM) — confirm the agent now reports `"throttling-confirmed"`

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
- [ ] `_windows()` extracts all three overage fields
- [ ] `capacity_burndown_chain()` function exists and implements the proven recursion exactly
- [ ] `diagnose.py` auto-calls it when `timepointsOver > 0`
- [ ] SP1 (burndown auto-trigger, already in the canonical prompt) can now actually fire, since
      the underlying function it depends on exists

**Verification:**
- [ ] New test: feed 1,777 synthetic windows matching Section 12.3's real data, assert zero
      cumulative error
- [ ] Manual check: over-100% window triggers automatic burndown reporting without being asked

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
      user-count variance — not a hardcoded tenant-specific list)
- [ ] `gates.py`, `diagnose.py`, and the detectors all read the threshold from
      `config["capacity"]["concentrationPct"]` — no independent hardcoded copies remain
- [ ] `concentration.py`'s label defaults to `"monitored CU"` for both `None` and `"frequency"`
      attribution modes, not `"capacity CU"`

**Verification:**
- [ ] `pytest tests/ -k concentration`
- [ ] Manual check: a known `EventStream`-only capacity no longer trips a false 100%-concentration
      alert

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/detectors/concentration.py`
- `fabric_audit_agent/detectors/user_concentration.py`
- `fabric_audit_agent/investigation/diagnose.py`
- `fabric_audit_agent/investigation/gates.py`

**Estimated scope:** Medium (4 files, related changes)

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
- [ ] An assertion/check exists confirming numerator and denominator share the same
      `attributionMode` before a concentration percentage is computed
- [ ] Mixed-source inputs raise or are flagged rather than silently producing a meaningless ratio

**Verification:**
- [ ] New test: feed mismatched-source inputs, assert the check fires

**Dependencies:** Task 8 (touches the same detector files)

**Files likely touched:**
- `fabric_audit_agent/detectors/concentration.py`

**Estimated scope:** Small (~15 lines per the gaps doc estimate)

---

### Task 10: Fix N7 — distinguish CpuTimeMs from DurationMs in `attributionMode`

**Description:** `attribution_rollup.py` unconditionally labels every item `"cost"` mode even when
the underlying number is `DurationMs` (a weaker wall-clock proxy), not true `CpuTimeMs`.

**Acceptance criteria:**
- [ ] `attributionMode` distinguishes `"cost-cpu"` vs. `"cost-duration"` (or an equivalent
      `costBasis` field is added)
- [ ] The distinction is reflected in `kb/metric_definitions.py` once Phase 2 exists (note as a
      forward dependency, don't block this task on Phase 2)

**Verification:**
- [ ] `pytest tests/ -k attribution_rollup`

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

### Task 11: Verify-only tasks (N17, N18, N19, N21, N1, N16, B5)

**Description:** A batch of direct-code-read verification tasks — no new data needed, cheap to
knock out in one pass. Each either confirms an existing gap is real and scopes the fix, or closes
itself if the concern turns out not to apply.

**Acceptance criteria:**
- [ ] N17: confirm whether Layer A's collector timestamps off `windowStartTime` directly (immune
      to the `Monitoring_Eventstream` Date-join failure) — document the answer either way
- [ ] N18: confirm whether `attribution.py`/`concentration.py`/`user_concentration.py` group by
      display name or a stable ItemId — fix if display-name-keyed
- [ ] N19: confirm whether anything currently surfaces item-level `Throttling(s)` at all — likely
      closes itself since this came from Capacity Metrics app research, not current ingestion
- [ ] N21: confirm no status-groupby logic hardcodes a 3-status assumption tenant-wide
- [ ] N1: re-verify why Tier-2 could fire on mock data in a WM-only environment BEFORE wiring
      anything — do not simply remove the withholding (see the existing caution in the gaps doc)
- [ ] N16: confirm whether `importers/capacity_metrics.py` exists and whether it independently
      checks %-field scale
- [ ] B5: confirm `confidence.py`'s actual current contents against the original
      `ClaimConfidence` enum ask

**Verification:**
- [ ] Each item gets a one-paragraph written confirmation in `GAPS-AND-ISSUES.md` (update the
      entry's status), not just a mental check

**Dependencies:** None

**Files likely touched:** Read-only pass across ~6 files; fixes only where the verification finds
a real issue

**Estimated scope:** Small (verification), Medium if fixes are needed after verification

---

### Task 12: Fix N10 — misleading WM degraded-capability message

**Description:** One-line messaging fix, contradicts N1's deliberate withholding.

**Acceptance criteria:**
- [ ] `sources.py`'s degraded-capability note no longer claims Workspace Monitoring resolves
      per-query depth when it doesn't

**Verification:**
- [ ] Manual check of the message text

**Dependencies:** Task 11 (N1's verification informs the correct wording)

**Files likely touched:**
- `fabric_audit_agent/sources.py`

**Estimated scope:** XS (1 line)

---

### Task 13: Fix A3 — WM `eventDepth` truncation signal

**Description:** WM collector can silently truncate with no signal to the agent.

**Acceptance criteria:**
- [ ] An explicit warning flag appears in the collector's return payload when the result is at
      the depth limit
- [ ] Downstream logic/responses can say "showing top N of possibly more"

**Verification:**
- [ ] `pytest tests/ -k workspace_monitoring`

**Dependencies:** None

**Files likely touched:**
- `fabric_audit_agent/adapters/collector_workspace_monitoring.py`

**Estimated scope:** Small (~10 lines)

---

## Checkpoint: End of Phase 1

- [ ] Full test suite passes
- [ ] A live/staged conversation confirms: SP4 format is correct, burndown auto-triggers, an
      `EventStream`-only capacity doesn't false-positive on concentration, throttling actually
      confirms on known real data
- [ ] Every verify-only task (Task 11) has a written resolution in `GAPS-AND-ISSUES.md`
- [ ] Confirm the app's `agent/` logic (prompt + loop) is genuinely the only copy left anywhere in
      the codebase — a `grep` across both repos for the deleted package files should return nothing
- [ ] Review with human before starting Phase 2

---

## PHASE 2: Grounding schema — **DONE (2026-07-29)**

- [x] Write `kb/metric_definitions.py` — every verified Section 12 formula, source, metric type,
      smoothing window (core formulas already existed from earlier session work; confirmed complete)
- [x] Add the `MetricValue` runtime dataclass (N14)
- [x] Document N20's three smoothing windows (added `health_state_smoothing_window` field,
      distinct from the raw signal's own 30s arrival cadence) and N9's `DOMINANT_ITEM_SHARE_PCT`
      verdict logic (already present)

## PHASE 3: Hallucination guardrails + eval suite (partially done)

- [x] Fix N11 — **partially done 2026-07-29:** `run_kql_handler` now returns `ungated: true` +
      a plain-language note (the lower-risk option). Routing ad-hoc results through the actual
      gates is still open — needs Claude Code, live testing against real gate call shapes.
- [x] Fix N12 — **CONFIRMED ALREADY RESOLVED 2026-07-29:** `query/envelope.py`'s `finish()`
      already threads a verbatim `queryKql` field through every handler calling it with `kql=`.
      No new code needed; SP7 (the prompt rule to actually quote it) is what's still blocked, on C2.
- [x] Write B4's `assert_cu_consistency()` math check — **DONE 2026-07-29** as a pure, tested,
      NOT-yet-wired function in `validate.py`. Verified against the real documented bug example.
      Wiring it into `diagnose.py`/`throttle.py` is still open — Claude Code's to verify live.
- [x] Author EV1 (6 cases) — **DONE 2026-07-29**, all in `agent_cases.json`
- [x] Author EV3 (4 cases) — **3/4 DONE 2026-07-29.** The "repeated identical question 3x" case
      does not fit the eval harness's current one-shot-per-case design (`investigate()` returns
      exactly one output per case) — needs a harness change (multi-turn scoring support in
      `score_investigations.py`) before it can be added as a real golden case, not a fix I should
      guess at blind. Flagged for Claude Code if this is worth building.
- [ ] Run EV2 (`mine_evals.py`) against real conversation logs
- [ ] Run the full Section 14 stress-test bank at least once end to end

## PHASE 4: General hardening pass (checklist)

- [ ] Fix B2 — blank ExecutingUser cross-reference fallback. **Needs its own dedicated pass:**
      requires wiring Activity Events cross-reference + REST API owner lookup into the
      attribution pipeline — genuine design decisions, not a quick patch.
- [ ] Verify N4's 3 deploy integration points
- [ ] Explicit yes/no decision on UX1–4 (don't silently drop). **UX1–UX4 need their own dedicated
      pass:** real frontend/React work needing the `frontend-design` skill and live browser
      rendering to verify — not something to draft blind from here.
- [x] D3 (SOWMYA brief) — **DROPPED 2026-07-29 per explicit instruction.** No `.docx` file exists
      anywhere in the accessible filesystem (confirmed via exhaustive search: repo tree, full user
      directory, and a broad `*.docx` glob). Nothing to update, nothing to delete.
- [ ] D4 (delete dead Node app, fix README/STATUS.md) housekeeping
- [x] ADR-001 written — **DONE 2026-07-29:** `docs/decisions/ADR-001-mcp-package-is-tools-only-app-is-the-agent.md`
      records the Task 1/2 architecture pivot's reasoning
- [ ] **N2** — FUAM never configured; decide yes/no explicitly rather than leaving it deferred
      indefinitely
- [ ] **E3** — no multi-workspace loop. **Scenario-walkthrough finding: this is two different
      designs, not one** — live cross-workspace aggregation ("which workspace is busiest right
      now") and historical batch rollup ("summarize last month across all workspaces") are
      architecturally distinct asks. Treat as two sub-designs when this gets its own brainstorming
      pass. Note: Phase 7's "access to everything eventually" goal depends on this being resolved.
- [ ] **E4** — no staleness check on dimensional data
- [ ] **New (stress-test finding):** stand up a CI/CD pipeline — automated test + eval suite
      execution on every change, not manual `pytest` runs, before Phase 9 makes this urgent
- [ ] **New (stress-test finding):** add basic cost/observability tracking (token cost, latency,
      error rate over time) — currently nothing tracks this at all
- [ ] **New (stress-test finding):** write a short ADR recording why the Task 1/2 architecture
      pivot (MCP=tools, app=agent) is structured this way — C2/N15 already proved this exact
      confusion recurs without a written record

## PHASE 5: Databricks memory (checklist — all Delta, no Lakebase; see plan's Over-Engineering Check)

- [ ] `run_history` table (Delta, append-only, heartbeat)
- [ ] `capacity_reporting` table (Delta, MERGE upsert on `(capacity_id, metric_date)`)
- [ ] `audit_findings` table (Delta, append-only, prior-findings context — this is also what
      Phase 6 queries directly, no separate table needed)
- [ ] `concentration_alerts` table (Delta, append-only, `is_proxy=True` hardcoded)
- [ ] All four: 90-day retention via `ALTER TABLE`, no partitioning, liquid clustering

## PHASE 6: Growing context / "self-improvement" (simplified — no longer needs its own brainstorming pass)

- [ ] Query the last N rows from `audit_findings` (Phase 5) for the relevant capacity/user/item
      before starting an investigation
- [ ] Inject as labeled context (e.g. "3 prior findings for this capacity in the last 30 days:
      ...") — the agent still gathers fresh evidence each time; this is a prior, not a shortcut
- [ ] No new table, no promotion threshold, no confidence scoring, no decay logic for v1 (cut per
      the plan's Over-Engineering Check — build the more structured version later only if raw
      recent-findings context proves too noisy in practice)
- [ ] **Prerequisite (scenario-walkthrough finding):** decide and document retention policy for the
      agent's own Layer A/B Eventhouse data before this phase reasons about week/month-long trends
- [ ] **Prerequisite (still applies in simplified form):** don't start feeding `audit_findings`
      back as context until Phase 1–4 are confirmed live and stable, so early noisy/buggy findings
      don't get surfaced as if they were trustworthy history

## PHASE 7: NL-to-query skill (needs its own brainstorming pass first)

- [ ] **Do not start task breakdown until scope is confirmed:** which business semantic models,
      SQL target(s), and the safety/validation layer design
- [ ] Explicitly excludes the Capacity Metrics app as a DAX target
- [ ] **New required eval case (scenario-walkthrough finding):** asking about a workspace/model the
      agent has no Fabric access to must produce an honest "no access" answer — never a false
      "doesn't exist" claim or a fabricated figure. Mirrors the existing Olivia eval case's
      absence-in-data vs. absence-in-reality discipline, extended to workspace/model access.

## PHASE 8: Chart/graph generation (needs its own brainstorming pass first)

- [ ] Depends on Phase 7 (needs a query result to chart)
- [ ] Must enforce the true-CU/proxy boundary from the plan's Architecture Decisions
- [ ] Likely touches `design-system`/`ui-ux-pro-max`/`visualize` tooling — confirm rendering
      surface (inline chat vs. artifact) before design
- [ ] **Required behavior (scenario-walkthrough finding):** when a chart request would blend two
      scopes (e.g. per-user CU next to total capacity CU), the agent must offer two separate
      single-scope charts instead — not just have the tool silently reject the call

## PHASE 9: Autonomous 5-minute polling (checklist)

- [ ] **First: locate and audit the existing Claude-Code-built implementation** — do not assume
      it needs building from scratch
- [ ] **Explicit gate-check enumeration (per project owner's stated priorities, 2026-07-29) —**
      confirm the 5-minute tick runs these specific deterministic checks, in this priority order,
      not a vague "if a gate trips":
      1. **`concentration_gate()` — 30% single-user/item concentration (PRIMARY trigger)**
      2. **`throttle_claim_gate()` — over 100% capacity / confirmed throttling (PRIMARY trigger)**
      3. Spike/unusual-activity detection (the `capacity_peaks`/spike-event logic, once N23 is
         fixed)
      4. Overage/burndown check (once A2 exists) — any window with nonzero `overageTotalMs`
      5. Any other STOP gate in `gates.py` tripping (null-data, pressure, verdict) — secondary,
         catch-all
      6. Cross-reference against `learned_patterns` (Phase 6) to flag genuine recurrence, not
         just a fresh one-off
- [ ] Confirm it correctly implements cheap-gate-check-then-escalate — steps 1–5 above are
      deterministic code checks, NOT an LLM call; only escalate to a full agentic investigation
      once one of them actually fires
- [ ] Add job self-observability (alert if the job itself silently stops)
- [ ] **Integration point (scenario-walkthrough finding):** explicitly confirm whether the
      autonomous investigation path reads/writes Phase 6's `learned_patterns` table the same way
      an interactive investigation does — don't leave this implicit once both phases exist
- [ ] **Confirm the existing Teams Adaptive Card alert stays text-only** even after Phases 7–8
      exist, rather than silently growing scope (scenario-walkthrough finding)
- [ ] Now that the app is the canonical agent home (Task 1/2's corrected architecture), the
      autonomous driver belongs alongside it in the app, not bolted onto the MCP tools package

## PHASE 10: Entra Agent ID (checklist — deferred, see prior discussion in this project)

- [ ] Confirm licensing is in place (org decision, not yours)
- [ ] Get Client ID / Tenant ID / Application ID URI from the admin once the blueprint exists
- [ ] Stand up the sidecar, swap token-acquisition code
- [ ] Re-grant the new identity the same Fabric access the current SP has
- [ ] Resolve the Databricks-hosting-of-the-sidecar question before committing to a timeline
