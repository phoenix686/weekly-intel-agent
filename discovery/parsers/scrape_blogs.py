"""
Scrape blog feeds and parse entries into plain Python dicts.

No langgraph imports, no I/O side effects beyond HTTP fetches.
Row-level failures are collected in ParseResult.errors.

SOURCES: not yet configured — awaiting confirmation of which blog URLs to scrape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# TODO: populate with confirmed blog feed URLs before use
BLOG_FEEDS: list[str] = []


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def fetch_blog_entries(feed_urls: list[str] | None = None) -> ParseResult:
    """Fetch and parse entries from the given RSS/Atom feed URLs.

    Each successfully parsed entry produces a dict with keys:
        title           str
        text            str        entry summary / description
        url             str        entry link
        author_name     str
        author_handle   str        empty string if not available
        is_thread       bool       always False for blog entries
        thread_contents None
        fetched_at      str        ISO 8601
        expanded_urls   list[str]  always [] for blog entries

    Raises ValueError on malformed feed XML.
    Entry-level failures are appended to ParseResult.errors as
    (feed_url, message) and parsing continues.
    """
    raise NotImplementedError(
        "scrape_blogs: BLOG_FEEDS not configured. "
        "Confirm which blog URLs to scrape before implementing."
    )
