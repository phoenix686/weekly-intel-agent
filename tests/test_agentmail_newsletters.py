"""
discovery/parsers/agentmail_newsletters.py -- covers the URL-extraction
and HTML-to-text logic against a realistic, manually-constructed HTML
fixture modeled on the real, documented Substack /p/{slug} URL
convention. NOT a real received newsletter email -- no AgentMail inbox
exists yet (AGENTMAIL_API_KEY not yet added), so this is the honest
ceiling of what can be verified until that exists. See this module's own
docstring and feature_list.json's agentmail-newsletter-integration entry
for the exact gating condition on real evidence.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discovery.parsers.agentmail_newsletters as agentmail_mod
from discovery.parsers.agentmail_newsletters import (
    fetch_agentmail_newsletters, _extract_article_url, _html_to_text,
)

# Modeled on the real, publicly-documented Substack email template shape:
# the post title, a "Read on Substack" button, and inline images all link
# to the same canonical https://{subdomain}.substack.com/p/{slug} URL,
# alongside unrelated tracking-pixel/unsubscribe links to other domains.
_REALISTIC_NEWSLETTER_HTML = """
<html><body>
<div class="email-header"><img src="https://substackcdn.com/tracking-pixel.gif"></div>
<h1><a href="https://jamwithai.substack.com/p/why-agent-memory-is-hard">
  Why Agent Memory Is Hard</a></h1>
<p>By Jane Doe</p>
<div class="post-content">
  <p>Building durable memory for AI agents requires more than a vector store...</p>
  <p>Read the <a href="https://jamwithai.substack.com/p/why-agent-memory-is-hard">full post on Substack</a>.</p>
</div>
<a href="https://jamwithai.substack.com/subscribe">Manage your subscription</a>
<a href="https://substack.com/unsubscribe?token=abc123">Unsubscribe</a>
</body></html>
"""

_NO_MATCH_HTML = """
<html><body>
<p>This is a promotional email with no recognizable Substack post link.</p>
<a href="https://example.com/some-other-thing">Click here</a>
</body></html>
"""


def test_extract_article_url_finds_the_real_post_link_among_several_hrefs():
    url = _extract_article_url(_REALISTIC_NEWSLETTER_HTML)
    assert url == "https://jamwithai.substack.com/p/why-agent-memory-is-hard"


def test_extract_article_url_ignores_unrelated_links():
    url = _extract_article_url(_NO_MATCH_HTML)
    assert url is None


def test_extract_article_url_matches_all_four_known_subdomains():
    for subdomain in (
        "jamwithai.substack.com", "thenuancedperspective.substack.com",
        "aishwaryasrinivasan.substack.com", "theneuralmaze.substack.com",
    ):
        html = f'<a href="https://{subdomain}/p/some-post">Read</a>'
        assert _extract_article_url(html) == f"https://{subdomain}/p/some-post"


def test_html_to_text_strips_tags_and_collapses_whitespace():
    text = _html_to_text("<p>Hello   <b>world</b></p>\n<p>Second   paragraph</p>")
    assert text == "Hello world Second paragraph"


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


def test_fetch_agentmail_newsletters_parses_a_real_shaped_message_into_a_rawitem_and_marks_it_read():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-1", "Why Agent Memory Is Hard")],
        get_by_id={
            "msg-1": _FakeMessage(
                "msg-1", "Why Agent Memory Is Hard", _REALISTIC_NEWSLETTER_HTML,
                "jane@jamwithai.substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", limit=20)

    assert result.errors == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["url"] == "https://jamwithai.substack.com/p/why-agent-memory-is-hard"
    assert row["title"] == "Why Agent Memory Is Hard"
    assert "vector store" in row["text"]
    assert row["author_name"] == "jane@jamwithai.substack.com"

    # marked as read after successful processing, so the next run doesn't
    # reprocess it -- same conceptual pattern as seen_items.mark_seen
    assert fake_messages.update_calls == [("inbox-123", "msg-1", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_records_error_and_does_not_mark_read_when_no_url_found():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-2", "Some promo email")],
        get_by_id={
            "msg-2": _FakeMessage("msg-2", "Some promo email", _NO_MATCH_HTML, "noreply@example.com", now),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "no recognized Substack post URL" in result.errors[0][1]
    assert fake_messages.update_calls == []  # never marked read -- eligible for retry next run


def test_fetch_agentmail_newsletters_one_bad_message_does_not_block_the_others():
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("bad", "Bad one"), _FakeMessageItem("good", "Good one")],
        get_by_id={
            "bad": _FakeMessage("bad", "Bad one", _NO_MATCH_HTML, "noreply@example.com", now),
            "good": _FakeMessage(
                "good", "Why Agent Memory Is Hard", _REALISTIC_NEWSLETTER_HTML,
                "jane@jamwithai.substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", limit=20)

    assert len(result.rows) == 1
    assert len(result.errors) == 1
    assert fake_messages.update_calls == [("inbox-123", "good", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_inbox_level_failure_never_raises():
    class _BrokenAgentMail:
        def __init__(self, api_key=None):
            raise RuntimeError("invalid API key")

    with patch.object(agentmail_mod, "AgentMail", _BrokenAgentMail), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "invalid API key" in result.errors[0][1]
