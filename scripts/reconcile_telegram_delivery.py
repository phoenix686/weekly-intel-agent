import argparse

from dotenv import load_dotenv

from saturday.memory_store_config import get_store
from telegram.delivery_reconciliation import reconcile_delivery

load_dotenv()

parser = argparse.ArgumentParser()
parser.add_argument("ledger_key", help="Exact ledger key, for example daily:2026-07-29")
parser.add_argument("resolution", choices=["not_sent", "accepted"])
parser.add_argument("--message-id", type=int)
args = parser.parse_args()
reconcile_delivery(
    get_store(),
    args.ledger_key,
    args.resolution,
    message_id=args.message_id,
)
print(f"Telegram delivery {args.ledger_key} reconciled as {args.resolution}")
