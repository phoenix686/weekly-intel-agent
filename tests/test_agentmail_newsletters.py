"""
discovery/parsers/agentmail_newsletters.py -- covers URL extraction,
sender attribution, and the full fetch flow.

_resolve_redirect (the real HTTP redirect-following call) is mocked in
most tests here -- its real behavior was verified directly against real
received welcome emails from Ahead of AI (Substack) and "AI Engineering"
(beehiiv) on 2026-07-18: both wrap every link in an opaque click-tracking
redirect with the real destination only recoverable by actually
resolving it (e.g. https://email.mg-d0.substack.com/c/{token} ->
https://magazine.sebastianraschka.com/subscribe, confirmed live). Most
tests cover the logic around that real, already-verified mechanism --
routing, sender matching, error handling -- not re-prove the redirect
resolution itself, which needs a real network call to verify and was
already done by hand.

Exception: the "_resolve_redirect observability" section below (2026-07-26,
Issue 6 investigation) exercises the REAL _resolve_redirect (via
urllib.request.urlopen mocked instead), since what's under test there is
specifically what that function itself logs and at what level -- mocking
the function away entirely would defeat the point.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import discovery.parsers.agentmail_newsletters as agentmail_mod
from discovery.parsers.agentmail_newsletters import (
    fetch_agentmail_newsletters, _extract_article_url, _html_to_text, _match_sender_name,
    _WELCOME_SUBJECT_PATTERN, _resolve_redirect,
)

_SENDER_TO_NAME = {
    "jamwithai@substack.com": "JamWithAI",
    "sebastianraschka@substack.com": "Ahead of AI",
    "aiengineering@mail.beehiiv.com": "AI Engineering (Sumanth P)",
}


def test_html_to_text_strips_tags_and_collapses_whitespace():
    text = _html_to_text("<p>Hello   <b>world</b></p>\n<p>Second   paragraph</p>")
    assert text == "Hello world Second paragraph"


def test_match_sender_name_extracts_address_from_display_name_format():
    assert _match_sender_name("Shirin & Shantanu from Jam With AI <jamwithai@substack.com>", _SENDER_TO_NAME) == "JamWithAI"


def test_match_sender_name_returns_none_for_unrecognized_sender():
    assert _match_sender_name("Someone Else <someone@random.com>", _SENDER_TO_NAME) is None


def test_extract_article_url_resolves_each_href_and_returns_first_real_post_match():
    """Mirrors what a real Substack email actually contains: every href is
    an opaque tracking redirect, none of which look like an article link
    until resolved. The first one that resolves to a real /p/{slug} URL
    wins."""
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Title</a> <a href="https://email.mg-d0.substack.com/c/tokenB">Unsubscribe</a>'
    resolved = {
        "https://email.mg-d0.substack.com/c/tokenA": "https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms",
        "https://email.mg-d0.substack.com/c/tokenB": "https://magazine.sebastianraschka.com/action/disable_email",
    }
    with patch.object(agentmail_mod, "_resolve_redirect", side_effect=lambda url, timeout=10.0: resolved[url]):
        assert _extract_article_url(html) == ("https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms", False)


def test_extract_article_url_returns_none_when_nothing_resolves_to_a_post():
    """The real signature of a welcome/onboarding email (confirmed via a
    real received message, 2026-07-18): every link resolves to something
    real (subscribe, unsubscribe, about page) but none are a /p/ post.
    Every resolution succeeded (none returned None), so had_transient_error
    must be False -- this is a confirmed content-free email, not a
    resolution failure."""
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Subscribe</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/subscribe"):
        assert _extract_article_url(html) == (None, False)


def test_extract_article_url_skips_unresolvable_links_and_keeps_checking():
    html = '<a href="https://x/dead">Dead</a> <a href="https://x/good">Good</a>'
    def _resolve(url, timeout=10.0):
        return None if url == "https://x/dead" else "https://pub.substack.com/p/real-post"
    with patch.object(agentmail_mod, "_resolve_redirect", side_effect=_resolve):
        assert _extract_article_url(html) == ("https://pub.substack.com/p/real-post", False)


def test_extract_article_url_reports_transient_error_when_a_resolution_fails_and_nothing_else_matches():
    """Step 4 audit (2026-07-22): if a tier-3 resolution attempt itself
    fails (_resolve_redirect returns None -- its documented signal for a
    real request-level failure, not 'resolved but not a post'), and no
    other candidate resolves to a post either, had_transient_error must
    be True -- this run couldn't confirm the email is really content-free,
    so the caller must not mark it read."""
    html = '<a href="https://x/dead">Dead</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value=None):
        assert _extract_article_url(html) == (None, True)


# ── Three-tier extraction fix (2026-07-22, real production bug) ────────────
# A real Saturday Pipeline run (c6c5624d, 2026-07-22) failed to extract an
# article URL for 3 messages that genuinely had one -- confirmed by
# re-running this exact code against the exact real HTML from an unblocked
# machine, which found a match every time. Real hrefs below are captured
# verbatim from those actual received messages.

def test_tier1_raw_href_already_matching_post_pattern_needs_zero_network_calls():
    """Decoding AI Magazine's real "What's Harness Engineering?" email
    (2026-07-22): a plain, non-redirect open.substack.com href containing
    /p/{slug} directly, among a sea of opaque substack.com/redirect/...
    tracking links. Must be found WITHOUT ever calling _resolve_redirect."""
    html = (
        '<a href="https://substack.com/redirect/8e247eaa-6807-4f1f-ac3f-79215c4b64bf?j=eyJ1IjoiMXhrcGtmIn0">t</a>'
        '<a href="https://substack.com/@pauliusztin">profile</a>'
        '<a href="https://open.substack.com/pub/technically/p/whats-harness-engineering?utm_source=substack">Read</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result == ("https://open.substack.com/pub/technically/p/whats-harness-engineering?utm_source=substack", False)
    mock_resolve.assert_not_called()


_REAL_NEURAL_MAZE_SUBSCRIBE_HREF = (
    "https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly90aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbS9zdWJzY3JpYmU_dXRtX3NvdXJjZT1lbWFpbCZ1dG1fY2FtcGFpZ249ZW1haWwtc3Vic2NyaWJlJnI9OHJ6b201Jm5leHQ9aHR0cHMlM0ElMkYlMkZ0aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbSUyRnAlMkZ0aGUtc2xtLW9jci1jb3Vyc2UtbGl2ZS1xLWFuZC1hLWFuZCIsInAiOjIwNzI2MzUzOSwicyI6MzMzMjIwOSwiZiI6dHJ1ZSwidSI6NTMwNzQzOTAxLCJpYXQiOjE3ODQ1NDUxOTYsImV4cCI6MjEwMDEyMTE5NiwiaXNzIjoicHViLTAiLCJzdWIiOiJsaW5rLXJlZGlyZWN0In0.hzQyWw0y4ftdN5hmiUms_ycATbh9yR529hlSjGyjD6I?"
)
_REAL_NEURAL_MAZE_POST_HREF = (
    "https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly90aGVuZXVyYWxtYXplLnN1YnN0YWNrLmNvbS9wL3RoZS1zbG0tb2NyLWNvdXJzZS1saXZlLXEtYW5kLWEtYW5kP3V0bV9jYW1wYWlnbj1lbWFpbC1oYWxmLXBvc3Qmcj04cnpvbTUmdG9rZW49ZXlKMWMyVnlYMmxrSWpvMU16QTNORE01TURFc0luQnZjM1JmYVdRaU9qSXdOekkyTXpVek9Td2lhV0YwSWpveE56ZzBOVFExTVRrMkxDSmxlSEFpT2pFM09EY3hNemN4T1RZc0ltbHpjeUk2SW5CMVlpMHpNek15TWpBNUlpd2ljM1ZpSWpvaWNHOXpkQzF5WldGamRHbHZiaUo5LlI1eTVxYkpxcVhqV1VreFVqdEdHTzNDcVhMZU82clFRWjNVZHFEZzF6dTgiLCJwIjoyMDcyNjM1MzksInMiOjMzMzIyMDksImYiOnRydWUsInUiOjUzMDc0MzkwMSwiaWF0IjoxNzg0NTQ1MTk2LCJleHAiOjIxMDAxMjExOTYsImlzcyI6InB1Yi0wIiwic3ViIjoibGluay1yZWRpcmVjdCJ9.WhqgTGA68J9KL1E-nGRCeGzhO-o-uqcPeEQWB_zF7tA?"
)


def test_tier2_substack_redirect_2_decodes_without_network_calls():
    """The Neural Maze's real "The SLM OCR Course..." email (2026-07-22):
    a substack.com/redirect/2/{base64} href whose (JWT-shaped, payload.signature)
    token's payload segment decodes directly to real JSON containing
    "e": "https://theneuralmaze.substack.com/p/the-slm-ocr-course-live-q-and-a-and?...".
    Both hrefs below are captured verbatim, full and untruncated, from the
    actual received message. Must resolve WITHOUT any network call."""
    html = (
        f'<a href="{_REAL_NEURAL_MAZE_SUBSCRIBE_HREF}">subscribe</a>'
        f'<a href="{_REAL_NEURAL_MAZE_POST_HREF}">Read</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result[0] is not None
    assert "theneuralmaze.substack.com/p/the-slm-ocr-course-live-q-and-a-and" in result[0]
    assert result[1] is False
    mock_resolve.assert_not_called()


def test_tier2_ignores_redirect_2_link_that_decodes_to_a_non_post_url():
    """The first redirect/2/ link in the same real email decodes to a
    /subscribe page, not a post -- tier 2 must correctly skip it (fall
    through to the next href/tier), not treat any successful decode as a
    match regardless of destination. Tier 3 then also fails via a real
    resolution error (mocked return_value=None, _resolve_redirect's real
    signal for a request-level failure), so had_transient_error is True --
    this is NOT a confirmed content-free result."""
    html = f'<a href="{_REAL_NEURAL_MAZE_SUBSCRIBE_HREF}">subscribe</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value=None) as mock_resolve:
        result = _extract_article_url(html)

    assert result == (None, True)
    mock_resolve.assert_called_once()  # falls through to tier 3, which also fails here


def test_tier3_still_used_for_genuinely_opaque_tracking_links():
    """AI Engineering's real beehiiv email (2026-07-22): link.mail.beehiiv.com
    tokens are NOT base64-JSON-decodable (confirmed directly) -- tiers 1-2
    must correctly fall through to tier 3's real HTTP resolution, which
    still works and must still be exercised."""
    html = '<a href="https://link.mail.beehiiv.com/v1/c/wNq66YaGm0G2BLC2rCTdiVywre9Ryz3w2kdC3UqVQW3WxqRUdGcC2zgWe3GK">Read</a>'
    with patch.object(agentmail_mod, "_resolve_redirect", return_value="https://aiengineering.beehiiv.com/p/hands-on-build-a-browser-automation-agent") as mock_resolve:
        result = _extract_article_url(html)

    assert result == ("https://aiengineering.beehiiv.com/p/hands-on-build-a-browser-automation-agent", False)
    mock_resolve.assert_called_once()


# ── _resolve_redirect observability (2026-07-26, Issue 6 investigation) ────
# Real bug: every real "AI Engineering" (beehiiv) resolution failure across
# multiple Saturday runs left zero trace of the actual exception -- the only
# place it was ever logged was a logger.debug() call, invisible in every
# real captured log/artifact since core/logging_config.py's global level is
# INFO. These exercise the REAL _resolve_redirect (not mocked, unlike every
# other test in this file) via caplog, proving the real exception now
# survives at INFO/WARNING regardless of the global level.

def test_resolve_redirect_failure_logs_the_real_exception_at_warning_level(caplog):
    with patch("urllib.request.urlopen", side_effect=TimeoutError("simulated network timeout")), \
         caplog.at_level(logging.INFO, logger="discovery.parsers.agentmail_newsletters"):
        result = _resolve_redirect("https://link.mail.beehiiv.com/ss/c/faketoken")

    assert result is None
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, f"expected a WARNING-or-above record, got levels: {[r.levelname for r in caplog.records]}"
    assert any("simulated network timeout" in r.getMessage() for r in warning_records), \
        "expected the real exception message in the log record"
    assert any("TimeoutError" in r.getMessage() for r in warning_records), \
        "expected the real exception TYPE in the log record, not just a generic failure notice"


def test_resolve_redirect_logs_the_attempt_before_resolving(caplog):
    """Request-attempt visibility (item 2): a log line must exist for the
    attempt itself, not just the outcome -- matching what httpx already
    logs for free on every AgentMail API call."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError("simulated network timeout")), \
         caplog.at_level(logging.INFO, logger="discovery.parsers.agentmail_newsletters"):
        _resolve_redirect("https://link.mail.beehiiv.com/ss/c/faketoken")

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("https://link.mail.beehiiv.com/ss/c/faketoken" in r.getMessage() for r in info_records), \
        "expected an INFO-level log line naming the URL being resolved, before the outcome is known"


