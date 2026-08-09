"""Explicit operator resolution for ambiguous Telegram update effects."""
from __future__ import annotations
from datetime import datetime, timezone

from core.connection_pool import get_connection_pool

EFFECT_NAMESPACE = ("weekly_intel", "telegram_update_effects")


def reconcile_effect(store, update_id: int, resolution: str) -> None:
    if resolution not in {"retry", "processed"}:
        raise ValueError("resolution must be retry or processed")
    key = str(update_id)
    if resolution == "retry":
        store.delete(EFFECT_NAMESPACE, key)
    else:
        store.put(EFFECT_NAMESPACE, key, {
            "status": "processed", "reconciled_at": datetime.now(timezone.utc).isoformat()
        })
    with get_connection_pool().connection() as connection:
        connection.execute(
            """UPDATE telegram_update_inbox
               SET status=%s, lease_until=NULL, claim_token=NULL, updated_at=now()
               WHERE update_id=%s""",
            ("retry" if resolution == "retry" else "processed", update_id),
        )
