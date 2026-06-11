"""SLA age vs target per finding. Port of ``core/sla.js``. Pure (time injected)."""
import math
from datetime import datetime, timezone
from .accountability import first_seen_map

_SLA_DAYS = {"Critical": 1, "Warning": 7, "Info": 30}
_DAY_MS = 86_400_000


def _parse_ms(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def assess_sla(findings=None, history=None, now_ms=0, sla_days=None):
    findings = findings or []
    history = history or []
    sla_days = sla_days if sla_days is not None else _SLA_DAYS
    first_seen = first_seen_map(history)
    out = []
    for f in findings:
        fs = first_seen.get(f.get("key"))
        target = sla_days.get((f.get("score") or {}).get("level"))
        if not fs or target is None or now_ms <= 0:
            out.append(f)
            continue
        first_seen_ms = _parse_ms(fs)
        if first_seen_ms is None:
            out.append(f)
            continue
        age_days = math.floor((now_ms - first_seen_ms) / _DAY_MS)
        out.append({**f, "sla": {"ageDays": age_days, "targetDays": target, "breached": age_days > target}})
    return out


def summarize_sla(findings=None):
    findings = findings or []
    breached = [f for f in findings if (f.get("sla") or {}).get("breached")]
    return {
        "breachedCount": len(breached),
        "items": [{"key": f.get("key"), "level": (f.get("score") or {}).get("level"), "ageDays": f["sla"]["ageDays"], "targetDays": f["sla"]["targetDays"]} for f in breached],
    }
