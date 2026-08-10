# RESUME HERE — pre-ship audit loop (updated 2026-08-10, end of round 3)

Read this first, then `PRESHIP-AUDIT-LEDGER.md` for the full finding history.

## The standing instruction

Keep running full deep audit sweeps, fixing everything found, and re-sweeping — **until THREE
CONSECUTIVE sweeps find ZERO problems.** A sweep with any finding does not count.

The bug class that matters most, in the user's words: errors "that could be missed because they
work but dont work the way intended and logically what i want." Crashes are LESS important than
silent logical wrongness.

## Current state

- **Clean-sweep counter: 0 of 3.** Round 4 found ~10 more, including a shipping blocker (stale chat
  core) and a defect inside a fix written the same day. Rounds 1-3 found ~30/~30/~60. **The rate is
  not converging.**
- **Production: wheel `0.2.25`** on all four jobs AND on the MCP app, deployed and live-verified
  (tier2 `TERMINATED SUCCESS`, `preflight: ok`, `peakCuPct=86.2`, `windowCuSeconds=12983.6`, no
  Degraded line; daily digest `delivered=True`).
- Suite **2267 passing**. Repo `main` clean.

## Round 3's single most important finding

An empirical mutation audit ran 38 mutations against the full suite. **17 SURVIVED.** Every
survivor reverts a guard whose own comment documents a past production incident. The suite is a
regression-*description* corpus, not a regression-*detection* one: the comments record the bugs, the
tests do not re-check them. That fully explains rounds 0–2's defect rate.

Two new test files close the worst of it (`test_mutation_guards_state.py`,
`test_capacity_collector_deployed_shape.py`) — each mutation empirically verified killed. The
remaining survivors from that report's list are still open (see below).

## FIXED AND DEPLOYED in round 3

P0s: materiality's middle tier didn't exist in production (`ambiguous` == `report`, because the v1
reasoner hardcodes `report: True`) · a failed Teams card was lost forever, never retried ·
`outbound` defaulted `delivered` to True · tier2 exited 0 on a degraded run · `throttle.py` stage 2
concluded "throttling-confirmed" on a CONSTANT reference field · `_build_events_collector` failed
open, silencing five detectors invisibly · `capacity_metrics` reported `peakCuPct: 0` from
unparseable input.

P1s: `silent_failure` couldn't reach Teams · alerting silently OFF read as a healthy quiet run ·
egress size cap failed OPEN while asserting completeness · `audit_findings` write `except: pass` ·
`run_history` read failure read as an empty table · daily job never rendered its health line ·
`severity.py`/`playbooks.py` labelled proxy data "capacity CU" via a dead `== "cost"` test ·
`cpuMs` counted as a duration proxy when it IS `sum(CpuTimeMs)` · `if raw:` ignored a `0` env
override · dead-man switch response was `pass` · no job had `timeout_seconds` ·
**B2/B3 turned ON** (`TIER2_BASELINE_ENABLED` was "0"; precondition verified live: 1,440 baseline
user rows) · findings ticketed under families the notification center filters out (`lineage`,
`meta`, `capacity`) · `_FAMILY_MAP` mapped a sweep finding onto a tier2-OWNED checkType, so tier2
deactivated it within 5 minutes of creation.

## INCIDENT — a guard deletion was committed by accident

Commit `569135c` landed `sigs |= {...}` → `pass` (the signal-set high-water union) under an
unrelated message. Cause: `git add -A` while a mutation-testing subagent had M8c applied. Repaired
in the following commit and now guarded by a test. **Never `git add -A` while a subagent is
mutation-testing the same tree** — give it a worktree. The audit round whose purpose was finding
silently-wrong code was itself the vector.

## ROUND 4 — CLOSED (all deployed + live-verified on 0.2.25)

