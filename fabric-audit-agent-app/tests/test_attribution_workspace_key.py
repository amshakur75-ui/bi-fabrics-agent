"""N18 regression: ``enrich_items`` now supports workspace-aware (workspace, name) keys
in ``events_by_item`` to prevent cross-workspace attribution collisions when two items
share a display name across workspaces.

Backward-compat: existing name-only keys still work unchanged (all existing tests in
``test_attribution.py`` continue to pass with no edits)."""
from fabric_audit_agent.attribution import enrich_items


def test_workspace_aware_key_isolates_events_across_workspaces():
    """Two items share the display name 'Ent-Reporting-Sales' in different workspaces --
    per the real-world tenant data documented in GAPS-AND-ISSUES.md N18 (Enterprise Sales
    vs Enterprise Sales - DBX). Events keyed by (workspace, name) must NOT cross-contaminate."""
    items = [
        {"name": "Ent-Reporting-Sales", "workspace": "Enterprise Sales", "sharePct": 60},
        {"name": "Ent-Reporting-Sales", "workspace": "Enterprise Sales - DBX", "sharePct": 30},
    ]
    events_by_item = {
        ("Enterprise Sales", "Ent-Reporting-Sales"): [
            {"user": "alice@co", "cpuMs": 100, "interactive": True},
            {"user": "bob@co",   "cpuMs": 50,  "interactive": True},
        ],
        ("Enterprise Sales - DBX", "Ent-Reporting-Sales"): [
            {"user": "carol@co", "cpuMs": 200, "interactive": True},
        ],
    }
    out = enrich_items(items, events_by_item)
    sales_prod = next(i for i in out if i["workspace"] == "Enterprise Sales")
    sales_dbx = next(i for i in out if i["workspace"] == "Enterprise Sales - DBX")

    assert sales_prod["userCount"] == 2 and [u["user"] for u in sales_prod["topUsers"]] == ["alice@co", "bob@co"]
    assert sales_dbx["userCount"] == 1 and sales_dbx["topUsers"][0]["user"] == "carol@co"
    # And the two must NOT be pointing at the same event bucket.
    assert sales_prod["topUsers"] != sales_dbx["topUsers"]


def test_name_only_keys_still_work_backward_compat():
    """The old dict shape (``{name: events}``) must keep working -- every test in
    ``tests/test_attribution.py`` passes an events_by_item keyed by name, and the production
    ``collector_activity`` never keys by anything else."""
    items = [{"name": "GL Model", "workspace": "Fin", "sharePct": 70}]
    events_by_item = {"GL Model": [
        {"user": "a@x.com", "cpuMs": 10, "interactive": True},
    ]}
    out = enrich_items(items, events_by_item)
    assert out[0]["userCount"] == 1
    assert out[0]["topUsers"][0]["user"] == "a@x.com"


def test_workspace_key_wins_over_name_only_fallback():
    """When BOTH shapes are present, the workspace-strict (tuple) key takes precedence over
    the name-only fallback. This is important: a caller migrating from name-only to
    workspace-aware could leave a stale name-only entry behind, and the fresh workspace-keyed
    events should still win rather than the older stale bucket."""
    items = [{"name": "Sales", "workspace": "Ent-A", "sharePct": 50}]
    events_by_item = {
        ("Ent-A", "Sales"): [{"user": "correct@co", "cpuMs": 100, "interactive": True}],
        "Sales":            [{"user": "stale@co",   "cpuMs": 100, "interactive": True}],
    }
    out = enrich_items(items, events_by_item)
    assert out[0]["topUsers"][0]["user"] == "correct@co"


def test_no_workspace_on_item_falls_through_to_name_key():
    """An item without a workspace field just uses the name-only key -- the workspace-strict
    check simply doesn't match anything and the fallback kicks in cleanly."""
    items = [{"name": "OrphanItem", "sharePct": 50}]                # no workspace
    events_by_item = {"OrphanItem": [
        {"user": "u@x.com", "cpuMs": 1, "interactive": True},
    ]}
    out = enrich_items(items, events_by_item)
    assert out[0]["userCount"] == 1
    assert out[0]["topUsers"][0]["user"] == "u@x.com"
