"""Databricks Job entrypoint — the production read-only sweep (Python-wheel task).

Builds REAL adapters from environment / Databricks secret-scope config and runs one audit:
  collector -> REST over Fabric/Power BI Admin APIs (Entra SP, client-credentials)
  reasoner  -> Claude (Anthropic SDK / Databricks-hosted), KB fallback on error
  delivery  -> Teams push (incoming webhook)
  store     -> run history (local JSON here; swap to Delta / Unity Catalog at deploy)

Every port is injectable, so the wiring is unit-testable without the real SDKs (tests pass
fakes). Read-only posture is absolute: the agent only reads telemetry and posts findings.

Identity note: only an Entra app registration / service principal in an allowed security
group can call the Power BI / Fabric Admin APIs — a bare Managed Identity cannot.
"""
import json
import os

from .pipeline import run_audit
from .config import DEFAULT_CONFIG, merge_config
from .reasoner_stub import create_stub_reasoner

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
        # Databricks-hosted Claude (in-tenant; no external key). Confirm the endpoint name under Serving.
        from .adapters.clients import build_databricks_claude_client
        endpoint = endpoint or "databricks-claude-3-7-sonnet"
        return create_claude_reasoner(build_databricks_claude_client(endpoint), model=endpoint, config=config)
    from .adapters.clients import build_anthropic_client
    return create_claude_reasoner(build_anthropic_client(api_key=env.get("ANTHROPIC_API_KEY")), config=config)


def _default_delivery(env):
    from .adapters.clients import PlainJsonHttp
    from .adapters.delivery_teams import create_teams_delivery
    return create_teams_delivery(PlainJsonHttp(), _require(env, "TEAMS_WEBHOOK_URL"))


def _default_store(env):
    from .adapters.store_local import create_local_store
    return create_local_store(env.get("AUDIT_HISTORY_PATH", "/tmp/fabric-audit/history.json"))


def run_job(collector=None, reasoner=None, delivery=None, store=None,
            config=None, agent_id="fabric-audit-agent", tenant=None, now=None, env=None):
    """Run one production sweep. Pass any port to override; unset ports are built from ``env``."""
    env = env if env is not None else os.environ
    if config is None:
        raw = env.get("FABRIC_AUDIT_CONFIG")
        config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG
    collector = collector if collector is not None else _default_collector(env)
    reasoner = reasoner if reasoner is not None else _default_reasoner(env, config)
    delivery = delivery if delivery is not None else _default_delivery(env)
    store = store if store is not None else _default_store(env)
    return run_audit(collector, reasoner, delivery, store=store, config=config,
                     agent_id=agent_id, tenant=tenant, now=now)


# ---- no-permission CSV sweep (host on Databricks today; live sources plug in later) ----

def _csv_paths_from_env(env):
    raw = (env.get("FABRIC_CSV_PATHS") or "").replace(";", ",")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _csv_delivery(env):
    """Teams push if a webhook is set, else a no-op (outputs are still written to out_dir)."""
    if env.get("TEAMS_WEBHOOK_URL"):
        from .adapters.clients import PlainJsonHttp
        from .adapters.delivery_teams import create_teams_delivery
        return create_teams_delivery(PlainJsonHttp(), env["TEAMS_WEBHOOK_URL"])
    return {"deliver": lambda envelope: None}


def _write_outputs(out_dir, envelope):
    from .report_md import build_markdown_report
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(build_markdown_report(envelope))
    return out_dir


