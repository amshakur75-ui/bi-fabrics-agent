# Implementation Plan: Proactivity & Alerting (Phase 6) — v2

**Spec:** `docs/superpowers/specs/2026-07-09-proactive-alerting-design.md`
**Branch:** `feat/proactive-alerting` (off `main` `40e5086`; spec commit `26dc3b9`)
**Method:** superpowers SDD, TDD, per-task review. Offline; deterministic; no MCP tool added (tool count stays 18). Deploys with the scheduled Job/bundle.
**v2:** after 3 plan reviews (coverage, technical-accuracy, opus-improvability). Change log at bottom.

## Overview
Make the scheduled sweep proactive: after each run, decide whether a **material change** vs the previous run warrants an alert (low-noise), and if so deliver it via **email (inert until SMTP configured)** through a **typed outbound allowlist** and the **5.2 egress gate**. Read-only autonomy: observe → surface, NEVER auto-act. Teams/Activator/Graph/ADO stay deferred to Phase 7 (registered-but-disabled).

## Architecture decisions (grounded in the code)
- **`decide_alert` reads the envelope + PREVIOUS history.** `run_audit` appends the current run to the store *inside* the run (`pipeline.py:101`), so after it returns `store["history"]()[-1]` is the *current* run. The Job MUST capture `prev_history = store["history"]()` **before** calling `run_audit` and pass that in — otherwise "resolved"/"verdict-change" compare a run against itself. Single most important wiring fact; baked into Task 4. **The `history()` contract is load-bearing:** it MUST return an immutable snapshot (a fresh list) so the subsequent in-run `append` can't mutate the captured `prev_history`. `create_local_store` re-reads from disk (satisfies this); the prod Delta/UC store must too — stated as a contract + proven by a Task-4 test using a store whose `append` mutates the same underlying object.
- **Signals are computed from per-key LEVEL deltas, not string-matching.** `decide_alert` compares current keys/levels against `prev_history[-1]`. Level rank **Critical > Warning > Info**. This one mechanism yields *new* / *worsened* / *resolved* and covers BOTH annotated escalation (`automation/escalate.py`) and any detector/Claude-driven severity increase — dropping the brittle substring coupling on `score.reason`. `digest.newCount` is a cross-check, not the source of truth.
- **Snooze-safe "resolved".** A snoozed finding moves to `data.suppressed` (`pipeline.py:155-156`), NOT `data.findings`. "Current keys" therefore = `data.findings ∪ data.suppressed` (both carry `key`). *Resolved* = a prior non-suppressed key absent from that union — so a snooze is never mis-reported as a resolution (which would be both noise and an honesty defect).
- **Two additive history fields.** History run entries (`pipeline.py:101-108`) store `{runAt, tenant, metrics, findings:[{key,level,where,what,suppressed}]}` — no verdict, no SLA count. Add `"verdictDecision"` and `"slaBreachedCount"` to the appended run (additive, backward-compatible; `whats_changed` ignores unknown keys). Both are `.get()`-read with None-safe fallback → absent (old runs / first run) never produces a false alert. These enable *verdict-change* and *SLA-increase* as genuine change signals (not alert-on-standing-state).
- **Low-noise is on-CHANGE, never on-state.** SLA breach alerts only when `breachedCount` **increased** vs the prev run (a standing breach across 10 sweeps → one alert, not ten). Verdict change alerts only on a move to a **worse** decision (ordinal `healthy < optimize < size-up`; transitions to/from `unknown` are recorded in `changes` but do NOT by themselves qualify — avoids telemetry-gap flapping). `changes` contains ONLY the keys that qualified at/above `min_level`, so the email body matches the alert reason.
- **Email mirrors the Teams delivery contract.** `create_email_delivery(env, *, sender=None) -> {"deliver": fn(envelope)}`, same shape as `create_teams_delivery` (`adapters/delivery_teams.py`). Body = `build_markdown_report(envelope)` (`report_md.py`) as text/plain. The SMTP sender is **injected** for tests (never real network), exactly as Teams injects `http`.
- **Outbound allowlist is the choke point + disclosure carrier.** `dispatch_outbound(action_type, payload, env, *, sinks)` refuses any type not registered/enabled, runs `safe, meta = apply_egress_controls(payload, sink="alert")` (`egress.py:131`), **injects `disclosure_line(meta)` into `safe["summary"]`** (mirroring the three existing sinks at `pipeline.py:167-169`, `job.py:125-126`, `job.py:191-192`) so the emailed report discloses any capped/withheld content, THEN calls the sink. Enablement is **static** (email_notify permitted; teams_notify/ado_create_ticket disabled→P7); `dispatch_outbound` does NOT read `SMTP_*` — runtime configured-vs-inert is the *sink's* sole responsibility (one source of truth for the "send when unconfigured" invariant).
- **Defense-in-depth on the new exfil surface.** `delivery_email` documents "receives an already-gated payload; only reachable via `dispatch_outbound`" AND self-gates internally (`apply_egress_controls` is deterministic and idempotent on already-safe input — a second pass finds nothing to redact and `disclosure_line` returns `None`, so no double disclosure). Email is the new outbound channel; this closes the "wired outside the gate" hole permanently rather than by convention.
- **Failure isolation.** An alert-path exception never fails the sweep. The existing dead-man's-switch (`_alert_failure`, `job.py:173`) keeps its current gated behavior and ADDITIONALLY dispatches `email_notify` (inert unless SMTP configured); a failure card has no verdict/digest, so the email subject falls back to `summary`.

