"""First blood from the server's memory (Dale, 2026-11-22: "read the servers memory").

22 Nov, AS-ColdWaterGapTAC, from the live map recorder:
  18:36:54.150  elk road kills FMJ-BadgerLove
  18:36:54.352  FMJ-BadgerLove kills elk road     (a trade: both in ONE player list, K+1 D+1 each)
  18:38:38      BadgerLove's satchel kills elk road and himself (his K+1 D+1 in one list)
  18:40:15      elk road kills BadgerLove -> "elk road drew first blood" (the map's 4th kill)

From memory, first blood goes to elk road on the trade (0.2 s earlier, by the
server's frame clock); from the player list (WolfRAT on another PC) it is said on
the trade's list too, to the first gainer in list order.  The fake server below
lays its memory out at the real offsets (first_blood_memory.py says where each
comes from) and changes it the way GameEvent_PlayerDeath does.
"""

import struct
import sys

import pytest

from wolfrat import first_blood_memory as fbm

FRAMES_PER_SECOND = 62.5          # g_ServerFrameClock, read live 2026-11-22
BASE, ENTITIES = 0x1000000, 0x8000000
DM, AAS, TDM = 0x00000, 0x10010, 0x10000


class FakeMemory:
    def __init__(self):
        self.data = {}
        self.reads = 0
        self.fail = False

    def put(self, address, raw):
        for i, b in enumerate(raw):
            self.data[address + i] = b

    def u32(self, address, value):
        self.put(address, struct.pack("<I", value & 0xFFFFFFFF))

    def i32(self, address, value):
        self.put(address, struct.pack("<i", value))

    def read(self, address, size):
        self.reads += 1
        if self.fail:
            raise OSError("Could not read the server's memory (has it closed?).")
        return bytes(self.data.get(address + i, 0) for i in range(size))


class FakeServer:
    """The slot table and player entities at the offsets the module reads."""

    def __init__(self, capacity=8, game_type=AAS):
        self.mem = FakeMemory()
        self.mem.u32(fbm.CAPACITY_VA, capacity)
        self.mem.u32(fbm.SLOTPTR_VA, BASE)
        self.mem.u32(fbm.GAME_TYPE_VA, game_type)
        self.round_over(False)

    def slot(self, index):
        return BASE + index * fbm.SLOT_STRIDE

    def entity(self, index):
        return ENTITIES + index * 0x1000

    def join(self, index, name, team=1, kills=0):
        s = self.slot(index)
        self.mem.u32(s + fbm.ENTITY_OFFSET, self.entity(index))
        self.mem.put(s + fbm.ACTIVE_OFFSET, b"\x01")
        self.mem.put(s + fbm.NAME_OFFSET, name.encode("latin-1").ljust(fbm.NAME_LEN, b"\0"))
        self.set_kills(index, kills)
        self.mem.u32(s + fbm.DEATH_CLOCK_OFFSET, 0)
        self.mem.put(self.entity(index) + fbm.TEAM_OFFSET, bytes([team]))

    def leave(self, index):
        self.mem.put(self.slot(index) + fbm.ACTIVE_OFFSET, b"\x00")

    def kills(self, index):
        return struct.unpack("<i", self.mem.read(self.slot(index) + fbm.KILLS_OFFSET, 4))[0]

    def set_kills(self, index, kills):
        self.mem.i32(self.slot(index) + fbm.KILLS_OFFSET, kills)

    def round_over(self, over):
        self.mem.u32(fbm.ROUND_OVER_FLAG, 1 if over else 0)

    def kill(self, killer, victim, frame):
        """What GameEvent_PlayerDeath leaves behind: the victim's death clock and
        last attacker; the killer's enemy-kill counter only for an enemy kill (a
        suicide goes to raw[6], a team kill to raw[4])."""
        self.mem.u32(self.slot(victim) + fbm.DEATH_CLOCK_OFFSET, frame)
        self.mem.u32(self.entity(victim) + fbm.KILLER_OFFSET, self.entity(killer))
        game = struct.unpack("<I", self.mem.read(fbm.GAME_TYPE_VA, 4))[0]
        team = lambda i: self.mem.read(self.entity(i) + fbm.TEAM_OFFSET, 1)
        if killer != victim and not (game & fbm.TEAMS and team(killer) == team(victim)):
            self.set_kills(killer, self.kills(killer) + 1)

    def spawn(self, index):
        self.mem.u32(self.slot(index) + fbm.DEATH_CLOCK_OFFSET, 0)

    def bot_kill(self, killer):
        """A co-op bot dies (Entity_CheckAndProcessDeath): the killer's counter, no slot death."""
        self.set_kills(killer, self.kills(killer) + 1)


