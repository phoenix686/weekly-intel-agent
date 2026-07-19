"""
telegram/bot_client.py -- zero unit test coverage existed before this
file. Covers the 2026-07-19 fixes: default parse_mode switched to HTML,
and urllib.error.HTTPError now caught and its real response body
surfaced (root-caused a real send_telegram_plan 400 that otherwise only
showed the generic "HTTP Error 400: Bad Request" -- see
docs/WORKFLOW.md). urllib.request.urlopen mocked throughout -- no real
Telegram API call, no real TELEGRAM_BOT_TOKEN needed.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import urllib.error
from unittest.mock import patch, MagicMock

import telegram.bot_client as bot_client_mod
from telegram.bot_client import send_message


def _ok_response(body: dict | None = None):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body or {"ok": True, "result": {"message_id": 42}}).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _env():
    return {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_CHAT_ID": "fake-chat-id"}


def test_missing_bot_token_raises_key_error():
    with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "x"}, clear=True):
        try:
            send_message("hello")
            assert False, "expected KeyError"
        except KeyError as e:
            assert "TELEGRAM_BOT_TOKEN" in str(e)


def test_missing_chat_id_raises_key_error():
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x"}, clear=True):
        try:
            send_message("hello")
            assert False, "expected KeyError"
        except KeyError as e:
            assert "TELEGRAM_CHAT_ID" in str(e)


def test_default_parse_mode_is_html():
    captured = {}

    def _capture(req):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _ok_response()

    with patch.object(bot_client_mod.urllib.request, "urlopen", side_effect=_capture), \
         patch.dict(os.environ, _env()):
        send_message("hello")

    assert captured["payload"]["parse_mode"] == "HTML"


def test_parse_mode_none_omits_key_entirely():
    """approval_actions.py's confirmation messages use parse_mode=None --
    plain text, no formatting intent, sidesteps escaping entirely."""
    captured = {}

    def _capture(req):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _ok_response()

    with patch.object(bot_client_mod.urllib.request, "urlopen", side_effect=_capture), \
         patch.dict(os.environ, _env()):
        send_message("plain text message", parse_mode=None)

    assert "parse_mode" not in captured["payload"]


def test_explicit_parse_mode_overrides_default():
    captured = {}

    def _capture(req):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _ok_response()

    with patch.object(bot_client_mod.urllib.request, "urlopen", side_effect=_capture), \
         patch.dict(os.environ, _env()):
        send_message("hello", parse_mode="MarkdownV2")

    assert captured["payload"]["parse_mode"] == "MarkdownV2"


def test_successful_send_returns_real_result():
    with patch.object(bot_client_mod.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.dict(os.environ, _env()):
        result = send_message("hello")

    assert result["ok"] is True
    assert result["result"]["message_id"] == 42


def test_http_error_response_body_is_surfaced_not_swallowed():
    """The actual fix: the real 2026-07-19 bug (send_telegram_plan 400)
    was only diagnosable via a manual reproduction because this path
    used to let HTTPError propagate unread -- the real Telegram
    description ("can't parse entities: ...") was never captured."""
    error_body = json.dumps({
        "ok": False, "error_code": 400,
        "description": "Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 3911",
    }).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.telegram.org/fake", code=400, msg="Bad Request",
        hdrs=None, fp=MagicMock(read=MagicMock(return_value=error_body)),
    )

    with patch.object(bot_client_mod.urllib.request, "urlopen", side_effect=http_error), \
         patch.dict(os.environ, _env()):
        try:
            send_message("some text")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "can't parse entities" in str(e)
            assert "byte offset 3911" in str(e)


def test_ok_false_response_raises_with_real_body():
    """A 200 response with ok:false (distinct from an HTTPError status)
    must also surface the real body, not a generic message."""
    with patch.object(bot_client_mod.urllib.request, "urlopen",
                       return_value=_ok_response({"ok": False, "description": "chat not found"})), \
         patch.dict(os.environ, _env()):
        try:
            send_message("some text")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "chat not found" in str(e)