## Confirmed interfaces (verified by reading the code)
- `build_digest(findings, history) -> {totals, byDomain, newCount, recurring}` — `automation/digest.py`.
- `apply_escalation(findings, history)` — `automation/escalate.py`; escalated finding `score.reason` ends `"(escalated: unresolved 3 consecutive runs)"`.
- `apply_egress_controls(payload, *, sink, max_chars=12000) -> (safe, meta)`; `disclosure_line(meta)` — `egress.py:131/185`; deep-copies, fail-closed.
- `create_local_store(file_path, keep=180) -> {history, append}` — `adapters/store_local.py`; `history()` returns a list (oldest→newest).
- `create_teams_delivery(http, webhook_url) -> {deliver}` — `adapters/delivery_teams.py` (the contract to mirror).
- `build_markdown_report(envelope) -> str` — `report_md.py`.
- `run_unified_job(env, out_dir, reasoner, delivery, store, config, agent_id, tenant, now)` — `job.py:333`; calls `run_audit(...)` then `_write_outputs`. `_csv_delivery(env)` (`job.py:108`), `_default_store(env)` (`job.py:81`), `_alert_failure(exc, env, now_iso)` (`job.py:173`).
- Envelope: `wrap_envelope(...)` → `{summary, data:{...}}`; `data.verdict` always, `data.digest` when history present, `data.sla` only when `breachedCount>0`, `data.findings[].score.level` ∈ {Critical,Warning,Info}.

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → **1052**. Keep green + add new tests.

---

