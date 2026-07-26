import sys
import os
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
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


def _mock_response(body: bytes, content_type: str = "application/rss+xml"):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers.get_content_charset.return_value = "utf-8"
    resp.headers.get_content_type.return_value = content_type
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
    """scrape_blogs.py passes max_age_hours=48 to fetch_rss_feed for
    bucket=daily entries (e.g. TLDR AI)."""
    stale_36h = _NOW - timedelta(hours=36)   # within 48h, kept
    stale_60h = _NOW - timedelta(hours=60)   # outside 48h, dropped
    body = _rss([
        ("Within window", "https://example.com/a", stale_36h),
        ("Outside window", "https://example.com/b", stale_60h),
    ])
    daily_entry = [{"name": "TLDR AI", "feed_url": "https://example.com/feed", "bucket": "daily"}]
    with patch("urllib.request.urlopen", return_value=_mock_response(body)), \
         patch("discovery.parsers.scrape_blogs.entries_for_context", return_value=daily_entry):
        from discovery.parsers.scrape_blogs import fetch_blog_entries
        result = fetch_blog_entries("daily")

    titles = [r["title"] for r in result.rows]
    assert titles == ["Within window"]


def test_parse_error_dumps_raw_body_to_logs_parse_errors(tmp_path):
    """On an XML ParseError, the raw (pre-decode) response body must be
    written to logs/parse_errors/{source}_{timestamp}.xml -- the only way
    to get byte-level proof of a malformed feed the next time this fires
    in CI, since manual refetches after the fact keep missing the moment."""
    malformed_body = b"<rss><channel><item><title>Bad & unescaped</title></item>"  # never closed, invalid token

    import discovery.parsers.rss_common as rss_common_mod
    fake_dir = tmp_path / "logs" / "parse_errors"
    with patch("urllib.request.urlopen", return_value=_mock_response(malformed_body)), \
         patch.object(rss_common_mod, "_PARSE_ERROR_LOG_DIR", fake_dir):
        result = fetch_rss_feed("https://example.com/feed", source_name="Bad Source")

    assert len(result.errors) == 1
    assert result.errors[0][0] == "Bad Source"

    dumped = list(fake_dir.glob("Bad_Source_*.xml"))
    assert len(dumped) == 1


def test_bare_ampersand_recovers_via_fallback_retry(tmp_path):
    """MarkTechPost's real "not well-formed (invalid token)" failure (hit 3
    times in production, 2026-07-19/07-20) is the canonical ElementTree
    error for a bare `&` in text content -- e.g. "Q&A" instead of "Q&amp;A".
    An otherwise well-formed feed containing exactly that must now recover
    via the bare-ampersand-escape retry, not be treated as a hard failure:
    the row is returned, zero errors, and no diagnostic dump is written
    (dumping is reserved for the case where even the fallback fails)."""
    malformed_body = (
        b"<rss><channel><item>"
        b"<title>Q&A with the DevRel team</title>"
        b"<link>https://example.com/qa</link>"
        b"</item></channel></rss>"
    )

    import discovery.parsers.rss_common as rss_common_mod
    fake_dir = tmp_path / "logs" / "parse_errors"
    with patch("urllib.request.urlopen", return_value=_mock_response(malformed_body)), \
         patch.object(rss_common_mod, "_PARSE_ERROR_LOG_DIR", fake_dir):
        result = fetch_rss_feed("https://example.com/feed", source_name="MarkTechPost")

    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0]["title"] == "Q&A with the DevRel team"
    assert result.rows[0]["url"] == "https://example.com/qa"

    # the fallback succeeded -- nothing should have been dumped
    assert not fake_dir.exists() or list(fake_dir.glob("*.xml")) == []


def test_unparseable_even_after_ampersand_fallback_still_fails_cleanly(tmp_path):
    """A bare `&` is not the only way to be malformed -- if the fallback
    retry still can't parse it (e.g. genuinely truncated/unclosed XML),
    this must still degrade to a clean per-source error (dumped for
    forensics) rather than raising and taking down the whole scrape_blogs
    fan-out -- unchanged behavior from before this fix, just confirming
    the new fallback layer doesn't break the existing failure path."""
    malformed_body = b"<rss><channel><item><title>Bad & unescaped and never closed"

    import discovery.parsers.rss_common as rss_common_mod
    fake_dir = tmp_path / "logs" / "parse_errors"
    with patch("urllib.request.urlopen", return_value=_mock_response(malformed_body)), \
         patch.object(rss_common_mod, "_PARSE_ERROR_LOG_DIR", fake_dir):
        result = fetch_rss_feed("https://example.com/feed", source_name="MarkTechPost")

    assert len(result.errors) == 1
    assert result.errors[0][0] == "MarkTechPost"
    assert result.rows == []

    dumped = list(fake_dir.glob("MarkTechPost_*.xml"))
    assert len(dumped) == 1
    assert dumped[0].read_bytes() == malformed_body
    assert dumped[0].read_bytes() == malformed_body


