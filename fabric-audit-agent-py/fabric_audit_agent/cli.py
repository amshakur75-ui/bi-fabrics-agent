"""Local CLI logic. Port of the Node ``import.js`` + ``mytest.js``.

Exposed as functions that RETURN their output text (so they're unit-testable); the
``run.py`` dispatcher prints them. 100% local: no network, no API key.

  run_import(files)              import exports + run the diagnosis
  run_import(files, inspect=True) safe per-column stats (no sensitive values)
  run_mytest()                   re-run the diagnosis on my-estate.json
"""
import copy
import json
import math
import os
from pathlib import Path

from .importers.csv import parse_csv
from .importers.map import map_table, merge_facts
from .importers.vpax import vpax_to_models
from .importers.capacity_metrics import (
    looks_like_items, map_items, looks_like_timepoints, analyze_timepoints, inspect_columns,
)
from .diagnosis import diagnose, format_diagnosis

_USAGE = """
  Usage:  python run.py import <file> [moreFiles...]
          python run.py inspect <file.csv>     (safe column stats; no sensitive values)

  Examples:
     python run.py import "Capacity Metrics export.csv"
     python run.py import data.csv Items.csv            (merges both)

  Supported: .csv and .vpax. For Excel, Save As CSV first.
"""


def _default_estate():
    return Path(__file__).resolve().parent.parent / "my-estate.json"


def _n0(x):
    """Math.round(x).toLocaleString() — half-up + thousands separators (x >= 0)."""
    return f"{math.floor(x + 0.5):,}"


def _fmt(x):
    """Render a number like a JS template literal: 70.0 -> '70', 32.4 -> '32.4'."""
    return str(int(x)) if x == int(x) else str(x)


def _read_text(f):
    with open(f, encoding="utf-8") as fh:
        return fh.read()


def _read_bytes(f):
    with open(f, "rb") as fh:
        return fh.read()


