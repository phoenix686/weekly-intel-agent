# Batch 2 — Semantic Dedup + Taste-Similarity Pre-Filter (+ search_web removal)

**Status:** Locked 2026-07-15, revised after a full ambiguity audit. Builds
on the Phase 5B/closeout reconciliation (`phase-5b-spec.md` Section 10).
Adds Checkpoint 5 to `feature_list.json`. Also formally retires
`search_web`.

**Process note:** this revision exists because earlier passes surfaced
open questions one at a time across many messages instead of all at once.
Section 0 below is deliberately structured as "answer these first" —
Claude Code should resolve every item there before building anything, not
discover gaps mid-build the way this spec's own history did.

---

## 0. Confirm before building — answer all of these first, in writing, before touching code

1. **What text describes each tag in the existing taste profile?**
   `score_node`'s prompt (or the YAML preference file) presumably has
   some description per tag (`agentic-engineering`, `memory-systems`,
   `llm-tooling`, `evals`, `learning-resource`, `distributed-systems`).
   Report the exact source text for each — this is what gets embedded
   to bootstrap Day-1 topic vectors (Section 6), rather than starting
   the taste pre-filter with nothing to compare against.
2. **Ad-hoc queue mechanism:** what store namespace holds a queued
   ad-hoc message between the daily poll picking it up and Sunday's
   `process_adhoc_input` consuming it, and what happens if more than one
   arrives in the same week? Required behavior: each queued message
   produces its own separate `RawItem` — no merging, no "most recent
   only." Confirm the real behavior against the actual code; if it
   doesn't match, that's a bug to fix, not new scope.
3. **`state-nodecost-error-field` (Checkpoint 1) status.** Last checked,
   still `not_started`. This blocks graceful-degradation logging for
   Checkpoint 5 (and, previously, for `scrape_blogs` and Checkpoint 4)
   from being more than mocked. This is a blocking prerequisite that
   predates this checkpoint — build it first if it's still missing,
   don't work around its absence again.
4. **`item-feedback-logging` (Checkpoint 4) status.** Confirm whether
   daily-digest replies already write a real discrete record, or
   whether daily feedback currently goes nowhere. Section 7's same-day
   mechanism assumes that landing spot exists.
5. **Current shape of `smoke_test_phase0.py`.** It asserts exactly 3
   cost records — almost certainly wrong now given how many nodes exist.
   Report what it currently checks so it can be corrected accurately
   (Section 9), not just patched blind.
6. **Full current list of `weekly_intel` store namespaces in actual use**
   — report every one that exists in the real code right now, so
   Section 10's registry reflects reality rather than this spec's
   assumptions about what's been built.

---

## 1. Why this exists

Cross-source duplicate content is no longer theoretical: `scrape_blogs`
now pulls from 14 real feeds simultaneously (post-reconciliation), so the
same underlying story (e.g. a model release) covered by two different
sources under two different URLs is a live risk starting with the very
next real run. Existing dedup (`seen_items`, URL keyed) cannot catch
this, by design — different URL, same story, sails straight through.

**Scope correction (stated explicitly):** this is not a within-one-run
problem only. TLDR AI (daily bucket) could cover a story Wednesday;
MarkTechPost (also daily) or a Sunday-only source could cover the same
story days later, under a different URL, on a different invocation. The
dedup window needs to span a short rolling period across runs, not just
the current run's batch.

## 2. Two separate mechanisms — do not conflate

Worth stating plainly, since these work on entirely different bases and
share nothing except both running before `score_node`:

- **Cross-source semantic dedup (Section 4)** compares the *meaning* of
  article text — embeddings of title+summary — to detect "are these two
  items about the same story." Never touches URLs, keywords, or tags.
- **Taste-similarity pre-filter (Section 5)** compares an item against
  your **tag** vectors (`memory-systems`, `evals`, etc.) — nothing to do
  with dedup.
- **Same-day feedback adjustment (Section 7)** only touches the taste
  side. A dislike never affects dedup; dedup never looks at tags.

They log to a shared store namespace for auditability, which is the only
thing that made them look like one overloaded system — see Section 8 for
why that schema is now split into two distinct fields.

## 3. Mechanism: embeddings + cosine similarity — validated, not assumed

Checked against real-world practice before locking this in: a production
article-dedup API (Newscatcher) uses exactly this approach commercially —
embed article text, cosine similarity with a high threshold (~0.95) to
catch same-story-different-wording cases that exact title/URL matching
misses (documented to catch only ~10% of real near-duplicates alone). At
this project's scale (dozens of items/day, not millions), plain pairwise
cosine comparison against a small rolling window is fully sufficient —
no clustering/ANN infrastructure needed.

