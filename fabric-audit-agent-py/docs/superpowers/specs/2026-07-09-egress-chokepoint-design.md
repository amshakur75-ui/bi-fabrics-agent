# Egress Chokepoint (Anti-Exfil) — Design Spec

**Date:** 2026-07-09 · **Roadmap:** Phase 5, item 2 (Interaction, Personality & Trust) · **Status:** design, pre-plan

## Purpose

Establish a single, enforced **egress chokepoint** every outbound/broadcast payload passes through
before it leaves the agent to a sink (the scheduled Job's delivery/notification today; Phase-6
Teams/Activator alerts, Phase-9 UI exports, Phase-8 external memory later). It applies secret
redaction, a labeled-sensitive floor, and a size cap — so no current or future sink can broadcast a
credential, an explicitly-sensitive item, or an unbounded data dump. This is the foundational control
the roadmap says **gates Phases 6 & 9** (both add new egress surfaces).

**Approved decisions (brainstorm 2026-07-09):** scope = the egress chokepoint (not injection-exfil
defense, not conversational-answer redaction); **identifiers/names PASS through** — broadcast sinks are
treated as trusted internal, so alerts/UI stay actionable (the user's explicit call; disclosed as the
weakest-privacy option; the gate's shape leaves room to add per-sink name control later without rework).

## Invariants

The three project invariants hold. This feature is **outbound-only** — it never touches a data read, a
tool, or a schema, so read-only is trivially preserved. Feature-specific honesty rule: the gate is
**transparent** — it returns `meta` disclosing what it redacted/dropped/truncated; a sink surfaces that
(e.g. "3 rows omitted", "a sensitive item was withheld"). The gate **never silently drops** a
load-bearing figure without a disclosable signal.

## What already exists (reused, not rebuilt)

- `query/redact.py::redact_secrets(text)` — masks SAS tokens, `bearer`, and secret-like `key=value`
  in a **string**. (Used today only on stdout audit lines.)
- `sanitize.py::sanitize_evidence` — drops elements flagged `sensitive: true` / `sensitivityLabel`,
  keeps numbers/enums, strips identifying strings. (Used today only on the Job reasoner's LLM input.)
- `query/envelope.py::cap_rows(records, *, max_chars)` — char-budget row cap returning `(rows, meta)`.

The egress gate **composes** these; the LLM-input `sanitize()` stays as its own (separate) control.

## Design

**New pure/stdlib module `fabric_audit_agent/egress.py`:**

```python
def apply_egress_controls(payload, *, sink, max_chars=12000) -> tuple[object, dict]:
    """Return (safe_payload, meta) for an outbound payload bound for a broadcast/external *sink*.
    ALWAYS, in order:
      1. Labeled-sensitive floor: any dict flagged sensitive:true or carrying a sensitivityLabel is
         replaced with {"redacted": true} (reuse sanitize's rule), recursively.
      2. Deep secret redaction: every string value (recursively, in dicts/lists) is run through
         redact.redact_secrets, so a credential in any field/URL/deeplink is masked.
      3. Size cap: if payload is a list, apply cap_rows(max_chars). If payload is a dict, apply
         cap_rows to each list-valued field whose own serialized size exceeds max_chars (cap each
         independently; record the largest omission in meta). Scalars/small fields untouched.
      4. Identifiers/names PASS through unchanged (approved).
    Pure, deterministic; never raises (a malformed payload degrades to a safe, disclosed result).
    meta = {"sink": sink, "secretsRedacted": int, "sensitiveDropped": int, "truncated": bool,
            "rowsOmitted": int}.
    """
```
- `sink` is a label (e.g. `"delivery"`, `"teams"`, `"ui"`, `"memory"`) recorded in `meta` for audit;
  it does not change behavior today (hook for future per-sink policy — e.g. the deferred name control).
- Deep-walk helper is internal; handles dict/list/scalar; leaves non-string scalars (numbers/bools)
  untouched (they're never secrets and are load-bearing).

**Wiring (enforcement):** route the current outbound seam — the Job's `deliver` port
(`adapters/delivery_file.py` and any notification delivery) — through `apply_egress_controls` before
the payload leaves. The delivered envelope is the gated one; `meta` is attached/logged.

**The "gates Phases 6 & 9" contract:** documented in the module docstring + HANDOFF: **every outbound
sink MUST call `apply_egress_controls`; the gate is the only sanctioned way to emit outward.** Phase-6
alerts, Phase-9 UI export, and Phase-8 memory each carry "routes through `apply_egress_controls`" as a
task acceptance criterion, checked in their reviews. (A static "no sink bypasses" test can't cover
not-yet-written code; the enforceable part today is: the gate exists, the current delivery path uses
it, and a test proves that.)

## Testing (TDD, offline, deterministic)

- **Secret redaction:** a payload with a SAS `?sig=...`/`bearer x`/`client_secret=x` in a nested
  string/URL → masked; `secretsRedacted` counts them; a benign `where Status=200` predicate is NOT
  mangled (redact's allowlist behavior).
- **Sensitivity floor:** a dict with `sensitive: true` or `sensitivityLabel` → `{"redacted": true}`,
  recursively (nested sensitive element dropped); `sensitiveDropped` counts them; `sensitive: false`
  passes.
- **Size cap:** an over-budget list → capped; `truncated: true`, `rowsOmitted` correct; under-budget
  unchanged.
- **Names pass:** a payload with user emails / dataset names → those survive unchanged (proves the
  approved decision, guards a future over-zealous edit from silently stripping them).
- **Purity/robustness:** deterministic; non-dict/None/malformed payload → safe result, no raise;
  numbers/bools untouched.
- **Wiring:** the delivery seam emits a gated payload (a planted secret in a delivered finding is
  masked at the sink); `meta` present.
- Full suite stays green.

## Deploy

The gate lives in the `fabric_audit_agent` package. It affects the **scheduled Job's** delivery path
(and future sinks) — NOT the MCP tools or the conversational agent. So: redeploy the **Job** (bundle
wheel) when it next ships; **no MCP-app or agent-app behavior change** from this item (the MCP tools
don't deliver outward). Confirm at deploy time; this item is primarily foundational plumbing consumed
by later phases.

## Explicitly NOT pursued — with reasons

- **Stripping / aggregating / pseudonymizing identifiers on broadcast** — the user chose names-pass
  (trusted internal sinks; actionable alerts). The `sink` param + `meta` leave room to add per-sink
  name policy later without reshaping callers.
- **Conversational-answer redaction** — declined in brainstorm; the requester is trusted and needs the
  detail; already covered by the honesty prompt + `cap_rows`.
- **Prompt-injection exfil defense** — a separate Phase-5 concern (the prompt-level injection defense
  already exists); not this item.
- **A static "no future sink bypasses the gate" test** — can't cover unwritten code; enforced instead
  by the documented contract + per-phase task acceptance criteria + the wiring test on today's seam.
- **Changing the LLM-input `sanitize()`** — it's a separate, still-valid control on what the reasoner
  sees; untouched.
