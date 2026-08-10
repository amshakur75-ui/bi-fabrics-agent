"""routing_table.py — Newell informal-terminology routing data (Python port of v2.1).

Port of the plugin's ``routing-table.ts``: informal Newell Z-model terminology mapped
to canonical ``Ent-Reporting-*`` dataset names and their Power BI workspace
(``pbiWorkspaceName`` — the Power BI workspace, never the Azure Log Analytics workspace
the collector connects to).

Structure and behaviour preserved from the source, in particular:
  * 15 canonical entries. Two (``CMMS`` and ``OEE Monthly Reports``) are ``LOW``
    confidence: they exist here so the catalog build has a mapping target, but are
    EXCLUDED from the match index AND from the known-models list until the BI team
    verifies alias variants (tightening 26a / term-resolver.ts).
  * ``matchMode: "exact"`` on bare generic single-word variants ("sales", "finance",
    "HR", "marketing", "quality") so they do not containment-hijack longer unrelated
    questions.
  * The documented DTC <-> Ecomm data overlap, applied SYMMETRICALLY via
    ``ambiguousWith`` (bare "DTC" on the DTC entry; "online sales" / "digital sales" on
    the Ecomm entry).
  * ``connectionPath`` XMLA connect strings on the 8 entries that have them (Sales,
    Ecomm, Marketing, Ops-Finance, Quality, SCM, SLM, Walmart — tightening 26b).
  * ``TABLE_VERSION`` / ``LAST_REVIEWED`` exported as machine-readable constants
    (tightening 25h), not just a comment.

Governance: the data is review-controlled. Entries are dicts with camelCase keys,
mirroring the source JSON shape so downstream tool output round-trips identically.
"""
from __future__ import annotations

from typing import Dict, List

from .text_normalize import normalize_for_matching

TABLE_VERSION = "2.1.0"
LAST_REVIEWED = "2026-07-30"

# Shared annotation for the documented DTC <-> Ecomm data overlap, applied from both
# directions (bare "DTC" on the DTC entry; "online sales" / "digital sales" on Ecomm).
DTC_IN_ECOMM_REASON = (
    "DTC sales and POS data are documented as part of the Z.eCommerce model in addition "
    "to the standalone DTC model — see Z.eCommerce release notes."
)

_SHAREPOINT_SOURCE = (
    "Connecting to Model Z (SharePoint): "
    "https://newellco.sharepoint.com/teams/nb-analytics/SitePages/"
    "New%20Way%20of%20Connecting.aspx"
)

