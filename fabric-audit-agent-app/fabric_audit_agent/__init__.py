"""Read-only Microsoft Fabric / Power BI capacity & performance audit agent (Python).

Functional core + swappable adapters, ported from the Node reference implementation.
Read-only posture is absolute: the agent reads telemetry and advises; it never edits,
refreshes, scales, or deletes anything.
"""

# Single source of truth is pyproject.toml. A hardcoded literal drifted to "1.0.0" while the
# package shipped 0.2.18, so anyone logging this to identify a deployed build got a wrong answer.
try:                                    # installed (wheel) — the production case
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("fabric-audit-agent")
except Exception:                       # running from a source checkout with no dist-info
    __version__ = "0+unknown"
