"""redact_secrets — log-safety masking for URLs/tokens before they hit stdout/logs."""
from fabric_audit_agent.query.redact import redact_secrets


def test_redact_bearer_token():
    out = redact_secrets("bearer abc.def")
    assert "abc.def" not in out
    assert "bearer" in out.lower()


def test_redact_url_credentials():
    out = redact_secrets("https://user:s3cret@host/path")
    assert "user" not in out
    assert "s3cret" not in out
    assert "@host" in out


def test_redact_secret_key_value_masks_secret_not_benign():
    out = redact_secrets("password=hunter2&x=1")
    assert "hunter2" not in out
    assert "x=1" in out


def test_redact_does_not_over_mask_benign_kql():
    text = "PowerBIDatasetsWorkspace | where Status==200 and TimeGenerated > ago(1d)"
    out = redact_secrets(text)
    assert out == text


def test_redact_non_str_input_does_not_raise():
    assert isinstance(redact_secrets(12345), str)
    assert isinstance(redact_secrets(None), str)
