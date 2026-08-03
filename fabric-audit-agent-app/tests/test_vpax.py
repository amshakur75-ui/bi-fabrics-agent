import io
import json
import zipfile
import pytest
from fabric_audit_agent.importers.vpax import vpax_to_models


def _make_vpax(name, obj):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr(name, json.dumps(obj))
    return bio.getvalue()


def test_extracts_size_bidi_autodate():
    vpax = _make_vpax("DaxModel.json", {
        "ModelName": "Sales Model",
        "Tables": [
            {"TableName": "Sales", "Columns": [{"TotalSize": 3_000_000_000}, {"TotalSize": 2_500_000_000}]},
            {"TableName": "LocalDateTable_abc", "Columns": [{"TotalSize": 100_000_000}]},
        ],
        "Relationships": [{"CrossFilteringBehavior": "BothDirections"}, {"CrossFilteringBehavior": "OneDirection"}],
    })
    models = vpax_to_models(vpax)["models"]
    assert len(models) == 1
    assert models[0]["name"] == "Sales Model"
    assert models[0]["sizeGB"] == 5.6
    assert models[0]["bidirectionalRels"] == 1
    assert models[0]["autoDateTime"] is True


def test_no_model_json_raises():
    with pytest.raises(ValueError, match="no DaxModel"):
        vpax_to_models(_make_vpax("readme.txt", {"not": "a model"}))


def test_not_a_zip_raises():
    with pytest.raises(ValueError, match="not a ZIP"):
        vpax_to_models(b"this is not a zip at all")


def test_reads_deflated_vpax():
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("DaxModel.json", json.dumps({
            "ModelName": "M",
            "Tables": [{"TableName": "T", "Columns": [{"TotalSize": 2_000_000_000}]}],
            "Relationships": [],
        }))
    assert vpax_to_models(bio.getvalue())["models"][0]["sizeGB"] == 2.0


def test_daxvpaview_fallback_filterdirection_and_bidirectional_flag():
    vpax = _make_vpax("DaxVpaView.json", {
        "Name": "ViewModel",
        "Tables": [{"Name": "T", "Columns": [{"ColumnSize": 1_000_000_000}]}],
        "Relationships": [{"FilterDirection": 2}, {"Bidirectional": True}, {"CrossFilteringBehavior": "OneDirection"}],
    })
    m = vpax_to_models(vpax)["models"][0]
    assert m["name"] == "ViewModel"
    assert m["sizeGB"] == 1.0
    assert m["bidirectionalRels"] == 2   # FilterDirection==2 + Bidirectional:true; OneDirection excluded
