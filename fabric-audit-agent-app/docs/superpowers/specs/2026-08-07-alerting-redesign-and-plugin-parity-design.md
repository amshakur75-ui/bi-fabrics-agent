# Alerting Redesign + Plugin Parity — Design Spec

**Date:** 2026-08-07
**Status:** approved (brainstorming), decomposed into 6 sequenced sub-plans.

## Goal

Close the gap between what `tightening.md` (Parts 0–26) and the KQL plugin describe and what the
code actually does. Two independent audits (tightening.md-vs-code, and plugin-vs-code) found that
the **plugin-adoption / resolve / stats / export infrastructure is largely built**, but the
**behavioral noise-reduction redesign the project was written for is largely not** — plus a set of
real production bugs and plugin-depth gaps. This spec is the design for fixing all of it, as a
sequence of independently-testable sub-plans.

## The core principle (from tightening.md Part 5, now enforced in DATA not just prompt)

> CU answers exactly one question — **is the capacity in trouble** (throttle / pressure / overage /
> verdict). Log Analytics / monitored activity answers **WHO / WHAT / WHY**, on its own honest
> terms, never blended into a capacity percentage.

Today the *system prompt* enforces this but the *detectors and daily summary* do not: the CU-blended
per-user concentration metric still fires, and the digest still leads with CU%. The redesign moves
this discipline into the code path.

## Load-bearing decisions (approved)

1. **Remove-and-replace** the CU-blended per-user concentration alert (`user_concentration.metric()
   = share × capPct / 100`). It is retired entirely, replaced by absolute-fact detectors. (Part 1d
   option i.)
2. **Query-text dependency verified in Phase 0** of Sub-plan 1 — the query-SHAPE detector and
   audit-before-execute both need the real executed query text (the "B1" capture). Confirm it exists
   before building on it; if absent, capturing it is the first task.
3. **Concentration threshold is a low-stakes config knob**, not a noise source. The per-USER alert
   that used it is removed. Where a concentration signal remains — item-level dominance as an
   *investigation trigger* and the capacity *verdict* — it becomes one configurable value
   (`config["capacity"]["concentrationPct"]`, default **60** for the item signal per the plugin's
   evidence), only gating whether the agent *looks*, never what it *reports*.
4. **Audit-rule porting = domain-relevant subset** — port BEST / HINT / relevant PERF+CORRECT rules;
   explicitly skip the App-Insights-only TELEMETRY rules (out of domain), with a logged reason.
5. **Deferred (NOT in this program):** HR enrichment (`hr-loader`, HCMIF xlsx, coverage-gating) and
   outbound Teams-push enablement (Phase 10). Both stay read-only-deferred as before.

## Global constraints (every sub-plan inherits these)

- **Read-only toward Fabric / Power BI / Azure.** No writes / refreshes / scale. New detectors only
  read telemetry.
- **STANDING RULE (tightening.md):** blast-radius review BEFORE and AFTER every change — grep every
  caller/importer/consumer of what changes; check sibling files for the same pattern; run the FULL
  suite, not just the touched file. Record it.
- **`agent.py` + `loop.py` are twins** — any loop change lands in BOTH via the shared
  `agent_server/loop_hooks.py`; never inline a hook.
- **`kql_guard.py` signatures are frozen** — new audit rules live in `query/kql_audit_rules.py`.
- **`system_prompt.py` is single-sourced (ADR-001)** — no second copy.
- **camelCase data keys / snake_case identifiers; nullish `x if x is not None else d`** (never falsy
  `or`); numeric guards reject bool + non-finite; uniform error envelope (handlers never raise).
- Full suite baseline: **1775 passed / 55 subtests**. Every task keeps it green or higher.

---

## Sub-plan 1 — Alerting redesign (the noise-reduction core) — HIGHEST PRIORITY

**Files:** `detectors/user_concentration.py` (retire), `detectors/absolute_cost.py` (new),
`detectors/query_shape.py` (new), `investigation/query_fingerprint.py` (new), `detectors/__init__.py`,
`automation/tier2_check.py`, `automation/sweep_delivery.py`, `sla.py`, `accountability.py`,
`adapters/collector_merge.py` / `mappers/capacity.py` (FIX 0), `config.py`.

**Phase 0 (gate):** confirm the executed **query text** is captured on events (the B1 dependency).
Inspect `spike_events`/`raw_events`/`collector_events_la.py` for an `eventText`/`queryText` field. If
present → proceed. If absent → the first task is to capture it (read-only, from Log Analytics
`PowerBIDatasetsWorkspace` query text) — the shape detector cannot exist without it.

- **1a — Absolute-cost detector** (`detectors/absolute_cost.py`): flag any single operation whose
  duration ≥ `N` seconds OR CU-seconds ≥ `M` (config thresholds), independent of any share. Pure
  Log-Analytics fact. Emits `activity.slow-operation` findings with user/item/operation/durationMs/
  cuSeconds and a one-off-vs-recurring flag (set by 1b).
