"""GAP-2 wiring regression (N14, 2026-07-30): kb/metric_definitions.py provenance is attached to
live tool output ADDITIVELY. These tests pin the binding invariant: every existing output key on
capacity_peaks / capacity_overloads / render_chart keeps its exact pre-wiring name, type, and
value -- the wiring only adds new sibling ``metrics`` keys.

SCOPE LIMITS -- read before trusting this file as proof of the invariant (adversarial review
2026-07-30, finding I5). It covers capacity_peaks, capacity_overloads and render_chart ONLY.
``user_activity_handler`` and ``list_workspaces_handler`` are ALSO wired but have NO test here.
Further, ``test_preexisting_keys_unchanged`` re-asserts the SEEDED INPUT keys: it cannot detect
an added key, a reordered key, or a change to a derived field -- ``distinctUsers`` and
``peakPctBaseConverted`` (tools.py ~1342-1357) are computed from the wired columns and are
untested. The invariant DOES hold: it was proven separately by differential execution against a
pre-change build of the package across nine scope/flag combinations. This file is a regression
guard, not that proof. Closing these two gaps is open work -- do not read a green run here as
"the invariant is fully pinned".

capacity_peaks_handler and capacity_overloads_handler are exercised end-to-end through
create_tool_definitions(), with the underlying ranking functions (_timepoint_peaks /
_overload_windows) monkeypatched to return fixed, hand-checked rows -- this pins the handler's
own post-processing (display-time attachment + the new metrics wiring) without depending on the
live/mock event-source plumbing producing any particular row for "today"."""
import dataclasses

import pytest

from fabric_audit_agent import tools as tools_mod
from fabric_audit_agent.tools import create_tool_definitions
from fabric_audit_agent.confidence import ClaimConfidence
from fabric_audit_agent.kb import MetricValue, METRIC_DEFINITIONS, get_metric


def _tool(name, tools=None):
    tools = tools or create_tool_definitions()
    t = next((t for t in tools if t["name"] == name), None)
    assert t is not None, f"tool '{name}' not found"
    return t


def _handler(name, tools=None):
    return _tool(name, tools)["handler"]


# ---------------------------------------------------------------------------
# Step 2: the two new catalog entries
# ---------------------------------------------------------------------------

class TestNewMetricDefinitions:
    def test_pct_base_lifetime_registered(self):
        m = get_metric("pct_base_lifetime")
        assert m is not None
        assert m["formula"] == "cuSeconds / baseCu * 100"
        assert m["metric_type"] == "presentational"
        assert m["verified"] is False
        assert "not capacity utilization" in m["notes"].lower() or "NOT capacity" in m["notes"]
        assert "throttl" in m["notes"].lower()

    def test_pct_base_converted_registered(self):
        m = get_metric("pct_base_converted")
        assert m is not None
        assert m["formula"] == "pctBaseLifetime / 10  (== cuSeconds / (baseCu * 10) * 100)"
        assert m["metric_type"] == "presentational"
        # Must NOT claim to match the Capacity Metrics app (and the retired timepoint lens is noted).
        assert "NOT app-comparable" in m["notes"] or "not app-comparable" in m["notes"]
        assert "retire" in m["notes"].lower()

    def test_pct_base_converted_not_more_verified_than_its_input(self):
        """C3: pct_base_converted is derived (/10) from pct_base_lifetime, which is
        verified=False. A derived value must not be marked more-verified than its input --
        must be False here too, and display_caveat() (the file's sanctioned honesty channel)
        must therefore be non-empty rather than silently returning ''."""
        m = get_metric("pct_base_converted")
        assert m["verified"] is False
        mv = MetricValue.from_definition("pct_base_converted", 47.1, confidence=ClaimConfidence.LIKELY)
        assert mv.display_caveat() != ""

    def test_unknown_metric_still_raises_keyerror(self):
        """Fail-loud contract (constraint 1): no silent fallback for an unknown metric name."""
        with pytest.raises(KeyError):
            MetricValue.from_definition("no_such_metric", 1.0, confidence=ClaimConfidence.LIKELY)

    def test_registry_is_baseline_plus_the_two_lifetime_lens_metrics(self):
        """The pre-GAP-2 baseline plus exactly the two surviving per-operation lens metrics
        (pct_base_lifetime + its /10 view pct_base_converted). The timepoint lens metric
        (pct_base_timepoint) was retired in Step 4, so it must NOT be present."""
        pre_gap2_names = {
            "sku_cu_pct", "cu_limit", "peak_utilization_pct",
            "cumulative_carry_forward_pct", "minutes_to_burndown",
            "overage_add_ms", "overage_burndown_ms",
            "interactive_delay_threshold_pct", "interactive_rejection_threshold_pct",
            "background_rejection_threshold_pct",
            "user_cpu_share_pct", "user_duration_share_pct",
            "concentration_threshold_pct", "dominant_item_share_pct",
        }
        gap2_names = {"pct_base_lifetime", "pct_base_converted"}
        assert set(METRIC_DEFINITIONS) == pre_gap2_names | gap2_names
        assert "pct_base_timepoint" not in METRIC_DEFINITIONS
        assert len(METRIC_DEFINITIONS) == len(pre_gap2_names) + 2


