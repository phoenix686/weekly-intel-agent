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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"

# XML 1.0 disallows most C0 control chars in content (only tab/LF/CR are
# legal). Some real-world feeds embed them anyway (e.g. a <code> sample
# containing literal control bytes) -- strip rather than let a single
# malformed byte take down the whole feed.
_XML_ILLEGAL_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    if child is None or not child.text:
        return ""
    return child.text.strip()


def _parse_pubdate(raw: str) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


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


def fetch_rss_feed(feed_url: str, source_name: str, limit: int = 30) -> ParseResult:
    """Fetch and parse a single RSS 2.0 feed into plain row dicts.

    Row keys match RawItem's shape minus `source` (the caller sets that):
        title, text, url, author_name, author_handle, fetched_at,
        is_thread, thread_contents, expanded_urls, has_video, video_url

    Feed-level failures (network error, malformed XML) are appended to
    ParseResult.errors as (source_name, message); rows is empty in that case.
    """
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "weekly-intel-bot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"

        raw_text = _XML_ILLEGAL_CHARS.sub("", raw_bytes.decode(charset, errors="replace"))
        # re-encode to bytes: ET.fromstring rejects a `str` that still carries
        # an <?xml encoding=...?> declaration
        root = ET.fromstring(raw_text.encode("utf-8"))
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")

        for item in items[:limit]:
            link = _text(item, "link")
            if not link:
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
                "fetched_at": _parse_pubdate(_text(item, "pubDate")),
                "is_thread": False,
                "thread_contents": None,
                "expanded_urls": [],
                "has_video": has_video,
                "video_url": video_url,
            })
    except Exception as e:
        errors.append((source_name, str(e)))

    return ParseResult(rows=rows, errors=errors)
