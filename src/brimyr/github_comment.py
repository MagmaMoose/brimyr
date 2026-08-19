"""GitHub PR comment client — optional, idempotent, and failure-isolated.

Posts **one** consolidated patch-coverage comment on the pull request: the
percentage, the threshold, and the changed lines the tests never executed.

Idempotency is the whole point. The comment carries a hidden HTML marker
(:data:`SUMMARY_MARKER`); on every run we look for the prior marked comment and
``PATCH`` it, else ``POST`` a new one — so a re-push updates one comment instead
of stacking a new one per commit.

**One owner per surface.** The marker is brimyr-specific, so brimyr and chargate
never claim each other's comment even though both comment on the same PR. Do not
widen this match to "any comment we can parse".

Stdlib only (``urllib``), mirroring the rest of the package — no third-party HTTP
dependency. By contract a GitHub API failure NEVER raises out of
:func:`post_pr_comment`: it returns a result with ``ok=False`` so the caller can
log and continue. A broken comment must never turn a passing gate red, exactly as
:mod:`brimyr.sonar` never lets a Sonar outage fail the build.

This module is pure transport. The body is rendered by :mod:`brimyr.report`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from brimyr import __version__

# A hidden HTML marker: invisible in rendered Markdown, but it lets a later run
# recognise the comment it owns. Deliberately namespaced to brimyr.
SUMMARY_MARKER = "<!-- brimyr:pr-summary -->"

_USER_AGENT = f"brimyr/{__version__} (+https://github.com/MagmaMoose/brimyr)"
_API_VERSION = "2022-11-28"
# Cap pagination so a pathological PR can't loop forever (100/page -> 2000 comments).
_MAX_PAGES = 20


@dataclass(frozen=True)
class CommentConfig:
    """Everything needed to address one PR's comment thread."""

    base_url: str = "https://api.github.com"
    repo_slug: str = ""  # "owner/repo"
    pr_number: int = 0
    token: str = ""
    timeout: float = 30.0

    def repo_path(self, suffix: str) -> str:
        return f"{self.base_url.rstrip('/')}/repos/{self.repo_slug}{suffix}"

    def missing(self) -> tuple[str, ...]:
        """Names of the required fields that are empty — cheap pre-flight."""
        pairs = (("repo_slug", self.repo_slug), ("token", self.token))
        names = [name for name, value in pairs if not value]
        if self.pr_number <= 0:
            names.append("pr_number")
        return tuple(names)


@dataclass(frozen=True)
class CommentResult:
    """The outcome of a comment attempt. ``ok=False`` never fails the gate."""

    ok: bool
    message: str = ""
    action: str | None = None  # "created" | "updated" | "skipped"
    errors: tuple[str, ...] = field(default_factory=tuple)


class _GitHubAPI:
    """Thin urllib wrapper. Raises ``urllib.error.*`` on transport/HTTP errors."""

    def __init__(
        self,
        config: CommentConfig,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._config = config
        self._opener = opener or urllib.request.build_opener()

    def request(self, method: str, url: str, payload: Any | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._config.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", _API_VERSION)
        request.add_header("User-Agent", _USER_AGENT)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with self._opener.open(request, timeout=self._config.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return _safe_json(raw)

    def paginate(self, base_url: str) -> list[dict[str, Any]]:
        """Collect all items across pages of a list endpoint."""
        items: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            sep = "&" if "?" in base_url else "?"
            data = self.request("GET", f"{base_url}{sep}per_page=100&page={page}")
            batch = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            items.extend(batch)
            if len(batch) < 100:
                break
        return items


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _http_detail(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")[:300] if exc.fp else ""
    return f"HTTP {exc.code}: {body}".strip()


def _find_prior(api: _GitHubAPI, config: CommentConfig) -> int | None:
    """The id of the comment this tool owns on that PR, if it posted one before."""
    list_url = config.repo_path(f"/issues/{config.pr_number}/comments")
    for comment in api.paginate(list_url):
        if SUMMARY_MARKER in (comment.get("body") or "") and isinstance(comment.get("id"), int):
            return int(comment["id"])
    return None


def post_pr_comment(
    config: CommentConfig,
    body: str,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> CommentResult:
    """Create or update the single brimyr comment on a PR. Never raises.

    ``body`` is rendered Markdown; the marker is prepended here so a caller can
    never forget it and orphan the previous comment.
    """
    missing = config.missing()
    if missing:
        return CommentResult(
            ok=False,
            action="skipped",
            message=f"PR comment skipped — missing {', '.join(missing)}",
        )

    marked = body if body.startswith(SUMMARY_MARKER) else f"{SUMMARY_MARKER}\n{body}"
    api = _GitHubAPI(config, opener)

    try:
        prior = _find_prior(api, config)
        if prior is not None:
            api.request("PATCH", config.repo_path(f"/issues/comments/{prior}"), {"body": marked})
            action = "updated"
        else:
            api.request(
                "POST",
                config.repo_path(f"/issues/{config.pr_number}/comments"),
                {"body": marked},
            )
            action = "created"
    except urllib.error.HTTPError as exc:
        detail = _http_detail(exc)
        return CommentResult(ok=False, message=f"PR comment failed: {detail}", errors=(detail,))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        detail = str(exc)
        return CommentResult(ok=False, message=f"PR comment failed: {detail}", errors=(detail,))

    return CommentResult(ok=True, action=action, message=f"PR comment {action}")
