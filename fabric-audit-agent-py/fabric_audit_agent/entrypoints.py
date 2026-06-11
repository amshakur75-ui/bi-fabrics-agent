"""CLI entry points (offline, mock adapters). Ports of the Node root CLIs
``audit.js`` / ``eval.js`` / ``whatif.js`` / ``triggers.js`` / ``lifecycle.js`` / ``dax.js``.

Each returns a text block (testable) and, where the Node CLI did, performs the same file
side effects (audit writes ``runs/latest.json`` + ``runs/report.md``). ``base_dir`` locates
``fixtures/`` and ``runs/`` (defaults to the repo root) so tests can redirect to a temp dir.
"""
import json
import os

from .adapters import (
    create_mock_collector, create_stub_reasoner, create_file_delivery,
    create_local_store, create_lifecycle_store, create_claude_reasoner,
)
from .pipeline import run_audit
from .config import DEFAULT_CONFIG
from .outcomes import summarize_outcomes
from .report_md import build_markdown_report
from .detectors import detect_all
from .eval import score_case, score_suite
from .whatif import assess_what_if
from .triggers import evaluate_threshold_triggers
from .lifecycle import set_state
from .dax import analyze_dax

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_ID = "fabric-audit-agent"
_LIFECYCLE_ACTIONS = ("open", "acknowledged", "snoozed", "resolved", "wontfix")


def _base(base_dir):
    return base_dir if base_dir is not None else _BASE


