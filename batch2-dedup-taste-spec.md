# Batch 2 — Semantic Dedup + Taste-Similarity Pre-Filter (+ search_web removal)

**Status:** Locked 2026-07-15, revised twice — once after a full ambiguity
audit, once after Claude Code's Section 0 investigation surfaced a real
pre-existing design problem (Section 7). Builds on the Phase 5B/closeout
reconciliation (`phase-5b-spec.md` Section 10). Adds Checkpoint 5 to
`feature_list.json`. Also formally retires `search_web`.

---

## 0. Resolved this session — investigation findings, folded in below

Claude Code investigated the prior version's "confirm before building"
list directly against the real codebase. Findings, and what each changed:

1. **Tag descriptions:** `data/taste_profile.yaml` has no per-tag text —
   only `proposal_filters` and a free-text `notes` field. The real
   per-tag description lives in `discovery/nodes/score.py`'s
   `TASTE_PROFILE` prompt constant (a bulleted topic list, **loosely**
   aligned to the 6 fixed tags, not a clean 1:1 mapping). **Locked:**
   Section 6's bootstrap uses this bulleted source, mapped best-effort to
   the 6 tags — flag any tag with no clearly corresponding bullet rather
   than guessing silently.
2. **Ad-hoc queue:** confirmed correct as built — namespace
   `("weekly_intel", "adhoc_queue")`, one `RawItem` per queued message, no
   merging. No changes needed.
3. **`state-nodecost-error-field`:** passing, built. No longer blocking.
4. **`item-feedback-logging`:** was `not_started` — now upgraded from
   "nice to have for future eval" to a **hard prerequisite** for the
   entire taste-update mechanism (Section 7). Must be built as part of
   this same batch of work, not deferred.
5. **`smoke_test_phase0.py`:** still hardcodes `assert
   len(final_state["costs"]) == 3` — still stale, still needs fixing
   against the real current node count (Section 11).
