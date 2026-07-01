# LangGraph Weekly Intelligence Agent

Phase 0 status: empty discovery-subgraph skeleton, compiles and runs,
traceable. No real discovery logic yet (that's Phase 1).

## Setup (on your machine)

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in `LANGSMITH_API_KEY` (your real one
   — never commit this)
3. `python scripts/smoke_test_phase0.py`
4. Check the LangSmith project named `langgraph-weekly-intel` for the trace
   of that run — this is the Phase 0.4 proof step that can't be done from
   the build sandbox (no key, no network path to LangSmith from there).

## What exists so far

- `state.py` — DiscoverySubgraphState schema (search/scrape -> cluster/dedupe -> score)
- `discovery/graph.py` — 3-node placeholder skeleton, no real logic, no LLM calls
- `scripts/smoke_test_phase0.py` — compiles, runs once, prints state, emits Mermaid
- `discovery_graph.mmd` — Mermaid source generated from the last smoke-test run

## What's explicitly NOT here yet

- Real search/scrape/cluster/score logic (Phase 1)
- Daily/Sunday path graphs (Phase 2-4)
- Telegram, Trello, LangMem, GEPA/DSPy (later phases per spec)
