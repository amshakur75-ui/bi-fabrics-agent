"""Importers: turn real Fabric/Power BI exports (CSV, .vpax) into the agent's facts shape.

Ported from the Node ``core/importers/``. The Node hand-rolled ZIP reader (zip.js) is
replaced here by Python's stdlib ``zipfile`` (used inside vpax.py) — same behavior, no
dependency, more robust.
"""
