"""Tests for resolve.text_normalize — the ONE shared normalization function."""
import pytest

from fabric_audit_agent.resolve import normalize_for_matching as norm


def test_z_sales_variants_collapse_identically():
    # The canonical example from text-normalize.ts: case, dots, and hyphens all fold.
    assert norm("Z.Sales") == "z sales"
    assert norm("Z SALES") == "z sales"
    assert norm("Z-SALES") == "z sales"
    assert norm("z   sales") == "z sales"
    assert norm("Z.Sales") == norm("z sales") == norm("Z-SALES")


def test_trim_and_collapse_runs():
    assert norm("  Z...Sales!!!  ") == "z sales"
    assert norm("a---b___c   d") == "a b c d"


@pytest.mark.parametrize(
    "s",
    ["Z.Sales", "  weird__INPUT--here  ", "Ent-Reporting-Ops-Finance", "already normalized", "", "!!!", "café crème"],
)
def test_idempotent(s):
    once = norm(s)
    assert norm(once) == once


def test_non_ascii_letters_treated_as_punctuation():
    # Mirrors the TS regex /[^a-z0-9]+/ — accented letters are non-[a-z0-9] and collapse.
    assert norm("café") == "caf"
    assert norm("naïve data") == "na ve data"


def test_empty_and_punctuation_only():
    assert norm("") == ""
    assert norm("   ") == ""
    assert norm("...---...") == ""
