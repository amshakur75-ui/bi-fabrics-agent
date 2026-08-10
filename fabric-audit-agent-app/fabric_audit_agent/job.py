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


def _check_startup_invariant(health=None, *, catalog=None, known_names=None):
    """Sub-plan 4 Part 4: the MODEL_MAP invariant (``resolve/catalog.py::assert_model_map_invariant``)
    used to be tests-only — call it once at job startup so a drifted catalog (a catalog model with no
    matching routing entry) is caught immediately instead of surfacing later as a silently wrong/
    missing model resolution. Never crashes the run: a mismatch degrades to a recorded health issue
    (and a WARN print), not a failed job. ``catalog``/``known_names`` are injection points for tests
    only — production always uses the real default catalog + the real routing table.
    """
    try:
        from .resolve.catalog import assert_model_map_invariant
        from .resolve.routing_table import ROUTING_TABLE
        names = known_names if known_names is not None else {e["canonicalName"] for e in ROUTING_TABLE}
        assert_model_map_invariant(names, catalog=catalog)
    except Exception as exc:
        msg = f"startup MODEL_MAP invariant failed: {type(exc).__name__}: {exc}"
        print(f"[startup] WARN: {msg}")
        if health is not None:
            health.record_issue(msg)


def _run_startup_preflight(env, health=None):
    """Sub-plan 5 Part 5d: run the read-only config/health preflight snapshot
    (``automation.preflight.run_preflight``) once at job startup, log it, and -- when a
    ``HealthReport`` is available -- fold any failed check into it as a recorded issue (reusing
    the existing health surface rather than a parallel one). Never raises: any failure inside
    the preflight itself is already caught by ``run_preflight``; this wrapper adds no new
    exception path of its own."""
    try:
        from .automation.preflight import run_preflight
        report = run_preflight(env)
    except Exception as exc:
        msg = f"startup preflight failed to run: {type(exc).__name__}: {exc}"
        print(f"[startup] WARN: {msg}")
        if health is not None:
            health.record_issue(msg)
        return {"ok": False, "checks": [], "degraded": True}

    if report["ok"]:
        print("[startup] preflight: ok (" + ", ".join(c["name"] for c in report["checks"]) + ")")
    else:
        failed = [c for c in report["checks"] if not c["ok"]]
        detail = "; ".join(f"{c['name']}: {c['detail']}" for c in failed)
        print(f"[startup] WARN: preflight degraded: {detail}")
        if health is not None:
            health.record_issue(f"startup preflight degraded: {detail}")
    return report


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

# Bounded cap for the raw events attached onto facts["events"] (TASK 1-WIRE) — keeps the costliest
# events (order="cost", same default as create_event_collector / tools.py's _EVENT_CAP) so a merged
# sweep's facts dict never bloats. Matches the row cap the live event-depth MCP tools already use.
_EVENTS_CAP = 5000


