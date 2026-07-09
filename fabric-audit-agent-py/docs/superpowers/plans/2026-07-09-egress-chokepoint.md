# Implementation Plan: Egress Chokepoint (Phase 5.2) — v2

**Spec:** `docs/superpowers/specs/2026-07-09-egress-chokepoint-design.md`
**Branch:** `feat/egress-chokepoint` (off `main` `b6cb175`)
**Method:** superpowers SDD, TDD, per-task review. Outbound-only; no data-read/tool/schema change.
**v2:** rewritten after 3 plan-reviewers (coverage / technical-accuracy / opus leak-hunt). Change log at bottom.

## Overview

One enforced egress gate `apply_egress_controls(payload, *, sink)` composing **key/shape-aware secret
redaction** + labeled-sensitive floor + a **findings-targeted** size cap, routed through **all three**
current outbound surfaces, with the drop/cap **disclosed in the delivered payload**. Names pass
(approved). Foundational; gates Phases 6/8/9.

## Critical context (verified in review — do not re-litigate)

- **Size cap must target `payload["data"]["findings"]`**, the envelope's only unbounded list. The
  envelope's top-level fields are `success/agent_id/data(dict)/summary/timestamp` — none are lists, so a
  "cap top-level lists" gate is INERT (Critical). Do NOT blanket-cap other `data` lists
  (`roadmap`/`correlations`/`anomalies`/`suppressed`) — they're bounded, structured, and indexed by
  `report_md`/`entrypoints` (`roadmap[:3]`), so capping them mid-structure breaks consumers.
- **`redact_secrets` alone under-reaches on structured payloads** (Critical): it only masks `name=value`
  *inside one string*. In an envelope the secret name is the dict KEY and the value is separate, so
  `{"clientSecret":"s3cr3t"}` and `{"conn":"...AccountKey=YWJj==;"}` (`\bkey=` misses `AccountKey`) and a
  bare JWT all LEAK. The gate needs **key-aware** (mask value when the dict key is secret-shaped) +
  **value-shape** (JWT / connection-string) redaction, THEN `redact_secrets` for the in-string cases.
- **Three outbound surfaces to gate**, not two: `pipeline.py:160`, `job.py:175` (failure card), and
  `_write_outputs` (writes full envelope → `latest.json`+`report.md` on a Volume — a durable, shareable
  dump; gate its content). Gate the deliver/write ARG only; `return envelope` stays full. The
  run-history `store` persists EARLIER (`pipeline.py:100-108`) independent of the return — untouched.
- **`meta` must reach the recipient:** fold a disclosure line into the delivered `summary` (read by
  `teams_card.build_teams_card` — in `teams_card.py:20` — and `report_md`); internal logging ≠ disclosure.
- **Two unwired-but-real sinks to name in the contract:** `adapters/ticketing.py`
  (`{"open": open_}` port, takes a findings LIST) and `conversation.py::build_concentration_alert`.
- Interfaces confirmed: `redact.redact_secrets(text)`, `sanitize` rule
  (`sensitive is True or sensitivityLabel`), `cap_rows(records,*,max_chars=12000,min_rows=1)->(rows,meta)`.
  `build_teams_card` reads `envelope["data"]` (verdict, findings) + `["summary"]`; a redacted finding
  loses `score.level` and simply drops from the Critical section (no crash).

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → 931. Keep green + new tests. (Package-only; agent-app unaffected.)

---

## Task List

### Task 1 — `egress.py` gate module (pure) + tests

**Description:** New `fabric_audit_agent/egress.py`.

