"""The decision ladder, called directly.

chargate's equivalent drives these through its FastAPI shell with a TestClient. brimyr's
broker has no shell — LocalStack covers local running — so the ladder is exercised through
``handle_token`` itself, which is the same code the Lambda reaches. The HTTP layer is
covered separately by ``test_lambda_handler.py`` and, against a real API Gateway, by
``localstack/smoke.py``.
"""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.broker import handle_ready, handle_token
from app.config import GITHUB_OIDC_ISSUER, BrokerConfig

AUDIENCE = "brimyr"
REPOSITORY = "MagmaMoose/brimyr"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, key


@pytest.fixture
def config(keypair) -> BrokerConfig:
    pem, _ = keypair
    return BrokerConfig(app_id="123456", private_key=pem, oidc_audience=AUDIENCE)


class _StaticResolver:
    """Hands back one public key, standing in for GitHub's JWKS."""

    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str):
        return type("Key", (), {"key": self._key})()


def _oidc(key, **claims) -> str:
    payload = {
        "iss": GITHUB_OIDC_ISSUER,
        "aud": AUDIENCE,
        "repository": REPOSITORY,
        "iat": int(time.time()) - 10,
        "exp": int(time.time()) + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256")


async def _call(body: dict | str, config, key=None, transport=None):
    raw = body if isinstance(body, str) else json.dumps(body)
    resolver = _StaticResolver(key.public_key()) if key is not None else None
    return await handle_token(
        raw.encode(), config=config, key_resolver=resolver, transport=transport
    )


async def test_malformed_json_is_answered_before_config_is_consulted():
    """A caller who sent nonsense is told so even on a broker that was never finished."""
    status, body = await handle_token(b"not json", config=None)
    assert (status, body["error"]) == (400, "invalid_json")  # nosec B101


async def test_missing_fields(config):
    status, body = await _call({"owner": "MagmaMoose"}, config)
    assert (status, body["error"]) == (400, "missing_fields")  # nosec B101


@pytest.mark.parametrize("repo", ["../../etc/passwd", "a/b", "with%2Fescape", ".."])
async def test_repository_that_could_steer_a_url_is_rejected(config, repo):
    status, body = await _call({"oidcToken": "x", "owner": "MagmaMoose", "repo": repo}, config)
    assert (status, body["error"]) == (400, "invalid_repository")  # nosec B101


async def test_unsigned_token_is_invalid_oidc(config, keypair):
    _, key = keypair
    status, body = await _call(
        {"oidcToken": "not.a.token", "owner": "MagmaMoose", "repo": "brimyr"}, config, key
    )
    assert (status, body["error"]) == (401, "invalid_oidc")  # nosec B101


async def test_wrong_audience_is_rejected(config, keypair):
    """The audience is the boundary that stops chargate's runners minting brimyr's identity."""
    _, key = keypair
    token = _oidc(key, aud="chargate")
    status, body = await _call(
        {"oidcToken": token, "owner": "MagmaMoose", "repo": "brimyr"}, config, key
    )
    assert (status, body["error"]) == (401, "invalid_oidc")  # nosec B101


async def test_claim_must_match_the_repo_being_asked_for(config, keypair):
    """This is what stops repo A minting a token for repo B."""
    _, key = keypair
    token = _oidc(key, repository="MagmaMoose/some-other-repo")
    status, body = await _call(
        {"oidcToken": token, "owner": "MagmaMoose", "repo": "brimyr"}, config, key
    )
    assert (status, body["error"]) == (403, "repo_mismatch")  # nosec B101


async def test_allowlist_blocks_a_repo_outside_it(keypair):
    pem, key = keypair
    config = BrokerConfig(
        app_id="1",
        private_key=pem,
        oidc_audience=AUDIENCE,
        allowed_repositories="MagmaMoose/something-else",
    )
    token = _oidc(key)
    status, body = await _call(
        {"oidcToken": token, "owner": "MagmaMoose", "repo": "brimyr"}, config, key
    )
    assert (status, body["error"]) == (403, "repo_not_allowed")  # nosec B101


async def test_app_not_installed_is_403_not_502(config, keypair):
    _, key = keypair
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    token = _oidc(key)
    status, body = await _call(
        {"oidcToken": token, "owner": "MagmaMoose", "repo": "brimyr"}, config, key, transport
    )
    assert (status, body["error"]) == (403, "app_not_installed")  # nosec B101


async def test_successful_mint_returns_a_scoped_token(config, keypair):
    _, key = keypair

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/installation"):
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(
            200,
            json={"token": "ghs_minted", "expires_at": "2026-08-19T18:00:00Z"},  # nosec B105
        )

    token = _oidc(key)
    status, body = await _call(
        {"oidcToken": token, "owner": "MagmaMoose", "repo": "brimyr"},
        config,
        key,
        httpx.MockTransport(respond),
    )
    assert status == 200  # nosec B101
    assert body["token"] == "ghs_minted"  # nosec B101 B105
    assert body["repository"] == REPOSITORY  # nosec B101


async def test_readyz_is_503_without_config(monkeypatch):
    monkeypatch.delenv("APP_ID", raising=False)
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SECRET_PATH", raising=False)
    status, body = handle_ready()
    assert (status, body["status"]) == (503, "misconfigured")  # nosec B101
