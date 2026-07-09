# Implementation Plan: Agent-Identity Plumbing (Phase 5.3)

**Spec:** `docs/superpowers/specs/2026-07-09-agent-identity-plumbing-design.md`
**Branch:** `feat/agent-identity-plumbing` (off `main` `f6b22de`)
**Method:** superpowers SDD, TDD, per-task review. Read-only-safe, INERT — no data path/scope/credential change.

## Overview

Build the inert identity plumbing: (A) an identity-aware token-provider **resolver + honest label**
(SP path real; agentIdentity/OBO branches are Phase-7 fill-ins whose *selection + label* are tested
now by injection), and (B) a **least-privilege scope manifest** (`scopes.json` + loader + read-only
guard). Wire the resolver into one token-provider construction site and emit an `[identity]` audit line.
Outbound-action allowlist (C) is deferred to Phase 6 (per spec).

## Architecture decisions (grounded in the code)

- **Reuse existing providers, don't reimplement MSAL.** `clients.build_entra_token_provider(tenant,
  client_id, secret, scope)` (SP client-credentials) and `build_user_token_provider(...)` (device-code)
  already exist and return zero-arg token callables. `resolve_identity` is a thin selection+label seam
  over them; the SP branch delegates to `build_entra_token_provider` (injectable as
  `sp_provider_factory` for tests).
- **Non-SP branches are inert extension points**, exercised in tests via injection (a fake fmi source; a
  passed `user_token`) — NOT half-built real federated/OBO MSAL calls (those land in Phase 7 against the
  real grant). This tests the *selection logic + label* now without guessing the P7 API.
- **Minimal wiring:** call `resolve_identity` at ONE real token-provider construction site (the Job's
  primary data-collector auth path) so the seam is live (not dead code) and emit a single
  `[identity] {"servedBy": "...", ...}` stdout line (mirroring `tools._adhoc_audit_log`'s `[adhoc-kql]`
  pattern). No threading through collector→pipeline→runLog. Behavior is identical (same SP token).
- **Scope manifest is data + guard, not a live authz control.** `scopes.json` documents the least-priv
  READ scopes (from `PERMISSIONS.md`) for Phase-7 provisioning; the guard test asserts every entry is
  read-only (no `.Write`/`.Manage`/`.ReadWrite`) and the set is non-empty/covers the documented core.
  (Collector-declares-required-scope drift-checking is a future enhancement — collectors don't declare
  scopes today, so asserting that now would be speculative; noted, not built.)

## Confirmed interfaces
- `clients.build_entra_token_provider(tenant_id, client_id, client_secret, scope=POWERBI_SCOPE) -> callable`.
- `clients.build_user_token_provider(...) -> callable` (device-code; exists).
- `PERMISSIONS.md` (repo) lists the scopes: `Capacity.Read.All`, `Workspace.Read.All`, `Dataset.Read.All`,
  `Tenant.Read.All`, `Report.Read.All`, `Dashboard.Read.All`, `Dataflow.Read.All`, `Item.Read.All`,
  Graph `User.ReadBasic.All`/`Group.Read.All`, Azure `Reader`/`Monitoring Reader`/`Log Analytics Reader`,
  `Storage Blob Data Reader` — all READ.
- `[adhoc-kql]` audit-line pattern: `print("[adhoc-kql] " + json.dumps(rec, ...))` — mirror as `[identity]`.

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → 973 on main. Keep green + new tests.

---

## Task List

### Task 1 — `identity.py` (resolver + label) + `scopes.json` (manifest + loader) + tests

**Interface:**
```python
def resolve_identity(env, *, user_token=None, sp_provider_factory=None):
    """Return {"provider": <zero-arg token callable>, "identity": <label>, "note": <str>}.
    Priority (first available wins):
      1. Agent Identity (fmi_path) — if env has FABRIC_AGENT_IDENTITY_* configured. INERT today:
         unconfigured -> None -> fall through. (Real acquisition = Phase-7 fill-in; the branch exists
         and, if a test injects a fake fmi provider via env/factory, returns it labeled "agentIdentity".)
      2. User OBO — if user_token is not None -> a provider returning it, label "user". (P7 exchange = fill-in.)
      3. Service principal -> (sp_provider_factory or build_entra_token_provider)(tenant, client_id,
         secret, ...) from env; label "servicePrincipal". Missing SP env surfaces the SAME RuntimeError
         the SP path already raises (don't mask it).
    label ∈ {"agentIdentity","user","servicePrincipal"}. Pure selection+labeling; delegates token
    acquisition to the existing providers."""

