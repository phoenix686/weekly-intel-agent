"""
Fetch proxy for newsletter sources read as email via a shared AgentMail
inbox rather than fetched as RSS -- originally built for the 4 Substack
sources confirmed unreachable specifically from GitHub Actions (IP-based
blocking of GitHub's shared runner ranges), then expanded (2026-07-18) to
also cover 6 real senders that never had an RSS entry at all: Decoding AI
Magazine, Ahead of AI (both moved off RSS onto this same inbox for
consistency), The AI Merge (Alex Razvant), DiamantAI, The Batch, and "AI
Engineering" (Sumanth P). See discovery/config/agentmail_sources.yaml
(gitignored -- real sender-address-to-source-name mapping, personal
subscription data) and its tracked .example counterpart.

One shared inbox receives all of these -- this is a single fetch per run
(list unread messages), not one per-source fetch the way blog_sources.yaml's
other entries work, since AgentMail's API is inbox-scoped, not
publication-scoped. discovery/nodes/scrape_blogs.py calls
fetch_agentmail_newsletters() directly, alongside (not through)
blog_sources.yaml's per-entry loop.

Real AgentMail API shape (agentmail>=0.5.8, verified against the
installed package's actual method signatures via direct inspection):
- client.inboxes.messages.list(inbox_id, labels=["unread"], limit=N) ->
  metadata only (subject, from_, labels, timestamps), no body.
- client.inboxes.messages.get(inbox_id, message_id) -> full Message
  (.html/.text/.extracted_html/.extracted_text).
- client.inboxes.messages.update(inbox_id, message_id, add_labels=[...],
  remove_labels=[...]) -- AgentMail has no dedicated mark-read endpoint;
  labels ARE the read/unread state, same conceptual pattern as
  discovery/seen_items.py's mark_seen(), but the "seen" state lives in
  AgentMail's own store, not ours.

Read window: messages.list(inbox_id, labels=["unread"]) IS the entire
time boundary -- every run processes everything unread since the last
check, and add_labels=["read"] after successful processing means the
next run naturally only sees what's new since then. No separate
date-range filter exists or should be added; duplicating the unread
label with a lookback window would be redundant and could actually
reintroduce a message a partial prior run already marked read.

URL EXTRACTION -- REAL, VERIFIED FINDING (2026-07-18), not the original
design: a real newsletter email's raw HTML does NOT contain the actual
destination URL in its hrefs at all. Both Substack (a real welcome email
from Ahead of AI, inspected directly) and beehiiv (a real welcome email
from "AI Engineering", inspected directly) wrap EVERY link in an opaque
click-tracking redirect -- e.g. https://email.mg-d0.substack.com/c/{encoded
token} or https://link.mail.beehiiv.com/ss/c/{encoded token} -- with the
real destination only recoverable by actually following the HTTP
redirect (confirmed real: resolving one of these by hand returned
https://magazine.sebastianraschka.com/subscribe, a real, different URL
than the tracking link itself). Parsing the raw HTML for a /p/{slug}
pattern directly (the original design) would NEVER match a real email --
this was caught before it shipped by inspecting real received messages,
not assumed to work from the synthetic fixture alone. _extract_article_url
now resolves each candidate href via a real HTTP request (following
redirects) and checks the RESOLVED url for the /p/{slug} convention
(shared by both Substack and beehiiv), not the raw href.

No langgraph imports.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from agentmail import AgentMail

logger = logging.getLogger(__name__)

_BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Bounds real HTTP resolution cost per message -- a real newsletter email
# can have dozens of hrefs (footer, social links, etc); the real article
# link is reliably among the first few in document order (title, "view
# in browser", first CTA), confirmed via real inspection of actual
# received emails. Stops as soon as a match is found, so this cap is a
# worst case, not a typical case.
_MAX_LINKS_TO_RESOLVE = 15

# The real, shared post-URL convention across both Substack and beehiiv
# (confirmed via real resolved redirects from actual received emails, not
# assumed) -- "/p/" followed by a slug. Deliberately not restricted to a
# hardcoded list of sender domains, since resolution reveals the real
# destination domain directly regardless of which ESP sent the email.
_POST_PATH_PATTERN = re.compile(r"/p/[\w-]")

# Groups every unrecognized-sender error together under one stable key --
# callers (discovery/parsers/scrape_blogs.py) group ParseResult.errors by
# their first tuple element to attribute one NodeCost per real source; a
# message from an address not in agentmail_sources.yaml has no real
# source_name to group under, so it needs a name too, distinct from any
# configured one.
_UNRECOGNIZED_SENDER = "AgentMail Newsletters (unrecognized sender)"


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _resolve_redirect(url: str, timeout: float = 10.0) -> str | None:
    """Follows a real HTTP redirect chain and returns the final resolved
    URL, or None on any failure. Real newsletter emails wrap every link
    in an opaque click-tracking redirect (Substack, beehiiv both
    confirmed) -- the raw href is never the real destination."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.url
    except Exception as e:
        logger.debug(f"agentmail_newsletters: redirect resolution failed for {url}: {e}")
        return None