def run_csv_job(csv_paths=None, out_dir=None, env=None, reasoner=None, delivery=None,
                store=None, config=None, agent_id="fabric-audit-agent", tenant=None, now=None):
    """No-permission sweep: audit a Capacity Metrics CSV/.vpax export end to end on Databricks.

    Builds the CSV collector + reasoner (Claude if ANTHROPIC_API_KEY, else the offline stub), runs
    the full read-only pipeline, writes ``latest.json`` + ``report.md`` to ``out_dir`` (a Volume),
    and posts a Teams card if ``TEAMS_WEBHOOK_URL`` is set. Every port is injectable for tests.
    Needs no service principal / tenant permissions — only the exported CSV(s).
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
        delivery = _csv_delivery(env)
    if store is None:
        store = _default_store(env)

    from .adapters.collector_csv import create_csv_collector
    envelope = run_audit(create_csv_collector(paths), reasoner, delivery, store=store,
                         config=config, agent_id=agent_id, tenant=tenant, now=now)
    _write_outputs(out_dir, envelope)
    return envelope


def main():
    envelope = run_job()
    print(envelope["summary"])
    return envelope


def csv_main():
    envelope = run_csv_job()
    print(envelope["summary"])
    return envelope


# ---- unified production sweep (CSV now; live sources auto-included once configured) ----

def build_collector_from_env(env):
    """Compose the collector from whatever is configured — the SAME deployment grows as access lands.

    CSV (no permissions) is included whenever ``FABRIC_CSV_PATHS`` is set; the live sources switch on
    automatically once their env + SP secrets exist. CSV is listed first so its authoritative CU
    share wins on merge (see ``collector_merge``).
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
        wm_cfg = {"window": env.get("FABRIC_KUSTO_WINDOW", "1d")}
        if env.get("FABRIC_KUSTO_KQL"):
            wm_cfg["kql"] = env["FABRIC_KUSTO_KQL"]
        collectors.append(create_workspace_monitoring_collector(query, wm_cfg))

    # Capacity sku/quota over Azure ARM List Usages — when permissions land.
    if env.get("FABRIC_USAGES_URL") or env.get("FABRIC_CAPACITIES_URL"):
        from .adapters.clients import EntraHttp, build_entra_token_provider, ARM_SCOPE
        from .adapters.collector_list_usages import create_list_usages_collector
        tenant = _require(env, "FABRIC_TENANT_ID")
        client = _require(env, "FABRIC_CLIENT_ID")
        secret = _require(env, "FABRIC_CLIENT_SECRET")
        # Fabric REST (api.fabric.microsoft.com) uses the Power BI token audience; only the Azure ARM
        # "List Usages" endpoint (management.azure.com) needs the ARM scope — one token per audience.
        capacities_http = EntraHttp(build_entra_token_provider(tenant, client, secret))   # Power BI scope (default)
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


def run_unified_job(env=None, out_dir=None, reasoner=None, delivery=None, store=None,
                    config=None, agent_id="fabric-audit-agent", tenant=None, now=None):
    """Production sweep: audit whatever sources are configured, end to end.

    Composes the collector via ``build_collector_from_env`` (CSV now; live sources auto-included as
    permissions land), runs the read-only pipeline, writes ``latest.json`` + ``report.md`` to
    ``out_dir`` (a Volume), and posts a Teams card if ``TEAMS_WEBHOOK_URL`` is set. Ports are
    injectable for tests. The deployed job is unchanged as access grows.
    """
    env = env if env is not None else os.environ
    out_dir = out_dir if out_dir is not None else env.get("FABRIC_OUT_DIR", "/tmp/fabric-audit")
    if config is None:
        raw = env.get("FABRIC_AUDIT_CONFIG")
        config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG
    collector = build_collector_from_env(env)
    if reasoner is None:
        reasoner = _default_reasoner(env, config) if _wants_llm(env) else create_stub_reasoner(config)
    if delivery is None:
        delivery = _csv_delivery(env)
    if store is None:
        store = _default_store(env)
    envelope = run_audit(collector, reasoner, delivery, store=store, config=config,
                         agent_id=agent_id, tenant=tenant, now=now)
    _write_outputs(out_dir, envelope)
    return envelope


def job_main():
    envelope = run_unified_job()
    print(envelope["summary"])
    return envelope


if __name__ == "__main__":
    main()
