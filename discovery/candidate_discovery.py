"""
Orchestrates Part C steps 1-3: search for candidate sources, filter to
real ongoing publications, sample recent items, and score them against
taste_profile.yaml using the EXISTING score_node -- no new scoring
mechanism is written here. Returns candidates that consistently score
keep=True, ready for a Telegram proposal.

Part C step 4 (the actual Send-based per-candidate proposal subgraph,
reusing await_approval.py's dedicated-thread pattern) is NOT built here --
that's LangGraph node/interrupt authorship and is Pooja's, per the same
line as the rest of this project. This module only produces the list of
qualifying candidates for that subgraph to fan out over.

No langgraph imports. Calls the existing score_node directly -- that file
owns the Anthropic call; this module just invokes it with sampled data,
it doesn't author any new prompt/classification logic.
"""

from __future__ import annotations

import logging
import urllib.request
from urllib.parse import urlparse

from discovery.candidate_search import search_duckduckgo
from discovery.cadence import detect_cadence
from discovery.parsers.rss_common import fetch_rss_feed
from discovery.nodes.score import score_node
from sunday.source_discovery_actions import is_already_rejected

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 5
MIN_KEEP_RATE = 1.0  # "consistently keep=True" -- every sampled item must keep

_FEED_URL_CANDIDATES = ("/feed", "/rss.xml", "/rss", "/feed.xml", "/atom.xml")


def _guess_feed_url(page_url: str) -> str | None:
    """Best-effort: try common feed URL suffixes against the candidate's
    domain. Returns None if none resolve to real XML -- caller treats
    that as 'not an ongoing publication with a discoverable feed'."""
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for suffix in _FEED_URL_CANDIDATES:
        candidate = base + suffix
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": "weekly-intel-bot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200 and "xml" in (resp.headers.get_content_type() or ""):
                    return candidate
        except Exception:
            continue
    return None


def find_candidates(taste_domain_query: str, search_limit: int = 10) -> list[dict]:
    """Returns [{"name", "feed_url", "bucket", "sample_keep_rate",
    "sample_reasoning"}, ...] for candidates worth proposing."""
    hits = search_duckduckgo(taste_domain_query, limit=search_limit)
    candidates: list[dict] = []

    for hit in hits:
        feed_url = _guess_feed_url(hit["url"])
        if feed_url is None:
            continue
        if is_already_rejected(feed_url):
            logger.info(f"find_candidates: skipping previously-rejected {hit['title']}")
            continue

        cadence = detect_cadence(feed_url, source_name=hit["title"])
        if cadence is None:
            continue  # too few items / unfetchable -- not a real ongoing publication

        sample = fetch_rss_feed(feed_url, source_name=hit["title"], limit=SAMPLE_SIZE)
        if not sample.rows:
            continue

        clustered_items = [
            {
                "url": row["url"], "title": row["title"], "text": row["text"],
                "author_name": row["author_name"], "author_handle": row["author_handle"],
                "fetched_at": row["fetched_at"], "is_thread": row["is_thread"],
                "thread_contents": row["thread_contents"], "expanded_urls": row["expanded_urls"],
                "source": hit["title"], "duplicate_count": 1,
            }
            for row in sample.rows
        ]
        scored = score_node({"clustered_items": clustered_items, "run_id": "source-discovery"})
        scored_items = scored["scored_items"]
        keep_rate = sum(1 for i in scored_items if i["keep"]) / len(scored_items)

        if keep_rate >= MIN_KEEP_RATE:
            bucket = "daily" if cadence == "daily" else "sunday"
            candidates.append({
                "name": hit["title"],
                "feed_url": feed_url,
                "bucket": bucket,
                "sample_keep_rate": keep_rate,
                "sample_reasoning": scored_items[0]["reasoning"],
            })
            logger.info(
                f"find_candidates: {hit['title']} qualifies "
                f"(keep_rate={keep_rate:.0%}, bucket={bucket})"
            )

    return candidates
