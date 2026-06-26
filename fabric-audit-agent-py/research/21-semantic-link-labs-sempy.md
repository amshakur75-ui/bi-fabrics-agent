# 21 — semantic-link (sempy) + semantic-link-labs (sempy_labs) as an in-Fabric collector

Research date: 2026-06-23. Scope: evaluate the `semantic-link` (`sempy`) and `semantic-link-labs`
(`sempy_labs`) Python libraries as a programmatic collector for the **bi-fabrics-audit-agent**
(read-only Fabric/PBI capacity audit). Focus: programmatic VertiPaq Analyzer, Best Practice
Analyzer (BPA), DAX, TOM metadata, admin lists/scanner/activity, the auth/identity model, and
whether any of it works **outside Fabric** and **with a service principal (SP)**.

---

## TL;DR for the agent

- **Two libraries, one stack.** `sempy` (Microsoft's `semantic-link`, **pre-installed** in the
  Fabric Spark runtime) provides the primitives: `evaluate_dax`, `evaluate_measure`, `read_table`,
  `list_datasets/measures/relationships/...`, `FabricRestClient`, `PowerBIRestClient`,
  `FabricDataFrame`. `sempy_labs` (`semantic-link-labs`, community/Microsoft, **NOT pre-installed —
  must `%pip install`**) is the high-level layer: `run_model_bpa`, `vertipaq_analyzer`,
  `connect_semantic_model` (TOM), `refresh_semantic_model`, Direct Lake helpers, and the
  **`sempy_labs.admin`** module (Scanner API, activity events, tenant settings).
- **This is the single best programmatic path for VertiPaq + BPA + DAX + admin in one library** —
  it directly addresses every detector the agent needs (DAX anti-patterns, model size, refresh).
- **Service principal IS now supported (sempy ≥ 0.12.0, Sept 2025).** This is the key change vs.
  the `notebookutils.getToken('pbi')` limitation already researched: SP auth via
  `set_service_principal(...)` / `ClientSecretCredential` / `ServicePrincipalTokenProvider`
  unblocks `run_model_bpa`, `vertipaq_analyzer`, TOM connect, `evaluate_dax`, AND the
  admin/Scanner module — **with read-only admin APIs**. A handful of functions remain SP-blocked
  (`evaluate_measure`, `list_apps`, `read_table(mode='rest')`, `execute_tmsl`).
- **Outside Fabric: officially unsupported, practically partial.** Microsoft states semantic link
  is "supported only within Microsoft Fabric." `sempy_labs` REST/admin wrappers (which are just
  HTTP) tend to work from a local machine / Databricks if you supply a token; **XMLA-backed**
  functions (TOM, VertiPaq, `evaluate_dax`, most `list_*`) are the fragile part outside Fabric.
  **Do not design the agent to run these from Databricks as a supported path** — run the collector
  inside a Fabric notebook (SP-triggered) and ship results out.

---

## 1. `semantic-link` / `sempy` — install & runtime

**TITLE:** semantic-link-sempy · PyPI
**URL:** https://pypi.org/project/semantic-link-sempy/
- Package name on PyPI is `semantic-link-sempy`; import is `import sempy.fabric as fabric`.
- `sempy` is **pre-installed in the Microsoft Fabric Spark runtime** (no install needed in a
  notebook). To get SP support you may need to upgrade: `%pip install -U semantic-link` and check
  `sempy.__version__` (need ≥ 0.12.0). Python 3.10–3.12.
- **How it helps:** the agent's in-Fabric collector gets the DAX/metadata primitives for free.
- **Limit:** version skew — the bundled runtime image can ship an older `sempy` without SP support.

**TITLE:** What is semantic link? — Microsoft Fabric | Microsoft Learn
**URL:** https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-overview
- Semantic link bridges Power BI semantic models and Synapse Data Science in Fabric. Data is read
  from models via **XMLA**; the model must be in a workspace on **dedicated capacity (Premium or
  Fabric)** for XMLA-backed reads. **"Supported only within Microsoft Fabric."**
- **Limit (load-bearing):** the "only within Fabric" + "XMLA needs dedicated capacity" constraints
  are the two hard walls for any out-of-Fabric or Pro-only ambition.

---

## 2. `sempy.fabric` core — DAX, metadata, REST clients

