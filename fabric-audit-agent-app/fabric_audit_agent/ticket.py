"""Build a tracker-agnostic work item from a finding. Port of ``core/ticket.js``. Pure."""
from .key_utils import domain_of


def build_ticket(finding=None):
    finding = finding or {}
    lvl = (finding.get("score") or {}).get("level")
    level = lvl if lvl is not None else "Info"   # nullish (?? 'Info'), not falsy
    what = finding.get("what")
    what = what if what is not None else "Fabric audit finding"
    fixes = "\n".join(f"- {x}" for x in (finding.get("fix") or []))
    return {
        "title": f"[{level}] {what}",
        "body": "\n\n".join([
            f"Where: {finding.get('where') or ''}",
            f"Why: {finding.get('why') or ''}",
            f"Impact: {finding.get('impact') or ''}",
            f"Fix:\n{fixes}",
        ]),
        "severity": level,
        "labels": ["fabric-audit", domain_of(finding.get("key"))],
        "externalKey": finding.get("key"),
    }