- **THE BLOCKER: the chat agent ran 6-day-old core.** The MCP app is the App's ONLY tool source
  (tools come over HTTP via `FABRIC_MCP_URL`; the App does not run `tools.py` in-process) and it
  pinned wheel `0.2.14` from 2026-08-04. So every answer a human got came from core code predating
  the alerts `_FIELDS` P0 fix, the multi-capacity fix and the throttle retirement, while the jobs ran
  current. Structural cause: jobs get the wheel as a bundle ARTIFACT (automatic); the app names ONE
  FILENAME (manual). Re-pinned to 0.2.25 + marker bumped; build log confirms
  `Successfully installed fabric-audit-agent-0.2.25 fabric-audit-mcp-1.9.18`, no import errors.
  **Whenever you deploy a job wheel, re-upload it to the Volume and bump the marker.**
- The capacity card now NAMES WHO (`_likely_drivers` -> all four capacity checks -> composite ->
  both card paths), labelled "monitored CPU-time, not billed CU" + proxy disclosure.
- Concentration has a minimum-activity floor (`min_window_cu`, 60 CU-s), CALIBRATED against a live
  busy window of ~12,980 CU-s and applied only when the window is measurable, so a missing cost
  column can never become silence.
- Overage-only incidents carry `peakCuPct`, so their peak-escalation axis works and surfaces show a
  utilisation figure.
- Informational rows SETTLE: the upsert now carries `presenceCount`, so an established pattern stops
  cycling pending/pending/informational every three sweeps.
- `_parse_ts` delegates to `timefmt.parse_iso_utc`. It hand-rolled `fromisoformat`, which on the
  **Python 3.10 job compute** rejects the SEVEN fractional digits real LA `TimeGenerated` values
  carry -- so the activity cross-reference silently no-op'd in production while passing on local 3.12
  and the App's 3.11.
- **ALL 38 mutation-audit survivors are now killed** (17 originally survived the full suite). The two
  batches live in `test_mutation_guards_state.py`, `test_capacity_collector_deployed_shape.py` and
  `test_mutation_guards_round4.py`.
- Docs corrected: the 30% alert advertised "User -> Item -> **Owner**". Owner is NOT shipped -- see
  the item below -- and README also called a CPU-time proxy "capacity CU".
- REFUTED, do not re-chase: the App's `requirements.txt` omitting `msal`/`azure-kusto-data` is
  harmless. The App does not execute `tools.py` in-process; the MCP app does and has those deps.
  Build/runtime logs show zero related import errors. `openpyxl` was genuinely missing from both and
  is now added to the MCP app.
- REFUTED: the `base <= 0` guard in the capacity collector is an EQUIVALENT MUTANT (`budget <= 0` two
  lines later is logically identical), so it is unkillable by construction. The code is right.

## KNOWN-OPEN, NOT YET FIXED (carry into round 5, ranked)

1. **The digest says "No significant issues found ✅" while a capacity incident is open and
   firing.** `daily_summary._EXCLUDE` drops the capacity family from BOTH `open_tickets` and
   `stale_open`, and the mitigating "Capacity context" line is itself gated on `has_issues`. A
   three-day incident produces one Teams card and two digests that deny it exists. **A false
   statement to a human — highest remaining item.**
2. **The card names NOBODY.** `_facts_for("capacity_incident")` never reads `facts["items"]` /
   `topUsers`, though the LA attribution collector populates them on every sweep. This is the P4
   product promise ("the card names who caused it"). With B2/B3 now ON, `correlatedUserSpikes` can
   populate — but only for users with a baseline.
3. **Concentration has no minimum-activity floor.** One overnight refresh in an idle 5-minute
   window is ~100% share → warn → report. The "30% alert" fires on a near-empty denominator by
   construction.
4. **DQ-3: zero-cost rows collapse the share denominator**, so `sharePct: 0` for everything and the
   headline concentration feature goes permanently quiet — and `_check_cross_source_blind_spot`
   can't see it because the items DO exist, just all zero-cost.
5. Overage-only incidents: `_check_overage` never copies `peakCuPct`, so `metric` is NULL forever
   and the peak-escalation axis is dead for them; the card shows raw ms (`84600000.0 ms`).
