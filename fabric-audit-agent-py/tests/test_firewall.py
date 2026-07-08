"""Read-only ad-hoc KQL firewall (pure). Static rejection before any engine touch."""
import pytest
from fabric_audit_agent.query.firewall import validate_adhoc_kql, FirewallRejection


def _reject(kql):
    with pytest.raises(FirewallRejection) as ei:
        validate_adhoc_kql(kql)
    return ei.value


def test_clean_query_passes_unchanged():
    kql = 'CapacityEvents\n| where cap == "c1"\n| summarize sum(pct) by bin(ts, 1h)'
    assert validate_adhoc_kql(kql) == kql


def test_oversize_rejected():
    r = _reject("T | take 1 " + "x" * 10_001)
    assert r.stage == "length"


def test_top_level_semicolon_rejected_not_truncated():
    r = _reject("CapacityEvents | take 5; CapacityEvents | count")
    assert r.stage == "multi-statement"


def test_trailing_semicolon_rejected():
    r = _reject("CapacityEvents | take 5;")
    assert r.stage == "multi-statement"


def test_semicolon_inside_string_literal_is_fine():
    # A ';' inside a quoted literal is NOT a statement separator.
    kql = 'CapacityEvents | where note == "a; b" | take 1'
    assert validate_adhoc_kql(kql) == kql


def test_control_command_rejected():
    r = _reject(".drop table CapacityEvents")
    assert r.stage == "control-command"


def test_stacked_control_command_rejected():
    r = _reject("CapacityEvents | take 1 | .drop table X")
    assert r.stage == "control-command"


def test_tautology_rejected():
    r = _reject("CapacityEvents | where cap == 'x' or 1 == 1")
    assert r.stage == "control-command"   # assert_read_only_kql owns tautology


@pytest.mark.parametrize("kql", [
    "externaldata(x:string)[@'https://evil/x.csv']",
    "CapacityEvents | join (cluster('other').database('d').T) on cap",
    "CapacityEvents | join (database('other').T) on cap",
    "union workspace('other').PowerBIDatasetsWorkspace | take 1",
    "app('other').requests | take 1",
    "PowerBIDatasetsWorkspace | evaluate bag_unpack(x)",
])
def test_denied_operators_rejected(kql):
    assert _reject(kql).stage == "denied-operator"


def test_denied_keyword_inside_string_literal_passes():
    # 'externaldata'/'cluster(' appearing only inside a quoted literal must NOT reject.
    kql = 'CapacityEvents | where note == "see externaldata docs and cluster() usage" | take 1'
    assert validate_adhoc_kql(kql) == kql


def test_word_boundary_no_false_positive_on_appname():
    # 'app(' must not match inside an identifier like 'myapp(' — word boundary required.
    kql = "MyTable | extend v = myapp_metric | take 1"
    assert validate_adhoc_kql(kql) == kql


def test_legitimate_multiline_analytical_query_passes():
    kql = ("PowerBIDatasetsWorkspace\n"
           "| where TimeGenerated > ago(1d)\n"
           "| where OperationName == 'QueryEnd'\n"
           "| summarize total = sum(CpuTimeMs) by ExecutingUser\n"
           "| top 10 by total desc")
    assert validate_adhoc_kql(kql) == kql
