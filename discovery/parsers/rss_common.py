"""
Shared RSS 2.0 fetch+parse helper for single-feed blog/newsletter sources.

No langgraph imports, no LLM calls, no I/O side effects beyond one HTTP
fetch per call. Pure stdlib (urllib + xml.etree.ElementTree).
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"

# Real browser UA, not a bot-identifying string (was "weekly-intel-bot/1.0"
# -- 2026-07-17, real production 403s from 4 Substack-hosted sources on a
# real Sunday run). Matches discovery/parsers/anthropic_blog.py's already-
# working UA. Honest caveat: re-tested all four blocked sources from this
# machine afterward and every one succeeded with BOTH the old bot UA and
# this one -- the block did not reproduce here, so this could not be
# verified as the actual fix the way a reproducible failure would allow.
# Most likely explanation: Substack rate-limits/blocks by IP range (e.g.
# GitHub Actions' shared runner IPs), not by this exact UA string. Applied
# as a legitimate defensive improvement regardless -- a bot-labeled UA is
# objectively more likely to be blocked somewhere than a real browser one
# -- but the real test is the next live GitHub Actions run, not this.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# XML 1.0 disallows most C0 control chars in content (only tab/LF/CR are
# legal). Some real-world feeds embed them anyway (e.g. a <code> sample
# containing literal control bytes) -- strip rather than let a single
# malformed byte take down the whole feed.
_XML_ILLEGAL_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

# Canonical cause of ElementTree's "not well-formed (invalid token)" error --
# a bare `&` in text content that isn't part of a recognized entity
# reference (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&apos;`, or a numeric
# `&#123;`/`&#x7B;`). MarkTechPost's WordPress-generated feed has hit this
# 3 times in real production runs (2026-07-19, 2026-07-20, recurring),
# always intermittent -- content-dependent on a specific post's
# title/excerpt, never reproducible on a direct refetch since the
# offending post has typically already rotated out of the feed by the
# time anyone checks, and no raw-byte dump was ever preserved from those
# occurrences to confirm the exact character. Used only as a retry
# fallback after the first parse attempt fails (see fetch_rss_feed), not
# applied unconditionally like the control-char strip above, since
# escaping every bare `&` is a real content rewrite, not a no-op.
_BARE_AMPERSAND = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")

# Distinct from a malformed-XML feed (_BARE_AMPERSAND above): these are
# real HTTP 200s whose body is an HTML bot-challenge/CAPTCHA interstitial
# instead of the feed itself -- confirmed 2026-07-22, MarkTechPost, via a
# raw-body dump showing a Cloudflare `/.well-known/sgcaptcha/` redirect
# page. A plain ET.ParseError on this body would be misclassified as the
# same "not well-formed" bug as the ampersand case, hiding the real cause
# (bot-blocked, not malformed) behind an identical-looking error message.
# Checked BEFORE attempting any XML parse, not as a parse-failure retry.
_BOT_CHALLENGE_MARKERS = (
    "sgcaptcha",
    "cf-chl",
    "cdn-cgi/challenge-platform",
    "checking your browser before accessing",
    "attention required! | cloudflare",
    "just a moment...",
)


def _looks_like_bot_challenge(content_type: str, raw_text: str) -> bool:
    """A real feed always declares an xml/rss content-type -- trust that
    over sniffing the body. Only sniff for challenge markers when the
    content-type itself doesn't already look like a feed."""
    if content_type and ("xml" in content_type.lower() or "rss" in content_type.lower()):
        return False
    lowered = raw_text[:2000].lower()
    return any(marker in lowered for marker in _BOT_CHALLENGE_MARKERS)


_PARSE_ERROR_LOG_DIR = Path("logs/parse_errors")


def _dump_parse_error_body(source_name: str, raw_bytes: bytes) -> None:
    """Writes the raw (pre-decode, pre-strip) response body to
    logs/parse_errors/{source}_{timestamp}.xml on an XML ParseError, or on
    a detected bot-challenge response (see _looks_like_bot_challenge) --
    the only way to get byte-level proof of what's actually wrong, since a
    manual refetch after the fact keeps missing the moment (confirmed
    twice: 2026-07-19, MarkTechPost's "not well-formed (invalid token):
    line 1, column 119" reproduced in CI but not on a direct local
    refetch, same exact error both times; and 2026-07-22, same source,
    a Cloudflare bot-challenge page reproduced in CI but not locally).
    Best-effort -- a failure to write this diagnostic file must never mask
    or replace the original error itself.
    """
    _PARSE_ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", source_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dump_path = _PARSE_ERROR_LOG_DIR / f"{safe_name}_{timestamp}.xml"
    try:
        dump_path.write_bytes(raw_bytes)
    except OSError:
        pass


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    if child is None or not child.text:
        return ""
    return child.text.strip()


