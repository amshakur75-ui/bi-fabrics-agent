"""Tests for mcp_server._make_with_args -- the deployed-MCP arg-forwarding contract.

The union-signature wrapper FastMCP calls must forward only non-None values so handlers own
their own real defaults. Before this fix, `days`/`topN` had hard-coded non-None defaults
(30/5) baked into the wrapper's signature, so a handler could never observe "omitted" -- e.g.
spike_events' own topN=100/capacity_patterns' days=1 special-casing could never trigger.
"""
from fabric_audit_agent.mcp_server import _make_with_args


def _capturing_handler():
    captured = {}
    def handler(payload):
        captured["payload"] = payload
        return payload
    return handler, captured


def test_no_args_forwards_empty_payload_days_and_topn_not_forced():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool()
    # days/topN must NOT be force-defaulted to 30/5 -- the handler owns its own defaults.
    assert "days" not in captured["payload"]
    assert "topN" not in captured["payload"]
    assert captured["payload"] == {}


def test_passed_days_and_topn_are_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(days=7, topN=3)
    assert captured["payload"]["days"] == 7
    assert captured["payload"]["topN"] == 3


def test_hours_is_forwarded_when_passed():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(hours=6)
    assert captured["payload"]["hours"] == 6
    assert "days" not in captured["payload"]


def test_hours_fractional_is_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(hours=0.25)
    assert captured["payload"]["hours"] == 0.25


def test_start_and_end_are_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(start="2026-07-05T12:45:00Z", end="2026-07-05T13:00:00Z")
    assert captured["payload"]["start"] == "2026-07-05T12:45:00Z"
    assert captured["payload"]["end"] == "2026-07-05T13:00:00Z"


def test_all_new_optional_params_forwarded_when_given():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(format="columnar", order="recent", source="live", table="CapacityEvents", n=10)
    payload = captured["payload"]
    assert payload["format"] == "columnar"
    assert payload["order"] == "recent"
    assert payload["source"] == "live"
    assert payload["table"] == "CapacityEvents"
    assert payload["n"] == 10


def test_unset_optional_params_are_not_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(user="alice@co")
    payload = captured["payload"]
    assert payload == {"user": "alice@co"}
    for k in ("days", "topN", "hours", "start", "end", "format", "order", "source", "table", "n", "when"):
        assert k not in payload


def test_zero_values_are_forwarded_not_treated_as_unset():
    # 0 is meaningful (a 0-day/0-hour/0-index value), not "unset" -- must still be forwarded.
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(days=0, hours=0, n=0)
    payload = captured["payload"]
    assert payload["days"] == 0
    assert payload["hours"] == 0
    assert payload["n"] == 0


def test_empty_string_values_are_forwarded_not_treated_as_unset():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(user="", when="")
    payload = captured["payload"]
    assert payload["user"] == ""
    assert payload["when"] == ""


def test_when_still_forwarded_for_investigate_capacity_spike():
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(when="yesterday 12:45pm")
    assert captured["payload"]["when"] == "yesterday 12:45pm"


def test_item_is_forwarded_for_raw_events():
    # raw_events (Task 7) is the first tool with an 'item' scoping param -- must reach the
    # handler like the other union params, and stay absent when omitted.
    handler, captured = _capturing_handler()
    tool = _make_with_args(handler)
    tool(item="Sales")
    assert captured["payload"]["item"] == "Sales"

    handler2, captured2 = _capturing_handler()
    tool2 = _make_with_args(handler2)
    tool2(user="alice@co")
    assert "item" not in captured2["payload"]