# Each entry is a dict with camelCase keys mirroring routing-table.ts RoutingEntry:
#   canonicalName, pbiWorkspaceName, confidence (HIGH|MEDIUM|LOW), variants[]
#   optional: catalogModelName, connectionPath
# Each variant is a dict mirroring RoutingVariant:
#   text, verified; optional: matchMode ("exact"|"containment"), source, ambiguousWith[]
ROUTING_TABLE: List[Dict] = [
    # ── Catalog-only entries (v2.1): LOW confidence, excluded from term matching ──
    {
        "canonicalName": "CMMS",
        "catalogModelName": "CMMS",
        "pbiWorkspaceName": "Enterprise Ops Finance",
        "confidence": "LOW",
        "variants": [],
    },
    {
        "canonicalName": "OEE Monthly Reports",
        "catalogModelName": "OEE",
        "pbiWorkspaceName": "Enterprise Ops Finance",
        "confidence": "LOW",
        "variants": [],
    },
    {
        "canonicalName": "Ent-Reporting-Sales",
        "catalogModelName": "Z_Sales",
        "pbiWorkspaceName": "Enterprise Sales",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Sales",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Sales", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "ZSALES", "verified": True},
            {"text": "sales", "verified": False, "matchMode": "exact"},
            {"text": "sales data", "verified": False},
            {"text": "sales model", "verified": False},
            {"text": "sales dataset", "verified": False},
            {"text": "sales reporting", "verified": False},
            {"text": "z model for sales", "verified": False},
            {"text": "sales report", "verified": False},
            {"text": "sales numbers", "verified": False},
            {"text": "invoice sales", "verified": False},
            {"text": "POS and sales", "verified": False},
            {"text": "sales workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Ecomm",
        "catalogModelName": "Ecomm",
        "pbiWorkspaceName": "Enterprise E-Comm",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise E-Comm",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.eCommerce", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "Z.Ecomm", "verified": True},
            {"text": "ZeComm", "verified": True},
            {
                "text": "Ze.Commerce",
                "verified": True,
                "source": "Typo found verbatim in Newell's own Z.eCommerce release notes",
            },
            {"text": "ecommerce", "verified": False},
            {"text": "e-commerce", "verified": False},
            {"text": "e comm", "verified": False},
            {"text": "ecomm", "verified": False},
            {"text": "ecommerce data", "verified": False},
            {"text": "ecommerce model", "verified": False},
            {"text": "ecommerce dataset", "verified": False},
            {"text": "ecommerce reporting", "verified": False},
            {
                "text": "online sales",
                "verified": False,
                "ambiguousWith": [
                    {
                        "canonicalName": "Ent-Reporting-DTC",
                        "pbiWorkspaceName": "Enterprise DTC",
                        "reason": DTC_IN_ECOMM_REASON,
                    }
                ],
            },
            {
                "text": "digital sales",
                "verified": False,
                "ambiguousWith": [
                    {
                        "canonicalName": "Ent-Reporting-DTC",
                        "pbiWorkspaceName": "Enterprise DTC",
                        "reason": DTC_IN_ECOMM_REASON,
                    }
                ],
            },
            {"text": "ecomm workspace", "verified": False},
            {"text": "z model for ecommerce", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Finance",
        "catalogModelName": "Finance",
        "pbiWorkspaceName": "Enterprise Finance",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Finance", "verified": True},
            {"text": "finance", "verified": False, "matchMode": "exact"},
            {"text": "finance data", "verified": False},
            {"text": "finance model", "verified": False},
            {"text": "finance dataset", "verified": False},
            {"text": "finance reporting", "verified": False},
            {"text": "AR aging", "verified": False},
            {"text": "accounts receivable", "verified": False},
            {"text": "finance workspace", "verified": False},
            {"text": "z model for finance", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Purchasing-Finance",
        "catalogModelName": "Purchasing-Finance",
        "pbiWorkspaceName": "Enterprise Finance",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.PurFin", "verified": True},
            {"text": "purchasing finance", "verified": False},
            {"text": "purfin", "verified": False},
            {"text": "pur fin", "verified": False},
            {"text": "purchasing data", "verified": False},
            {"text": "procurement finance", "verified": False},
            {"text": "procurement data", "verified": False},
            {"text": "material price variance", "verified": False},
            {"text": "MPV", "verified": False},
            {"text": "purchasing finance dataset", "verified": False},
            {"text": "z model for purchasing finance", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Profitability",
        "catalogModelName": "Profitability",
        "pbiWorkspaceName": "Enterprise Finance",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Profitability", "verified": True},
            {"text": "profitability", "verified": False},
            {"text": "profitability data", "verified": False},
            {"text": "profitability model", "verified": False},
            {"text": "profitability dataset", "verified": False},
            {"text": "PCM", "verified": False},
            {"text": "PCM data", "verified": False},
            {"text": "margin data", "verified": False},
            {"text": "profit data", "verified": False},
            {"text": "z model for profitability", "verified": False},
            {"text": "profitability workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-HR",
        "pbiWorkspaceName": "Enterprise HR",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.HR", "verified": True},
            {"text": "HR", "verified": False, "matchMode": "exact"},
            {"text": "HR reporting", "verified": False},
            {"text": "HR dataset", "verified": False},
            {"text": "HR model", "verified": False},
            {"text": "z model for HR", "verified": False},
            {"text": "HR data", "verified": False},
            {"text": "human resources", "verified": False},
            {"text": "human resources data", "verified": False},
            {"text": "people analytics", "verified": False},
            {"text": "HR workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Marketing",
        "catalogModelName": "Z_Marketing",
        "pbiWorkspaceName": "Enterprise Marketing",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Marketing",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Marketing", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "marketing", "verified": False, "matchMode": "exact"},
            {"text": "marketing data", "verified": False},
            {"text": "marketing model", "verified": False},
            {"text": "marketing dataset", "verified": False},
            {"text": "marketing reporting", "verified": False},
            {"text": "ad spend", "verified": False},
            {"text": "ad spend data", "verified": False},
            {"text": "campaign data", "verified": False},
            {"text": "z model for marketing", "verified": False},
            {"text": "marketing workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Ops-Finance",
        "catalogModelName": "Z_OpsFin",
        "pbiWorkspaceName": "Enterprise Ops Finance",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Ops Finance",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.OpsFin", "verified": True, "source": _SHAREPOINT_SOURCE},
            {
                "text": "Z.OpsFinance",
                "verified": True,
                "source": "Same model — one Confluence connection-string table used this spelling",
            },
            {"text": "ops finance", "verified": False},
            {"text": "opsfin", "verified": False},
            {"text": "ops fin", "verified": False},
            {"text": "operations finance", "verified": False},
            {"text": "4-wall", "verified": False},
            {"text": "OEE", "verified": False},
            {"text": "OEE data", "verified": False},
            {"text": "ops finance data", "verified": False},
            {"text": "ops finance dataset", "verified": False},
            {"text": "z model for ops finance", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Quality",
        "catalogModelName": "Z_Quality",
        "pbiWorkspaceName": "Enterprise Quality/Consumer Service",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Quality/Consumer Service",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Quality", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "quality", "verified": False, "matchMode": "exact"},
            {"text": "quality data", "verified": False},
            {"text": "quality model", "verified": False},
            {"text": "quality dataset", "verified": False},
            {"text": "quality reporting", "verified": False},
            {"text": "consumer complaints", "verified": False},
            {"text": "complaints data", "verified": False},
            {"text": "quality excellence", "verified": False},
            {"text": "z model for quality", "verified": False},
            {"text": "quality workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-SCM",
        "catalogModelName": "SCM",
        "pbiWorkspaceName": "Enterprise Supply Chain",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Supply Chain",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.SCM", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "zscm", "verified": True},
            {"text": "SCM", "verified": False},
            {"text": "supply chain", "verified": False},
            {"text": "supply chain data", "verified": False},
            {"text": "supply chain model", "verified": False},
            {"text": "supply chain dataset", "verified": False},
            {"text": "supply chain reporting", "verified": False},
            {"text": "z model for supply chain", "verified": False},
            {"text": "supply chain workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-SLM",
        "catalogModelName": "SLM",
        "pbiWorkspaceName": "Enterprise SLM",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise SLM",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.SLM", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "SLM", "verified": False},
            {"text": "service level metrics", "verified": True},
            {"text": "fill rate", "verified": True},
            {"text": "fill rate data", "verified": False},
            {"text": "SLM dataset", "verified": False},
            {"text": "SLM reporting", "verified": False},
            {"text": "z model for SLM", "verified": False},
            {"text": "SLM workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-Walmart",
        "catalogModelName": "Walmart",
        "pbiWorkspaceName": "Enterprise Walmart",
        "connectionPath": "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Walmart",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.Walmart", "verified": True, "source": _SHAREPOINT_SOURCE},
            {"text": "walmart", "verified": False},
            {"text": "walmart data", "verified": False},
            {"text": "walmart model", "verified": False},
            {"text": "walmart dataset", "verified": False},
            {"text": "walmart reporting", "verified": False},
            {"text": "walmart luminate", "verified": True},
            {"text": "z model for walmart", "verified": False},
            {"text": "walmart workspace", "verified": False},
        ],
    },
    {
        "canonicalName": "Ent-Reporting-DTC",
        "catalogModelName": "DTC",
        "pbiWorkspaceName": "Enterprise DTC",
        "confidence": "HIGH",
        "variants": [
            {"text": "Z.DTC", "verified": True},
            {
                "text": "DTC",
                "verified": False,
                "ambiguousWith": [
                    {
                        "canonicalName": "Ent-Reporting-Ecomm",
                        "pbiWorkspaceName": "Enterprise E-Comm",
                        "reason": DTC_IN_ECOMM_REASON,
                    }
                ],
            },
            {"text": "direct to consumer", "verified": False},
            {"text": "DTC data", "verified": False},
            {"text": "DTC model", "verified": False},
            {"text": "DTC dataset", "verified": False},
            {"text": "DTC reporting", "verified": False},
            {"text": "z model for DTC", "verified": False},
            {"text": "DTC workspace", "verified": False},
        ],
    },
]


class IndexedVariant:
    """One (entry, variant, normalized-text) triple in the match index."""

    __slots__ = ("entry", "variant", "normalized")

    def __init__(self, entry: Dict, variant: Dict, normalized: str) -> None:
        self.entry = entry
        self.variant = variant
        self.normalized = normalized


def _participating_entries() -> List[Dict]:
    """Entries that participate in matching — everything except LOW confidence."""
    return [e for e in ROUTING_TABLE if e.get("confidence") != "LOW"]


def match_index() -> List[IndexedVariant]:
    """Flattened list of every variant on every non-LOW entry, with normalized text.

    Built from ``confidence != "LOW"`` entries only: "treat as if it didn't exist" is
    enforced at the index-construction boundary, so the match logic never re-checks
    confidence (term-resolver.ts pattern).
    """
    index: List[IndexedVariant] = []
    for entry in _participating_entries():
        for variant in entry["variants"]:
            index.append(
                IndexedVariant(entry, variant, normalize_for_matching(variant["text"]))
            )
    return index


def canonical_index() -> List[IndexedVariant]:
    """Every participating entry's own canonical name, as an exact-match variant.

    A canonical name is the most specific thing a caller can say, yet it was in no index at all:
    ``Ent-Reporting-Sales`` normalizes to "ent reporting sales", which no variant equals, and the
    bare "sales" variant is ``matchMode: "exact"`` so the containment pass skipped it. The
    resolver therefore answered "No canonical model matched 'Ent-Reporting-Sales' … Known models:
    Ent-Reporting-Sales, …" — listing the string it had just failed to match. ``Ent-Reporting-DTC``
    failed differently: it containment-matched the curated ambiguous bare "DTC" and came back
    ambiguous with itself.
    """
    return [
        IndexedVariant(
            entry,
            {"text": entry["canonicalName"], "verified": True, "matchMode": "exact"},
            normalize_for_matching(entry["canonicalName"]),
        )
        for entry in _participating_entries()
    ]


def all_canonical_names() -> str:
    """Comma-joined canonical names for the no_match message — excludes LOW entries."""
    return ", ".join(e["canonicalName"] for e in _participating_entries())


def known_models() -> List[str]:
    """List of canonical names that participate in matching (excludes LOW entries)."""
    return [e["canonicalName"] for e in _participating_entries()]
