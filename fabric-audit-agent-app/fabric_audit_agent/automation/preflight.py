"""preflight.py — startup config/health preflight (alerting redesign Sub-plan 5, Part 5d).

``job.py::_check_startup_invariant`` already catches ONE specific drift (the MODEL_MAP catalog
invariant) at startup. This extends that startup moment into a fuller, still read-only, health
SNAPSHOT: which data sources have their required env vars present/absent, and whether the
pre-built field catalog is on disk. It is deliberately NOT a connection test -- it never makes a
live network call, so it is safe and cheap to run on every job start (Job/Tier2/daily) without
adding latency or a new failure mode of its own.

``run_preflight`` never raises: any unexpected error while probing is folded into a failed check
rather than propagated, mirroring ``_check_startup_invariant``'s never-crash-the-run contract.
"""
from pathlib import Path


def _env_check(name, env, required):
    """One named check: does *env* have every var in *required* (a list of env var names)?"""
    missing = [k for k in required if not env.get(k)]
    if not missing:
        return {"name": name, "ok": True, "detail": None}
    if len(missing) == len(required):
        detail = f"not configured (missing: {', '.join(missing)})"
    else:
        detail = f"partially configured (missing: {', '.join(missing)})"
    return {"name": name, "ok": False, "detail": detail}


def _catalog_check():
    """Is the pre-built field catalog manifest on disk? Read-only stat, no parsing."""
    try:
        from ..resolve import catalog_dir
        manifest = catalog_dir() / "manifest.json"
        if manifest.exists():
            return {"name": "catalog-manifest", "ok": True, "detail": None}
        return {"name": "catalog-manifest", "ok": False,
                "detail": f"manifest.json not found at {manifest}"}
    except Exception as exc:
        return {"name": "catalog-manifest", "ok": False,
                "detail": f"{type(exc).__name__}: {exc}"}


def _csv_check(env):
    """CSV source: present iff FABRIC_CSV_PATHS is set AND every listed path exists on disk.
    Absent (unset) is reported as ok -- CSV is one of several optional sources, not a required
    one; only a *configured-but-missing* path is a real problem."""
    raw = (env.get("FABRIC_CSV_PATHS") or "").replace(";", ",")
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    if not paths:
        return {"name": "csv-paths", "ok": True, "detail": "not configured (optional source)"}
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        return {"name": "csv-paths", "ok": False,
                "detail": f"configured path(s) not found: {', '.join(missing)}"}
    return {"name": "csv-paths", "ok": True, "detail": None}


def run_preflight(env, expect=None):
    """Read-only startup health snapshot. Never raises, never makes a live/network call.

    Checks:
      - Log Analytics (per-user attribution): FABRIC_LA_WORKSPACE_ID + FABRIC_CLIENT_ID
      - Capacity Events (Kusto true-CU stream): FABRIC_CAPACITY_EVENTS_CLUSTER +
        FABRIC_CAPACITY_EVENTS_DB + FABRIC_CLIENT_ID
      - CSV export source: FABRIC_CSV_PATHS, when set, must resolve to existing file(s)
      - Field catalog: ``data/plugin/catalog/manifest.json`` present on disk

    Each data-source env check reports ok=True when EITHER fully configured OR (for the
    optional CSV source) simply unset -- an unconfigured *live* source (LA / capacity events)
    is reported ok=False since at least one live or CSV source is expected to be usable in a
    real deployment, but this function does not decide policy about which combination is
    required -- it only reports what it observed; callers decide what "degraded" means for them.

    ``expect`` (optional): the set of DATA-SOURCE check names this particular job actually needs
    (e.g. ``{"log-analytics"}`` for the nightly baseline job, which deliberately passes no Kusto
    vars). A source outside that set is reported ``ok=True`` with a "not applicable" detail
    instead of a failure. Without this, the baseline job reported
    ``preflight degraded: capacity-events: partially configured`` on EVERY run — permanently
    yellow, so a real degradation in that same line would go unnoticed. Omit ``expect`` to check
    every source (the sweep / tier2 behaviour).

    Returns ``{"ok": bool, "checks": [...], "degraded": bool}`` -- ``ok`` is True only when every
    check passed; ``degraded`` mirrors ``not ok`` (kept as a separate key for symmetry with
    ``automation.health.HealthReport``, whose callers already look for a ``degraded`` field).
    """
    env = env if env is not None else {}
    checks = []

    def _scoped(check):
        """Neutralize a data-source check this job does not use."""
        if expect is None or check["ok"] or check["name"] in expect:
            return check
        return {"name": check["name"], "ok": True,
                "detail": "not applicable to this job"}
    try:
        checks.append(_scoped(_env_check(
            "log-analytics", env, ["FABRIC_LA_WORKSPACE_ID", "FABRIC_CLIENT_ID"])))
        checks.append(_scoped(_env_check(
            "capacity-events", env,
            ["FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB", "FABRIC_CLIENT_ID"])))
        checks.append(_csv_check(env))
        checks.append(_catalog_check())
    except Exception as exc:
        checks.append({"name": "preflight", "ok": False,
                        "detail": f"unexpected error: {type(exc).__name__}: {exc}"})

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "degraded": not ok}
