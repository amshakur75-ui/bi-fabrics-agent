"""B3 -- Static grounding table mapping metric names to formula, source, metric type,
and smoothing window.

Formulas in this file are VERIFIED against real production data (2026-07-27 formula-validation
session, GAPS-AND-ISSUES Section 12). Each entry carries a ``verified`` flag: True means the
formula was confirmed exactly against real Capacity Metrics app exports; False means the formula
is a reasonable assumption that has NOT been empirically tested yet.

Metric types:
- ``true_CU``    -- actual billed capacity CU; authoritative, capacity-level only.
- ``proxy_cpu``  -- CpuTimeMs CPU-time proxy; per-user / per-item, NOT billed CU.
- ``proxy_dur``  -- DurationMs wall-clock proxy; weaker than proxy_cpu.
- ``constant``   -- raw API field confirmed to be a constant flag (value = 1), NOT a
                    meaningful scale value -- do not use as a divisor or threshold.
- ``reference``  -- a structural/threshold value used to gate decisions.
- ``presentational`` -- a display convenience derived from other metrics; not independently
                        meaningful for computation.

DO NOT add entries to this file without either (a) confirming the formula exactly against
production data, or (b) marking ``verified=False`` and noting the open question.
"""

# ---------------------------------------------------------------------------
# Core CU% measures -- Group 2 (GAPS Section 12.2)
# ---------------------------------------------------------------------------

SKU_CU_PCT = {
    "name": "sku_cu_pct",
    "description": "30-second window CU% -- the authoritative capacity utilization figure",
    "formula": "capacityUnitMs / (baseCapacityUnits * 1000 * 30) * 100",
    "source_table": "Capacity Overview Events (Real-Time Hub)",
    "source_columns": ["capacityUnitMs", "baseCapacityUnits"],
    "metric_type": "true_CU",
    "smoothing_window": "30 seconds",
    "verified": True,
    "notes": (
        "Confirmed exact across F1024 and F512 SKUs, 6+ windows, two independent export sources "
        "(Timepoint Detail AND raw Utilization chart export). SKU changes mid-session are handled "
        "automatically because baseCapacityUnits is read from the event payload, not from a static "
        "config value."
    ),
}

CU_LIMIT = {
    "name": "cu_limit",
    "description": "Total CU budget for one 30-second window",
    "formula": "baseCapacityUnits * 1000 * 30",
    "source_table": "Capacity Overview Events",
    "source_columns": ["baseCapacityUnits"],
    "metric_type": "true_CU",
    "smoothing_window": "30 seconds",
    "verified": True,
    "notes": "Unit: CU-milliseconds. Same denominator as SKU_CU_PCT.",
}

PEAK_UTILIZATION_PCT = {
    "name": "peak_utilization_pct",
    "description": "Peak CU% across the analysis window",
    "formula": "MAX(sku_cu_pct) over the date range",
    "source_table": "Capacity Overview Events",
    "source_columns": ["capacityUnitMs", "baseCapacityUnits"],
    "metric_type": "true_CU",
    "smoothing_window": "full date range",
    "verified": True,
    "notes": (
        "Matched the Capacity Metrics app's 19898.34% header figure to 4 decimal places from a "
        "3,132-row 14-day sample. Peak identified as Jul 16, 10:15:30 PM, approximately 199x base."
    ),
}

# ---------------------------------------------------------------------------
# Carry-forward / burndown chain -- Group 3 (GAPS Section 12.3)
# ---------------------------------------------------------------------------

