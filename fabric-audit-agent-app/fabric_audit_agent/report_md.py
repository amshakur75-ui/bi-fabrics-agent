"""Render a finished audit envelope as a Markdown report. Port of ``core/report-md.js``. Pure."""


def build_markdown_report(envelope=None):
    envelope = envelope or {}
    d = envelope.get("data") or {}
    findings = d.get("findings") or []
    L = []

    L.append("# Fabric Audit Report")
    L.append("")
    if envelope.get("summary"):
        L.append(f"_{envelope['summary']}_")
    if d.get("tenant"):
        L.append(f"Tenant: **{d['tenant']}**")
    if d.get("narrative"):
        L.append("")
        L.append(d["narrative"])

    if d.get("healthScore"):
        L += ["", f"## Health: {d['healthScore']['overall']}/100", "", "| Domain | Score |", "|---|---|"]
        for dom, s in (d["healthScore"].get("byDomain") or {}).items():
            L.append(f"| {dom} | {s} |")

    if d.get("verdict"):
        L += ["", f"## Capacity verdict: {str(d['verdict']['decision']).upper()}", "", d["verdict"].get("reason") or ""]

    if d.get("roadmap"):
        L += ["", "## Remediation roadmap"]
        for r in d["roadmap"]:
            fix_part = f" — _Fix:_ {r['fix']}" if r.get("fix") else ""
            L.append(f"{r['rank']}. **[{r['level']}]** {r['what']}{fix_part}")

    L += ["", f"## Findings ({len(findings)})"]
    for f in findings:
        lvl = (f.get("score") or {}).get("level")
        lvl = lvl if lvl is not None else "Info"
        L += ["", f"### [{lvl}] {f.get('what') or ''}"]
        L.append(f"- **Where:** {f.get('where') or ''}")
        L.append(f"- **Why:** {f.get('why') or ''}")
        L.append(f"- **Impact:** {f.get('impact') or ''}")
        L.append(f"- **Fix:** {'; '.join(f.get('fix') or [])}")

    if d.get("correlations"):
        L += ["", "## Correlations"]
        for c in d["correlations"]:
            L.append(f"- **{c['theme']}:** {c['narrative']}")

    return "\n".join(L)
