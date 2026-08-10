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
#
# The second group is a CORRECTNESS requirement, not a taste call. `_workspace_of` reads the text
# before " / " and these five families do not put a workspace there at all -- they set `resource` to
# a bare item name (long_running, query_shape, query_antipatterns, xmla_errors) or to a USER EMAIL
# (absolute_cost), and none of them carries a workspace in evidence either. So each distinct user or
# item counted as a distinct "workspace": three slow operations by three people in ONE workspace
# produced a Warning-level finding reading
#
#   "The 'activity.slow-operation' issue appears across 3 workspaces (aaron@newellco.com,
#    brenda@newellco.com, carl@newellco.com) - likely a shared/copy-pasted pattern... Fix it once at
#    the source/template."
#
# ...which is false on its face AND leaks user emails into a Teams card under the label
# "workspaces". Clustering these across workspaces is impossible with the data they carry; if a
# workspace is ever added to those flags, remove the prefix here and the clustering starts working.
_EXCLUDE_PREFIXES = ("capacity", "meta", "pattern", "concentration", "cross_user", "blind_spot",
                     "activity", "query", "xmla")


def _workspace_of(resource):
    """The workspace a flag belongs to, or None when the resource does not name one.

    Resources are "Workspace / Item" (report/model/refresh/share) or a bare "Workspace"
    (admin-grant). Returning a sentinel like "(unknown)" would make every unattributable flag
    cluster together under one fake workspace, so this returns None and the caller skips it.
    """
    r = str(resource or "").strip()
    if not r:
        return None
    if "@" in r.split(" / ")[0]:
        # A user, not a workspace. Belt-and-braces for any future detector that sets resource to a
        # principal -- the _EXCLUDE_PREFIXES above cover today's five.
        return None
    return r.split(" / ")[0].strip() or None


def cross_workspace_patterns(flags, min_workspaces=3):
    """Return ``pattern.cross-workspace`` flags for any anti-pattern type present in
    ``>= min_workspaces`` DISTINCT workspaces. Sorted by breadth (most workspaces first). Pure."""
    by_type = {}
    for f in flags or []:
        t = f.get("type") or ""
        if not t or any(t.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        ws = _workspace_of(f.get("resource"))
        if ws is None:
            continue          # no workspace to cluster on; counting it would invent one
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
