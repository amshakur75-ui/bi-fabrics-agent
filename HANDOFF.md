# HANDOFF — bi-fabrics-agent, mid-execution of master-integration-plan.md

**Handed off:** 2026-08-07. Machine A ran out of usage. Continue on Machine B.

**Repo:** https://github.com/amshakur75-ui/bi-fabrics-agent (branch `main`).
**Last local commit:** `0ada347` — verify Machine B sees this after `git clone` / `git pull`.

---

## The situation in one paragraph

You are executing `fabric-audit-agent-app/tasks/master-integration-plan.md` (Phases 0–7) under the
autonomy contract in `fabric-audit-agent-app/tasks/CLAUDE-CODE-EXECUTION-PROMPT.md`. **Phases 0–6
are complete or dispositioned; only Phase 7 close-out remains.** Full test suite is
**1766 passed / 55 subtests** (baseline was 1547; +219 tests, zero regressions). Your job is to
finish Phase 7 items **7.2 / 7.3 / 7.4**, plus dispose of the last 11 unchecked plan items
(each already has a logged disposition), then write the final top-of-log summary and stop.

---

## Where to find EVERYTHING

Read these files in order — they are the durable record and continuation point:

1. `fabric-audit-agent-app/tasks/CLAUDE-CODE-EXECUTION-PROMPT.md` — the autonomy contract (per-item
   loop with before/after blast-radius review, STANDING RULE, hard constraints).
2. `fabric-audit-agent-app/tasks/EXECUTION-LOG.md` — the running record with every item's
   disposition and the "definition of done" final-summary header already drafted at the top.
3. `fabric-audit-agent-app/tasks/master-integration-plan.md` — the plan, checkboxes reflect current
   state. 11 items remain unchecked — each has a disposition in EXECUTION-LOG.md.
4. `fabric-audit-agent-app/tasks/tightening.md` — Parts 0–26, the evidence base and STANDING RULE.
5. `fabric-audit-agent-app/tasks/GAPS-RECONCILIATION.md` — Phase 0.1's reconciliation (93 items ×
   status + phase mapping + the 16 uncovered items already absorbed into phases).
6. `fabric-audit-agent-app/tasks/BLAST-RADIUS-CORE.md` — Phase 0.3's core-file map (tools.py
   registration pattern, agent.py↔loop.py twin seams, kql_guard.py frozen signatures).
7. `fabric-audit-agent-app/GAPS-AND-ISSUES.md` — the ledger (Phase 7.3 updates this file).

---

## What was done (12 commits this run, all on `main`)

Latest first:
```
0ada347 chore(integration): Phase 6.6/6.7 verifications + plan checkbox updates + final summary header
83da84b feat(integration): Phase 3.8/3.9/3.10 + 2.2/2.6 + 5.4 tool-surface wiring
519239e feat(integration): Phase 3 resolve layer port (10 modules, 100 tests)
1f963dc chore(integration): log Phase 2/5 port status + integration sequence
8d9e73b feat(integration): Phase 2 KQL audit engine + Phase 5 export layer (ports)
4bd5382 feat(integration): Phase 4.1-4.4 statistical rigor (stats.py) + 4.5-4.11 dispositions
a820220 chore(integration): Phase 0 complete + Phase 1.6/1.8/1.9 verified; 1.7 scoped
c922099 feat(integration): Phase 0 pre-flight + Phase 1.1-1.5 LA collector hardening
97da613 fix(alerts): notification center 500 — bind chatIds IN-list instead of = ANY()
2c5a326 fix: LLM chat titles, daily-summary 404, alert timing anchor, concentration visibility+gate
24760ae fix(titles): compress concentration phrasing + smarter, safer backfill
0cbe3bd fix(titles): short glanceable chat names — generators + backfill
```

Highlights of what landed:
- **Phase 0:** plugin data extracted to `fabric_audit_agent/data/plugin/`; GAPS reconciled; core-file
  blast-radius map produced; baseline recorded.