def _build_events_collector(env, window=None):
    """CollectorPort wrapping ``create_event_collector`` — the SAME builder the event-depth MCP
    tools (``tools.py``) and the 5-min watcher (``watch_run.py``) already use against Log Analytics
    ``PowerBIDatasetsWorkspace`` — into ``{"events": [...]}`` so ``collector_merge`` folds it onto
    the top-level ``facts["events"]`` key (TASK 1-WIRE: wires the dormant
    ``detectors/absolute_cost.py`` + ``detectors/query_shape.py`` into the sweep). No new outward
    call type; read-only.

    Capped at ``_EVENTS_CAP`` events, costliest-first (``order="cost"``), excluding VertiPaq
    storage-engine sub-query children (``VertiPaqSE*``) that double-count a parent QueryEnd — same
    exclusion ``watch_run.py``'s live pull uses.

    FAIL-OPEN at collect time: an auth/query/schema error is logged and yields ``{"events": []}``
    rather than raising, so a Log Analytics outage never breaks the sweep — the activity detectors
    already no-op on empty/missing ``facts["events"]``.
    """
    tenant = _require(env, "FABRIC_TENANT_ID")
    client = _require(env, "FABRIC_CLIENT_ID")
    secret = _require(env, "FABRIC_CLIENT_SECRET")
    lookback = window if window is not None else env.get("FABRIC_LA_WINDOW", "1d")

    def collect():
        try:
            from .adapters.clients import build_log_analytics_query
            from .adapters.collector_events_la import create_event_collector
            la_query = build_log_analytics_query(env["FABRIC_LA_WORKSPACE_ID"], tenant, client, secret)
            cfg = {"window": f"| where TimeGenerated > ago({lookback})", "cap": _EVENTS_CAP,
                   "order": "cost", "excludePrefixes": ["VertiPaqSE"]}
            return {"events": create_event_collector(la_query, cfg)["collect"]()}
        except Exception as exc:
            print(f"[sweep] events pull failed ({type(exc).__name__}: {exc})")
            return {"events": []}

    return {"collect": collect}


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

        # Raw per-operation events (TASK 1-WIRE) — feeds facts["events"] for detectors/absolute_cost.py
        # + detectors/query_shape.py. Gated on the SAME env as the summarized LA collector just above
        # (it is the same live source, just a different query shape); construction-time errors (e.g. a
        # missing secret resolved after the `if` check above) are caught here so one bad source never
        # blocks the rest of build_collector_from_env — collection-time errors fail open inside
        # _build_events_collector itself.
        try:
            collectors.append(_build_events_collector(env, window))
        except Exception as exc:
            print(f"[sweep] events collector skipped ({type(exc).__name__}: {exc})")

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

    # Refresh-history (B3) — feeds detectors/refresh.py (failing / retry-storm / slow-phase /
    # chronic). OFF unless FABRIC_REFRESH_DATASETS_JSON is set (a JSON list of
    # {group,dataset,workspace,datasetName}); the per-dataset endpoint needs SP workspace
    # membership, so estate-wide enumeration is a documented follow-up, not silently-empty here.
    if env.get("FABRIC_REFRESH_DATASETS_JSON") and env.get("FABRIC_CLIENT_ID"):
        try:
            from .adapters.clients import EntraHttp, build_entra_token_provider
            from .adapters.collector_refresh import create_refresh_collector
            datasets = json.loads(env["FABRIC_REFRESH_DATASETS_JSON"])
            http = EntraHttp(build_entra_token_provider(
                _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"],
                _require(env, "FABRIC_CLIENT_SECRET")))
            collectors.append(create_refresh_collector(http, {
                "datasets": datasets, "top": int(env.get("FABRIC_REFRESH_TOP", "20"))}))
        except Exception as exc:
            print(f"[sweep] refresh collector skipped ({type(exc).__name__}: {exc})")

    # Model metadata via the Scanner API (FIX C) — feeds detectors/model.py (bidirectional
    # relationships) which powers coaching. OFF unless FABRIC_SCANNER_WORKSPACE_IDS is set (a
    # comma-separated workspace-id list); estate-wide enumeration + the async scan cost make a
    # bounded, deliberate rollout the right call rather than scanning all 96 workspaces every sweep.
    if env.get("FABRIC_SCANNER_WORKSPACE_IDS") and env.get("FABRIC_CLIENT_ID"):
        try:
            from .adapters.clients import EntraHttp, build_entra_token_provider
            from .adapters.collector_scanner import create_scanner_models_collector
            ws_ids = [w.strip() for w in env["FABRIC_SCANNER_WORKSPACE_IDS"].split(",") if w.strip()]
            http = EntraHttp(build_entra_token_provider(
                _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"],
                _require(env, "FABRIC_CLIENT_SECRET")))
            collectors.append(create_scanner_models_collector(http, {"workspaceIds": ws_ids}))
        except Exception as exc:
            print(f"[sweep] scanner models collector skipped ({type(exc).__name__}: {exc})")

    # Security / access via Activity Events (FIX C) — feeds detectors/security.py (external shares,
    # admin grants). OFF unless FABRIC_SECURITY_ENABLED is truthy. Window = today (UTC) so far
    # (activity events must be a same-UTC-day, <=24h range).
    if str(env.get("FABRIC_SECURITY_ENABLED", "")).strip().lower() in ("1", "true", "yes") \
            and env.get("FABRIC_CLIENT_ID"):
        try:
            from datetime import datetime, timezone
            from .adapters.clients import EntraHttp, build_entra_token_provider
            from .adapters.collector_security import create_security_collector
            now = datetime.now(timezone.utc)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            http = EntraHttp(build_entra_token_provider(
                _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"],
                _require(env, "FABRIC_CLIENT_SECRET")))
            collectors.append(create_security_collector(http, {
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "orgDomains": [d.strip() for d in (env.get("FABRIC_ORG_DOMAINS") or "").split(",") if d.strip()],
                "sensitiveWorkspaces": [w.strip() for w in (env.get("FABRIC_SENSITIVE_WORKSPACES") or "").split(",") if w.strip()],
            }))
        except Exception as exc:
            print(f"[sweep] security collector skipped ({type(exc).__name__}: {exc})")

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
    _deliver_sweep_findings(envelope, env)
    _maybe_alert(envelope, prev_history, env)
    _check_tier2_health(env)
    return envelope


