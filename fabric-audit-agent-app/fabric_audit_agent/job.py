"""Databricks Job entrypoint — the production read-only sweep (Python-wheel task).

Builds REAL adapters from environment / Databricks secret-scope config and runs one audit:
  collector -> REST over Fabric/Power BI Admin APIs (Entra SP, client-credentials)
  reasoner  -> Claude (Anthropic SDK / Databricks-hosted), KB fallback on error
  delivery  -> no-op stub (Phase 10 will wire real Entra bot delivery)
  store     -> run history (Delta / Unity Catalog)

Every port is injectable, so the wiring is unit-testable without the real SDKs (tests pass
fakes). Read-only posture is absolute: the agent only reads telemetry and posts findings.

Identity note: only an Entra app registration / service principal in an allowed security
group can call the Power BI / Fabric Admin APIs — a bare Managed Identity cannot.
"""
import json
import os
import time

from .pipeline import run_audit
from .config import DEFAULT_CONFIG, merge_config
from .reasoner_stub import create_stub_reasoner
from .identity import resolve_identity, emit_identity_audit

def _append_error_record(store, t0):
    """Append a minimal observability record on sweep failure. Failure-isolated — never raises."""
    from datetime import datetime, timezone
    try:
        if store is not None:
            store["append"]({
                "runAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "durationMs": round((time.monotonic() - t0) * 1000),
                "errored": True,
                "tokenUsage": None,
            })
    except Exception:
        pass


_REST_ENV = {
    "capacityUrl": "FABRIC_CAPACITY_URL",
    "refreshesUrl": "FABRIC_REFRESHES_URL",
    "datasetsUrl": "FABRIC_DATASETS_URL",
    "reportsUrl": "FABRIC_REPORTS_URL",
    "pipelinesUrl": "FABRIC_PIPELINES_URL",
    "lineageUrl": "FABRIC_LINEAGE_URL",
    "accessUrl": "FABRIC_ACCESS_URL",
    "usageUrl": "FABRIC_USAGE_URL",
}


def _require(env, name):
    v = env.get(name)
    if not v:
        raise RuntimeError(f"Missing required config: {name} (set via Databricks secret scope / job env).")
    return v


def build_rest_config(env=None):
    """Collector endpoint URLs present in the environment (representative; verify at deploy)."""
    env = env if env is not None else os.environ
    return {key: env[var] for key, var in _REST_ENV.items() if env.get(var)}


def _default_collector(env):
    from .adapters.clients import EntraHttp, build_entra_token_provider
    from .adapters.collector_rest import create_rest_collector
    token = build_entra_token_provider(
        _require(env, "FABRIC_TENANT_ID"), _require(env, "FABRIC_CLIENT_ID"), _require(env, "FABRIC_CLIENT_SECRET")
    )
    return create_rest_collector(EntraHttp(token), build_rest_config(env))


def _wants_llm(env):
    """True if any LLM reasoner is configured; else the offline stub is used."""
    return bool(env.get("ANTHROPIC_API_KEY") or env.get("DATABRICKS_CLAUDE_ENDPOINT")
                or env.get("FABRIC_REASONER", "").lower() == "databricks")


def _default_reasoner(env, config):
    from .adapters.reasoner_claude import create_claude_reasoner
    endpoint = env.get("DATABRICKS_CLAUDE_ENDPOINT")
    if endpoint or env.get("FABRIC_REASONER", "").lower() == "databricks":
        from .adapters.clients import build_databricks_claude_client
        endpoint = endpoint or "databricks-claude-opus-4-7"
        return create_claude_reasoner(build_databricks_claude_client(endpoint), model=endpoint, config=config)
    from .adapters.clients import build_anthropic_client
    return create_claude_reasoner(build_anthropic_client(api_key=env.get("ANTHROPIC_API_KEY")), config=config)


def _default_store(env):
    catalog = env.get("FABRIC_DELTA_CATALOG")
    schema = env.get("FABRIC_DELTA_SCHEMA")
    if catalog and schema:
        try:
            from .adapters.store_delta import create_delta_store
            return create_delta_store(catalog, schema)
        except (ImportError, RuntimeError):
            pass
    from .adapters.store_local import create_local_store
    return create_local_store(env.get("AUDIT_HISTORY_PATH", "/tmp/fabric-audit/history.json"))


def _default_findings_store(env):
    """Construct the audit_findings store when Delta is configured, else None."""
    catalog = env.get("FABRIC_DELTA_CATALOG")
    schema = env.get("FABRIC_DELTA_SCHEMA")
    if catalog and schema:
        try:
            from .context_findings import create_findings_store_delta
            return create_findings_store_delta(catalog, schema)
        except (ImportError, RuntimeError):
            pass
    return None


