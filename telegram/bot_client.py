import os
import urllib.request
import json


def send_message(text: str, parse_mode: str = "Markdown") -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        raise KeyError("TELEGRAM_BOT_TOKEN is not set in the environment")
    if not chat_id:
        raise KeyError("TELEGRAM_CHAT_ID is not set in the environment")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")

    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")

    return result
