"""
One-off diagnostic (NOT a permanent test): calls the real _resolve_redirect()
directly against the real, currently-failing beehiiv URLs from the three
stuck "AI Engineering" emails (Issue 6 investigation), to get the actual
exception (timeout / connection refused / non-2xx / something else)
without waiting for another scheduled Sunday run.

Read-only: fetches messages via messages.list()/messages.get() only --
NEVER calls messages.update(), so this cannot mark anything read or
change the real inbox's unread state. No LLM calls (no anthropic client
touched at all).

Relies on the 2026-07-26 observability fix (discovery/parsers/
agentmail_newsletters.py's _resolve_redirect now logs at INFO/WARNING,
not DEBUG) -- the real exception prints via the logging handler itself
when _resolve_redirect is called directly here, same as it would in a
real run's captured log.

Run: uv run --env-file .env python scripts/diagnose_agentmail_resolution.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

import os
import re

from agentmail import AgentMail

from discovery.agentmail_sources_config import load_agentmail_config
from discovery.parsers.agentmail_newsletters import (
    _match_sender_name, _resolve_redirect, _decode_substack_redirect_2,
    _POST_PATH_PATTERN, _MAX_LINKS_TO_RESOLVE,
)

TARGET_SUBJECTS = {
    "Unlimited OCR: One-shot Long-horizon Parsing",
    "[Hands-On] Build a Research Assistant With Memory",
    "[Hands-On] Build a Browser Automation Agent",
}


def main() -> None:
    config = load_agentmail_config()
    inbox_id = config["inbox_id"]
    sender_to_name = {s["sender"]: s["name"] for s in config.get("sources", [])}

    client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
    listing = client.inboxes.messages.list(inbox_id, labels=["unread"], limit=20)

    found = 0
    for item in listing.messages:
        subject = getattr(item, "subject", None) or ""
        if subject not in TARGET_SUBJECTS:
            continue
        found += 1

        message = client.inboxes.messages.get(inbox_id, item.message_id)
        source_name = _match_sender_name(message.from_, sender_to_name)
        html = message.extracted_html or message.html or ""
        hrefs = re.findall(r'href="([^"]+)"', html)[:_MAX_LINKS_TO_RESOLVE]

        print("=" * 70)
        print(f"SUBJECT: {subject!r}")
        print(f"SENDER: {source_name!r}  (from_={message.from_!r})")
        print(f"Candidate hrefs (first {_MAX_LINKS_TO_RESOLVE}): {len(hrefs)}")

        for i, href in enumerate(hrefs, 1):
            if _POST_PATH_PATTERN.search(href):
                print(f"  [{i}] TIER-1 MATCH (raw href already a post link): {href}")
                continue

            decoded = _decode_substack_redirect_2(href)
            if decoded and _POST_PATH_PATTERN.search(decoded):
                print(f"  [{i}] TIER-2 MATCH (redirect/2 decode): {href} -> {decoded}")
                continue

            print(f"  [{i}] TIER-3 attempt: {href}")
            try:
                resolved = _resolve_redirect(href)
            except Exception as e:
                print(f"      -> UNCAUGHT EXCEPTION (outside _resolve_redirect's own try/except): {type(e).__name__}: {e}")
                continue
            if resolved is None:
                print("      -> FAILED (real exception type/message logged above via the WARNING line)")
            else:
                print(f"      -> SUCCESS: {resolved}")

    print("=" * 70)
    print(f"\nTotal target messages found in current unread inbox (limit=20): {found}/3")
    if found < 3:
        print(
            "NOTE: fewer than 3 found -- either already resolved/marked read by a "
            "prior run, or fell outside the 20-message fetch_limit window this time."
        )


if __name__ == "__main__":
    main()
