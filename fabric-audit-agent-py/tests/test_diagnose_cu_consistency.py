"""B4 wire-in regression (Task follow-up, 2026-07-29): ``diagnose_throttle`` now runs
``assert_cu_consistency`` per burndown-chain window when a live ``base_cu`` is available.
Any window where reported ``cuPct`` and reported ``overageAddMs`` don't reconcile to the same
excess-over-100% is captured in the step's ``sourceInconsistencies`` evidence -- the diagnosis
stays useful, but the mismatch is visible so the agent can caveat instead of presenting the
two figures as if they agree.

Scenario mirrors the documented B4 example: cuPct 105.1 with a much-smaller overageAddMs that
implies ~102.6% -- a 2.5pp discrepancy that exceeds the 1pp tolerance."""
from fabric_audit_agent.investigation.diagnose import diagnose_throttle


def _base_cu():
    return 1024


def _window_budget_ms():
    # 30-second window at F1024: base_cu * 1000 * 30 = 30,720,000 CU-ms.
    return _base_cu() * 1000 * 30


def _consistent_burndown_series():
    """A tiny series whose cuPct and overageAddMs reconcile cleanly. cuPct=105 -> excess=5%
    -> overageAddMs must equal ~ 5% of one window's budget = 1,536,000 CU-ms."""
    budget = _window_budget_ms()
    excess_ms = 0.05 * budget
    return [
        {"ts": "2026-07-29T12:00:00Z", "cuPct": 105.0, "pct": 105.0,
         "overageAddMs": excess_ms, "overageTotalMs": excess_ms, "overageCumulativePct": 5.0,
         "minutesToBurndown": 5},
    ]


def _inconsistent_burndown_series():
    """The documented B4 bug case: reported cuPct 105.1 alongside a very small overageAddMs
    (786,000 CU-ms -- only ~2.56% of one window's budget) so the two figures disagree by well
    over the 1pp tolerance."""
    return [
        {"ts": "2026-07-29T13:00:00Z", "cuPct": 105.1, "pct": 105.1,
         "overageAddMs": 786_000, "overageTotalMs": 786_000, "overageCumulativePct": 5.1,
         "minutesToBurndown": 3},
    ]


def _events(cu_seconds=200.0, ts="2026-07-29T12:00:15Z"):
    """One event roughly in the window so the throttle diagnosis passes stage-1 (timepointsOver
    > 0) and reaches the burndown step where B4's check runs."""
    return [{"ts": ts, "item": "Ent-Reporting-Sales", "user": "alice@co",
             "cuSeconds": cu_seconds, "operation": "QueryEnd", "kind": "interactive",
             "durationMs": 5000}]


def test_diagnose_throttle_flags_source_inconsistency_when_base_cu_is_supplied():
    """The B4 documented mismatch: 105.1% CU% with 786K overageAddMs implies only ~2.56%
    excess, a >2pp gap that must be surfaced."""
    result = diagnose_throttle(_inconsistent_burndown_series(), _events(),
                                has_real_cost=True, base_cu=_base_cu())
    burndown_step = next(s for s in result["chain"]
                          if s["hypothesis"] == "Overage is accumulating on this capacity")
    assert "sourceInconsistencies" in burndown_step["evidence"]
    issues = burndown_step["evidence"]["sourceInconsistencies"]
    assert len(issues) == 1
    assert "105.1" in issues[0]["reason"]
    assert "786000" in issues[0]["reason"] or "786,000" in issues[0]["reason"] \
        or "786.000" in issues[0]["reason"]
    # Human-readable caveat is present so the agent doesn't have to invent one.
    assert "sourceInconsistenciesNote" in burndown_step["evidence"]
    assert "reconcile" in burndown_step["evidence"]["sourceInconsistenciesNote"]


def test_diagnose_throttle_stays_silent_when_windows_are_consistent():
    """A clean burndown series (cuPct + overageAddMs agree on the excess) produces no
    sourceInconsistencies key -- absence is the signal that everything reconciles."""
    result = diagnose_throttle(_consistent_burndown_series(), _events(),
                                has_real_cost=True, base_cu=_base_cu())
    burndown_step = next(s for s in result["chain"]
                          if s["hypothesis"] == "Overage is accumulating on this capacity")
    assert "sourceInconsistencies" not in burndown_step["evidence"]
    assert "sourceInconsistenciesNote" not in burndown_step["evidence"]


def test_diagnose_throttle_skips_check_when_base_cu_missing():
    """A caller without a resolved base_cu simply gets NO added check -- the diagnosis still
    completes and the burndown step still fires (backward-compat: no base_cu param before)."""
    result = diagnose_throttle(_inconsistent_burndown_series(), _events(),
                                has_real_cost=True)   # no base_cu
    burndown_step = next(s for s in result["chain"]
                          if s["hypothesis"] == "Overage is accumulating on this capacity")
    # No consistency check ran -> no key. The chain step is still emitted and confirmed.
    assert burndown_step["verdict"] == "confirmed"
    assert "sourceInconsistencies" not in burndown_step["evidence"]


def test_diagnose_throttle_does_not_crash_on_check_failure():
    """Even with a deliberately-broken window that would trip assert_cu_consistency, the
    diagnosis MUST NOT raise -- the check's failure is captured as evidence, not propagated."""
    result = diagnose_throttle(_inconsistent_burndown_series(), _events(),
                                has_real_cost=True, base_cu=_base_cu())
    # Sanity: the diagnosis returned a normal-shaped result (symptom + chain + confidence)
    # rather than raising InconsistentSourcesError out.
    assert result["symptom"] == "throttle"
    assert result["confidence"] in ("low", "medium", "high")
    assert isinstance(result["chain"], list) and len(result["chain"]) >= 2
