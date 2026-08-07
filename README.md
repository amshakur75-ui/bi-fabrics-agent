# Fabric Audit Agent

A read-only auditor for Microsoft Fabric and Power BI capacity. It watches a Fabric capacity,
finds the real problems, explains why they happened, says who caused them, and reports what to do.
It is written in Python and runs on Databricks.

The agent only reads. It never changes, refreshes, resizes, or writes anything back to Fabric,
Power BI, or Azure. The only things it sends outward are its findings and notifications.

## What it does

The agent answers the questions a Power BI or capacity team asks every day:

- Is the capacity healthy right now, or is it in trouble?
- What went wrong today, and why?
- Who ran the expensive work, and on which report or dataset?
- Is this a new problem or something that keeps coming back?
- What should we fix first?

It reaches these answers on its own. It pulls the telemetry, runs a set of checks, decides on a
verdict, and writes up the findings. A person can also chat with it and ask follow-up questions.

## How it works

There are three parts, all running in Databricks:

1. A scheduled job that audits the capacity on a timer and stores its findings.
2. An MCP tool server that exposes the audit as read-only tools.
3. An agent and a web chat app that a person talks to. It calls the same tools.

The audit itself follows one path: collect, detect, decide, report.

- **Collect.** Collectors read telemetry from Fabric and Power BI: the Admin REST APIs, the capacity
  event stream, and Log Analytics query events. Each collector returns plain data. If one source is
  down or not configured, the others still run, and the missing one is recorded as a gap.
- **Detect.** The collected data goes through a set of detectors. Each detector looks for one kind of
  problem and returns findings.
- **Decide.** The findings feed a capacity verdict: healthy, optimize, or size up. The verdict comes
  with the evidence behind it.
- **Report.** Findings go to the notification center, a daily summary, and a chat answer when a person
  asks. Each finding carries what happened, where, who, and how sure the agent is.

## What it detects

**Capacity state.** Throttling, sustained pressure (CU% over 100), and overage that is burning down.
These are the signals that the capacity itself is in trouble.

**Expensive activity.** A single operation that runs too long or costs too much on its own. A query
shape that keeps coming back across days and users, which points at a report or model design problem
rather than one person. A cluster of long-running queries against the same item.

**Query anti-patterns.** Costly DAX patterns like nested iterators and whole-table filters, and the
heavy MDX cross-join shape that matrix visuals produce.

**Refresh failures.** Dataset refresh problems, sorted by cause: bad credentials, gateway trouble,
source timeouts, concurrency limits, and constraint errors. Chronic failures and retry storms are
flagged on their own.

**Connection errors.** XMLA and connection-level failures, with benign transient states filtered out
so they never turn into a false alarm.

**Concentration.** When one item or user drives a large share of the capacity, the agent uses that as
a reason to investigate the item and report the real cost behind it.

## Attribution and honesty

The agent is careful about what it claims.

Capacity CU is the ground truth for whether the capacity is in trouble. Log Analytics activity
answers who ran what and why. The two are kept separate. The agent does not blend a per-user activity
number into a capacity percentage and present it as capacity cost.

Some numbers come from a CPU-time proxy (`CpuTimeMs`), not from metered capacity units. The agent
labels those as a proxy so they are never mistaken for billed cost.

When a data source is not available for a window, the agent says so instead of showing an empty
result as if nothing happened. If the reported SKU disagrees with the live capacity base, it raises a
mismatch flag rather than computing percentages against a wrong number.

## Ad-hoc queries and the firewall

When no fixed tool answers a question, the agent can write and run its own read-only query. Every
query passes three gates before a row comes back:

1. A static check rejects writes, control commands, multiple statements, cross-database and
   cross-cluster escapes, and a set of dangerous operators.
2. A rehearsal runs the query with `take 0` against the real engine, so a bad table or column fails
   on the engine's own error before anything real runs.
3. A bounded run executes the query with a hard row cap.

A rule engine scores the query for correctness and performance and gives it a grade. There is also a
library of pre-written, grounded query templates. If a result has more than fifty rows, the agent
stops and offers to summarize, filter, take the top N, or proceed, so a large pull does not dump
thousands of rows at once.

## Business terms and the resolve layer

Users ask about business terms, not table columns. The resolve layer maps a term like "Invoice
Quantity" to the actual field and the model it lives in. From there the agent can build a usage query
that finds who used that field, without the user or the agent hand-writing a fragile filter. This
layer is backed by the Newell schema and field catalog.

## Alerts and memory

A lighter check runs every five minutes with no model calls. It watches the capacity signals
(throttle, pressure, overage) and trips an alert when one fires.

Alerts go through a state machine so they do not become noise:

- Repeated alerts for the same incident update the existing ticket instead of making a new one.
- An attribution signal has to persist across several checks before it alerts.
- An open incident sends a reminder after 48 hours.
- A worsening incident escalates.
- A resolved incident that comes back reopens itself.
- A person can resolve or acknowledge an incident.

Findings appear in the app's notification center. A daily summary at 6pm lists the real problems by
category, ranks the day's heaviest users, keeps refreshes in their own section, and mentions capacity
only as a one-line note. Each alert has a link that starts a live investigation when clicked. If a
collector or a delivery fails, a health banner says so instead of failing silently.

The agent stores run history and findings in Delta tables, so it can compare against past runs and
tell whether a problem is new, recurring, or already fixed.

Outbound delivery to Microsoft Teams is built but turned off. It is planned for a later phase.

## Reports and charts

The agent can render an in-chat chart, and it can export a result as a branded HTML report or an
Excel workbook. Exports are generated on the server and returned as a download. Nothing is written to
a user's local disk.

## The chat app

The web app is a Next.js chatbot. It streams answers, renders tables and charts, shows a read-only
KQL viewer with a copy button, and has a notification center with a resolve flow. Answers carry a
confidence badge and a marker for whether a number is true capacity CU or a proxy.

## Repository layout

```
fabric-audit-agent-app/     The core Python package and the agent
  fabric_audit_agent/       Collectors, detectors, investigation, verdict, tools, config
  agent_server/             The agent loop, system prompt, tools, chart and export helpers
  e2e-chatbot-app-next/     The Next.js chat app (client, server, database)
  tests/                    The Python test suite
  data/plugin/              Newell schema, field catalog, and artifact map (read by the resolver)
  scripts/                  SQL migrations for the Delta and Lakebase tables
fabric-audit-mcp/           The MCP server that exposes the tools over MCP
```

## Running it

The core and its offline test doubles need only the Python standard library. Everything runs without
Fabric credentials, using mock data.

```bash
cd fabric-audit-agent-app
pip install -e ".[dev]"
python -m pytest -q          # run the full test suite
python run.py audit          # run a full audit against mock data
```

For a real audit, the collectors read live telemetry when the Fabric and Log Analytics environment
variables are set. See `fabric-audit-agent-app/DEPLOYMENT.md` for the Databricks setup and
`fabric-audit-agent-app/PERMISSIONS.md` for the access the service principal needs.

## Documentation

- `docs/how-it-works.md` explains the code and the audit flow for a new reader.
- `fabric-audit-agent-app/DEPLOYMENT.md` covers deploying to Databricks.
- `fabric-audit-agent-app/PERMISSIONS.md` covers the required access.
- `fabric-audit-mcp/MCP-AGENT.md` describes the MCP server and the tool list.
