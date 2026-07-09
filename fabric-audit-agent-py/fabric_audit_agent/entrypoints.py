"""CLI entry points (offline, mock adapters). Ports of the Node root CLIs
``audit.js`` / ``eval.js`` / ``whatif.js`` / ``triggers.js`` / ``lifecycle.js`` / ``dax.js``.

Each returns a text block (testable) and, where the Node CLI did, performs the same file
side effects (audit writes ``runs/latest.json`` + ``runs/report.md``). ``base_dir`` locates
``fixtures/`` and ``runs/`` (defaults to the repo root) so tests can redirect to a temp dir.
"""
import argparse
import json
import os
import sys

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
from .query.mine import parse_audit_lines, rank_candidates, to_library_entries

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_ID = "fabric-audit-agent"
_LIFECYCLE_ACTIONS = ("open", "acknowledged", "snoozed", "resolved", "wontfix")


def _base(base_dir):
    return base_dir if base_dir is not None else _BASE


def _json(obj):
    """Compact JSON like Node ``JSON.stringify`` (no spaces), Unicode kept literal."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _num(x):
    """JS ``Number``->string: drop the trailing ``.0`` for whole floats (1.0 -> '1', 0.67 -> '0.67')."""
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


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
        out.append(f'{"PASS" if sc["pass"] else "FAIL"} {r["name"]} (recall {_num(sc["recall"])}, precision {_num(sc["precision"])}){miss}')
    out.append(f'Suite: {suite["passed"]}/{suite["cases"]} passed, avgRecall {_num(suite["avgRecall"])}, avgPrecision {_num(suite["avgPrecision"])}')
    return "\n".join(out)


# ---- whatif (port of whatif.js) ----
def run_whatif_cli(kind=None, size_gb=0, refresh_at=None, base_dir=None):
    base = _base(base_dir)
    facts = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))["collect"]()
    if isinstance(size_gb, float) and size_gb.is_integer():
        size_gb = int(size_gb)   # JS Number("5") -> 5 (prints "5", not "5.0")
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


# ---- mine-queries (query-library growth loop, Task 4: the CLI seam) ----

_DEFAULT_LIBRARY_NAME = "query_library.json"
_MINE_KQL_PREVIEW_MAX = 120


class _MineArgError(Exception):
    """Raised by ``_MineArgParser.error`` instead of the base class's ``sys.exit`` so a bad CLI
    invocation degrades to a clean returned string, not a stack trace / process exit."""


class _MineArgParser(argparse.ArgumentParser):
    def error(self, message):
        raise _MineArgError(message)


def _resolve_library_path(base_dir=None, library_path=None):
    """One path used for BOTH the dedup-existing read and the --write mutation (plan Architecture
    decision: ``_load_query_library()`` in tools.py hardcodes the package path and can't be
    redirected, so this CLI resolves its own path instead of calling it). ``library_path`` wins
    when given (tests); otherwise the package-adjacent ``query_library.json`` ships. ``base_dir``
    is accepted for signature parity with the other ``run_*_cli`` helpers but does not affect this
    resolution -- the library is package data, not a per-run fixture/output like ``runs/``."""
    if library_path is not None:
        return library_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _DEFAULT_LIBRARY_NAME)


def _read_query_library_from(path):
    """Load templates from *path*. A missing or malformed file degrades to ``[]`` (same
    tolerance as ``tools._load_query_library``) rather than raising. Used for the dedup/preview
    READ, where tolerance is correct; the ``--write`` path uses ``_library_write_base`` instead."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _library_write_base(path):
    """The existing entries to preserve on ``--write``, or ``(None, error_string)`` if the file is
    present but unreadable / not a JSON list. A truly ABSENT file is fine -> ``([], None)`` (create a
    new library). This is deliberately STRICTER than ``_read_query_library_from``: silently treating
    a present-but-malformed library as ``[]`` here would OVERWRITE a curated library with just the
    mined entries — turning a recoverable parse error into unrecoverable data loss. A malformed file
    is recoverable; a clobbered one is not, so we refuse before opening for write."""
    if not os.path.exists(path):
        return [], None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, (f"mine-queries: refusing to --write — the library at {path} exists but is "
                      f"unreadable ({exc}); fix or remove it first (no changes made)")
    if not isinstance(data, list):
        return None, (f"mine-queries: refusing to --write — the library at {path} is not a JSON "
                      f"list; fix it first (no changes made)")
    return data, None


