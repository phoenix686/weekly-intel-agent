"""
Smoke test for the 4B.2 gate — exercises create_trello_card and
update_trello_card against the real Trello board.

Creates a test card in the Dump list, updates its name, then archives it
(sets closed=True) so it doesn't clutter the board. Review output to
confirm both branches work before wiring write_outputs into the graph.

Run:
    uv run --env-file .env python scripts/test_trello_write.py
"""
import sys
from saturday.trello_client import get_dump_list_id, create_trello_card, update_trello_card, _trello_put

TEST_CARD_NAME = "[TEST] weekly-intel write smoke test — safe to archive"


def main():
    print("Fetching Dump list ID...")
    try:
        dump_list_id = get_dump_list_id()
        print(f"  Dump list ID: {dump_list_id}")
    except (KeyError, ValueError) as e:
        print(f"ERROR fetching list: {e}")
        sys.exit(1)

    print("\nCreating test card...")
    try:
        created = create_trello_card(
            name=TEST_CARD_NAME,
            list_id=dump_list_id,
            desc="Created by test_trello_write.py — will be archived automatically.",
        )
        print(f"  Created: card_id={created['card_id']} | url={created['url']}")
    except Exception as e:
        print(f"ERROR creating card: {e}")
        sys.exit(1)

    card_id = created["card_id"]

    print("\nUpdating card name...")
    try:
        updated = update_trello_card(card_id, name=TEST_CARD_NAME + " [updated]")
        print(f"  Updated: name='{updated['name']}'")
    except Exception as e:
        print(f"ERROR updating card: {e}")
        sys.exit(1)

    print("\nArchiving test card (closed=true)...")
    try:
        _trello_put(f"/cards/{card_id}", {"closed": "true"})
        print("  Archived.")
    except Exception as e:
        print(f"ERROR archiving card: {e}")
        print(f"  Please manually archive card {card_id} on the board.")

    print("\n✓ Both create and update confirmed working.")


if __name__ == "__main__":
    main()
