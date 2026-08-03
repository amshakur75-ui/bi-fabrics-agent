"""VPAX reader. Port of the Node ``core/importers/vpax.js``.

A .vpax is a ZIP of JSON. Uses Python's stdlib ``zipfile`` instead of the Node
hand-rolled ZIP reader (zip.js) — identical behavior, no dependency, more robust.
Defensive about schema variants (DAX Studio / Tabular Editor / Bravo).
"""
import io
import json
import math
import re
import zipfile


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pick(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) is not None:
            return obj[k]
    return None


def _round3(x):
    return math.floor(x * 1000 + 0.5) / 1000


def _fmt(x):
    return str(int(x)) if x == int(x) else str(x)


def vpax_to_models(buf):
    """Read a .vpax (bytes) -> {models, coverage}. Raises ValueError on a bad/empty archive."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(buf))
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"not a ZIP file: {e}")

    json_name = None
    for k in zf.namelist():
        low = k.lower()
        if low.endswith("daxmodel.json") or low.endswith("daxvpaview.json"):
            json_name = k
            if low.endswith("daxmodel.json"):
                break
    if not json_name:
        raise ValueError("no DaxModel.json / DaxVpaView.json inside the .vpax")

    data = json.loads(zf.read(json_name).decode("utf-8"))
    cov = []

    model_name = _pick(data, ["ModelName", "Name"]) or _pick(data.get("Model") or {}, ["Name", "ModelName"]) or "model"
    tables = _pick(data, ["Tables", "tables"])
    if tables is None:
        tables = _pick(data.get("Model") or {}, ["Tables"])
    tables = tables or []
    rels = _pick(data, ["Relationships", "relationships"])
    if rels is None:
        rels = _pick(data.get("Model") or {}, ["Relationships"])
    rels = rels or []

    # size (bytes -> GB): prefer explicit table size, else sum column sizes
    byte_total = 0
    for t in tables:
        t_size = _pick(t, ["TableSize", "TotalSize"])
        if _is_num(t_size):
            byte_total += t_size
            continue
        for c in (_pick(t, ["Columns", "columns"]) or []):
            c_size = _pick(c, ["TotalSize", "ColumnSize", "DataSize"])
            if c_size is None:
                c_size = 0
            if _is_num(c_size):
                byte_total += c_size
    size_gb = _round3(byte_total / 1e9)
    if byte_total > 0:
        cov.append({"field": "models[].sizeGB", "source": json_name, "value": f"{_fmt(size_gb)} GB ({len(tables)} tables)"})
    else:
        cov.append({"field": "models[].sizeGB", "source": json_name, "value": 0, "note": "no per-table/column sizes found in this .vpax schema"})

    def _is_bidi(r):
        b = _pick(r, ["CrossFilteringBehavior", "crossFilteringBehavior", "FilterDirection"])
        return (isinstance(r, dict) and r.get("Bidirectional") is True) or b == 2 or bool(re.search("both", str(b if b is not None else ""), re.I))

    bidi = sum(1 for r in rels if _is_bidi(r))
    cov.append({"field": "models[].bidirectionalRels", "source": json_name, "value": bidi})

    auto = any(re.match(r"^(LocalDateTable_|DateTableTemplate_)", str(_pick(t, ["TableName", "Name", "name"]) or "")) for t in tables)
    cov.append({"field": "models[].autoDateTime", "source": json_name, "value": auto})

    model = {
        "workspace": "(vpax import)", "name": model_name, "sizeGB": size_gb,
        "bidirectionalRels": bidi, "autoDateTime": auto, "refreshFailRatePct": 0,
    }
    return {"models": [model], "coverage": cov}
