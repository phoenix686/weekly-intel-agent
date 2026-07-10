"""
Execute web searches and parse results into plain Python dicts.

No langgraph imports, no I/O side effects beyond HTTP calls.
Result-level failures are collected in ParseResult.errors.

SEARCH MECHANISM: not yet configured — awaiting confirmation of which
search API/provider to use and how queries are sourced.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def run_searches(queries: list[str]) -> ParseResult:
    """Execute web searches for the given queries and return results.

    Each successfully parsed result produces a dict with keys:
        title           str
        text            str        snippet / description
        url             str        result URL
        author_name     str        site name / publisher if available
        author_handle   str        empty string if not available
        is_thread       bool       always False for web results
        thread_contents None
        fetched_at      str        ISO 8601 (time of the search request)
        expanded_urls   list[str]  always []

    Raises ValueError on API authentication failures.
    Result-level failures are appended to ParseResult.errors as
    (query, message) and processing continues.
    """
    raise NotImplementedError(
        "search_web: search API not configured. "
        "Confirm which search provider to use before implementing."
    )
