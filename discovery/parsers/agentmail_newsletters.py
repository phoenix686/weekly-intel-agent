"""
Fetch proxy for newsletter sources read as email via a shared AgentMail
inbox rather than fetched as RSS -- originally built for the 4 Substack
sources confirmed unreachable specifically from GitHub Actions (IP-based
blocking of GitHub's shared runner ranges), then expanded (2026-07-18) to
also cover 6 real senders that never had an RSS entry at all: Decoding AI
Magazine, Ahead of AI (both moved off RSS onto this same inbox for
consistency), The AI Merge (Alex Razvant), DiamantAI, The Batch, and "AI
Engineering" (Sumanth P). See discovery/config/agentmail_sources.yaml
(gitignored -- real sender-address-to-source-name mapping, personal
subscription data) and its tracked .example counterpart.

One shared inbox receives all of these -- this is a single fetch per run
(list unread messages), not one per-source fetch the way blog_sources.yaml's
other entries work, since AgentMail's API is inbox-scoped, not
publication-scoped. discovery/nodes/scrape_blogs.py calls
fetch_agentmail_newsletters() directly, alongside (not through)
blog_sources.yaml's per-entry loop.

Real AgentMail API shape (agentmail>=0.5.8, verified against the
installed package's actual method signatures via direct inspection):
- client.inboxes.messages.list(inbox_id, labels=["unread"], limit=N) ->
  metadata only (subject, from_, labels, timestamps), no body.
- client.inboxes.messages.get(inbox_id, message_id) -> full Message
  (.html/.text/.extracted_html/.extracted_text).
- client.inboxes.messages.update(inbox_id, message_id, add_labels=[...],
  remove_labels=[...]) -- AgentMail has no dedicated mark-read endpoint;
  labels ARE the read/unread state, same conceptual pattern as
  discovery/seen_items.py's mark_seen(), but the "seen" state lives in
  AgentMail's own store, not ours.

Read window: messages.list(inbox_id, labels=["unread"]) IS the entire
time boundary -- every run processes everything unread since the last
check, and add_labels=["read"] after successful processing means the
next run naturally only sees what's new since then. No separate
date-range filter exists or should be added; duplicating the unread
label with a lookback window would be redundant and could actually
reintroduce a message a partial prior run already marked read.

URL EXTRACTION -- REAL, VERIFIED FINDING (2026-07-18), not the original
design: a real newsletter email's raw HTML does NOT contain the actual
destination URL in its hrefs at all. Both Substack (a real welcome email
from Ahead of AI, inspected directly) and beehiiv (a real welcome email
from "AI Engineering", inspected directly) wrap EVERY link in an opaque
click-tracking redirect -- e.g. https://email.mg-d0.substack.com/c/{encoded
token} or https://link.mail.beehiiv.com/ss/c/{encoded token} -- with the
real destination only recoverable by actually following the HTTP
redirect (confirmed real: resolving one of these by hand returned
https://magazine.sebastianraschka.com/subscribe, a real, different URL
than the tracking link itself). Parsing the raw HTML for a /p/{slug}
pattern directly (the original design) would NEVER match a real email --
this was caught before it shipped by inspecting real received messages,
not assumed to work from the synthetic fixture alone.

URL EXTRACTION -- THREE-TIER FIX (2026-07-22, real production bug): a
real Sunday Pipeline run (2026-07-22, run c6c5624d) failed to extract an
article URL for EVERY message it processed, including 3 with genuinely
real, present article links (Decoding AI Magazine "What's Harness
Engineering?", The Neural Maze "The SLM OCR Course...", AI Engineering
"[Hands-On] Build a Browser Automation Agent") -- confirmed a real bug,
not a content-free email, by re-running this exact extraction code
against the exact same real HTML from an unblocked machine: it found a
valid /p/{slug} match every time. The old code's ONLY extraction path
was a live HTTP redirect-follow per href (_resolve_redirect) -- the same
class of GitHub-Actions-shared-runner-IP-blocking issue already
confirmed twice elsewhere in this project (Substack RSS 403s,
2026-07-17; MarkTechPost's Cloudflare challenge, 2026-07-22) is the most
likely explanation, though it can't be directly confirmed without a live
GH Actions network trace.

Fix: _extract_article_url now tries three tiers, cheapest/most-robust
first, over the SAME hrefs list:
  1. The raw href already matches /p/{slug} directly -- e.g. a plain
     open.substack.com/pub/{name}/p/{slug} link, not itself a redirect.
     Zero network calls; cannot be affected by any redirect-target
     blocking, confirmed present in both the Decoding AI Magazine and
     The Neural Maze real messages.
  2. Substack's newer redirect/2/{base64} format embeds the real
     destination directly as base64-encoded JSON ({"e": "https://..."})
     -- decoded locally, zero network calls. Confirmed real: The Neural
     Maze's actual email decodes cleanly to its real /p/{slug} URL.
  3. Fall back to the original live HTTP redirect-follow -- still needed
     for genuinely opaque tracking-only links (beehiiv's
     link.mail.beehiiv.com tokens confirmed NOT base64-JSON-decodable;
     Substack's older redirect/{uuid}?j=... tokens are opaque too).
Tiers 1-2 remove the network dependency entirely for the cases they
cover (both real Substack failures above), directly shrinking exposure
to whatever caused the real failure; tier 3 remains the only path for
beehiiv and is still exposed to the same class of risk.

SEEN-MARKING ON EXTRACTION FAILURE (2026-07-22, Step 4 audit, post-
5cc11dd): traced the real control flow and found the code never marked
ANY extraction failure read/seen -- correct for a transient resolution
error (retryable), but wrong for a confirmed content-free email (a real
welcome/subscription message with no post link anywhere), which would
otherwise be re-fetched and re-erred on every future run forever, since
it can never succeed. _extract_article_url now reports
had_transient_error (True iff some tier-3 _resolve_redirect() call
itself failed, distinct from "resolved fine but wasn't a post");
fetch_agentmail_newsletters marks a confirmed-content-free message read
but leaves a transiently-failed one unread for retry.

CONTENT-FREE WARN TRIPWIRE (2026-07-22, same follow-up): before trusting
"confirmed content-free" as safe to mark read, read-only-inspected every
real currently-unread message that lands there (list+get only, no
messages.update() -- did not touch the live inbox). 6 of 7 were genuine
boilerplate (390-1851 real visible chars); one (DiamantAI's welcome
email) had 3013 chars of real content about the newsletter's repos,
course, and books -- still never produces a row (no href matches a post
URL, unchanged from before this fix), but worth a human glance if a
sender's format shifts. fetch_agentmail_newsletters now logs a WARNing
(not an error, still marks read) when a confirmed-content-free body
exceeds _SUBSTANTIAL_CONTENT_FREE_BODY_CHARS -- visibility only, no new
extraction/scoring path.

No langgraph imports.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from agentmail import AgentMail

logger = logging.getLogger(__name__)

_BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Bounds real HTTP resolution cost per message -- a real newsletter email
# can have dozens of hrefs (footer, social links, etc); the real article
# link is reliably among the first few in document order (title, "view
# in browser", first CTA), confirmed via real inspection of actual
# received emails. Stops as soon as a match is found, so this cap is a
# worst case, not a typical case.
_MAX_LINKS_TO_RESOLVE = 15

# The real, shared post-URL convention across both Substack and beehiiv
# (confirmed via real resolved redirects from actual received emails, not
# assumed) -- "/p/" followed by a slug. Deliberately not restricted to a
# hardcoded list of sender domains, since resolution reveals the real
# destination domain directly regardless of which ESP sent the email.
_POST_PATH_PATTERN = re.compile(r"/p/[\w-]")

# Groups every unrecognized-sender error together under one stable key --
# callers (discovery/parsers/scrape_blogs.py) group ParseResult.errors by
# their first tuple element to attribute one NodeCost per real source; a
# message from an address not in agentmail_sources.yaml has no real
# source_name to group under, so it needs a name too, distinct from any
# configured one.
_UNRECOGNIZED_SENDER = "AgentMail Newsletters (unrecognized sender)"


@dataclass
class ParseResult:
    rows: list[dict]
    errors: list[tuple[str, str]] = field(default_factory=list)


def _resolve_redirect(url: str, timeout: float = 10.0) -> str | None:
    """Follows a real HTTP redirect chain and returns the final resolved
    URL, or None on any failure. Real newsletter emails wrap every link
    in an opaque click-tracking redirect (Substack, beehiiv both
    confirmed) -- the raw href is never the real destination."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.url
    except Exception as e:
        logger.debug(f"agentmail_newsletters: redirect resolution failed for {url}: {e}")
        return None