CUMULATIVE_CARRY_FORWARD_PCT = {
    "name": "cumulative_carry_forward_pct",
    "description": "Cumulative overage carry-forward as % of one 30-second window's budget",
    "formula": "overageTotalCapacityUnitMs / (baseCapacityUnits * 1000 * 30) * 100",
    "source_table": "Capacity Overview Events",
    "source_columns": ["overageTotalCapacityUnitMs", "baseCapacityUnits"],
    "metric_type": "true_CU",
    "smoothing_window": "rolling (carry-forward accumulates across windows)",
    "verified": True,
    "notes": (
        "Confirmed against 1,777 consecutive 30-second windows with zero error. "
        "The recursion is: Cumulative[T] = Cumulative[T-1] + Add[T-1] + Burndown[T-1] "
        "(Burndown is stored negative; recursion is one-window lagged). "
        "overageTotalCapacityUnitMs is the Capacity Metrics app's "
        "'Cumulative carry forward' (Timepoint Overage Detail table)."
    ),
}

MINUTES_TO_BURNDOWN = {
    "name": "minutes_to_burndown",
    "description": "Estimated minutes until the carry-forward overage burns off at the current rate",
    "formula": "cumulative_carry_forward_pct / 200",
    "source_table": "Capacity Overview Events (derived)",
    "source_columns": ["overageTotalCapacityUnitMs", "baseCapacityUnits"],
    "metric_type": "true_CU",
    "smoothing_window": "point-in-time",
    "verified": True,
    "notes": (
        "Constant divisor of 200 confirmed exact -- matched 'Expected time to recover (min)' "
        "header in the Capacity Metrics app across multiple capacities. "
        "Thresholds: <33% cumulative = minor smoothing; 33-100% = noticeable delay risk; "
        "100-300% = significant user-visible throttling; >300% = extended outage risk."
    ),
}

OVERAGE_ADD_MS = {
    "name": "overage_add_ms",
    "description": "Carry-forward added in this 30-second window (CU-milliseconds)",
    "formula": "overageAddCapacityUnitMs (raw field, no transformation needed)",
    "source_table": "Capacity Overview Events",
    "source_columns": ["overageAddCapacityUnitMs"],
    "metric_type": "true_CU",
    "smoothing_window": "30 seconds",
    "verified": True,
    "notes": (
        "Maps to Capacity Metrics app 'Timepoint Overage Detail[Carry forward add]'. "
        "Value is positive when overage is accumulating."
    ),
}

OVERAGE_BURNDOWN_MS = {
    "name": "overage_burndown_ms",
    "description": "Carry-forward burned down in this 30-second window (CU-milliseconds, stored negative)",
    "formula": "overageBurndownCapacityUnitMs (raw field; negative = burning down)",
    "source_table": "Capacity Overview Events",
    "source_columns": ["overageBurndownCapacityUnitMs"],
    "metric_type": "true_CU",
    "smoothing_window": "30 seconds",
    "verified": True,
    "notes": (
        "Maps to Capacity Metrics app 'Timepoint Overage Detail[Carry forward burndown]'. "
        "Stored as a negative number by the API."
    ),
}

# ---------------------------------------------------------------------------
# Throttle threshold signals -- Group 4 (GAPS Section 12.4)
# ---------------------------------------------------------------------------

INTERACTIVE_DELAY_THRESHOLD_PCT = {
    "name": "interactive_delay_threshold_pct",
    "description": "Interactive delay throttle threshold as a % of base capacity",
    "formula": "interactiveDelayThresholdPercentage * 100  (raw API = 0-1 fraction, scale x100)",
    "source_table": "Capacity Overview Events",
    "source_columns": ["interactiveDelayThresholdPercentage"],
    "metric_type": "reference",
    "smoothing_window": "30 seconds (raw signal arrival cadence)",
    "health_state_smoothing_window": "10 minutes",
    "verified": True,
    "notes": (
        "Raw API field is a fraction (e.g. 1.2371); must be scaled x100 before passing to "
        "throttle.py's `max(vals) > 100.0` check (which expects percentage points, e.g. 123.71). "
        "Confirmed constant = 1 (i.e. 100%) across all windows in the validation session -- "
        "throttle fires when CU% exceeds 100% of base. "
        "IMPORTANT: do not confuse this field with the raw [CU_limit] or "
        "[Interactive_delay_threshold] export columns, which are boolean flags (value = 1), "
        "NOT scale values. "
        "N20: 'smoothing_window' above (30s) is how often the RAW SIGNAL itself arrives -- this "
        "is NOT the same as 'health_state_smoothing_window' (10 minutes), which is the window "
        "Microsoft's own Health-page state machine smooths OVER before flipping a capacity to "
        "the 'Throttling' state. A single 30-second spike above 100% does not mean the 10-minute "
        "smoothed value has crossed 100% -- these are two different thresholds for two different "
        "questions (official fabric-docs source, GAPS Section 12.7/12.11/N20). Never conflate them "
        "when reporting whether a capacity is 'throttled'."
    ),
}

