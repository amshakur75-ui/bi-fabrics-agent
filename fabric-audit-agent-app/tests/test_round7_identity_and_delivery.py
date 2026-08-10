"""Round 7: things the agent SAID that were not true.

None of these crashed. Each produced a confident, well-formed, false statement to a human.
"""
import os

import pytest

from agent_server.export_tool import export_html_report_result
from agent_server.loop_hooks import normalize_executing_user_display as norm


# ---- do not invent people ---------------------------------------------------

@pytest.mark.parametrize("raw", [
    "b3f2c1d4-1111-2222-3333-444455556677",          # SP object id, the documented XMLA case
    "{b3f2c1d4-1111-2222-3333-444455556677}",        # ... with braces
    "svc-refresh-agent",                             # a service identity, not a UPN
    "Aaron Smith",                                   # a display name
])
def test_a_non_upn_identity_is_never_completed_into_an_email(raw):
    """The docstring promised "Never synthesizes a fake address" while the code appended the domain
    to ANY value without an "@". ExecutingUser legitimately carries service-principal GUIDs and
    non-UPN service identities, so an XMLA refresh row rendered in the chat table and in every
    export as `<guid>@newellco.com` — a person-shaped identity that does not exist, attached to real
    capacity cost. The mutation is in-place before caching, so it propagated everywhere."""
    assert norm(raw) == raw


def test_a_bare_username_is_still_completed():
    assert norm("aaron") == "aaron@newellco.com"


def test_an_existing_address_is_untouched():
    assert norm("aaron@newellco.com") == "aaron@newellco.com"


def test_empty_and_none_are_empty():
    assert norm("") == "" and norm(None) == ""


# ---- do not offer a file that cannot be fetched -----------------------------

def _report():
    return export_html_report_result(
        {"title": "Top users", "columns": [{"name": "u", "label": "User"}],
         "rows": [{"u": "aaron"}]})


def test_an_export_does_not_claim_a_download_when_none_can_be_served(monkeypatch):
    """Both export tools wrote into the agent container's temp dir and unconditionally returned
    "Download id: <file>.", which the model relays as success. There is no download route in the
    server and the only static mount is the client build, so the file is unreachable, in a different
    container from the browser, and gone on restart."""
    monkeypatch.delenv("FABRIC_EXPORT_DIR", raising=False)
    out = _report()
    assert out["downloadable"] is False
    assert "downloadId" not in out, "a handle the user cannot use must not be offered"
    assert "cannot be delivered" in out["summary"]


def test_an_export_does_offer_a_download_when_a_served_dir_is_configured(tmp_path, monkeypatch):
    """The builder itself is fine — only the delivery claim was false. Configure a served location
    and the normal path returns."""
    monkeypatch.setenv("FABRIC_EXPORT_DIR", str(tmp_path))
    out = _report()
    assert out["downloadable"] is True
    assert out["downloadId"] and "Download id:" in out["summary"]
    assert os.path.exists(out["downloadPath"])
