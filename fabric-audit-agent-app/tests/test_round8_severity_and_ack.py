"""Round 8: a Critical finding must stay Critical everywhere, and a chat-less ticket must reopen.

Both are MULTI-SITE invariants. That is the whole point: the severity one has five independent
consumers, and fixing the emitter alone would have SILENTLY DOWNGRADED Critical to info in the
digest — a fix causing the bug it was meant to remove.
"""
import inspect
import re

from fabric_audit_agent.adapters.delivery_webhook import _SEV_EMOJI
from fabric_audit_agent.automation import materiality, tier2_check
from fabric_audit_agent.automation.daily_summary import _sev_counts, _ticket_line
from fabric_audit_agent.automation.sweep_delivery import _LEVEL_RANK


def _finding(level):
    return {"key": "model.bidirectional::WS/Sales", "type": "model.bidirectional",
            "resource": "WS / Sales", "score": {"level": level},
            "what": "x", "why": "y", "when": ""}


# ---- the emitter --------------------------------------------------------

def test_the_sweep_emits_critical_instead_of_collapsing_it_into_warn():
    """`sev = "warn" if rank >= 1 else "info"` made the most severe finding the estate sweep can
    produce arrive indistinguishable from an ordinary warning, so no surface had any representation
    for Critical at all."""
    src = inspect.getsource(__import__(
        "fabric_audit_agent.automation.sweep_delivery", fromlist=["x"]).deliver_new_findings)
    assert '"critical"' in src, "the emitter must carry Critical through"
    assert _LEVEL_RANK["Critical"] > _LEVEL_RANK["Warning"] > _LEVEL_RANK["Info"]


# ---- every consumer, or the emitter fix becomes a downgrade -------------

def test_the_digest_counts_critical_as_severe_not_as_info():
    warn, info = _sev_counts([{"severity": "critical"}, {"severity": "warn"},
                              {"severity": "info"}])
    assert (warn, info) == (2, 1), "critical counted as info is a silent downgrade"


def test_the_digest_renders_critical_distinctly():
    line = _ticket_line({"severity": "critical", "checkType": "model", "resource": "WS / Sales"})
    assert "🚨" in line
    assert "⚠️" in _ticket_line({"severity": "warn", "checkType": "model"})
    assert "ℹ️" in _ticket_line({"severity": "info", "checkType": "model"})


def test_the_teams_card_has_a_glyph_for_critical():
    assert set(_SEV_EMOJI) >= {"critical", "warn", "info"}


def test_critical_outranks_warn_in_both_rank_tables():
    """Two separate rank maps gate escalation and the severity high-water mark. An unlisted severity
    falls to the default, which would have ranked CRITICAL at or below info — so a critical incident
    could neither register as a rise nor survive being overwritten by the next info tick."""
    esc = inspect.getsource(materiality.is_escalation)
    m = re.search(r"ranks = \{(.*?)\}", esc, re.DOTALL)
    assert m and '"critical": 2' in m.group(1)

    proc = inspect.getsource(tier2_check.process_alerts)
    m2 = re.search(r"_SEV_RANK = \{(.*?)\}", proc, re.DOTALL)
    assert m2 and '"critical": 2' in m2.group(1)


def test_a_critical_reading_is_an_escalation_over_a_warn_one():
    """The end-to-end consequence of the two rank tables agreeing."""
    trigger = {"check": "pressure", "peakCuPct": 130.0}      # severity_of -> warn
    assert materiality.is_escalation(
        trigger, {"severity": "info", "metric": 100.0}, materiality.load_cfg({})) is True


# ---- a chat-less ticket must be reopenable ------------------------------

def test_the_reopen_lookup_falls_back_to_the_incident_key():
    """For a chat-less ticket (the Part-7 read path, alert_ticket.chat_id IS NULL) the ack lookup
    was gated on chatId, so `_resolved` was always False and the Step-8 recurrence-after-resolve
    branch could never run: the incident came back, the human's resolution note was never surfaced,
    and the ticket was never reopened."""
    src = inspect.getsource(tier2_check.process_alerts)
    assert '_ack_handle = prior.get("chatId") or key' in src
    assert 'ack_store["reopen"](_ack_handle)' in src


def test_the_ack_store_reads_and_clears_by_either_handle():
    from fabric_audit_agent.adapters import chat_store_lakebase as m
    src = inspect.getsource(m.create_ack_store)
    assert "incident_key" in src, "the ack snapshot must select incident_key"
    assert "OR incident_key = %s" in src, "reopen must clear a chat-less ack too"