_SUBSTACK_REDIRECT_2 = re.compile(r"substack\.com/redirect/2/([A-Za-z0-9_-]+)")


def _decode_substack_redirect_2(href: str) -> str | None:
    """Substack's newer /redirect/2/{base64} format embeds the real
    destination directly as base64url-encoded JSON ({"e": "https://..."})
    -- decodable with zero network calls, immune to any blocking of the
    resolution step itself. Confirmed real, 2026-07-22 (The Neural
    Maze's actual received email): decodes cleanly to its real
    /p/{slug} URL. Returns None on anything that doesn't match or
    doesn't decode -- never raises, same degrade-gracefully contract as
    _resolve_redirect."""
    match = _SUBSTACK_REDIRECT_2.search(href)
    if not match:
        return None
    token = match.group(1)
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("e")
    except Exception as e:
        logger.debug(f"agentmail_newsletters: redirect/2 decode failed for {href}: {e}")
        return None


def _extract_article_url(html: str) -> tuple[str | None, bool]:
    """Three tiers, cheapest/most-robust first, over the SAME hrefs list
    (document order, first _MAX_LINKS_TO_RESOLVE only) -- see this
    module's docstring for the real 2026-07-22 production failure that
    motivated tiers 1-2:
    1. The raw href already matches /p/{slug} directly (not itself a
       redirect) -- zero network calls.
    2. Substack's redirect/2/{base64} format decodes locally -- zero
       network calls.
    3. Fall back to a real HTTP request per href (the original,
       only-ever mechanism) -- still needed for genuinely opaque
       tracking-only links (beehiiv, Substack's older redirect/{uuid}
       format).

    Returns (url, had_transient_error). had_transient_error is True iff
    at least one tier-3 _resolve_redirect() call returned None -- under
    its documented contract (catches its own exceptions, never raises),
    that specifically means the HTTP request itself failed (timeout,
    connection error, non-2xx), not "resolved fine but wasn't a post" --
    that second case is a normal iteration miss, not an error. Real
    2026-07-22 finding (Step 4 audit): without this signal, a message
    where every candidate href genuinely has no post anywhere (e.g. a
    real welcome/subscription email) was indistinguishable from one that
    failed only because resolution errored out network-side -- both
    produced url=None with no way to tell them apart. Callers use this
    to decide whether the failure is safe to mark read (confirmed, won't
    change on retry) or must stay unread (retryable)."""
    hrefs = re.findall(r'href="([^"]+)"', html or "")
    candidates = hrefs[:_MAX_LINKS_TO_RESOLVE]

    for href in candidates:
        if _POST_PATH_PATTERN.search(href):
            return href, False

    for href in candidates:
        decoded = _decode_substack_redirect_2(href)
        if decoded and _POST_PATH_PATTERN.search(decoded):
            return decoded, False

    had_transient_error = False
    for href in candidates:
        resolved = _resolve_redirect(href)
        if resolved is None:
            had_transient_error = True
            continue
        if _POST_PATH_PATTERN.search(resolved):
            return resolved, False

    return None, had_transient_error