def test_resolve_redirect_success_logs_the_resolved_url_and_status(caplog):
    """Success path also gets real visibility -- status code and the
    real resolved destination, not silence on the happy path."""
    fake_resp = MagicMock()
    fake_resp.url = "https://magazine.sebastianraschka.com/p/real-post"
    fake_resp.status = 200
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=fake_resp), \
         caplog.at_level(logging.INFO, logger="discovery.parsers.agentmail_newsletters"):
        result = _resolve_redirect("https://email.mg-d0.substack.com/c/tokenA")

    assert result == "https://magazine.sebastianraschka.com/p/real-post"
    assert any(
        "https://magazine.sebastianraschka.com/p/real-post" in r.getMessage() and "200" in r.getMessage()
        for r in caplog.records
    ), f"expected an INFO record with the resolved URL and status code, got: {[r.getMessage() for r in caplog.records]}"


def test_tier1_before_tier2_when_both_present():
    """When a raw href already matches directly (tier 1), tier 2's decode
    attempt on a LATER href must never even be reached -- tier 1 wins."""
    html = (
        '<a href="https://open.substack.com/pub/name/p/first-match">Read</a>'
        '<a href="https://substack.com/redirect/2/eyJlIjoiaHR0cHM6Ly9leGFtcGxlLmNvbS9wL3NlY29uZC1tYXRjaCJ9">Also a post</a>'
    )
    with patch.object(agentmail_mod, "_resolve_redirect") as mock_resolve:
        result = _extract_article_url(html)

    assert result == ("https://open.substack.com/pub/name/p/first-match", False)
    mock_resolve.assert_not_called()


