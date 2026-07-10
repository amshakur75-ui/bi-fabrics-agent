"""Adapter (ports) tests — cluster 8. All I/O is faked: no real network or SDK deps.

Mirrors the Node ``adapters/*.test.js`` intent: each port honours its contract, the
production adapters drive their injected client correctly, and the Claude reasoner falls
back to the KB on any API/parse error.
"""
import json

import pytest

from fabric_audit_agent.adapters import (
    create_mock_collector, create_file_delivery, create_local_store,
    create_lifecycle_store, create_ticketing_delivery, create_rest_collector,
    fetch_all_pages, create_claude_reasoner, create_teams_delivery, create_stub_reasoner,
)
from fabric_audit_agent.adapters.clients import EntraHttp
from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.config import DEFAULT_CONFIG
from fabric_audit_agent.kb import get_remediation


# ---------- collector.mock ----------
def test_mock_collector_reads_fixture(tmp_path):
    p = tmp_path / "estate.json"
    p.write_text(json.dumps({"capacity": {"capacityId": "P"}}), encoding="utf-8")
    assert create_mock_collector(str(p))["collect"]() == {"capacity": {"capacityId": "P"}}


# ---------- delivery.file ----------
def test_file_delivery_writes_pretty_json_creates_dirs_keeps_unicode(tmp_path):
    out = tmp_path / "nested" / "latest.json"
    env = {"summary": "x", "data": {"findings": [{"what": "GL 70% — incremental refresh"}]}}
    ret = create_file_delivery(str(out))["deliver"](env)
    assert ret == str(out) and out.exists()
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == env
    assert "\n  " in text            # indent=2
    assert "—" in text and "\\u2014" not in text   # ensure_ascii=False — em-dash kept literal like Node


# ---------- store.local ----------
def test_local_store_history_missing_returns_empty(tmp_path):
    assert create_local_store(str(tmp_path / "none.json"))["history"]() == []


def test_local_store_append_roundtrip_and_trim(tmp_path):
    store = create_local_store(str(tmp_path / "h" / "hist.json"), keep=2)
    assert store["append"]({"runAt": "1"}) == 1
    assert store["append"]({"runAt": "2"}) == 2
    assert store["append"]({"runAt": "3"}) == 2          # trimmed to keep=2
    assert [r["runAt"] for r in store["history"]()] == ["2", "3"]


def test_local_store_history_returns_immutable_snapshot(tmp_path):
    # Phase 6 contract: history() MUST return a fresh snapshot so a captured prev_history is not
    # retroactively mutated by a later in-run append. decide_alert (resolved/verdict/SLA change)
    # relies on run_unified_job capturing prev_history before run_audit appends the current run;
    # if history() returned a live reference, that capture would be silently corrupted.
    store = create_local_store(str(tmp_path / "h" / "hist.json"))
    store["append"]({"runAt": "1"})
    snap = store["history"]()
    store["append"]({"runAt": "2"})
    assert snap == [{"runAt": "1"}]                          # earlier snapshot unaffected by later append
    assert store["history"]() is not store["history"]()      # a fresh object each call


def test_local_store_append_is_atomic_no_tmp_residue(tmp_path):
    hist_path = tmp_path / "h" / "hist.json"
    store = create_local_store(str(hist_path))
    store["append"]({"runAt": "1"})
    store["append"]({"runAt": "2"})
    assert not (tmp_path / "h" / "hist.json.tmp").exists()
    assert json.loads(hist_path.read_text(encoding="utf-8")) == [{"runAt": "1"}, {"runAt": "2"}]


# ---------- lifecycle.store ----------
def test_lifecycle_store_missing_then_roundtrip(tmp_path):
    lc = create_lifecycle_store(str(tmp_path / "lc.json"))
    assert lc["load"]() == {}
    assert lc["save"]({"k": {"state": "snoozed"}}) == {"k": {"state": "snoozed"}}
    assert lc["load"]() == {"k": {"state": "snoozed"}}


