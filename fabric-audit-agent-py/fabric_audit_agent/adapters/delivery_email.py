"""Email DeliveryPort (Phase 6): sends the audit report over SMTP. Mirrors ``delivery_teams``.

INERT until ``SMTP_HOST`` + ``SMTP_TO`` are set — an unconfigured environment yields a no-op
``deliver`` (like ``job._csv_delivery``), so the deployed Job sends nothing until email is
deliberately configured. No admin consent needed (a plain SMTP relay, not Graph). This module is the
SOLE owner of the "send only when configured" invariant.

The SMTP sender is INJECTED (``sender(message, cfg)``) so tests never touch a real socket, exactly
as ``delivery_teams`` injects its HTTP client. Recipients come from ``SMTP_TO`` ONLY — never from the
payload/findings/observed content (the key anti-exfil choice for a comms channel).

``deliver`` receives an already-egress-gated payload (its only caller is
``outbound.dispatch_outbound``, which gates first) but SELF-GATES again as defense-in-depth on this
new outbound surface: ``apply_egress_controls`` is deterministic and idempotent on already-safe
input, so a second pass costs nothing and permanently closes any "wired outside the gate" hole.
"""
import smtplib
from email.message import EmailMessage

from ..egress import apply_egress_controls
from ..report_md import build_markdown_report


def _smtp_send(message, cfg):
    """Default sender: real SMTP. ``cfg`` = {host, port, user, password, starttls}."""
    with smtplib.SMTP(cfg["host"], cfg["port"]) as smtp:
        if cfg.get("starttls"):
            smtp.starttls()
        if cfg.get("user"):
            smtp.login(cfg["user"], cfg.get("password") or "")
        smtp.send_message(message)


def _bool_env(env, name, default):
    raw = env.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def create_email_delivery(env, *, sender=None):
    host = env.get("SMTP_HOST")
    recipients = [a.strip() for a in (env.get("SMTP_TO") or "").split(",") if a.strip()]
    configured = bool(host) and bool(recipients)
    send = sender if sender is not None else _smtp_send

    def deliver(envelope):
        if not configured:
            return {"delivered": False, "reason": "unconfigured"}

        # Defense-in-depth backstop gate (idempotent on already-gated input).
        safe, _ = apply_egress_controls(envelope, sink="alert")
        data = safe.get("data") or {}
        verdict = (data.get("verdict") or {}).get("decision")
        # Failure cards carry no verdict/digest -> fall back to the summary for the subject.
        subject = f"[Fabric audit] {verdict}" if verdict else f"[Fabric audit] {safe.get('summary') or 'report'}"

        msg = EmailMessage()
        msg["From"] = env.get("SMTP_FROM") or env.get("SMTP_USER") or "fabric-audit@localhost"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(build_markdown_report(safe))

        send(msg, {
            "host": host,
            "port": int(env.get("SMTP_PORT") or 587),
            "user": env.get("SMTP_USER"),
            "password": env.get("SMTP_PASSWORD"),
            "starttls": _bool_env(env, "SMTP_STARTTLS", True),
        })
        return {"delivered": True, "target": recipients}

    return {"deliver": deliver}