- **Phase 1:** Log Analytics HTTP hardening (55s timeout, retry, response-shape validation) in
  `adapters/clients.py`. Items 1.4/1.5/1.6/1.7/1.8/1.9 verified already-correct — no redundant edits.
- **Phase 2:** `fabric_audit_agent/query/kql_audit_rules.py` — the ported 4-rule + 8-preflight
  audit engine + `parse_kusto_error`. Wired into `query/firewall.py` (`audit_adhoc_kql`, new stage
  `"audit-rule"`). `kql_guard.py` signatures untouched (frozen).
- **Phase 3:** `fabric_audit_agent/resolve/` — 10 modules ported (routing_table, term_resolver,
  field_resolver, field_aliases, schema_link, catalog, usage_query_builder, artifact_lookup,
  text_normalize, __init__). 7 tools registered in `tools.py` (resolve_term, resolve_field,
  field_usage_query, workspace_usage_query, field_search, field_detail, artifact_lookup). System
  prompt appended surgically. **Three loop hooks land in BOTH twins** via a single shared
  `agent_server/loop_hooks.py` so they can't drift.
- **Phase 4:** `fabric_audit_agent/stats.py` — shared statistical primitives (OLS+R², median+4×MAD
  spike + severity bands, ≥6-point/±15% trend gate, min-volume floor). Wired additively into
  `forecast.py`/`anomaly.py` (adds `r2`/`weakFit`/`directionStrict`/`severity`/`isSpikeMad` fields;
  legacy vocabulary unchanged so no existing consumer breaks).
- **Phase 5:** `fabric_audit_agent/export/` (html_utils/html_report/xlsx_report). `agent_server/
  export_tool.py` registers `export_html_report`/`export_xlsx_report` following chart_tool.py's
  pattern. `openpyxl>=3.1` added to `pyproject.toml`.
- **Tool count:** 26 → **33**. **`agent_server/eval_data/agent_cases.json`** got 7 new golden cases.
- **`CLAUDE.md`** + **`STATUS.md`**: corrected stale "841 passed / 18 tools / byte-identical-to-Node"
  → "1766 passed / 33 tools / Node retired".

---

## What remains (11 unchecked items — each has a logged disposition)

Every item below is in `master-integration-plan.md` unchecked; each already has a written
disposition in `tasks/EXECUTION-LOG.md`. Your job is to (a) confirm each disposition against current
code, (b) execute the ones flagged executable, (c) write the final Phase-7.4 sweep + top-of-log
summary, (d) commit + push.

| Item | Status the log records | What YOU do |
|---|---|---|
| **4.5** | Threshold source unified via config; routing detectors *through* concentration_gate() is a small follow-up (drops `==` edge). | Verify the disposition in the log; if you agree, mark done with a one-line note. If you disagree and want to route them through the gate, do so with regression tests. |
| **4.11** | SKU / base-CU mismatch cross-check — scoped, needs live verification against real capacity data. | Implement `check_sku_base_consistency(configured_base, live_base)` in `investigation/sku.py` + surface a `skuMismatch` flag. Tests OK; the live-fire part goes into 7.2's checklist. |
| **5.5** | `kql-viewer.tsx` (frontend U4 KQL display) — depends on B1 real query text. | Node/TS change under `e2e-chatbot-app-next/client/src/components/elements/`. Ship as `code-block.tsx` extension. Same deploy pattern as prior title fixes (see commit 2c5a326). |
| **5.6** | chart.tsx Newell brand-token parity check. | Read the file, confirm/apply `#288FC2/#01405C/#696158`, Arial. |
| **6.1** | Teams delivery — Phase 10 dependency (deferred by design). | Leave deferred with reason in log. |
| **6.2** | Delta memory tables — verification, not new code. | Grep for the 4 tables; verify no partitioning + liquid clustering + 90-day retention; log result. |
| **6.3** | HR enrichment — optional; only if attribution enrichment wanted this round. | Leave deferred unless you want it. |
| **6.4** | EXTERNALMEASURE thread — stays as-is pending Jiao/Vegasina/Srikanth. | Leave deferred with reason. |
| **7.2** | Live checks against deployed app — the ONLY permitted deferral. | Write the exact checklist at the end of EXECUTION-LOG.md (the 5 canned questions + 3 new ones from the plan). Don't try to run them if you can't reach the app. |
| **7.3** | Update `fabric-audit-agent-app/GAPS-AND-ISSUES.md` — close every landed item with the phase id. | Use `tasks/GAPS-RECONCILIATION.md` as the source of truth for what's closed. |
| **7.4** | Final tightening.md Parts 0–26 sweep — one-line disposition per Part (done/deferred-why/superseded-by-what). | Append to EXECUTION-LOG.md. No silent drops. |

