"""
discovery/parsers/tldr_ai.py -- covers real-fixture blurb parsing
(tests/fixtures/tldr_ai_2026-07-21.html, a verbatim excerpt of a real
live fetch of https://tldr.tech/ai/2026-07-21, 2026-07-22) and the
two-stage fetch_tldr_roundup() orchestration.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from unittest.mock import patch

from discovery.parsers.rss_common import ParseResult as RssParseResult
from discovery.parsers.tldr_ai import parse_issue_page, fetch_tldr_roundup
import discovery.parsers.tldr_ai as tldr_ai_mod

_FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "tldr_ai_2026-07-21.html").read_text(encoding="utf-8")


def test_parse_issue_page_extracts_multiple_distinct_real_blurbs():
    """The real fixture contains 1 sponsor block + 7 real blurbs (3 in
    Headlines & Launches, 4 in Deep Dives & Analysis). Sponsor excluded,
    7 real, distinct, scorable items remain."""
    rows = parse_issue_page(_FIXTURE_HTML, fetched_at="2026-07-21T00:00:00+00:00")

    assert len(rows) == 7
    titles = [r["title"] for r in rows]
    assert "Kimi Work (Website)" in titles
    assert "AMD's Helios" in titles
    assert "On Kimi K3: Its Capabilities And Related Discontents" in titles
    # Every row is its own distinct URL, not one giant blob for the whole page.
    assert len({r["url"] for r in rows}) == 7


def test_parse_issue_page_skips_sponsored_blurbs():
    rows = parse_issue_page(_FIXTURE_HTML, fetched_at="2026-07-21T00:00:00+00:00")
    assert not any("sponsor" in r["title"].lower() for r in rows)
    assert not any("crusoe" in r["url"].lower() for r in rows)


def test_parse_issue_page_strips_reading_time_suffix_but_keeps_other_parens():
    rows = parse_issue_page(_FIXTURE_HTML, fetched_at="2026-07-21T00:00:00+00:00")
    by_title = {r["title"]: r for r in rows}

    assert "AMD's Helios" in by_title  # "(4 minute read)" stripped
    assert "AMD's Helios (4 minute read)" not in by_title
    # "(Website)" is a real content-type annotation, not reading-time noise -- kept.
    assert "Kimi Work (Website)" in by_title


def test_parse_issue_page_decodes_entities_and_produces_substantive_text():
    rows = parse_issue_page(_FIXTURE_HTML, fetched_at="2026-07-21T00:00:00+00:00")
    helios = next(r for r in rows if r["title"] == "AMD's Helios")
    assert "'" in helios["title"]  # &#x27; decoded, not left raw
    assert len(helios["text"]) > 100  # real substantive snippet, not just a title
    assert "Nvidia" in helios["text"]


def test_parse_issue_page_strips_nested_tags_from_snippet_html():
    """Even though the sponsor block itself is skipped, the fixture proves
    parse_issue_page must tolerate a snippet with nested <a>/<p>/<ul> markup
    (real TLDR HTML) without leaking raw tags into any kept row's text."""
    rows = parse_issue_page(_FIXTURE_HTML, fetched_at="2026-07-21T00:00:00+00:00")
    for row in rows:
        assert "<" not in row["text"]
        assert "<" not in row["title"]


def test_parse_issue_page_returns_empty_for_a_page_with_no_articles():
    assert parse_issue_page("<html><body>not a real TLDR page</body></html>", fetched_at="now") == []


