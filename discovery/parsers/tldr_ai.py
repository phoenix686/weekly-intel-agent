"""
TLDR AI's RSS feed (https://tldr.tech/api/rss/ai) carries a real title,
link, and pubDate per issue, but zero <description> and zero
<content:encoded> -- confirmed 2026-07-22 via a direct live fetch. Every
issue's real content lives only on its own roundup page
(https://tldr.tech/ai/{date}), confirmed (same live check) to be a
multi-item page, NOT a single article: the 2026-07-21 issue has 19
<article> blocks across 5 sections (Headlines & Launches, Deep Dives &
Analysis, Engineering & Research, Miscellaneous, Quick Links), each with
its own title, snippet, and outbound link. Modeling a whole day's page as
one RawItem would force many unrelated stories into a single scored item;
instead each blurb becomes its own separate RawItem, the same way any
other multi-item source (RSS, HN, etc.) contributes multiple rows per
fetch.

Two-stage fetch, reusing existing infra rather than duplicating it:
  1. discovery/parsers/rss_common.py's fetch_rss_feed() over the RSS feed
     itself, used here for ISSUE-LEVEL discovery only (real title/link/
     pubDate per day, even though description/content:encoded are empty)
     -- gets the same max_age_hours staleness cutoff, network error and
     malformed-XML/bot-challenge classification as every other RSS
     source, for free. entry["fetch_limit"] caps how many ISSUES this
     stage considers, same as any other feed_url entry.
  2. For each surviving issue (normally 1-2 per daily-bucket run, given
     TLDR's near-daily weekday cadence and the 48h cutoff), fetch that
     issue's own roundup page and parse it into its individual blurbs.
     A failure fetching or parsing one issue's page is its own error
     entry -- never blocks another issue or another source.

Sponsored blurbs are skipped -- confirmed real, 2026-07-21 issue: titles
end literally in "(Sponsor)" (e.g. "Serverless Fine Tuning: ... (Sponsor)",
"Verda: ... (Sponsor)"), a stable textual marker, not a guess.

No langgraph imports.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.request
from dataclasses import dataclass, field

from discovery.parsers.rss_common import fetch_rss_feed

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Structural, not styling-based (no dependence on Tailwind utility classes
# like "mt-3", which could change on a redesign) -- confirmed real, every
# <article> on a real TLDR issue page is a blurb (no other use of the tag
# on the page: 19 <article> blocks, 19 real blurbs, 2026-07-21 issue).
_ARTICLE_PATTERN = re.compile(r"<article[^>]*>.*?</article>", re.DOTALL)
_HREF_TITLE_PATTERN = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>', re.DOTALL)
_SNIPPET_PATTERN = re.compile(r'<div[^>]*class="[^"]*newsletter-html[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")

# Confirmed real (2026-07-21 issue): "AMD's Helios (4 minute read)" --
# reading-time noise, not real title content. Deliberately narrow (only
# "(N minute read)") -- other parenthetical suffixes TLDR uses, e.g.
# "Kimi Work (Website)", are a real content-type annotation, not noise,
# and are left alone.
_READING_TIME_SUFFIX = re.compile(r"\s*\(\d+\s*minute read\)\s*$", re.IGNORECASE)

# Confirmed real (2026-07-21 issue): sponsored blurbs' titles end
# literally in "(Sponsor)" -- e.g. "Serverless Fine Tuning: ... (Sponsor)".
_SPONSOR_TITLE = re.compile(r"\(sponsor\)\s*$", re.IGNORECASE)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _clean_text(raw: str) -> str:
    return html_module.unescape(_TAG_PATTERN.sub("", raw or "")).strip()


def parse_issue_page(html: str, fetched_at: str) -> list[dict]:
    """Parses one TLDR issue roundup page into its individual blurbs.
    Public (not underscore-prefixed) so tests can exercise it directly
    against a real captured page fixture without a live fetch."""
    rows: list[dict] = []
    for block in _ARTICLE_PATTERN.findall(html):
        match = _HREF_TITLE_PATTERN.search(block)
        if not match:
            continue
        href, raw_title = match.group(1), match.group(2)
        title = _clean_text(raw_title)
        if not title or _SPONSOR_TITLE.search(title):
            continue
        title = _READING_TIME_SUFFIX.sub("", title).strip()

        snippet_match = _SNIPPET_PATTERN.search(block)
        text = _clean_text(snippet_match.group(1)) if snippet_match else ""

        rows.append({
            "title": title,
            "text": text,
            "url": href,
            "author_name": "TLDR AI",
            "author_handle": "",
            "fetched_at": fetched_at,
            "is_thread": False,
            "thread_contents": None,
            "expanded_urls": [],
            "has_video": False,
            "video_url": None,
        })
    return rows


def _fetch_issue_page(url: str, fetched_at: str, source_name: str) -> ParseResult:
    """Stage 2 for a single issue: fetch its roundup page and parse its
    blurbs. A network/HTTP failure and a "fetched fine but found zero
    blurbs" outcome (page structure changed, or a non-TLDR response) are
    both real, distinctly-worded error entries -- neither is silently
    swallowed as a quiet zero-item source, matching this project's
    standing per-source error-visibility contract (see
    discovery/parsers/rss_common.py's malformed-XML vs bot-challenge
    distinction for the same rationale)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return ParseResult(rows=[], errors=[(source_name, f"issue page {url}: {e}")])

    rows = parse_issue_page(page_html, fetched_at)
    if not rows:
        return ParseResult(rows=[], errors=[(
            source_name,
            f"issue page {url}: fetched but found 0 blurbs (page structure may have changed)",
        )])
    return ParseResult(rows=rows, errors=[])


def fetch_tldr_roundup(feed_url: str, source_name: str, limit: int, max_age_hours: int) -> ParseResult:
    """Stage 1 (issue-level discovery via fetch_rss_feed, real title/link/
    pubDate, same max_age_hours cutoff/UA/error-classification as every
    other RSS source) + Stage 2 (fetch and parse each surviving issue's
    own roundup page). `limit` bounds how many ISSUES stage 1 considers,
    same as any other feed_url entry's fetch_limit -- the real per-run
    blurb count is (issues surviving the cutoff) * (~15-19 blurbs/issue),
    not itself limited per issue, since a full day's roundup is the
    natural per-issue unit.

    One failing issue's page fetch/parse is its own error entry and never
    blocks another issue -- same per-item resilience contract as every
    other multi-item source in this project."""
    issue_result = fetch_rss_feed(feed_url, source_name=source_name, limit=limit, max_age_hours=max_age_hours)
    rows: list[dict] = []
    errors: list[tuple[str, str]] = list(issue_result.errors)

    for issue in issue_result.rows:
        page_result = _fetch_issue_page(issue["url"], issue["fetched_at"], source_name)
        rows.extend(page_result.rows)
        errors.extend(page_result.errors)

    return ParseResult(rows=rows, errors=errors)
