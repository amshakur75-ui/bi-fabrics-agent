# Implementation Plan: Proactivity & Alerting (Phase 6) — v1

**Spec:** `docs/superpowers/specs/2026-07-09-proactive-alerting-design.md`
**Branch:** `feat/proactive-alerting` (off `main` `40e5086`; spec commit `26dc3b9`)
**Method:** superpowers SDD, TDD, per-task review. Offline; deterministic; no MCP tool added (tool count stays 18). Deploys with the scheduled Job/bundle.

## Overview
Make the scheduled sweep proactive: after each run, decide whether a **material change** vs the previous run warrants an alert (low-noise), and if so deliver it via **email (inert until SMTP configured)** through a **typed outbound allowlist** and the **5.2 egress gate**. Read-only autonomy: observe → surface, NEVER auto-act. Teams/Activator/Graph/ADO stay deferred to Phase 7 (registered-but-disabled).

## Architecture decisions (grounded in the code)
- **`decide_alert` reads the envelope + PREVIOUS history.** `run_audit` appends the current run to the store *inside* the run (`pipeline.py:101`), so after it returns `store["history"]()[-1]` is the *current* run. The Job MUST capture `prev_history = store["history"]()` **before** calling `run_audit` and pass that in — otherwise "resolved"/"verdict-change" compare a run against itself. This is the single most important wiring fact; baked into Task 4.
- **Signals already exist in the envelope.** `data.digest.newCount` (`automation/digest.py`), escalation already applied to findings (`automation/escalate.py`, reason contains `"escalated"`), `data.sla.breachedCount` (present only when `>0`), `data.verdict.decision`. `decide_alert` reads these — no re-computation, no redundant `run_diagnosis` (v1 attaches the existing envelope analysis, per spec §2).
- **Verdict-change needs one additive history field.** History run entries (`pipeline.py:101-108`) store `{runAt, tenant, metrics, findings:[{key,level,where,what,suppressed}]}` — **no verdict**. Add `"verdictDecision": verdict["decision"]` to the appended run (additive, backward-compatible; `whats_changed` ignores unknown keys). `decide_alert` compares `envelope.data.verdict.decision` to `prev_history[-1].get("verdictDecision")`; absent (old runs / first run) → no verdict-change alert (graceful degrade, never a false positive).
- **Email mirrors the Teams delivery contract.** `create_email_delivery(env) -> {"deliver": fn(envelope)}`, same shape as `create_teams_delivery` (`adapters/delivery_teams.py`). Body = `build_markdown_report(envelope)` (`report_md.py`) as text/plain. The SMTP sender is **injected** for tests (never real network), exactly as Teams injects `http`.
- **Outbound allowlist is the choke point.** `dispatch_outbound(action_type, payload, env, *, sinks)` refuses any type not registered/enabled, then routes the enabled payload through `apply_egress_controls(payload, sink="alert")` (`egress.py:131`) before the sink sends. This is where the invariant "outbound is a typed allowlist, never open-ended" becomes code.
- **Failure isolation.** An alert-path exception never fails the sweep. The existing dead-man's-switch (`_alert_failure`, `job.py:173`) is retained and also routed through the allowlist/gate.

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

## Task 1 — `automation/alerting.py::decide_alert` (pure) + history `verdictDecision` + tests
**Description:** Add the pure alert-on-change decision, and the one additive history field it needs for verdict-change.
**Interface:** `decide_alert(envelope, prev_history, *, min_level="Warning") -> {"alert": bool, "reason": str, "changes": {...}}`.
- `changes` carries the material deltas: `{"new": [keys], "escalated": [keys], "resolved": [keys], "verdictChange": {"from","to"}|None, "slaBreached": int}`.
- `alert` is True iff at least one material change **at/above `min_level`** exists (SLA breach and verdict-change→worse always qualify; `new`/`escalated` gated by level; `resolved` alerts too — a resolution is worth surfacing — but never alone raises severity).
- Level gate uses an ordered rank Critical>Warning>Info; `min_level="Warning"` drops Info-only new findings.
- **Resolved** = keys in `prev_history[-1].findings` (non-suppressed) absent from current `data.findings`.
- **Verdict-change** = `data.verdict.decision` vs `prev_history[-1].get("verdictDecision")`; absent → no verdict-change.
- Empty `prev_history` (first run) → alert only on genuinely new material findings/SLA (no resolved/verdict-change). Pure/deterministic; no I/O.
**Also:** in `pipeline.py` (~line 101) add `"verdictDecision": verdict["decision"]` to the appended run dict (additive; verdict is computed at line 122 — move the append below verdict or capture the decision; keep the change minimal and ordering-correct).
**Acceptance criteria:**
- [ ] Each signal in isolation → `alert:True` with the correct `reason`/`changes`: new-at-Warning, escalation, resolved, verdict-change, SLA-breach.
- [ ] No-change run → `alert:False`, empty `changes`.
- [ ] `min_level` honored (Info-only new finding → no alert at default; alerts at `min_level="Info"`).
- [ ] First run (`prev_history=[]`) → no `resolved`/`verdictChange`; new material findings still alert.
- [ ] Appended history run carries `verdictDecision`; a `prev_history` entry lacking it → no verdict-change (no crash, no false positive).
- [ ] Pure/deterministic; suite green.
**Files:** `fabric_audit_agent/automation/alerting.py`, `fabric_audit_agent/pipeline.py`, `tests/test_alerting.py` (+ touch the pipeline history test). **Deps:** None. **Scope:** M.

