"""Egress chokepoint: key+shape-aware redaction + sensitivity floor + findings-targeted size cap.
Pure stdlib, deterministic. See docs/superpowers/specs/2026-07-09-egress-chokepoint-design.md."""
import copy

from fabric_audit_agent.egress import apply_egress_controls, disclosure_line


# ---------------------------------------------------------------------------
# Key-aware redaction
# ---------------------------------------------------------------------------

def test_key_aware_client_secret_masked():
    payload = {"clientSecret": "s3cr3t"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["clientSecret"] == "***"
    assert meta["secretsRedacted"] == 1


def test_key_aware_account_key_masked_case_insensitive_key():
    payload = {"AccountKey": "YWJj=="}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["AccountKey"] == "***"
    assert meta["secretsRedacted"] == 1


# ---------------------------------------------------------------------------
# Shape-aware redaction (regardless of key)
# ---------------------------------------------------------------------------

def test_shape_aware_connection_string_masked():
    payload = {"conn": "Server=x;AccountKey=YWJj==;Pwd=p"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["conn"] == "***"
    assert meta["secretsRedacted"] == 1


def test_shape_aware_bare_jwt_masked_regardless_of_key():
    payload = {"note": "eyJhbGciOiJI.eyJzdWIi.sig123"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["note"] == "***"
    assert meta["secretsRedacted"] == 1


def test_shape_aware_long_base64_blob_masked():
    # A real base64 token: long, base64 alphabet, AND carrying base64 special chars (+ / =).
    blob = "TWFuIGlzIGRpc3Rpbmd1aXNoZWQ+by9mcm9tIGFuaW1hbHM=" + "AB12cd34"
    payload = {"blob": blob}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["blob"] == "***"
    assert meta["secretsRedacted"] == 1


def test_long_plain_name_is_NOT_masked_names_pass():
    # Regression (plan-review Important): a long space-free PascalCase item/dataset name has no
    # base64 special char, so it must PASS (approved names-pass rule) -- length alone must not mask.
    name = "EnterpriseReportingSalesConsolidatedGlobalMonthlyView"  # 53 chars, letters only
    payload = {"data": {"findings": [{"dataset": name, "workspace": "Fin-Enterprise-Reporting-Prod-2026"}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0]["dataset"] == name
    assert safe["data"]["findings"][0]["workspace"] == "Fin-Enterprise-Reporting-Prod-2026"
    assert meta["secretsRedacted"] == 0


def test_finding_key_identity_field_survives():
    # Regression (plan-review Critical): a finding's identity field is literally named "key" and is
    # propagated into data.roadmap/suppressed/sla/accountability/digest. It is NOT a secret and must
    # survive -- masking it would break card/report/lifecycle correlation. "key" is not key-aware.
    payload = {"data": {
        "findings": [{"key": "capacity.throttle::CapacityX", "what": "throttled"}],
        "roadmap": [{"rank": 1, "key": "capacity.throttle::CapacityX", "level": "Critical"}],
        "suppressed": [{"key": "model.oversized::DatasetY"}],
    }}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0]["key"] == "capacity.throttle::CapacityX"
    assert safe["data"]["roadmap"][0]["key"] == "capacity.throttle::CapacityX"
    assert safe["data"]["suppressed"][0]["key"] == "model.oversized::DatasetY"
    # but a genuinely secret-named key is still masked
    s2, _ = apply_egress_controls({"clientSecret": "s3cr3t", "accountKey": "abc"}, sink="t")
    assert s2["clientSecret"] == "***" and s2["accountKey"] == "***"


# ---------------------------------------------------------------------------
# In-string redaction (delegates to redact_secrets)
# ---------------------------------------------------------------------------

def test_in_string_sas_sig_masked_nested():
    payload = {"data": {"findings": [{"link": "https://x?sig=abc&y=1"}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert "sig=***" in safe["data"]["findings"][0]["link"]
    assert "sig=abc" not in safe["data"]["findings"][0]["link"]
    assert meta["secretsRedacted"] == 1


def test_in_string_bearer_masked():
    payload = {"header": "bearer tok123"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["header"] == "bearer ***"
    assert meta["secretsRedacted"] == 1


def test_in_string_benign_foo_bar_unchanged():
    payload = {"note": "foo=bar"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["note"] == "foo=bar"
    assert meta["secretsRedacted"] == 0


def test_in_string_benign_kql_status_predicate_unchanged():
    payload = {"query": "where Status=200"}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["query"] == "where Status=200"
    assert meta["secretsRedacted"] == 0


# ---------------------------------------------------------------------------
# secretsRedacted counts changed strings only
# ---------------------------------------------------------------------------

def test_secrets_redacted_counts_only_changed_strings():
    payload = {"a": "clean string", "clientSecret": "s3cr3t", "b": "another clean one"}
    _, meta = apply_egress_controls(payload, sink="test")
    assert meta["secretsRedacted"] == 1


# ---------------------------------------------------------------------------
# Sensitivity floor (recursive)
# ---------------------------------------------------------------------------

def test_sensitivity_floor_true_recursive_in_findings_list():
    payload = {"data": {"findings": [{"sensitive": True, "x": 1}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0] == {"redacted": True}
    assert meta["sensitiveDropped"] == 1


def test_sensitivity_floor_sensitivity_label_recursive():
    payload = {"data": {"findings": [{"sensitivityLabel": "Confidential", "x": 1}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0] == {"redacted": True}
    assert meta["sensitiveDropped"] == 1


def test_sensitivity_floor_false_passes_through():
    payload = {"data": {"findings": [{"sensitive": False, "x": 1}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0] == {"sensitive": False, "x": 1}
    assert meta["sensitiveDropped"] == 0


def test_sensitivity_floor_nested_deep_in_structure():
    payload = {"data": {"correlations": [{"related": {"sensitive": True, "y": 2}}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["correlations"][0]["related"] == {"redacted": True}
    assert meta["sensitiveDropped"] == 1


# ---------------------------------------------------------------------------
# Size cap targets data.findings ONLY
# ---------------------------------------------------------------------------

def test_size_cap_targets_findings_leaves_roadmap_and_correlations_intact():
    findings = [{"id": i, "blob": "x" * 100} for i in range(50)]
    roadmap = [{"step": 1, "text": "do this"}, {"step": 2, "text": "do that"}]
    correlations = [{"pair": ["a", "b"], "score": 0.9}]
    payload = {
        "success": True,
        "agent_id": "audit",
        "data": {"findings": findings, "roadmap": roadmap, "correlations": correlations},
        "summary": "ok",
        "timestamp": "2026-07-09T00:00:00Z",
    }
    safe, meta = apply_egress_controls(payload, sink="test", max_chars=200)
    assert meta["truncated"] is True
    assert meta["rowsOmitted"] > 0
    assert len(safe["data"]["findings"]) < len(findings)
    assert safe["data"]["roadmap"] == roadmap
    assert safe["data"]["correlations"] == correlations


def test_size_cap_under_budget_findings_unchanged():
    findings = [{"id": 1}, {"id": 2}]
    payload = {"data": {"findings": findings}}
    safe, meta = apply_egress_controls(payload, sink="test", max_chars=12000)
    assert safe["data"]["findings"] == findings
    assert meta["truncated"] is False
    assert meta["rowsOmitted"] == 0


def test_size_cap_bare_over_budget_list_capped_directly():
    payload = [{"id": i, "blob": "y" * 100} for i in range(50)]
    safe, meta = apply_egress_controls(payload, sink="test", max_chars=200)
    assert meta["truncated"] is True
    assert meta["rowsOmitted"] > 0
    assert len(safe) < len(payload)


# ---------------------------------------------------------------------------
# Names/identifiers pass through unchanged
# ---------------------------------------------------------------------------

def test_names_and_identifiers_pass_through_unchanged():
    payload = {"data": {"findings": [{"user": "alice@co", "dataset": "Sales GL"}]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"][0]["user"] == "alice@co"
    assert safe["data"]["findings"][0]["dataset"] == "Sales GL"


# ---------------------------------------------------------------------------
# No mutation of caller's object
# ---------------------------------------------------------------------------

def test_no_mutation_of_original_input():
    payload = {
        "data": {
            "findings": [{"clientSecret": "s3cr3t", "sensitive": True, "user": "alice"}],
        }
    }
    original = copy.deepcopy(payload)
    apply_egress_controls(payload, sink="test")
    assert payload == original


# ---------------------------------------------------------------------------
# Robustness -- never raises
# ---------------------------------------------------------------------------

def test_robust_none_payload():
    safe, meta = apply_egress_controls(None, sink="test")
    assert meta["sink"] == "test"


def test_robust_str_payload():
    safe, meta = apply_egress_controls("just a string", sink="test")
    assert isinstance(meta, dict)


def test_robust_int_payload():
    safe, meta = apply_egress_controls(42, sink="test")
    assert isinstance(meta, dict)


def test_robust_dict_without_data_key():
    safe, meta = apply_egress_controls({"summary": "x"}, sink="test")
    assert safe["summary"] == "x"
    assert meta["truncated"] is False
    assert meta["rowsOmitted"] == 0


def test_robust_data_findings_missing():
    payload = {"data": {"roadmap": ["a"]}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["roadmap"] == ["a"]
    assert meta["truncated"] is False


def test_robust_data_findings_not_a_list():
    payload = {"data": {"findings": "not-a-list"}}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe["data"]["findings"] == "not-a-list"
    assert meta["truncated"] is False


def test_robust_numbers_bools_none_untouched():
    payload = {"n": 42, "f": 3.14, "b": True, "none": None}
    safe, meta = apply_egress_controls(payload, sink="test")
    assert safe == payload
    assert meta["secretsRedacted"] == 0


def test_deterministic_repeated_calls_same_result():
    payload = {"data": {"findings": [{"clientSecret": "s3cr3t"}]}}
    safe1, meta1 = apply_egress_controls(payload, sink="test")
    safe2, meta2 = apply_egress_controls(payload, sink="test")
    assert safe1 == safe2
    assert meta1 == meta2


# ---------------------------------------------------------------------------
# meta shape
# ---------------------------------------------------------------------------

def test_meta_carries_sink_label():
    _, meta = apply_egress_controls({"x": 1}, sink="teams")
    assert meta["sink"] == "teams"


def test_meta_has_all_expected_keys():
    _, meta = apply_egress_controls({"x": 1}, sink="test")
    for key in ("sink", "secretsRedacted", "sensitiveDropped", "truncated", "rowsOmitted"):
        assert key in meta


# ---------------------------------------------------------------------------
# disclosure_line
# ---------------------------------------------------------------------------

def test_disclosure_line_none_when_nothing_dropped():
    meta = {"sink": "test", "secretsRedacted": 0, "sensitiveDropped": 0, "truncated": False, "rowsOmitted": 0}
    assert disclosure_line(meta) is None


def test_disclosure_line_mentions_omitted_findings():
    meta = {"sink": "test", "secretsRedacted": 0, "sensitiveDropped": 0, "truncated": True, "rowsOmitted": 12}
    line = disclosure_line(meta)
    assert line is not None
    assert "12" in line


def test_disclosure_line_mentions_sensitive_withheld():
    meta = {"sink": "test", "secretsRedacted": 0, "sensitiveDropped": 1, "truncated": False, "rowsOmitted": 0}
    line = disclosure_line(meta)
    assert line is not None
    assert "1" in line


def test_disclosure_line_composes_both_parts():
    meta = {"sink": "test", "secretsRedacted": 2, "sensitiveDropped": 1, "truncated": True, "rowsOmitted": 12}
    line = disclosure_line(meta)
    assert line is not None
    assert "12" in line
    assert "1" in line
