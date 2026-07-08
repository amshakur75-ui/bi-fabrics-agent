"""Tier-1 activity→event-shaped adapter: operation-level records, cuSeconds=None (honest: no CU here)."""
from fabric_audit_agent.adapters.collector_activity_events import create_activity_event_collector


class _FakeHttp:
    def __init__(self, pages):
        self._pages = list(pages)
    def get_json(self, url):
        return self._pages.pop(0) if self._pages else {"activityEventEntities": []}


_PAGE = {"activityEventEntities": [
    {"UserId": "john@co", "Operation": "ViewReport", "ReportName": "Sales",
     "WorkspaceName": "Finance", "CreationTime": "2026-07-07T09:02:00Z"},
    {"UserId": "john@co", "Operation": "RefreshDataset", "DatasetName": "Sales Model",
     "WorkspaceName": "Finance", "CreationTime": "2026-07-07T10:30:00Z"},
    {"UserId": "amy@co", "Operation": "ViewReport", "ReportName": "HR",
     "WorkspaceName": "People", "CreationTime": "2026-07-07T09:05:00Z"},
]}


def _collect(config):
    col = create_activity_event_collector(_FakeHttp([_PAGE]), config)
    return col["collect"]()


def test_maps_to_event_shape_with_null_cost():
    events = _collect({"start": "2026-07-07T00:00:00Z", "end": "2026-07-08T00:00:00Z"})
    assert len(events) == 3
    view = events[0]
    assert view == {"ts": "2026-07-07T09:02:00Z", "user": "john@co", "item": "Sales",
                    "workspace": "Finance", "kind": "interactive", "cuSeconds": None,
                    "queryText": None, "operation": "ViewReport"}

def test_background_op_maps_to_refresh_kind():
    events = _collect({"start": "2026-07-07T00:00:00Z", "end": "2026-07-08T00:00:00Z"})
    assert events[1]["kind"] == "refresh" and events[1]["operation"] == "RefreshDataset"

def test_user_scope_filters_case_insensitive():
    events = _collect({"start": "2026-07-07T00:00:00Z", "end": "2026-07-08T00:00:00Z",
                       "user": "JOHN@CO"})
    assert {e["user"] for e in events} == {"john@co"}

def test_item_scope_filters():
    events = _collect({"start": "2026-07-07T00:00:00Z", "end": "2026-07-08T00:00:00Z",
                       "item": "Sales"})
    assert len(events) == 1 and events[0]["item"] == "Sales"

def test_missing_window_raises_valueerror():
    import pytest
    with pytest.raises(ValueError):
        create_activity_event_collector(_FakeHttp([]), {"start": "2026-07-07T00:00:00Z"})["collect"]()
