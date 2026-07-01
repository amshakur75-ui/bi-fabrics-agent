"""Tests for user_spike_history — TDD before implementation."""
import pytest
from fabric_audit_agent.investigation.spike_history import user_spike_history


def _ev(ts, user, item, operation, kind, cu):
    """Helper: build a normalized event dict."""
    return {
        "ts": ts,
        "user": user,
        "item": item,
        "workspace": "WS1",
        "operation": operation,
        "kind": kind,
        "cuSeconds": cu,
        "durationMs": int(cu * 1000),
        "throttled": False,
    }


# Events for user "alice@co" — 8 events; p95 over these will be used to detect spikes.
# cuSeconds values: [1, 2, 3, 4, 5, 6, 7, 100]
# p95 of sorted [1,2,3,4,5,6,7,100] (len=8, rank=0.95*7=6.65) = 7*(1-0.65)+100*0.65 = 67.45
# So 100 is above p95 (spike); everything else is below. floor_cu=0 so no absolute-floor spikes.
ALICE_EVENTS = [
    _ev("2026-06-30T08:05:00Z", "alice@co", "Sales",   "QueryEnd",    "interactive", 1.0),
    _ev("2026-06-30T09:10:00Z", "alice@co", "Sales",   "QueryEnd",    "interactive", 2.0),
    _ev("2026-06-30T10:15:00Z", "alice@co", "Inventory","QueryEnd",   "interactive", 3.0),
    _ev("2026-06-30T11:20:00Z", "alice@co", "Inventory","QueryEnd",   "interactive", 4.0),
    _ev("2026-06-30T12:25:00Z", "alice@co", "Sales",   "CommandEnd",  "refresh",     5.0),
    _ev("2026-06-30T13:30:00Z", "alice@co", "Sales",   "CommandEnd",  "refresh",     6.0),
    _ev("2026-06-30T14:35:00Z", "alice@co", "Inventory","QueryEnd",   "interactive", 7.0),
    _ev("2026-06-30T15:40:00Z", "alice@co", "Sales",   "QueryEnd",    "interactive", 100.0),  # spike
]

# Bob has two events — one huge spike above floor_cu, one small
BOB_EVENTS = [
    _ev("2026-06-30T09:00:00Z", "bob@co", "Finance", "QueryEnd", "interactive", 0.5),
    _ev("2026-06-30T21:00:00Z", "bob@co", "Finance", "QueryEnd", "interactive", 999.0),  # spike (floor=500)
]

ALL_EVENTS = ALICE_EVENTS + BOB_EVENTS


