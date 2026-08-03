"""Log-safety secret redaction. Adapted from johnib/mcp-kql-server's ``redact_secrets`` (MIT).
Pure stdlib (``re``). Deterministic -- no clock/random.

``redact_secrets`` masks credentials that might otherwise land in a printed log line (a Fabric
REST ``nextLink`` can carry a ``?...&sig=...`` SAS-style token, for example). It deliberately does
NOT blanket-mask every ``key=value`` pair -- this helper also runs over logged KQL/URLs, and a
blanket mask would corrupt a legitimate KQL predicate like ``where Status=200``. Only a fixed
allowlist of secret-like key names is masked.
"""
import re

# scheme://user:pass@host -- mask both the user and the password, keep the host.
_URL_CREDENTIALS_RE = re.compile(r'(://)[^/\s:@]+:[^/\s@]+@')

# "bearer <token>" (case-insensitive) -- mask the token, preserve the word "bearer".
_BEARER_TOKEN_RE = re.compile(r'(?i)(bearer)\s+\S+')

# key=value where key is a known secret-like name (case-insensitive) -- mask the VALUE only.
# Restricted to an allowlist so a benign "key=value" (e.g. a KQL predicate) is left untouched.
_SECRET_KV_RE = re.compile(
    r'(?i)\b(password|pwd|secret|token|apikey|api_key|key|client_secret|sig|access_token)=[^&\s]+'
)


def redact_secrets(text):
    """Mask credentials in *text* before it is logged. Never raises -- non-``str`` input is
    coerced via ``str(text)`` first. Returns the (possibly unchanged) string."""
    out = str(text)
    out = _URL_CREDENTIALS_RE.sub(r'\1***:***@', out)
    out = _BEARER_TOKEN_RE.sub(r'\1 ***', out)
    out = _SECRET_KV_RE.sub(r'\1=***', out)
    return out
