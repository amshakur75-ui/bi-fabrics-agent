"""Tests for resolve.term_resolver — canonical/exact/containment matching, curated ambiguity,
LOW exclusion."""
from fabric_audit_agent.resolve import resolve_term
from fabric_audit_agent.resolve.routing_table import ROUTING_TABLE, known_models


def test_exact_match_resolved_with_connection_path():
    r = resolve_term("Z.Sales")
    assert r["status"] == "resolved"
    assert r["canonicalName"] == "Ent-Reporting-Sales"
    assert r["pbiWorkspaceName"] == "Enterprise Sales"
    assert r["matchedVariant"] == "Z.Sales"
    assert r["matchedVariantNormalized"] == "z sales"
    assert r["variantVerified"] is True
    assert r["entryConfidence"] == "HIGH"
    # 26q: connectionPath surfaced on the resolved result.
    assert r["connectionPath"] == "powerbi://api.powerbi.com/v1.0/myorg/Enterprise Sales"
    assert "Connect via XMLA" in r["message"]


def test_normalization_folds_case_and_punctuation():
    for form in ("z sales", "Z-SALES", "z.sales", "  Z.SALES  "):
        assert resolve_term(form)["canonicalName"] == "Ent-Reporting-Sales"


def test_resolved_without_connection_path_omits_key():
    r = resolve_term("Z.Finance")
    assert r["status"] == "resolved"
    assert r["canonicalName"] == "Ent-Reporting-Finance"
    assert "connectionPath" not in r  # Finance has no XMLA path


def test_containment_match_whole_word():
    r = resolve_term("who is querying z sales this week")
    assert r["status"] == "resolved"
    assert r["canonicalName"] == "Ent-Reporting-Sales"


def test_bare_generic_word_is_exact_only():
    # matchMode "exact": bare "sales" resolves alone, but must not hijack a longer question.
    assert resolve_term("sales")["status"] == "resolved"
    assert resolve_term("quality issues in the marketing pipeline")["status"] == "no_match"


def test_curated_ambiguity_dtc():
    r = resolve_term("DTC")
    assert r["status"] == "ambiguous"
    names = {c["canonicalName"] for c in r["ambiguity"]["candidates"]}
    assert names == {"Ent-Reporting-DTC", "Ent-Reporting-Ecomm"}
    assert "release notes" in r["ambiguity"]["reason"]


def test_symmetric_ambiguity_online_sales():
    r = resolve_term("online sales")
    assert r["status"] == "ambiguous"
    names = {c["canonicalName"] for c in r["ambiguity"]["candidates"]}
    assert names == {"Ent-Reporting-Ecomm", "Ent-Reporting-DTC"}


def test_unambiguous_dtc_forms_resolve():
    # "Z.DTC" and "direct to consumer" are documented unambiguous.
    assert resolve_term("Z.DTC")["status"] == "resolved"
    assert resolve_term("direct to consumer")["status"] == "resolved"


def test_low_confidence_entries_excluded():
    # LOW entries are invisible to matching: their canonical name is never returned.
    # "CMMS" has no token overlap with any participating entry -> no_match outright.
    assert resolve_term("CMMS")["status"] == "no_match"
    # "OEE Monthly Reports" is a LOW entry, but the STRING contains the legitimate
    # Ops-Finance variant "OEE" (whole-word containment) — so it resolves to
    # Ops-Finance, NEVER to the excluded LOW "OEE Monthly Reports" entry.
    oee = resolve_term("OEE Monthly Reports")
    assert oee.get("canonicalName") != "OEE Monthly Reports"
    if oee["status"] == "resolved":
        assert oee["canonicalName"] == "Ent-Reporting-Ops-Finance"
    # Neither LOW canonical name is ever the resolved target for its own name.
    for low in ("CMMS", "OEE Monthly Reports"):
        assert resolve_term(low).get("canonicalName") != low


def test_empty_and_unknown():
    assert resolve_term("")["status"] == "no_match"
    assert resolve_term("   ")["status"] == "no_match"
    r = resolve_term("something totally unrelated xyzzy")
    assert r["status"] == "no_match"
    assert "Known models" in r["message"]


def test_unverified_variant_carries_soft_cue():
    r = resolve_term("supply chain")  # verified: False on SCM
    assert r["status"] == "resolved"
    assert r["canonicalName"] == "Ent-Reporting-SCM"
    assert "unverified phrasing" in r["message"]


# ── Invariant: every CANONICAL NAME resolves to itself ────────────────────────────────
def test_every_canonical_name_resolves_to_itself():
    """The invariant below only iterated ``variants``, which is how this survived: canonical
    names are not variants, so 5 of the 13 participating models answered no_match for their own
    name and Ent-Reporting-DTC came back ambiguous with itself. The system prompt tells the agent
    to call resolve_term FIRST, so the failure surfaced as "No canonical model matched
    'Ent-Reporting-Sales' … Known models: Ent-Reporting-Sales, …".
    """
    for name in known_models():
        r = resolve_term(name)
        assert r["status"] == "resolved", f"{name} did not resolve: {r}"
        assert r["canonicalName"] == name
        assert r["matchedVariant"] == name
        assert r["variantVerified"] is True
        assert "ambiguous" not in r["message"]
    # Case and punctuation folding applies to canonical names too.
    assert resolve_term("ent reporting sales")["canonicalName"] == "Ent-Reporting-Sales"
    assert resolve_term("ENT_REPORTING_HR")["canonicalName"] == "Ent-Reporting-HR"


# ── Invariant: every registered variant resolves to itself, never a generic collision ──
def test_every_variant_resolves_or_curated_ambiguous():
    curated_texts = set()
    for e in ROUTING_TABLE:
        for v in e["variants"]:
            if v.get("ambiguousWith"):
                curated_texts.add(v["text"])

    for entry in ROUTING_TABLE:
        if entry["confidence"] == "LOW":
            continue
        for variant in entry["variants"]:
            r = resolve_term(variant["text"])
            assert r["status"] != "no_match", f"{variant['text']} failed to resolve"
            if r["status"] == "ambiguous":
                # Only the curated overlap variants may be ambiguous — never the generic
                # "unreviewed lexical collision" path.
                assert variant["text"] in curated_texts, variant["text"]
                assert "unreviewed lexical collision" not in r["ambiguity"]["reason"]
            else:
                assert r["status"] == "resolved"