- **1b — Query-SHAPE recurrence** (`investigation/query_fingerprint.py` + `detectors/query_shape.py`):
  `fingerprint(queryText)` normalizes a KQL/DAX/MDX query to a shape hash (strip string/number
  literals, whitespace, parameter values; keep operators/functions/structure). `query_shape.py`
  clusters events by shape across the window (and, when history is available, across days/users) and
  emits `activity.recurring-shape` when a shape recurs ≥ `K` times from ≥ `2` distinct users/days —
  a model/report design problem, not a person problem.
- **1c/1d — Retire the CU-blend:** delete `user_concentration.py`'s `metric()` share×capPct path and
  its `capacity.user-concentration` finding. Remove its Tier-2 `_check_concentration` per-user branch
  and the `_ATTR_CHECKS` hysteresis that existed only to dampen its flood. Keep the **item-level**
  concentration signal (`detectors/concentration.py`) but reframed: it triggers investigation, and
  its threshold reads the single config value (default 60).
- **0 — Hysteresis** is now moot for the removed detector; the remaining item signal keeps Tier-2's
  existing persistence gate.
- **FIX 0** (verdict "optimize" unreachable): `collector_merge.py` merges refreshes to top-level
  `facts["refreshes"]`, but `detectors/capacity.py` reads `facts["capacity"]["refreshes"]`. Nest it
  (or teach the detector to read both), so the optimize branch fires on the merged/App path.
- **FIX 3** (`sla.py` + `accountability.py`): add a check-type exclusion so throttle/pressure/overage
  findings never get "open N runs with no resolution" SLA-breach language (they auto-resolve).

**Tests:** absolute-cost fires/doesn't at the boundary; fingerprint is stable across literal changes
and distinct across structural changes; shape recurrence needs ≥2 users; the CU-blend finding no
longer appears anywhere; FIX 0 optimize verdict fires on a merged-refresh fixture; FIX 3 excludes
capacity types.

## Sub-plan 2 — Bad-activity taxonomy detectors (Part 12)

**Files:** `detectors/refresh.py` (expand), `detectors/query_antipatterns.py` (new or extend
`dax.py`), `detectors/xmla_errors.py` (new), `detectors/__init__.py`, `investigation/baseline.py`
(wire as detector), `config.py`.

- **Refresh sub-causes:** classify credential/auth, gateway offline/overloaded, source timeout
  (`Execution Timeout Expired` / -2147467259), concurrency-limit, constraint/duplicate-key, and
  silent-success (status success but rows/bytes = 0). Each a distinct `refresh.<cause>` finding.
- **Query anti-patterns:** fact-vs-dimension `FILTER()` distinction; high storage-engine query count
  (≥ threshold); the flagship **MDX GrandTotal / Hierarchize / CrossJoin** shape (works with the
  1b fingerprint). Keep the existing nested-iterator + bidirectional + auto-date detectors.
- **Category-3 XMLA/connection errors:** taxonomy for XMLA "Bad Request" / TMSL / connection-auth,
  WITH the "session moved to another node" **suppression** (a benign transient, never a finding).
- **Category-4:** cluster of long-running queries (> 5 min) against the same item; wire
  `investigation/baseline.py` per-user deviation as a standing detector (own-history, not
  capacity-blended).
- **"Explicitly NOT bad":** suppress normal multi-visual dashboard bursts.

## Sub-plan 3 — Daily-summary + card/notification redesign (Parts 5, 6, 7, 13, 14, 15)

**Files:** `automation/sweep_delivery.py` (FIX Part 7), `automation/daily_summary.py` (rebuild),
`adapters/delivery_webhook.py` (`build_card`), `automation/tier2_check.py` (`_facts_for`,
`_investigate_query`), `agent_server/system_prompt.py` (pivot rule), frontend
`notification-center.tsx`.

- **Part 7 FIRST (real prod bug):** `sweep_delivery.py:188 if ticket_writer and chat_id:` — a
  `chat_writer` exception silently skips the ticket write forever. Decouple: always write the ticket
  row; a chat-write failure degrades the deep-link to a root `?query=` link (as tier2 already does),
  never drops the ticket. Surface the chat-write failure to the Sub-plan-4 health report.
- **Daily-summary rebuild (13/14):** demote CU% from headline to a single one-line cross-reference;
  body = the taxonomy bad-findings; add a **Top-10 users** section (plain ranking by CU-seconds/op
  count, no capacity %); **refreshes get their own section**, never interleaved; recurring-shape vs
  one-off subsections; a **"no significant issues found"** taxonomy fallback that does NOT fall back
  to CU.
- **Cards (5/15):** `build_card`/`_facts_for` add capacity + attribution as **separate** facts
  ("Capacity during this window: {peakCuPct}% (no throttle)" as its own fact, never computed FROM
  the attribution number) and a **"When / first noticed"** timestamp fact.
- **Investigation pivot (6):** when the anchored ±30-min window does not corroborate the named
  user/finding, PIVOT — search that user's own history broadly (7–30d) for their actual anomaly —
  rather than widening the same empty window. Deep-link prompt + a durable system-prompt rule.

