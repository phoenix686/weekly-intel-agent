"""
Real, non-mocked integration test for discovery/parsers/agentmail_
newsletters.py's _resolve_redirect() -- exercises the ACTUAL tier-3 HTTP
resolution mechanism (real urllib.request.urlopen, real TLS, real
redirect-following) against real network targets. Every other test in
tests/test_agentmail_newsletters.py mocks this call away entirely; this
catches a regression in the real mechanism itself (a stdlib behavior
change, a TLS/cert issue, the request logic silently breaking) that a
mocked test structurally cannot.

Deliberately excluded from the default `pytest`/`pytest tests/` run via
tests/conftest.py's collect_ignore -- depends on real external uptime
(httpbin.org), which would make CI flaky/non-deterministic if run
automatically. Runnable on demand:
  pytest tests/test_resolve_redirect_live.py
  uv run --env-file .env python tests/test_resolve_redirect_live.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.parsers.agentmail_newsletters import _resolve_redirect


def test_resolve_redirect_follows_a_real_http_redirect():
    """http://www.google.com's own http->https redirect -- chosen over a
    dedicated test service (httpbin.org, httpstat.us) after this test
    itself caught httpbin.org returning a real 503 mid-development
    (exactly the kind of external flakiness this test is deliberately
    isolated from CI for). Google's http->https redirect is about as
    durable a real-world redirect as exists; asserts a prefix, not exact
    equality, since Google may append/vary query params on the
    destination."""
    result = _resolve_redirect("http://www.google.com")

    assert result is not None, "expected a real resolved URL, got None -- see the WARNING log line above for the real exception"
    assert result.startswith("https://www.google.com"), f"expected an https://www.google.com redirect, got {result!r}"


def test_resolve_redirect_returns_none_for_a_real_dead_domain():
    """The failure path exercised for real too -- a domain that can never
    resolve (DNS failure) proves _resolve_redirect's except clause still
    correctly returns None (not raising) against a genuine network error,
    not just a mocked one."""
    result = _resolve_redirect("https://this-domain-does-not-exist-weekly-intel-diagnostic.invalid/")
    assert result is None


if __name__ == "__main__":
    test_resolve_redirect_follows_a_real_http_redirect()
    print("test_resolve_redirect_follows_a_real_http_redirect: PASS")
    test_resolve_redirect_returns_none_for_a_real_dead_domain()
    print("test_resolve_redirect_returns_none_for_a_real_dead_domain: PASS")