**Interface:**
```python
def apply_egress_controls(payload, *, sink, max_chars=12000) -> tuple[object, dict]:
    """(safe_payload, meta) for an outbound payload → broadcast/external *sink*. Deep-copy first
    (never mutate caller's object). In order:
      1. Sensitivity floor (recursive): any dict with `sensitive is True` or truthy `sensitivityLabel`
         → {"redacted": True}; count -> sensitiveDropped.
      2. Redaction (recursive, key+shape-aware): for each string value —
         (a) if its dict KEY (lowercased) in _SECRET_KEYS {secret,token,password,pwd,apikey,api_key,
             key,client_secret,sig,access_token,connectionstring,accountkey,sharedaccesskey} → mask;
         (b) elif value matches a secret SHAPE (JWT `eyJ[...]\.[...]\.[...]`; connection-string
             `(?i)(accountkey|sharedaccesskey|password)=`; long base64 token) → mask;
         (c) else value = redact_secrets(value).  Count strings actually changed -> secretsRedacted.
         Numbers/bools/None untouched.
      3. Size cap: if payload is a dict with data.findings (list), cap_rows(findings, max_chars) and
         replace; if payload IS a list, cap it directly. Do NOT touch other lists. -> truncated, rowsOmitted.
      4. Names/identifiers pass unchanged.
    Pure/deterministic; NEVER raises (None/malformed → safe disclosed result).
    meta = {sink, secretsRedacted, sensitiveDropped, truncated, rowsOmitted}."""

def disclosure_line(meta) -> str | None:
    """A plain sink-facing sentence when anything was dropped/capped, else None —
    e.g. '(12 findings omitted for length; 1 sensitive item withheld)'. For the wiring to append to summary."""
```
`_MASK = "***"`. Internal recursive `_walk` handles dict/list/scalar and carries the parent key for (2a).

