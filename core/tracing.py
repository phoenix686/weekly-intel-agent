"""LangSmith configuration and privacy-safe trace metadata."""
from __future__ import annotations
import os
import subprocess


def validate_tracing(expected_project: str = "weekly-intel-prod") -> None:
    enabled = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    if enabled and not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_TRACING=true requires LANGSMITH_API_KEY")
    if enabled and os.getenv("LANGSMITH_PROJECT") != expected_project:
        raise RuntimeError(f"Production tracing requires LANGSMITH_PROJECT={expected_project}")


def trace_metadata(pipeline: str, run_id: str) -> dict:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sha = os.getenv("GITHUB_SHA", "unknown")
    return {
        "run_id": run_id, "git_sha": sha, "pipeline": pipeline,
        "prompt_version": "score-v2", "clusterer_version": "story-v1",
        "ranker_version": "mmr-v1", "source_set_version": "sources-v1",
    }
