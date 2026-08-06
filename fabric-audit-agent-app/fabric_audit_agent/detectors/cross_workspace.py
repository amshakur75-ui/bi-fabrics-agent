"""B4 — cross-workspace pattern matching. Pure post-pass over the per-item detector flags.

If the SAME structural anti-pattern (e.g. a DirectQuery report, a bidirectional-heavy model, a
chronic refresh error) shows up in several DIFFERENT workspaces, that's a systemic signal — a
copy-pasted measure, a shared template, or a team-wide habit — not N isolated problems, and the fix
recommendation should say "fix it once at the source" rather than repeating the same advice N times.

Deliberately scoped to STRUCTURAL anti-patterns. Capacity is single-tenant; attribution
(concentration / cross_user) and meta/error flags are excluded — clustering those across workspaces
isn't the same "one root cause" signal.
"""
# flag-type prefixes that are NOT meaningful to cluster across workspaces
_EXCLUDE_PREFIXES = ("capacity", "meta", "pattern", "concentration", "cross_user", "blind_spot")


def _workspace_of(resource):
    # resources are "Workspace / Item" (report/model/refresh/share) or just "Workspace" (admin-grant)
    return (str(resource or "").split(" / ")[0].strip()) or "(unknown)"


def cross_workspace_patterns(flags, min_workspaces=3):
    """Return ``pattern.cross-workspace`` flags for any anti-pattern type present in
    ``>= min_workspaces`` DISTINCT workspaces. Sorted by breadth (most workspaces first). Pure."""
    by_type = {}
    for f in flags or []:
        t = f.get("type") or ""
        if not t or any(t.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        ws = _workspace_of(f.get("resource"))
        by_type.setdefault(t, {}).setdefault(ws, f)  # keep one sample flag per (type, workspace)

    out = []
    for t, ws_map in by_type.items():
        if len(ws_map) >= min_workspaces:
            workspaces = sorted(ws_map)
            shown = ", ".join(workspaces[:5]) + ("…" if len(workspaces) > 5 else "")
            out.append({
                "type": "pattern.cross-workspace", "resource": t, "when": "",
                "evidence": {"patternType": t, "workspaceCount": len(workspaces),
                             "workspaces": workspaces},
                "what": (f"The \"{t}\" issue appears across {len(workspaces)} workspaces ({shown}) — "
                         "likely a shared/copy-pasted pattern or a team-wide gap, not "
                         f"{len(workspaces)} isolated problems. Fix it once at the source/template."),
            })
    return sorted(out, key=lambda x: x["evidence"]["workspaceCount"], reverse=True)