class _TextStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def _html_to_text(html: str) -> str:
    """Minimal, dependency-free HTML-to-text -- strips tags, collapses
    whitespace. Good enough for score_node's prompt (which only ever uses
    the first 500 chars anyway, see discovery/nodes/score.py's
    _score_batch), not a general-purpose renderer."""
    stripper = _TextStripper()
    stripper.feed(html or "")
    return re.sub(r"\s+", " ", " ".join(stripper.parts)).strip()


# Coarse tripwire, not a classifier (2026-07-22, Step 4 follow-up): real
# inspection of every currently-unread "confirmed content-free" message
# (read-only, live inbox) found 6 genuine welcome/subscription emails with
# 390-1851 chars of real visible boilerplate text, and one (DiamantAI's)
# with 3013 chars describing real newsletter content -- open-source repos,
# a course, two books. This threshold sits above the clear-boilerplate
# cluster and below that real example, so a WARN fires if a "confirmed
# content-free" message's body is this substantial -- purely so Pooja can
# notice if a sender's format ever shifts toward writing real content
# inline, without building any new extraction/scoring path for it. A false
# positive (a long but still-boilerplate email) is expected and cheap to
# dismiss at WARN level; a false negative is the actual risk being
# guarded against, so this deliberately leans toward over-flagging.
_SUBSTANTIAL_CONTENT_FREE_BODY_CHARS = 2000


def _visible_body_length(html: str) -> int:
    """Strips <style>/<script> before handing off to _html_to_text --
    confirmed real, 2026-07-22: _html_to_text's HTMLParser-based tag
    stripping does NOT skip <style> content on its own, and a real
    newsletter email's <head><style>...</style></head> block alone runs
    to thousands of chars of raw CSS, which would swamp any real signal
    in the visible-body length used by the WARN tripwire above. Scoped
    to that check only -- does not change what _html_to_text returns for
    an actually-matched row's own 'text' field; that's a separate,
    pre-existing question out of scope here."""
    cleaned = re.sub(r"(?is)<style.*?</style>", " ", html or "")
    cleaned = re.sub(r"(?is)<script.*?</script>", " ", cleaned)
    return len(_html_to_text(cleaned))


