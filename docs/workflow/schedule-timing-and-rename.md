[← back to WORKFLOW.md index](../WORKFLOW.md)

# Checkpoint history: Schedule timing fix + Sunday → Saturday rename (2026-07-26)

Two explicitly gated phases, run via a `/goal` with a strict stop between
them: Phase 1 (cron timing) had to be committed, tested, and explicitly
confirmed by Pooja before Phase 2 (the rename) was allowed to start.

## Phase 1 — Cron timing fix

Both `daily.yml` and the weekly pipeline's own workflow were observed
delivering later than their scheduled trigger time — a real GitHub Actions
scheduling lag, not a bug in this project's code (GitHub documents
`schedule` triggers as best-effort, no delivery SLA; this project had
already found and documented the same pattern once before, see the
`daily.yml` schedule-delay entry earlier in this file's Scheduled Runs
section).

- **`daily.yml`**: `30 1 * * 1-6` (01:30 UTC / 07:00 IST target) →
  `0 22 * * *` (22:00 UTC / 03:30 IST) — compensates for an observed ~3.5h
  lag (the old target was actually delivering ~10:30 IST). The day-of-week
  restriction was deliberately dropped in this first commit and flagged
  in-line as worth revisiting once the weekly pipeline's own new schedule
  was known.
- **`sunday.yml`** (pre-rename filename): `30 5 * * 0` (05:30 UTC / 11:00
  IST Sunday target) → `0 23 * * 5` (23:00 UTC Friday) — compensates for an
  observed ~2.5h lag (the old target was actually delivering ~13:30 IST).
  Friday-trigger converts to **04:30 IST Saturday** delivery — the weekly
  pipeline's delivery day moved from Sunday to Saturday as a direct
  consequence of this fix, which is what motivated Phase 2.

**Follow-up, same day**: `daily.yml`'s dropped day-of-week restriction was
re-added once the weekly pipeline's real new schedule was confirmed —
`0 22 * * *` → `0 22 * * 0-4,6` (excludes UTC day-of-week 5). Verified
directly, not assumed: cron's day-of-week field is evaluated in UTC (the
scheduler's own timezone), so "the day to exclude" is NOT "Saturday" read
naively in UTC terms — IST is ahead of UTC, so a late-UTC-evening cron
rolls over to the next calendar day in IST. Fired the cron on every UTC
weekday and converted each to IST directly:

```
Mon 22:00 UTC -> Tue 03:30 IST      Tue 22:00 UTC -> Wed 03:30 IST
Wed 22:00 UTC -> Thu 03:30 IST      Thu 22:00 UTC -> Fri 03:30 IST
Fri 22:00 UTC -> Sat 03:30 IST      <-- collides with the weekly pipeline's
                                        Sat 04:30 IST delivery
Sat 22:00 UTC -> Sun 03:30 IST      Sun 22:00 UTC -> Mon 03:30 IST
```

Confirms UTC-Friday (cron day-of-week `5`) is what needs excluding —
excluding UTC-Saturday instead (the naive reading) would have delivered on
IST *Sunday*, not Saturday, and would have been wrong.

**Verification for both cron changes**: both workflow files parsed via
`yaml.safe_load` after each edit; UTC→IST math independently computed via
`datetime`/`timezone` (not eyeballed); no test in the suite references
either cron string (grepped, zero hits) so no test updates were needed for
this phase. Full suite: 342 passed, 1 skipped, 0 failed after each of the
three commits in this phase (initial cron fix, then the day-exclusion
follow-up).

## Phase 2 — Sunday → Saturday rename

Once the weekly pipeline genuinely triggers Friday and delivers Saturday,
keeping everything named for "Sunday" would misrepresent the actual
schedule to any future reader. Comprehensive rename, not drip-fed, since
this touches a locked architecture (per `/goal`'s own explicit instruction
to do this as one pass, not incrementally).

**Renamed** (via `git mv`, preserving history):
- `sunday/` → `saturday/` (directory + all files inside, unchanged
  filenames within)
- `scripts/run_sunday.py` → `scripts/run_saturday.py`
- `.github/workflows/sunday.yml` → `.github/workflows/saturday.yml`
- `tests/test_sunday_approval.py` → `tests/test_saturday_approval.py`
- `tests/test_sunday_rewrite_live_roundtrip.py` →
  `tests/test_saturday_rewrite_live_roundtrip.py`
