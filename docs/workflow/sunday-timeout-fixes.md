[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Sunday timeout root-cause investigation (2026-07-17)

## sunday.yml timeout: real root cause, not just "give it more time"

Pooja reported a real `sunday.yml` run cancelled at almost exactly its
`timeout-minutes: 20` (20m17s), with real progress visible in the log
(51 real already-seen items correctly skipped) right up to the cutoff --
not a real error, a genuine timeout. That limit predates Checkpoint 5's
local embedding workload entirely (set for the interrupt/resume design).
Investigated two real causes before just raising the number:

1. **Per-item embed loop, confirmed real** -- `dedupe_semantic()` and
   `taste_prefilter()` both called `embed_text()` (singular) once per
   item in a loop, never `embed_texts()`, the batch API that already
   existed. Real local benchmark (150 items): looped 3.05s vs batched
   0.89s -- a real 3.4x speedup, but only a few seconds at typical item
   counts, nowhere near enough to explain an 18-20 minute stall by
   itself. Fixed anyway (see `discovery/embeddings.py`,
   `discovery/semantic_dedup.py`, `discovery/taste_vectors.py` below).
2. **Model-load network chatter, the far bigger real factor** -- isolated
   measurement: `SentenceTransformer(MODEL_NAME)`'s constructor took
   14.97s in this process, vs 0.10s for the actual batched embed call on
   60 items. The weights are already cached locally on this exact
   machine (run dozens of times tonight) -- the cost is a chain of
   ~10-15 real network round-trips to huggingface.co checking file
   revisions, which happens on every model load regardless of cache
   state, unless offline mode is set. This is a one-time-per-run cost
   (not per item), and on GitHub Actions' network this chain could run
   considerably slower than the 15s measured locally. NOT fixed this
   session -- `HF_HUB_OFFLINE=1` would make the very first real CI run
   of this dependency fail outright (no cache yet to read offline from)
   rather than slowly succeed. Flagged as a real follow-up once a run has
   actually completed and the HuggingFace cache action has something to
   restore.

### `discovery/embeddings.py`
`embed_texts()` now returns `(vectors, per_item_tokens: list[int])`
instead of one aggregate token int -- computed from
`attention_mask.sum(dim=1)` per row (already had the right tensor, was
just collapsing it to one scalar). `embed_text()` unpacks
`per_item_tokens[0]`, unchanged for existing single-item callers.

### `discovery/semantic_dedup.py`, `discovery/taste_vectors.py`
`dedupe_semantic()`/`taste_prefilter()` embed the whole item batch in ONE
`embed_texts()` call upfront, then run the same per-item comparison/
tie-break/audit-log logic against the precomputed vectors. One real
behavior change, flagged not hidden: a batch-wide embed failure now
degrades every item in that call at once (previously per-item) --
acceptable since a local-model failure realistically means the model
itself is broken, not that one request failed (the original per-item
graceful-degradation design was written for an API-based provider where
one request could fail while others succeeded). `recompute_topic_vectors`
(taste_vectors.py) intentionally NOT changed -- only ~5-6 calls total,
and its partial-failure test relies on per-tag independent failure.
tests/test_semantic_dedup.py, test_taste_vectors.py, test_prefilter_drops.py
updated to patch `embed_texts` instead of `embed_text` -- all passing,
117 total suite-wide, zero regressions.

### `.github/workflows/sunday.yml`
`timeout-minutes: 20 -> 45` -- a starting-point buffer for both real
costs above, not a precisely-derived number. Adjust with a real
completed run's actual duration once one exists.

**Follow-up question answered, same investigation**: is
`SentenceTransformer(MODEL_NAME)` instantiated once per `run_sunday.py`
execution, or once per call site (`dedupe_semantic`, `taste_prefilter`,
`recompute_topic_vectors`)? Verified empirically (not just by reading the
code): called all three real functions in sequence in one process and
tracked the actual model object identity. First call (`dedupe_semantic`)
paid the full ~10s model-load cost; the other two took under 1.1s each;
`id(embeddings_mod._model)` was identical across all three. Confirmed:
`discovery/embeddings.py`'s module-level `_get_model()` singleton already
works as intended -- the ~15s tax is genuinely paid once per real run,
not multiplied by call-site count. No fix needed here.

## `blog_sources.yaml` per-source `fetch_limit` (2026-07-17)

Schema gap confirmed before making any change (per instruction, not
assumed): `blog_sources.yaml` had no per-source limit override at all --
`fetch_rss_feed(feed_url, source_name, limit=30, ...)`'s `limit` param
was never passed by any caller, so every source silently used the same
hardcoded default of 30 regardless of bucket/cadence. Added a small
schema addition, not a redesign: an optional `fetch_limit` key per entry.

### `discovery/config/blog_sources.yaml`
All 12 entries now have `fetch_limit` set explicitly, per-bucket:
daily-bucket (TLDR AI, Latent Space, MarkTechPost, The New Stack (AI)) ->
15; sunday-only bucket (the other 8) -> 6. Deliberate volume reduction
from the prior blanket 30, not left unchanged.

### `discovery/parsers/scrape_blogs.py`
`fetch_one_source()` reads `entry.get("fetch_limit", _DEFAULT_FETCH_LIMIT)`
(default 30, unchanged for any future entry that omits it) and passes it
through as `limit=` to both `fetch_rss_feed()` (feed_url entries) and
`fetch_anthropic_engineering()` (scrape_url entries -- confirmed that
function already accepted a `limit` param, just never received one from
this call site before).

tests/test_scrape_blogs_fetch_limit.py (new): 6/6 passing -- confirms
`fetch_one_source()` passes an entry's own `fetch_limit` through
correctly for both feed_url and scrape_url entries, falls back to 30 when
absent, and asserts the real `blog_sources.yaml`'s 4 daily entries all
have `fetch_limit=15` and all 8 sunday entries have `fetch_limit=6`
(fails loudly if a future edit drops one). REAL LIVE VERIFICATION (ad-hoc
script, all 12 real sources, no mocks): every source's real returned row
count sat at or under its configured limit; LangChain Blog and Anthropic
Engineering Blog both hit exactly 6 (the cap), confirming it's actually
binding for at least two sources, not just coincidentally satisfied by
low natural volume.

## Durable run/node observability: `run_history` + `node_summary` (2026-07-17)

Motivated by a real question: is there any durable, queryable record of
"what happened in a given run," or does reconstructing that always
require piecing together LangSmith + raw GitHub Actions logs + a manual
Postgres query, the way that night's investigations had to? Answer:
no such record existed -- `data/cost_log.csv` was the closest thing, and
it's Sunday-only, lives on the CI runner's local (gitignored, never
persisted) disk, and is only ever written on a clean finish, so it has
zero record of exactly the crash case most worth capturing.

**Design confirmed before building, not assumed**: constructed a real
108-item synthetic stress test (matching genuine fetch-limit volume: 4
daily x 15 + 8 sunday-only x 6 = 108) through the real `cluster_dedupe_node`
inside an actual traced LangGraph invocation. The real trace's `costs`
output held all 125 real per-item records intact (108 semantic_dedup + 16
taste_prefilter + 1 base), including real `error` strings with genuine
similarity scores and comparison targets (e.g. `"dropped as duplicate of
<url> (cosine=0.910)"`). Payload size: 23.6 KB, far under any practical
limit. Confirmed: LangSmith already holds full per-item detail at real
scale -- so the new log is deliberately thin (aggregate counts + a
LangSmith pointer), not a second copy of per-item detail. The granular
WHY stays queryable from `prefilter_drops` for the rare deep-dive.

### `observability.py` (new, repo root)
`get_current_trace_url()` -- real LangSmith trace URL for whichever node
is currently executing, via `langsmith.run_helpers.get_current_run_tree()`;
returns `None` gracefully if tracing is off or there's no run context
(e.g. a bare unit test calling a node function directly). `record_node_summary(run_id, node_name, items_in, items_out, cost_usd,
error_summary)` writes one entry per `(run_id, node_name)` to
`("weekly_intel","node_summary")` -- `dropped` is derived
(`items_in - items_out`), not a second value callers compute themselves.
`record_run_history(path, run_id, started_at, finished_at, status,
total_cost_usd, items_in, items_out, duration_seconds, error_summary)`
writes one entry per entrypoint invocation to
`("weekly_intel","run_history")`. Every write wrapped in try/except,
logged and swallowed on failure -- same pattern as
`classification_log`/`approval_log`, a failed observability write must
never mask or block the real run/node outcome it's describing.

### `discovery/nodes/cluster_dedupe.py`, `discovery/nodes/scrape_blogs.py`
Both call `record_node_summary` once at the end. `cluster_dedupe`:
`items_in`/`items_out` = raw_items in / clustered_items out (same unit).
`scrape_blogs`: `items_in`/`items_out` = active sources attempted / raw
items fetched (different units, same generic shape -- documented per-node
in a comment rather than inventing a separate schema per node, matching
how `NodeCost` itself is one generic shape reused everywhere).

### `scripts/run_daily.py`, `scripts/run_sunday.py`, `scripts/run_poll.py`
Each wraps its real work in try/except/finally and calls
`record_run_history` once at the very end -- including on a crash
(`status="failed"`, `error_summary=str(exception)`), which is the exact
case `cost_log.csv` could never capture. The real exception still
re-raises after recording, so the GitHub Actions job keeps failing loudly
-- this only adds a durable record, never swallows the real failure
signal. `run_sunday.py` additionally distinguishes `status="paused"`
(proposals awaiting Telegram approval -- a normal, expected outcome) from
a genuine crash. `telegram/polling.py`'s `poll_once()` now returns
`{"updates_in": N}` instead of `None` (minor, backward-compatible return
contract change -- no existing caller checked the return value) so
`run_poll.py` has a real count to record.

**Real regression found and fixed during this build**: wiring
`record_node_summary` into `cluster_dedupe_node` made
`tests/test_cluster_dedupe_adhoc_bypass.py` silently start hitting the
real live Postgres store on every test run (that test never mocked the
new call) -- caught by noticing the test suite's wall time, not assumed
fine. Fixed by mocking `record_node_summary` in both of that file's tests,
same as every other real dependency they already mock.

tests/test_observability.py (new): 7/7 passing -- `record_node_summary`
writes the correct shape with a correctly-derived `dropped` count and a
real trace URL when tracing is active; both record functions swallow a
failing store write without raising; `get_current_trace_url` returns
`None` gracefully both when there's no run context and when the lookup
itself raises. REAL LIVE VERIFICATION: a real `cluster_dedupe_node`
invocation (inside a real traced single-node graph) produced a real
`node_summary` entry with a real, resolvable LangSmith URL, correct
`items_in`/`items_out`/`dropped`. A real `uv run scripts/run_poll.py`
invocation produced a real `run_history` entry: `{path: "poll", status:
"success", items_in: 0, items_out: 0, duration_seconds: 2.48}` -- left in
place (not cleaned up) since it's genuine production data, exactly what
this namespace is for, not test pollution.

**Extended (2026-07-17, follow-up turn)**: `score_node`, `correlate_trello`,
and `classify_item` -- the three most consequential decision-making nodes
(keep/drop, correlation match, plan/proposal) -- now call
`record_node_summary` too, same fail-safe wiring, same LangSmith pointer.
`score_node`: `items_in`/`items_out` = clustered_items in / kept count out.
`correlate_trello`/`classify_item`: neither node actually drops items
(every item survives, just annotated) -- `items_out` is redefined per-node
to reflect the real judgment call instead (matched-count for
`correlate_trello`, proposal-count for `classify_item`), so the derived
`dropped` field means "unmatched"/"plan_items" respectively, not a
literal loss of items. Both nodes' JSON-parse-failure fallback paths also
call `record_node_summary` (`items_out=0`, `error_summary` set), not just
their happy paths.

**Real regression found again, more rigorously this time**: plain
`pytest` (no `.env` loaded) can't prove this either way -- `DB_URI` is
absent from that environment, so `get_connection_pool()` raises
`KeyError` immediately, which `record_node_summary`'s try/except
swallows in ~0ms regardless of whether the test mocks it. Wall-clock
timing under plain `pytest` is not a reliable signal by itself. Checked
properly instead: ran `tests/test_classify_item.py` via
`uv run --env-file .env` (real `DB_URI` loaded) BEFORE fixing its mocks --
one test call took a real 0.60s, and a real junk entry
(`run-classify-1:classify_item`) landed in the live `node_summary`
namespace, confirmed by directly querying the store before/after. This
also retroactively confirms last turn's `cluster_dedupe_node` fix was
catching a real issue too, not just responding to noisy timing --a
second leftover junk entry (`run-1:cluster_dedupe`) from before that fix
was still sitting in the live store. Both cleaned up. Fixed by adding
`patch.object(classify_item_mod, "record_node_summary")` to all three of
that file's tests (same pattern as the `cluster_dedupe` fix), then
re-ran under `uv run --env-file .env` again -- 0 new entries, confirmed
clean. No pre-existing tests reference `score_node`/`correlate_trello`
directly at all (confirmed by grep, the only 3 hits were the literal
string "score_node" inside unrelated test fixture text) and no test
anywhere calls `.invoke()` on a real graph, so there was nothing else to
fix for those two.

tests/test_classify_item.py: 3/3 still passing after the mock fix.
REAL LIVE VERIFICATION (all three nodes, each invoked inside a real
traced single-node graph, real Haiku calls, real store): `score_node`
correctly kept 1/2 items (a real agentic-engineering item survived, a
fabricated off-topic hiking item didn't) -- `items_in=2, items_out=1`;
`correlate_trello` correctly found no match against an unrelated fake
Trello card -- `items_in=1, items_out=0`; `classify_item` correctly
classified a routine item as `plan_item`, not a proposal -- `items_in=1,
items_out=0`. All three produced real, resolvable LangSmith URLs. All
test entries deleted after assertion.

### Follow-up close-out (same day): junk cleanup, rigor upgrade, real test coverage

Three explicit asks, each with real evidence, not assumed:

**1. Junk entries deleted, confirmed with a fresh query.** Both
`run-classify-1:classify_item` and `run-1:cluster_dedupe` deleted from
the live `node_summary` namespace. Re-queried directly afterward (both
the two specific keys AND a full namespace scan): `ABSENT` for both
keys, `0 entries total` in the namespace.

**2. Test-suite fix, verified the only way that actually proves it.**
Plain `pytest` can't distinguish "properly mocked" from "not mocked" --
`DB_URI` is absent from that environment entirely, so any real store
attempt fails in ~0ms via `KeyError`, caught silently by
`record_node_summary`'s own try/except regardless of whether a test
mocks it. Checked with real credentials instead
(`uv run --env-file .env`): queried the live `node_summary` namespace
(0 entries), ran all four test files that touch a `record_node_summary`-
wired node (`test_score_node.py`, `test_correlate_trello.py`,
`test_classify_item.py`, `test_cluster_dedupe_adhoc_bypass.py`) -- 15/15
passed, all under 0.01s each -- then queried the namespace again: still
0 entries. Real before/after proof, not an inference from green tests or
timing alone.

**3. Real unit test coverage for `score_node` and `correlate_trello`
(previously zero, confirmed by grep).**

`tests/test_score_node.py` (5 new): keep=True and keep=False items both
survive into `scored_items` with the real decision intact (score_node
doesn't filter -- that's `correlate_trello`'s job); an invalid tag gets
filtered out of the item's tags and passed to `_log_dropped_tag`
(mocked, no real file write); `mark_seen` is called with every scored
URL regardless of keep/drop; `record_node_summary`'s `items_out`
reflects the real kept count, not the total; items beyond `BATCH_SIZE`
(50) still all get scored across multiple real (mocked) Haiku calls, not
silently dropped (60 items -> exactly 2 calls, sized 50 + 10).

`tests/test_correlate_trello.py` (5 new): an item matches a specific
card when the model says so; an item with no real connection stays
`matched_card_id: None`; only `keep=True` scored items are ever
correlated or even reach the prompt (a `keep=False` item's URL confirmed
absent from the actual prompt text sent to the model); the
JSON-parse-failure fallback path sets every item's `matched_card_id` to
`None` and calls `record_node_summary` with `items_out=0` and a real
`error_summary`; `record_node_summary`'s `items_out` reflects the real
matched count (1 of 3), not the total correlated count.

Both files mock every real external dependency the same way the rest of
this suite already does (Anthropic client, `record_node_summary`,
`mark_seen`, the dropped-tag file write) -- built with the mocking gap
in mind from the start, not discovered after the fact. Full suite: 140
passed, 1 skipped, zero regressions.

## Public-repo security pre-flight (2026-07-17) -- clean, nothing fixed

Before flipping repo visibility to public, searched the FULL git history
(`git log --all -p`, every commit, not just the current tree) for
committed secrets. Clean across every pattern checked: Google API key
shape (`AIza...`), Anthropic (`sk-ant-...`, generic `sk-...`), Telegram
bot token shape, Postgres connection strings with embedded credentials,
every real credential env-var name (`GEMINI_API_KEY`, `VOYAGE_API_KEY`,
`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TRELLO_API_KEY`, `TRELLO_TOKEN`, `DB_URI`) assigned to a real literal
value anywhere, LangSmith key shapes, Supabase/JWT-shaped tokens, and
literal `password=` assignments. The only `.env`-shaped file ever
committed, in any commit, is `.env.example` (`ae49909`) -- diffed
against the current version, both are the template with every value
left blank.

`.gitignore` confirmed correctly excludes `.env`, `data/` (zero tracked
files under it, confirmed via `git ls-files`), and `.claude/`. Cross-
checked that `feature_list.json`/`phase-5b-spec.md`/`session-handoff.md`/
`tests/closeout-spec.md` are genuinely untracked (present locally, never
committed) -- nothing got swept into tracked status when `docs/`/
`.github/` were deliberately un-ignored earlier. Full 124-file tracked
list is entirely source/tests/docs, spot-checked `discovery_graph.mmd`
and `eval/parser_fixtures/*.json` -- all clean, fixtures are explicitly
synthetic (`synth_tester`, `example.com`). The specifically-flagged
`39d7235` commit (companion-store digest/plan writes) reviewed in full
diff -- legitimate code + synthetic test fixtures, nothing from another
project.

**Not checked (no access)**: GitHub's secret scanning / push protection
repo settings -- no `gh` CLI, no API token in this environment, private
repo returns 404 unauthenticated. Needs a manual check via the GitHub
web UI (Settings -> Code security and analysis) before or right after
going public.

## Real 45-minute Sunday timeout: root-cause fixes (2026-07-17)

Four fixes, in priority order, each with real evidence -- not just
raising the timeout again without addressing causes.

### 1. Batched `filter_unseen`/`mark_seen` (`discovery/seen_items.py`)
Both used to issue one `store.get()`/`store.put()` per item -- confirmed
the most concrete, measured inefficiency: a real benchmark against the
live store showed 10 individual `store.get()` calls at 2636ms total vs.
one `store.batch([GetOp(...), ...])` call at 219ms -- a real 12.1x
speedup. Rewrote both to use `PostgresStore.batch()` with `GetOp`/`PutOp`
lists covering every item in one call. `tests/test_seen_items.py` (new,
4 tests, zero prior coverage) confirms correctness of the batched
rewrite via a `_FakeStore.batch()` that mirrors real Op handling, not
just that it runs. REAL LIVE VERIFICATION: 20 fresh items -> all unseen
-> `mark_seen` -> re-checked -> all seen, correct end to end against the
real store.

### 2. Substack 403s -- real browser User-Agent (`discovery/parsers/rss_common.py`)
Was `"weekly-intel-bot/1.0"`, an obviously bot-identifying string.
Changed to the same real browser UA `anthropic_blog.py` already uses
successfully. **Honest caveat, not overclaimed**: re-tested all four
blocked sources (JamWithAI, The Nuanced Perspective, AI with Aish, The
Neural Maze) from this machine -- every one succeeded with BOTH the old
bot UA and the new browser UA. The block did not reproduce here, so this
could not be verified as the actual fix the way a reproducible failure
would allow. Most likely explanation: Substack blocks/rate-limits by IP
range (GitHub Actions' shared runner IPs specifically), not by this
exact UA string. Applied as a legitimate defensive improvement regardless
-- the real test is the next live GitHub Actions run, not this.

### 3. `actions/cache` save skipped on timeout cancellation -- confirmed via GitHub's own community discussions (`.github/workflows/sunday.yml`, `daily.yml`)
Searched for GitHub's documented behavior rather than assuming: GitHub
Actions runs post-job steps (which is how `actions/cache@v4`'s combined
action saves its cache) ONLY if the job reaches a "completed" state.
Multiple real GitHub community discussions/issues confirm post steps are
skipped when a job is cancelled -- and a `timeout-minutes` cancellation
is an external termination, not a completion, the same category of
problem as the Python `finally`-block gap found in the run itself. This
closes the vicious cycle Pooja named: every timed-out attempt was
re-paying the full cold pip-install + HuggingFace-model-download cost
from scratch, since the cache never got a chance to save. Fixed by
switching from the combined `actions/cache@v4` action to explicit
`actions/cache/restore@v4` (early) + `actions/cache/save@v4` (right
after a new, cheap warm-up step that forces the HuggingFace model
download -- `python -c "from discovery.embeddings import _get_model;
_get_model()"` -- confirmed working locally), placed BEFORE the long,
timeout-risking main pipeline step. Applied to both `sunday.yml` and
`daily.yml` (same cache pattern, same theoretical risk, for consistency).

### 4. Crash-durability gap for real timeouts, not just exceptions (`observability.py`)
Question 0 of this investigation confirmed the gap was real: after the
actual 45-minute timeout, `run_history` had ZERO entries and
`node_summary` had exactly ONE (`scrape_blogs`, the only node that
finished before the kill) -- the `finally` block in `run_sunday.py`
never got to run, and no LangSmith trace existed for the run either
(tracing wasn't connected during that real execution). A `finally` block
alone isn't enough against an external kill -- confirmed by the same
GitHub post-step research above (a killed process may never get to run
ANY of its own remaining code, `finally` included). Fixed with
`record_run_started(path, run_id, started_at)`, called at the very
start of each entrypoint (`run_daily.py`/`run_sunday.py`/`run_poll.py`),
before any real work -- writes a real `status="in_progress"` record to
the SAME `run_id` key that `record_run_history` (still called from the
existing `finally` block) later overwrites if the process survives that
long. A run stuck at `status="in_progress"` with no later overwrite is
now itself a legible signal that the run never finished, not silence.
Also added `duration_seconds` to `node_summary` (missing entirely
before -- question 4 of the same investigation couldn't be answered
partly because this field never existed), wired into all five
`record_node_summary` call sites (`cluster_dedupe`, `scrape_blogs`,
`score_node`, `correlate_trello`, `classify_item`), each passing their
own already-measured elapsed time.

REAL LIVE VERIFICATION, the actual crash case: called `record_run_started`
in one process, deliberately never called `record_run_history` --
simulating exactly what a hard external kill does. A separate process
then queried the store directly: a real, complete
`{status: "in_progress", finished_at: None, ...}` record was there,
confirmed -- proof the fix survives the exact failure mode it targets,
not just the happy path. Separately ran the real `scripts/run_poll.py`
end to end: confirmed the in-progress marker gets correctly overwritten
by the final `status: "success"` record when the process does survive.
`duration_seconds` confirmed real and non-zero (9.969s) in a real
`node_summary` entry from an actual `cluster_dedupe_node` invocation.

`tests/test_observability.py` gained 4 new tests (11 total): `record_run_started`
writes the correct in-progress shape; confirms the same-key overwrite
semantics with `record_run_history`; confirms a failed write doesn't
raise; confirms `record_node_summary`'s `duration_seconds` defaults to
0.0 for any caller not yet passing it. Full suite: 148 passed, 1
skipped, zero regressions. Re-verified with real credentials
(`uv run --env-file .env`) that none of the mocked node-touching test
files (`test_score_node.py`, `test_correlate_trello.py`,
`test_classify_item.py`, `test_cluster_dedupe_adhoc_bypass.py`,
`test_seen_items.py`, `test_observability.py` -- 30 tests) hit the live
store: `node_summary` namespace unchanged (still exactly the one real
production entry from the actual timed-out run) before and after.

## Finishing the batching job + cross-process model reuse check (2026-07-17, same day follow-up)

### 1. `dedupe_semantic`'s survivor persistence + both filters' drop-audit logging -- now batched
Confirmed still per-item via a fresh grep sweep before touching anything
(per instruction): `discovery/semantic_dedup.py`'s final survivor-write
loop and both `_log_drop()` call sites (semantic + taste), plus
`discovery/taste_vectors.py`'s `_log_drop()`. All four rewritten to
collect records across the per-item loop into a plain list, then issue
ONE `store.batch([PutOp(...), ...])` call at the end -- same fix and
API as `filter_unseen`/`mark_seen`. `_log_drop()` functions renamed to
`_drop_record()` (build a dict, don't write it) since writing now
happens once, batched, not inline per call.

**Two per-item `store` calls confirmed still remaining, left alone
deliberately, not missed**: `semantic_dedup.py`'s `_load_window()` still
deletes stale (>7-day) window entries one at a time -- bounded by how
many entries just crossed the staleness threshold since the last run
(typically 0-few), not by this run's item count, so it doesn't scale
the same way. `taste_vectors.py`'s `recompute_topic_vectors()` still
writes topic vectors one per tag -- bounded to ~6 tags total regardless
of item volume, and its partial-failure test relies on per-tag
independent failure (already decided against batching this in the
first round, reconfirmed still correct here).

Existing `_FakeStore` test doubles (`test_semantic_dedup.py`,
`test_prefilter_drops.py`, `test_taste_vectors.py`) gained a `.batch()`
method mirroring real `PostgresStore.batch()` (dispatches `PutOp`/`GetOp`
through the existing `put()`/lookup so `self.puts` still records every
write). Full suite: 148 passed, 1 skipped, zero regressions.

### 2. Cross-process model reuse -- confirmed NOT shared, a separate real cost
Tested directly with two genuinely separate OS processes (matching how
GitHub Actions steps actually work -- not just two function calls in one
script, which wouldn't answer this): a "warm-up" subprocess loads the
model (17.31s), then a SECOND, separate subprocess times its own model
load with the disk cache now fully warm from the first -- **17.56s,
essentially the same cost, not eliminated**. Confirmed: `discovery/
embeddings.py`'s `_model` global is process-local; the real "Run Sunday
pipeline" step's own `python scripts/run_sunday.py` process cannot reuse
the warm-up step's in-memory model instance no matter how warm the disk
cache is -- it independently re-triggers the same huggingface.co
revision-check network chatter every real run. The warm-up step's real
value is narrower than it might look: it guarantees the cache gets
populated and saved before the risky main step (the actual crash-
durability/vicious-cycle fix), but it does NOT remove this network-
chatter tax from the main step itself. Flagged, not fixed here --
`HF_HUB_OFFLINE=1` on the main step specifically (now safe to set,
since the warm-up step guarantees the cache is populated in the same
job) would close this remaining gap, but wasn't asked for this round.

### Cheap local verification before touching GitHub Actions again
Timed a real `cluster_dedupe_node` invocation with 13 items (matching
the real survivor count from tonight's actual production runs) against
the live store: **11.80s total wall time**, `node_summary`'s own
`duration_seconds` confirms 10.621s of that -- consistent with the
one-time model-load tax being the dominant real cost, everything else
(batched embedding, batched store writes) fast. This is the real "fast
locally" confirmation the next Sunday Pipeline trigger was gated on.

**Found while re-checking the store for this report, not something I
triggered**: a real `run_history` entry (`873db6a8...`, `path="sunday"`)
shows `status="in_progress"`, `finished_at=None`, started
`2026-07-17T08:21:05Z` -- either a real Sunday Pipeline run is active
right now, or it already ended and this in-progress marker is the only
trace left (which would itself be today's crash-durability fix working
exactly as designed). Also found a second real `node_summary` entry
from a genuinely new production run, already carrying the pushed UA fix
(confirmed by the presence of `duration_seconds`) -- and it still hit
the same 4 Substack 403s, unprompted additional confirmation that the
UA change alone doesn't resolve them.

## HF_HUB_OFFLINE on the main step + Substack 403s confirmed graceful (2026-07-17, same day follow-up)

### 1. `HF_HUB_OFFLINE=1` on the main pipeline step -- closes the cross-process gap found above
The prior section flagged that the warm-up step's cache does NOT get
reused in-memory by the main step's separate process, so the main step
was still paying the huggingface.co revision-check network tax on every
real run. That blocker (needing real network access on a genuinely cold
run) is now gone -- the warm-up step + explicit cache restore/save
already guarantee the model is present on disk before the main step
starts, so the main step can safely skip that network check entirely.

Verified locally with two genuinely separate subprocesses (matching how
Actions steps actually work, not two calls in one script): first
measurement (full subprocess wall time, including one-time Python
import overhead) showed a real but modest 13.70s -> 8.87s. Isolated
further to separate `import torch` (1.99s) + `import sentence_transformers`
(6.13s) from actual model construction time, then re-measured
construction time alone: **6.58s -> 0.92s, an 86% reduction**, exit
code 0 both times (no silent fallback/failure). Also tried adding
`TRANSFORMERS_OFFLINE=1` alongside -- no additional benefit (0.95s vs
0.92s), so `HF_HUB_OFFLINE=1` alone is sufficient.

Added `env: HF_HUB_OFFLINE: "1"` to the "Run Sunday pipeline" step in
`sunday.yml` and the "Run daily digest" step in `daily.yml` -- both
main steps only, deliberately not the warm-up step (which still needs
real network access to populate a genuinely cold cache).

### 2. Substack 403s -- confirmed graceful, not attempted to fix further
Per prior instruction, stopped attempting to fix these directly (already
confirmed unreproducible from this machine, most likely GitHub-runner-IP
blocking). Confirmed graceful failure with real, current evidence rather
than re-deriving it from code alone: queried the live `node_summary`
store directly for the two most recent `scrape_blogs` entries --

```
items_in (sources): 12   items_out (raw items): 26
error_summary: JamWithAI: HTTP Error 403: Forbidden; The Nuanced
  Perspective: HTTP Error 403: Forbidden; AI with Aish: HTTP Error 403:
  Forbidden; The Neural Maze: HTTP Error 403: Forbidden
```
in both of the last two real runs. This confirms all three required
properties at once: the error is captured (per-source, by name, in
`error_summary`), the run continues (the node completes and returns a
`node_summary` row rather than crashing), and other sources are
unaffected (`items_out=26` -- the other 8 sources' items came through
fine both times). This was already true at the code level
(`fetch_rss_feed`'s per-source try/except in `rss_common.py`,
`scrape_blogs`'s per-source loop in `discovery/nodes/scrape_blogs.py`
isolating one source's exception from the others) -- this just
confirms it held in two real production runs, not only in tests.

Documented directly in `discovery/config/blog_sources.yaml` (a dated
comment block naming all 4 affected sources, the real evidence, and the
IP-blocking hypothesis) so this isn't mistaken for a new bug by a future
session encountering the same 403s again.

### Full verification before pushing
`pytest tests/` (the real test suite -- `scripts/test_*.py` files are
manual live-run scripts requiring a real checkpointer/interrupt setup,
not automated tests, and were already failing collection for unrelated
reasons before this session touched anything): **148 passed, 1 skipped,
zero regressions**. Both YAML files (`blog_sources.yaml`, `daily.yml`,
`sunday.yml`) parse cleanly via `yaml.safe_load`.

Next: Sunday Pipeline to be triggered once more (no `gh` CLI/API access
in this environment to do it directly) to get one real measured
duration with every fix from today in place.

## Real hang diagnosis: fine-grained checkpoints + missing psycopg timeouts (2026-07-17, same day follow-up)

Two consecutive Sunday runs stopped being merely slow -- they hung. Both
died silently right after cluster_dedupe's "skipped N already-seen" log
line: zero traceback, zero exception, nothing until GitHub's own
45-minute timeout killed the job externally. That signature (silence, not
a raised error) points at a call that never returns, not one that's
merely slow -- a different failure class than the earlier duration-based
investigation, and not fixable by more batching.

### 1. Fine-grained BEFORE/AFTER checkpoints on every remaining network call
Added a paired `logger.info()` immediately before and immediately after
every store.get/put/search/batch/delete call, every embed_texts()/
embed_text() call, and the SentenceTransformer construction itself, across
`cluster_dedupe_node -> dedupe_semantic -> taste_prefilter`:
`connection_pool.py` (ConnectionPool construction), `sunday/
memory_store_config.py` (store.setup()), `discovery/seen_items.py`
(filter_unseen/mark_seen's store.batch()), `discovery/semantic_dedup.py`
(_load_window's search/delete, embed_texts(), both batch writes),
`discovery/taste_vectors.py` (_load_topic_vectors's search, embed_texts(),
drop-records batch), `discovery/embeddings.py` (SentenceTransformer
construction, model.encode(), model.preprocess()), `observability.py`
(all three store.put() call sites). One log per actual blocking call, not
one per node -- the next real run's log will show exactly which specific
line it stalls on. `logging_config.py`'s `setup_logging()` is already
called at the top of every entrypoint script, so these reach GitHub
Actions' captured stdout, not silently dropped.

REAL LOCAL VERIFICATION (live store, real credentials): ran
`cluster_dedupe_node` end to end against 2 real items -- every single
BEFORE paired with an AFTER, nothing hung, full round trip ~10s
(dominated by a cold model load). Full test suite: 148 passed, 1 skipped,
zero regressions from adding the logging.

### 2. Missing connect_timeout / statement_timeout -- the real root cause candidate
Checked huggingface_hub first: `constants.py` bakes in
`DEFAULT_REQUEST_TIMEOUT = 10` / `DEFAULT_ETAG_TIMEOUT = 10` regardless of
HF_HUB_OFFLINE -- worst case there is a bounded 10s stall, not an infinite
hang. Ruled out as the likely cause on this basis.

psycopg is a different story. `connection_pool.py` set no
`connect_timeout` anywhere. Read psycopg_pool 3.3.1's actual source
(`pool.py`): the pool's own `timeout=30.0` only bounds how long a *caller*
waits in queue for an already-open connection -- it does NOT bound the
underlying libpq `connect()` call itself. `_add_connection()` (the method
that opens every real connection the pool ever uses, both initial fill
and any reconnect after a lost connection) calls `self._connect()` with
**no timeout argument at all** (confirmed at the exact call site), so
`_connect()`'s own `if timeout: kwargs["connect_timeout"] = ...` override
never fires for these connections. libpq's documented default for an
unset `connect_timeout` is "wait indefinitely." No `statement_timeout` was
configured anywhere either (no conninfo option, no post-connect SET), so
a query that gets past connection but stalls server-side (e.g. lock
contention) had no bound of its own either. This is a real, code-verified
match for "hangs forever, no exception" -- not a hypothesis needing a
separate validation round, since the source itself was read directly.

**Fix**: `connection_pool.py` now sets `connect_timeout=10` (same order of
magnitude as huggingface_hub's own default) directly in the `kwargs` dict
passed to `ConnectionPool()` -- this makes it part of the *resolved*
kwargs dict every real `connect()` call uses regardless of which code path
opens the connection, sidestepping `_connect()`'s optional
timeout-override parameter entirely rather than depending on it. A new
`configure` callback (`_configure_connection`) runs `SET
statement_timeout = '30s'` on every newly-opened connection (psycopg_pool
calls `configure` from `_add_connection()` each time a connection is
created, not just once at pool construction) -- generous for real work,
well short of the 45-minute job ceiling.

REAL LOCAL VERIFICATION (live store, real credentials) that both values
actually take effect rather than being silently ignored:
- `SHOW statement_timeout` on a real pooled connection returned exactly
  `'30s'`.
- `pool._resolve_kwargs()` (the actual dict passed to every real
  `connect()` call) contains `connect_timeout: 10`.
- A direct `psycopg.connect(...)` with the same kwarg shows
  `connect_timeout: '10'` as a real libpq-level parameter in
  `conn.info.get_parameters()`, not just a Python-side dict entry.

Full test suite re-run after this change: 148 passed, 1 skipped, zero
regressions.

### Next real run
This environment has no `gh` CLI/API access to trigger GitHub Actions
directly. Pooja triggers Sunday Pipeline manually after this push. Given
the new checkpoint logging, the next run tells us something real either
way: a clean success, or -- if the timeouts are what was needed -- a
fast, specific timeout exception naming the exact stuck call, replacing
another silent 45-minute kill with an actual diagnosis.

