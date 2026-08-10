"""audit_alerts store — row mapping + in-memory state machine round-trip."""
from fabric_audit_agent.context_alerts import (
    _to_row, _from_row, create_alerts_store_memory, create_alerts_store_delta,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSpark:
    """Records SQL issued; answers DESCRIBE with a fixed column list, SELECT with no rows."""
    def __init__(self, existing_cols):
        self.existing = existing_cols
        self.sqls = []

    def sql(self, q):
        self.sqls.append(q)
        if q.strip().upper().startswith("DESCRIBE"):
            return _FakeResult([{"col_name": c} for c in self.existing])
        return _FakeResult([])


def test_delta_store_self_heals_missing_columns():
    # A pre-existing table missing the two hysteresis/currently-active columns must be ALTERed to add
    # them (autoMerge proved unreliable) — otherwise the streak counter can't persist and attribution
    # never promotes. Idempotent: the ensure runs once per store instance.
    legacy = ["incident_key", "status", "severity", "check_type", "resource", "chat_id", "metric",
              "first_alerted_at", "last_alerted_at", "last_reminded_at", "resolved_at",
              "escalation_count", "materiality_reason", "investigation_summary", "delivered", "run_at"]
    fake = _FakeSpark(legacy)
    store = create_alerts_store_delta("cat", "sch", spark=fake)

    store["query_active"]()   # triggers the one-time self-heal
    alters = [q for q in fake.sqls if "ALTER TABLE" in q]
    assert any("currently_active" in q and "BOOLEAN" in q for q in alters)
    assert any("presence_count" in q and "INT" in q for q in alters)

    n = len(alters)
    store["query_pending"]()  # second call must NOT re-ALTER (ensured once)
    assert len([q for q in fake.sqls if "ALTER TABLE" in q]) == n


def test_row_mapping_round_trips():
    alert = {
        "incidentKey": "capacity::capacity", "status": "active", "severity": "warn",
        "checkType": "throttle", "resource": "capacity", "chatId": "chat-1", "metric": 8.0,
        "firstAlertedAt": "t0", "lastAlertedAt": "t0", "lastRemindedAt": None,
        "resolvedAt": None, "escalationCount": 0, "materialityReason": "throttle 8m",
        "investigationSummary": "sustained throttle", "delivered": True, "runAt": "t0",
        "currentlyActive": None, "presenceCount": None,
        # Design A' capacity-incident state — MUST round-trip or the dedup silently dies
        # in production (see test_alerts_store_delta_fidelity.py for the behavioral proof).
        "absenceCount": 3, "signalTypes": ["pressure", "throttle"], "throttleMinutes": 8.0,
    }
    row = _to_row(alert)
    assert row["incident_key"] == "capacity::capacity"
    assert row["check_type"] == "throttle" and row["metric"] == 8.0
    assert row["absence_count"] == 3 and row["throttle_minutes"] == 8.0
    # signalTypes is a LIST in the dict but a JSON STRING in the Delta row — handing
    # createDataFrame a raw list against the StringType column raises inside upsert(),
    # which has no try/except and would drop the alert entirely.
    assert row["signal_types"] == '["pressure","throttle"]'
    assert _from_row(row) == alert


def test_memory_store_upsert_query_resolve():
    store = create_alerts_store_memory()
    assert store["query_active"]() == {}

    a = {"incidentKey": "capacity::capacity", "status": "active", "severity": "warn",
         "checkType": "pressure", "metric": 130.0}
    store["upsert"](a)
    active = store["query_active"]()
    assert set(active) == {"capacity::capacity"}
    assert active["capacity::capacity"]["metric"] == 130.0

    # update in place (escalation)
    a2 = dict(a, metric=150.0, escalationCount=1)
    store["upsert"](a2)
    assert store["query_active"]()["capacity::capacity"]["metric"] == 150.0

    # resolve -> drops out of active
    store["resolve"]("capacity::capacity", "t1")
    assert store["query_active"]() == {}
    assert store["_data"]["capacity::capacity"]["status"] == "resolved"
    assert store["_data"]["capacity::capacity"]["resolvedAt"] == "t1"


def test_resolve_noop_when_absent():
    store = create_alerts_store_memory()
    store["resolve"]("nope::x", "t1")  # must not raise
    assert store["query_active"]() == {}
