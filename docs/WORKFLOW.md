# Workflow Map

Last updated: Checkpoint 4 closeout + scrape-blogs-node fix (see bottom sections)

## Scheduled runs (GitHub Actions)

- **`.github/workflows/daily.yml`** — `30 2 * * 1-6` (02:30 UTC / 08:00 IST,
  Monday-Saturday; Sunday is skipped since the Sunday workflow's discovery
  subgraph already covers that day). Runs `scripts/run_daily.py`.
- **`.github/workflows/sunday.yml`** — `30 13 * * 0` (13:30 UTC / 19:00 IST,
  Sunday only). Runs `scripts/run_sunday.py`.
- **`.github/workflows/poll.yml`** _(new, Checkpoint 3)_ — `0 3 * * *` (03:00
  UTC / 08:30 IST, **every day including Sunday** — unlike `daily.yml`, which
  skips Sunday, this must run all 7 days since resumes/feedback/ad-hoc input
  can arrive any day). Runs `scripts/run_poll.py`, which calls
  `telegram.polling.poll_once()`. Reuses the same secrets as `sunday.yml`
  (Trello + Telegram + Anthropic + `DB_URI`) — no new secrets needed, since a
  resume can trigger `handle_approval`/`handle_rejection`.
- All three also have `workflow_dispatch:` for manual triggering (Actions tab →
  select the workflow → "Run workflow"), and a `concurrency` group
  (`cancel-in-progress: false`) so a manual trigger can't stack with an
  already-running scheduled job — the newer run queues instead of
  cancelling the older one, since killing a run mid-Anthropic-call would
  waste the API cost already spent.
- The Sunday job does NOT stay alive waiting for Telegram approve/reject
  replies — each proposal pauses on its own dedicated checkpointer thread in
  Postgres (per-proposal `interrupt()`, verified in Parts 1-7), and the job
  itself exits normally once every proposal has been sent, whether or not any
  are still pending. Resuming a paused proposal happens later, in a separate
  process, via `telegram/polling.py`'s `poll_once()`, now scheduled daily via
  `poll.yml` (Checkpoint 3). `timeout-minutes: 20` on daily/sunday and
  `timeout-minutes: 10` on poll (lighter job, no LLM calls of its own beyond
  what a resume/feedback path triggers) are safety nets against a genuine
  hang, not a wait for replies.
- **Blocker found and resolved this session:** `.github/` had been added to
  `.gitignore` in an uncommitted working-tree change that predated Checkpoint
  3 (bundled with unrelated changes). `daily.yml` and `sunday.yml` had in
  fact **never been committed, in any commit, at any point in this repo's
  history** before now. Flagged to Pooja rather than silently edited; she
  chose to un-ignore `.github/` and commit all three workflows (commit
  `77bc623`). All three now show up under `git ls-files .github/` and will
  actually run on GitHub once their secrets are confirmed configured.
- All three workflows require these repo secrets to be configured under Settings →
  Secrets and variables → Actions (this workflow file only *references* them
  — adding the actual values is a manual step, not something committed code
  can do): `DB_URI`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, plus for Sunday/poll `TRELLO_API_KEY`, `TRELLO_TOKEN`,
  `TRELLO_BOARD_ID`. `LANGSMITH_TRACING`/`LANGSMITH_API_KEY`/`LANGSMITH_PROJECT`
  are passed through to all three if set, for tracing parity with local runs.
- **Known gap, not worked around:** `ingest_bookmarks` reads
  `TWILLOT_JSON_PATH` (default `data/tweets.json`), but `data/` is
  gitignored — that file doesn't exist in a fresh Actions checkout, so both
  workflows will currently fail at `ingest_bookmarks` until this is resolved
  (e.g. committing a bookmarks export despite the gitignore, fetching it from
  external storage in a workflow step, or making `ingest_bookmarks` tolerate
  a missing file in automated contexts). Flagged rather than guessed around.
- `requirements.txt` was missing `python-dotenv` and
  `langgraph-checkpoint-postgres` (both entrypoint scripts need the former;
  `checkpointer_config.py`/`memory_store_config.py` need the latter) — added
  both; `pyproject.toml` already listed them correctly, `requirements.txt` had
  drifted out of sync.

## How data flows through this project

**Daily pipeline** — `python scripts/run_daily.py` ingests bookmarks, scores them,
formats a digest, and sends it to Telegram. No checkpointer; no interrupts.

**Sunday pipeline** — `python scripts/run_sunday.py` runs the full Sunday graph,
which:
1. Runs the discovery subgraph (ingest → cluster_dedupe → score)
2. Reads open Trello cards from the Brain board
3. Correlates scored items against Trello cards (filters to `keep=True` items only)
4. Classifies each kept item: `plan_item` or `project_proposal`
5. **Fans out** via `Send`: simultaneously assembles the plan and dispatches each
   proposal to a `proposal_worker` node
