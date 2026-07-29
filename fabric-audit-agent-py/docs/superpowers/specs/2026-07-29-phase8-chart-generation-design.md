# Design: Phase 8 — Chart / Graph Generation

**Date:** 2026-07-29
**Depends on:** Phase 7 (needs a query result to chart)
**Must enforce:** the true-CU/proxy boundary established in `tasks/plan.md`'s Architecture
Decisions — this is the one rule that must not silently erode when data becomes visual instead of
textual.

## Grounded in What Actually Exists

Checked the real frontend (`fabric-audit-agent-app/e2e-chatbot-app-next/client/src`) before
designing this, per the brainstorming skill's "explore project context" step. Findings:

- **No chart-rendering component exists yet** — but the design system already anticipates one:
  `BarChartIcon`, `PieChartIcon`, `ChartLineIcon`, `BarGroupedIcon`, `BarStackedIcon`,
  `BarStackedPercentageIcon`, `TrendingIcon` are all already present in `components/icons/`.
- **`databricks-message-part-transformers.ts`** already exists — this is the extension point that
  turns custom message parts (tool outputs, citations) into rendered React components. A chart is
  a new message-part type through this same mechanism, not a bolt-on.
- **`elements/tool.tsx`** already renders structured tool-call output (this is also what UX1 in
  the gaps doc wants extended into check-cards). A chart tool's output flows through the same
  family of components.
- This is a custom Databricks-hosted app, not a Claude.ai artifact context — charts render as
  real React components in this app's own UI, not as a Claude-style Visualizer output.

**Conclusion: this is filling a real, anticipated gap in an existing extension point, not building
a chart system from scratch.**

## Three Approaches Considered

**A — Agent returns a structured chart spec (data + chart type); frontend renders it with a
charting library.** No image generation, no server-side rendering step. The agent's tool output is
just data + a type hint; the React app does the actual drawing.

**B — Agent renders a static image (server-side, e.g. matplotlib) and returns an image
reference.** Works, but throws away interactivity (hover, zoom) the existing chat UI's other rich
elements (like `code-block.tsx`) already support, and adds a rendering dependency on the Python
side that doesn't need to exist given the frontend is already React.

**C — Agent calls out to an external charting service/API.** Unnecessary complexity for a
Databricks-hosted app with its own React frontend already in place.

**Recommendation: A.** It's the natural fit for the existing architecture (message-part
transformers, a React app that already has chart iconography waiting for a component), it's
interactive, and it avoids introducing a server-side rendering dependency the Python backend
doesn't currently have.

## Architecture

**New MCP/direct tool: `render_chart`** (naming TBD at implementation time), called by the agent
after it has query results (from Phase 7 or an existing structured tool) it wants to visualize.
Tool output shape:
```json
{
  "chartType": "line" | "bar" | "grouped-bar" | "stacked-bar" | "pie",
  "title": "string",
  "series": [{"name": "string", "data": [{"x": ..., "y": ...}]}],
  "axisLabels": {"x": "string", "y": "string"},
  "sourceScope": "capacity" | "item" | "user",
  "isProxy": true | false
}
```

**`sourceScope` and `isProxy` are not optional decoration — they are the enforcement mechanism for
the true-CU/proxy boundary.** Any chart whose data traces back to `CpuTimeMs`/Workspace Monitoring
must set `isProxy: true`; any chart at the `user` or per-operation scope must default to
`isProxy: true` unless explicitly proven otherwise (mirrors the existing text-response discipline
of always attaching the proxy caveat to per-user figures). The frontend component renders a visible
badge/footnote when `isProxy` is true — the same honesty rule the text responses already follow,
enforced structurally in the chart's own data contract so it can't be silently forgotten the way a
prose caveat could be.

**Frontend component (new):** a `chart.tsx` in `components/elements/`, following the existing
pattern of `code-block.tsx`/`tool.tsx`, registered in `databricks-message-part-transformers.ts`
alongside the existing part types. Uses a React-idiomatic charting library (recharts is a
reasonable default — confirm against the app's actual `package.json` dependencies at
implementation time rather than assuming).

**Never blend scopes on one chart.** A chart comparing an item's users against the capacity's
total users (the exact anti-pattern the system prompt already warns against in text) must be
rejected at the tool level, not left to the LLM's judgment at chart-authoring time — `sourceScope`
must be singular and consistent across all series in one `render_chart` call.

## Data Flow

```
Agent has query results (from Phase 7, or an existing capacity/concentration tool)
   → decides a chart would help (or the user explicitly asks for one)
   → calls render_chart with sourceScope + isProxy set correctly for the data's actual origin
   → tool output streams to the frontend as a new message part
   → databricks-message-part-transformers.ts routes it to the new chart.tsx component
   → chart.tsx renders with the isProxy badge visible if set
```

## Error Handling

- Empty or single-point data: don't render a chart that would visually mislead (e.g., a bar chart
  with one bar looks like a KPI card, not a trend) — fall back to a plain-text answer for
  genuinely thin data rather than forcing a chart.
- A `sourceScope` mismatch across series (caught at the tool level) should raise, not silently
  render a misleading combined chart.

## Guardrail Tie-In

This phase is the second place (alongside Phase 7) where an existing textual honesty rule needs to
be re-implemented as a structural constraint rather than trusted to survive as prose guidance. Do
not consider this phase done until there's an explicit test confirming a proxy-sourced chart
renders its badge — not just that the chart renders at all.

## Open Item for Implementation

Confirm the actual charting library already available in `package.json` before committing to
recharts specifically — this design assumes it's available or easily added, not confirmed against
the real dependency list yet.