def _extract_article_url(html: str) -> str | None:
    """Resolves candidate hrefs (in document order, first
    _MAX_LINKS_TO_RESOLVE only) via a real HTTP request each, returns the
    first RESOLVED url matching the real /p/{slug} post convention.
    Raw hrefs are never checked directly -- see this module's docstring
    for why (real click-tracking redirects, confirmed via actual received
    emails)."""
    hrefs = re.findall(r'href="([^"]+)"', html or "")
    for href in hrefs[:_MAX_LINKS_TO_RESOLVE]:
        resolved = _resolve_redirect(href)
        if resolved and _POST_PATH_PATTERN.search(resolved):
            return resolved
    return None


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


def _match_sender_name(from_address: str, sender_to_name: dict[str, str]) -> str | None:
    """Matches a message's real from_ header (e.g. 'Jane Doe <jane@pub.
    substack.com>') against the configured sender_to_name map's bare
    addresses. Every RawItem from this inbox must be attributed to the
    real publication it came from -- 10 distinct senders share one
    inbox, so a generic 'agentmail' source label would collapse them all
    into one indistinguishable bucket."""
    match = re.search(r"[\w.+-]+@[\w.-]+", from_address or "")
    address = match.group(0).lower() if match else ""
    return sender_to_name.get(address)


def fetch_agentmail_newsletters(
    inbox_id: str, sender_to_name: dict[str, str], limit: int = 20
) -> ParseResult:
    """Fetch unread newsletter emails from the shared AgentMail inbox,
    parse each into a RawItem-shaped row (source-attributed via
    sender_to_name -- see _match_sender_name), mark each as read after
    successful processing (add_labels=["read"], remove_labels=["unread"])
    so the next run doesn't reprocess it. The unread label IS the read
    window -- no separate date-range filter.

    A per-message failure (parse error, unrecognized sender, no
    resolvable article URL, mark-read failure) is recorded in errors and
    does not stop the rest of the batch -- same reliability contract as
    discovery/parsers/rss_common.py's fetch_rss_feed. An inbox-level
    failure (bad API key, network error) is caught the same way: rows
    stays empty, errors gets one entry, never raises.

    errors' first tuple element is always a real source_name (matching a
    row's own author_name) or _UNRECOGNIZED_SENDER -- never a raw email
    subject or message_id -- so callers can group errors by source the
    same way they group rows, for one NodeCost per real sender rather
    than one generic "AgentMail Newsletters" bucket covering all 10."""
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
        listing = client.inboxes.messages.list(inbox_id, labels=["unread"], limit=limit)
    except Exception as e:
        errors.append(("AgentMail Newsletters", str(e)))
        return ParseResult(rows=rows, errors=errors)

    for item in listing.messages:
        label = getattr(item, "subject", None) or item.message_id
        source_name = None
        try:
            message = client.inboxes.messages.get(inbox_id, item.message_id)

            source_name = _match_sender_name(message.from_, sender_to_name)
            if source_name is None:
                errors.append((_UNRECOGNIZED_SENDER, f"{label} (from {message.from_!r}): unrecognized sender"))
                continue

            html = message.extracted_html or message.html or ""
            article_url = _extract_article_url(html)
            if not article_url:
                errors.append((source_name, f"{label}: no resolvable article URL found in email body"))
                continue

            text_source = message.extracted_html or message.html or message.extracted_text or message.text or ""
            text = _html_to_text(text_source) if "<" in text_source else text_source

            rows.append({
                "title": message.subject or article_url,
                "text": text,
                "url": article_url,
                "author_name": source_name,
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
            errors.append((source_name or _UNRECOGNIZED_SENDER, f"{label}: {e}"))

    return ParseResult(rows=rows, errors=errors)
