"""
discovery/parsers/anthropic_blog.py's max_age_hours recency filter
(2026-07-26 fix). Real production bug: run 08b5d13b served three posts
from 2026-02-05 and 2026-03-24/25 as if new -- this was the only
blog_sources.yaml entry with no recency cutoff at all (every feed_url
entry already gets one via discovery/parsers/rss_common.py's
fetch_rss_feed). A dormant source (nothing published since 2026-04-23)
kept re-serving the same stale top-`limit` posts every single run.

Mirrors tests/test_rss_common_max_age.py's fixture/mocking pattern for
the sibling fix in the RSS path.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from discovery.parsers.anthropic_blog import fetch_anthropic_engineering

_NOW = datetime.now(timezone.utc)


def _date_str(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).strftime("%b %d, %Y")


def _listing_html(entries: list[tuple[str, str, str | None]]) -> str:
    """entries: list of (title, slug, date_str | None) -- date_str in the
    real page's own "%b %d, %Y" format (e.g. "Mar 24, 2026"), or None to
    simulate an entry with no matching _DATE_PATTERN at all."""
    blocks = []
    for title, slug, date_str in entries:
        date_html = f'<div class="Post-module__date">{date_str}</div>' if date_str else ""
        blocks.append(
            f'<article class="Post-module__article">'
            f'<a href="/engineering/{slug}"><h3>{title}</h3></a>'
            f'{date_html}'
            f'</article>'
        )
    return "<html><body>" + "".join(blocks) + "</body></html>"


def _mock_response(html: str):
    resp = MagicMock()
    resp.read.return_value = html.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_max_age_hours_drops_items_older_than_cutoff():
    html = _listing_html([
        ("Fresh Post", "fresh-post", _date_str(1)),
        ("Stale Post", "stale-post", _date_str(30)),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        result = fetch_anthropic_engineering(max_age_hours=216)  # 9 days, real saturday-bucket value

    titles = [r["title"] for r in result.rows]
    assert titles == ["Fresh Post"]


def test_max_age_hours_none_keeps_everything():
    """Default behavior (no max_age_hours passed) is unchanged -- every
    existing caller that doesn't opt in keeps getting every row, same as
    before this fix."""
    html = _listing_html([
        ("Fresh Post", "fresh-post", _date_str(1)),
        ("Ancient Post", "ancient-post", _date_str(365)),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        result = fetch_anthropic_engineering()

    titles = {r["title"] for r in result.rows}
    assert titles == {"Fresh Post", "Ancient Post"}


def test_missing_date_always_kept_even_with_cutoff():
    """An entry with no matching _DATE_PATTERN has no real timestamp to
    judge staleness against -- always kept, same contract as
    rss_common.fetch_rss_feed's missing/unparseable pubDate handling."""
    html = _listing_html([("No Date Post", "no-date-post", None)])
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        result = fetch_anthropic_engineering(max_age_hours=1)

    assert len(result.rows) == 1
    assert result.rows[0]["title"] == "No Date Post"


def test_dormant_source_yields_zero_rows_under_real_saturday_cutoff():
    """Direct regression test for the real bug: a source that's been
    dormant well past the 216h (9-day) saturday-bucket window must yield
    zero rows, not silently re-serve its unchanging top-N listing every
    week."""
    html = _listing_html([
        ("Old Post 1", "old-post-1", _date_str(60)),
        ("Old Post 2", "old-post-2", _date_str(94)),
        ("Old Post 3", "old-post-3", _date_str(140)),
    ])
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        result = fetch_anthropic_engineering(max_age_hours=216)

    assert result.rows == []
