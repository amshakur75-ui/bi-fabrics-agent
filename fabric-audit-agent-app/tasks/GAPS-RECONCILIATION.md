# GAPS-AND-ISSUES ↔ MASTER-INTEGRATION-PLAN Reconciliation

Created: 2026-08-07
Sources reconciled:
- `fabric-audit-agent-app/GAPS-AND-ISSUES.md` (2687 lines, read in full)
- `fabric-audit-agent-app/tasks/master-integration-plan.md` (Phases 0–7)
- `fabric-audit-agent-app/tasks/tightening.md` (Part 17a referenced for the `sla.py` pattern-bug note)

This is a READ-ONLY reconciliation. No code was changed. Statuses below are transcribed **exactly
as GAPS-AND-ISSUES.md states them** (its own `STATUS:` headers, Section 11 FIXED table, and the
Priority Order table). Where the ledger contradicts itself, that is called out explicitly rather
than silently resolved.

## Summary counts

Every item carrying an id (or a titled un-coded entry) in the ledger is inventoried below — 93 in
total, larger than the "~43 prioritized items" the plan references because this counts *every*
Section 11 FIXED-only entry, every OB/OB-F/CAMP/UX/SP entry, and every Section 12.x formula-group
finding, not just the prioritized subset.

| Status | Count |
|---|---|
| FIXED | 54 |
| PARTIAL | 16 |
| OPEN | 23 |
| **Total** | **93** |

Note on "moot" items: `N19` and `N21` are counted FIXED because the Priority Order groups them with
"confirmed moot … nothing in the codebase currently does either thing they warn about, so no action
is currently needed." They are self-closing guards, not code fixes.

---

## 1. Full item inventory — id → title → status (as the doc states it)

### Section 1 — Code Gaps