## Task 1 — `automation/alerting.py::decide_alert` (pure) + history `verdictDecision`/`slaBreachedCount` + tests
**Description:** Add the pure alert-on-change decision, plus the two additive history fields it needs for verdict-change and SLA-increase.
**Interface:** `decide_alert(envelope, prev_history, *, min_level="Warning") -> {"alert": bool, "reason": str, "changes": {...}}`.
- `changes` carries only the QUALIFYING material deltas (≥`min_level` where level-gated): `{"new": [keys], "worsened": [keys], "resolved": [keys], "verdictChange": {"from","to"}|None, "slaIncrease": {"from","to"}|None}`.
- **Level rank** helper: `Critical(3) > Warning(2) > Info(1)`; `min_level` maps to a rank floor (default Warning ⇒ drop Info-only signals).
- **Current keys** = `{f.key for f in data.findings} ∪ {s.key for s in data.get("suppressed", [])}` (snooze-safe).
- **New** = current-active keys (from `data.findings`) not in `prev_history[-1].findings` keys; kept iff its level ≥ floor.
- **Worsened** = key in both prev and current-active with `rank(current) > rank(prev)`; kept iff the *target* level ≥ floor. (Covers annotated escalation AND any severity increase — no `score.reason` string match.)
- **Resolved** = a prev non-suppressed key absent from the current union; kept iff its *prev stored* level ≥ floor. (Never alone raises severity, but is surfaced.)
- **Verdict-change** = `data.verdict.decision` vs `prev_history[-1].get("verdictDecision")`; qualifies as material ONLY when the move is to a *worse* decision on the ordinal `healthy(1) < optimize(2) < size-up(3)`. `unknown` transitions are recorded in `changes.verdictChange` but do NOT by themselves set `alert`. Absent prev field → no verdict-change.
- **SLA-increase** = `data.get("sla",{}).get("breachedCount",0)` vs `prev_history[-1].get("slaBreachedCount",0)`; material only when current > prev. A standing breach (equal count) does NOT re-alert. Absent prev field → treated as 0 (first observation of a breach counts as an increase).
- `alert` = True iff any of {new, worsened, resolved, verdict-worse, sla-increase} is non-empty after gating.
- Empty `prev_history` (first run) → `reason="baseline"`; only *new* material findings and a first SLA breach qualify; no resolved/verdict-change. Pure/deterministic; no I/O.
**Also (`pipeline.py`):** compute `verdict = build_capacity_verdict(facts, flags)` **once, before the store append** (both `facts` [line 70] and `flags` [line 77] are already in scope; today it's computed at line 122 — move it up and reuse at line 130, deduping the second call — pure function, zero behavioral change). Add to the appended run dict (line 101): `"verdictDecision": verdict["decision"]` and `"slaBreachedCount": summarize_sla(findings)["breachedCount"]` (findings are SLA-annotated by `assess_sla` at line 96; reuse the value at line 136 or accept a duplicate pure call). Confirm the append's findings projection still reads only `key/level/where/what/suppressed` (unchanged by the intervening coaching/confidence steps).
**Acceptance criteria:**
- [ ] Each signal in isolation → `alert:True` with the correct `reason`/`changes`: new-at-Warning, worsened (Warning→Critical), resolved, verdict-worse (optimize→size-up), sla-increase.
- [ ] No-change run → `alert:False`, empty `changes`. **Standing SLA breach (equal count) → no alert.** **Verdict move to a *better* decision (size-up→healthy) → not material** (recorded, no alert).
- [ ] **Snooze-safe:** a finding that moved active→`data.suppressed` is NOT reported as resolved (no alert from that alone).
- [ ] `min_level` honored (Info-only new finding → no alert at default; alerts at `min_level="Info"`); `changes` lists only qualifying keys.
- [ ] `unknown` verdict transition → recorded in `changes.verdictChange`, does not by itself set `alert`.
- [ ] First run (`prev_history=[]`) → `reason="baseline"`, no `resolved`/`verdictChange`; new material findings + first SLA breach still alert.
- [ ] Appended history run carries `verdictDecision` + `slaBreachedCount`; a `prev_history` entry lacking either → no crash, no false positive.
- [ ] Pure/deterministic; suite green.
**Files:** `fabric_audit_agent/automation/alerting.py`, `fabric_audit_agent/pipeline.py`, `tests/test_alerting.py` (+ touch the pipeline history test). **Deps:** None. **Scope:** M.

## Task 2 — `outbound.py` typed allowlist `dispatch_outbound` + tests
**Description:** The typed outbound-action registry (the 5.3-C item, landing with its first consumer). Enforces "typed allowlist, never open-ended", carries the egress disclosure, and routes every payload through the egress gate.
**Interface:** `dispatch_outbound(action_type, payload, env, *, sinks) -> {"dispatched": bool, "actionType": str, "disclosure": str|None, "reason": str|None}`.
- Registry: module-level typed dict with **STATIC** enablement — `email_notify` → enabled (permitted type); `teams_notify`, `ado_create_ticket` → **registered but DISABLED (→ Phase 7)**. `dispatch_outbound` does NOT read `SMTP_*` — the sink owns configured-vs-inert (single source of truth).
- Unknown or disabled type → refuse (`dispatched:False`, `reason`), send nothing, never raise on refusal.
- Enabled path (in order): `safe, meta = apply_egress_controls(payload, sink="alert")`; `line = disclosure_line(meta)`; if `line` and `safe` is a dict, `safe["summary"] = f"{(safe.get('summary') or '').rstrip()} {line}".strip()` (mirror `pipeline.py:167-169`); **then** `sinks["email"]["deliver"](safe)`; return `disclosure=line`.
- Nothing data-mutating is registrable (registry has no write/scale/refresh types; assert the registry keys by test).
**Acceptance criteria:**
- [ ] `email_notify` enabled → payload passes through `apply_egress_controls` (assert a planted secret in the payload is masked at the sink) then delivered.
- [ ] **Disclosure injected:** when the gate caps/redacts, the sink receives a `summary` that includes the disclosure sentence (assert substring); when nothing is dropped, no spurious disclosure appended.
- [ ] `teams_notify` / `ado_create_ticket` → refused (disabled→P7); no send; no raise.
- [ ] Unknown action type → refused; no send.
- [ ] Deep-copy honored — the caller's payload object is unmutated after dispatch.
- [ ] Registry contains no data-mutating action type (explicit assertion on registry contents).
- [ ] Suite green.
**Files:** `fabric_audit_agent/outbound.py`, `tests/test_outbound.py`. **Deps:** None. **Scope:** M.

## Task 3 — `adapters/delivery_email.py::create_email_delivery` (inert until configured, self-gating) + tests
**Description:** Stdlib-SMTP email delivery mirroring the Teams delivery contract; a no-op until SMTP is configured; self-gates as defense-in-depth on the new outbound channel.
**Interface:** `create_email_delivery(env, *, sender=None) -> {"deliver": fn(envelope)}`.
- Config from env: `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_FROM`, `SMTP_TO` (comma-split → multiple recipients), optional `SMTP_USER`/`SMTP_PASSWORD`, `SMTP_STARTTLS` (default true).
- **Inert until `SMTP_HOST`+`SMTP_TO` set** → `deliver` returns `{"delivered": False, "reason": "unconfigured"}`, sends nothing, never raises (mirrors the `_csv_delivery` no-op pattern). This is the *sole* owner of the "send when unconfigured" invariant.
- **Self-gate (defense-in-depth):** `deliver` runs `safe, _ = apply_egress_controls(envelope, sink="alert")` on its input before rendering. Idempotent on already-gated input (a second pass redacts nothing; `disclosure_line` returns `None` → no double disclosure). Docstring states: "receives an already-gated payload; only reachable via `dispatch_outbound`; self-gates as a backstop."
- Configured → build an `email.message.EmailMessage`; **subject** = `[Fabric audit] {verdict.decision}` when present, else falls back to `summary` (failure cards have no verdict/digest); body = `build_markdown_report(safe)` as text/plain. Send via the **injected** `sender` (default a thin `smtplib.SMTP` wrapper doing connect/STARTTLS/login/send). Tests inject a fake sender — **never real network**.
- Returns `{"delivered": True, "target": <to-list>}`.
**Acceptance criteria:**
- [ ] Unconfigured env → no-op (no send, no error), `delivered:False`.
- [ ] Configured → injected fake sender receives one message with correct from/to (comma-split)/subject; body equals the markdown report text.
- [ ] **Failure-card shape** (`{"summary": "...FAILED..."}`, no verdict/digest) → no crash; subject falls back to `summary`.
- [ ] Self-gate proven: an un-gated envelope with a planted secret passed directly to `deliver` → the sent body has the secret masked (backstop works even off the `dispatch_outbound` path).
- [ ] STARTTLS + auth path exercised against the fake sender (no real socket).
- [ ] `deliver(envelope)` contract matches `delivery_teams`; suite green.
**Files:** `fabric_audit_agent/adapters/delivery_email.py`, `tests/test_delivery_email.py`. **Deps:** None. **Scope:** S/M.

---

### Checkpoint: components
- [ ] Tasks 1–3 green in isolation; `decide_alert` pure; email inert-by-default; allowlist refuses disabled/unknown types; egress gate proven in the outbound path.

---

## Task 4 — Job wiring in `run_unified_job` (+ dead-man's-switch through the allowlist) + tests
**Description:** Wire alerting into the scheduled sweep, failure-isolated, using previous-run history.
**Changes (`job.py`):**
- In `run_unified_job`: **before** `run_audit`, `prev_history = store["history"]()`. After the envelope, `decision = decide_alert(envelope, prev_history)`; if `decision["alert"]`, build `sinks = {"email": create_email_delivery(env)}` and `dispatch_outbound("email_notify", envelope, env, sinks=sinks)`. Wrap the whole alert block in try/except that logs and swallows (an alert-path error NEVER fails the sweep). Return the envelope unchanged.
- **Do NOT** add a separate `run_diagnosis`/re-investigation call — the envelope already carries the deep analysis (spec §2). (Negative check to prevent reintroducing the redundancy.)
- `_alert_failure` (dead-man's-switch): keep its existing gated Teams path unchanged; **ADDITIONALLY** dispatch `email_notify` via `dispatch_outbound` (inert unless SMTP configured) — addition, not replacement. The failure card is a minimal `{"summary": ...}` dict; `build_markdown_report` tolerates it and the email subject falls back to `summary`. Keep it independent of the main alert path and failure-isolated.
- No change to the read-only pipeline logic, tool count, or MCP surface.
**Acceptance criteria:**
- [ ] Injected fake email sink receives a **gated** alert only when `decide_alert` says alert; a no-change run sends nothing.
- [ ] `prev_history` captured **before** `run_audit` — test with a store whose `append` mutates the *same underlying list* returned by `history()`; a run that resolves the only prior finding still triggers a `resolved` alert (proves the snapshot is captured pre-append, not compared against the just-appended current run). This also exercises the `history()`-immutable-snapshot contract.
- [ ] An exception in the alert path does **not** fail the sweep (envelope still returned) — failure isolation.
- [ ] Dead-man's-switch: on total sweep failure, the failure card is dispatched through the allowlist email channel (no crash on the minimal-dict shape; subject = summary; no-op when unconfigured) AND the existing Teams path still fires when configured.
- [ ] Email unconfigured (prod default) → sweep behaves exactly as today (no send, no error).
- [ ] No `run_diagnosis`/second-investigation call added (assert by inspection/test intent).
- [ ] Full suite green; tool count 18.
**Files:** `fabric_audit_agent/job.py`, `tests/test_job.py` (or the existing job test module). **Deps:** Tasks 1, 2, 3. **Scope:** M.

---

### Checkpoint: complete
- [ ] Full suite green (1052 + new); tool count 18; no MCP/agent-app change.
- [ ] Read-only held: no write/scale/refresh anywhere; alerts surface only.
- [ ] Email inert until `SMTP_*` set; Teams/ADO/Graph refused (disabled→P7).
- [ ] Ready for opus final review — attack surface: prev-vs-current history timing (incl. a mutating-store); ANY payload reaching a sink without passing the egress gate (incl. the email self-gate backstop); disclosure actually present in the delivered body; snooze mis-reported as resolved; standing SLA breach or verdict-improvement re-alerting (noise); any path that could send when unconfigured; alert-path error escaping and failing the sweep; verdict/SLA false positive from a missing history field; first-run baseline honesty (mock/proxy label survives into subject/body).

## Global constraints (verbatim into implementer + reviewer prompts)
- **Read-only absolute** — autonomy surfaces findings; NEVER write/scale/refresh/remediate. No always-on loop (this is the existing scheduled sweep, made smarter).
- **Every outbound payload passes the typed allowlist THEN the egress gate** before any sink emits. Nothing data-mutating is registrable. Teams/ADO/Graph registered-but-DISABLED (→P7).
- **Email inert until `SMTP_HOST`+`SMTP_TO` set**; SMTP sender injected in tests — no real network, offline deterministic.
- **Failure-isolated** alert path; dead-man's-switch retained.
- Honest labels (monitored-CU proxy/coverage/mock-vs-live) carried into alert content unchanged.
- camelCase data keys / snake_case identifiers; nullish-not-falsy; stdlib-only; py≥3.10. Keep suite green (1052 + new). Tool count 18. No MCP/agent-app change.
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (alerting.py + verdictDecision) ┐
Task 2 (outbound.py allowlist)         ├─→ Task 4 (job wiring)
Task 3 (delivery_email.py)             ┘
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| `decide_alert` compares current run against itself (append timing) | High (wrong/no alerts) | Capture `prev_history` BEFORE `run_audit`; explicit test proving `resolved` fires |
| A payload reaches a sink without egress gating | High (exfil) | `dispatch_outbound` is the ONLY send path; gate inside it; test asserts planted secret masked at sink |
| Email sends when it shouldn't (misconfig) | High | Inert until `SMTP_HOST`+`SMTP_TO`; unconfigured no-op test; injected sender in all tests |
| Alert-path error fails the sweep | Med | try/except swallow around the whole alert block; failure-isolation test |
| Verdict-change false positive from missing history field | Med | Additive `verdictDecision`; absent → skip; graceful-degrade test |
| Alert noise (fires every run) | Med | On-CHANGE gate (new/worsened/resolved/verdict-worse/sla-INCREASE) + `min_level`; standing-breach & verdict-improvement no-alert tests |
| Snooze mis-reported as resolved | Med | "Current keys" = `findings ∪ suppressed`; snooze-safe test |
| Disclosure lost on the email channel | Med | `dispatch_outbound` injects `disclosure_line` into `summary` (mirrors 3 existing sinks); substring test |
| Email wired outside the gate later | Med | Self-gate backstop inside `delivery_email` (idempotent) + docstring + no-direct-wire test |
| First-run baseline floods / over-claims authority | Low | `reason="baseline"`; subject/body inherit the honest mock/proxy/coverage caveat from `summary` |

## Open questions
- None blocking. Subject = `[Fabric audit] {verdict.decision}` (fallback `summary`); `SMTP_TO` comma-split.

## Change log (v1 → v2)
- **Technical-accuracy (self, reviewer stalled):** confirmed append-during-run at `pipeline.py:101`, `verdict.decision` key, `sla` only when `>0`, `digest` when history, `history()` oldest→newest. Instruction added: compute `verdict` once before the append (facts@70/flags@77 in scope) and reuse — zero behavioral change.
- **Coverage (self, reviewer stalled):** all spec §1–§5 + Testing bullets map to task ACs; added the explicit "no `run_diagnosis`" negative check to Task 4 (spec §2).
- **Opus improvability (SHIP-after-revise):**
  - INV-1 email-bypass → self-gate backstop + docstring + no-direct-wire test (Task 3/4).
  - INV-2 lost disclosure → `dispatch_outbound` injects `disclosure_line` into `summary` (Task 2).
  - INV-3 snooze-as-resolved → current keys = `findings ∪ suppressed` (Task 1).
  - INV-4 standing SLA re-alert → `slaBreachedCount` in history; alert on INCREASE only (Task 1).
  - MUST-1 verdict "worse" ordering → explicit ordinal `healthy<optimize<size-up`, `unknown` recorded-not-material (Task 1).
  - MUST-2 config single-source → static registry enablement; sink owns configured-vs-inert; `dispatch_outbound` doesn't read `SMTP_*` (Task 2).
  - MUST-3 verdictDecision reorder → compute verdict pre-append and reuse (Task 1).
  - MUST-4 dead-man's-switch subject/shape → subject fallback to `summary`; addition-not-replacement; failure-card test (Task 3/4).
  - MUST-5 resolved vs `min_level` → resolved gated at prev stored level (Task 1).
  - NICE-6 brittle "escalated" substring → per-key LEVEL-delta mechanism (Task 1).
  - NICE-7 store snapshot contract → stated + mutating-store test (Task 4).
  - NICE-8 first-run baseline honesty → `reason="baseline"` + caveat-bearing subject (Task 1/3).
  - NICE-9 `changes` vs `min_level` → `changes` lists only qualifying keys (Task 1).