## Task 2 — `outbound.py` typed allowlist `dispatch_outbound` + tests
**Description:** The typed outbound-action registry (the 5.3-C item, landing with its first consumer). Enforces "typed allowlist, never open-ended" and routes every payload through the egress gate.
**Interface:** `dispatch_outbound(action_type, payload, env, *, sinks) -> {"dispatched": bool, "actionType": str, "disclosure": str|None, "reason": str|None}`.
- Registry: `email_notify` → **enabled when SMTP configured** (delegates to `sinks["email"]`); `teams_notify`, `ado_create_ticket` → **registered but DISABLED (→ Phase 7)**.
- Unknown or disabled type → refuse (`dispatched:False`, `reason`), send nothing, never raise on refusal.
- Enabled path: `safe, meta = apply_egress_controls(payload, sink="alert")` **then** `sinks["email"]["deliver"](safe)`; return `disclosure=disclosure_line(meta)`.
- Nothing data-mutating is registrable (assert by construction: registry is a module-level typed dict, no write/scale/refresh types).
**Acceptance criteria:**
- [ ] `email_notify` enabled+configured → payload passes through `apply_egress_controls` (assert a planted secret in the payload is masked at the sink) then delivered.
- [ ] `teams_notify` / `ado_create_ticket` → refused (disabled→P7); no send; no raise.
- [ ] Unknown action type → refused; no send.
- [ ] Deep-copy honored — the caller's payload object is unmutated after dispatch.
- [ ] Suite green.
**Files:** `fabric_audit_agent/outbound.py`, `tests/test_outbound.py`. **Deps:** None. **Scope:** M.

## Task 3 — `adapters/delivery_email.py::create_email_delivery` (inert until configured) + tests
**Description:** Stdlib-SMTP email delivery mirroring the Teams delivery contract; a no-op until SMTP is configured.
**Interface:** `create_email_delivery(env, *, sender=None) -> {"deliver": fn(envelope)}`.
- Config from env: `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_FROM`, `SMTP_TO`, optional `SMTP_USER`/`SMTP_PASSWORD`, `SMTP_STARTTLS` (default true).
- **Inert until `SMTP_HOST`+`SMTP_TO` set** → `deliver` returns `{"delivered": False, "reason": "unconfigured"}`, sends nothing, never raises (mirrors the `_csv_delivery` no-op pattern).
- Configured → build a `email.message.EmailMessage` (subject from verdict/summary; body = `build_markdown_report(envelope)` as text/plain) and send via the **injected** `sender` (default a thin `smtplib.SMTP` wrapper). Tests inject a fake sender — **never real network**.
- Returns `{"delivered": True, "target": <to>}`.
**Acceptance criteria:**
- [ ] Unconfigured env → no-op (no send, no error), `delivered:False`.
- [ ] Configured → injected fake sender receives one message with correct from/to/subject; body equals the markdown report text.
- [ ] STARTTLS + auth path exercised against the fake sender (no real socket).
- [ ] `deliver(envelope)` contract matches `delivery_teams` (same return-shape spirit); suite green.
**Files:** `fabric_audit_agent/adapters/delivery_email.py`, `tests/test_delivery_email.py`. **Deps:** None. **Scope:** S/M.

---

### Checkpoint: components
- [ ] Tasks 1–3 green in isolation; `decide_alert` pure; email inert-by-default; allowlist refuses disabled/unknown types; egress gate proven in the outbound path.

---

## Task 4 — Job wiring in `run_unified_job` (+ dead-man's-switch through the allowlist) + tests
**Description:** Wire alerting into the scheduled sweep, failure-isolated, using previous-run history.
**Changes (`job.py`):**
- In `run_unified_job`: **before** `run_audit`, `prev_history = store["history"]()`. After the envelope, `decision = decide_alert(envelope, prev_history)`; if `decision["alert"]`, build `sinks = {"email": create_email_delivery(env)}` and `dispatch_outbound("email_notify", envelope, env, sinks=sinks)`. Wrap the whole alert block in try/except that logs and swallows (an alert-path error NEVER fails the sweep). Return the envelope unchanged.
- `_alert_failure` (dead-man's-switch): route its failure card through `dispatch_outbound` as well (email channel), keeping the existing behavior when unconfigured (no-op). Keep it independent of the main alert path.
- No change to the read-only pipeline, tool count, or MCP surface.
**Acceptance criteria:**
- [ ] Injected fake email sink receives a **gated** alert only when `decide_alert` says alert; a no-change run sends nothing.
- [ ] `prev_history` is captured **before** `run_audit` (test: a run that resolves the only prior finding triggers a `resolved` alert — proves it isn't comparing against the just-appended current run).
- [ ] An exception in the alert path does **not** fail the sweep (envelope still returned) — failure isolation.
- [ ] Dead-man's-switch still fires on total sweep failure, routed through the allowlist (no-op when unconfigured).
- [ ] Email unconfigured (prod default) → sweep behaves exactly as today (no send, no error).
- [ ] Full suite green; tool count 18.
**Files:** `fabric_audit_agent/job.py`, `tests/test_job.py` (or the existing job test module). **Deps:** Tasks 1, 2, 3. **Scope:** M.

---

### Checkpoint: complete
- [ ] Full suite green (1052 + new); tool count 18; no MCP/agent-app change.
- [ ] Read-only held: no write/scale/refresh anywhere; alerts surface only.
- [ ] Email inert until `SMTP_*` set; Teams/ADO/Graph refused (disabled→P7).
- [ ] Ready for opus final review — attack surface: prev-vs-current history timing; a payload reaching a sink WITHOUT passing the egress gate; any path that could send when unconfigured; alert-path error escaping and failing the sweep; verdict-change false positive from a missing `verdictDecision`.

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
| Alert noise (fires every run) | Med | Material-change gate + `min_level`; no-change→no-alert test |

## Open questions
- None blocking. (Subject-line format and multi-recipient handling are cosmetic; default: subject = verdict decision + newCount, `SMTP_TO` comma-split.)