6. `assemble_plan` → `send_telegram_plan` sends the weekly plan to Telegram
7. Each `proposal_worker` invokes a **per-proposal child graph** on its own dedicated
   checkpointer thread (hash of proposal URL). The child graph sends a Telegram message
   and calls `interrupt()`. The parent completes normally — proposals sit paused on
   independent threads, fully decoupled from the parent run's lifecycle.
8. `update_profile` fires once all branches complete (fan-in): logs per-run cost summary
   to `data/cost_log.csv`.

**Proposal resume flow** (happens hours later, separate process) — `telegram/polling.py`:
1. Calls Telegram `getUpdates`, persisting the offset in the Postgres store
2. For each update that is a reply to a known proposal message:
   - Looks up `{thread_id, proposal_id, run_id}` from `pending_resume_map` in store
   - Parses "approve"/"reject" from reply text (case-insensitive + common synonyms)
   - Resumes the proposal's child graph with `Command(resume=decision)`
   - Calls `approval_actions.handle_approval` or `handle_rejection` with the resolved item
   - Deletes the `pending_resume_map` entry
3. Other replies → `feedback_router.handle_feedback` (stub — daily feedback path not yet built)
4. Non-reply messages → queued in `("weekly_intel", "adhoc_queue")` for next Sunday's
   `process_adhoc_input` node

State handoff: `DailyGraphState` and `SundayGraphState` share `run_id`, `scored_items`,
`costs`, and `errors` with `DiscoverySubgraphState` by key intersection. `raw_items`,
`clustered_items`, and `stage` stay internal to the subgraph.

---

## File-by-file reference

### `state.py`
- **What it does:** Defines all shared TypedDicts for LangGraph state.
- **Key schemas:**
  - `RawItem`, `ClusteredItem`, `ScoredItem`, `NodeCost`, `DiscoverySubgraphState`,
    `DailyGraphState`, `make_daily_initial_state()`
  - `SundayGraphState` — contains `pending_resumes: Annotated[list[dict], operator.add]`
    (populated by `proposal_worker` per fan-out branch: `{proposal_id, thread_id, message_id}`).
    `approval_results` removed — proposals now resolve async, outside the Sunday run.
- **Key exports:** all TypedDicts + `make_sunday_initial_state()`, `make_daily_initial_state()`
- **Depended on by:** every node file and both graph files

### `connection_pool.py`  _(added Parts 1-7)_
- **What it does:** Singleton `psycopg_pool.ConnectionPool` shared by both
  `checkpointer_config.py` and `memory_store_config.py`. Prevents each config
  module from opening its own raw connection. Pool kwargs: `autocommit=True`,
  `prepare_threshold=0`.
- **Key exports:** `get_connection_pool() -> ConnectionPool`
- **Depended on by:** `checkpointer_config.py`, `sunday/memory_store_config.py`

### `checkpointer_config.py`  _(updated Parts 1-7)_
- **What it does:** Creates a `PostgresSaver` using a connection borrowed from
  `get_connection_pool()` (was: raw `psycopg.connect`). Sets `LANGGRAPH_STRICT_MSGPACK=true`.
  `DEFAULT_RECURSION_LIMIT = 50`.
- **Key exports:** `get_checkpointer() -> PostgresSaver`, `DEFAULT_RECURSION_LIMIT`
- **Depended on by:** `sunday/graph.py`, `sunday/nodes/await_approval.py`, `scripts/run_sunday.py`

### `sunday/memory_store_config.py`  _(updated Parts 1-7)_
- **What it does:** Creates a `PostgresStore` using a connection borrowed from
  `get_connection_pool()` (was: raw `psycopg.Connection.connect`).
- **Key exports:** `get_store() -> PostgresStore`
- **Depended on by:** `sunday/nodes/update_profile.py`, `sunday/nodes/await_approval.py`,
  `sunday/nodes/process_adhoc_input.py`, `telegram/polling.py`

### `sunday/approval_actions.py`  _(added Parts 1-7)_
- **What it does:** Plain functions (no LangGraph) for writing proposal outcomes.
  Called from `polling.py` at resume time — NOT from the Sunday graph.
  - `handle_approval(item, thread_id)` — creates or updates Trello card + sends Telegram confirmation
  - `handle_rejection(item, run_id)` — writes `rejection_event` to Postgres store
    + calls `_update_yaml_for_rejection` (incremental Haiku-based taste profile update)
  Moved from `write_outputs.py` (approval path) and `update_profile.py` (rejection path).
  Per-resolution timing instead of batched-at-Sunday-end — a tighter fit with the
  project's incremental-update philosophy.
- **Key exports:** `handle_approval(item, thread_id)`, `handle_rejection(item, run_id)`
- **Depended on by:** `telegram/polling.py`

