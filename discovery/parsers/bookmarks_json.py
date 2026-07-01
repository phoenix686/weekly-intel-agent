"""
Parse bookmark export JSON files into plain Python dicts.

No langgraph imports, no I/O side effects beyond reading the given file path.
Row-level failures are collected in ParseResult.errors; whole-file structural
problems (missing top-level key, invalid JSON) raise immediately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """Result of parsing a bookmark export JSON.

    rows:   successfully parsed records, each a plain dict.
    errors: list of (item_index, error_message) for items that could not be
            parsed. item_index is 0-based within the top-level data array.
    """
    rows: list[dict]
    errors: list[tuple[int, str]] = field(default_factory=list)


def _make_title(text: str) -> str:
    """Derive a title from the first ~80 chars of text, truncated at a word boundary."""
    if not text:
        return "(no text)"
    if len(text) <= 80:
        return text
    truncated = text[:80]
    last_space = truncated.rfind(" ")
    if last_space == -1:
        return truncated + "…"
    return truncated[:last_space] + "…"


def parse_bookmarks_json(path: str) -> ParseResult:
    """Read a bookmark export JSON, return a ParseResult.

    Expected top-level shape: {"data": [...], ...}

    Each successfully parsed item produces a dict with keys:
        title           str        first ~80 chars of text, word-truncated
        text            str        full tweet text
        url             str        bookmark URL
        author_name     str        user.name
        author_handle   str        user.handle (no @ prefix, as exported)
        is_thread       bool       True if quote_status key is present
        thread_contents str|None   quote_status.text if is_thread else None
        fetched_at      str        date field (ISO 8601)
        expanded_urls   list[str]  expandedUrl values from links[], or []

    Raises ValueError immediately on missing 'data' key or invalid JSON.
    Item-level failures are appended to ParseResult.errors as
    (item_index, message) and parsing continues.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON at {path!r} could not be parsed: {exc}") from exc

    if not isinstance(raw, dict) or "data" not in raw:
        raise ValueError(
            f"Expected a JSON object with a top-level 'data' key; "
            f"got {type(raw).__name__} with keys: {list(raw.keys()) if isinstance(raw, dict) else 'n/a'}"
        )

    items = raw["data"]
    if not isinstance(items, list):
        raise ValueError(f"'data' key must be a list, got {type(items).__name__}")

    rows: list[dict] = []
    errors: list[tuple[int, str]] = []

    for i, item in enumerate(items):
        try:
            text = item.get("text") or ""
            user = item.get("user") or {}
            links = item.get("links") or []
            quote = item.get("quote_status")

            expanded_urls = [
                lnk["expandedUrl"]
                for lnk in links
                if isinstance(lnk, dict) and "expandedUrl" in lnk
            ]

            rows.append(
                {
                    "title": _make_title(text),
                    "text": text,
                    "url": item.get("url") or "",
                    "author_name": user.get("name") or "",
                    "author_handle": user.get("handle") or "",
                    "is_thread": quote is not None,
                    "thread_contents": quote.get("text") if quote else None,
                    "fetched_at": item.get("date") or "",
                    "expanded_urls": expanded_urls,
                }
            )
        except Exception as exc:
            errors.append((i, f"{type(exc).__name__}: {exc}"))

    return ParseResult(rows=rows, errors=errors)
