"""
Shared MarkdownV2 escaping helper for Telegram messages. Extracted out of
await_approval.py so any node sending a Telegram message with
LLM-generated variable text can reuse it -- unescaped underscores/
asterisks/etc. 400 under Telegram's MarkdownV2 parse mode if not escaped.
"""

import re

_SPECIAL_CHARS = r'_*[]()~`>#+-=|{}.!'


def escape_markdown_v2(text: str) -> str:
    return re.sub(f'([{re.escape(_SPECIAL_CHARS)}])', r'\\\1', text)
