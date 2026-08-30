"""Shared HTTP hardening for the two stdlib clients.

`urllib`'s default redirect handler carries every header of the original request onto
the redirect target, including `Authorization`. A redirect to another host therefore
hands that host the bearer token: a GitHub installation token with `pull_requests:
write`, or the OIDC token the broker exchanges. `requests` has refused to do this since
2014; `urllib` still does, and the package is stdlib-only by design, so the guard has to
live here.

The exposure is not theoretical. Both clients talk to a host the consumer configures
(`--github-api-url` for GitHub Enterprise, `token_broker_url` for the broker), so a
misconfigured or hostile value is enough. Nothing in the redirect chain is authenticated
before the header is resent.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

#: Headers stripped when a redirect crosses to a different origin.
_SENSITIVE = ("authorization", "cookie", "proxy-authorization", "www-authenticate")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Drop credentials when a redirect changes scheme, host or port."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        before = urllib.parse.urlsplit(req.full_url)
        after = urllib.parse.urlsplit(newurl)
        if (before.scheme, before.hostname, before.port) != (
            after.scheme,
            after.hostname,
            after.port,
        ):
            for header in _SENSITIVE:
                # Both spellings: urllib normalises added headers to .capitalize().
                new.headers.pop(header.capitalize(), None)
                new.headers.pop(header, None)
                new.unredirected_hdrs.pop(header.capitalize(), None)
                new.unredirected_hdrs.pop(header, None)
        return new


def build_opener() -> urllib.request.OpenerDirector:
    """An opener that will not hand a bearer token to a redirect's new host."""
    return urllib.request.build_opener(_SameOriginRedirectHandler)