def test_fetch_tldr_roundup_stage1_discovers_issues_stage2_fetches_each_page():
    """Two issues survive stage 1 (RSS discovery); each issue's own page
    is fetched and parsed independently in stage 2."""
    issue_result = RssParseResult(rows=[
        {"title": "issue title 1", "text": "", "url": "https://tldr.tech/ai/2026-07-22",
         "author_name": "TLDR", "author_handle": "", "fetched_at": "2026-07-22T00:00:00+00:00",
         "is_thread": False, "thread_contents": None, "expanded_urls": []},
        {"title": "issue title 2", "text": "", "url": "https://tldr.tech/ai/2026-07-21",
         "author_name": "TLDR", "author_handle": "", "fetched_at": "2026-07-21T00:00:00+00:00",
         "is_thread": False, "thread_contents": None, "expanded_urls": []},
    ])

    class _FakeResponse:
        def __init__(self, body):
            self._body = body.encode("utf-8")
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=15):
        return _FakeResponse(_FIXTURE_HTML)

    with patch.object(tldr_ai_mod, "fetch_rss_feed", return_value=issue_result), \
         patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = fetch_tldr_roundup("https://tldr.tech/api/rss/ai", source_name="TLDR AI", limit=15, max_age_hours=48)

    assert result.errors == []
    assert len(result.rows) == 14  # 7 real blurbs * 2 issues
    assert result.rows[0]["fetched_at"] == "2026-07-22T00:00:00+00:00"
    assert result.rows[7]["fetched_at"] == "2026-07-21T00:00:00+00:00"


def test_fetch_tldr_roundup_one_issue_page_failure_does_not_block_the_other():
    issue_result = RssParseResult(rows=[
        {"title": "issue title 1", "text": "", "url": "https://tldr.tech/ai/2026-07-22",
         "author_name": "TLDR", "author_handle": "", "fetched_at": "2026-07-22T00:00:00+00:00",
         "is_thread": False, "thread_contents": None, "expanded_urls": []},
        {"title": "issue title 2", "text": "", "url": "https://tldr.tech/ai/2026-07-21",
         "author_name": "TLDR", "author_handle": "", "fetched_at": "2026-07-21T00:00:00+00:00",
         "is_thread": False, "thread_contents": None, "expanded_urls": []},
    ])

    class _FakeResponse:
        def __init__(self, body):
            self._body = body.encode("utf-8")
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=15):
        if "2026-07-22" in req.full_url:
            raise TimeoutError("simulated network timeout")
        return _FakeResponse(_FIXTURE_HTML)

    with patch.object(tldr_ai_mod, "fetch_rss_feed", return_value=issue_result), \
         patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = fetch_tldr_roundup("https://tldr.tech/api/rss/ai", source_name="TLDR AI", limit=15, max_age_hours=48)

    assert len(result.rows) == 7  # only the surviving issue's blurbs
    assert len(result.errors) == 1
    assert result.errors[0][0] == "TLDR AI"
    assert "2026-07-22" in result.errors[0][1]
    assert "simulated network timeout" in result.errors[0][1]


def test_fetch_tldr_roundup_zero_blurbs_on_a_page_is_a_distinct_error():
    issue_result = RssParseResult(rows=[
        {"title": "issue title", "text": "", "url": "https://tldr.tech/ai/2026-07-21",
         "author_name": "TLDR", "author_handle": "", "fetched_at": "2026-07-21T00:00:00+00:00",
         "is_thread": False, "thread_contents": None, "expanded_urls": []},
    ])

    class _FakeResponse:
        def __init__(self, body):
            self._body = body.encode("utf-8")
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with patch.object(tldr_ai_mod, "fetch_rss_feed", return_value=issue_result), \
         patch("urllib.request.urlopen", return_value=_FakeResponse("<html><body>no articles here</body></html>")):
        result = fetch_tldr_roundup("https://tldr.tech/api/rss/ai", source_name="TLDR AI", limit=15, max_age_hours=48)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "0 blurbs" in result.errors[0][1]


def test_fetch_tldr_roundup_feed_level_failure_propagates_with_no_issues_to_fetch():
    with patch.object(tldr_ai_mod, "fetch_rss_feed", return_value=RssParseResult(rows=[], errors=[("TLDR AI", "feed-level failure")])), \
         patch("urllib.request.urlopen") as mock_urlopen:
        result = fetch_tldr_roundup("https://tldr.tech/api/rss/ai", source_name="TLDR AI", limit=15, max_age_hours=48)

    assert result.rows == []
    assert result.errors == [("TLDR AI", "feed-level failure")]
    mock_urlopen.assert_not_called()
