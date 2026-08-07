# Wiring Map — fail-open posture of the main paths

**Sub-plan 4 (`docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md`),
Parts 3+4.** Scope is deliberately bounded to the **known silent-failure sites** identified in the
design spec — not an exhaustive census of the ~107 `except Exception` blocks in the codebase. This
is a map of the main path (**collector → facts → detectors → delivery → notification-center**),
classifying each known failure point as:

- **SAFE** — fails loud (raises/propagates) or was already visible (returned in a result dict a
  caller inspects); no change needed.
- **DANGEROUS-now-surfaced** — used to be swallowed with a bare `print` nobody watches; now also
  recorded on an `automation.health.HealthReport` (see `automation/health.py`), which the daily
  digest renders as a banner (`render_health_line`) when `degraded`.
- **DANGEROUS-still-silent** — a known gap, explicitly left as future work (not in this task's
  bounded scope).

## Collector layer

| Site | Failure mode | Classification |
|---|---|---|
| `adapters/collector_merge.py::create_merged_collector._one` | One collector in a multi-source merge raises. Caught, logged via `logging.warning` (not a `print` — still easy to miss in prod), and folded into `merged["sourcesFailed"]` (a list of error strings). | **DANGEROUS-now-surfaced.** `sourcesFailed` already existed but had only one consumer (`investigation/evidence.py::build_coverage`, used inside a live chat). It now ALSO flows into `HealthReport.record_collector_failures()` from two call sites: `automation/tier2_check.py::run_tier2_check` (every 5-min check) and `job.py::run_daily_summary_job` (the digest's own 1d collect). |
| `adapters/collector_merge.py::create_merged_collector.collect` | **All** collectors failed (`results` empty) → raises `RuntimeError`. | **SAFE** — fails loud, the caller's own try/except handles it (see below), never silently returns an empty audit. |
| `automation/tier2_check.py::run_tier2_check` | `collector["collect"]()` raises entirely (not a merge — a single collector, or the merge's own `RuntimeError`). Caught, printed, and the silent-failure gate is fed. | **DANGEROUS-now-surfaced.** Now also calls `health.record_collector(None, False, ...)` before returning. |
| `job.py::run_daily_summary_job` (the digest's 1d collect) | Collector raises (e.g. Delta/REST unavailable). Caught, printed (`[daily] capacity collect skipped ...`), digest continues with `capacity={}`. | **DANGEROUS-now-surfaced.** Now also calls `health.record_collector("daily-1d", False, ...)`, which reaches the digest banner in the SAME run that lost the data. |
| `pipeline.py::run_audit` (`facts = collector["collect"]()`) | Not wrapped locally — propagates. | **SAFE** — `job.py::run_unified_job` wraps the whole `run_audit` call, records an error history row (`_append_error_record`), and re-raises. Fails loud at the job level; Databricks Job UI shows the failure. Not health-surfaced (redundant with the job's own failure state), left as-is. |

## Detectors

| Site | Failure mode | Classification |
|---|---|---|
| `pipeline.py::run_audit` → `detect_all(facts, config)` | No per-detector isolation — one detector raising kills the whole `run_audit` call. | **SAFE (fails loud)** but **architecturally fragile** — a single bad detector (e.g. a KeyError on an unexpected event shape) takes down the entire sweep rather than being skipped. Not a *silent* failure (it crashes visibly, caught by `run_unified_job`'s try/except above), so it is out of THIS task's bounded scope, but it's the natural next candidate for `HealthReport.record_detector()` per-detector isolation — **DANGEROUS-still-silent in the sense that a real per-detector failure surface doesn't exist yet**; `record_detector` is built and tested (`tests/test_health.py`) but has no live caller yet. Flagged as future work. |

## Delivery — Tier-2 (`automation/tier2_check.py`)

| Site | Failure mode | Classification |
|---|---|---|
| `_send` chat-write (`chat_writer` in `_new`/escalation/reopen paths) | Raises → caught, printed as `WARN`, `chat_id` stays `None` (deep-link degrades to a root auto-investigate link; the alert is NOT dropped). | **DANGEROUS-now-surfaced.** Now also `health.record_delivery("chat", False, ...)`. |
| `_write_ticket` (`ticket_writer`) | Raises → caught, printed. Ticket write is best-effort; the alert itself was already sent. | **DANGEROUS-now-surfaced.** Now also `health.record_delivery("ticket", ...)`. |
| `ack_store["reopen"]` (recurrence-after-resolve path) | Raises → caught, printed. The re-alert still proceeds even if the reopen call fails. | **DANGEROUS-now-surfaced.** Now also `health.record_issue(...)`. |
| `_record_reading` (readings store `append`/`recent`) | Raises → caught, printed, degrades to an empty history (the STATEFUL gates — sustained-band, rate-of-change, silent-failure — simply stop firing, with no signal that they've gone blind). | **DANGEROUS-now-surfaced.** Now also `health.record_issue(...)` — this is the one most worth having, since it silently disables a whole class of gates. |
| `run_tier2_check._deliver` (wraps the entire `process_alerts` call) | `process_alerts` itself raises (not a per-item failure inside it) → caught, returned as `{"error": ...}` in the result dict. | **DANGEROUS-still-silent** (partial) — the error string IS returned to the caller (`tier2_main` prints `delivered={...}`), so it's not invisible, but it is NOT fed into `HealthReport` (the `health` object passed in only accumulates outcomes from *inside* a successful `process_alerts` call). Left as future work — would need the `except` to also call `health.record_issue` before returning. |

## Delivery — sweep (`automation/sweep_delivery.py`)

| Site | Failure mode | Classification |
|---|---|---|
| `deliver_new_findings` chat-write | Raises → caught, printed. | **DANGEROUS-now-surfaced** — `deliver_new_findings` now accepts an optional `health` param and records the outcome. |
| `deliver_new_findings` ticket-write | Raises → caught, printed. | **DANGEROUS-now-surfaced** — same as above. |
| `job.py::_deliver_sweep_findings` | Wraps the ENTIRE sweep-delivery call (webhook sink build, `create_ticket_writer()`, `deliver_new_findings`) in one outer try/except, printed only (`[sweep] delivery failed: ...`). Does not pass a `health` object into `deliver_new_findings`. | **DANGEROUS-still-silent.** `deliver_new_findings` is ready to record into a `HealthReport` (tested), but `run_unified_job` (the sweep entry point) does not yet construct/thread one through — unlike the daily-digest and Tier-2 paths, the hourly/daily sweep has no persistent digest surface to render a banner on. Left as future work: either give the sweep its own printed health line (mirroring `tier2_main`/`job_main`), or thread a shared `HealthReport` end-to-end from `run_unified_job` through `_deliver_sweep_findings`. |

## Startup invariant

| Site | Failure mode | Classification |
|---|---|---|
| `resolve/catalog.py::assert_model_map_invariant` | Previously called ONLY from `tests/test_catalog.py` — a catalog/routing drift in production would never be caught until a lookup silently returned nothing. | **DANGEROUS-now-surfaced.** `job.py::_check_startup_invariant(health=None, *, catalog=None, known_names=None)` wraps it in try/except, prints a `[startup] WARN`, and (when a `HealthReport` is passed) records the issue instead of raising. Wired at the top of `job_main()`, `tier2_main()`, and inside `run_daily_summary_job()` (the last one threads the SAME `HealthReport` all the way to the digest banner — a drifted catalog is now visible in the digest, not just a job log). |

## Notification center / ticket rows

All of the ticket-write sites above (`_write_ticket` in `tier2_check.py`, the ticket-write block in
`sweep_delivery.py`) were already "failure-isolated" in the sense that a ticket-write failure never
drops the underlying alert (Part 7, tightening.md) — that invariant is untouched. What Part 4 adds
is visibility: those same failures now also increment `HealthReport`, so a *persistent* ticket-write
outage (e.g. Lakebase auth broken for a day) shows up as a degraded banner instead of being
discoverable only by reading job logs one run at a time.

## Not attempted (explicitly out of bounded scope)

- **The ~107 `except Exception` blocks generally.** This task targeted the KNOWN silent-failure
  sites named in the design spec (collector merge, chat/ticket writes, readings store, startup
  invariant). A full classification of every except-block in the codebase (webhook `URLError`
  handling — Part 16c, Lakebase auth precedence — Part 16, error-conflation in `describe_source` —
  Part 25e) is separately scoped work in the same Sub-plan 4 and is NOT covered here.
- **Per-detector isolation in `detect_all`** (see Detectors section above) — `HealthReport.record_detector`
  exists and is tested but has no live caller; wiring it would mean catching exceptions PER detector
  inside `detect_all` rather than letting one bad detector crash the whole audit, which is a
  behavior change beyond "add visibility" and was left for a follow-up.
- **Sweep-path (`run_unified_job`) health threading** — `deliver_new_findings` supports it; the job
  entry point does not yet construct/print/persist it (see Delivery — sweep, above).
