# ADR-001: The MCP-serving package is tools-only; the Databricks App is the real agent

## Status
Accepted

## Date
2026-07-29

## Context

This project has two SEPARATE deployed Databricks Apps, communicating over the network (not
a shared process or a package-plus-thin-wrapper on one host):

| Local folder | Deployed Databricks App name | Role |
|---|---|---|
| `fabric-audit-agent-app\` | **fabric-audit-agent** | Chat app hosting the chat UI |
| `fabric-audit-agent-py\` | **mcp-bi-fabrics-auditor** | MCP server exposing ~19 read-only tools |

The chat app calls the MCP server's tools over real HTTP/OAuth (`DatabricksMCPClient`), not an
in-process import. This network boundary makes the tools-only/host-agent split below a genuine
architectural requirement, not just a code-organization preference — the MCP app has no reason
to ever construct a prompt or run a tool-calling loop; it only ever answers `tools/list` and
`tools/call`.

Until this decision, the *MCP server's package* also owned the actual agent logic — the system
prompt (`fabric_audit_agent/agent/system_prompt.py`) and the tool-calling loop
(`fabric_audit_agent/agent/loop.py`) both lived inside the tools package, not the chat app. This
was backwards: standard MCP architecture puts orchestration (system prompt, tool-calling loop,
decision logic) in the client/host, not the server.

The chat app was supposed to import and delegate to that package logic, staying a thin wrapper
(auth, transport, streaming). Instead it grew its own separate, hardcoded copy of both the
prompt and the loop. The two copies diverged over time: system-prompt fixes (SP1–SP7,
including an actively-wrong `"converted% (lifetime%)"` cell format) were written into the
package's canonical file across multiple sessions, but the *deployed* chat app kept running its
own stale copy that never received any of them. This was discovered by directly comparing two
real production transcripts against the two files — the deployed app was verifiably still
producing output that the canonical file had already fixed weeks earlier (tracked as gap **C2**,
and the tool-loop half of the same problem as **N15**).

The root question this ADR resolves: once the duplication is fixed, which file should be
canonical — should the chat app import from the MCP package, or should the package's agent logic
move into the chat app and the package keep only tools?

## Decision

**The package is tools-only. The app owns the system prompt and the tool-calling loop.**

`fabric_audit_agent/agent/system_prompt.py` and `fabric_audit_agent/agent/loop.py` are deleted.
Their content (the canonical, up-to-date versions, since the package's copies had received the
SP1–SP7 fixes and the app's had not) moves directly into the app's own codebase. The package
retains: tool definitions and dispatch (`tools.py`), the collectors/adapters, the detectors,
the investigation/gates logic, and the Phase 2 grounding schema (`kb/`). None of that is agent
orchestration — it's the deterministic data-processing logic the tools call into, which
correctly belongs in a tools package regardless of which client calls it.

## Alternatives Considered

### Keep agent logic in the package; fix the app to import from it
- **Pros:** Matches the divergence's naive fix — "make the app import from the package" is the
  smaller-looking diff.
- **Cons:** Backwards from standard MCP architecture. An MCP server's job is to expose tools;
  the orchestration (system prompt, tool loop, decision logic) belongs in the client/host. This
  option would keep the package doing a job it structurally shouldn't be doing, and would block
  any *other* client (a different UI, a CLI, a different chat surface) from bringing its own
  personality/orchestration while still using these same tools — the whole point of exposing
  them via MCP in the first place.
- **Rejected:** Fixes the symptom (divergence) but not the underlying architectural mismatch
  that let the divergence happen unnoticed for weeks.

### Leave both copies in place, just keep them manually in sync
- **Pros:** No refactor needed.
- **Cons:** This is exactly the situation that produced C2/N15. Manual sync across two files in
  two separate repos has already been proven, empirically, not to hold up over time.
- **Rejected:** Doesn't solve the actual problem, just re-commits to the failure mode that
  caused it.

## Consequences

- The app (`agent_server/agent.py` or a clean split of sibling files within the app) becomes the
  single, canonical home of the prompt and the loop. There is exactly one copy of each,
  anywhere, in either codebase.
- The Phase 9 autonomous-polling driver (not yet audited/built) belongs in the app alongside
  the rest of the agent logic, not bolted onto the tools package.
- Any future client wanting to use these same tools (a different UI, a CLI, an internal Slack
  bot) can bring its own system prompt and loop without needing to fork or reinterpret anything
  in the tools package — it only needs to speak MCP to it.
- The migration itself needs care: the package's `system_prompt.py` held the up-to-date content
  (SP1–7) at the time of this decision; the app's copy was stale. The correct migration
  direction is "package's good content moves into the app, then the package's `agent/`
  subfolder is deleted" — not the reverse, and not a blind merge of the two.
- **The agent-case eval suite must move too, or it silently breaks.**
  `fabric_audit_agent/agent/investigator.py` imports `build_system_prompt()` and
  `run_tool_loop()` directly from the two files this decision deletes. It's what
  `score_investigations.py`'s `score_agent_case()` uses to run every case in `agent_cases.json`
  (35 cases as of 2026-07-29). Deleting `agent/system_prompt.py`/`agent/loop.py` without moving
  `investigator.py` (and the `agent_cases.json`-driven half of `score_investigations.py`) into
  the chat app breaks that eval suite silently — it's testing agent behavior, so once the agent
  lives in the chat app, its test harness belongs there too. This is consistent with the decision,
  not an exception to it. `investigation_cases.json` and `score_investigation_case()` (the OTHER
  half of the same file, testing playbooks directly with no prompt/loop involved) stay in the
  package — that's genuinely tools-side logic. Do this move as PART of Task 1/2, not as separate
  follow-up cleanup — an interim state where the move is only half-done is exactly the kind of
  gap that let C2/N15 happen in the first place.
- This work is tracked as Phase 1, Tasks 1–2 in `tasks/todo.md`, assigned to Claude Code since
  it requires live test execution to verify safely (see `tasks/plan.md`'s division of labor
  between this session and Claude Code).