def test_bot_challenge_page_classified_distinctly_from_malformed_xml(tmp_path):
    """MarkTechPost, 2026-07-22: a real 200 whose body is a Cloudflare
    bot-challenge interstitial (confirmed via a raw-body dump showing a
    `/.well-known/sgcaptcha/` redirect), not the feed itself. This must be
    reported as a distinct "blocked by bot-challenge" error -- not the
    generic ElementTree "not well-formed" message a malformed feed
    produces -- so the two causes are distinguishable from daily_run.log
    alone, without pulling the raw artifact dump."""
    challenge_body = (
        b'<html><head><link rel="icon" href="data:;">'
        b'<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2Ffeed%2F">'
        b"</meta></head></html>"
    )

    import discovery.parsers.rss_common as rss_common_mod
    fake_dir = tmp_path / "logs" / "parse_errors"
    with patch("urllib.request.urlopen", return_value=_mock_response(challenge_body, content_type="text/html")), \
         patch.object(rss_common_mod, "_PARSE_ERROR_LOG_DIR", fake_dir):
        result = fetch_rss_feed("https://www.marktechpost.com/feed/", source_name="MarkTechPost")

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "MarkTechPost"
    assert "bot-challenge" in result.errors[0][1]
    assert "not well-formed" not in result.errors[0][1]

    dumped = list(fake_dir.glob("MarkTechPost_*.xml"))
    assert len(dumped) == 1
    assert dumped[0].read_bytes() == challenge_body


def test_real_xml_content_type_never_misclassified_as_bot_challenge():
    """A feed whose content happens to mention a word like "cloudflare" in
    a post title/body must not be misclassified as a bot-challenge purely
    from body text -- the content-type check must short-circuit first for
    any real xml/rss response."""
    body = _rss([("Cloudflare just a moment... outage postmortem", "https://example.com/a", _NOW - timedelta(hours=2))])
    with patch("urllib.request.urlopen", return_value=_mock_response(body, content_type="application/rss+xml")):
        result = fetch_rss_feed("https://example.com/feed", source_name="test")

    assert result.errors == []
    assert len(result.rows) == 1


def test_saturday_bucket_sources_use_216h_cutoff():
    """scrape_blogs.py passes max_age_hours=216 (9 days) to fetch_rss_feed
    for bucket=saturday entries."""
    within = _NOW - timedelta(hours=200)   # within 216h, kept
    outside = _NOW - timedelta(hours=300)  # outside 216h, dropped
    body = _rss([
        ("Within 9 days", "https://example.com/a", within),
        ("Outside 9 days", "https://example.com/b", outside),
    ])
    saturday_entry = [{"name": "LangChain Blog", "feed_url": "https://example.com/feed", "bucket": "saturday"}]
    with patch("urllib.request.urlopen", return_value=_mock_response(body)), \
         patch("discovery.parsers.scrape_blogs.entries_for_context", return_value=saturday_entry):
        from discovery.parsers.scrape_blogs import fetch_blog_entries
        result = fetch_blog_entries("saturday")

    titles = [r["title"] for r in result.rows]
    assert titles == ["Within 9 days"]


def test_full_text_prefers_content_encoded_over_description_real_fixture():
    """The confirmed root cause of the empty-digest investigation: item
    text was read from RSS's <description> teaser only, discarding the
    much longer <content:encoded> body already present in the same
    response. Real fixture, captured live 2026-07-22 from
    https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top --
    <description> is 51 chars ("Several new Cyber headlines make us
    observe a trend"), <content:encoded> is 29,368 chars of the real
    article. `text` must now be the long one, not the 51-char teaser
    that produced a cosine=0.186 taste-prefilter drop on 2026-07-22.

    pubDate is rewritten to 2 hours before whenever this test actually
    runs (2026-07-26 fix) -- the fixture's real captured pubDate is a
    fixed 2026-07-22 timestamp, which drifted past this call's own
    max_age_hours=48 cutoff days ago and will keep drifting further out
    of range every day this suite exists. What's under test here is the
    content:encoded-vs-description preference, not the real pubDate
    value itself, so relative-to-now is the honest fix rather than
    re-editing a fixed date that would only buy another couple of days.
    Everything else in the fixture (title, content:encoded, description)
    stays exactly as captured."""
    fixture_path = Path(__file__).parent / "fixtures" / "latent_space_cybersecurity_item.xml"
    raw_xml = fixture_path.read_text(encoding="utf-8")
    fresh_pubdate = format_datetime(datetime.now(timezone.utc) - timedelta(hours=2))
    raw_xml = re.sub(r"<pubDate>.*?</pubDate>", f"<pubDate>{fresh_pubdate}</pubDate>", raw_xml)
    body = raw_xml.encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_mock_response(body, content_type="application/rss+xml")):
        result = fetch_rss_feed(
            "https://www.latent.space/feed", source_name="Latent Space", max_age_hours=48
        )

    assert result.errors == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["url"] == "https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top"
    # Not the 51-char description teaser -- the real article body.
    assert len(row["text"]) > 25000, f"expected the full ~29,368-char article, got {len(row['text'])} chars"
    assert "Several new Cyber headlines make us observe a trend" not in row["text"][:100]


def test_full_text_falls_back_to_description_when_content_encoded_absent():
    """TLDR AI and plain feeds with no <content:encoded> at all must keep
    working exactly as before -- <description> stays the source, and an
    absent/empty <description> still degrades to "" rather than raising."""
    body = (
        b"<rss><channel><item>"
        b"<title>Plain feed item</title>"
        b"<link>https://example.com/plain</link>"
        b"<description>Just a short teaser, no content:encoded here.</description>"
        b"</item></channel></rss>"
    )
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test")

    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0]["text"] == "Just a short teaser, no content:encoded here."


def test_full_text_empty_content_encoded_falls_back_to_description():
    """An empty <content:encoded/> tag (present but blank) must not win
    over a real, non-empty <description> -- "present but empty" is
    treated the same as "absent"."""
    body = (
        b'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        b"<channel><item>"
        b"<title>Empty encoded item</title>"
        b"<link>https://example.com/empty-encoded</link>"
        b"<description>Real teaser text here.</description>"
        b"<content:encoded></content:encoded>"
        b"</item></channel></rss>"
    )
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = fetch_rss_feed("https://example.com/feed", source_name="test")

    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0]["text"] == "Real teaser text here."
