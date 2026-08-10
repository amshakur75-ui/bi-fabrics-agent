"""Every ticket the backend writes must be displayable by the frontend that shows tickets.

The notification center filters on an `ACTIONABLE` set of checkType strings held in TypeScript,
while the checkTypes themselves are produced in Python — `sweep_delivery._family` for estate-sweep
findings and the `check` field for Tier-2 triggers. Nothing connected the two, so a family could be
ticketed and then never rendered: written, invisible, unclearable. That is how `lineage.blast-radius`
(family `lineage`) and `meta.detector-error` (family `meta`) both ended up silently undisplayable,
the second of which hides a DETECTOR CRASH — the worst possible thing to hide, because it means the
sweep quietly stopped covering something.

This test parses the TS set rather than duplicating it, so the two cannot drift apart.
"""
import pathlib
import re

import pytest

from fabric_audit_agent.automation.sweep_delivery import _family, _tier2_owned

_TSX = (pathlib.Path(__file__).resolve().parents[1]
        / "e2e-chatbot-app-next" / "client" / "src" / "components" / "notification-center.tsx")


def _actionable():
    """The ACTIONABLE set as the shipped component defines it."""
    if not _TSX.exists():                       # fail loudly: a skip is how this class of bug lives
        pytest.fail(f"notification-center.tsx not found at {_TSX}")
    text = _TSX.read_text(encoding="utf-8")
    m = re.search(r"const ACTIONABLE = new Set\(\[(.*?)\]\)", text, re.DOTALL)
    if not m:
        pytest.fail("could not locate the ACTIONABLE set literal in notification-center.tsx")
    body = re.sub(r"//[^\n]*", "", m.group(1))  # strip comments before harvesting the strings
    return set(re.findall(r"'([^']+)'", body))


# Finding-key prefixes the detectors actually emit. Derived from the detector modules, not from
# what anyone assumed the families were called -- the `blast_radius`-vs-`lineage` mismatch was
# exactly an assumed name that no producer used.
_DETECTOR_KEYS = [
    "activity.slow-operation", "capacity.concentration", "capacity.user-ranking",
    "cost.idle-capacity", "lineage.blast-radius", "meta.detector-error",
    "model.bidirectional", "pattern.cross-workspace", "pipeline.contention",
    "refresh.failure", "report.visual-count", "security.external-share",
    "query.dax-antipattern", "xmla.error",
    # Only capacity.concentration / capacity.throttle are Tier-2-owned; every OTHER capacity.*
    # key is ticketed by the sweep under the plain `capacity` family. Omitting these two from an
    # earlier draft of this list is why the gap survived the first pass -- the list has to be
    # driven by what the detectors emit, not by which names came to mind.
    "capacity.contention", "capacity.oversized-model",
]

# Tier-2 checkTypes that are meant to be USER-ACTIONABLE tickets. The early-warning signals
# (sustained / rate_change) and the digest are deliberately excluded from the center, so they are
# deliberately absent here too -- but see the auto-resolve test below for the other half of that.
_TIER2_ACTIONABLE_CHECKS = [
    "throttle", "pressure", "overage", "extreme_peak", "capacity_incident",
    "concentration", "cross_user", "blind_spot", "silent_failure",
]


@pytest.mark.parametrize("key", _DETECTOR_KEYS)
def test_every_sweep_finding_is_either_skipped_or_displayable(key):
    """The real invariant is a disjunction, not "everything is in ACTIONABLE": a finding whose
    family Tier-2 owns is deliberately never ticketed by the sweep (Tier-2 raises it in real time
    instead), so it needs no entry. What must never happen is the third case — ticketed AND not
    displayable."""
    if _tier2_owned(key):
        return                                  # never ticketed here; Tier-2 owns the real-time path
    family = _family(key)
    assert family in _actionable(), (
        f"{key!r} tickets as checkType {family!r}, which the notification center filters out — "
        "the finding would be written and then invisible in the app")


@pytest.mark.parametrize("check", _TIER2_ACTIONABLE_CHECKS)
def test_every_actionable_tier2_check_is_displayable(check):
    assert check in _actionable(), (
        f"tier-2 {check!r} creates a ticket the notification center will not render")


def test_the_early_warning_signals_are_excluded_on_purpose_but_can_still_resolve():
    """sustained / rate_change are capacity-status early warnings, not to-do items, so they stay
    out of the center by design. That is only safe if they also RESOLVE themselves: they used to
    take the attribution branch, which never resolves, so each firing minted a warn-severity
    ticket that no surface displayed and no human could clear — and rate_change (+15 points in one
    5-minute window) fires routinely on this tenant's 6->72% swings."""
    import inspect

    from fabric_audit_agent.automation import tier2_check
    src = inspect.getsource(tier2_check.process_alerts)
    m = re.search(r"_CAPACITY_CHECKS = \((.*?)\)", src, re.DOTALL)
    assert m, "could not locate _CAPACITY_CHECKS"
    auto_resolving = set(re.findall(r'"([^"]+)"', m.group(1)))
    for check in ("sustained", "rate_change"):
        assert check not in _actionable(), f"{check} is meant to stay out of the center"
        assert check in auto_resolving, (
            f"{check} is invisible in the app AND cannot auto-resolve — it would sit open forever")


def test_no_sweep_family_collides_with_a_tier2_owned_checktype():
    """The shared-table hazard from the other direction.

    audit_alerts is shared between the hourly sweep and the 5-minute tier2 job. Tier2's ownership
    filter decides what it may touch by checkType alone -- there is no producer column -- so if a
    SWEEP finding is ever written under a checkType tier2 owns, tier2 will mark it inactive within
    five minutes and the notification center will hide it from the Open tab. That is verbatim the
    P0 the ownership filter was added to fix, and it can come back through a naming collision
    rather than through the filter itself.

    This is not hypothetical: audit_alerts today holds a `capacity.user-concentration` row written
    under checkType `concentration` (a tier2-owned name) by a since-deleted detector, and the live
    tier2 run marks it inactive on every sweep.
    """
    import inspect

    from fabric_audit_agent.automation import tier2_check
    src = inspect.getsource(tier2_check.process_alerts)
    m = re.search(r"_TIER2_OWNED = set\(_CAPACITY_CHECKS\) \| \{(.*?)\}", src, re.DOTALL)
    assert m, "could not locate _TIER2_OWNED"
    owned = set(re.findall(r'"([^"]+)"', m.group(1)))
    m2 = re.search(r"_CAPACITY_CHECKS = \((.*?)\)", src, re.DOTALL)
    owned |= set(re.findall(r'"([^"]+)"', m2.group(1)))

    for key in _DETECTOR_KEYS:
        if _tier2_owned(key):
            continue                    # deliberately never ticketed by the sweep
        family = _family(key)
        assert family not in owned, (
            f"sweep finding {key!r} tickets as checkType {family!r}, which tier2 OWNS — tier2 will "
            "deactivate it within 5 minutes and the app will hide it")