# ── Welcome/onboarding subject-pattern pre-filter (2026-07-26, real bug) ────
# Real run 08b5d13b: "Welcome to Decoding AI Magazine" had no article of its
# own, but tier 1 of _extract_article_url found an unrelated /p/ link in its
# body anyway (a "recommended posts" or evergreen footer link) and it was
# ingested as real content. These directly regression-test that the
# welcome/onboarding filter now stops that class of message before
# extraction ever runs, regardless of what's findable in its body.

def test_welcome_subject_pattern_matches_real_confirmed_subjects():
    """Every one of these is a real subject seen across two Saturday runs
    (07-22, 07-26) that should be filtered."""
    real_subjects = [
        "Welcome to Decoding AI Magazine \U0001F680",
        "Welcome to AI Engineering!",
        "Welcome to the DiamantAI Community! \U0001F48E",
        "Welcome to The Neural Maze",
        "Welcome to The Nuanced Perspective",
        "Welcome to AI with Aish",
        "Welcome to Jam with AI",
        "Welcome, we're getting more technical",
    ]
    for subject in real_subjects:
        assert _WELCOME_SUBJECT_PATTERN.search(subject), f"expected a match for {subject!r}"


def test_welcome_subject_pattern_is_case_insensitive_and_anchored_to_start():
    assert _WELCOME_SUBJECT_PATTERN.search("WELCOME TO THE TEAM")
    assert not _WELCOME_SUBJECT_PATTERN.search("You're welcome to join our next event")


