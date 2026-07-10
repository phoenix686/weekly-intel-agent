"""
Free web search for candidate new sources (Part C), via DuckDuckGo's
unofficial HTML endpoint -- no API key/card needed, matching the spec's
"occasional, low-volume, this is fine" framing.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.parse
import urllib.request

_RESULT_PATTERN = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")


def search_duckduckgo(query: str, limit: int = 10) -> list[dict]:
    """Search DuckDuckGo's HTML endpoint and return [{"title", "url"}, ...].

    Raises on network failure -- caller decides whether to skip this
    week's source-discovery run rather than silently returning nothing.
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (weekly-intel-bot)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    results: list[dict] = []
    for href, title_html in _RESULT_PATTERN.findall(raw):
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        real_url = qs.get("uddg", [None])[0]
        if not real_url:
            continue
        real_url = urllib.parse.unquote(real_url)
        title = html_module.unescape(_TAG_PATTERN.sub("", title_html)).strip()
        if title and real_url:
            results.append({"title": title, "url": real_url})
        if len(results) >= limit:
            break
    return results