def run_job(collector=None, reasoner=None, delivery=None, store=None,
            config=None, agent_id="fabric-audit-agent", tenant=None, now=None, env=None):
    """Run one production sweep. Pass any port to override; unset ports are built from ``env``."""
    env = env if env is not None else os.environ
    if config is None:
        raw = env.get("FABRIC_AUDIT_CONFIG")
        config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG
    collector = collector if collector is not None else _default_collector(env)
    reasoner = reasoner if reasoner is not None else _default_reasoner(env, config)
    if delivery is None:
        delivery = {"deliver": lambda envelope: None}
    store = store if store is not None else _default_store(env)
    t0 = time.monotonic()
    try:
        return run_audit(collector, reasoner, delivery, store=store, config=config,
                         agent_id=agent_id, tenant=tenant, now=now)
    except Exception:
        _append_error_record(store, t0)
        raise


# ---- no-permission CSV sweep (host on Databricks today; live sources plug in later) ----

def _csv_paths_from_env(env):
    raw = (env.get("FABRIC_CSV_PATHS") or "").replace(";", ",")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _write_outputs(out_dir, envelope):
    """Write ``latest.json``/``report.md`` to *out_dir* (a Volume — a durable, shareable dump).
    Egress chokepoint (Phase 5.2): gate the WRITTEN copy only (sink="file"); the caller's
    ``envelope`` (and its return value elsewhere) is never mutated or replaced."""
    from .report_md import build_markdown_report
    from .egress import apply_egress_controls, disclosure_line
    safe, meta = apply_egress_controls(envelope, sink="file")
    line = disclosure_line(meta)
    if line and isinstance(safe, dict):
        safe["summary"] = f"{(safe.get('summary') or '').rstrip()} {line}".strip()
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(safe, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(build_markdown_report(safe))
    return out_dir


def run_csv_job(csv_paths=None, out_dir=None, env=None, reasoner=None, delivery=None,
                store=None, findings_store=None, config=None, agent_id="fabric-audit-agent",
                tenant=None, now=None):
    """No-permission sweep: audit a Capacity Metrics CSV/.vpax export end to end on Databricks.

    Builds the CSV collector + reasoner (Claude if ANTHROPIC_API_KEY, else the offline stub), runs
    the full read-only pipeline, writes ``latest.json`` + ``report.md`` to ``out_dir`` (a Volume).
    Every port is injectable for tests. Needs no service principal / tenant permissions — only the
    exported CSV(s).
    """
    env = env if env is not None else os.environ
    paths = csv_paths if csv_paths is not None else _csv_paths_from_env(env)
    if not paths:
        raise RuntimeError("run_csv_job: no CSV paths — pass csv_paths or set FABRIC_CSV_PATHS.")
    out_dir = out_dir if out_dir is not None else env.get("FABRIC_OUT_DIR", "/tmp/fabric-audit")

    if config is None:
        raw = env.get("FABRIC_AUDIT_CONFIG")
        config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG
    if reasoner is None:
        reasoner = _default_reasoner(env, config) if _wants_llm(env) else create_stub_reasoner(config)
    if delivery is None:
        delivery = {"deliver": lambda envelope: None}
    if store is None:
        store = _default_store(env)
    if findings_store is None:
        findings_store = _default_findings_store(env)

    from .adapters.collector_csv import create_csv_collector
    t0 = time.monotonic()
    try:
        envelope = run_audit(create_csv_collector(paths), reasoner, delivery, store=store,
                             findings_store=findings_store, config=config,
                             agent_id=agent_id, tenant=tenant, now=now)
    except Exception:
        _append_error_record(store, t0)
        raise
    _write_outputs(out_dir, envelope)
    return envelope


def _alert_failure(exc, env, now_iso=None):
    """Failure alert stub — delivery wired in Phase 10 (Entra bot identity).
    Called from job_main/tier2_main on sweep failure; currently a no-op."""
    return False


def main():
    try:
        envelope = run_job()
    except Exception as exc:
        _alert_failure(exc, os.environ)
        raise
    print(envelope["summary"])
    return envelope


def csv_main():
    envelope = run_csv_job()
    print(envelope["summary"])
    return envelope


# ---- unified production sweep (CSV now; live sources auto-included once configured) ----

def build_collector_from_env(env, window=None):
    """Compose the collector from whatever is configured — the SAME deployment grows as access lands.

    CSV (no permissions) is included whenever ``FABRIC_CSV_PATHS`` is set; the live sources switch on
    automatically once their env + SP secrets exist. CSV is listed first so its authoritative CU
    share wins on merge (see ``collector_merge``).

    ``window`` (e.g. ``"7d"``) overrides every telemetry source's lookback — used by tools that
    thread a ``days`` argument; when None, each source keeps its own env-configured window.
    """
    collectors = []

    paths = _csv_paths_from_env(env)
    if paths:
        from .adapters.collector_csv import create_csv_collector
        collectors.append(create_csv_collector(paths))

    # Full estate metadata over the Admin REST API (Entra SP) — when permissions land.
    if env.get("FABRIC_CLIENT_ID") and build_rest_config(env):
        collectors.append(_default_collector(env))

    # Per-user attribution from Workspace Monitoring (KQL Eventhouse) — when permissions land.
    if env.get("FABRIC_KUSTO_CLUSTER") and env.get("FABRIC_KUSTO_DB") and env.get("FABRIC_CLIENT_ID"):
        from .adapters.clients import build_kusto_query
        from .adapters.collector_workspace_monitoring import create_workspace_monitoring_collector
        query = build_kusto_query(
            env["FABRIC_KUSTO_CLUSTER"], env["FABRIC_KUSTO_DB"],
            _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"),
        )
        wm_cfg = {"window": window if window is not None else env.get("FABRIC_KUSTO_WINDOW", "1d")}
        if env.get("FABRIC_KUSTO_KQL"):
            wm_cfg["kql"] = env["FABRIC_KUSTO_KQL"]
        collectors.append(create_workspace_monitoring_collector(query, wm_cfg))

    # Per-user attribution from Azure Log Analytics — for workspaces wired to LA instead of the
    # Eventhouse (e.g. capacity-sensitive prod, where the Monitoring Eventhouse's CU cost is undesirable).
    if env.get("FABRIC_LA_WORKSPACE_ID") and env.get("FABRIC_CLIENT_ID"):
        from .adapters.clients import build_log_analytics_query
        from .adapters.collector_log_analytics import create_log_analytics_collector
        la_query = build_log_analytics_query(
            env["FABRIC_LA_WORKSPACE_ID"],
            _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"),
        )
        la_cfg = {"window": window if window is not None else env.get("FABRIC_LA_WINDOW", "1d")}
        if env.get("FABRIC_LA_WORKSPACE_FILTER"):
            la_cfg["workspaceFilter"] = env["FABRIC_LA_WORKSPACE_FILTER"]
        if env.get("FABRIC_LA_KQL"):
            la_cfg["kql"] = env["FABRIC_LA_KQL"]
        if env.get("FABRIC_LA_WORKSPACE_LABEL"):
            la_cfg["workspace"] = env["FABRIC_LA_WORKSPACE_LABEL"]
        collectors.append(create_log_analytics_collector(la_query, la_cfg))

    # Live capacity CU% / throttle from Real-Time Hub Capacity Overview Events (custom Eventhouse).
    if (env.get("FABRIC_CAPACITY_EVENTS_CLUSTER") and env.get("FABRIC_CAPACITY_EVENTS_DB")
            and env.get("FABRIC_CLIENT_ID")):
        from .adapters.clients import build_kusto_query
        from .adapters.collector_capacity_events import create_capacity_events_collector
        ce_query = build_kusto_query(
            env["FABRIC_CAPACITY_EVENTS_CLUSTER"], env["FABRIC_CAPACITY_EVENTS_DB"],
            _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"),
        )
        ce_cfg = {"window": window if window is not None else env.get("FABRIC_CAPACITY_EVENTS_WINDOW", "1d")}
        if env.get("FABRIC_CAPACITY_EVENTS_TABLE"):
            ce_cfg["table"] = env["FABRIC_CAPACITY_EVENTS_TABLE"]
        if env.get("FABRIC_CAPACITY_EVENTS_KQL"):
            ce_cfg["kql"] = env["FABRIC_CAPACITY_EVENTS_KQL"]
        collectors.append(create_capacity_events_collector(ce_query, ce_cfg))

    # Capacity sku/quota over Azure ARM List Usages — when permissions land.
    if env.get("FABRIC_USAGES_URL") or env.get("FABRIC_CAPACITIES_URL"):
        from .adapters.clients import EntraHttp, build_entra_token_provider, ARM_SCOPE
        from .adapters.collector_list_usages import create_list_usages_collector
        tenant = _require(env, "FABRIC_TENANT_ID")
        client = _require(env, "FABRIC_CLIENT_ID")
        secret = _require(env, "FABRIC_CLIENT_SECRET")
        capacities_http = EntraHttp(build_entra_token_provider(tenant, client, secret))
        usages_http = (EntraHttp(build_entra_token_provider(tenant, client, secret, scope=ARM_SCOPE))
                       if env.get("FABRIC_USAGES_URL") else None)
        collectors.append(create_list_usages_collector(capacities_http, {
            "capacitiesUrl": env.get("FABRIC_CAPACITIES_URL"),
            "usagesUrl": env.get("FABRIC_USAGES_URL"),
            "capacity": env.get("FABRIC_CAPACITY"),
        }, usages_http=usages_http))

    if not collectors:
        raise RuntimeError("build_collector_from_env: no sources configured — set FABRIC_CSV_PATHS "
                           "and/or the live-source env (FABRIC_*_URL / FABRIC_KUSTO_*).")
    if len(collectors) == 1:
        return collectors[0]
    from .adapters.collector_merge import create_merged_collector
    return create_merged_collector(collectors)


def _emit_identity_audit(env):
    """Label-only identity audit at the primary sweep path (Phase 5.3 Task 2)."""
    try:
        resolved = resolve_identity(env)
    except RuntimeError:
        return
    emit_identity_audit(resolved)


def run_unified_job(env=None, out_dir=None, reasoner=None, delivery=None, store=None,
                    findings_store=None, config=None, agent_id="fabric-audit-agent",
                    tenant=None, now=None):
    """Production sweep: audit whatever sources are configured, end to end.

    Composes the collector via ``build_collector_from_env`` (CSV now; live sources auto-included as
    permissions land), runs the read-only pipeline, writes ``latest.json`` + ``report.md`` to
    ``out_dir`` (a Volume). Ports are injectable for tests. The deployed job is unchanged as access
    grows.
    """
    env = env if env is not None else os.environ
    out_dir = out_dir if out_dir is not None else env.get("FABRIC_OUT_DIR", "/tmp/fabric-audit")
    if config is None:
        raw = env.get("FABRIC_AUDIT_CONFIG")
        config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG
    _emit_identity_audit(env)
    collector = build_collector_from_env(env)
    if reasoner is None:
        reasoner = _default_reasoner(env, config) if _wants_llm(env) else create_stub_reasoner(config)
    if delivery is None:
        # Phase 10: Entra bot identity will provide Teams delivery here.
        delivery = {"deliver": lambda envelope: None}
    if store is None:
        store = _default_store(env)
    if findings_store is None:
        findings_store = _default_findings_store(env)
    prev_history = store["history"]()
    t0 = time.monotonic()
    try:
        envelope = run_audit(collector, reasoner, delivery, store=store,
                             findings_store=findings_store, config=config,
                             agent_id=agent_id, tenant=tenant, now=now)
    except Exception:
        _append_error_record(store, t0)
        raise
    _write_outputs(out_dir, envelope)
    _maybe_alert(envelope, prev_history, env)
    _check_tier2_health(env)
    return envelope


def _check_tier2_health(env):
    """Task 9.4: check Tier 2 heartbeat; alert if stale. Failure-isolated — never fails the sweep."""
    try:
        status = _check_tier2_heartbeat(env)
        if status.get("stale"):
            pass  # Phase 10: Entra bot identity will provide alerting here.
    except Exception:
        pass


def _maybe_alert(envelope, prev_history, env):
    """Alert-on-change decision — delivery wired in Phase 10 (Entra bot identity).
    decide_alert() still runs and its result is returned for observability."""
    try:
        from .automation.alerting import decide_alert
        return decide_alert(envelope, prev_history)
    except Exception:
        return None


def _merge_named_params_into_env():
    """Parse ``--KEY=VALUE`` named parameters from sys.argv into os.environ.

    Databricks serverless Python wheel tasks pass ``named_parameters`` as
    ``--KEY=VALUE`` command-line arguments. Classic clusters use ``spark_env_vars``
    instead. This bridges the two — the rest of the code reads ``os.environ``
    regardless of compute type.
    """
    import sys
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            key, _, value = arg[2:].partition("=")
            if key:
                os.environ.setdefault(key, value)


def job_main():
    """The deployed Databricks wheel-task entry (pyproject: fabric-audit-job)."""
    _merge_named_params_into_env()
    try:
        envelope = run_unified_job()
    except Exception as exc:
        _alert_failure(exc, os.environ)
        raise
    print(envelope["summary"])
    return envelope


# ---- Tier 2: cheap deterministic check (Phase 9 Task 9.2) ----

def _tier2_heartbeat_store(env):
    """Simple heartbeat store: writes a timestamp to a JSON file on each Tier 2 run.
    The full sweep checks this file for staleness (Task 9.4)."""
    path = env.get("FABRIC_TIER2_HEARTBEAT_PATH")
    if not path:
        return None

    def write(timestamp):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            import json as _json
            _json.dump({"lastRun": timestamp, "tier": "tier2"}, fh)
        os.replace(tmp, path)

    def read():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return None

    return {"write": write, "read": read}


def _check_tier2_heartbeat(env):
    """Task 9.4: Check whether the Tier 2 check has run recently."""
    store = _tier2_heartbeat_store(env)
    if store is None:
        return {"checked": False, "reason": "no heartbeat path configured"}
    state = store["read"]()
    if state is None:
        return {"checked": True, "stale": True, "reason": "no heartbeat file found — Tier 2 may never have run"}
    last_run = state.get("lastRun")
    if not last_run:
        return {"checked": True, "stale": True, "reason": "heartbeat file has no lastRun"}
    try:
        from datetime import datetime, timezone
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        stale = age_minutes > 20  # 20 min = 4 missed 5-min checks
        result = {"checked": True, "stale": stale, "lastRun": last_run,
                  "ageMinutes": round(age_minutes, 1)}
        if stale:
            result["reason"] = (f"Tier 2 heartbeat is {round(age_minutes, 0)} minutes old "
                                "(threshold: 20 min). The Tier 2 check may have silently stopped.")
        return result
    except Exception as exc:
        return {"checked": True, "stale": True, "reason": f"heartbeat parse error: {exc}"}


def _build_tier2_collector(env, window="5m"):
    """Live-stream-only collector for Tier 2. Never reads CSV (static, doesn't update between
    5-min checks). Returns a no-op collector if no live sources are configured."""
    collectors = []
    if (env.get("FABRIC_CAPACITY_EVENTS_CLUSTER") and env.get("FABRIC_CAPACITY_EVENTS_DB")
            and env.get("FABRIC_CLIENT_ID")):
        from .adapters.clients import build_kusto_query
        from .adapters.collector_capacity_events import create_capacity_events_collector
        ce_query = build_kusto_query(
            env["FABRIC_CAPACITY_EVENTS_CLUSTER"], env["FABRIC_CAPACITY_EVENTS_DB"],
            _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"],
            _require(env, "FABRIC_CLIENT_SECRET"),
        )
        ce_cfg = {"window": window}
        if env.get("FABRIC_CAPACITY_EVENTS_TABLE"):
            ce_cfg["table"] = env["FABRIC_CAPACITY_EVENTS_TABLE"]
        if env.get("FABRIC_CAPACITY_EVENTS_KQL"):
            ce_cfg["kql"] = env["FABRIC_CAPACITY_EVENTS_KQL"]
        collectors.append(create_capacity_events_collector(ce_query, ce_cfg))

    if not collectors:
        return {"collect": lambda: {"capacity": None, "items": [], "models": []}}
    if len(collectors) == 1:
        return collectors[0]
    from .adapters.collector_merge import create_merged_collector
    return create_merged_collector(collectors)


def run_tier2_job(env=None, collector=None, delivery_sinks=None, findings_store=None,
                  heartbeat_store=None, config=None, tenant=None, scope=None):
    """Tier 2 entry point: cheap deterministic check, no LLM calls.

    Builds real adapters from environment when ports are not injected. Same DI pattern as
    ``run_job`` / ``run_unified_job`` — pass any port to override for tests.
    ``delivery_sinks`` is reserved for Phase 10; pass None for now.
    """
    env = env if env is not None else os.environ
    if collector is None:
        collector = _build_tier2_collector(env, window="5m")
    if delivery_sinks is None:
        # Phase 10: Entra bot identity will provide delivery sinks here.
        delivery_sinks = {}
    if heartbeat_store is None:
        heartbeat_store = _tier2_heartbeat_store(env)

    from .automation.tier2_check import run_tier2_check
    return run_tier2_check(
        collector,
        delivery_sinks=delivery_sinks,
        findings_store=findings_store,
        heartbeat_store=heartbeat_store,
        config=config,
        tenant=tenant,
        scope=scope,
    )


def tier2_main():
    """The deployed Databricks wheel-task entry for Tier 2 (pyproject: fabric-audit-tier2)."""
    _merge_named_params_into_env()
    env = os.environ
    try:
        result = run_tier2_job(env=env)
    except Exception as exc:
        _alert_failure(exc, env)
        raise
    triggered = result.get("triggered", False)
    n = len(result.get("triggers", []))
    print(f"[tier2] triggered={triggered} triggers={n} delivered={result.get('delivered', {})}")
    return result


if __name__ == "__main__":
    main()
