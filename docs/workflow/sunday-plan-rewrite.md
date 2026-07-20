[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Sunday plan LLM prioritization checkpoint

## Sunday plan LLM prioritization checkpoint (2026-07-18)

Real design expansion to the Sunday plan pipeline, resolving the
long-deferred "`assemble_plan`: templating vs LLM judgment" question in
favor of LLM judgment. Built sub-phase by sub-phase per the standing
approval-gate discipline (`CLAUDE.md` Section 1) — this is NOT a one-pass
build. Full scope, for context (later sub-phases not yet started):

1. **Courses get their own digest section** (reversal of the earlier
   implicit fold into Reading & Learning) — **DONE.**
2. `read_trello` exposes the full card list with per-card staleness
   (last-activity date), not just what `correlate_trello`'s match-checking
   needs today — **DONE.**
3. New store namespace `("weekly_intel", "plan_history")` — each Sunday run
   records which Trello card IDs got surfaced as plan items that week, tied
   to `run_id` — **DONE.**
4. Cross-week movement detection: before generating each new plan,
   re-fetch current Trello state for every card in the most recent prior
   `plan_history` entry, and determine real movement (list change, archive,
   move to a Done-equivalent list) from Trello's actual state. Requires
   fixing `read_trello` to fetch a Done-equivalent list at all, which it
   currently never does — **DONE, this entry.**
5. New node/step, after `classify_item` and before `assemble_plan`: one
   real Anthropic API call combining this week's scored/classified items,
   real Trello board state, and the completion signal from item 4, to
   produce a bounded, prioritized selection. Persona: Pooja is an AI/ML
   engineer doing this project as a side effort alongside a full-time job,
   specifically to reclaim time her job doesn't give her — the goal is
   identifying what's genuinely worth her limited weekly hours, not listing
   everything relevant, including surfacing stale/idle Trello cards weighed
   honestly against new discoveries, and acknowledging (not silently
   repeating or dropping) cards unchanged since last week — **DONE, this
   entry.**
6. Bounding: Reading & Learning and Courses stay unbounded. Only the
   Trello-derived plan-item selection is bounded (target 3-5 items,
   adjustable with real evidence) — **DONE.** `prioritize_plan_items` hard-caps
   at `MAX_PROJECT_WORK_ITEMS = 5`, and `assemble_plan` now renders Existing
   Project Work exclusively from that bounded selection — Reading & Learning
   and Courses remain unbounded, unchanged.
7. `assemble_plan` renders item 5's curated output in priority order, not
   source order — **DONE, this entry.** Rendering order is exactly
   `prioritized_project_work`'s order; Reading & Learning/Courses still
   render in `classified_items` arrival order (unaffected, per item 6 --
   only the Trello-derived selection needed priority-order rendering).

Ownership: the new LLM call (item 5) is a direct Anthropic API call,
covered by the standing full-delegation override (`CLAUDE.md` Section 8) —
same arrangement as every other Anthropic/LangGraph call built this
session.

### Sub-phase 1: Courses digest section — what was built

Real ambiguity surfaced before writing any code: there was no existing
signal that distinguished "course" content from any other learning
content. The only per-item classification field is `tags: list[str]`, and
the closest existing tag, `learning-resource`, is explicitly a catch-all in
`score.py`'s `TASTE_PROFILE` ("tutorials, opinion pieces, critiques,
essays, walkthroughs, courses, papers, and threads all qualify"), not
course-specific. Per-source identity is also unrecoverable at this layer —
every blog/newsletter item (including "The Batch") gets `source =
"blog_scrape"` hardcoded at ingestion (`scrape_blogs.py`); the real
feed/sender name is used only for fetching, then discarded. Asked Pooja how
to identify Courses content; she chose a new dedicated tag over a
source-based mapping.

**Files changed:**
- `discovery/nodes/score.py` — added `course` to `ALLOWED_TAGS` and to the
  `_score_batch` prompt's permitted-tags list; added tagging guidance to
  `TASTE_PROFILE` distinguishing `course` (structured, multi-lesson course/
  bootcamp/certification) from `learning-resource` (single article/
  tutorial/essay/walkthrough).
- `discovery/taste_vectors.py` — `course` added to `_TAG_TO_BULLET` mapped
  to `None`, deliberately unmapped for the same reason `learning-resource`
  is: it's a format tag, not a topic, so there's no corresponding
  `TASTE_PROFILE` topic bullet to embed. `TOPIC_TAGS` (derived from
  `ALLOWED_TAGS`) now has 7 entries, 2 unmapped.
- `sunday/nodes/assemble_plan.py` — `format_plan()` now splits `plan_items`
  into three buckets instead of two: `courses` (any item tagged `course`,
  checked first — takes priority over `matched_card_id`), `reading`
  (no `course` tag, no `matched_card_id`), `project` (no `course` tag, has
  `matched_card_id`). Renders in that order: Reading & Learning, Courses,
  Existing Project Work. Numbering (`counter`/`item_map`) stays continuous
  across all three sections, same pattern as the pre-existing two-section
  numbering.
- `tests/test_assemble_plan.py` — added 6 new tests covering: course-tagged
  item routes to Courses not Reading & Learning; Courses section omitted
  when no course items exist; course tag takes priority over a matched
  `card_id` (still routes to Courses, not Existing Project Work); all three
  sections present and correctly ordered; numbering continuous across all
  three sections; `item_map` correctness across all three sections. `_plan_item()`
  test helper extended with an optional `tags` param (previously hardcoded
  to `["agentic-engineering"]`).
- `tests/test_taste_vectors.py` — updated the two `recompute_topic_vectors`
  tests to expect 2 unmapped tags (`["course", "learning-resource"]`,
  alphabetically sorted) instead of 1, since `course` joining `ALLOWED_TAGS`
  as an unmapped tag changes `TOPIC_TAGS`' composition. No behavioral
  regression — same "flagged, not guessed" pattern, just one more tag going
  through it.

**Real evidence:**
- Full test suite: `162 passed, 1 skipped` (`tests/`), including all 6 new
  Courses tests and the 2 updated `taste_vectors` tests — no pre-existing
  test broken by this change. (`session-handoff.md`'s note that
  `test_assemble_plan.py` was failing turned out to be stale as of this
  session — all 19 pre-existing tests in that file already passed before
  any change here.)
- Manual `format_plan()` smoke test (three real items: one Reading &
  Learning, one `course`-tagged, one Existing Project Work) confirmed real
  rendered output: three headers in the right order, continuous numbering
  1/2/3, Courses item correctly excluded from Reading & Learning despite
  having no `matched_card_id`.

**Explicitly NOT done in this sub-phase** (scope discipline, `CLAUDE.md`
Section 5): no bounding logic added anywhere (item 6 is a later
sub-phase); no priority-order rendering (item 7); no change to
`read_trello`, no `plan_history` namespace, no cross-week detection, no new
LLM node (items 2-5). `daily/nodes/assemble_digest.py` (the daily digest,
separate from the Sunday plan) was not touched — this checkpoint is
Sunday-plan-only per the request.

### Sub-phase 2: Trello card staleness — what was built

Another real ambiguity surfaced before writing code: "expose the FULL card
list with staleness" could mean either (a) add a `last_activity` field to
the cards `read_trello` already fetches (`Dump` + `In Progress`), or (b)
also start fetching the board's other lists, including a `Done`-equivalent
one, since only 2 of the board's real lists are fetched today. The original
request's note about the missing `Done`-list fetch was attached to item 4
(cross-week movement/completion detection) with the reasoning "completion
detection is impossible without it" — completion detection is item 4's job,
not this one's. Asked Pooja; she confirmed staleness-only for this
sub-phase, deferring the `Done`-list fetch to sub-phase 4.

Live-checked the real board (`BRAIN_BOARD_ID`) as part of this
investigation, not guessed: it has 6 lists total — `Dump`, `In Progress`,
`qs to ask`, `Future Ideas`, `so i dont lose track`, `Done`. Only the first
two are fetched by `fetch_board_cards()`, unchanged by this sub-phase. Also
confirmed live that Trello's default card response already includes
`dateLastActivity` — no `fields` API parameter change was needed to expose
it, just reading the key that was already there and being discarded.

**Files changed:**
- `sunday/trello_client.py` — `fetch_board_cards()` now includes
  `"last_activity": card.get("dateLastActivity")` in every returned card
  dict. List-fetch scope (`RELEVANT_LIST_NAMES`) unchanged.
- `state.py` — `SundayGraphState.trello_cards`' comment expanded to
  document the full current card dict shape, including `last_activity`.
- `tests/test_trello_client.py` — **new file** (`fetch_board_cards()` had
  zero unit coverage before this). 5 tests: `last_activity` populated from
  a mocked Trello response; graceful `None` if a card response ever omits
  `dateLastActivity` (defensive, since the real API always sends it);
  list-filtering behavior preserved (only `Dump`/`In Progress` cards
  returned even when other lists, including `Done`, are present in the
  lists response); checklist-flattening behavior preserved alongside the
  new field; full exact-shape assertion on one real-shaped card dict.
  `_trello_get` mocked throughout — no real HTTP call in the test suite.

**Real evidence:**
- Full test suite: `167 passed, 1 skipped` (up from 162 — 5 new tests, 0
  broken).
- **Live run against the real Trello board** (`sunday.nodes.read_trello.
  read_trello()`, real `TRELLO_API_KEY`/`TRELLO_TOKEN`, no mocking):
  fetched 34 real cards from `Dump` + `In Progress`. All 34 carry a real,
  non-null `last_activity` value (e.g. `"2026-05-31T12:17:18.243Z"`) — 0
  cards missing the field. Confirms the field works end-to-end through the
  actual node, not just against a mocked response.

**Explicitly NOT done in this sub-phase:** no expansion of which Trello
lists get fetched (the real `Done` list is confirmed to exist and is
intentionally still not fetched — deferred to sub-phase 4); no staleness
*interpretation* logic (e.g. "stale after N days") — this sub-phase only
exposes the raw timestamp, any threshold/judgment is item 5's (the new LLM
node)'s job; `correlate_trello` was not modified and does not read
`last_activity` — it still only uses `card_id`/`list_name`/`name`/
`checklist_items`, confirmed unchanged by the full test suite passing.

