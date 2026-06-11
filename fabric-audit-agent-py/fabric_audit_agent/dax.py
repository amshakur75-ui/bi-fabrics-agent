"""DAX performance anti-pattern hints (rule-based text analysis). Port of ``core/dax.js``.

Heuristic — may have false positives; intended as hints, not verdicts. At deployment the
measure text can come from the Power BI remote MCP (getModelSchema + executeDax).
"""
import re

_RULES = [
    (re.compile(r"FILTER\s*\(\s*'?[A-Za-z0-9_ ]+'?\s*,", re.I), "filter-whole-table",
     "FILTER() over an entire table is slow — filter a single column (KEEPFILTERS or a boolean column filter) instead."),
    (re.compile(r"(SUMX|AVERAGEX|MAXX|MINX|COUNTX)[\s\S]*?(SUMX|AVERAGEX|MAXX|MINX|COUNTX)", re.I), "nested-iterators",
     "Possible nested iterators (X-functions inside X-functions). If nested, they evaluate row-by-row quadratically — precompute with a VAR or an aggregation table."),
    (re.compile(r"\bCALCULATE\b[\s\S]*\bCALCULATE\b", re.I), "repeated-calculate",
     "Repeated CALCULATE recomputes filter context — hoist shared expressions into VARs."),
    (re.compile(r"\bEARLIER\s*\(", re.I), "earlier",
     "EARLIER() signals legacy row-context nesting — refactor with VARs."),
    (re.compile(r"(?<![/:])[^/]/[^/]"), "raw-division",
     'Use DIVIDE(numerator, denominator) instead of "/" for safe, efficient division.'),
]


def analyze_dax(measure_text="", stats=None):
    stats = stats or {}
    text = str(measure_text)
    suggestions = [{"pattern": p, "suggestion": s} for (rx, p, s) in _RULES if rx.search(text)]
    if (stats.get("durationMs") or 0) >= 5000 and len(suggestions) == 0:
        suggestions.append({
            "pattern": "slow-no-obvious-cause",
            "suggestion": f"Measure runs in {stats.get('durationMs')} ms with no obvious anti-pattern — profile with Performance Analyzer / DAX Studio and check the storage-engine vs formula-engine split.",
        })
    return suggestions
