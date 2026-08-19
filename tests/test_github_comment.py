"""Unit tests for the PR comment client (brimyr.github_comment)."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from brimyr.github_comment import (
    SUMMARY_MARKER,
    CommentConfig,
    post_pr_comment,
)


class _Response:
    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeOpener:
    """Records requests and replays queued responses, like the real opener."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def open(self, request: Any, timeout: float | None = None) -> _Response:
        body = json.loads(request.data.decode()) if request.data else None
        self.calls.append((request.get_method(), request.full_url, body))
        payload = self.responses.pop(0) if self.responses else []
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)


def _config(**over: Any) -> CommentConfig:
    base = {"repo_slug": "MagmaMoose/brimyr", "pr_number": 7, "token": "t0ken"}
    base.update(over)
    return CommentConfig(**base)


def test_creates_a_comment_when_none_exists():
    opener = _FakeOpener([[]])  # empty first page -> no prior comment
    result = post_pr_comment(_config(), "## body", opener=opener)

    assert result.ok and result.action == "created"
    method, url, body = opener.calls[-1]
    assert method == "POST"
    assert url.endswith("/repos/MagmaMoose/brimyr/issues/7/comments")
    assert body is not None and body["body"].startswith(SUMMARY_MARKER)


def test_updates_the_prior_marked_comment_in_place():
    prior = [{"id": 42, "body": f"{SUMMARY_MARKER}\nold"}]
    opener = _FakeOpener([prior])
    result = post_pr_comment(_config(), "## new", opener=opener)

    assert result.ok and result.action == "updated"
    method, url, body = opener.calls[-1]
    assert method == "PATCH"
    assert url.endswith("/repos/MagmaMoose/brimyr/issues/comments/42")
    assert body is not None and "## new" in body["body"]


def test_ignores_comments_owned_by_another_tool():
    """One owner per surface: chargate's comment must never be hijacked."""
    others = [{"id": 9, "body": "<!-- chargate:pr-summary -->\nnot ours"}]
    opener = _FakeOpener([others])
    result = post_pr_comment(_config(), "## body", opener=opener)

    assert result.action == "created"  # posted its own, left chargate's alone
    assert opener.calls[-1][0] == "POST"


def test_marker_is_not_duplicated_when_already_present():
    opener = _FakeOpener([[]])
    post_pr_comment(_config(), f"{SUMMARY_MARKER}\n## body", opener=opener)

    body = opener.calls[-1][2]
    assert body is not None and body["body"].count(SUMMARY_MARKER) == 1


@pytest.mark.parametrize(
    "over, missing",
    [
        ({"token": ""}, "token"),
        ({"repo_slug": ""}, "repo_slug"),
        ({"pr_number": 0}, "pr_number"),
    ],
)
def test_skips_cleanly_when_config_is_incomplete(over, missing):
    opener = _FakeOpener()
    result = post_pr_comment(_config(**over), "## body", opener=opener)

    assert not result.ok and result.action == "skipped"
    assert missing in result.message
    assert opener.calls == []  # never touched the network


def test_http_error_never_raises_and_never_gates():
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
    opener = _FakeOpener([err])
    result = post_pr_comment(_config(), "## body", opener=opener)

    assert not result.ok
    assert "403" in result.message
    assert result.errors


def test_transport_error_never_raises():
    opener = _FakeOpener([urllib.error.URLError("no route to host")])
    result = post_pr_comment(_config(), "## body", opener=opener)

    assert not result.ok and "no route" in result.message


def test_pagination_stops_on_a_short_page():
    full = [{"id": i, "body": "x"} for i in range(100)]
    opener = _FakeOpener([full, [{"id": 999, "body": f"{SUMMARY_MARKER}\nold"}]])
    result = post_pr_comment(_config(), "## body", opener=opener)

    assert result.action == "updated"
    assert opener.calls[-1][1].endswith("/issues/comments/999")


def test_honours_a_ghes_base_url():
    opener = _FakeOpener([[]])
    post_pr_comment(_config(base_url="https://ghe.example.com/api/v3"), "b", opener=opener)

    assert opener.calls[-1][1].startswith("https://ghe.example.com/api/v3/repos/")
