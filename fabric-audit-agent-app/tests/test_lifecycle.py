from fabric_audit_agent.lifecycle import apply_lifecycle, set_state


def _f(key, level="Warning"):
    return {"key": key, "what": "w", "score": {"level": level, "reason": "r"}}


def test_apply_lifecycle_splits_active_suppressed():
    r = apply_lifecycle([_f("a"), _f("b"), _f("c")], {"b": {"state": "resolved"}, "c": {"state": "acknowledged"}})
    active = [x["key"] for x in r["active"]]
    assert "a" in active and "c" in active           # open + acknowledged are active
    assert [x["key"] for x in r["suppressed"]] == ["b"]   # resolved suppressed
    assert r["active"][0]["lifecycle"]["state"] == "open"  # default applied


def test_apply_lifecycle_reactivates_expired_snooze():
    r = apply_lifecycle([_f("a")], {"a": {"state": "snoozed", "snoozeUntil": "2020-01-01T00:00:00Z"}}, now_ms=2_000_000_000_000)
    assert r["active"][0]["lifecycle"]["state"] == "open"
    assert r["active"][0]["lifecycle"]["snoozeUntil"] is None


def test_apply_lifecycle_future_snooze_stays_suppressed():
    r = apply_lifecycle([_f("a")], {"a": {"state": "snoozed", "snoozeUntil": "2099-01-01T00:00:00Z"}}, now_ms=2_000_000_000_000)
    assert r["suppressed"][0]["lifecycle"]["state"] == "snoozed"


def test_set_state_is_pure():
    states = {"a": {"state": "open"}}
    out = set_state(states, "b", "snoozed", {"snoozeUntil": "2099-01-01T00:00:00Z", "note": "later", "now": "2026-06-11"})
    assert out["b"] == {"state": "snoozed", "snoozeUntil": "2099-01-01T00:00:00Z", "note": "later", "since": "2026-06-11"}
    assert states == {"a": {"state": "open"}}   # input untouched


def test_apply_lifecycle_wontfix_suppressed():
    assert apply_lifecycle([_f("a")], {"a": {"state": "wontfix"}})["suppressed"][0]["key"] == "a"


def test_apply_lifecycle_nowms_zero_disables_expiry():
    r = apply_lifecycle([_f("a")], {"a": {"state": "snoozed", "snoozeUntil": "2020-01-01T00:00:00Z"}}, now_ms=0)
    assert r["suppressed"][0]["lifecycle"]["state"] == "snoozed"   # now_ms=0 -> no expiry check