# ---------- ticketing ----------
class _FakeIssueClient:
    def __init__(self):
        self.tickets = []

    def create_issue(self, ticket):
        self.tickets.append(ticket)


def test_ticketing_unknown_min_level_raises():
    with pytest.raises(ValueError):
        create_ticketing_delivery(_FakeIssueClient(), min_level="Bogus")


def test_ticketing_severity_gate_and_dedupe():
    client = _FakeIssueClient()
    tk = create_ticketing_delivery(client, min_level="Critical")
    findings = [
        {"key": "a", "score": {"level": "Critical"}, "what": "w", "where": "x", "fix": ["f"]},
        {"key": "b", "score": {"level": "Warning"}, "what": "w", "where": "x", "fix": ["f"]},   # below floor
        {"key": "c", "score": {"level": "Critical"}, "what": "w", "where": "x", "fix": ["f"]},   # deduped
    ]
    assert tk["open"](findings, already_ticketed={"c"}) == {"created": ["a"]}
    assert len(client.tickets) == 1


def test_ticketing_warning_floor_includes_warnings():
    client = _FakeIssueClient()
    tk = create_ticketing_delivery(client, min_level="Warning")
    findings = [{"key": "a", "score": {"level": "Warning"}, "what": "w", "where": "x", "fix": ["f"]}]
    assert tk["open"](findings)["created"] == ["a"]


# ---------- shared fake HTTP ----------
class _FakeHttp:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.get_calls = []
        self.post_calls = []

    def get_json(self, url):
        self.get_calls.append(url)
        return self.pages.get(url, {})

    def post_json(self, url, body):
        self.post_calls.append((url, body))
        return {"ok": True}


# ---------- collector.rest + fetch_all_pages ----------
def test_fetch_all_pages_follows_nextlink():
    http = _FakeHttp({"u1": {"value": [1, 2], "nextLink": "u2"}, "u2": {"value": [3], "nextLink": None}})
    assert fetch_all_pages(http, "u1") == [1, 2, 3]
    assert http.get_calls == ["u1", "u2"]


def test_fetch_all_pages_non_value_payload_appended():
    assert fetch_all_pages(_FakeHttp({"u1": {"id": "x"}}), "u1") == [{"id": "x"}]


def test_rest_collector_builds_facts_and_tolerates_unconfigured_domains():
    http = _FakeHttp({
        "cap": {"value": [{"displayName": "PROD", "tenantName": "Acme", "memoryGb": 64}]},
        "ds": {"value": [{"groupName": "W", "name": "M", "sizeBytes": 6_000_000_000}]},
    })
    facts = create_rest_collector(http, {"capacityUrl": "cap", "datasetsUrl": "ds"})["collect"]()
    assert facts["capacity"]["capacityId"] == "PROD" and facts["capacity"]["tenant"] == "Acme"
    assert facts["models"][0]["name"] == "M" and facts["models"][0]["sizeGB"] == 6.0
    assert facts["reports"] == [] and facts["pipelines"] == []   # unconfigured -> empty


def test_rest_collector_capacity_object_fallback():
    http = _FakeHttp({"cap": {"displayName": "C1"}})   # no .value -> use object itself
    facts = create_rest_collector(http, {"capacityUrl": "cap"})["collect"]()
    assert facts["capacity"]["capacityId"] == "C1"


# ---------- reasoner.claude ----------
def _opt_facts():
    return {"capacity": {"tenant": "Acme", "capacityId": "P", "sku": "F64", "memoryGB": 64,
                         "peakCuPct": 95, "peakAt": "t", "throttleMinutes": 20,
                         "refreshes": [{"workspace": "Fin", "dataset": "A", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 6},
                                       {"workspace": "Fin", "dataset": "B", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1},
                                       {"workspace": "Fin", "dataset": "C", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1}]}}


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.received = None

    def create(self, **kwargs):
        self.received = kwargs
        return {"content": [{"text": self._text}]}


