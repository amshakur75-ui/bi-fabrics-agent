# How it works

This walks through the code for someone reading it for the first time. It follows the same order the
data does: from raw telemetry to a finished report.

Start in `fabric-audit-agent-app/fabric_audit_agent/`. That package is the whole audit. Everything
else (the MCP server, the chat app) is a way to call into it.

## The shape of the code

The core is pure. It takes data in, returns data out, and does no input or output of its own. All the
real world (HTTP, files, the database, the model) is passed in as a small set of functions called
ports. This is why the same logic runs offline with mock data and in production with live data:
only the ports change.

`pipeline.py` is the top of the audit. Its `run_audit()` takes a collector, a reasoner, and a
delivery target, and runs the flow end to end.

## Step 1: collect

Collectors live in `adapters/`. Each one reads one source and returns plain data.

- `collector_rest.py` reads the Fabric and Power BI Admin REST APIs (workspaces, datasets, refresh
  history).
- `collector_capacity_events.py` reads the capacity event stream, which carries CU%, throttle, and
  overage.
- `collector_events_la.py` reads Log Analytics query events, which carry who ran what, how long it
  took, and the query text.
- `collector_merge.py` runs several collectors together and merges them into one `facts` dictionary.
  If a collector fails, it records the gap in `sourcesFailed` and keeps going.

The mock versions (`collector_mock.py`, and the fixtures in `fixtures/`) let the whole thing run with
no credentials. That is what the tests use.

The result of this step is one dictionary, `facts`, with keys like `capacity`, `items`, `refreshes`,
and `events`.

## Step 2: detect

Detectors live in `detectors/`. `detectors/__init__.py` has `detect_all()`, which runs every
detector over `facts` and collects their findings. Each detector is wrapped so that if one fails, the
others still run.

Each detector looks for one kind of problem:

- `capacity.py` reads the throttle, pressure, and overage state and drives the verdict.
- `concentration.py` finds an item that takes a large share of the capacity.
- `refresh.py` finds refresh failures and sorts them by cause.
- `absolute_cost.py` flags a single operation that ran too long or cost too much.
- `query_shape.py` finds a query shape that repeats across days and users.
- `query_antipatterns.py` finds costly DAX and MDX patterns.
- `long_running.py` finds a cluster of slow queries on one item.
- `xmla_errors.py` finds connection and XMLA errors, and ignores benign transient ones.
- `model.py`, `report.py`, `pipeline.py`, `security.py`, `cost.py`, `cross_workspace.py`, and
  `blast_radius.py` cover model design, report design, pipeline health, access, cost, and
  cross-workspace patterns.

A finding is a dictionary with a `type` (like `activity.slow-operation`), a `resource`, some
`evidence`, and a plain-English `what`.

The events that the activity detectors read (`facts["events"]`) are attached during collection by
`job.py`, so the detectors run against real query data during a sweep.

## Step 3: decide

`severity.py` gives every finding a level: Info, Warning, or Critical. This matters because the
delivery layer only sends findings at Warning or above, so the level decides what a person sees.

`verdict.py` reads the capacity findings and returns the overall verdict: healthy, optimize, or
size up, with the evidence behind it.

`investigation/` holds the deeper analysis the agent uses when it digs into a problem: throttle
decomposition, time-to-throttle forecasting, per-operation cost, and the query fingerprint that
groups repeated shapes.

## Step 4: report

`automation/` turns findings into notifications.

- `sweep_delivery.py` writes each finding to the alert store and the notification center, and a card
  when delivery is enabled. It filters out anything below Warning.
- `tier2_check.py` is the light five-minute check. It watches the capacity signals with no model
  calls and runs the alert state machine (`process_alerts`): dedup, hysteresis, reminders,
  escalation, reopen, and resolve.
- `daily_summary.py` builds the 6pm digest: real problems grouped by category, the day's top users,
  refreshes in their own section, and a one-line capacity note.
- `health.py` collects what failed during a run (a collector, a chat write, a delivery) and shows a
  banner when the run is degraded.

## The knowledge base and honesty rules

`kb/` holds the grounded facts the agent leans on: the verified metric formulas
(`metric_definitions.py`) and the remediation text for each finding type (`kb/refresh.py`,
`kb/query.py`, and so on). This is where "what does this finding mean and how do you fix it" comes
from.

The proxy rule lives across the code: any number derived from Log Analytics `CpuTimeMs` is labeled as
a proxy, and it is never mixed into a capacity percentage.

## Tools, the firewall, and the resolve layer

`tools.py` (`create_tool_definitions`) defines the read-only tools the agent and the MCP server both
use. Each tool is a function that takes an input dictionary and returns a result.

`query/` holds the ad-hoc query safety code:

- `firewall.py` validates an agent-written query, rehearses it with `take 0`, then runs it bounded.
- `kql_guard.py` is the low-level read-only check.
- `kql_audit_rules.py` scores a query against a set of correctness and performance rules and gives it
  a grade.
- `kql_format.py` formats a query for display.

`resolve/` maps business terms to data. `term_resolver.py`, `field_resolver.py`, and `catalog.py`
turn a name like "Invoice Quantity" into the real field and model, using the schema and catalog in
`data/plugin/`.

`export/` builds the HTML and Excel reports.

## The agent and the chat app

`agent_server/` is the agent brain. `agent.py` and `loop.py` are the async and sync versions of the
same tool loop, kept in step through the shared hooks in `loop_hooks.py`. `system_prompt.py` holds
the single copy of the system prompt. The agent calls the same tools from `tools.py`.

`e2e-chatbot-app-next/` is the Next.js chat app. The client renders the chat, tables, charts, the KQL
viewer, and the notification center. The server streams answers and serves the alerts API.

## Configuration

`config.py` holds the default thresholds: how slow an operation has to be to flag, how many times a
shape has to repeat, the concentration percentage, and so on. Detectors read these with a fallback to
the defaults, so a partial override never breaks.

## Where to start reading

- To follow one audit, read `pipeline.py`, then `detectors/__init__.py`, then `verdict.py`.
- To understand alerting, read `automation/tier2_check.py` and `automation/sweep_delivery.py`.
- To understand the tools, read `tools.py` and `query/firewall.py`.
- To run it, install the package and run `python -m pytest -q` and `python run.py audit`.
