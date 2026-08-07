"""Plan 2.2/2.6 — the audit-rule gate layered on the ad-hoc KQL firewall.

``audit_adhoc_kql`` runs the ported named KQL audit rules + service-limit pre-flight ON TOP of
``validate_adhoc_kql`` (which is unchanged). Error-severity findings BLOCK (raise FirewallRejection,
stage 'audit-rule'); warnings + pre-flight risks are surfaced, never blocking. ``parse_kusto_error``
is re-exported from the firewall module for the execution path's error handling. kql_guard is
untouched."""
import pytest

from fabric_audit_agent.query.firewall import (
    audit_adhoc_kql,
    validate_adhoc_kql,
    FirewallRejection,
    parse_kusto_error,
)


def _blocking(kql):
    with pytest.raises(FirewallRejection) as ei:
        audit_adhoc_kql(kql)
    return ei.value


# ── blocking (error-severity) findings ───────────────────────────────────────────────
def test_hand_authored_eventtext_filter_blocks_with_audit_rule_stage():
    kql = ('PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n'
           '| where EventText has "invoice"\n| take 5')
    rej = _blocking(kql)
    assert rej.stage == "audit-rule"
    assert "CORRECT007" in rej.reason


def test_col_equals_null_blocks():
    rej = _blocking("CapacityEvents | where user == null | take 5")
    assert rej.stage == "audit-rule" and "CORRECT001" in rej.reason


def test_powerbi_query_without_time_filter_blocks():
    rej = _blocking("PowerBIDatasetsWorkspace | summarize count() by ExecutingUser")
    assert rej.stage == "audit-rule" and "PERF003_POWER_BI" in rej.reason


# ── warnings surface, never block ────────────────────────────────────────────────────
def test_contains_warning_surfaces_without_blocking():
    # PERF001 (contains -> has) is a WARNING: it must NOT raise, and must be surfaced.
    kql = 'CapacityEvents | where TimeGenerated > ago(1h) | where note contains "abc" | take 5'
    adv = audit_adhoc_kql(kql)
    assert any(w["ruleId"] == "PERF001" for w in adv["warnings"])
    assert isinstance(adv["risks"], list)
    assert "grade" in adv and "score" in adv


def test_clean_query_passes_with_no_warnings():
    kql = ("PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n"
           "| summarize c = count() by ExecutingUser\n| top 10 by c desc")
    adv = audit_adhoc_kql(kql)
    assert adv["warnings"] == []


# ── the static firewall's own contract is untouched by the new gate ──────────────────
def test_static_firewall_still_returns_kql_unchanged():
    kql = "CapacityEvents | where cap == 'c1' | take 5"
    assert validate_adhoc_kql(kql) == kql


# ── Kusto error mapping routed through the firewall module ───────────────────────────
def test_parse_kusto_error_maps_runaway_to_suggestions():
    r = parse_kusto_error("Query failed: E_RUNAWAY_QUERY memory limit exceeded")
    assert r["code"] == "E_RUNAWAY_QUERY" and r["suggestions"]


def test_parse_kusto_error_unknown_passthrough():
    r = parse_kusto_error("some unrecognized failure")
    assert r["code"] == "UNKNOWN" and r["suggestions"] == ["some unrecognized failure"]
