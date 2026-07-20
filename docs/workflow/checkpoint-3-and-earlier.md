[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Part 7 + ownership pieces + Checkpoint 3

## Part 7 additions (cross-run dedup, daily/Sunday sources, source-discovery, digest feedback)

### `discovery/seen_items.py`  _(new)_
- **What it does:** Cross-run dedup against `("weekly_intel", "seen_items")`, keyed
  by item URL. "Seen" = already scored (keep=True or False), no expiry.
  `filter_unseen(items)` is called from `cluster_dedupe_node` (check side, done).
  `mark_seen(urls)` needs a one-line call at the end of `score_node`, after the
  scoring loop completes — **not yet wired; that edit is inside score.py, Pooja's
  LLM-calling file, flagged rather than done.**
- **Key exports:** `filter_unseen(items) -> (unseen, seen_urls)`, `mark_seen(urls)`
- **Depended on by:** `discovery/nodes/cluster_dedupe.py`

### `discovery/nodes/cluster_dedupe.py`  _(updated)_
- Now calls `filter_unseen()` right after building `clustered_items`, before
  returning — an already-seen item never reaches `score_node`'s paid Haiku call.
  Also passes through `has_video`/`video_url` if present on the representative item.

### `discovery/parsers/rss_common.py`  _(new)_
- **What it does:** Shared stdlib-only RSS 2.0 fetch+parse helper (urllib +
  `xml.etree.ElementTree`). Strips XML-illegal control characters before parsing
  (found genuinely malformed control bytes in the real Smol AI News feed — this
  guards against that class of real-world feed defect generally, not just that feed).
  Detects a companion YouTube link in the description (`has_video`/`video_url`).
- **Key exports:** `fetch_rss_feed(feed_url, source_name, limit=30) -> ParseResult`

### `discovery/parsers/scrape_blogs.py`  _(implemented — was a stub)_
- **What it does:** `BLOG_FEEDS` populated with 4 confirmed, verified-live Sunday-only
  feeds: LangChain blog, JamWithAI, DecodingML/DecodingAI, Latent Space. Applies a
  keyword heuristic to drop LangChain customer case-study/success-story posts (no
  `<category>` signal exists in that feed to filter on structurally — heuristic
  only, `score_node`'s own taste-profile scoring is the second line of defense).
  **Anthropic's developer blog is NOT included** — confirmed no official RSS
  (`/engineering/rss.xml`, `/news/rss.xml`, `/research/rss.xml`, `/index.xml` all
  404), and the page is client-side-rendered (Next.js) so a plain HTML scrape finds
  no post links either. Flagged, not guessed around.
- **Key exports:** `fetch_blog_entries(feed_urls) -> ParseResult`, `BLOG_FEEDS`

### `discovery/nodes/tldr_ai.py`, `discovery/nodes/smol_ai_news.py`, `discovery/nodes/hacker_news.py`  _(new)_
- **What they do:** Daily-cadence source nodes, one per source, following
  `ingest_bookmarks`'s pattern exactly. Feed URLs verified live: `tldr.tech/api/rss/ai`,
  `news.smol.ai/rss.xml`, `news.ycombinator.com/rss`.
- **Depended on by:** `discovery/graph.py` (once wired — Pooja's; needs to be included
  in BOTH daily and Sunday discovery subgraph invocations)

### `discovery/candidate_search.py`  _(new)_
- **What it does:** DuckDuckGo unofficial HTML-endpoint search (no key/card), stdlib
  only. Observed real rate-limiting/anti-bot throttling under repeated rapid testing
  in this session (returns a challenge page instead of results after several quick
  calls) — worked fine on the first real call. Weekly, low-volume production usage is
  unlikely to hit this, but there's no hard guarantee; worth knowing about.
- **Key exports:** `search_duckduckgo(query, limit=10) -> list[{"title","url"}]`

### `discovery/cadence.py`  _(new)_
- **What it does:** Fetches a candidate feed, filters out one-off pages (too few
  items = not an ongoing publication), classifies "daily" vs "sporadic" by median
  gap between item timestamps.
- **Key exports:** `detect_cadence(feed_url, source_name) -> "daily"|"sporadic"|None`

### `discovery/source_config.py`  _(new)_
- **What it does:** Persisted, editable source list — `data/sources.json` (JSON, not
  YAML — no `yaml` package installed, and JSON is explicitly allowed by the spec),
  `{"daily": [...], "sunday": [...]}`. `add_source()` is idempotent by feed_url.
- **Key exports:** `load_sources() -> dict`, `add_source(bucket, name, feed_url)`
- **Depended on by:** `discovery/nodes/discovered_sources.py`

### `discovery/nodes/discovered_sources.py`  _(new)_
- **What it does:** Generic node reading `data/sources.json` per bucket at runtime —
  `discovered_daily_sources`/`discovered_sunday_sources`. This is how NEWLY-approved
  sources (Part C) get ingested without a code change; the 8 originally-confirmed
  sources keep their own dedicated node files (Part B) per the spec's "each source is
  its own node" instruction. Currently a no-op (empty config) until sources are approved.
- **Depended on by:** `discovery/graph.py` (once wired — Pooja's)

### `discovery/candidate_discovery.py`  _(new)_
- **What it does:** Orchestrates search → feed-guess → cadence filter → sample →
  score via the EXISTING `score_node` (no new scoring mechanism authored). Returns
  qualifying candidates (`sample_keep_rate >= 1.0`, i.e. every sampled item kept).
  **Does NOT build the actual Send-based per-candidate proposal subgraph (Part C
  step 4)** — that reuses `await_approval.py`'s dedicated-thread `interrupt`/`Send`
  pattern and is Pooja's, per this project's LangGraph-authorship line. This module
  produces the candidate list that subgraph would fan out over.
- **Key exports:** `find_candidates(taste_domain_query, search_limit=10) -> list[dict]`

### `sunday/source_discovery_actions.py`  _(new)_
- **What it does:** Plain functions mirroring `approval_actions.py`'s split —
  `handle_source_approval` (writes to `data/sources.json`, sends Telegram
  confirmation), `handle_source_rejection` (records to
  `("weekly_intel", "rejected_source_candidates")`, keyed by feed_url, so it's never
  re-proposed), `is_already_rejected(feed_url)`.

### `sunday/approval_actions.py`  _(generalized)_
- `handle_rejection(item, run_id)` is now a thin wrapper over a new
  `handle_feedback(item, feedback_text, sentiment, run_id)`, which reuses the exact
  same incremental Haiku-YAML-update mechanism (prompt now framed as "new signal
  (sentiment, count)" rather than rejection-only). Writes to
  `("weekly_intel", "feedback_events")` (renamed from `rejection_events` — broader
  scope now covers both digest/plan feedback and proposal rejections).

### `state.py`  _(updated)_
- `RawItem`/`ClusteredItem`/`ScoredItem` gained optional `has_video`/`video_url`
  (`NotRequired`) for Part B's video-link detection.
- `DailyGraphState` gained `digest_item_map: dict[int, dict]`; `SundayGraphState`
  gained `plan_item_map: dict[int, dict]` — populated by `assemble_digest`/
  `assemble_plan`, persisted (keyed by the sent message_id, alongside `run_id`) by
  `send_telegram_digest`/`send_telegram_plan` into `("weekly_intel", "digest_item_map")`.

### `daily/nodes/assemble_digest.py`, `sunday/nodes/assemble_plan.py`  _(updated)_
- `format_digest`/`format_plan` now return `(text, item_map)` instead of just text —
  `item_map` is `{number: {url, title, text, tags, reasoning}}`, matching the numbers
  shown in the sent message.

### `daily/nodes/send_telegram_digest.py`, `sunday/nodes/send_telegram_plan.py`  _(updated)_
- Capture the real `message_id` from `send_message`'s response and persist
  `{run_id, items: item_map}` to `("weekly_intel", "digest_item_map")`, keyed by that
  message_id — this is what lets a reply hours/days later resolve back to real items.

### `telegram/feedback_router.py`  _(built out — was a stub)_
- **What it does:** `handle_feedback(message)` (the existing entry point polling.py
  already calls) now checks `reply_to_message.message_id` against
  `digest_item_map`; if found, resolves each numbered item and calls
  `approval_actions.handle_feedback` per item — NOT gated behind approval.
  **`_parse_numbered_feedback(text)` is a stub** (`NotImplementedError`) — turning
  free-form reply text into structured `[{item_number, feedback_text, sentiment}]`
  is a direct Haiku call, Pooja's per the LangGraph/LLM authorship line. Sentiment
  inference is folded into that same call rather than a second LLM round-trip.
  Falls through safely to the existing unrouted-log behavior if the parse isn't
  implemented yet or the reply isn't a match.
- Verified end-to-end except for the one stubbed call: a real digest was sent, a
  real Telegram reply was correctly matched back to it via `reply_to_message`, and
  a stand-in parse output was used to prove resolution + real dual-directional
  `taste_profile.yaml` updates (both a positive and a negative signal landed
  correctly in one test run).

## Ownership-line pieces (explicitly handed to Claude Code, built + verified real)

Pooja explicitly authorized building all four previously-flagged pieces directly
(overriding the standing LangGraph/LLM-authorship split in CLAUDE.md Section 6 for
these specific items):

### `discovery/nodes/score.py` -- `mark_seen()` call added
End of `score_node`, after the scoring loop: `mark_seen([item["url"] for item in all_scored])`.
Verified real: cluster_dedupe -> score_node -> rerun cluster_dedupe with the same
input now returns 0 clustered_items automatically, no manual call needed.

### `discovery/graph.py` -- full daily/Sunday wiring
`build_discovery_subgraph(include_sunday_only: bool = False)`. Daily
(`daily/graph.py`, default) fans out `ingest_bookmarks`, `tldr_ai`, `smol_ai_news`,
`hacker_news`, `discovered_daily_sources` -> `cluster_dedupe` -> `score`. Sunday
(`sunday/graph.py`, `include_sunday_only=True`) adds `scrape_blogs`,
`anthropic_blog`, `process_adhoc_input`, `discovered_sunday_sources` on top.
`search_web` is NOT wired in -- still an unconfigured `NotImplementedError` stub,
out of scope (X dropped from V1); wiring it in would break every run.
Required a real fix along the way: `errors` in `DiscoverySubgraphState` wasn't a
reducer, so multiple parallel source nodes writing to it in one superstep hit a
genuine `InvalidUpdateError` -- changed to `Annotated[list[str], operator.add]`.
Verified real: ran both subgraphs live. Daily: 88 real items (HN, Twillot,
Smol AI News, TLDR AI), $0.009290. Sunday: 182 raw items across 6 sources,
deduped to 94 clustered (the 88 daily-source items were correctly filtered as
already-seen from the daily run moments earlier -- an unplanned but real
cross-invocation proof of Part A working).

### `discovery/parsers/anthropic_blog.py`, `discovery/nodes/anthropic_blog.py`  _(new)_
Pooja corrected an earlier finding of mine: anthropic.com/engineering IS
server-side rendered with the full post list, when fetched with a real browser
User-Agent (a request without one gets served a JS-only shell -- that's what
caused the original "needs headless browser" conclusion). Scrapes the listing
page's `<article>` blocks (href/h3/date) via regex matched on DOM structure, not
Next.js's hashed CSS module class names (which can change on redeploy). Verified
real: 24 real posts with correct titles/dates/URLs.

### `telegram/markdown.py`  _(new)_
`escape_markdown_v2()` extracted out of `await_approval.py` (which now imports it)
since `discover_sources.py` needed the identical escaping.

### `sunday/nodes/discover_sources.py`  _(new)_
Mirrors `await_approval.py`'s exact verified pattern: `SourceProposalState`,
`thread_id_for` (prefixed `source-proposal-` to avoid thread_id collisions with
content proposals), `get_source_proposal_graph()` (own checkpointer, own
compiled child graph), `route_to_source_discovery` (conditional-edge fan-out,
mirroring `classify_item` -> `_fan_out_after_classify`'s two-step shape),
`source_proposal_worker` (writes to a SEPARATE store namespace,
`pending_source_resume_map`, not `pending_resume_map` -- keeps source proposals
fully decoupled from content proposals). `discover_sources` node calls
`find_candidates()` (Part C, already built) with a hardcoded query
(`DISCOVERY_QUERY = "AI agent engineering newsletter blog"`).
Wired into `sunday/graph.py`: fans out from START in parallel with
`discovery_subgraph`, `source_proposal_worker` edges into `update_profile`
alongside `proposal_worker`.
`telegram/polling.py` updated: checks `pending_source_resume_map` (in addition to
the existing `pending_resume_map`) on a reply, and a new
`_handle_source_approval_reply` resumes `get_source_proposal_graph()` and calls
`handle_source_approval`/`handle_source_rejection` instead of the content-proposal
action functions.
Verified real, both directions: sent 2 real Telegram source proposals, approved
one (landed in `data/sources.json`'s bucket + real Telegram confirmation sent),
rejected the other (`is_already_rejected` confirmed `True` afterward --
won't be re-proposed). `pending_source_resume_map` confirmed empty after both
resolved.

### `telegram/feedback_router.py` -- `_parse_numbered_feedback` implemented
Real Haiku call, same structured-output pattern as `classify_item.py` (prompt with
item context -> JSON parse -> retry-on-malformed -> defensive validation of
`item_number`/`sentiment`). Verified real: given the exact reply text
"1. I really liked this\n2. Not interested", correctly parsed to
`[{item_number: 1, sentiment: positive}, {item_number: 2, sentiment: negative}]`,
and both directions landed correctly in `taste_profile.yaml` in one run.

## Checkpoint 3 additions (resume scheduling + ad-hoc test coverage)

Scope: `await_approval.py`'s message_id capture and `telegram/polling.py`'s
three-way routing and `process_adhoc_input.py` were all already implemented
(committed in a prior session) but had no test coverage and no scheduled
trigger. This checkpoint added both, and treated the existing code as
unverified per CLAUDE.md Section 5 rather than assuming it was already
correct.

### `tests/test_await_approval.py`  _(new)_
- Mocked unit tests for `proposal_worker`: confirms the `store.put()` call
  after a proposal's child graph returns has the correct
  namespace/key/value, and that the child graph is invoked on the right
  dedicated `thread_id`. 3/3 passing.

### `scripts/test_pending_resume_map_roundtrip.py`  _(new)_
- Real-store smoke test, same pattern as `scripts/test_companion_store_roundtrip.py`:
  mocks only the Telegram send + child graph invoke, writes/reads/deletes a
  real `pending_resume_map` entry against the live Supabase Postgres store.
  Run and passing (see `feature_list.json` evidence for output).

### `tests/test_polling.py`  _(new)_
- Mocked unit tests for `poll_once()`'s three routing outcomes (resume /
  feedback / ad-hoc) in a single call, plus reject-path, unrecognized-decision,
  offset-advancement, and empty-updates coverage. 5/5 passing.

### `tests/test_process_adhoc_input.py`  _(new)_
- Mocked unit tests for the node (one-item, empty-queue, blank-text-skipped,
  multi-item) plus a graph-structure test confirming `process_adhoc_input` is
  present in `build_discovery_subgraph(include_sunday_only=True)` and absent
  from the daily-only graph. 5/5 passing. Found and flagged (did not silently
  fix) a spec-vs-code discrepancy: the node uses `source="adhoc_telegram"`,
  not the literal `"adhoc"` in the original checkpoint-3 spec text — already
  true before this checkpoint and already documented above.

### `scripts/run_poll.py`, `.github/workflows/poll.yml`  _(new)_
- See "Scheduled runs" above. `run_poll.py` mirrors `run_daily.py`'s
  load_dotenv/setup_logging/invoke shape. Ran live once against production
  Telegram/Postgres via Bash — completed cleanly with no pending items to
  process.

