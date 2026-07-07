"""Tests for mcp_server._make_tool_fn -- the deployed-MCP per-tool signature + arg-forwarding contract.

FastMCP derives the schema MCP clients see from the wrapper's function SIGNATURE (via
``inspect.signature``, honoring ``__signature__``), not from the input_schema dict. So
``_make_tool_fn`` builds a signature that mirrors each tool's authored ``input_schema`` exactly:
required props have no default (the client MUST supply them); optional props default to None and
are dropped from the payload so the handler owns its own real default (e.g. spike_events'
topN=5/capacity_patterns' days=1 special-casing could never trigger if a wrapper forced a value).

Only non-None values are forwarded (nullish, not falsy -- 0/""/False are meaningful and stay).
This replaces the earlier ``_make_with_args`` union-signature wrapper, which advertised phantom
params on every tool and lost required-enforcement.
"""
import inspect

from fabric_audit_agent.mcp_server import _make_tool_fn


def _capturing_handler():
    captured = {}
    def handler(payload):
        captured["payload"] = payload
        return payload
    return handler, captured


def _schema(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or []}


# The event-tool window props (days/topN/hours/start/end/format/order/source/table/n) mirror the
# real input_schemas the arg-taking tools authored.
_EVENT_SCHEMA = _schema({
    "user": {"type": "string"}, "item": {"type": "string"},
    "days": {"type": "integer"}, "topN": {"type": "integer"},
    "hours": {"type": "number"}, "start": {"type": "string"}, "end": {"type": "string"},
    "format": {"type": "string"}, "order": {"type": "string"},
    "source": {"type": "string"}, "table": {"type": "string"}, "n": {"type": "integer"},
})


def test_no_props_forwards_no_payload_arg():
    # A no-arg tool (empty schema) takes no params and calls handler() with no payload.
    captured = {}
    def handler():
        captured["called"] = True
        return {}
    tool = _make_tool_fn(handler, _schema({}))
    tool()
    assert captured["called"] is True
    assert list(inspect.signature(tool).parameters) == []


def test_no_args_forwards_empty_payload_days_and_topn_not_forced():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool()
    # days/topN must NOT be force-defaulted to 30/5 -- the handler owns its own defaults.
    assert "days" not in captured["payload"]
    assert "topN" not in captured["payload"]
    assert captured["payload"] == {}


def test_passed_days_and_topn_are_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(days=7, topN=3)
    assert captured["payload"]["days"] == 7
    assert captured["payload"]["topN"] == 3


def test_hours_is_forwarded_when_passed():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(hours=6)
    assert captured["payload"]["hours"] == 6
    assert "days" not in captured["payload"]


def test_hours_fractional_is_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(hours=0.25)
    assert captured["payload"]["hours"] == 0.25


def test_start_and_end_are_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(start="2026-07-05T12:45:00Z", end="2026-07-05T13:00:00Z")
    assert captured["payload"]["start"] == "2026-07-05T12:45:00Z"
    assert captured["payload"]["end"] == "2026-07-05T13:00:00Z"


def test_all_new_optional_params_forwarded_when_given():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(format="columnar", order="recent", source="live", table="CapacityEvents", n=10)
    payload = captured["payload"]
    assert payload["format"] == "columnar"
    assert payload["order"] == "recent"
    assert payload["source"] == "live"
    assert payload["table"] == "CapacityEvents"
    assert payload["n"] == 10


def test_unset_optional_params_are_not_forwarded():
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(user="alice@co")
    payload = captured["payload"]
    assert payload == {"user": "alice@co"}
    for k in ("days", "topN", "hours", "start", "end", "format", "order", "source", "table", "n"):
        assert k not in payload


def test_zero_values_are_forwarded_not_treated_as_unset():
    # 0 is meaningful (a 0-day/0-hour/0-index value), not "unset" -- must still be forwarded.
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(days=0, hours=0, n=0)
    payload = captured["payload"]
    assert payload["days"] == 0
    assert payload["hours"] == 0
    assert payload["n"] == 0


