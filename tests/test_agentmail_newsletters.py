"""
discovery/parsers/agentmail_newsletters.py -- covers URL extraction,
sender attribution, and the full fetch flow.

_resolve_redirect (the real HTTP redirect-following call) is mocked in
every test here -- its real behavior was verified directly against real
received welcome emails from Ahead of AI (Substack) and "AI Engineering"
(beehiiv) on 2026-07-18: both wrap every link in an opaque click-tracking
redirect with the real destination only recoverable by actually
resolving it (e.g. https://email.mg-d0.substack.com/c/{token} ->
https://magazine.sebastianraschka.com/subscribe, confirmed live). These
tests cover the logic around that real, already-verified mechanism --
routing, sender matching, error handling -- not re-prove the redirect
resolution itself, which needs a real network call to verify and was
already done by hand.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discovery.parsers.agentmail_newsletters as agentmail_mod
from discovery.parsers.agentmail_newsletters import (
    fetch_agentmail_newsletters, _extract_article_url, _html_to_text, _match_sender_name,
)

_SENDER_TO_NAME = {
    "jamwithai@substack.com": "JamWithAI",
    "sebastianraschka@substack.com": "Ahead of AI",
    "aiengineering@mail.beehiiv.com": "AI Engineering (Sumanth P)",
}


def test_html_to_text_strips_tags_and_collapses_whitespace():
    text = _html_to_text("<p>Hello   <b>world</b></p>\n<p>Second   paragraph</p>")
    assert text == "Hello world Second paragraph"


def test_match_sender_name_extracts_address_from_display_name_format():
    assert _match_sender_name("Shirin & Shantanu from Jam With AI <jamwithai@substack.com>", _SENDER_TO_NAME) == "JamWithAI"


def test_match_sender_name_returns_none_for_unrecognized_sender():
    assert _match_sender_name("Someone Else <someone@random.com>", _SENDER_TO_NAME) is None


def test_extract_article_url_resolves_each_href_and_returns_first_real_post_match():
    """Mirrors what a real Substack email actually contains: every href is
    an opaque tracking redirect, none of which look like an article link
    until resolved. The first one that resolves to a real /p/{slug} URL
    wins."""
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Title</a> <a href="https://email.mg-d0.substack.com/c/tokenB">Unsubscribe</a>'
    resolved = {
        "https://email.mg-d0.substack.com/c/tokenA": "https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms",
        "https://email.mg-d0.substack.com/c/tokenB": "https://magazine.sebastianraschka.com/action/disable_email",
    }
    with patch.object(agentmail_mod, "_resolve_redirect", side_effect=lambda url, timeout=10.0: resolved[url]):
        assert _extract_article_url(html) == "https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms"


def test_extract_article_url_returns_none_when_nothing_resolves_to_a_post():
    """The real signature of a welcome/onboarding email (confirmed via a
    real received message, 2026-07-18): every link resolves to something
    real (subscribe, unsubscribe, about page) but none are a /p/ post."""
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Subscribe</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/subscribe"):
        assert _extract_article_url(html) is None


def test_extract_article_url_skips_unresolvable_links_and_keeps_checking():
    html = '<a href="https://x/dead">Dead</a> <a href="https://x/good">Good</a>'
    def _resolve(url, timeout=10.0):
        return None if url == "https://x/dead" else "https://pub.substack.com/p/real-post"
    with patch.object(agentmail_mod, "_resolve_redirect", side_effect=_resolve):
        assert _extract_article_url(html) == "https://pub.substack.com/p/real-post"


# ── Three-tier extraction fix (2026-07-22, real production bug) ────────────
# A real Sunday Pipeline run (c6c5624d, 2026-07-22) failed to extract an
# article URL for 3 messages that genuinely had one -- confirmed by
# re-running this exact code against the exact real HTML from an unblocked
# machine, which found a match every time. Real hrefs below are captured
# verbatim from those actual received messages.

def test_tier1_raw_href_already_matching_post_pattern_needs_zero_network_calls():
    """Decoding AI Magazine's real "What's Harness Engineering?" email
    (2026-07-22): a plain, non-redirect open.substack.com href containing
    /p/{slug} directly, among a sea of opaque substack.com/redirect/...
    tracking links. Must be found WITHOUT ever calling _resolve_redirect."""
    html = (
        '<a href="https://substack.com/redirect/8e247eaa-6807-4f1f-ac3f-79215c4b64bf?j=eyJ1IjoiMXhrcGtmIn0">t</a>'
        '<a href="https://substack.com/@pauliusztin">profile</a>'
        '<a href="https://open.substack.com/pub/technically/p/whats-harness-engineering?utm_source=substack">Read</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result == "https://open.substack.com/pub/technically/p/whats-harness-engineering?utm_source=substack"
    mock_resolve.assert_not_called()


_REAL_NEURAL_MAZE_SUBSCRIBE_HREF = (
    "https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly90aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbS9zdWJzY3JpYmU_dXRtX3NvdXJjZT1lbWFpbCZ1dG1fY2FtcGFpZ249ZW1haWwtc3Vic2NyaWJlJnI9OHJ6b201Jm5leHQ9aHR0cHMlM0ElMkYlMkZ0aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbSUyRnAlMkZ0aGUtc2xtLW9jci1jb3Vyc2UtbGl2ZS1xLWFuZC1hLWFuZCIsInAiOjIwNzI2MzUzOSwicyI6MzMzMjIwOSwiZiI6dHJ1ZSwidSI6NTMwNzQzOTAxLCJpYXQiOjE3ODQ1NDUxOTYsImV4cCI6MjEwMDEyMTE5NiwiaXNzIjoicHViLTAiLCJzdWIiOiJsaW5rLXJlZGlyZWN0In0.hzQyWw0y4ftdN5hmiUms_ycATbh9yR529hlSjGyjD6I?"
)
_REAL_NEURAL_MAZE_POST_HREF = (
    "https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly90aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbS9wL3RoZS1zbG0tb2NyLWNvdXJzZS1saXZlLXEtYW5kLWEtYW5kP3V0bV9jYW1wYWlnbj1lbWFpbC1oYWxmLXBvc3Qmcj04cnpvbTUmdG9rZW49ZXlKMWMyVnlYMmxrSWpvMU16QTNORE01TURFc0luQnZjM1JmYVdRaU9qSXdOekkyTXpVek9Td2lhV0YwSWpveE56ZzBOVFExTVRrMkxDSmxlSEFpT2pFM09EY3hNemN4T1RZc0ltbHpjeUk2SW5CMVlpMHpNek15TWpBNUlpd2ljM1ZpSWpvaWNHOXpkQzF5WldGamRHbHZiaUo5LlI1eTVxYkpxcVhqV1VreFVqdEdHTzNDcVhMZU82clFRWjNVZHFEZzF6dTgiLCJwIjoyMDcyNjM1MzksInMiOjMzMzIyMDksImYiOnRydWUsInUiOjUzMDc0MzkwMSwiaWF0IjoxNzg0NTQ1MTk2LCJleHAiOjIxMDAxMjExOTYsImlzcyI6InB1Yi0wIiwic3ViIjoibGluay1yZWRpcmVjdCJ9.WhqgTGA68J9KL1E-nGRCeGzhO-o-uqcPeEQWB_zF7tA?"
)


def test_tier2_substack_redirect_2_decodes_without_network_calls():
    """The Neural Maze's real "The SLM OCR Course..." email (2026-07-22):
    a substack.com/redirect/2/{base64} href whose (JWT-shaped, payload.signature)
    token's payload segment decodes directly to real JSON containing
    "e": "https://theneuralmaze.substack.com/p/the-slm-ocr-course-live-q-and-a-and?...".
    Both hrefs below are captured verbatim, full and untruncated, from the
    actual received message. Must resolve WITHOUT any network call."""
    html = (
        f'<a href="{_REAL_NEURAL_MAZE_SUBSCRIBE_HREF}">subscribe</a>'
        f'<a href="{_REAL_NEURAL_MAZE_POST_HREF}">Read</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result is not None
    assert "theneuralmaze.substack.com/p/the-slm-ocr-course-live-q-and-a-and" in result
    mock_resolve.assert_not_called()


def test_tier2_ignores_redirect_2_link_that_decodes_to_a_non_post_url():
    """The first redirect/2/ link in the same real email decodes to a
    /subscribe page, not a post -- tier 2 must correctly skip it (fall
    through to the next href/tier), not treat any successful decode as a
    match regardless of destination."""
    html = f'<a href="{_REAL_NEURAL_MAZE_SUBSCRIBE_HREF}">subscribe</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value=None) as mock_resolve:
        result = _extract_article_url(html)

    assert result is None
    mock_resolve.assert_called_once()  # falls through to tier 3, which also fails here


def test_tier3_still_used_for_genuinely_opaque_tracking_links():
    """AI Engineering's real beehiiv email (2026-07-22): link.mail.beehiiv.com
    tokens are NOT base64-JSON-decodable (confirmed directly) -- tiers 1-2
    must correctly fall through to tier 3's real HTTP resolution, which
    still works and must still be exercised."""
    html = '<a href="https://link.mail.beehiiv.com/v1/c/wNq66YaGm0G2BLC2rCTdiVywre9Ryz3w2kdC3UqVQW3WxqRUdGcC2zgWe3GK">Read</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value="https://aiengineering.beehiiv.com/p/hands-on-build-a-browser-automation-agent") as mock_resolve:
        result = _extract_article_url(html)

    assert result == "https://aiengineering.beehiiv.com/p/hands-on-build-a-browser-automation-agent"
    mock_resolve.assert_called_once()


def test_tier1_before_tier2_when_both_present():
    """When a raw href already matches directly (tier 1), tier 2's decode
    attempt on a LATER href must never even be reached -- tier 1 wins."""
    html = (
        '<a href="https://open.substack.com/pub/name/p/first-match">Read</a>'
        '<a href="https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly9leGFtcGxlLmNvbS9wL3NlY29uZC1tYXRjaCJ9">Also a post</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result == "https://open.substack.com/pub/name/p/first-match"
    mock_resolve.assert_not_called()


class _FakeMessageItem:
    def __init__(self, message_id, subject):
        self.message_id = message_id
        self.subject = subject


class _FakeMessage:
    def __init__(self, message_id, subject, html, from_, timestamp):
        self.message_id = message_id
        self.subject = subject
        self.html = html
        self.extracted_html = html
        self.text = None
        self.extracted_text = None
        self.from_ = from_
        self.timestamp = timestamp


class _FakeMessagesClient:
    def __init__(self, list_items, get_by_id):
        self._list_items = list_items
        self._get_by_id = get_by_id
        self.update_calls: list[tuple] = []

    def list(self, inbox_id, labels=None, limit=None):
        return SimpleNamespace(messages=self._list_items)

    def get(self, inbox_id, message_id):
        return self._get_by_id[message_id]

    def update(self, inbox_id, message_id, add_labels=None, remove_labels=None):
        self.update_calls.append((inbox_id, message_id, add_labels, remove_labels))


class _FakeInboxesClient:
    def __init__(self, messages_client):
        self.messages = messages_client


class _FakeAgentMailClient:
    def __init__(self, messages_client, api_key=None):
        self.inboxes = _FakeInboxesClient(messages_client)


def test_fetch_agentmail_newsletters_parses_and_attributes_a_real_shaped_message_and_marks_it_read():
    now = datetime.now(timezone.utc)
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Title</a>'
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-1", "Coding the KV Cache in LLMs")],
        get_by_id={
            "msg-1": _FakeMessage(
                "msg-1", "Coding the KV Cache in LLMs", html,
                '"Sebastian Raschka, PhD from Ahead of AI" <sebastianraschka@substack.com>', now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms"), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.errors == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["url"] == "https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms"
    assert row["author_name"] == "Ahead of AI"  # attributed by real sender match, not a generic label

    assert fake_messages.update_calls == [("inbox-123", "msg-1", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_unrecognized_sender_grouped_under_stable_key_and_not_marked_read():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-2", "Random newsletter")],
        get_by_id={
            "msg-2": _FakeMessage("msg-2", "Random newsletter", "<a href='x'>x</a>", "Someone <someone@random.com>", now),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "AgentMail Newsletters (unrecognized sender)"
    assert fake_messages.update_calls == []


def test_fetch_agentmail_newsletters_no_resolvable_url_grouped_under_real_source_name():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-3", "Welcome to Ahead of AI!")],
        get_by_id={
            "msg-3": _FakeMessage(
                "msg-3", "Welcome to Ahead of AI!", '<a href="https://email.mg-d0.substack.com/c/tokenX">Subscribe</a>',
                "sebastianraschka@substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/subscribe"), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "Ahead of AI"  # grouped by real source, not the raw subject/message_id
    assert fake_messages.update_calls == []  # eligible for retry -- not marked read


def test_fetch_agentmail_newsletters_one_bad_message_does_not_block_the_others():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("bad", "Welcome"), _FakeMessageItem("good", "Real post")],
        get_by_id={
            "bad": _FakeMessage("bad", "Welcome", '<a href="https://x/c/t">x</a>', "sebastianraschka@substack.com", now),
            "good": _FakeMessage("good", "Real post", '<a href="https://x/c/t2">x</a>', "jamwithai@substack.com", now),
        },
    )

    def _resolve(url, timeout=10.0):
        return "https://magazine.sebastianraschka.com/subscribe" if url == "https://x/c/t" else "https://jamwithai.substack.com/p/some-real-post"

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", side_effect=_resolve), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert len(result.rows) == 1
    assert result.rows[0]["author_name"] == "JamWithAI"
    assert len(result.errors) == 1
    assert fake_messages.update_calls == [("inbox-123", "good", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_inbox_level_failure_never_raises():
    class _BrokenAgentMail:
        def __init__(self, api_key=None):
            raise RuntimeError("invalid API key")

    with patch.object(agentmail_mod, "AgentMail", _BrokenAgentMail), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "invalid API key" in result.errors[0][1]
