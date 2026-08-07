"""Tests for resolve.field_aliases — ALIAS_MAP, trailing-s strip, variant expansion."""
from fabric_audit_agent.resolve.field_aliases import (
    ALIAS_MAP,
    expand_field_alias_variants,
    strip_trailing_s,
)


def test_alias_map_has_the_ported_entries():
    # PORT NOTE: the authoritative field-aliases.ts (and 25c's enumerated list) contain
    # exactly 27 entries, despite the plan's "35" figure. Assert the real count + members.
    assert len(ALIAS_MAP) == 27
    expected = {
        "qty", "amt", "cust", "custs", "qtys", "amts", "desc", "descr", "num", "nbr",
        "addr", "invc", "inv", "ord", "qy", "pct", "avg", "tot", "disc", "vend", "whs",
        "wh", "sku", "rev", "mgr", "dept", "qtr",
    }
    assert set(ALIAS_MAP.keys()) == expected


def test_alias_map_key_values():
    assert ALIAS_MAP["qty"] == "quantity"
    assert ALIAS_MAP["cust"] == "customer"
    assert ALIAS_MAP["inv"] == "invoice"
    assert ALIAS_MAP["pct"] == "percent"
    assert ALIAS_MAP["rev"] == "revenue"
    assert ALIAS_MAP["sku"] == "sku"  # identity entry


def test_strip_trailing_s_rules():
    assert strip_trailing_s("customers") == "customer"
    assert strip_trailing_s("orders") == "order"
    assert strip_trailing_s("sku") == "sku"      # <= 3 chars: untouched
    assert strip_trailing_s("abc") == "abc"      # exactly 3: untouched
    assert strip_trailing_s("address") == "address"  # ends in "ss": untouched
    assert strip_trailing_s("class") == "class"      # ends in "ss": untouched
    assert strip_trailing_s("date") == "date"        # no trailing s


def test_expand_maps_abbreviations():
    variants = expand_field_alias_variants("qty")
    assert "quantity" in variants


def test_expand_multiword_maps_each_word():
    variants = expand_field_alias_variants("cust qty")
    assert "customer quantity" in variants


def test_expand_pluralization_only_variant():
    # "customers" is not in ALIAS_MAP, but the plural strip yields "customer".
    variants = expand_field_alias_variants("customers")
    assert "customer" in variants


def test_expand_does_not_include_original():
    assert "quantity" not in expand_field_alias_variants("quantity")  # nothing to change
    assert expand_field_alias_variants("quantity") == []


def test_expand_empty():
    assert expand_field_alias_variants("") == []
    assert expand_field_alias_variants("   ") == []