6. A draining overage emits ~5 near-identical cards (burndown halving axis has no absolute floor).
7. An ongoing incident spends most of its life in the app's tab labelled **"Resolved"**
   (`currentlyActive=False` on every absent tick for up to 55 min).
8. Remaining surviving mutants: `severity_of` concentration boundary (`>= 50`), the correlation
   window boundary (`<= window`), `_TRUE_CU_CHECKS` widening, `pending = {**_informational,...}`,
   the suppress-resets-streak branch.
9. `reasoner_claude` falls back to canned KB text on ANY error with no log and no counter — a
   permanently broken reasoner looks like a working one with generic prose.
10. `agent_server/loop_hooks.py` CORRECT007 guard fails **OPEN** (an exception means the query is
    judged not-improvised and is executed).
11. Silent Delta→local-JSON store downgrade (`except (ImportError, RuntimeError): pass`).
12. Daily digest silently undelivered (`delivered=False`, no log, no health record, exit 0).
13. `create_readings_store_delta` has no `_ensure_schema` (unlike the other three stores) — drift
    silences all three stateful gates including the blindness detector.
14. Blind capacity source reads as healthy: `_ok = peakCuPct is not None or len(items) > 0`.
15. `run_history` is the one allowlist store NOT covered by the `_FIELDS` invariant test (it uses a
    hand-written mapper) — the original P0 can recur there verbatim.
16. `requirements.txt` for the App omits `msal` / `azure-kusto-data` / `openpyxl`, which its live
    paths lazy-import. If real, the entire live-data half of the chat app is inert. **Verify with
    one command before shipping.**
17. The MCP app pins core wheel `0.2.14` while jobs run `0.2.22` — six versions behind, i.e. before
    the `_FIELDS` P0 fix. Decommission it or re-pin.
18. `resolve_blank_user` (the User → Item → **Owner** tier of the headline promise) has no
    production caller, and the default LA query filters out blank users anyway.
19. `minHistory` writer default 20 vs `config.baselineMinHistory` 5: users with 5–19 samples never
    get a row while the card says their baseline "isn't ready yet".
20. 16 legacy `capacity.user-concentration` rows sit in `audit_alerts` from a since-deleted
    detector, one under a tier2-owned checkType. **Needs a data cleanup — not done, it is a
    production write and wants explicit sign-off.** `scripts/cleanup_stale_alerts.sql` exists.

## Working practices that earned their keep

- **Never trust "Deployment complete!"** — always `databricks bundle run <job>` and read the output
  for `Degraded` / `WARN`. Several P0s were found only this way.
- **Query production, don't just read code.** Three instances of the invisible-ticket bug were found
  by `SELECT check_type FROM audit_alerts`, none by reading the source.
- **Never `git add -A` while a subagent is mutation-testing.** See the incident above.
- **Derive test fixtures from producers, not from memory.** A hand-written list of detector keys
  missed `capacity.*` entirely and let the gap survive a pass.
- Never deploy from a dirty tree. Bump `pyproject.toml` + `rm -rf build/ dist/` before every deploy.
- Run pytest from `fabric-audit-agent-app/`, not the repo root.
- Ad-hoc SQL: the Databricks SDK `w.statement_execution.execute_statement` with warehouse
  `7e04f1a894e8c9bb`, catalog `shakur-main`, schema `bi-fabrics-audit`, profile `fabric-test`.
  (`databricks api post /api/2.0/sql/statements` returns "Not Found" on this CLI version.)

## Standing recommendation to the user

**Do not ship on a clean-sweep count of 0 of 3.** Three independent audit rounds have each found
P0-class logic errors and the rate is not converging — that is evidence more exist. What is live now
is materially better than this morning: the alerting path no longer loses cards silently, the
materiality gate actually has three tiers, the "throttling confirmed" claim is no longer fabricated
from a constant, and B2/B3 are finally running. But item 1 above means the daily digest can still
tell a human "no significant issues" during a live incident, and that is a wrong answer, not a
missing feature.
