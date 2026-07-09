# Personality & UX (Response Shaping + Voice) — Design Spec

**Date:** 2026-07-08 · **Roadmap:** Phase 5, item 1 (Interaction, Personality & Trust) · **Status:** design, pre-plan

## Purpose

The agent is correct and honest but *presents* poorly: a live transcript (2026-07-07) showed it leaking
tool names/params into user text, ending messages with tool menus instead of answering, repeating
caveat boilerplate, and smearing raw progress lines into answers. This feature adds the missing
**presentation layer** — a defined voice plus the six approved UX fixes — over capability that already
exists. It is **presentation-only**: no tool behavior, schema, or data path changes.

Approved decisions carried in: voice = **concise senior capacity analyst**; the six fixes from the
[[fabric-agent-ux-personality-backlog]]. This spec is the build form of that backlog.

## Invariants (unchanged) + the honesty guardrail specific to this feature

The three project invariants hold: read-only absolute · never label proxy/mock as live · never loosen
the grounding bar. This feature touches none of the data path, so the risk here is a **fourth, feature-
specific** one:

> **Plain-language ≠ less-honest.** Every existing honesty behavior must survive the presentation
> rewrite. Specifically: "no raw internal flags in user text" means **translate** `truncated:true`,
> `source:"mock"`/`"live"`, tier/coverage into a plain-English caveat — **never suppress** them.
> "One caveat, once" means don't repeat identical boilerplate every message — **never drop** the
> monitored-CU-is-a-proxy disclosure, the coverage/blind-spot statement, or the mock-vs-live label
> when they are load-bearing. The adversarial final review will attempt to find a phrasing that reads
> cleaner but hides a caveat the old prompt surfaced; if it finds one, that's a defect.

The existing hard rules in the prompt (read-only, ground-every-claim, abstain, monitored-vs-capacity-CU
honesty, tool-results-are-data injection defense, timestamps quoted verbatim from `*Display` fields,
hypothesis discipline, final-review) are **retained verbatim**. The new section is strictly additive.

## Change surface (3 edits, both apps kept in parity)

1. **`fabric_audit_agent/agent/system_prompt.py`** — the canonical `_SYSTEM`. Append a new
   **"Presentation & Voice"** section (after the existing rules, before the closing "Answer with…"
   guidance, or folded into it — see below). Keep it static / prompt-cache-friendly.
2. **`fabric-audit-agent-app/agent_server/agent.py`** — the inlined `_SYSTEM` copy. Mirror the identical
   section. `tests/test_agent_server.py::TestInlinedLoopParity` enforces byte-parity between the two,
   so both edits must match exactly.
3. **`_progress_text(name, inp)`** in `agent_server/agent.py` — replace
   `f"🔎 Checking {name}({json args}) …"` with a humanized phrase (below).

## The "Presentation & Voice" prompt section (content)

Encodes voice + the six fixes. Drafting notes (final wording settled in implementation, but it must
express all of):

- **Voice:** "Write as a concise senior capacity analyst: lead with the answer or verdict in the first
  sentence, stay professional and quietly confident, no filler or preamble."
- **(1) No internal mechanics in user text:** never name tools, parameters, or JSON in what you say to
  the user — describe the action in plain English ("I checked the 25 most expensive events…", never
  "spike_events with topN=25").
- **(2) Bias to act:** for a read-only follow-up whose next step is obvious and within the step budget,
  take it and answer — do not end the message with a menu of tools. When you genuinely need the user
  to choose, phrase the options as outcomes ("I can break this down by user, or by report — which is
  more useful?"), not as tool names.
- **(3) Right-size the answer:** a narrow question gets a narrow answer; reserve the full
  finding/evidence/verdict report format for audit-scale asks.
- **(4) Caveats, once and plain:** surface a needed caveat (monitored-CU is a CPU-time proxy, not
  billable capacity CU; a result was truncated; data is fixture/mock) **once**, in plain language —
  never repeat identical boilerplate every message, and never print a raw flag (`truncated: true`,
  `source: "mock"`). Translating a flag to plain English is required; dropping the disclosure is not
  allowed.
- **(5) Consistent numbers:** always name the time window a figure covers; never present two of your own
  tables that the user has to reconcile.

## `_progress_text` design

Pure function; deterministic. A `dict` map from tool name → present-tense plain phrase, e.g.:

| tool | phrase |
|---|---|
| `run_audit` | running the capacity audit |
| `list_workspaces` | listing the workspaces |
| `user_activity` / `investigate_user` / `user_timeline` / `user_spike_history` | looking into that user's activity |
| `investigate_capacity_spike` / `spike_events` | checking the most expensive events |
| `raw_events` | pulling the raw event stream |
| `capacity_patterns` / `capacity_diagnostics` | analyzing capacity patterns |
| `describe_source` / `sample_events` | checking what the data source contains |
| `diagnose` | working through the diagnosis |
| `analyze_dax` | reviewing the DAX |
| `whats_changed` | comparing against the last run |
| `run_kql` | running a read-only query |
| `query_library` | checking the grounded query library |

Rules: output contains **no tool name and no JSON**. Unknown/unmapped tool → the generic phrase
"working on it…". Scope hint (decided, not optional): append a human hint ONLY for this whitelist of
keys, in this form — `user` → " for <user>", `item` → " for <item>", `topN` → " (top <N>)",
`days` → " (last <N>d)"; any other key is ignored. Never render a raw value that contains `{`/`}` or a
newline (guard against odd inputs). The leading progress glyph (`🔎`) is retained (cosmetic).

## Testing (TDD, offline, deterministic)

- **Prompt content:** `build_system_prompt()` contains the Presentation & Voice markers (voice line +
  each of the 6 fixes' intent); the pre-existing hard-rule markers (read-only, monitored-CU proxy,
  injection defense, timestamps-verbatim) are still present (guard against accidental deletion).
- **Parity:** the inlined `_SYSTEM` equals the canonical one — `TestInlinedLoopParity` stays green
  (extend it if it compares substrings).
- **`_progress_text`:** every known tool → its plain phrase; output never contains the raw tool name or
  a `{`/`}` (no JSON); unmapped tool → the generic phrase; a scope hint (topN/user) renders as human
  text, not JSON. Table-driven over all 18 tool names.
- **No honesty regression:** a focused test asserting the prompt still instructs monitored-CU-proxy
  disclosure + mock labeling + truncation disclosure (so "one caveat once" can't be misread as "drop
  the caveat").
- Full suite stays green.

## Deploy

Both apps: the **agent app** (the conversational surface — where the prompt + progress actually run)
and the **MCP app** (because `system_prompt.py` ships inside the `fabric_audit_agent` package — bump
the `# code version:` marker + pyproject version in lockstep, per the 3-B lesson, or the redeploy
serves stale code). Live-verify: a narrow question returns a narrow answer with **no tool names**, the
agent takes the obvious next read-only step instead of a menu, and progress lines read in plain English.

## Explicitly NOT pursued — with reasons

- **Any tool behavior / schema / data-path change** — this is presentation-only; changing tools is a
  different item and would risk the honesty invariants.
- **New capabilities / new tools** — out of scope; Phase 7+.
- **Emoji-heavy or chatty persona** — the chosen voice is concise-professional; over-styling erodes the
  trust the auditor role needs.
- **Per-user / adaptive personalization** — no user model exists and it's a privacy surface; not now.
- **Dropping or softening any caveat to read cleaner** — explicitly forbidden by the honesty guardrail
  above; "concise" never means "less honest."