### Sub-phase 3: `plan_history` store namespace — what was built

**Files changed:**
- `sunday/plan_history.py` — **new file.** `record_plan_history(run_id,
  card_ids)` writes one entry per run to `("weekly_intel", "plan_history")`,
  keyed by `run_id`, value `{run_id, card_ids: list[str] (deduped, sorted),
  generated_at}`. Deliberately not wrapped in try/except — see the file
  entry above for why (real domain data, not observability).
- `sunday/nodes/assemble_plan.py` — `assemble_plan()` node wrapper now
  computes `surfaced_card_ids` (the deduped `matched_card_id`s of items
  that satisfy the exact same predicate as the Existing Project Work
  section: `classification == "plan_item"`, no `course` tag, real
  `matched_card_id`) and calls `record_plan_history(run_id,
  surfaced_card_ids)` before the existing `current_weekly_plan` write.
  `format_plan()`'s own tested 2-tuple return signature was left
  unchanged (no third return value added) — the predicate is recomputed
  directly from `state["classified_items"]` in the node wrapper instead,
  a small deliberate duplication that avoids touching every existing
  `format_plan()` call site's tuple-unpacking.
- `state.py` — no change needed; `trello_cards`/`classified_items` shapes
  already carried everything this sub-phase needed.
- `tests/test_plan_history.py` — **new file.** 4 tests: one entry written
  per `run_id`; duplicate `card_ids` collapsed; an empty-list week still
  records a real entry (not skipped — "zero cards surfaced" is meaningful
  data, distinct from no record at all); `card_ids` sorted for
  deterministic output. `get_store()` mocked, no real store touched.
