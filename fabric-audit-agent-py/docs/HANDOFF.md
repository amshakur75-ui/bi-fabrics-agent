# Fabric Audit Agent — Build Handoff

**Written:** 2026-07-08 · **For:** the next Claude picking up this project cold.
**Read this top-to-bottom once, then start at Part 2 (the current build).**

---

## Part 0 — What this project is (30 seconds)

A **read-only** Microsoft Fabric / Power BI capacity & performance **audit agent**, all-Python, deployed on **Databricks**. It detects throttling / oversized models / refresh contention, gives an evidence-backed **optimize-vs-size-up verdict**, and runs a **User → Item → Owner 30% concentration alert**. It exposes its capabilities as **read-only MCP tools** an agent (Databricks-hosted Claude) calls conversationally, plus a scheduled sweep Job.

**The single most important rule: READ-ONLY IS ABSOLUTE.** No writes, no refreshes, no scale actions, ever. The only outward actions are delivering findings and sending notifications. Every design decision defers to this. A described-but-unemitted field, or mock data labeled as live, is treated as a **honesty defect** as serious as a write path.

- **Repo:** `github.com/amshakur75-ui/bi-fabrics-agent` — **PUBLIC**. No real tenant/client IDs or secrets in commits, ever.
- **Package path on the build machine:** `C:/Users/shaku/corporate/fabric-audit-agent-py/`
- **Git repo root:** `C:/Users/shaku/corporate/` (the package is a subdirectory; committed paths are prefixed `fabric-audit-agent-py/`).

---

## Part 1 — Everything done so far

### 1a. Where the code is RIGHT NOW

- **`main` @ `6193740`** — everything below through Phase 4 is merged and green.
- **Current working branch: `feat/query-firewall`** (2 commits ahead of main: the firewall **spec** `4f78a60` and **plan** `2c07c62` — docs only, no code yet).
- **Test baseline: `804 passed, 3 skipped`** (`cd fabric-audit-agent-py && python -m pytest -q`). Evals: `python -m fabric_audit_agent eval-agent` → 17/17; `eval-investigations` → 2/2.
- **16 read-only tools** live on `main` (`python -c "from fabric_audit_agent.tools import create_tool_definitions as f; print(len(f()))"` → 16).

### 1b. The tools that exist (16)

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

### 1c. Merged history (what each PR delivered)

- **PR #1 (Phase 1)** — offline investigation core: coverage-honest, abstaining, evidence-citing.
- **PR #3 (Phase 2)** — agent brain (the ReAct tool-loop `agent/loop.py`, `agent/investigator.py`) + deploy runbook.
- **PR #4 (Deploy Part B)** — Databricks Apps path: an agent App calls the MCP App.
- **PR #5 (Phase 3A)** — event depth & temporal patterns (offline, TDD).
- **PR #6/#7** — Phase 3 hygiene; deterministic event sampling + consistent `cuSeconds` units.
- **PR #8** — Phase-4 spec + Phase-5 approval sheet + MCP harvest inventory (`research/23-*`, `research/24-*`).
- **PR #9 (MCP Harvest Upgrade)** — 13 tasks: absorbed read-side patterns from 4 MS/OSS MCPs (fabric-rti-mcp, azure-mcp, johnib/kusto-mcp, mcp-kql-server). Delivered the KQL guard (`query/kql_guard.py`), client hardening (`request_readonly_hardline`), result envelopes + char-budget limiter (`query/envelope.py`), columnar + per-query cost metadata, time windows (`query/windows.py`, py3.10 Z-safe), `raw_events`, `describe_source`, `sample_events`, `capacity_diagnostics`, verify-in-Fabric deeplinks (`query/deeplinks.py`), honesty labels (`cuUnit`), log redaction (`query/redact.py`). 12 tools.
- **PR #10 (Phase 4)** — source-capability layer + 9 deepening ADDs, 15 tasks. Delivered `sources.py` (capability registry + coverage resolver), Tier-1 activity→event adapter, the `_resolve_event_sources` tiered seam (Tier-2 per-query → Tier-1 operation-level → mock, with `tier`/`coverageNote`/`hasRealCost` labels), throttle decomposition (`investigation/throttle.py`, honest 3-stage gate), time-to-throttle forecast (`investigation/forecast_throttle.py`), refresh-failure detector (`detectors/refresh.py`), dead-man's-switch (`job.py`), `analyze_dax` tool, the `diagnose` decision-tree engine (`investigation/diagnose.py`) + tool, `whats_changed` (agent memory + atomic store write), `user_timeline`, eval golden cases for all 16 tools + a permanent coverage invariant. 12 → 16 tools.

