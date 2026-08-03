"""Key helpers. Faithful port of the Node ``core/key-utils.js``."""


def domain_of(key):
    """Extract the domain prefix from a finding key ('type::resource' -> 'type' -> 'domain')."""
    if not isinstance(key, str):
        return "other"
    type_ = key.split("::")[0]
    return type_.split(".")[0] if "." in type_ else "other"


def _user_local(s):
    """Normalise a user handle for matching: lower-cased, domain stripped.

    Log Analytics / Workspace Monitoring store ``ExecutingUser`` as a full UPN
    (``kory.johnson@newellco.com``), but the agent (and users) naturally refer to people by the
    short display name it prints in tables (``Kory.Johnson``). An exact-string match on the full
    email therefore returns ``found: false`` for people who ARE in the data. Reducing both sides to
    the local part (before ``@``) lets ``Kory.Johnson`` match ``kory.johnson@newellco.com`` while
    still distinguishing genuinely different people."""
    s = (s or "").strip().lower()
    return s.split("@", 1)[0] if "@" in s else s


def user_matches(stored, query):
    """True when ``query`` refers to the same person as ``stored`` (case- and domain-insensitive).

    Matches a full-email query exactly AND a short-display-name query against the stored email's
    local part, in either direction. Empty on either side never matches."""
    s = (stored or "").strip().lower()
    q = (query or "").strip().lower()
    if not s or not q:
        return False
    if s == q:
        return True
    return _user_local(s) == _user_local(q)
