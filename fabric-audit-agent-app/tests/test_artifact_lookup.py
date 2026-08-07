"""Tests for resolve.artifact_lookup — dedup, conflicts, 3-way lookup, degraded mode."""
import importlib.util

import pytest

from fabric_audit_agent.resolve.artifact_lookup import ArtifactLookup, default_artifact_lookup

_HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None

_ROWS = [
    {"PowerBIWorkspaceName": "Enterprise Sales", "ArtifactName": "Ent-Reporting-Sales", "ArtifactId": "id-sales-1"},
    {"PowerBIWorkspaceName": "Enterprise DTC", "ArtifactName": "Ent-Reporting-DTC", "ArtifactId": "id-dtc-1"},
    # Duplicate ArtifactId with a DIFFERENT workspace -> first-encountered wins, conflict logged.
    {"PowerBIWorkspaceName": "Enterprise Sales v2", "ArtifactName": "Ent-Reporting-Sales", "ArtifactId": "id-sales-1"},
    # Same name, different id -> distinct artifacts (name lookup is 'multiple').
    {"PowerBIWorkspaceName": "Enterprise Sales", "ArtifactName": "Ent-Reporting-Sales", "ArtifactId": "id-sales-2"},
    # Row with no ArtifactId -> skipped.
    {"PowerBIWorkspaceName": "Ghost", "ArtifactName": "Ghost", "ArtifactId": ""},
]


def _loaded():
    al = ArtifactLookup()
    al.load_from_rows(_ROWS)
    return al


def test_load_from_rows_dedups_first_encountered_wins(capsys):
    al = _loaded()
    assert al.is_available()
    r = al.lookup(artifact_id="id-sales-1")
    assert r["status"] == "found"
    assert r["artifact"]["pbiWorkspaceName"] == "Enterprise Sales"  # first-encountered
    # Conflict logged to stderr.
    assert "ArtifactDataset conflict" in capsys.readouterr().err


def test_exactly_one_param_guard():
    al = _loaded()
    with pytest.raises(ValueError):
        al.lookup()
    with pytest.raises(ValueError):
        al.lookup(artifact_name="a", artifact_id="b")


def test_lookup_by_id_found_and_not_found():
    al = _loaded()
    assert al.lookup(artifact_id="id-dtc-1")["status"] == "found"
    assert al.lookup(artifact_id="nope")["status"] == "not_found"


def test_lookup_by_name_normalized():
    al = _loaded()
    # Two distinct ids share the name "Ent-Reporting-Sales" -> multiple.
    r = al.lookup(artifact_name="ent reporting sales")  # normalized match
    assert r["status"] == "multiple"
    assert len(r["matches"]) == 2
    # DTC name resolves to a single artifact.
    assert al.lookup(artifact_name="Ent-Reporting-DTC")["status"] == "found"
    assert al.lookup(artifact_name="does not exist")["status"] == "not_found"


def test_lookup_by_workspace():
    al = _loaded()
    r = al.lookup(pbi_workspace_name="Enterprise Sales")
    assert r["status"] == "found_workspace"
    assert r["workspaceName"] == "Enterprise Sales"
    assert {a["artifactId"] for a in r["artifacts"]} == {"id-sales-1", "id-sales-2"}
    assert al.lookup(pbi_workspace_name="No Such WS")["status"] == "not_found"


def test_missing_columns_marks_unavailable():
    al = ArtifactLookup()
    al.load_from_rows([{"Foo": "bar"}])
    assert al.is_available() is False
    r = al.lookup(artifact_id="x")
    assert r["status"] == "unavailable"
    assert "missing required columns" in r["load_error"] if "load_error" in r else True
    assert "ArtifactId" in al.load_error


def test_unavailable_when_never_loaded():
    al = ArtifactLookup()  # load=False by default
    assert al.is_available() is False
    assert al.lookup(artifact_id="x")["status"] == "unavailable"


def test_degraded_on_missing_file(tmp_path):
    al = ArtifactLookup(xlsx_path=tmp_path / "missing.xlsx", load=True)
    assert al.is_available() is False
    assert al.lookup(artifact_id="x")["status"] == "unavailable"


@pytest.mark.skipif(not _HAS_OPENPYXL, reason="openpyxl not installed — xlsx path degrades")
def test_real_xlsx_loads_via_openpyxl():
    al = default_artifact_lookup()
    assert al.is_available()
    # The extracted inventory contains the Sales dataset in the Enterprise Sales workspace.
    r = al.lookup(pbi_workspace_name="Enterprise Sales")
    assert r["status"] == "found_workspace"
    assert any(a["artifactName"] == "Ent-Reporting-Sales" for a in r["artifacts"])
