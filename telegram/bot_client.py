import logging
import os
import urllib.error
import urllib.request
import json

logger = logging.getLogger(__name__)


def send_message(text: str, parse_mode: str | None = "HTML") -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        raise KeyError("TELEGRAM_BOT_TOKEN is not set in the environment")
    if not chat_id:
        raise KeyError("TELEGRAM_CHAT_ID is not set in the environment")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            response_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # The real diagnostic value is in the response BODY, not the bare
        # HTTP status -- Telegram's error responses name the exact
        # problem (e.g. "can't parse entities: Can't find end of the
        # entity starting at byte offset N"). Letting urlopen's HTTPError
        # propagate unread (the prior behavior) throws that body away
        # entirely -- the caller only ever sees the generic
        # "HTTP Error 400: Bad Request" string, which is what made a
        # real 2026-07-19 send_telegram_plan failure take a manual
        # reproduction to root-cause instead of being visible immediately
        # in run_history's error_summary.
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error(f"Telegram sendMessage failed: HTTP {e.code} -- {error_body}")
        raise RuntimeError(f"Telegram API error (HTTP {e.code}): {error_body}") from e

    result = json.loads(response_body)
    if not result.get("ok"):
        logger.error(f"Telegram sendMessage returned ok=false: {response_body}")
        raise RuntimeError(f"Telegram API error: {response_body}")

    return result
