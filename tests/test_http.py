"""Credentials must not survive a cross-origin redirect (brimyr.http).

`urllib`'s default redirect handler replays every header onto the redirect target,
`Authorization` included, so a redirect to another host is handed the bearer token: a
GitHub installation token with `pull_requests: write`, or the OIDC token the broker
exchanges. Both clients talk to a host the consumer configures (`--github-api-url`,
`token_broker_url`), so a misconfigured or hostile value is the whole attack.
"""

from __future__ import annotations

import urllib.request

from brimyr.http import _SameOriginRedirectHandler, build_opener


def _redirect(from_url: str, to_url: str) -> urllib.request.Request:
    req = urllib.request.Request(from_url)
    req.add_header("Authorization", "Bearer super-secret")
    req.add_header("Accept", "application/json")
    handler = _SameOriginRedirectHandler()
    return handler.redirect_request(req, None, 302, "Found", {}, to_url)


def _headers(req) -> dict:
    merged = dict(req.headers)
    merged.update(req.unredirected_hdrs)
    return {k.lower(): v for k, v in merged.items()}


def test_authorization_is_dropped_when_the_host_changes():
    new = _redirect("https://api.github.com/repos/o/r", "https://evil.example/x")
    assert "authorization" not in _headers(new)  # nosec B101
    assert "accept" in _headers(new)  # nosec B101 - only credentials are stripped


def test_authorization_is_dropped_when_the_scheme_changes():
    # The http:// target IS the test: a scheme downgrade must drop the credential.
    downgraded = "http://api.example.com/a"  # DevSkim: ignore DS137138
    new = _redirect("https://api.example.com/a", downgraded)
    assert "authorization" not in _headers(new)  # nosec B101


def test_authorization_is_dropped_when_the_port_changes():
    new = _redirect("https://api.example.com/a", "https://api.example.com:8443/a")
    assert "authorization" not in _headers(new)  # nosec B101


def test_authorization_survives_a_same_origin_redirect():
    """A plain path redirect on the same host is normal and must keep working."""
    new = _redirect("https://api.github.com/a", "https://api.github.com/b")
    assert _headers(new)["authorization"] == "Bearer super-secret"  # nosec B101


def test_build_opener_installs_the_handler():
    opener = build_opener()
    assert any(  # nosec B101
        isinstance(h, _SameOriginRedirectHandler) for h in opener.handlers
    )