def _deliver_sweep_findings(envelope, env):
    """Step 6a: deliver the sweep's NEW material findings to Teams — only the finding families Tier-2
    does NOT own, deduped via the shared ``audit_alerts`` store so nothing is alerted twice. Gated on
    ``SWEEP_WEBHOOK_ENABLED`` + ``POWER_AUTOMATE_ALERT_URL``. Failure-isolated: never fails the sweep."""
    enabled = str(env.get("SWEEP_WEBHOOK_ENABLED", "")).strip().lower() in ("1", "true", "yes")
    if not (enabled and env.get("POWER_AUTOMATE_ALERT_URL")):
        return None
    try:
        from .adapters.delivery_webhook import create_webhook_sink
        from .adapters.chat_store_lakebase import create_alert_chat, create_ticket_writer
        from .context_alerts import create_alerts_store_delta
        from .automation.sweep_delivery import deliver_new_findings
        catalog, schema = env.get("FABRIC_DELTA_CATALOG"), env.get("FABRIC_DELTA_SCHEMA")
        if not (catalog and schema):
            return None
        data = envelope.get("data") or {}
        findings = data.get("findings") or []
        # ticket_writer -> estate-wide sweep findings also appear in the app notification center
        # (not just Teams). Fail-open: without it, delivery still works, just no sidebar ticket.
        try:
            _ticket_writer = create_ticket_writer()
        except Exception as exc:
            print(f"[sweep] ticket writer unavailable ({type(exc).__name__}: {exc})")
            _ticket_writer = None
        result = deliver_new_findings(
            findings,
            alerts_store=create_alerts_store_delta(catalog, schema),
            delivery_sinks={"webhook": create_webhook_sink(env["POWER_AUTOMATE_ALERT_URL"])},
            app_url=env.get("APP_URL", ""),
            chat_writer=create_alert_chat,
            ticket_writer=_ticket_writer,
            min_level=env.get("SWEEP_MIN_LEVEL", "Warning"),
        )
        print(f"[sweep] delivered {len(result['delivered'])} new finding(s); skipped "
              f"dup={result['skipped_dup']} tier2={result['skipped_tier2']} minor={result['skipped_minor']}")
        return result
    except Exception as exc:  # delivery must never crash the sweep
        print(f"[sweep] delivery failed: {type(exc).__name__}: {exc}")
        return None


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
    _resolve_secret_refs()


def _resolve_secret_refs():
    """Resolve ``{{secrets/scope/key}}`` env values via the SDK.

    Python-wheel-task ``named_parameters`` do NOT auto-resolve ``{{secrets/...}}`` references
    (unlike spark_env_vars) — they arrive as the literal string. Resolve them here so the rest of
    the code sees real values. Failure-isolated: a bad ref is logged and left as-is."""
    import re
    pat = re.compile(r"^\{\{secrets/([^/]+)/([^}]+)\}\}$")
    refs = {k: m for k, m in ((k, pat.match(str(v))) for k, v in os.environ.items()) if m}
    if not refs:
        return
    try:
        import base64
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
    except Exception as exc:
        print(f"[secrets] SDK unavailable, cannot resolve {list(refs)}: {exc}")
        return
    for key, m in refs.items():
        try:
            resp = w.secrets.get_secret(scope=m.group(1), key=m.group(2))
            raw = getattr(resp, "value", None)
            os.environ[key] = base64.b64decode(raw).decode("utf-8") if raw else ""
        except Exception as exc:
            print(f"[secrets] failed to resolve {key}: {type(exc).__name__}: {exc}")


