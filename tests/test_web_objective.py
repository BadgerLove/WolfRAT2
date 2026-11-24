"""What decides the running match, for the phone page's score card (2026-11-24).

``web_state.objective_block`` with made-up data for every kind and for every
case that must give nothing, and the two new reads (the hill clocks, the flag
targets) against a fake server memory.
"""

import json
import os
import struct
import sys
import types

import pytest

from wolfrat import jo_players as jp
from wolfrat import web_state
from wolfrat.jo_players import (GAME_CTF, GAME_FB, GAME_KOTH, GAME_TKOTH, HillInfo, HillPlayer,
                                HillTeam, LocalServerPlayers, ZoneInfo)

AAS, CAC, TDM, DM, COOP = 0x10010, 0x50010, 0x10000, 0x00000, 0x10020
SNAKE_RIVER = [ZoneInfo(3, 0), ZoneInfo(1, 1), ZoneInfo(2, 1), ZoneInfo(5, 2), ZoneInfo(4, 2)]
TEAM_HILL = HillInfo(420, 5, (HillTeam(1, 252, 2), HillTeam(2, 108, 0)))
SOLO_HILL = HillInfo(420, 5, players=(HillPlayer("ValleyDog", 175, False),
                                      HillPlayer("Ghost-Six", 259, True)))


def block(game_type, zones=(), hill=None, caps=None, goals=None, coop=None, ai=None):
    return web_state.objective_block(game_type, zones, hill, caps, goals, coop, ai)


# ---- the block, kind by kind ----------------------------------------------------

def test_advance_and_secure_lists_the_zones_in_order_with_who_holds_each():
    assert block(AAS, SNAKE_RIVER) == {
        "kind": "zones",
        "zones": [{"letter": "A", "team": 1}, {"letter": "B", "team": 1}, {"letter": "C", "team": 0},
                  {"letter": "D", "team": 2}, {"letter": "E", "team": 2}],
        "held": {"1": 2, "2": 2}, "neutral": 1, "total": 5,
    }
    assert block(CAC, SNAKE_RIVER)["kind"] == "zones"        # the same family


def test_zone_letters_run_a_to_z_then_the_number():
    zones = [ZoneInfo(tier, 1) for tier in (26, 27, 1, 0)]
    assert [row["letter"] for row in block(AAS, zones)["zones"]] == ["0", "A", "Z", "27"]


def test_a_zone_owner_that_is_no_team_counts_as_nobody():
    got = block(AAS, [ZoneInfo(1, 7), ZoneInfo(2, 2)])
    assert got["zones"] == [{"letter": "A", "team": 0}, {"letter": "B", "team": 2}]
    assert (got["held"], got["neutral"], got["total"]) == ({"1": 0, "2": 1}, 1, 2)


def test_team_king_of_the_hill_gives_both_clocks_and_who_is_on_the_hill():
    assert block(GAME_TKOTH, hill=TEAM_HILL) == {
        "kind": "hill_team", "limit_s": 420, "delta": 5,
        "teams": [{"team": 1, "hold_s": 252, "left_s": 168, "holders": 2},
                  {"team": 2, "hold_s": 108, "left_s": 312, "holders": 0}],
    }


def test_team_hill_with_no_limit_and_a_clock_past_the_limit():
    endless = block(GAME_TKOTH, hill=HillInfo(None, 5, TEAM_HILL.teams))
    assert endless["limit_s"] is None
    assert [team["left_s"] for team in endless["teams"]] == [None, None]
    over = block(GAME_TKOTH, hill=HillInfo(240, 5, TEAM_HILL.teams))
    assert [team["left_s"] for team in over["teams"]] == [0, 132]     # never below 0


def test_team_hill_always_has_joint_ops_first_and_needs_both_teams():
    swapped = HillInfo(420, 5, (HillTeam(2, 108, 0), HillTeam(1, 252, 2)))
    assert [team["team"] for team in block(GAME_TKOTH, hill=swapped)["teams"]] == [1, 2]
    assert block(GAME_TKOTH, hill=HillInfo(420, 5, (HillTeam(1, 252, 2),))) is None
    assert block(GAME_TKOTH, hill=None) is None


