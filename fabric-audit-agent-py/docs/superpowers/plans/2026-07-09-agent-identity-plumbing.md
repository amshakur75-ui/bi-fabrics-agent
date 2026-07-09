# Implementation Plan: Agent-Identity Plumbing (Phase 5.3) — v2

**Spec:** `docs/superpowers/specs/2026-07-09-agent-identity-plumbing-design.md`
**Branch:** `feat/agent-identity-plumbing` (off `main` `f6b22de`)
**Method:** superpowers SDD, TDD, per-task review. Read-only-safe, INERT — no data path/scope/credential change.
**v2:** trimmed after 3 plan-reviewers (coverage ✅ / technical-accuracy / opus improvability). Change log at bottom.

## Overview

Build the inert identity plumbing, in its leanest honest form: (A) an SP-only `resolve_identity` +
honest run-level identity **label** (with a documented Phase-7 extension point for agentIdentity, and a
tiny real `user_token`/OBO branch), and (B) a **least-priv scope manifest** (`scopes.json` + loader +
read-only guard + a `PERMISSIONS.md`↔`scopes.json` consistency test). Wiring is **label-only**: emit one
`[identity] {"runIdentity": ...}` line at the primary sweep path; leave all six SP token sites untouched.

## Architecture decisions (grounded + review-adjusted)

- **No token-provider chokepoint exists.** `build_entra_token_provider` is called at 6 scattered sites
  with *different audiences*: `job.py:50` (POWERBI, primary sweep), `job.py:294` (POWERBI), `job.py:295`
  (ARM), `clients.py:337` (LOGANALYTICS), `connectivity.py:141` (POWERBI preflight), `tools.py:219`
  (run_kql live path). So threading the resolver's provider everywhere = a 6-site refactor across 3
  audiences — NOT justified for inert plumbing (YAGNI) and it would mutate live auth (violating INERT).
