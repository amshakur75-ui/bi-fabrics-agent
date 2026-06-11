"""Score reasoner output against golden expected finding types. Port of ``core/eval.js``. Pure."""
import math


def _round2(x):
    return math.floor(x * 100 + 0.5) / 100


def _types_of(findings):
    seen, out = set(), []
    for f in findings:
        k = f.get("key")
        if isinstance(k, str):
            t = k.split("::")[0]
            if t not in seen:
                seen.add(t)
                out.append(t)   # ordered unique (mirrors JS Set insertion order)
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