class TestUserSpikeHistoryBasicShape:
    def test_returns_required_keys(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        for key in ("user", "spikeCount", "totalCuSeconds", "peakCuSeconds",
                    "spikes", "topItems", "byHour", "interactiveVsRefresh"):
            assert key in result, f"Missing key: {key}"

    def test_user_field_matches_requested_user(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        assert result["user"] == "alice@co"

    def test_filters_to_requested_user_only(self):
        # Bob's spike should not appear in Alice's results
        alice = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        for spike in alice["spikes"]:
            assert spike.get("user", "alice@co") != "bob@co"


class TestSpikeCount:
    def test_spike_count_uses_p95_baseline(self):
        # Only the 100 CU event is above p95 (~67.45)
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        assert result["spikeCount"] == 1

    def test_spike_count_with_floor_cu(self):
        # Bob: p95 of [0.5, 999] = 0.95*(999-0.5)+0.5 ≈ 935.5+0.5=936; 999 > p95 → spike
        # also floor_cu=500 would catch it but p95 is sufficient
        result = user_spike_history(BOB_EVENTS, "bob@co", floor_cu=500)
        assert result["spikeCount"] == 1

    def test_no_spikes_when_single_event(self):
        # A single event has p95 = that event; is_spike uses strict >, so nothing beats itself.
        events = [
            _ev("2026-06-30T08:00:00Z", "eve@co", "R1", "QueryEnd", "interactive", 1.0),
        ]
        result = user_spike_history(events, "eve@co", floor_cu=0)
        assert result["spikeCount"] == 0
        assert result["spikes"] == []

    def test_empty_user_events(self):
        result = user_spike_history(ALL_EVENTS, "nobody@co", floor_cu=0)
        assert result["spikeCount"] == 0
        assert result["totalCuSeconds"] == 0
        assert result["peakCuSeconds"] == 0
        assert result["spikes"] == []


class TestSpikesListShape:
    def test_each_spike_has_required_fields(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        for spike in result["spikes"]:
            for field in ("ts", "item", "operation", "kind", "cuSeconds"):
                assert field in spike, f"Spike missing field: {field}"

    def test_spike_carries_correct_values(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        assert len(result["spikes"]) == 1
        spike = result["spikes"][0]
        assert spike["ts"] == "2026-06-30T15:40:00Z"
        assert spike["item"] == "Sales"
        assert spike["operation"] == "QueryEnd"
        assert spike["kind"] == "interactive"
        assert spike["cuSeconds"] == 100.0

    def test_spikes_sorted_by_cu_desc(self):
        # Create events where multiple spikes exist; verify ordering
        events = [
            _ev("2026-06-30T08:00:00Z", "charlie@co", "R1", "QueryEnd", "interactive", 5.0),
            _ev("2026-06-30T09:00:00Z", "charlie@co", "R2", "QueryEnd", "interactive", 5.0),
            _ev("2026-06-30T10:00:00Z", "charlie@co", "R3", "QueryEnd", "interactive", 200.0),
            _ev("2026-06-30T11:00:00Z", "charlie@co", "R4", "QueryEnd", "interactive", 150.0),
        ]
        result = user_spike_history(events, "charlie@co", floor_cu=0)
        cus = [s["cuSeconds"] for s in result["spikes"]]
        assert cus == sorted(cus, reverse=True)


class TestAggregates:
    def test_total_cu_seconds_is_sum_of_all_user_events(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        expected = sum(e["cuSeconds"] for e in ALICE_EVENTS)
        assert abs(result["totalCuSeconds"] - expected) < 1e-6

    def test_peak_cu_seconds_is_max_of_all_user_events(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        expected = max(e["cuSeconds"] for e in ALICE_EVENTS)
        assert result["peakCuSeconds"] == expected


class TestTopItems:
    def test_top_items_lists_items_by_total_cu(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        # Sales: 1+2+5+6+100=114, Inventory: 3+4+7=14
        top = result["topItems"]
        assert isinstance(top, list)
        assert len(top) >= 1
        assert top[0]["item"] == "Sales"
        assert abs(top[0]["cuSeconds"] - 114.0) < 1e-6

    def test_top_items_has_item_and_cu_fields(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        for entry in result["topItems"]:
            assert "item" in entry
            assert "cuSeconds" in entry


class TestByHour:
    def test_by_hour_keys_are_hour_integers(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        bh = result["byHour"]
        assert isinstance(bh, dict)
        for k in bh:
            assert isinstance(k, int)
            assert 0 <= k <= 23

    def test_by_hour_counts_spike_events_per_hour(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        bh = result["byHour"]
        # Only 1 spike (100 CU at hour 15); byHour tracks spikes, not all events
        assert bh.get(15) == 1
        # Non-spike hours are not present (or 0)
        assert bh.get(8, 0) == 0

    def test_by_hour_values_sum_to_spike_count(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        # byHour counts SPIKE events per hour (not all events)
        assert sum(result["byHour"].values()) == result["spikeCount"]


class TestInteractiveVsRefresh:
    def test_interactive_vs_refresh_has_required_keys(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        ivr = result["interactiveVsRefresh"]
        assert "interactiveCuSeconds" in ivr
        assert "refreshCuSeconds" in ivr

    def test_interactive_vs_refresh_totals(self):
        result = user_spike_history(ALL_EVENTS, "alice@co", floor_cu=0)
        ivr = result["interactiveVsRefresh"]
        # Alice interactive: 1+2+3+4+7+100=117, refresh: 5+6=11
        assert abs(ivr["interactiveCuSeconds"] - 117.0) < 1e-6
        assert abs(ivr["refreshCuSeconds"] - 11.0) < 1e-6

    def test_interactive_vs_refresh_for_user_with_only_refresh(self):
        events = [
            _ev("2026-06-30T08:00:00Z", "rex@co", "DS1", "CommandEnd", "refresh", 50.0),
            _ev("2026-06-30T09:00:00Z", "rex@co", "DS2", "CommandEnd", "refresh", 60.0),
            _ev("2026-06-30T10:00:00Z", "rex@co", "DS3", "CommandEnd", "refresh", 500.0),
        ]
        result = user_spike_history(events, "rex@co", floor_cu=0)
        ivr = result["interactiveVsRefresh"]
        assert ivr["interactiveCuSeconds"] == 0.0
        assert abs(ivr["refreshCuSeconds"] - 610.0) < 1e-6
