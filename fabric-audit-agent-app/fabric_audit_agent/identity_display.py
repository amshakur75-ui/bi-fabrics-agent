"""The single implementation of executing-user display normalization.

There were three copies of this function -- ``agent_server/loop_hooks.py``, ``export/html_report.py``
and ``export/xlsx_report.py``. When the fabrication bug was found, the fix landed in ONE of them, so
the behaviour did not stop: it MOVED. The chat table stopped inventing an address while both exports
kept inventing one, and the exports are the artifact people forward to other people. A test asserted
the fix and its own docstring claimed "in every export", but it imported only the ``loop_hooks``
copy, so it passed against two unfixed copies.

Hence one module, imported by all three. A future fix cannot land in a subset of the call sites
because there is no longer a subset to land in.
"""
import re
from typing import Any

NEWELL_EMAIL_DOMAIN = "@newellco.com"

# A plausible bare UPN local part: letters, digits and the punctuation Entra actually allows.
# Deliberately EXCLUDES the hyphen. That does cost us `mary-jane.smith`, an ordinary real name shape,
# which is now left bare rather than completed -- but the hyphen is also the shape of every service
# identity we must not touch (`svc-refresh-agent`, and dashed GUIDs), and those are not
# distinguishable from a hyphenated person by pattern alone. Leaving a real person's username
# uncompleted is merely unhelpful; completing `svc-refresh-agent` invents a colleague who does not
# exist and attaches real capacity cost to them. The asymmetry decides it.
_BARE_UPN_LOCAL_PART = re.compile(r"^[A-Za-z0-9._%+]+$")

# Service-principal object ids arrive as 8-4-4-4-12 hex, with or without braces.
_GUID_SHAPE = re.compile(r"^\{?[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}?$")


def normalize_executing_user_display(raw: Any) -> str:
    """Complete a bare username to a Newell address; pass everything else through untouched.

    ``None`` / empty -> ``""``. A value already containing ``@`` is returned unchanged, any domain.

    The original was ``s if "@" in s else s + domain`` under a docstring promising it never
    synthesized a fake address -- i.e. it appended the domain to ANY value lacking an "@".
    ExecutingUser / EffectiveUsername legitimately carry service-principal object GUIDs and non-UPN
    service identities (``collector_events_la`` documents exactly this for XMLA sessions), so an
    XMLA refresh row rendered as ``b3f2c1d4-...-9e8f@newellco.com``: a person-shaped identity that
    does not exist, attached to real capacity cost. Only a plausible bare UPN local part is
    completed now; a GUID, a service identity or a display name is left alone.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if s == "":
        return ""
    if "@" in s:
        return s
    if _GUID_SHAPE.match(s) or not _BARE_UPN_LOCAL_PART.match(s):
        return s          # a GUID, a service identity, a display name -- not ours to complete
    return f"{s}{NEWELL_EMAIL_DOMAIN}"