def job_main():
    """The deployed Databricks wheel-task entry (pyproject: fabric-audit-job)."""
    _merge_named_params_into_env()
    from .automation.health import HealthReport, render_health_line
    health = HealthReport()
    _check_startup_invariant(health)
    _run_startup_preflight(os.environ, health)
    try:
        envelope = run_unified_job()
    except Exception as exc:
        _alert_failure(exc, os.environ)
        raise
    print(envelope["summary"])
    line = render_health_line(health)
    if line:
        print(f"[job] {line}")
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

    # Per-user / per-item attribution from Log Analytics (Step 2): this is the source that carries
    # the WHO — without it the collector is capacity-only and items=0, so concentration / per-user
    # surge / same-item cross-user gates never fire. SP client-credentials, same as the sweep path.
    if env.get("FABRIC_LA_WORKSPACE_ID") and env.get("FABRIC_CLIENT_ID"):
        from .adapters.clients import build_log_analytics_query
        from .adapters.collector_log_analytics import create_log_analytics_collector
        la_query = build_log_analytics_query(
            env["FABRIC_LA_WORKSPACE_ID"],
            _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"],
            _require(env, "FABRIC_CLIENT_SECRET"),
        )
        # topUsers cap raised (default 3) so the same-item cross-user gate can see >=N users/item.
        la_cfg = {"window": window, "topUsers": int(env.get("FABRIC_LA_TOP_USERS", "8"))}
        if env.get("FABRIC_LA_WORKSPACE_FILTER"):
            la_cfg["workspaceFilter"] = env["FABRIC_LA_WORKSPACE_FILTER"]
        if env.get("FABRIC_LA_KQL"):
            la_cfg["kql"] = env["FABRIC_LA_KQL"]
        if env.get("FABRIC_LA_WORKSPACE_LABEL"):
            la_cfg["workspace"] = env["FABRIC_LA_WORKSPACE_LABEL"]
        collectors.append(create_log_analytics_collector(la_query, la_cfg))

        # RAW per-event stream (Design A' B2/B3). The attribution collector above SUMMARIZES per
        # (workspace, item, user) and emits `items`/`users` — it never emits `events`. Without
        # this, `facts["events"]` does not exist in the Tier-2 sweep, so
        # `detect_user_baseline_deviation_precomputed` iterates an empty list and the correlation
        # booster no-ops: B2 and B3 were DEAD CODE in production (found 2026-08-10; every test
        # injected the key by hand, so nothing caught it).
        #
        # `window=window` is passed EXPLICITLY and must stay that way: _build_events_collector
        # defaults to FABRIC_LA_WINDOW ("1d"), which on a 5-minute sweep would re-pull a full day
        # of events every run and re-flag the same spikes ~288 times a day.
        # Fail-open at collect time, so an LA outage degrades the correlation booster rather than
        # breaking the capacity gates.
        try:
            collectors.append(_build_events_collector(env, window=window))
        except Exception as exc:
            print(f"[tier2] events collector skipped ({type(exc).__name__}: {exc})")

    if not collectors:
        return {"collect": lambda: {"capacity": None, "items": [], "models": []}}
    if len(collectors) == 1:
        return collectors[0]
    from .adapters.collector_merge import create_merged_collector
    return create_merged_collector(collectors)


def _build_tier2_reasoner(env, config):
    """Build the investigation callable ``reasoner(trigger) -> {markdown, summary, report}``.

    v1 produces a structured, deterministic facts-based investigation (reliable, no LLM). The
    ``report`` verdict is only consulted for the ambiguous middle (v1 = surface it); the LLM-written
    narrative + ambiguous verdict are a scoped follow-up (see the sub-project #2 spec)."""
    from .automation.tier2_check import _facts_for, _title_for

    def reasoner(trigger):
        lines = [f"## {_title_for(trigger)}", ""]
        for name, value in _facts_for(trigger):
            lines.append(f"- **{name}:** {value}")
        hint = trigger.get("normalityHint")
        if hint:
            lines += ["", f"_{hint}_"]
        rec = trigger.get("recurrence") or {}
        if rec.get("note"):
            lines += ["", rec["note"]]
        return {"markdown": "\n".join(lines), "summary": _title_for(trigger), "report": True}

    return reasoner