**TITLE:** sempy.fabric package | Microsoft Learn (API reference)
**URL:** https://learn.microsoft.com/en-us/python/api/semantic-link-sempy/sempy.fabric?view=semantic-link-python

### DAX / measure evaluation
```python
evaluate_dax(dataset, dax_string, workspace=None, verbose=0, num_rows=None,
             role=None, effective_user_name=None, use_readwrite_connection=False,
             credential: TokenCredential | None = None) -> FabricDataFrame
# Read access. Arbitrary DAX query → FabricDataFrame. SP: SUPPORTED (pass credential=).

evaluate_measure(dataset, measure, groupby_columns=None, filters=None,
                 fully_qualified_columns=None, num_rows=None, use_xmla=False,
                 workspace=None, verbose=0, use_readwrite_connection=False,
                 credential=None) -> FabricDataFrame
# SP: NOT SUPPORTED (see §6). Use evaluate_dax instead for SP runs.
```
- **How it helps the agent:** `evaluate_dax` is the workhorse for DAX-based probes — run
  `INFO.VIEW.MEASURES()` / `INFO.*()` DMV-style DAX, `DISCOVER_CALC_DEPENDENCY`, RLS filter dumps,
  or any custom anti-pattern detector query against a live model. Returns a pandas-subclass.

### Metadata listing (all XMLA-backed unless `mode='rest'`)
```python
list_datasets(workspace=None, mode='xmla', additional_xmla_properties=None,
              endpoint=None, credential=None) -> DataFrame   # mode='rest' is SP-OK
list_tables(dataset, include_columns=False, include_partitions=False, extended=False,
            advanced=False, additional_xmla_properties=None, workspace=None,
            include_internal=False, credential=None) -> DataFrame
list_columns(dataset, table=None, extended=False, ..., workspace=None, credential=None) -> DataFrame
list_measures(dataset, additional_xmla_properties=None, workspace=None, credential=None) -> DataFrame
list_relationships(dataset, extended=False, calculate_missing_rows=False, ...) -> DataFrame
list_partitions(dataset, table=None, extended=False, ...) -> DataFrame
list_datasources(dataset, ...) -> DataFrame
list_hierarchies(...) ; list_perspectives(...) ; list_annotations(...) ; list_calculation_items(...)
get_roles(dataset, include_members=False, ...) -> DataFrame
get_row_level_security_permissions(dataset, ...) -> DataFrame   # RLS DAX filter expressions
```
- `extended=True` on `list_tables`/`list_columns`/`list_relationships`/`list_partitions` pulls
  **VertiPaq-derived size/cardinality stats** (these are the same numbers VertiPaq Analyzer reports).
- **How it helps:** complete model inventory for the size + relationship + RLS detectors without
  parsing a `.vpax`. `additional_xmla_properties` lets you pull any TOM property (e.g.
  `Model.DefaultMode`, `Partition.SourceType`) into the dataframe.
- **Limit:** most are XMLA-backed → require dedicated capacity, XMLA endpoint enabled, and (per MS
  docs) historically **ReadWrite** workspace access; SP support added in 0.12.0.

### Calc dependencies / data-quality
```python
get_model_calc_dependencies(dataset, workspace=None) -> Iterator[ModelCalcDependencies]
# Measure/column/calc-item dependency graph — directly feeds DAX-anti-pattern + blast-radius logic.

list_relationship_violations(tables: dict|list[FabricDataFrame], missing_key_errors='raise',
                             coverage_threshold=1.0, n_keys=10) -> DataFrame
# Data-quality: detects RI/coverage violations across FabricDataFrames.
```
- **Note:** the prompt's `find_dependencies` is the **semantic-functions / data-quality** family in
  the `sempy.functions` / `sempy.relationships` namespace (`find_relationships`,
  `list_relationship_violations`, `plot_relationship_metadata`). `get_model_calc_dependencies`
  above is the model-internal DAX dependency graph — the more useful one for an audit agent.

### REST clients (lowest-common-denominator, most portable)
```python
FabricRestClient(token_provider=None)      # Fabric public API (capacities, workspaces, items, LRO)
PowerBIRestClient(token_provider=None)     # Power BI API (datasets, reports, gateways, dataflows...)
# .get/.post/.patch/.delete(path, ...) -> requests.Response ; auto token acquisition in-Fabric.
```
- **How it helps:** when a typed wrapper is missing, the agent can hit any REST endpoint directly
  (e.g. `/admin/...`, refresh history, capacity metrics) through one authenticated client. Accepts a
  custom `token_provider` → this is the SP injection point (see §6) and the most likely thing to
  work outside Fabric.
