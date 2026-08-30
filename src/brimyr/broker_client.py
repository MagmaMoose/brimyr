"""Exchange an Actions OIDC token for a ``Brimyr[bot]`` installation token.

The other half of the token broker (``broker/``). The broker mints; this asks. Without
it the broker has nothing to author and the comment posts as ``github-actions[bot]``.

The flow, all inside one job:

1. Ask the Actions runtime for an OIDC token with ``aud=brimyr``. Only possible when the
   job declares ``id-token: write``; there is no way to forge one, which is the whole
   point of the design.
2. ``POST`` it to the broker with the repo being commented on.
3. The broker verifies the signature, the issuer, the audience, and that the token's own
   ``repository`` claim matches the repo asked for, then mints a token scoped to that
   repo with ``pull_requests: write`` and nothing else.

**FAILING SOFT IS THE CONTRACT, and it is deliberately silent.** Every failure path here
returns ``token=None`` and the caller falls back to ``GITHUB_TOKEN``. The comment still
posts, the gate verdict is untouched, and the only visible symptom is the byline reverting
to ``github-actions[bot]``. That is the right trade — a broken broker must never cost
anyone a merge — but it means nothing goes red when this stops working, which is why
``.github/workflows/broker-smoke.yml`` exists and is not optional.

**The minted token never leaves this process.** It is passed straight to
:mod:`brimyr.github_comment` as a bearer credential. It is never printed, never written to
a step output, never put in the job summary, and never logged — not even truncated. The
only thing that reaches a log here is a failure *reason*.

Stdlib ``urllib`` only, like the rest of the package; the opener is injected so the tests
need no network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from brimyr import __version__
from brimyr import http as brimyr_http

#: Must match ``oidc_audience`` on the deployed broker. The audience is the boundary that
#: stops chargate's runners minting brimyr's identity and vice versa — the two services
#: run separate brokers, separate Apps and separate audiences on purpose.
OIDC_AUDIENCE = "brimyr"

_USER_AGENT = f"brimyr/{__version__} (+https://github.com/MagmaMoose/brimyr)"
_TIMEOUT = 15.0

#: The broker's complete error vocabulary (``app.broker`` + ``app.lambda_handler``). Only
#: these strings are ever echoed into a message. An exception's own text is NEVER
#: interpolated: a transport error can carry the request URL, and the OIDC token travels
#: in the body of that request — a caught test (`test_a_failure_message_never_leaks_the_
#: oidc_token`) proved the naive `f"...: {exc}"` form leaks it straight to stderr, since
#: the caller prints this message. Same reasoning as the broker's own refusal to log
#: request-derived strings, from the other end of the wire.
_BROKER_ERRORS = frozenset(
    {
        "invalid_json",
        "missing_fields",
        "invalid_repository",
        "config_unavailable",
        "repo_not_allowed",
        "jwks_unavailable",
        "invalid_oidc",
        "repo_mismatch",
        "app_not_installed",
        "mint_failed",
        "not_found",
        "method_not_allowed",
    }
)


@dataclass(frozen=True)
class BrokerResult:
    """Outcome of a mint attempt. ``token is None`` always means "fall back"."""

    token: str | None
    message: str

    @property
    def ok(self) -> bool:
        return self.token is not None


def _get_json(
    url: str,
    *,
    bearer: str,
    payload: Any | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Authorization", f"Bearer {bearer}")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", _USER_AGENT)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    active = opener or brimyr_http.build_opener()
    with active.open(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def request_actions_oidc_token(
    audience: str = OIDC_AUDIENCE,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> str | None:
    """The job's own OIDC token, or ``None`` when the runtime will not issue one.

    ``ACTIONS_ID_TOKEN_REQUEST_URL`` / ``_TOKEN`` are injected by the runner **only** when
    the job declares ``permissions: id-token: write``. Absent means exactly that
    permission is missing — by far the most common reason a byline silently reverts — so
    the caller reports it as its own message rather than a generic failure.
    """
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not (url and request_token):
        return None

    # The runner's URL already carries `?api-version=...`, so the audience is appended
    # with `&`. Percent-encoded because it lands in a query string.
    separator = "&" if "?" in url else "?"
    try:
        body = _get_json(
            f"{url}{separator}audience={quote(audience, safe='')}",
            bearer=request_token,
            opener=opener,
        )
    except (urllib.error.URLError, OSError, ValueError):
        return None
    value = body.get("value") if isinstance(body, dict) else None
    return value if isinstance(value, str) and value else None


def mint_bot_token(
    broker_url: str,
    owner: str,
    repo: str,
    *,
    audience: str = OIDC_AUDIENCE,
    opener: urllib.request.OpenerDirector | None = None,
    oidc_token: str | None = None,
) -> BrokerResult:
    """Mint a ``Brimyr[bot]`` token for ``owner/repo``. Never raises.

    ``oidc_token`` is the test seam; production leaves it ``None`` and the token is
    requested from the Actions runtime.
    """
    if not broker_url:
        return BrokerResult(None, "no broker configured")
    if not (owner and repo):
        return BrokerResult(None, "broker needs owner and repo")

    token = oidc_token or request_actions_oidc_token(audience, opener=opener)
    if not token:
        return BrokerResult(
            None,
            "no Actions OIDC token — does the job declare `permissions: id-token: write`?",
        )

    try:
        body = _get_json(
            f"{broker_url.rstrip('/')}/token",
            bearer=token,
            payload={"oidcToken": token, "owner": owner, "repo": repo},
            opener=opener,
        )
    except urllib.error.HTTPError as exc:
        # The broker's error codes are a fixed vocabulary (app.broker), so the body is
        # safe to surface and is the single most useful thing an operator can see:
        # `app_not_installed` and `repo_mismatch` are entirely different fixes.
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8", errors="replace"))
            if isinstance(parsed, dict):
                candidate = str(parsed.get("error", ""))
                # Whitelisted, not merely parsed. A broker that echoed the request back
                # would otherwise put the OIDC token into this message.
                detail = candidate if candidate in _BROKER_ERRORS else ""
        except (ValueError, OSError):
            detail = ""
        return BrokerResult(None, f"broker returned {exc.code}{f' ({detail})' if detail else ''}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Class name only — see _BROKER_ERRORS. The name is the useful field anyway:
        # URLError vs SSLError vs TimeoutError names the fault an operator is chasing.
        return BrokerResult(None, f"broker unreachable ({type(exc).__name__})")

    minted = body.get("token") if isinstance(body, dict) else None
    if not isinstance(minted, str) or not minted:
        return BrokerResult(None, "broker returned no token")
    return BrokerResult(minted, "minted a Brimyr[bot] token")
