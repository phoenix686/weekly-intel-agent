"""
Fetch every source configured in discovery/config/blog_sources.yaml that's
active for the current invocation context (daily/saturday), and parse
entries into plain Python dicts. This is the single generic source
fetcher -- TLDR AI, Smol AI News, and Anthropic's dev blog used to each
have their own dedicated node file; now that blog_sources.yaml exists as
the one config, there's no remaining reason to keep them separate.

Dispatches per entry: `feed_url` entries go through
discovery/parsers/rss_common.py's fetch_rss_feed() (RSS/Atom);
`scrape_url` entries (currently only Anthropic's dev blog, which has no
RSS feed) go through discovery/parsers/anthropic_blog.py's
fetch_anthropic_engineering(); `feed_url` entries additionally marked
`roundup: true` (currently only TLDR AI, whose RSS carries real
title/link/pubDate per issue but zero real content -- confirmed
2026-07-22) go through discovery/parsers/tldr_ai.py's
fetch_tldr_roundup() instead, which fetches each surviving issue's own
roundup page and parses it into its individual blurbs rather than
treating the whole day's page as one item.

AgentMail-sourced newsletters (discovery/config/agentmail_sources.yaml,
gitignored -- real sender-address-to-source-name mapping for a shared
inbox) are NOT a blog_sources.yaml entry at all -- one shared inbox
covers up to 10 real senders at once, which doesn't fit this file's
one-entry-per-fetch model. fetch_agentmail_sources() below is a
separate, parallel path discovery/nodes/scrape_blogs.py calls directly,
producing its own per-real-sender SourceResults from one shared
messages.list() call.

No langgraph imports, no I/O side effects beyond HTTP fetches.
Row-level failures are collected in ParseResult.errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from discovery.parsers.rss_common import fetch_rss_feed
from discovery.parsers.anthropic_blog import fetch_anthropic_engineering
from discovery.parsers.tldr_ai import fetch_tldr_roundup
from discovery.parsers.agentmail_newsletters import fetch_agentmail_newsletters
from discovery.blog_sources_config import entries_for_context
from discovery.agentmail_sources_config import load_agentmail_config

# Heuristic only (LangChain's feed has no <category> distinguishing case
# studies from technical posts) -- title/text keyword match. Not perfect;
# score_node's own taste-profile scoring is the second line of defense.
_LANGCHAIN_CASE_STUDY_MARKERS = (
    "case study", "success story", "customer story",
    "how [company]", "partners with", "chose langchain", "chose langgraph",
)
_LANGCHAIN_FEED_URL = "https://blog.langchain.dev/rss.xml"

# pubDate pre-filter window per bucket -- see discovery/parsers/rss_common.py's
# max_age_hours. Applied uniformly to every entry type, including scrape_url
# (fetch_anthropic_engineering gained max_age_hours support 2026-07-26 --
# it was the one entry silently exempt from this cutoff, which let a
# dormant source re-serve the same stale top-N posts every run).
_MAX_AGE_HOURS_BY_BUCKET = {"daily": 48, "saturday": 216}


def _is_langchain_case_study(row: dict) -> bool:
    haystack = f"{row['title']} {row['text']}".lower()
    return any(marker in haystack for marker in _LANGCHAIN_CASE_STUDY_MARKERS)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SourceResult:
    """Per-source fetch outcome -- lets the node stamp one NodeCost per
    source (name, error) instead of one per node invocation, now that
    NodeCost.error exists (state-nodecost-error-field, Checkpoint 1)."""
    name: str
    rows: list[dict]
    error: str | None = None


_DEFAULT_FETCH_LIMIT = 30


def fetch_one_source(entry: dict) -> SourceResult:
    """Dispatch a single blog_sources.yaml entry to its fetcher (feed_url
    -> fetch_rss_feed, scrape_url -> fetch_anthropic_engineering), applying
    the same per-entry filtering fetch_blog_entries always has (LangChain
    case-study heuristic, blank-title drop). A fetch failure produces zero
    rows and a non-None error message -- never raises. Public (not
    underscore-prefixed): discovery/nodes/scrape_blogs.py calls this
    directly, one entry at a time, so it can time each source's real fetch
    latency individually for its own NodeCost record.

    entry['fetch_limit'] (optional, per blog_sources.yaml entry) caps how
    many items are fetched from that source -- falls back to
    _DEFAULT_FETCH_LIMIT when the entry doesn't set one."""
    fetch_limit = entry.get("fetch_limit", _DEFAULT_FETCH_LIMIT)

    if "feed_url" in entry and entry.get("roundup"):
        max_age = _MAX_AGE_HOURS_BY_BUCKET[entry["bucket"]]
        result = fetch_tldr_roundup(
            entry["feed_url"], source_name=entry["name"], limit=fetch_limit, max_age_hours=max_age
        )
        rows = [row for row in result.rows if row["title"]]
        error = "; ".join(msg for _, msg in result.errors) if result.errors else None
        return SourceResult(name=entry["name"], rows=rows, error=error)

    if "feed_url" in entry:
        max_age = _MAX_AGE_HOURS_BY_BUCKET[entry["bucket"]]
        result = fetch_rss_feed(
            entry["feed_url"], source_name=entry["name"], limit=fetch_limit, max_age_hours=max_age
        )
        rows = [
            row for row in result.rows
            if row["title"]
            and not (entry["feed_url"] == _LANGCHAIN_FEED_URL and _is_langchain_case_study(row))
        ]
        error = result.errors[0][1] if result.errors else None
        return SourceResult(name=entry["name"], rows=rows, error=error)

    max_age = _MAX_AGE_HOURS_BY_BUCKET[entry["bucket"]]
    result = fetch_anthropic_engineering(url=entry["scrape_url"], limit=fetch_limit, max_age_hours=max_age)
    rows = [row for row in result.rows if row["title"]]
    error = result.errors[0][1] if result.errors else None
    return SourceResult(name=entry["name"], rows=rows, error=error)