### 1d. Architecture you must respect

- **Functional core + swappable dict-ports.** Core is pure; all I/O is dependency-injected as dict-style ports (`{"collect": fn}`, `{"reason": fn}`, `{"deliver": fn}`, `{"history","append"}`, `{"load","save"}`). Same logic runs offline (mock adapters) or in prod (real adapters). Nothing in the core knows about HTTP/files.
- **Tools live in `tools.py::create_tool_definitions`.** Each tool is `{name, description, input_schema, handler}`. `mcp_server._make_tool_fn(handler, input_schema)` derives the FastMCP signature from the schema — a new tool needs only a complete `input_schema`, no registration edits beyond the docstring tool list.
- **The tiered event seam** `_resolve_event_sources(...)` returns `(events, series, meta)` with `meta` carrying `tier` (`perQuery`/`operationLevel`/`mock`), `coverageNote`, `hasRealCost`. It's the honesty spine — study it before touching any event tool.
- **The query building blocks** (all in `query/`, all reused by the firewall you're about to build): `kql_guard.assert_read_only_kql`, `first_statement`, `_strip_string_literals`, `assert_kusto_host`; `envelope.finish`/`cap_rows`/`to_columnar`; `deeplinks.kusto_deeplink`; `redact.redact_secrets`. In `tools.py`: `dry_run` (take-0 rehearsal), `_capacity_kusto_query`, `_queryplan_estimate`, `_memo_client`.

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

## Part 2 — What you are building RIGHT NOW: the Query Firewall

**Goal:** let the agent run **read-only, agent-authored ad-hoc KQL** it composes on demand (turning the fixed 16-tool menu into open-ended investigation), plus a **grounded query-library** of proven templates. 16 → 18 tools.

**The two documents that define it (READ BOTH FIRST):**
- **Spec:** `docs/superpowers/specs/2026-07-08-query-firewall-design.md`
- **Plan:** `docs/superpowers/plans/2026-07-08-query-firewall.md` — **4 tasks, complete code in every step.**

**The design in one paragraph:** A pure `query/firewall.py::validate_adhoc_kql` does static rejection (length → multi-statement → read-only gate via the existing `assert_read_only_kql` → a dangerous-operator deny-list covering `cluster(`/`database(`/`workspace(`/`app(`/`externaldata`/`evaluate` in both KQL flavors). Then the `run_kql` handler runs a **take-0 rehearsal** against the target engine's own binder (that IS the live-schema check — no schema cache, no homemade parser), then bounded execution + honest envelope + a redacted stdout audit line. Two engines: `capacity` (Eventhouse) + `la` (Log Analytics). `query_library` is an inert catalog of grounded plain-KQL templates that run through `run_kql` with **no privileged bypass** — a test enforces that every template passes the firewall (the "grounding bar").

**Why it's small (4 tasks):** it rides on Phase 4. The firewall is mostly *wiring the `assert_read_only_kql` validator that already exists but was never called*; the only genuinely new security surface is the ~10-line deny-list.

### Your execution directions (do this)

1. **You are already on `feat/query-firewall`** (branch exists, spec + plan committed). Confirm: `cd C:/Users/shaku/corporate && git branch --show-current` → `feat/query-firewall`; `cd fabric-audit-agent-py && python -m pytest -q` → `804 passed, 3 skipped`.
2. **Invoke the `superpowers:subagent-driven-development` skill** and execute `docs/superpowers/plans/2026-07-08-query-firewall.md` task-by-task, per Part 1g above. Create the ledger section for these 4 tasks first.
3. **Task order (from the plan):** T1 firewall (pure) → T2 `run_kql` + audit log → T3 `query_library.json` + tool + grounding-bar test → T4 eval golden cases + docs (16→18) + final sweep.
4. **Watch these load-bearing points** (they're in the plan, but they're where a defect would hide):
   - The deny-list scans **after** blanking string literals (reuse `_strip_string_literals`) and is **word-boundary anchored** (`app(` must not match inside `myapp(`) — both are tested.
   - `run_kql` must run the firewall **before** any engine call (a test asserts zero engine calls on a denied query).
   - The `| take maxRows` bound is appended **after** validation (can't reintroduce a rejected construct); `maxRows` hard-cap 1000.
   - **The grounding bar is load-bearing:** every library template must pass `validate_adhoc_kql`. If a template fails, FIX or DROP the template — NEVER loosen the bar. Author templates only against confirmed-live schema (`CapacityEvents` nested-`data` envelope; `PowerBIDatasetsWorkspace` confirmed columns — both listed in the plan). Ship what grounds (~15–25 expected); no padding.
   - The mock/unconfigured paths return an **honest** "no live query engine configured" note — that's the eval case, not a bug.
5. **Final whole-branch review** on opus, then merge per Part 1g. Expected end state: ~830+ passed, 3 skipped; 18 tools; eval-agent 19/19.
6. **Docs to update in T4:** `mcp_server.py` docstring tool list → 18; `MCP-AGENT.md` (add run_kql/query_library + a firewall paragraph + the audit-log deployment note: full query text is logged to the App log, which may contain user emails — org-policy parallel to `user_timeline`); `CLAUDE.md`/`STATUS.md` counts 16 → 18.

### Firewall exclusions (already decided — do NOT expand scope)

FUAM/SQL leg (gated), Workspace Monitoring engine (withheld until wired), parameterization machinery (2nd injection surface), usage-tracking storage (App is write-free; the stdout audit log replaces it), schema cache (rehearsal IS the live check), homemade KQL parser (rejected — the engine's binder is the real parser). Each reason is in the spec's "explicitly NOT pursued" section.

---

## Part 3 — Everything after the firewall (the forward roadmap, to the last phase)

Do these **in order**, each as its own brainstorm → spec → plan → subagent-driven execution → review → merge cycle. Do NOT batch them; each is a separate PR. Several are **gated** — do not start a gated item until the user confirms the gate opened.

### Next up (build when the firewall merges)

**A. Verified-query library growth loop (small, ungated).** The firewall ships with the stdout audit log as the learning signal. Once real ad-hoc queries flow, mine the App logs for the most-repeated *allowed* query shapes and promote them into `query_library.json` (each still must pass the grounding bar). This is a recurring ~10-minute PR, not a one-time build. It's the payoff for logging — do a first pass after the agent has real usage.

**B. Deploy-switch activation (ops, ungated, needs the user).** Two things flip capability on without new code: set `FABRIC_HISTORY_PATH` on the MCP App to the Job's history file → `whats_changed` goes live; redeploy the App/Job wheel so the 18 tools + firewall reach production. Coordinate with the user; confirm the read-only App still can't write.

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

- [ ] `cd C:/Users/shaku/corporate` — confirm branch `feat/query-firewall`, `git log --oneline main..HEAD` shows the spec + plan commits.
- [ ] `cd fabric-audit-agent-py && python -m pytest -q` → `804 passed, 3 skipped`.
- [ ] Read `docs/superpowers/specs/2026-07-08-query-firewall-design.md` then `docs/superpowers/plans/2026-07-08-query-firewall.md`.
- [ ] Invoke `superpowers:subagent-driven-development`; create the ledger; execute the 4 tasks with per-task review.
- [ ] Final whole-branch review (opus) → fix wave → merge (push, PR, CI green on 3.10+3.12, merge, sync, verify 18 tools on main).
- [ ] Then Part 3 item A/B; hold C–H until their gates open (ask the user).
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

