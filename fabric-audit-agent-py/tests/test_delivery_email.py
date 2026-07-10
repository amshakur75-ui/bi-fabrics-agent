"""Tests for the Phase 6 SMTP email delivery adapter. Offline — no real network."""
from fabric_audit_agent.adapters.delivery_email import create_email_delivery


def _fake_sender():
    sent = []

    def sender(message, cfg):
        sent.append({"message": message, "cfg": cfg})

    return sent, sender


def _env(**over):
    e = {"SMTP_HOST": "smtp.local", "SMTP_TO": "ops@x.com", "SMTP_FROM": "audit@x.com"}
    e.update(over)
    return e


def _envelope():
    return {"summary": "audit ok", "data": {"tenant": "Acme", "verdict": {"decision": "size-up", "reason": "hot"}}}


# ---- inert until configured ----
def test_unconfigured_no_host_is_noop():
    sent, sender = _fake_sender()
    d = create_email_delivery({"SMTP_TO": "ops@x.com"}, sender=sender)   # no SMTP_HOST
    out = d["deliver"](_envelope())
    assert out == {"delivered": False, "reason": "unconfigured"}
    assert sent == []


def test_unconfigured_no_recipients_is_noop():
    sent, sender = _fake_sender()
    d = create_email_delivery({"SMTP_HOST": "smtp.local"}, sender=sender)   # no SMTP_TO
    assert d["deliver"](_envelope()) == {"delivered": False, "reason": "unconfigured"}
    assert sent == []


# ---- configured happy path ----
def test_configured_sends_with_correct_headers_and_body():
    sent, sender = _fake_sender()
    d = create_email_delivery(_env(SMTP_TO="a@x.com, b@x.com"), sender=sender)
    out = d["deliver"](_envelope())
    assert out["delivered"] is True and out["target"] == ["a@x.com", "b@x.com"]
    msg = sent[0]["message"]
    assert msg["From"] == "audit@x.com"
    assert msg["To"] == "a@x.com, b@x.com"           # comma-split then rejoined
    assert msg["Subject"] == "[Fabric audit] size-up"
    body = msg.get_content()
    assert "# Fabric Audit Report" in body           # the markdown report is the body
    assert "SIZE-UP" in body


# ---- failure-card shape (no verdict/digest) ----
def test_failure_card_subject_falls_back_to_summary():
    sent, sender = _fake_sender()
    d = create_email_delivery(_env(), sender=sender)
    out = d["deliver"]({"summary": "SWEEP FAILED: boom"})   # minimal dict, no data/verdict
    assert out["delivered"] is True
    assert sent[0]["message"]["Subject"] == "[Fabric audit] SWEEP FAILED: boom"


# ---- self-gate backstop ----
def test_self_gate_masks_secret_even_off_dispatch_path():
    sent, sender = _fake_sender()
    d = create_email_delivery(_env(), sender=sender)
    leaky = {"summary": "leak eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2", "data": {"tenant": "Acme"}}
    d["deliver"](leaky)   # passed directly, bypassing dispatch_outbound
    body = sent[0]["message"].get_content()
    assert "eyJhbGciOiJI" not in body                # the JWT-shaped secret was gated out


# ---- connection config ----
def test_starttls_and_auth_config_passed_to_sender():
    sent, sender = _fake_sender()
    d = create_email_delivery(
        _env(SMTP_PORT="2525", SMTP_USER="u", SMTP_PASSWORD="p", SMTP_STARTTLS="true"),
        sender=sender,
    )
    d["deliver"](_envelope())
    cfg = sent[0]["cfg"]
    assert cfg["host"] == "smtp.local" and cfg["port"] == 2525
    assert cfg["user"] == "u" and cfg["password"] == "p" and cfg["starttls"] is True


def test_starttls_can_be_disabled():
    sent, sender = _fake_sender()
    d = create_email_delivery(_env(SMTP_STARTTLS="false"), sender=sender)
    d["deliver"](_envelope())
    assert sent[0]["cfg"]["starttls"] is False
