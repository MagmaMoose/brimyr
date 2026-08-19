"""Configuration: PEM normalisation, the allowlist, permissions, and the SSM overlay."""

from __future__ import annotations

import json

import pytest

from app import ssm
from app.config import BrokerConfig, ConfigError, load_config


@pytest.fixture(autouse=True)
def _clean_ssm_cache():
    ssm.reset_cache()
    yield
    ssm.reset_cache()


def test_escaped_newlines_are_restored():
    """Secret stores commonly \\n-escape a PEM; cryptography needs real newlines."""
    # gitleaks:allow - a literal test fixture, not a key
    escaped = "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"  # nosemgrep: generic.secrets.security.detected-private-key
    config = BrokerConfig(app_id="1", private_key=escaped)
    assert "\n" in config.private_key  # nosec B101
    assert "\\n" not in config.private_key  # nosec B101


def test_real_newlines_are_left_alone():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    assert BrokerConfig(app_id="1", private_key=pem).private_key == pem  # nosec B101


def test_allowed_empty_means_any():
    assert BrokerConfig(app_id="1", private_key="k").allowed() == set()  # nosec B101


def test_allowed_parses_and_strips():
    config = BrokerConfig(app_id="1", private_key="k", allowed_repositories="a/b, c/d ,")
    assert config.allowed() == {"a/b", "c/d"}  # nosec B101


def test_permissions_defaults_to_pull_requests_write():
    assert BrokerConfig(app_id="1", private_key="k").permissions() == {"pull_requests": "write"}  # nosec B101


def test_permissions_parses_override():
    config = BrokerConfig(
        app_id="1", private_key="k", token_permissions_json=json.dumps({"issues": "read"})
    )
    assert config.permissions() == {"issues": "read"}  # nosec B101


def test_load_config_reads_the_environment(monkeypatch):
    monkeypatch.delenv("SECRET_PATH", raising=False)
    monkeypatch.setenv("APP_ID", "42")
    monkeypatch.setenv("PRIVATE_KEY", "pem")
    monkeypatch.setenv("OIDC_AUDIENCE", "brimyr")
    config = load_config()
    assert config.app_id == "42"  # nosec B101
    assert config.private_key == "pem"  # nosec B101


def test_load_config_raises_when_unconfigured(monkeypatch):
    """A deployment that was never finished must be distinguishable from a bug."""
    monkeypatch.delenv("SECRET_PATH", raising=False)
    monkeypatch.delenv("APP_ID", raising=False)
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_config()


def test_secret_path_unset_does_not_touch_ssm(monkeypatch):
    """The local and CI path must never import boto3, let alone call AWS."""
    monkeypatch.delenv("SECRET_PATH", raising=False)
    monkeypatch.setenv("APP_ID", "42")
    monkeypatch.setenv("PRIVATE_KEY", "pem")

    def explode(*_args, **_kwargs):
        raise AssertionError("SSM must not be consulted when SECRET_PATH is unset")

    monkeypatch.setattr(ssm, "secrets", explode)
    assert load_config().app_id == "42"  # nosec B101


def test_ssm_overlay_wins_over_the_environment(monkeypatch):
    """The secrets come from Parameter Store, never from the function's own config.

    Anything holding lambda:GetFunctionConfiguration can read an environment variable,
    so a PRIVATE_KEY that leaked into one must not beat the value in SSM.
    """
    monkeypatch.setenv("SECRET_PATH", "/brimyr/prod")
    monkeypatch.setenv("APP_ID", "stale")
    monkeypatch.setenv("PRIVATE_KEY", "stale")
    monkeypatch.setattr(ssm, "secrets", lambda path: {"app_id": "99", "private_key": "real"})

    config = load_config()
    assert config.app_id == "99"  # nosec B101
    assert config.private_key == "real"  # nosec B101