def run_tier2_job(env=None, collector=None, delivery_sinks=None, findings_store=None,
                  heartbeat_store=None, config=None, tenant=None, scope=None, health=None):
    """Tier 2 entry point: cheap deterministic check; LLM-free unless a gate fires and alerting is on.

    Builds real adapters from environment when ports are not injected. Same DI pattern as
    ``run_job`` / ``run_unified_job`` — pass any port to override for tests. When
    ``TIER2_WEBHOOK_ENABLED`` + ``POWER_AUTOMATE_ALERT_URL`` are set, wires the Tier-2 -> Teams alert
    path (sub-project #2): materiality gate -> investigation -> Adaptive Card + pre-created chat.

    ``health``: optional ``automation.health.HealthReport``; a fresh one is created when omitted so
    ``run_tier2_check`` always has somewhere to record collector/delivery outcomes. The result dict
    carries it back as ``"health"`` (``.to_dict()``) for callers that want to log/query it.
    """
    env = env if env is not None else os.environ
    if health is None:
        from .automation.health import HealthReport
        health = HealthReport()
    if collector is None:
        collector = _build_tier2_collector(env, window="5m")
    if heartbeat_store is None:
        heartbeat_store = _tier2_heartbeat_store(env)
    if findings_store is None:
        findings_store = _default_findings_store(env)  # for recurrence cross-reference

    # Rolling-readings store (Step 2) powering the stateful gates (sustained/rate-of-change/
    # silent-failure). Needs the Delta catalog/schema; degrades to None (gates just don't fire).
    readings_store = None
    _rcat, _rsch = env.get("FABRIC_DELTA_CATALOG"), env.get("FABRIC_DELTA_SCHEMA")
    if _rcat and _rsch:
        try:
            from .context_readings import create_readings_store_delta
            readings_store = create_readings_store_delta(_rcat, _rsch)
        except Exception as exc:
            print(f"[tier2] readings store init skipped ({type(exc).__name__}: {exc})")

    # B1/B2 user_baseline store (Design A' Phase B). OFF by default until the nightly
    # bootstrap has populated the table at least once — a store that's wired but empty is
    # safe (the detector's 3-layer fallback lands on the silent layer), but keeping the
    # flip explicit avoids surprising empty rows on the first sweep. Flip on with
    # ``TIER2_BASELINE_ENABLED=1`` after ``fabric-audit-baseline`` has run.
    baseline_store = None
    _baseline_on = str(env.get("TIER2_BASELINE_ENABLED", "")).strip().lower() in (
        "1", "true", "yes")
    if _baseline_on and _rcat and _rsch:
        try:
            from .context_user_baseline import create_user_baseline_store_delta
            baseline_store = create_user_baseline_store_delta(_rcat, _rsch)
        except Exception as exc:
            print(f"[tier2] baseline store init skipped ({type(exc).__name__}: {exc})")
            health.record_issue(f"baseline store init skipped: "
                                f"{type(exc).__name__}: {exc}")

    # B4 capacity_reporting store. Passive archival (append-only) — safe to leave on by
    # default, but gated on the same catalog/schema being configured. Turn OFF for a run
    # with ``TIER2_REPORTING_ENABLED=0`` if a specific investigation needs to skip archives.
    reporting_store = None
    _reporting_on = str(env.get("TIER2_REPORTING_ENABLED", "1")).strip().lower() in (
        "1", "true", "yes")
    if _reporting_on and _rcat and _rsch:
        try:
            from .context_capacity_reporting import create_capacity_reporting_store_delta
            reporting_store = create_capacity_reporting_store_delta(_rcat, _rsch)
        except Exception as exc:
            print(f"[tier2] reporting store init skipped ({type(exc).__name__}: {exc})")
            health.record_issue(f"reporting store init skipped: "
                                f"{type(exc).__name__}: {exc}")

    alerts_store = reasoner = chat_writer = ack_store = ticket_writer = None
    app_url = env.get("APP_URL", "")
    enabled = str(env.get("TIER2_WEBHOOK_ENABLED", "")).strip().lower() in ("1", "true", "yes")
    if delivery_sinks is None and enabled and env.get("POWER_AUTOMATE_ALERT_URL"):
        from .adapters.delivery_webhook import create_webhook_sink
        from .adapters.chat_store_lakebase import create_alert_chat, create_ack_store, create_ticket_writer
        from .context_alerts import create_alerts_store_delta
        catalog, schema = env.get("FABRIC_DELTA_CATALOG"), env.get("FABRIC_DELTA_SCHEMA")
        if catalog and schema:
            alerts_store = create_alerts_store_delta(catalog, schema)
        reasoner = _build_tier2_reasoner(env, config)
        chat_writer = create_alert_chat
        delivery_sinks = {"webhook": create_webhook_sink(env["POWER_AUTOMATE_ALERT_URL"])}
        # 6c: ack/snooze snapshot from Lakebase — fail-open (a read error must never drop a real alert).
        try:
            ack_store = create_ack_store()
        except Exception as exc:
            print(f"[tier2] ack store unavailable ({type(exc).__name__}: {exc}); reminders unsuppressed")
        # Step 9: ticket-detail writer (Lakebase alert_ticket) — the app reads it for the sidebar.
        # Fail-open: metadata is best-effort, never a reason to drop an alert.
        try:
            ticket_writer = create_ticket_writer()
        except Exception as exc:
            print(f"[tier2] ticket writer unavailable ({type(exc).__name__}: {exc}); sidebar detail off")
    if delivery_sinks is None:
        delivery_sinks = {}

    from .automation.tier2_check import run_tier2_check
    result = run_tier2_check(
        collector,
        delivery_sinks=delivery_sinks,
        findings_store=findings_store,
        heartbeat_store=heartbeat_store,
        readings_store=readings_store,
        config=config,
        tenant=tenant,
        scope=scope,
        alerts_store=alerts_store,
        reasoner=reasoner,
        chat_writer=chat_writer,
        app_url=app_url,
        ack_store=ack_store,
        ticket_writer=ticket_writer,
        health=health,
        baseline_store=baseline_store,
        reporting_store=reporting_store,
    )
    result["health"] = health.to_dict()
    return result


