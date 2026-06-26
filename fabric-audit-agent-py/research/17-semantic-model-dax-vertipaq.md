# Research 17 — Semantic Model Internals, DAX, VertiPaq & Analysis Tooling

**Purpose:** Deepen detector accuracy + author-coaching for the read-only Fabric/PBI capacity audit agent. Focus = Power BI / Fabric **semantic model internals + DAX + analysis tooling** (the agent already parses `.vpax` and runs a DAX analyzer). Out of scope here (already researched elsewhere): capacity CU%/throttling telemetry, REST APIs, XMLA endpoint basics, executeQueries.

**Date:** 2026-06-23. Sources prioritized: learn.microsoft.com, sqlbi.com / docs.sqlbi.com, docs.tabulareditor.com, daxstudio.org, github.com/microsoft/Analysis-Services, daxguide. Every item below carries a URL; a flat URL list is at the very end.

**Detector vocabulary used below:** `oversized-model`, `dax-antipattern`, `refresh-contention`, `dq-bloat` (DirectQuery/visual bloat), `framing-fallback`, `relationship-risk`, `author-coaching`.

---

## PART A — VertiPaq columnar engine internals (what drives model memory)

### A1. The columnar store + segmentation
**Summary:** VertiPaq stores each column in its own compressed data structure (a column store), not row-by-row. Each column is divided into **segments of ~1 million rows** (8M rows for Large-format models — see F1), and **each segment is compressed independently**. Import and Direct Lake queries are both answered by VertiPaq; DirectQuery federates to the source.
**Exact identifiers:** segment size = 1,048,576 rows default (8M for large models); compression chosen per-segment.
**How it sharpens which detector:** `oversized-model` — segment count and per-segment encoding feed the size model; tables far below 1M rows gain little from partitioning, tables far above benefit from partitioning + good sort order (RLE, see A2).
**URL:** https://www.sqlbi.com/articles/data-model-size-with-vertipaq-analyzer/ ; https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-large-models

### A2. Three compression methods: Value, Dictionary (Hash), Run-Length Encoding (RLE)
**Summary:**
- **Value encoding** — used for numeric columns; stores the value directly (with a mathematical offset/ratio). Most compact + fastest; no dictionary lookup.
- **Dictionary (Hash) encoding** — used for text and non-numeric data: VertiPaq builds a **dictionary** of unique values and stores a numeric ID per row. Requires a hash lookup at storage + query time. **Dictionary size is driven by (a) the number of unique values (cardinality) and (b) the size of each unique value** (string length / data type).
- **RLE (Run-Length Encoding)** — applied **on top of** value or dictionary encoding; replaces runs of repeated values with (value, count). Its effectiveness depends entirely on **sort order** within a segment — sorting low-cardinality, frequently-repeated columns first maximizes runs.
**Exact identifiers:** `Encoding` column metric = `HASH` (dictionary) or `VALUE`.
**How it sharpens which detector:** `oversized-model` + `author-coaching`. (1) Flag text columns that could be converted to integers (e.g. order numbers `SO123456` → strip prefix → Int64) to switch HASH→VALUE. (2) Coach: numeric keys compress/join best. (3) RLE/sort-order explains *why* a low-cardinality flag column is cheap and a high-cardinality GUID is not.
**URL:** https://learn.microsoft.com/en-us/power-bi/guidance/import-modeling-data-reduction ; https://www.sqlbi.com/articles/data-model-size-with-vertipaq-analyzer/ ; https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/column

### A3. Column **cardinality** is the #1 size driver
**Summary:** Compression is more effective with fewer unique values. **The higher a column's cardinality, the larger its dictionary and the worse its compression.** High-cardinality columns (keys, GUIDs, free text, high-precision datetime, decimals) dominate model memory and slow processing. This is the single most important size lever.
**Exact identifiers:** `Cardinality` (column metric) = "number of unique values of a column".
**How it sharpens which detector:** `oversized-model` is fundamentally a cardinality-ranking problem. Rank columns by `Columns Total Size` and by `Cardinality`; the worst offenders are usually high-cardinality, high-`Dictionary Size` columns. Coaching: remove the column, lower its cardinality (split, round, summarize), or convert to a relationship key.
**URL:** https://www.sqlbi.com/articles/data-model-size-with-vertipaq-analyzer/ ; https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/column

### A4. What drives memory footprint (the budget)
For each column, memory = **Data Size + Dictionary Size + Columns Hierarchies Size** (= `Columns Total Size`). For each table, memory = sum of columns' total size + **User Hierarchies Size** + **Relationships Size**. So the model-level footprint is driven by: high-cardinality column dictionaries, attribute (auto) hierarchies, user hierarchies, and high-cardinality relationship structures. (Exact metric definitions in Part B.)
**How it sharpens which detector:** `oversized-model` — decompose any table's size into these four buckets and attribute the bloat to the right cause (dictionary vs auto-hierarchy vs relationship).
**URL:** https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/table ; https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/column

---

## PART B — `.vpax` format + VertiPaq Analyzer metrics (exact definitions)

### B1. The `.vpax` file format
**Summary:** VertiPaq Analyzer is a set of open-source libraries that extract statistics from a Tabular model. A **`.vpax` is a ZIP** containing **`DaxModel.json`** (serialization of `Dax.Metadata.Model` — table/column/measure/relationship definitions + statistics) and **`DaxVpaView.json`** (the view consumed by the Excel template). **No data is included — only metadata + statistics** (table/column/measure names, measure definitions, cardinalities, sizes). An obfuscated **`.ovpax`** variant exists (with a `.dict` name-mapping file).
**Exact identifiers:** `DaxModel.json`, `DaxVpaView.json`, `.ovpax`/`.dict`.
**How it sharpens which detector:** confirms the ingestion artifact is **safe to transmit** (no customer data), and tells the parser exactly which JSON to read. The `DaxModel.json` is the richer object model to key detectors off.
**URL:** https://www.sqlbi.com/tools/vertipaq-analyzer/ ; https://docs.sqlbi.com/vertipaq-analyzer/ ; https://daxstudio.org/docs/features/model-metrics/

