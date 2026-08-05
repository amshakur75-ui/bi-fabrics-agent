"""audit_alerts store — row mapping + in-memory state machine round-trip."""
from fabric_audit_agent.context_alerts import (
    _to_row, _from_row, create_alerts_store_memory,
)


def test_row_mapping_round_trips():
    alert = {
        "incidentKey": "throttle::capacity", "status": "active", "severity": "warn",
        "checkType": "throttle", "resource": "capacity", "chatId": "chat-1", "metric": 8.0,
        "firstAlertedAt": "t0", "lastAlertedAt": "t0", "lastRemindedAt": None,
        "resolvedAt": None, "escalationCount": 0, "materialityReason": "throttle 8m",
        "investigationSummary": "sustained throttle", "delivered": True, "runAt": "t0",
        "currentlyActive": None, "presenceCount": None,
    }
    row = _to_row(alert)
    assert row["incident_key"] == "throttle::capacity"
    assert row["check_type"] == "throttle" and row["metric"] == 8.0
    assert _from_row(row) == alert


def test_memory_store_upsert_query_resolve():
    store = create_alerts_store_memory()
    assert store["query_active"]() == {}

    a = {"incidentKey": "pressure::capacity", "status": "active", "severity": "warn",
         "checkType": "pressure", "metric": 130.0}
    store["upsert"](a)
    active = store["query_active"]()
    assert set(active) == {"pressure::capacity"}
    assert active["pressure::capacity"]["metric"] == 130.0

    # update in place (escalation)
    a2 = dict(a, metric=150.0, escalationCount=1)
    store["upsert"](a2)
    assert store["query_active"]()["pressure::capacity"]["metric"] == 150.0

    # resolve -> drops out of active
    store["resolve"]("pressure::capacity", "t1")
    assert store["query_active"]() == {}
    assert store["_data"]["pressure::capacity"]["status"] == "resolved"
    assert store["_data"]["pressure::capacity"]["resolvedAt"] == "t1"


def test_resolve_noop_when_absent():
    store = create_alerts_store_memory()
    store["resolve"]("nope::x", "t1")  # must not raise
    assert store["query_active"]() == {}
