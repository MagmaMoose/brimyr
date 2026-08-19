"""Parameter Store lookup: naming, caching, pagination, and the negative cache."""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from app import ssm


@pytest.fixture(autouse=True)
def _clean():
    ssm.reset_cache()
    yield
    ssm.reset_cache()


class _FakeSsm:
    """Records calls and replays pre-canned pages."""

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self.calls: list[dict] = []

    def get_parameters_by_path(self, **kwargs):
        self.calls.append(kwargs)
        return self._pages[len(self.calls) - 1]


def _install(monkeypatch, fake) -> None:
    monkeypatch.setattr(ssm, "client", lambda service: fake)


def test_parameter_names_become_config_field_names(monkeypatch):
    """/brimyr/prod/private-key -> private_key, so the values land on BrokerConfig."""
    fake = _FakeSsm(
        [
            {
                "Parameters": [
                    {"Name": "/brimyr/prod/app-id", "Value": "123"},
                    {"Name": "/brimyr/prod/private-key", "Value": "pem"},
                ]
            }
        ]
    )
    _install(monkeypatch, fake)
    assert ssm.secrets("/brimyr/prod") == {"app_id": "123", "private_key": "pem"}  # nosec B101


def test_decryption_is_requested(monkeypatch):
    """SecureString values come back as ciphertext without WithDecryption."""
    fake = _FakeSsm([{"Parameters": []}])
    _install(monkeypatch, fake)
    ssm.secrets("/brimyr/prod")
    assert fake.calls[0]["WithDecryption"] is True  # nosec B101
    assert fake.calls[0]["Recursive"] is True  # nosec B101


def test_second_call_is_served_from_cache(monkeypatch):
    fake = _FakeSsm([{"Parameters": [{"Name": "/x/app-id", "Value": "1"}]}])
    _install(monkeypatch, fake)
    ssm.secrets("/x")
    ssm.secrets("/x")
    assert len(fake.calls) == 1  # nosec B101


def test_pagination_follows_next_token(monkeypatch):
    """Page size is 10. Without this an eleventh parameter vanishes silently, and a
    missing secret presents as a 503 with nothing pointing at pagination."""
    fake = _FakeSsm(
        [
            {"Parameters": [{"Name": "/x/app-id", "Value": "1"}], "NextToken": "more"},
            {"Parameters": [{"Name": "/x/private-key", "Value": "pem"}]},
        ]
    )
    _install(monkeypatch, fake)
    assert ssm.secrets("/x") == {"app_id": "1", "private_key": "pem"}  # nosec B101
    assert len(fake.calls) == 2  # nosec B101
    assert fake.calls[1]["NextToken"] == "more"  # nosec B101


def test_failure_is_negative_cached_then_retried(monkeypatch):
    """SSM Standard is 40 TPS shared account-wide, so retrying every request during an
    IAM misconfiguration is how one broken deployment throttles the whole account."""
    attempts = {"n": 0}
    # A controlled clock, installed before the first call. Using the real monotonic()
    # and then jumping to an absolute value does not work: on macOS it is uptime, so
    # "now" can already be far past whatever constant the test picks.
    clock = {"t": 1_000.0}

    class _Boom:
        def get_parameters_by_path(self, **_kwargs):
            attempts["n"] += 1
            raise RuntimeError("AccessDenied")

    _install(monkeypatch, _Boom())
    monkeypatch.setattr(ssm.time, "monotonic", lambda: clock["t"])

    with pytest.raises(RuntimeError):
        ssm.secrets("/x")
    assert attempts["n"] == 1  # nosec B101

    # Inside the window: refused without touching AWS.
    clock["t"] += ssm.NEGATIVE_TTL_SECONDS / 2
    with pytest.raises(RuntimeError):
        ssm.secrets("/x")
    assert attempts["n"] == 1  # nosec B101

    # Past the window: retried, so a late-seeded parameter recovers with no redeploy.
    clock["t"] += ssm.NEGATIVE_TTL_SECONDS
    with pytest.raises(RuntimeError):
        ssm.secrets("/x")
    assert attempts["n"] == 2  # nosec B101


def test_importing_the_module_does_not_import_boto3():
    """boto3 must be imported lazily inside client().

    Two things depend on it: plain pytest collection must not require AWS environment
    variables, and test_lambda_package's poisoned-sys.modules run proves the shipped zip
    does not quietly rely on what the Lambda runtime happens to supply.
    """
    broker_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # nosec B603
        [sys.executable, "-c", "import app.ssm, sys; print('boto3' in sys.modules)"],
        cwd=broker_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"  # nosec B101
