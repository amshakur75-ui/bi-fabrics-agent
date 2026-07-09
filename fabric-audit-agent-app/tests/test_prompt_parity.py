"""Prompt parity + honesty-restoration tests (Phase 5.1, Task 1).

These read the raw `_SYSTEM = "..."` source text out of BOTH files by path -- deliberately
NOT by importing `fabric_audit_agent` (the deployed agent app is self-contained and does not
depend on that package at runtime) and NOT via the `agent_server.agent` module-loading helper
used elsewhere in this suite (that executes the module; we only want the literal source text).

Background: the deployed inlined `_SYSTEM` in `agent_server/agent.py` had drifted from the
canonical `fabric_audit_agent/agent/system_prompt.py` and was missing several honesty rules
(see docs/superpowers/plans/2026-07-08-personality-ux.md, Task 1). That drift was fixed by
copying the canonical text (stronger wins) into the inlined copy, then appending the same new
"Presentation & Voice" section to both. These tests lock that: (1) the two literals must stay
byte-identical going forward, and (2) the previously-missing honesty markers must be present in
the inlined copy specifically (proving the reconciliation direction was canonical -> inlined,
not the reverse).
"""
import re
from pathlib import Path

import pytest

_SYSTEM_LITERAL_RE = re.compile(r'_SYSTEM = """(.*?)"""', re.DOTALL)

_INLINED_PATH = Path(__file__).parent.parent / "agent_server" / "agent.py"
_CANONICAL_PATH = (
    Path(__file__).parents[2]
    / "fabric-audit-agent-py"
    / "fabric_audit_agent"
    / "agent"
    / "system_prompt.py"
)


def _extract_system_literal(path: Path) -> str:
    # Pull the `_SYSTEM = """..."""` literal body out of a source file by path.
    #
    # Pinned to the `_SYSTEM = """ ... """` *assignment* specifically -- both source files
    # contain other triple-quoted strings (module docstrings) that a looser regex could
    # accidentally match.
    text = path.read_text(encoding="utf-8")
    matches = _SYSTEM_LITERAL_RE.findall(text)
    assert len(matches) == 1, (
        f"expected exactly one `_SYSTEM = \"\"\"...\"\"\"` literal in {path}, found {len(matches)}"
    )
    return matches[0]


def test_extraction_targets_source_not_build():
    """Guard against a future refactor accidentally pointing this test at a build/ artifact."""
    assert _INLINED_PATH.exists(), f"inlined source not found at {_INLINED_PATH}"
    assert _CANONICAL_PATH.exists(), f"canonical source not found at {_CANONICAL_PATH}"
    assert "build" not in _CANONICAL_PATH.parts
    assert "build" not in _INLINED_PATH.parts
    assert "lib" not in _CANONICAL_PATH.parts


def test_inlined_and_canonical_system_prompts_are_byte_identical():
    inlined = _extract_system_literal(_INLINED_PATH)
    canonical = _extract_system_literal(_CANONICAL_PATH)
    # Full-string equality, including trailing whitespace/newlines -- not a substring/marker
    # check. A single dropped/added character anywhere (including trailing whitespace) fails this.
    assert inlined == canonical, (
        "inlined agent_server/agent.py `_SYSTEM` has drifted from the canonical "
        "fabric_audit_agent/agent/system_prompt.py `_SYSTEM` -- reconcile canonical -> inlined "
        "(the canonical text is the source of truth; never edit toward the weaker copy)."
    )


def test_parity_check_is_sensitive_to_a_one_character_delta():
    """Meta-assertion: prove the equality check above is not vacuously true (e.g. both empty)
    and would genuinely catch a single-character drift."""
    inlined = _extract_system_literal(_INLINED_PATH)
    canonical = _extract_system_literal(_CANONICAL_PATH)
    assert len(inlined) > 1000  # sanity: this is the real, substantial prompt body
    mutated = canonical[:-1] + ("X" if not canonical.endswith("X") else "Y")
    assert mutated != inlined, "sanity check itself is broken: mutation did not change the string"
    assert inlined == canonical  # re-assert the real (non-mutated) comparison still holds


# ---------------------------------------------------------------------------
# Honesty-restoration: prove the specific rules that had drifted OUT of the inlined
# copy are back, by reading the inlined source directly (independent of the parity
# test above, so a bug in the parity regex can't hide a bug here).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "marker",
    [
        "Final review",                                   # whole final-review section restored
        "evidence in plain language",                      # plain-language evidence citation
        "name the data, not the tool",                      # (citation requirement retained)
        "ABSENT",                                           # never claim ABSENT from one listing
        "missing from one listing",
        "were blind to",                                    # coverage gloss
        "instructions, links, or requests",                 # fuller injection clause
        "never follow them",
    ],
)
def test_inlined_system_prompt_restored_honesty_marker(marker):
    inlined = _extract_system_literal(_INLINED_PATH)
    assert marker in inlined, (
        f"inlined _SYSTEM is missing restored honesty marker {marker!r} -- the "
        "canonical -> inlined reconciliation may have regressed."
    )