def _detect_newline(path):
    """The library file's existing newline style, so a ``--write`` rewrite doesn't flip every
    line's ending (CRLF<->LF churn that would bury the intended additions in the PR diff). An
    absent/unreadable file -> ``"\\n"`` (repo-friendly LF default)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return "\n"
    return "\r\n" if b"\r\n" in raw else "\n"


def _mine_one_line_kql(kql, limit=_MINE_KQL_PREVIEW_MAX):
    """Collapse a (possibly multi-line) kql to one line for the preview table, truncated."""
    flat = " ".join(str(kql).split())
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


def _mine_no_candidates_message(min_count, write):
    base = (
        f"No promotable query shapes found (need >= {min_count} repeat(s) of a new, "
        "not-already-in-the-library query shape)."
    )
    if write:
        return base + " Nothing to add — the library file was left unchanged."
    return base


def _mine_format_preview(entries, logfile):
    lines = [f"Found {len(entries)} promotable query shape(s) in {logfile} (preview only — nothing written):", ""]
    lines.append(f'{"rank":>4}  {"hitCount":>8}  {"engine":<8}  name / kql')
    for i, e in enumerate(entries, start=1):
        lines.append(f'{i:>4}  {e["hitCount"]:>8}  {e["engine"]:<8}  {e["name"]}')
        lines.append(f'      {_mine_one_line_kql(e["kql"])}')
    lines.append("")
    lines.append("Ready-to-paste query_library.json entries:")
    for e in entries:
        lines.append(json.dumps(e, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("Re-run with --write to append these entries to the query library.")
    return "\n".join(lines)


def run_mine_queries_cli(rest, base_dir=None, library_path=None) -> str:
    """``mine-queries`` CLI: mine the ``[adhoc-kql]`` audit log for repeated, firewall-passing
    query shapes not already in ``query_library.json`` and either preview them (default) or
    ``--write`` them to the library. Read-only unless ``--write`` is passed, and even then the
    ONLY file ever mutated is the single resolved library path (see ``_resolve_library_path``).

    ``rest`` is the CLI arg list: positional ``logfile`` (``-`` = stdin), ``--min-count`` (default
    3), ``--top`` (default 10), ``--write`` (flag). Never raises for user-facing failures (a bad
    arg list or a missing logfile returns a clean error string).
    """
    parser = _MineArgParser(prog="mine-queries", add_help=False)
    parser.add_argument("logfile")
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--write", action="store_true")
    try:
        args = parser.parse_args(list(rest))
    except _MineArgError as exc:
        return f"mine-queries: {exc}"

    lib_path = _resolve_library_path(base_dir=base_dir, library_path=library_path)
    existing = _read_query_library_from(lib_path)

    if args.logfile == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.logfile, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            return f"mine-queries: could not read log file {args.logfile!r}: {exc.strerror or exc}"

    records = parse_audit_lines(text.splitlines())
    ranked = rank_candidates(records, existing, min_count=args.min_count, top_n=args.top)
    entries = to_library_entries(ranked, existing)

    if not entries:
        return _mine_no_candidates_message(args.min_count, args.write)

    if not args.write:
        return _mine_format_preview(entries, args.logfile)

    # --write: never clobber a present-but-unreadable curated library. A parse error must abort
    # BEFORE we open for write (a malformed file is recoverable; a wiped one is not). An absent
    # file is fine -> create a new library. This strict re-read is separate from `existing` above,
    # which is intentionally tolerant for dedup/preview.
    base_entries, err = _library_write_base(lib_path)
    if err is not None:
        return err

    updated = list(base_entries) + entries
    with open(lib_path, "w", encoding="utf-8", newline=_detect_newline(lib_path)) as fh:
        json.dump(updated, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    names = ", ".join(e["name"] for e in entries)
    plural = "y" if len(entries) == 1 else "ies"
    return f"Wrote {len(entries)} new quer{plural} to {lib_path}: {names}"