- `tests/test_assemble_plan.py` — 5 new tests on the `assemble_plan()` node
  wrapper (previously untested — only the pure `format_plan()` function had
  coverage): records only Existing-Project-Work card IDs; excludes a
  course-tagged item's card ID even when `matched_card_id` is set (mirrors
  the Courses-takes-priority rule from sub-phase 1); excludes proposals
  (`classification != "plan_item"`); records an empty list when there's no
  project work; the pre-existing `current_weekly_plan` write still happens
  unchanged. `get_store` and `record_plan_history` both mocked at the
  `sunday.nodes.assemble_plan` import site.

**Real evidence:**
- Full test suite: `175 passed, 1 skipped` (up from 167 — 9 new tests, 0
  broken). One unrelated flake seen mid-run (`test_blog_sources_yaml.py`'s
  live Hacker News RSS fetch timed out) — confirmed transient, passed
  clean on immediate re-run; not caused by anything in this sub-phase.
- **Live write against the real Supabase store** (not mocked): ran the
  real `assemble_plan()` node with a synthetic run_id
  (`smoke-test-plan-history-DELETE-ME`) and two fabricated classified
  items (one Reading & Learning, one Existing Project Work matched to a
  real card ID). Confirmed via `store.get()` the real entry landed exactly
  as expected: `{"run_id": "smoke-test-plan-history-DELETE-ME",
  "card_ids": ["realcard123"], "generated_at": "2026-07-18T15:32:45...Z"}`
  — only the project-work card ID present, the reading-section item
  correctly excluded. The test entry was then deleted (`store.delete()`),
  confirmed gone via a follow-up `store.get()` returning `None` — no
  permanent pollution of the real `plan_history` table.