- **Status:** marked Experimental.

### FabricDataFrame / plotting
- `FabricDataFrame` subclasses `pandas.DataFrame`, carrying `column_metadata` (table, column,
  dataset, workspace, data_type, data_category, description). Lets detectors reason over semantics.
- `plot_relationships(...)` (in `sempy.relationships`) renders the model relationship graph — useful
  for a human-readable blast-radius/diagram artifact, less so for headless audit.

**TITLE:** Read data from semantic models … using python — Microsoft Fabric | Microsoft Learn
**URL:** https://learn.microsoft.com/en-us/fabric/data-science/read-write-power-bi-python
- Canonical usage of `read_table`, `evaluate_measure`, `evaluate_dax`, `list_datasets`. Confirms
  read-write connection support via `use_readwrite_connection=True` for
  `evaluate_measure`/`evaluate_dax`/`execute_xmla`.
- **Limit:** `read_table(mode='rest')` and `evaluate_measure` are SP-blocked (see §6).

---

## 3. `sempy_labs` — install & high-level analyzers

**TITLE:** semantic-link-labs · PyPI
**URL:** https://pypi.org/project/semantic-link-labs/
- Install: `%pip install semantic-link-labs` (import `import sempy_labs as labs`). **NOT bundled in
  the Fabric runtime** — must be installed per-notebook or baked into a **custom Fabric
  Environment** (recommended for a scheduled agent). MIT license, maintained by Microsoft. Python
  3.10–3.12.
- Feature categories: Semantic Models (BPA, VertiPaq Analyzer, TOM, Direct Lake migration,
  backup/restore, refresh), Reports (report BPA, broken-visual detection, rebind), Lakehouses,
  Direct Lake, Admin & Capacity, API wrappers (Power BI / Fabric / Azure / Graph), **Service
  Principal Authentication**.
- **How it helps:** this is the one-stop programmatic layer that turns the agent's `.vpax`/manual
  detectors into live in-Fabric collectors.

**TITLE:** semantic-link-labs (GitHub repo + README)
**URL:** https://github.com/microsoft/semantic-link-labs
- README confirms `service_principal_authentication` context manager support for the **admin
  subpackage, the Azure API wrappers, and `connect_semantic_model`** (i.e., the three things an
  audit agent most needs). Helper notebook provided in the repo.

### Best Practice Analyzer (BPA)
**TITLE:** `_model_bpa.py` source — run_model_bpa
**URL:** https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_model_bpa.py
```python
run_model_bpa(dataset: str | UUID, rules: pd.DataFrame | None = None, workspace=None,
              export: bool = False, return_dataframe: bool = False, extended: bool = False,
              language: str | None = None, check_dependencies: bool = True, **kwargs)
```
- Runs the Tabular Editor-style BPA rule set against a **live** semantic model. `return_dataframe=True`
  → raw pandas DataFrame of violations (object, rule, category, severity) instead of HTML.
  `export=True` → appends to a delta table `modelbparesults` in the attached lakehouse (with
  workspace/dataset/capacity/timestamp metadata — perfect for trend storage).
  `extended=True` first runs VertiPaq annotations so size-aware rules fire. `rules=` accepts a
  **custom rules DataFrame** → the agent can ship its own anti-pattern rule pack.
- `run_model_bpa_bulk(...)` — runs BPA across **many models / all workspaces** and writes results to
  a lakehouse delta table; designed for tenant-wide scans. (This is the function GitHub issue #171
  flagged as needing SP — now addressable via SP admin auth.)
- **How it helps:** directly replaces a hand-rolled DAX-anti-pattern detector with Microsoft's
  maintained BPA rule set + your custom rules, programmatically, returning structured rows.
- **Limit:** XMLA-backed → dedicated capacity + XMLA endpoint; bulk needs admin/SP to enumerate
  models the runner doesn't personally own.