**Embedding provider: `gemini-embedding-001` via Google AI Studio** (the
Gemini Developer API — not Vertex AI, which has no equivalent free path
and uses a different, enterprise-oriented auth model). Free tier: 10
million tokens/minute, **recurring, not a depleting lifetime cap** —
genuinely free at this project's volume, indefinitely, as long as
billing stays off the backing Google Cloud project (enabling billing for
any reason kills the free tier project-wide, permanently, not just for
whatever triggered it — use a dedicated, billing-disabled project). New
secret: `GEMINI_API_KEY`. Known tradeoff: Google's free-tier terms permit
using inputs to improve their models (human review possible) — low
stakes for this content, stated plainly rather than hidden. API-based,
not local, for the same reason considered earlier: avoids
`sentence-transformers`' hard `torch` dependency, a documented,
unresolved CI/container-size pain point that would weigh down every
scheduled GitHub Actions run.

## 4. Cross-source/cross-run semantic dedup

- **Store namespace:** `("weekly_intel", "recent_item_embeddings")`,
  `{item_id, url, embedding_vector, fetched_at, scored_at}`, **7-day
  rolling expiry** (an item past that is stale enough that "duplicate"
  stops being meaningfully redundant — don't let this grow unbounded).
- Runs **after** `cluster_dedupe_node`'s existing URL-heuristic check,
  **before** `score_node`: embed each surviving item's `title + text`
  (existing `RawItem.text` field, no new fetching), compare cosine
  similarity against every embedding in the rolling window.
- **Threshold: 0.90** (slightly more lenient than Newscatcher's 0.95 —
  this project's items are AI/tech content with naturally higher
  baseline topical overlap than general news; a stricter threshold risks
  false negatives, which are worse here — a duplicate slipping through
  costs one wasted `score_node` call, cheap and recoverable; a real story
  wrongly flagged as duplicate is invisible and unrecoverable).
- **Tie-breaker on a match: keep whichever item was published earlier**
  (compare `fetched_at`/pubDate). Not text length — verbosity isn't
  quality, and a padded rewrite could beat a tight original purely by
  being wordier. The earlier item is more likely the original reporting;
  a later item on the same story is more likely a derivative summary.
- Dropped item logged per Section 8.

## 5. Taste-similarity pre-filter

- **Not a single vector for the whole taste profile.** The real profile
  has distinct facets (the fixed tag vocabulary) — averaging them into
  one vector blurs distinct interests together.
- **Multi-vector: one embedding per tag.** Compare each item against all
  topic vectors, take the **max** similarity, not average — an item
  strongly matching one topic shouldn't be dragged down by irrelevance
  to the rest.
- **Permissive pre-filter before `score_node`, not a replacement for it.**
  Cuts Haiku calls on near-zero-relevance items only. Threshold
  deliberately low: **0.3** — a false negative (filtering out something
  good before Haiku sees it) is worse than a false positive (mediocre
  item reaches `score_node`, scored "drop" there, trivial extra cost).
  Tune upward only with real evidence, never tighten preemptively.

## 6. Bootstrapping Day-1 topic vectors — no cold start

Rather than starting the taste pre-filter with nothing to compare
against on its first run, bootstrap each tag's initial vector from the
existing taste-profile description already used elsewhere in the
pipeline (exact source text: Section 0, item 1 — confirm before
assuming). This closes the cold-start gap simply, without inventing new
profile content — it's the same profile, just given an embedding
representation from day one instead of waiting for the first real
`update_profile` run to create one from nothing.

## 7. Same-day feedback adjustment — symmetric, LLM-derived, capped

**Problem:** `update_profile` only runs Sunday, so any single day's
feedback sits inert until the following Sunday — unlike Hermes, which
corrects conversationally in real time because it's a live agent. A
clear negative reaction Monday could mean similar mismatched content
keeps surfacing all week with nothing correcting it.

**Mechanism — feedback is language, not a pre-labeled direction:** when
daily feedback is logged (`item-feedback-logging`), a Haiku call reads
the actual `feedback_text` and classifies it into a direction
(`up`/`down`/`neutral`) and a magnitude tier (`mild`/`moderate`/
`strong`), mapped to fixed values (+0.05/+0.10/+0.20, negative for
`down`). The call does **not** guess which tag(s) are affected — those
are already known from the item's existing `score_node` tags, just
looked up.

**Stacking, capped:** multiple reactions on the same tag in one week sum
together, but the **running total is capped at ±0.3** regardless of how
many reactions occur — no single volatile week can swing a topic's
matching further than that bound, no matter how many things get reacted
to.

**Expiry:** cleared at the start of each Sunday `update_profile` run,
once the full week's feedback has been properly absorbed into a real
YAML regen and topic-vector recompute (Section 6's mechanism, run
again). The same-day nudge is a fast, cheap, bounded patch on top of the
slow, thorough weekly pass — never a replacement for it.

**Store namespace:** `("weekly_intel", "same_day_adjustments")`,
`{tag, cumulative_adjustment, item_ids_contributing: [...], week_of}`.

## 8. Filtered-item audit logging — two fields, not one overloaded field

Both thresholds above are explicitly "starting points... needs
real-world tuning" — tuning requires seeing what got filtered and why.
As built, a dropped item leaves zero trace — same blind spot Checkpoint
4's eval-logging closed for `classify_item`, recurring here.

**Store namespace:** `("weekly_intel", "prefilter_drops")`,
`{item_id, filter_type: "dedup"|"taste", similarity_score,
compared_against_item_id: str | None, compared_against_tag: str | None,
run_id}` — **two separate optional fields, not one overloaded field**:
a dedup drop populates `compared_against_item_id` and leaves
`compared_against_tag` null; a taste drop does the reverse. Splitting
this avoids exactly the "one field, two unrelated meanings" confusion
that made these two independent mechanisms (Section 2) look like one
tangled system.

## 9. search_web — retired

`blog_sources.yaml`'s 14 live-verified sources now cover the ground
`search_web` was meant to, at better signal quality and lower cost.

- Delete `discovery/nodes/search_web.py` and
  `discovery/parsers/search_web.py` entirely.
- Remove `search-web-node` from `feature_list.json` entirely — not
  deferred, not failed, just no-longer-applicable.
- Remove any dangling references in `route_sources`, `CLAUDE.md`, or
  `phase-5b-spec.md`.

## 10. Ad-hoc input — bypasses both new filters entirely

`process_adhoc_input`'s output skips both the dedup check (Section 4)
and the taste pre-filter (Section 5), going straight to `classify_item`.

**Reasoning:** both filters exist to cut cost/noise on *discovered*
content. An ad-hoc item is the opposite — something Pooja personally
chose to text the bot about, maximally relevant and opted-in by
construction. Running it through noise filters is backwards, and risks
a real failure in both directions: it could get wrongly dropped as a
"duplicate" of scraped content, or its embedding could cause a later
real article to get wrongly dropped as "duplicating" it. **An ad-hoc
item's embedding is never computed or stored at all** — zero added
embedding cost for this path, and zero risk of it becoming a future
dedup target.

**Implementation:** skip both checks and skip embedding computation
entirely when `RawItem.source == "adhoc"` — a source-based bypass, not a
duplicated code path.

## 11. Test and documentation debt — fixed, not just flagged

- **`smoke_test_phase0.py`** gets updated to reflect the actual current
  node count (Section 0, item 5, for the real current assertion to
  correct against) — not left stale.
- **New store-namespace registry**, added to `WORKFLOW.md` alongside its
  existing file-by-file reference: one entry per `weekly_intel`
  namespace (real list per Section 0, item 6), documenting shape and
  which node reads/writes it. This is the same kind of tracking gap that
  let Part 7's files go unreconciled — closing it here so it doesn't
  recur as more namespaces get added later.

## 12. Ownership

| File | Owner | Why |
|---|---|---|
| Embedding generation (dedup + taste vectors + bootstrap + same-day sentiment call) | 🔵 Pooja | Direct model-call logic, same class as `score_node` |
| `cluster_dedupe_node` extension (cosine comparison, tie-breaker) | 🟡 Mixed — Claude Code: comparison mechanics; Pooja: threshold/tie-break calls | Touches existing LangGraph state |
| Store writes/reads (`recent_item_embeddings`, `prefilter_drops`, `same_day_adjustments`) | 🟡 Mixed — same split | Plain `store.put()`/`get()`, embedded in a node Pooja owns |
| `update_profile` topic-vector recompute + bootstrap | 🔵 Pooja | Already her file |
| `smoke_test_phase0.py` fix, store-namespace registry doc | 🟢 Claude Code | Pure documentation/test hygiene, no judgment call |
| search_web deletion, feature_list.json cleanup | 🟢 Claude Code | Pure removal, no judgment call |

## 13. Non-functional requirements

- **Cost:** embedding calls cheap relative to Haiku; net effect is cost
  reduction (fewer unnecessary `score_node` calls). Gemini's free tier
  means $0 at current volume. The new sentiment-classification call
  (Section 7) is a small additional Haiku cost, same class as existing
  `score_node`/`classify_item` calls — not free, but trivial at this
  project's feedback volume.
- **Reliability:** a failed embedding or sentiment call degrades
  gracefully — let the item through / skip the adjustment rather than
  silently failing, consistent with Section 5's false-negative-is-worse
  principle.
- **Maintainability:** 7-day expiry on `recent_item_embeddings`, weekly
  clearing of `same_day_adjustments`, and the new namespace registry
  (Section 11) keep this bounded and visible going forward.

## 14. Summary: locked vs. still open

**Locked:** cosine-similarity semantic dedup, 7-day rolling window, 0.90
threshold, earliest-published tie-breaker; multi-vector per-tag taste
pre-filter, 0.30 threshold, max-similarity; Day-1 bootstrap from the
existing taste profile; symmetric, LLM-derived, capped (±0.3) same-day
feedback adjustment; embedding provider Gemini `gemini-embedding-001` via
AI Studio; `search_web` retired; ad-hoc bypasses both filters entirely;
two-field (not overloaded) audit logging; `smoke_test_phase0.py` and the
namespace registry are real fixes, not flags.

**Still open:** only what Section 0 hasn't been answered yet — this
spec's design is complete pending those six confirmations, not pending
further design decisions.
