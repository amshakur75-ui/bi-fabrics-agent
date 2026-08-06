# FIX C — estate-wide detector collectors: status

The 10 detectors in `detectors/__init__.py` are all correct; the work is feeding them. Status of
each data source (confirmed by live probe on 2026-08-06 with the current SP admin grants):

| Detector | facts key | Collector | Status |
|---|---|---|---|
| capacity | `capacity` | capacity-events / list-usages | **live** (pre-existing) |
| concentration / user_concentration | `items` | Log Analytics / activity | **live** (pre-existing) |
| **refresh** | `refreshes` | `collector_refresh.py` | **built + tested**, gated `FABRIC_REFRESH_DATASETS_JSON`. GAP: per-dataset `/refreshes` needs SP **workspace membership** (404s otherwise). |
| **model** | `models` | `collector_scanner.py` (Scanner API) | **built + tested + live-verified** (41 models collected). GAP: `relationships` (bidirectional signal) are empty unless the Fabric admin enables *"Enhance admin API responses with detailed metadata + DAX/mashup expressions"*. |
| **report** | `reports` | `collector_scanner.py` (same scan) | **built + tested**. `DirectQuery` signal is reachable now (dataset `targetStorageMode`); `visuals` needs the same detailed-metadata setting; `slowestVisualMs` has **no reachable source**. |
| **security** | `access` | `collector_security.py` (Activity Events) | **built + tested**, gated `FABRIC_SECURITY_ENABLED`. `externalShares` reliable (SharingInformation); `adminGrants` best-effort (validate grant field names vs a live event; `sensitive` only for `FABRIC_SENSITIVE_WORKSPACES`); `accessEvents` NOT produced (needs a per-user historical baseline store — follow-up). |
| pipeline | `pipelines` | — | **not built**: Fabric Data Pipelines run-history is per-workspace + uncertain shape; heavier than the above. Follow-up. |
| cost | `usage` | — | **not built**: `views30d` needs a 30-day activity-event scan; `usage` also needs a merge slot (dict). Follow-up. |
| blast_radius | lineage | — | **not built**: needs Scanner `lineage=true`, gated by the same detailed-metadata tenant setting as models. Follow-up. |

## Merge slots
`collector_merge.merge_facts_list` now carries `models / reports / pipelines / users / refreshes`
(lists) and `access` (a dict of sub-lists). `usage` (cost) would need a new slot when that lands.

## To activate what's built (once auth/permissions land)
- **models + reports (Scanner):** set `FABRIC_SCANNER_WORKSPACE_IDS`; for bidirectional/visual detail,
  a Fabric admin enables the detailed-metadata tenant setting.
- **security:** set `FABRIC_SECURITY_ENABLED=true` (+ optional `FABRIC_ORG_DOMAINS`,
  `FABRIC_SENSITIVE_WORKSPACES`).
- **refresh:** set `FABRIC_REFRESH_DATASETS_JSON` after adding the SP to the target workspaces.

Every collector is fail-open and gated OFF by default, so none destabilizes the live sweep until
deliberately configured.