def tier2_main():
    """The deployed Databricks wheel-task entry for Tier 2 (pyproject: fabric-audit-tier2)."""
    _merge_named_params_into_env()
    from .automation.health import HealthReport, render_health_line
    health = HealthReport()
    _check_startup_invariant(health)
    env = os.environ
    _run_startup_preflight(env, health)
    try:
        result = run_tier2_job(env=env, health=health)
    except Exception as exc:
        _alert_failure(exc, env)
        raise
    triggered = result.get("triggered", False)
    n = len(result.get("triggers", []))
    print(f"[tier2] triggered={triggered} triggers={n} delivered={result.get('delivered', {})}")
    line = render_health_line(health)
    if line:
        print(f"[tier2] {line}")
    return result


# ---- B1 nightly baseline bootstrap (Design A' Phase B) ----

def run_baseline_bootstrap_job(env=None, *, collector=None, baseline_store=None,
                                min_history=None, as_of=None, health=None):
    """Nightly job entry: pull the last N days of activity events and rebuild the
    ``user_baseline`` Delta table (per-user p95 + one estate-wide row).

    Reads env:
      - FABRIC_BASELINE_WINDOW (default ``14d``) — LA lookback window.
      - FABRIC_BASELINE_MIN_HISTORY (default 20) — per-user sample floor.
      - FABRIC_DELTA_CATALOG / FABRIC_DELTA_SCHEMA — target of the Delta upsert.

    All ports DI'd for tests. Fail-open: an events-collector outage yields a summary
    with rowsWritten=0 rather than crashing the job — the OLD baseline stays live until
    the next successful run.
    """
    env = env if env is not None else os.environ
    if health is None:
        from .automation.health import HealthReport
        health = HealthReport()
    lookback = env.get("FABRIC_BASELINE_WINDOW", "14d")
    if min_history is None:
        try:
            min_history = int(env.get("FABRIC_BASELINE_MIN_HISTORY", "20"))
        except (TypeError, ValueError):
            min_history = 20
    if as_of is None:
        from datetime import datetime, timezone
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    aggregate = False
    if collector is None:
        # Server-side percentile aggregation (adapters/collector_baseline_la). This REPLACES the
        # old reuse of _build_events_collector, which capped at the 5,000 costliest events and
        # therefore computed a "p95" that was really the ~99.9th percentile of the true
        # distribution (measured: estate p95 = 1052 CPU-s). Aggregating in KQL has no row cap and
        # returns a few hundred summary rows instead of a 700k-row pull.
        # Guarded against missing env so a job scheduled before secrets exist degrades to an
        # empty-write summary rather than an unhandled exception.
        try:
            from .adapters.clients import build_log_analytics_query
            from .adapters.collector_baseline_la import create_baseline_collector
            la_query = build_log_analytics_query(
                env["FABRIC_LA_WORKSPACE_ID"], _require(env, "FABRIC_TENANT_ID"),
                _require(env, "FABRIC_CLIENT_ID"), _require(env, "FABRIC_CLIENT_SECRET"),
            )
            collector = create_baseline_collector(la_query, {
                "window": f"| where TimeGenerated > ago({lookback})",
                "minHistory": min_history,
                "asOf": as_of,
            })
            aggregate = True
        except Exception as exc:
            print(f"[baseline] aggregate collector init failed "
                  f"({type(exc).__name__}: {exc})")
            health.record_issue(f"baseline aggregate collector init failed: "
                                f"{type(exc).__name__}: {exc}")
            return {"rowsWritten": 0, "users": 0, "hasEstate": False, "asOf": as_of,
                    "error": f"{type(exc).__name__}: {exc}",
                    "health": health.to_dict()}
    if baseline_store is None:
        cat, sch = env.get("FABRIC_DELTA_CATALOG"), env.get("FABRIC_DELTA_SCHEMA")
        if cat and sch:
            try:
                from .context_user_baseline import create_user_baseline_store_delta
                baseline_store = create_user_baseline_store_delta(cat, sch)
            except Exception as exc:
                print(f"[baseline] store init failed ({type(exc).__name__}: {exc})")
                health.record_issue(f"baseline store init failed: "
                                    f"{type(exc).__name__}: {exc}")
                return {"rowsWritten": 0, "users": 0, "hasEstate": False, "asOf": as_of,
                        "error": f"{type(exc).__name__}: {exc}",
                        "health": health.to_dict()}

    from .automation.user_baseline_bootstrap import run_bootstrap, run_bootstrap_aggregate
    try:
        if aggregate:
            # Collector already returns finished baseline rows (percentiles computed in KQL).
            summary = run_bootstrap_aggregate(collector, as_of=as_of,
                                              baseline_store=baseline_store)
        else:
            # Injected raw-event collector (tests / an explicit override): reduce in Python.
            summary = run_bootstrap(collector, min_history=min_history, as_of=as_of,
                                    baseline_store=baseline_store)
    except Exception as exc:
        print(f"[baseline] bootstrap failed ({type(exc).__name__}: {exc})")
        health.record_issue(f"baseline bootstrap failed: {type(exc).__name__}: {exc}")
        summary = {"rowsWritten": 0, "users": 0, "hasEstate": False, "asOf": as_of,
                   "error": f"{type(exc).__name__}: {exc}"}
    summary["health"] = health.to_dict()
    return summary


