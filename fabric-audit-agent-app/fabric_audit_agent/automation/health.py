"""Permanent unhealthy-state visibility (alerting redesign Sub-plan 4, Part 4).

Today the only "health" signal is a heartbeat timestamp (``tier2_check.py``'s
``heartbeat_store``) — it says the job *ran*, never *whether it worked*. Real silent failures (a
collector returning no data, a chat-write throwing, a ticket-write failing, the readings store
being unavailable, the startup MODEL_MAP invariant drifting) are swallowed with a bare ``print``
that nobody watches. ``HealthReport`` gives those outcomes ONE place to land, so a degraded agent
is visible — in the daily digest banner and to anything that wants to query it — instead of only
discoverable by reading job logs after the fact.

Pure and injectable: no I/O of its own. Callers own the instance, pass it down through the
functions that already know when something silently failed, and read ``degraded`` / ``summary``
back out. See ``docs/WIRING-MAP.md`` for the full map of which known silent-failure sites feed
this and which remain print-only (future work).
"""


class HealthReport:
    """Accumulates collector / detector / delivery outcomes for one run (or one process)."""

    def __init__(self):
        self.collectors = []   # [{"name": str|None, "ok": bool, "reason": str|None}]
        self.detectors = []    # [{"name": str, "ok": bool, "reason": str|None}]
        self.deliveries = []   # [{"channel": str, "ok": bool, "reason": str|None}]
        self.issues = []       # [str] — free-form (e.g. startup invariant, readings store)

    # ---- recorders ----------------------------------------------------------------

    def record_collector(self, name, ok, reason=None):
        """One collector's outcome. ``name`` may be None when the failure came from a merged
        source that only carries an error string (see ``record_collector_failures``)."""
        self.collectors.append({"name": name, "ok": bool(ok), "reason": reason})

    def record_collector_failures(self, failed):
        """Feed ``merged["sourcesFailed"]`` (adapters/collector_merge.py) straight in — it is
        already a list of per-source error strings; reuse it rather than reclassifying it."""
        for reason in failed or []:
            self.record_collector(None, False, str(reason))

    def record_detector(self, name, ok, reason=None):
        self.detectors.append({"name": name, "ok": bool(ok), "reason": reason})

    def record_delivery(self, channel, ok, reason=None):
        """``channel`` e.g. ``"chat"``, ``"ticket"``, ``"webhook"``."""
        self.deliveries.append({"channel": channel, "ok": bool(ok), "reason": reason})

    def record_issue(self, text):
        """A standalone health issue not tied to a collector/detector/delivery outcome — e.g. the
        startup MODEL_MAP invariant, or the readings store being unavailable."""
        if text:
            self.issues.append(str(text))

    # ---- derived state --------------------------------------------------------------

    @property
    def degraded(self):
        return (
            any(not c["ok"] for c in self.collectors)
            or any(not d["ok"] for d in self.detectors)
            or any(not d["ok"] for d in self.deliveries)
            or bool(self.issues)
        )

    @property
    def summary(self):
        """Human-readable digest of everything wrong. Empty string when healthy."""
        parts = []

        failed_collectors = [c for c in self.collectors if not c["ok"]]
        if failed_collectors:
            reasons = "; ".join(c["reason"] or c["name"] or "unknown reason" for c in failed_collectors)
            noun = "collector" if len(failed_collectors) == 1 else "collectors"
            parts.append(f"{len(failed_collectors)} {noun} failed ({reasons})")

        failed_detectors = [d for d in self.detectors if not d["ok"]]
        if failed_detectors:
            names = ", ".join(d["name"] or "unknown" for d in failed_detectors)
            noun = "detector" if len(failed_detectors) == 1 else "detectors"
            parts.append(f"{len(failed_detectors)} {noun} errored ({names})")

        failed_deliveries = [d for d in self.deliveries if not d["ok"]]
        if failed_deliveries:
            by_channel = {}
            for d in failed_deliveries:
                by_channel.setdefault(d["channel"], []).append(d["reason"])
            bits = []
            for ch, reasons in sorted(by_channel.items()):
                named = [r for r in reasons if r]
                detail = f" ({'; '.join(named)})" if named else ""
                bits.append(f"{len(reasons)} {ch} write(s) failed{detail}")
            parts.append("; ".join(bits))

        parts.extend(self.issues)
        return "; ".join(parts)

    def to_dict(self):
        """A queryable snapshot — safe to embed in a run envelope or serve from an endpoint."""
        return {
            "degraded": self.degraded,
            "summary": self.summary,
            "collectors": list(self.collectors),
            "detectors": list(self.detectors),
            "deliveries": list(self.deliveries),
            "issues": list(self.issues),
        }


def render_health_line(report):
    """A one-line digest banner, or ``None`` when everything is healthy (including when
    ``report`` itself is None — no report means nothing was tracked, never treated as an outage)."""
    if report is None or not report.degraded:
        return None
    return f"⚠ Degraded: {report.summary}"
