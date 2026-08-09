"""Deterministic story clustering and diversified ranking."""
from __future__ import annotations
import hashlib
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_STOP = {"a", "an", "and", "for", "from", "in", "is", "of", "on", "the", "to", "with", "new"}
_QUALITY = {"official": 5, "research": 4, "blog_scrape": 3, "TLDR AI": 2}


def canonicalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")
             and k.lower() not in {"fbclid", "gclid", "ref", "s"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
                       urlencode(sorted(query)), ""))


def _tokens(item: dict) -> set[str]:
    title = re.sub(r"\[[^\]]+\]", " ", item.get("title", "").lower())
    return {v for v in re.findall(r"[a-z0-9][a-z0-9.+-]{1,}", title) if v not in _STOP}


def _similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def assign_story_clusters(items: list[dict], threshold: float = 0.48) -> list[dict]:
    groups: list[tuple[set[str], list[dict]]] = []
    for item in items:
        tokens = _tokens(item)
        match = next((g for g in groups if _similarity(tokens, g[0]) >= threshold), None)
        if match is None:
            groups.append((tokens, [item]))
        else:
            match[0].update(tokens)
            match[1].append(item)
    stories = []
    for tokens, group in groups:
        primary = max(group, key=lambda i: (_QUALITY.get(i.get("source", ""), 1),
                                            len(i.get("text", ""))))
        story = dict(primary)
        fingerprint = " ".join(sorted(tokens))
        story.update({
            "story_id": hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
            "event_fingerprint": fingerprint,
            "canonical_url": canonicalize_url(primary["url"]),
            "supporting_sources": [
                {"source": i.get("source", ""), "title": i.get("title", ""), "url": i["url"]}
                for i in group if i["url"] != primary["url"]
            ],
            "duplicate_count": sum(i.get("duplicate_count", 1) for i in group),
        })
        stories.append(story)
    return stories


def diversify(items: list[dict], limit: int = 10) -> list[dict]:
    remaining, selected, counts = list(items), [], Counter()
    while remaining and len(selected) < limit:
        chosen = max(
            remaining,
            key=lambda i: (1.0 if i.get("keep", True) else 0.0)
            + 0.2 / max(1, i.get("duplicate_count", 1))
            - 0.18 * max((counts[t] for t in i.get("tags", [])), default=0),
        )
        remaining.remove(chosen)
        selected.append(chosen)
        counts.update(chosen.get("tags", []))
    return selected