def load_scope_manifest(path=None) -> list[dict]:
    """Load scopes.json (package-adjacent default). Each entry: {scope, purpose, grantedBy,
    requiredForSources, tier}. Missing/malformed -> [] (like _load_query_library)."""
```
`scopes.json`: package-adjacent data file, entries transcribed from `PERMISSIONS.md`, every scope READ-only.

**Acceptance criteria:**
- [ ] SP-only env → `identity=="servicePrincipal"`, provider works (delegates to injected `sp_provider_factory`).
- [ ] `user_token` passed → `identity=="user"`, provider returns that token.
- [ ] fake fmi configured (via env + factory injection) → `identity=="agentIdentity"`; priority proven (agentIdentity > user > SP).
- [ ] label always matches the branch actually taken (honesty); missing SP env → the existing RuntimeError (not masked).
- [ ] `load_scope_manifest` parses; missing file → `[]`.
- [ ] **Read-only guard:** every manifest scope is read-only — no `.Write`/`.Manage`/`.ReadWrite`/`Contributor`/`Owner`; set non-empty and includes the documented core (Capacity.Read.All, Workspace.Read.All, Dataset.Read.All).
- [ ] Pure/deterministic; suite green.

**Files:** `fabric_audit_agent/identity.py`, `fabric_audit_agent/scopes.json`, `tests/test_identity.py`. **Deps:** none. **Scope:** M.

### Task 2 — Wire the resolver + `[identity]` audit line

**Changes:** at the Job's primary token-provider construction site (in `clients.py` build path used by the
live collector, or `job.py` collector build — pick the single cleanest site), obtain the provider via
`resolve_identity(env)` and emit one `[identity] {"servedBy": <label>, "note": ...}` stdout line. Behavior
unchanged (SP token identical). Keep it to one site (don't refactor every client).

**Acceptance criteria:**
- [ ] The wired site uses `resolve_identity` (seam is live, not dead code); the SP token path still works (existing collector/job tests pass unchanged).
- [ ] An `[identity]` stdout line with `servedBy=="servicePrincipal"` is emitted on a normal SP-config run — asserted via capsys with an injected fake provider (no real network).
- [ ] No data-path/behavior change; read-only holds; suite green.

**Files:** `fabric_audit_agent/clients.py` or `job.py` (one site), tests. **Deps:** Task 1. **Scope:** S.

---

### Checkpoint (feature complete)
- [ ] Suite green; resolver selection+label tested; manifest read-only-guarded; `[identity]` line emitted; SP behavior unchanged.
- [ ] Ready for opus final review — attack: does any branch mislabel the serving identity (honesty)? does the manifest list a write scope? did wiring change the data path or leak a token into the audit line?

## Global constraints (verbatim into implementer + reviewer prompts)
- Read-only absolute; INERT plumbing — no new scope/credential/data-path change; SP path unchanged; activations are Phase-7 admin-gated (do NOT implement real fmi/OBO acquisition).
- Identity label must be ACCURATE (report the identity that served the token; never over/under-state).
- Never log a token/secret in the `[identity]` line — label + note only (no token material).
- Reuse existing MSAL providers; don't reimplement. camelCase data / snake_case ids; nullish-not-falsy; stdlib-only (+ lazy MSAL via the existing providers); py≥3.10. Offline deterministic tests (inject fakes; no real network); suite green (973 + new).
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (identity.py + scopes.json) → Task 2 (wire + [identity] line)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Mislabeled identity (says SP but used something else) | Med (honesty) | Label derived from the branch taken; tests assert label==branch |
| Manifest lists a write scope | Med | Read-only guard test (reject .Write/.Manage/.ReadWrite/Contributor/Owner) |
| Wiring changes the data path / breaks SP auth | Med | One site; SP delegates to existing provider; existing collector/job tests must pass |
| Token material leaked into the [identity] audit line | Med | Line carries label+note only; test asserts no token substring |
| Speculative dead code (non-SP branches) | Low | Branches are minimal seams tested by injection; real acquisition deferred to P7 with a clear marker |

## Open questions
- None blocking. (Design pre-approved; scope trimmed per YAGNI — C deferred to P6.)