- `tests/test_update_profile_sunday_rewrite.py` →
  `tests/test_update_profile_saturday_rewrite.py`

**Content updated** (case-preserving `Sunday`→`Saturday`/`sunday`→`saturday`
substitution, applied file-by-file, not a blind repo-wide sed): every
Python file under the renamed `saturday/` package, every file elsewhere
that imports from it (`core/`, `discovery/`, `telegram/`, `scripts/`,
`tests/`), `.github/workflows/{daily,poll,saturday}.yml`, `discovery/config/
blog_sources.yaml` and `agentmail_sources.yaml`(`.example`) — including
runtime string/enum VALUES, not just import paths: `source_context:
Literal["daily", "sunday"]` → `"saturday"`, `bucket: sunday` YAML values →
`bucket: saturday`, `_MAX_AGE_HOURS_BY_BUCKET`'s dict key, `SundayGraphState`
→ `SaturdayGraphState`, `make_sunday_initial_state` →
`make_saturday_initial_state`. Getting these runtime values wrong (as
opposed to just cosmetic renames) would have been a real functional bug —
code checking `bucket == "saturday"` against a YAML file that still said
`sunday` would have silently stopped routing sources correctly.

Also updated: `README.md` (user-facing, describes current architecture)
and `.env.example` (a path comment) — corrected past the literal rename to
also fix two now-doubly-wrong schedule descriptions ("Monday–Saturday
mornings" → "every day except Friday") while already touching those exact
lines for Phase 1's own cron change, which had never been reflected there.

**Deliberately NOT touched** (historical record, not rewritten — same
principle already applied to `docs/workflow/agentmail-and-sources.md` after
the AI Engineering sender removal): every file under `docs/workflow/*.md`
is itself titled "Checkpoint history: ..." and links back to this file's
own index — they ARE this file's historical entries, just paginated out
(2026-07-20, when this file had grown to 201KB/3300 lines). This includes
`sunday-plan-rewrite.md` and `sunday-timeout-fixes.md`, whose own filenames
still say "sunday" — they describe what was actually built and named at
the time, and renaming them would misrepresent that history. Also
untouched: this repository's locked spec documents (`batch2-dedup-taste-
spec.md`, `phase-5b-spec.md`, `tests/closeout-spec.md`, `session-
handoff.md`) — Pooja's own authored source material, not something Claude
Code edits for terminology cleanup regardless of how "current" vs.
"historical" they are. Also untouched: gitignored/untracked files
(`feature_list.json`, `data/*.json`, `data/*.csv`, `data/taste_profile.yaml`)
— outside the committed codebase this rename covers.

**Real discrepancy found, flagged not silently worked around**: the
`/goal`'s Phase 2 instructions named `architecture-v2.md` and `file-map.md`
as needing terminology updates. Neither file exists anywhere in this
repository (confirmed via exhaustive filename search, case-insensitive,
no match under any variant) — despite being referenced as real, existing
documents by both `CLAUDE.md` itself (Section 7) and the locked
`phase-5b-spec.md` (which explicitly says it "supersedes... `architecture-
v2.md` (Section 1) and the corresponding rows in `file-map.md`"). They
were either lost, never actually committed, or removed at some point
before this session. Not recreated here — that's a real gap worth Pooja's
own attention, not something to silently invent placeholder content for.

**`WORKFLOW.md` itself**: the substitution was applied to every section
EXCEPT "## Checkpoint history" (this file's own historical links section,
left completely untouched per the `/goal`'s explicit instruction). The
"Scheduled runs" section's cron *values* were also corrected to match
Phase 1's real current state (they still showed the pre-Phase-1 crons even
after the rename substitution) — this is a current-state section describing
what's configured right now, not a historical narrative, so this wasn't
"rewriting history."

**Final verification**: exhaustive case-insensitive grep across the entire
repository (excluding `.venv/`, `__pycache__/`, `.git/`, `.pytest_cache/`)
confirms zero remaining `sunday` references outside: this file's own
historical description of what changed FROM, the `docs/workflow/*.md`
historical checkpoint files, the locked spec/handoff docs, and gitignored/
untracked data files — all listed and reasoned about above, not silent
omissions.

Full suite: 342 passed, 1 skipped, 0 failed (unchanged from before the
rename — the same pre-existing, unrelated live-network flake pattern
already documented elsewhere in this project's history is the only
occasional non-zero-failure state this suite has shown all session).
