#!/usr/bin/env python3
"""End-to-end smoke test against the broker deployed on LocalStack.

Drives the real API Gateway -> Lambda path with the real built zip. Every assertion here
is something a unit test cannot make: that the package imports on the runtime image, that
the SSM/IAM/KMS wiring actually resolves the App identity, and that payload format 2.0
routing behaves the way ``app.lambda_handler`` assumes.

Deliberately NOT asserted: a successful mint. That needs a token signed by GitHub's
Actions OIDC key and a Brimyr App installed on the repo, and LocalStack emulates AWS, not
GitHub. The issuer pin in ``app/config.py`` is not overridable on purpose — see the note
in ``main.tf``. ``/token`` is therefore driven to ``invalid_oidc``, which still proves the
whole request path: routing, body decode, field validation, config resolution from SSM,
and the evaluation order the ladder fixes.

Usage:  ./smoke.py <api-endpoint>          e.g. ./smoke.py "$(terraform output -raw api_endpoint)"
Exit 0 = every check passed. Exit 1 = a failure, printed with what was expected.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

_TIMEOUT = 30.0


def _call(
    endpoint: str, path: str, *, method: str = "GET", body: Any = None, raw: str | None = None
) -> tuple[int, dict[str, Any]]:
    """One request. Returns ``(status, parsed_body)``; HTTP errors are results, not raises."""
    url = f"{endpoint.rstrip('/')}{path}"
    data = None
    if raw is not None:
        data = raw.encode()
    elif body is not None:
        data = json.dumps(body).encode()

    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            status, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read().decode()

    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = {"_raw": payload}
    return status, parsed if isinstance(parsed, dict) else {"_raw": payload}


class _Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def expect(self, label: str, got: Any, want: Any) -> None:
        if got == want:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}: got {got!r}, want {want!r}")
            self.failures.append(label)


def main(endpoint: str) -> int:
    print(f"Smoke-testing the brimyr token broker at {endpoint}\n")
    checks = _Checks()

    # The zip imports and the function cold-starts. Weak on purpose (handle_health answers
    # 200 with no config at all) — it proves the package, nothing more.
    print("Liveness")
    status, body = _call(endpoint, "/healthz")
    checks.expect("GET /healthz -> 200", status, 200)
    checks.expect("GET /healthz body", body.get("status"), "ok")

    # The one that actually exercises SSM + IAM + KMS. 503 here means the parameters are
    # not seeded, or the policy/ViaService condition is wrong.
    print("\nReadiness (resolves APP_ID + PRIVATE_KEY through SSM)")
    status, body = _call(endpoint, "/readyz")
    checks.expect("GET /readyz -> 200", status, 200)
    if status == 503:
        print("        ^ run ./seed.sh first, or the IAM/KMS grant is wrong")

    # Routing through payload format 2.0, including the 404/405 distinction the handler
    # keeps and API Gateway would otherwise collapse.
    print("\nRouting")
    status, body = _call(endpoint, "/nope")
    checks.expect("GET /nope -> 404", status, 404)
    checks.expect("GET /nope body", body.get("error"), "not_found")

    status, body = _call(endpoint, "/healthz", method="POST", body={})
    checks.expect("POST /healthz -> 405", status, 405)
    checks.expect("POST /healthz body", body.get("error"), "method_not_allowed")

    status, _ = _call(endpoint, "/token", method="GET")
    checks.expect("GET /token -> 405", status, 405)

    # Trailing slash is cosmetic; a consumer that joined paths should not get a 404.
    status, _ = _call(endpoint, "/healthz/")
    checks.expect("GET /healthz/ -> 200 (trailing slash)", status, 200)

    # The /token ladder, in the order app.broker fixes. A malformed request is answered
    # before configuration is consulted, so these hold even on an unseeded broker.
    print("\n/token error ladder")
    status, body = _call(endpoint, "/token", method="POST", raw="not json")
    checks.expect("malformed body -> 400", status, 400)
    checks.expect("malformed body -> invalid_json", body.get("error"), "invalid_json")

    status, body = _call(endpoint, "/token", method="POST", body={"owner": "MagmaMoose"})
    checks.expect("missing fields -> 400", status, 400)
    checks.expect("missing fields -> missing_fields", body.get("error"), "missing_fields")

    # A path separator in `repo` is the py/partial-ssrf primitive validate_repository
    # exists to stop. It must be rejected before anything reaches a URL.
    status, body = _call(
        endpoint,
        "/token",
        method="POST",
        body={"oidcToken": "x", "owner": "MagmaMoose", "repo": "../../etc/passwd"},
    )
    checks.expect("path traversal in repo -> 400", status, 400)
    checks.expect("path traversal -> invalid_repository", body.get("error"), "invalid_repository")

    # A syntactically fine but unsigned token. Reaching invalid_oidc means the request
    # traversed routing, decode, validation and config resolution — everything but the
    # part that needs GitHub.
    status, body = _call(
        endpoint,
        "/token",
        method="POST",
        body={"oidcToken": "not.a.real.token", "owner": "MagmaMoose", "repo": "brimyr"},
    )
    checks.expect("unsigned OIDC token -> 401", status, 401)
    checks.expect("unsigned OIDC token -> invalid_oidc", body.get("error"), "invalid_oidc")

    print()
    if checks.failures:
        print(f"FAILED: {len(checks.failures)} check(s) — {', '.join(checks.failures)}")
        return 1
    print("All checks passed.")
    print(
        "NOT proven here (and not provable locally): a successful mint, the stage "
        "throttle, and disable_execute_api_endpoint. See main.tf."
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
