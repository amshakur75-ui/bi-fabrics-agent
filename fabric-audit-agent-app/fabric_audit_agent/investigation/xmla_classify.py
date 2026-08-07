"""Pure XMLA / connection-level error classification. tightening.md Part 12 Category 3
(Sub-plan 2 of the alerting redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and
-plugin-parity-design.md``, Task 2c).

``classify_xmla_error(error_text) -> str | None`` maps a raw error/event-text blob to one of
``"auth"`` / ``"bad-request"`` / ``"timeout"`` / ``"connection-drop"``, or ``None`` when the text
doesn't match any known XMLA/connection-failure shape -- INCLUDING the explicit suppression for
"session moved to another node" (and equivalent benign Premium-cluster rebalancing transients),
which must never be classified as an error (tightening.md: "informational only, must never be
surfaced as a problem"). Matching is case-insensitive substring matching against real-world
message shapes; malformed/non-string input returns ``None`` (fail-open), never raises.

Where the text comes from: ``PowerBIDatasetsWorkspace``'s ``EventText`` column (verbose per-event
text -- DAX/MDX query text for normal ops, but also where XMLA/connection failure text shows up
per Microsoft's docs and tightening.md Cat 3: "greppable in EventText"). ``investigation/events.py``
``normalize_event`` already carries ``EventText`` forward as ``queryText`` on every event, so this
classifier is called on ``event["queryText"]`` -- no new field is required (see
``detectors/xmla_errors.py`` for the wiring).
"""

# Checked FIRST and unconditionally: any of these substrings mark the whole blob benign, no
# matter what else is in the text. "session moved to another node" is a normal Premium-cluster
# rebalancing event, not an error (tightening.md Part 12 Cat 3 + "Explicitly NOT bad").
_SUPPRESSED = (
    "session moved to another node",
    "session has been moved to another node",
    "the session was moved to another node",
)

_AUTH_MARKERS = (
    "authentication failed", "authentication error", "unauthorized", "401",
    "invalid token", "token has expired", "token is expired", "access token has expired",
    "aadsts", "credentials are invalid", "invalid credentials", "forbidden",
    "the caller does not have permission",
)

_BAD_REQUEST_MARKERS = (
    "bad request", "400", "tmsl", "the json document is invalid", "invalid xmla request",
    "the request could not be deserialized", "malformed",
)

_TIMEOUT_MARKERS = (
    "xml for analysis request timed out", "xmla request timed out", "request timed out",
    "-2147467259", "execution timeout expired",
)

_CONNECTION_DROP_MARKERS = (
    "connection was forcibly closed", "connection reset", "connection dropped",
    "connection was lost", "the underlying connection was closed",
    "unable to connect", "connection refused", "server was not found",
    "network-related or instance-specific error",
)


def classify_xmla_error(error_text):
    """Classify a raw error/event-text blob into an XMLA/connection-error cause, or ``None`` for
    benign/no-match text. Pure, never raises."""
    if not isinstance(error_text, str) or not error_text.strip():
        return None
    blob = error_text.lower()

    if any(marker in blob for marker in _SUPPRESSED):
        return None
    if any(marker in blob for marker in _AUTH_MARKERS):
        return "auth"
    if any(marker in blob for marker in _BAD_REQUEST_MARKERS):
        return "bad-request"
    if any(marker in blob for marker in _TIMEOUT_MARKERS):
        return "timeout"
    if any(marker in blob for marker in _CONNECTION_DROP_MARKERS):
        return "connection-drop"
    return None
