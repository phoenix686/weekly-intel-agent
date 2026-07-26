"""
Schema + live-fetch verification for discovery/config/blog_sources.yaml,
matching blog-sources-yaml-scaffold's original verification text: YAML
parses, every entry has a name plus exactly one of feed_url/scrape_url/
agentmail_inbox_id (agentmail_inbox_id added 2026-07-18, see discovery/
parsers/agentmail_newsletters.py -- a fetch mechanism this file's live
tests can't exercise the same way, since it requires a real AgentMail
inbox, not a public HTTP fetch), at least 2 entries present, and every
feed_url entry returns real content on a live fetch (not assumed from a
prior commit).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from discovery.blog_sources_config import load_blog_sources, CONFIG_PATH
from discovery.parsers.rss_common import fetch_rss_feed


def test_yaml_parses_without_error():
    entries = load_blog_sources()
    assert isinstance(entries, list)


def test_every_entry_has_name_and_exactly_one_url_key():
    entries = load_blog_sources()
    for entry in entries:
        assert "name" in entry and entry["name"], f"entry missing name: {entry}"
        key_count = sum(k in entry for k in ("feed_url", "scrape_url", "agentmail_inbox_id"))
        assert key_count == 1, (
            f"{entry['name']!r} must have exactly one of feed_url/scrape_url/agentmail_inbox_id, got: {entry}"
        )


def test_every_entry_has_a_bucket():
    entries = load_blog_sources()
    for entry in entries:
        assert entry.get("bucket") in ("daily", "saturday"), f"bad/missing bucket: {entry}"


def test_at_least_two_entries_present():
    entries = load_blog_sources()
    assert len(entries) >= 2


@pytest.mark.parametrize("entry", load_blog_sources(), ids=lambda e: e["name"])
def test_feed_url_entries_return_real_content_live(entry):
    if "feed_url" not in entry:
        reason = "uses scrape_url, not feed_url" if "scrape_url" in entry else "uses agentmail_inbox_id, not feed_url -- no public HTTP fetch to test live here"
        pytest.skip(f"{entry['name']} {reason}")
    result = fetch_rss_feed(entry["feed_url"], source_name=entry["name"], limit=5)
    assert not result.errors, f"{entry['name']} ({entry['feed_url']}) fetch failed: {result.errors}"
    assert len(result.rows) > 0, f"{entry['name']} ({entry['feed_url']}) returned zero items"