class _FakeAnthropic:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


class _BoomAnthropic:
    class _M:
        def create(self, **kwargs):
            raise RuntimeError("network down")

    def __init__(self):
        self.messages = _BoomAnthropic._M()


def test_claude_reasoner_empty_flags_returns_empty():
    assert create_claude_reasoner(_FakeAnthropic("[]"))["reason"]({}, []) == []


def test_claude_reasoner_enriches_matched_id_and_kb_fallback_for_rest():
    facts = _opt_facts()
    flags = detect_all(facts, DEFAULT_CONFIG)
    assert len(flags) >= 2
    client = _FakeAnthropic('Here you go: [{"id":0,"why":"W0","impact":"I0","fix":["fa","fb"]}]')
    out = create_claude_reasoner(client)["reason"](facts, flags)
    assert out[0]["why"] == "W0" and out[0]["impact"] == "I0" and out[0]["fix"] == ["fa", "fb"]
    assert out[0]["reasonedBy"] == "claude"
    assert out[0]["key"] == f'{flags[0]["type"]}::{flags[0]["resource"]}'
    kb1 = get_remediation(flags[1]["type"])
    assert out[1]["why"] == kb1["rootCause"] and out[1]["impact"] == "Impact not assessed."
    assert "reasonedBy" not in out[1]
    # request carried the cache_control system block (prompt caching) + sanitized user payload
    assert client.messages.received["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert client.messages.received["model"] and client.messages.received["max_tokens"] == 1024


def test_claude_reasoner_api_error_falls_back_to_kb():
    facts = _opt_facts()
    flags = detect_all(facts, DEFAULT_CONFIG)
    out = create_claude_reasoner(_BoomAnthropic())["reason"](facts, flags)
    assert out and all("reasonedBy" not in f for f in out)
    assert out[0]["why"] == get_remediation(flags[0]["type"])["rootCause"]


def test_stub_reasoner_still_exported_from_adapters():
    out = create_stub_reasoner()["reason"](_opt_facts(), detect_all(_opt_facts(), DEFAULT_CONFIG))
    assert out and all("key" in f for f in out)


# ---------- delivery.teams ----------
def test_teams_delivery_posts_card():
    http = _FakeHttp()
    env = {"summary": "Audit", "data": {"verdict": {"decision": "optimize", "reason": "r"},
                                        "findings": [{"score": {"level": "Critical"}, "what": "w", "fix": ["f"]}]}}
    res = create_teams_delivery(http, "https://hook")["deliver"](env)
    assert res == {"delivered": True, "target": "https://hook", "sections": 3}
    assert http.post_calls[0][0] == "https://hook" and http.post_calls[0][1]["type"] == "message"


# ---------- clients.EntraHttp ----------
class _FakeResp:
    def __init__(self, payload, raise_on_json=False):
        self._payload = payload
        self._raise = raise_on_json

    def raise_for_status(self):
        pass

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.get_args = None
        self.post_args = None

    def get(self, url, headers=None, timeout=None):
        self.get_args = {"url": url, "headers": headers, "timeout": timeout}
        return self._resp

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_args = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return self._resp


def test_entra_http_get_json_attaches_bearer():
    sess = _FakeSession(_FakeResp({"value": [1]}))
    http = EntraHttp(lambda: "TOKEN", session=sess)
    assert http.get_json("https://api") == {"value": [1]}
    assert sess.get_args["headers"]["Authorization"] == "Bearer TOKEN"


def test_entra_http_post_json_tolerates_non_json_reply():
    sess = _FakeSession(_FakeResp(None, raise_on_json=True))
    http = EntraHttp(lambda: "T", session=sess)
    assert http.post_json("https://hook", {"a": 1}) is None
    assert sess.post_args["json"] == {"a": 1}
    assert sess.post_args["headers"]["Authorization"] == "Bearer T"