class FakeReader:
    def __init__(self, server):
        self.server = server
        self.pid = 4242
        self.readable = True
        self.status = "Server process not found on this PC."
        self.released = []

    def handle(self):
        return self.server.mem if self.readable else None

    def release(self, why):
        self.released.append(why)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def watch(server, clock):
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    w.start(new_map=False)
    return w


def names(kills):
    return [k.name for k in kills]


def at(seconds):
    return 4_000_000 + int(seconds * FRAMES_PER_SECOND)


# ---- the order of kills -------------------------------------------------------

@pytest.mark.parametrize("dale_slot, elk_slot", [(1, 2), (2, 1)])
def test_a_trade_inside_one_read_comes_out_in_the_order_it_happened(server, watch, dale_slot, elk_slot):
    server.join(dale_slot, "FMJ-BadgerLove", team=2)
    server.join(elk_slot, "elk road", team=1)
    assert watch.poll() == []                                    # first look: where counting starts
    server.kill(elk_slot, dale_slot, at(54.150))                 # elk road kills Dale
    server.kill(dale_slot, elk_slot, at(54.352))                 # Dale kills elk road 0.2 s later
    kills = watch.poll()
    assert names(kills) == ["elk road", "FMJ-BadgerLove"]        # not slot order: the server's clock
    assert kills[0].victim == "FMJ-BadgerLove" and kills[0].clock < kills[1].clock


def test_a_satchel_that_kills_its_owner_and_an_enemy_is_the_owners_kill(server, watch):
    server.join(1, "FMJ-BadgerLove", team=2)
    server.join(2, "elk road", team=1)
    watch.poll()
    server.kill(1, 1, at(38.410))                                # the blast kills Dale: a suicide
    server.kill(1, 2, at(38.712))                                # ... and elk road: Dale's kill
    kills = watch.poll()
    assert names(kills) == ["FMJ-BadgerLove"]
    assert kills[0].victim == "elk road"


def test_a_suicide_is_never_a_kill(server, watch):
    server.join(1, "Dale")
    server.join(2, "Ham")
    watch.poll()
    server.kill(1, 1, at(1))
    assert watch.poll() == []


def test_a_team_kill_is_never_a_kill_and_does_not_lend_its_time(server, clock):
    server = FakeServer(game_type=TDM)
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    w.start()
    server.join(1, "Killer", team=1)
    server.join(2, "Mate", team=1)
    server.join(3, "Other", team=2)
    server.join(4, "Enemy", team=2)
    server.join(5, "Victim", team=1)
    w.poll()
    server.kill(1, 2, at(10.0))                                  # team kill: raw[4], not raw[5]
    assert w.poll() == []
    server.spawn(2)
    w.poll()
    server.kill(1, 2, at(20.0))                                  # team kill again ...
    server.kill(3, 5, at(20.2))                                  # ... Other kills Victim ...
    server.kill(1, 4, at(20.4))                                  # ... then Killer's enemy kill
    assert names(w.poll()) == ["Other", "Killer"]                # Killer's kill timed by Enemy's death


def test_in_deathmatch_everyone_is_an_enemy(server, clock):
    server = FakeServer(game_type=DM)                            # DM puts everybody on team 1
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    w.start()
    server.join(1, "Dale", team=1)
    server.join(2, "Ham", team=1)
    w.poll()
    server.kill(2, 1, at(5))
    kills = w.poll()
    assert names(kills) == ["Ham"] and kills[0].clock == at(5)


def test_a_bot_kill_has_no_clock_and_comes_after_the_timed_ones(server, watch):
    server.join(1, "Coop1")
    server.join(3, "Dale", team=2)
    server.join(4, "Ham", team=1)
    watch.poll()
    server.bot_kill(1)
    server.kill(3, 4, at(7))
    kills = watch.poll()
    assert names(kills) == ["Dale", "Coop1"]
    assert kills[1].clock is None


