# Tightening Plan — Reduce CU-Blended Noise, Increase Real-Fact Alerting

Created: 2026-08-07
Status: Ready for Claude Code — read fully before starting, this consolidates every issue
found across the deep-dive audit sessions plus new empirical proof from a real production day.

---

## THE CORE PROBLEM, PROVEN EMPIRICALLY

A real notification-center screenshot from a single day (2026-08-06) shows this pattern:

| Time | Users flagged this hour | Share range |
|---|---|---|
| 11:02 AM | 4 users | 30.1% – 33.6% |
| 12:02 PM | 8 users | 30.2% – 71.3% |
| 12:31 PM | **REAL capacity event**: CU pressure 112.8%, throttle 0.5 min | — |
| 01:02 PM | 4 users | 30.4% – 42.8% |
| 02:02 PM | 4 users | 31.1% – 44.9% |
| 03:02 PM | 6 users | 30.1% – 59.0% |
| 08:02 PM | 1 user (Timothy Manton) | 31.0% |

This is not 30+ separate real problems. This is the SAME structural bug
(`detectors/user_concentration.py`'s `metric() = monitored_share × cap_pct / 100`) firing
once per hour, every hour capacity was under any elevated load, producing a fresh batch of
5-8 newly-"concentrated" users each time — clustered tightly around the real 12:31 PM capacity
event. When capacity pressure is genuinely elevated for several consecutive hours, EVERY
user active in each of those hours gets their ordinary activity inflated across the threshold,
one full new batch per hour. This is direct, real-data confirmation of the bug already
diagnosed from the Timothy Manton investigation — not a one-off, a systemic hourly flood.

**Meanwhile, when asked directly "what problems did we have today" in chat, the agent
correctly ignored all of this and produced genuinely useful output**: two specific slow MDX
queries (611s and 800s) from one user, a recurring poorly-optimized query SHAPE (nested
Hierarchize/CrossJoin pattern) appearing across multiple days from different users, and two
users correctly identified as unremarkable/normal activity. Zero mention of "% of capacity."
This is the quality bar every automated alert should be held to — not a lower bar for
interactive chat and a noisier bar for automation.

---

## STANDING RULE — applies to EVERY change in this entire file, no exceptions

This rule is not optional and is not scoped to any one Part below. It exists because this
session found direct proof of what happens without it: `sla.py` had the exact same blanket
SLA-language bug as `accountability.py` (Part 17a) — a completely separate file, never caught
when `accountability.py` was fixed because nobody checked whether the same PATTERN existed
elsewhere. Separately, `detectors/concentration.py` and `detectors/user_concentration.py` both
reimplemented their own inline threshold check instead of calling the shared
`concentration_gate()` — meaning a fix to the gate would silently not reach either detector.
Both are exactly the failure mode this rule exists to prevent.

**Before making any change:**
1. Identify every caller, importer, and consumer of the function/file/data-shape being
   changed — grep for the function name, grep for the import, and check every file that reads
   the specific dict keys or return shape being modified.
2. Identify every OTHER place in the codebase that implements similar logic to what's being
   fixed — ask explicitly: "does this same bug or pattern exist anywhere else?" (This is the
   check that would have caught `sla.py` when `accountability.py` was fixed.)
3. Identify every existing test that covers the function being changed AND every test that
   covers its callers — not just the function's own unit tests.

**After making any change:**
4. Re-run the full test suite, not just tests for the file touched.
5. Manually re-verify (read the code, don't just trust green tests) that every caller
   identified in step 1 still receives the shape/behavior it expects — a passing test suite
   does not guarantee this if the test itself didn't cover the real call site.
6. If the change was a bug fix for a specific pattern (a mislabeled metric, a bypassed gate, a
   missing exclusion, a silent failure), explicitly grep the rest of the codebase for the same
   pattern before considering the fix complete — do not assume a bug is isolated to the one
   file where it was found.
7. Report back specifically what was checked in steps 1-6, not just "tests pass" — name the
   callers reviewed, name any sibling patterns checked and their result (found nothing further
   / found and fixed X).

This applies to every fix in Parts 0 through 18 without exception, and to every future change
made to this codebase going forward — treat it as a permanent project rule, not a one-time
instruction for this batch of fixes.

---

## PART 0: Immediate noise stop (do this first, before the redesign)

The current per-user-per-hour ticketing model is unusable regardless of the formula fix —
even with a correct percentage, getting a fresh ticket for 5-8 people every single hour for
several consecutive hours is not something anyone can act on. Two changes needed immediately,
independent of the deeper redesign in Part 1:

- [ ] Add hysteresis/persistence to `detect_user_concentration()`'s findings the same way
      Tier-2 already has it for its own concentration checks — a user must appear elevated
      across multiple consecutive hourly sweeps before a ticket is created, not on the first
      hour it happens.
- [ ] Deduplicate by user, not by user+hour — if Timothy Manton is already an open ticket,
      a fresh hourly recurrence of his name must update the EXISTING ticket, never mint a new
      one. Confirm whether `incident_key()` for this finding type includes a timestamp/hour
      component that's causing fresh keys every hour — if so, this is the same class of bug
      already fixed for Tier-2's incident keys (dropping the exact user list/percentage from
      the key) and needs the identical fix applied here.

---

## PART 1: Redesign — replace capacity-percentage alerting with absolute, fact-based alerting

The chat investigation above reveals the actual right shape for automated alerting. Rebuild
`detect_user_concentration()`'s alerting criteria around what genuinely worked in that
transcript, not percentage-of-capacity estimates:

### 1a. Alert on individual operation duration/cost in absolute terms, not share-of-anything

A single query running 611 seconds or 800 seconds is worth flagging on its own — regardless
of what percentage of anyone's capacity or monitored pool it represents. Add an absolute
duration/CU-seconds threshold check (e.g. "any single operation exceeding N seconds or N
CU-seconds") as a genuinely new, separate detector or an extension of the existing one. This
requires zero capacity data at all — it's pure Log Analytics fact-checking, exactly matching
the "let Log Analytics answer WHO/WHAT/WHY on its own terms" direction already established.

### 1b. Alert on recurring query-SHAPE patterns, not recurring people

The chat's most valuable finding was structural: the same expensive MDX query shape
(nested Hierarchize/CrossJoin) recurring across multiple days from DIFFERENT users. This
points at a model/report design problem, not a person problem — exactly the kind of root
cause worth surfacing automatically. This depends on real query text retrieval (the B1 item
already scoped in an earlier plan — confirm its current status before building this, since it
may already be partially wired via the runtime-query-text half mentioned in a recent Claude
Code report).

### 1c. Keep the WHO/WHAT/WHY framing, drop the percentage framing, for anything user-level

When a user genuinely IS worth naming (their own operation crossed the absolute duration/cost
threshold from 1a), report it the way the chat did: what they ran, how long it took, what
shape the query has, whether it's a one-off or matches a known recurring pattern — never a
"% of capacity" number. If percentage-of-monitored-activity is still useful context, present
it as a plain ranking ("their heaviest hour today") never as an implied capacity share.

### 1d. Decide the fate of the current metric() formula

Given 1a-1c largely replace what user_concentration.py was trying to do, decide explicitly:
either (i) remove the capacity-blended concentration alert type entirely in favor of the
absolute-threshold + shape-recurrence detectors above, or (ii) keep a much narrower version
of it purely for genuine, sustained, hysteresis-gated concentration (matching Tier-2's
existing discipline) as a secondary signal, never as the primary "who had a problem today"
mechanism. Recommendation: (i) — the chat transcript proves the absolute/shape-based approach
is strictly better at answering the actual question users care about.

---

## PART 2: Carry forward from the prior deep-dive audit (already scoped, not yet built)

These were found in earlier passes this session and still need to land:

- [ ] FIX 0: `verdict.py`'s "optimize" path is structurally unreachable — no collector
      populates `facts["capacity"]["refreshes"]`, so `detect_capacity()`'s contention and
      oversized-model checks can never fire, meaning every confirmed throttle has always
      resolved to "size-up." (See prior prompt — full investigation and fix steps already
      written.)
- [ ] FIX 1: Remove the CU-blended estimate from `user_concentration.py`'s `metric()` —
      superseded/absorbed by Part 1's redesign above, but the specific multiplication bug
      must be gone regardless of which alerting shape replaces it.
- [ ] FIX 2: Route `detect_concentration()` and `detect_user_concentration()` through the
      shared `concentration_gate()` in `gates.py` instead of each reimplementing its own
      inline threshold check.
- [ ] FIX 3: Exclude capacity findings (throttle/pressure/overage) from
      `accountability.py`'s "no resolution" / SLA-style language — they auto-resolve by
      design and should never be told they lack resolution.

---

## PART 3: Wiring integrity audit (methodology + one-time run)

- [ ] Build `docs/WIRING-MAP.md` — for every detector: what it reads, what actually
      populates that field, what gate/materiality it passes through, what surfaces it can
      reach, and an honest LIVE / PARTIAL / DEAD status. Include FIX 0 above as a documented
      example of the exact failure class this map exists to catch.
- [ ] Grep every `except Exception` block codebase-wide; classify each as
      FAIL-OPEN-SAFE or FAIL-OPEN-DANGEROUS; fix every DANGEROUS one per Part 4.
- [ ] Write end-to-end tests (real pipeline, not isolated unit calls) for all 10 sweep
      detectors confirming a finding actually reaches the final envelope.

---

## PART 4: Permanent unhealthy-state visibility

- [ ] Extend the Tier-2 heartbeat into a genuine pipeline health report: per-detector
      live-vs-gated status, per-delivery-path recent success/failure, whether
      `audit_findings` received a write last sweep.
- [ ] Promote every FAIL-OPEN-DANGEROUS pattern found in Part 3 to increment a counter/flag
      in this health report — never just a print statement.
- [ ] Add a "system health" section to the daily digest, separate from capacity findings,
      surfacing any detector with zero live data, any delivery path with a recent failure
      streak, and any known gated/dead capability.

---

## PART 5: CU/CPU exposure in chat and cards

- [ ] System prompt: CU answers exactly one question (is the capacity in trouble —
      throttle/pressure/overage/verdict). Log Analytics/monitored activity answers WHO/WHAT/WHY
      on its own honest terms, never blended into a capacity percentage.
- [ ] Extend the card builder (`delivery_webhook.py`'s `build_card`, or wherever cards are
      assembled) so any card involving attribution shows a distinct capacity-context fact
      alongside the attribution finding when capacity data is available for that window — e.g.
      "Capacity during this window: {peakCuPct}% (no throttle)" as its own fact, never computed
      FROM the attribution number.
- [ ] Extend the hover-detail card (`notification-center.tsx`) the same way — show capacity
      status and monitored activity as two separate facts on any ticket where both exist, never
      merged into one figure.
- [ ] Reframe existing proxy-caveat language: lead with what Log Analytics data IS (real,
      measured telemetry, genuinely useful for who/what/why), not what it's NOT — don't open
      every mention of monitored activity with an apology.

---

## PART 6: Investigation strategy — pivot, don't just expand, when the anchored window is empty

Found during the Timothy Manton investigation: the deep-link's pre-seeded prompt anchors to the
finding's detection timestamp and says to investigate ±30 min around it. This works when the
named user's activity genuinely clusters near that time. It does NOT work when the user has no
real connection to that specific window at all — widening the same window doesn't help if
they simply weren't active anywhere near it.

- [ ] Update the investigation prompt (wherever `_investigate_query` or equivalent builds the
      auto-seeded prompt) to include an explicit fallback strategy: if the anchored ±30 min
      window does NOT corroborate the named user (they don't appear among top actors, or their
      activity there is trivial), the next step must PIVOT — stop expanding the same window, and
      instead search the named user's own activity broadly (e.g. last 7-30 days) to find when
      THEY were actually most active or anomalous, then investigate that time instead. This is a
      distinct fallback strategy, not a wider version of the same search.
- [ ] Add this as a durable system prompt rule too, not just the one-off deep-link text, so it
      holds for freely-typed investigation questions as well.
- [ ] Regression test: construct a case where the named user has zero activity near the anchor
      time but real activity elsewhere in their history; confirm the investigation correctly
      pivots and finds it rather than concluding "nothing found" after only checking the anchor.

---

## PART 7: Notification center shows 0 tickets while the daily digest correctly shows real counts

Root-caused directly in code: `sweep_delivery.py`'s `deliver_new_findings()` always writes the
raw ticket row to `audit_alerts` via `alerts_store["upsert"]()` — this is why the daily digest's
count is always correct. But `ticket_writer()` — the only thing that makes a finding visible in
`/api/alerts` and therefore the notification center — only runs `if ticket_writer and chat_id`.
If `chat_writer()` throws for any reason, `chat_id` silently stays `None`, the exception is
caught and only printed to a log nobody watches, and `ticket_writer()` is skipped entirely —
for every single finding, silently, indefinitely.

- [ ] Confirm the actual failure by reading real job run logs for the line
      `"[sweep] alert chat write failed (...)"` — do not guess further, the exact exception type
      and message settles this immediately.
- [ ] Also check Tier 2's equivalent chat-write path — a card's "Investigate in chat" button
      degrades gracefully to the app root URL when `chat_id` is `None`, so a normal-looking
      button in a screenshot does NOT prove chat creation succeeded. Verify directly against the
      Lakebase chats table, don't infer from the UI.
- [ ] Fix whatever the log reveals — likely candidates: `FABRIC_LAKEBASE_USER` is hardcoded to a
      human user's email rather than the job's actual execution identity, which would break
      Postgres auth consistently for every automated write.
- [ ] Promote this failure out of a silent print statement into the Part 4 health report — a
      sustained chat-write failure must be visible the same day it starts, not discovered weeks
      later by comparing two UI surfaces by hand.

---

## PART 8: Daily Summary's "Review & acknowledge" link 404s

Root-caused directly: the frontend router (`App.tsx`) has exactly two routes — `/` and
`/chat/:id`. There is no `/alerts` route and never has been; the notification center is a
floating widget mounted globally, not a page. `daily_summary.py` builds
`ack_url = f"{app_url}/alerts"`, pointing at a page that doesn't exist.

- [ ] Point the Daily Summary's action at the app's root URL (`/`) instead — the notification
      center widget is already mounted globally and visible from there. Simplest correct fix.
- [ ] If a genuine full-page `/alerts` view is wanted instead of just fixing the link, scope
      that as its own separate feature — don't bundle it into this fix.

---

## PART 9: Broadening — deeper investigative capability (carried forward, not yet built)

- [ ] **B2 — Lineage awareness**: before recommending a fix to a semantic model, check how many
      downstream reports/dashboards depend on it via Fabric's lineage API. Confirm what's
      actually accessible given current permissions before designing — don't assume the data is
      reachable.
- [ ] **B4 — Cross-workspace pattern matching**: if the same anti-pattern or expensive-query
      shape appears across multiple different workspaces, that's a training/governance signal,
      not five isolated bugs. Most speculative item on this list — scope feasibility first: if
      the honest answer is "not worth building yet," say so plainly rather than building a weak
      version.

---

## PART 10: Tightening — keep output focused as depth grows (the section this file is named for)

- [ ] **T1**: extend the existing step-budget classifier so a simple question never triggers an
      expensive deep-dig tool (model-structure scan, lineage pull, cross-workspace comparison)
      just because those tools exist.
- [ ] **T2**: design a structured response template — short headline answer, supporting
      evidence, then an optional deeper section (raw query text, full recurrence history) that's
      present but not forced into every response by default.
- [ ] **T3**: add an explicit pre-send trim rule to the system prompt — before finalizing any
      response, drop tangential content, drop a caveat already stated once in the same response,
      never leak raw tool JSON into a user-facing reply.
- [ ] **T4**: never restate the proxy/monitored-activity caveat more than once per response; if
      multiple findings need the same disclosure, state it once, not per-finding.

---

## PART 11: UI quality (carried forward, not yet built)

- [ ] **U1**: structured investigation card layout — distinct Finding / Evidence / Root Cause /
      Fix sections, replacing the single markdown block for investigation-type chat responses.
      Natural home for T2's collapsible deep-evidence tier.
- [ ] **U2**: confidence level (validated / likely / inconclusive) as an actual colored badge
      component, not plain text.
- [ ] **U3**: a small, consistent icon distinguishing true-CU figures from monitored-activity
      figures inline, next to the number itself — reduces the repetition T3/T4 are meant to cut
      while keeping the distinction visually unmissable.
- [ ] **U4**: a "show me the query"/"show me the model" action rendering retrieved DAX/model
      metadata in an actual code viewer. Explicitly depends on B1 (real query text retrieval)
      landing first — don't build against a placeholder.

---

## PART 12: The BAD Activity Taxonomy — researched, comprehensive, the foundation for Parts 1 and 13

Researched against Microsoft's own documentation and current (2026) community-reported failure
patterns, cross-referenced against what's already partially built in refresh.py, model.py, and
dax.py. This is the canonical list of what counts as a genuinely reportable problem. If an
investigation (triggered by a CU wake-up) does not match one of these categories with real
evidence, IT DOES NOT ALERT — silence is the correct default, not a percentage-based fallback.

### Category 1 — Refresh failures (ALWAYS reported separately, never blended with interactive
activity findings — see Part 14)
- Credential/auth failure (expired password, disabled service account, expired OAuth token —
  confirmed as the fastest-growing 2026 cause specifically for Fabric/Lakehouse-connected
  sources, not just traditional gateway credentials)
- Gateway offline, outdated, or overloaded — detectable as failures clustering by time of day
- Source timeout ("Execution Timeout Expired", numeric error code -2147467259)
- Refresh concurrency limit exceeded ("You've exceeded the capacity limit for dataset
  refreshes" — shows a retry-with-backoff pattern before final failure)
- Constraint violation / duplicate-key error at the source
- SILENT FAILURE: refresh reports success but rows/bytes processed = 0 — nothing was actually
  retrieved; easy to miss since the refresh technically "succeeded"
- Calculated-column/measure referencing a missing table or field — surfaces as a refresh
  WARNING (since March 2026 Microsoft change), not a hard failure; easy to miss for the same
  reason
- Chronic recurrence of the SAME error across multiple days (already partially built in
  refresh.py's chronic flag — confirm it's wired to real data, per the earlier B3 access-gap
  finding)

### Category 2 — Query performance anti-patterns (model/report design problems, not people
problems)
- Nested iterator functions (SUMX/COUNTX/AVERAGEX wrapping another iterator or a full-table
  FILTER) — already partially covered in dax.py, confirm coverage is current
- CALCULATE(SUM(...), FILTER(FactTable, ...)) filtering a whole fact table instead of a
  dimension table — a specific, well-documented, high-impact anti-pattern not yet confirmed
  covered in dax.py
- Bidirectional relationships forcing unnecessary cross-filtering — already covered in
  model.py (confirm live once the Scanner API tenant-setting gap clears)
- High storage-engine query count (50+) on a single query execution — indicates row-by-row
  formula-engine iteration; requires query-plan-level data, may not be available from EventText
  alone — scope feasibility before committing to build
- Repeated, unbounded MDX cross-joins (the exact "GrandTotal Hierarchize/CrossJoin" shape found
  in the real Ent-Reporting-Sales investigation this session) — NEW category, not yet in the
  codebase anywhere, and the single strongest real-world finding from this session's chat
  transcript. Prioritize this one.
- Auto Date/Time left enabled — already covered in model.py

### Category 3 — XMLA / connection-level errors
- "XML for Analysis request timed out" with its specific numeric error codes (greppable in
  EventText)
- Bad Request on a large TMSL/XMLA command
- Authentication/token failures specific to XMLA endpoint access
- EXPLICITLY NOT BAD: "session moved to another node" messages — informational only, must
  never be surfaced as a problem (confirmed via research: this is a normal Premium-cluster
  rebalancing event, not an error)

### Category 4 — Operational/behavioral patterns (patterns across time, not single events)
- The same expensive query SHAPE recurring across multiple days from DIFFERENT users — a
  report design flaw, not a person problem (the second-most-valuable real finding from this
  session's chat transcript)
- A cluster of multiple long-running queries (e.g. >5 min) against the same item within a short
  window — points at the item's design, not any one user
- A user's single operation duration significantly exceeding THEIR OWN historical baseline — a
  real per-user anomaly signal, computed from their own history, never from a capacity-blended
  estimate

### Explicitly NOT bad — must never be reported as a problem
- Normal multi-visual dashboard rendering (several small queries in a tight cluster) —
  confirmed benign in this session's real chat investigation, must not be flagged
- A single unremarkable query, regardless of what percentage of anything it happens to compute
  to under the old (now-removed) capacity-blended formula
- "Session moved to another node" XMLA messages (see Category 3)

---

## PART 13: Daily Summary redesign — bad things, not CU

The current daily digest (`daily_summary.py`) leads with CU/throttle/pressure metrics. Redesign
it to lead with the Part 12 taxonomy instead:

- [ ] Remove CU/throttle/pressure as the headline content. CU-derived facts may still appear
      as brief context (e.g. "1 confirmed throttle today, see capacity alerts" as a one-line
      cross-reference) but the digest's main body is the day's BAD findings from Log Analytics,
      categorized per Part 12.
- [ ] Add a "Top 10 users of the day" section — ranked by real monitored activity (CU-seconds
      or operation count, whichever is more stable), presented as a plain ranking, never as an
      implied capacity percentage.
- [ ] Refreshes get their OWN clearly separated section (see Part 14) — never interleaved with
      interactive-activity findings in the same list.
- [ ] Recurring query-shape problems (Category 2/4) get their own subsection, distinct from
      one-off slow queries — the recurring ones are the higher-value finding (points at a design
      fix, not a one-time event) and should be visually distinguished, not buried in a flat list.
- [ ] If NOTHING in Category 1-4 was found today: say so plainly ("No significant issues found
      in today's activity") rather than falling back to CU metrics to fill the space — an empty
      taxonomy result is a genuinely good, reportable outcome, not an information gap to patch
      with CU numbers.

---

## PART 14: Refreshes must be reported as their own separate category, always

Explicit requirement, applies to BOTH the daily summary and interactive chat:

- [ ] In the daily summary: refreshes are their own section, never blended into interactive
      query findings, even when both involve the same item or the same time window.
- [ ] In chat: if a user asks about heavy activity on an item/workspace and the actual answer
      includes both interactive queries AND scheduled refreshes, the agent must report both but
      keep them visibly separate — e.g. "Interactive activity: ... / Scheduled refreshes
      (separate from the above): ..." — never merge a refresh's cost into the same ranking or
      total as interactive query activity.
- [ ] Add this as an explicit system prompt rule: "Refreshes and interactive queries are
      different categories of activity and must never be combined into one total, one ranking,
      or one finding. Always name which category a given piece of activity belongs to."
- [ ] Regression test: a workspace with both heavy refresh activity and heavy interactive
      activity in the same window must produce a response where the two are clearly labeled and
      never summed into a single combined figure.

---

## PART 15: Card timestamp display + universal auto-ticketing until resolved

Two explicit requirements that must apply across every current and future finding type,
including everything new from Parts 1/12/13's redesign — neither is fully stated elsewhere in
this file as a blanket rule, so stating them here explicitly to make sure the redesign work
doesn't accidentally miss them.

### 15a. Every alert card must display WHEN it happened, not just anchor an investigation to it

Part 6 already uses the detection timestamp to anchor the auto-seeded investigation prompt.
That is not the same as showing it to the user. The Timothy Manton card (and every current
card) shows Severity / Where / Finding but never a timestamp — someone looking at the card has
no idea if this happened five minutes ago or five hours ago.

- [ ] Add a human-readable "When" fact to every card (`build_card` in `delivery_webhook.py`,
      or wherever card facts are assembled) — e.g. "2026-08-07 14:32 UTC (10:32 AM EDT)",
      matching the timestamp formatting convention already used elsewhere in chat responses.
- [ ] For finding types that represent a rolling-window detection rather than one precise
      instant (e.g. concentration-style findings, once redesigned per Part 1), label it
      "First noticed" rather than "Detected" — same distinction already established for
      point-in-time vs. rolling-window findings.
- [ ] Apply this to the hover-detail card in `notification-center.tsx` too — the "Since" field
      already exists there for some cases; confirm it's populated for every finding type, not
      just some.

### 15b. Every finding, current and future, automatically becomes a ticket and stays open until
a human explicitly resolves it

This already works correctly for the finding types wired into the existing pipeline (once Part
7's silent chat-write failure is fixed). State it here as a blanket, permanent rule so it is not
accidentally missed for the NEW finding types Part 1/12/13 introduce (absolute-duration alerts,
recurring query-shape alerts, refresh-failure-category alerts):

- [ ] Every finding that qualifies as reportable under Part 12's taxonomy must automatically
      write a ticket via the existing `audit_alerts` + `ticket_writer` pipeline — no new finding
      type gets built as "chat-answerable only" without also being wired into automatic
      ticket creation. This is a wiring requirement to check explicitly for every new detector
      built under Parts 1/12/13, not just the finding types that already exist today.
- [ ] A ticket stays open (visible in the notification center, included in `query_active()`)
      until a human explicitly resolves it — whether they get there by clicking "Investigate in
      chat" on the Teams card and then resolving from the hover-detail card, or by resolving
      directly from the notification center's list view without opening detail first. Both paths
      call the same underlying resolve action; confirm both remain wired correctly for every
      finding type covered by this redesign.
- [ ] Capacity-level findings (throttle/pressure/overage) remain the one exception, per the
      existing, deliberate design: they auto-resolve when the physical state clears, since they
      don't need a human to confirm anything changed. Every OTHER finding type — including all
      new ones from Parts 1/12/13 — follows the human-resolve-only rule.
- [ ] Regression test: construct one example from each NEW finding type introduced in Parts
      1/12/13 (an absolute-duration alert, a recurring-shape alert, a refresh-failure alert) and
      confirm each one automatically creates a ticket, appears in the notification center, and
      remains open until a resolve action is explicitly taken — not just that the finding logic
      itself fires correctly in isolation.

---

## PART 16: Manual code trace findings — Lakebase auth, connection retry, webhook error handling

Found by reading `adapters/chat_store_lakebase.py` and `adapters/delivery_webhook.py` directly,
line by line, rather than inferring behavior from call sites.

### 16a. CONFIRMED (not just "likely") root cause of Part 7's silent chat-write failure

`_lakebase_conn()` in `chat_store_lakebase.py`:
```python
user = os.environ.get("FABRIC_LAKEBASE_USER") or os.environ.get("DATABRICKS_CLIENT_ID")
...
cred = client.postgres.generate_database_credential(_endpoint_path())
token = getattr(cred, "token", None) or cred["token"]
...
return connect(host=host, port=5432, dbname=db, user=user, password=token, sslmode="require")
```
Postgres token auth requires the connecting `user` to match the identity that generated the
token. `generate_database_credential()` mints a token for whatever identity is actually running
the code (the job's own execution identity). But `databricks.yml` hardcodes
`FABRIC_LAKEBASE_USER` to a literal human email on all three jobs, and the `or` operator means
that value always wins over the `DATABRICKS_CLIENT_ID` fallback the code already anticipates.
Unless the job happens to run as that exact human identity, every single Lakebase connection
attempt from the automated jobs is a structural identity mismatch — not a transient failure,
a guaranteed one, every time.

- [ ] Remove `FABRIC_LAKEBASE_USER` from the three job definitions in `databricks.yml`, or stop
      giving it precedence in `_lakebase_conn()` — let `DATABRICKS_CLIENT_ID` (the job's actual
      running identity) be the connecting user for automated contexts. Confirm the correct
      Postgres role/grant exists for that identity before flipping this — this may need a
      one-time grant on the Lakebase instance for the job's client ID, separate from the code
      change.
- [ ] If a human user genuinely does need a separate connection identity for a different code
      path (e.g. the chat app's own manual-ticket-creation flow, which may legitimately run as
      an interactive user's identity via the app's own auth), keep that path distinct rather
      than sharing one function with an env-var override that silently wins in both contexts.
- [ ] After fixing: run a real sweep and confirm via a direct Lakebase query that a chat row
      actually gets created — this closes Part 7 definitively rather than leaving it as
      "confirm via logs."

### 16b. `create_ticket_writer()`'s reused connection has no retry/reconnect logic

The writer lazily creates one connection and reuses it for the whole run ("the tier2 wheel task
exits after one run" — a reasonable design for a short-lived process). But if that connection
fails or drops mid-run for any reason, `state["conn"]` stays set to the broken object, and every
subsequent `write()` call in that same run silently fails for the rest of the sweep — one
transient blip early on effectively disables ticket-writing for everything that follows it in
that run.

- [ ] Add a basic reconnect-on-failure: if a `write()` call raises a connection-level error,
      clear `state["conn"]` and retry once with a fresh connection before giving up and letting
      the existing failure-isolation (caller-side try/except) handle it.
- [ ] Note for later: if this writer is ever reused by the chat app's manual-ticket flow (a
      long-running process, not a short-lived wheel task), the "one connection for the whole
      run" design would need revisiting — a long-running process holding one Postgres connection
      open indefinitely is a different risk profile than a job that exits after one run.

### 16c. Webhook delivery only catches HTTP-response errors, not connection-level failures

`create_webhook_sink()`'s `_post()`:
```python
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.status)
except urllib.error.HTTPError as e:
    return int(e.code)
```
`HTTPError` only covers a bad HTTP response (4xx/5xx). `URLError` (its parent class) covers
connection-level failures — DNS resolution failure, connection refused, or the 30-second socket
timeout firing. None of those are caught here, so a genuine network outage while delivering a
Teams alert propagates as an unhandled exception rather than a graceful `delivered: False`.

- [ ] Broaden the except clause to also catch `urllib.error.URLError`, returning a sentinel
      status (e.g. `0`) to distinguish "never got an HTTP response at all" from a real HTTP
      error code, and confirm the caller (`dispatch_outbound` in `outbound.py`) handles a `0`
      status the same safe way it handles a 4xx/5xx.
- [ ] Regression test: simulate a connection failure (not an HTTP error response) in the poster
      injection point and confirm `deliver()` returns `{"delivered": False, ...}` rather than
      raising.

---

## PART 17: Manual line-by-line trace, continued — sla.py, egress bypass, dead code candidates

From reading every file `pipeline.py` actually imports and runs on every sweep, plus
`egress.py`'s own self-documented gaps, followed to their source.

### 17a. `sla.py` has the SAME blanket SLA-language bug as `accountability.py` — NOT covered by
the existing FIX 3

`assess_sla()` applies breach tracking to every finding uniformly — a Critical throttle finding
open 2 days gets `breached: True` against a 1-day target (`_SLA_DAYS = {"Critical": 1, ...}`),
with zero exclusion for capacity findings designed to auto-resolve without human action. This is
a separate file and mechanism from `accountability.py` — fixing FIX 3 does not fix this.

- [ ] Add the same check-type exclusion used in FIX 3 to `assess_sla()` —
      throttle/pressure/overage findings never get an SLA breach assessment, regardless of age.
- [ ] Regression test: a Critical throttle finding open 5+ days does not get `sla.breached =
      True`; a genuine attribution finding open past its target still does.

### 17b. `adapters/ticketing.py` and `conversation.py` bypass the egress/redaction chokepoint

Confirmed directly in code, exactly as `egress.py`'s own docstring already documented as an
open gap: `ticketing.py`'s `open_()` calls `client.create_issue(build_ticket(f))` on the raw
finding with no call to `apply_egress_controls()`. `conversation.py`'s
`build_concentration_alert()` builds its card straight from the raw finding/evidence dict, same
gap. If a finding's `what` text or evidence ever contained something secret-shaped (a query
string embedding a credential, for instance), either path would emit it completely unredacted
to an external system (Jira/ADO/ServiceNow, or a Teams channel).

- [ ] Confirm via grep whether `create_ticketing_delivery` or `build_concentration_alert` are
      imported/called ANYWHERE in the live pipeline (`job.py`, `tier2_check.py`,
      `sweep_delivery.py`, `daily_summary.py`, or anywhere in `agent_server/`). Not found in any
      of the files read this session so far — confirm this holds across the full codebase.
- [ ] If genuinely dead/unwired: either delete both files, or — if there's a reason to keep
      them for future use — fix the egress gap now, before either is ever wired in, so a future
      session doesn't revive this code without noticing the missing safety gate.
- [ ] `conversation.py`'s docstring describes a two-way Bot Framework Teams conversation
      requiring infrastructure that was never built (an Azure Bot Service/Function fronting a
      Databricks App, since a Databricks App cannot itself be a Bot Framework messaging
      endpoint). This looks like an abandoned earlier design, superseded by the one-way webhook
      approach actually deployed today (`delivery_webhook.py` + Power Automate). Confirm this
      reading is correct and document the decision (keep as future-phase design reference, or
      remove) rather than leaving it ambiguous.

### 17c. Confirmed clean this pass (no issues found)

`dedupe.py`, `escalate.py`, `trend.py`, `digest.py`, `forecast.py`, `anomaly.py`, and
`egress.py`'s own redaction/sensitivity-floor logic — all read in full, all correct.

---

## PART 18: Remaining full-codebase trace — handoff for Claude Code

This session's manual trace covered every file `pipeline.py` imports and runs on every sweep
(the highest-value, most-connected code), plus the files directly implicated by bugs already
found. That is genuine, real coverage — but it is not the whole codebase. Being honest about
scope: this package has 100+ files. The following have NOT yet been read line-by-line this
session and need the same treatment:

- **`investigation/`** (15 files: `diagnose.py`, `baseline.py`, `events.py`, `evidence.py`,
  `expensive.py`, `forecast_throttle.py`, `overloads.py`, `patterns.py`, `playbooks.py`,
  `sku.py`, `spike_history.py`, `throttle.py`, `timepoint_peaks.py`, `watch.py`, `workload.py`)
  — `diagnose.py` especially, since it's the root-cause narrative chain and has not been read
  this session at all.
- **`detectors/`** remaining: `blast_radius.py`, `cost.py`, `report.py`, `security.py`,
  `cross_workspace.py`, `pipeline.py` (the detector, not the top-level one) — only
  `concentration.py`, `user_concentration.py`, `capacity.py`, `refresh.py`, `model.py` have
  been read this session.
- **`query/`** (9 files: `dax_guard.py`, `sql_guard.py` already spot-checked earlier in this
  project but not this session, `kql_guard.py`, `envelope.py`, `firewall.py`, `mine.py`,
  `redact.py`, `target_classifier.py`, `windows.py`, `deeplinks.py`) — these are the safety
  layer for the NL-to-query feature, high-value to re-verify.
- **`kb/`** (9 files) — the grounding/metric-definition knowledge base, confirmed earlier this
  project as disconnected from `__init__.py`'s exports; worth re-checking if that's since
  been fixed.
- **Remaining top-level files**: `finding.py`, `confidence.py`, `health_score.py`,
  `roadmap.py`, `audience.py`, `narrative.py`, `run_log.py`, `lifecycle.py`, `outcomes.py`,
  `correlate.py`, `stagger.py`, `routing.py`, `validate.py`, `identity.py`, `sources.py`,
  `attribution.py`, `severity.py`, `staleness.py`, `ticket.py`, `triggers.py`, `whatif.py`,
  `diagnosis.py`, `sanitize.py`, `connectivity.py`, `scopes.json`, `entrypoints.py`,
  `watch_run.py`, `cli.py`, `key_utils.py`, `timefmt.py`, `report_md.py`, `reasoner_stub.py`
  — all still unread this session.
- **`tools.py`** — the MCP tool handler file, likely large, needs its own dedicated pass given
  it's the single most tool-call-connected file in the package.
- **`adapters/`** remaining: `attribution_rollup.py`, `clients.py`, `collector_activity.py`,
  `collector_activity_events.py`, `collector_csv.py`, `collector_events_la.py`,
  `collector_list_usages.py`, `collector_log_analytics.py`, `collector_mock.py`,
  `collector_refresh.py`, `collector_rest.py`, `collector_scanner.py`,
  `collector_security.py`, `collector_workspace_monitoring.py`, `delivery_file.py`,
  `lifecycle_store.py`, `reasoner_claude.py`, `reasoner_investigation.py`, `store_local.py`
  (re-verify, was read earlier this project not this session).
- **`agent_server/`** (the chat app's brain — system prompt, tool loop, investigator) — not
  touched by this pass at all.
- **The frontend** (`e2e-chatbot-app-next/client/src/`) — only `App.tsx` and
  `notification-center.tsx` have been read; every other component, hook, and page has not.

### Instructions for completing this trace

- [ ] Read every file listed above, in full, in the order listed (investigation/ and the
      remaining detectors first — these are the next-highest-value after what's already been
      covered).
- [ ] For each file: confirm what it's supposed to do from its own docstring, then verify the
      code actually does that — the exact discipline that found the `sla.py` bug (symmetric to
      an already-known bug in a sibling file) and the `ticketing.py`/`conversation.py` egress
      bypass (already self-documented in a DIFFERENT file's comments, just never followed up on).
- [ ] For every function that touches an external system (network call, database write, file
      write) or handles a security-sensitive concern (redaction, auth, secrets): confirm it's
      actually reachable from a real entry point, and confirm error handling doesn't silently
      swallow a failure the way `chat_writer`'s did.
- [ ] Record every new finding in this file, following the same format as Parts 16 and 17 —
      what's wrong, why, and a concrete fix with regression tests.
- [ ] This directly feeds Part 3's `WIRING-MAP.md` — build that document AS you go through this
      list, rather than as a separate pass afterward, since you'll have the exact information
      the map needs freshly in hand for each file as you read it.

---

## PART 19: Final end-to-end re-verification (do this LAST, after every other part)

Every fix in this file has its own regression test. None of that guarantees the SYSTEM still
behaves correctly as a whole once everything lands together. Before this is considered done,
re-run a full live verification pass — not unit tests, actual live behavior against the
deployed app.

- [ ] Re-run the original five live verification questions from earlier in this project against
      the fully-updated, fully-deployed app:
      1. "What's the current capacity health?"
      2. "Who's using the most capacity right now?" — confirm the response now uses WHO/WHAT/WHY
         framing (Part 1/12), never a capacity percentage estimate.
      3. "What problems did we have today?" — confirm this now surfaces Part 12 taxonomy
         findings, not CU metrics, and correctly separates refresh activity (Part 14).
      4. "Show me a chart of CU% over time."
      5. "What caused the throttling yesterday?" — confirm the investigation pivot (Part 6)
         correctly anchors and, if needed, pivots to the right time window.
- [ ] Trigger one real Tier-2 cycle and one real hourly sweep end to end; confirm a genuine
      finding (if one exists in real data) creates a ticket, appears in the notification center
      with a timestamp (Part 15a), and can be resolved through both paths (Part 15b).
- [ ] Confirm the Daily Summary delivers, opens without a 404 (Part 8), leads with Part 12
      findings not CU (Part 13), and shows a correct Top 10 users list.
- [ ] Confirm `verdict.py` can now genuinely produce "optimize" given real contention/oversized-
      model data (FIX 0) — not just that the code path exists, that it fires on a real or
      realistic test case.
- [ ] Only after all of the above pass live: mark this file's overall effort complete. If
      anything in this list fails, that failure blocks handoff regardless of how many
      individual Parts above show as done.

---

## PART 21 (LATER — after everything above works, not before): Adopt from the KQL plugin

The plugin lives locally at `C:\Users\am08570\Downloads\kql-mcp-server-v5`. Point Claude Code
at this path directly and have it read the actual source — everything below is a map of what's
there and a verdict on each piece, not a substitute for Claude Code reading the real files
itself. Do not implement anything in this Part from memory of this document's summaries alone;
re-read the actual source file before porting or adapting it.

Do not start this until Parts 0-19 are complete and verified. Full plugin read, no exceptions:
every service file read in full (audit-rules.ts, analysis.ts, term-resolver.ts,
routing-table.ts, field-resolver.ts, field-aliases.ts, text-normalize.ts, schema-link.ts,
schema-cache.ts, catalog.ts, workspace-client.ts, hr-loader.ts, artifact-lookup.ts,
usage-query-builder.ts, azure-auth.ts, index.ts, constants.ts, format.ts, nl-generator.ts,
editor.ts, visualizer.ts, html-visualizer.ts, html-utils.ts) plus every command, every skill's
SKILL.md and reference docs, the full data layer, the build scripts, and the test suite.
Nothing was skipped without being read — the visual-output files initially deprioritized were
subsequently read in full and confirmed as skips for stated, evidence-based reasons (see
CONFIRMED SKIP below), not left unchecked.

### DIRECTLY PORT — new capability we don't have at all, low design risk

- [ ] **The Newell routing table + two-pass resolver** (`routing-table.ts` +
      `term-resolver.ts`). A fully curated, versioned mapping of every informal Newell name
      ("Z.Sales", "4-wall", "supply chain data") to the exact canonical `Ent-Reporting-*`
      dataset names this agent already investigates — the SAME tenant's data, not a pattern to
      reinvent. Includes curated ambiguity handling (e.g. bare "DTC" documented as overlapping
      Ecomm data) and a confidence tier system (HIGH/MEDIUM/LOW, LOW entries excluded from
      matching entirely). Port the table structure and matching logic into Python as a new
      tool; gives the agent informal-name resolution it currently lacks completely.
- [ ] **Field/measure-level resolution to EventText patterns**
      (`field-resolver.ts` + `field-aliases.ts` + `schema-link.ts`). A complete, layered
      fallback pipeline: exact match → alias expansion ("qty"→"quantity", "cust"→"customer")
      → catalog token-intersection → containment, each pass only trying if the previous found
      nothing. Resolves a field name to the authoritative DAX (`'Table'[Field]`) and MDX
      (`[Measures].[Field]`) patterns actually found in production `EventText` samples, then
      mints a ready-to-run, provenance-tracked usage query. This closes a real, current gap:
      our agent cannot currently answer "who used Invoice Quantity" at all without someone
      hand-writing (and likely getting wrong) an EventText filter.
- [ ] **The branded-type provenance pattern** (`usage-query-builder.ts`, previously flagged
      as the single strongest technical pattern in this plugin). A query's filter fragment can
      only be constructed by a resolver function — the type system itself prevents a raw
      agent-supplied string from being smuggled in as a filter. Python doesn't have compile-time
      branding the same way, but a sentinel/wrapper class checked at code-review time would give
      the same guarantee. This is a real hardening upgrade for `sql_guard.py`/`dax_guard.py`,
      not just a nice-to-have.
- [ ] **The 20k-field catalog inverted-index search** (`catalog.ts`) — lazy per-model loading
      (only ~2.5MB manifest+index at startup, not the full 14MB catalog), OR-semantics scoring
      with AND-preference. Directly portable if the same field catalog data is available or
      can be regenerated from the same source Excel exports.
- [ ] **Artifact inventory lookup** (`artifact-lookup.ts`) — three-way lookup (by name, ID, or
      workspace) over the same `ArtifactsMappedtoWorkspace.xlsx` already referenced elsewhere in
      this project's data. Confirm whether this exact file is already available/current before
      building a duplicate loader.

### HARDEN EXISTING — use the plugin's more rigorous version to upgrade something we already have

- [ ] **The EventText/DAX-MDX filter rule, made execution-blocking** (`CORRECT007`). Our
      system prompt already states "never hand-author an EventText filter" as a written
      instruction. The plugin makes the identical rule a hard, deterministic, error-severity
      check that blocks execution outright — a real hardening upgrade over relying on the LLM
      to remember a written rule every time.
- [ ] **Reconcile `forecast.py`/`anomaly.py`'s statistical thresholds** against the more
      rigorous, internally-consistent set in the plugin's `analysis.ts` and usage-analytics
      skill: trend direction requires n≥6 points with R²<0.3 flagged as weak (our
      `forecast.py` uses min_points=3 with no R² check at all); percentage-change is
      explicitly suppressed when the prior-period base is small (nothing in our own codebase
      currently prevents a misleading "200% increase" claim off a tiny denominator); spike
      severity is graded by z-score bands (moderate ≥2σ, severe ≥3σ or ≥100% above baseline) —
      more granular than our current binary anomaly flag.
- [ ] **Adopt median+4×MAD spike detection** (`analysis.ts`'s `medianAbsDeviation`) as an
      alternative/upgrade to `anomaly.py`'s current mean+stddev approach — MAD is outlier-
      robust in a way plain stddev isn't, meaning one extreme historical spike won't silently
      raise the threshold and mask the next real spike.
- [ ] **The error-conflation bug class, checked against our own collectors.** `workspace-
      client.ts`'s `getLogAnalyticsTableSchema` explicitly documents a bug they found and fixed:
      it used to swallow ALL errors as "table not found," which meant an expired auth token got
      misreported as a missing table — sending troubleshooting in the wrong direction entirely.
      This is the exact same failure CLASS found repeatedly in our own audit this session
      (silent, plausible-looking failures). Explicitly check `collector_workspace_monitoring.py`,
      `collector_log_analytics.py`, and `collector_rest.py` for the same conflation — do they
      distinguish "genuinely no data" from "the query/auth actually failed"?
- [ ] **The `normalizeExecutingUserDisplay()` defense-in-depth pattern** (`format.ts`). This
      plugin enforces "every displayed user identity must carry the full email domain" at
      THREE separate points — the display text, the Excel export, AND the raw structured data
      handed back to the LLM — so no code path can leak a bare username even if something
      bypasses the formatted display. Check whether our own agent enforces the equivalent
      invariant (a full identity string, never a bare username) at every point a user identity
      is shown — chat responses, alert cards, the notification center, exported data — or only
      in whichever single place someone happened to add it first.
- [ ] **The NL-generator structural guard, checked against our own NL-to-SQL/DAX skill.**
      `nl-generator.ts` hard-checks, before any pattern-matching runs, whether a question is
      really a Power BI usage question — and if so, refuses to freeform-generate a query at
      all, redirecting to the authoritative builder instead, even if the agent's own prompt
      instructions were somehow missed. This is a stronger safety net than a written
      instruction: the wrong tool is structurally incapable of being misused for that class of
      question. Check whether Phase 7's NL-to-SQL/DAX skill has an equivalent hard redirect for
      any question that should route through a more specific, safer existing path instead of
      being freely generated.
- [ ] **Verify Newell brand color tokens in `chart.tsx`.** The plugin's HTML visualizer uses
      approved Newell brand colors (`Newell Blue #288FC2`, `Newell Navy #01405C`) sourced from
      the same `newell-doc-templates` organizational skill already available to this project.
      Quick, low-risk check: confirm our own chart rendering uses these same approved tokens
      rather than generic chart-library defaults; if not, this is a small, safe visual
      improvement, not a structural change.

### ADAPT PATTERN — same idea, needs a different implementation for our architecture

- [ ] **The 5-part answer structure** (headline / trend / top consumers / risks /
      recommendation) — confirmed as a deliberately reused standard across multiple skills in
      this plugin (kql-usage-analytics AND kql-analytics-interpreter both specify it
      identically), not a one-off. Adopt as the Daily Summary's answer format (Part 13),
      adapted to lead with Part 12's taxonomy instead of adoption metrics.
- [ ] **Kusto error-code-to-fix-suggestion mapping** (`parseKustoError` in
      `workspace-client.ts`: `E_RUNAWAY_QUERY`, `E_QUERY_RESULT_SET_TOO_LARGE`, timeout, each
      with 3-4 concrete fix options). If our own NL-to-SQL/DAX query execution surfaces similar
      raw errors today, adapt this pattern — turn a cryptic Kusto/SQL error into an actionable
      suggestion rather than a raw stack trace.
- [ ] **HR coverage-threshold discipline** (`hr-loader.ts`) — already noted, restated here with
      the now-confirmed full column schema (Function, Sub Function, Business Unit, Region,
      Manager, Work Location) from the same real Newell HR file
      (`HCMIF0485_IDT_DASHBOARD.xlsx`) if HR/org enrichment on top of user attribution is ever
      wanted. The exact governance reasoning for why this reads a local snapshot rather than
      live Graph (AADSTS65002, M365 MCP connector confirmed non-viable) is also directly
      relevant if this project ever explores similar enrichment and hits the same wall.
- [ ] **The "large-result gate" UX pattern** — confirmed word-for-word identical across two
      separate commands (`kql-ask.md`, `kql-query.md`), i.e. a deliberate, standardized safety
      pattern, not incidental duplication: before displaying any result over 50 rows, STOP and
      offer the user an explicit choice (skip and query it themselves / truncate to N / show an
      aggregate only / show the first 100 capped) rather than unilaterally dumping a huge table
      into the conversation. Check whether our own agent has an equivalent gate before a large
      query result or investigation output lands in a chat response, particularly once the
      NL-to-SQL/DAX skill or any new Part 12 investigative tooling can return large result sets.
- [ ] **"Don't re-execute if results already exist in context"** (`kql-visualize.md`) — a small
      efficiency check: confirm our own agent reuses already-fetched data already present in the
      conversation for a follow-up visualization/export request instead of unnecessarily
      re-running the same query.

### CRITICAL CROSS-CHECK — our own concentration threshold may be miscalibrated independent
of the formula bug already being fixed

Two independent reference docs in this plugin (`kql-analytics-interpreter/references/
thresholds.md` and `kql-usage-analytics/references/adoption-thresholds.md`) — built by a
different team, for the SAME underlying `PowerBIDatasetsWorkspace` table — both independently
land on the same number: **top-1-user concentration risk begins at 60% of queries.** This
project's thresholds are `concentrationPct=30` (config.py) and `DOMINANT_ITEM_SHARE_PCT=40`
(gates.py) — a real, material gap, not a rounding difference.

- [ ] This does NOT replace the metric()-formula fix already planned (Part 1/FIX 1) — that bug
      is real and confirmed regardless of threshold value. But once the formula is fixed,
      explicitly re-evaluate whether 30%/40% is still the right bar, or whether it was set too
      aggressively and is part of why normal variation has been read as a problem more often
      than it should. Consider raising toward the externally-validated 60% figure, or at minimum
      document why this project's number is deliberately different if it's kept as-is.

### ADOPT METHOD — a real analytical technique our own anomaly detection is missing

- [ ] **Same-hour/day-of-week comparison before calling something an anomaly**
      (`kql-usage-analytics/references/spike-drilldown.md`, stated explicitly: "if a spike
      recurs at the same hour every day, compare same-hour buckets across days before calling
      it an anomaly — a Monday 9am peak is a pattern, not an incident"). `anomaly.py` currently
      compares only against a flat historical mean/stddev with no time-of-day or day-of-week
      decomposition at all. If Fabric capacity has any real daily/weekly usage cycle (near
      certain — quiet overnight, busy business hours), this is a second, independent likely
      source of false-positive noise beyond the metric()-formula bug already found. Build a
      same-hour-across-days (or same-weekday-across-weeks) comparison baseline as an upgrade to
      `anomaly.py`'s current flat-baseline approach.
- [ ] **Minimum-volume floor before treating a change as meaningful** — the same reference doc
      explicitly names "a jump from 2 to 8 queries is noise, not an incident" as a known common
      pitfall. This is the THIRD independent source this session confirming the exact failure
      class behind the Timothy Manton bug (a team with no connection to this codebase, working
      on the same underlying data, documented the identical problem). Strong additional
      validation that Part 1's redesign direction (absolute thresholds, not raw percentage
      swings on thin samples) is correct.

### CONFIRMED SKIP — all six remaining files now read, verdicts based on actual content

- Azure CLI delegated-user auth (`azure-auth.ts`) — built for an interactive human; this
  agent runs unattended via Databricks Jobs. Our service-principal client-credentials flow is
  correct; do not touch it.
- Single-workspace/single-subscription pin — deliberately narrow for this plugin; this project
  is explicitly moving toward multi-workspace support (Part 9's B4), the opposite direction.
- `editor.ts` — generates a standalone, offline Monaco KQL editor HTML file written to local
  disk. No fit: the agent itself is the query interface, we have no use case for handing a
  user an offline query-writing tool.
- `visualizer.ts` — hand-constructs real native Excel OOXML chart/drawing XML embedded into
  `.xlsx` files on local disk. Genuinely sophisticated technique, but redundant with the
  `xlsx` skill this project already has access to, and the local-file-write model doesn't fit
  a hosted multi-user app.
- `html-visualizer.ts` — a full Newell-branded ECharts HTML generator, same local-file-write
  mismatch as above; redundant with our own `chart.tsx`/`render_chart`. One narrow thing DID
  come out of reading it — see the Newell brand color token check added above.
- `html-utils.ts` — trivial HTML-escaping utility. Our chart rendering is React/JSX-based,
  which auto-escapes text content by default, so this specific concern is already handled by
  the framework and doesn't need porting.
- `nl-generator.ts`'s 16 domain-specific pattern matchers (App Insights/Azure Monitor
  telemetry: slow endpoints, container errors, dependency failures) — a genuinely different
  monitoring domain than Power BI/Fabric capacity; not applicable content, though see the
  structural-guard pattern captured above from the same file.
- `kql-debugging` skill — covers `evaluate python()` inline debugging, an Azure Data Explorer
  (ADX)-only feature that doesn't exist in Log Analytics/App Insights or in this project's
  stack at all. Not applicable.
- `kql-nl-generator` and `kql-schema-explorer` skills' domain content — both scoped entirely
  to Application Insights/Azure Monitor telemetry tables (requests, exceptions, dependencies,
  ContainerLogV2, etc.), a different monitoring domain than Power BI/Fabric capacity. The
  general principle both restate ("never guess a column name — a wrong one returns 0 rows with
  no error, it silently lies to you") is genuinely relevant as further, independent validation
  of this project's own "silence is dangerous" principle already built into the audit
  methodology (Part 3) — not new content, but a third external confirmation of the same
  concern.
- `kql-sdk-integration` skill — covers embedding official Azure SDKs into external
  applications; not relevant to improving this agent itself.
- `kql-visualizer` skill's chart-type decision table — redundant with whatever chart-type
  auto-selection `chart.tsx`/`render_chart` already does. Worth a quick parity check (does our
  own logic correctly choose line vs. bar vs. scatter using comparably simple, correct rules?)
  but not a port.
- All 8 test files (`__tests__/*.test.ts`) and the fixture — read for their assertions; they
  confirm the implementation matches its own documented contracts rigorously (byte-identical
  determinism, zero untraceable provenance clauses, symmetric ambiguity, injection-safe title
  comments) but revealed no new integration-relevant content beyond what direct implementation
  reading already surfaced.

Everything in this plugin has now been read: every service file, every command, every skill's
SKILL.md and reference docs, the full data layer (`newell-schema.json`, the field catalog JSON
files, the search index, the build scripts that generate them), and the test suite. Not read:
the two source `.xlsx` files directly (binary; their derived JSON output has been verified
instead) and `KQL-Plugin-Loading-Guide.pdf` (a setup/installation guide, not a source of
integration-relevant logic).

### Before starting any of the above

- [ ] Confirm whether this project has a real, independent Log Analytics connector to
      `PowerBIDatasetsWorkspace` outside the fabric-audit-agent's own collectors, or whether
      porting the field/term-resolution logic requires building a fresh KQL execution path
      first. Do not assume the plumbing already exists just because the plugin's queries and
      table names look identical to what this project already reads.
- [ ] Confirm whether `data/newell-schema.json` (the field-to-DAX/MDX-pattern schema this
      plugin's field-resolver depends on) or an equivalent already exists anywhere accessible
      to this project, or whether it would need to be generated fresh from the same or similar
      source Excel exports ("Grant's Excel exports," per the plugin's own comments).

---

## PART 22: JSON/data asset plan — concrete steps, not an open question

The plugin's entire field-resolution capability (routing table, field catalog, DAX/MDX
patterns) rests on JSON files generated from real Newell source Excel files by two build
scripts (`scripts/generate-schema.cjs`, `scripts/build-field-catalog.cjs`, both at the local
plugin path above). This is a concrete, sequenced plan for getting equivalent data into this
project, not just "check if it exists."

- [ ] **Step 1 — Inventory what already exists in this project.** Check whether
      `ArtifactsMappedtoWorkspace.xlsx` (already referenced elsewhere in this project's own
      data per earlier sessions) is the SAME file the plugin uses, and whether it's current.
      Check whether anything resembling `newell-schema.json` or the Dim Catalog CSVs already
      exists anywhere in this project's reach.
- [ ] **Step 2 — If the source Excel files are accessible** (the "Grant" DAX-Queries-for-Schema
      folder, the Dim Catalog CSVs, referenced by the build scripts' own comments): port
      `generate-schema.cjs` and `build-field-catalog.cjs` to Python. Both scripts are pure,
      mechanical transforms (string-template DAX/MDX pattern generation, JSON reshaping) with
      no LLM calls and no complex logic — a low-risk, well-scoped port. Confirm the three Excel
      format variants the original script handles (Format A/B/C per its own comments) and
      whether all three are still needed for whatever source files this project can access.
- [ ] **Step 3 — If the source Excel files are NOT accessible to this project:** do not attempt
      to fabricate or approximate this data. Scope down explicitly — either request access to
      the same source materials through whatever channel makes sense (the plugin's own author,
      or whoever maintains the underlying Excel exports), or drop the field-resolution capability
      from this round of work and revisit later. State this decision plainly rather than
      shipping a partial or guessed schema.
- [ ] **Step 4 — Once real data is in hand:** build the routing-table equivalent and the
      field-schema equivalent as versioned, reviewable data files in this project (matching the
      plugin's own discipline — `routing-table.ts` documents its own review history and
      confidence tiers; do the same here rather than treating this as throwaway config).

---

## PART 23: Prompting/enforcement layer — the instructions that make the new tools actually get used correctly

Porting a tool's code is not the same as the agent knowing when and how to use it correctly.
The plugin enforces correct tool usage through TWO layers working together — written
instructions in the agent/command files, AND a mechanical backstop (`hooks.json`'s
PostToolUse interceptor) that catches misuse even if the written instructions are missed. Both
layers need an equivalent here for every new tool ported from Part 21.

- [ ] **System prompt additions mirroring `kql-analyst.md`'s explicit tool-sequencing rules.**
      For each new capability ported (term resolution, field resolution, artifact lookup): add
      an explicit rule to `agent_server/system_prompt.py` stating exactly when it must be called
      — e.g. "whenever the user mentions an informal Newell dataset name or alias, call the term
      resolver FIRST, before any query generation" (mirrors `kql-analyst.md`'s identical rule for
      `kql_resolve_term`). Write these as explicit, unambiguous rules, not vague guidance — the
      plugin's own agent file is a direct, adaptable template for the exact wording pattern.
- [ ] **A hard "never hand-author this" rule for field-usage filters**, mirroring
      `kql-analyst.md`'s explicit list of wrong patterns it tells the agent to recognize and
      never produce (bare `EventText has "invoice"`, invented underscore variants, etc.). Port
      this same explicit "here is what NOT to do, and why each version is wrong" framing into
      the system prompt for the new field-resolution tool, not just a positive instruction to
      use the right tool.
- [ ] **A mechanical backstop equivalent to `hooks.json`'s PostToolUse interceptor.** The plugin
      doesn't rely on written instructions alone — a hook fires after every `kql_generate` call
      and force-corrects the agent mid-conversation if it detects the exact misuse pattern
      (using freeform generation for a question that should have used the authoritative
      resolver instead), independent of whether the LLM remembered the rule. Design an
      equivalent checkpoint in `agent_server`'s own tool-calling loop: after any general-purpose
      query-generation tool is called, check whether the question actually matched a pattern
      that should have routed through the new term/field resolver instead, and if so, redirect
      before continuing — don't just hope the prompt instruction was followed.
- [ ] **Confirm every new tool's description text carries the same self-contained guidance
      the plugin's tool descriptions do** — e.g. `kql_field_usage`'s description explicitly
      tells the calling model "you never see, write, edit, or verify the EventText filter
      yourself" directly in the tool schema, not just in a separate agent file. This means the
      guidance travels with the tool itself and survives even if the system prompt is later
      edited without remembering to update every cross-reference.

---

## PART 24: Plugin gap corrections — findings from reading the actual zip (2026-08-07)

The prior session's Part 21 analysis was done without the actual plugin files in hand. This
part documents every discrepancy found by re-reading the zip directly. Every item here either
corrects a gap in Part 21, updates Part 22's data-access status, or adds new findings.
**Parts 21–23 remain valid but must be read WITH these corrections applied.**

### 24a. kql-performance-tuner skill — COMPLETELY MISSED, DIRECTLY PORT

`skills/kql-performance-tuner/` was not mentioned anywhere in Part 21 — not ported, not
skipped, simply unread. It has direct applicability to our KQL generation layer:

- **5-step workflow:** audit (`kql_audit`) → profile (operator-cost.md) → apply fixes →
  benchmark → document. Every KQL our agent generates should pass this same discipline.
- **The "5 biggest gains" (in order of impact):** time filter first, `has` not `contains`,
  `=~` not `tolower()`, filter before join, `project` after join. These are the top 5
  anti-patterns in generated KQL.
- **operator-cost.md** classifies every KQL operator: ⚡ fast (index-aware: `has`,
  `startswith`, `==`, `=~`, `in`, time filter), 🟡 moderate (`summarize`, pre-filtered join,
  `mv-expand`), 🔴 slow/critical (`contains`, `tolower(col)==`, regex, `order by` without
  `take`, unfiltered join, no time filter on large table).
- **optimization-patterns.md** has 7 before/after pattern pairs (not 5 — the SKILL lists 5
  but the reference has 7, including `dcount` over `count distinct` and `top N` over
  `order by + take`).

Port this as a KQL quality gate into `kql_guard.py`:
- [ ] Add a pre-generation checklist mirroring the operator-cost table — when the agent
      generates a KQL query against `PowerBIDatasetsWorkspace` or any other high-volume Log
      Analytics table, validate it against the 5 biggest gains BEFORE execution.
- [ ] Add the 7 optimization patterns as named checks in `kql_guard.py` (alongside the
      existing guards), each with a specific error message and the corrected form.
- [ ] Exception clause (already exists in the plugin): `contains` used in EventText DAX/MDX
      patterns (operand contains `[` or `]`) must NOT be rewritten to `has` — `has` breaks
      bracket-structured field references. This exemption must be coded explicitly, not left
      to judgment. PERF001 in the plugin already implements this exemption; port its logic.
- [ ] System prompt rule: add the "5 biggest gains" as a named, ordered checklist to the
      NL-to-KQL section of `agent_server/system_prompt.py`.

### 24b. Three PostToolUse hooks, not one — correction to Part 23

Part 23 described "a PostToolUse interceptor" as if there were one. There are three distinct
hooks, each with different logic:

1. **After `kql_execute`:** auto-call `kql_analyze` on the results (unless user asked for raw
   only). This is the analysis auto-trigger.
2. **After `kql_execute`:** if results contain an `ExecutingUser` column, auto-call
   `kql_enrich_hr` with the UPN list WITHOUT asking the user first. Enforces the HR
   enrichment rule at the tool-call level, not just in the prompt.
3. **After `kql_generate`:** check if the request was about Power BI field/measure usage —
   if yes, `kql_generate` was the WRONG TOOL; force-call `kql_field_usage` instead and
   discard the generated query. This is the most important redirect hook.

Plus a **`SessionStart` hook** (not mentioned in Part 21): runs `hooks/scripts/check-setup.sh`
on session start to verify auth/build status.

For Part 23's mechanical backstop design:
- [ ] Implement three equivalent hooks in `agent_server`'s tool-calling loop, not one:
  (a) After any query execution tool: auto-run result analysis.
  (b) After any query execution tool: if ExecutingUser column present, auto-enrich identity
      display (strip domain check, per the identity normalization pattern from Part 21).
  (c) After any general KQL/DAX generation tool: check if the question was field/measure
      usage; if so, redirect to the field-resolution path BEFORE running the generated query.
- [ ] Hook (c) is the critical safety one — it fires even if the prompt rule was missed.

### 24c. The full 26-rule audit engine — Part 21 undersold this

Part 21 only highlighted CORRECT007 from `audit-rules.ts`. The full engine has 26 rules
across 4 categories (10 PERF, 7 CORRECT, 6 BEST, 3 TELEMETRY) with a scoring system
(100 − deductions: error=−25, warning=−10, info=−2, hint=0) producing an A–F grade.

Four rules are directly applicable to our KQL guard layer beyond CORRECT007:

- **PERF001** (`contains` → `has`, with EventText exemption): our collector queries sometimes
  use `contains` for token matching where `has` is correct. The exemption logic (operand
  contains `[` or `]`) is the key — see 24a above.
- **PERF003** (missing time filter on high-volume tables, error-severity): `PowerBIDatasetsWorkspace`
  is a high-volume table. Any collector query without a time filter should be blocked at the
  guard level with error severity, same as the plugin blocks `kql_execute`.
- **CORRECT001** (`== null` → `isnull()`): KQL `column == null` always returns false. If any
  of our generated queries do null-checks this way, they silently return wrong results.
- **CORRECT007** (hand-authored EventText filter): already in Part 21, confirmed accurate.

For the `kql_guard.py` upgrade:
- [ ] Port PERF001, PERF003, CORRECT001, and CORRECT007 as named guard checks in
      `kql_guard.py`. Each should produce a named, categorized error with the same
      correction suggestion the plugin uses.
- [ ] The PERF001 EventText exemption (`operandIsEventTextPattern` — operand contains `[`
      or `]`) must be implemented in the Python port or PERF001 will incorrectly flag
      legitimate `EventText contains "'Table'[Field]"` patterns as WRONG.
- [ ] Consider porting the scoring/grade output as a diagnostic artifact (not user-facing,
      but useful for debugging guard results in CI).

### 24d. Data files ARE ALREADY PRESENT — Part 22 Steps 1 and 3 are answered

Part 22 asked "confirm whether the Excel files are accessible" as Step 1, with Step 3 being
"if not accessible, state this plainly." The files are IN THE ZIP and available now:

- `data/ArtifactsMappedtoWorkspace.xlsx` ✓ (same file referenced in this project)
- `data/newell-schema.json` ✓ — 10,675 column/measure entries across 13 named models,
  each with `dax` and `mdx` pattern strings (e.g. `"dax": "'Ad Groups'[Ad Group ID]"`)
- `data/enriched-field-catalog.json` ✓ — raw build input for the catalog
- `data/HCMIF0485_IDT_DASHBOARD.xlsx` ✓ — the HR file (columns: Function, Sub Function,
  Business Unit, Region, Manager, Work Location)
- `data/catalog/` ✓ — complete: `manifest.json`, `search-index.json` (20,683 field entries),
  and 13 per-model JSON files (CMMS, DTC, Ecomm, Finance, Marketing, Ops-Finance,
  Profitability, Purchasing-Finance, Quality, Sales, SCM, SLM, Walmart)
- `scripts/build-field-catalog.cjs` ✓ and `scripts/generate-schema.cjs` ✓ — both present

Revised Part 22 sequencing:
- [ ] **Step 1 is done**: data exists. The zip itself is the source; extract to a `data/`
      folder at an agreed location within this project.
- [ ] **Step 2 is now concrete**: port `build-field-catalog.cjs` and `generate-schema.cjs`
      to Python. Both are pure mechanical transforms (string-template pattern generation,
      JSON reshaping, Excel reading via openpyxl). No LLM calls, no complex logic.
- [ ] **Step 3 ("if not accessible") is moot**: the data is here. Proceed to Step 4.
- [ ] **Step 4**: build `routing-table.py` (from `routing-table.ts`) and port
      `field-resolver.py` (from `field-resolver.ts` + `field-aliases.ts`) as versioned,
      reviewed data + logic files. Use the extracted `data/` files as their inputs.

### 24e. kql-analyst.md content not captured in Part 21

Three items from the actual agent file that were not included:

**xmSQL format — must never be searched:**
The plugin documents three EventText formats: DAX (`DEFINE/EVALUATE`), MDX
(`SELECT ... FROM [ModelName]`), and xmSQL (VertiPaq storage engine internals with numeric
col IDs like `[Invoice Quantity (N)].[Invoice Quantity (M)]`). xmSQL has the highest volume
(269,343 occurrences per day sample) but the IDs are internal and unmappable.
> "Do NOT attempt to search EventText for xmSQL field references. The numeric IDs are
> internal VertiPaq identifiers with no mapping to display names."

- [ ] Add this as an explicit system prompt rule: "Never search EventText using xmSQL-format
      column ID references (numeric IDs in brackets like `[Invoice Quantity (N)]`). Only DAX
      and MDX patterns produced by the field resolver are correct."

**Workspace lookup KQL when `kql_resolve_term` doesn't return a workspace:**
```kql
PowerBIDatasetsWorkspace
| where TimeGenerated > ago(24h)
| where ArtifactName == 'Ent-Reporting-DTC'
| summarize Rows=count(), WorkspaceName=any(PowerBIWorkspaceName),
            WorkspaceId=any(PowerBIWorkspaceId), DatasetName=any(ArtifactName)
```
- [ ] Add this pattern to the agent's investigation toolkit — a workspace lookup that doesn't
      require a pre-built routing table and works from live Log Analytics data.

**`kql_enrich_hr` invocation rule (from the agent file):**
After any `kql_execute` that returns an `ExecutingUser` column, call `kql_enrich_hr`
automatically without asking — this is stated in both the agent prompt AND enforced by hook
#2 in hooks.json. The HR coverage gate (75% for percentages, lowCohortFlags annotation) is
part of the same flow. Our attribution display has no equivalent HR coverage disclosure;
if we ever add HR enrichment via the Graph API or a similar source, this three-layer
(display text + export + structured data) discipline is the correct model to follow.

### 24f. Four additional tools not described in Part 21

The plugin has 19 tools (not ~15 as implied). Four were not mentioned:

- **`kql_field_search`** — fuzzy field discovery over the 20,683-field catalog. Input: a
  partial field name; output: candidate fields with their models. The discovery step before
  `kql_field_detail` or `kql_field_usage`. Port this as a search endpoint once the catalog
  is loaded.
- **`kql_field_detail`** — full metadata drill-down for one catalog field: description,
  examples, DAX and MDX patterns. The inspect step before running a usage query.
- **`kql_query_limits`** — pre-flight check against Azure Monitor service limits (row caps,
  MB limits, timeout thresholds). Our collector queries can hit these silently. Add an
  equivalent pre-flight check to `collector_log_analytics.py`.
- **`kql_format`** — normalize KQL pipe indentation and spacing. Useful as a pre-commit step
  on any KQL stored in `query_library.json` — ensures stored queries are consistently
  formatted and human-readable.

### 24g. kql-audit.md and kql-explore.md commands — not discussed in Part 21

These two commands were never mentioned:

**`kql-audit.md`** (`/kql-audit`): calls `kql_audit` with `explain: true`, leads with
score/grade, explains WHY each correctness error produces wrong results (not just states
the rule), then offers to (a) fix errors and return corrected query, or (b) format with
`kql_format`. The audit-before-execute pattern is also enforced in `kql-query.md`'s
step 1 ("Block on correctness errors; show warnings for user awareness").
- [ ] The `kql-audit.md` workflow is the template for how `kql_guard.py` should present
      findings: score, grade, WHY each error is wrong, corrected form. Currently `kql_guard.py`
      returns errors without explaining why or providing a corrected version.

**`kql-explore.md`** (`/kql-explore`): groups workspace tables by category including a
explicit "Power BI Usage & Adoption" category covering `PowerBIDatasetsWorkspace`. Shows
the `kql_field_search` + `kql_field_detail` discovery workflow for Power BI field questions
vs. `kql_schema_tables`/`kql_schema_table` for Azure table columns. Maps both paths clearly.
- [ ] The explore command's two-path structure (field questions → field-resolver tools;
      Azure table columns → schema tools) is the model for how the agent should route
      Power BI vs. Log Analytics questions differently in `system_prompt.py`.

### 24h. types.ts — not mentioned, `QueryOutline` is relevant

`types.ts` was never read in the prior session. The `QueryOutline` type is relevant:
```typescript
interface QueryOutline {
  sourceTable: string | null;   // first non-keyword table name
  letBindings: string[];        // let-bound variable names
  pipelineSteps: PipelineStep[]; // ordered list of | operators
  hasTimeFilter: boolean;
  hasRowLimit: boolean;
  hasSummarize: boolean;
  hasJoin: boolean;
  estimatedDataSource: "log-analytics" | "app-insights" | "unknown";
}
```
This is produced unconditionally by `auditKql()` and passed to downstream tools. Our own
KQL investigation layer (`investigation/evidence.py`, `investigation/events.py`) does ad-hoc
query string manipulation; a structured parse equivalent to `QueryOutline` would make those
manipulations safer and testable.
- [ ] Consider whether `kql_guard.py`'s analysis should produce a `QueryOutline`-equivalent
      Python dataclass, making structural query analysis (does it have a time filter? does it
      have a row limit?) reusable by callers rather than re-parsed each time.

---

## EXECUTION ORDER

1. Part 16a (Lakebase auth identity fix) — the single most confidently-diagnosed bug in this
   file; fixing it is likely to resolve Part 7 completely on its own
2. Part 0 (immediate noise stop) — can ship fast, stops the bleeding
3. Part 7 + Part 8 (notification center / daily summary bugs — Part 8 also fixes the Daily
   Summary "Not Found" page, same root cause; Part 7 should now just be verification, since
   16a is the fix) — high-value, fully root-caused, quick to fix
4. FIX 0 (verdict.py optimize path) — most foundational correctness gap
5. Part 1 (the redesign) — the actual fix to "too much CU, not enough real facts"
6. Part 6 (investigation pivot strategy) — directly improves what users see when they click
   into any alert
7. Part 12 (the BAD taxonomy) — the foundation Part 1's redesign and Part 13's daily summary
   redesign both depend on; build this before either
8. Part 13 (Daily Summary redesign) + Part 14 (refresh separation)
9. Part 2 remaining items (FIX 2, FIX 3)
10. Part 16b, 16c (connection retry, webhook error handling) — smaller robustness fixes, can
    land alongside Part 3's wiring audit
11. Part 3 (wiring audit) — build the map, it will likely surface more before Part 5/9/10/11
12. Part 4 (permanent visibility)
13. Part 5 (CU/CPU exposure polish)
14. Part 9, 10, 11 (broadening, tightening, UI) — larger scope, brainstorm → spec → build each
    individually, same process as every prior sub-project in this codebase
15. Part 15 (card timestamp + universal auto-ticketing) — 15a can ship alongside Part 5 (both
    touch the same card-building code); 15b's regression tests should run LAST, after Parts
    1/12/13's new finding types actually exist, since it tests that they were correctly wired
    into the ticket lifecycle rather than built as standalone chat-only detectors
16. Part 21/22/23/24 (plugin adoption — code, data, and prompting/enforcement together):
    - **Read Part 24 first** before starting any plugin work; it corrects the prior session's
      analysis in 8 concrete ways (missed skill, wrong hook count, incomplete rule set, data
      availability confirmed, missed tools, missed commands, additional system prompt rules,
      missing dataclass pattern).
    - Sequence within this group: Part 24d (data is already present in the zip — extract it)
      → Part 22 Step 2 (port both build scripts to Python using the extracted data) → Part
      21's DIRECTLY PORT items + Part 24a's performance-tuner additions → Part 23 (three
      hooks, not one, per Part 24b) + Part 24's system prompt additions (24a, 24e, 24g) →
      Part 21's HARDEN EXISTING / ADAPT PATTERN / ADOPT METHOD / CRITICAL CROSS-CHECK items
      + Part 24c (full audit rule set) + Part 24f (four missing tools) + Part 24h (QueryOutline)

---

## PART 26: Three-pass exhaustive audit — all remaining gaps (2026-08-07)

Three full passes through every file in the zip. Every file was read. What follows is
every finding NOT already in Parts 21–25. Ordered by implementation priority.

### 26a. CMMS and OEE Monthly Reports are LOW confidence — excluded from term matching

Not just missing from the catalog — they are in the routing table but with
`confidence: "LOW"`. `term-resolver.ts` explicitly filters these out at index-build time:
> "LOW-confidence entries are excluded from the match index entirely... CMMS and
> 'OEE Monthly Reports' catalog-only entries (routing-table v2.1) are LOW and
> intentionally excluded from term matching and from the 'Known models' list until the
> BI team verifies alias variants for them."

So the routing table has 15 entries, but only 13 participate in matching. The catalog
has 14 models (DTC is in catalog via CSV synthesis; CMMS and OEE Monthly Reports are
HIGH in the catalog but LOW in the routing table). This creates a 3-way split:
- Routing table only (participating): 13 models (excludes CMMS and OEE Monthly Reports)
- Catalog only (14 models incl. DTC): can be searched via `kql_field_search`
- newell-schema.json (13 models, excl. DTC): field patterns for EventText searches

- [ ] The Python routing-table port must filter out LOW confidence entries the same way:
      build the match index from `confidence != "LOW"` entries only. LOW entries remain
      in the file for future promotion but are invisible to matching.
- [ ] `ALL_CANONICAL_NAMES` (the no_match message string) must also exclude LOW entries.

### 26b. 8 routing table entries have XMLA connectionPath strings

Sales, Ecomm, Marketing, Ops-Finance, Quality, SCM, SLM, and Walmart each have:
```
connectionPath: "powerbi://api.powerbi.com/v1.0/myorg/{WorkspaceName}"
```
The resolved result includes this when present. Finance, Profitability, PurFin, DTC,
HR, CMMS, and OEE Monthly Reports do not. These connect strings let users connect
Excel Analyze-in-Excel or DAX Studio directly to the model.

- [ ] The Python routing-table.py must include the `connectionPath` field on entries
      that have it. The `resolved` result should surface this when present.

### 26c. schema-link.ts Pass 1c guardrails — 4 rules needed for the Python port

The schema-link tokenizer (Pass 1c) has these explicit guardrails:
1. Single-token queries must be ≥4 chars (`SINGLE_TOKEN_MIN_LENGTH = 4`). Below 4
   chars, Pass 1c returns empty and field-resolver falls through to containment.
   This means "qty" (3 chars) skips Pass 1c entirely; "qty" is ALIAS_MAP's job.
2. Multi-token queries use AND-semantics (intersection of per-token field-name sets).
3. Runaway sanity ceiling: 500 (`RUNAWAY_SANITY_CEILING`). Only for pathological cases.
4. Alias-expanded variants are tried FIRST; raw query tried last. If an alias expansion
   hits, the raw query is never tried, preventing a worse containment match from winning.

AND vs OR semantics matters: a query "invoice quantity" returns only fields whose name
contains BOTH "invoice" AND "quantity" tokens. A query "sales" (4 chars) returns all
fields with any "sales" token (potentially large, which is OK — field-resolver.ts caps
the final result at 20 before treating them as a match set).

- [ ] Port all 4 guardrails into `schema_link.py`. The min-length and AND-intersection
      logic are not obvious from the docstring and will be missed without this note.

### 26d. kql_execute has a `skip_audit` parameter — relevant for testing

`kql_execute` accepts `skip_audit: boolean (default false)` that bypasses the
pre-execution CORRECT* error gate. The tool description says "not recommended."
Relevant for our test harness: if we ever test collector_log_analytics.py queries
against a live workspace in CI, queries with known audit violations (e.g., missing
time filter) can be run with skip_audit=true to verify the query shape, then the
fix is applied and tested separately.

### 26e. executeLogAnalyticsQuery timeout = 55 seconds, not 4 minutes

`workspace-client.ts`'s `executeLogAnalyticsQuery` sets:
```json
{ "query.timeoutMs": 55000 }
```
Comment: "use PT55S so the client gets a clean error before the connection drops."
The default Log Analytics timeout is 4 minutes — 55s was chosen to fail fast with
a clean error rather than letting the connection timeout silently.

- [ ] Check `collector_log_analytics.py`'s timeout setting. If it uses the 4-minute
      default, users will see silent hangs rather than actionable timeout errors.
      Adopt the 55-second pattern with a specific timeout error message.

### 26f. inferTimespan bug that existed in v3 kql_client.py — check our collector

`workspace-client.ts` has an explicit BUG FIX comment:
> "The original code used Math.ceil for days, making the sub-day hourly branch
> unreachable dead code. e.g. ago(4h) + 1h margin = 18000s was returning P1D,
> now correctly returns PT5H."

If our `collector_log_analytics.py` derives an ISO 8601 timespan from `ago()` calls
in KQL queries, check whether it has the same Math.ceil/Math.floor error. Querying
with P1D when the query specified `ago(4h)` means Log Analytics scans 24h of data
instead of 5h — 6x unnecessary scan cost on every query.

- [ ] Find every place in the codebase that converts an `ago(Xh)` or `ago(Xm)` KQL
      expression to an ISO 8601 timespan string. Verify it uses floor for days and
      ceiling for remaining hours, not ceiling for the full day count.

### 26g. apiFetchWithRetry — retry pattern our collector lacks

The `workspace-client.ts` uses bounded retry with backoff:
- Max 2 retries (3 total attempts)
- Backoff: 1s, 2s
- Only retries transient failures: HTTP 429 (throttling), 503, 504
- Non-transient errors (401, 400, semantic errors) propagate immediately without retry

Our `collector_log_analytics.py` has no equivalent retry logic. A single 429 from
Log Analytics on a busy query will fail the entire sweep rather than retrying.

- [ ] Add the same 2-retry / transient-only pattern to `collector_log_analytics.py`'s
      HTTP layer. The transient-detection pattern: `HTTP (429|503|504)|throttled`.

### 26h. Zod-equivalent API response validation our collector lacks

`workspace-client.ts` validates every Log Analytics API response against a Zod schema:
```typescript
const LaQueryResponseSchema = z.object({
  tables: z.array(z.object({
    name: z.string(),
    columns: z.array(z.object({ name: z.string(), type: z.string() })),
    rows: z.array(z.array(z.unknown())),
  })),
});
```
If the response shape changes (API version drift, network proxy, cold-start), the
error message is specific: "Unexpected API response shape... Validation errors: ..."
rather than a cryptic AttributeError or KeyError that's hard to diagnose.

- [ ] Add equivalent Pydantic or TypedDict validation to `collector_log_analytics.py`'s
      response parsing. At minimum, check that `tables` is a list with at least one
      element and that `columns` and `rows` exist before accessing them.

### 26i. kql_workspace_usage always includes DistinctUsers + staleness fields

`buildWorkspaceUsageQuery` always outputs:
- Single-window: `QueryCount = count(), DistinctUsers = dcount(ExecutingUser),
  LastUsed = max(TimeGenerated)`
- Compare-periods: `QueryCount = count(), DistinctUsers = dcount(ExecutingUser)` (no LastUsed)

The compare-periods mode also doubles the scan window: a `30d` comparison scans `60d`
total (30d current + 30d prior). The retention check applies to the doubled span, so a
`31d` comparison would warn because `62d > 60d`. For our daily digest adoption queries,
this means we must use ≤30d per period to fit within the retention window.

- [ ] Our daily digest's Log Analytics queries for workspace adoption should always
      use `ago(30d)` max (not `ago(60d)`) for single-window, and 30d per period for
      compare-mode, to stay within the 60-day retention without triggering a warning.

### 26j. `kql_execute` display cap is 100 rows in format.ts, NOT 500

`MAX_ROWS = 500` (constants.ts) is the service-side row cap; `MAX_DISPLAY_ROWS = 100`
(format.ts) is the DISPLAY cap. The Markdown table shown in `content[0].text` is
capped at 100 rows. The full up-to-500 rows are in `structuredContent.rows`. The
display cap is separate from the service cap. This distinction matters when users ask
why they only see 100 rows in chat but the tool says `rowCount: 350`.

### 26k. Catalog model record counts — complete picture

All 14 catalog models and their record counts (from manifest.json):
```
CMMS: 872 records (787 with description)
Ent-Reporting-DTC: 400 records (389 with description)  [synthesized from CSV]
Ent-Reporting-Ecomm: 1,698 records (397 with description)
Ent-Reporting-Finance: 212 records (86 with description)
Ent-Reporting-Marketing: 1,105 records (278 with description)
Ent-Reporting-Ops-Finance: 1,723 records (1,001 with description)
Ent-Reporting-Profitability: 157 records (44 with description)
Ent-Reporting-Purchasing-Finance: 170 records (116 with description)
Ent-Reporting-Quality: 649 records (119 with description)
Ent-Reporting-SCM: 5,495 records (655 with description)  [largest model]
Ent-Reporting-SLM: 1,628 records (413 with description)
Ent-Reporting-Sales: 3,110 records (1,063 with description)
Ent-Reporting-Walmart: 2,525 records (920 with description)
OEE Monthly Reports: 939 records (793 with description)
Total: 20,683 records
```
Note: HR has NO catalog model (no field metadata). CMMS and OEE Monthly Reports ARE
in the catalog but LOW confidence in routing. DTC is in the catalog but NOT in newell-schema.json.

### 26l. nl-generator REDIRECT_PBI_USAGE — three patterns that redirect

Confirmed from `nl-generator.test.ts`: `kql_generate` redirects to `kql_field_usage`
or `kql_workspace_usage` (returning `REDIRECT_PBI_USAGE` with empty `kql: ""`) for:
1. Field/measure usage questions ("Invoice Quantity field last month")
2. Workspace adoption questions ("Enterprise Sales workspace adoption")
3. Direct `PowerBIDatasetsWorkspace` queries ("query PowerBIDatasetsWorkspace for...")

For our agent: if we ever add a NL-to-KQL feature, it must have an equivalent structural
redirect — these three patterns must NEVER go through general KQL generation. The test
case explicitly verifies `kql: ""` and checks that the explanation text references the
correct specialized tool by name.

### 26m. HR cohort flags are Function/Sub Function grouped, not Function only

`hr-loader.ts` builds cohort flags by `Function / Sub Function` combined key (or just
`Function` when Sub Function is null). Groups with fewer than 5 users get a CohortFlag.
The coverage check uses `coveragePct >= 75` exactly. The coverage percentage is computed
to ONE decimal place: `Math.round((matched / total) * 1000) / 10`.

The five unmatched users shown in the message: `unmatched.slice(0, 5).join(", ")` + "and N more". So
at most 5 unmatched users are named explicitly.

### 26n. Newell brand color tokens for visualizations (confirmed)

Confirmed from `kql_visualize` tool description and html-visualizer.ts context:
- Newell Blue: `#288FC2`
- Newell Navy: `#01405C`
- Body Gray: `#696158`
- Font: Arial throughout
- Chart: ECharts 5 from `jsdelivr.net` CDN (blocked on corporate networks — data
  table still renders, only chart affected)
- 7-color accent palette

### 26o. Two distinct chart auto-selection systems — the tool vs. the skill differ

The `kql_visualize` HTML tool (html-visualizer.ts) auto-selects:
- datetime + numeric → line chart
- ≤20 categorical values + numeric → vertical bar
- >20 categorical values + numeric → horizontal bar
- else → data table only

The `kql-visualizer` skill's `chart-type-guide.md` specifies:
- Has datetime → timechart
- 1 string + 1 numeric, ≤15 rows → piechart
- 1+ string + 1+ numeric → barchart
- 2+ numeric, no string → scatterchart
- Fallback → table

These are different systems. The skill's guide drives manual chart type selection;
the tool uses the simpler html-visualizer.ts logic automatically. A piechart (from the
skill) is never auto-selected by the tool. A scatterchart (from the skill) is never
auto-selected by the tool.

### 26p. `kql-visualize.md` "don't re-execute" rule — exact enforcement

Confirmed from the command file itself: "If query results from a prior kql_execute call
are available in this conversation, pass those rows and columns directly to kql_visualize
now. Do not re-execute the query." This is the efficiency pattern already noted in Part 21
but now confirmed with the exact wording from the source.

### 26q. Term resolver returns `connectionPath` in resolved result

`term-resolver.ts`'s resolved result includes:
```typescript
...(best.entry.connectionPath !== undefined ? { connectionPath: best.entry.connectionPath } : {})
```
So when a user resolves "Z.Sales", the result includes
`connectionPath: "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Sales"` which can be
used for Excel Analyze-in-Excel or DAX Studio connections. The Python port should include
this in the resolved result so the agent can surface XMLA connection instructions.

### 26r. `kql_execute` enforces `skip_audit` bypass only for CORRECT* errors

The execution gate in `kql_execute` blocks only on `severity === "error"` findings.
Performance WARNINGs never block execution. This is the right model for our `kql_guard.py`:
error-severity rules (CORRECT001, CORRECT007, PERF003 custom, etc.) should block; warnings
should be surfaced but not block.

### 26s. `resolveField` vs `resolveFieldUsage` — four statuses vs. five

- `resolveField` returns 4 statuses: `resolved`, `ambiguous`, `no_match`, `unavailable`
- `resolveFieldUsage` returns 5 statuses: adds `invalid_request` (builder rejected inputs)

The `invalid_request` status means the field was found but the caller passed a bad
`groupBy`/`timespan`/`topN`. This distinction is important for the Python port:
`no_match` = wrong field name; `invalid_request` = field found, query params wrong.

### 26t. `formatProvenance` is a first-class tool output — include in Python port

`formatProvenance(provenance: ProvenanceEntry[])` renders:
```
Provenance (every clause traced to an authoritative origin):
  [resolver] EventText contains "'Invoice Sales'[Invoice Quantity]" or ...
      ↳ embedded verbatim from resolver output (branded AuthoritativeFilter)
  [builder-derived] | where TimeGenerated > ago(30d)
      ↳ timespan '30d' validated to KQL duration form
```
This is returned in `kql_field_usage` and `kql_workspace_usage` responses. Our Python
port should include equivalent provenance output — it's not optional decoration, it's
the audit trail that confirms no improvised filters were introduced.

---

## PART 25: Remaining genuine gaps — found by reading all logic, JSON, and enforcement files

This part covers what was still missing after Part 24. Two items in Part 24 also need
corrections (24c and 24d); those corrections are stated here so Part 24 is not silently wrong.

### 25a. CORRECTION to Part 24c — PowerBIDatasetsWorkspace is NOT in HIGH_VOLUME_TABLES

Part 24c stated: "PERF003 applies to PowerBIDatasetsWorkspace." This is wrong.
`constants.ts` defines `HIGH_VOLUME_TABLES` as App Service, Container, Azure Monitor, VM,
and Networking tables. `PowerBIDatasetsWorkspace` is NOT in that set. PERF003 fires only
for tables in that set. The rule for PowerBIDatasetsWorkspace is CORRECT007 (hand-authored
EventText filter, already correctly stated in Part 21/24c).

The spirit of Part 24c's recommendation is still right: validate time filters on
`PowerBIDatasetsWorkspace` queries in `kql_guard.py`. But this is a CUSTOM check, not
PERF003. `WORKSPACE_RETENTION_DAYS = 60` (confirmed in constants.ts) is the bound;
queries with timespans > 60d should warn that the table's retention won't cover it,
the same way usage-query-builder.ts emits a `retentionWarning` on those queries.

- [ ] Add `PowerBIDatasetsWorkspace` to a separate `POWER_BI_TABLES` set in the Python
      port of the guard. Apply two checks: (a) time filter required (same as PERF003 for
      high-volume tables), (b) timespan > 60d emits a retention warning, not an error.

### 25b. CORRECTION to Part 22 / 24d — Build scripts need source files NOT in the zip

Part 22 said "port both build scripts to Python" and Part 24d confirmed the data is present.
Both are partially correct but need a critical correction:

**`generate-schema.cjs`** reads from `C:\Users\HJ45676\Downloads\DAX Queries for Schema\`
(Ent-Reporting-*.xlsx files). These Excel files are NOT in the zip. The pre-built
`data/newell-schema.json` (output) IS present and usable now.

**`build-field-catalog.cjs`** reads from two local directories:
- `C:\Users\HJ45676\Downloads\Gapped Dims\` and
- `C:\Users\HJ45676\Downloads\Dim Catalog\`
  containing Dim Catalog CSV files (Z.DTC Data Dictionary.csv, Z.Sales Data Dictionary.csv,
  Z.eComm Data Dictionary.csv, Z.OpsFin Data Dictionary.csv, Z.Walmart Data Dictionary.csv,
  Z.Finance Dimension Catalog.csv, Z.SCM Dimension Catalog.csv, etc.)
  These CSVs are NOT in the zip. Ent-Reporting-DTC is synthesized ENTIRELY from
  `Z.DTC Data Dictionary.csv` — without it, the DTC model would have zero catalog records.

The pre-built `data/catalog/` directory (output) IS complete and correct as-is.

**Revised approach:**
- [ ] Use the pre-built outputs directly: `data/newell-schema.json` + `data/catalog/`.
      Do NOT attempt to rerun either build script without first locating the source files.
- [ ] The build scripts' value is for REFRESH when Grant provides new Excel files or the
      Dim Catalog CSVs are updated. Port them to Python for that future use, but flag that
      the DIM CATALOG CSV files need to be located separately (same person who maintains
      the Gapped Dims / Dim Catalog folders at the default paths in the script).
- [ ] Note `MODEL_MAP` in build-field-catalog.cjs (short names → canonical names) MUST
      stay in sync with `routing-table.ts` `catalogModelName` fields. The build script
      explicitly calls this out: "KEEP IN SYNC with routing-table.ts catalogModelName
      fields; the build FAILS on an unmapped catalog model rather than guessing."

### 25c. The field-resolver has 4 passes, not 3 — correct the port plan

Part 21 described "three passes." The actual resolution strategy in `field-resolver.ts` is:

- **Pass 1:** Exact normalized match — O(1) index lookup.
- **Pass 1b:** Alias expansion — `expandFieldAliasVariants()` from `field-aliases.ts`:
  ALIAS_MAP (35 abbreviation/synonym entries: `qty`→`quantity`, `amt`→`amount`,
  `cust`→`customer`, `inv`→`invoice`, `pct`→`percent`, `rev`→`revenue`, etc.) +
  trailing-s pluralization strip (>3 chars, not ending in `ss`).
- **Pass 1c:** Schema-linking — `findLinkedFieldNames()` from `schema-link.ts`: tokenizes
  user input and looks up matching field name STRINGS from the catalog token index. Each
  name is re-verified against the production INDEX; catalog-only names not in INDEX are
  discarded, never fabricated. Capped at 20 candidates.
- **Pass 2:** Containment fallback — raw containment of normalized input inside field names,
  minimum 3-char input, capped at 20.

Disambiguation order after any pass finds multiple candidates:
  1. `model_hint` (from prior `kql_resolve_term`) → narrows to one model → HIGH confidence.
  2. Measure preference → if only one candidate is a measure, prefer it → MEDIUM confidence.
  3. Still ambiguous → return all candidates + a `combinedKqlFilter` covering every model.

The `combinedKqlFilter` is an `AuthoritativeFilter`-branded type built from the FULL
candidate list (not the display-capped 10), so an ambiguous all-models search is always
complete, never silently truncated.

- [ ] The Python port of `field-resolver.py` must implement all 4 passes and the
      disambiguation order exactly. Skipping 1b or 1c would break "qty" → "quantity" and
      multi-word business term matching (e.g. "business group" → real field names).
- [ ] Port `field-aliases.ts`'s ALIAS_MAP and pluralization rule as a Python dict/function.
      The 35 entries are: qty, amt, cust, custs, qtys, amts, desc, descr, num, nbr, addr,
      invc, inv, ord, qy, pct, avg, tot, disc, vend, whs, wh, sku (identity), rev, mgr,
      dept, qtr — and their pluralized forms.
- [ ] `normalizeForMatching()` is the shared normalization function used by BOTH
      `term-resolver.ts` AND `field-resolver.ts`. Port as a single shared Python function:
      lowercase → replace all non-alphanumeric runs with single space → trim.
      Both resolvers must use the SAME function or normalization will drift.

### 25d. SafeUsageColumn enum — DatasetName removal and its implications

The `SAFE_USAGE_COLUMNS` in `usage-query-builder.ts` are the ONLY columns allowed in
groupBy or equality-filter positions for PowerBIDatasetsWorkspace queries:
```
["ExecutingUser", "ArtifactName", "PowerBIWorkspaceName",
 "PowerBIWorkspaceId", "OperationName", "ApplicationName"]
```
`DatasetName` was EXPLICITLY REMOVED after confirming it does not exist on the live
table (ArtifactName is the correct column). The build was validated by `getschema` against
`ent-aas-workspace-prd` on 2026-07-28.

- [ ] Check every KQL query in `query_library.json` and every generated query in
      `collector_log_analytics.py` that references `PowerBIDatasetsWorkspace` — confirm none
      uses `DatasetName`. If any does, replace with `ArtifactName`.
- [ ] The Python port of the query builder must implement the same column allowlist.
      The rejection pattern is: if groupBy contains any column not in the safe set,
      return an error rather than guessing or silently dropping the column.
- [ ] When building PowerBIDatasetsWorkspace queries for user-supplied values (e.g. a
      specific user's email), use the KQL string-escaping rule from usage-query-builder.ts:
      escape `\\` first, then `"`. Order matters — reversing it corrupts the escaping.

### 25e. workspace-client.ts error-conflation fix — the exact pattern

Part 21 mentioned "the error-conflation bug" as a pattern to check in our collectors.
Here is the actual fix from `workspace-client.ts`:
```python
# The pattern to follow:
except Exception as e:
    msg = str(e)
    if re.search(r'sem0100|semantic error|failed to resolve table|'
                 r'entitynotfound|badargument', msg, re.IGNORECASE):
        return None   # table genuinely not found
    raise           # auth/network/throttling/timeout — must propagate
```
The old (wrong) code swallowed ALL errors as "table not found," causing an expired auth
token to be misreported as a missing table — sending investigation down the wrong path.

- [ ] Apply this exact pattern in `collector_workspace_monitoring.py`,
      `collector_log_analytics.py`, and `collector_rest.py`. Check each file's
      `except Exception` blocks against the STANDING RULE's step 6: same bug class,
      check all three files, not just the one where it was first found.
- [ ] The regex covers Kusto semantic errors; ALL other exceptions (network, auth,
      throttling, timeout, unexpected HTTP status) must propagate. Do not broaden
      the catch clause to swallow more.

### 25f. Routing table: 15 models, not 13 — HR and OEE present in routing but not schema

The routing-table has 15 canonical model entries:
  CMMS, Ent-Reporting-DTC, Ent-Reporting-Ecomm, Ent-Reporting-Finance,
  **Ent-Reporting-HR**, Ent-Reporting-Marketing, Ent-Reporting-Ops-Finance,
  Ent-Reporting-Profitability, Ent-Reporting-Purchasing-Finance, Ent-Reporting-Quality,
  Ent-Reporting-SCM, Ent-Reporting-SLM, Ent-Reporting-Sales, Ent-Reporting-Walmart,
  **OEE Monthly Reports**

`newell-schema.json` has 13 models (no DTC entry because DTC is synthesized from CSV;
no HR entry because HR has no field-pattern schema in the Excel exports).

Implications:
- `Ent-Reporting-HR` exists in the routing table (can be resolved) but has no field
  resolver schema — `resolveField` with `modelHint="Ent-Reporting-HR"` will return
  `no_match` for every field. This is by design: HR data is enriched via `kql_enrich_hr`
  (HCMIF0485_IDT_DASHBOARD.xlsx), not searched via EventText field patterns.
- `Ent-Reporting-DTC` exists in `data/catalog/models/Ent-Reporting-DTC.json` (400
  records from Z.DTC Data Dictionary.csv) but NOT in `newell-schema.json` because its
  source data comes from the CSV, not the Grant Excel format.
- The Python port must handle the HR and DTC cases explicitly: HR → redirect to HR
  enrichment; DTC → catalog records exist, field patterns exist (400 records), but
  they're in the catalog, not the schema JSON.

### 25g. HR loader: full column schema, UPPERCASE email join, and refresh cadence

`hr-loader.ts` confirmed details not previously documented:

**Full column schema** (29 columns, not the 6 mentioned in Part 21):
User ID, Last Name, First Name, Function, Sub Function, Business Unit, Personnel Area,
Personnel Area Description, City, State/Province, Country, Region, Position Title,
Latest Hire Date, Email Address, Employee Group, Employee Subgroup, Status, HRBP Admin,
Manager User Id, Manager Full Name, Manager Position, Work Location Code,
Work Location Address, Work Location City, Work Location State,
Rep. Function Level 1, Rep. Function Level 2, Rep. Function Level 3.

**Email join key**: HR file stores emails in UPPER CASE. Log Analytics ExecutingUser
values are typically mixed-case UPNs. Both sides must be lowercased before joining or
the join finds nothing.

**Refresh**: manual file replace. Graph API is structurally blocked (AADSTS65002 —
Azure CLI client not preauthorized for Files.* delegated scopes; not fixable by tenant
admin). M365 MCP connector also confirmed not viable (HTTP 406). The file is read
from disk with a 1-hour TTL cache; no auth, no network call.

**Coverage gate exact values**: `HR_LOW_COHORT_THRESHOLD = 5` (groups with fewer than
5 users get a low-sample annotation) and `HR_MIN_COVERAGE_PCT = 75` (below this,
statistics must not state percentages, only list matched users).

- [ ] If our agent ever enriches user attribution with HR metadata, use the local-file
      pattern (read HCMIF0485_IDT_DASHBOARD.xlsx from `data/`) rather than attempting
      a Graph API call or M365 MCP connector call — both are confirmed dead paths.
- [ ] Lowercase both sides before joining on Email Address.
- [ ] The 6 columns Part 21 listed (Function, Sub Function, Business Unit, Region,
      Manager, Work Location) are the display columns. The join key is Email Address.
      The full 29-column schema is available if deeper HR context is ever needed.

### 25h. routing-table.ts versioning and the MODEL_MAP invariant

Two structural details not in Parts 21-24:

**Version exports**: routing-table.ts exports `TABLE_VERSION = "2.1.0"` and
`LAST_REVIEWED = "2026-07-30"` as machine-readable constants. Per Part 21's note that
the table is "review-controlled," the Python port should maintain equivalent versioning
— a `TABLE_VERSION` and `LAST_REVIEWED` string at the top of `routing-table.py`, not
just a comment.

**MODEL_MAP invariant**: build-field-catalog.cjs explicitly states:
> "KEEP IN SYNC with routing-table.ts catalogModelName fields; the build FAILS on an
> unmapped catalog model rather than guessing."

The Python port must enforce the same invariant: if a catalog model name doesn't appear
in MODEL_MAP, fail loudly (raise), never silently drop or guess the canonical name.
This prevents the catalog and routing table from drifting apart silently.

---

Before marking ANY item above complete: re-read the STANDING RULE near the top of this file.
Every single fix, without exception, requires the before/after review it describes — identify
callers, check for the same pattern elsewhere, re-verify manually, report what was checked. A
green test suite alone is not sufficient evidence that a fix is complete or that it didn't
break something adjacent.

Report back at each phase with what was found, not just what was changed. Do not mark
anything done without confirming it against real data, the way this file's Part 0 evidence
was confirmed against a real day's worth of production tickets, not assumed from reading code
alone.