def baseline_bootstrap_main():
    """Databricks wheel-task entry for the nightly baseline bootstrap (pyproject:
    fabric-audit-baseline). Schedule this ONCE PER DAY (e.g. 02:00 UTC); the 5-min tier2
    sweep reads whatever the last successful run wrote."""
    _merge_named_params_into_env()
    from .automation.health import HealthReport, render_health_line
    health = HealthReport()
    _check_startup_invariant(health)
    env = os.environ
    _run_startup_preflight(env, health)
    try:
        result = run_baseline_bootstrap_job(env=env, health=health)
    except Exception as exc:
        _alert_failure(exc, env)
        raise
    print(f"[baseline] rowsWritten={result.get('rowsWritten')} users={result.get('users')} "
          f"hasEstate={result.get('hasEstate')} asOf={result.get('asOf')}")
    line = render_health_line(health)
    if line:
        print(f"[baseline] {line}")
    # Fail LOUDLY. Every internal error path returns a summary rather than raising (so a
    # transient outage can't crash the wheel task mid-write), but a nightly job that writes
    # nothing and still reports SUCCESS is invisible: the sweep would keep comparing against
    # a stale baseline for weeks. Surface it as a red run so the job's failure notification
    # fires and the detector's staleness guard isn't the only backstop.
    err = result.get("error")
    if err:
        raise RuntimeError(f"baseline bootstrap failed: {err}")
    if not result.get("rowsWritten"):
        raise RuntimeError(
            "baseline bootstrap wrote 0 rows — Log Analytics returned no usable activity for "
            "the window, or the Delta write was rejected. The previous baseline (if any) is "
            "untouched; investigate before the next tier2 sweep relies on it.")
    return result


# ---- Step 10: daily 6pm capacity digest (once-a-day summary card + Acknowledge) ----

