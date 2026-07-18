"""
Fetch proxy for the 4 Substack sources confirmed unreachable specifically
from GitHub Actions (JamWithAI, The Nuanced Perspective, AI with Aish, The
Neural Maze -- see blog_sources.yaml's dated comment on this) -- likely
IP-based blocking of GitHub's shared runner ranges, not fixable from our
side (discovery/parsers/rss_common.py's real browser User-Agent made no
difference when re-tested). Routes around it entirely: one AgentMail
inbox's real email address gets subscribed to each newsletter directly
(a real email subscription, same as subscribing any real address -- not
an API workaround against Substack), then this module reads the
newsletters AS EMAIL via AgentMail's API instead of fetching RSS feeds.

One shared inbox receives all 4 newsletters -- this is a single fetch per
run (list unread messages), not 4 separate per-source fetches the way
blog_sources.yaml's other entries work, since AgentMail's API is
inbox-scoped, not publication-scoped. See discovery/parsers/
scrape_blogs.py's fetch_one_source() for the blog_sources.yaml dispatch
(an `agentmail_inbox_id` entry routes here instead of `feed_url`/
`scrape_url`).

Real AgentMail API shape (agentmail>=0.5.8, verified against the
installed package's actual method signatures via direct inspection, not
assumed from docs alone):
- client.inboxes.messages.list(inbox_id, labels=["unread"], limit=N) ->
  ListMessagesResponse.messages -- METADATA ONLY (subject, from_, labels,
  timestamps, etc.), no body content.
- client.inboxes.messages.get(inbox_id, message_id) -> Message -- the
  full message, including .html/.text/.extracted_html/.extracted_text.
- client.inboxes.messages.update(inbox_id, message_id, add_labels=[...],
  remove_labels=[...]) -- AgentMail has no dedicated "mark read" endpoint;
  labels ARE the read/unread state. Same conceptual pattern as
  discovery/seen_items.py's mark_seen(), but the "seen" state lives in
  AgentMail's own store, not ours.

URL extraction: a real Substack newsletter email contains several links
to the same canonical post (the title, a "Read on Substack" button,
inline images) all pointing to https://{subdomain}.substack.com/p/{slug}
-- confirmed as the real, stable Substack post URL pattern via this
project's own successful RSS fetches of sibling Substack-hosted sources
(Decoding AI Magazine, Ahead of AI both use this exact /p/ pattern; see
blog_sources.yaml). Scans every href in the email HTML and keeps the
first match against one of the 4 target publications' known subdomains --
more robust than targeting a specific "View in browser" link's exact
position/CSS class, since that exact template layout is real,
publisher-controlled structure this project has NOT seen directly yet
(no live inbox exists to confirm a real received email against -- see
this module's own honest caveat in its docstring below about what has
and hasn't actually been verified).

HONEST CAVEAT (2026-07-18): this module has been verified against a
realistic, manually-constructed HTML fixture modeled on the real,
documented Substack /p/ URL convention -- NOT against an actual received
newsletter email, since no AgentMail inbox exists yet (AGENTMAIL_API_KEY
not yet added). Real evidence of an actual received email parsed into a
real RawItem is still outstanding -- see feature_list.json's
agentmail-newsletter-integration entry and WORKFLOW.md for the exact
gating condition.

No langgraph imports.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from agentmail import AgentMail

logger = logging.getLogger(__name__)

# The 4 sources this proxy exists for (blog_sources.yaml's dated comment) --
# each publication's real Substack subdomain, used to identify which href
# in a newsletter email is the real article link versus tracking pixels,
# unsubscribe links, or Substack's own marketing/domain links.
_KNOWN_SUBDOMAINS = (
    "jamwithai.substack.com",
    "thenuancedperspective.substack.com",
    "aishwaryasrinivasan.substack.com",
    "theneuralmaze.substack.com",
)

_POST_URL_PATTERN = re.compile(
    r'https://(?:' + "|".join(re.escape(d) for d in _KNOWN_SUBDOMAINS) + r')/p/[^\s"\'<>]+'
)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _extract_article_url(html: str) -> str | None:
    """First href matching a known publication's /p/{slug} pattern -- a
    real Substack post email links to the same canonical post URL
    multiple times (title, button, images); the /p/ URL itself (not the
    template position) is the stable, real signal, per this project's own
    successful RSS fetches of sibling Substack sources."""
    match = _POST_URL_PATTERN.search(html or "")
    return match.group(0) if match else None


class _TextStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def _html_to_text(html: str) -> str:
    """Minimal, dependency-free HTML-to-text -- strips tags, collapses
    whitespace. Good enough for score_node's prompt (which only ever uses
    the first 500 chars anyway, see discovery/nodes/score.py's
    _score_batch), not a general-purpose renderer."""
    stripper = _TextStripper()
    stripper.feed(html or "")
    return re.sub(r"\s+", " ", " ".join(stripper.parts)).strip()


def fetch_agentmail_newsletters(inbox_id: str, limit: int = 20) -> ParseResult:
    """Fetch unread newsletter emails from the shared AgentMail inbox,
    parse each into a RawItem-shaped row, mark each as read after
    processing (add_labels=["read"], remove_labels=["unread"]) so the
    next run doesn't reprocess it.

    A per-message failure (parse error, no recognized article URL,
    mark-read failure) is recorded in errors and does not stop the rest
    of the batch -- same reliability contract as
    discovery/parsers/rss_common.py's fetch_rss_feed. An inbox-level
    failure (bad API key, network error) is caught the same way
    fetch_rss_feed catches a feed-level failure: rows stays empty,
    errors gets one entry, never raises."""
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
        listing = client.inboxes.messages.list(inbox_id, labels=["unread"], limit=limit)
    except Exception as e:
        errors.append(("AgentMail Newsletters", str(e)))
        return ParseResult(rows=rows, errors=errors)

    for item in listing.messages:
        try:
            message = client.inboxes.messages.get(inbox_id, item.message_id)
            html = message.extracted_html or message.html or ""
            article_url = _extract_article_url(html)
            if not article_url:
                errors.append((message.subject or item.message_id, "no recognized Substack post URL found in email body"))
                continue

            text_source = message.extracted_html or message.html or message.extracted_text or message.text or ""
            text = _html_to_text(text_source) if "<" in text_source else text_source

            rows.append({
                "title": message.subject or article_url,
                "text": text,
                "url": article_url,
                "author_name": message.from_ or "",
                "author_handle": "",
                "fetched_at": message.timestamp.isoformat(),
                "is_thread": False,
                "thread_contents": None,
                "expanded_urls": [],
            })

            client.inboxes.messages.update(
                inbox_id, item.message_id, add_labels=["read"], remove_labels=["unread"]
            )
        except Exception as e:
            errors.append((getattr(item, "subject", None) or item.message_id, str(e)))

    return ParseResult(rows=rows, errors=errors)
