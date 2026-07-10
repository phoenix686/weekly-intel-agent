"""
Scrape Sunday-only blog feeds and parse entries into plain Python dicts.

No langgraph imports, no I/O side effects beyond HTTP fetches.
Row-level failures are collected in ParseResult.errors.

SOURCES confirmed for Part B (verified live 2026-07-10):
- LangChain blog       https://blog.langchain.dev/rss.xml
- JamWithAI            https://jamwithai.substack.com/feed
- DecodingML/DecodingAI https://decodingml.substack.com/feed
- Latent Space (swyx)  https://www.latent.space/feed

Anthropic's developer blog is NOT included here -- it has no official RSS
feed, but its listing page IS server-side rendered with the full post
list when fetched with a real browser User-Agent (a request without one
gets served a JS-only shell instead). It's scraped separately in
discovery/parsers/anthropic_blog.py + discovery/nodes/anthropic_blog.py,
since the fetch mechanism (HTML scrape vs RSS) differs from every other
source here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from discovery.parsers.rss_common import fetch_rss_feed

BLOG_FEEDS: list[str] = [
    "https://blog.langchain.dev/rss.xml",
    "https://jamwithai.substack.com/feed",
    "https://decodingml.substack.com/feed",
    "https://www.latent.space/feed",
]

# Heuristic only (LangChain's feed has no <category> distinguishing case
# studies from technical posts) -- title/text keyword match. Not perfect;
# score_node's own taste-profile scoring is the second line of defense.
_LANGCHAIN_CASE_STUDY_MARKERS = (
    "case study", "success story", "customer story",
    "how [company]", "partners with", "chose langchain", "chose langgraph",
)


def _is_langchain_case_study(row: dict) -> bool:
    haystack = f"{row['title']} {row['text']}".lower()
    return any(marker in haystack for marker in _LANGCHAIN_CASE_STUDY_MARKERS)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def fetch_blog_entries(feed_urls: list[str] | None = None) -> ParseResult:
    """Fetch and parse entries from the given RSS/Atom feed URLs.

    Each successfully parsed entry produces a dict with keys:
        title, text, url, author_name, author_handle, is_thread,
        thread_contents, fetched_at, expanded_urls, has_video, video_url

    Entry-level failures are appended to ParseResult.errors as
    (feed_url, message) and parsing continues.
    """
    feeds = feed_urls if feed_urls is not None else BLOG_FEEDS
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    for feed_url in feeds:
        result = fetch_rss_feed(feed_url, source_name=feed_url)
        errors.extend(result.errors)
        for row in result.rows:
            if feed_url == "https://blog.langchain.dev/rss.xml" and _is_langchain_case_study(row):
                continue
            if not row["title"]:
                continue
            rows.append(row)

    return ParseResult(rows=rows, errors=errors)