def test_empty_string_values_are_forwarded_not_treated_as_unset():
    handler, captured = _capturing_handler()
    schema = _schema({"user": {"type": "string"}, "when": {"type": "string"}})
    tool = _make_tool_fn(handler, schema)
    tool(user="", when="")
    payload = captured["payload"]
    assert payload["user"] == ""
    assert payload["when"] == ""


def test_when_still_forwarded_for_investigate_capacity_spike():
    handler, captured = _capturing_handler()
    schema = _schema({"when": {"type": "string"}, "days": {"type": "integer"}})
    tool = _make_tool_fn(handler, schema)
    tool(when="yesterday 12:45pm")
    assert captured["payload"]["when"] == "yesterday 12:45pm"


def test_item_is_forwarded_for_raw_events():
    # raw_events (Task 7) carries an 'item' scoping param -- must reach the handler, and stay
    # absent when omitted.
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, _EVENT_SCHEMA)
    tool(item="Sales")
    assert captured["payload"]["item"] == "Sales"

    handler2, captured2 = _capturing_handler()
    tool2 = _make_tool_fn(handler2, _EVENT_SCHEMA)
    tool2(user="alice@co")
    assert "item" not in captured2["payload"]


def test_surge_users_and_cu_spike_pct_forwarded_for_capacity_patterns():
    # Task 10: capacity_patterns gains tool-tunable surgeUsers/cuSpikePct -- must reach the
    # handler, and stay absent when omitted.
    schema = _schema({"days": {"type": "integer"},
                      "surgeUsers": {"type": "integer"}, "cuSpikePct": {"type": "number"}})
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, schema)
    tool(surgeUsers=2, cuSpikePct=55.0)
    assert captured["payload"]["surgeUsers"] == 2
    assert captured["payload"]["cuSpikePct"] == 55.0

    handler2, captured2 = _capturing_handler()
    tool2 = _make_tool_fn(handler2, schema)
    tool2()
    assert "surgeUsers" not in captured2["payload"]
    assert "cuSpikePct" not in captured2["payload"]


def test_surge_users_zero_forwarded_not_treated_as_unset():
    # 0 is a meaningful threshold value, not "unset" -- must still be forwarded (nullish, not
    # falsy semantics, matching the wrapper's contract).
    schema = _schema({"surgeUsers": {"type": "integer"}, "cuSpikePct": {"type": "number"}})
    handler, captured = _capturing_handler()
    tool = _make_tool_fn(handler, schema)
    tool(surgeUsers=0, cuSpikePct=0.0)
    assert captured["payload"]["surgeUsers"] == 0
    assert captured["payload"]["cuSpikePct"] == 0.0


def test_signature_mirrors_schema_required_and_optional():
    # The advertised signature is per-tool: required props have no default (client MUST supply),
    # optional props default to None. No phantom union params.
    schema = _schema({"user": {"type": "string"}, "days": {"type": "integer"}},
                     required=["user"])
    handler, _ = _capturing_handler()
    tool = _make_tool_fn(handler, schema)
    params = inspect.signature(tool).parameters
    assert set(params) == {"user", "days"}
    assert params["user"].default is inspect.Parameter.empty   # required
    assert params["days"].default is None                      # optional
    # No phantom params from other tools.
    assert "surgeUsers" not in params
    assert "when" not in params


def test_required_prop_has_no_default_in_advertised_signature():
    # Required-enforcement is what FastMCP reads off the advertised __signature__: a required
    # prop has NO default, so the MCP client MUST supply it (vs. the old union wrapper, where
    # every param defaulted to None and a missing 'user' silently became zeros). The wrapper's
    # runtime body is **kwargs, so enforcement lives in the signature FastMCP validates against.
    schema = _schema({"user": {"type": "string"}}, required=["user"])
    handler, _ = _capturing_handler()
    tool = _make_tool_fn(handler, schema)
    param = inspect.signature(tool).parameters["user"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