INTERACTIVE_REJECTION_THRESHOLD_PCT = {
    "name": "interactive_rejection_threshold_pct",
    "description": "Interactive rejection throttle threshold as a % of base capacity",
    "formula": "interactiveRejectionThresholdPercentage * 100",
    "source_table": "Capacity Overview Events",
    "source_columns": ["interactiveRejectionThresholdPercentage"],
    "metric_type": "reference",
    "smoothing_window": "30 seconds (raw signal arrival cadence)",
    "health_state_smoothing_window": "60 minutes",
    "verified": True,
    "notes": (
        "Confirmed constant = 1 (100%) across all validation windows. See INTERACTIVE_DELAY notes "
        "for the raw-signal-vs-health-state smoothing-window distinction (N20) -- this threshold "
        "type is evaluated over a 60-MINUTE smoothed window for the official Health state, distinct "
        "from Interactive Delay's 10-minute window and Background Rejection's 24-hour window."
    ),
}

BACKGROUND_REJECTION_THRESHOLD_PCT = {
    "name": "background_rejection_threshold_pct",
    "description": "Background rejection throttle threshold as a % of base capacity",
    "formula": "backgroundRejectionThresholdPercentage * 100",
    "source_table": "Capacity Overview Events",
    "source_columns": ["backgroundRejectionThresholdPercentage"],
    "metric_type": "reference",
    "smoothing_window": "30 seconds (raw signal arrival cadence)",
    "health_state_smoothing_window": "24 hours",
    "verified": True,
    "notes": (
        "Confirmed constant = 1 (100%) across all validation windows. See INTERACTIVE_DELAY notes "
        "for the raw-signal-vs-health-state smoothing-window distinction (N20) -- this threshold "
        "type is evaluated over a 24-HOUR smoothed window for the official Health state, the "
        "longest of the three (10min / 60min / 24hr)."
    ),
}

# ---------------------------------------------------------------------------
# Per-user attribution -- CPU-time proxy (Layer B)
# ---------------------------------------------------------------------------

USER_CPU_SHARE_PCT = {
    "name": "user_cpu_share_pct",
    "description": "A user's share of total CpuTimeMs in the analysis window (CPU-time proxy)",
    "formula": "SUM(user.CpuTimeMs) / SUM(all.CpuTimeMs) * 100",
    "source_table": "Workspace Monitoring SemanticModelLogs / Log Analytics PowerBIDatasetsWorkspace",
    "source_columns": ["CpuTimeMs", "ExecutingUser"],
    "metric_type": "proxy_cpu",
    "smoothing_window": "query window (variable)",
    "verified": False,
    "notes": (
        "NOT billed capacity CU -- this is a CPU-time proxy. Must always be labeled as such. "
        "True billed CU per user is permanently inaccessible via service principal (Capacity "
        "Metrics app only). "
        "Item History Summary[CpuTimeMs] in the Metrics app schema (TableID 250) is the same "
        "underlying field -- confirms proxy-attribution consistency between Layer A and Layer B. "
        "In production, the SemanticModelLogs table may expose DurationMs instead of CpuTimeMs "
        "(field absent in some tenants) -- if so, attribution_mode should be 'cost-duration', "
        "NOT 'cost-cpu' (see N7)."
    ),
}

