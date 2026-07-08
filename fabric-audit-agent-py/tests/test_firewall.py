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
    # NOTE: no @'...' / @"..." here — externaldata(...)[@'url'] is covered separately below;
    # a verbatim-string literal now rejects at the earlier "verbatim-string" stage, not this one.
    "CapacityEvents | join (cluster('other').database('d').T) on cap",
    "CapacityEvents | join (database('other').T) on cap",
    "union workspace('other').PowerBIDatasetsWorkspace | take 1",
    "app('other').requests | take 1",
    "PowerBIDatasetsWorkspace | evaluate bag_unpack(x)",
])
def test_denied_operators_rejected(kql):
    assert _reject(kql).stage == "denied-operator"


def test_externaldata_with_verbatim_url_rejected_at_verbatim_stage():
    # The classic externaldata() bypass uses a verbatim-string URL literal (@'...'); that now
    # trips the earlier verbatim-string stage rather than reaching the denied-operator stage —
    # still rejected, just caught sooner (and for a broader reason).
    assert _reject("externaldata(x:string)[@'https://evil/x.csv']").stage == "verbatim-string"


def test_denied_keyword_inside_string_literal_passes():
    # 'externaldata'/'cluster(' appearing only inside a quoted literal must NOT reject.
    kql = 'CapacityEvents | where note == "see externaldata docs and cluster() usage" | take 1'
    assert validate_adhoc_kql(kql) == kql


def test_word_boundary_no_false_positive_on_appname():
    # 'app(' must not match inside an identifier like 'myapp(' — word boundary required.
    # The identifier is immediately followed by '(' so a NAIVE substring check for 'app('
    # WOULD false-reject here; only the \b-anchored regex passes it. This discriminates.
    kql = "MyTable | extend v = myapp(col) | take 1"
    assert validate_adhoc_kql(kql) == kql


def test_external_table_denied():
    # external_table('T') references a pre-registered external table — an external-read escape
    # sibling to externaldata; denied.
    with pytest.raises(FirewallRejection) as ei:
        validate_adhoc_kql("external_table('T') | take 1")
    assert ei.value.stage == "denied-operator"


def test_legitimate_multiline_analytical_query_passes():
    kql = ("PowerBIDatasetsWorkspace\n"
           "| where TimeGenerated > ago(1d)\n"
           "| where OperationName == 'QueryEnd'\n"
           "| summarize total = sum(CpuTimeMs) by ExecutingUser\n"
           "| top 10 by total desc")
    assert validate_adhoc_kql(kql) == kql


# --- verbatim-string bypass (final-review C-1) -----------------------------------------------
# KQL verbatim strings (@"..."/@'...') are NOT modeled by the regular-string state machines in
# kql_guard (first_statement / _strip_string_literals): '\' is literal in a verbatim string and
# the string closes at the very next quote, but the state machine still thinks it's escaping.
# A verbatim string ending in a literal '\"' therefore makes the state machine believe the string
# never closes, so everything after it is treated as "inside a string" and skipped by every later
# stage -- while the real Kusto/LA engine closes the string right there and executes the trailing
# text. These four are proven bypasses that must now be rejected at the "verbatim-string" stage.

def test_verbatim_string_bypass_cross_database_read_rejected():
    kql = 'CapacityEvents | where Msg == @"x\\" | union database(\'SecretDB\').SecretTable'
    assert _reject(kql).stage == "verbatim-string"


def test_verbatim_string_bypass_cross_cluster_read_rejected():
    kql = ("T | where m == @\"a\\\" | where cluster('other.kusto.windows.net')"
           ".database('d').X")
    assert _reject(kql).stage == "verbatim-string"


def test_verbatim_string_bypass_multi_statement_control_command_rejected():
    kql = 'T | where m == @"a\\" ; .drop table Foo'
    assert _reject(kql).stage == "verbatim-string"


def test_verbatim_string_bypass_single_quote_variant_rejected():
    kql = "T | where m == @'a\\' | union database('D').T"
    assert _reject(kql).stage == "verbatim-string"


def test_normal_regular_string_query_still_passes():
    # Guard: the verbatim-string stage must not false-reject ordinary quoted strings.
    kql = 'CapacityEvents | where note == "a normal string" | take 1'
    assert validate_adhoc_kql(kql) == kql


def test_double_quote_verbatim_marker_rejected():
    assert _reject('T | where m == @"plain verbatim" | take 1').stage == "verbatim-string"


def test_single_quote_verbatim_marker_rejected():
    assert _reject("T | where m == @'plain verbatim' | take 1").stage == "verbatim-string"