# ---------------------------------------------------------------------------
# capacity_peaks_handler
# ---------------------------------------------------------------------------

_FAKE_PEAK = {
    "ts": "2026-07-29T12:00:00Z",
    "user": "alice@example.com",
    "item": "Sales Model",
    "operation": "Query",
    "operationDetail": None,
    "kind": "interactive",
    "durationMs": 600000,
    "cuSeconds": 4825.3,
    "pctBaseLifetime": 471.2,
    "pctBaseConverted": 47.1,
}


class TestCapacityPeaksWiring:
    def _run(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "_timepoint_peaks", lambda *a, **k: [dict(_FAKE_PEAK)])
        return _handler("capacity_peaks")({})

    def test_preexisting_keys_unchanged(self, monkeypatch):
        out = self._run(monkeypatch)
        row = out["peaks"][0]
        for key, expected in _FAKE_PEAK.items():
            assert row[key] == expected, f"{key} changed: {row[key]!r} != {expected!r}"
        # top-level pre-existing keys/shape untouched
        assert out["thresholdLens"] == "lifetime"
        assert set(out["lensExplained"]) == {"pctBaseLifetime", "pctBaseConverted"}

    def test_metrics_attached_and_correct(self, monkeypatch):
        out = self._run(monkeypatch)
        row = out["peaks"][0]
        metrics = row["metrics"]
        life = metrics["pctBaseLifetime"]
        assert life["value"] == 471.2
        assert life["metric_type"] == "presentational"
        assert life["formula"] == "cuSeconds / baseCu * 100"
        assert life["verified"] is False
        assert life["confidence"] == "likely"
        assert life["metricName"] == "pct_base_lifetime"

        conv = metrics["pctBaseConverted"]
        assert conv["value"] == 47.1
        assert conv["metric_type"] == "presentational"
        # C3 fix: pct_base_converted is derived (/10) from pct_base_lifetime, which is itself
        # verified=False -- a derived value cannot be marked more-verified than its input, so
        # this must be False (and display_caveat() must therefore be non-empty).
        assert conv["verified"] is False
        assert conv["metricName"] == "pct_base_converted"

        # Step 4: the timepoint lens was retired — no pctBaseTimepoint stamp is emitted anymore.
        assert "pctBaseTimepoint" not in metrics

    def test_metrics_rows_do_not_carry_notes_prose(self, monkeypatch):
        """I4: per-row metrics stamps must NOT duplicate the (long) catalog 'notes' prose --
        that's what bloated capacity_peaks' payload ~6x at default top_n. 'notes' belongs only
        in the once-per-response 'metricsCatalog'."""
        out = self._run(monkeypatch)
        row = out["peaks"][0]
        for mv in row["metrics"].values():
            assert "notes" not in mv

    def test_metrics_catalog_attached_once_with_full_provenance(self, monkeypatch):
        """I4: the full definition (formula/notes/source) each row's metricName points to must
        be resolvable from a single top-level 'metricsCatalog', not repeated per row."""
        out = self._run(monkeypatch)
        catalog = out["metricsCatalog"]
        assert set(catalog) == {"pct_base_lifetime", "pct_base_converted"}
        for name, mv in out["peaks"][0]["metrics"].items():
            entry = catalog[mv["metricName"]]
            assert entry["formula"] == mv["formula"]
            assert "notes" in entry and entry["notes"]

    def test_metrics_wiring_shrinks_payload_vs_unstamped_baseline(self, monkeypatch):
        """I4 regression: multiple rows must not each carry the full notes prose -- payload
        should grow roughly linearly with row *values*, not with catalog prose repeated per row."""
        import json
        monkeypatch.setattr(tools_mod, "_timepoint_peaks",
                            lambda *a, **k: [dict(_FAKE_PEAK) for _ in range(20)])
        out = _handler("capacity_peaks")({"topN": 20})
        payload = json.dumps(out, separators=(",", ":"))
        # One full catalog entry's notes is on the order of a few hundred bytes; 20 rows x 3
        # metrics x notes duplicated would add many KB. Assert the actual full payload stays
        # well under what duplicating notes 60x would cost (a generous, not exact, ceiling).
        assert len(payload.encode("utf-8")) < 60_000


# ---------------------------------------------------------------------------
# capacity_overloads_handler
# ---------------------------------------------------------------------------

