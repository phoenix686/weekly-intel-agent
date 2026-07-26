"""
Pulls the real current taste_profile.yaml content from Postgres (the real
source of truth, see discovery/taste_profile_store.py) and writes it to
the local data/taste_profile.yaml path -- for readability/manual
inspection ONLY. update_profile() (sunday/nodes/update_profile.py) is the
only writer of the real profile; this script never reads the local file
and never writes to Postgres -- one direction, always Postgres -> local.

data/ is gitignored and stays that way (personal-taste-derived content,
same privacy class as the AgentMail inbox address and Trello board ID
already kept out of git) -- this script's output is for a human looking
at the file locally, never for git tracking or a CI commit-back step.

Run: uv run --env-file .env python scripts/sync_taste_profile.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from discovery.taste_profile_store import get_taste_profile

LOCAL_PATH = Path("data/taste_profile.yaml")


def main() -> None:
    content = get_taste_profile()
    if content is None:
        print("No taste_profile row in Postgres yet -- nothing to sync.")
        return
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(content, encoding="utf-8")
    print(f"Synced {len(content)} chars to {LOCAL_PATH}")


if __name__ == "__main__":
    main()
