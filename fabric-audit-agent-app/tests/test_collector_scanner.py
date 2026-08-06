"""FIX C: Scanner-API models collector -> facts["models"] -> detect_models bidirectional flag."""
from fabric_audit_agent.adapters.collector_scanner import create_scanner_models_collector
from fabric_audit_agent.detectors import detect_all


class _FakeScannerHttp:
    """Simulates the async getInfo -> scanStatus (Running then Succeeded) -> scanResult flow."""
    def __init__(self, result):
        self._result = result
        self._status_calls = 0
        self.posts = []

    def post_json(self, url, body, headers=None):
        assert "workspaces/getInfo" in url and "datasetSchema=True" in url
        self.posts.append(body)
        return {"id": "scan-1"}

    def get_json(self, url):
        if "scanStatus" in url:
            self._status_calls += 1
            return {"status": "Succeeded" if self._status_calls >= 2 else "Running"}
        if "scanResult" in url:
            return self._result
        raise AssertionError(f"unexpected GET {url}")


def _result():
    return {"workspaces": [{"id": "w1", "name": "Finance", "datasets": [
        {"id": "ds-sales", "name": "Sales Model", "targetStorageMode": "DirectQuery",
         "relationships": [
            {"crossFilteringBehavior": "BothDirections"},
            {"crossFilteringBehavior": "BothDirections"},
            {"crossFilteringBehavior": "BothDirections"},
            {"crossFilteringBehavior": "BothDirections"},
            {"crossFilteringBehavior": "OneDirection"},
        ]},
        {"id": "ds-tiny", "name": "Tiny Model", "targetStorageMode": "Import",
         "relationships": [{"crossFilteringBehavior": "OneDirection"}]},
    ], "reports": [
        {"name": "Sales DQ Report", "datasetId": "ds-sales"},
        {"name": "Tiny Import Report", "datasetId": "ds-tiny"},
    ]}]}


def test_scanner_maps_bidirectional_relationships():
    http = _FakeScannerHttp(_result())
    col = create_scanner_models_collector(http, {"workspaceIds": ["w1"], "sleep": lambda s: None})
    facts = col["collect"]()
    models = {m["name"]: m for m in facts["models"]}
    assert models["Sales Model"]["bidirectionalRels"] == 4
    assert models["Tiny Model"]["bidirectionalRels"] == 0
    assert models["Sales Model"]["workspace"] == "Finance"
    assert http.posts == [{"workspaces": ["w1"]}]     # batched workspace ids


def test_scanner_emits_reports_with_directquery():
    http = _FakeScannerHttp(_result())
    facts = create_scanner_models_collector(http, {"workspaceIds": ["w1"], "sleep": lambda s: None})["collect"]()
    reports = {r["name"]: r for r in facts["reports"]}
    assert reports["Sales DQ Report"]["mode"] == "DirectQuery"      # dataset targetStorageMode=DirectQuery
    assert reports["Tiny Import Report"]["mode"] != "DirectQuery"
    # end to end: DirectQuery report -> a report.directquery flag from detect_all
    types = {f["type"] for f in detect_all(facts)}
    assert "report.directquery" in types


def test_scanner_feeds_model_detector():
    # 4 bidirectional rels >= bidirectionalMin(4) -> a model.bidirectional flag from detect_all
    http = _FakeScannerHttp(_result())
    facts = create_scanner_models_collector(http, {"workspaceIds": ["w1"], "sleep": lambda s: None})["collect"]()
    types = {f["type"] for f in detect_all(facts)}
    assert any(t.startswith("model") for t in types)


def test_scanner_fail_open_on_error():
    class _Boom:
        def post_json(self, *a, **k):
            raise RuntimeError("scanner unavailable")
        def get_json(self, *a, **k):
            raise RuntimeError("scanner unavailable")
    col = create_scanner_models_collector(_Boom(), {"workspaceIds": ["w1"], "sleep": lambda s: None})
    assert col["collect"]()["models"] == []


def test_scanner_gives_up_when_scan_never_succeeds():
    class _NeverDone(_FakeScannerHttp):
        def get_json(self, url):
            if "scanStatus" in url:
                return {"status": "Running"}
            return self._result
    http = _NeverDone(_result())
    col = create_scanner_models_collector(http, {"workspaceIds": ["w1"], "sleep": lambda s: None,
                                                 "maxPolls": 3})
    assert col["collect"]()["models"] == []   # polled out, no crash
