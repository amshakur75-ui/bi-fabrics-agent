import json
from fabric_audit_agent.cli import run_import, run_mytest


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_run_import_items_csv_end_to_end(tmp_path):
    csv = _write(tmp_path, "Items.csv",
                 "Workspace,Item kind,Item name,CU (s),Duration (s),Users,Rejected count,Billing type\n"
                 "Finance,SemanticModel,GL Model,700000,40,12,3,Billable\n"
                 "Sales,Report,Exec,250000,5,80,0,Billable\n"
                 "Ops,SemanticModel,Inv,50000,9,4,0,Billable\n")
    estate = tmp_path / "my-estate.json"
    out = run_import([csv], estate_path=str(estate))
    assert "Top CU consumers" in out
    assert "GL Model" in out and "70%" in out
    assert "THROTTLING CONFIRMED: 3" in out
    assert "DIAGNOSIS" in out
    assert "[Critical]" in out and "70% of capacity CU" in out
    assert estate.exists()
    # the written estate is re-runnable by mytest
    assert "DIAGNOSIS" in run_mytest(estate_path=str(estate))


def test_run_inspect_hides_names_shows_numbers_and_categories(tmp_path):
    csv = _write(tmp_path, "Items.csv", "Item name,CU (s),Item kind\nSecret-X,100,Report\nSecret-Y,300,SemanticModel\n")
    out = run_import([csv], inspect=True)
    assert "[num]   CU (s):" in out and "max=300" in out
    assert "[label] Item name" in out and "Secret-X" not in out
    assert "[cat]   Item kind: Report, SemanticModel" in out


def test_run_import_timepoint_unreadable_pct(tmp_path):
    csv = _write(tmp_path, "data.csv",
                 "100% in CU(s),Timepoint,Total CU Usage %,Total CU(s),Capacity State Change\n"
                 "30720,t1,42,12902,None\n"
                 "30720,t2,23069,46080,Overloaded\n")
    out = run_import([csv], estate_path=str(tmp_path / "my-estate.json"))
    assert "raw pre-smoothing spike" in out          # 23069% flagged as unreadable
    assert "capacity states:" in out and "Overloaded=1" in out
    assert "NOT readable" in out


def test_run_mytest_missing_then_present(tmp_path):
    assert "not found yet" in run_mytest(estate_path=str(tmp_path / "nope.json"))
    estate = tmp_path / "my-estate.json"
    estate.write_text(json.dumps({"capacity": {"tenant": "A", "capacityId": "P", "sku": "F64", "memoryGB": 64,
                                                "peakCuPct": 95, "peakAt": "t", "throttleMinutes": 20, "refreshes": []}}), encoding="utf-8")
    out = run_mytest(estate_path=str(estate))
    assert "DIAGNOSIS" in out and "Capacity verdict:" in out


def test_run_import_no_files_prints_usage():
    assert "Usage:" in run_import([])