### B2. **Column-level** metrics (exact names + definitions)
| Metric | Exact definition | Detector use |
|---|---|---|
| **Cardinality** | number of unique values of a column | #1 size driver; rank for `oversized-model` |
| **Data Size** | bytes for all compressed data in segments+partitions; **excludes** dictionary & hierarchies | size attribution |
| **Dictionary Size** | bytes of dictionary structures | high = high-cardinality text/decimal; convert to int / drop |
| **Columns Hierarchies Size** | bytes of **automatically generated (attribute) hierarchies** used by MDX | candidate for `IsAvailableInMdx=false` savings (see E2) |
| **Columns Total Size** | Data Size + Dictionary Size + Columns Hierarchies Size | primary "expensive column" ranking |
| **Table Size %** | Columns Total Size ÷ Table Size | within-table contribution |
| **Database Size %** | Table Size ÷ Database Size | model-wide contribution |
| **Encoding** | HASH or VALUE | HASH on numeric-convertible col = coaching opportunity |
| **Segments #**, **Partitions #** | counts | partitioning analysis |

**How it sharpens which detector:** `oversized-model` core. A "fat column" = high `Columns Total Size` AND high `Cardinality` AND high `Dictionary Size`. Coaching messages can quote the exact byte breakdown.
**URL:** https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/column

### B3. **Table-level** metrics (exact names + composition)
- **Cardinality / Rows** — number of rows of a table (equivalent at table grain).
- **Data Size**, **Dictionary Size**, **Columns Hierarchies Size** — summed across columns.
- **User Hierarchies Size** — bytes of user-defined (multi-level) hierarchies.
- **Relationship Size** — bytes of relationship structures between tables.
- **RI Violations** — referential-integrity violations in M:1 relationships.
- **Bidirectional Filters** — number of relationships with bidirectional filter propagation.
- **MMR** — number of many-to-many *cardinality* relationships.
- **Segments #**, **Partitions #**, **Columns #**, **Table Size %**, **Database Size %**.

**Composition (exact):** `Table Size = (sum of Columns Total Size for all columns) + User Hierarchies Size + Relationships Size`, where `Columns Total Size = Data Size + Dictionary Size + Columns Hierarchies Size`.
**How it sharpens which detector:** `oversized-model` (size attribution per table), `relationship-risk` (Bidirectional Filters, MMR, RI Violations are direct flags — see Part D).
**URL:** https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/table

### B4. **Relationship-level** metrics (exact names)
`Relationship Type` (`M:1`/`M:M`/`1:1`), `Relationship Size` (bytes), `Max From Cardinality`, `Max To Cardinality`, `1:M Ratio %` (Max To Cardinality ÷ many-side rows), `Missing Keys` (unique From values absent on To side), `Invalid Rows` (From rows whose key is missing on To side = RI violations), `Bid. Filters`, `MMR`.
**How it sharpens which detector:** `relationship-risk` — flag large `Relationship Size`/high `Max From|To Cardinality` (memory + CPU-cache pressure), `Missing Keys`/`Invalid Rows > 0` (blank "unknown member" understating totals), `Bid. Filters = true` and `MMR` (perf + ambiguity).
**URL:** https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/relationship

---

## PART C — Best Practice Analyzer (BPA) standard rules

### C1. Provenance, canonical file, rule schema
**Summary:** BPA ships in **Tabular Editor** (Daniel Otykier); the standard ~70-rule set is maintained in **microsoft/Analysis-Services**. Each rule = JSON with **`ID`**, **`Name`** (`[Category] ...`), **`Category`** (Performance | DAX Expressions | Error Prevention | Maintenance | Naming Conventions | Formatting), **`Description`**, **`Severity`** (1=info, 2=standard, **3=most severe**), **`Scope`** (TOM object types), **`Expression`** (Dynamic LINQ boolean over TOM — true = violation), optional **`FixExpression`** (auto-fix) and **`CompatibilityLevel`**.
**Exact identifier — canonical raw URL:** `https://raw.githubusercontent.com/microsoft/Analysis-Services/master/BestPracticeRules/BPARules.json`
**Run programmatically:** TE2 CLI `TabularEditor.exe Model.bim -A <rulesUrl> -V` (`-A`/`-ANALYZE`, `-AX` excludes in-model rules, `-V` emits Azure DevOps logging); new CLI `te bpa run [--fix] [--save]`; C# API `var bpa = new Analyzer(); bpa.SetModel(Model); bpa.AnalyzeAll();` exposing `Rule.Category`, `RuleName`, `ObjectName`, `ObjectType`, `Rule.Severity`, `CanFix`.
**How it sharpens which detector:** the agent can **ingest `BPARules.json` directly** and key its own findings off the same `ID`/`Severity`/`Category`, giving every detector a recognized, industry-standard label and severity — and a known auto-fix flag.
**URL:** https://github.com/microsoft/Analysis-Services/blob/master/BestPracticeRules/BPARules.json ; https://docs.tabulareditor.com/te2/Command-line-Options.html ; https://tabulareditor.com/blog/introducing-the-tabular-editor-cli-limited-public-preview ; https://www.elegantbi.com/post/exportbparesults

### C2. Key Performance rules (exact ID — Sev)
- `AVOID_FLOATING_POINT_DATA_TYPES` (2) — columns typed `Double`; round-off + worse compression → use Int64/Decimal. → `oversized-model`/`author-coaching`.
- `ISAVAILABLEINMDX_FALSE_NONATTRIBUTE_COLUMNS` (2) — set `IsAvailableInMdx=false` on hidden non-attribute columns (drops attribute hierarchy). → `oversized-model`.
- `AVOID_BI-DIRECTIONAL_RELATIONSHIPS_AGAINST_HIGH-CARDINALITY_COLUMNS` (2) → `relationship-risk`.
- `REDUCE_USAGE_OF_LONG-LENGTH_COLUMNS_WITH_HIGH_CARDINALITY` (2) — long text + many uniques bloats VertiPaq. → `oversized-model`.
- `SPLIT_DATE_AND_TIME` (2) — datetime with sub-day precision = huge cardinality; split. → `oversized-model`.
- `LARGE_TABLES_SHOULD_BE_PARTITIONED` (2) — tables > ~25M rows. → `refresh-contention`.
- `REDUCE_NUMBER_OF_CALCULATED_COLUMNS` (2) / `REDUCE_USAGE_OF_CALCULATED_COLUMNS_THAT_USE_THE_RELATED_FUNCTION` (2) — calc columns compress worse + extend refresh. → `oversized-model`.
- `MINIMIZE_POWER_QUERY_TRANSFORMATIONS` (2) — may break query folding → slow refresh. → `refresh-contention`.
- `REMOVE_AUTO-DATE_TABLE` (2) — hidden per-column date tables waste memory. → `oversized-model`.
- `AVOID_EXCESSIVE_BI-DIRECTIONAL_OR_MANY-TO-MANY_RELATIONSHIPS` (2) / `MANY-TO-MANY_RELATIONSHIPS_SHOULD_BE_SINGLE-DIRECTION` (2). → `relationship-risk`.
- `SNOWFLAKE_SCHEMA_ARCHITECTURE` (2) — prefer star schema. → `author-coaching`.
- `MODEL_USING_DIRECT_QUERY_AND_NO_AGGREGATIONS` (1) / `MEASURES_USING_TIME_INTELLIGENCE_AND_MODEL_IS_USING_DIRECT_QUERY` (2). → `dq-bloat`.
- `LIMIT_ROW_LEVEL_SECURITY_(RLS)_LOGIC` (2). → `dax-antipattern`.

