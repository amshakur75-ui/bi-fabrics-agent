"""Tier-2 collector composition — the Log Analytics attribution source is included when its env is
present (Step 2). Without LA the collector is capacity-events-only (why items=0 / concentration
never fired). Factories are monkeypatched so no real auth happens."""
import fabric_audit_agent.adapters.clients as clients
import fabric_audit_agent.adapters.collector_log_analytics as la_mod
import fabric_audit_agent.adapters.collector_capacity_events as ce_mod
from fabric_audit_agent.job import _build_tier2_collector


def _env():
    return {
        "FABRIC_TENANT_ID": "t", "FABRIC_CLIENT_ID": "c", "FABRIC_CLIENT_SECRET": "s",
        "FABRIC_CAPACITY_EVENTS_CLUSTER": "https://x.kusto", "FABRIC_CAPACITY_EVENTS_DB": "db",
        "FABRIC_LA_WORKSPACE_ID": "ws-guid",
    }


def _patch_capacity(monkeypatch):
    monkeypatch.setattr(clients, "build_kusto_query", lambda *a, **k: (lambda q: []))
    monkeypatch.setattr(ce_mod, "create_capacity_events_collector",
                        lambda q, cfg: {"collect": lambda: {"capacity": {"peakCuPct": 80}, "items": []}})


def test_tier2_collector_includes_la_when_configured(monkeypatch):
    _patch_capacity(monkeypatch)
    seen = {}
    monkeypatch.setattr(clients, "build_log_analytics_query",
                        lambda ws, *a, **k: seen.setdefault("ws", ws) or (lambda q: []))
    monkeypatch.setattr(la_mod, "create_log_analytics_collector",
                        lambda q, cfg: {"collect": lambda: {"items": [{"name": "Sales", "sharePct": 42}]}})
    col = _build_tier2_collector(_env(), window="5m")
    facts = col["collect"]()
    assert facts["capacity"]["peakCuPct"] == 80                       # capacity-events present
    assert any(it.get("sharePct") == 42 for it in facts["items"])      # LA per-user items present
    assert seen["ws"] == "ws-guid"                                     # workspace id threaded through


def test_tier2_collector_capacity_only_without_la(monkeypatch):
    _patch_capacity(monkeypatch)
    env = _env()
    del env["FABRIC_LA_WORKSPACE_ID"]
    col = _build_tier2_collector(env, window="5m")
    facts = col["collect"]()
    assert facts["items"] == []                                        # no LA -> no attribution items