USER_DURATION_SHARE_PCT = {
    "name": "user_duration_share_pct",
    "description": "A user's share of total DurationMs in the analysis window (wall-clock duration proxy)",
    "formula": "SUM(user.DurationMs) / SUM(all.DurationMs) * 100",
    "source_table": "Workspace Monitoring SemanticModelLogs / Log Analytics PowerBIDatasetsWorkspace",
    "source_columns": ["DurationMs", "ExecutingUser"],
    "metric_type": "proxy_dur",
    "smoothing_window": "query window (variable)",
    "verified": False,
    "notes": (
        "Fallback attribution mode when CpuTimeMs is not available in the tenant's "
        "SemanticModelLogs table. DurationMs is wall-clock time, not CPU time -- it includes "
        "idle/wait time, so it's a weaker proxy than CpuTimeMs for actual compute cost. "
        "Activated automatically by attribution_rollup.py when hasCpuTime is False for a "
        "group/user; labeled 'cost-duration' in attributionMode (vs 'cost-cpu' when real "
        "CpuTimeMs is present). See N7 in GAPS-AND-ISSUES.md."
    ),
}

# ---------------------------------------------------------------------------
# Verdict thresholds (gates.py constants -- documented here for discoverability)
# ---------------------------------------------------------------------------

CONCENTRATION_THRESHOLD_PCT = {
    "name": "concentration_threshold_pct",
    "description": "Single-entity share threshold that triggers a concentration alert",
    "formula": "config['capacity']['concentrationPct']  (default 30.0)",
    "source_table": "Agent config",
    "source_columns": [],
    "metric_type": "reference",
    "smoothing_window": "N/A",
    "verified": True,
    "notes": (
        "Currently duplicated in 3-4 places (gates.py CONCENTRATION_THRESHOLD_PCT=30.0, "
        "diagnose.py inline > 30.0, concentration.py via config, user_concentration.py via config). "
        "All should read from config['capacity']['concentrationPct'] -- see N9. "
        "Must exclude system item kinds (EventStream, Activator, FabricEvents-...) before "
        "computing shares -- see N5/N6/N8."
    ),
}

DOMINANT_ITEM_SHARE_PCT = {
    "name": "dominant_item_share_pct",
    "description": "Single-item share threshold that governs the optimize-vs-size-up verdict",
    "formula": "gates.py DOMINANT_ITEM_SHARE_PCT = 40.0",
    "source_table": "Agent config / gates.py",
    "source_columns": [],
    "metric_type": "reference",
    "smoothing_window": "N/A",
    "verified": True,
    "notes": (
        "Core verdict logic (gates.py verdict_gate()): "
        "SIZE-UP eligible iff throttling confirmed AND persistent (prior-run signal) AND NOT "
        "dominant (no single item > 40% share). "
        "OPTIMIZE eligible iff throttling confirmed AND a single item > 40% share "
        "(named fixable target -- sizing up would mask it). "
        "Neither eligible when not throttling now; history alone cannot establish 'persistent'."
    ),
}


# ---------------------------------------------------------------------------
# Registry -- keyed by metric name for agent lookup
# ---------------------------------------------------------------------------

METRIC_DEFINITIONS = {
    m["name"]: m for m in [
        SKU_CU_PCT,
        CU_LIMIT,
        PEAK_UTILIZATION_PCT,
        CUMULATIVE_CARRY_FORWARD_PCT,
        MINUTES_TO_BURNDOWN,
        OVERAGE_ADD_MS,
        OVERAGE_BURNDOWN_MS,
        INTERACTIVE_DELAY_THRESHOLD_PCT,
        INTERACTIVE_REJECTION_THRESHOLD_PCT,
        BACKGROUND_REJECTION_THRESHOLD_PCT,
        USER_CPU_SHARE_PCT,
        USER_DURATION_SHARE_PCT,
        CONCENTRATION_THRESHOLD_PCT,
        DOMINANT_ITEM_SHARE_PCT,
    ]
}


def get_metric(name):
    """Look up a metric definition by name. Returns None if not found."""
    return METRIC_DEFINITIONS.get(name)