| ID | Title | Status |
|---|---|---|
| A1 | Throttle threshold fields never extracted — stage-2 gate dead | FIXED |
| A2 | Burndown chain missing — overage fields never extracted | FIXED |
| A3 | `workspace_monitoring` underpowered eventDepth, no truncation signal | FIXED |
| B2 | Blank `ExecutingUser` — no cross-reference fallback | FIXED |
| B3 | `kb/metric_definitions.py` does not exist | FIXED |
| B4 | Math-consistency check missing | FIXED (header `STATUS: FIXED`; Priority #8 flags live-wiring still needs Claude-Code verification) |
| B5 | No `ClaimConfidence` enum confirmed in `confidence.py` | FIXED |
| N1 | Workspace Monitoring event seam not wired to tools | OPEN |
| N3 | `concentration.py` wrong default label / incomplete `"frequency"` mode | FIXED |
| N5 | Concentration detector has no item-kind filter | FIXED |
| E1 | Concentration threshold applied to wrong/mixed denominator | FIXED |
| N6 | `user_concentration.py` no item-kind awareness | FIXED |
| N7 | `attributionMode:"cost"` hardcoded (cpu vs duration hidden) | FIXED |
| N8 | Third inline concentration check in `diagnose.py`, item-kind-blind | FIXED |
| N9 | Fourth copy of 30% threshold hardcoded in `gates.py` | FIXED |
| N10 | `sources.py` degraded-capability message misleading about WM | FIXED |
| N11 | Ad-hoc KQL queries bypass every gate | PARTIAL (option-b `ungated` flag done; option-a routing through gates still open) |
| N12 | No query provenance capture | FIXED (`queryKql` already threaded; SP7 prompt-side remains) |
| N13 | No startup health probes for the 3 semi-verified data connections | OPEN |
| N14 | Runtime `MetricValue` dataclass / metric-type stamper | PARTIAL (header `STATUS: FIXED` "as scoped"; KNOWN OPEN: `peakCuPct` provenance wiring deferred) |
| N15 | Two independent tool-loop implementations | FIXED |
| N16 | UI-exported %-measures may be fraction-scaled (0–1) | PARTIAL |
| N17 | `Monitoring_Eventstream` no Date-dimension linkage | FIXED (Layer A immune by construction) |
| N18 | Item display name not unique key across workspaces | FIXED |
| N19 | Item-level `Throttling(s)` unit mislabeled (ms not s) | FIXED (moot / self-closing — no current code path) |
| N20 | Three throttle types use three different smoothing windows | FIXED |
| N21 | Operation status taxonomy inconsistent across tables | FIXED (moot / self-closing — no current code path) |
| C2 (REOPENED) | System prompt duplication in `agent_server/agent.py` | **CONTRADICTORY** — Section 1 heading + Priority #1 = REOPENED/OPEN/urgent; Section 11 + plan cross-check = FIXED via ADR-001. Counted FIXED (later ADR-001 entry supersedes). |
| N22 | Hidden keyword step-budget classifier, zero disclosure | PARTIAL (Section 11 says FIXED; consolidation note flags NEEDS VERIFICATION post Task-1/2 migration) |
| N23 | Date-filter bug in capacity-overloads/spike tool | FIXED |

### Section 2 — System Prompt Conflicts / Missing Rules

| ID | Title | Status |
|---|---|---|
| SP4 | "% of base" combined format is WRONG | PARTIAL (fixed in canonical file; reaches prod only once C2 lands) |
| SP1 | Burndown-chain auto-trigger rule missing | PARTIAL (rule written in canonical; A2 code auto-call wired) |
| SP2 | "validated" label not precise enough | OPEN |
| SP3 | Cadence-vs-causation rule missing entirely | OPEN |
| SP5 | % of base timepoint-vs-lifetime distinction not surfaced | OPEN |
| SP6 | Inferred/derived data must be labeled inline | OPEN |
| SP7 | Query provenance — quote query verbatim | PARTIAL (written in canonical; blocked on C2) |

### Section 3 — Behavioral Observations (mirror SP/code items)

| ID | Title | Status |
|---|---|---|
| OB1 | Stage-2 throttle gate never confirms (→A1) | FIXED (via A1) |
| OB2 | Agent asked "want me to pull burndown?" (→SP1) | PARTIAL (via SP1) |
| OB3 | "Confidence: validated" on rows-only results (→SP2) | OPEN (via SP2) |
| OB4 | Matthew Mungo cadence blamed as driver (→SP3) | OPEN (via SP3) |
| OB5 | Combined % of base format confusion (→SP4) | PARTIAL (via SP4) |
| OB6 | % of base not distinguished from Timepoint Detail (→SP5) | OPEN (via SP5) |
| OB7 | Arithmetic error 17% vs 0.5% (→B4) | FIXED (via B4) |

### Section 4 — Eval Suite Gaps

| ID | Title | Status |
|---|---|---|
| EV1 | 6 new golden eval cases | FIXED (all 6 authored) |
| EV3 | 4 more golden eval candidates | PARTIAL (3 of 4 authored; "repeated identical question" needs harness change) |
| EV2 | Run `mine_evals` on conversation log | OPEN (blocked on real usage + persistent log surface) |

### Section 5 — Validation & Calibration

| ID | Title | Status |
|---|---|---|
| V1 | Automated validation harness not built | OPEN (manual equivalent done by hand in Section 12) |
| V2 | Level-1 CU% calibration | FIXED (CLOSED — zero drift) |
| V3 | Peak util % / burndown minutes / avg util % | FIXED (CLOSED — 2/3 verified; avg-util blocked by UI-export sampling, not a formula gap) |

### Section 6 — EXTERNALMEASURE / DAX Schema

| ID | Title | Status |
|---|---|---|
| D1 | EXTERNALMEASURE stubs | PARTIAL (core formulas resolved via fingerprinting; ~140 non-UI measures still unverified, none blocking) |
| M1 | `extract_measures.py` needs DB-listing step | OPEN (low priority) |

### Section 7 — Architecture & Deployment

| ID | Title | Status |
|---|---|---|
| (Autonomous deployment) | Lakeflow Job + Teams alerting not built | OPEN |
| (Memory tables) | Four Unity Catalog Delta tables not built | OPEN (deferred) |
| N2 | FUAM never configured | FIXED (CLOSED — decision "No, not now") |
| E3 | No multi-workspace loop | OPEN (NEEDS DESIGN — two distinct designs) |
| E4 | No staleness check on dimensional data | FIXED |

### Section 8 — App / UX (CAMP)

| ID | Title | Status |
|---|---|---|
| UX1 | Feature 3 — side-by-side check cards not built | OPEN |
| UX2 | Feature 4 — animated "..." loading indicator not built | OPEN |
| UX3 | `audience.py` / `coaching.py` not wired to chat UI | OPEN |
| UX4 | No audience detection / selection in frontend | OPEN |

### Section 9 — Deploy Risks

| ID | Title | Status |
|---|---|---|
| N4 | Three unverified integration points in `agent_server/agent.py` | OPEN (verify only) |

### Section 10 — Deferred

| ID | Title | Status |
|---|---|---|
| D3 | `SOWMYA-AZURE-ROLES-BRIEF.docx` needs updating | FIXED (DROPPED — no `.docx` exists anywhere; nothing to update) |
| D4 | Dead Node.js reference app deletion + README cleanup | FIXED (deleted, commit fb3a783) |

### Section 11 — FIXED-only entries (no open counterpart elsewhere)

| ID | Title | Status |
|---|---|---|
| B1 | Activity Events collector built | FIXED |
| C1 | Proxy caveat tied to source | FIXED |
| C3 | Time-window labeling rule | FIXED |
| C4 | Unsolicited sizing recommendations suppressed | FIXED |
| C5 | Null-data gate | FIXED |
| E2 | Capacity-events dedup | FIXED |
| Gates | Six deterministic STOP gates in `gates.py` | FIXED |
| OB-F1 | Proxy caveat wrongly applied to capacity-event data | FIXED |
| OB-F2 | Opening lines asserting wrong numbers | FIXED |
| OB-F3 | Unsolicited sizing-down recommendations | FIXED |
| OB-F4 | 30-day figure labeled "weekly footprint" | FIXED |
| OB-F5 | Healthy verdict on null data after timeout | FIXED |
| OB-F6 | False claim that permissions unlock Metrics model | FIXED |
| OB-F7 | Suggested ReadWrite scopes | FIXED |
| CAMP F1 | Greeting + capability bubbles | FIXED |
| CAMP F2 | Direct Fabric access (`fabric_direct.py`) | FIXED |

### Section 15 — Live Validation Findings (2026-07-30)

| ID | Title | Status |
|---|---|---|
| N24 | Proxy users don't appear prominently in Metrics app for same windows | PARTIAL (N24A prompt caveat done; N24B ExecutingUser-blank + N24C time-alignment still open) |
| N25 | "% of base" applied per-user produces misleading large numbers | FIXED (via system-prompt documentation; column-rename option correctly rejected) |
| N26 | SKU mismatch between agent and Metrics app for same capacity | OPEN |
| N27 | Phase 8 chart component fails to render in deployed chat app | OPEN |

### Section 12.x — Formula Validation Findings

| ID | Title | Status |
|---|---|---|
| 12.2 | Group 2 — core CU% measures | PARTIAL (CLOSED except non-billable %, basecore, avg-util %) |
| 12.3 | Group 3 — carry-forward / burndown chain | FIXED (FULLY CLOSED, zero error) |
| 12.4 | Group 4 — throttle threshold measures | FIXED (FULLY CLOSED) |
| 12.5 | Group 5 — Health page / time-window measures | OPEN (mostly untouched, not blocking) |
| 12.6 | Multi-metric ribbon chart (all 4 tabs) | FIXED (COMPLETE) |
| 12.7 | Health page schema + cross-capacity findings | PARTIAL (state machine resolved; Usage variance / P95 formulas open) |
| 12.10 | Item History tab export session | PARTIAL (produced N16–N19; Pass-rate reconciliation open) |
| 12.11 | External validation (FUAM source + official docs) | FIXED (informational; resolved several open questions) |
| 12.12 | Full measure catalog via self-owned composite model | PARTIAL (Option-1 closed; basecore / Usage variance / P95 formula-text still open) |

---

## 2. OPEN / PARTIAL items → master-integration-plan phase mapping

Mapped by subject against Phases 0–7. "COVERED" means a concrete plan task addresses it;
"COVERED (deferred)" means the plan explicitly acknowledges and parks it; "UNCOVERED" means no phase
task addresses it (see Section 3).

| ID | Status | Plan phase / disposition |
|---|---|---|
| N1 | OPEN | **Phase 6.5** — N1 reminder: WM eventDepth withholding is deliberate; leave unless the mock-labeling root cause is solved first. COVERED. |
| N11 | PARTIAL | **Phase 2** (KQL guard upgrade) — firewall/severity path 2.2 + preflight 2.5 are where ad-hoc KQL gating lands. Option-a (route ad-hoc rows through the real gates) fits here. COVERED. |
| N13 | OPEN | Partial overlap with **Phase 1.6** (per-collector try/except surfaced in health output) and **Phase 4.11** (SKU startup cross-check), but the specific startup schema/XMLA-join/FUAM-owner probes are NOT scheduled. → UNCOVERED (see §3). |
| N14 | PARTIAL | **Phase 4.8** — "verify `kb/metric_definitions.py` is exported/wired." The KNOWN-OPEN `peakCuPct` provenance-wiring residual belongs here. COVERED (residual). |
| N16 | PARTIAL | **Phase 1.8** handles the ×100 fraction-scale fix for the *streaming* collector only. N16's UI-export path (`importers/capacity_metrics.py`) is not in any phase. → UNCOVERED (dormant guard; see §3). |
| N22 | PARTIAL | No phase re-verifies the step-budget disclosure survived the Task-1/2 migration or hardens the substring classifier. → UNCOVERED (see §3). |
| SP1 | PARTIAL | **Phase 4.10** — ">100% findings automatically invoke the burndown chain." COVERED. |
| SP4 | PARTIAL | Rides on **C2** (plan cross-check marks C2 done, ADR-001) + **Phase 3.9** keeps the prompt single-sourced; format already fixed in the canonical file. COVERED-via-C2. |
| SP7 | PARTIAL | N12 code done; prompt rule written in canonical; rides on **C2** + **Phase 3.9** single-sourcing. COVERED-via-C2. |
| SP2 | OPEN | No plan task adds the "validated"-precision rule. → UNCOVERED (see §3). |
| SP3 | OPEN | No plan task adds the cadence-vs-causation rule. → UNCOVERED (see §3). |
| SP5 | OPEN | **Phase 7.2** verifies "no capacity-% blend" as a live check, but no phase *implements* the timepoint-vs-lifetime labeling rule. → UNCOVERED (weak verification backstop only; see §3). |
| SP6 | OPEN | No plan task adds inline `[inferred]`/`(derived)` labeling. → UNCOVERED (see §3). |
| OB2 | PARTIAL | Mirrors SP1 → **Phase 4.10**. COVERED. |
| OB5 | PARTIAL | Mirrors SP4 → COVERED-via-C2. |
| OB3 | OPEN | Mirrors SP2 → UNCOVERED. |
| OB4 | OPEN | Mirrors SP3 → UNCOVERED. |
| OB6 | OPEN | Mirrors SP5 → UNCOVERED. |
| EV3 | PARTIAL | 4th case ("repeated identical question 3×") needs a multi-turn eval-harness change; no phase covers `score_investigations.py`/harness work. → UNCOVERED (see §3). |
| EV2 | OPEN | **Phase 6.2** creates the `audit_findings` Delta surface `mine_evals` could read, but running `mine_evals` is not scheduled and is blocked on real traffic. → UNCOVERED (blocked; see §3). |
| V1 | OPEN | **Phase 4.8** (grounding formulas) + **Phase 4.9** (agent-arithmetic math-consistency) are related, but the automated 3-level `validate.py` cross-check harness is not scheduled. → UNCOVERED (partial overlap; see §3). |
| D1 | PARTIAL | **Phase 6.4** — EXTERNALMEASURE thread; `extract_measures.py` pending real formulas; basecore stays OPEN, do not guess. COVERED (deferred). |
| M1 | OPEN | **Phase 6.4** — `extract_measures.py` stays as-is pending Jiao/Vegasina/Srikanth. COVERED (deferred). |
| Autonomous deployment | OPEN | **Phase 6.1** explicitly assigns Teams/Job delivery to a "Phase-10-owned" track OUTSIDE this plan's Phases 0–7. COVERED-by-reference (deferred to Phase 10 — not built by this plan). |
| Memory tables | OPEN | **Phase 6.2** — verify the four Delta tables (no partitioning, liquid clustering, 90-day retention). COVERED. |
| E3 | OPEN | Plan states the stance "we go multi-workspace" (completeness cross-check) but schedules NO orchestration/loop task in Phases 0–7. → UNCOVERED (needs design; see §3). |
| UX1 | OPEN | Phase 5 covers export/chart/kql-viewer only — not side-by-side check cards. → UNCOVERED (see §3). |
| UX2 | OPEN | Not in Phase 5. → UNCOVERED (see §3). |
| UX3 | OPEN | No phase wires `audience.py`/`coaching.py`. → UNCOVERED (see §3). |
| UX4 | OPEN | No phase adds audience detection/selection. → UNCOVERED (see §3). |
| N4 | OPEN | **Phase 7.2** live checks exercise the deployed app end-to-end (which requires the mlflow decorator import, `DatabricksMCPClient` methods, and Claude endpoint dialect to all work). COVERED (verification-only, via Phase 7.2). |
| N24 | PARTIAL | **Phase 7.2** enforces the WHO/WHAT/WHY, no-capacity-%-blend framing (covers the N24A caveat direction). N24C's concrete ±30-second cross-source alignment tolerance is not scheduled. → PARTIALLY COVERED; N24C sub-item UNCOVERED (see §3). |
| N26 | OPEN | **Phase 4.11** — SKU mismatch, called the HIGHEST-RISK open item; startup/collection cross-check of base-CU vs capacity-API SKU. COVERED. |
| N27 | OPEN | **Phase 5.5 / 5.6** — chart rendering (`chart.tsx` parity + `kql-viewer.tsx`); the recharts render-path/`npm install` fix fits the Phase-5 frontend visuals work. COVERED. |
| 12.2 | PARTIAL | **Phase 6.4** (measures deferred) + **Phase 4.11** (SKU operational risk). COVERED (deferred). |
| 12.5 | OPEN | **Phase 6.4** — Group-5 measures (Usage variance, P95) parked, do not guess. COVERED (deferred/research). |
| 12.7 | PARTIAL | **Phase 6.4** — Usage variance / P95 formulas remain open. COVERED (deferred). |
| 12.10 | PARTIAL | **Phase 6.4** — Pass-rate reconciliation remains research. COVERED (deferred). |
| 12.12 | PARTIAL | **Phase 6.4** — basecore/Usage variance/P95 formula-text pending external source. COVERED (deferred). |

Note on the `sla.py` blanket-SLA-language bug (task's mapping example): this is NOT a
GAPS-AND-ISSUES.md numbered item — it originates in **tightening.md Part 17a** (the same pattern as
the already-fixed `accountability.py` FIX 3). In the master plan it is absorbed by the STANDING RULE
(grep for the same pattern in sibling files) and by Phase 4's `investigation/*` + detector
reconciliation blast radius, rather than by a dedicated task id.

---

## 3. OPEN GAPS ITEMS NOT COVERED BY ANY PLAN PHASE

These are the completeness-backstop items: OPEN or PARTIAL entries in GAPS-AND-ISSUES.md that no
task in master-integration-plan.md Phases 0–7 addresses. Listed conservatively — each is here
because a genuine search of all eight phases found no task that implements it. Where a phase touches
the same area but does not do the work, that partial overlap is named so it is not mistaken for
coverage.

**Prompt-behavior rules (Section 2/3) — the plan schedules only SP1 and the resolution-layer prompt
rules; the behavioral-quality rules below are unscheduled:**

1. **SP2 — "validated"-label precision rule** (OPEN). Plan adds resolution tool-sequencing prompt
   rules (3.9) and the burndown rule (4.10), but never the rule gating the word "validated" to
   formula-verified/gate-checked claims. Mirror: **OB3**.
2. **SP3 — cadence-vs-causation rule** (OPEN). No phase adds the ">80% of consecutive hot windows =
   automated pattern, not the driver" rule (the Matthew-Mungo failure). Mirror: **OB4**.
3. **SP5 — % of base timepoint-vs-lifetime distinction** (OPEN). Phase 7.2 *verifies* "no capacity-%
   blend" as a live check, but no phase *implements* the always-label-both rule in the prompt.
   Verification backstop only. Mirror: **OB6**.
4. **SP6 — inline `[inferred]`/`(derived)` labeling** (OPEN). No phase adds the rule requiring
   inferred/derived values to be labeled at the point they appear (not only in an end caveat).

**Frontend / UX (Section 8) — Phase 5 is export/chart/KQL-viewer visuals only:**

5. **UX1 — side-by-side check cards** (OPEN). Structured tool-call events + CSS-grid card layout;
   nothing in Phase 5.
6. **UX2 — animated "..." loading indicator** (OPEN). `tool.tsx` in-progress animation; nothing in
   Phase 5.
7. **UX3 — `audience.py`/`coaching.py` wired to chat** (OPEN). Dead-code exec/author/team views;
   no phase.
8. **UX4 — audience detection/selection mechanism** (OPEN). Prerequisite for UX3; no phase.

**Eval / validation harness (Sections 4/5) — the plan builds tests (7.1) but no harness/mining work:**

9. **EV2 — run `mine_evals` on conversation log** (OPEN, blocked). Phase 6.2 creates the
   `audit_findings` Delta surface it could read, but running the miner is not scheduled and is
   blocked on real traffic.
10. **EV3 4th case — repeated-identical-question multi-turn eval** (PARTIAL). Needs a
    `score_investigations.py` multi-turn scoring change; no phase covers eval-harness modification.
11. **V1 — automated 3-level `validate.py` cross-check harness** (OPEN). Phase 4.8 (grounding
    formulas) and 4.9 (agent-arithmetic consistency) are adjacent, but the CU%/carry-forward/
    operation-id cross-check harness that runs on every deploy is not scheduled.

**Architecture / deploy (Sections 7/9) and remaining code guards (Section 1/15):**

12. **E3 — multi-workspace loop orchestration** (OPEN, needs design). Plan asserts the stance "we go
    multi-workspace" but schedules no live-aggregation or historical-batch-rollup orchestration task
    in Phases 0–7.
13. **N13 — startup health probes for the 3 semi-verified data connections** (OPEN). Phase 1.6
    (surface collector failures) and 4.11 (SKU cross-check) overlap in spirit, but the specific
    deploy-time `probe_capacity_events_schema` / `probe_xmla_join_path` / `probe_fuam_owner_resolution`
    probes are not scheduled.
14. **N16 — UI-export fraction-scale guard** (PARTIAL, dormant). Phase 1.8 fixes ×100 for the
    *streaming* collector; the UI-export parser path (`importers/capacity_metrics.py`) has no phase.
    A dormant "verify scale if ever parsing a UI export" guard.
15. **N22 — step-budget classifier verification + robustness** (PARTIAL). No phase re-verifies the
    disclosure survived the Task-1/2 migration of `agent_server/agent.py`, nor broadens/replaces the
    19-substring keyword classifier.
16. **N24C — ±30-second cross-source time-window alignment tolerance** (part of N24, PARTIAL). The
    one concrete, buildable sub-fix of N24; not scheduled in any phase (N24A is done, N24B ties to
    the already-fixed B2).

### Items deliberately NOT listed as uncovered (and why)

- **N19, N21** — the doc itself marks these moot/self-closing ("nothing in the codebase currently
  does either thing"); no plan action is required now, so not a backstop gap.
- **N4** — Phase 7.2's end-to-end live checks cannot pass unless the three deploy integration points
  work, so it is functionally exercised (verification-only item).
- **Autonomous deployment / Memory tables** — Memory tables are Phase 6.2; autonomous deployment is
  explicitly owned by a future "Phase 10" per 6.1 (acknowledged and parked, not silently dropped).
- **All Section 12.x PARTIAL/OPEN formula items** — parked under Phase 6.4 ("do not guess; SKU
  cross-check 4.11 covers the operational risk meanwhile").
- **C2** — internally contradictory in the ledger, but the plan's completeness cross-check states
  "C2 prompt dedup (done, ADR-001)", so the plan treats it as covered/done. The residual risk is
  that GAPS-AND-ISSUES.md's own Priority #1 still lists it as urgent-open — a documentation
  reconciliation the plan should settle, not a build gap.
