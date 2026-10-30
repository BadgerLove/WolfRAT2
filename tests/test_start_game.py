"""!startgame: the start-of-map countdown is cut to 5 seconds, nothing else."""
import struct

import pytest

from wolfrat import start_game
from wolfrat.mod_ranks import DEFAULT_MODERATOR, ModRoster, permission_for


class FakeMemory:
    def __init__(self, seconds_left, store=None):
        address, expected = start_game.COUNTDOWN_STORE
        self.bytes = {
            address: store if store is not None else expected,
            start_game.PREROUND_TIMER: struct.pack("<i", seconds_left),
        }
        self.writes = []
        self.closed = False

    def read(self, address, size):
        return self.bytes[address][:size]

    def write(self, address, data):
        self.writes.append((address, data))
        self.bytes[address] = data

    def close(self):
        self.closed = True

    def left(self):
        return struct.unpack("<i", self.bytes[start_game.PREROUND_TIMER])[0]


def test_a_running_countdown_is_cut_to_five_seconds():
    memory = FakeMemory(28)
    outcome = start_game.shorten_countdown(memory)
    assert outcome == start_game.Outcome(start_game.STARTED, 28)
    assert memory.left() == 5
    assert memory.writes == [(start_game.PREROUND_TIMER, struct.pack("<i", 5))]
    assert start_game.reply_for(outcome) == "Game starting in 5 seconds!"


@pytest.mark.parametrize("left, kind", [(0, start_game.RUNNING), (5, start_game.ALMOST),
                                        (3, start_game.ALMOST)])
def test_nothing_is_written_once_the_wait_is_over_or_nearly(left, kind):
    memory = FakeMemory(left)
    assert start_game.shorten_countdown(memory).kind == kind
    assert memory.writes == []


def test_an_unknown_build_is_refused_untouched():
    memory = FakeMemory(30, store=b"\x90" * 10)
    with pytest.raises(start_game.StartGameError):
        start_game.shorten_countdown(memory)
    assert memory.writes == []


def test_the_local_server_handle_is_always_closed():
    memory = FakeMemory(30)
    outcome = start_game.start_local_server(attach=lambda writable: (None, memory))
    assert outcome.kind == start_game.STARTED and memory.closed

    broken = FakeMemory(30, store=b"\x00" * 10)
    with pytest.raises(start_game.StartGameError):
        start_game.start_local_server(attach=lambda writable: (None, broken))
    assert broken.closed


def test_replies_fit_in_jo_chat():
    for kind in (start_game.STARTED, start_game.ALMOST, start_game.RUNNING):
        assert len(start_game.reply_for(start_game.Outcome(kind, 30))) <= 62


def test_moderators_get_startgame_out_of_the_box():
    assert permission_for("!startgame") == "startgame"
    assert "startgame" in DEFAULT_MODERATOR
    roster = ModRoster.from_config({"mods": ["Mudd"]})
    assert roster.allows("mudd", "!startgame")
    assert not roster.allows("stranger", "!startgame")
