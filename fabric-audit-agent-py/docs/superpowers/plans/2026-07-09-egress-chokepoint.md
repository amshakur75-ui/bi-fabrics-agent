# Implementation Plan: Egress Chokepoint (Phase 5.2)

**Spec:** `docs/superpowers/specs/2026-07-09-egress-chokepoint-design.md`
**Branch:** `feat/egress-chokepoint` (off `main` `b6cb175`)
**Method:** superpowers SDD, TDD, per-task review. Outbound-only; no data-read/tool/schema change.

## Overview

Add one enforced egress gate `apply_egress_controls(payload, *, sink)` that composes secret redaction +
labeled-sensitive floor + size cap, and route the outbound delivery seam through it. Names pass
(approved). Foundational control that gates Phases 6/8/9.

## Architecture decisions (grounded in the code)

- **Gate the DELIVERED payload only, not the returned/stored envelope.** `run_audit` (pipeline.py:160)
  calls `delivery["deliver"](envelope)` then `return envelope`; the returned envelope also feeds
  `_write_outputs` (Job output files) and the run-history `store`. Those are internal persistence, NOT
  broadcast egress — leave them full. Apply the gate ONLY to the object handed to `deliver`.
- **Two deliver call sites to cover:** (1) `pipeline.py:160` (the main sweep → file/Teams/no-op
  adapters); (2) `job.py:175` the failure-delivery (`delivery["deliver"]({"summary": ...})` — an error
  card posted to Teams). Both broadcast; both gated.
- **Reuse, don't rebuild:** `redact.redact_secrets` (per-string), the `sanitize` sensitivity rule
  (`sensitive:true`/`sensitivityLabel` → drop), `envelope.cap_rows` (char-budget list cap).
- **`sink` is a label** recorded in `meta` (`"delivery"`, `"failure"`, later `"teams"`/`"ui"`/`"memory"`)
  — no behavior change today; the hook for future per-sink policy (deferred name control).
- **Names/identifiers pass through** (approved) — the gate does NOT strip user emails / item names.
- **Out of scope for wiring:** `_write_outputs` (Volume report files) — internal Job output, not a
  broadcast sink; noted as a possible future sink, not gated now. The LLM-input `sanitize()` is untouched.

## Confirmed interfaces
- `run_audit` (pipeline.py:59) delivers at **pipeline.py:160**: `delivery["deliver"](envelope)`, then `return envelope`.
- Failure-delivery at **job.py:175**: `delivery["deliver"]({"summary": ...})`.
- `deliver` port contract: `{"deliver": fn(envelope) -> Any}` (delivery_file/delivery_teams/no-op).
- `redact.redact_secrets(text) -> str` (allowlisted secret masking; never raises).
- `sanitize.sanitize_evidence` shows the sensitivity rule: `evidence.get("sensitive") is True or evidence.get("sensitivityLabel")` → redact.
- `envelope.cap_rows(records, *, max_chars=12000, min_rows=1) -> (rows, meta)`.

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` (931 on main). Keep green + new tests. (Agent-app suite unaffected — this is package-only.)

---

## Task List

### Task 1 — `egress.py` gate module (pure) + tests

**Description:** New `fabric_audit_agent/egress.py`.

**Interface:**
```python
def apply_egress_controls(payload, *, sink, max_chars=12000) -> tuple[object, dict]:
    """Return (safe_payload, meta) for an outbound payload bound for broadcast/external *sink*.
    Deep-copy semantics (never mutate the caller's object). In order:
      1. Sensitivity floor: any dict with `sensitive is True` or a truthy `sensitivityLabel` → replaced
         with {"redacted": True}, recursively (walk dicts/lists). Count -> sensitiveDropped.
      2. Secret redaction: every remaining string value (recursively) → redact_secrets(s). Count the
         strings actually changed -> secretsRedacted.
      3. Size cap: if payload is a list, cap_rows(max_chars). If a dict, cap_rows each list-valued field
         whose serialized size exceeds max_chars (independently); record truncated + rowsOmitted (max
         over capped fields). Scalars untouched.
      4. Identifiers/names pass through unchanged.
    Pure/deterministic; NEVER raises (malformed/None payload → a safe, disclosed result).
    meta = {"sink", "secretsRedacted", "sensitiveDropped", "truncated", "rowsOmitted"}.
    """
