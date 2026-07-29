"""Durable Telegram webhook inbox with bounded retries and dead letters."""
from __future__ import annotations
import json

from core.connection_pool import get_connection_pool


def persist_updates(updates: list[dict]) -> None:
    if not updates:
        return
    with get_connection_pool().connection() as conn:
        with conn.cursor() as cursor:
            for update in updates:
                cursor.execute(
                    """INSERT INTO telegram_update_inbox(update_id, payload)
                       VALUES (%s, %s::jsonb) ON CONFLICT (update_id) DO NOTHING""",
                    (update["update_id"], json.dumps(update)),
                )


def process_inbox(handler, *, limit: int = 100, max_attempts: int = 3) -> dict:
    processed = dead_lettered = failed = 0
    pool = get_connection_pool()
    with pool.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """WITH candidates AS (
                     SELECT update_id FROM telegram_update_inbox
                     WHERE status IN ('pending', 'retry')
                        OR (status='processing' AND lease_until < now())
                     ORDER BY update_id
                     FOR UPDATE SKIP LOCKED LIMIT %s
                   )
                   UPDATE telegram_update_inbox AS inbox
                   SET status='processing', attempts=inbox.attempts+1,
                       lease_until=now() + interval '5 minutes',
                       claim_token=gen_random_uuid(), updated_at=now()
                   FROM candidates
                   WHERE inbox.update_id=candidates.update_id
                   RETURNING inbox.update_id, inbox.payload, inbox.attempts, inbox.claim_token""",
                (limit,),
            )
            rows = cursor.fetchall()
    for row in rows:
        update_id, attempts, claim_token = row["update_id"], row["attempts"], row["claim_token"]
        try:
            handler(row["payload"])
        except Exception as exc:
            status = "dead_letter" if attempts >= max_attempts else "retry"
            with pool.connection() as conn:
                conn.execute(
                    """UPDATE telegram_update_inbox
                       SET status=%s, attempts=%s, last_error=%s, updated_at=now()
                       WHERE update_id=%s AND claim_token=%s AND status <> 'processed'""",
                    (status, attempts, f"{type(exc).__name__}: {exc}"[:1000],
                     update_id, claim_token),
                )
            dead_lettered += status == "dead_letter"
            failed += status == "retry"
            continue
        with pool.connection() as conn:
            conn.execute(
                """UPDATE telegram_update_inbox
                   SET status='processed', attempts=%s, processed_at=now(), updated_at=now()
                   WHERE update_id=%s AND claim_token=%s""",
                (attempts, update_id, claim_token),
            )
        processed += 1
    return {"processed": processed, "retry": failed, "dead_letter": dead_lettered}
