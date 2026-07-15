"""
Fetch every source configured in discovery/config/blog_sources.yaml that's
active for the current invocation context (daily/sunday), and parse
entries into plain Python dicts. This is the single generic source
fetcher -- TLDR AI, Smol AI News, and Anthropic's dev blog used to each
have their own dedicated node file; now that blog_sources.yaml exists as
the one config, there's no remaining reason to keep them separate.

Dispatches per entry: `feed_url` entries go through
discovery/parsers/rss_common.py's fetch_rss_feed() (RSS/Atom);
`scrape_url` entries (currently only Anthropic's dev blog, which has no
RSS feed) go through discovery/parsers/anthropic_blog.py's
fetch_anthropic_engineering().

No langgraph imports, no I/O side effects beyond HTTP fetches.
Row-level failures are collected in ParseResult.errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from discovery.parsers.rss_common import fetch_rss_feed
from discovery.parsers.anthropic_blog import fetch_anthropic_engineering
from discovery.blog_sources_config import entries_for_context

# Heuristic only (LangChain's feed has no <category> distinguishing case
# studies from technical posts) -- title/text keyword match. Not perfect;
# score_node's own taste-profile scoring is the second line of defense.
_LANGCHAIN_CASE_STUDY_MARKERS = (
    "case study", "success story", "customer story",
    "how [company]", "partners with", "chose langchain", "chose langgraph",
)
_LANGCHAIN_FEED_URL = "https://blog.langchain.dev/rss.xml"

# pubDate pre-filter window per bucket -- see discovery/parsers/rss_common.py's
# max_age_hours. Not applied to scrape_url entries (fetch_anthropic_engineering
# has no pubDate-cutoff support).
_MAX_AGE_HOURS_BY_BUCKET = {"daily": 48, "sunday": 216}


def _is_langchain_case_study(row: dict) -> bool:
    haystack = f"{row['title']} {row['text']}".lower()
    return any(marker in haystack for marker in _LANGCHAIN_CASE_STUDY_MARKERS)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def fetch_blog_entries(source_context: str) -> ParseResult:
    """Fetch every blog_sources.yaml entry active for source_context
    ("daily" or "sunday" -- sunday is a superset, see
    blog_sources_config.entries_for_context).

    Each successfully parsed entry produces a dict with keys:
        title, text, url, author_name, author_handle, is_thread,
        thread_contents, fetched_at, expanded_urls, has_video, video_url

    Entry-level failures are appended to ParseResult.errors as
    (entry_name, message) and parsing continues.
    """
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    for entry in entries_for_context(source_context):
        if "feed_url" in entry:
            max_age = _MAX_AGE_HOURS_BY_BUCKET[entry["bucket"]]
            result = fetch_rss_feed(
                entry["feed_url"], source_name=entry["name"], max_age_hours=max_age
            )
            errors.extend(result.errors)
            for row in result.rows:
                if entry["feed_url"] == _LANGCHAIN_FEED_URL and _is_langchain_case_study(row):
                    continue
                if not row["title"]:
                    continue
                rows.append(row)
        else:
            result = fetch_anthropic_engineering(url=entry["scrape_url"])
            errors.extend((entry["name"], msg) for _, msg in result.errors)
            rows.extend(row for row in result.rows if row["title"])

    return ParseResult(rows=rows, errors=errors)
