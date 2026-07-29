import argparse
from dotenv import load_dotenv

from saturday.memory_store_config import get_store
from telegram.effect_reconciliation import reconcile_effect

load_dotenv()

parser = argparse.ArgumentParser()
parser.add_argument("update_id", type=int)
parser.add_argument("resolution", choices=["retry", "processed"])
args = parser.parse_args()
reconcile_effect(get_store(), args.update_id, args.resolution)
print(f"Telegram update {args.update_id} reconciled as {args.resolution}")