6. **Real `weekly_intel` namespaces in production:** `polling_state`,
   `pending_resume_map`, `adhoc_queue`, `digest_item_map`,
   `feedback_events`, `seen_items` — plus this checkpoint's new
   `recent_item_embeddings` and `taste_topic_vectors`. **`rejection_events`
   is orphaned** — written only by a manual test script
   (`scripts/test_update_profile_rejections.py`), no production code
   writes it, looks like Part-7-era dead code from before `feedback_events`
   consolidated everything. **Locked: leave it alone, document as known-
   dead in the registry (Section 11), don't delete something not fully
   understood yet.** `prefilter_drops` and `same_day_adjustments` (this
   checkpoint's own new namespaces) do not exist yet — build per Sections
   7–8.

**Also resolved this session, all confirmed:**
- Embedding provider: **local `sentence-transformers`, `all-MiniLM-L6-v2` — final, see Section 3.** (Gemini was tried and abandoned this session after an unresolved multi-hour quota issue; history preserved in Section 3.)
- `blog_sources.yaml` has **12 entries, confirmed intentional** —
  ByteByteGo and Daily Dose of Data Science were deliberately removed.
  Every reference to "14 sources" elsewhere in this spec or
  `phase-5b-spec.md` is now stale and should read 12.
- Bucket reassignments **confirmed intentional**: Latent Space,
  MarkTechPost, and The New Stack (AI) are daily-bucket, not Sunday-only.
- The uncapped per-reply profile rewrite currently live in
  `approval_actions.py` (see Section 7) is **not being treated as an
  emergency** — fixed as part of this same batch, no separate urgent
  patch first.

---

## 1. Why the dedup work exists

Cross-source duplicate content is no longer theoretical: `scrape_blogs`
now pulls from 12 real feeds simultaneously (post-reconciliation), so the
same underlying story covered by two different sources under two
different URLs is a live risk starting with the very next real run.
Existing dedup (`seen_items`, URL keyed) cannot catch this, by design —
different URL, same story, sails straight through.

**Scope correction (stated explicitly):** this is not a within-one-run
problem only. A daily-bucket source could cover a story one day; another
source (daily or Sunday-only) could cover the same story days later,
under a different URL, on a different invocation. The dedup window needs
to span a short rolling period across runs, not just the current run's
batch.

## 2. Two (now three) separate mechanisms — do not conflate

- **Cross-source semantic dedup (Section 4)** compares the *meaning* of
  article text — embeddings of title+summary — to detect "are these two
  items about the same story." Never touches URLs, keywords, or tags.
- **Taste-similarity pre-filter (Section 5)** compares an item against
  your **tag** vectors — nothing to do with dedup.
- **Taste-profile update mechanism (Section 7)** — a Sunday consolidated
  batch rewrite of the whole profile, plus a fast, capped, same-day
  nudge on top. Distinct from both of the above; reads feedback, not
  discovered content.

These log to shared store namespaces for auditability, which is what
made earlier drafts look like one tangled system — Section 8 splits the
schema so each mechanism's records stay distinguishable.

## 3. Mechanism: embeddings + cosine similarity — validated, not assumed

Checked against real-world practice before locking this in: a production
article-dedup API (Newscatcher) uses exactly this approach commercially —
embed article text, cosine similarity with a high threshold (~0.95) to
catch same-story-different-wording cases that exact title/URL matching
misses (documented to catch only ~10% of real near-duplicates alone). At
this project's scale (dozens of items/day, not millions), plain pairwise
cosine comparison against a small rolling window is fully sufficient.

**Embedding provider: `sentence-transformers`, model `all-MiniLM-L6-v2`,
run locally — final decision.** History, stated plainly so this doesn't
get re-litigated: Voyage AI was the original choice, replaced by
Gemini's `gemini-embedding-001` for a better free-tier shape. Gemini was
then abandoned after a multi-hour live debugging session — confirmed
real key, confirmed correct project, confirmed correct key format
(Google's `AIza`→`AQ.` migration was investigated and ruled out as the
cause) — and the project's very first API request still returned `429`,
unresolved. Cohere (1,000 calls/month free cap, likely exceeded within
weeks at this project's real volume) and HuggingFace's Inference
Providers (conflicting documented free-credit figures, real reports of
accounts hitting unexpected `402` errors on steady low usage) were both
considered next and rejected before implementation, for the same
underlying risk category that just cost hours with Gemini.

**Local, not API-based, is now the deliberate choice, not the rejected
option it was earlier in this spec's history.** No API key, no account,
no billing tier, no credit balance that can silently run out or
misconfigure — the entire category of problem that blocked this
checkpoint for hours does not exist for a local model. Same model the
project's original reference script used.

**Known, accepted tradeoffs:**
- Model quality is lower-tier than Gemini/Voyage/Cohere on standard
  benchmarks — accepted, since both use cases here (cross-source dedup,
  taste pre-filter) are coarse comparisons, not high-precision search.
- `torch` is a hard dependency (~503 MB for the default CUDA build,
  confirmed; meaningfully smaller but not independently verified for the
  CPU-only build). **Required mitigation:** pin the CPU-only `torch`
  index explicitly in `requirements.txt`, don't let pip resolve the
  default CUDA build.
- Model weights (~80-90MB) also download fresh each run, a separate cost
  from the `pip install` itself. **Required mitigation:** cache **both**
  the pip package cache and the HuggingFace model-weights cache
  directory across GitHub Actions runs — caching only one leaves the
  other cost unaddressed.

**No new secret required.** Remove any `GEMINI_API_KEY`,
`VOYAGE_API_KEY`, or `GOOGLE_STUDIO_KEY` references remaining in code or
GitHub Secrets — none needed going forward.

**Blast-radius check, done:** `semantic_dedup.py`, `taste_vectors.py`,
and `embeddings.py` have no hardcoded vector-length coupling —
`cosine_similarity` uses `zip()` over arbitrary-length lists, store
schemas hold `embedding_vector` as an opaque `list[float]`, and all
existing tests mock `embed_text`/`embed_texts` directly. **This is an
isolated swap**: `embeddings.py`'s client/model/env-var-name, and
`requirements.txt`'s embedding-provider dependency. `all-MiniLM-L6-v2`
produces 384-dimension vectors (different from both Voyage's and
Gemini's) — re-confirm nothing downstream assumed a specific dimension
before treating this swap as complete.

## 4. Cross-source/cross-run semantic dedup

- **Store namespace:** `("weekly_intel", "recent_item_embeddings")`,
  `{item_id, url, embedding_vector, fetched_at, scored_at}`, **7-day
  rolling expiry**.
- Runs **after** `cluster_dedupe_node`'s existing URL-heuristic check,
  **before** `score_node`: embed each surviving item's `title + text`
  (existing `RawItem.text` field, no new fetching), compare cosine
  similarity against every embedding in the rolling window.
- **Threshold: 0.90** (slightly more lenient than Newscatcher's 0.95 —
  this project's items are AI/tech content with naturally higher
  baseline topical overlap than general news; false negatives here are
  worse — a duplicate slipping through costs one wasted `score_node`
  call; a real story wrongly flagged as duplicate is invisible and
  unrecoverable).
- **Tie-breaker on a match: keep whichever item was published earlier**
  (compare `fetched_at`/pubDate) — not text length, which measures
  verbosity, not quality. The earlier item is more likely the original
  reporting; a later item on the same story is more likely a derivative
  summary.
- Dropped item logged per Section 8.

## 5. Taste-similarity pre-filter

- **Not a single vector for the whole taste profile** — the real profile
  has distinct facets (the fixed tag vocabulary); averaging them blurs
  distinct interests together.
- **Multi-vector: one embedding per tag.** Compare each item against all
  topic vectors, take the **max** similarity, not average.
- **Permissive pre-filter before `score_node`, not a replacement for it.**
  Threshold deliberately low: **0.3** — a false negative (filtering out
  something good before Haiku sees it) is worse than a false positive
  (mediocre item reaches `score_node`, scored "drop" there, trivial extra
  cost). Tune upward only with real evidence, never tighten preemptively.

## 6. Bootstrapping Day-1 topic vectors

Bootstrap each tag's initial vector from `score.py`'s `TASTE_PROFILE`
prompt constant (per Section 0, item 1) — a bulleted topic list, mapped
best-effort to the 6 fixed tags. Where a tag has no clearly corresponding
bullet, flag it rather than inventing text to embed. This closes the
cold-start gap without inventing new profile content — the same profile,
just given an embedding representation from day one instead of starting
empty.

## 7. Taste-profile update mechanism — restoring batch cadence, then adding the capped same-day nudge

**What was actually discovered, not assumed:** `approval_actions.py`
currently performs a **full, uncapped, immediate Haiku rewrite of the
entire `taste_profile.yaml` on every single reply** — not just Sunday,
not just proposal approvals, every daily-digest reply too. This is
pre-existing Part-7-era behavior, not something built this checkpoint,
and it was silently running the whole time this project was designed
around the assumption that the profile only updates weekly.

**Why this needs fixing, not just accepting:** a single noisy reply on
an off day currently triggers a full, permanent rewrite of the *entire*
profile — real overfitting risk from one data point, exactly the
instability this project has otherwise been careful to avoid. Confirmed
locked direction (Pooja's call): **restore batch cadence.** Sunday reads
every feedback record accumulated since the previous Sunday — each
still linked to which specific item it replied to — and does **one
consolidated rewrite** considering the whole week's pattern together
(e.g. "three positive reactions to memory-systems content, one negative
reaction to a distributed-systems item that looks like an outlier" can
be weighed in context, rather than each reply blindly rewriting the
profile in isolation with no view of anything else that week).

**Concretely, three things must change, not just `approval_actions.py`'s
trigger:**

1. **`item-feedback-logging` must actually get built.** On a reply,
   `approval_actions.py` logs a discrete record — `{item_id,
   feedback_text, replied_at, run_id}` — to `feedback_events` (the real
   existing namespace, confirmed by Section 0, item 6) and **stops
   there**. No Haiku call, no YAML touch, same-day.
2. **Sunday's consolidated rewrite needs a real home — confirm before
   building, don't guess:** `update_profile.py` is confirmed gutted to
   only cost-log CSV writing now (Section 0). Ask Claude Code to propose,
   given current file layout: restore real rewrite logic into
   `update_profile.py`, or add Sunday-gating directly to
   `approval_actions.py` so it only performs the full rewrite when
   invoked from the Sunday path. Either is acceptable — this is an
   implementation-layout question, not a design question requiring
   Pooja's input, but it must be answered explicitly before building,
   same discipline as everything else in this spec.
3. **The Sunday rewrite reads every `feedback_events` record since the
   last Sunday run**, joined against each item's original source/content,
   and produces one new `taste_profile.yaml` via a single consolidated
   LLM call — not one call per reply.

**Same-day nudge, unchanged in design, now correctly built on top of a
batch foundation instead of one that was silently doing something
riskier underneath it:** a Haiku call reads each new `feedback_events`
entry's `feedback_text` and classifies it into a direction
(`up`/`down`/`neutral`) and magnitude tier (`mild`/`moderate`/`strong`),
mapped to fixed values (+0.05/+0.10/+0.20). The tag(s) affected are
already known from the item's existing `score_node` tags — not
re-derived. **Stacking, capped:** multiple reactions on the same tag in
one week sum together, capped at **±0.3** total, regardless of how many
reactions occur. **Expiry:** cleared at the start of each Sunday
consolidated rewrite, once the full week's feedback has been properly
absorbed. This reads the *same* `feedback_events` log as the Sunday
batch process — one log, two consumers (fast capped nudge, thorough
weekly pass) — no duplicate logging needed.

**Store namespace:** `("weekly_intel", "same_day_adjustments")`,
`{tag, cumulative_adjustment, item_ids_contributing: [...], week_of}`.

**Urgency, per Pooja's call:** not treated as an emergency patch — fixed
as part of this same batch of work, not a separate urgent first step.

## 8. Filtered-item audit logging — two fields, not one overloaded field

Both thresholds above are explicitly "starting points... needs
real-world tuning" — tuning requires seeing what got filtered and why.
As built, a dropped item leaves zero trace.

**Store namespace:** `("weekly_intel", "prefilter_drops")`,
`{item_id, filter_type: "dedup"|"taste", similarity_score,
compared_against_item_id: str | None, compared_against_tag: str | None,
run_id}` — two separate optional fields, not one overloaded field: a
dedup drop populates `compared_against_item_id` and leaves
`compared_against_tag` null; a taste drop does the reverse.

## 9. search_web — retired

`blog_sources.yaml`'s 12 live-verified sources (confirmed count, Section
0) now cover the ground `search_web` was meant to, at better signal
quality and lower cost.

- Delete `discovery/nodes/search_web.py` and
  `discovery/parsers/search_web.py` entirely.
- Remove `search-web-node` from `feature_list.json` entirely.
- Remove any dangling references in `route_sources`, `CLAUDE.md`, or
  `phase-5b-spec.md` — including stale "14 sources" language, now 12.

## 10. Ad-hoc input — bypasses both new filters (confirmed correct as built)

`process_adhoc_input`'s output skips both the dedup check (Section 4)
and the taste pre-filter (Section 5) entirely, going straight to
`classify_item`. Confirmed by Section 0, item 2: the queue mechanism
itself (`adhoc_queue` namespace, one `RawItem` per message, no merging)
is already correctly built.

**Reasoning, unchanged:** both filters exist to cut cost/noise on
*discovered* content. An ad-hoc item is something Pooja personally chose
to text the bot about — maximally relevant and opted-in by construction.
An ad-hoc item's embedding is never computed or stored at all — zero
added embedding cost for this path, zero risk of it becoming a future
dedup target.

## 11. Test and documentation debt — fixed, not just flagged

- **`smoke_test_phase0.py`** gets updated to reflect the actual current
  node count — still stale as of Section 0's investigation.
- **New store-namespace registry**, added to `WORKFLOW.md` alongside its
  existing file-by-file reference. Real current list (Section 0, item 6):
  `polling_state`, `pending_resume_map`, `adhoc_queue`, `digest_item_map`,
  `feedback_events`, `seen_items`, `recent_item_embeddings` (new),
  `taste_topic_vectors` (new), `prefilter_drops` (new, this checkpoint),
  `same_day_adjustments` (new, this checkpoint). **`rejection_events`
  documented as known-dead** — orphaned since `feedback_events`
  consolidated everything, written only by a manual test script, left
  alone rather than deleted without full understanding of why it's still
  there.

## 12. Ownership

| File | Owner | Why |
|---|---|---|
| Embedding generation (dedup + taste vectors + bootstrap + same-day sentiment call) | 🔵 Pooja | Direct model-call logic, same class as `score_node` |
| `cluster_dedupe_node` extension (cosine comparison, tie-breaker) | 🟡 Mixed — Claude Code: comparison mechanics; Pooja: threshold/tie-break calls | Touches existing LangGraph state |
| Store writes/reads (`recent_item_embeddings`, `prefilter_drops`, `same_day_adjustments`, `feedback_events` logging) | 🟡 Mixed — same split | Plain `store.put()`/`get()`, embedded in a node Pooja owns |
| Sunday consolidated rewrite (home TBD per Section 7, item 2) + same-day nudge logic | 🔵 Pooja | Direct Claude API call, profile-affecting logic |
| `approval_actions.py`'s reply-time behavior (log only, no rewrite) | 🔵 Pooja | Already her file, LangGraph-adjacent |
| `smoke_test_phase0.py` fix, store-namespace registry doc | 🟢 Claude Code | Pure documentation/test hygiene, no judgment call |
| search_web deletion, feature_list.json cleanup | 🟢 Claude Code | Pure removal, no judgment call |

## 13. Non-functional requirements

- **Cost:** embedding calls cheap relative to Haiku; net effect is cost
  reduction (fewer unnecessary `score_node` calls). Local
  `sentence-transformers` means $0 embedding cost regardless of volume —
  the real cost is CI weight (Section 3's `torch`/caching mitigations),
  not per-call spend. The sentiment-classification call and the
  Sunday consolidated rewrite are both small additional Haiku costs,
  same class as existing `score_node`/`classify_item` calls — and this
  is actually a cost *reduction* versus the current uncapped
  every-single-reply rewrite, not an addition.
- **Reliability:** a failed embedding or sentiment call degrades
  gracefully — let the item through / skip the adjustment rather than
  silently failing.
- **Maintainability:** 7-day expiry on `recent_item_embeddings`, weekly
  clearing of `same_day_adjustments`, and the namespace registry
  (Section 11) keep this bounded and visible.

## 14. Summary: locked vs. still open

**Locked:** cosine-similarity semantic dedup, 7-day rolling window, 0.90
threshold, earliest-published tie-breaker; multi-vector per-tag taste
pre-filter, 0.30 threshold, max-similarity; Day-1 bootstrap from
`score.py`'s `TASTE_PROFILE` constant; embedding provider **local
`sentence-transformers` (`all-MiniLM-L6-v2`)** — final, after Voyage,
Gemini, Cohere, and HuggingFace were all tried or evaluated and rejected
this session, isolated swap, CPU-only `torch` index + dual caching
(pip + model weights) required as mitigation;
`blog_sources.yaml` confirmed correct at 12 entries with three
intentional daily-bucket reassignments; taste-profile updates restored
to Sunday batch cadence (consolidated rewrite over the week's linked
feedback) with a capped (±0.3), LLM-derived, symmetric same-day nudge on
top, not urgent to patch separately; `search_web` retired; ad-hoc
confirmed already correctly bypassing both filters; two-field audit
logging; `rejection_events` documented as known-dead, left alone.

**Still open:** only Section 7, item 2 (where Sunday's consolidated
rewrite logic should live) — a real implementation-layout question for
Claude Code to propose and confirm before building, not a design
decision requiring further input from Pooja.