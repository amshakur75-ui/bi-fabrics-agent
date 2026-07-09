# Agent-Identity Plumbing (read-only-safe, inert) — Design Spec

**Date:** 2026-07-09 · **Roadmap:** Phase 5, item 3 (Interaction, Personality & **Trust**) · **Status:** design, pre-plan
**Design pre-approved:** brainstorm 2026-07-08, memory `agent-reach-identity-design` (hybrid own-identity + OBO, phased B→A; read-only + safe-outbound; build the plumbing now, INERT until Phase-7 grants).

## Purpose

Build the **read-only-safe, inert** identity plumbing so that when the Phase-7 admin grants land (Entra
Agent Identity provisioning, the Databricks user-auth/OBO toggle), activation is a config flip — not a
rewrite — and so that **today** every data read honestly records *which identity* performed it. This is
the "Trust" leg of Phase 5. It changes no data path and needs no new credentials or shared infra.

## Scope decision (YAGNI, autonomous)

The pre-approved plumbing had three parts. This item builds the two that are useful-now and
non-speculative; it **defers the third** to where its consumer lives:

- ✅ **(A) Identity-aware token-provider resolver + honest identity label.**
- ✅ **(B) Least-privilege scope manifest** (machine-readable, derived from `PERMISSIONS.md`).
- ⏭️ **(C) Outbound-action allowlist** → **deferred to Phase 6 (Proactivity & Alerting)**, its first real
  consumer. Building a typed action registry now — with zero callers until P6 — is speculative
  scaffolding; the invariant it encodes ("outbound is a typed allowlist, never open-ended") is recorded
  in the memory + HANDOFF and will be built alongside the alert delivery that uses it. (The egress
  chokepoint from 5.2 already governs outbound *payload content*; C governs outbound *action types* and
  belongs with the actions.)

## Invariants

Read-only absolute (this is auth *for reads*; it grants no write and no new scope — the SP path is
unchanged, the new branches are inert). Never label mock/proxy as live. **Feature honesty rule:** the
identity label must be *accurate* — it reports the identity that actually served the token
(`servicePrincipal` today), never a more-privileged/less-privileged identity than the one used.

## Design

### (A) Identity-aware token-provider resolver + label

New `fabric_audit_agent/identity.py` (pure/stdlib except the reused MSAL providers, lazy-imported):

**Trimmed after plan review (opus improvability):** the speculative Agent-Identity fmi_path branch (it
would test a *guessed* Phase-7 env contract / API) is DROPPED — replaced by a documented in-code
extension point. Wiring is **label-only** (option b): resolve for the label, emit the audit line, leave
all six SP token-construction sites untouched (truest to INERT — no live auth-path mutation).

```python
POWERBI_SCOPE = ...  # reuse clients.POWERBI_SCOPE
def resolve_identity(env, *, user_token=None, scope=POWERBI_SCOPE, sp_provider_factory=None):
    """Return {"provider": <zero-arg token callable>, "identity": <label>, "note": <str>}.
    Selection PRIORITY (first available wins):
      # 1. Agent Identity (federated, fmi_path) -- Phase-7 EXTENSION POINT ONLY (not implemented):
      #    when the real Agent Identity + federated-credential grant lands, add the branch HERE,
      #    ABOVE user/SP, returning identity "agentIdentity". Built against the real grant, not guessed.
      2. User OBO -- if *user_token* is passed -> a provider returning it; identity "user". (No caller
         passes one today; the real OBO exchange is a Phase-7 fill-in. Kept as a tiny roadmapped branch.)
      3. Service principal -> (sp_provider_factory or build_entra_token_provider)(tenant, client_id,
         secret, scope) from env; identity "servicePrincipal" (TODAY's only real path).
    identity label ∈ {"agentIdentity"(future),"user","servicePrincipal"}. `scope` lets a caller get the
    right-audience SP provider (POWERBI/ARM/LOGANALYTICS) so the returned provider isn't misleadingly
    POWERBI-only. Pure selection+labeling; SP delegates to the existing tested provider; a missing SP
    config surfaces the SAME RuntimeError the SP path already raises (unmasked)."""
```
- Thin seam over the *existing* providers — no MSAL reimplementation. The SP path + `user_token` branch
  + label are real and unit-tested; the agentIdentity branch is a documented extension point, NOT built
  or test-mocked (avoids locking P7 into a guessed API).
- **Wire the label (honesty value today), label-only:** at the Job's primary sweep path, call
  `resolve_identity(env)` and emit one `[identity] {"runIdentity": "servicePrincipal", ...}` stdout line
  (mirroring the `[adhoc-kql]` pattern). Do NOT thread the provider into the six SP sites (leave them —
  same identity today; no live-path change). The field is **`runIdentity`** (the run's primary-path
  identity), not `servedBy` (which would falsely imply "served every call").