def _match_sender_name(from_address: str, sender_to_name: dict[str, str]) -> str | None:
    """Matches a message's real from_ header (e.g. 'Jane Doe <jane@pub.
    substack.com>') against the configured sender_to_name map's bare
    addresses. Every RawItem from this inbox must be attributed to the
    real publication it came from -- 10 distinct senders share one
    inbox, so a generic 'agentmail' source label would collapse them all
    into one indistinguishable bucket."""
    match = re.search(r"[\w.+-]+@[\w.-]+", from_address or "")
    address = match.group(0).lower() if match else ""
    return sender_to_name.get(address)


def fetch_agentmail_newsletters(
    inbox_id: str, sender_to_name: dict[str, str], limit: int = 20
) -> ParseResult:
    """Fetch unread newsletter emails from the shared AgentMail inbox,
    parse each into a RawItem-shaped row (source-attributed via
    sender_to_name -- see _match_sender_name), mark each as read after
    successful processing (add_labels=["read"], remove_labels=["unread"])
    so the next run doesn't reprocess it. The unread label IS the read
    window -- no separate date-range filter.

    A message with no resolvable article URL is ALSO marked read, but
    only if _extract_article_url confirms every candidate href was
    checked without a resolution error (had_transient_error=False) --
    i.e. it's a real content-free email (welcome/subscription
    confirmation), not one that merely failed to resolve this run. A
    resolution error (had_transient_error=True) leaves it unread so the
    next run retries. Real gap found and fixed 2026-07-22 (Step 4 audit,
    post-5cc11dd): before this, EVERY "no resolvable article URL" case
    was left unread unconditionally -- safe for transient failures, but
    meant a genuinely content-free email would be re-fetched and
    re-erred on forever, since it can never succeed.

    A per-message failure (parse error, unrecognized sender, no
    resolvable article URL, mark-read failure) is recorded in errors and
    does not stop the rest of the batch -- same reliability contract as
    discovery/parsers/rss_common.py's fetch_rss_feed. An inbox-level
    failure (bad API key, network error) is caught the same way: rows
    stays empty, errors gets one entry, never raises.

    errors' first tuple element is always a real source_name (matching a
    row's own author_name) or _UNRECOGNIZED_SENDER -- never a raw email
    subject or message_id -- so callers can group errors by source the
    same way they group rows, for one NodeCost per real sender rather
    than one generic "AgentMail Newsletters" bucket covering all 10."""
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
        listing = client.inboxes.messages.list(inbox_id, labels=["unread"], limit=limit)
    except Exception as e:
        errors.append(("AgentMail Newsletters", str(e)))
        return ParseResult(rows=rows, errors=errors)

    for item in listing.messages:
        label = getattr(item, "subject", None) or item.message_id
        source_name = None
        try:
            message = client.inboxes.messages.get(inbox_id, item.message_id)

            source_name = _match_sender_name(message.from_, sender_to_name)
            if source_name is None:
                errors.append((_UNRECOGNIZED_SENDER, f"{label} (from {message.from_!r}): unrecognized sender"))
                continue

            html = message.extracted_html or message.html or ""
            article_url, had_transient_error = _extract_article_url(html)
            if not article_url:
                if had_transient_error:
                    errors.append((source_name, f"{label}: no resolvable article URL found in email body (resolution error -- left unread for retry)"))
                    continue
                errors.append((source_name, f"{label}: no resolvable article URL found in email body (confirmed content-free -- marking read)"))
                body_len = _visible_body_length(html)
                if body_len > _SUBSTANTIAL_CONTENT_FREE_BODY_CHARS:
                    logger.warning(
                        f"agentmail_newsletters: confirmed content-free but body is substantial "
                        f"({body_len} chars) -- sender: {source_name}, subject: {label!r} -- consider reviewing"
                    )
                client.inboxes.messages.update(
                    inbox_id, item.message_id, add_labels=["read"], remove_labels=["unread"]
                )
                continue

            text_source = message.extracted_html or message.html or message.extracted_text or message.text or ""
            text = _html_to_text(text_source) if "<" in text_source else text_source

            rows.append({
                "title": message.subject or article_url,
                "text": text,
                "url": article_url,
                "author_name": source_name,
                "author_handle": "",
                "fetched_at": message.timestamp.isoformat(),
                "is_thread": False,
                "thread_contents": None,
                "expanded_urls": [],
            })

            client.inboxes.messages.update(
                inbox_id, item.message_id, add_labels=["read"], remove_labels=["unread"]
            )
        except Exception as e:
            errors.append((source_name or _UNRECOGNIZED_SENDER, f"{label}: {e}"))

    return ParseResult(rows=rows, errors=errors)