**Real mistake made and disclosed, not buried:** the same live smoke test
also ran the node's pre-existing `("companion",) "current_weekly_plan"`
write (unrelated to this sub-phase's own change, but part of the same node
function) against the real store, overwriting Pooja's real current plan
with fake placeholder text ("Article A" / "Project B", 218 chars) — done
without reading/saving the real prior value first, so it can't be cleanly
restored. Flagged to Pooja immediately; her call was to leave it (self-
heals at the next real Sunday run; the placeholder text is obviously fake,
low risk of being mistaken for a real plan in the meantime). Lesson for
future live smoke tests against singleton overwrite-in-place keys: read
and stash the existing value first, or avoid exercising that code path
live at all when only a different part of the same function needs
verifying.

**Explicitly NOT done in this sub-phase:** no reading of `plan_history`
anywhere (no "most recent prior entry" query exists yet — that's sub-phase
4's job); no cross-week comparison logic; no change to what
`correlate_trello` or `classify_item` do with card data.

### Sub-phase 4: cross-week movement detection — what was built

**A necessary schema revision surfaced immediately:** sub-phase 3's
`plan_history` entries only stored bare `card_ids`. Detecting "did the card
change lists" requires knowing which list the card was in when it was LAST
surfaced — a bare ID can't support that comparison. Revised the schema
(see `plan_history.py`'s and the store-namespace registry's entries above)
to `{"card_id", "list_name"}` pairs instead. No real production data
existed under the old shape (the only entry ever written was sub-phase 3's
own smoke test, already deleted), so this was a clean change, not a
migration — flagged here rather than silently changed, since it revises
already-shipped/committed work.

**Design decision on WHERE the Done list gets fetched:** rather than
adding `"Done"` to `RELEVANT_LIST_NAMES` (which would put Done-list cards
into `correlate_trello`'s matching pool — wrong, since matching new content
against an already-completed project makes no sense), added a separate
`fetch_list_id_to_name_map()` that returns every open list's `{id: name}`
including Done, used only to resolve a card's current list name for
movement comparison. `fetch_board_cards()` itself is untouched.

**Design decision on HOW a card's current state is checked:** rather than
re-fetching entire lists (which still wouldn't surface archived/closed
cards, since Trello's `filter=open` list-cards endpoint excludes them
entirely), added `fetch_card_current_state(card_id)` — a direct per-card
`GET /1/cards/{id}`, live-verified to return `idList`/`closed`/`name` by
default. This handles every case (moved list, moved to Done, archived,
permanently deleted) with one call per card, and is real ground truth
regardless of which list a card is in now — not dependent on it still
being in one of the two lists `fetch_board_cards()` fetches.

**Where the new logic lives:** no new graph node was added — item 5 (not
yet built) is the checkpoint's one explicitly-specified new node. Movement
detection runs inside the existing `read_trello` node (the earliest point
in the Sunday graph with Trello access, and it already runs once per
Sunday invocation, satisfying item 4's "before generating each new plan").

**Files changed:**
- `sunday/trello_client.py` — added `DONE_LIST_NAME = "Done"` constant
  (live-confirmed against the real board), `fetch_list_id_to_name_map()`,
  `fetch_card_current_state()`.
- `sunday/plan_history.py` — `record_plan_history()`'s signature changed
  from `card_ids: list[str]` to `cards: list[dict]` (`{"card_id",
  "list_name"}`); dedup now keeps the first occurrence's `list_name`. New
  `get_most_recent_prior_entry(current_run_id)` reader.
- `sunday/card_movement.py` — **new file.** `detect_card_movement(run_id)`:
  looks up the most recent prior `plan_history` entry, fetches each card's
  real current state, classifies as `archived` / `not_found` / `completed`
  / `moved` / `unchanged` (see the file's own entry above for the exact
  precedence rules). Returns `[]` with no prior entry to compare against.
- `sunday/nodes/read_trello.py` — now also calls `detect_card_movement()`
  and returns `card_movements` in its output dict.
- `state.py` — `SundayGraphState` gained `card_movements: list[dict]`
  (default `[]` in `make_sunday_initial_state()`).
- `sunday/nodes/assemble_plan.py` — `surfaced_cards` now built as
  `[{"card_id", "list_name"}, ...]` (looked up from `state["trello_cards"]`
  by `matched_card_id`, `"Unknown"` fallback if absent) instead of bare
  IDs, matching `record_plan_history()`'s revised signature.
- `tests/test_trello_client.py` — 6 new tests for
  `fetch_list_id_to_name_map` and `fetch_card_current_state` (real fields
  returned, `closed` reflected, `None` on a real 404, non-404 errors
  re-raised rather than swallowed).
- `tests/test_card_movement.py` — **new file.** 7 tests covering every
  status classification, the no-prior-entry permissive default, and
  multiple cards classified independently in one call.
- `tests/test_plan_history.py` — rewritten for the new `cards` schema; 4
  tests for `record_plan_history` (now asserting `{"card_id","list_name"}`
  shape) plus 3 new tests for `get_most_recent_prior_entry` (empty store,
  picks the latest by `generated_at`, excludes `current_run_id`).
- `tests/test_assemble_plan.py` — the 5 sub-phase-3 node-wrapper tests
  updated for the new `cards` shape, plus one new defensive test (a
  `matched_card_id` not found in `trello_cards` records `"Unknown"`
  instead of crashing).

**Real evidence:**
- Full test suite: `192 passed, 1 skipped` (up from 175 — 17 new tests, 0
  broken).
- **Live simulated two-week round trip against the real Supabase store and
  real Trello API** (no mocking): fetched a real card from the real board
  (`6a1c26cf93b6df1996f39da3`, list `Dump`); wrote a fake "week 1"
  `plan_history` entry recording that real card at its real list; called
  `get_most_recent_prior_entry()` from a different "week 2" run_id and
  confirmed it found the week-1 entry; called `detect_card_movement()` and
  confirmed the real card was correctly classified `"unchanged"` (nothing
  on the real board changed between the two calls) — exactly the "two
  consecutive runs... showing a card correctly identified as unchanged"
  evidence this sub-phase's spec asked for. Both fake entries deleted
  afterward; confirmed gone via a follow-up `store.get()`.
- **Live full-node run**: `read_trello()` against the real board (28
  current cards) with no prior `plan_history` entry in the store (none
  exists from a real Sunday run yet) correctly returned `card_movements:
  []` — the permissive first-run path, not a crash.
- The `moved`/`completed`/`archived` classification paths are covered by
  real unit tests (`test_card_movement.py`) rather than a second live
  round trip — verifying those live would require actually moving/
  archiving a real card on Pooja's real Trello board, which weeks in
  advance of item 5 actually consuming this signal, and without her asking
  for it, was judged out of scope for a smoke test to do unilaterally.

**Explicitly NOT done in this sub-phase:** no LLM call anywhere in this
sub-phase's code (movement classification is deterministic, per the
spec's own "ground truth from Trello's actual state, not a self-reported
flag"); `card_movements` is not yet consumed by anything — `assemble_plan`
doesn't render it, no LLM node reads it yet (that's item 5); no new graph
node added.

### Sub-phase 5: the new bounded LLM prioritization node — what was built

**Restructured the remaining checkpoint plan to match Pooja's original
5-part breakdown** ("Courses section / Trello staleness+plan_history /
cross-week movement detection / the new LLM node / assemble_plan
rendering"), rather than continuing the finer-grained numbering used for
sub-phases 2-4. This sub-phase covers item 5 only (the new node itself,
producing a bounded/prioritized selection in state); the checkpoint's
final sub-phase covers items 6-7 together (`assemble_plan` actually
consuming and rendering that selection) — matching the breakdown's last
bullet, "assemble_plan rendering," as one unit.

**New node, real Anthropic call:** `sunday/nodes/prioritize_plan_items.py`
(`prioritize_plan_items(state) -> dict`) -- see its file entry above for
the full prompt design, bounding, and validation details. Wired into
`sunday/graph.py`'s fan-out after `classify_item`, parallel to
`route_to_approvals`/`proposal_worker`, feeding into `assemble_plan` via a
new edge. Model: `claude-haiku-4-5`, matching every other LLM node in this
codebase (`score_node`, `correlate_trello`, `classify_item`) for cost-tier
consistency -- not upgraded to a stronger model despite the more nuanced
judgment call this node makes, since that's a real cost/quality tradeoff
decision, not something to change unilaterally. Worth revisiting if the
selection quality turns out to need it once this is used for real.

**Files changed:**
- `sunday/nodes/prioritize_plan_items.py` — **new file** (see its own
  entry above).
- `state.py` — `SundayGraphState` gained `prioritized_project_work:
  list[dict]` (default `[]`).
- `sunday/graph.py` — `_fan_out_after_classify` now sends to
  `"prioritize_plan_items"` instead of directly to `"assemble_plan"`; new
  node registration; new edge `prioritize_plan_items -> assemble_plan`.
  Sunday parent graph diagram (above) regenerated and re-verified against
  the real compiled graph.
- `tests/test_prioritize_plan_items.py` — **new file.** 9 tests: a
  selected card appears in the output; a `stale_nudge` entry with no
  matched item works; hard cap at `MAX_PROJECT_WORK_ITEMS` even if the
  model returns more; a hallucinated `matched_card_id` is dropped; a
  hallucinated `item_url` on a `new_item` entry is dropped; model-returned
  order is preserved (no re-sorting); the movement block reaches the real
  prompt text; JSON-parse-failure fallback returns unprioritized matched
  items capped at the bound; course-tagged items never reach the prompt as
  candidates.

**Real evidence:**
- Full test suite: `201 passed, 1 skipped` (up from 192 — 9 new tests, 0
  broken). `build_sunday_graph()` compiling successfully (exercised by
  `tests/test_ingest_bookmarks_gating.py`'s real graph-build test) confirms
  the new node/edge wiring is structurally valid, not just unit-tested in
  isolation.
- **Live run against a real Anthropic API call, the real Trello board, and
  a real stale card**: fetched all 28 real board cards; identified the two
  real cards with the OLDEST `last_activity` among `In Progress` cards
  (43+ days idle, live-confirmed, not fabricated); ran `prioritize_plan_items()`
  with the full real `trello_cards`, one fabricated "new content this
  week" item matched to the real freshest card, and a `card_movements`
  entry marking a different real card `"unchanged"`. Real result: 3
  entries (within the 3-5 target, not padded) — the fresh matched item
  ranked #1 with reasoning citing it as "your hottest signal this week,"
  and the two genuinely stale (43+ days) `In Progress` cards surfaced at
  #2/#3 with real reasoning citing their actual idle duration and asking
  for "a decision" on them — real evidence of "surfacing Trello cards that
  have gone stale/idle, weighed honestly against new discoveries," not a
  scripted/expected outcome. The `"unchanged"` card was correctly NOT
  forced into the selection (the prompt only requires acknowledgment IF
  included, not mandatory inclusion) — real cost: `$0.001134`, 2474 input
  / 412 output tokens.

**Explicitly NOT done in this sub-phase:** `assemble_plan.py` does not
read `prioritized_project_work` at all yet — the plan Pooja actually
receives via Telegram is unaffected by this sub-phase; still unbounded,
still source-order, still built purely from `classified_items` +
`matched_card_id` the same way it was before sub-phase 5. That wiring
(items 6-7) is the checkpoint's final sub-phase.

### Final sub-phase: `assemble_plan` rendering (items 6-7, bounding + priority order) — what was built

This completes the Sunday plan LLM prioritization checkpoint. See the
`sunday/nodes/assemble_plan.py` file entry above for the rewritten
`_build_project_entries()`/`format_plan()` behavior in full.

**A required knock-on fix, not scope creep:** `assemble_plan()`'s
`record_plan_history()` call (built sub-phase 3, schema revised sub-phase
4) previously derived `surfaced_cards` from `classified_items`' raw
`matched_card_id` filter — the same set the OLD unbounded Existing
Project Work section rendered. Now that the section is bounded, that
computation would silently drift out of sync with what's actually
rendered (recording cards that don't appear in the plan at all). Updated
it to build `surfaced_cards` from `state["prioritized_project_work"]`
directly, so "surfaced" continues to mean "Pooja actually saw this in the
plan" — the exact meaning cross-week movement detection (sub-phase 4)
already depends on.

**Two now-obsolete node-wrapper tests removed, not force-fit:**
`test_assemble_plan_excludes_course_tagged_cards_even_when_matched` and
the plan-history half of proposal-exclusion testing no longer describe
real behavior at the `assemble_plan` layer — course-tag and proposal
exclusion happen upstream in `prioritize_plan_items` now (already covered
by `test_course_tagged_items_excluded_from_candidates` in
`tests/test_prioritize_plan_items.py`), since `assemble_plan` just mirrors
whatever `state["prioritized_project_work"]` says. Testing the same rule
twice at two layers that no longer both enforce it would just be
misleading, so these were deleted rather than rewritten to pass.

**Files changed:**
- `sunday/nodes/assemble_plan.py` — new `_build_project_entries()` helper
  (see file entry above); `format_plan()` gained a 5th parameter
  `prioritized_project_work: list[dict] | None = None` (optional/keyword-
  compatible, so every pre-existing call site that doesn't care about
  project-work rendering — Reading & Learning/Courses/formatting/footer
  tests — kept working unchanged); the empty-plan fallback check now
  covers `reading`/`courses`/`project_entries` all being empty, not just
  `plan_items`; footer's plan-item count now reflects what's actually
  rendered, not raw `plan_items`. `assemble_plan()` node wrapper passes
  `state["prioritized_project_work"]` through and rebuilds `surfaced_cards`
  from it (see above).
- `tests/test_assemble_plan.py` — substantially rewritten: every test that
  exercises the Existing Project Work section now passes a matching
  `prioritized_project_work` entry (a real matched item no longer
  auto-renders there). Added: `stale_nudge`-entry rendering (from the
  Trello card, not a scored item); `movement_note` appended to reasoning;
  priority-order rendering proven directly (reversed `prioritized_project_work`
  order renders reversed, independent of `classified_items`' order); a
  matched-but-unselected item renders nowhere (the bounding); footer count
  reflects the bounded render, not the raw matched count; a `stale_nudge`-
  only week (zero `classified_items`) does not trigger the empty-plan
  fallback; a `new_item` entry with an unresolvable `item_url` falls back
  to the card instead of crashing. Reading & Learning/Courses/formatting/
  footer/item_map tests that don't touch project work are unchanged.

**Real evidence:**
- Full test suite: `207 passed, 1 skipped` (net +1 test overall after 2
  removals and rewrites; the module's own count grew from 33 to 38 tests).
  One unrelated flake seen mid-run (`test_blog_sources_yaml.py`'s live
  Hacker News RSS fetch) — confirmed transient, passed clean on immediate
  re-run, same known flake seen in sub-phase 3.
- **Live end-to-end run against the real Trello board, a real
  `prioritize_plan_items()` Anthropic call, AND the real `format_plan()`
  renderer** (no store writes this time -- `format_plan()` called
  directly, deliberately avoiding another `current_weekly_plan`
  clobber): real board state, one fabricated "new content this week" item
  matched to the freshest real card, real `prioritize_plan_items()` call
  selected 4 entries, fed directly into `format_plan()`. Real rendered
  output: the `new_item` entry at #1 with its own title/url/reasoning; 3
  real `stale_nudge` entries at #2-4 rendering from their real Trello
  cards (`https://trello.com/c/...` URLs, real card names, e.g. `"create
  claude notes"`, `"jami with ai blog"`), each with real reasoning citing
  actual idle duration (43-73 real days) and a movement-style note
  appended after `" — "` exactly as designed; footer correctly read `"4
  plan items"` (the bounded count, not the full board). Confirms every
  piece — `new_item` rendering, `stale_nudge` rendering from a card,
  `movement_note` concatenation, priority-order rendering, and bounded
  footer counting — works end-to-end against real data, not just mocks.
- **Honest observation from the same live run, not a bug (fixed as a
  follow-up, see below):** `card_movements` was passed as `[]` in this
  particular test (deliberately simplified, no sub-phase 4 data wired up
  for this specific live check) — yet the model still populated
  `movement_note` for the `stale_nudge` entries with plausible-sounding
  staleness text ("Unchanged since last check; no movement in 43
  days..."), inferred from `last_activity` rather than from any real
  cross-week signal, since none was provided.

### Follow-up fix: movement_note fabrication when card_movements is empty (2026-07-19)

The observation above was a real fabrication risk, not cosmetic: this
content ships directly into a real Telegram message Pooja reads as fact.
Added an explicit `CRITICAL` rule to `PRIORITIZE_PROMPT`
(`sunday/nodes/prioritize_plan_items.py`) -- if a card has no entry in the
cross-week movement block, `movement_note` must be `null` or explicitly
say movement status is unavailable, never state or imply a cross-week
change (e.g. "unchanged since last week"). Staleness reasoning from
`last_activity` belongs in `priority_reasoning` instead, phrased as
staleness, not as a movement claim.

**Real evidence:** re-ran the identical live test (real Trello board, real
Anthropic call, `card_movements=[]`) that originally surfaced the
fabrication. Result: all 3 `stale_nudge` entries now return
`movement_note: "Movement status unknown; idle per last_activity."` (or
equivalent) instead of fabricated "unchanged since last week" text.
Explicitly checked all returned `movement_note` values against a list of
fabrication-indicating phrases ("unchanged since", "no movement in",
"still stuck", "since last week/check") -- zero matches. Staleness
reasoning correctly still appears, just relocated to `priority_reasoning`
("idle 35 days per last_activity") rather than fabricated into
`movement_note`.

**Checkpoint complete.** All 7 items across 5 sub-phases (Courses section;
Trello card staleness; `plan_history` namespace; cross-week movement
detection, including the real `Done`-list discovery; the new bounded LLM
prioritization node; assemble_plan rendering) are built, tested, and
backed by real live evidence against the actual Trello board, the actual
Supabase store, and real Anthropic API calls.

