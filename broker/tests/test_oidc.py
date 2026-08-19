"""Direct tests for app/oidc.py: JWKS fetch, TTL cache, kid-rotation, and error paths."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.oidc import JwksResolver, JwksUnavailable, OidcError, _jwks_cache, verify_oidc_token
from app.config import GITHUB_OIDC_ISSUER


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def public_pem(rsa_key):
    return rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


@pytest.fixture(autouse=True)
def _reset_cache():
    _jwks_cache["keys"] = None
    _jwks_cache["fetched_at"] = 0.0
    yield
    _jwks_cache["keys"] = None
    _jwks_cache["fetched_at"] = 0.0


def _make_token(key, *, audience: str = "brimyr", exp_offset: int = 300, **extra) -> str:
    now = int(time.time())
    payload = {
        "iss": GITHUB_OIDC_ISSUER,
        "aud": audience,
        "repository": "MagmaMoose/brimyr",
        "iat": now - 10,
        "exp": now + exp_offset,
    }
    payload.update(extra)
    return jwt.encode(payload, key, algorithm="RS256")


def _fake_jwks_transport(key) -> httpx.MockTransport:
    """Return a transport that serves a one-key JWKS for the given RSA private key."""
    public_numbers = key.public_key().public_numbers()

    def _b64url(n: int) -> str:
        import base64
        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    jwks_payload = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-kid-1",
                "use": "sig",
                "alg": "RS256",
                "n": _b64url(public_numbers.n),
                "e": _b64url(public_numbers.e),
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_payload)

    return httpx.MockTransport(handler)


def _key_with_kid(key, kid: str):
    """Wrap a raw RSA key so JwksResolver can return it."""
    return type("_Key", (), {"key": key.public_key(), "key_id": kid})()


class _StaticJwksResolver:
    def __init__(self, key, kid: str = "test-kid-1") -> None:
        self._wrapped = _key_with_kid(key, kid)

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        return self._wrapped


async def test_verify_oidc_token_accepts_a_valid_token(rsa_key):
    token = _make_token(rsa_key)
    claims = await verify_oidc_token(token, "brimyr", key_resolver=_StaticJwksResolver(rsa_key))
    assert claims["repository"] == "MagmaMoose/brimyr"  # nosec B101
    assert claims["iss"] == GITHUB_OIDC_ISSUER  # nosec B101


async def test_verify_oidc_token_rejects_wrong_audience(rsa_key):
    token = _make_token(rsa_key, audience="other-service")
    with pytest.raises(OidcError):
        await verify_oidc_token(token, "brimyr", key_resolver=_StaticJwksResolver(rsa_key))


async def test_verify_oidc_token_rejects_expired_token(rsa_key):
    token = _make_token(rsa_key, exp_offset=-60)
    with pytest.raises(OidcError):
        await verify_oidc_token(token, "brimyr", key_resolver=_StaticJwksResolver(rsa_key))


async def test_verify_oidc_token_rejects_wrong_issuer(rsa_key):
    now = int(time.time())
    payload = {"iss": "https://evil.example.com", "aud": "brimyr", "iat": now, "exp": now + 300}
    token = jwt.encode(payload, rsa_key, algorithm="RS256")
    with pytest.raises(OidcError):
        await verify_oidc_token(token, "brimyr", key_resolver=_StaticJwksResolver(rsa_key))


async def test_verify_oidc_token_raises_when_no_resolver_and_no_client():
    with pytest.raises(OidcError, match="no key resolver"):
        await verify_oidc_token("x.y.z", "brimyr")


async def test_jwks_resolver_fetches_key_from_transport(rsa_key):
    token = _make_token(rsa_key)
    # Patch get_unverified_header to return our kid
    header_patch = {"kid": "test-kid-1", "alg": "RS256"}
    with patch("app.oidc.jwt.get_unverified_header", return_value=header_patch):
        async with httpx.AsyncClient(transport=_fake_jwks_transport(rsa_key)) as client:
            resolver = JwksResolver(client)
            key = await resolver.get_signing_key_from_jwt(token)
    assert key is not None  # nosec B101


async def test_jwks_resolver_force_refetches_on_unknown_kid(rsa_key):
    """An unknown kid triggers a forced re-fetch (key rotation path)."""
    token = _make_token(rsa_key)
    fetch_count = {"n": 0}

    async def counting_fetch(_client):
        fetch_count["n"] += 1
        # A valid-structured JWKS with a key whose kid won't match "rotated-kid".
        numbers = rsa_key.public_key().public_numbers()

        def _b64url(n: int) -> str:
            import base64
            length = (n.bit_length() + 7) // 8
            return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

        return jwt.PyJWKSet.from_dict({
            "keys": [{"kty": "RSA", "kid": "other-kid", "use": "sig", "alg": "RS256",
                      "n": _b64url(numbers.n), "e": _b64url(numbers.e)}]
        })

    with (
        patch("app.oidc._fetch_jwks", side_effect=counting_fetch),
        patch("app.oidc.jwt.get_unverified_header", return_value={"kid": "rotated-kid"}),
    ):
        async with httpx.AsyncClient() as client:
            resolver = JwksResolver(client)
            with pytest.raises(OidcError, match="no signing key"):
                await resolver.get_signing_key_from_jwt(token)

    assert fetch_count["n"] == 2  # nosec B101


async def test_jwks_unavailable_raised_on_http_error(rsa_key):
    """A network failure maps to JwksUnavailable so the caller returns 503, not 401."""
    token = _make_token(rsa_key)

    async def failing_fetch(_client):
        raise httpx.ConnectError("connection refused")

    with (
        patch("app.oidc._fetch_jwks", side_effect=failing_fetch),
        patch("app.oidc.jwt.get_unverified_header", return_value={"kid": "k1"}),
    ):
        async with httpx.AsyncClient() as client:
            with pytest.raises(JwksUnavailable):
                await verify_oidc_token(token, "brimyr", client=client)


async def test_jwks_cache_is_reused_within_ttl(rsa_key):
    fetch_count = {"n": 0}
    numbers = rsa_key.public_key().public_numbers()

    def _b64url(n: int) -> str:
        import base64
        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    async def counting_fetch(_client):
        fetch_count["n"] += 1
        return jwt.PyJWKSet.from_dict({
            "keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                      "n": _b64url(numbers.n), "e": _b64url(numbers.e)}]
        })

    with (
        patch("app.oidc._fetch_jwks", side_effect=counting_fetch),
        patch("app.oidc.time.time", return_value=1_000_000.0),
    ):
        async with httpx.AsyncClient() as client:
            from app.oidc import _jwks
            await _jwks(client)
            await _jwks(client)

    assert fetch_count["n"] == 1  # nosec B101
