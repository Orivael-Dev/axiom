"""Obvious-secret redaction (aui.redact)."""
from aui.redact import redact_secrets


def test_redacts_labelled_password_and_apikey():
    assert "[REDACTED]" in redact_secrets("my password = hunter2")
    assert "hunter2" not in redact_secrets("my password = hunter2")
    out = redact_secrets("api_key: AbC123xyz_secret-value")
    assert "AbC123xyz_secret-value" not in out and "api_key" in out  # label kept


def test_redacts_provider_tokens():
    assert "AKIAIOSFODNN7EXAMPLE" not in redact_secrets("aws AKIAIOSFODNN7EXAMPLE here")
    assert "sk-" not in redact_secrets("openai sk-abcdefghijklmnopqrstuvwxyz012345")
    assert "ghp_" not in redact_secrets("token ghp_abcdefghijklmnopqrstuvwxyz0123456789")


def test_redacts_bearer_and_jwt():
    assert "[REDACTED]" in redact_secrets("Authorization: Bearer abc.def.ghijk")
    jwt = "eyJhbGciOi.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4"
    assert jwt not in redact_secrets(f"here is a jwt {jwt}")


def test_redacts_pem_and_url_creds():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\n-----END RSA PRIVATE KEY-----")
    assert "MIIabc123" not in redact_secrets(pem)
    out = redact_secrets("postgres://user:s3cr3t@db.example.com/app")
    assert "s3cr3t" not in out and "db.example.com" in out   # host preserved


def test_leaves_normal_text_alone():
    text = "Let's meet at 3pm to talk about the launch demo and the music mix."
    assert redact_secrets(text) == text
    assert redact_secrets("") == ""