def run_daily_summary_job(env=None, *, collector=None, alerts_store=None, ack_store=None,
                          chat_writer=None, delivery_sinks=None, now=None):
    """Compose + deliver the daily capacity digest. Reuses the Tier-2 alert infra (audit_alerts +
    alert_ack + pre-created alert chats), so no new tables/UI. Ports injectable for tests; when run
    from the job they are built from ``env`` and every optional wire degrades gracefully."""
    env = env if env is not None else os.environ
    app_url = env.get("APP_URL", "")
    catalog, schema = env.get("FABRIC_DELTA_CATALOG"), env.get("FABRIC_DELTA_SCHEMA")

    from .automation.health import HealthReport
    health = HealthReport()
    # Startup invariant (Sub-plan 4 Part 4): run once here rather than only in tests — a drifted
    # catalog degrades to a recorded health issue (surfaced in the digest banner below), never a
    # crashed job.
    _check_startup_invariant(health)
    _run_startup_preflight(env, health)

    # Day high-water CU% / throttle from a fresh 1d collect (degrade to {} on any error). The same
    # collect also carries facts["events"] (normalize_event-shaped, the same source the Part 12
    # taxonomy detectors read) — reused for the Top-N users ranking so the digest doesn't need a
    # second pull; falls back to a finding-count proxy inside build_daily_summary when absent.
    capacity, coverage_gaps, events = {}, [], None
    try:
        if collector is None:
            collector = _build_tier2_collector(env, window="1d")
        facts = collector["collect"]() or {}
        # Merged collectors (adapters/collector_merge.py) surface per-source failures here — feed
        # them straight into the health report so a partially-blind digest is visible, not silent.
        if facts.get("sourcesFailed"):
            health.record_collector_failures(facts["sourcesFailed"])
        cap = facts.get("capacity") or {}
        capacity = {"peakCuPct": cap.get("peakCuPct"), "throttleMinutes": cap.get("throttleMinutes")}
        items = facts.get("items") or []
        events = facts.get("events")
        from .automation.materiality import load_cfg
        blind = load_cfg().get("blind_spot_cu", 70.0)
        if cap.get("peakCuPct") is not None and cap["peakCuPct"] >= blind and not items:
            coverage_gaps.append(
                f"true CU% reached {cap['peakCuPct']:.0f}% with zero monitored activity")
    except Exception as exc:
        print(f"[daily] capacity collect skipped ({type(exc).__name__}: {exc})")
        health.record_collector("daily-1d", False, f"{type(exc).__name__}: {exc}")

    if alerts_store is None and catalog and schema:
        try:
            from .context_alerts import create_alerts_store_delta
            alerts_store = create_alerts_store_delta(catalog, schema)
        except Exception as exc:
            print(f"[daily] alerts store unavailable ({type(exc).__name__}: {exc})")
    if alerts_store is None:
        print("[daily] no alerts store (Delta not configured) — nothing to summarize")
        return {"delivered": False, "openTickets": 0, "unackedPrior": 0, "skipped": True}

    enabled = str(env.get("DAILY_SUMMARY_ENABLED", "")).strip().lower() in ("1", "true", "yes")
    if delivery_sinks is None and enabled and env.get("POWER_AUTOMATE_ALERT_URL"):
        from .adapters.delivery_webhook import create_webhook_sink
        delivery_sinks = {"webhook": create_webhook_sink(env["POWER_AUTOMATE_ALERT_URL"])}
    if chat_writer is None and enabled:
        try:
            from .adapters.chat_store_lakebase import create_alert_chat
            chat_writer = create_alert_chat
        except Exception:
            chat_writer = None
    if ack_store is None:
        try:
            from .adapters.chat_store_lakebase import create_ack_store
            ack_store = create_ack_store()
        except Exception as exc:
            print(f"[daily] ack store unavailable ({type(exc).__name__}: {exc}); prior digests uncounted")

    from .automation.daily_summary import run_daily_summary
    return run_daily_summary(alerts_store=alerts_store, ack_store=ack_store, capacity=capacity,
                             coverage_gaps=coverage_gaps, delivery_sinks=delivery_sinks,
                             chat_writer=chat_writer, app_url=app_url, now_dt=now, events=events,
                             health=health)


def daily_summary_main():
    """The deployed Databricks wheel-task entry for the daily digest (pyproject: fabric-audit-daily)."""
    _merge_named_params_into_env()
    env = os.environ
    try:
        result = run_daily_summary_job(env=env)
    except Exception as exc:
        _alert_failure(exc, env)
        raise
    print(f"[daily] delivered={result.get('delivered')} open={result.get('openTickets')} "
          f"unackedPrior={result.get('unackedPrior')}")
    return result


if __name__ == "__main__":
    main()
