"""Remove duplicate findings by key. Port of ``core/automation/dedupe.js``.

Findings without a key are always kept (keyless test fakes are unaffected).
"""


def dedupe(findings):
    seen = set()
    out = []
    for f in findings:
        if f.get("key") is None:
            out.append(f)
            continue
        if f["key"] in seen:
            continue
        seen.add(f["key"])
        out.append(f)
    return out