def test_two_bot_kills_in_one_read_go_in_slot_order(server, watch):
    server.join(5, "Later")
    server.join(2, "Earlier")
    watch.poll()
    server.bot_kill(5)
    server.bot_kill(2)
    assert names(watch.poll()) == ["Earlier", "Later"]


def test_a_death_read_before_its_killers_counter_still_gives_the_time(server, watch):
    server.join(1, "Dale")
    server.join(2, "Ham", team=2)
    server.join(3, "Angel", team=2)
    watch.poll()
    # the read caught Ham dead but Dale's counter not yet up (it fell between the two)
    server.mem.u32(server.slot(2) + fbm.DEATH_CLOCK_OFFSET, at(30.0))
    server.mem.u32(server.entity(2) + fbm.KILLER_OFFSET, server.entity(1))
    assert watch.poll() == []
    server.set_kills(1, 1)
    server.kill(3, 1, at(30.3))                                  # Angel kills Dale after that
    assert names(watch.poll()) == ["Dale", "Angel"]


def test_a_death_left_waiting_too_long_gives_no_time(server, watch, clock):
    server.join(1, "Dale")
    server.join(2, "Ham", team=2)
    watch.poll()
    server.mem.u32(server.slot(2) + fbm.DEATH_CLOCK_OFFSET, at(30.0))
    server.mem.u32(server.entity(2) + fbm.KILLER_OFFSET, server.entity(1))
    watch.poll()
    clock.now += fbm.DEATH_KEEP_SECONDS + 0.5
    watch.poll()
    server.set_kills(1, 1)
    kills = watch.poll()
    assert names(kills) == ["Dale"] and kills[0].clock is None


def test_the_round_end_stamp_on_everyone_is_no_kill(server, watch):
    server.join(1, "Dale")
    server.join(2, "Ham", team=2)
    server.kill(2, 1, at(1))                                     # an old kill: Dale's last attacker is Ham
    server.spawn(1)
    watch.poll()
    for index in (1, 2):                                         # Server_ProcessRoundEnd stamps everyone
        server.mem.u32(server.slot(index) + fbm.DEATH_CLOCK_OFFSET, at(600))
    assert watch.poll() == []


# ---- what is never a kill -------------------------------------------------------

def test_keep_stats_putting_a_score_back_is_not_a_kill(server, watch):
    server.join(1, "Oscar")
    server.join(2, "Ham", team=2)
    watch.poll()
    server.set_kills(1, 30)                                      # Keep Stats writes the old score back ...
    watch.restored("Oscar", 30)                                  # ... and says so (note_stats_restored)
    assert watch.poll() == []
    server.kill(1, 2, at(3))
    assert names(watch.poll()) == ["Oscar"]                      # real kills count again


def test_a_score_coming_back_while_the_list_settles_is_not_a_kill(server, watch):
    server.join(1, "Oscar")
    watch.poll()
    server.set_kills(1, 30)
    assert watch.poll(skip=lambda name: name == "Oscar") == []
    server.set_kills(1, 31)
    assert names(watch.poll()) == ["Oscar"]


def test_a_lower_score_is_counted_from_not_read_as_kills(server, watch):
    server.join(1, "Dale", kills=5)
    server.join(2, "Ham", team=2)
    watch.poll()
    server.set_kills(1, 0)                                       # team change or Zero Score
    assert watch.poll() == []
    server.kill(1, 2, at(9))
    assert names(watch.poll()) == ["Dale"]


def test_someone_new_in_a_slot_counts_from_what_they_have(server, watch):
    server.join(1, "Dale")
    watch.poll()
    server.join(2, "Rejoiner", kills=7)                          # score already back when first seen
    assert watch.poll() == []
    server.leave(1)
    server.join(1, "Someone else", kills=3)
    assert watch.poll() == []


def test_a_rejoin_in_the_same_slot_between_two_reads_is_not_a_kill(server, watch):
    server.join(1, "Dale", kills=2)
    watch.poll()
    server.join(1, "Oscar", kills=9)
    assert watch.poll() == []


