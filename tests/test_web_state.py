"""Pure helpers behind the phone page's state (wolfrat/web_state.py)."""

import pytest

from wolfrat import web_state
from wolfrat.admin_commands import MissionEntry, PlayerEntry


@pytest.mark.parametrize("ping, band", [
    (None, None), ("-", None), (True, None), (-1, None),
    (0, 0), (79, 0), (80, 1), (149, 1), (150, 2), (999, 2), ("120", 1),
])
def test_ping_bands_follow_lag_comp(ping, band):
    assert web_state.ping_band(ping) == band


@pytest.mark.parametrize("message, kind, name", [
    ({"text": "Dale: hello all"}, "player", "Dale"),
    ({"text": "FMJ-BadgerLove: !kd Ham"}, "player", "FMJ-BadgerLove"),
    ({"text": "Ham joined from Wales", "own": True}, "wolfrat", None),
    ({"text": "WARNING to Ham: stop", "own": True}, "wolfrat", None),
    ({"text": "Player Ham has entered the game"}, "console", None),
    ({"text": "Unknown command: !foo"}, "console", None),
    ({"text": ": no name"}, "console", None),
    ({"text": ""}, "console", None),
])
def test_chat_kinds(message, kind, name):
    assert web_state.chat_kind(message) == (kind, name)


def test_chat_rows_are_copies_with_kind_and_name():
    original = [{"id": 1, "text": "Dale: hi", "raw": "Dale: hi"},
                {"id": 2, "text": "Ham was kicked by an admin", "raw": "x", "own": True}]
    rows = web_state.chat_rows(original)
    assert rows[0]["kind"] == "player" and rows[0]["name"] == "Dale"
    assert rows[1]["kind"] == "wolfrat" and rows[1]["name"] is None
    assert "kind" not in original[0]


def test_team_rows_count_players_and_kills():
    entries = (
        PlayerEntry(server_id=0, name="Host", team=1, kills=50),
        PlayerEntry(server_id=1, name="A", team=1, kills=3),
        PlayerEntry(server_id=2, name="B", team=1, kills=None),
        PlayerEntry(server_id=3, name="C", team=2, kills=7),
        PlayerEntry(server_id=4, name="D", team=0, kills=9),
    )
    assert web_state.team_rows(entries) == [
        {"team": 1, "name": "Joint Ops", "kills": 3, "players": 2},
        {"team": 2, "name": "Rebels", "kills": 7, "players": 1},
    ]


@pytest.mark.parametrize("raw, expected", [
    ("12/30", (12, 30)), ("0/30", (0, 30)), ("0/0", (None, None)),
    ("", (None, None)), (None, (None, None)), ("abc/30", (None, None)), ("30", (None, None)),
])
def test_gametime(raw, expected):
    assert web_state.parse_gametime(raw) == expected


def test_game_type_from_the_server_or_the_file_name():
    assert web_state.game_type_of(0x10010, "CP08.BMS") == ("AAS", "Advance & Secure")
    assert web_state.game_type_of(0x10000, None) == ("TDM", "Team Deathmatch")
    assert web_state.game_type_of(0x10020, "x.bms")[0] == "COOP"
    # Not on this PC (None) or 0: the file name's family.
    code, name = web_state.game_type_of(None, "TDM_Snakebite.bms")
    assert code == "TDM" and name == "Team Deathmatch"
    assert web_state.game_type_of(None, None) == (None, None)


def test_next_map_marked_or_guessed():
    current = MissionEntry(queue_index=0, filename="A.BMS", is_current=True)
    marked = MissionEntry(queue_index=2, filename="C.BMS", is_next=True)
    plain = MissionEntry(queue_index=1, filename="B.BMS")
    assert web_state.next_entry((current, plain, marked)) == (marked, False)
    assert web_state.next_entry((current, plain)) == (plain, True)
    assert web_state.next_entry((plain, current)) == (plain, True)       # wraps
    assert web_state.next_entry(()) == (None, False)


class _Manager:
    game_settings = {"servername": "FMJ Squad", "gametime": "12/30"}
    mission_entries = (
        MissionEntry(queue_index=0, filename="AAS_Doslin.bms", is_current=True),
        MissionEntry(queue_index=1, filename="TDM_X.bms", is_next=True),
    )


def test_server_and_next_map_blocks():
    titles = {"AAS_DOSLIN.BMS": "Doslin Oblast"}
    block = web_state.server_block(_Manager(), titles)
    assert block == {
        "name": "FMJ Squad", "game_type": "AAS", "game_type_name": "Advance & Secure",
        "map_title": "Doslin Oblast", "map_file": "AAS_Doslin.bms",
        "time_left_min": 12, "time_limit_min": 30,
    }
    assert web_state.next_map_block(_Manager(), titles) == {
        "filename": "TDM_X.bms", "display_name": "TDM_X", "queue_index": 1, "guessed": False}
    block = web_state.server_block(_Manager(), titles, game_type=0x10000, time_left=(None, None))
    assert block["game_type"] == "TDM" and block["time_left_min"] is None
