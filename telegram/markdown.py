"""
Shared MarkdownV2 escaping helper for Telegram messages. Extracted from
await_approval.py since discover_sources.py needs the identical escaping
for the same reason (LLM-generated variable text can contain unescaped
underscores/asterisks/etc., which 400s under Telegram's MarkdownV2 parse
mode if not escaped).
"""

import re

_SPECIAL_CHARS = r'_*[]()~`>#+-=|{}.!'


def escape_markdown_v2(text: str) -> str:
    return re.sub(f'([{re.escape(_SPECIAL_CHARS)}])', r'\\\1', text)