# ---- maps and rounds --------------------------------------------------------------

def test_after_a_map_change_the_old_scores_wait_for_the_next_map(server, clock):
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    server.join(1, "Dale", kills=12)
    server.join(2, "Ham", team=2, kills=7)
    server.round_over(True)                                      # the list shows the new map, the server is still on the scoreboard
    w.start(new_map=True)
    assert w.poll() == []
    server.round_over(False)                                     # Game_StartMission: stats to 0, then the latch down
    server.set_kills(1, 0)
    server.set_kills(2, 1)                                       # Ham's kill came before the first look
    kills = w.poll()
    assert names(kills) == ["Ham"] and kills[0].clock is None


def test_a_new_maps_first_look_does_not_use_old_deaths(server, clock):
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    server.join(1, "Dale", team=1)
    server.join(2, "Ham", team=2)
    server.join(3, "Angel", team=2)
    server.kill(1, 2, at(1))                                     # old map: Dale killed Ham, Ham not spawned yet
    server.set_kills(1, 0)
    server.set_kills(3, 1)                                       # new map: Angel's kill before the first look
    server.set_kills(1, 1)                                       # and Dale's
    w.start(new_map=True)
    kills = w.poll()
    assert sorted(names(kills)) == ["Angel", "Dale"]
    assert all(k.clock is None for k in kills)                   # Ham's old death times nothing


def test_the_round_ending_says_nothing_and_the_next_map_counts_from_zero(server, watch):
    server.join(1, "Dale", kills=4)
    server.join(2, "Ham", team=2, kills=3)
    watch.poll()
    server.round_over(True)
    server.set_kills(1, 5)                                       # a kill on the scoreboard screen
    assert watch.poll() == []
    server.round_over(False)
    server.set_kills(1, 0)
    server.set_kills(2, 1)
    assert names(watch.poll()) == ["Ham"]


def test_no_server_on_this_pc_means_the_list_decides(server, watch):
    watch._reader.readable = False
    assert watch.poll() is None
    assert watch.status == "Server process not found on this PC."


def test_a_failed_read_lets_go_of_the_handle(server, watch):
    server.join(1, "Dale")
    watch.poll()
    server.mem.fail = True
    assert watch.poll() is None
    assert watch._reader.released and "Could not read" in watch.status
    server.mem.fail = False
    server.set_kills(1, 3)
    assert watch.poll() == []                                    # back: counts from what is there now


def test_a_failed_read_just_after_a_map_change_still_counts_this_maps_kills(server, clock):
    w = fbm.FirstBloodMemory(reader=FakeReader(server), clock=clock)
    server.join(1, "Dale")
    server.join(2, "Ham", team=2)
    w.start(new_map=True)
    server.mem.fail = True
    assert w.poll() is None
    server.mem.fail = False
    server.set_kills(2, 1)                                       # made while the read was failing
    assert names(w.poll()) == ["Ham"]


def test_another_server_process_starts_counting_again(server, watch):
    server.join(1, "Dale")
    watch.poll()
    watch._reader.pid = 9999
    server.set_kills(1, 6)
    assert watch.poll() == []


def test_stop_lets_go_of_the_handle(server, watch):
    watch.stop()
    assert watch._reader.released == ["First blood is not due."]


# ---- the cost of one read -------------------------------------------------------------

def test_one_read_is_two_small_reads_per_player_and_one_per_empty_slot(server, watch):
    server.join(1, "Dale")
    server.join(2, "Ham", team=2)
    server.join(5, "Angel", team=2)
    watch.poll()
    server.mem.reads = 0
    watch.poll()
    # latch + capacity + slot table, 8 slot heads, 3 death clocks
    assert server.mem.reads == 3 + 8 + 3
    server.kill(1, 2, at(2))
    server.mem.reads = 0
    watch.poll()
    # + the victim's last attacker, the game type, both team bytes
    assert server.mem.reads == 3 + 8 + 3 + 4


