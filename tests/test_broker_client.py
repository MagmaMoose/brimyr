"""Unit tests for the token-broker client (brimyr.broker_client)."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from brimyr.broker_client import (
    OIDC_AUDIENCE,
    mint_bot_token,
    request_actions_oidc_token,
)

_OIDC = "eyJhbGciOiJSUzI1NiJ9.fake-oidc"
_MINTED = "ghs_a_minted_installation_token"


class _Response:
    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeOpener:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def open(self, request: Any, timeout: float | None = None) -> _Response:
        body = json.loads(request.data.decode()) if request.data else None
        self.calls.append((request.get_method(), request.full_url, body))
        payload = self.responses.pop(0) if self.responses else {}
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)


@pytest.fixture
def actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend to be a job that declared `permissions: id-token: write`."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://runner.example/tok?api-version=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-secret")


def test_no_broker_configured_never_touches_the_network():
    opener = _FakeOpener()
    result = mint_bot_token("", "MagmaMoose", "brimyr", opener=opener)

    assert not result.ok and result.token is None
    assert opener.calls == []


def test_without_id_token_permission_the_message_names_the_fix(monkeypatch):
    """By far the most common cause of a silently reverted byline."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr")

    assert not result.ok
    assert "id-token: write" in result.message


def test_oidc_request_asks_for_the_brimyr_audience(actions_env):
    """The audience is the boundary between brimyr's broker and chargate's."""
    opener = _FakeOpener([{"value": _OIDC}])
    token = request_actions_oidc_token(opener=opener)

    assert token == _OIDC
    _, url, _ = opener.calls[0]
    assert f"audience={OIDC_AUDIENCE}" in url
    assert url.startswith("https://runner.example/tok?api-version=1&")


def test_successful_mint_returns_the_bot_token(actions_env):
    opener = _FakeOpener([{"value": _OIDC}, {"token": _MINTED, "repository": "MagmaMoose/brimyr"}])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    assert result.ok and result.token == _MINTED
    method, url, body = opener.calls[-1]
    assert method == "POST"
    assert url == "https://broker.example/token"
    assert body == {"oidcToken": _OIDC, "owner": "MagmaMoose", "repo": "brimyr"}


def test_trailing_slash_on_the_broker_url_is_tolerated(actions_env):
    opener = _FakeOpener([{"value": _OIDC}, {"token": _MINTED}])
    mint_bot_token("https://broker.example/", "MagmaMoose", "brimyr", opener=opener)

    assert opener.calls[-1][1] == "https://broker.example/token"


@pytest.mark.parametrize(
    "code, error, expected",
    [
        (403, "app_not_installed", "app_not_installed"),
        (403, "repo_mismatch", "repo_mismatch"),
        (503, "config_unavailable", "config_unavailable"),
        (401, "invalid_oidc", "invalid_oidc"),
    ],
)
def test_broker_errors_fall_back_and_name_the_reason(actions_env, code, error, expected):
    """The broker's error vocabulary is fixed, and each one is a different fix."""
    exc = urllib.error.HTTPError(
        "u", code, "err", {}, __import__("io").BytesIO(json.dumps({"error": error}).encode())
    )
    opener = _FakeOpener([{"value": _OIDC}, exc])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    assert not result.ok and result.token is None
    assert str(code) in result.message
    assert expected in result.message


def test_unreachable_broker_falls_back(actions_env):
    opener = _FakeOpener([{"value": _OIDC}, urllib.error.URLError("no route to host")])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    assert not result.ok and "unreachable" in result.message


def test_broker_answering_without_a_token_falls_back(actions_env):
    opener = _FakeOpener([{"value": _OIDC}, {"repository": "MagmaMoose/brimyr"}])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    assert not result.ok and "no token" in result.message


def test_missing_owner_or_repo_short_circuits(actions_env):
    opener = _FakeOpener()
    assert not mint_bot_token("https://broker.example", "", "brimyr", opener=opener).ok
    assert opener.calls == []


def test_the_minted_token_never_appears_in_the_message(actions_env):
    """Nothing carrying the credential may reach a log line."""
    opener = _FakeOpener([{"value": _OIDC}, {"token": _MINTED}])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    assert _MINTED not in result.message
    assert _OIDC not in result.message


def test_a_failure_message_never_leaks_the_oidc_token(actions_env):
    opener = _FakeOpener([{"value": _OIDC}, urllib.error.URLError(f"bad request to {_OIDC}")])
    result = mint_bot_token("https://broker.example", "MagmaMoose", "brimyr", opener=opener)

    # This test CAUGHT A REAL LEAK: the first implementation interpolated the exception
    # with f"broker unreachable: {exc}", and the caller prints that message to stderr, so
    # a transport error carrying the request URL put the OIDC token straight into the job
    # log. Only the exception CLASS NAME is reported now.
    assert not result.ok
    assert _OIDC not in result.message
    assert "URLError" in result.message
