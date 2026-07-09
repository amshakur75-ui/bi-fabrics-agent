"""Egress chokepoint (anti-exfil): the one gate every outbound/broadcast payload passes through
before it leaves the agent to a sink. Composes (in order) a labeled-sensitive floor, key- and
shape-aware secret redaction, and a findings-targeted size cap. Pure stdlib (``re``, ``copy``),
deterministic, never raises. See docs/superpowers/specs/2026-07-09-egress-chokepoint-design.md.

Reuses (does not reimplement): ``query.redact.redact_secrets`` (in-string name=value / SAS /
bearer masking), the ``sanitize`` sensitivity rule (``sensitive is True`` or a truthy
``sensitivityLabel``), and ``query.envelope.cap_rows`` (char-budget row cap).
"""
import copy
import re

from .query.envelope import cap_rows
from .query.redact import redact_secrets

_MASK = "***"

# Dict keys (case-insensitive, underscores ignored) whose value is masked outright regardless of
# shape -- catches the structured case redact_secrets misses: the secret NAME is the dict key and
# the value is a separate string (e.g. {"clientSecret": "s3cr3t"}).
_SECRET_KEYS = {
    "secret", "token", "password", "pwd", "apikey", "api_key", "key", "client_secret", "sig",
    "access_token", "connectionstring", "accountkey", "sharedaccesskey",
}
_SECRET_KEYS_NORM = {k.replace("_", "") for k in _SECRET_KEYS}

# A JWT: three base64url segments separated by dots, starting with the near-universal "eyJ" header.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
# A connection-string secret segment: AccountKey=/SharedAccessKey=/Password= (case-insensitive).
_CONN_STRING_RE = re.compile(r"(?i)(accountkey|sharedaccesskey|password)\s*=")
# A long opaque base64-alphabet token with no other structure -- the whole string, not embedded.
_LONG_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")


def _normalize_key(key):
    return str(key).lower().replace("_", "")


def _is_secret_key(key):
    if key is None:
        return False
    return _normalize_key(key) in _SECRET_KEYS_NORM


def _looks_like_secret_shape(value):
    return bool(_JWT_RE.search(value) or _CONN_STRING_RE.search(value) or _LONG_BASE64_RE.match(value))


def _redact_string(value, parent_key):
    """Return (new_value, changed) for one string value, key- then shape- then in-string-aware."""
    if _is_secret_key(parent_key):
        return _MASK, value != _MASK
    if _looks_like_secret_shape(value):
        return _MASK, value != _MASK
    redacted = redact_secrets(value)
    return redacted, redacted != value


def _is_sensitive_dict(node):
    return isinstance(node, dict) and (
        node.get("sensitive") is True or bool(node.get("sensitivityLabel"))
    )


def _walk(node, parent_key=None):
    """Recursively apply the sensitivity floor then key/shape/in-string redaction.

    Returns (new_node, secrets_redacted_count, sensitive_dropped_count). Order matters: a dict
    that trips the sensitivity floor is replaced whole and NOT walked further for redaction.
    """
    if _is_sensitive_dict(node):
        return {"redacted": True}, 0, 1

    if isinstance(node, dict):
        out = {}
        secrets = 0
        sensitive = 0
        for k, v in node.items():
            new_v, s, d = _walk(v, parent_key=k)
            out[k] = new_v
            secrets += s
            sensitive += d
        return out, secrets, sensitive

    if isinstance(node, list):
        out = []
        secrets = 0
        sensitive = 0
        for item in node:
            new_item, s, d = _walk(item, parent_key=parent_key)
            out.append(new_item)
            secrets += s
            sensitive += d
        return out, secrets, sensitive

    if isinstance(node, str):
        new_value, changed = _redact_string(node, parent_key)
        return new_value, (1 if changed else 0), 0

    # numbers, bools, None -- load-bearing, never secrets, left untouched.
    return node, 0, 0


def apply_egress_controls(payload, *, sink, max_chars=12000):
    """Return (safe_payload, meta) for an outbound *payload* bound for a broadcast/external
    *sink*. Deep-copies the input first -- the caller's object is never mutated. Then, in order:

      1. Sensitivity floor (recursive): any dict with ``sensitive is True`` or a truthy
         ``sensitivityLabel`` -> ``{"redacted": True}``; counted into ``sensitiveDropped``.
      2. Redaction (recursive, key + shape aware) over every remaining string value: (a) a
         secret-shaped containing dict KEY masks the whole value; (b) else a JWT / connection-
         string / long-base64 SHAPE masks the whole value; (c) else ``redact_secrets`` handles the
         in-string ``name=value`` / SAS / bearer cases. Strings actually changed count into
         ``secretsRedacted``. Numbers/bools/None are untouched.
      3. Size cap targeting the envelope's only unbounded list, ``payload["data"]["findings"]``
         (via ``cap_rows``); if *payload* IS itself a list, it is capped directly. No other list
         (roadmap/correlations/anomalies/suppressed, ...) is touched.
      4. Identifiers/names pass through unchanged (approved decision).

    Pure and deterministic; NEVER raises -- ``None``, a non-dict, a dict without ``data``, or any
    other malformed shape degrades to a safe, disclosed result.
    """
    meta = {"sink": sink, "secretsRedacted": 0, "sensitiveDropped": 0, "truncated": False, "rowsOmitted": 0}

    try:
        working = copy.deepcopy(payload)
    except Exception:
        working = payload

    try:
        safe, secrets_count, sensitive_count = _walk(working)
    except Exception:
        return working, meta

    meta["secretsRedacted"] = secrets_count
    meta["sensitiveDropped"] = sensitive_count

    try:
        if isinstance(safe, dict):
            data = safe.get("data")
            if isinstance(data, dict):
                findings = data.get("findings")
                if isinstance(findings, list):
                    capped, cap_meta = cap_rows(findings, max_chars=max_chars)
                    data["findings"] = capped
                    meta["truncated"] = cap_meta["truncated"]
                    meta["rowsOmitted"] = cap_meta["originalRowCount"] - cap_meta["rowCount"]
        elif isinstance(safe, list):
            capped, cap_meta = cap_rows(safe, max_chars=max_chars)
            safe = capped
            meta["truncated"] = cap_meta["truncated"]
            meta["rowsOmitted"] = cap_meta["originalRowCount"] - cap_meta["rowCount"]
    except Exception:
        pass

    return safe, meta


def disclosure_line(meta):
    """A plain, sink-facing sentence disclosing what was dropped/capped, or ``None`` when *meta*
    shows nothing happened. Composed only from the non-zero parts, e.g.:
    ``"(12 findings omitted for length; 1 sensitive item withheld)"``.
    """
    if not isinstance(meta, dict):
        return None

    rows_omitted = meta.get("rowsOmitted") or 0
    sensitive_dropped = meta.get("sensitiveDropped") or 0

    parts = []
    if rows_omitted:
        noun = "finding" if rows_omitted == 1 else "findings"
        parts.append(f"{rows_omitted} {noun} omitted for length")
    if sensitive_dropped:
        noun = "item" if sensitive_dropped == 1 else "items"
        parts.append(f"{sensitive_dropped} sensitive {noun} withheld")

    if not parts:
        return None
    return "(" + "; ".join(parts) + ")"