**Acceptance criteria (tests — include the review's edge cases):**
- [ ] Key-aware: `{"clientSecret":"s3cr3t"}` and `{"AccountKey":"..."}` → value masked (the case `redact_secrets` misses).
- [ ] Shape-aware: `{"conn":"Server=x;AccountKey=YWJj==;"}` and a bare JWT `"eyJhbGciOi.J9.sig"` value → masked regardless of key.
- [ ] In-string: nested `"...&sig=abc"` / `"bearer xyz"` → masked (via redact_secrets); benign `foo=bar` / `Status=200` NOT changed.
- [ ] Sensitivity floor: `sensitive:true`/`sensitivityLabel` dict (incl. nested in a list) → `{"redacted":True}`; `sensitive:false` passes; count right.
- [ ] Size cap targets `data.findings`: an envelope with an over-budget `data.findings` → capped, `truncated`, `rowsOmitted>0`; **`data.roadmap`/`data.correlations` returned INTACT** (not capped); a bare over-budget list payload → capped directly.
- [ ] Names pass: emails / dataset names survive unchanged.
- [ ] No mutation: original input object unchanged after the call (deep-copy).
- [ ] Robust/pure: None / non-dict / dict without `data` (e.g. `{"summary":...}`) / malformed → safe result, no raise; numbers/bools untouched; deterministic.
- [ ] `disclosure_line`: returns a sentence when meta shows drops/caps, None otherwise.

**Files:** `fabric_audit_agent/egress.py`, `tests/test_egress.py`. **Deps:** none. **Scope:** M/L.

---

### Task 2 — Wire the gate into all 3 outbound surfaces + disclosure + contract docs

**Changes:**
- `pipeline.py:160`: gate the envelope, append `disclosure_line(meta)` into the delivered payload's
  `summary` (so the Teams card/report shows it), deliver the safe payload; keep `return envelope` FULL.
- `job.py:175`: gate the failure `{"summary":...}` (sink="failure").
- `job.py` `_write_outputs`: gate the envelope content before writing `latest.json`/`report.md`
  (sink="file"); disclosure into the written summary. (Write the gated copy; the in-memory returned
  envelope elsewhere stays full.)
- `egress.py` docstring + `docs/HANDOFF.md`: the contract — **every outbound sink routes through
  `apply_egress_controls`; it's the only sanctioned way to emit outward** — explicitly naming the two
  unwired surfaces (`ticketing.py` `open(findings)` → gate the list, sink="ticketing";
  `conversation.build_concentration_alert`) as MUST-gate-when-activated.

**Acceptance criteria:**
- [ ] A planted secret (`sig=...`, `{"clientSecret":...}`) in a delivered finding is masked at the sink; the **returned** envelope from `run_audit` is UNchanged (full) — asserted both ways.
- [ ] `_write_outputs`: planted secret in a finding → masked in the written `latest.json`; over-budget `data.findings` → capped in the file; disclosure present in the written summary.
- [ ] Failure card gated (planted secret in summary masked).
- [ ] **Disclosure reaches the sink:** after a cap/drop, the DELIVERED card `summary` (and `report.md`) contains the "N omitted / M withheld" text — asserted on the delivered payload, not just the return value.
- [ ] Existing delivery + report tests still pass; `build_teams_card` still renders (summary/data intact; a redacted finding just drops from the Critical section).
- [ ] Contract doc lines present (docstring + HANDOFF) naming ticketing + conversation.
- [ ] Full suite green.

**Files:** `pipeline.py`, `job.py`, `egress.py` (docstring), `docs/HANDOFF.md`, tests. **Deps:** Task 1. **Scope:** M.

---

### Checkpoint (feature complete)
- [ ] Suite green; all 3 surfaces gated; returned/stored envelope full; disclosure reaches the sink.
- [ ] Ready for opus adversarial final review — attack: any outbound path (incl. `_write_outputs`, a structured/shaped secret, a nested `data.findings` dump) that still leaks or bypasses; does redact corrupt the Teams card or a delivered KQL beyond the disclosed residual?

## Global constraints (verbatim into implementer + reviewer prompts)
- Outbound-only; NO data-read/tool/schema change. Read-only + three invariants hold.
- Gate the DELIVERED/WRITTEN payload only — never the returned envelope or the earlier-persisted store.
- Redaction is KEY+SHAPE-aware, not just `redact_secrets` per string. Size cap targets `data.findings` ONLY. Names pass (approved).
- Transparent: fold `disclosure_line` into the delivered summary; never silently drop.
- Deep-copy (never mutate the caller's object). Reuse redact_secrets / sanitize rule / cap_rows.
- Conventions (Part 1e): camelCase data / snake_case ids; nullish-not-falsy; stdlib-only (`re`,`copy`,`json`); py≥3.10. Offline deterministic tests; suite green (931 + new).
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (egress.py: redaction+floor+cap+disclosure) → Task 2 (wire 3 surfaces + disclosure + docs)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Size cap inert (misses data.findings) | Critical | Target data.findings by name; test asserts findings capped + roadmap intact |
| Structured/shaped secret leaks | Critical | Key+shape-aware redaction; tests for clientSecret dict / AccountKey / JWT |
| `_write_outputs` durable dump ungated | Important | Gate _write_outputs content; test the written file is redacted/capped |
| Disclosure never reaches recipient | Important | Fold disclosure_line into delivered summary; assert on the delivered card, not the return |
| Gate mutates shared envelope → corrupts history | Med | Deep-copy; test original unchanged; return value full |
| Cap breaks report_md/entrypoints (roadmap etc.) | Med | Cap ONLY findings; test roadmap/correlations returned intact |
| redact over-masks delivered KQL (`where key==`) | Low (disclosed) | Accepted residual; documented; surfaced via disclosure |
| Future sink bypass (ticketing/conversation/P6-9) | Med (future) | Contract doc names them + per-phase task AC + tests on the 3 current surfaces |

## Open questions
- None blocking. (Scope = chokepoint; names pass — approved.)

## Change log (v1 → v2)
- **Coverage:** added the contract-docs acceptance criterion.
- **Technical-accuracy:** confirmed pipeline.py:160 + job.py:175 are the only wired deliver sites; corrected "store fed by return" → store persists earlier independent of return; named `ticketing.py` (`open` port) + `conversation.build_concentration_alert` as unwired surfaces; noted `build_teams_card` lives in `teams_card.py` and tolerates a redacted finding.
- **Opus leak-hunt:** size cap now targets `data.findings` (was inert); redaction is key+shape-aware (was under-reaching on structured secrets); `_write_outputs` now gated; `meta` disclosure folded into the delivered summary; residual limits (sensitivity floor inert today, redact KQL over-reach, failure re-raise) disclosed in the spec; edge-case tests added.
