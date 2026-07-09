# Fabric Audit Agent — Build Handoff

**Written:** 2026-07-08 · **For:** the next Claude picking up this project cold.
**Read this top-to-bottom once. The firewall (Part 2) has SHIPPED — start your work at Part 3-A.**

---

## Part 0 — What this project is (30 seconds)

A **read-only** Microsoft Fabric / Power BI capacity & performance **audit agent**, all-Python, deployed on **Databricks**. It detects throttling / oversized models / refresh contention, gives an evidence-backed **optimize-vs-size-up verdict**, and runs a **User → Item → Owner 30% concentration alert**. It exposes its capabilities as **read-only MCP tools** an agent (Databricks-hosted Claude) calls conversationally, plus a scheduled sweep Job.

**The single most important rule: READ-ONLY IS ABSOLUTE.** No writes, no refreshes, no scale actions, ever. The only outward actions are delivering findings and sending notifications. Every design decision defers to this. A described-but-unemitted field, or mock data labeled as live, is treated as a **honesty defect** as serious as a write path.

- **Repo:** `github.com/amshakur75-ui/bi-fabrics-agent` — **PUBLIC**. No real tenant/client IDs or secrets in commits, ever.
- **Package path on the build machine:** `C:/Users/shaku/corporate/fabric-audit-agent-py/`
- **Git repo root:** `C:/Users/shaku/corporate/` (the package is a subdirectory; committed paths are prefixed `fabric-audit-agent-py/`).

---

## Part 1 — Everything done so far

### 1a. Where the code is RIGHT NOW  (updated 2026-07-08 post-firewall-merge)