_FAKE_WINDOW = {
    "windowEpoch": 1785000000,
    "totalCuPct": 137.4,
    "interactiveCuPct": 80.0,
    "backgroundCuPct": 57.4,
    "contributors": [{"user": "bob@example.com", "item": "Ops Model",
                       "operation": "Refresh", "cuInWindow": 12.3}],
}


class TestCapacityOverloadsWiring:
    def _run(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "_overload_windows", lambda *a, **k: [dict(_FAKE_WINDOW)])
        return _handler("capacity_overloads")({})

    def test_preexisting_keys_unchanged(self, monkeypatch):
        out = self._run(monkeypatch)
        row = out["overloads"][0]
        assert row["totalCuPct"] == 137.4
        assert row["interactiveCuPct"] == 80.0
        assert row["backgroundCuPct"] == 57.4
        assert row["contributors"] == _FAKE_WINDOW["contributors"]
        assert "windowEpoch" not in row          # popped by the handler, same as before wiring
        assert "windowStart" in row               # derived key still produced

    def test_total_cu_pct_metric_attached(self, monkeypatch):
        out = self._run(monkeypatch)
        row = out["overloads"][0]
        mv = row["metrics"]["totalCuPct"]
        assert mv["value"] == 137.4
        assert mv["metric_type"] == "true_CU"
        assert mv["formula"] == "capacityUnitMs / (baseCapacityUnits * 1000 * 30) * 100"
        assert mv["verified"] is True
        assert mv["confidence"] == "likely"
        # I4: light stamp -- no per-row notes prose; full definition in metricsCatalog instead.
        assert "notes" not in mv
        assert mv["metricName"] == "sku_cu_pct"
        assert out["metricsCatalog"]["sku_cu_pct"]["formula"] == mv["formula"]
        assert out["metricsCatalog"]["sku_cu_pct"]["notes"]

    def test_derived_split_columns_have_no_invented_catalog_entry(self, monkeypatch):
        """interactiveCuPct/backgroundCuPct are bespoke derived estimates (overloads.py) with no
        METRIC_DEFINITIONS entry -- confirm we did NOT invent one; only totalCuPct is wired."""
        out = self._run(monkeypatch)
        row = out["overloads"][0]
        assert set(row["metrics"].keys()) == {"totalCuPct"}


# ---------------------------------------------------------------------------
# render_chart_handler: isProxy / proxyCaveat now derive from MetricValue
# ---------------------------------------------------------------------------

class TestRenderChartProxyWiring:
    def _chart_input(self, source_scope, is_proxy=None):
        inp = {
            "chartType": "bar",
            "title": "t",
            "series": [{"name": "s", "data": [{"x": "a", "y": 1}, {"x": "b", "y": 2}]}],
            "sourceScope": source_scope,
        }
        if is_proxy is not None:
            inp["isProxy"] = is_proxy
        return inp

    def test_user_scope_defaults_proxy_true_with_caveat(self):
        out = _handler("render_chart")(self._chart_input("user"))
        chart = out["chart"]
        assert chart["isProxy"] is True
        assert chart["sourceScope"] == "user"
        assert "proxyCaveat" in chart and chart["proxyCaveat"]
        assert "CPU-time proxy" in chart["proxyCaveat"]

    def test_capacity_scope_defaults_proxy_false_no_caveat(self):
        out = _handler("render_chart")(self._chart_input("capacity"))
        chart = out["chart"]
        assert chart["isProxy"] is False
        assert "proxyCaveat" not in chart

    def test_explicit_false_overrides_user_scope_default(self):
        out = _handler("render_chart")(self._chart_input("user", is_proxy=False))
        chart = out["chart"]
        assert chart["isProxy"] is False
        assert "proxyCaveat" not in chart

    def test_capacity_scope_explicit_proxy_true_gets_no_cpu_time_caveat(self):
        """I2 regression: this was the wrong branch -- passing isProxy=true for a CAPACITY-scoped
        chart (true_CU, not a CPU-time proxy) previously still attached the CpuTimeMs-proxy
        wording. isProxy itself follows the caller's explicit override, but the caveat text must
        not falsely assert CPU-time-proxy data for a capacity scope."""
        out = _handler("render_chart")(self._chart_input("capacity", is_proxy=True))
        chart = out["chart"]
        assert chart["isProxy"] is True
        assert "proxyCaveat" not in chart

    def test_item_scope_explicit_proxy_true_gets_cpu_time_caveat(self):
        out = _handler("render_chart")(self._chart_input("item", is_proxy=True))
        chart = out["chart"]
        assert chart["isProxy"] is True
        assert "proxyCaveat" in chart and chart["proxyCaveat"]
        assert "CPU-time proxy" in chart["proxyCaveat"]