### C3. Key DAX Expressions rules (exact ID — Sev)
- `DAX_COLUMNS_FULLY_QUALIFIED` (3) — column refs must be `'Table'[Col]`.
- `DAX_MEASURES_UNQUALIFIED` (3) — measure refs must be `[Measure]` (no table prefix).
- `USE_THE_DIVIDE_FUNCTION_FOR_DIVISION` (2) — replace `/` with `DIVIDE()` (see G2 for the nuance).
- `AVOID_USING_THE_IFERROR_FUNCTION` (2) — IFERROR forces row-by-row eval → slow.
- `USE_THE_TREATAS_FUNCTION_INSTEAD_OF_INTERSECT` (2) — virtual relationships.
- `FILTER_COLUMN_VALUES` (2) — filter a column, not `FILTER(wholeTable)`.
- `FILTER_MEASURE_VALUES_BY_COLUMNS` (2) — filter measure values via column tables, not whole tables.
- `INACTIVE_RELATIONSHIPS_THAT_ARE_NEVER_ACTIVATED` (2) — dead inactive relationship (never used by USERELATIONSHIP).
- `AVOID_DUPLICATE_MEASURES` (2) / `MEASURES_SHOULD_NOT_BE_DIRECT_REFERENCES_OF_OTHER_MEASURES` (2).
- `AVOID_USING_'1-(X/Y)'_SYNTAX` (2). → all `dax-antipattern`/`author-coaching`.

### C4. Error Prevention / Formatting / Maintenance highlights
- `PROVIDE_FORMAT_STRING_FOR_MEASURES` (3) — visible measures need a FormatString. → `author-coaching`.
- `DATECOLUMN_FORMATSTRING` (1) / `MONTHCOLUMN_FORMATSTRING` (1) / `PERCENTAGE_FORMATTING` (2) / `INTEGER_FORMATTING` (2).
- `HIDE_FOREIGN_KEYS` (2) / `HIDE_FACT_TABLE_COLUMNS` (2) / `NUMERIC_COLUMN_SUMMARIZE_BY` (3, set SummarizeBy=None) / `MARK_PRIMARY_KEYS` (1).
- `RELATIONSHIP_COLUMNS_SAME_DATA_TYPE` (3) / `RELATIONSHIP_COLUMNS_SHOULD_BE_OF_INTEGER_DATA_TYPE` (1). → `relationship-risk`.
- `OBJECTS_SHOULD_NOT_START_OR_END_WITH_A_SPACE` (3).
- `UNNECESSARY_COLUMNS` (2) / `UNNECESSARY_MEASURES` (2) — hidden + unreferenced. → `oversized-model`/`author-coaching`.
- `FIX_REFERENTIAL_INTEGRITY_VIOLATIONS` (2). → `relationship-risk`.
- `CALCULATION_GROUPS_WITH_NO_CALCULATION_ITEMS` (2).
**URL (all C):** https://raw.githubusercontent.com/microsoft/Analysis-Services/master/BestPracticeRules/BPARules.json ; https://powerbi.microsoft.com/en-ca/blog/best-practice-rules-to-improve-your-models-performance/ ; https://tabulareditor.com/blog/best-practice-analyzer-bpa-rules-for-semantic-models

---

## PART D — Relationships & hierarchies (memory + correctness)

### D1. Cardinality types + cross-filter direction
**Summary:** Four cardinality types — **one-to-many (1:\*)**, **many-to-one (\*:1)**, **one-to-one (1:1)**, **many-to-many (\*:\*)**. Cross-filter direction: **Single** or **Both** (1:1 is forced Both; M:M offers Single-either-way or Both). "Both" = **bidirectional**. TOM property: **`CrossFilteringBehavior`** (`OneDirection`/`BothDirections`).
**How it sharpens which detector:** `relationship-risk` — `1:1` flagged as likely redundant storage; `*:*` cardinality flagged as **limited** relationship (slower).
**URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-relationships-understand

### D2. Why bidirectional filters are costly (ambiguity + perf)
**Summary:** Microsoft: "minimize the use of bi-directional relationships" — they hurt query performance and create **ambiguous filter-propagation paths** (resolved by priority tiers then path **weight**; ties → ambiguous-path error). Three legit uses (1:1/M:M bridge, slicer "with data", dim-to-dim). Recommended alternatives: use a **visual-level filter** ("Total Qty is not blank") instead of bidi for slicers, and **`CROSSFILTER(..., BOTH)`** *in a measure* instead of a model-level bidi for dim-to-dim.
**How it sharpens which detector:** `relationship-risk` — flag every `BothDirections` relationship / VPAX `Bid. Filters=true`; escalate severity when multiple coexist (ambiguity). Coaching message names the measure-level `CROSSFILTER` alternative.
**URL:** https://learn.microsoft.com/en-us/power-bi/guidance/relationships-bidirectional-filtering ; https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-relationships-understand

### D3. Regular vs limited relationships
**Summary:** **Regular** = engine confirms the "one" side is unique (intra-source 1:\*); VertiPaq builds an indexed mapping enabling **table expansion** (LEFT/FULL OUTER JOIN, blank "unknown member" rows for RI violations); `RELATED` works. **Limited** = no guaranteed one side (M:M cardinality OR cross-source-group in composite models); **no data structure built**, joined at query time with INNER JOIN, `RELATED` unavailable, RLS restricted. Speed order: 1:\* intra-source > M:M via bridge+bidi > M:M cardinality > cross-source-group.
**How it sharpens which detector:** `relationship-risk` + `dq-bloat` — limited relationships are a perf flag; recommend a bridge table with proper 1:\* relationships.
**URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-relationships-understand

