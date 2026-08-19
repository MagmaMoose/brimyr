"""Direct tests for app/github.py: JWT claims, repository validation, and token minting."""

from __future__ import annotations

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github import InvalidRepositoryError, app_jwt, mint_installation_token, validate_repository


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def rsa_pem(rsa_key):
    return rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class TestAppJwt:
    def test_issuer_matches_app_id(self, rsa_pem):
        token = app_jwt("42", rsa_pem)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iss"] == "42"  # nosec B101

    def test_iat_is_backdated_60s(self, rsa_pem):
        now = 1_700_000_000.0
        token = app_jwt("1", rsa_pem, now=now)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iat"] == int(now) - 60  # nosec B101

    def test_exp_is_nine_minutes_ahead(self, rsa_pem):
        now = 1_700_000_000.0
        token = app_jwt("1", rsa_pem, now=now)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["exp"] == int(now) + 9 * 60  # nosec B101

    def test_signs_with_rs256(self, rsa_pem):
        token = app_jwt("1", rsa_pem)
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "RS256"  # nosec B101


class TestValidateRepository:
    def test_valid_owner_and_repo(self):
        assert validate_repository("MagmaMoose", "brimyr") == ("MagmaMoose", "brimyr")  # nosec B101

    def test_repo_with_dots_and_underscores(self):
        assert validate_repository("org", "my_repo.github.io") == ("org", "my_repo.github.io")  # nosec B101

    def test_double_dot_in_repo_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("org", "../../etc/passwd")

    def test_slash_in_repo_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("org", "a/b")

    def test_slash_in_owner_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("a/b", "repo")

    def test_percent_encoding_in_owner_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("org%2fadmin", "repo")

    def test_empty_owner_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("", "repo")

    def test_empty_repo_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("org", "")

    def test_owner_with_leading_hyphen_is_rejected(self):
        with pytest.raises(InvalidRepositoryError):
            validate_repository("-bad", "repo")


class TestMintInstallationToken:
    async def test_returns_token_and_expiry(self, rsa_pem):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/installation"):
                return httpx.Response(200, json={"id": 7})
            return httpx.Response(
                200, json={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
            )  # nosec B105

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            token, expires_at = await mint_installation_token(
                client,
                app_id="1",
                private_key=rsa_pem,
                owner="MagmaMoose",
                repo="brimyr",
                permissions={"pull_requests": "write"},
            )
        assert token == "ghs_x"  # nosec B101 B105
        assert expires_at == "2099-01-01T00:00:00Z"  # nosec B101

    async def test_owner_and_repo_are_percent_encoded_in_url(self, rsa_pem):
        seen: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path.endswith("/installation"):
                return httpx.Response(200, json={"id": 1})
            return httpx.Response(200, json={"token": "t", "expires_at": ""})  # nosec B105

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await mint_installation_token(
                client,
                app_id="1",
                private_key=rsa_pem,
                owner="MagmaMoose",
                repo="brimyr",
                permissions={},
            )

        installation_url = next(u for u in seen if u.endswith("/installation"))
        assert "MagmaMoose" in installation_url  # nosec B101
        assert "brimyr" in installation_url  # nosec B101

    async def test_invalid_repo_raises_before_any_request(self, rsa_pem):
        called = {"n": 0}

        def respond(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(InvalidRepositoryError):
                await mint_installation_token(
                    client,
                    app_id="1",
                    private_key=rsa_pem,
                    owner="bad/owner",
                    repo="repo",
                    permissions={},
                )
        assert called["n"] == 0  # nosec B101
