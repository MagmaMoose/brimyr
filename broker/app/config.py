"""Broker configuration — the Brimyr GitHub App identity + OIDC policy.

Two runtimes, one settings class:

* **AWS Lambda** (production) — non-secret values arrive as function environment
  variables; ``APP_ID`` and ``PRIVATE_KEY`` come from SSM Parameter Store under
  ``SECRET_PATH`` and are overlaid on top of ``os.environ`` by :func:`load_config`.
* **Local / CI** — everything is in the process environment, ``SECRET_PATH`` is
  unset, the overlay is empty, and nothing imports boto3 at all.

**No pydantic**, deliberately: ``pydantic`` + ``pydantic_core`` is ~5 MB of the
Lambda zip and the dominant cold-start term, to validate six strings. A frozen
dataclass does the same job for nothing. Ported from chargate's broker, which
reached that conclusion by removing pydantic rather than never adding it.

The App private key arrives as a PEM string; secret stores commonly ``\\n``-escape
it, so we un-escape (mirrors caldrith's ``settings`` normalizer). PKCS#1 (the
``BEGIN RSA PRIVATE KEY`` form GitHub hands out) is loaded as-is — ``pyjwt[crypto]``
uses ``cryptography``, which reads it directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from typing import Any

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"


class ConfigError(Exception):
    """Configuration is missing or malformed.

    Raised instead of pydantic's ``ValidationError``. ``/token`` turns this into a
    503 ``config_unavailable`` and ``/readyz`` into a 503 — a deployment that was
    never finished, not a broker bug.
    """


def _normalize_pem(value: str) -> str:
    if "\\n" in value and "-----BEGIN" in value:
        return value.replace("\\n", "\n")
    return value


@dataclass(frozen=True)
class BrokerConfig:
    """The App identity and the policy applied to every mint request."""

    # From SSM (production) or the environment (local).
    app_id: str
    private_key: str

    # The OIDC `aud` the consumer's action requests; must match on both sides.
    oidc_audience: str = "brimyr"
    # Optional comma-separated owner/repo allowlist. Empty = allow any repo the
    # App is installed on (the public-app model, which is what is deployed).
    allowed_repositories: str = ""
    github_api_url: str = "https://api.github.com"
    # Least privilege: minted tokens can only comment on PRs.
    token_permissions_json: str = '{"pull_requests": "write"}'

    def __post_init__(self) -> None:
        # frozen=True blocks plain assignment even here, so go through object.
        object.__setattr__(self, "private_key", _normalize_pem(self.private_key))

    def allowed(self) -> set[str]:
        """The owner/repo allowlist as a set (empty set = allow any)."""
        return {entry.strip() for entry in self.allowed_repositories.split(",") if entry.strip()}

    def permissions(self) -> dict[str, Any]:
        return json.loads(self.token_permissions_json)


def _ssm_overlay() -> dict[str, str]:
    """Secrets from SSM Parameter Store, or ``{}`` when ``SECRET_PATH`` is unset.

    Unset is the local/CI case and must not import boto3 — see ``app.ssm`` for why
    the import lives inside the function rather than at module scope.
    """
    path = os.environ.get("SECRET_PATH", "").strip()
    if not path:
        return {}

    from app.ssm import secrets

    return secrets(path)


def load_config() -> BrokerConfig:
    """Build config for the current invocation.

    The SSM overlay wins over the process environment: on Lambda the non-secret
    fields are function env vars and the two secrets come from Parameter Store,
    so neither ever appears in the function's configuration where
    ``lambda:GetFunctionConfiguration`` would reveal it.

    Resolved per request rather than at import so a Lambda whose SSM parameters are
    seeded *after* the first deploy recovers without a redeploy. That matters here:
    the parameters are seeded by hand, so "deployed" and "finished" are genuinely
    different moments.
    """
    names = {f.name for f in fields(BrokerConfig)}
    values: dict[str, str] = {}
    for name in names:
        env_value = os.environ.get(name.upper())
        if env_value is not None:
            values[name] = env_value
    values.update({k: v for k, v in _ssm_overlay().items() if k in names})

    missing = [name for name in ("app_id", "private_key") if not values.get(name)]
    if missing:
        raise ConfigError(f"missing required configuration: {', '.join(sorted(missing))}")

    return BrokerConfig(**values)