### `telegram/polling.py`  _(added Parts 1-7)_
- **What it does:** Standalone poller for Telegram updates. `poll_once()` fetches new
  updates since last stored offset, routes each:
  - Reply to known proposal message → resumes child graph via `Command(resume=...)`,
    calls `approval_actions`, deletes `pending_resume_map` entry
  - Reply to unknown message → `feedback_router.handle_feedback` (stub)
  - Non-reply → queued as ad-hoc in `("weekly_intel", "adhoc_queue")`
  Persists update offset in `("weekly_intel", "polling_state")` store.
  Unrecognized decision text → sends a re-prompt to Telegram (doesn't consume the update).
- **Key exports:** `poll_once()`
- **Depended on by:** `scripts/run_polling.py` (not yet built — needs a scheduler)

### `telegram/feedback_router.py`  _(added Parts 1-7 — stub)_
- **What it does:** Stub for routing non-approval Telegram replies to the daily
  feedback path. Currently logs only — daily feedback handling not yet built.
- **Key exports:** `handle_feedback(message: dict)`
- **Depended on by:** `telegram/polling.py`

### `sunday/nodes/await_approval.py`  _(rewritten Parts 1-7)_
- **What it does:** Per-proposal child graph with its own dedicated checkpointer thread.
  `thread_id_for(proposal_id)` hashes the proposal URL. `send_proposal_message` captures
  the real Telegram `message_id` from the API response. `proposal_worker` stores
  `pending_resume_map[str(message_id)] = {thread_id, proposal_id, run_id}` in the Postgres
  store so `polling.py` can look up resume targets by reply message ID.
  `ProposalState` now includes `run_id` so it flows through to the store entry.
- **Key exports:** `ProposalState`, `thread_id_for`, `get_proposal_graph()`,
  `route_to_approvals(state) -> list[Send]`, `proposal_worker(state) -> dict`
- **Depended on by:** `sunday/graph.py`, `telegram/polling.py`

### `sunday/nodes/write_outputs.py`  _(gutted Parts 1-7)_
- **What it does:** Empty — logic moved to `sunday/approval_actions.py`. Node removed
  from `sunday/graph.py`.
- **Depended on by:** nothing (no longer a graph node)

### `sunday/nodes/update_profile.py`  _(simplified Parts 1-7)_
- **What it does:** Cost-summing and `data/cost_log.csv` append only. Rejection writes
  and YAML preference update moved to `approval_actions.handle_rejection`. CSV schema
  change: `rejections` column removed (not knowable at Sunday-run time; rejections now
  resolve async via polling).
- **Key exports:** `update_profile(state) -> dict`
- **Depended on by:** `sunday/graph.py`

### `sunday/nodes/process_adhoc_input.py`  _(added Parts 1-7)_
- **What it does:** Node function — reads all queued ad-hoc messages from
  `("weekly_intel", "adhoc_queue")`, converts each to a `RawItem` (source `"adhoc_telegram"`,
  url `"adhoc:<key>"`), deletes each key after consuming. Zero-cost node (no LLM calls).
  **Graph wiring in `discovery/graph.py` is Pooja's** — this file provides the function only.
- **Key exports:** `process_adhoc_input(state) -> dict`
- **Depended on by:** `discovery/graph.py` (once wired — pending Pooja)

### `discovery/parsers/scrape_blogs.py`  _(scaffolding only — Parts 1-7)_
- **What it does:** Parser stub for RSS/Atom blog feed scraping. `BLOG_FEEDS` list is
  empty; `fetch_blog_entries()` raises `NotImplementedError` until confirmed blog URLs
  are added. Matches `bookmarks_json.py` pattern: no langgraph imports, returns
  `ParseResult(rows, errors)`.
- **Key exports:** `fetch_blog_entries(feed_urls) -> ParseResult`, `BLOG_FEEDS`
- **Depended on by:** `discovery/nodes/scrape_blogs.py`

### `discovery/nodes/scrape_blogs.py`  _(scaffolding only — Parts 1-7)_
- **What it does:** Node wrapper for `fetch_blog_entries`. Matches `ingest_bookmarks`
  pattern: maps rows to `RawItem`s (source `"blog_scrape"`), stamps `NodeCost`.
- **Key exports:** `scrape_blogs(state) -> dict`
- **Depended on by:** `discovery/graph.py` (once wired — pending Pooja + source confirmation)

### `discovery/parsers/search_web.py`, `discovery/nodes/search_web.py`  _(RETIRED 2026-07-16)_
- Deleted entirely (batch2-dedup-taste-spec.md Section 6). Never left
  NotImplementedError-stub state; blog_sources.yaml's live-verified
  sources cover the same ground with better signal, lower cost. No
  remaining reference anywhere in the graph.

### `discovery/__init__.py`
- **What it does:** Empty package marker.

### `discovery/graph.py`  _(current shape: see "Checkpoint 5" section below)_
- **What it does:** Compiles the discovery subgraph with a real
  `route_sources()` conditional entry point. See the Checkpoint 5 section
  of this file for the current fan-out shape and cluster_dedupe_node's
  full pipeline (URL dedup → seen_items → semantic dedup → taste
  pre-filter).
- **Key exports:** `build_discovery_subgraph()`, `route_sources()`, `make_initial_state()`
- **Depended on by:** `daily/graph.py`, `sunday/graph.py`

### `discovery/parsers/bookmarks_json.py`
- **What it does:** Standalone JSON parser for Twillot bookmark exports.
- **Key exports:** `parse_bookmarks_json(path) -> ParseResult`
- **Depended on by:** `discovery/nodes/ingest_bookmarks.py`

### `discovery/nodes/ingest_bookmarks.py`
- **What it does:** LangGraph node — reads bookmarks JSON, returns `raw_items` + `NodeCost`.
- **Key exports:** `ingest_bookmarks(state) -> dict`
- **Depended on by:** `discovery/graph.py`

### `discovery/nodes/cluster_dedupe.py`
- **What it does:** URL-heuristic deduplication node.
- **Key exports:** `cluster_dedupe_node(state) -> dict`
- **Depended on by:** `discovery/graph.py`

### `discovery/nodes/score.py`
- **What it does:** Scores `ClusteredItem`s against taste profile using Claude Haiku.
- **Key exports:** `score_node(state) -> dict`
- **Depended on by:** `discovery/graph.py`

### `sunday/__init__.py`, `sunday/nodes/__init__.py`
- **What it does:** Empty package markers.

### `sunday/trello_client.py`
- **What it does:** Pure-stdlib HTTP wrapper for Trello REST API.
- **Key exports:** `fetch_board_cards`, `get_dump_list_id`, `create_trello_card`,
  `update_trello_card`
- **Depended on by:** `sunday/nodes/read_trello.py`, `sunday/approval_actions.py`

### `sunday/nodes/read_trello.py`
- **What it does:** LangGraph node — calls `fetch_board_cards()`, returns `trello_cards`.
- **Key exports:** `read_trello(state) -> dict`

### `sunday/nodes/correlate_trello.py`
- **What it does:** LangGraph node — matches scored items against Trello cards via Haiku.
- **Key exports:** `correlate_trello(state) -> dict`

### `sunday/nodes/classify_item.py`
- **What it does:** LangGraph node — classifies items as `plan_item` or `project_proposal`.
- **Key exports:** `classify_item(state) -> dict`

### `sunday/nodes/assemble_plan.py`
- **What it does:** `format_plan()` + `assemble_plan` node wrapper. Produces weekly plan text.
- **Key exports:** `format_plan(...)`, `assemble_plan(state) -> dict`

### `sunday/nodes/send_telegram_plan.py`
- **What it does:** Posts `state["plan_text"]` to Telegram.
- **Key exports:** `send_telegram_plan(state) -> dict`

### `sunday/graph.py`  _(updated Parts 1-7)_
- **What it does:** Builds and compiles the Sunday parent graph. `write_outputs` node
  removed — `proposal_worker` edges directly to `update_profile`.
- **Key exports:** `build_sunday_graph() -> CompiledStateGraph`
- **Depended on by:** `scripts/run_sunday.py`

### `telegram/__init__.py`
- **What it does:** Empty package marker.

### `telegram/bot_client.py`
- **What it does:** Stdlib HTTP wrapper for Telegram `sendMessage`. Returns full API
  response dict (`{"ok": true, "result": {"message_id": int, ...}}`).
- **Key exports:** `send_message(text, parse_mode="Markdown") -> dict`

### `daily/__init__.py`, `daily/nodes/__init__.py`
- **What it does:** Empty package markers.

### `daily/nodes/assemble_digest.py`
- **What it does:** Formats the daily Telegram digest.
- **Key exports:** `format_digest(...)`, `assemble_digest(state) -> dict`

### `daily/nodes/send_telegram_digest.py`
- **What it does:** Posts digest to Telegram.
- **Key exports:** `send_telegram_digest(state) -> dict`

### `daily/graph.py`
- **What it does:** Daily parent graph — discovery subgraph → assemble_digest → send_telegram_digest.
- **Key exports:** `build_daily_graph()`

### `scripts/run_daily.py`
- **What it does:** Daily run entry point.

### `scripts/run_sunday.py`
- **What it does:** Sunday run entry point. Prints pending proposals + resume instructions.

## Current state of the graph

**Daily parent graph:**

```mermaid
graph TD
    START(["__start__"]) --> discovery_subgraph
    discovery_subgraph --> assemble_digest
    assemble_digest --> send_telegram_digest
    send_telegram_digest --> END(["__end__"])
```

**Discovery subgraph** (shared) -- regenerated for real via
`graph.get_graph().draw_mermaid()` during Checkpoint 5's smoke-test fix
(the previous version of this diagram was stale, still showing the
long-removed `ingest_bookmarks` entry point). `route_sources` is a real
conditional entry point (`set_conditional_entry_point`), so both
`scrape_blogs` and `process_adhoc_input` show as reachable from
`__start__` in the compiled graph shape -- which one actually fires for a
given invocation is decided at runtime by `state["source_context"]`
("daily" -> `scrape_blogs` only; "sunday" -> both), not by two separate
compiled graphs:

```mermaid
graph TD
    __start__([__start__]):::first
    cluster_dedupe(cluster_dedupe)
    score(score)
    scrape_blogs(scrape_blogs)
    process_adhoc_input(process_adhoc_input)
    __end__([__end__]):::last
    __start__ -.-> process_adhoc_input
    __start__ -.-> scrape_blogs
    cluster_dedupe --> score
    process_adhoc_input --> cluster_dedupe
    scrape_blogs --> cluster_dedupe
    score --> __end__
```

**Sunday parent graph** (Parts 1-7 — `write_outputs` removed, `proposal_worker` → `update_profile` directly):

```mermaid
graph TD
    START(["__start__"]) --> discovery_subgraph
    discovery_subgraph --> read_trello
    read_trello --> correlate_trello
    correlate_trello --> classify_item
    classify_item -->|"Send(assemble_plan)"| assemble_plan
    classify_item -->|"Send(proposal_worker) × N"| proposal_worker
    assemble_plan --> send_telegram_plan
    send_telegram_plan --> update_profile
    proposal_worker -->|"child graph: send msg + interrupt()\neach on own thread"| proposal_worker
    proposal_worker --> update_profile
    update_profile --> END(["__end__"])
```

**Proposal resume flow** (separate process, driven by `telegram/polling.py`):

```mermaid
graph TD
    A["poll_once()"] --> B{"reply_to known\nproposal msg?"}
    B -->|yes| C["lookup pending_resume_map\nparse approve/reject"]
    C --> D["child.invoke(Command(resume=decision))"]
    D --> E{"decision?"}
    E -->|approve| F["handle_approval → Trello + Telegram"]
    E -->|reject| G["handle_rejection → store + YAML update"]
    F --> H["delete pending_resume_map entry"]
    G --> H
    B -->|reply to other| I["feedback_router (stub)"]
    B -->|no reply| J["queue adhoc_queue"]
```

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

## Checkpoint 5 additions (semantic dedup + taste pre-filter + taste-profile update mechanism + search_web retirement)

Scope: `batch2-dedup-taste-spec.md`. Built across two passes -- an initial
pass against Voyage AI that a mid-checkpoint investigation (the spec's own
Section 0) found had drifted from the locked spec (embedding provider,
Section 7's whole taste-update design), and further passes as the
embedding provider itself changed twice more: Voyage -> Gemini (abandoned
after a multi-hour live debugging session against a real, correctly
configured key -- unresolved 429/API_KEY_INVALID errors from the very
first request) -> local `sentence-transformers` (final decision, no
external account/key/billing tier of any kind).

### `discovery/embeddings.py`
- Local `sentence-transformers` (`all-MiniLM-L6-v2`) embedding wrapper,
  shared by dedup, taste pre-filter, and topic-vector recompute. No API
  key, no account, no billing tier -- the entire category of problem that
  cost hours with Gemini doesn't exist for a local model.
  `COST_PER_TOKEN_USD = 0.0` (local compute, not billed).
  `total_tokens` is real, summed from the model's own `attention_mask`
  (not padded batch width). 384-dimension vectors -- confirmed no
  downstream code hardcodes a dimension. `torch` pinned to the CPU-only
  build in `requirements.txt` (`torch==2.13.0+cpu` via
  `--extra-index-url`) -- GitHub Actions runners have no GPU, and a bare
  version pin risks pip resolving the default (much larger) CUDA build.

### `discovery/semantic_dedup.py`
- Cross-source/cross-run dedup: embeds title+text, cosine >=0.90 against a
  7-day rolling window (`recent_item_embeddings`). Cross-run match drops
  the new item unconditionally; within-run match keeps whichever item was
  **published earlier** (`fetched_at`) -- not fuller text, per the spec's
  revised tie-breaker. Logs every drop to `prefilter_drops`.

### `discovery/taste_vectors.py`
- Multi-vector per-tag pre-filter, max-similarity, threshold 0.30. Bootstrap
  embedding input is `score.py`'s `TASTE_PROFILE` prompt constant, mapped
  best-effort per tag -- `learning-resource` has no clearly corresponding
  bullet and is flagged (no vector computed), not guessed. Logs every drop
  to `prefilter_drops`.

### `discovery/nodes/cluster_dedupe.py`
- Ad-hoc items (`source == "adhoc_telegram"`) are split out before either
  filter runs and merged back into `clustered_items` untouched -- one
  source-based bypass point, not a duplicated check inside each filter.

### `sunday/approval_actions.py`
- `handle_feedback` now ONLY logs a `feedback_events` record and triggers
  the same-day nudge -- no Haiku call, no `taste_profile.yaml` write. This
  **replaced** a pre-existing (Part-7-era) uncapped full-profile rewrite
  that had been firing on every single reply, daily included -- found by
  this checkpoint's own investigation, not assumed.

### `sunday/same_day_nudge.py`  _(new)_
- Haiku classifies each reply's `feedback_text` into direction/magnitude,
  applies the mapped delta to every tag on the item, stacked and capped at
  +/-0.3 per tag per week in `same_day_adjustments`.

### `sunday/nodes/update_profile.py`
- Restored from cost-log-only to real work: reads `feedback_events` from
  the last 7 days, ONE consolidated Haiku rewrite (not one per reply),
  recomputes topic vectors on the fresh text, clears `same_day_adjustments`.
  File-layout decision (spec Section 7 item 2): here, not
  `approval_actions.py` -- that file's handlers are live per-reply
  functions outside any graph invocation with no natural "Sunday path"
  signal; this node already runs exactly once per Sunday graph invocation.

### `scripts/smoke_test_phase0.py`
- Cost-count assertion corrected: the old hardcoded `== 3` assumed one
  `NodeCost` per node, no longer true now that `semantic_dedup`/
  `taste_prefilter` append one record per item processed. Now asserts the
  three always-present per-invocation node names appear, not an exact
  total. Also fixed an unrelated Windows console encoding crash
  (`sys.stdout.reconfigure(encoding="utf-8")`) blocking the script from
  completing at all.

### Embedding provider -- final resolution
- Landed on local `sentence-transformers` (`all-MiniLM-L6-v2`) after
  Voyage AI and Gemini were both tried and abandoned this checkpoint
  (Gemini specifically: real key, real project, correct format, still an
  unresolved 429/API_KEY_INVALID from the very first request after
  hours of live debugging). `.github/workflows/daily.yml` and
  `sunday.yml` (the two workflows that run `discovery/scrape_blogs`) cache
  both the pip package cache and the HuggingFace model-weights cache
  directory. `requirements.txt` pins `torch==2.13.0+cpu` via
  `--extra-index-url https://download.pytorch.org/whl/cpu` -- verified
  against a genuinely fresh venv with real `pip` (not `uv`), confirming
  `torch.cuda.is_available() == False` and the resolved version string
  ends in `+cpu`, not the ~500MB-larger default CUDA build.
- All four previously GEMINI_API_KEY-blocked features
  (`semantic-dedup-embeddings`, `taste-similarity-prefilter`,
  `topic-vector-recompute`, `sunday-consolidated-taste-rewrite`) are now
  live-verified against this local model and the real Supabase Postgres
  store -- see `feature_list.json` for evidence. No external account, key,
  or secret involved anywhere in this provider.

## Checkpoint 5 loose ends, closed out (post-embedding-verification session)

Four items Pooja asked to be confirmed/closed before moving to anything new,
each verified for real rather than assumed:

- **`stage` field now actually progresses.** `DiscoverySubgraphState.stage`
  renamed to `Literal["start","sourced","clustered","scored"]` (no separate
  `"done"` -- `score_node`'s own `"scored"` is the terminal marker).
  `scrape_blogs`, `process_adhoc_input`, `cluster_dedupe_node`, and
  `score_node` each now set `state["stage"]` on return. Real technical
  subtlety found and handled: on Sunday runs `scrape_blogs` and
  `process_adhoc_input` both run in the same LangGraph superstep and both
  write `stage="sourced"` -- a plain key raises `InvalidUpdateError` on
  more than one write per step regardless of value equality (confirmed
  against `langgraph`'s own `LastValue.update()` source), the same class of
  bug `operator.add` previously fixed for `errors`. `stage` now uses a
  small last-write-wins reducer (`state.py`'s `_last_write_wins`, via
  `BinaryOperatorAggregate`) so concurrent identical writes are safe.
  Verified live against both daily and Sunday contexts (the Sunday case
  being the actual concurrent-write risk) with no error, and via a real run
  of `scripts/smoke_test_phase0.py` asserting `final_state["stage"] ==
  "scored"`.
- **`same-day-capped-nudge`, `item-feedback-logging`** — re-confirmed
  passing, not just assumed from `feature_list.json`'s prior state. Unit
  tests re-run fresh (7/7 and 3/3). Both live roundtrip scripts
  (`scripts/test_same_day_nudge_roundtrip.py`,
  `scripts/test_item_feedback_logging_roundtrip.py`) re-run against the
  real Supabase store + real Haiku calls this session, producing fresh
  real numbers, not reused evidence.
- **Ad-hoc bypass, re-verified against the real (unmocked) embedding
  path.** `tests/test_cluster_dedupe_adhoc_bypass.py` (2/2, mocks
  `dedupe_semantic`/`taste_prefilter` themselves to isolate the
  call-boundary split) re-run fresh. Additionally ran a real, non-mocked
  check: `cluster_dedupe_node` invoked with one real `adhoc_telegram` item
  and one real `blog_scrape` item, spying on the real `embed_texts` call
  site directly -- confirmed `embed_texts` was called exactly once, for
  the blog item's text only; the ad-hoc item's marker text was never
  embedded, and it survived to `clustered_items` untouched. Note: the
  real source value is `"adhoc_telegram"`, not the literal `"adhoc"` --
  a pre-existing, already-documented spec-vs-code naming difference from
  Checkpoint 3, re-confirmed still accurate.
- **Full `feature_list.json` status** — 27 features total across
  Checkpoints 1-5: 20 passing, 1 `in_progress` (`scrape-blogs-node`,
  Checkpoint 2, unrelated to this session), 6 `not_started`
  (`content-quality-review`, `resume-live-check`,
  `classification-decision-logging`, `approval-outcome-logging`,
  `score-eval-script`, `ingest-bookmarks-ci-removal`). All 5 Checkpoint 5
  features (plus the newly added `stage-field-progression`) are passing.

## scrape-blogs-node fix + Checkpoint 4 closeout (full delegation, CLAUDE.md Section 8)

### `discovery/parsers/scrape_blogs.py`, `discovery/nodes/scrape_blogs.py`
`scrape-blogs-node` (Checkpoint 2) had sat `in_progress` on a stale
blocker: its own evidence said `NodeCost` had no `error` field yet, but
that field shipped and passed back in Checkpoint 1 -- the node was simply
never updated to use it, still emitting one aggregate `NodeCost` per node
invocation instead of one per source. Added `fetch_one_source(entry)`
(dispatches ONE `blog_sources.yaml` entry, never raises) and
`fetch_blog_entries_per_source()` to the parser; the node now loops
`entries_for_context()` directly, timing each source's real fetch
individually and stamping one `NodeCost` per source (`error` set via
`NodeCost.error` on failure). `fetch_blog_entries()` kept as a thin
aggregate wrapper for existing callers. Verified live against the real
`blog_sources.yaml` (all 12 sources, sunday context): 12 `NodeCost`
records, 58 real `raw_items`, real per-source latencies 298ms-2063ms. A
deliberately-broken URL, checked directly against `fetch_one_source()`,
produced a real network error and zero rows with no effect on a real
working source run in the same process.

### `sunday/nodes/classify_item.py`
`classification-decision-logging` (Checkpoint 4, closeout-spec.md Section
4 point 1): every classify_item decision -- `plan_item` and
`project_proposal` alike, including the JSON-parse-failure fallback path
-- now logs `{item_id, decision, proposal_type, run_id}` to
`("weekly_intel","classification_log")`. Closes the blind spot where
`plan_item` decisions (the majority of all items, since they bypass the
approval gate by design) left zero trace. Store write wrapped in
try/except, never blocks the node's real return value. Verified live: a
real Haiku call classified one routine item as `plan_item` and one
structurally-new item as `project_proposal`; both landed as real, separate
`classification_log` entries.

### `sunday/approval_actions.py`, `telegram/polling.py`
`approval-outcome-logging` (Checkpoint 4, closeout-spec.md Section 4 point
2): the spec's original target file, `write_outputs.py`, no longer
exists (removed entirely in the Parts 1-7 rewrite) -- its logic lives in
`handle_approval`/`handle_rejection` now, so the fix landed there instead
of assuming the old spec's file layout still applied. Added
`_log_approval_outcome(item_id, outcome, run_id)`, writing
`{item_id, outcome, run_id}` to `("weekly_intel","approval_log")`, called
from both `handle_approval` (`outcome="approved"`) and `handle_rejection`
(`outcome="rejected"`, alongside its existing `feedback_events` write --
kept separate since that namespace doesn't itself distinguish a
proposal's approve/reject outcome). `handle_approval` gained a `run_id`
parameter it was previously missing entirely; its one real caller
(`telegram/polling.py`'s `_handle_approval_reply`) now passes it through.
Verified live (Trello/Telegram calls mocked -- creating a real card or
sending a real message isn't something a smoke test should do unprompted
-- store itself real): one approved and one rejected proposal produced
two real, distinct `approval_log` entries.

### `eval/score_eval.py`, `eval/labeled_set.json`
`score-eval-script` (Checkpoint 4, closeout-spec.md Section 3) -- **script
complete, deliberately NOT marked passing.** Per CLAUDE.md Section 8's
standing exception, label content is Pooja's judgment call, not Claude
Code's to invent, so `eval/labeled_set.json` ships empty (`[]`). Schema:
`{item_id, correct_decision: "keep"|"drop", correct_tags: [...], notes}`,
`item_id` must match a `url` in `data/scored_items.json` (the real
510-item bootstrap run). `score_eval.py` loads the labeled set, joins
against `data/scored_items.json`, re-runs score_node's own `_score_batch`
(reused directly, not reimplemented, so this eval can't silently drift
from production), reports `keep_accuracy` and `mean_tag_overlap`; raises
(not silently skips) on a labeled `item_id` missing from source data.
Verified: against the real (empty) `labeled_set.json` it correctly prints
a "nothing to do" message rather than a fake number. A throwaway,
NOT-committed 3-item demo (using each item's own prior score_node
decision as a mechanical self-consistency check, not a real correctness
claim) run through the real pipeline with a real Haiku call produced a
genuine, non-placeholder result: `keep_accuracy=100.00%`,
`mean_tag_overlap=100.00%`, real tokens input=818/output=190. Awaiting
Pooja to fill in real labels before this can mean anything as an actual
eval.

### `ingest-bookmarks-ci-removal` (Checkpoint 4) -- already satisfied
No code change needed. `ingest_bookmarks` was already absent from
`.github/workflows/daily.yml`/`sunday.yml`/`poll.yml` (confirmed by grep
across every `.yml`/`.yaml` in the repo) as a side effect of the Phase 5B
`route_sources` rebuild, which never registered it as a graph node in the
first place. `tests/test_ingest_bookmarks_gating.py` (4/4) re-confirms
this against the real compiled graphs.

## Store-namespace registry

Every real `weekly_intel` store namespace, per `batch2-dedup-taste-spec.md`
Section 11 (Section 0's investigation confirmed this list against the
actual code, not assumed from the spec's prior draft).

| Namespace | Shape | Written by | Read by |
|---|---|---|---|
| `polling_state` | `{value: int}` (update_offset) | `telegram/polling.py` | `telegram/polling.py` |
| `pending_resume_map` | `{thread_id, proposal_id, run_id}` | `sunday/nodes/await_approval.py` | `telegram/polling.py` |
| `adhoc_queue` | `{text, queued_at}` | `telegram/polling.py` | `sunday/nodes/process_adhoc_input.py` |
| `digest_item_map` | `{run_id, items: {number: {url,title,tags,reasoning}}}` | `daily/nodes/send_telegram_digest.py`, `sunday/nodes/send_telegram_plan.py` | `telegram/feedback_router.py` |
| `feedback_events` | `{item_id, feedback_text, replied_at, run_id, tags, title, content_summary, sentiment}` | `sunday/approval_actions.py` (`handle_feedback`) | `sunday/nodes/update_profile.py` (Sunday consolidated rewrite) |
| `seen_items` | `{seen: true}` | `discovery/seen_items.py` (`mark_seen`) | `discovery/seen_items.py` (`filter_unseen`) |
| `recent_item_embeddings` | `{item_id, url, embedding_vector, fetched_at, scored_at}` | `discovery/semantic_dedup.py` | `discovery/semantic_dedup.py` |
| `taste_topic_vectors` | `{tag, embedding_vector, updated_at}` | `discovery/taste_vectors.py` (`recompute_topic_vectors`) | `discovery/taste_vectors.py` (`taste_prefilter`) |
| `prefilter_drops` | `{item_id, filter_type: "dedup"\|"taste", similarity_score, compared_against_item_id, compared_against_tag, run_id}` | `discovery/semantic_dedup.py`, `discovery/taste_vectors.py` | audit log only -- no reader yet |
| `same_day_adjustments` | `{tag, cumulative_adjustment, item_ids_contributing, week_of}` | `sunday/same_day_nudge.py` | `sunday/nodes/update_profile.py` (cleared weekly; not yet consumed to influence live scoring/pre-filter comparisons -- spec Section 7 scopes this namespace's build to storage/computation/clearing only, no consumer described) |
| `rejection_events` | **KNOWN-DEAD** -- orphaned, no schema in production use | `scripts/test_update_profile_rejections.py` only (manual test script) | none in production |
| `classification_log` | `{item_id, decision: "plan_item"\|"project_proposal", proposal_type, run_id}` | `sunday/nodes/classify_item.py` (every item, not just proposals) | future eval work (`classify_item` eval, not yet built) |
| `approval_log` | `{item_id, outcome: "approved"\|"rejected", run_id}` | `sunday/approval_actions.py` (`handle_approval`, `handle_rejection`) | future eval work (`classify_item` eval, not yet built) |

## What does NOT exist yet

- **Taste profile in LangMem** — currently a plain YAML file, read/written by
  `sunday/nodes/update_profile.py` (Sunday consolidated rewrite) and
  `sunday/approval_actions.py` (log-only as of Checkpoint 5).
- Numeric score field on `ScoredItem`, tag feedback loop — deferred.
- **`resume-live-check`** (Checkpoint 3) — a real Telegram approve/reject
  round-trip against a real paused Sunday proposal, confirming `poll.yml`'s
  next run actually resumes the graph and writes the correct Trello outcome.
  Human-only per `feature_list.json` — Claude Code must not and did not mark
  this passing.