- **Wiring = label-only (option b).** Call `resolve_identity(env)` once at the primary sweep path for the
  LABEL; emit `[identity] {"runIdentity": "servicePrincipal", ...}`; do not thread the provider. Zero
  live-path change; still a real P7 seam. Field is **`runIdentity`** (the run's primary-path identity),
  not `servedBy` (which would falsely imply every call).
- **Drop the speculative Agent-Identity fmi branch.** It would mock a *guessed* P7 env contract/API.
  Replace with a documented in-code extension point above user/SP. Keep the small `user_token` OBO branch
  (real, testable by injection). `resolve_identity` gets a `scope` param so its SP provider isn't
  misleadingly POWERBI-only.
- **P7 honesty marker (code + spec):** activating any non-SP identity requires routing ALL 6 sites
  through `resolve_identity`, else `runIdentity` overstates the identity — recorded as a gated checklist item.
- **`scopes.json` earns its place via a consistency test** vs `PERMISSIONS.md` (both files exist now —
  non-speculative), plus the read-only guard. The collector-declares-scope drift test is deferred
  (collectors don't declare scopes) → HANDOFF follow-up.

## Confirmed interfaces
- `clients.build_entra_token_provider(tenant_id, client_id, client_secret, scope=POWERBI_SCOPE)`, `build_user_token_provider(...)`, `clients.POWERBI_SCOPE`/`ARM_SCOPE`/`LOGANALYTICS_SCOPE`.
- `PERMISSIONS.md` scopes (all READ): Capacity/Workspace/Dataset/Tenant/Report/Dashboard/Dataflow/Item `.Read.All`; Graph `User.ReadBasic.All`/`Group.Read.All`/`Team.ReadBasic.All`; Azure `Reader`/`Monitoring Reader`/`Log Analytics Reader`/`Storage Blob Data Reader`.
- `tools._adhoc_audit_log` → `print("[adhoc-kql] " + json.dumps(rec, ...))` (mirror as `[identity]`).
- `tools._load_query_library` tolerant-load (missing/malformed → `[]`) — mirror in `load_scope_manifest`.

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → 973 on main. Keep green + new tests.

---

## Task List

### Task 1 — `identity.py` (SP resolver + label) + `scopes.json` (manifest + guards) + tests

**Interface:**
```python
def resolve_identity(env, *, user_token=None, scope=None, sp_provider_factory=None):
    """{"provider": <zero-arg token callable>, "identity": <label>, "note": <static str>}.
    Priority: (# agentIdentity — Phase-7 EXTENSION POINT, not implemented, add above here) →
      user_token given -> provider returns it, identity "user";
      else SP -> (sp_provider_factory or build_entra_token_provider)(tenant, client_id, secret,
        scope or POWERBI_SCOPE) from env, identity "servicePrincipal"; missing env -> existing RuntimeError.
    `note` is a STATIC string (never interpolates env/secret/tenant)."""

def load_scope_manifest(path=None) -> list[dict]:
    """Load package-adjacent scopes.json; entries {scope, purpose, grantedBy, requiredForSources, tier};
    missing/malformed -> []."""
```
`scopes.json`: entries transcribed from `PERMISSIONS.md`, every scope READ-only.

**Acceptance criteria:**
- [ ] SP-only env → `identity=="servicePrincipal"`, provider delegates to injected `sp_provider_factory`; `scope` passed through.
- [ ] `user_token` given → `identity=="user"`, provider returns that token; user_token beats SP (priority).
- [ ] label matches the branch actually taken; missing SP env → the existing RuntimeError (unmasked).
- [ ] `note` is static — a test seeding a sentinel secret in env asserts it never appears in `note`.
- [ ] agentIdentity is a documented extension-point comment (above user/SP), NOT implemented/mocked.
- [ ] `load_scope_manifest` parses; missing → `[]`.
- [ ] **Read-only guard:** every scope rejects `.Write`/`.Manage`/`.ReadWrite`/`Contributor`/`Owner`; non-empty; core present (Capacity.Read.All, Workspace.Read.All, Dataset.Read.All).
- [ ] **Consistency test:** API scopes (`\w+(\.\w+)*\.Read\.All`, Graph `*.Read*.All`) + named RBAC roles (Reader/Monitoring Reader/Log Analytics Reader/Storage Blob Data Reader) parsed from `PERMISSIONS.md` == the `scopes.json` set (no orphan either direction).
- [ ] Pure/deterministic; suite green.

**Files:** `fabric_audit_agent/identity.py`, `fabric_audit_agent/scopes.json`, `tests/test_identity.py`. **Deps:** none. **Scope:** M.

### Task 2 — Label-only wiring + `[identity]` audit line + P7 marker

**Changes:** at the primary sweep path (`job.py` around the `job.py:50` default-collector token build),
call `resolve_identity(env)` and emit one `[identity] {"runIdentity": <label>, "note": ...}` stdout line
(a small `_identity_audit_log` helper mirroring `_adhoc_audit_log`). Do **not** thread the provider into
the 6 SP sites (leave them). Add the P7 honesty-marker comment (activating non-SP requires routing all 6
sites) at the wire site and/or in `identity.py`.

**Acceptance criteria:**
- [ ] `[identity]` line emitted at the primary sweep path with `runIdentity=="servicePrincipal"` on SP config — capsys, injected fake provider, no real network.
- [ ] No data-path/behavior change; the 6 SP sites unchanged; existing job/collector tests pass.
- [ ] No secret/token in the emitted line (sentinel test).
- [ ] P7 honesty-marker comment present (names the 6 sites).
- [ ] Suite green.

**Files:** `fabric_audit_agent/job.py` (one site), `fabric_audit_agent/identity.py` (helper/marker), tests. **Deps:** Task 1. **Scope:** S.

### Also (docs, in Task 2)
- HANDOFF follow-up line: the collector-declares-required-scope drift test is deferred (collectors don't declare scopes yet).

---

### Checkpoint (feature complete)
- [ ] Suite green; resolver SP/user label tested; manifest read-only + PERMISSIONS.md-consistency guarded; `[identity]` line emits `runIdentity`; SP behavior unchanged; no secret in line; P7 marker present.
- [ ] Ready for opus final review — attack: does the label ever overstate identity? does the manifest list a write scope or drift from PERMISSIONS.md? does wiring change the data path or leak a token?

## Global constraints (verbatim into implementer + reviewer prompts)
- Read-only absolute; INERT — no new scope/credential/data-path change; SP path unchanged; do NOT implement real fmi/OBO acquisition (Phase-7, admin-gated).
- Identity label ACCURATE + honestly scoped as the run's primary-path identity (`runIdentity`), never "every call".
- Never log token/secret in the `[identity]` line; `note` is static.
- Reuse existing MSAL providers. camelCase data / snake_case ids; nullish-not-falsy; stdlib-only (+ lazy MSAL via existing providers); py≥3.10. Offline deterministic tests (inject fakes; no real network); suite green (973 + new).
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (identity.py + scopes.json + guards) → Task 2 (label-only wiring + [identity] line + P7 marker)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Label overstates identity (esp. after partial P7 activation) | Med (honesty) | `runIdentity` scoped to primary path; P7 marker requires wiring all 6 sites; label==branch test |
| Manifest lists a write scope | Med | Read-only guard test |
| scopes.json drifts from PERMISSIONS.md (2 sources of truth) | Med | Consistency test (parse PERMISSIONS.md, assert equal sets) |
| Speculative dead code (guessed fmi API) | — | Dropped; agentIdentity is a comment-only extension point |
| Token leaked into audit line | Med | label+static note only; sentinel-secret-absent test |
| Wiring changes data path / breaks SP auth | Low | Label-only; the 6 sites untouched; existing tests pass |

## Open questions
- None blocking.

## Change log (v1 → v2)
- **Coverage:** flagged the dropped collector-drift test as a conscious deviation → now a HANDOFF follow-up.
- **Technical-accuracy (self):** `build_entra_token_provider` is at 6 scattered sites/3 audiences, not one → wiring reframed as label-only; PERMISSIONS.md scopes + `[adhoc-kql]` pattern confirmed.
- **Opus improvability:** wiring → label-only (option b, no live-path mutation); dropped the speculative fmi branch/env-contract → documented extension point; renamed `servedBy`→`runIdentity` + added the P7-must-wire-all-6-sites honesty marker; added `scope` param so the SP provider isn't POWERBI-only; gave `scopes.json` teeth via a `PERMISSIONS.md` consistency test; added a no-secret-in-line test.
