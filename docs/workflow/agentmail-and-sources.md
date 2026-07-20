[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: AgentMail integration + source cleanup (2026-07-18 to 07-19)

## seen_items rolling 35-day expiry + poll.yml schedule change (2026-07-18)

### 1. seen_items expiry
Same pattern as `recent_item_embeddings`' 7-day window
(`discovery/semantic_dedup.py`'s `_WINDOW_DAYS`): every source only ever
fetches its 5-15 most recent items, so an entry older than the window is
provably unreachable again -- dead weight, not real dedup coverage.
`mark_seen()` now writes a `seen_at` timestamp alongside `seen`.
`filter_unseen()` runs a lazy sweep (`_expire_stale_entries()`) once per
call -- one `store.search()` + one batched `store.batch()` of deletes
(`store.delete()` is itself just `PutOp(namespace, key, None)` under the
hood, per `langgraph.store.base`, so N deletes batch into one round trip
the same way N writes do). Window set to 35 days -- top half of the
30-45 day range this was scoped to, giving real buffer for the slowest
sunday-bucket/weekly-cadence sources (`fetch_limit=6`).

Deliberate choice, not the literal ask: an entry with no `seen_at` at all
(everything written before today) is treated as **not yet eligible for
expiry**, not as already-expired. There's no real signal for how old
those 422 real entries actually are, and deleting them on a guess would
repeat the exact mistake flagged earlier tonight (bulk-deleting real
cross-run dedup history with no way to verify what's actually safe to
remove). `scripts/backfill_seen_items_timestamp.py` backfills every
pre-existing entry with today's date once; the window then applies
honestly going forward.

REAL LIVE VERIFICATION: queried the live store directly before and after
the backfill -- **422 entries before, 422 after** (count unchanged,
nothing deleted), all 422 now carry a real `seen_at`. `tests/
test_seen_items.py` gained 2 new tests (11 total) covering the sweep
itself: a genuinely stale entry (40 days old) gets deleted and correctly
reappears as unseen; an entry with no `seen_at` is left alone. Full test
suite: 150 passed, 1 skipped, zero regressions.

### 2. poll.yml schedule change
Checked git history first: no stated reason was ever recorded for the
original 08:30 IST time, just carried over from whenever the workflow
was first written -- confirmed via `git log --follow -p`, not assumed.
Changed `poll.yml`'s cron from `0 3 * * *` (03:00 UTC = 08:30 IST) to
`30 16 * * *` (16:30 UTC = 22:00 IST), all 7 days unchanged.
`daily.yml`'s cron was already exactly `30 2 * * 1-6` (02:30 UTC = 08:00
IST, Mon-Sat) -- confirmed matching the requested value with zero diff,
including the Sunday-exclusion logic (daily-bucket sources are already
covered by Sunday's own run that day) still intact. Both YAML files
validated via `yaml.safe_load`.

## Hacker News (Show HN) re-added + new-tool-launch tag (2026-07-18)

Re-added via `hnrss.org/show`, sunday bucket, `fetch_limit=8` (the one
deliberate exception to the sunday-bucket default of 6, since Show HN's
volume is higher than the newsletter sources). Deliberately no keyword
query filter on the fetch itself (hnrss.org supports one, e.g. `?q=agent`)
-- using it would reintroduce the exact keyword-matching brittleness this
project moved away from; every other source relies on semantic scoring,
not a narrowed fetch, to find the signal in a noisy stream (MarkTechPost's
higher daily volume is handled the same way).

Added a new real tag, `new-tool-launch`, to `score.py`'s `ALLOWED_TAGS` --
flagged to Pooja first (via AskUserQuestion) since it wasn't in the
existing tag set and adding it is a real schema change (flows into
`taste_vectors.py`'s `TOPIC_TAGS` derivation and `_TAG_TO_BULLET` mapping
automatically), not a one-line prompt tweak. Decided: add it as a real
tag, mapped to a real bullet ("New AI tool, framework, API, or agent
project launches") rather than left unmapped like `learning-resource` --
unlike that tag, this one represents a genuine, coherent interest area
the taste pre-filter can anchor to.

`TASTE_PROFILE` gained an HN-specific tagging instruction: only assign
`new-tool-launch` for genuine AI/agent tool, framework, API, or project
launches, identified via a "Show HN:" title prefix -- NOT a
news.ycombinator.com URL as first drafted. REAL finding from a live fetch
that caught this before it shipped wrong: `hnrss.org/show`'s own `url`
field is the submitter's external link (GitHub repo, personal site, etc),
never HN's own domain -- the title prefix is the only reliable signal
that survives into the scoring prompt. Fixed before any real evidence was
gathered against the wrong assumption.

REAL EVIDENCE: live fetch of `hnrss.org/show` (zero errors, 8 real rows).
Ran those 8 real items through a real `score_node` call (Claude Haiku,
`mark_seen`/`record_node_summary` mocked out so the test fetch doesn't
pollute the live store) --
- "Show HN: Google Search Console MCP" -> kept, tagged
  `['agentic-engineering', 'new-tool-launch']` -- correct, genuine agent
  tooling launch.
- 5 unrelated Show HN posts (a desktop-icon customizer, an IKEA
  complexity visualization, a domain-name finder, an orbit-transfer
  simulator, a React Native puzzle game) -> all dropped as `noise`, none
  tagged `new-tool-launch`.
- 2 borderline AI-adjacent items (a model-routing benchmark, a
  distributed-systems scenario-testing post) -> kept under other real
  tags (`evals`, `distributed-systems`) but deliberately NOT tagged
  `new-tool-launch` -- confirms the model is applying the tag
  selectively, not indiscriminately to every HN launch that's vaguely
  AI-adjacent.

`tests/test_scrape_blogs_fetch_limit.py`'s sunday-bucket fetch_limit test
updated (9 entries now, HN is the one fetch_limit=8 exception). Full test
suite: 151 passed, 1 skipped, zero regressions (one live-fetch test
transiently failed on the first full-suite run from rate-limiting after
repeated manual fetches during this investigation -- passed cleanly in
isolation and on re-run, not a real code issue).

## AgentMail integration for the 4 blocked Substack sources (2026-07-18) -- SUPERSEDED

**Superseded the same day by the full 10-source consolidation below** --
this section is kept as the real historical record of the first pass
(placeholder inbox_id, hardcoded 4-subdomain HTML parsing that turned out
to be wrong), not the current design. See "AgentMail full consolidation"
further down for what's actually running.

Built, per explicit instruction, to route around the GitHub-Actions-IP
403 block confirmed earlier (JamWithAI, The Nuanced Perspective, AI with
Aish, The Neural Maze) -- not by fetching RSS anymore, but by reading
the newsletters AS EMAIL via a dedicated AgentMail inbox Pooja subscribes
herself, same as subscribing any real email address.

New module `discovery/parsers/agentmail_newsletters.py`. Real AgentMail
API shape (`agentmail>=0.5.8`, added to `requirements.txt`, verified
against the installed package's actual method signatures via direct
`inspect.signature()`, not assumed from docs):
- `client.inboxes.messages.list(inbox_id, labels=["unread"], limit=N)` ->
  metadata only, no body.
- `client.inboxes.messages.get(inbox_id, message_id)` -> full `Message`
  (`.html`/`.text`/`.extracted_html`/`.extracted_text`).
- `client.inboxes.messages.update(inbox_id, message_id,
  add_labels=["read"], remove_labels=["unread"])` -- AgentMail has no
  dedicated mark-read endpoint; labels ARE the read/unread state, same
  conceptual pattern as `discovery/seen_items.py`'s `mark_seen`, but the
  "seen" state lives in AgentMail's own store, not ours.

URL extraction: scans every href in the email HTML for the real,
documented Substack post pattern `https://{subdomain}.substack.com/p/
{slug}`, matched against the 4 known target subdomains -- more robust
than targeting a specific "Read on Substack" button's exact position/CSS
class, which is real template structure this project hasn't observed
directly yet.

Wired into `discovery/parsers/scrape_blogs.py`'s `fetch_one_source()`
dispatch: an `agentmail_inbox_id` entry routes here instead of
`feed_url`/`scrape_url`. `blog_sources.yaml`'s 4 individual feed_url
entries for these sources replaced with ONE combined "AgentMail
Newsletters" entry (`fetch_limit=20` -- one shared inbox covering 4
weekly publications, not one feed each), with the comment block updated
to explicitly flag that this entry uses a different fetch mechanism than
every other entry in the file, so a future session doesn't mistake it
for a feed_url/scrape_url fetch.

**HONEST STATUS -- explicitly NOT the full real-evidence bar yet.**
Verified against a realistic, manually-constructed HTML fixture modeled
on the real, documented Substack `/p/` URL convention (`tests/
test_agentmail_newsletters.py`, 8 new tests, all passing): URL
extraction correctly finds the real post link among several
tracking/unsubscribe hrefs, correctly returns nothing for an unrelated
email, HTML-to-text stripping works, and the full `fetch_agentmail_
newsletters()` flow correctly parses a message into a RawItem-shaped row
and marks it read -- one bad message never blocks the others, an
inbox-level failure (bad key, network error) never raises. This is
**not** the same as real evidence of an actual received email -- no
AgentMail inbox exists yet.

**Stopped here, per explicit instruction and CLAUDE.md's standing
constraint on adding secrets: AGENTMAIL_API_KEY needed.** Once Pooja adds
it and creates a real inbox (subscribing it to the 4 newsletters
herself), `blog_sources.yaml`'s `agentmail_inbox_id: "TODO-fill-in-once-
a-real-inbox-exists"` placeholder needs the real inbox ID, and only then
can the actual required evidence (one real received newsletter parsed
into a real RawItem with a real, clickable destination URL) be produced.
Full test suite: 155 passed, 2 skipped (Anthropic's scrape_url entry and
the new AgentMail entry, neither exercised by the live-HTTP-fetch test
the same way feed_url entries are), zero regressions.

## AgentMail full consolidation: 10 sources, real HTTP redirect resolution, gitignored config (2026-07-18)

Replaces the section above entirely -- AGENTMAIL_API_KEY and a real
inbox now exist (`<REDACTED_AGENTMAIL_ADDRESS>`), so this pass builds
against real data throughout, not a placeholder.

### 1. Real, critical finding that invalidated the original design
The original `_extract_article_url()` parsed raw HTML for a
`{subdomain}.substack.com/p/{slug}` pattern directly. Inspecting a REAL
received welcome email (Ahead of AI, Substack) before trusting that
design: **every single href in a real Substack email is an opaque
click-tracking redirect** (`https://email.mg-d0.substack.com/c/
{encoded-token}`) -- the real destination is never in the raw HTML at
all. Confirmed the same is true for beehiiv ("AI Engineering" 's real
welcome email: `https://link.mail.beehiiv.com/ss/c/{token}`). Verified
the fix directly: resolving one of these tracking links via a real HTTP
request (following redirects) returned a real, different URL
(`https://magazine.sebastianraschka.com/subscribe`) -- proving
resolution is both necessary and sufficient. Rewrote
`_extract_article_url()` to resolve each candidate href via a real HTTP
call (`_resolve_redirect()`, capped at 15 links per message, stops at
first match) and check the RESOLVED url for the shared Substack/beehiiv
`/p/{slug}` convention -- not restricted to a hardcoded subdomain list
anymore, since resolution reveals the real destination domain directly
regardless of which ESP sent the email. This was caught by inspecting
real data BEFORE shipping, not discovered by a later failure.

### 2. Sender-address-to-source-name mapping kept out of git entirely
New `discovery/config/agentmail_sources.yaml` (gitignored -- added to
`.gitignore`, same category as `.env`/`data/`): real `inbox_id` plus all
10 senders' real addresses, confirmed via a live `client.inboxes.list()`
+ `client.inboxes.messages.list()` query against the actual subscribed
inbox, not guessed. Tracked `agentmail_sources.yaml.example` documents
the same shape with placeholders. New loader
`discovery/agentmail_sources_config.py` (mirrors `blog_sources_config.py`'s
pattern) -- raises a clear `FileNotFoundError` if the real file doesn't
exist on a machine (e.g. a fresh clone), which
`fetch_agentmail_sources()` catches and turns into one informative,
non-fatal `SourceResult` rather than crashing the pipeline.

Confirmed via `git log --all -p` before building anything: no real
sender address or inbox ID was ever committed anywhere -- only the
placeholder string `"TODO-fill-in-once-a-real-inbox-exists"` from the
prior (now-superseded) pass. Clean setup, not a cleanup, as expected.

### 3. blog_sources.yaml: 6 sources removed, 6 confirmed untouched
JamWithAI, The Nuanced Perspective, AI with Aish, The Neural Maze (already
gone as of the prior pass), plus Decoding AI Magazine and Ahead of AI
(newly removed this pass) -- all 6 now read via AgentMail instead, not
alongside RSS. Confirmed via a real parsed `load_blog_sources()` call
that the remaining 7 entries are exactly TLDR AI, LangChain Blog, Latent
Space, Anthropic Engineering Blog, MarkTechPost, The New Stack (AI), and
Hacker News (Show HN) (added the same day, prior turn) -- untouched.
AgentMail is no longer represented in `blog_sources.yaml` as an entry at
all (the placeholder `agentmail_inbox_id` entry from the prior pass is
gone) -- one shared inbox covering 10 senders doesn't fit that file's
one-entry-per-fetch model, so `discovery/parsers/scrape_blogs.py`'s new
`fetch_agentmail_sources()` is a separate path `discovery/nodes/
scrape_blogs.py` calls directly, alongside (not through) the
`blog_sources.yaml`-driven loop.

### 4. Read/unread window -- confirmed already correct, no fix needed
`messages.list(inbox_id, labels=["unread"])` was already the mechanism
in the prior pass and still is -- the unread label IS the time boundary;
`add_labels=["read"]` after successful processing means each Sunday run
naturally only sees what's new since the last one, no separate
date-range filter exists or was added.

### 5. Source attribution -- one SourceResult per real sender, not one generic bucket
`fetch_agentmail_sources()` makes ONE shared `messages.list()` call, then
groups the results by each row's real `author_name` (matched against the
sender address via `_match_sender_name()`) into one `SourceResult` per
real publication -- same per-source `NodeCost.error` visibility every
other source gets. An unrecognized sender (anything not in
`agentmail_sources.yaml`) groups under a stable sentinel name
(`"AgentMail Newsletters (unrecognized sender)"`), not silently dropped
or confused with a real source.

### REAL EVIDENCE -- run directly against the live inbox, not simulated
`fetch_agentmail_sources("sunday")` executed for real (2026-07-18) against
the actual subscribed inbox:

| Source | Result |
|---|---|
| Decoding AI Magazine | **1 real row** -- `https://www.decodingai.com/p/ai-engineering-roadmaps`, confirmed live (HTTP 200), message confirmed marked `read` afterward |
| JamWithAI, The Nuanced Perspective, AI with Aish, The Neural Maze, Ahead of AI, The AI Merge (Alex Razvant), DiamantAI, AI Engineering (Sumanth P) | 0 rows, correctly reported "no resolvable article URL found" -- all 8 are genuine welcome/subscription-confirmation emails with no real post link (confirmed by manually resolving every href in two of them), correctly left `unread` for retry once a real issue arrives |
| The Batch | 0 rows, no error -- no message has arrived from this sender yet |
| (unrecognized) | Pooja's own real test email correctly caught and grouped separately, not mistaken for a configured source |

**Honest status**: this is real, live, end-to-end proof the entire
mechanism works (list -> get -> resolve redirects -> extract real URL ->
attribute to the correct real sender -> build a RawItem -> mark read),
not a synthetic fixture. It is NOT yet "one real parsed RawItem per
distinct sender (10 total)" -- 9 of 10 senders have so far only sent a
welcome email, not a real newsletter issue; The AI Merge's real display
name is "Alex Razvant @ The AI Merge", not "The AI Edge" as first
described, corrected to match the real sender. This will close out
naturally as each publication sends its next real weekly issue --
requires real-world time, not something this session can force.

Full test suite after the full rewrite: 156 passed, 1 skipped, zero
regressions. `tests/test_agentmail_newsletters.py` fully rewritten (mocks
`_resolve_redirect`, not raw HTML parsing, since that's what real data
proved is actually needed).

## Final cleanup checkpoint: log verbosity, contamination sweep, trace_run.py, log artifacts (2026-07-19)

### 1. Diagnostic checkpoint logging demoted to DEBUG, not deleted
The 40 real BEFORE/AFTER checkpoint log lines added for the hang
investigation (`connection_pool.py`, `sunday/memory_store_config.py`,
`observability.py`, `discovery/embeddings.py`, `discovery/
semantic_dedup.py`, `discovery/taste_vectors.py`, `discovery/
seen_items.py`) are now `logger.debug`, not `logger.info` -- still there,
just off by default, so the exact tool that found the real hang stays
available if a similar issue ever recurs.

REAL EVIDENCE: captured a real run's log at default (INFO) level before
and after, under real production conditions (`HF_HUB_OFFLINE=1`, matching
what the actual GitHub Actions step sets): **91 lines before -> 15 lines
after**, and the 15 remaining lines are third-party library noise
(`sentence_transformers`'s own 2 internal lines, tqdm progress bars) plus
exactly one real, meaningful line (`taste_vectors: no topic vectors yet,
pre-filter skipped for this run`) -- zero checkpoint noise survives.
Real gap found and flagged, not silently fixed: `discovery/nodes/
scrape_blogs.py` has **zero `logger` calls at all** -- "per-source fetch
counts" only exist as structured `NodeCost` data today, not an actual
INFO log line, despite that being a stated expectation. Full suite: 156
passed, 1 skipped, zero regressions.

### 2. Contamination sweep -- real, substantial finding
Searched every `weekly_intel` namespace that carries a `run_id` concept
for non-UUID-shaped run_ids (`classification_log`, `prefilter_drops`,
`feedback_events`, `digest_item_map`, `node_summary` -- `approval_log`
and `run_history` came back clean). Full inventory reported before
deleting anything, then deleted only confirmed test entries:

| Namespace | Before | Deleted | After |
|---|---|---|---|
| `classification_log` | 31 | 25 | 6 |
| `prefilter_drops` | 177 | **177** | **0** |
| `feedback_events` | 5 | 4 | 1 |
| `digest_item_map` | 3 | 1 | 2 |
| `node_summary` | 21 | 10 | 11 |

Real, notable finding: **`prefilter_drops` had zero real production
entries** -- the entire namespace was test/stress-test data from
tonight's earlier verification work (`scale-stress-test-*`, `batch-
verify-dedup`, `singleton-check`, `log-verbosity-check-run`). Also swept
2 lingering `example.com` entries from `seen_items` (a namespace with no
`run_id` field at all, so out of scope for the UUID-based sweep, but
caught by URL pattern the same way earlier tonight's cleanup worked) --
420 -> 418.

### 3. `scripts/trace_run.py` -- built and verified against a real run
Given a `run_id`, queries `run_history`, `node_summary` (printed in real
graph execution order, not insertion order), `classification_log`,
`approval_log`, and `prefilter_drops`, and prints one unified report --
the actual fix for "manually check multiple Postgres tables to debug a
run." Run for real against `63b04873` (the run that crashed on
`cost_log.csv`): one call surfaced the crash point, every node's real
counts/costs/durations in order, the one real `project_proposal` named
specifically, and its real `approval_log` outcome (`rejected`) --
cross-referenced automatically, not manually.

### 4. `actions/upload-artifact` -- implemented, NOT YET independently verified
Real research finding that changed the recommendation: unlike
`actions/cache`'s save step (a POST step, confirmed skipped entirely on
a timeout-cancellation -- this project's own earlier finding), a plain
`if: always()` step gets a real ~5-minute cancellation grace period
during which GitHub Actions attempts to run it before force-terminating
the job -- sourced via WebSearch (community discussions on this exact
behavior), not assumed. This means an upload-artifact step SHOULD still
capture a partial log from a run that times out, the exact scenario this
exists for -- unlike the cache/save case.

Implemented in both `daily.yml`/`sunday.yml`: the main pipeline step's
output is now tee'd to a log file (GH Actions bash steps run with
`pipefail` on by default, so a real failure still fails the step despite
the pipe), followed by an `actions/upload-artifact@v4` step with `if:
always()` and `retention-days: 14`.

**Explicitly not marked verified yet** -- this is a real, sourced,
reasoned expectation, not this project's own direct observation. Pooja
will trigger a real Sunday run to confirm the artifact actually appears
with real captured content, ideally including a run that genuinely times
out (not just a clean completion), before this is considered closed.