- **`main` @ `9b70657`** — the **query firewall shipped** (PR #11, 2026-07-08): `run_kql` (17th tool, agent-authored read-only ad-hoc KQL) + `query_library` (18th tool, 21 grounded templates). Before that: Phase 4 (PR #10) + follow-ups (`bddbdb8`: `whats_changed` code-side activation — `FABRIC_HISTORY_PATH` set → `run_audit` appends history + the diff works, durable Job path still pending; a diagnose stage-3 over-window refetch; a loop.py forced-answer fix; a version bump).
- **No open feature branch** — `feat/query-firewall` was merged + deleted. Cut a fresh branch for the next item (Part 3-A).
- **Test baseline: `856 passed, 3 skipped`** on `main` (`cd fabric-audit-agent-py && python -m pytest -q`). Evals: `python -m fabric_audit_agent eval-agent` → **19/19**; `eval-investigations` → 2/2.
- **18 read-only tools** live on `main` (`python -c "from fabric_audit_agent.tools import create_tool_definitions as f; print(len(f()))"` → 18).

### 1b. The tools that exist (18)

| Group | Tools |
|---|---|
| Audit / verdict | `run_audit`, `list_workspaces` |
| User attribution | `user_activity`, `investigate_user`, `investigate_capacity_spike`, `user_spike_history` |
| Event depth + windows | `spike_events`, `raw_events`, `capacity_patterns` |
| Grounding | `describe_source`, `sample_events` |
| Capacity diagnostics | `capacity_diagnostics` |
| Deduction | `diagnose`, `analyze_dax` |
| Memory | `whats_changed` |
| Per-user | `user_timeline` |
| Ad-hoc + library | `run_kql`, `query_library` |

### 1c. Merged history (what each PR delivered)

- **PR #1 (Phase 1)** — offline investigation core: coverage-honest, abstaining, evidence-citing.
- **PR #3 (Phase 2)** — agent brain (the ReAct tool-loop `agent/loop.py`, `agent/investigator.py`) + deploy runbook.
- **PR #4 (Deploy Part B)** — Databricks Apps path: an agent App calls the MCP App.
- **PR #5 (Phase 3A)** — event depth & temporal patterns (offline, TDD).
- **PR #6/#7** — Phase 3 hygiene; deterministic event sampling + consistent `cuSeconds` units.
- **PR #8** — Phase-4 spec + Phase-5 approval sheet + MCP harvest inventory (`research/23-*`, `research/24-*`).
- **PR #9 (MCP Harvest Upgrade)** — 13 tasks: absorbed read-side patterns from 4 MS/OSS MCPs (fabric-rti-mcp, azure-mcp, johnib/kusto-mcp, mcp-kql-server). Delivered the KQL guard (`query/kql_guard.py`), client hardening (`request_readonly_hardline`), result envelopes + char-budget limiter (`query/envelope.py`), columnar + per-query cost metadata, time windows (`query/windows.py`, py3.10 Z-safe), `raw_events`, `describe_source`, `sample_events`, `capacity_diagnostics`, verify-in-Fabric deeplinks (`query/deeplinks.py`), honesty labels (`cuUnit`), log redaction (`query/redact.py`). 12 tools.
- **PR #10 (Phase 4)** — source-capability layer + 9 deepening ADDs, 15 tasks. Delivered `sources.py` (capability registry + coverage resolver), Tier-1 activity→event adapter, the `_resolve_event_sources` tiered seam (Tier-2 per-query → Tier-1 operation-level → mock, with `tier`/`coverageNote`/`hasRealCost` labels), throttle decomposition (`investigation/throttle.py`, honest 3-stage gate), time-to-throttle forecast (`investigation/forecast_throttle.py`), refresh-failure detector (`detectors/refresh.py`), dead-man's-switch (`job.py`), `analyze_dax` tool, the `diagnose` decision-tree engine (`investigation/diagnose.py`) + tool, `whats_changed` (agent memory + atomic store write), `user_timeline`, eval golden cases for all 16 tools + a permanent coverage invariant. 12 → 16 tools. The final whole-branch review here caught **F1** (Workspace Monitoring declared as an event source the seam couldn't serve → mock mislabeled as live), fixed at three layers before merge — the reason WM is gated (Part 1f).
- **PR #11 (Query Firewall)** — `run_kql` + `query_library`, 4 tasks. `query/firewall.py::validate_adhoc_kql` (pure static gate) → take-0 rehearsal (engine binder = live-schema check) → bounded execute; 21 grounded templates, every one firewall-passing (test-enforced). 16 → 18 tools. **The adversarial final review found a proven Critical *bypass class*** — see Part 4.12; it's the sharpest lesson on this branch.

### 1d. Architecture you must respect

- **Functional core + swappable dict-ports.** Core is pure; all I/O is dependency-injected as dict-style ports (`{"collect": fn}`, `{"reason": fn}`, `{"deliver": fn}`, `{"history","append"}`, `{"load","save"}`). Same logic runs offline (mock adapters) or in prod (real adapters). Nothing in the core knows about HTTP/files.
- **Tools live in `tools.py::create_tool_definitions`.** Each tool is `{name, description, input_schema, handler}`. `mcp_server._make_tool_fn(handler, input_schema)` derives the FastMCP signature from the schema — a new tool needs only a complete `input_schema`, no registration edits beyond the docstring tool list.
- **The tiered event seam** `_resolve_event_sources(...)` returns `(events, series, meta)` with `meta` carrying `tier` (`perQuery`/`operationLevel`/`mock`), `coverageNote`, `hasRealCost`. It's the honesty spine — study it before touching any event tool.
- **The query building blocks** (all in `query/`): `kql_guard.assert_read_only_kql`, `first_statement`, `_strip_string_literals`, `assert_kusto_host`; `envelope.finish`/`cap_rows`/`to_columnar`; `deeplinks.kusto_deeplink`; `redact.redact_secrets`; and now `firewall.validate_adhoc_kql` (the shipped ad-hoc gate). In `tools.py`: `dry_run` (take-0 rehearsal), `_capacity_kusto_query`, `_queryplan_estimate`, `_memo_client`, `_load_query_library`.

### 1e. Non-negotiable conventions (violating these = a review rejection)

- **Read-only absolute** (repeated because it's the whole game).
- **Data keys camelCase** (`peakCuPct`, `cuSeconds`); **Python identifiers snake_case**.
- **Nullish, not falsy:** `x if x is not None else default` — NEVER `x or default` where `0`/`""`/`False` is a valid value.
- **Numeric guards exclude bool + non-finite:** `isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)`.
- **Uniform error envelope:** every handler catches and returns `{"error": ..., "source": ...}` — NEVER raises to the MCP host.
- **stdlib-only core;** prod extras (`requests`/`msal`/`anthropic`/`mcp`) are opt-in. Python ≥ 3.10.
- **Honesty labels:** never present a proxy/mock/operation-level figure as authoritative/live. `cuSeconds` is a CPU-time PROXY, not authoritative capacity CU — it's labeled as such everywhere.
- **Offline deterministic tests** with injected fakes; never hit a live endpoint from the suite.
- **MIT attribution** in any module adapting an external source.

### 1f. Governance / gated items (do NOT build these without an explicit go)

- **FUAM** (Fabric Unified Admin Monitoring) collector — Phase-5 **approval-gated**, pending the org's sign-off. `sources.py` has a `fuam` descriptor that is never configured (a placeholder, by design).
- **Workspace Monitoring as an event engine** — deliberately WITHHELD (Phase-4 final-review finding F1: it was declared as an event source the seam couldn't serve, which mislabeled mock as live). It still feeds the aggregate audit. Re-add to the event/query surface only when the seam genuinely consumes it.
- **SKU $ pricing** — explicitly out of scope per the user (2026-07-07). Azure Retail Prices API is the path if ever wanted.
- **ADO ticketing / change-correlation** — Phase-5, org-dependent.
- **Never create/deploy/modify shared Databricks resources** without asking.
- **Standing user action (not code):** the `FABRIC_CLIENT_SECRET` still needs rotation on their Azure side.

### 1g. How work gets executed here (the process — follow it exactly)

This project uses **superpowers subagent-driven-development**. The pattern that landed Phases 3, 4, and the harvest cleanly:

1. **Per task:** extract the task brief (`scripts/task-brief PLAN N`), dispatch a fresh implementer subagent (model: sonnet) with the brief + interfaces + a "do the work yourself, don't delegate" instruction. It works TDD (failing test → implement → green → commit), writes a report file, returns STATUS/commit/tests/concerns.
2. **Review each task:** generate a review package (`scripts/review-package BASE HEAD`), dispatch a fresh reviewer subagent (sonnet) with the brief + report + package. It returns **Spec ✅/❌** + **Quality Approved/Changes-needed** + findings.
3. **On Important/Critical findings:** dispatch ONE fix subagent with all findings; on correctness-critical fixes, re-review before proceeding.
4. **Track progress in a ledger** at `C:/Users/shaku/corporate/.superpowers/sdd/progress.md` (survives compaction — trust it + `git log` over memory). Mark each task complete with its commit range; log Minor findings there for the final review.
5. **After the last task:** a **final whole-branch review** on the most capable model (opus), with the accumulated Minor-findings rollup. Fix any findings in one consolidated wave, re-review.
6. **Merge:** push → `gh pr create` → wait for CI (a GitHub Actions workflow runs pytest on Python 3.10 + 3.12; it already exists) → `gh pr merge N --merge --delete-branch` → sync local main → verify on main.

**Commit trailer:** end commit messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. **PR body trailer:** `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Part 2 — The Query Firewall (SHIPPED — reference + its security lesson)

> The firewall was the "current build" when this handoff was first written; it **merged on 2026-07-08 (PR #11)**. This section is now REFERENCE: what it is, how it works, and the hard security lesson it taught. Your actual next task is in Part 3-A.

**What it does:** lets the agent run **read-only, agent-authored ad-hoc KQL** it composes on demand (open-ended investigation beyond the fixed tool menu), plus a **grounded query-library** of 21 proven templates. Spec: `docs/superpowers/specs/2026-07-08-query-firewall-design.md`; plan: `docs/superpowers/plans/2026-07-08-query-firewall.md`.

**How it works:** `query/firewall.py::validate_adhoc_kql` (pure) does static rejection — length → **verbatim-string** → **multiline-string** → **comment** → multi-statement → control-command → **denied-operator** deny-list (`cluster(`/`database(`/`workspace(`/`app(`/`externaldata`/`external_table`/`evaluate`, both KQL flavors, word-boundary anchored, scanned after blanking regular string literals). Then `run_kql` runs a **take-0 rehearsal** against the engine's own binder (the live-schema check — no schema cache, no homemade parser), then bounded execute (`| take maxRows` appended AFTER validation, hard-cap 1000) + honest envelope + a redacted `[adhoc-kql]` stdout audit line. Engines: `capacity` (Eventhouse) + `la` (Log Analytics). `query_library` is an inert catalog; templates run through `run_kql` with **no bypass** — a test enforces every template passes the firewall (the "grounding bar").

**Firewall exclusions (decided; don't reopen without cause):** FUAM/SQL leg (gated), WM engine (withheld until wired), parameterization machinery (2nd injection surface), usage-tracking storage (App is write-free; the stdout audit log replaces it), schema cache (rehearsal IS the live check), homemade KQL parser (rejected — the engine's binder is the real parser). All in the spec's "explicitly NOT pursued" section.

**Fail-closed restriction the agent must know (disclosed in MCP-AGENT.md):** ad-hoc `run_kql` queries **cannot contain** `@"`/`@'` verbatim strings, backticks, or `//` comments — including a `//` inside a URL string literal. This is deliberate (see Part 4.12); the agent rephrases.

---

## Part 3 — The forward roadmap (to the last phase)

Do these **in order**, each as its own brainstorm → spec → plan → subagent-driven execution → review → merge cycle. Do NOT batch them; each is a separate PR. Several are **gated** — do not start a gated item until the user confirms the gate opened.

### Next up (the firewall is merged — start here)

**A. Verified-query library growth loop (small, ungated).** The firewall ships with the stdout audit log as the learning signal. Once real ad-hoc queries flow, mine the App logs for the most-repeated *allowed* query shapes and promote them into `query_library.json` (each still must pass the grounding bar). This is a recurring ~10-minute PR, not a one-time build. It's the payoff for logging — do a first pass after the agent has real usage.

**B. Deploy-switch activation (ops, ungated, needs the user).** PARTLY DONE as of `bddbdb8`: the code-side `whats_changed` activation already landed — when `FABRIC_HISTORY_PATH` is set, `run_audit` appends its run record via the `store_local` contract and the diff works; an interim local container path is wired in `app.yaml` (ephemeral across redeploys). What REMAINS: point `FABRIC_HISTORY_PATH` at a durable path (the scheduled Job's `AUDIT_HISTORY_PATH`, same contract), and redeploy the App/Job wheel so the 18 tools + firewall reach production. Coordinate with the user; confirm the read-only App still can't write anywhere it shouldn't.

### Gated — build only when the gate opens (ask the user first each time)

**C. FUAM collector (Phase-5 approval-gated).** When the org approves FUAM access: build the `fuam` collector against the descriptor already in `sources.py` (provides `perItemCU`, `owner`; daily; authoritative). This unlocks authoritative per-item CU — a real credibility upgrade over the current proxy. The coverage resolver already accounts for it (it just never resolves today).

**D. Workspace Monitoring as a live engine (gated on it being genuinely wired).** WM is deliberately withheld (finding F1). When WM is actually wired into the event seam: re-add it as an `eventDepth` source in `sources.py` AND add it as a `run_kql` engine — but ONLY with a test proving the seam serves real data (never mock-labeled-live again). This is the fix that must not regress.

**E. Authoritative CU unlock (the biggest ceiling-raiser; data-authority, not code).** Today per-user/per-item consumption is a CPU-time proxy. Real billable CU% lives only in the Capacity Metrics semantic model / Workspace Monitoring. When Workspace Monitoring rollout or the Capacity Metrics semantic model becomes queryable per-tenant, wire it — this is what makes the verdict authoritative rather than indicative. Partly overlaps C/D; it's the strategic north star.

**F. `semantic-link-labs` spike (gated on a short investigation, then a decision).** Microsoft's MIT VertiPaq/BPA analyzer — genuinely useful model-bloat diagnostics we lack. Time-boxed spike first: does it authenticate + run read-only against a live semantic model from a *generic Databricks* cluster (not just a Fabric notebook)? If yes → ADD as a model-quality tool; if the auth path fights the SDK → SKIP. Don't force it.

**G. ADO ticketing + change-correlation (Phase-5, org-dependent).** "What deployed right before the spike" — high value, but org-dependent on whether PBI/Fabric ships via ADO. Build the minimal read-only ADO client in our own MCP (not a dependency on the external azure-devops-mcp) if/when the org confirms the deployment path.

### The last phase

**H. Production hardening + operate.** Once the capability set is complete (firewall shipped; FUAM/WM/authoritative-CU landed as their gates open): the terminal work is *operational*, not more features — validate against real user traffic, tune thresholds from real data, prune/grow the query library from real logs, and keep the honesty labels accurate as data sources change. The strongest signal for what to build next stops being code archaeology and becomes **live usage**. The explicit non-goals stay non-goals: ML anomaly detection (the deterministic decision trees do it explainably), auto-remediation (a write action — forbidden), and always-on streaming watchers (the scheduled sweep + on-demand MCP already cover it).

---

## Quick-start checklist for the next Claude

- [ ] `cd C:/Users/shaku/corporate` — on `main`; `git pull --ff-only origin main`; confirm `main` at/after `9b70657`.
- [ ] `cd fabric-audit-agent-py && python -m pytest -q` → `856 passed, 3 skipped`; `python -c "from fabric_audit_agent.tools import create_tool_definitions as f; print(len(f()))"` → `18`.
- [ ] The firewall (Part 2) is SHIPPED — read it as reference. Your next task is **Part 3-A** (query-library growth loop) or **3-B** (deploy activation) — both ungated.
- [ ] For a build task: `superpowers:brainstorming` (if design needed) → `writing-plans` → 3 plan-reviewers → cut a fresh branch → `subagent-driven-development` with per-task review → opus final review → merge. (Part 4 is the full method.)
- [ ] Actively reach for any useful skill as you go (TDD, systematic-debugging, verification-before-completion, brainstorming, subagent-driven-development) — not just a curated list.
- [ ] Hold gated items C–H until the user confirms the gate opened.
- [ ] Never break read-only. Never label mock/proxy as live. Never loosen the grounding bar.

---

## Part 4 — HOW to work like the previous Claude (methods, not just outcomes)

Everything above says *what*. This part says *how* — the actual working methods that produced clean, review-gated PRs. Follow these; they are why the defect-catch rate was high and the merges were clean.

### 4.0 The mental model / the goal

You are not "an AI that writes code." You are the **controller of a small engineering team**. You decompose, delegate to fresh subagents with precisely-scoped context, gate their work through independent review, and keep your own context clean for coordination. The goal is **maximally correct, honest, read-only capability** — token cost is not the constraint, *correctness and honesty* are. When you find yourself about to write a big chunk of implementation yourself, stop: that's a subagent's job. Your job is the brief, the review, and the judgment calls.

**The prime directive, restated so you feel it:** this agent tells humans the truth about their capacity. A wrong number, a mock labeled live, a proxy presented as authoritative — these are worse than a crash, because a human acts on them. Every design choice, every review, every subagent prompt should be pressure-testing "is this honest?" as hard as "does this work?"

### 4.1 The full lifecycle of a feature (the loop you repeat)

```
brainstorming skill  →  spec (docs/superpowers/specs/)  →  user reviews spec
   →  writing-plans skill  →  plan (docs/superpowers/plans/)  →  3 verification subagents on the PLAN
   →  subagent-driven-development skill:
        for each task:  task-brief → implementer subagent → review-package → reviewer subagent
                        → (fix subagent if findings) → (re-review if correctness-critical) → ledger
   →  final whole-branch review (opus) + minor-rollup  →  fix wave  →  re-review
   →  push → gh pr create → CI (3.10+3.12) → gh pr merge → sync main → verify on main
```

Never skip the spec. Never skip per-task review. Never skip the final whole-branch review. Each gate has caught real defects — including two would-be production bugs the tests wouldn't have caught, because the tests were written around a flawed assumption.

### 4.2 Brainstorming → spec (how the design gets made)

- **Invoke `superpowers:brainstorming` BEFORE any design or code.** It forces: explore context → ask ONE question at a time → propose 2–3 approaches with a recommendation → present the design in sections → get approval → write the spec → self-review → user reviews spec.
- **Ask one question at a time, prefer multiple-choice.** The firewall spec was built from ~6 single questions (which engines? table-scope strictness? library? …). Each answer narrowed the next. Do NOT dump a wall of questions.
- **Always propose 2–3 approaches and recommend one with reasoning.** For the firewall it was A (parse-and-allowlist) / B (engine-validated via take-0 rehearsal) / C (homemade AST parser). Recommend, explain the trade-off, let the user choose. When the user pushed back ("why exclude these?", "why bring up a homemade parser?"), the right move was to *answer honestly and in depth*, not defend — that dialogue is where the real design hardens.
- **When the user asks "is there more we can do?" — actually go look.** The Phase-4 scope grew from 3 ADDs to 9 because each "anything else?" triggered a real audit of the codebase (grep the detectors, read the investigation engine, check what's collected-but-unread). The find that `facts["refreshes"]` was collected but no detector read it came from *reading collector_rest.py*, not from guessing. Ground every "we could add X" in a file you actually opened.
- **Write the spec with an explicit "Explicitly NOT pursued — with reasons" section.** This is a signature of this project. Every excluded option gets a one-line reason (gated / superseded / YAGNI), so the next reviewer doesn't reopen it assuming you missed it. The homemade-parser rejection is *in the spec* precisely so nobody re-litigates it.
- Spec self-review checklist: placeholder scan, internal consistency, scope (one plan?), ambiguity (pick one interpretation, make it explicit). Fix inline. The firewall self-review caught a real gap — the deny-list was Kusto-only, missing LA's `workspace(`/`app(` cross-resource escapes.

### 4.3 How the research was actually done

Research fed the specs (especially Phase 4). Methods, in order of preference:

1. **The deep-research skill / workflow** for broad multi-source questions. It fans out web searches across angles, fetches sources, and **adversarially verifies** each claim with a 2-or-3-vote panel before accepting it. Phase 4's throttling-internals came back as *17 claims verified 3-0/2-1 against Microsoft Learn*. Caveat learned: it is token- and rate-limit-heavy — it hit a session limit mid-run once, so **pull the partial results** (the workflow writes an output file with `confirmed`/`unverified`/`sources`), then finish the unverified axes yourself with targeted `WebFetch`.
2. **`WebFetch` + `WebSearch`** for targeted verification. Fetch the actual Microsoft Learn page and ask a specific question of it; don't answer capacity/throttling questions from memory — Microsoft's mechanics are exact (the 10-min/60-min/24-hr throttling stages, smoothing windows, carryforward/burndown) and must be quoted from the source.
3. **`gh api "user/starred?..."`** to read the user's recent GitHub stars (with `PYTHONIOENCODING=utf-8` on Windows, and `-H "Accept: application/vnd.github.star+json"` to get starred-at timestamps). This is how "look at my starred repos" was answered — but the honest finding was that none were domain-specific, so they were evaluated on merit and mostly SKIP'd. Don't force a starred repo into the design because it was starred.
4. **Read our own code first.** Half the best Phase-4 finds came from `grep`/`Read` on the repo, not the web — the unread `facts["refreshes"]`, the orphaned CLI-only `analyze_dax`, the automation brain (`whats_changed`) that existed but wasn't exposed. Before proposing a new capability, check whether the capability already exists and is merely unreached.
5. **Every finding gets a verdict: ADD / SKIP / GATED**, each with a one-line why and a rough effort. Default to SKIP for anything speculative; default to GATED (not ADD) for anything needing org approval or a data-source unlock. When you present research, a decision-ready ranked shortlist beats a prose dump. (An Artifact HTML page was used once to present the Phase-4 research shortlist — good for a scannable decision matrix, optional.)
6. **Security audit any external code before absorbing it.** The MCP harvest did line-by-line reads of each source MCP and ported ONLY read-side patterns, never write tools. When a CVE surfaced in research (a table-name f-string injection in a sibling MCP), the move was to *check our own equivalent code against it* (`describe_source`/`sample_events`) and confirm `escape_entity` already defended it — research findings become audits of our own code, not just notes.

### 4.4 Plan → three verification subagents (before ANY implementation)

After `writing-plans` produces the plan, and before executing it, the previous Claude dispatched **three independent reviewer subagents in parallel against the plan itself**:
- one for **coverage** (does every spec item map to a task? anything silently dropped?),
- one for **technical accuracy** (do the cited interfaces/signatures actually exist in the code? — this caught the dead-man's-switch wired to the wrong entrypoint, and a test helper referenced but never defined),
- one for **improvability** (is any task mis-sized, any design bug latent? — this caught the mock-series-leak honesty bug *in the plan*, before a line was written).

They found 14 planning defects including two would-be production bugs. **Do this.** Dispatch them with `run_in_background` and wait; apply all findings in one plan-revision pass. It is far cheaper to fix a plan than a merged bug.

### 4.5 Subagent dispatch — the exact mechanics

**Tool:** `Agent`. **Types used:** `general-purpose` for essentially everything (implementers, reviewers, fixers, research). **Model selection is deliberate and explicit — never omit it:**
- **Implementers:** `model: sonnet`. Most tasks are well-specified transcription+testing; sonnet is the right floor.
- **Reviewers (per-task):** `model: sonnet`, scaled to the diff.
- **Final whole-branch review:** `model: opus` — the one place to spend the top model, because cross-task honesty defects (like F1) only show at the whole-branch level.
- **Fix subagents:** `model: sonnet`.

**The implementer prompt template (copy this shape):**
1. One line: where this task fits in the project.
2. "READ THIS FIRST — your requirements, with exact values verbatim: `<task-N-brief.md path>`."
3. Interfaces from earlier tasks the brief can't know (exact signatures).
4. Your resolution of any ambiguity you spotted (decisions made FOR them — e.g. "use `has_real_cost=(meta['tier'] != 'operationLevel')`, NOT `meta['hasRealCost']`").
5. The report-file path + report contract (return only STATUS / commit / one-line test summary / concerns).
6. **Always include: "Do the work YOURSELF with Edit/Write/Bash — do NOT dispatch or delegate to another agent."** (A subagent once spawned its own nested agent and left partial work; this guard prevents it.)
7. Global constraints block (read-only, camelCase/snake_case, nullish, error envelope, offline tests, exact baseline test count).

**Dispatch in the background** (`run_in_background: true`, the default) and continue coordinating; you get a completion notification. **Do not read the subagent's raw output file via the shell** — it's the full JSONL transcript and will overflow your context. The returned final message is your result.

**Handle the four implementer statuses:** DONE → review it; DONE_WITH_CONCERNS → read the concerns first; NEEDS_CONTEXT → provide it, re-dispatch; BLOCKED → change something (more context, a stronger model, or split the task) — never re-dispatch unchanged.

**Recovery pattern (this happened several times):** an implementer stalls (watchdog), drops (API connection), or spawns a nested agent that dies mid-write. When that happens: check `git status`, run pytest, and if the work is complete and green, **commit it yourself** with the intended message and move on. The ledger + `git log` are truth, not the subagent's memory.

### 4.6 The scripts (exact invocations)

From the SDD skill dir `C:/Users/shaku/.claude/plugins/cache/claude-plugins-official/superpowers/6.1.1/skills/subagent-driven-development/scripts/`:

- **Extract a task brief:** `bash .../scripts/task-brief <PLAN_FILE> <N>` → writes `<repo>/.superpowers/sdd/task-N-brief.md` and prints the path. Hand THAT path to the implementer — never paste the plan into the prompt.
- **Build a review package:** `bash .../scripts/review-package <BASE> <HEAD>` → writes `<repo>/.superpowers/sdd/review-<base7>..<head7>.diff` (commit list + stat + full `-U10` diff in one file) and prints the path. **BASE is the commit you recorded before dispatching the implementer — NEVER `HEAD~1`** (that silently drops all but the last commit of a multi-commit task).
- Everything moves as **files**, never pasted text — pasted diffs/reports stay resident in your context on every later turn and blow it out.

### 4.7 The reviewer prompt (per-task gate)

Give the reviewer three files: the **brief**, the implementer's **report**, and the **review-package diff**. Then the binding **global-constraints block copied verbatim from the plan** (exact values — it's the reviewer's attention lens). Ask for TWO verdicts: **Spec ✅/❌** (every requirement met, nothing extra) and **Quality Approved/Changes-needed** (findings by severity, each with `file:line`). Rules that keep reviews honest:
- **Never pre-judge** — don't tell a reviewer "don't flag X" or "this is at most Minor." If you think a finding would be a false positive, let them raise it and adjudicate in the loop.
- When the implementer disclosed a deviation, **ask the reviewer to adjudicate it explicitly** (acceptable / flag) rather than assuming.
- **Fix Critical/Important with ONE fix subagent carrying all findings;** log Minor findings in the ledger for the final review; re-review after a correctness-critical fix.

### 4.8 The ledger (survives compaction — this is your memory)

At `C:/Users/shaku/corporate/.superpowers/sdd/progress.md`. It lists every task with its commit range and review status, plus a "Minor findings (for final review)" section. **Update it in the same turn you finish a task.** After a context compaction, trust the ledger + `git log` over your own recollection — the single most expensive failure mode is re-dispatching already-complete work because memory lost it. The ledger prevents that.

### 4.9 The final whole-branch review + merge

- After the last task, run `review-package <MERGE_BASE> <HEAD>` (MERGE_BASE = where the branch started, e.g. the commit before task 1), copy the ledger's Minor-rollup to a file, and dispatch **one opus reviewer** with: read-only verdict, honesty-architecture verdict, branch verdict, findings, and a triage of every rolled-up Minor (must-fix vs acceptable). This is where cross-task defects surface — Phase 4's F1 (WM mock-labeled-live) was caught HERE, at the last gate, and fixed at three layers before merge.
- **Merge flow:** `git push -u origin <branch>` → `gh pr create --base main --title ... --body ...` → poll `gh pr view N --json mergeable,statusCheckRollup` until CI (test 3.10 + test 3.12) is SUCCESS → `gh pr merge N --merge --delete-branch` → `git checkout main && git pull --ff-only` → verify on main (`pytest -q`, tool count, evals). Do NOT merge on red or pending CI.
- **Merging to a PUBLIC repo is outward-facing** — the user authorizes it explicitly (they said "push, open PR, wait for CI, land it"). Don't merge without that go.

### 4.10 Communicating with the user (how to report)

- Lead every turn's final message with the outcome (what happened / what you found), then supporting detail. The user reads the last message; put everything load-bearing there.
- Between subagent dispatches, one short status line ("Task N building… / review dispatched") — the ledger and tool results carry the record, don't narrate every step.
- Surface judgment calls for veto rather than burying them (e.g. "I trimmed WM from the registry — that deviates from the spec table; here's why; flag if you disagree").
- When the user is *asking* (not requesting a change) — "is there more we can do?", "why did you exclude these?" — the deliverable is your honest assessment, not immediate code. Answer, then ask if they want it built.

### 4.11 The rhythm, condensed

`task-brief → implementer (sonnet, background) → notification → review-package → reviewer (sonnet) → fix if needed → ledger line → next task`. Repeat until the plan is done. Then opus final review → fix wave → merge. Keep your own hands off the code except for recovery (committing a stalled subagent's green work) and tiny controller-level fixes. Your value is the briefs, the reviews, and never letting an honesty defect through.

### 4.12 The firewall security-review lesson (why the final review is non-negotiable, esp. for a security boundary)

The query firewall (PR #11) is the sharpest proof that the process catches what tests don't. Every per-task review passed; the suite was green at 841; the docstring *claimed* the bypass class was closed. Then the **adversarial final whole-branch review (opus)** — told to "attack the firewall as a security boundary" — found a **proven Critical bypass**, and it took **three successive rounds** to fully close, because it was a *class*, not a single bug:

1. **Round 1 — verbatim strings.** KQL `@"..."`/`@'...'` verbatim strings aren't modeled by the `'`/`"` state machine (`_strip_string_literals`/`first_statement`); a verbatim string ending in `\"` fools it into thinking the string never closes, so `@"x\" | union database('SecretDB').SecretTable` slipped a cross-database read (and stacked `.drop`) past ALL THREE gates. The take-0 rehearsal doesn't save it — a sibling DB the SP can reach binds cleanly.
2. **Round 2 — triple-backtick multiline strings.** Same class, different unmodeled form. The re-review that verified round 1 hunted for siblings and found it.
3. **Round 3 — `//` line comments.** Same class again. The re-review after round 2 did a *complete grammar enumeration* of KQL literal/comment forms and found `//` was the last one.

**The lessons, in order of importance:**
- **The final whole-branch review must be adversarial and run on the top model, especially for anything security-shaped.** Tell it to *attack*, to hunt bypasses, to enumerate the complete grammar — not just "check the diff." It found what 4 clean per-task reviews and a green suite did not.
- **Fix the CLASS, not the instance.** After round 1 we asked "is this the whole class?" — that framing is what surfaced rounds 2 and 3. When a bug has a root cause ("any unmodeled construct desyncs the parser"), enumerate the complete set and close all of it; don't whack one mole and declare victory. The final docstring carries the grammar-complete enumeration as an *auditable closure argument*, not a claim.
- **Re-review every security fix, and have the re-review look for siblings.** Each round's re-review found the next hole precisely because it was told to look beyond the reported instance.
- **Fail-closed is the right default at a security boundary.** The fix rejects `@"`, backticks, and `//` outright (even a `//` inside a URL) rather than trying to parse around them — because "parse around it" is exactly the fragility being exploited. Over-rejection is a disclosed usability cost (the agent rephrases); a bypass is a breach. Disclose the restriction honestly (it's in MCP-AGENT.md) — an honest limitation beats a clever gate that's wrong.
- **Don't trust a docstring that says "closed."** Round 1's fix docstring claimed class closure and was wrong (it conflated "string literal" with "any construct whose content the parser scans"). Make closure claims *provable* (enumerate) or don't make them.

If you build another validator/guard/firewall (e.g. a SQL leg for FUAM, Part 3-C): assume the same class exists in that grammar, enumerate its literal/comment/quoting forms up front, and have the adversarial review attack it before merge.

---

## Part 3 — The forward roadmap (restructured 2026-07-08)

*Addendum appended 2026-07-08 — this restructured Phases 4–9 view SUPERSEDES the original Part 3 "A–H" list above, which is retained there as sub-aliases (`3-A`…`3-H`).*

Phases run in order; each is its own brainstorm → spec → plan → 3 reviewers → SDD → adversarial final review → merge cycle (one PR per item, never batched). Gated items wait for the user to confirm the gate opened. Guardrails above every phase: read-only absolute · never label proxy/mock as live · never loosen the grounding bar · any new egress (alerts/UI/external-memory) is anti-exfil-reviewed before it ships.

### Phase 4 — Ship it live (foundation; in flight, ungated)
- **3-A** verified-query-library growth loop (in progress — Task 2 of 4).
- **3-B** deploy activation: redeploy the wheel so the 18 tools + firewall reach prod; point FABRIC_HISTORY_PATH at a durable path.
- Why first: everything downstream is data-starved until the current build is live and logging. Needs the user's go (shared-resource approval) + B0 secret rotation.

### Phase 5 — Interaction, Personality & Trust (combines old Interaction + Trust; do early)
- **Personality / UX** (deferred backlog `fabric-agent-ux-personality-backlog`): no tool names to users, act-don't-ask, right-size answers, humanized progress.
- **Response shaping:** lead with the verdict/answer, evidence on demand, honesty labels in plain language. Risk: low. Value: high perceived — a pure interaction layer over capability that already exists, and it makes every later phase land better.
- **Anti-exfil hardening:** egress control, output redaction, row/PII caps — proving alerts/UI/external-memory can't leak. (Gates Phases 6 & 9.) **Egress chokepoint contract (Phase 5.2, done):** every outbound sink MUST route through `fabric_audit_agent.egress.apply_egress_controls` — it is the only sanctioned way to emit outward. Wired today: `pipeline.run_audit`'s delivery, `job._alert_failure`'s failure card, `job._write_outputs`'s `latest.json`/`report.md`. NOT yet wired — MUST gate when activated: `adapters/ticketing.py`'s `open(findings)` port (gate the findings list, `sink="ticketing"`) and `conversation.py::build_concentration_alert`.
- **Eval flywheel:** grow the golden eval suite from real conversations — the 3-A pattern applied to quality. Grows from logs/interim store now; gains the durable backend in Phase 8.
- **Agent identity & scope (plumbing — read-only-safe, INERT until the Phase-7 grants land):** an identity-aware token-provider port (own Entra Agent Identity `fmi_path` → requesting-user OBO → today's shared-SP fallback; labels *which* identity served each call); an outbound-action allowlist framework (typed registry — `teams_notify`, `ado_create_ticket` — non-data-mutating, off by default); a versioned least-privilege scope manifest derived from `PERMISSIONS.md`; and the **invariant refinement**: read-only on data/capacity stays ABSOLUTE, plus a bounded, allowlisted, audited *outbound* set (never a data/capacity write). Design + rationale: [[agent-reach-identity-design]] (approach B — own-identity + OBO hybrid — now; approach A, the Entra-OIDC user-sign-in bridge, documented as a later increment).
  - **Follow-up (deferred from Phase 5.3 Task 2, `scopes.json`):** a collector-declares-required-scope drift test (each collector asserts the scopes it actually needs, checked against `scopes.json`) is DEFERRED — collectors don't declare their required scopes yet. Add that declaration + the drift test as a future `scopes.json` enhancement once collectors carry a `requiredScopes` attribute.

### Phase 6 — Proactivity & Alerting (read-only autonomy; needs Phase 5 anti-exfil)
- **Watchdog Job:** harden the scheduled sweep into an always-on monitor (dead-man's-switch already exists).
- **Autonomy:** self-triggered observe → investigate → deduce → surface, without a human prompt. Hard line: never auto-act; remediation stays forbidden.
- **Activator / Teams alerts:** delivery adapter (Fabric Data Activator / Teams webhook). Publishing → explicit auth + anti-exfil redaction + derived only from read-only data. Alert de-dup/state uses 3-B's interim store until Phase 8's durable memory upgrades it.

### Phase 7 — Authoritative Data + Authorizations (gated — org / data unlocks + admin grants; parallelizable as gates open)
- **FUAM collector** (3-C) — authoritative per-item CU + owner.
- **Workspace Monitoring as a live engine** (3-D) — re-add only with a real-data proof (the F1 regression must not return).
- **Authoritative CU unlock** (3-E) — the north star: verdict flips from indicative → authoritative.
- **semantic-link-labs spike** (3-F) — VertiPaq/BPA model-bloat analyzer; spike → ADD or SKIP.
- **ADO ticketing + change-correlation** (3-G) — "what deployed right before the spike."
- **Identity & permission activations (light up the Phase-5 plumbing):** provision the Entra Agent Identity (Blueprint → BlueprintPrincipal → Agent Identity, tenant-admin) + least-privilege role grants; enable the Databricks **"User authorization for Apps"** toggle (per-user OBO on the Databricks plane); the approach-A Entra OIDC sign-in bridge + Entra-OBO-to-Fabric delegated consent; the ADO outbound grant. Each is a separate admin gate — ask before starting.

### Phase 8 — Durable Memory (after authoritative data, deliberately)
- **Delta / Lakebase backend** for whats_changed history, investigation state, learned patterns. 3-B is the interim; this is the durable store. Placed after Phase 7 so it stores AUTHORITATIVE history, not proxy history — and it retro-upgrades Phase 6's alert de-dup/state.

### Phase 9 — UI, Operate & Harden (terminal — combines UI + 3-H; needs Phase 5 anti-exfil)
- **UI & scopes:** dashboards + scoped views per capacity / workspace / user; drill-downs over audit output. Pulls in the design/dataviz skills.
- **Operate & harden** (3-H): threshold tuning from real data, library/eval prune-and-grow from real logs, honesty labels kept accurate as sources change. Live usage drives "what next."
- Non-goals stay non-goals: ML anomaly detection (deterministic trees do it explainably), auto-remediation (a write — forbidden), always-on streaming beyond the sweep.