- **P7 honesty marker (in code + spec):** activating any non-SP identity requires routing ALL SIX token
  sites (`job.py:50/294/295`, `clients.py:337` LA, `connectivity.py:141`, `tools.py:219`) through
  `resolve_identity` — otherwise `runIdentity` would overstate the identity while other audiences (ARM,
  LA, run_kql) still use the SP. This is a gated checklist item for P7, recorded so it can't be missed.

### (B) Least-privilege scope manifest

New data file `fabric_audit_agent/scopes.json` + loader `identity.load_scope_manifest()`:
- A versioned, machine-readable list of the **least-priv READ scopes** the agent identity needs,
  derived from `PERMISSIONS.md` (e.g. `Capacity.Read.All`, `Workspace.Read.All`, `Dataset.Read.All`,
  `Tenant.Read.All`, Log Analytics `Reader`, …), each with: `scope`, `purpose`, `grantedBy`,
  `requiredForSources` (which collectors need it), and `tier` (core vs gated-source).
- `load_scope_manifest()` returns it (tolerates missing/malformed → `[]`, like `_load_query_library`).
- **Value now (its teeth):** it is the P7 provisioning contract (exactly what to grant, least
  privilege), guarded by TWO tests: (1) a **read-only guard** — every scope is read-only (reject
  `.Write`/`.Manage`/`.ReadWrite`/`Contributor`/`Owner`); (2) a **`PERMISSIONS.md` ↔ `scopes.json`
  consistency test** — parse the enumerable API scopes (`\w+(\.\w+)*\.Read\.All`, Graph `*.Read*.All`)
  and the named Azure RBAC roles (Reader / Monitoring Reader / Log Analytics Reader / Storage Blob Data
  Reader) out of `PERMISSIONS.md` and assert set-consistency with `scopes.json` (no orphan either
  direction). Since `scopes.json` is hand-transcribed from `PERMISSIONS.md` (two sources of truth), this
  consistency test is what stops silent drift — and is precisely what justifies building the manifest
  now rather than deferring it (its only other consumer is P7). The collector-declares-required-scope
  drift test from the brainstorm is NOT built (collectors don't declare scopes today — it would be
  speculative); tracked as a HANDOFF follow-up.
- **Honesty:** every entry is READ-only; the manifest asserts the agent asks for nothing write-capable.

## Testing (TDD, offline, deterministic)

- `resolve_identity`: SP-only env → `{identity:"servicePrincipal"}` + a working provider (delegates to
  the injected `sp_provider_factory`); a passed `user_token` → `{identity:"user"}` + provider returns
  that token; `user_token` takes priority over SP; label always matches the branch taken (honesty);
  missing SP config surfaces the existing RuntimeError (unmasked); `scope` is passed through to the SP
  factory. (No agentIdentity-branch test — it's a documented extension point, not built.)
- Wiring: the primary sweep path emits an `[identity] {...}` stdout line with
  `runIdentity == "servicePrincipal"` on a normal (SP-only) config (capsys); and a **no-secret test** —
  seed a sentinel `FABRIC_CLIENT_SECRET` and assert it is absent from the emitted line (`note` is static,
  never interpolates env/secret/tenant).
- `load_scope_manifest`: parses; missing/malformed file → `[]`.
- Scope manifest **read-only guard**: every scope rejects `.Write`/`.Manage`/`.ReadWrite`/`Contributor`/`Owner`; non-empty; includes the documented core.
- Scope manifest **consistency**: the API scopes + named RBAC roles parsed from `PERMISSIONS.md` equal the set in `scopes.json` (no orphan either direction).
- Drift test: each live collector's declared required scope appears in the manifest; no orphan scope.
- Full suite stays green.

## Deploy

None required now — inert plumbing. The label wiring rides the next Job/App deploy; no behavior change
(SP path unchanged). No shared-infra action. Phase-7 activation (provisioning + toggle) is separate and
admin-gated.

## Explicitly NOT pursued — with reasons

- **Real fmi_path / OBO token acquisition** — Phase-7 (needs the Agent Identity + toggle to exist; the
  exact federated-credential API is best built against the real grant, not guessed now).
- **A built/tested Agent-Identity (fmi) branch or `FABRIC_AGENT_IDENTITY_*` env contract** — dropped
  after review: mocking a guessed P7 API is speculative and risks locking P7 into an invented shape. It's
  a documented in-code extension point above the user/SP branches instead. (The tiny `user_token` OBO
  branch is kept — real + testable by injection.)
- **Threading the resolver's provider through the 6 SP token sites** — not now (label-only wiring, option
  b; keeps the change INERT). P7 does this (and MUST, per the honesty marker) when activating a non-SP identity.
- **Outbound-action allowlist (C)** — deferred to Phase 6 (its consumer); see Scope decision.
- **Changing the SP path / requesting new scopes / any write scope** — read-only absolute; the manifest
  is documentation + drift-guard, it grants nothing.
- **Provisioning / role grants / the Databricks toggle** — Phase-7, admin-gated (never auto-done).