```
Internal deep-walk helper handles dict/list/scalar; non-string scalars (numbers/bools) untouched.

**Acceptance criteria:**
- [ ] Secret redaction: SAS `?sig=`, `bearer x`, `client_secret=`/`token=` in a nested string/URL → masked; `secretsRedacted` correct; a benign `Status=200` / non-allowlisted `foo=bar` NOT changed.
- [ ] Sensitivity floor: dict with `sensitive:true` or `sensitivityLabel` → `{"redacted":True}`, recursively (a nested sensitive dict inside a list is dropped); `sensitive:false` passes; `sensitiveDropped` correct.
- [ ] Size cap: over-budget list → capped, `truncated`+`rowsOmitted`; dict with an over-budget list field → that field capped; under-budget unchanged.
- [ ] **Names pass:** payload with `user`/emails/dataset names → survive unchanged (guards a future over-strip).
- [ ] Does NOT mutate the input object (assert the original is intact).
- [ ] Robust/pure: None/non-dict/non-list/malformed → safe result, no raise; deterministic; numbers/bools untouched.

**Files:** `fabric_audit_agent/egress.py`, `tests/test_egress.py`. **Deps:** none. **Scope:** M.

---

### Task 2 — Wire the gate into the delivery seam + contract docs

**Description:** Route the two outbound deliver sites through the gate; document the contract.

**Changes:**
- `pipeline.py:160`: `delivery["deliver"](envelope)` → gate first, deliver the safe payload; keep
  `return envelope` UNCHANGED (returned/stored envelope stays full). Attach/log `meta` (e.g. into the
  run log or a debug line) so redaction/truncation is disclosable — do not silently drop.
- `job.py:175`: gate the failure `{"summary": ...}` payload before `deliver` (sink="failure").
- Module docstring in `egress.py` + a line in `docs/HANDOFF.md`: **contract — every outbound sink MUST
  route through `apply_egress_controls`; it is the only sanctioned way to emit outward.** Phase-6/8/9
  sinks carry this as a task AC.

**Acceptance criteria:**
- [ ] A planted secret (`sig=...`) in a delivered finding is masked at the sink; the **returned** envelope from `run_audit` is UNchanged (full) — asserted both ways.
- [ ] The failure-delivery card is gated (planted secret in the summary masked).
- [ ] Existing delivery tests still pass (file/Teams adapters receive a valid gated envelope; no schema break — `build_teams_card` still reads `summary`/`data`).
- [ ] `meta` is surfaced (not silently discarded).
- [ ] Full suite green.

**Files:** `fabric_audit_agent/pipeline.py`, `fabric_audit_agent/job.py`, `fabric_audit_agent/egress.py` (docstring), `docs/HANDOFF.md`, tests. **Deps:** Task 1. **Scope:** S/M.

---

### Checkpoint (feature complete)
- [ ] Suite green; delivered payloads gated, returned/stored envelope full.
- [ ] Contract documented; ready for opus adversarial final review (attack: can any outbound path reach a sink WITHOUT the gate? can a secret/sensitive item slip through? does the gate mutate/lose non-secret data or break the Teams card?).

## Global constraints (verbatim into implementer + reviewer prompts)
- Outbound-only; NO data-read/tool/schema change. Read-only + three invariants hold.
- Gate the DELIVERED payload only — never the returned/stored envelope (internal history stays full).
- Reuse redact_secrets / sanitize's sensitivity rule / cap_rows — don't reimplement. Names pass (approved).
- Transparent: `meta` discloses redacted/dropped/truncated; never silently drop a load-bearing figure.
- Conventions (Part 1e): camelCase data / snake_case ids; nullish-not-falsy; stdlib-only; py≥3.10. Offline deterministic tests; keep the suite green (931 + new).
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (egress.py) → Task 2 (wire + docs)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Gate mutates/loses non-secret data or breaks the Teams card | Med | Deep-copy (no mutate); names-pass test; existing delivery tests must pass; assert build_teams_card still reads summary/data |
| Returned/stored envelope accidentally gated (history loses data) | Med | Gate ONLY the deliver argument; test that run_audit's return value is unchanged |
| redact over-masks a legit `key=` in narrative | Low | Pre-existing redact allowlist behavior; only affects broadcast payloads (correct caution); disclosed in meta |
| A future sink bypasses the gate | Med (future) | Documented contract + per-phase task AC + wiring test on today's seam (static all-future-sinks test infeasible) |

## Open questions
- None blocking. (Scope = chokepoint; names pass — both approved.)
