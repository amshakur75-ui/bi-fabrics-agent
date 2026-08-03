"""Read-only Microsoft Fabric / Power BI capacity & performance audit agent (Python).

Functional core + swappable adapters, ported from the Node reference implementation.
Read-only posture is absolute: the agent reads telemetry and advises; it never edits,
refreshes, scales, or deletes anything.
"""

__version__ = "1.0.0"