# ── "Thanks for subscribing" phrasing (2026-07-26, real 10-sender audit) ────
# Ahead of AI's real subject wasn't caught by "^welcome" alone -- found by
# pulling every real subject line across all 10 senders from every Saturday
# run with per-message AgentMail data, not by guessing common confirmation
# phrasings (see _WELCOME_SUBJECT_PATTERN's own docstring for the full
# audit and what was deliberately NOT added as a result).

def test_welcome_subject_pattern_matches_thanks_for_subscribing_phrasing():
    assert _WELCOME_SUBJECT_PATTERN.search("Thanks for subscribing to Ahead of AI!")
    assert _WELCOME_SUBJECT_PATTERN.search("THANKS FOR SUBSCRIBING to our list")


def test_welcome_subject_pattern_does_not_match_unrelated_real_subjects():
    """Real, non-onboarding subjects from the same production logs --
    must not be caught by this filter. Includes the one real onboarding
    subject confirmed to still slip through both phrasings: The AI
    Merge's real welcome email subject is a slogan/tagline ("No Vibes,
    Just Real AI/ML Engineering!"), not a confirmation phrase -- there is
    no honest subject-text pattern for it without risking false
    positives on real article titles (see _WELCOME_SUBJECT_PATTERN's
    docstring)."""
    real_subjects = [
        "Coding the KV Cache in LLMs",
        "What's Harness Engineering?",
        "Kimi K3 Redraws the Open Frontier, Muse Spark 1.1 Undercuts Competitors",
        "No Vibes, Just Real AI/ML Engineering!",
    ]
    for subject in real_subjects:
        assert not _WELCOME_SUBJECT_PATTERN.search(subject), f"unexpected match for {subject!r}"