# ---- in the Sprees tab ---------------------------------------------------------------------

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wolfrat.app as app_module  # noqa: E402
from wolfrat import manual_mode  # noqa: E402
from wolfrat.manual_mode import MANUAL  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class Server:
    players = []
    is_connected = True

    def __init__(self):
        self.refreshes = 0

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass

    def refresh_players(self, quiet=False):
        self.refreshes += 1


class Messages:
    _kd_enabled = False


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


@pytest.fixture
def game():
    return FakeServer()


@pytest.fixture
def sprees(qtbot, tmp_path, monkeypatch, game):
    said = []
    outcome = {"fail": False}

    def submit(owner, op, on_success=None, context="", *a, **k):
        said.append(op.__defaults__[0])
        if outcome["fail"]:
            on_failure = a[0] if a else k.get("on_failure")
            if on_failure is not None:
                on_failure("send failed")
        elif on_success is not None:
            on_success(None)

    monkeypatch.setattr(app_module, "submit_admin", submit)
    tab = app_module.SpreeTab(Server(), Messages(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._first_blood_enabled = True
    tab._spree_enabled = True
    tab._fb_memory = fbm.FirstBloodMemory(reader=FakeReader(game))
    tab.said = said
    tab.outcome = outcome
    return tab


def p(pid, name, kills, deaths=0):
    return {"id": str(pid), "name": name, "kills": str(kills), "deaths": str(deaths)}


def first_blood(said):
    return [line for line in said if "first blood" in line.lower()]


def missions(name):
    return [f"0: {name} - (1x) () () <> <CURRENT MISSION>"]


def change_to_cold_water_gap(tab):
    tab.on_missions_updated(missions("AS-SiakaRiver.bms"))       # WolfRAT starting up
    tab.on_missions_updated(missions("AS-ColdWaterGapTAC.npj"))  # 18:26 the real change


def test_replay_22_nov_from_memory_gives_first_blood_to_elk_road_on_the_trade(sprees, game):
    change_to_cold_water_gap(sprees)
    assert sprees._memory_timer.isActive()
    sprees._memory_tick()                                        # empty server
    sprees.check_sprees([])
    assert sprees._fb_memory_live
    game.join(1, "FMJ-BadgerLove", team=2)                       # 18:34:40
    sprees._memory_tick()
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 0, 0)])
    game.join(2, "elk road", team=1)                             # 18:35:51
    sprees._memory_tick()
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 0, 0), p(2, "elk road", 0, 0)])

    game.kill(2, 1, at(54.150))                                  # 18:36:54.150 elk road kills Dale
    game.kill(1, 2, at(54.352))                                  # 18:36:54.352 Dale kills elk road
    # the player list may come first: it leaves first blood to the memory
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 1, 1), p(2, "elk road", 1, 1)])
    assert first_blood(sprees.said) == []
    sprees._memory_tick()
    after_trade = first_blood(sprees.said)
    assert len(after_trade) == 1 and "elk road" in after_trade[0]

    game.spawn(1)
    game.spawn(2)
    game.kill(1, 1, at(158.410))                                 # 18:38:38 the satchel: Dale ...
    game.kill(1, 2, at(158.712))                                 # ... and elk road
    sprees._memory_tick()
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 2, 2), p(2, "elk road", 1, 2)])
    game.spawn(1)
    game.spawn(2)
    game.kill(2, 1, at(255.535))                                 # 18:40:15 elk road kills Dale
    sprees._memory_tick()
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 2, 3), p(2, "elk road", 2, 2)])
    assert first_blood(sprees.said) == after_trade               # said once
    assert not sprees._memory_timer.isActive()                   # and the reading stopped


def test_replay_22_nov_from_the_player_list_says_it_on_the_trade(sprees, game):
    sprees._fb_memory._reader.readable = False                   # WolfRAT on another PC
    change_to_cold_water_gap(sprees)
    sprees._memory_tick()
    assert not sprees._fb_memory_live
    sprees.check_sprees([])
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 0, 0)])
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 0, 0), p(2, "elk road", 0, 0)])
    # the trade: both gain a kill and a death in one list; the list can't say who
    # was first, so the first gainer in list order gets it
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 1, 1), p(2, "elk road", 1, 1)])
    after_trade = first_blood(sprees.said)
    assert len(after_trade) == 1 and "FMJ-BadgerLove" in after_trade[0]
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 2, 2), p(2, "elk road", 1, 2)])
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 2, 3), p(2, "elk road", 2, 2)])
    assert first_blood(sprees.said) == after_trade


