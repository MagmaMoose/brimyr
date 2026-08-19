"""The API Gateway (payload format 2.0) integration surface.

Synthetic events rather than a test client: what runs in production is this handler
being handed a dict by the Lambda runtime, and a client that builds its own request
would not reproduce the two things most likely to break — `isBase64Encoded` and the
`requestContext.http.method` nesting.
"""

from __future__ import annotations

import base64
import json

import pytest

from app import ssm
from app.lambda_handler import handler


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    """No secrets anywhere, so /token reaches the config check and stops there."""
    monkeypatch.delenv("SECRET_PATH", raising=False)
    monkeypatch.delenv("APP_ID", raising=False)
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    ssm.reset_cache()
    yield
    ssm.reset_cache()


def _event(method: str, path: str, *, body=None, base64_encoded=False) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": body,
        "isBase64Encoded": base64_encoded,
    }


def _body(response: dict) -> dict:
    return json.loads(response["body"])


def test_healthz_answers_with_no_configuration_at_all():
    """The first deploy has no SSM parameters yet and must still come up."""
    response = handler(_event("GET", "/healthz"))
    assert response["statusCode"] == 200  # nosec B101
    assert _body(response) == {"status": "ok"}  # nosec B101
    assert response["headers"]["content-type"] == "application/json"  # nosec B101


def test_readyz_reports_misconfigured_without_secrets():
    response = handler(_event("GET", "/readyz"))
    assert response["statusCode"] == 503  # nosec B101
    assert _body(response)["status"] == "misconfigured"  # nosec B101


def test_readyz_ok_when_configured(monkeypatch):
    monkeypatch.setenv("APP_ID", "123")
    monkeypatch.setenv("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n")
    response = handler(_event("GET", "/readyz"))
    assert response["statusCode"] == 200  # nosec B101


def test_token_rejects_non_json_before_consulting_config():
    """A malformed request is the caller's fault even on an unfinished deployment."""
    response = handler(_event("POST", "/token", body="not json"))
    assert response["statusCode"] == 400  # nosec B101
    assert _body(response)["error"] == "invalid_json"  # nosec B101


def test_token_missing_fields_is_400():
    response = handler(_event("POST", "/token", body=json.dumps({"owner": "org"})))
    assert response["statusCode"] == 400  # nosec B101
    assert _body(response)["error"] == "missing_fields"  # nosec B101


def test_token_unconfigured_is_503():
    payload = json.dumps({"oidcToken": "x", "owner": "org", "repo": "repo"})
    response = handler(_event("POST", "/token", body=payload))
    assert response["statusCode"] == 503  # nosec B101
    assert _body(response)["error"] == "config_unavailable"  # nosec B101


def test_base64_encoded_body_is_decoded():
    """API Gateway base64-encodes a body it considers binary — i.e. any request whose
    Content-Type is not recognised as text. Skipping the decode makes a perfectly
    well-formed call answer 400 invalid_json."""
    payload = json.dumps({"owner": "org"})
    encoded = base64.b64encode(payload.encode()).decode()
    response = handler(_event("POST", "/token", body=encoded, base64_encoded=True))
    # Reached the field check, which means the JSON parsed — the point of the test.
    assert response["statusCode"] == 400  # nosec B101
    assert _body(response)["error"] == "missing_fields"  # nosec B101


def test_unknown_path_is_404():
    response = handler(_event("GET", "/nope"))
    assert response["statusCode"] == 404  # nosec B101
    assert _body(response)["error"] == "not_found"  # nosec B101


def test_known_path_wrong_method_is_405():
    """FastAPI supplied this distinction implicitly; losing it would turn 'wrong verb'
    into 'no such endpoint', which is a much longer debugging session."""
    response = handler(_event("GET", "/token"))
    assert response["statusCode"] == 405  # nosec B101
    assert _body(response)["error"] == "method_not_allowed"  # nosec B101

    response = handler(_event("POST", "/healthz"))
    assert response["statusCode"] == 405  # nosec B101


def test_trailing_slash_is_the_same_endpoint():
    assert handler(_event("GET", "/healthz/"))["statusCode"] == 200  # nosec B101


def test_missing_body_is_treated_as_empty():
    response = handler(_event("POST", "/token"))
    assert response["statusCode"] == 400  # nosec B101
    assert _body(response)["error"] == "invalid_json"  # nosec B101