def _json(obj):
    """Compact JSON like Node ``JSON.stringify`` (no spaces), Unicode kept literal."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# ---- audit (port of audit.js) ----
def run_audit_cli(base_dir=None):
    base = _base(base_dir)
    collector = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
    config = DEFAULT_CONFIG
    reasoner = create_stub_reasoner(config)
    note = None
    if os.environ.get("FABRIC_AUDIT_REASONER") == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        from .adapters.clients import build_anthropic_client
        reasoner = create_claude_reasoner(build_anthropic_client(), config=config)
        note = "Reasoner: Claude"
    out_path = os.path.join(base, "runs", "latest.json")
    delivery = create_file_delivery(out_path)
    store = create_local_store(os.path.join(base, "runs", "history.json"))
    lifecycle_store = create_lifecycle_store(os.path.join(base, "runs", "lifecycle.json"))

    envelope = run_audit(collector, reasoner, delivery, store=store,
                         lifecycle_store=lifecycle_store, config=config, agent_id=AGENT_ID)
    d = envelope["data"]
    out = []
    if note:
        out.append(note)
    out.append(envelope["summary"])
    if d.get("digest"):
        dg = d["digest"]
        out.append(f'Digest — new: {dg["newCount"]}, recurring: {len(dg["recurring"])}, by domain: {_json(dg["byDomain"])}')
    v = d["verdict"]
    out.append(f'Verdict: {str(v["decision"]).upper()} — {v["reason"]}')
    if d.get("suppressed"):
        out.append(f'Suppressed (handled): {len(d["suppressed"])}')
    hs = d["healthScore"]
    out.append(f'Health: {hs["overall"]}/100  {_json(hs["byDomain"])}')
    top = "  |  ".join(f'#{r["rank"]} [{r["level"]}] {r["what"]}' for r in d["roadmap"][:3])
    if top:
        out.append(f"Top fixes: {top}")
    if d.get("correlations"):
        out.append("Correlations: " + ", ".join(c["theme"] for c in d["correlations"]))
    if d.get("forecast"):
        out.append(f'Forecast: {d["forecast"]["message"]}')
    if d.get("accountability") and d["accountability"].get("ignoredCount"):
        out.append(f'Accountability: {d["accountability"]["ignoredCount"]} finding(s) advised 3+ runs and still unresolved.')
    if d.get("outcomes"):
        s = summarize_outcomes(d["outcomes"])
        if s:
            out.append(f"Outcomes: {s}.")
    if d.get("anomalies"):
        out.append("Anomalies: " + "  |  ".join(a["message"] for a in d["anomalies"]))
    if d.get("staggerPlan"):
        out.append("Stagger plan: " + ", ".join(f'{s["dataset"]} {s["from"]}→{s["to"]}' for s in d["staggerPlan"]))
    if d.get("sla") and d["sla"].get("breachedCount"):
        out.append(f'SLA: {d["sla"]["breachedCount"]} finding(s) past their resolution target.')
    if d.get("routing"):
        r = ", ".join(f"{dest}({len(keys)})" for dest, keys in d["routing"].items())
        out.append(f"Routing: {r}")
    if d.get("runLog"):
        rl = d["runLog"]
        out.append(f'Run log: read {len(rl["collectedDomains"])} domain(s), {rl["findingCount"]} findings (read-only).')
    if d.get("narrative"):
        out.append(f'\nSummary: {d["narrative"]}')
    out.append(f"Findings written to {out_path}")
    report_path = os.path.join(base, "runs", "report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(build_markdown_report(envelope))
    out.append(f"Report written to {report_path}")
    return "\n".join(out)


# ---- eval (port of eval.js) ----
def run_eval_cli(base_dir=None, cases_path=None):
    base = _base(base_dir)
    path = cases_path if cases_path is not None else os.path.join(base, "fixtures", "golden", "cases.json")
    with open(path, "r", encoding="utf-8") as fh:
        cases = json.load(fh)
    reasoner = create_stub_reasoner()
    results = []
    for c in cases:
        findings = reasoner["reason"](c["facts"], detect_all(c["facts"]))
        results.append({"name": c["name"], "score": score_case(findings, c["expected"])})
    suite = score_suite(results)
    out = []
    for r in results:
        sc = r["score"]
        miss = (" missing: " + ",".join(sc["missing"])) if sc["missing"] else ""
        out.append(f'{"PASS" if sc["pass"] else "FAIL"} {r["name"]} (recall {sc["recall"]}, precision {sc["precision"]}){miss}')
    out.append(f'Suite: {suite["passed"]}/{suite["cases"]} passed, avgRecall {suite["avgRecall"]}, avgPrecision {suite["avgPrecision"]}')
    return "\n".join(out)


# ---- whatif (port of whatif.js) ----
def run_whatif_cli(kind=None, size_gb=0, refresh_at=None, base_dir=None):
    base = _base(base_dir)
    facts = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))["collect"]()
    res = assess_what_if(facts, {"kind": kind, "sizeGB": size_gb, "refreshAt": refresh_at})
    out = [f'What-if verdict: {str(res["verdict"]).upper()} (risk {res["riskScore"]})']
    for i in res["impacts"]:
        out.append(f"  - {i}")
    return "\n".join(out)


# ---- triggers (port of triggers.js) ----
def run_triggers_cli(base_dir=None):
    base = _base(base_dir)
    facts = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))["collect"]()
    events = evaluate_threshold_triggers(facts)
    if not events:
        return "No immediate triggers."
    return "\n".join(f'[{e["severity"]}] {e["reason"]}' for e in events)


# ---- lifecycle (port of lifecycle.js) ----
def run_lifecycle_cli(action=None, key=None, snooze_until=None, note=None, now=None, base_dir=None):
    base = _base(base_dir)
    if action not in _LIFECYCLE_ACTIONS:
        raise ValueError(f'Unknown action "{action}" (use: {", ".join(_LIFECYCLE_ACTIONS)})')
    if not key:
        raise ValueError("A finding key is required.")
    if action == "snoozed" and not snooze_until:
        raise ValueError("snoozed requires snoozeUntil (an ISO date)")
    store = create_lifecycle_store(os.path.join(base, "runs", "lifecycle.json"))
    nxt = set_state(store["load"](), key, action, {"note": note, "snoozeUntil": snooze_until, "now": now})
    store["save"](nxt)
    return f'Set {key} -> {nxt[key]["state"]}'


# ---- dax (port of dax.js) ----
def run_dax_cli(measure=""):
    suggestions = analyze_dax(measure)
    if not suggestions:
        return "No obvious DAX anti-patterns detected."
    return "\n".join(f'[{s["pattern"]}] {s["suggestion"]}' for s in suggestions)