def test_welcome_subject_message_never_reaches_extraction_even_with_a_real_matchable_url():
    """Direct regression test for the real bug: a welcome-subject
    message's body genuinely contains a real, matchable /p/{slug} href
    (exactly the "Welcome to Decoding AI Magazine" shape) -- must still
    be skipped entirely, proven by _resolve_redirect/tier-1-2 never being
    reached at all (mock_extract never called), not just by the final
    row count."""
    now = datetime.now(timezone.utc)
    html = '<a href="https://www.decodingai.com/p/ai-engineering-roadmaps">Read our latest</a>'
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-welcome", "Welcome to Decoding AI Magazine \U0001F680")],
        get_by_id={
            "msg-welcome": _FakeMessage(
                "msg-welcome", "Welcome to Decoding AI Magazine \U0001F680", html,
                "decodingai@substack.com", now,
            ),
        },
    )
    sender_map = dict(_SENDER_TO_NAME, **{"decodingai@substack.com": "Decoding AI Magazine"})

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_extract_article_url") as mock_extract, \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", sender_map, limit=20)

    mock_extract.assert_not_called()
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "Decoding AI Magazine"
    assert "welcome/onboarding" in result.errors[0][1]
    assert fake_messages.update_calls == [("inbox-123", "msg-welcome", ["read"], ["unread"])]


def test_thanks_for_subscribing_subject_message_never_reaches_extraction():
    """Fetch-level regression test for the real gap this expansion
    closes: Ahead of AI's real "Thanks for subscribing to Ahead of AI!"
    subject, with a body that genuinely contains a matchable /p/{slug}
    href -- must now be skipped before extraction, same as the "Welcome"
    phrasing already was."""
    now = datetime.now(timezone.utc)
    html = '<a href="https://magazine.sebastianraschka.com/p/some-post">Read our latest</a>'
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-thanks", "Thanks for subscribing to Ahead of AI!")],
        get_by_id={
            "msg-thanks": _FakeMessage(
                "msg-thanks", "Thanks for subscribing to Ahead of AI!", html,
                "sebastianraschka@substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_extract_article_url") as mock_extract, \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    mock_extract.assert_not_called()
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "Ahead of AI"
    assert "welcome/onboarding" in result.errors[0][1]
    assert fake_messages.update_calls == [("inbox-123", "msg-thanks", ["read"], ["unread"])]


def test_non_welcome_subject_with_matchable_url_still_extracts_normally():
    """Sanity check the filter is scoped correctly: a normal (non-welcome)
    subject with a real matchable URL is completely unaffected -- still
    reaches extraction and produces a real row."""
    now = datetime.now(timezone.utc)
    html = '<a href="https://www.decodingai.com/p/ai-engineering-roadmaps">Read</a>'
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-real", "AI Engineering Roadmaps")],
        get_by_id={
            "msg-real": _FakeMessage(
                "msg-real", "AI Engineering Roadmaps", html, "decodingai@substack.com", now,
            ),
        },
    )
    sender_map = dict(_SENDER_TO_NAME, **{"decodingai@substack.com": "Decoding AI Magazine"})

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", sender_map, limit=20)

    assert len(result.rows) == 1
    assert result.rows[0]["url"] == "https://www.decodingai.com/p/ai-engineering-roadmaps"


