"""
Shared escaping helpers for Telegram messages.

escape_markdown_v2: extracted out of await_approval.py so any node
sending a Telegram message with LLM-generated variable text can reuse it
-- unescaped underscores/asterisks/etc. 400 under Telegram's MarkdownV2
parse mode if not escaped. Used only where parse_mode="MarkdownV2" is
passed explicitly (saturday/nodes/await_approval.py) -- NOT the project's
default parse_mode (see bot_client.py).

escape_html: added 2026-07-19, root-causing a real send_telegram_plan
400 (docs/WORKFLOW.md has the full investigation) -- assemble_plan.py/
assemble_digest.py were escaping underscores with MarkdownV2 syntax
(`\\_`) while bot_client.py's default parse_mode was legacy v1
"Markdown", which has NO escape mechanism at all (a literal backslash
does nothing, the underscore still opens/closes an entity) -- an
unrelated real "_" appearing in LLM-generated text (e.g. "last_activity")
threw off entity pairing for the entire rest of the message. Fixed by
switching the project's default parse_mode to "HTML" instead (bot_client.py)
and this project's dynamic/free-text content (titles, reasoning, card
names, tags, URLs) now needs HTML-escaping instead of Markdown-escaping
-- Telegram's HTML mode only reserves '&', '<', '>' in text content
(and inside attribute values like <a href="...">), a much smaller and
less error-prone set than MarkdownV2's, with real formatting done via
actual <b>/<i>/<a>/<code> tags instead of */_/[]() syntax.
"""

import re

_SPECIAL_CHARS = r'_*[]()~`>#+-=|{}.!'


def escape_markdown_v2(text: str) -> str:
    return re.sub(f'([{re.escape(_SPECIAL_CHARS)}])', r'\\\1', text)


def escape_html(text: str) -> str:
    """'&' must be replaced first -- otherwise the '&' introduced by
    escaping '<'/'>' would itself get re-escaped into '&amp;lt;'/
    '&amp;gt;' on a second pass, which this single sequential
    .replace() chain avoids by construction (each .replace() call scans
    the ALREADY-escaped-so-far string exactly once, left to right)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_cost_line(cost_breakdown: dict[str, float] | None) -> str:
    """Real $ cost, broken out by provider (2026-07-26) -- shared by
    daily/nodes/assemble_digest.py and saturday/nodes/assemble_plan.py so a
    future model-provider swap's cost impact is directly visible per
    provider in both, not buried in one lump total. Returns "" (not
    appended anywhere) when no breakdown is given, so existing callers/
    tests that don't pass one see byte-identical output to before this
    existed."""
    if not cost_breakdown:
        return ""
    anthropic = cost_breakdown.get("anthropic", 0.0)
    nvidia = cost_breakdown.get("nvidia", 0.0)
    total = cost_breakdown.get("total", 0.0)
    return f"<i>Cost: ${total:.4f} (Anthropic: ${anthropic:.4f}, NVIDIA embeddings: ${nvidia:.4f})</i>"