def _parse_pubdate(raw: str) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _detect_video(description: str) -> tuple[bool, str | None]:
    for marker in ("youtube.com/watch", "youtu.be/"):
        idx = description.find(marker)
        if idx == -1:
            continue
        start = description.rfind("http", 0, idx)
        if start == -1:
            return True, None
        end = start
        while end < len(description) and description[end] not in ' "\'<>)\n\t':
            end += 1
        return True, description[start:end]
    return False, None


def fetch_rss_feed(
    feed_url: str,
    source_name: str,
    limit: int = 30,
    max_age_hours: int | None = None,
) -> ParseResult:
    """Fetch and parse a single RSS 2.0 feed into plain row dicts.

    Row keys match RawItem's shape minus `source` (the caller sets that):
        title, text, url, author_name, author_handle, fetched_at,
        is_thread, thread_contents, expanded_urls, has_video, video_url

    max_age_hours: when set, an item whose pubDate is older than this many
    hours is dropped before it's ever returned -- cheaper than parsing it,
    scoring it, and letting discovery/seen_items.py's cross-run dedup catch
    it after the fact. This is an additional pre-filter, NOT a replacement
    for seen_items -- an item within the window can still be a duplicate
    seen_items needs to catch. Items with a missing/unparseable pubDate
    fall back to "now" (see _parse_pubdate) and are always kept, since
    there's no real timestamp to judge staleness against.

    Feed-level failures (network error, malformed XML, or a bot-challenge
    interstitial served in place of the feed) are appended to
    ParseResult.errors as (source_name, message), each distinctly worded
    per cause -- rows is empty in that case.
    """
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": _BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            content_type = resp.headers.get_content_type()

        raw_text = _XML_ILLEGAL_CHARS.sub("", raw_bytes.decode(charset, errors="replace"))

        if _looks_like_bot_challenge(content_type, raw_text):
            _dump_parse_error_body(source_name, raw_bytes)
            errors.append((
                source_name,
                f"blocked by bot-challenge (content-type={content_type!r}, "
                f"not XML/RSS) -- raw body dumped to logs/parse_errors/ for inspection",
            ))
            return ParseResult(rows=rows, errors=errors)

        # re-encode to bytes: ET.fromstring rejects a `str` that still carries
        # an <?xml encoding=...?> declaration
        try:
            root = ET.fromstring(raw_text.encode("utf-8"))
        except ET.ParseError:
            # Retry once against a bare-ampersand-escaped version before
            # giving up on this source entirely -- see _BARE_AMPERSAND's
            # comment for why this is the first thing worth trying.
            try:
                root = ET.fromstring(_BARE_AMPERSAND.sub("&amp;", raw_text).encode("utf-8"))
            except ET.ParseError:
                _dump_parse_error_body(source_name, raw_bytes)
                raise

        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            if max_age_hours is not None else None
        )

        for item in items[:limit]:
            link = _text(item, "link")
            if not link:
                continue

            pubdate_raw = _text(item, "pubDate")
            fetched_dt = _parse_pubdate(pubdate_raw)
            if cutoff is not None and pubdate_raw and fetched_dt < cutoff:
                continue

            title = _text(item, "title")
            description = _text(item, "description")
            author = _text(item, _DC_CREATOR) or _text(item, "author")
            has_video, video_url = _detect_video(description)

            rows.append({
                "title": title or link,
                "text": description,
                "url": link,
                "author_name": author,
                "author_handle": "",
                "fetched_at": fetched_dt.isoformat(),
                "is_thread": False,
                "thread_contents": None,
                "expanded_urls": [],
                "has_video": has_video,
                "video_url": video_url,
            })
    except Exception as e:
        errors.append((source_name, str(e)))

    return ParseResult(rows=rows, errors=errors)
