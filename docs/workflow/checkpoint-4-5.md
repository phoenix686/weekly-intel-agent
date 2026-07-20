[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Checkpoint 4 closeout + Checkpoint 5 additions

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

### `discovery/embeddings.py`  _(provider swapped 2026-07-19 -- see "Embedding provider" changelog entries below)_
- **NVIDIA NIM** (`nvidia/nemotron-3-embed-1b`, hosted, via
  `build.nvidia.com`'s OpenAI-compatible `/v1/embeddings` endpoint), shared
  by dedup, taste pre-filter, and topic-vector recompute -- same interface
  as the prior local provider (`embed_text`, `embed_texts`,
  `cosine_similarity`, `COST_PER_TOKEN_USD`), confirmed isolated swap (no
  changes needed in `semantic_dedup.py`/`taste_vectors.py`).
  `EMBEDDING_DIM = 2048` -- different from the prior local model's 384.
  `INPUT_TYPE = "passage"`, used for every call: NVIDIA's model is
  measurably asymmetric (live-tested, `query` vs `passage` on identical
  text: cosine ~0.85, not ~1.0), so a single consistent `input_type` was
  chosen to preserve the old model's "everything in one comparable space"
  symmetric behavior -- NVIDIA's own documented guidance on the
  query/passage split for this specific model could not be fetched live
  (two attempts timed out); flagged as worth revisiting if match quality
  suggests otherwise, `taste_vectors.py`'s topic-vs-item comparison being
  the more likely candidate (topic description ~ query, item ~ passage is
  the closer fit to typical retrieval framing).
  `COST_PER_TOKEN_USD` is **UNVERIFIED** -- both live pricing-page fetches
  timed out and the real API response carries no cost/credit header at
  all; left at `0.0` as a placeholder, not a confirmed free rate the way
  local compute was. Per-item token counts are real for a single-text
  call (the API's aggregate usage.total_tokens IS that one text's count)
  and approximated by character-length share of the batch for a
  multi-text call, since the API only returns one aggregate count per
  batch request, not real per-item counts. Auth via `NVIDIA_API_KEY`
  (`Bearer` header), same `_api_key()`-raises-`KeyError`-if-missing
  pattern as `trello_client.py`'s `_auth_params()`. `cosine_similarity()`
  gained a dimension-mismatch guard (returns `0.0`, logs a warning)
  instead of letting `zip()` silently truncate to the shorter vector's
  length -- exists specifically because of this swap's dimension change,
  but is provider-agnostic protection going forward.

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
  bullet and is flagged (no vector computed), not guessed. `course` (added
  sub-phase 1 of the Sunday-plan-LLM-prioritization checkpoint) is
  deliberately unmapped the same way -- it's a format tag, not a topic, so
  it has no corresponding topic bullet either. `TOPIC_TAGS` now has 7
  entries, 2 unmapped. Logs every drop to `prefilter_drops`.

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

### Embedding provider -- final resolution AT THE TIME (2026-07-17) -- SUPERSEDED 2026-07-19, see "Embedding provider: NVIDIA NIM swap" near the end of this file
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

## CRITICAL: local main is 19 commits ahead of origin/main, unpushed

**Found this session, triggered by a real production failure**: Pooja
reported `scripts/run_sunday.py` hitting the exact `data/tweets.json`
`FileNotFoundError` `ingest-bookmarks-gating` was supposed to have fixed,
on a real scheduled Sunday run. Investigation traced this to a deployment
gap, not a code regression:

- `git rev-list --count origin/main..HEAD` = 19; `..origin/main` = 0 --
  local `main` is strictly ahead, origin has nothing local is missing.
- `origin/main`'s HEAD (`0a756ae`) is the commit **immediately before**
  `11fd528`, the original ingest_bookmarks fix commit -- so `origin/main`
  predates that fix, the entire `route_sources` rebuild (`81e96c8`), and
  every commit since, including all of Checkpoint 5 and Checkpoint 4
  closeout.
- `git show origin/main:discovery/graph.py` confirms it directly: still
  the old `DAILY_SOURCE_NODES`/`SUNDAY_ONLY_SOURCE_NODES` additive-dict
  design, `ingest_bookmarks` unconditionally in `DAILY_SOURCE_NODES`,
  merged into Sunday too -- the exact original bug, still live.
- GitHub Actions' `daily.yml`/`sunday.yml`/`poll.yml` all check out
  `origin/main` (`actions/checkout@v4`'s default). Every real scheduled
  run this entire session -- and the session before it -- has executed
  this stale, pre-fix code, regardless of how much local passing evidence
  was gathered against local `main` in the meantime.

**A compounding, independent gap** also found in this investigation:
`ingest-bookmarks-gating`'s prior evidence tested the Sunday path only by
calling `build_discovery_subgraph()` directly in Python -- never by
actually running `scripts/run_sunday.py`, the real entrypoint script
GitHub Actions invokes. So even setting the origin/main gap aside, the
claim of having verified "real entrypoints" for Sunday specifically was
inaccurate.

**Local code re-confirmed correct, real evidence this time**: ran
`uv run --env-file .env python scripts/run_sunday.py` for real (the
actual script, not a direct graph call) against the live Supabase
Postgres store, real Trello board, real Anthropic calls -- completed
cleanly, `Run cf85bab6 complete`, zero errors, $0.0044 total cost. The
log shows `cluster_dedupe` skipping 57 already-seen items, every one a
real `blog_scrape` URL (LangChain, Latent Space, Anthropic Engineering,
DecodingAI, TheNeuralMaze, MarkTechPost, TheNewStack) -- confirms
`scrape_blogs` fired correctly. Zero references to `ingest_bookmarks` or
`data/tweets.json` anywhere in the output.

**RESOLVED**: local `main` pushed to `origin/main` (commit `9003ce9`,
clean fast-forward, confirmed no divergence) after Pooja explicitly
authorized it. `origin/main` now matches local exactly.

**Systemic follow-up finding, same session**: went through every
currently-`passing` feature's evidence checking for this same shape (real
entrypoint script vs. direct function/graph call) -- found it's the
dominant pattern, not isolated to these two. Only `poll-once-three-way-
routing` actually ran its real entrypoint (`scripts/run_poll.py`) as
evidence; everything else touching the discovery subgraph or Sunday-
specific logic used direct calls in ad-hoc test/roundtrip scripts, or a
dedicated smoke-test script (`smoke_test_phase0.py`) distinct from the
real production entrypoints. `process-adhoc-input-node`'s evidence text
was also found stale -- it describes calling
`build_discovery_subgraph(include_sunday_only=True)`, a parameter that no
longer exists in the current signature (confirmed via grep: zero matches
in the current test file). Not re-verified across the board this
session -- flagged as scope for a future pass, not silently left implied
as already fine.

