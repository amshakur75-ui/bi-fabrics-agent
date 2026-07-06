# MCP Harvest Inventory — what we absorb, from where (audited 2026-07-06)

Verdict basis: every useful MCP here is a wrapper over a public API we already call (REST/KQL/SQL),
so features are absorbed into OUR MCP/adapters — we run no third-party servers. Write/authoring
features are never ported (read-only posture). Everything below was verified against the actual
source, not READMEs.

## 1. `microsoft/fabric-rti-mcp` (MIT) — the main harvest [Phase 4]

Audited: `services/kusto/*` (service 1,156 lines, formatter 380, connection 34, config 215).
`eventstream/activator/map` services NOT audited (authoring-oriented; out of scope).

**Port into the P4 firewall / our MCP (read-side only):**
- `_crp()` (kusto_service.py:344) — ClientRequestProperties builder that sets
  **`request_readonly` + `request_readonly_hardline` on every non-destructive call** and
  **refuses caller overrides of those keys** (`_BLOCKED_CRP_KEYS`, :319). This is the
  production-validated form of our firewall's primary KQL control. Also: `servertimeout`
  parsing (HH:MM:SS → timedelta, :327) and a **per-call `client_request_id`**
  (`KFRTI_MCP.{action}:{uuid}`, :349) — the audit-trace pattern for `.show queries`; adopt with
  our own prefix.
- `kql_escape_entity_name` / `kql_escape_string` / `_validate_no_escape_chars` (:177-230) —
  KQL identifier/string escaping (stronger than our current quote-stripping).
- `_find_first_statement` (:210) — first-statement extraction = single-statement enforcement
  for the KQL firewall leg.
- `KustoFormatter` (kusto_formatter.py, full file) — 7 compact result formats with round-trip
  `parse()`; **`columnar` / `header_arrays` cut token cost heavily** vs per-row JSON dicts
  (no repeated keys). Adopt for large event-list tool outputs.
- Schema/discovery tools: `kusto_list_entities` (:687), `kusto_describe_database` (:750),
  `kusto_describe_database_entity` (:775), `kusto_sample_entity` (:837, `| sample N`) — the
  schema-grounding + table-allowlist feeders for the firewall; sampling helps the agent
  understand a table before querying.
- `kusto_show_queryplan` + `_extract_physical_plan_hints` (:1059, :994) — query-plan retrieval
  + cost hints = a concrete implementation seed for the P4 **query-cost guardrail** (estimate
  before execute).
- `kusto_get_shots` (:917) — few-shot example retrieval for queries; **their version of our
  verified-query-library**; review its shape when building ours.
- Deeplink builders (`_build_adx_deeplink` :95, `_build_fabric_deeplink` :125) — generate a
  clickable ADX/Fabric web link that reruns the exact query. Trust feature: every figure the
  agent quotes can carry a "verify in Fabric" link.

**Never port:** `kusto_command` (destructive), `kusto_ingest_inline_into_table` (write),
`destructive_operation` paths, eventstream/activator/map authoring.

## 2. `microsoft/azure-devops-mcp` (MIT) — reference only [P5]
Endpoint/auth reference for OUR minimal ADO client when the ticketing + change-correlation leg
is approved: work-item create/query (rides existing `adapters/ticketing.py`), plus
`_apis/build/builds`, `_apis/release/releases`, `_apis/git/.../pullrequests` reads
("what deployed right before the spike"). We do not run the server (its surface = all of ADO).

## 3. Azure MCP Server (`microsoft/mcp`) — pattern reference [P4]
Its Monitor tools (workspace KQL query, **table list + schema discovery**, resource-scoped
query) mirror what we built; harvest only the table-list/schema-discovery UX for the firewall's
allowlist building. Nothing else in scope.

## 4. Fabric MCP Server (`microsoft/mcp`) — endpoint reference [P4/backlog]
Official wrapper over `api.fabric.microsoft.com` REST (same API as `collector_rest`). Use as the
endpoint reference for the **capacity-name→id resolver** backlog item.

## 5. FabricIQ (hosted MCP wired in `skills-for-fabric/.mcp.json`) — nothing to absorb [P5-if-ever]
`https://api.fabric.microsoft.com/v1/mcp/fabricaihub/...` — a Microsoft-hosted service (no
source). Would be federation under user identity + Fabric AI Hub enablement; overlaps our own
agent's job. Parked.

## 6. fabric-toolbox MCPs [P5]
- `DAXPerformanceTunerMCPServer` — an optimization WORKFLOW engine (not an API wrapper):
  the one case where absorb-vs-federate is decided at approval time (needs XMLA execute).
- `SemanticModelMCPServer` — semantic-link/XMLA model reads → a collector when model-internals
  access is approved.
- `MicrosoftFabricMgmtMCPServer` — management/write. Never.

## Standing rules applied to every harvest
Line-by-line security review before adaptation; external text = untrusted input; MIT license
attribution in adapted-file docstrings; `fabric-rest-api-specs` is license-NOASSERTION →
consult only, never vendor.