def test_king_of_the_hill_lists_the_players_longest_on_the_hill_first():
    assert block(GAME_KOTH, hill=SOLO_HILL) == {
        "kind": "hill_solo", "limit_s": 420, "in_zone": 1,
        "players": [{"name": "Ghost-Six", "hold_s": 259, "left_s": 161, "in_zone": True},
                    {"name": "ValleyDog", "hold_s": 175, "left_s": 245, "in_zone": False}],
    }


def test_solo_hill_shows_five_at_most_and_only_who_has_been_on_it():
    players = tuple(HillPlayer(f"P{n}", n * 10, False) for n in range(8))      # P0 never on it
    players += (HillPlayer("Fresh", 0, True), HillPlayer("Idle", 0, False))
    got = block(GAME_KOTH, hill=HillInfo(None, 5, players=players))
    assert [row["name"] for row in got["players"]] == ["P7", "P6", "P5", "P4", "P3"]
    assert got["in_zone"] == 1 and got["limit_s"] is None
    assert all(row["left_s"] is None for row in got["players"])
    few = block(GAME_KOTH, hill=HillInfo(420, 5, players=(HillPlayer("Fresh", 0, True),
                                                          HillPlayer("Idle", 0, False))))
    assert few["players"] == [{"name": "Fresh", "hold_s": 0, "left_s": 420, "in_zone": True}]
    nobody = block(GAME_KOTH, hill=HillInfo(420, 5))
    assert nobody == {"kind": "hill_solo", "limit_s": 420, "in_zone": 0, "players": []}
    assert block(GAME_KOTH, hill=None) is None


def test_capture_the_flag_and_flagball_give_captures_against_the_target():
    assert block(GAME_CTF, caps={1: 2, 2: 1}, goals={1: 5, 2: 5}) == {
        "kind": "flags", "mode": "ctf", "caps": {"1": 2, "2": 1}, "goal": {"1": 5, "2": 5}}
    assert block(GAME_FB, caps={1: 0, 2: 3}, goals={1: 4, 2: 4}) == {
        "kind": "flags", "mode": "fb", "caps": {"1": 0, "2": 3}, "goal": {"1": 4, "2": 4}}
    assert block(GAME_FB, caps={1: 0, 2: 3})["goal"] is None                  # no target
    assert block(GAME_CTF, caps={1: 0, 2: 3}, goals={1: 5, 2: 0})["goal"] is None
    assert block(GAME_CTF, caps=None, goals={1: 5, 2: 5}) is None             # nothing read


def test_co_op_gives_objectives_done_and_the_ai_left():
    assert block(COOP, coop=(2, 5), ai=(34, 80)) == {
        "kind": "coop", "objectives_done": 2, "objectives_total": 5, "ai_alive": 34, "ai_start": 80}
    assert block(COOP | 0x20000, coop=(0, 3)) == {
        "kind": "coop", "objectives_done": 0, "objectives_total": 3, "ai_alive": None, "ai_start": None}
    assert block(COOP, coop=None, ai=(34, 80)) is None


@pytest.mark.parametrize("game_type", [TDM, DM, None, 0x00008, 0x10002])
def test_game_types_the_player_list_decides_get_nothing(game_type):
    assert web_state.objective_block(game_type, SNAKE_RIVER, TEAM_HILL, {1: 2, 2: 1},
                                     {1: 5, 2: 5}, (2, 5), (34, 80)) is None


def test_each_kind_needs_its_own_game_type_and_its_own_data():
    assert block(AAS) is None                                   # no zones read
    assert block(AAS, hill=TEAM_HILL, caps={1: 1, 2: 1}) is None
    assert block(GAME_TKOTH, SNAKE_RIVER, caps={1: 1, 2: 1}) is None
    assert block(GAME_KOTH, SNAKE_RIVER, hill=TEAM_HILL)["players"] == []
    assert block(GAME_CTF, SNAKE_RIVER, hill=TEAM_HILL) is None


def test_every_block_goes_to_the_page_as_it_is():
    """Team numbers are text keys already, so JSON changes nothing."""
    for made in (block(AAS, SNAKE_RIVER), block(GAME_TKOTH, hill=TEAM_HILL),
                 block(GAME_KOTH, hill=SOLO_HILL),
                 block(GAME_CTF, caps={1: 2, 2: 1}, goals={1: 5, 2: 5}),
                 block(COOP, coop=(2, 5), ai=(34, 80))):
        assert json.loads(json.dumps(made)) == made


# ---- the reads, against a fake server memory -------------------------------------

