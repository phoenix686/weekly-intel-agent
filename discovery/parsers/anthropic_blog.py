"""
Scrape Anthropic's developer/engineering blog listing page directly -- no
official RSS feed exists (confirmed 404 on /engineering/rss.xml,
/news/rss.xml, /research/rss.xml, /index.xml). The listing page IS
server-side rendered with the full post list in the initial HTML,
confirmed via a direct fetch with a real browser User-Agent -- a request
without one gets served a JS-only shell instead (Next.js bot-differentiated
response), which is what caused an earlier, incorrect "needs a headless
browser" conclusion.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

LISTING_URL = "https://www.anthropic.com/engineering"
BASE_URL = "https://www.anthropic.com"

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Matches on structure (article boundary + href/h3/date), not on Next.js's
# hashed CSS module class suffix, which can change on any site redeploy.
_ARTICLE_PATTERN = re.compile(
    r'<article[^>]*class="[^"]*__article"[^>]*>.*?</article>', re.DOTALL
)
_HREF_PATTERN = re.compile(r'href="(/engineering/[^"]+)"')
_TITLE_PATTERN = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL)
_DATE_PATTERN = re.compile(r'class="[^"]*__date"[^>]*>([^<]+)</div>')
_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _parse_date(raw: str) -> str:
    try:
        return (
            datetime.strptime(raw.strip(), "%b %d, %Y")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def fetch_anthropic_engineering(
    url: str = LISTING_URL, limit: int = 30, max_age_hours: int | None = None
) -> ParseResult:
    """Fetch and parse Anthropic's engineering blog listing page.

    `url` defaults to LISTING_URL but is overridable so the caller can
    source it from discovery/config/blog_sources.yaml instead of this
    module's hardcoded constant.

    max_age_hours (2026-07-26, real production bug: run 08b5d13b served
    posts from 2026-02-05 and 2026-03-24/25 as if new, because this was
    the only blog_sources.yaml entry with no recency cutoff at all --
    every feed_url entry already gets one via
    discovery/parsers/rss_common.py's fetch_rss_feed). Same contract as
    fetch_rss_feed's own max_age_hours: when set, an entry whose parsed
    date is older than this many hours is dropped before it's ever
    returned. Only applied when a real date was actually parsed off the
    page (see _parse_date) -- an entry with no matching _DATE_PATTERN
    falls back to "now" and is always kept, since there's no real
    timestamp to judge staleness against, same as rss_common's handling
    of a missing/unparseable pubDate. A dormant source (nothing published
    in months, real case: this blog has posted nothing since 2026-04-23)
    now correctly yields zero fresh rows on a given run instead of
    re-serving the same stale top-`limit` posts every time.

    Each successfully parsed entry produces a dict with keys matching
    RawItem's shape minus `source`: title, text (always "" -- the listing
    page gives no summary text), url, author_name, author_handle,
    fetched_at, is_thread, thread_contents, expanded_urls, has_video,
    video_url.
    """
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        if max_age_hours is not None else None
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        for block in _ARTICLE_PATTERN.findall(html)[:limit]:
            href_match = _HREF_PATTERN.search(block)
            title_match = _TITLE_PATTERN.search(block)
            if not href_match or not title_match:
                continue

            date_match = _DATE_PATTERN.search(block)
            title = _TAG_PATTERN.sub("", title_match.group(1)).strip()
            fetched_at = _parse_date(date_match.group(1)) if date_match else datetime.now(timezone.utc).isoformat()

            if cutoff is not None and date_match and datetime.fromisoformat(fetched_at) < cutoff:
                continue

            rows.append({
                "title": title,
                "text": "",
                "url": BASE_URL + href_match.group(1),
                "author_name": "Anthropic",
                "author_handle": "",
                "fetched_at": fetched_at,
                "is_thread": False,
                "thread_contents": None,
                "expanded_urls": [],
                "has_video": False,
                "video_url": None,
            })
    except Exception as e:
        errors.append(("anthropic_engineering", str(e)))

    return ParseResult(rows=rows, errors=errors)
