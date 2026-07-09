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
      2. Secret redaction, KEY- and SHAPE-aware (not just string-internal — plan-review Critical):
         recursively walk dicts/lists and for each string value apply, in this order:
           (a) KEY-aware: if the containing dict KEY matches the secret allowlist (secret, token,
               password, pwd, apikey, api_key, key, client_secret, sig, access_token, connectionstring,
               accountkey, sharedaccesskey — case-insensitive) → mask the whole value. This catches the
               structured case redact_secrets misses (`{"clientSecret": "s3cr3t"}` — name is the key,
               value is separate).
           (b) VALUE-shape: mask a value that looks like a secret regardless of key — a JWT
               (`eyJ...` two-dot base64url), a connection-string segment (`AccountKey=`/
               `SharedAccessKey=`/`Password=`), a long opaque base64 token.
           (c) then run redact_secrets(value) for the in-string `name=value`/SAS/bearer cases.
         (redact_secrets alone under-reaches: `\bkey=` misses `AccountKey=`, and a value whose key
         holds the secret name never matches.) Numbers/bools untouched.
      3. Size cap: cap the KNOWN rows list `payload["data"]["findings"]` (the audit envelope's only
         unbounded list) via cap_rows(max_chars); if `payload` is itself a list, cap it directly.
         Do NOT blanket-cap other `data` lists (roadmap/correlations/anomalies/suppressed are
         bounded, structured, and indexed by consumers — capping them mid-structure breaks
         report_md/entrypoints). Record truncated + rowsOmitted.
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

**Wiring (enforcement) — three current outbound surfaces, all gated:**
1. `pipeline.py:160` `delivery["deliver"](envelope)` (main sweep → file/Teams). Gate the deliver ARG
   only; leave `return envelope` full (the run-history `store` already persisted earlier at
   `pipeline.py:100-108`, independent of the return value — so it's untouched either way).
2. `job.py:175` the failure-delivery card (`{"summary": ...}`).
3. `_write_outputs` (`job.py`, writes `latest.json` + `report.md` to a Databricks Volume): a durable,
   shareable dump — gate its file content too (at minimum secret-redaction; a Volume file is arguably
   MORE exfil-prone than a Teams card). Not "internal, therefore exempt."

**Meta disclosure MUST reach the recipient (not just a log):** the gate returns `meta`, and the wiring
folds a one-line disclosure into the DELIVERED payload's `summary` (which `teams_card.build_teams_card`
and `report_md` both read) when anything was dropped/capped — e.g. "(N findings omitted; M sensitive
items withheld)". Computing `meta` and logging it internally is NOT disclosure to the sink.

**The "gates Phases 6 & 9" contract:** documented in the module docstring + HANDOFF: **every outbound
sink MUST call `apply_egress_controls`; the gate is the only sanctioned way to emit outward.** Two
existing package surfaces are NOT yet wired but MUST route through it when activated (name them so
they're not missed): `adapters/ticketing.py::create_ticketing_delivery` (an `{"open": open_}` port,
NOT `deliver` — it takes a findings LIST, so it gates the list: `apply_egress_controls(findings,
sink="ticketing")`) and `conversation.py::build_concentration_alert` (builds a Teams card from raw
evidence). Phase-6/8/9 sinks carry "routes through `apply_egress_controls`" as a task AC. (A static
"no sink bypasses" test can't cover unwritten code; today's enforcement = the gate exists, all three
current surfaces use it, and tests prove it.)

## Known residual limits (disclosed, not hidden)

- **Sensitivity floor is inert on today's envelopes** — findings carry no `sensitive`/`sensitivityLabel`
  (those live on detector *evidence*, handled on the separate LLM-input `sanitize()` path). The floor is
  future-proofing + catches only *explicitly-flagged* dicts; it does NOT detect an unflagged sensitive
  dataset named in a finding string (names pass, by decision). State this; don't imply blanket coverage.
- **redact over-reach on delivered KQL:** `redact_secrets` masks `key=value` for allowlisted names, so a
  legitimate KQL predicate on a column literally named `key` (`| where key=="prod"`) in
  `runLog.queryKql` renders as `key=***`. Accepted (only affects broadcast payloads; correct caution)
  and surfaced via the meta/disclosure; documented so it's not mistaken for a bug.
- **Failure re-raise:** `job.py` gates the failure *card*, but `main()`/`job_main()` still `raise` after,
  so the raw exception (possibly secret-bearing) lands in the Databricks driver stderr — that's
  log-safety, out of this gate's scope; noted as a follow-up (the redaction helper could later wrap it).

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
