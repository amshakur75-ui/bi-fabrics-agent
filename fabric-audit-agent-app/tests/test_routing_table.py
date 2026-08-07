"""Tests for resolve.routing_table — structure, versioning, LOW exclusion, invariants."""
from fabric_audit_agent.resolve import normalize_for_matching as norm
from fabric_audit_agent.resolve.routing_table import (
    LAST_REVIEWED,
    ROUTING_TABLE,
    TABLE_VERSION,
    all_canonical_names,
    known_models,
    match_index,
)

_CONNECTION_PATH_MODELS = {
    "Ent-Reporting-Sales",
    "Ent-Reporting-Ecomm",
    "Ent-Reporting-Marketing",
    "Ent-Reporting-Ops-Finance",
    "Ent-Reporting-Quality",
    "Ent-Reporting-SCM",
    "Ent-Reporting-SLM",
    "Ent-Reporting-Walmart",
}
_LOW_MODELS = {"CMMS", "OEE Monthly Reports"}


def test_version_constants():
    assert TABLE_VERSION == "2.1.0"
    assert LAST_REVIEWED == "2026-07-30"


def test_fifteen_entries_total():
    # 25f: 15 canonical model entries (13 participating + 2 LOW).
    assert len(ROUTING_TABLE) == 15


def test_low_entries_excluded_from_matching_and_known_models():
    # 26a: CMMS and OEE Monthly Reports are LOW — present in the table but invisible to matching.
    canonical = {e["canonicalName"] for e in ROUTING_TABLE}
    assert _LOW_MODELS <= canonical  # present in the file
    km = set(known_models())
    assert km.isdisjoint(_LOW_MODELS)  # excluded from known models
    assert len(known_models()) == 13
    # Excluded from the match index as well.
    index_entries = {iv.entry["canonicalName"] for iv in match_index()}
    assert index_entries.isdisjoint(_LOW_MODELS)
    # Excluded from the no_match message string.
    for low in _LOW_MODELS:
        assert low not in all_canonical_names()


def test_connection_path_on_exactly_the_eight_entries():
    # 26b: exactly 8 entries carry an XMLA connectionPath.
    with_path = {e["canonicalName"] for e in ROUTING_TABLE if "connectionPath" in e}
    assert with_path == _CONNECTION_PATH_MODELS
    for e in ROUTING_TABLE:
        if e["canonicalName"] in _CONNECTION_PATH_MODELS:
            assert e["connectionPath"].startswith("powerbi://api.powerbi.com/v1.0/myorg/")


def test_hr_has_no_catalog_model_name():
    hr = next(e for e in ROUTING_TABLE if e["canonicalName"] == "Ent-Reporting-HR")
    assert "catalogModelName" not in hr
    # Every other entry has a catalogModelName.
    for e in ROUTING_TABLE:
        if e["canonicalName"] != "Ent-Reporting-HR":
            assert e.get("catalogModelName")


# ── Invariant: no duplicate normalized variants per entry ──────────────────────────
def test_no_duplicate_normalized_variants_per_entry():
    for entry in ROUTING_TABLE:
        normalized = [norm(v["text"]) for v in entry["variants"]]
        assert len(normalized) == len(set(normalized)), (
            f"duplicate normalized variant in {entry['canonicalName']}: {normalized}"
        )


# ── Invariant: no uncurated cross-entry lexical collisions ─────────────────────────
def test_no_uncurated_cross_entry_exact_collisions():
    # Every normalized variant across the participating index maps to exactly one
    # canonical entry — there is no uncurated lexical collision (term-resolver.ts's
    # generic safety net is unreachable by the locked table).
    by_norm = {}
    for iv in match_index():
        by_norm.setdefault(iv.normalized, set()).add(iv.entry["canonicalName"])
    collisions = {n: names for n, names in by_norm.items() if len(names) > 1}
    assert collisions == {}, f"uncurated cross-entry collisions: {collisions}"


# ── Invariant: every ambiguousWith target names a real entry ───────────────────────
def test_every_ambiguous_with_target_is_a_real_entry():
    canonical = {e["canonicalName"] for e in ROUTING_TABLE}
    workspaces = {e["canonicalName"]: e["pbiWorkspaceName"] for e in ROUTING_TABLE}
    found_any = False
    for entry in ROUTING_TABLE:
        for variant in entry["variants"]:
            for ref in variant.get("ambiguousWith", []):
                found_any = True
                assert ref["canonicalName"] in canonical, ref
                assert ref["reason"]
                # Workspace annotation must match the real target entry.
                assert workspaces[ref["canonicalName"]] == ref["pbiWorkspaceName"]
    assert found_any  # the DTC<->Ecomm overlap must exist


def test_dtc_ecomm_overlap_is_symmetric():
    # The documented overlap is annotated from both directions (26 / v2 symmetry note).
    dtc = next(e for e in ROUTING_TABLE if e["canonicalName"] == "Ent-Reporting-DTC")
    ecomm = next(e for e in ROUTING_TABLE if e["canonicalName"] == "Ent-Reporting-Ecomm")
    dtc_bare = next(v for v in dtc["variants"] if v["text"] == "DTC")
    assert dtc_bare["ambiguousWith"][0]["canonicalName"] == "Ent-Reporting-Ecomm"
    ecomm_ambig = [v for v in ecomm["variants"] if v.get("ambiguousWith")]
    assert {v["text"] for v in ecomm_ambig} == {"online sales", "digital sales"}
    for v in ecomm_ambig:
        assert v["ambiguousWith"][0]["canonicalName"] == "Ent-Reporting-DTC"
