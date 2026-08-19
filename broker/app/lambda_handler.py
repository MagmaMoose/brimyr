"""AWS Lambda entrypoint, behind an API Gateway HTTP API (payload format 2.0).

The whole file is transport: pull the method, path and body out of the event, hand
them to :mod:`app.broker`, and wrap the ``(status, body)`` it returns. No decision
lives here, which is what makes the Lambda and the local FastAPI shell the same code
path rather than two implementations that agree until they don't.

**Payload format 2.0 specifically.** ``rawPath`` is the real path with no stage prefix
because the API uses the ``$default`` stage; a named stage would put ``/prod`` in
front of every route and every request would 404 against the table below. Format 2.0
is also byte-for-byte what a Lambda function URL delivers, so a local harness can
exercise this handler without an API Gateway in front of it.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from app.broker import handle_health, handle_ready, handle_token

#: Kept in one place so ``405`` can be distinguished from ``404`` — FastAPI supplied
#: that distinction implicitly and losing it would turn "you used the wrong verb" into
#: "that endpoint does not exist", which is a much longer debugging session.
_ROUTES = frozenset({"/healthz", "/readyz", "/token"})


def _body(event: dict[str, Any]) -> bytes:
    """The request body as bytes, honouring ``isBase64Encoded``.

    API Gateway base64-encodes a body it considers binary, which it decides from the
    content type. A client that omits ``Content-Type: application/json`` therefore
    arrives base64-encoded, and skipping this decode makes ``/token`` answer
    ``400 invalid_json`` for a perfectly well-formed request — with the caller's own
    payload visible in no log, because logging it would be the injection sink the
    broker deliberately avoids.
    """
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode() if isinstance(raw, str) else bytes(raw)


def _respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Route one API Gateway request. The name the Terraform ``handler`` points at."""
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "").upper()
    # Strip a trailing slash so /token/ and /token are the same endpoint; a consumer
    # that built its URL by joining paths should not get a 404 for a cosmetic
    # difference it cannot see.
    path = (event.get("rawPath") or "").rstrip("/") or "/"

    if path not in _ROUTES:
        return _respond(404, {"error": "not_found"})

    if path == "/healthz":
        if method != "GET":
            return _respond(405, {"error": "method_not_allowed"})
        return _respond(*handle_health())

    if path == "/readyz":
        if method != "GET":
            return _respond(405, {"error": "method_not_allowed"})
        return _respond(*handle_ready())

    # /token
    if method != "POST":
        return _respond(405, {"error": "method_not_allowed"})

    # asyncio.run rather than a persisted loop: one request per invocation, and a
    # loop cached across invocations in a reused execution environment is how you get
    # "attached to a different loop" errors from httpx's connection pool.
    status, body = asyncio.run(handle_token(_body(event)))
    return _respond(status, body)
