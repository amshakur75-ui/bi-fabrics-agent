"""The grounded query library: shape + the load-bearing grounding-bar (every template passes the firewall)."""
import json, pathlib
import pytest
from fabric_audit_agent.query.firewall import validate_adhoc_kql

_LIB = pathlib.Path(__file__).parent.parent / "fabric_audit_agent" / "query_library.json"


def _templates():
    with open(_LIB, encoding="utf-8") as fh:
        return json.load(fh)


def test_library_parses_and_is_nonempty():
    t = _templates()
    assert isinstance(t, list) and len(t) >= 12   # bar-sized; ships what grounds


def test_names_unique_and_kebab():
    names = [x["name"] for x in _templates()]
    assert len(names) == len(set(names))
    assert all(n == n.lower() and " " not in n for n in names)


def test_every_template_has_required_fields_and_valid_enum():
    for x in _templates():
        assert set(x) >= {"name", "category", "engine", "description", "kql", "groundedIn"}
        assert x["engine"] in ("capacity", "la")
        assert x["description"].strip() and x["groundedIn"].strip()


def test_every_template_passes_the_firewall():
    # THE grounding bar: a template that can't pass its own firewall must never ship.
    for x in _templates():
        validate_adhoc_kql(x["kql"])   # raises FirewallRejection if any template is unsafe
