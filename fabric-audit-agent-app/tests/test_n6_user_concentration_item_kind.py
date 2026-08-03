"""N6 regression: user_concentration.py must exclude users whose topItems are ALL system items.

A user whose only work is on system-item-kind items (EventStream, Activator,
FabricEvents-CapacityUtilizationEvents) is a service/system account whose apparent
100% concentration is structural -- not a real finding.  The exclusion cross-references
item names from ``facts["items"]`` (which carries ``kind``) against the per-user ``topItems``
(which carries only names).
"""
from fabric_audit_agent.detectors.user_concentration import detect_user_concentration


def _make_facts(users, items=None, capacity=None):
    facts = {"users": users}
    if items is not None:
        facts["items"] = items
    if capacity is not None:
        facts["capacity"] = capacity
    return facts


# ---------------------------------------------------------------------------
# Core: pure-system-item users are excluded
# ---------------------------------------------------------------------------

def test_user_with_all_system_topitems_is_excluded():
    """A user whose topItems are ALL system items should be silently skipped --
    their concentration is structural, not a real workload finding."""
    users = [
        {"user": "fabric-svc@internal", "sharePct": 100.0, "cuSeconds": 400000,
         "topItems": [{"name": "Monitoring_Eventstream", "cuSeconds": 400000}],
         "itemCount": 1},
    ]
    items = [
        {"name": "Monitoring_Eventstream", "kind": "EventStream",
         "sharePct": 100.0, "cuSeconds": 400000},
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    assert flags == [], (
        "N6 regression: a pure-system-item user was flagged for concentration"
    )


def test_user_with_multiple_system_topitems_all_excluded():
    """A user working exclusively across multiple system items is still excluded."""
    users = [
        {"user": "system@internal", "sharePct": 90.0, "cuSeconds": 9000,
         "topItems": [
             {"name": "ES-Prod", "cuSeconds": 5000},
             {"name": "Cap-Events", "cuSeconds": 4000},
         ],
         "itemCount": 2},
    ]
    items = [
        {"name": "ES-Prod", "kind": "EventStream", "sharePct": 50.0, "cuSeconds": 5000},
        {"name": "Cap-Events", "kind": "FabricEvents-CapacityUtilizationEvents",
         "sharePct": 40.0, "cuSeconds": 4000},
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    assert flags == []


# ---------------------------------------------------------------------------
# Mix of system and real items: user is NOT excluded
# ---------------------------------------------------------------------------

def test_user_with_mix_of_system_and_real_topitems_is_not_excluded():
    """If even one topItem is a real (non-system) item, the user must NOT be excluded."""
    users = [
        {"user": "heavy@x.com", "sharePct": 50.0, "cuSeconds": 5000,
         "topItems": [
             {"name": "Monitoring_Eventstream", "cuSeconds": 3000},
             {"name": "Sales Model", "cuSeconds": 2000},
         ],
         "itemCount": 2},
    ]
    items = [
        {"name": "Monitoring_Eventstream", "kind": "EventStream",
         "sharePct": 30.0, "cuSeconds": 3000},
        {"name": "Sales Model", "kind": "Dataset",
         "sharePct": 20.0, "cuSeconds": 2000},
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    conc = [f for f in flags if f["type"] == "capacity.user-concentration"]
    assert len(conc) == 1
    assert conc[0]["resource"] == "heavy@x.com"


# ---------------------------------------------------------------------------
# Conservative: no items / no kind -> nobody excluded
# ---------------------------------------------------------------------------

def test_no_items_in_facts_means_nobody_excluded():
    """When facts["items"] is absent, the system-item-name set is empty and
    nobody is excluded (conservative -- no data = no exclusion)."""
    users = [
        {"user": "maybe-svc@co", "sharePct": 80.0, "cuSeconds": 8000,
         "topItems": [{"name": "Monitoring_Eventstream", "cuSeconds": 8000}],
         "itemCount": 1},
    ]
    flags = detect_user_concentration(_make_facts(users, items=None))
    conc = [f for f in flags if f["type"] == "capacity.user-concentration"]
    assert len(conc) == 1, (
        "Without facts['items'], the user should NOT be excluded"
    )


def test_items_without_kind_means_nobody_excluded():
    """When items exist but none have a ``kind`` field, nobody matches as system."""
    users = [
        {"user": "svc@co", "sharePct": 60.0, "cuSeconds": 6000,
         "topItems": [{"name": "Monitoring_Eventstream", "cuSeconds": 6000}],
         "itemCount": 1},
    ]
    items = [
        {"name": "Monitoring_Eventstream", "sharePct": 60.0, "cuSeconds": 6000},
        # No "kind" field -- conservative
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    conc = [f for f in flags if f["type"] == "capacity.user-concentration"]
    assert len(conc) == 1


# ---------------------------------------------------------------------------
# Case insensitivity: name matching is lowercased
# ---------------------------------------------------------------------------

def test_name_matching_is_case_insensitive():
    """topItems name and items name may differ in case; matching must be case-insensitive."""
    users = [
        {"user": "svc@internal", "sharePct": 100.0, "cuSeconds": 1000,
         "topItems": [{"name": "MONITORING_EVENTSTREAM", "cuSeconds": 1000}],
         "itemCount": 1},
    ]
    items = [
        {"name": "monitoring_eventstream", "kind": "EventStream",
         "sharePct": 100.0, "cuSeconds": 1000},
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    assert flags == [], "Case-insensitive name match should have excluded this user"


# ---------------------------------------------------------------------------
# Real users alongside excluded system users still get flagged
# ---------------------------------------------------------------------------

def test_real_user_still_flagged_when_system_user_is_excluded():
    """Exclusion is per-user, not a set-level short-circuit. A real heavy user in the same
    facts as a system user must still be flagged."""
    users = [
        {"user": "fabric-svc@internal", "sharePct": 60.0, "cuSeconds": 6000,
         "topItems": [{"name": "ES-Prod", "cuSeconds": 6000}], "itemCount": 1},
        {"user": "heavy@company.com", "sharePct": 40.0, "cuSeconds": 4000,
         "topItems": [{"name": "Sales Model", "cuSeconds": 4000}], "itemCount": 1},
    ]
    items = [
        {"name": "ES-Prod", "kind": "EventStream", "sharePct": 60.0, "cuSeconds": 6000},
        {"name": "Sales Model", "kind": "Dataset", "sharePct": 40.0, "cuSeconds": 4000},
    ]
    flags = detect_user_concentration(_make_facts(users, items))
    conc = [f for f in flags if f["type"] == "capacity.user-concentration"]
    assert len(conc) == 1
    assert conc[0]["resource"] == "heavy@company.com"