def test_the_list_path_counts_a_satchel_kill_seen_with_the_owners_death(sprees, game):
    sprees._fb_memory._reader.readable = False
    change_to_cold_water_gap(sprees)
    sprees._memory_tick()
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 0, 0), p(2, "elk road", 0, 0)])
    sprees.check_sprees([p(1, "FMJ-BadgerLove", 1, 1), p(2, "elk road", 0, 1)])
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "FMJ-BadgerLove" in lines[0]
    assert sprees._player_stats["1"]["streak"] == 0              # sprees as before: a death ends the streak


def test_memory_keeps_reading_long_after_the_map_change(sprees, game, monkeypatch):
    change_to_cold_water_gap(sprees)
    sprees._memory_tick()
    later = app_module.time.monotonic() + 8.5 * 60               # tonight they joined 8.5 min in
    monkeypatch.setattr(app_module.time, "monotonic", lambda: later)
    sprees._fast_poll()                                          # the list's fast reads end at 3 minutes ...
    assert not sprees._fast_poll_timer.isActive()
    assert sprees._memory_timer.isActive()                       # ... the memory read does not
    game.join(1, "FMJ-BadgerLove", team=2)
    game.join(2, "elk road", team=1)
    sprees._memory_tick()
    game.kill(2, 1, at(54.150))
    sprees._memory_tick()
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "elk road" in lines[0]


def test_a_score_put_back_by_keep_stats_is_not_first_blood_from_memory(sprees, game):
    change_to_cold_water_gap(sprees)
    game.join(1, "Oscar", team=1)
    game.join(2, "Ham", team=2)
    sprees._memory_tick()
    game.set_kills(1, 30)                                        # Keep Stats' write ...
    sprees.note_stats_restored("Oscar", 30, 5)                   # ... and its call, same moment
    sprees._memory_tick()
    assert first_blood(sprees.said) == []
    game.kill(2, 1, at(3))
    sprees._memory_tick()
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "Ham" in lines[0]


def test_manual_mode_says_no_first_blood_from_memory_either(sprees, game):
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)
    manual_mode.state.choose(MANUAL)
    change_to_cold_water_gap(sprees)
    game.join(1, "Dale", team=1)
    game.join(2, "Ham", team=2)
    sprees._memory_tick()
    game.kill(1, 2, at(1))
    sprees._memory_tick()
    assert sprees.said == []
    assert not sprees._first_blood_ready and not sprees._first_blood_pending
    sprees._memory_tick()
    assert not sprees._memory_timer.isActive()


def test_memory_lost_mid_map_hands_first_blood_to_the_list(sprees, game):
    change_to_cold_water_gap(sprees)
    game.join(1, "Dale", team=1)
    game.join(2, "Ham", team=2)
    sprees._memory_tick()
    sprees.check_sprees([p(1, "Dale", 0), p(2, "Ham", 0)])
    assert sprees._fb_memory_live
    game.mem.fail = True                                         # the read fails
    sprees._memory_tick()
    assert not sprees._fb_memory_live
    sprees.check_sprees([p(1, "Dale", 0, 1), p(2, "Ham", 1)])
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "Ham" in lines[0]


def test_a_failed_send_from_memory_waits_for_the_next_kill(sprees, game):
    change_to_cold_water_gap(sprees)
    game.join(1, "Dale", team=1)
    game.join(2, "Ham", team=2)
    sprees._memory_tick()
    sprees.outcome["fail"] = True
    game.kill(1, 2, at(1))
    sprees._memory_tick()
    assert sprees._first_blood_ready and not sprees._first_blood_pending
    sprees.outcome["fail"] = False
    game.spawn(2)
    game.kill(2, 1, at(9))
    sprees._memory_tick()
    assert "Ham" in first_blood(sprees.said)[-1]
    assert not sprees._first_blood_ready


def test_no_memory_reading_when_first_blood_is_off(sprees):
    sprees._first_blood_enabled = False
    change_to_cold_water_gap(sprees)
    assert not sprees._memory_timer.isActive()
