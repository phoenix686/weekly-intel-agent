"""
telegram/markdown.py -- escape_markdown_v2 had no dedicated test file
before this one either; both helpers covered here since they're small
and share a module. escape_html added 2026-07-19 alongside the switch
to bot_client.py's HTML parse_mode default.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram.markdown import escape_html, escape_markdown_v2


def test_escape_html_ampersand():
    assert escape_html("Research & Compare") == "Research &amp; Compare"


def test_escape_html_angle_brackets():
    assert escape_html("<LangGraph>") == "&lt;LangGraph&gt;"


def test_escape_html_ampersand_escaped_before_brackets_no_double_escaping():
    """'&' must be replaced first -- otherwise the '&' introduced by
    escaping '<'/'>' would itself get re-escaped into '&amp;lt;'."""
    assert escape_html("<a & b>") == "&lt;a &amp; b&gt;"


def test_escape_html_underscore_untouched():
    """The actual point of the whole fix: HTML mode doesn't reserve '_',
    so it must pass through completely unescaped."""
    assert escape_html("last_activity and run_id") == "last_activity and run_id"


def test_escape_html_asterisk_untouched():
    assert escape_html("2 * 3 = 6") == "2 * 3 = 6"


def test_escape_html_plain_text_unchanged():
    assert escape_html("Nothing special here.") == "Nothing special here."


def test_escape_markdown_v2_still_escapes_underscore():
    """Confirms the pre-existing MarkdownV2 helper (used only where
    parse_mode="MarkdownV2" is passed explicitly, e.g.
    saturday/nodes/await_approval.py) is unaffected by adding escape_html."""
    assert escape_markdown_v2("last_activity") == "last\\_activity"