**When 7.2/7.3/7.4 are done**, write the top-of-log final summary (a section is already drafted at
the top of EXECUTION-LOG.md — edit it to reflect final state), commit, push, stop.

---

## The RULES that govern every change (do not violate)

From `tasks/CLAUDE-CODE-EXECUTION-PROMPT.md` — the STANDING RULE and hard constraints:

1. **Blast radius before + after every change.** Grep every caller, every sibling implementing the
   same pattern, every test. Write to EXECUTION-LOG.md what you checked.
2. **Read-only toward Fabric/Power BI/Azure.** No writes, no mutations.
3. **agent.py + loop.py are TWINS.** Any loop change lands in BOTH, structurally identical.
   Three loop hooks live in `agent_server/loop_hooks.py` — do NOT inline them again.
4. **`system_prompt.py` is single-sourced** (ADR-001). Do not create a second copy.
5. **`kql_guard.py` signatures are frozen.** New audit rules live in `query/kql_audit_rules.py`.
6. **Do NOT rerun the plugin `.cjs` build scripts.** Their inputs aren't in the repo. Pre-built
   `data/plugin/*.json` outputs are authoritative.
7. Every code change runs the FULL suite (`cd fabric-audit-agent-app && python -m pytest -q`).
   Any regression against **1766 passed** blocks progress.

---

## How to commit and push (matches Machine A's pattern)

Machine A committed every landed batch with descriptive multi-line messages using a temp file for
the message body (Bash heredocs on Git Bash caused stray `@` chars — file-based messages avoided
that). Follow the same pattern:

```bash
# 1. See what changed
git status --short

# 2. Stage precisely (avoid `git add -A` unless you've reviewed everything)
git add fabric-audit-agent-app/<specific paths>

# 3. Commit with a file-based message (writes to a scratchpad file, then -F)
# Message convention: type(integration): <what phase/item> — <one-line summary>
# Include a "Co-Authored-By: Claude <noreply@anthropic.com>" trailer.

# 4. Full-suite check BEFORE the commit if you touched code
cd fabric-audit-agent-app && python -m pytest -q
# expect: 1766 passed  (or higher after your additions)

# 5. Push
git push origin main
```

**Note on classifier-blocks:** on Machine A the auto-mode classifier blocked `git push` and any
raw Lakebase mutation (bulk chat-title backfill). If the classifier blocks a command on Machine B,
ask the user to run it in their own shell — do NOT try to work around it.

---

## Environment on Machine B

- **Clone:** `git clone https://github.com/amshakur75-ui/bi-fabrics-agent.git`
- **Python deps:** in `fabric-audit-agent-app/`: `pip install -e ".[dev]"` (needs `openpyxl>=3.1`,
  now in pyproject).
- **Databricks CLI profile:** `fabric-test` (only needed if the new machine wants to deploy or hit
  Lakebase live — Phase 7.2 live checks). `databricks auth login --profile fabric-test` if the
  token has expired.