def is_proxy(name):
    """True if the metric is a CPU-time or duration proxy (not authoritative billed CU)."""
    m = METRIC_DEFINITIONS.get(name)
    return m is not None and m["metric_type"] in ("proxy_cpu", "proxy_dur")


def is_verified(name):
    """True if the formula was confirmed against production data."""
    m = METRIC_DEFINITIONS.get(name)
    return m is not None and m.get("verified", False)


# ---------------------------------------------------------------------------
# N14 -- Runtime MetricValue dataclass. Broader than the static METRIC_DEFINITIONS table
# above: every individual number the agent emits at runtime carries its provenance as
# structured data traveling WITH the value, rather than the agent having to separately
# recall and re-attach the right label/caveat/confidence every time. The output renderer
# decides whether to surface this as inline text, a footnote, or a hover -- but the
# provenance can no longer be silently dropped once a value is wrapped this way.
# ---------------------------------------------------------------------------
from dataclasses import dataclass
from typing import Literal, Optional

from ..confidence import ClaimConfidence

MetricType = Literal["true_CU", "proxy_cpu", "proxy_dur", "constant", "reference", "presentational"]


@dataclass
class MetricValue:
    """A single number plus everything needed to present it honestly.

    Construct via ``MetricValue.from_definition()`` when the value corresponds to a named
    entry in METRIC_DEFINITIONS (preferred -- keeps metric_type/source/smoothing_window/formula
    in sync with the grounding table by construction, not by copy-paste). Construct directly
    only for a genuinely ad-hoc/derived value with no METRIC_DEFINITIONS entry -- and in that
    case, label it accordingly (see SP6: any such value must be marked ``(derived)`` inline
    wherever it's presented, with `formula` populated so the footnote can be written).
    """
    value: float
    unit: str
    metric_type: MetricType
    source: str                          # field name + table/event, e.g. "capacityUnitMs (Capacity Overview Events)"
    smoothing_window: str
    formula: str
    confidence: ClaimConfidence
    verified: bool = False
    health_state_smoothing_window: Optional[str] = None  # N20: only set for the 3 throttle threshold types
    notes: str = ""

    @classmethod
    def from_definition(cls, name, value, *, confidence, unit=""):
        """Build a MetricValue from a METRIC_DEFINITIONS entry, so the static grounding table
        stays the single source of truth for everything except the live `value` and the
        per-claim `confidence` (which depends on what was actually present in THIS call's
        data, per confidence.claim_confidence() -- not a property of the metric definition
        itself).

        Raises KeyError if `name` isn't in METRIC_DEFINITIONS -- fail loudly rather than
        silently constructing an unlabeled MetricValue for an unknown metric.
        """
        d = METRIC_DEFINITIONS[name]
        return cls(
            value=value,
            unit=unit,
            metric_type=d["metric_type"],
            source=f"{', '.join(d['source_columns']) or d['name']} ({d['source_table']})",
            smoothing_window=d["smoothing_window"],
            formula=d["formula"],
            confidence=confidence,
            verified=d.get("verified", False),
            health_state_smoothing_window=d.get("health_state_smoothing_window"),
            notes=d.get("notes", ""),
        )

    def is_proxy(self):
        """True if this value is a CPU-time/duration proxy, never authoritative billed CU."""
        return self.metric_type in ("proxy_cpu", "proxy_dur")

    def display_caveat(self):
        """One-line, plain-language caveat for this value, or empty string if none applies.
        Never print the raw metric_type/flags to the user -- always route through this (or
        equivalent) translation, per the system prompt's 'never print a raw flag' rule."""
        if self.is_proxy():
            return ("a CPU-time proxy from monitored telemetry, not authoritative billed "
                    "capacity CU" if self.metric_type == "proxy_cpu" else
                    "a wall-clock duration proxy -- weaker than a CPU-time proxy, not billed CU")
        if self.metric_type == "constant":
            return "a constant/boolean flag, not a meaningful scale value -- never use as a divisor"
        if not self.verified:
            return "an unverified/assumed formula, not yet confirmed against production data"
        return ""
