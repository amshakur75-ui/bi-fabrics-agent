"""Reasoner/detector eval scorers.

``score_case``/``score_suite`` here score the auditor's findings against expected finding types
(recall/precision) — used by the ``eval`` CLI and entrypoints. The investigation playbooks have
their own groundedness/coverage scorer in ``eval.score_investigations``."""
import math


def _round2(x):
    return math.floor(x * 100 + 0.5) / 100


def _types_of(findings):
    seen, out = set(), []
    for f in findings:
        k = f.get("key")
        if isinstance(k, str):
            t = k.split("::")[0]
            if t and t not in seen:   # `t and`: JS .filter(Boolean) drops an empty-string type (e.g. a "::res" key); ordered-unique like JS Set
                seen.add(t)
                out.append(t)
    return out


def score_case(actual_findings=None, expected=None):
    actual_findings = actual_findings or []
    expected = expected or {}
    found = _types_of(actual_findings)
    found_set = set(found)
    want = list(dict.fromkeys(expected.get("types") or []))   # ordered unique
    want_set = set(want)
    matched = [t for t in want if t in found_set]
    missing = [t for t in want if t not in found_set]
    extra = [t for t in found if t not in want_set]
    recall = len(matched) / len(want) if want else 1
    precision = len(matched) / len(found) if found else 1
    return {"matched": len(matched), "missing": missing, "extra": extra,
            "recall": _round2(recall), "precision": _round2(precision), "pass": len(missing) == 0}


def score_suite(results=None):
    results = results or []
    passed = len([r for r in results if r["score"]["pass"]])

    def avg(sel):
        return _round2(sum(sel(r["score"]) for r in results) / len(results)) if results else 1

    return {
        "cases": len(results), "passed": passed, "failed": len(results) - passed,
        "avgRecall": avg(lambda s: s["recall"]), "avgPrecision": avg(lambda s: s["precision"]),
    }