## Sub-plan 4 — Infra, health & wiring integrity (Parts 3, 4, 16, 17, 25e)

**Files:** `automation/health.py` (new), `automation/tier2_check.py`, `adapters/collector_*.py`,
`adapters/chat_store_lakebase.py`, `adapters/delivery_webhook.py`, `adapters/ticketing.py`,
`conversation.py`, `outbound.py`, `databricks.yml`, `resolve/catalog.py` (wire the invariant).

- **Health report (4):** a per-detector / per-delivery-path health surface (not just a heartbeat
  timestamp) — which collectors succeeded, which detectors ran, which deliveries landed — surfaced
  in the digest and queryable. Wire `assert_model_map_invariant()` into startup (today tests-only).
- **Wiring integrity (3):** classify the ~107 `except Exception` blocks FAIL-OPEN-SAFE vs
  FAIL-OPEN-DANGEROUS; promote DANGEROUS ones to increment the health flag instead of silent
  `print`. Produce `docs/WIRING-MAP.md`.
- **Lakebase auth (16):** stop giving a hardcoded human email precedence — use the job's execution
  identity (`DATABRICKS_CLIENT_ID`) as the default; add reconnect/retry around `write()`; fix
  `databricks.yml` hardcoded `FABRIC_LAKEBASE_USER`.
- **Webhook (16c):** catch `URLError` (DNS/refused/timeout), not only `HTTPError`.
- **Error-conflation (25e):** in `describe_source`/collectors, distinguish an auth/semantic failure
  from "no data" — regex-classify Kusto error text; a genuine failure is surfaced as a failure, never
  as empty. (Same class as Part 7.)
- **Egress (17b):** route `ticketing.py` / `conversation.py` delivery through the egress chokepoint,
  or delete the confirmed-dead unwired paths with a logged decision.

## Sub-plan 5 — Plugin query-audit depth + safety rails (Parts 24a/f, plugin gaps)

**Files:** `query/kql_audit_rules.py` (add rules), `query/kql_format.py` (new), `tools.py`
(large-result gate + wire), `agent_server/system_prompt.py` (audit-before-execute), a
startup-preflight module.

- **Audit rules (domain subset):** port BEST001–006, HINT001–002, and the relevant PERF002/004–010
  + CORRECT002–006 that apply to Fabric/LA/Power-BI KQL; skip the App-Insights TELEMETRY rules with a
  logged reason. Feed them through the existing `audit_kql` score/grade already wired into the
  firewall.
- **Performance-tuner patterns:** `tolower()`→`=~`, filter-before-join, project-after-join,
  `dcount` vs `count distinct`, `top N` vs `order by`+`take` — as named checks.
- **Large-result display gate:** when a query returns > 50 rows, STOP and present the 4-option menu
  (summarize / filter / top-N / proceed), hard-cap the display at 100 rows. A token-cost safety rail
  the plugin enforces and the app lacks.
- **`kql_format`:** a read-only KQL pipe-indentation formatter (from `format.ts`).
- **SessionStart-style preflight:** a startup config/health check (env vars, catalog present, data
  connections reachable) analogous to the plugin's `check-setup.sh`.
- **Audit-before-execute:** a system-prompt rule mandating `audit` on an agent-authored query before
  `run_kql`, fixing correctness errors first (the plugin's kql-analyst workflow gate).

## Sub-plan 6 — Frontend UX (Part 11)

**Files:** `e2e-chatbot-app-next/client/src/components/**`.

- **U1 — structured investigation card:** a Finding / Evidence / Root-Cause / Fix card layout for
  investigation responses (currently a single markdown block).
- **U3 — per-number scope marker:** an inline true-CU vs monitored-proxy marker next to each figure
  (today a single per-response chip). (U2 confidence badge and U4 KQL-viewer already shipped.)

## Improvements folded in (opportunistic, logged)

- Consolidate the duplicated `normalizeExecutingUserDisplay` (in both export modules) into one shared
  helper.
- The measures insight: the agent has no computable business measures (source never had formulas) —
  recorded as a known limitation; obtaining real DAX via `extract_measures.py`/EXTERNALMEASURE stays
  deferred (6.4), not in scope.

## Testing & sequencing

Each sub-plan ends with the full suite green vs the 1775 baseline (plus its own new tests). Frontend
sub-plans verify with `npm run build:client` + Biome lint (no live render available). Sub-plans are
executed in order 1 → 6 via subagent-driven-development (implementer + task-review per task, broad
review at the end of each sub-plan). Sub-plan 1 is the priority; each is independently shippable.

## Deferred (explicit, not dropped)

- **HR enrichment** — `hr-loader` port, HCMIF xlsx wiring, coverage-gate, cohort suppression.
- **Teams-push (Phase 10)** — webhook secret + Entra bot identity to actually deliver cards outward.
- **App-Insights TELEMETRY audit rules** — out of domain.
- **Business-measure formulas** — source data never carried them.
