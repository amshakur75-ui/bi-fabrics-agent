"""Schedule + threshold triggers. Port of ``core/triggers.js``. Pure (time injected)."""
import re
from .config import DEFAULT_CONFIG


def should_run_scheduled(schedule=None, now=None):
    schedule = schedule or {}
    now = now or {}
    cadence = schedule.get("cadence", "daily")
    at_hour = schedule.get("atHour", 6)
    at_minute = schedule.get("atMinute", 0)
    target_dow = schedule.get("dayOfWeek", 1)
    if now.get("minute") != at_minute:
        return False
    if cadence == "hourly":
        return True
    if cadence == "daily":
        return now.get("hour") == at_hour
    if cadence == "weekly":
        return now.get("hour") == at_hour and now.get("dayOfWeek") == target_dow
    return False


def evaluate_threshold_triggers(facts=None, config=None):
    facts = facts or {}
    config = config or DEFAULT_CONFIG
    events = []
    c = facts.get("capacity")
    if c and (c.get("peakCuPct") or 0) >= config["capacity"]["throttleCritPct"]:
        events.append({"reason": f"Capacity {c.get('capacityId')} at {c.get('peakCuPct')}% CU (>= {config['capacity']['throttleCritPct']}% critical)", "severity": "Critical"})
    for p in (facts.get("pipelines") or []):
        if p.get("lastStatus") == "Failed":
            events.append({"reason": f'Pipeline "{p.get("name")}" failed', "severity": "Critical"})
    for g in ((facts.get("access") or {}).get("adminGrants") or []):
        if re.search("admin", str(g.get("role") or ""), re.I) and g.get("sensitive"):
            events.append({"reason": f'Admin grant on sensitive workspace "{g.get("workspace")}"', "severity": "Critical"})
    return events