class _FakeMessageItem:
    def __init__(self, message_id, subject):
        self.message_id = message_id
        self.subject = subject


class _FakeMessage:
    def __init__(self, message_id, subject, html, from_, timestamp):
        self.message_id = message_id
        self.subject = subject
        self.html = html
        self.extracted_html = html
        self.text = None
        self.extracted_text = None
        self.from_ = from_
        self.timestamp = timestamp


class _FakeMessagesClient:
    def __init__(self, list_items, get_by_id):
        self._list_items = list_items
        self._get_by_id = get_by_id
        self.update_calls: list[tuple] = []

    def list(self, inbox_id, labels=None, limit=None):
        return SimpleNamespace(messages=self._list_items)

    def get(self, inbox_id, message_id):
        return self._get_by_id[message_id]

    def update(self, inbox_id, message_id, add_labels=None, remove_labels=None):
        self.update_calls.append((inbox_id, message_id, add_labels, remove_labels))


class _FakeInboxesClient:
    def __init__(self, messages_client):
        self.messages = messages_client


class _FakeAgentMailClient:
    def __init__(self, messages_client, api_key=None):
        self.inboxes = _FakeInboxesClient(messages_client)


def test_fetch_agentmail_newsletters_parses_and_attributes_a_real_shaped_message_and_marks_it_read():
    now = datetime.now(timezone.utc)
    html = '<a href="https://email.mg-d0.substack.com/c/tokenA">Title</a>'
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-1", "Coding the KV Cache in LLMs")],
        get_by_id={
            "msg-1": _FakeMessage(
                "msg-1", "Coding the KV Cache in LLMs", html,
                '"Sebastian Raschka, PhD from Ahead of AI" <sebastianraschka@substack.com>', now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms"), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.errors == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["url"] == "https://magazine.sebastianraschka.com/p/coding-the-kv-cache-in-llms"
    assert row["author_name"] == "Ahead of AI"  # attributed by real sender match, not a generic label

    assert fake_messages.update_calls == [("inbox-123", "msg-1", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_unrecognized_sender_grouped_under_stable_key_and_marked_read():
    """2026-07-26 fix: an unrecognized sender is now marked read, not left
    unread -- otherwise it's the exact same error every single run,
    forever, since no config change can ever "fix" a message from a
    sender that by definition isn't in config. Still unconditionally
    logged under the stable _UNRECOGNIZED_SENDER key (distinct from any
    real source's own errors) -- marking it read doesn't mean it
    vanishes without a trace, just that it stops being weekly noise."""
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-2", "Random newsletter")],
        get_by_id={
            "msg-2": _FakeMessage("msg-2", "Random newsletter", "<a href='x'>x</a>", "Someone <someone@random.com>", now),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "AgentMail Newsletters (unrecognized sender)"
    assert "unrecognized sender" in result.errors[0][1]
    assert fake_messages.update_calls == [("inbox-123", "msg-2", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_confirmed_content_free_grouped_under_real_source_name_and_marked_read():
    """Every href resolves successfully (no resolution error) but none is
    a /p/ post -- a confirmed content-free email. Step 4 fix (2026-07-22):
    this case is now marked read, since retrying it next run could never
    produce a different outcome.

    Subject is a real, confirmed production subject (The AI Merge's real
    welcome email, per the 2026-07-26 10-sender audit) that does NOT
    match the welcome/onboarding subject-pattern filter at all -- a
    slogan/tagline, not a "Welcome..."/"Thanks for subscribing..."
    phrase (see _WELCOME_SUBJECT_PATTERN's docstring for why this is a
    confirmed, deliberately-uncovered gap) -- so this specific subject is
    what keeps this test actually exercising the extraction-based
    content-free path rather than the subject-pattern short-circuit."""
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-3", "No Vibes, Just Real AI/ML Engineering!")],
        get_by_id={
            "msg-3": _FakeMessage(
                "msg-3", "No Vibes, Just Real AI/ML Engineering!", '<a href="https://email.mg-d0.substack.com/c/tokenX">Subscribe</a>',
                "sebastianraschka@substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.sebastianraschka.com/subscribe"), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == "Ahead of AI"  # grouped by real source, not the raw subject/message_id
    assert fake_messages.update_calls == [("inbox-123", "msg-3", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_warns_when_confirmed_content_free_body_is_substantial():
    """DiamantAI-shaped fixture (Step 4 WARN tripwire, 2026-07-22): a
    real visible body that's substantial (well over
    _SUBSTANTIAL_CONTENT_FREE_BODY_CHARS), like the real DiamantAI
    welcome email found during the read-only audit -- real content about
    the newsletter's repos/course/books, still no /p/ post link anywhere.
    Must log a WARNing (visibility only) AND still mark the message read.

    Updated 2026-07-26: this real subject ("Welcome to the DiamantAI
    Community!") now matches the welcome/onboarding subject-pattern
    filter, so this now exercises THAT branch's own copy of the same
    WARN tripwire, not the post-extraction one -- confirmed via
    mock_resolve.assert_not_called(), proving extraction is skipped
    entirely while the substantial-body visibility net is still
    preserved."""
    now = datetime.now(timezone.utc)
    substantial_paragraph = (
        "DiamantAI's real newsletter content: deep dives on RAG techniques, "
        "open-source agent repositories, and hands-on production courses. "
    ) * 20  # well over 2000 chars once tags/whitespace are stripped
    html = (
        "<html><head><style>body { color: #030712; font-size: 14px; } "
        ".wrapper { max-width: 600px; }</style></head><body>"
        f"<p>{substantial_paragraph}</p>"
        '<a href="https://email.mg-d0.substack.com/c/tokenY">Unsubscribe</a>'
        "</body></html>"
    )
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-diamantai", "Welcome to the DiamantAI Community!")],
        get_by_id={
            "msg-diamantai": _FakeMessage(
                "msg-diamantai", "Welcome to the DiamantAI Community!", html,
                "diamantai@substack.com", now,
            ),
        },
    )
    diamantai_sender_map = dict(_SENDER_TO_NAME, **{"diamantai@substack.com": "DiamantAI"})

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://magazine.example.com/unsubscribe") as mock_resolve, \
         patch.object(agentmail_mod, "logger") as mock_logger, \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", diamantai_sender_map, limit=20)

    mock_resolve.assert_not_called()  # welcome-subject filter skips extraction entirely

    assert result.rows == []
    mock_logger.warning.assert_called_once()
    warn_message = mock_logger.warning.call_args[0][0]
    assert "substantial" in warn_message
    assert "DiamantAI" in warn_message
    # Still marked read -- visibility only, does not change the mark-seen outcome.
    assert fake_messages.update_calls == [("inbox-123", "msg-diamantai", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_does_not_warn_for_pure_boilerplate_content_free_body():
    """Pure-boilerplate fixture, mirroring the real 6/7 confirmed
    content-free messages found during the Step 4 audit (a short real
    welcome email, e.g. JamWithAI's 390-char body) -- well under
    _SUBSTANTIAL_CONTENT_FREE_BODY_CHARS. Must NOT warn, and must still
    mark read exactly as before."""
    now = datetime.now(timezone.utc)
    html = (
        "<html><head><style>body { color: #030712; }</style></head><body>"
        "<p>You're receiving free posts from Jam with AI. Unsubscribe in one click.</p>"
        '<a href="https://email.mg2.substack.com/c/tokenZ">Unsubscribe</a>'
        "</body></html>"
    )
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-boilerplate", "Welcome to Jam with AI")],
        get_by_id={
            "msg-boilerplate": _FakeMessage(
                "msg-boilerplate", "Welcome to Jam with AI", html,
                "jamwithai@substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", return_value="https://jamwithai.substack.com/unsubscribe"), \
         patch.object(agentmail_mod, "logger") as mock_logger, \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    mock_logger.warning.assert_not_called()
    assert fake_messages.update_calls == [("inbox-123", "msg-boilerplate", ["read"], ["unread"])]


def test_fetch_agentmail_newsletters_transient_resolve_error_not_marked_read():
    """A transient failure during resolution (e.g. a real socket timeout in
    _resolve_redirect's tier-3 HTTP call) must land the same way a
    confirmed-no-URL message does: NOT marked read, so the next run
    retries it. Confirmed real, 2026-07-22 (Step 4 audit): _resolve_redirect
    itself always catches its own exceptions internally and returns None
    (see its docstring), so this test raises past that boundary to prove
    the outer per-message try/except in fetch_agentmail_newsletters also
    skips the mark-read call on any real, uncaught exception -- same
    outcome as the "no resolvable URL" path below, since today the code
    has no way to tell "confirmed no post anywhere" apart from "one or
    more resolutions errored out network-side"."""
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("msg-transient", "Some real post")],
        get_by_id={
            "msg-transient": _FakeMessage(
                "msg-transient", "Some real post",
                '<a href="https://email.mg-d0.substack.com/c/tokenA">Read</a>',
                "sebastianraschka@substack.com", now,
            ),
        },
    )

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", side_effect=TimeoutError("simulated network timeout")), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "simulated network timeout" in result.errors[0][1]
    assert fake_messages.update_calls == []  # not marked read -- eligible for retry


def test_fetch_agentmail_newsletters_one_bad_message_does_not_block_the_others():
    """"bad" resolves cleanly to a non-post (/subscribe) with no resolution
    error -- confirmed content-free, so it's marked read too, alongside
    "good"'s real successful parse. Neither one blocks the other."""
    now = datetime.now(timezone.utc)
    fake_messages = _FakeMessagesClient(
        list_items=[_FakeMessageItem("bad", "Welcome"), _FakeMessageItem("good", "Real post")],
        get_by_id={
            "bad": _FakeMessage("bad", "Welcome", '<a href="https://x/c/t">x</a>', "sebastianraschka@substack.com", now),
            "good": _FakeMessage("good", "Real post", '<a href="https://x/c/t2">x</a>', "jamwithai@substack.com", now),
        },
    )

    def _resolve(url, timeout=10.0):
        return "https://magazine.sebastianraschka.com/subscribe" if url == "https://x/c/t" else "https://jamwithai.substack.com/p/some-real-post"

    with patch.object(agentmail_mod, "AgentMail", return_value=_FakeAgentMailClient(fake_messages)), \
         patch.object(agentmail_mod, "_resolve_redirect", side_effect=_resolve), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert len(result.rows) == 1
    assert result.rows[0]["author_name"] == "JamWithAI"
    assert len(result.errors) == 1
    assert fake_messages.update_calls == [
        ("inbox-123", "bad", ["read"], ["unread"]),
        ("inbox-123", "good", ["read"], ["unread"]),
    ]


def test_fetch_agentmail_newsletters_inbox_level_failure_never_raises():
    class _BrokenAgentMail:
        def __init__(self, api_key=None):
            raise RuntimeError("invalid API key")

    with patch.object(agentmail_mod, "AgentMail", _BrokenAgentMail), \
         patch.dict(os.environ, {"AGENTMAIL_API_KEY": "fake-key-for-test"}):
        result = fetch_agentmail_newsletters("inbox-123", _SENDER_TO_NAME, limit=20)

    assert result.rows == []
    assert len(result.errors) == 1
    assert "invalid API key" in result.errors[0][1]
