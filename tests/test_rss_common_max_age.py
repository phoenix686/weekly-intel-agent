import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch, MagicMock

from discovery.parsers.rss_common import fetch_rss_feed

_NOW = datetime.now(timezone.utc)


def _rss(items: list[tuple[str, str, datetime | None]]) -> bytes:
    """items: list of (title, link, pubdate | None)."""
    entries = []
    for title, link, pubdate in items:
        pubdate_tag = f"<pubDate>{format_datetime(pubdate)}</pubDate>" if pubdate else ""
        entries.append(f"<item><title>{title}</title><link>{link}</link>{pubdate_tag}</item>")
    body = "<rss><channel>" + "".join(entries) + "</channel></rss>"
    return body.encode("utf-8")


def _mock_response(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers.get_content_charset.return_value = "utf-8"
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_max_age_hours_drops_items_older_than_cutoff():
    fresh = _NOW - timedelta(hours=2)
    stale = _NOW - timedelta(hours=100)
    body = _rss([
        ("Fresh item", "https://example.com/fresh", fresh),
        ("Stale item", "https://example.com/stale", stale),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test", max_age_hours=48)

    titles = [r["title"] for r in result.rows]
    assert titles == ["Fresh item"]


def test_max_age_hours_none_keeps_everything():
    fresh = _NOW - timedelta(hours=2)
    stale = _NOW - timedelta(hours=1000)
    body = _rss([
        ("Fresh item", "https://example.com/fresh", fresh),
        ("Stale item", "https://example.com/stale", stale),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test", max_age_hours=None)

    titles = {r["title"] for r in result.rows}
    assert titles == {"Fresh item", "Stale item"}


def test_missing_pubdate_always_kept_even_with_cutoff():
    body = _rss([("No date item", "https://example.com/nodate", None)])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test", max_age_hours=1)

    assert len(result.rows) == 1
    assert result.rows[0]["title"] == "No date item"


def test_item_exactly_at_cutoff_boundary_kept():
    body = _rss([("Boundary item", "https://example.com/boundary", _NOW - timedelta(hours=48) + timedelta(minutes=1))])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test", max_age_hours=48)

    assert len(result.rows) == 1


def test_daily_bucket_sources_use_48h_cutoff():
    """tldr_ai.py / smol_ai_news.py pass max_age_hours=48 to fetch_rss_feed."""
    stale_36h = _NOW - timedelta(hours=36)   # within 48h, kept
    stale_60h = _NOW - timedelta(hours=60)   # outside 48h, dropped
    body = _rss([
        ("Within window", "https://example.com/a", stale_36h),
        ("Outside window", "https://example.com/b", stale_60h),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        from discovery.nodes.tldr_ai import tldr_ai
        with patch("discovery.nodes.tldr_ai.get_source", return_value={"feed_url": "https://example.com/feed"}):
            result = tldr_ai({})

    titles = [i["title"] for i in result["raw_items"]]
    assert titles == ["Within window"]


def test_sunday_bucket_sources_use_216h_cutoff():
    """scrape_blogs.py passes max_age_hours=216 (9 days) to fetch_rss_feed."""
    within = _NOW - timedelta(hours=200)   # within 216h, kept
    outside = _NOW - timedelta(hours=300)  # outside 216h, dropped
    body = _rss([
        ("Within 9 days", "https://example.com/a", within),
        ("Outside 9 days", "https://example.com/b", outside),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)), \
         patch("discovery.parsers.scrape_blogs.feed_urls_for_bucket", return_value=["https://example.com/feed"]):
        from discovery.parsers.scrape_blogs import fetch_blog_entries
        result = fetch_blog_entries()

    titles = [r["title"] for r in result.rows]
    assert titles == ["Within 9 days"]
