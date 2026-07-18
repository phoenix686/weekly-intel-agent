"""
Per-source fetch_limit override (blog_sources.yaml schema addition,
2026-07-17): fetch_one_source() reads entry['fetch_limit'] and passes it
through to the real fetcher (fetch_rss_feed for feed_url entries,
fetch_anthropic_engineering for scrape_url entries), falling back to
_DEFAULT_FETCH_LIMIT (30) when an entry doesn't set one.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from discovery.parsers.scrape_blogs import fetch_one_source, _DEFAULT_FETCH_LIMIT
from discovery.parsers.rss_common import ParseResult as RssParseResult
from discovery.parsers.anthropic_blog import ParseResult as AnthropicParseResult


def test_feed_url_entry_passes_its_own_fetch_limit():
    entry = {"name": "Test Feed", "feed_url": "https://example.com/feed", "bucket": "sunday", "fetch_limit": 6}

    with patch("discovery.parsers.scrape_blogs.fetch_rss_feed", return_value=RssParseResult(rows=[])) as mock_fetch:
        fetch_one_source(entry)

    mock_fetch.assert_called_once_with(
        "https://example.com/feed", source_name="Test Feed", limit=6, max_age_hours=216
    )


def test_feed_url_entry_without_fetch_limit_falls_back_to_default():
    entry = {"name": "Test Feed", "feed_url": "https://example.com/feed", "bucket": "daily"}

    with patch("discovery.parsers.scrape_blogs.fetch_rss_feed", return_value=RssParseResult(rows=[])) as mock_fetch:
        fetch_one_source(entry)

    mock_fetch.assert_called_once_with(
        "https://example.com/feed", source_name="Test Feed", limit=_DEFAULT_FETCH_LIMIT, max_age_hours=48
    )


def test_scrape_url_entry_passes_its_own_fetch_limit():
    entry = {"name": "Anthropic Engineering Blog", "scrape_url": "https://www.anthropic.com/engineering", "bucket": "sunday", "fetch_limit": 6}

    with patch("discovery.parsers.scrape_blogs.fetch_anthropic_engineering", return_value=AnthropicParseResult(rows=[])) as mock_fetch:
        fetch_one_source(entry)

    mock_fetch.assert_called_once_with(url="https://www.anthropic.com/engineering", limit=6)


def test_scrape_url_entry_without_fetch_limit_falls_back_to_default():
    entry = {"name": "Anthropic Engineering Blog", "scrape_url": "https://www.anthropic.com/engineering", "bucket": "sunday"}

    with patch("discovery.parsers.scrape_blogs.fetch_anthropic_engineering", return_value=AnthropicParseResult(rows=[])) as mock_fetch:
        fetch_one_source(entry)

    mock_fetch.assert_called_once_with(url="https://www.anthropic.com/engineering", limit=_DEFAULT_FETCH_LIMIT)


def test_real_blog_sources_yaml_daily_entries_all_have_fetch_limit_15():
    from discovery.blog_sources_config import load_blog_sources
    entries = load_blog_sources()
    daily_entries = [e for e in entries if e["bucket"] == "daily"]
    assert len(daily_entries) == 4, f"expected 4 daily-bucket entries, got {len(daily_entries)}"
    for entry in daily_entries:
        assert entry.get("fetch_limit") == 15, f"{entry['name']!r} expected fetch_limit=15, got {entry.get('fetch_limit')!r}"


def test_real_blog_sources_yaml_sunday_only_entries_all_have_fetch_limit_6():
    """Six sources moved off RSS onto the shared AgentMail inbox
    (2026-07-18): JamWithAI, The Nuanced Perspective, AI with Aish, The
    Neural Maze, Decoding AI Magazine, Ahead of AI -- none of them are
    blog_sources.yaml entries anymore (see discovery/config/
    agentmail_sources.yaml, gitignored, and blog_sources.yaml's dated
    comment). Hacker News (Show HN) is the one remaining deliberate
    exception to the sunday-bucket default of 6 (fetch_limit=8, higher
    real volume)."""
    from discovery.blog_sources_config import load_blog_sources
    entries = load_blog_sources()
    sunday_entries = [e for e in entries if e["bucket"] == "sunday"]
    assert len(sunday_entries) == 3, f"expected 3 sunday-bucket entries, got {len(sunday_entries)}"
    for entry in sunday_entries:
        expected = 8 if entry["name"] == "Hacker News (Show HN)" else 6
        assert entry.get("fetch_limit") == expected, f"{entry['name']!r} expected fetch_limit={expected}, got {entry.get('fetch_limit')!r}"