def run_import(files, inspect=False, estate_path=None):
    out = []

    def p(line=""):
        out.append(line)

    if not files:
        p(_USAGE)
        return "\n".join(out)

    if inspect:
        for f in files:
            if Path(f).suffix.lower() != ".csv":
                p(f"(skipping {os.path.basename(f)} — inspect is for .csv)")
                continue
            try:
                parsed = parse_csv(_read_text(f))
            except Exception as e:  # noqa: BLE001 - surface any read/parse failure to the user
                p(f"could not read {os.path.basename(f)}: {e}")
                continue
            p()
            p(f"===== INSPECT: {os.path.basename(f)}  ({len(parsed['rows'])} rows) =====")
            for s in inspect_columns(parsed["headers"], parsed["rows"]):
                if s["type"] == "number":
                    p(f"  [num]   {s['column']}: min={_fmt(s['min'])}  median={_fmt(s['median'])}  max={_fmt(s['max'])}  sum={_fmt(s['sum'])}")
                elif s["type"] == "category":
                    p(f"  [cat]   {s['column']}: {', '.join(s['values'])}")
                else:
                    p(f"  [{s['type']}] {s['column']}: {s['distinct']} distinct (values hidden)")
        p()
        return "\n".join(out)

    parts, report, items_analyses, tp_analyses = [], [], [], []
    for f in files:
        ext = Path(f).suffix.lower()
        label = os.path.basename(f)
        try:
            if ext == ".csv":
                parsed = parse_csv(_read_text(f))
                headers, rows = parsed["headers"], parsed["rows"]
                if not headers:
                    report.append({"label": label, "note": "empty / unreadable CSV"})
                    continue
                if looks_like_items(headers):
                    a = map_items(headers, rows)
                    a["label"] = label
                    items_analyses.append(a)
                    report.append({"label": label, "headers": headers, "rows": len(rows), "kind": "Capacity Metrics items table"})
                else:
                    part = map_table(headers, rows)
                    parts.append(part)
                    report.append({"label": label, "headers": headers, "rows": len(rows), "coverage": part["coverage"]})
                    if looks_like_timepoints(headers):
                        t = analyze_timepoints(headers, rows)
                        t["label"] = label
                        tp_analyses.append(t)
            elif ext == ".vpax":
                res = vpax_to_models(_read_bytes(f))
                parts.append({"capacity": None, "models": res["models"], "reports": [], "coverage": res["coverage"]})
                report.append({"label": label, "coverage": res["coverage"]})
            elif ext in (".xlsx", ".xls"):
                report.append({"label": label, "note": "Excel not parsed directly — File -> Save As -> CSV, then re-run"})
            else:
                report.append({"label": label, "note": f'unsupported type "{ext}" — use .csv or .vpax'})
        except Exception as err:  # noqa: BLE001
            report.append({"label": label, "note": f"could not read: {err}"})

    facts = merge_facts(parts)
    if items_analyses:
        facts["items"] = [it for a in items_analyses for it in a["items"]]
    peak = (facts.get("capacity") or {}).get("peakCuPct") or 0
    utilization_unreadable = peak > 1000

    # ---- WHAT I READ ----
    p()
    p("================  IMPORT — WHAT I READ  ===================")
    p()
    for r in report:
        rows_part = f"   ({r['rows']} data row(s))" if r.get("rows") is not None else ""
        kind_part = f"  [{r['kind']}]" if r.get("kind") else ""
        p(f"File: {r['label']}{rows_part}{kind_part}")
        if r.get("note"):
            p(f"   ! {r['note']}")
        if r.get("headers"):
            p(f"   columns: {' | '.join(r['headers'])}")
        for c in r.get("coverage") or []:
            if c.get("source"):
                p(f"   ok  {c['field']}  <-  \"{c['source']}\"  =  {c['value']}")
                if c.get("note"):
                    p(f"       ! {c['note']}")
            else:
                p(f"   --  {c['field']}: {c.get('note') or 'not found'}")
        p()

    # ---- utilization over time ----
    for t in tp_analyses:
        p(f"---- Capacity utilization over time ({t['label']}) ----")
        if t["reportedPeakPct"] is not None:
            spike = "   <- raw pre-smoothing spike, NOT the throttling number" if t["reportedPeakPct"] > 1000 else ""
            p(f"   \"Total CU Usage %\" peak (raw):       {_fmt(t['reportedPeakPct'])}%{spike}")
        if t["computedPeakPct"] is not None:
            p(f"   Total CU(s) / 100%-baseline peak:    {_fmt(t['computedPeakPct'])}%   (baseline {_n0(t['baseline'])} CU-s = 100%)")
        if t["states"]:
            p("   capacity states:  " + "   ".join(f"{k}={v}" for k, v in t["states"].items()))
        p()

    # ---- items: the optimize targets ----
    for a in items_analyses:
        p(f"---- Top CU consumers ({a['label']}) — your optimize targets ----")
        p(f"   {a['itemCount']} items, total {_n0(a['totalCu'])} CU-seconds")
        for i, it in enumerate(a["top"]):
            kind = f" [{it['kind']}]" if it.get("kind") else ""
            ws = f"  ({it['workspace']})" if it.get("workspace") else ""
            p(f"   {str(i + 1).rjust(2)}. {_fmt(it['pctOfTotal']).rjust(4)}%  {it['name']}{kind}{ws}  — {_n0(it['cuSeconds'])} CU-s")
        top5 = math.floor(sum(it["pctOfTotal"] for it in a["top"][:5]) + 0.5)
        p(f"   -> top 5 = {top5}% of all CU.")
        if a["rejectedTotal"] > 0:
            p(f"   THROTTLING CONFIRMED: {_n0(a['rejectedTotal'])} operation(s) rejected. Worst:")
            for it in a["rejectedItems"][:5]:
                p(f"        {_n0(it['rejected'])} rejected   {it['name']}")
        else:
            p("   No operations rejected in this window (no hard-throttling rejections recorded).")
        p()

    if not facts.get("capacity") and not facts.get("models") and not facts.get("reports") and not items_analyses:
        p("No usable capacity / item / model / report data recognized.")
        p("Run  python run.py inspect yourfile.csv  and paste me the output.")
        p()
        return "\n".join(out)

    # ---- persist + diagnose (sanitize an unreadable raw-% so it can't drive a bogus verdict) ----
    facts_for_diag = copy.deepcopy(facts)
    if utilization_unreadable and facts_for_diag.get("capacity"):
        facts_for_diag["capacity"]["peakCuPct"] = 0

    if facts.get("capacity") or facts.get("models") or facts.get("reports") or facts.get("items"):
        with open(estate_path or _default_estate(), "w", encoding="utf-8") as fh:
            json.dump(facts_for_diag, fh, indent=2)
        p("Wrote combined numbers to my-estate.json (gitignored — never pushed). Tweak + re-run: python run.py mytest")
        diag = diagnose(facts_for_diag)
        if diag["findings"]:
            p(format_diagnosis(diag))

    # ---- preliminary, honest read ----
    p()
    p("================  PRELIMINARY READ  =======================")
    p()
    ai = items_analyses[0] if items_analyses else None
    if ai:
        if ai["rejectedTotal"] > 0:
            p(f"* Throttling IS happening: {_n0(ai['rejectedTotal'])} rejected operation(s) — capacity is hitting its ceiling.")
        else:
            p("* No rejected operations recorded — no hard throttling in this window.")
        top5 = math.floor(sum(it["pctOfTotal"] for it in ai["top"][:5]) + 0.5)
        if top5 >= 50:
            p(f"* CU is concentrated: top 5 items = {top5}% of all CU  ->  OPTIMIZE those first before paying for a bigger SKU.")
        else:
            p(f"* CU is spread across many items (top 5 = {top5}%)  ->  less easy headroom; if utilization stays high, sizing up may be justified.")
    if utilization_unreadable:
        p(f"* Overall utilization: NOT readable from this file (the \"%\" column held raw spikes, peak {_n0(peak)}%). Need the smoothed % — see inspect.")
    elif facts.get("capacity"):
        p(f"* Peak utilization read: {_fmt(peak)}%.")

    first_headers_label = next((r["label"] for r in report if r.get("headers")), "data.csv")
    items_label = items_analyses[0]["label"] if items_analyses else ""
    p()
    p("NEXT — to finish the verdict:")
    p(f"  1) python run.py inspect {first_headers_label} {items_label}".rstrip())
    p("     (paste me the stats — numbers + categories only, no item names)")
    p("  2) tell me your capacity SKU  (F2 / F4 / F8 / F16 / F32 / F64 / F128 / F256 ...)")
    p("  3) include a throttling/overload export if you have one")
    p()
    return "\n".join(out)


def run_mytest(estate_path=None):
    target = Path(estate_path) if estate_path else _default_estate()
    if not target.exists():
        return ("\n  my-estate.json not found yet. Two ways to create it:\n"
                "     import your export:  python run.py import yourfile.csv   (or .vpax)\n"
                "     copy the template:   cp my-estate.example.json my-estate.json   (then fill it in)\n"
                "  Then re-run:  python run.py mytest\n")
    with open(target, encoding="utf-8") as fh:
        facts = json.load(fh)
    return format_diagnosis(diagnose(facts))