_AGENTMAIL_DEFAULT_FETCH_LIMIT = 20


def fetch_agentmail_sources(source_context: str) -> list[SourceResult]:
    """One shared client.inboxes.messages.list() call covering every
    AgentMail-sourced sender at once, then split into one SourceResult
    PER REAL SENDER (not one generic "AgentMail Newsletters" bucket) --
    source attribution is critical with this many distinct publications
    sharing one inbox. Saturday-only, matching every AgentMail source's
    bucket in discovery/config/agentmail_sources.yaml today.

    Gracefully degrades to a single informative SourceResult (zero rows,
    a clear error) if the gitignored real config doesn't exist on this
    machine yet -- never crashes the rest of the pipeline over a missing
    optional file, same reliability contract as every other source."""
    if source_context != "saturday":
        return []

    try:
        config = load_agentmail_config()
    except FileNotFoundError as e:
        return [SourceResult(name="AgentMail Newsletters", rows=[], error=str(e))]

    inbox_id = config["inbox_id"]
    sources = config.get("sources", [])
    sender_to_name = {s["sender"]: s["name"] for s in sources}
    fetch_limit = config.get("fetch_limit", _AGENTMAIL_DEFAULT_FETCH_LIMIT)

    result = fetch_agentmail_newsletters(inbox_id, sender_to_name, limit=fetch_limit)

    rows_by_name: dict[str, list[dict]] = {s["name"]: [] for s in sources}
    for row in result.rows:
        rows_by_name.setdefault(row["author_name"], []).append(row)

    errors_by_name: dict[str, list[str]] = {}
    for name, message in result.errors:
        errors_by_name.setdefault(name, []).append(message)

    all_names = set(rows_by_name) | set(errors_by_name)
    return [
        SourceResult(
            name=name,
            rows=[row for row in rows_by_name.get(name, []) if row["title"]],
            error="; ".join(errors_by_name[name]) if name in errors_by_name else None,
        )
        for name in sorted(all_names)
    ]


def fetch_blog_entries_per_source(source_context: str) -> list[SourceResult]:
    """One SourceResult per blog_sources.yaml entry active for
    source_context ("daily" or "saturday" -- saturday is a superset, see
    blog_sources_config.entries_for_context). A single failing source
    never affects another's result -- each entry is fetched and handled
    independently."""
    return [fetch_one_source(entry) for entry in entries_for_context(source_context)]


def fetch_blog_entries(source_context: str) -> ParseResult:
    """Flat aggregate view over fetch_blog_entries_per_source() -- kept for
    existing callers (tests/test_rss_common_max_age.py) that only need the
    combined rows/errors, not per-source granularity.

    Each successfully parsed entry produces a dict with keys:
        title, text, url, author_name, author_handle, is_thread,
        thread_contents, fetched_at, expanded_urls, has_video, video_url

    Entry-level failures are appended to ParseResult.errors as
    (entry_name, message) and parsing continues.
    """
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []
    for source_result in fetch_blog_entries_per_source(source_context):
        rows.extend(source_result.rows)
        if source_result.error is not None:
            errors.append((source_result.name, source_result.error))
    return ParseResult(rows=rows, errors=errors)
