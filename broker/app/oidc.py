"""Verify a GitHub Actions OIDC token: RS256, issuer-pinned, audience-checked.

The signing key comes from GitHub's JWKS, fetched with ``httpx`` rather than
:class:`jwt.PyJWKClient`. ``PyJWKClient`` fetches over ``urllib.request.urlopen``, a
blocking socket call in an otherwise async path; chargate's broker replaced it outright
when the service ran on a Cloudflare Worker, which has no blocking sockets, and there is
no reason to reintroduce it here.

``key_resolver`` stays injectable so tests supply a local public key instead of
reaching GitHub; it may be sync or async (see :func:`_resolve`).
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Protocol

import httpx
import jwt

from app.config import GITHUB_OIDC_ISSUER, GITHUB_OIDC_JWKS

#: How long a fetched JWKS is trusted before it is re-fetched. GitHub rotates its
#: Actions signing keys rarely and without notice; a rotation is handled by the
#: cache-miss path below rather than by this TTL, which only bounds staleness.
_JWKS_TTL_SECONDS = 3600

#: Module-level so a warm execution environment reuses it across invocations. A cold
#: start simply re-fetches, which is one extra request on that environment's first
#: token — the same trade ``app.ssm`` makes for Parameter Store.
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


class KeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class OidcError(Exception):
    """The OIDC token failed verification (bad signature, issuer, audience, …)."""


class JwksUnavailable(OidcError):
    """GitHub's JWKS could not be fetched, so the token could not be checked.

    Distinct from :class:`OidcError` because the caller's token may be perfectly
    valid — answering 401 here would send a runner off debugging its own identity
    while the real fault is upstream.
    """


async def _fetch_jwks(client: httpx.AsyncClient) -> jwt.PyJWKSet:
    response = await client.get(GITHUB_OIDC_JWKS, timeout=10.0)
    response.raise_for_status()
    return jwt.PyJWKSet.from_dict(response.json())


async def _jwks(client: httpx.AsyncClient, *, force: bool = False) -> jwt.PyJWKSet:
    fresh = time.time() - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS
    if not force and _jwks_cache["keys"] is not None and fresh:
        return _jwks_cache["keys"]
    key_set = await _fetch_jwks(client)
    _jwks_cache["keys"] = key_set
    _jwks_cache["fetched_at"] = time.time()
    return key_set


class JwksResolver:
    """Resolves an OIDC token's signing key from GitHub's published JWKS."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_signing_key_from_jwt(self, token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise OidcError(f"malformed token header: {exc}") from exc
        if not kid:
            raise OidcError("token header has no kid")

        key_set = await _jwks(self._client)
        signing_key = _by_kid(key_set, kid)
        if signing_key is None:
            # Either GitHub rotated its keys or this execution environment cached a set
            # from before a rotation. One forced re-fetch distinguishes the two; if the
            # kid is still absent the token was not signed by GitHub.
            key_set = await _jwks(self._client, force=True)
            signing_key = _by_kid(key_set, kid)
        if signing_key is None:
            raise OidcError(f"no signing key for kid {kid!r}")
        return signing_key


def _by_kid(key_set: jwt.PyJWKSet, kid: str) -> Any | None:
    for key in key_set.keys:
        if key.key_id == kid:
            return key
    return None


async def _resolve(resolver: Any, token: str) -> Any:
    """Call a resolver that may be sync or async.

    The production resolver awaits a network fetch; test doubles are a plain
    function returning a fixed key. Supporting both keeps the seam ergonomic
    rather than forcing every fake to be a coroutine.
    """
    result = resolver.get_signing_key_from_jwt(token)
    if inspect.isawaitable(result):
        result = await result
    return result


async def verify_oidc_token(
    token: str,
    audience: str,
    *,
    client: httpx.AsyncClient | None = None,
    key_resolver: KeyResolver | None = None,
) -> dict[str, Any]:
    """Return the verified claims, or raise :class:`OidcError`."""
    if key_resolver is None:
        if client is None:
            raise OidcError("no key resolver and no http client to fetch JWKS with")
        key_resolver = JwksResolver(client)

    try:
        signing_key = await _resolve(key_resolver, token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=GITHUB_OIDC_ISSUER,
        )
    except OidcError:
        raise
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError) as exc:
        raise OidcError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise JwksUnavailable(f"could not fetch JWKS: {exc}") from exc
    return claims