class Memory:
    """Blocks of bytes at addresses; anything else is unmapped."""

    def __init__(self):
        self.blocks = {}

    def put(self, address, data):
        self.blocks[address] = bytes(data)

    def read(self, address, size):
        for start, data in self.blocks.items():
            if start <= address and address + size <= start + len(data):
                return data[address - start:address - start + size]
        raise OSError("unmapped")

    def close(self):
        pass


def team_hill_memory(limit=7, delta=5, joint_ops=(2, 252), rebels=(0, 108)):
    memory = Memory()
    memory.put(jp.HILL_LIMIT_VA, struct.pack("<ii", limit, delta))
    for team, (holders, clock) in ((1, joint_ops), (2, rebels)):
        record = jp.TEAM_RECORD_VA + jp.TEAM_RECORD_STRIDE * team
        memory.put(record + jp.TEAM_HOLDERS_OFFSET, struct.pack("<ii", holders, clock))
    return memory


def reader_on(memory, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return LocalServerPlayers(find=lambda: [1], open_memory=lambda pid: memory)


def test_the_team_records_sit_where_the_score_blocks_say():
    """One table: the captures WolfRAT already reads are 0x44 into the same records."""
    for team in (1, 2):
        assert jp.TEAM_RECORD_VA + jp.TEAM_RECORD_STRIDE * team + 0x44 == jp.TEAM_SCORE_VA[team]


def test_read_hill_on_team_king_of_the_hill(monkeypatch):
    reader = reader_on(team_hill_memory(), monkeypatch)
    assert reader.read_hill(GAME_TKOTH) == TEAM_HILL
    assert web_state.objective_block(GAME_TKOTH, [], reader.read_hill(GAME_TKOTH),
                                     None, None, None, None)["teams"][0]["left_s"] == 168


@pytest.mark.parametrize("limit, limit_s", [(0, None), (-1, None), (0x2222222, None),
                                            (100000, None), (99999, 5999940), (1, 60)])
def test_a_hill_limit_of_nothing_or_a_huge_number_is_no_limit(monkeypatch, limit, limit_s):
    hill = reader_on(team_hill_memory(limit=limit), monkeypatch).read_hill(GAME_TKOTH)
    assert hill.limit_s == limit_s and hill.teams == TEAM_HILL.teams


@pytest.mark.parametrize("changes", [
    {"joint_ops": (2, -1)}, {"rebels": (0, 86401)}, {"joint_ops": (257, 10)},
    {"rebels": (-1, 10)}, {"delta": -1}, {"delta": 86401},
])
def test_numbers_that_cannot_be_right_throw_the_whole_read_away(monkeypatch, changes):
    assert reader_on(team_hill_memory(**changes), monkeypatch).read_hill(GAME_TKOTH) is None


def test_the_edges_that_can_be_right_are_kept(monkeypatch):
    hill = reader_on(team_hill_memory(joint_ops=(256, 86400), rebels=(0, 0), delta=0),
                     monkeypatch).read_hill(GAME_TKOTH)
    assert hill.teams == (HillTeam(1, 86400, 256), HillTeam(2, 0, 0)) and hill.delta == 0


def test_read_hill_gives_nothing_off_the_hill_types_or_without_the_server(monkeypatch):
    reader = reader_on(team_hill_memory(), monkeypatch)
    for game_type in (AAS, TDM, DM, GAME_CTF, COOP, None):
        assert reader.read_hill(game_type) is None
    gone = LocalServerPlayers(find=lambda: [], open_memory=lambda pid: team_hill_memory())
    assert gone.read_hill(GAME_TKOTH) is None
    half = team_hill_memory()
    del half.blocks[jp.TEAM_RECORD_VA + jp.TEAM_RECORD_STRIDE * 2 + jp.TEAM_HOLDERS_OFFSET]
    assert reader_on(half, monkeypatch).read_hill(GAME_TKOTH) is None        # a failed read
    assert reader_on(Memory(), monkeypatch).read_hill(GAME_TKOTH) is None


SLOTS_AT = 0x10000000


def solo_memory(slots, capacity=None, limit=7):
    """slots: (in use, state, name, zone mask, hill clock) per slot, slot 0 first."""
    memory = Memory()
    memory.put(jp.HILL_LIMIT_VA, struct.pack("<ii", limit, 5))
    memory.put(jp.CAPACITY_VA, struct.pack("<I", len(slots) if capacity is None else capacity))
    memory.put(jp.SLOTPTR_VA, struct.pack("<I", SLOTS_AT))
    table = bytearray(len(slots) * jp.SLOT_STRIDE)
    for index, (used, state, name, mask, clock) in enumerate(slots):
        at = index * jp.SLOT_STRIDE
        table[at + jp.SLOT_USED_OFFSET] = used
        struct.pack_into("<I", table, at + jp.SLOT_STATE_OFFSET, state)
        raw = name.encode("latin-1")
        table[at + jp.NAME_OFFSET:at + jp.NAME_OFFSET + len(raw)] = raw
        struct.pack_into("<I", table, at + jp.SLOT_ZONE_MASK_OFFSET, mask)
        struct.pack_into("<i", table, at + jp.SLOT_HILL_OFFSET, clock)
    memory.put(SLOTS_AT, table)
    return memory


SOLO_SLOTS = [
    (1, 6, "Host", 1, 999),            # slot 0 is the host: never listed
    (1, 6, "Ghost-Six", 1, 259),
    (1, 6, "ValleyDog", 0, 175),
    (0, 6, "Gone", 1, -5),             # a slot not in use: whatever it holds is ignored
    (1, 3, "Loading", 1, 0),           # not playing yet: not counted as on the hill
    (1, 6, "Other zone", 2, 12),       # bit 0 is the hill; other bits are not
]


def test_read_hill_on_king_of_the_hill_walks_the_player_slots(monkeypatch):
    hill = reader_on(solo_memory(SOLO_SLOTS), monkeypatch).read_hill(GAME_KOTH)
    assert hill.limit_s == 420 and hill.teams == ()
    assert hill.players == (HillPlayer("Ghost-Six", 259, True), HillPlayer("ValleyDog", 175, False),
                            HillPlayer("Loading", 0, False), HillPlayer("Other zone", 12, False))
    card = web_state.objective_block(GAME_KOTH, [], hill, None, None, None, None)
    assert [row["name"] for row in card["players"]] == ["Ghost-Six", "ValleyDog", "Other zone"]
    assert card["in_zone"] == 1


def test_a_player_clock_that_cannot_be_right_throws_the_solo_read_away(monkeypatch):
    for clock in (-1, 86401):
        slots = SOLO_SLOTS[:2] + [(1, 6, "Odd", 0, clock)]
        assert reader_on(solo_memory(slots), monkeypatch).read_hill(GAME_KOTH) is None
    for capacity in (0, 257):
        assert reader_on(solo_memory(SOLO_SLOTS, capacity=capacity),
                         monkeypatch).read_hill(GAME_KOTH) is None
    short = solo_memory(SOLO_SLOTS, capacity=len(SOLO_SLOTS) + 1)       # a slot that cannot be read
    assert reader_on(short, monkeypatch).read_hill(GAME_KOTH) is None


def caps_memory(ctf=(5, 5), max_score=5):
    memory = Memory()
    for team, target in zip((1, 2), ctf):
        memory.put(jp.CTF_GOAL_VA[team], struct.pack("<i", target))
        memory.put(jp.TEAM_SCORE_VA[team] + jp.CAPS_OFFSET, struct.pack("<i", team))
    memory.put(jp.MAX_SCORE_VA, struct.pack("<i", max_score))
    return memory


def test_read_cap_goals_is_each_teams_target(monkeypatch):
    reader = reader_on(caps_memory(ctf=(3, 4), max_score=6), monkeypatch)
    assert reader.read_cap_goals(GAME_CTF) == {1: 3, 2: 4}
    assert reader.read_cap_goals(GAME_FB) == {1: 6, 2: 6}
    assert reader.read_cap_goals(AAS) is None and reader.read_cap_goals(None) is None
    # The captures and the "to go" count come from the same numbers as before.
    assert reader.read_team_caps(GAME_CTF) == {1: 1, 2: 2}
    assert reader.read_caps_to_go(GAME_CTF) == 2 and reader.read_caps_to_go(GAME_FB) == 4


@pytest.mark.parametrize("ctf, max_score", [((0, 5), 0), ((5, -1), 65000), ((65000, 5), 70000)])
def test_no_target_gives_no_goals(monkeypatch, ctf, max_score):
    reader = reader_on(caps_memory(ctf=ctf, max_score=max_score), monkeypatch)
    assert reader.read_cap_goals(GAME_CTF) is None
    assert reader.read_cap_goals(GAME_FB) is None


def test_read_cap_goals_without_the_server_or_with_a_failed_read(monkeypatch):
    assert reader_on(Memory(), monkeypatch).read_cap_goals(GAME_CTF) is None
    gone = LocalServerPlayers(find=lambda: [], open_memory=lambda pid: caps_memory())
    assert gone.read_cap_goals(GAME_CTF) is None


def test_cap_goals_rules():
    assert jp.cap_goals(GAME_CTF, {1: 12, 2: 12}, 0) == {1: 12, 2: 12}
    assert jp.cap_goals(GAME_CTF, {1: 12}, 5) is None
    assert jp.cap_goals(GAME_FB, {1: 12, 2: 12}, 5) == {1: 5, 2: 5}
    assert jp.cap_goals(TDM, {1: 12, 2: 12}, 5) is None


# ---- the block in the phone's state -------------------------------------------------

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat.web_server import WolfWebServer  # noqa: E402
from tests.test_web_bridge import make_bridge  # noqa: E402
from tests.test_web_phone_actions import Desktop, Manager  # noqa: E402


def bans_tab(game_type, zones=(), hill=None, caps=None, goals=None, coop=None, ai=None):
    """The Bans tab as the bridge asks it: what its last poll read."""
    return types.SimpleNamespace(
        game_type_now=lambda: game_type, zones_now=lambda: list(zones), hill_now=lambda: hill,
        team_caps_now=lambda: caps, cap_goals_now=lambda: goals, objectives_now=lambda: coop,
        ai_left_now=lambda: ai, connection_for=lambda name: ("", None))


@pytest.mark.parametrize("tab, kind", [
    (bans_tab(AAS, SNAKE_RIVER), "zones"),
    (bans_tab(GAME_TKOTH, hill=TEAM_HILL), "hill_team"),
    (bans_tab(GAME_KOTH, hill=SOLO_HILL), "hill_solo"),
    (bans_tab(GAME_CTF, caps={1: 2, 2: 1}, goals={1: 5, 2: 5}), "flags"),
    (bans_tab(COOP, coop=(2, 5), ai=(34, 80)), "coop"),
])
def test_the_snapshot_carries_the_block_from_the_bans_tabs_last_poll(qtbot, tab, kind):
    bridge, _window, web = make_bridge(bans_tab=tab)
    bridge.publish()
    made = web.desktop_state["top"]["objective"]
    assert made["kind"] == kind
    assert made == web_state.objective_block(
        tab.game_type_now(), tab.zones_now(), tab.hill_now(), tab.team_caps_now(),
        tab.cap_goals_now(), tab.objectives_now(), tab.ai_left_now())


def test_the_snapshot_has_no_block_where_there_is_nothing_to_say(qtbot):
    for parts in ({}, {"bans_tab": bans_tab(TDM, SNAKE_RIVER)}, {"bans_tab": bans_tab(None)},
                  {"bans_tab": types.SimpleNamespace(game_type_now=lambda: AAS)}):   # an older stand-in
        bridge, window, web = make_bridge(**parts)
        bridge.publish()
        assert web.desktop_state["top"]["objective"] is None
        # ...and the rest of the snapshot is there as before.
        assert web.desktop_state["top"]["server"]["name"] == window.server.game_settings["servername"]


def test_no_block_while_the_link_to_the_game_server_is_down(qtbot):
    bridge, window, web = make_bridge(bans_tab=bans_tab(AAS, SNAKE_RIVER))
    window.server.is_connected = False                  # the last poll's zones are stale now
    bridge.publish()
    assert web.desktop_state["top"]["objective"] is None


def test_the_web_server_passes_the_block_on_as_it_is():
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    assert service._build_state()["objective"] is None            # no window: not available
    service.on_desktop = Desktop()
    for made in (block(AAS, SNAKE_RIVER), block(GAME_TKOTH, hill=TEAM_HILL),
                 block(GAME_KOTH, hill=SOLO_HILL),
                 block(GAME_CTF, caps={1: 2, 2: 1}, goals={1: 5, 2: 5}),
                 block(COOP, coop=(2, 5), ai=(34, 80)), None):
        service.desktop_state = {"top": {"objective": made}, "players": {}}
        state = service._build_state()
        assert state["objective"] == made
        assert json.loads(json.dumps(state))["objective"] == made
