"""
Scrape Sunday-only blog/newsletter feeds and parse entries into plain
Python dicts. Feed list comes from discovery/config/blog_sources.yaml
(bucket=sunday, feed_url entries only -- scrape_url entries like
Anthropic's dev blog are handled by their own dedicated node).

No langgraph imports, no I/O side effects beyond HTTP fetches.
Row-level failures are collected in ParseResult.errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from discovery.parsers.rss_common import fetch_rss_feed
from discovery.blog_sources_config import feed_urls_for_bucket

# Heuristic only (LangChain's feed has no <category> distinguishing case
# studies from technical posts) -- title/text keyword match. Not perfect;
# score_node's own taste-profile scoring is the second line of defense.
_LANGCHAIN_CASE_STUDY_MARKERS = (
    "case study", "success story", "customer story",
    "how [company]", "partners with", "chose langchain", "chose langgraph",
)
_LANGCHAIN_FEED_URL = "https://blog.langchain.dev/rss.xml"


def _is_langchain_case_study(row: dict) -> bool:
    haystack = f"{row['title']} {row['text']}".lower()
    return any(marker in haystack for marker in _LANGCHAIN_CASE_STUDY_MARKERS)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def fetch_blog_entries(feed_urls: list[str] | None = None) -> ParseResult:
    """Fetch and parse entries from the given RSS/Atom feed URLs (defaults
    to every sunday-bucket feed_url entry in blog_sources.yaml).

    Each successfully parsed entry produces a dict with keys:
        title, text, url, author_name, author_handle, is_thread,
        thread_contents, fetched_at, expanded_urls, has_video, video_url

    Entry-level failures are appended to ParseResult.errors as
    (feed_url, message) and parsing continues.
    """
    feeds = feed_urls if feed_urls is not None else feed_urls_for_bucket("sunday")
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    for feed_url in feeds:
        result = fetch_rss_feed(feed_url, source_name=feed_url, max_age_hours=216)
        errors.extend(result.errors)
        for row in result.rows:
            if feed_url == _LANGCHAIN_FEED_URL and _is_langchain_case_study(row):
                continue
            if not row["title"]:
                continue
            rows.append(row)

    return ParseResult(rows=rows, errors=errors)
