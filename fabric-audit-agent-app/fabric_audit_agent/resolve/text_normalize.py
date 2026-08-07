"""text_normalize.py — shared normalization for matching free-text input.

Python port of the plugin's ``services/text-normalize.ts``. This is the ONE
normalization function used by BOTH the term resolver and the field resolver (and the
artifact lookup), centralised here per the plugin's Change-3 sign-off so both apply the
identical case-insensitivity and punctuation rule rather than each carrying a copy that
could drift silently.

Rule: lowercase, collapse any run of non-alphanumeric characters (dots, hyphens,
slashes, multiple spaces, etc.) into a single space, then trim. This makes "Z.Sales",
"Z SALES", "z-sales", and "z   sales" all collapse to the identical string "z sales".

Idempotent: ``normalize_for_matching(normalize_for_matching(s)) == normalize_for_matching(s)``.
"""
from __future__ import annotations

import re

# Any run of characters that are NOT ASCII lowercase letters or digits. Applied AFTER
# lowercasing, so uppercase letters have already folded to a-z. This mirrors the TS
# regex ``/[^a-z0-9]+/g`` exactly: non-ASCII letters (accents, etc.) are treated as
# punctuation and collapse to a space, identical to the source behaviour.
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")


def normalize_for_matching(value: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to a single space, then trim."""
    return _NON_ALNUM_RUN.sub(" ", value.lower()).strip()
