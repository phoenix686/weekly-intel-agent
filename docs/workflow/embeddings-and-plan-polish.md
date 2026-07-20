[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Embedding provider swap + plan polish (2026-07-19)

## Embedding provider: NVIDIA NIM swap (2026-07-19)

A status check ("was the nemotron-3-embed-1b swap ever actually
implemented?") was investigated directly against the codebase, not
assumed from memory -- confirmed genuinely never implemented (zero
`nemotron`/`nvidia` references anywhere in `discovery/embeddings.py` or
`requirements.txt`; the file's own docstring documented the real full
provider history -- Voyage -> Gemini -> local sentence-transformers --
with no mention of NVIDIA at all). `NVIDIA_API_KEY` was confirmed present
in `.env`, but the first live test against it returned `401
Unauthorized` on every embeddings call while `/v1/models` succeeded fine
with the same key -- the key's prefix didn't match NVIDIA's documented
`nvapi-` format, strongly suggesting an invalid/wrong-format key, not a
code bug (confirmed by testing 4 payload variations and 2 different
models, all consistently 401). Pooja updated the key; the new one
(`nvapi-...` prefix) worked immediately.

**Real blast-radius finding, not assumed:** the new model's real output
dimension is 2048, different from the prior local model's 384.
`cosine_similarity()`'s `zip(a, b)` silently truncates to the shorter
vector's length on a mismatch -- comparing an old 384-dim stored vector
against a new 2048-dim live one would have computed a meaningless
partial-dimension "similarity" with no error at all, corrupting both
`semantic_dedup.py`'s cross-run window comparisons and
`taste_vectors.py`'s topic-vs-item comparisons silently. Fixed two ways:
a defensive dimension-mismatch guard added to `cosine_similarity()`
itself (returns `0.0`, logs a warning -- provider-agnostic protection
going forward, not just a one-time migration fix), AND the two affected
store namespaces (`recent_item_embeddings`, `taste_topic_vectors`) were
cleared as part of the swap, per Pooja's explicit confirmation -- both
are fully derivable/recomputable state, not source-of-truth data.
`taste_topic_vectors` was then immediately re-populated with real
2048-dim vectors computed from the actual current `data/taste_profile.yaml`
content (not left empty) -- `recompute_topic_vectors()`'s real signature
made this a one-line live re-run, and leaving real, correct vectors in
place is strictly better than deferring that benefit to the next Sunday
run. `recent_item_embeddings` was left empty (its own `_load_window()`
already degrades correctly against an empty window) -- it rebuilds
itself naturally over the next `_WINDOW_DAYS` (7) of real runs.

**A second real finding, also blast-radius, not assumed:** both
`daily.yml` and `sunday.yml`'s "Warm up local embedding model" CI step
literally called `python -c "from discovery.embeddings import _get_model;
_get_model()"` -- a function that no longer exists after the swap. Left
as-is, this would have broken the very next scheduled run (a real
`ImportError`/`AttributeError` on a step with no `if: always()`, failing
the whole job before the real pipeline step ever ran). Removed that step
plus the now-pointless HuggingFace model-weights cache restore/save
steps and the `HF_HUB_OFFLINE=1` env var (no HuggingFace calls happen at
all anymore) from both workflows. Added `NVIDIA_API_KEY:
${{ secrets.NVIDIA_API_KEY }}` to both workflows' job-level `env:` block
-- **this requires Pooja to add `NVIDIA_API_KEY` as a real GitHub Secret
on the repo before the next scheduled run**, per `CLAUDE.md` Section 8's
standing exception (Claude Code flags the need, never adds secrets
itself). Not yet confirmed added. `sunday.yml`'s 45-minute timeout
comment (previously justified by SentenceTransformer's real network
round-trip cost, now gone) was corrected to reflect the real current
state -- left at 45 minutes anyway since no real post-swap Sunday run
exists yet to measure the new real duration against.

**Also found and fixed in the same pass:** `.env.example` was missing
`NVIDIA_API_KEY` entirely (same category of gap as `AGENTMAIL_API_KEY`,
found and fixed during the README overhaul) -- added, with a comment
about the real key prefix format. `requirements.txt`'s `torch`/
`sentence-transformers` pins removed (confirmed zero other references to
either anywhere in the codebase before removing).

**Explicitly unresolved, flagged rather than guessed at:**
`COST_PER_TOKEN_USD` is **UNVERIFIED** -- two live attempts to fetch
NVIDIA's real pricing documentation both timed out, and the real API
response carries no cost/credit/billing header of any kind (checked
every response header directly). Left at `0.0` as a placeholder, not a
confirmed free rate the way local compute's `$0.0` was categorically
true -- `NodeCost.cost_usd` figures downstream of this module will
under-report real spend if `build.nvidia.com`'s embeddings endpoint turns
out to be billed. The `input_type="passage"` choice (see
`discovery/embeddings.py`'s file entry above) was also made without
being able to confirm NVIDIA's own documented query/passage convention
for this specific model, for the same reason (fetch timeouts) -- the
reasoning for the choice made instead is fully documented in the module
docstring and this entry.

**Files changed:** `discovery/embeddings.py` (full provider rewrite,
same public interface), `requirements.txt` (removed `torch`/
`sentence-transformers`), `.env.example` (added `NVIDIA_API_KEY`),
`.github/workflows/daily.yml` and `sunday.yml` (removed the now-broken
warm-up step and HF cache steps, added the new secret), `tests/
test_embeddings.py` (rewritten: dimension-mismatch guard test, and real
mocked-HTTP tests for `embed_text`/`embed_texts` -- request shape,
index-based response ordering, single-vs-batch token accounting -- where
none existed for the embedding calls themselves before, only for
`cosine_similarity`), `README.md`'s tech-stack table and setup
instructions (NVIDIA NIM in place of sentence-transformers).

**Real evidence:**
- Full test suite: `216 passed, 1 skipped` (8 new tests, 0 broken --
  `semantic_dedup.py`/`taste_vectors.py`'s existing tests all still pass
  unmodified, confirming the isolated-swap design held).
- **Live API verification** (real key, real endpoint, no mocking): single
  embedding call confirmed 2048-dim output; batch call (3 texts, then 50
  texts matching this project's own `BATCH_SIZE` convention) confirmed
  real batching in one request (~1.4s for 50), consistent dimensions, and
  a response `index` field used for ordering rather than assumed array
  order; `input_type="query"` vs `"passage"` confirmed to produce
  measurably different vectors for identical text (cosine ~0.85, not
  ~1.0) -- the real finding behind the `input_type` design decision above.
- **Live end-to-end pipeline verification** (real `dedupe_semantic()` and
  `taste_prefilter()`/`recompute_topic_vectors()` calls, not just the raw
  embedding function): a near-duplicate pair of fabricated items correctly
  deduped (cosine=0.969 >= 0.90 threshold), a genuinely distinct item
  correctly survived; real topic vectors recomputed from the real
  `data/taste_profile.yaml` content, all 6 mapped tags confirmed 2048-dim
  in the store; a real on-topic test item correctly survived
  `taste_prefilter()` and a real off-topic one was correctly dropped
  (cosine=0.286 < 0.30 threshold) -- both consumers working correctly
  against the new provider with zero code changes to either file.
- Test data written to the real store during this verification
  (`recent_item_embeddings`, plus my own fabricated-profile-text version
  of the topic vectors) was cleaned up afterward -- confirmed via
  follow-up `store.search()` calls showing 0 stray entries, and the topic
  vectors namespace was re-populated with the real profile-derived
  version described above rather than left empty or fake.

**Still open, needs Pooja's action, not Claude Code's:** add
`NVIDIA_API_KEY` as a real GitHub Secret on the repo (same standing rule
as every other secret this project has ever needed) before the next
scheduled `daily.yml`/`sunday.yml` run -- otherwise every embedding call
in that run will raise `KeyError` and `semantic_dedup`/`taste_prefilter`
will degrade to their already-built graceful-failure paths (pass
everything through unfiltered), not a hard pipeline failure, but real
filtering value lost until the secret is added.

## Capped one-time carry-forward for unfinished Reading/Courses items (2026-07-19)

Confirmed first, not assumed: `discovery/seen_items.py` marks a url seen
permanently (well, rolling-35-day, functionally permanent -- see that
file's own WORKFLOW.md entry) the moment it's scored, regardless of
keep/drop, and completely independent of whether Pooja ever actually
engaged with it. `plan_history`/`card_movements` (the recent Sunday plan
checkpoint) are Trello-card-only, verified via direct grep to have zero
functional reference to `seen_items` or item urls -- confirmed blind to
Reading & Learning/Courses items entirely. No existing signal, partial or
otherwise, distinguished "shown once" from "actually done" for those
items before this entry.

**Real prerequisite verified live before writing any code** (same
discipline as every other "is X actually there" check this session):
queried `information_schema.columns` directly against the real `DB_URI`
database for `companion_item_completions` -- confirmed it exists with
exactly the described schema (`url TEXT PK, checked BOOLEAN, run_id TEXT,
updated_at TIMESTAMPTZ`), one real test row already in it, zero existing
code references anywhere in this repo (genuinely new integration).

**A real gap found and closed before the feature could work at all:**
`digest_item_map` entries were identically shaped across Reading &
Learning, Courses, and Existing Project Work -- no field distinguished
which section an item came from. Without it, "last week's Reading/Courses
items" couldn't be reliably resolved from Existing Project Work items
(which are Trello-tracked separately and explicitly out of scope here).
Added a `section` field to every `item_map` entry in
`sunday/nodes/assemble_plan.py`'s `format_plan()` -- necessary
infrastructure for this feature, not scope creep, same category as
sub-phase 4's `plan_history` schema revision.

**Design decisions:**
- **Where it runs:** `sunday/carry_forward.py`'s `get_carry_forward_items()`,
  called from `assemble_plan()` -- the last real node before rendering.
  Placed here specifically so a carried item's already-scored data can be
  injected directly into a LOCAL `classified_items` copy, never into
  `state["classified_items"]` itself (so `plan_history`/
  `prioritize_plan_items`, both already run earlier in the graph, never
  see carried items -- they have no `matched_card_id` anyway).
- **Why no re-score/no seen_items block is structural, not just
  intended:** a carried item never becomes part of the CURRENT run's
  `raw_items`/`clustered_items` at all -- it's built directly from last
  week's `digest_item_map` entry and merged in at `assemble_plan`, which
  runs after `cluster_dedupe_node`/`score_node` have already finished
  with this run's own new items. There is no code path connecting
  `carry_forward.py` to either node, this run or any run -- confirmed by
  the module having zero import of either, and by the live test below
  showing zero Anthropic cost.
- **"Last week's Sunday run"** resolved via `run_history`
  (`path="sunday"`, `status="success"`, most recent `finished_at`,
  excluding the current run defensively) rather than assuming the
  previous run in wall-clock time was a Sunday run -- `run_history` also
  holds `daily`/`poll` entries interleaved with `sunday` ones.
- **`companion_item_completions` is read-only from this codebase** --
  SELECT only, confirmed by the module having zero INSERT/UPDATE/DELETE
  against it. Checking it off is `companion_writer`'s job (a separate
  app), not this pipeline's.
- **One url with no completion row at all is treated as unchecked** --
  "never interacted with" per the explicit spec, not skipped or treated
  as done.

**Files changed:**
- `sunday/nodes/assemble_plan.py` -- `section` field added to every
  `item_map` entry (all three sections); `assemble_plan()` now merges
  `get_carry_forward_items(run_id)`'s result into a local
  `classified_items` copy before calling `format_plan()`.
- `sunday/carry_forward.py` -- **new file** (see its own file-by-file
  entry above for full detail: `_find_prior_sunday_run_id`,
  `_load_prior_reading_and_course_items`, `_fetch_completion_status`
  (SELECT-only), `_already_carried`, `_log_carried`,
  `get_carry_forward_items`).
- `tests/test_carry_forward.py` -- **new file**, 11 tests: no-prior-run
  permissive default, no-row-at-all treated as unchecked, explicit
  `checked=false` carried, `checked=true` never carried, already-carried
  never carried twice, Existing Project Work items excluded via the
  `section` filter, a carried course item keeps its `course` tag, the
  carry gets logged with the correct `run_id`, most-recent-of-multiple-
  prior-runs picked correctly, non-Sunday and non-`success` `run_history`
  entries ignored.
- `tests/test_assemble_plan.py` -- 5 existing node-wrapper tests updated
  to mock `get_carry_forward_items` (now a real dependency of every
  `assemble_plan()` call); 3 new tests covering the merge itself (a
  carried item renders in the plan; `get_carry_forward_items` is called
  with the current run's real `run_id`; a carried item is never recorded
  in `plan_history`, since it has no `matched_card_id`).

**Real evidence:** full suite `230 passed, 1 skipped` (14 new tests, 0
broken). **Live, end-to-end, three-week simulation against the real
Supabase store and the real `companion_item_completions` table** (not a
dry description):

- **Week 1 setup:** wrote a real `run_history` entry (`path="sunday"`,
  `status="success"`) and a real `digest_item_map` entry with two items --
  one Reading item with NO completion row at all, one Courses item with a
  REAL `checked=true` row inserted into `companion_item_completions`.
- **Week 2 -- real `assemble_plan()` call:** rendered plan text (verbatim):
  ```
  📋 *Weekly Plan*

  **Reading & Learning**
  1. [An unfinished article (never interacted with)](https://live-carry-test.example.com/unfinished-article-DELETE-ME)
     _Directly relevant to active work._

  _1 plan items · run: live-car_
  ```
  **CHECK 1 (unchecked item carried forward): TRUE.** **CHECK 3 (checked=true
  item never carried): TRUE** -- the course item is absent from the output
  entirely. `assemble_plan`'s own returned cost: `{'cost_usd': 0.0, ...}`.
- **Week 2's real returned `item_map` was then persisted** as a real
  `run_history`/`digest_item_map` pair (not fabricated separately --
  exactly what `assemble_plan()` actually returned), to set up a genuine
  week 3 lookup.
- **Week 3 -- real `assemble_plan()` call:** rendered plan text (verbatim):
  ```
  📋 *Weekly Plan*

  _Nothing on the plan this week._
  ```
  **CHECK 2 (does not appear a third time): TRUE** -- correctly dropped
  permanently, no exceptions.
- **CHECK 4 (zero Haiku cost):** both week 2 and week 3's `assemble_plan`
  calls returned `cost_usd: 0.0`. Neither this test nor `carry_forward.py`
  itself ever imports or calls `score_node`/`classify_item`/
  `correlate_trello` -- the zero cost is architectural, not incidental.
- **One real mistake caught mid-verification, not hidden:** an earlier
  test attempt crashed (an unrelated Windows console Unicode error, after
  `assemble_plan()` had already fully executed and already logged the url
  to `carry_forward_log`); a same-day retry without also clearing that
  namespace produced a false CHECK 1 failure (the url was correctly
  excluded as "already carried" from the stale prior attempt, not a bug).
  Diagnosed, `carry_forward_log` cleared properly, re-ran clean -- the
  result above is from the clean run.
- **Cleanup fully verified, not assumed:** all real test entries
  (`run_history`, `digest_item_map`, `carry_forward_log`, the
  `companion_item_completions` test row) confirmed removed via follow-up
  queries returning zero remaining rows/entries each. `current_weekly_plan`
  was read and stashed before the test and restored to its exact real
  prior value afterward -- the lesson from this same mistake earlier in
  this project (sub-phase 3's live smoke test) applied this time, not
  repeated.

## Real send_telegram_plan 400: root cause, fix, and live re-verification (2026-07-19)

A real Sunday run (`run_id 5677ca1d`, `status: "failed"`,
`error_summary: "HTTPError: HTTP Error 400: Bad Request"` in `run_history`)
failed to post to Telegram. Investigated before touching any code, per the
explicit instruction -- root cause confirmed with real evidence, not
guessed.

### Root cause

`telegram/bot_client.py`'s `send_message()` defaulted to
`parse_mode="Markdown"` -- Telegram's **legacy v1** Markdown, which has
**no escape mechanism at all** (a backslash before a character does
nothing; the character is still a live entity delimiter). Meanwhile
`sunday/nodes/assemble_plan.py` (and, found during this investigation,
`daily/nodes/assemble_digest.py` independently) escaped underscores with
**MarkdownV2** syntax (`reasoning.replace("_", r"\_")`) -- a mismatch
between the escaping strategy and the actual parse_mode in use. This had
been latent until `prioritize_plan_items`'s real LLM-generated reasoning
happened to contain the literal substring `last_activity` (in the
Existing Project Work movement notes) -- the un-escaped-in-practice `_`
inside `last_activity`, nested inside an already-open `_..._` italic
span, threw off entity pairing for the rest of the entire message.

Also confirmed as a real, separate gap: `send_message()` let
`urllib.error.HTTPError` propagate completely unread on any failure --
the response body (where Telegram's real, specific error description
lives) was never captured. `run_history.error_summary` only ever showed
the generic `"HTTPError: HTTP Error 400: Bad Request"` string. Root-
causing this run required a manual reproduction script with proper
exception handling; that script's real Telegram error response:

```json
{"ok":false,"error_code":400,"description":"Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 3911"}
```

Byte offset 3911 was the very last character of the message (the
footer's closing `_`) -- not itself the problem, just the final casualty
of a pairing cascade that started with `last_activity` earlier in the
text.

**Length checked and ruled out for this specific failure:** the real
`plan_text` (pulled from `current_weekly_plan`, the exact text that was
sent) was 3876 characters -- under Telegram's 4096 limit. Not the cause
this time, but flagged below as a real separate risk.

### Fix

- `telegram/bot_client.py` -- default `parse_mode` changed from
  `"Markdown"` to `"HTML"`. `urllib.error.HTTPError` now caught, the real
  response body read, logged, and included in the raised `RuntimeError`
  -- this is what should have (and now will) surface the real cause
  immediately on any future failure, no manual reproduction needed.
  `parse_mode=None` support added (omits the key entirely -> plain,
  unformatted text) for callers with no formatting intent.
- `telegram/markdown.py` -- new `escape_html()` helper (`&` escaped
  first, then `<`/`>`, to avoid double-escaping). The pre-existing
  `escape_markdown_v2()` (used only by `sunday/nodes/await_approval.py`,
  which already correctly passes `parse_mode="MarkdownV2"` explicitly)
  is untouched and unaffected by this change.
- `sunday/nodes/assemble_plan.py` and `daily/nodes/assemble_digest.py` --
  both rewritten to render with real HTML tags (`<b>`, `<i>`,
  `<a href="...">`, `<code>` for digest tags) instead of Markdown syntax,
  every dynamic value HTML-escaped at the point of interpolation.
  `item_map` continues to store RAW unescaped values in both files --
  verified this doesn't create double-escaping for `assemble_plan.py`'s
  `carry_forward.py` reuse path with a dedicated test.
- **Real, necessary blast-radius fix, not scope creep:** `sunday/approval_actions.py`
  has two `send_message()` calls (card-approval confirmations) that relied
  on the *default* parse_mode with **zero escaping** of the interpolated
  real Trello `card['name']`/`card['url']`. Changing the default to HTML
  would have left these newly exposed to the exact same bug class if a
  card name ever contained `&`/`<`/`>`. Fixed by passing `parse_mode=None`
  explicitly (plain text -- neither message has any formatting intent, so
  this sidesteps needing to escape at all, simpler than escaping).
  `telegram/polling.py`'s one static-string `send_message()` call was
  checked and left alone -- no dynamic content, no special characters,
  genuinely no risk either way.

### Real evidence

Full test suite: `254 passed, 1 skipped` (new coverage: `tests/test_bot_client.py`
and `tests/test_telegram_markdown.py`, both previously zero-coverage files;
`tests/test_assemble_plan.py`/`tests/test_assemble_digest.py` rewritten for
the new HTML assertions plus new escaping-specific tests).

**Live re-send of the exact failed run's real content, through the fixed
pipeline** (not the old broken text resent as-is -- the real classified
items/Trello cards/`prioritized_project_work` entries reconstructed
faithfully from `run 5677ca1d`'s stored `plan_text`, including the exact
real `last_activity`-containing reasoning strings that caused the
original 400, fed through the FIXED `format_plan()` to produce a properly
HTML-rendered message, then sent through the FIXED `send_message()`):

```
REAL SEND SUCCEEDED
message_id: 48
ok: True
```

**Live daily digest send** (today's real stored `current_daily_digest`
turned out to be just the empty "Nothing new today" fallback -- `daily.yml`
doesn't run Sundays, confirmed via a live store read before deciding how
to test this -- so real content from the same day's real pipeline output
was reshaped as `ScoredItem`s to actually exercise `format_digest()`'s
tag-as-`<code>` rendering path, which the Sunday plan format doesn't
have at all):

```
REAL SEND SUCCEEDED
message_id: 49
ok: True
```

Both real messages are now sitting in Pooja's actual Telegram chat.

### Flagged, explicitly NOT fixed: message length risk

The reconstructed HTML version of `run 5677ca1d`'s real content is
**4032 characters** -- UP from the original Markdown version's 3876,
since `<b>`/`<i>`/`<a href="...">` tags are more verbose than `*`/`_`/
`[]()` syntax for the same content. This is now **98.4% of Telegram's
4096-character limit**, tighter than before this fix, not looser. Not
touched in this pass, per explicit instruction -- a real, separate
decision (truncation vs. splitting into multiple messages) is needed as
a follow-up before this silently becomes the next real production
failure, likely sooner than the original length margin would have
suggested.

## Length-budget follow-up: reasoning truncation over message splitting (2026-07-19)

The flagged risk above landed the same day: the HTML parse_mode fix
alone pushed a real message to 4032/4096 chars (98.4%), and normal week-
to-week variation in item count/reasoning length would exceed the real
4096 hard limit soon.

### Decision: truncation, not multi-message splitting

Checked the actual blast radius of splitting before choosing, not just
theorized: `send_telegram_plan.py` currently sends exactly one message
and persists exactly one `digest_item_map` entry per run; splitting would
require changes to `assemble_plan()`'s node wrapper AND
`send_telegram_plan.py` (looping over chunks, one `send_message()` call
and one `digest_item_map` write per chunk), and would introduce a real
correctness bug in already-shipped code: `sunday/carry_forward.py`'s
`_load_prior_reading_and_course_items()` returns on the FIRST
`digest_item_map` entry matching a given `run_id` -- with multiple
entries per run (one per message chunk), a second chunk's items would be
silently missed by next week's carry-forward lookup. Truncation is fully
contained inside `format_plan()`, with zero changes needed anywhere
downstream -- the less invasive choice given the current structure, per
the explicit ask.

### What was built

See `sunday/nodes/assemble_plan.py`'s file-by-file entry above for the
full mechanism (`_render()`, `_truncate()`, `MAX_PLAN_TEXT_CHARS = 3900`,
`REASONING_CHAR_BUDGET = 150`, the shrinking safety net). Also fixed,
found during this same investigation: `_build_project_entries()`'s
`stale_nudge` title path never truncated `card_name` to 80 characters
like every other title path already did -- a real card name observed at
~200 chars was rendering in full (contributing meaningfully to the
original overflow, independent of reasoning length).

### Real evidence

Full test suite: `260 passed, 1 skipped` (6 new tests in
`tests/test_assemble_plan.py`: under-budget text unaffected/no
truncation applied; over-budget reasoning truncated with a visible `…`
marker and the final render confirmed under budget; `item_map` confirmed
to keep the FULL untruncated reasoning even when the rendered text was
capped -- critical for `carry_forward.py` reuse; `card_name` truncated in
the "continues card" suffix when over budget; the `stale_nudge` title
`[:80]` bug fix tested directly; the shrinking safety net engaging for a
genuinely extreme 40-item case).

**Live test, deliberately constructed over 4096 chars** (9 Reading items
+ 4 Existing Project Work stale_nudge entries, all with realistic-length
synthetic reasoning/card names matching real observed lengths, not
minimal fixtures):

- Confirmed the **untruncated** render was **6260 characters** -- would
  have failed exactly like the original real 2026-07-19 400.
- Through the fixed `format_plan()`, real log output showed the safety
  net actually engaging, not just the first fixed cap succeeding by luck:
  ```
  format_plan: rendered text 6260 chars exceeds the 3900-char budget -- re-rendering with reasoning capped to 150 chars per item
  format_plan: still over budget at 4936 chars -- shrinking reasoning cap to 75
  format_plan: still over budget at 3924 chars -- shrinking reasoning cap to 37
  ```
- Final rendered length: **3287 characters** -- under both the 4096 hard
  limit and the 3900 soft budget. All 13 items still present (unbounded
  item count preserved, exactly as intended -- only reasoning verbosity
  was cut).
- **Real send via the fixed `send_message()`:**
  ```
  REAL SEND SUCCEEDED
  message_id: 50
  ok: True
  ```

Three real messages now sit in Pooja's actual Telegram chat from this
day's investigation-and-fix work (`message_id`s 48, 49, 50) -- the
original parse_mode fix's two live tests, plus this length-budget test.
