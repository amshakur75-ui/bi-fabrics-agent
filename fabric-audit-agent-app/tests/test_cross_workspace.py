"""B4: cross-workspace pattern matching over detector flags."""
from fabric_audit_agent.detectors.cross_workspace import cross_workspace_patterns
from fabric_audit_agent.detectors import detect_all


def _flag(t, resource):
    return {"type": t, "resource": resource, "when": "", "evidence": {}, "what": t}


def test_same_pattern_across_three_workspaces_is_systemic():
    flags = [
        _flag("report.directquery", "Finance / A"),
        _flag("report.directquery", "Sales / B"),
        _flag("report.directquery", "Ops / C"),
        _flag("report.directquery", "Finance / D"),   # 4th flag but 3rd distinct workspace already
    ]
    out = cross_workspace_patterns(flags, min_workspaces=3)
    assert len(out) == 1
    p = out[0]
    assert p["type"] == "pattern.cross-workspace"
    assert p["evidence"]["patternType"] == "report.directquery"
    assert p["evidence"]["workspaceCount"] == 3
    assert p["evidence"]["workspaces"] == ["Finance", "Ops", "Sales"]   # distinct + sorted


def test_two_workspaces_below_threshold_is_not_systemic():
    flags = [_flag("model.bidirectional", "Finance / A"), _flag("model.bidirectional", "Sales / B")]
    assert cross_workspace_patterns(flags, min_workspaces=3) == []


def test_capacity_attribution_and_meta_are_excluded():
    flags = [
        _flag("capacity.throttle", "capacity"),
        _flag("concentration", "Finance / A"),
        _flag("cross_user", "Sales / B"),
        _flag("meta.detector-error", "x"),
    ] * 3   # even repeated across many workspaces, these types never cluster
    assert cross_workspace_patterns(flags, min_workspaces=2) == []


def test_detect_all_surfaces_cross_workspace_pattern():
    # 3 workspaces each with a DirectQuery report -> detect_all appends a pattern.cross-workspace flag
    facts = {"reports": [
        {"workspace": "Finance", "name": "A", "mode": "DirectQuery"},
        {"workspace": "Sales", "name": "B", "mode": "DirectQuery"},
        {"workspace": "Ops", "name": "C", "mode": "DirectQuery"},
    ]}
    types = {f["type"] for f in detect_all(facts)}
    assert "report.directquery" in types and "pattern.cross-workspace" in types


def test_result_sorted_by_breadth():
    flags = [
        _flag("report.directquery", "W1 / a"), _flag("report.directquery", "W2 / b"),
        _flag("report.directquery", "W3 / c"), _flag("report.directquery", "W4 / d"),
        _flag("refresh.chronic", "W1 / x"), _flag("refresh.chronic", "W2 / y"),
        _flag("refresh.chronic", "W3 / z"),
    ]
    out = cross_workspace_patterns(flags, min_workspaces=3)
    assert [p["evidence"]["patternType"] for p in out] == ["report.directquery", "refresh.chronic"]
