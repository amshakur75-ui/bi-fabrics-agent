# Proactivity & Alerting (Phase 6) — Design Spec

**Date:** 2026-07-09 · **Roadmap:** Phase 6 · **Status:** design, pre-plan
**Needs:** Phase 5 anti-exfil (5.2 egress gate) — in place. **Brainstorm decisions (user, 2026-07-09):**
autonomy = *smarter sweep + alert-on-change*; delivery = **SMTP email, inert until configured**;
**Teams/Activator + Graph sendMail deferred to Phase 7** (admin-consent channels).

## Purpose

Make the scheduled sweep *proactive*: after each run, decide whether a **material change** vs the last
run warrants an alert, and if so deliver it (email, when configured) — **read-only autonomy**:
observe → investigate → **surface**, NEVER auto-act. Low-noise (alert on change, not every run). All
outbound goes through a typed **outbound-action allowlist** (the 5.3-C item, landing with its first
consumer) and the **5.2 egress gate**.

## Invariants
Read-only absolute — autonomy surfaces findings, never writes/scales/refreshes (no remediation, ever).
No always-on streaming (roadmap non-goal) — this is the *existing scheduled sweep*, made smarter, not a
new continuous loop. Every outbound payload passes the allowlist (typed, never open-ended) THEN the
egress gate (redact/cap). Honest labels (monitored-CU proxy, coverage, mock-vs-live) carried into alerts.

## Design

### 1. Alert-on-change decision — `automation/alerting.py` (pure)
`decide_alert(envelope, history, *, min_level="Warning") -> {"alert": bool, "reason": str, "changes": {...}}`.
Reuses signals the pipeline ALREADY computes into the envelope/history:
- **New finding** (`digest.newCount > 0` — `automation/digest.py`) at/above `min_level`.
- **Escalation** (a finding escalated Warning→Critical — `automation/escalate.py` annotations).
- **Resolved** (a finding key present in the last history run but absent now — computed from `history`).
- **Verdict change** (`data.verdict.decision` differs from the last run's).
- **SLA breach** (`data.sla.breachedCount > 0`).
No material change → `alert: False` (silent; the low-noise guarantee). Pure/deterministic; depends on
durable history (the Job's `AUDIT_HISTORY_PATH`→Volume, already wired in `databricks.yml`).

### 2. Deep analysis is already in the envelope (no redundant re-investigation)
The scheduled `run_audit` already runs detectors + `forecast_capacity`/`forecast_throttle` + throttle
decomposition + roadmap into the envelope — that IS the "deeper investigation." The alert attaches this
existing analysis (findings/verdict/roadmap/forecast/digest). A **separate on-breach `run_diagnosis`
call is NOT added** in v1 (it would largely duplicate the envelope); deferred unless a concrete gap
surfaces. (This refines the brainstorm's "auto-run diagnose" — same outcome, no redundant coupling.)

### 3. Outbound-action allowlist — `outbound.py` (the 5.3-C item)
A typed registry of permitted outbound action TYPES: `email_notify` (enabled when SMTP configured);
`teams_notify` / `ado_create_ticket` **registered but DISABLED (→ Phase 7)**. `dispatch_outbound(action_type,
payload, env, *, sinks)` refuses any type not in the allowlist or not enabled; the enabled type's payload
is passed through the **egress gate** (`apply_egress_controls`, 5.2) before the sink sends. Invariant made
concrete: outbound is a typed allowlist, never open-ended; nothing data-mutating is registrable.

### 4. Email delivery — `adapters/delivery_email.py` (inert until configured)
`create_email_delivery(env) -> {"deliver": fn(envelope)}` using stdlib `smtplib`. Config via env
(`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TO`, optional `SMTP_USER`/`SMTP_PASSWORD`, `SMTP_STARTTLS`).
**Inert until `SMTP_HOST`+`SMTP_TO` are set** — unconfigured → a no-op delivery (like today's `_csv_delivery`
no-op), so nothing sends and nothing breaks. Body = the existing markdown report (`report_md`) as
text/plain (+ subject from the verdict/summary). Same `deliver(envelope)` contract as `delivery_teams`;
the payload it receives is already egress-gated at the seam. No admin consent (SMTP relay, not Graph).

### 5. Job wiring + watchdog
In the scheduled sweep (`job.py` run_unified_job): after the envelope, call `decide_alert(envelope,
history)`; if `alert`, `dispatch_outbound("email_notify", envelope, env, sinks=...)` (which gates +
sends via the email delivery, or no-ops if unconfigured). The existing dead-man's-switch (`_alert_failure`)
is retained + also routed through the allowlist/gate. Alerting is **failure-isolated** — an alert-path
error never fails the sweep.

## Testing (TDD, offline, deterministic; no real SMTP/network)
- `decide_alert`: new-finding / escalation / resolved / verdict-change / SLA-breach each → `alert:True` with the right `reason`; a no-change run → `alert:False`; `min_level` honored; pure/deterministic.
- `outbound.py`: `email_notify` dispatched when enabled+configured; `teams_notify`/`ado_create_ticket` REFUSED (disabled→P7); an unknown action type refused; the payload passes through `apply_egress_controls` before the sink (assert a planted secret is masked at the sink).
- `delivery_email`: unconfigured env → no-op (no send, no error); configured → builds the message + calls an INJECTED fake smtp sender (never real network) with the right from/to/subject/body; body is the report text.
- Job wiring: an injected fake email sink receives a gated alert only when `decide_alert` says alert; no-change run sends nothing; an alert-path exception doesn't fail the sweep (failure isolation); dead-man's-switch still fires on total failure.
- Full suite green; tool count 18 (no MCP tool added).

## Deploy
Ships with the **scheduled Job / bundle** deploy (not the MCP/agent apps). Email stays inert until
`SMTP_*` env is set on the Job (no admin consent). Also carries the pending Job-side 5.2 egress + 5.3
`[identity]` (they deploy together). Coordinate the bundle deploy with the user (shared infra).

## Explicitly NOT pursued — with reasons
- **Teams / Activator delivery activation, Graph `sendMail`** — Phase 7 (admin-consent channels; the
  webhook/scopes are the authorization hurdle). Today's `delivery_teams.py` stays as-is, unused by P6.
- **A separate on-breach `run_diagnosis`** — the sweep envelope already carries the deep analysis; adding
  it would duplicate. Deferred unless a gap appears.
- **Always-on / streaming watcher** — roadmap non-goal; P6 is the scheduled sweep made smarter.
- **Any remediation / write / auto-act** — forbidden (read-only absolute); alerts surface, never act.
- **ADO ticketing** — Phase 7 (registered-but-disabled in the allowlist).