### D4. Relationship-column cardinality cost
**Summary (SQLBI):** relationship-structure cost is "directly related to the cardinality of the column involved." When the structure exceeds CPU cache, RAM-access overhead hurts queries badly. Rough dimension guidance: <200K rows safe; 200K–5M monitor; 5–10M consider denormalizing; >10M expect problems.
**How it sharpens which detector:** `relationship-risk`/`oversized-model` — flag relationships on high-cardinality keys (use VPAX `Max From/To Cardinality`, `Relationship Size`); coach toward surrogate **integer** keys (value encoding) + lower cardinality.
**URL:** https://www.sqlbi.com/articles/costs-of-relationships-in-dax/

### D5. Inactive relationships + USERELATIONSHIP / CROSSFILTER
**Summary:** Only one active path between two tables; inactive relationships activated per-calc via **`USERELATIONSHIP`** (raises that relationship's weight) for role-playing dimensions. **Inactive relationships are still expanded even if never used** (so a never-activated inactive relationship is pure dead weight → BPA `INACTIVE_RELATIONSHIPS_THAT_ARE_NEVER_ACTIVATED`). `CROSSFILTER` overrides direction or disables (`none`).
**How it sharpens which detector:** `relationship-risk`/`author-coaching` — cross-reference model relationships vs measures' DAX for `USERELATIONSHIP` usage; flag inactive relationships with zero references.
**URL:** https://learn.microsoft.com/en-us/dax/userelationship-function-dax ; https://learn.microsoft.com/en-us/power-bi/guidance/relationships-active-inactive

### D6. Hierarchies: attribute hierarchies + `IsAvailableInMdx`
**Summary:** Every column gets an auto-generated **attribute hierarchy** (used by MDX/Excel PivotTables), measured by **`Columns Hierarchies Size`**. TOM property **`Column.IsAvailableInMdx`** = false drops that structure (memory + refresh savings) AND enables a query-plan optimization. **Do NOT disable** on columns that are hierarchy levels (`UsedInHierarchies`), sort-by targets (`UsedInSortBy`/`SortByColumn`), used in variations, or date-table columns — that causes query errors / broken MDX browsing (BPA: `SET_ISAVAILABLEINMDX_TO_TRUE_ON_NECESSARY_COLUMNS`, Sev 3). **User hierarchies** (multi-level) consume `User Hierarchies Size`.
**How it sharpens which detector:** `oversized-model` — recommend `IsAvailableInMdx=false` for **hidden, high-`Columns Hierarchies Size`, non-attribute** columns, guarded by the never-disable list. Rank candidates by `Columns Hierarchies Size`.
**URL:** https://learn.microsoft.com/en-us/dotnet/api/microsoft.analysisservices.tabular.column.isavailableinmdx?view=analysisservices-dotnet ; https://docs.tabulareditor.com/en/kb/bpa-set-isavailableinmdx-true-necessary.html ; https://docs.sqlbi.com/dax-internals/optimization-notes/isavailableinmdx-property

### D7. Auto date/time hidden tables (detection by prefix)
**Summary:** With Auto date/time on, Power BI creates a **hidden calculated table per date column** (Import + date type + not the many-side of a relationship), each built by `CALENDAR` with columns **Date, Day, MonthNo, Month, QuarterNo, Quarter, Year** + a 4-level **Date Hierarchy** (Year/Quarter/Month/Day). These bloat size + refresh time.
**Exact identifiers (detection):** system table prefixes **`LocalDateTable_<GUID>`** (one per date column) and **`DateTableTemplate_<GUID>`** (template). Disable: Options → Time intelligence → Auto date/time off. (Confirm prefixes against a live model's `INFO.TABLES()` / `$SYSTEM.TMSCHEMA_TABLES`.)
**How it sharpens which detector:** `oversized-model` — enumerate tables matching `^LocalDateTable_` / `^DateTableTemplate_`, count + sum their size to quantify wasted memory; coach to disable and use one shared marked Date table + a time-intelligence calculation group (E1).
**URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-auto-date-time ; https://learn.microsoft.com/en-us/power-bi/guidance/auto-date-time ; https://www.sqlbi.com/articles/automatic-time-intelligence-in-power-bi/

---

## PART E — Calculation groups, dynamic format strings, large model format

### E1. Calculation groups (kill measure sprawl)
**Summary:** A calc group is a model table shown as a **single attribute column** whose values are **calculation items**; each item rewrites the in-context measure via the **`SELECTEDMEASURE()`** placeholder (compatibility level **1500+**, i.e. all Power BI models). They collapse dozens of near-duplicate time-intelligence measures into a handful of items. Functions: `SELECTEDMEASURE()`, `SELECTEDMEASURENAME()`, `ISSELECTEDMEASURE()`, `SELECTEDMEASUREFORMATSTRING()`.
- **Precedence** property orders how multiple calc groups combine with `SELECTEDMEASURE()`; the **highest-precedence calc group's dynamic format string wins** (a measure's own dynamic format string ranks below any calc group).
- **Only applied when a single calc item is in filter context**; multiple/empty selections governed by `multipleOrEmptySelectionExpression` / `noSelectionExpression` and the model setting **`selectionExpressionBehavior`** (`automatic`→`nonvisual` default | `nonvisual` | `visual`) — this controls subtotal/total correctness.
- **Sideways recursion** limit: an item may reference *other* items in the same group but not the same item twice; deeper recursion is ignored (filter abandoned).
- **Pitfalls:** adding any calc group makes all report measures **`variant`** type; math on text measures errors (guard with `ISNUMERIC(SELECTEDMEASURE())`); implicit measures unsupported (set **`DiscourageImplicitMeasures=true`**); no OLS/RLS on calc groups; dynamic format strings not applied to report-level measures in Live Connect.
**How it sharpens which detector:** `author-coaching` (flagship). Cluster measures whose DAX differs only in the wrapped base measure across TI functions (`DATESYTD/QTD/MTD`, `SAMEPERIODLASTYEAR`, `PARALLELPERIOD`, `DATEADD`, `TOTALYTD`); if `calculationGroups` is empty and N≥~6 such near-duplicates exist → recommend a Time Intelligence calc group. If a calc group exists, validate `DiscourageImplicitMeasures=true`, `formatStringDefinition` on %-items, and a deliberate `selectionExpressionBehavior`.
**URL:** https://learn.microsoft.com/en-us/analysis-services/tabular-models/calculation-groups?view=asallproducts-allversions ; https://www.sqlbi.com/articles/understanding-calculation-groups/ ; https://www.sqlbi.com/articles/understanding-calculation-group-precedence/ ; https://www.sqlbi.com/articles/sideways-recursion-in-dax-calculation-groups/

### E2. Dynamic format strings
**Summary:** Two surfaces, same DAX. (a) **Measure dynamic format string** — Measure tools → Format = **Dynamic**; keeps the measure numeric (unlike `FORMAT()` which returns text and breaks charts); scope = single measure. (b) **Calculation-item format-string expression** (`formatStringDefinition`) — often uses `SELECTEDMEASUREFORMATSTRING()` to inherit/override the base format. **Must return a scalar string.** Keep it trivial (a `SWITCH`/`SELECTEDVALUE` lookup) — complex format DAX hurts query performance. Not available for report/live-connect measures.
**How it sharpens which detector:** `author-coaching`/`dax-antipattern` — recommend dynamic format strings over `FORMAT()` for measures feeding charts; flag a format-string expression that calls measures or scans large tables as a perf risk.
**URL:** https://learn.microsoft.com/en-us/power-bi/create-reports/desktop-dynamic-format-strings ; https://www.sqlbi.com/articles/introducing-dynamic-format-strings-for-dax-measures/

### E3. Large semantic model storage format
**Summary:** Default in-memory model cap = **1 GB**. **Large semantic model storage format** raises it to the capacity size (required to grow beyond **10 GB**); also improves XMLA write perf. Enables **on-demand paging** (column segments paged into memory; default segment size **8M rows**) and per-model **eviction** (sum of model sizes can exceed capacity memory, but one model is still capped at SKU memory). Enable: model Settings → slider, or workspace default, or `Set-PowerBIDataset -TargetStorageMode PremiumFiles`. SKUs: Fabric **F**, Premium **P**, Embedded **A**, **PPU**, and Pro workspaces on reserved capacity (still 1 GB cap).
**Exact identifiers:** `ActualStorage.StorageMode` = **`Abf`** (default/small) | **`PremiumFiles`** (large). DMVs `DISCOVER_STORAGE_TABLE_COLUMNS` (`DICTIONARY_SIZE`) + `DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS` (`USED_SIZE`, `Temperature`, `Last Accessed`).
**Refresh caution:** a model near half capacity size (e.g. 12 GB on 25 GB) can OOM during refresh.
**How it sharpens which detector:** `oversized-model`/`refresh-contention` — flag `StorageMode=Abf` with size near/over 1 GB (or Pro 1 GB cap) → recommend Large format + incremental refresh; flag large model > ~50% capacity memory as refresh-OOM risk; flag cross-region workspace moves (region-locked once created large).
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-large-models

---

## PART F — Storage modes & framing: Direct Lake vs Import vs DirectQuery, composite, incremental refresh

### F1. Direct Lake — how it works + transcoding + framing
**Summary:** A table storage mode that loads Delta/Parquet from OneLake into VertiPaq on demand (**transcoding** = on first column access, merge local Parquet dictionaries into a global VertiPaq dictionary + load column segments; plain-encoded Parquet must be re-encoded = slower). Residency states cold→semiwarm→warm→hot; columns evicted on framing, idleness, or memory pressure. **Framing** = a metadata-only refresh that sets the point-in-time Delta version the model reads (cheap, seconds); **`Automatic updates`** model setting (default on) auto-frames on OneLake change. **Incremental framing** drops only affected segments. Destructive **Overwrite** loads / delete-update on unpartitioned tables force cold reloads; framing fails > **10,000 Parquet files**.
**How it sharpens which detector:** `framing-fallback`/`refresh-contention` — flag Overwrite/destructive load patterns, small-file proliferation, high-cardinality partition columns (>100–200 distinct) as cold-reload / framing-failure risks. Inspect residency via `INFO.STORAGETABLECOLUMNSEGMENTS()`.
**URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-how-it-works ; https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-understand-storage

### F2. Direct Lake — DirectQuery fallback + the behavior property
**Summary:** Fallback applies **only to Direct Lake on SQL endpoints**; **Direct Lake on OneLake = `DirectLakeOnly`, no fallback** (recommended for new models). Stays in Direct Lake only if ALL true: no SQL **RLS/DDM/OLS**, no **unmaterialized SQL view**, within per-table guardrails (Parquet files / row groups / rows), and **framed** after Delta changes. One table over a guardrail blocks the whole model.
**Exact identifiers:** **`DirectLakeBehavior`** property = **`Automatic`** (silent fallback, default) | **`DirectLakeOnly`** (errors if conditions unmet — use in dev) | **`DirectQueryOnly`**. Diagnose: `EVALUATE TABLETRAITS()` → **`[DirectLakeFallbackInfo]`** per table (**`None`** = healthy; else reason: SQL view / RLS-DDM-OLS at endpoint / unframed / guardrail breach / memory pressure).
**How it sharpens which detector:** `framing-fallback` — run `TABLETRAITS()`, flag any table with `[DirectLakeFallbackInfo] ≠ None` and map the reason to a fix; flag production Direct-Lake-on-SQL models set to `Automatic` as silent-fallback risk (recommend `DirectLakeOnly` in dev).
**URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-how-it-works

### F3. Direct Lake — guardrails by Fabric SKU (exact numbers)
| SKU | Parquet files/table | Row groups/table | Rows/table (M) | Max model size (GB) | Max memory (GB) |
|---|---|---|---|---|---|
| F2–F8 | 1,000 | 1,000 | 300 | 10 | 3 |
| F16 | 1,000 | 1,000 | 300 | 20 | 5 |
| F32 | 1,000 | 1,000 | 300 | 40 | 10 |
| F64/FT1/P1 | 5,000 | 5,000 | 1,500 | Unlimited | 25 |
| F128/P2 | 5,000 | 5,000 | 3,000 | Unlimited | 50 |
| F256/P3 | 5,000 | 5,000 | 6,000 | Unlimited | 100 |
| F512/P4 | 10,000 | 10,000 | 12,000 | Unlimited | 200 |
| F1024/P5, F2048 | 10,000 | 10,000 | 24,000 | Unlimited | 400 |

Max model size = **model-level** (affects all queries); other guardrails = **per query**. Breach: OneLake → refresh fails; SQL → fall back to DQ (refresh succeeds with warning). Delta tuning: row groups **1M–16M rows**; partition cardinality **<100–200**; apply V-Order + Spark `OPTIMIZE`; VACUUM must not remove the framed Delta version.
**How it sharpens which detector:** `oversized-model`/`framing-fallback` — compare per-table rows/files/row-groups to the SKU row; flag tables over per-query guardrails and models near model-level GB / Max memory (paging) caps.
**URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview

### F4. Import & DirectQuery essentials (the bloat traps)
**Import:** always fully memory-resident before query/refresh; ~10× compression; full refresh can use ~2× model memory; refresh limits **8/day (Pro)**, **48/day (Premium/PPU)**; enable Large format before first refresh if it may exceed 1 GB.
**DirectQuery (exact limits):** **1,000,000-row** intermediate result limit (raise via *Max Intermediate Row Set Count* on Premium); **4-minute** query timeout; **32,764-char** text limit; max connections default **10**; case-insensitivity vs case-sensitive source = undefined results. **Assume referential integrity** → INNER JOIN (faster). Diagnose with Performance Analyzer + `FlightRecorderCurrent.trc`. Perf targets: <5s good, >30s unusable. Traps: nonfolding M steps (check View Native Query), bidi filters, `DistinctCount`/TopN/Median/measure-filters, multi-select slicers.
**How it sharpens which detector:** `refresh-contention` (Import full-refresh-only large facts, refresh near 8/48-day caps), `dq-bloat` (group-bys likely > 1M rows, missing Assume-RI, nonfolding steps, expensive visuals, connection-cap pressure).
**URL:** https://learn.microsoft.com/en-us/power-bi/connect-data/service-dataset-modes-understand ; https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-directquery-about

### F5. Composite models + aggregations
**Summary:** Per-table storage mode **Import / DirectQuery / Dual** (Dual = engine picks per query; avoids limited relationships with Import). **Source groups**: all Import + Direct Lake = one group; cross-source-group relationships are always **limited** + default **many-to-many**. DirectQuery-over-semantic-model **chain max length = 3**. **Aggregations** (Manage aggregations): Detail Table **must be DirectQuery**; agg table set to **Import**; relationship-based aggs need regular relationships, GroupBy-based aggs need mandatory GroupBy entries; miss when query grain is below agg grain; precedence picks among multiple aggs. Detect hits via SQL Profiler **`Query Processing\Aggregate Table Rewrite Query`** event (`matchingResult`, `dataRequest`, `mapping`). Note: since Aug 2022, Import aggs are ignored when **SSO** is enabled on the source.
**How it sharpens which detector:** `dq-bloat`/`relationship-risk` — flag large DQ facts with **no** aggregation; flag aggregations that silently miss; flag DQ dimensions that should be **Dual**; flag cross-source join keys > 50,000 (`A×C ≥ 250,000` for strings); flag chains at length 3.
**URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-composite-models ; https://learn.microsoft.com/en-us/power-bi/transform-model/aggregations-advanced

### F6. Incremental refresh (reduce refresh contention)
**Summary:** Reserved, **case-sensitive Date/Time** params **`RangeStart`** / **`RangeEnd`**, referenced in a `Table.SelectRows` filter that **must fold** (else it pulls all rows, defeating the purpose). Policy: **archive period** (history kept) vs **incremental period** (rolling window refreshed). **Detect data changes** (a last-modified column, max-value compared per period; reduce its cardinality / override via custom **polling expression**). Service auto-manages partitions (rolling window). **Real-time/hybrid** (Premium) adds a DirectQuery partition (requires "Only refresh complete days", no nonfolding steps). Refresh time limits: **2h (Pro)**, **5h (Premium)**, **unlimited (XMLA)**; times default UTC.
**How it sharpens which detector:** `refresh-contention` — flag large Import facts with full-refresh duration nearing the 2h/5h cap and **no** incremental policy; flag policies whose filter doesn't fold; flag bad `RangeStart`/`RangeEnd` config or high-cardinality detect-changes columns.
**URL:** https://learn.microsoft.com/en-us/power-bi/connect-data/incremental-refresh-overview

---

## PART G — DAX engine internals + DAX Studio (storage engine vs formula engine, query plan, server timings)

### G1. Storage Engine (SE/VertiPaq) vs Formula Engine (FE)
**Summary:** Every DAX query splits between **FE** (handles any DAX op; **single-threaded, no cache**; sends SE requests **sequentially**) and **SE** (VertiPaq or DirectQuery; **multi-threaded, cacheable**). Goal = push work into SE. SE queries are expressed in **xmSQL** (simple SQL-like pseudo-language; only `SUM/MIN/MAX/COUNT/DCOUNT`, simple arithmetic, INNER/LEFT OUTER JOIN; implicit GROUP BY). The **datacache** (uncompressed in-memory table) bridges SE→FE; a large-cardinality datacache is the FE bottleneck and the memory cost of materialization.
**How it sharpens which detector:** `dax-antipattern` — frames every coaching message in engine terms (FE-bound vs SE-bound).
**URL:** https://www.sqlbi.com/articles/formula-engine-and-storage-engine-in-dax/ ; https://docs.sqlbi.com/dax-internals/vertipaq/xmSQL

### G2. CallbackDataID — the central runtime anti-pattern
**Summary:** **CallbackDataID** = VertiPaq calling back into the FE row-by-row during a scan to evaluate an expression it can't handle. **Bad because (1) its results are NOT cached** (scan re-runs every time) and **(2) it drags single-threaded FE logic into the scan loop.** Triggered by **`IF`/conditional logic inside an iterator** (`SUMX` etc.), and **`DIVIDE`** inside an iterator (DIVIDE always runs in FE). xmSQL signature: `SUM([CallbackDataID( IF(...) )])` / `CallbackDataID( DIVIDE(Audience[Weight],Audience[Age]) )`. **Fix:** pre-filter with `FILTER` so the aggregation is pure SE, e.g. `SUMX(FILTER(Audience, Audience[Age]<>0), Audience[Weight] / Audience[Age])` — restores caching (3 ms cached vs 4.6 s with callback).
**How it sharpens which detector:** `dax-antipattern` (flagship). **Static pattern detection:** flag `IF`/`DIVIDE`/division inside `SUMX`/`AVERAGEX`/other X-iterators as likely CallbackDataID even without running. **Runtime:** flag any SE query whose xmSQL contains `CallbackDataID`. Coaching: name the trigger + the FILTER rewrite + explain the cache loss.
**URL:** https://www.sqlbi.com/articles/divide-performance/ ; https://docs.sqlbi.com/dax-internals/vertipaq/xmSQL

### G3. DIVIDE vs `/`, IFERROR, and the nuance
**Summary:** `DIVIDE()` is the correct safe-division choice **outside** iterators (handles divide-by-zero without IFERROR; faster than `IF(y=0,BLANK(),x/y)`); BPA `USE_THE_DIVIDE_FUNCTION_FOR_DIVISION`. **Nuance:** *inside* an iterator DIVIDE forces FE/CallbackDataID — there the FILTER-then-`/` rewrite wins. **`IFERROR`** forces row-by-row evaluation (slow) — BPA `AVOID_USING_THE_IFERROR_FUNCTION`; prefer DIVIDE / proper guards.
**How it sharpens which detector:** `dax-antipattern`/`author-coaching` — recommend DIVIDE for top-level division, but FILTER-rewrite for division inside iterators; flag IFERROR.
**URL:** https://www.sqlbi.com/articles/divide-performance/ ; https://raw.githubusercontent.com/microsoft/Analysis-Services/master/BestPracticeRules/BPARules.json

### G4. Context transition cost (CALCULATE in a row context)
**Summary:** CALCULATE in a row context performs **context transition** — converting the current row into a filter context by filtering **every column** of the iterated table. Cost driver = **the number of columns** (and their cardinality) being turned into filters. Iterating a wide fact table (or a table with no unique key) is expensive; **iterate `VALUES(keyColumn)`** instead so only one column is filtered: `SUMX(VALUES(Customer[CustomerKey]), ...)`.
**How it sharpens which detector:** `dax-antipattern` — flag `CALCULATE`/measure-reference (implicit CALCULATE) inside iterators over wide/keyless tables; coach the `VALUES(keyColumn)` pattern. Pairs with G7 (Server Timings showing high Rows/KB).
**URL:** https://www.sqlbi.com/articles/understanding-context-transition-in-dax/

### G5. FILTER vs KEEPFILTERS, and filtering columns not tables
**Summary:** A filter argument in CALCULATE **overwrites** existing filters on those columns; **`KEEPFILTERS`** changes it to *intersect* (keep existing). For single-column conditions KEEPFILTERS is simpler + faster than wrapping `FILTER`. **`FILTER(wholeTable, ...)` is an anti-pattern** — it materializes the whole table; filter **only the column** you need (`FILTER(VALUES('T'[Col]), ...)` or a direct predicate) for far lower memory. BPA: `FILTER_COLUMN_VALUES`, `FILTER_MEASURE_VALUES_BY_COLUMNS`.
**How it sharpens which detector:** `dax-antipattern` — flag `FILTER(<table>, ...)` over large tables; coach column-level filters / KEEPFILTERS.
**URL:** https://www.sqlbi.com/articles/using-keepfilters-in-dax/ ; https://www.sqlbi.com/articles/when-to-use-keepfilters-over-iterators/

### G6. Variables (VAR) — compute once, reuse
**Summary:** A `VAR` is evaluated **at most once**, in the context where defined; repeating a subexpression does **not** guarantee single evaluation. Use variables to eliminate repeated subexpressions and to capture a row value before a context-transition CALCULATE filter. SQLBI rule of thumb: "when in doubt, define the variable." Also avoids re-triggering CallbackDataID-laden subexpressions.
**How it sharpens which detector:** `dax-antipattern`/`author-coaching` — flag measures that repeat an identical non-trivial subexpression (or repeat a measure reference) and recommend extracting a `VAR`.
**URL:** https://www.sqlbi.com/articles/variables-in-dax/ ; https://www.sqlbi.com/articles/optimizing-duplicated-dax-expressions-using-variables/ ; https://learn.microsoft.com/en-us/dax/best-practices/dax-variables

### G7. DAX query plan + Server Timings (DAX Studio) — exact metrics
**Query plan:** **logical** (resembles DAX) then **physical** (execution). Physical operators include **`Cache`** (datacache), **`AggregationSpool`/`ProjectionSpool`/`SpoolIterator`/`SpoolLookup`** (materialization), joins (`CrossApply`, `InnerHashJoin`). Each spool line is annotated **`#Records=…`**; a spool `#Records` far above the final result row count = large intermediate datacache (red flag).
**Server Timings metrics (exact):** **Total** (Query End duration), **SE** (sum of SE query durations), **FE** (= Total − SE; single-threaded), **SE CPU** (SE CPU time), **SE Queries** (# SE queries), **SE Cache** (# cache hits), **Rows** (datacache cardinality per SE query), **KB** (datacache size), **Par.** (parallelism = SE CPU ÷ SE Duration). (Rows/KB populate only against Power BI / Excel 2016 / AS 2016+.)
**Interpretation:** high **FE%** → single-threaded bottleneck (context transition / CallbackDataID / large materialization); **Par. ≈ 1** → SE op not scaling; many **SE Queries** + low **SE Cache** → non-optimizable filters / fragmented scans; **Rows >> result** or high **KB vs Rows** → over-materialization (table filter or keyless context transition).
**DAX Studio features:** Server Timings, Query Plan, **Model Metrics / VertiPaq Analyzer** tab (View Metrics; export/import `.vpax`/`.ovpax`), **Clear Cache** (cold measurement), **Run Benchmark** (default 5 cold + 5 warm), All-Queries capture.
**How it sharpens which detector:** `dax-antipattern` runtime tier — compute `FE% = FE/Total` (>~50–60% ⇒ FE-bound verdict), per-query `Par. = SE CPU/SE Duration` (≈1 ⇒ non-scaling flag), and `Rows >> result rows` ⇒ over-materialization flag. These turn raw timings into a *why-this-is-slow* coaching message and corroborate the static pattern detectors (G2/G4/G5).
**URL:** https://daxstudio.org/docs/features/traces/server-timings-trace/ ; https://www.sqlbi.com/blog/marco/2016/11/01/new-server-timings-features-in-dax-studio-2-5-0-dax-powerbi-ssas-tabular/ ; https://www.sqlbi.com/articles/analyzing-the-parallelism-of-storage-engine-operations-in-dax-studio/ ; https://docs.sqlbi.com/dax-internals/vertipaq/physical-query-plan ; https://daxstudio.org/docs/features/model-metrics/

---

## Detector-sharpening cheat sheet (identifiers → detector)

- **High `Cardinality` / `Dictionary Size` / `Columns Total Size`** → `oversized-model`; coach int conversion (HASH→VALUE), split datetime, summarize, drop column.
- **`Columns Hierarchies Size` on hidden non-attribute columns** → `IsAvailableInMdx=false` (guard against UsedInHierarchies/UsedInSortBy/variations/date).
- **`LocalDateTable_` / `DateTableTemplate_` tables** → `oversized-model`; disable Auto date/time.
- **VPAX `Bid. Filters`, `MMR`, `Missing Keys`/`Invalid Rows`, large `Relationship Size`/`Max From|To Cardinality`** → `relationship-risk`.
- **`CrossFilteringBehavior=BothDirections`** → `relationship-risk`; alt = visual filter / `CROSSFILTER(...,BOTH)` in a measure.
- **N≥6 near-duplicate TI measures + empty `calculationGroups`** → `author-coaching` (recommend calc group).
- **`IF`/`DIVIDE`/division inside X-iterators; xmSQL `CallbackDataID`** → `dax-antipattern` (FILTER rewrite).
- **`FILTER(<table>,…)`; `IFERROR`; `/` for safe division; repeated subexpression** → `dax-antipattern` (column filter / KEEPFILTERS / DIVIDE / VAR).
- **`CALCULATE`/measure-ref inside iterator over wide/keyless table** → `dax-antipattern` (iterate `VALUES(key)`).
- **`StorageMode=Abf` near 1 GB; model > ~50% capacity memory** → `oversized-model`/`refresh-contention` (Large format + incremental refresh).
- **`DirectLakeBehavior=Automatic` in prod; `TABLETRAITS()[DirectLakeFallbackInfo] ≠ None`; >10,000 Parquet files; Overwrite loads** → `framing-fallback`.
- **Per-table rows/files/row-groups vs SKU guardrail row** → `oversized-model`/`framing-fallback`.
- **DQ group-bys > 1M rows; missing Assume-RI; nonfolding M; large DQ fact w/o aggregation; chain length 3** → `dq-bloat`.
- **Full-refresh-only large fact; non-folding incremental filter; refresh near 2h/5h cap** → `refresh-contention`.
- **BPA `ID`/`Severity`/`Category`** → label + prioritize every finding against the industry-standard rule set.

---

## Flat URL list

- https://www.sqlbi.com/articles/data-model-size-with-vertipaq-analyzer/
- https://www.sqlbi.com/tools/vertipaq-analyzer/
- https://docs.sqlbi.com/vertipaq-analyzer/
- https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/column
- https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/table
- https://docs.sqlbi.com/vertipaq-analyzer/excel-metrics/relationship
- https://learn.microsoft.com/en-us/power-bi/guidance/import-modeling-data-reduction
- https://daxstudio.org/docs/features/model-metrics/
- https://github.com/microsoft/Analysis-Services/blob/master/BestPracticeRules/BPARules.json
- https://raw.githubusercontent.com/microsoft/Analysis-Services/master/BestPracticeRules/BPARules.json
- https://github.com/microsoft/Analysis-Services/blob/master/BestPracticeRules/README.md
- https://github.com/TabularEditor/BestPracticeRules
- https://docs.tabulareditor.com/te2/Command-line-Options.html
- https://docs.tabulareditor.com/en/kb/bpa-set-isavailableinmdx-true-necessary.html
- https://tabulareditor.com/blog/introducing-the-tabular-editor-cli-limited-public-preview
- https://tabulareditor.com/blog/best-practice-analyzer-bpa-rules-for-semantic-models
- https://www.elegantbi.com/post/exportbparesults
- https://powerbi.microsoft.com/en-ca/blog/best-practice-rules-to-improve-your-models-performance/
- https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-relationships-understand
- https://learn.microsoft.com/en-us/power-bi/guidance/relationships-bidirectional-filtering
- https://learn.microsoft.com/en-us/power-bi/guidance/relationships-active-inactive
- https://learn.microsoft.com/en-us/dax/userelationship-function-dax
- https://www.sqlbi.com/articles/costs-of-relationships-in-dax/
- https://learn.microsoft.com/en-us/dotnet/api/microsoft.analysisservices.tabular.column.isavailableinmdx?view=analysisservices-dotnet
- https://docs.sqlbi.com/dax-internals/optimization-notes/isavailableinmdx-property
- https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-auto-date-time
- https://learn.microsoft.com/en-us/power-bi/guidance/auto-date-time
- https://www.sqlbi.com/articles/automatic-time-intelligence-in-power-bi/
- https://learn.microsoft.com/en-us/analysis-services/tabular-models/calculation-groups?view=asallproducts-allversions
- https://www.sqlbi.com/articles/understanding-calculation-groups/
- https://www.sqlbi.com/articles/understanding-calculation-group-precedence/
- https://www.sqlbi.com/articles/sideways-recursion-in-dax-calculation-groups/
- https://www.sqlbi.com/articles/controlling-empty-or-multiple-selections-in-calculation-groups/
- https://learn.microsoft.com/en-us/power-bi/create-reports/desktop-dynamic-format-strings
- https://www.sqlbi.com/articles/introducing-dynamic-format-strings-for-dax-measures/
- https://learn.microsoft.com/en-us/dax/selectedmeasure-function-dax
- https://learn.microsoft.com/en-us/dax/selectedmeasureformatstring-function-dax
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-large-models
- https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview
- https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-how-it-works
- https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-understand-storage
- https://learn.microsoft.com/en-us/power-bi/connect-data/service-dataset-modes-understand
- https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-directquery-about
- https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-composite-models
- https://learn.microsoft.com/en-us/power-bi/transform-model/aggregations-advanced
- https://learn.microsoft.com/en-us/power-bi/connect-data/incremental-refresh-overview
- https://www.sqlbi.com/blog/marco/2025/05/13/direct-lake-vs-import-vs-direct-lakeimport-fabric-semantic-models-may-2025/
- https://www.sqlbi.com/articles/formula-engine-and-storage-engine-in-dax/
- https://docs.sqlbi.com/dax-internals/vertipaq/xmSQL
- https://docs.sqlbi.com/dax-internals/vertipaq/logical-query-plan
- https://docs.sqlbi.com/dax-internals/vertipaq/physical-query-plan
- https://www.sqlbi.com/articles/divide-performance/
- https://www.sqlbi.com/articles/understanding-context-transition-in-dax/
- https://www.sqlbi.com/articles/using-keepfilters-in-dax/
- https://www.sqlbi.com/articles/when-to-use-keepfilters-over-iterators/
- https://www.sqlbi.com/articles/variables-in-dax/
- https://www.sqlbi.com/articles/optimizing-duplicated-dax-expressions-using-variables/
- https://learn.microsoft.com/en-us/dax/best-practices/dax-variables
- https://www.sqlbi.com/articles/analyzing-the-parallelism-of-storage-engine-operations-in-dax-studio/
- https://www.sqlbi.com/blog/marco/2016/11/01/new-server-timings-features-in-dax-studio-2-5-0-dax-powerbi-ssas-tabular/
- https://daxstudio.org/docs/features/traces/server-timings-trace/
- https://dax.guide/keepfilters/
</content>
</invoke>
