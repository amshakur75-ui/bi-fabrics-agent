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

```python
def resolve_identity(env, *, user_token=None, sp_provider_factory=build_entra_token_provider):
    """Return {"provider": <zero-arg token callable>, "identity": <label>, "note": <str>}.
    Selection PRIORITY (first available wins), matching the phased B→A plan:
      1. Agent Identity (federated, fmi_path) — if env configures FABRIC_AGENT_IDENTITY_* .
         INERT today: the real fmi_path/federated-credential acquisition is a Phase-7 fill-in; when
         unconfigured this branch returns None and we fall through. (No half-built MSAL-federation call
         that can't be tested against a real Agent Identity — that lands in P7 when the grant exists.)
      2. User OBO — if a *user_token* is passed in (the Databricks user-auth/OBO toggle, Phase-7).
         INERT today: no caller passes one; the exchange is a P7 fill-in.
      3. Service principal (build_entra_token_provider) — TODAY'S path; label "servicePrincipal".
    identity label ∈ {"agentIdentity","user","servicePrincipal"}; note is a human one-liner for audit.
    Pure selection + labeling; the SP branch delegates to the existing tested provider. Never raises for
    a normal env (a missing SP config surfaces the same RuntimeError the SP path already raises)."""
```
- The resolver is a thin seam over the *existing* providers — it does not reimplement MSAL. The
  non-SP branches are extension points exercised in tests via injection (a fake fmi source / a passed
  `user_token`), so the *selection logic + label* are covered now; the real P7 token acquisition slots
  into the marked branches without touching callers.
- **Wire the label (honesty value today), minimally:** the Job/collector token-provider construction
  calls `resolve_identity(...)` (so the seam is real, not dead code) and emits a one-line
  `[identity] {"servedBy": "servicePrincipal", ...}` stdout audit record — mirroring the existing
  `[adhoc-kql]` audit-line pattern (captured by Databricks App/Job logging). No threading through
  collector→pipeline→runLog; the resolver also returns the label so any caller can use it. That's the
  honest "which identity read the data" signal, cheaply.

### (B) Least-privilege scope manifest

New data file `fabric_audit_agent/scopes.json` + loader `identity.load_scope_manifest()`:
- A versioned, machine-readable list of the **least-priv READ scopes** the agent identity needs,
  derived from `PERMISSIONS.md` (e.g. `Capacity.Read.All`, `Workspace.Read.All`, `Dataset.Read.All`,
  `Tenant.Read.All`, Log Analytics `Reader`, …), each with: `scope`, `purpose`, `grantedBy`,
  `requiredForSources` (which collectors need it), and `tier` (core vs gated-source).
- `load_scope_manifest()` returns it (tolerates missing/malformed → `[]`, like `_load_query_library`).
- **Value now:** it is the P7 provisioning contract (exactly what to grant the Agent Identity, least
  privilege), and a **drift test** asserts every live collector's required scope is present and no
  scope is orphaned — so scope creep/rot is caught in CI.
- **Honesty:** every entry is READ-only; the manifest asserts the agent asks for nothing write-capable.

## Testing (TDD, offline, deterministic)

- `resolve_identity`: SP-only env → `{identity:"servicePrincipal"}` + a working provider (delegates to
  the injected `sp_provider_factory`); a passed `user_token` → `{identity:"user"}`; a configured fake
  fmi source → `{identity:"agentIdentity"}`; priority order (agentIdentity > user > SP) proven; label
  always matches the branch taken (honesty); missing SP config surfaces the existing error.
- Wiring: constructing the token provider via `resolve_identity` emits an `[identity] {...}` stdout line
  with `servedBy == "servicePrincipal"` on a normal (SP-only) config; captured via capsys.
- `load_scope_manifest`: parses; every scope is READ-only (no `.Write`/`.Manage`); missing file → `[]`.
- Drift test: each live collector's declared required scope appears in the manifest; no orphan scope.
- Full suite stays green.

## Deploy

None required now — inert plumbing. The label wiring rides the next Job/App deploy; no behavior change
(SP path unchanged). No shared-infra action. Phase-7 activation (provisioning + toggle) is separate and
admin-gated.

## Explicitly NOT pursued — with reasons

- **Real fmi_path / OBO token acquisition** — Phase-7 (needs the Agent Identity + toggle to exist; the
  exact federated-credential API is best built against the real grant, not guessed now). The seam +
  selection logic + label are built now so activation is a fill-in.
- **Outbound-action allowlist (C)** — deferred to Phase 6 (its consumer); see Scope decision.
- **Changing the SP path / requesting new scopes / any write scope** — read-only absolute; the manifest
  is documentation + drift-guard, it grants nothing.
- **Provisioning / role grants / the Databricks toggle** — Phase-7, admin-gated (never auto-done).
