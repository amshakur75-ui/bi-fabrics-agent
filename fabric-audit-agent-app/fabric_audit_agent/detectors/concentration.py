"""Noisy-neighbor detector. Faithful port of the Node ``core/detectors/concentration.js``.

Flags any single item consuming >= threshold% of the capacity's CU (reads facts.items).
USER-FIRST: leads with the driving user(s) when activity-log attribution is attached,
names the owner for background-dominated load, else notes users are pending correlation.
"""
import math
from ..config import DEFAULT_CONFIG
from ..investigation.sku import round_pct
from .system_item_kinds import is_system_item_kind


def _fmt(x):
    """Render a share like JS template literals: 70.0 -> '70', 32.4 -> '32.4'."""
    return str(int(x)) if x == int(x) else str(x)


def detect_concentration(facts, config=None):
    config = config or DEFAULT_CONFIG
    items = (facts or {}).get("items") or []
    min_share = config["capacity"]["concentrationPct"]
    flags = []

    # E1 fix (Task 9, 2026-07-29): a concentration ratio only makes sense when its numerator
    # and denominator come from the SAME attribution mode. If the items feeding this detector
    # carry a MIX of modes (e.g. some ``cost-cpu`` from rows with CpuTimeMs alongside
    # ``cost-duration`` from rows that fell back to DurationMs), the pre-computed ``sharePct``
    # was formed with a MIXED denominator -- the resulting percentage silently blends two
    # different metrics and cannot be trusted at face value. We can only WARN here (sharePct
    # was computed upstream in rollup_attribution), so tag every emitted flag with an inline
    # caveat and a ``mixedSources`` evidence field a caller/agent can see. System-item-kind
    # items excluded above (N5) are NOT counted for mode-mixing purposes -- they were dropped
    # from the population, so their mode doesn't contribute to the denominator either.
    _modes = {
        it.get("attributionMode")
        for it in items
        if not is_system_item_kind(it.get("kind"))
        and it.get("attributionMode") is not None
    }
    mixed_sources = len(_modes) > 1
    unmeasurable = 0

    for it in items:
        # N5 fix (Task 8): a system item kind (EventStream / Activator /
        # FabricEvents-CapacityUtilizationEvents) is driven by exactly one service identity every
        # hour of every day -- "1 user = 100% concentration" fires trivially and permanently on
        # any of them. Silent skip when kind is present AND matches; leave items with an unknown
        # kind alone (conservative: better a false negative than falsely hiding a real workload).
        # See detectors/system_item_kinds.py for the evidence.
        if is_system_item_kind(it.get("kind")):
            continue
        # sharePct is None when NO row resolved a cost column (attribution_rollup sets
        # shareBasis="unavailable" alongside it). The bare `continue` treated that as "this item is
        # not concentrated", so a schema rename would take the flagship 30% feature out silently --
        # every item skipped, the estate reading clean, and blind_spot unable to help because the
        # items DO exist. Count them so the caller can tell "nothing concentrated" from "nothing
        # measurable"; tier2's _check_cross_source_blind_spot raises the alarm on the same signal.
        if it.get("sharePct") is None:
            unmeasurable += 1
            continue
        try:
            share = float(it.get("sharePct"))
        except (TypeError, ValueError):
            unmeasurable += 1
            continue
        if not math.isfinite(share) or share < min_share:
            continue
        share = round_pct(share)  # clip false precision (e.g. 49.213063380823705 -> 49.2)
        share_out = int(share) if share == int(share) else share   # 70.0 -> 70 for clean JSON parity

        ws = it.get("workspace") or "unknown workspace"
        # Honest label: Log Analytics / Eventhouse give a CPU-time PROXY over only the MONITORED
        # subset (the workspaces feeding telemetry), not a true capacity-wide CU share. Only
        # authoritative sources (CSV / Capacity Metrics) earn the words "capacity CU".
        # N3 fix: default to the HEDGED label ("monitored CU") for anything that isn't explicitly
        # unattributed -- covers "cost-cpu"/"cost-duration" (N7's split) AND the weaker
        # "frequency" mode (op-count only, no cost signal at all), which previously fell through
        # to the MOST authoritative label ("capacity CU") purely because it wasn't literally
        # the string "cost". Only a missing/None attributionMode gets "capacity CU" now.
        label = "capacity CU" if it.get("attributionMode") is None else "monitored CU"
        tu = it.get("topUsers")
        named = tu if isinstance(tu, list) and tu else None
        total_users = it.get("userCount")
        if total_users is None:
            total_users = it.get("users")
        if total_users is None and named:
            total_users = len(named)

        if named and it.get("background"):
            owner = it.get("owner") or named[0].get("user")
            what = (f"\"{it.get('name')}\" ({ws}) is using {_fmt(share)}% of {label} — "
                    f"driven mainly by background operations (owner/initiator: {owner}), not interactive users.")
        elif named:
            names = ", ".join(u.get("user") for u in named)
            more = max(0, total_users - len(named)) if total_users is not None else 0
            suffix = f" + {_fmt(more)} more" if more > 0 else ""   # int-valued floats render as "10" not "10.0"
            what = f"{names}{suffix} are driving {_fmt(share)}% of {label} via \"{it.get('name')}\" ({ws})."
        else:
            who = f"{_fmt(it['users'])} user(s)" if it.get("users") else "unknown users"
            what = (f"\"{it.get('name')}\" ({ws}) is using {_fmt(share)}% of {label} across {who} "
                    f"— specific users pending activity-log correlation.")

        evidence = {
            "sharePct": share_out, "cuSeconds": it.get("cuSeconds"), "kind": it.get("kind"),
            "users": it.get("users"), "userCount": it.get("userCount"), "topUsers": named,
            "background": it.get("background") or False, "owner": it.get("owner"),
            "attributionMode": it.get("attributionMode"),
        }
        # GAP-4 (N24, 2026-07-30): strengthen the proxy caveat on every fired concentration
        # alert -- additive field, does not alter any existing evidence key.
        # I1 fix (2026-07-30): gate the wording on attributionMode instead of stamping it
        # unconditionally. attributionMode is None on the branch reserved (see the "capacity CU"
        # vs "monitored CU" label above) for authoritative CSV/Capacity-Metrics data -- an
        # authoritative alert must NOT carry a CpuTimeMs-proxy caveat at all. "cost-duration"
        # rows are attributed via DurationMs (N7's fallback mode), not CpuTimeMs, so they need
        # their own accurate wording naming the right field.
        _attribution_mode = it.get("attributionMode")
        if _attribution_mode == "cost-cpu":
            evidence["proxyWarning"] = (
                "User attribution is based on CpuTimeMs proxy. XMLA Read Operations can "
                "undercount true CU by 10-25×. Validate in the Metrics app."
            )
        elif _attribution_mode == "cost-duration":
            evidence["proxyWarning"] = (
                "User attribution is based on DurationMs proxy (wall-clock duration, not CPU "
                "time -- a weaker proxy than CpuTimeMs). Validate in the Metrics app."
            )
        # else: attributionMode is None -- authoritative CSV/Capacity-Metrics data; no proxy
        # warning applies.
        if mixed_sources:
            evidence["mixedSources"] = True
            evidence["mixedSourcesNote"] = (
                "sharePct was formed with a denominator that mixed attribution modes "
                f"({sorted(m for m in _modes if m)}). The ratio silently blends two different "
                "cost signals -- treat the reported percentage as approximate, not authoritative."
            )
            what = what + " (Note: sharePct mixes cost bases across items -- treat as approximate.)"
        flags.append({
            "type": "capacity.concentration",
            "resource": f"{it.get('workspace') or '(unknown ws)'} / {it.get('name')}",
            "when": it.get("observedAt") or "",
            "evidence": evidence,
            "what": what,
        })
    # EVERY item unmeasurable is a coverage failure, not a clean estate. Without this, a cost column
    # that stopped resolving took the flagship 30% concentration feature out in total silence: each
    # item skipped individually, no flag raised, and the run reporting normally.
    if unmeasurable and unmeasurable == len([i for i in items
                                             if not is_system_item_kind(i.get("kind"))]):
        flags.append({
            "type": "meta.attribution-unmeasurable",
            "resource": "capacity",
            "when": "",
            "evidence": {"itemsSeen": unmeasurable,
                         "shareBasis": "unavailable"},
            "what": (f"{unmeasurable} item(s) came back with no usable cost signal, so no "
                     "concentration share could be computed for ANY of them. This is a coverage "
                     "gap, not an all-clear -- check the attribution query's cost column."),
        })
    return flags