- **Full suite command:** `cd fabric-audit-agent-app && python -m pytest -q` → expect **1766 passed**.

---

## The prompt to hand to the fresh Claude on Machine B

Paste the block between the `---` lines into the fresh session as your very first message.
It is self-contained (references files, not this HANDOFF.md, so nothing depends on this doc
staying loaded in context).

---

Read `fabric-audit-agent-app/tasks/CLAUDE-CODE-EXECUTION-PROMPT.md` in full — it is your autonomy
contract. Then read, in order, `fabric-audit-agent-app/tasks/EXECUTION-LOG.md` (the running record
of everything Machine A did, and the durable continuation point), `fabric-audit-agent-app/tasks/
master-integration-plan.md` (the plan; 11 items unchecked), `fabric-audit-agent-app/tasks/
tightening.md` (Parts 0–26 + the STANDING RULE), `fabric-audit-agent-app/tasks/GAPS-
RECONCILIATION.md`, and `fabric-audit-agent-app/tasks/BLAST-RADIUS-CORE.md`.

The full test suite is currently **1766 passed / 55 subtests**. Every landable code phase (0–6) is
complete or explicitly dispositioned. Your job is Phase 7 close-out only:

- **7.2** — write the live-check checklist at the end of EXECUTION-LOG.md (the 5 canned questions
  from the plan + the 3 new ones for the resolve→build→execute path + html/xlsx export). This is
  the plan's ONLY permitted deferral and must be explicit, not silent.
- **7.3** — update `fabric-audit-agent-app/GAPS-AND-ISSUES.md`: close every item Machine A landed,
  citing the phase id. Use `tasks/GAPS-RECONCILIATION.md` as the source of truth.
- **7.4** — append a `tightening.md Parts 0–26 disposition sweep` section to EXECUTION-LOG.md, one
  line per Part (done / deferred-why / superseded-by-what). No silent drops.
- Also finish the 8 non-7.x unchecked items (4.5, 4.11, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4) — each
  already has a disposition in EXECUTION-LOG.md; either confirm and mark done, or execute + commit.
  4.11 (SKU cross-check) is the one small piece of remaining code work I'd expect you to write.

**Rules (from the execution prompt, non-negotiable):**
- Before + after review on every change; write to EXECUTION-LOG.md what you grep'd.
- `agent.py` + `loop.py` are twins — any loop change lands in BOTH (already via shared
  `agent_server/loop_hooks.py` — do not inline the hooks again).
- `kql_guard.py` signatures are frozen; new audit rules live in `query/kql_audit_rules.py`.
- `system_prompt.py` is single-sourced (ADR-001) — do NOT create a copy.
- Do NOT rerun the plugin `.cjs` build scripts.
- Read-only toward Fabric/Power BI/Azure — no writes, no mutations.
- Full suite must stay green (`cd fabric-audit-agent-app && python -m pytest -q` → **1766**+).
- Commit convention: multi-line messages via a temp file (`git commit -F <file>`), never via a
  Bash heredoc (Git Bash mangles them). Include a `Co-Authored-By: Claude <noreply@anthropic.com>`
  trailer. Every commit for this integration is prefixed `feat(integration):` or `chore(integration):`.

**Definition of done** (from the execution prompt): all phases checked off; full suite green vs
baseline; GAPS reconciled; tightening Parts 0–26 each carrying a disposition; EXECUTION-LOG.md
tells the complete story. Then and only then, write the final top-of-log summary (a draft already
sits at the top of EXECUTION-LOG.md — edit it to reflect the final state), commit, and push. Then
stop. Do not ask clarifying questions along the way; make the reasonable call and record it.

Begin by running `git status && git log --oneline -12 && cd fabric-audit-agent-app && python -m
pytest -q` to confirm you're in sync with Machine A. Then read the six files listed above and start
Phase 7. Do not stop until 7.4 is complete and pushed.

---
