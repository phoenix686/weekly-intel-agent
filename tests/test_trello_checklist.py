from saturday.trello_client import fetch_board_cards

cards = fetch_board_cards()
website_card = next((c for c in cards if "personal website" in c["name"].lower()), None)

if website_card is None:
    print("Card not found — check the name match")
else:
    print("Card:", website_card["name"])
    print("checklist_items field present:", "checklist_items" in website_card)
    print("Items:", website_card.get("checklist_items"))