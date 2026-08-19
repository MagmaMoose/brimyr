"""Mint a short-lived, repo-scoped GitHub App installation token.

Signs an App JWT (RS256) with the Brimyr App key, resolves the repo's
installation, then mints a token scoped to **just that repo** with **just** the
requested permissions. The ``httpx`` client is injected so tests can mock GitHub.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt

_API_VERSION = "2022-11-28"

# GitHub owner (user/org) and repo naming rules, tightened to what can never alter
# the shape of a URL path: no '/', no '%', no ':'. Owners are alphanumeric with
# single hyphens. Repo names allow '_' and '.' (e.g. foo.github.io), but '..' is
# the path-traversal primitive — reject that sequence specifically, not all dots.
_OWNER_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
_REPO_RE = re.compile(r"\A(?!.*\.\.)[A-Za-z0-9_.-]{1,100}\Z")


class InvalidRepositoryError(ValueError):
    """``owner`` or ``repo`` is not a syntactically valid GitHub identifier."""


def validate_repository(owner: str, repo: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` iff both are safe to interpolate into an API path.

    The caller already pins these against the signed OIDC ``repository`` claim, so
    this is defence-in-depth: it guarantees that no value reaching an f-string URL
    can contain a path separator, a traversal sequence, or percent-encoding, which
    is what makes the request URL forgeable (CodeQL ``py/partial-ssrf``). Validating
    here rather than at the call site keeps the guarantee attached to the function
    that builds the URLs.
    """
    if not _OWNER_RE.match(owner or ""):
        raise InvalidRepositoryError(f"invalid owner: {owner!r}")
    if not _REPO_RE.match(repo or ""):
        raise InvalidRepositoryError(f"invalid repo: {repo!r}")
    return owner, repo


def app_jwt(app_id: str, private_key: str, *, now: float | None = None) -> str:
    """A ~9-minute App JWT (GitHub allows max 10), back-dated 60s for clock skew."""
    epoch = int(now if now is not None else time.time())
    payload = {"iat": epoch - 60, "exp": epoch + 9 * 60, "iss": str(app_id)}
    return jwt.encode(payload, private_key, algorithm="RS256")


async def mint_installation_token(
    client: httpx.AsyncClient,
    *,
    app_id: str,
    private_key: str,
    owner: str,
    repo: str,
    permissions: dict[str, Any],
    api_url: str = "https://api.github.com",
) -> tuple[str, str]:
    """Return ``(token, expires_at)`` scoped to ``owner/repo``.

    Raises :class:`InvalidRepositoryError` if ``owner``/``repo`` are not valid
    GitHub identifiers, or ``httpx.HTTPStatusError`` on an API error.
    """
    owner, repo = validate_repository(owner, repo)
    headers = {
        "Authorization": f"Bearer {app_jwt(app_id, private_key)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    base = api_url.rstrip("/")
    # Percent-encode with an EMPTY safe set, so `/`, `.`, `%` and `?` cannot survive into
    # the path even if validate_repository were bypassed or loosened. On a value that
    # passed validation this is a no-op — the point is that the request URL is safe by
    # construction at the interpolation site, not merely because a check ran earlier
    # (CodeQL py/partial-ssrf). httpx does not re-encode an already-encoded path segment.
    owner_seg = quote(owner, safe="")
    repo_seg = quote(repo, safe="")
    installation = await client.get(
        f"{base}/repos/{owner_seg}/{repo_seg}/installation", headers=headers
    )
    installation.raise_for_status()
    installation_id = installation.json()["id"]

    minted = await client.post(
        f"{base}/app/installations/{installation_id}/access_tokens",
        headers=headers,
        json={"repositories": [repo], "permissions": permissions},
    )
    minted.raise_for_status()
    body = minted.json()
    return body["token"], body.get("expires_at", "")
