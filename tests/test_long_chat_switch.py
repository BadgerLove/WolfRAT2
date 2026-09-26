"""The Chat tab's long-chat tick box (Dale 2026-09-26): 59 by default - the
proven stock cut - and 118 only when the server runs the long-chat patch."""

import pytest

from wolfrat import admin_commands as ac
from wolfrat.protocol import _split_chat_message


@pytest.fixture(autouse=True)
def stock_afterwards():
    ac.set_extended_chat(False)
    yield
    ac.set_extended_chat(False)


def test_off_by_default_and_stock_is_59():
    assert not ac.extended_chat()
    assert ac.chat_limit() == ac.STOCK_CHAT_LEN == 59
    assert ac.MAX_CHAT_LEN == 59        # WolfRAT's own automatic lines


def test_stock_rejects_60_characters():
    ac.AdminCommands.send_chat("x" * 59)
    with pytest.raises(ValueError):
        ac.AdminCommands.send_chat("x" * 60)


def test_ticked_allows_118_not_119():
    ac.set_extended_chat(True)
    assert ac.AdminCommands.send_chat("x" * 118).text == "CHAT SEND " + "x" * 118
    with pytest.raises(ValueError):
        ac.AdminCommands.send_chat("x" * 119)


def test_word_cap_stays_23_either_way():
    ac.set_extended_chat(True)
    with pytest.raises(ValueError):
        ac.AdminCommands.send_chat(" ".join(["x"] * 24))


def test_long_line_is_split_to_fit_when_off():
    line = "<c00ff00>Welcome to the server, " + "please be nice to each other " * 3
    assert len(line) > 59
    chunks = _split_chat_message(line)
    assert len(chunks) > 1 and all(len(c) <= 59 for c in chunks)
    ac.set_extended_chat(True)
    assert _split_chat_message(line) == (line.strip(),)


def test_listeners_hear_the_new_limit_once():
    heard = []
    ac.on_chat_limit_changed(heard.append)
    ac.set_extended_chat(True)
    ac.set_extended_chat(True)
    ac.set_extended_chat(False)
    assert heard == [118, 59]
    ac._chat_limit_listeners.remove(heard.append)