### VertiPaq Analyzer
**TITLE:** `_vertipaq.py` source — vertipaq_analyzer / import_vertipaq_analyzer
**URL:** https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_vertipaq.py
```python
vertipaq_analyzer(dataset: str | UUID, workspace=None, export: Literal['table'] | None = None,
                  read_stats_from_data: bool = False, export_lakehouse=None,
                  export_workspace=None, export_schema=None, dark_mode=False
                 ) -> dict[str, pd.DataFrame]
import_vertipaq_analyzer(folder_path: str, file_name: str)   # load a saved .vpax-style .zip
```
- Returns a **dict of DataFrames** keyed by model/table/partition/column/relationship/hierarchy —
  i.e. the full VertiPaq metric set (row counts, cardinality, dictionary/data/hierarchy size,
  encoding, compression) **computed programmatically from the live model**, no `.vpax` needed.
- `export='table'` writes delta tables `vertipaqanalyzer_model`, `_table`, `_partition`, `_column`,
  `_relationship`, `_hierarchy` to a lakehouse (great for the agent's history store). Older versions
  also support `export='zip'` to emit a `.vpax`-compatible zip; `import_vertipaq_analyzer` re-reads it.
- `read_stats_from_data=True` issues real DAX/queries for exact column stats (slower, more accurate);
  default reads cached engine stats.
- **How it helps:** the model-size detector gets exact VertiPaq numbers live; and the agent can keep
  ingesting offline `.vpax` zips via `import_vertipaq_analyzer` for parity with its existing parser.
- **Limit:** XMLA + dedicated capacity; large models can be heavy when `read_stats_from_data=True`.

### TOM (Tabular Object Model)
**TITLE:** sempy_labs.tom package — connect_semantic_model
**URL:** https://semantic-link-labs.readthedocs.io/en/stable/sempy_labs.tom.html
```python
from sempy_labs.tom import connect_semantic_model
with connect_semantic_model(dataset, readonly=True, workspace=None) as tom:
    for t in tom.model.Tables: ...
    # TOMWrapper exposes hundreds of helpers: all_columns, all_measures, all_partitions,
    # all_calculation_items, all_hierarchies, all_rls, get_annotations, used_in_relationships, etc.
```
- `readonly=True` (default) is exactly the agent's posture — full structural read of the model
  (tables, columns, measures + DAX expressions, partitions/Direct Lake mode, RLS roles, perspectives,
  calc groups, annotations, data sources). `readonly=False` needs XMLA **read/write** enabled.
- **How it helps:** deepest metadata access for blast-radius (dependency walking via TOM), Direct
  Lake detection, partition/refresh-policy inspection — richer than the flat `list_*` dataframes.
- **Limit:** XMLA; for an audit keep `readonly=True` (no write scope needed).

### Refresh / Direct Lake / size helpers
- `refresh_semantic_model(dataset, workspace=None, tables=None, partitions=None, refresh_type=...,
  visualize=False, ...)` — programmatic refresh trigger + monitoring (for a refresh-health probe;
  the agent would use it read-only to *inspect* rather than trigger in production).
- `sempy.fabric.list_refresh_requests(dataset, workspace, top_n, credential)` +
  `get_refresh_execution_details(...)` — refresh **history/status** (Enhanced Refresh API) → feeds
  the refresh detector without triggering anything.
- Direct Lake helpers (`sempy_labs.directlake`): `get_direct_lake_sources`,
  `update_direct_lake_model_connection`, `direct_lake_schema_sync`,
  `generate_direct_lake_semantic_model`, fallback-detection helpers — useful to flag Direct Lake
  models at fallback risk.
- `get_semantic_model_size` / `list_semantic_model_objects` style helpers exist for quick size +
  object inventory.

**TITLE:** Code Examples · microsoft/semantic-link-labs Wiki
**URL:** https://github.com/microsoft/semantic-link-labs/wiki/Code-Examples
- Canonical snippets: `labs.run_model_bpa(dataset=dataset, workspace=workspace)`,
  `labs.vertipaq_analyzer(...)`, `with connect_semantic_model(...) as tom:`,
  `labs.refresh_semantic_model(...)`, and the `service_principal_authentication` context manager.

---

## 4. `sempy_labs.admin` — admin-API-backed collectors (Scanner, activity, tenant settings)

**TITLE:** sempy_labs.admin package — semantic-link-labs documentation
**URL:** https://semantic-link-labs.readthedocs.io/en/stable/sempy_labs.admin.html
All of the following wrap the **Fabric/Power BI Admin REST APIs** and (per the docs) **support
service principal** auth — this is the path the previously-researched `notebookutils.getToken('pbi')`
could NOT reach (it gave only ~7 item scopes, no admin).
```python
admin.list_workspaces(capacity=None, workspace=None, workspace_state=None, workspace_type=None)
    # wraps Groups GetGroupsAsAdmin — ALL org workspaces
admin.list_datasets(top=None, filter=None, skip=None)        # GetDatasetsAsAdmin — all org datasets
admin.list_reports(top=None, skip=None, filter=None)         # GetReportsAsAdmin
admin.list_items(capacity=None, workspace=None, state=None, type=None, item=None)  # Items List
admin.list_capacities(capacity=None, include_tenant_key=False)  # Get Capacities As Admin
admin.list_tenant_settings()                                 # Tenants List Tenant Settings
admin.list_capacities_delegated_tenant_settings(...)         # per-capacity tenant-setting overrides
admin.list_activity_events(start_time, end_time, activity_filter=None,
                           user_id_filter=None, return_dataframe=True)  # Get Activity Events (audit)
admin.scan_workspaces(data_source_details=False, dataset_schema=False, dataset_expressions=False,
                      lineage=False, artifact_users=False, workspace=None)
    # Scanner API: PostWorkspaceInfo + GetScanStatus + GetScanResult — metadata, schema (TMSL-ish),
    # M/DAX expressions, lineage, per-item users. Returns a dict.
admin.get_refreshables(top, expand=None, filter=None, skip=None, capacity=None)  # 7-day refresh hist
admin.get_capacity_state(capacity) ; admin.get_capacity_assignment_status(workspace)
```
- **How it helps (this is the big unlock for the agent):**
  - `list_workspaces` / `list_datasets` / `list_items` / `list_capacities` → tenant-wide inventory.
  - `scan_workspaces` (Scanner API) → bulk metadata incl. **dataset schema + M/DAX expressions +
    lineage + item users** for governance/security detectors, in one async call — no XMLA, no
    per-model connection, **works with SP read-only admin scope**.
  - `list_activity_events` → audit log for usage/unused-asset and per-user-attribution detectors.
  - `list_tenant_settings` / `list_capacities_delegated_tenant_settings` → governance posture checks
    (e.g. is XMLA read-write on, are SPs allowed, export settings).
  - `get_refreshables` → tenant refresh health (7-day window).
- **Limits:** caller (user or SP) must be a **Fabric Administrator**, and tenant settings
  **"Service principals can access read-only admin APIs"** (and the broader "Service principals can
  use Fabric APIs") must be enabled for SP. Activity events limited to ~30/28-day lookback and
  per-day calls. Scanner has throttling and a per-call workspace cap (~100).

**TITLE:** Scan Fabric Workspaces With Scanner API Using Semantic Link Labs — fabric.guru
**URL:** https://fabric.guru/scan-fabric-workspaces-with-scanner-api-using-semantic-link-labs
- End-to-end `admin.scan_workspaces(...)` example; confirms it drives the 3-step Scanner workflow and
  returns rich metadata for governance use.

**TITLE:** Unlocking insights of user activity: sempy_labs.admin.list_activity_events() in Fabric Notebooks
**URL:** https://jihwanpowerbifabric.wixsite.com/supplychainflow/post/unlocking-power-bi-insights-using-sempy_labs-admin-list_activity_events-in-fabric-notebooks
- Practical `list_activity_events(start_time=..., end_time=...)` usage (ISO timestamps), returns a
  DataFrame of audit events. Useful pattern for the usage/per-user-attribution collectors.

---

## 5. Auth / identity model (user vs SP vs workspace identity)

- **Interactive user (default in a Fabric notebook):** functions run as the signed-in user; access
  limited to workspaces/items that user can see. Good for ad-hoc, bad for a headless agent.
- **Workspace identity / SP-triggered run:** a notebook scheduled via **Fabric Pipelines** or the
  **Job Scheduler API** under an SP runs non-interactively. The default token service auto-auths a
  **limited subset** of sempy functions (see §6) — no admin, no XMLA-heavy ops.
- **Manual SP auth (the agent's recommended mode):** explicitly authenticate sempy with an SP to
  unlock the broad function set including admin + XMLA + BPA + VertiPaq.

**TITLE:** Service principal support for Semantic Link — Microsoft Fabric | Microsoft Learn
**URL:** https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-service-principal-support
(ms.date 2025-09-01; SP support requires **sempy ≥ 0.12.0**.)

Three ways to supply SP credentials:
```python
# (A) set_service_principal context manager — plain values (test) or Key Vault tuples (prod)
from sempy.fabric import set_service_principal
with set_service_principal(tenant_id, client_id, client_secret=client_secret):
    fabric.run_model_bpa(dataset, workspace=workspace)
# Key Vault form: pass (vault_url, secret_name) tuples →
with set_service_principal(tenant_kv, client_kv, client_certificate=client_cert_kv): ...

# (B) Azure SDK TokenCredential passed per-call via credential=
from azure.identity import ClientSecretCredential
credential = ClientSecretCredential(tenant_id, client_id, client_secret)
fabric.run_model_bpa(dataset, workspace=workspace, credential=credential)

# (C) Set as default for a block (Fabric Analytics SDK)
from fabric.analytics.environment.credentials import SetFabricAnalyticsDefaultTokenCredentials
with SetFabricAnalyticsDefaultTokenCredentials(credential): ...
```

**TITLE:** `_authentication.py` source — ServicePrincipalTokenProvider (sempy_labs)
**URL:** https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_authentication.py
```python
class ServicePrincipalTokenProvider(TokenCredential):
    @classmethod
    def from_aad_application_key_authentication(cls, tenant_id, client_id, client_secret)  # TEST ONLY
    @classmethod
    def from_azure_key_vault(cls, key_vault_uri, key_vault_tenant_id,
                             key_vault_client_id, key_vault_client_secret)                 # PROD
# __call__ can mint tokens for audiences: pbi, storage, azure, graph, asazure, keyvault.

# sempy_labs.service_principal_authentication(...) — context manager that installs the token
# provider for admin subpackage + Azure API wrappers + connect_semantic_model.
```
- **Prereqs (admin must configure):** create the SP (Entra app + secret/cert); grant the SP a
  **workspace role via Manage Access**; enable tenant settings **"Service principals can use Fabric
  APIs"** and, for admin/Scanner, **"Service principals can access read-only admin APIs"**; enable
  **XMLA endpoint (read or read-write)** on the capacity for XMLA-backed functions; SP needs Key
  Vault **get** on secrets/certs if using the Key Vault form.
- **How it helps the agent:** this is the linchpin. A single Entra SP + Key Vault secret lets a
  scheduled Fabric notebook run BPA + VertiPaq + TOM + admin/Scanner read-only, tenant-wide, with no
  human in the loop — exactly the audit collector profile.

---

## 6. Hard limits — what SP / non-Fabric CANNOT do

**Functions NOT supported with manual SP auth** (per MS Learn SP doc):
- `sempy.fabric.list_apps`
- `sempy.fabric.list_dataflow_storage_accounts`
- `sempy.fabric.evaluate_measure`  ← use `evaluate_dax` instead
- `sempy.fabric.read_table(..., mode='rest')`
- `sempy.fabric.execute_tmsl`

**Default SP-triggered (pipeline/scheduler) auto-auth supports only** a small list:
`FabricRestClient`, `create_*`/`delete_*`/`list_items`/`list_folders`,
`list_datasets(mode='rest', endpoint='fabric')`, `list_dataflows/reports/workspaces(endpoint='fabric')`,
the `resolve_*`/`get_*_id` helpers, `run_notebook_job`. **No BPA/VertiPaq/TOM/admin** under default
auto-auth → you MUST use manual SP auth (§5) for the real collector functions.

**SP universal limits:** cannot access **"My workspace"** (any call targeting it fails); SP must be
explicitly granted workspace access; admin/Scanner requires Fabric Admin + read-only-admin-API tenant
setting.

**Outside Fabric (local machine / Databricks):**
- Microsoft is explicit: semantic link is **"supported only within Microsoft Fabric."**
- XMLA-backed functions (TOM, `vertipaq_analyzer`, `run_model_bpa`, `evaluate_dax`, most `list_*`)
  depend on the in-Fabric XMLA/AS client plumbing and a dedicated-capacity XMLA endpoint — these are
  the ones that break or are unsupported off-Fabric.
- The **pure-REST** surfaces (`FabricRestClient`/`PowerBIRestClient` + the `sempy_labs.admin`
  wrappers, which are just HTTP + a `TokenCredential`) are the most likely to function from a local
  Python process or Databricks if you inject your own SP token — but this is **not officially
  supported** and version/runtime-dependent.

**TITLE:** Run any semantic-link-labs function with a Service Principal · Issue #171
**URL:** https://github.com/microsoft/semantic-link-labs/issues/171
- Enhancement request; historically functions ran as the current user (workspace-scoped).
  `run_model_bpa_bulk` cited as the motivating case for tenant-wide SP access. Largely resolved by
  the 0.12.0 SP support, modeled on the Scanner API auth.

**TITLE:** Semantic Link Labs Support for Service Principals · Issue #577
**URL:** https://github.com/microsoft/semantic-link-labs/issues/577
- Community reports of `list_workspaces()` / `admin.list_workspaces` historically blocking SP in some
  versions — a reminder to **pin a recent version and test SP per-function** before relying on it.

**TITLE:** Service Principal Support in Semantic Link (Microsoft Fabric Blog, via Community redirect)
**URL:** https://community.fabric.microsoft.com/t5/Fabric-Updates-Blogs/forward/ba-p/5172549
- Official announcement of SP support in semantic link (the "scalable, secure automation" post);
  reiterates the two scenarios (SP-triggered runs vs. manual SP auth) and Key Vault best practice.

---

## 7. Recommended use in the agent (collector design)

1. **Package the collector as a Fabric notebook** (custom Environment with `semantic-link-labs`
   pinned ≥ a recent version; `sempy` upgraded to ≥ 0.12.0), scheduled via Pipeline/Job Scheduler.
2. **Authenticate with one Entra SP** using `service_principal_authentication` /
   `ServicePrincipalTokenProvider.from_azure_key_vault(...)` (Key Vault, not plaintext). Grant the SP
   workspace access + the read-only-admin-API tenant setting.
3. **Tenant inventory:** `admin.list_workspaces` / `list_datasets` / `list_capacities` /
   `scan_workspaces` (Scanner) for breadth (no XMLA); `admin.list_activity_events` +
   `admin.get_refreshables` for usage/refresh signals.
4. **Per-model deep audit (dedicated capacity, XMLA on):** `vertipaq_analyzer(export='table')` for
   size; `run_model_bpa(return_dataframe=True, rules=<custom>, extended=True)` for anti-patterns;
   `connect_semantic_model(readonly=True)` (TOM) + `get_model_calc_dependencies` for blast-radius;
   `evaluate_dax` for custom probes; `list_refresh_requests` for refresh health.
5. **Keep the `.vpax` path:** `import_vertipaq_analyzer` re-ingests saved zips → parity with the
   existing offline parser, and a fallback when XMLA/dedicated-capacity isn't available.
6. **Do NOT** rely on running these from Databricks/local as a supported path; if off-Fabric is
   required, restrict to REST/admin wrappers with an injected token and treat as best-effort.

---

## Flat URL list

- https://pypi.org/project/semantic-link-sempy/
- https://pypi.org/project/semantic-link-labs/
- https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-overview
- https://learn.microsoft.com/en-us/python/api/semantic-link-sempy/sempy.fabric?view=semantic-link-python
- https://learn.microsoft.com/en-us/fabric/data-science/read-write-power-bi-python
- https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-service-principal-support
- https://github.com/microsoft/semantic-link-labs
- https://github.com/microsoft/semantic-link-labs/wiki/Code-Examples
- https://github.com/microsoft/semantic-link-labs/issues/171
- https://github.com/microsoft/semantic-link-labs/issues/577
- https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_model_bpa.py
- https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_vertipaq.py
- https://raw.githubusercontent.com/microsoft/semantic-link-labs/main/src/sempy_labs/_authentication.py
- https://semantic-link-labs.readthedocs.io/en/stable/sempy_labs.html
- https://semantic-link-labs.readthedocs.io/en/stable/sempy_labs.admin.html
- https://semantic-link-labs.readthedocs.io/en/stable/sempy_labs.tom.html
- https://fabric.guru/scan-fabric-workspaces-with-scanner-api-using-semantic-link-labs
- https://fabric.guru/using-service-principal-authentication-with-fabricrestclient
- https://jihwanpowerbifabric.wixsite.com/supplychainflow/post/unlocking-power-bi-insights-using-sempy_labs-admin-list_activity_events-in-fabric-notebooks
- https://community.fabric.microsoft.com/t5/Fabric-Updates-Blogs/forward/ba-p/5172549
