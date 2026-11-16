"""Save player stats (OscarMike's request, Dale 2026-11-04): a player who drops and
rejoins the same round gets their stats back, and nothing else in the slot moves."""

import json
import struct
import sys
from types import SimpleNamespace

import pytest

from wolfrat.jo_players import CAPACITY_VA, SLOT_STRIDE, SLOTPTR_VA
from wolfrat.keep_stats import (COUNTER_COUNT, COUNTERS_OFFSET, DEATHS, FRIENDLY_KILL_LIMIT_VA, FRIENDLY_KILLS,
                                HILL_OFFSET, JOINED_OFFSET, KILLS, NOT_A_COUNT, POINTS, SUICIDES, TEAM_OFFSET,
                                TEAM_PICK_SECONDS, StatsKeeper, status_text)
from wolfrat.map_weapons import ROUND_OVER_FLAG

BASE = 0x10000000
RECORD_START = 72


class FakeServer:
    """The slot table in a dict of dwords and bytes.  Every write is recorded."""

    def __init__(self, capacity=4, friendly_limit=5):
        self.bytes = {}
        self.writes = []
        self.fail_reads = False
        self.next_tick = 1000
        self._put(CAPACITY_VA, struct.pack("<i", capacity))
        self._put(SLOTPTR_VA, struct.pack("<i", BASE))
        self._put(ROUND_OVER_FLAG, struct.pack("<i", 0))
        self._put(FRIENDLY_KILL_LIMIT_VA, struct.pack("<i", friendly_limit))

    def _put(self, address, data):
        for i, value in enumerate(data):
            self.bytes[address + i] = value

    def read(self, address, size):
        if self.fail_reads:
            raise OSError("gone")
        return bytes(self.bytes.get(address + i, 0) for i in range(size))

    def write(self, address, data):
        self.writes.append((address, len(data)))
        self._put(address, data)

    # ---- things that happen on the server --------------------------------------------
    def slot(self, index):
        return BASE + index * SLOT_STRIDE

    def join(self, index, name, state=6, team=1, net=True):
        """server_PlayerAdd: a clean slot, dword 35 set to 1, a new join time."""
        self.leave(index)
        address = self.slot(index)
        self.bytes[address + 4] = 1
        self._put(address + 28, struct.pack("<I", 0x5000 if net else 0))
        self._put(address + 32, struct.pack("<i", state))
        self._put(address + 40, name.encode())
        self.bytes[address + TEAM_OFFSET] = team
        self.next_tick += 1
        self._put(address + JOINED_OFFSET, struct.pack("<I", self.next_tick))
        self.set(index, NOT_A_COUNT, 1)
        self._put(address + RECORD_START, struct.pack("<I", 0xE0E0E0E0))       # the entity pointer
        self._put(address + RECORD_START + 46 * 4, struct.pack("<I", 0xB0B0B0B0))   # a buffer

    def leave(self, index):
        address = self.slot(index)
        for offset in list(range(0, TEAM_OFFSET + 1)) + list(range(HILL_OFFSET, JOINED_OFFSET + 4)):
            self.bytes.pop(address + offset, None)

    def set(self, index, field, value):
        self._put(self.slot(index) + RECORD_START + field * 4, struct.pack("<i", value))

    def get(self, index, field):
        return struct.unpack("<i", self.read(self.slot(index) + RECORD_START + field * 4, 4))[0]

    def play(self, index, kills=0, deaths=0, points=0):
        for field, value in ((KILLS, kills), (DEATHS, deaths), (POINTS, points)):
            self.set(index, field, value)

    def set_state(self, index, state):
        self._put(self.slot(index) + 32, struct.pack("<i", state))

    def change_team(self, index, team):
        """Server_ChangeEntityTeam: the record goes to zero (dword 35 too) and the team changes."""
        for field in range(1, COUNTER_COUNT + 1):
            self.set(index, field, 0)
        self.bytes[self.slot(index) + TEAM_OFFSET] = team

    def zero_score(self, index):
        for field in range(1, COUNTER_COUNT + 1):
            self.set(index, field, 1 if field == NOT_A_COUNT else 0)

    def round_over(self, up=True):
        self._put(ROUND_OVER_FLAG, struct.pack("<i", 1 if up else 0))


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
def told():
    return []


@pytest.fixture
def keeper(clock, told):
    return StatsKeeper(clock=clock, restored=lambda *args: told.append(args))


def test_a_player_who_drops_gets_their_stats_back_in_a_new_slot(server, keeper, told):
    server.join(1, "OscarMike")
    server.play(1, kills=30, deaths=5, points=240)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    assert keeper.saved["OscarMike"].kills == 30

    server.join(3, "OscarMike")
    keeper.tick(server)
    assert (server.get(3, KILLS), server.get(3, DEATHS), server.get(3, POINTS)) == (30, 5, 240)
    assert told == [("OscarMike", 30, 5)]
    assert keeper.saved == {}


def test_nothing_is_written_but_the_counters_and_the_hill_score(server, keeper):
    server.join(1, "OscarMike")
    server.play(1, kills=3)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(1, "OscarMike")
    server.writes.clear()
    keeper.tick(server)
    assert server.writes == [(server.slot(1) + COUNTERS_OFFSET, COUNTER_COUNT * 4), (server.slot(1) + HILL_OFFSET, 4)]
    assert server.get(1, NOT_A_COUNT) == 1
    assert server.get(1, 0) == struct.unpack("<i", struct.pack("<I", 0xE0E0E0E0))[0]
    assert server.get(1, 46) == struct.unpack("<i", struct.pack("<I", 0xB0B0B0B0))[0]


def test_the_hill_score_comes_back_too(server, keeper):
    server.join(1, "Hill")
    server._put(server.slot(1) + HILL_OFFSET, struct.pack("<i", 77))
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Hill")
    keeper.tick(server)
    assert struct.unpack("<i", server.read(server.slot(2) + HILL_OFFSET, 4))[0] == 77


def test_kills_made_before_the_write_are_kept(server, keeper):
    server.join(1, "Fast")
    server.play(1, kills=10)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Fast")
    server.play(2, kills=2)
    keeper.tick(server)
    assert server.get(2, KILLS) == 12


def test_it_waits_until_the_player_is_in_the_match(server, keeper):
    server.join(1, "Slow")
    server.play(1, kills=8)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Slow", state=2)                 # still loading
    server.writes.clear()
    keeper.tick(server)
    assert server.writes == []
    server.set_state(2, 6)
    keeper.tick(server)
    assert server.get(2, KILLS) == 8


def test_the_same_slot_again_between_two_looks_counts_as_a_rejoin(server, keeper):
    server.join(1, "Blink")
    server.play(1, kills=9, deaths=1)
    keeper.tick(server)
    server.join(1, "Blink")                         # left and came back inside one second
    keeper.tick(server)
    assert (server.get(1, KILLS), server.get(1, DEATHS)) == (9, 1)


def test_rejoining_before_the_old_slot_timed_out(server, keeper):
    server.join(1, "Ghost")
    server.play(1, kills=11)
    keeper.tick(server)
    server.join(2, "Ghost")                         # the dropped slot is still there
    keeper.tick(server)
    assert server.get(2, KILLS) == 0
    server.leave(1)
    keeper.tick(server)
    assert server.get(2, KILLS) == 11


def test_a_second_visit_does_not_add_them_twice(server, keeper):
    server.join(1, "Twice")
    server.play(1, kills=5)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(1, "Twice")
    keeper.tick(server)
    keeper.tick(server)
    assert server.get(1, KILLS) == 5
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Twice")
    keeper.tick(server)
    assert server.get(2, KILLS) == 5


def test_a_rename_is_not_a_rejoin(server, keeper):
    server.join(1, "Before")
    server.play(1, kills=6)
    keeper.tick(server)
    server._put(server.slot(1) + 40, b"After\0\0")
    keeper.tick(server)
    assert keeper.saved == {} and server.get(1, KILLS) == 6


def test_the_end_of_the_round_forgets_everything(server, keeper):
    server.join(1, "Gone")
    server.play(1, kills=4)
    server.join(2, "Stays")
    server.play(2, kills=1)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    assert "Gone" in keeper.saved
    server.round_over()
    keeper.tick(server)
    assert keeper.saved == {} and keeper.tracking == 0
    server.round_over(False)
    server.play(2)
    server.join(3, "Gone")
    keeper.tick(server)
    assert server.get(3, KILLS) == 0


def test_every_score_going_to_zero_also_forgets_what_was_saved(server, keeper):
    server.join(1, "Gone")
    server.play(1, kills=4)
    server.join(2, "Stays")
    server.play(2, kills=1)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.zero_score(2)                            # a new round the latch never showed
    keeper.tick(server)
    assert keeper.saved == {}


@pytest.mark.parametrize("field,value", [(FRIENDLY_KILLS, 6), (SUICIDES, 10)])
def test_stats_from_a_punt_are_not_saved(server, keeper, field, value):
    server.join(1, "Punted")
    server.play(1, kills=20)
    server.set(1, field, value)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    assert keeper.saved == {}


def test_friendly_kills_on_a_server_with_no_limit_are_saved(clock):
    server = FakeServer(friendly_limit=-1)
    keeper = StatsKeeper(clock=clock)
    server.join(1, "Careless")
    server.play(1, kills=20)
    server.set(1, FRIENDLY_KILLS, 50)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    assert "Careless" in keeper.saved


def test_picking_a_team_just_after_the_rejoin_does_not_lose_them(server, keeper, told):
    server.join(1, "Picker")
    server.play(1, kills=7, deaths=2)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Picker", team=1)
    keeper.tick(server)
    server.change_team(2, 2)
    keeper.tick(server)
    assert (server.get(2, KILLS), server.get(2, DEATHS)) == (7, 2)
    assert len(told) == 2


def test_a_team_change_long_after_the_rejoin_is_left_to_the_game(server, keeper, clock):
    server.join(1, "Later")
    server.play(1, kills=7)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Later", team=1)
    keeper.tick(server)
    clock.now += TEAM_PICK_SECONDS + 1
    keeper.tick(server)
    server.change_team(2, 2)
    keeper.tick(server)
    assert server.get(2, KILLS) == 0


def test_an_admins_zero_score_stays_zero(server, keeper):
    server.join(1, "Zeroed")
    server.play(1, kills=7)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Zeroed")
    keeper.tick(server)
    server.zero_score(2)
    keeper.tick(server)
    assert server.get(2, KILLS) == 0
    keeper.tick(server)
    assert server.get(2, KILLS) == 0


def test_the_host_and_bots_are_left_alone(server, keeper):
    server.join(0, "Host", net=False)
    server.bytes[server.slot(0) + 5] = 1
    server.join(1, "Robot #1", net=False)
    server.play(0, kills=3)
    server.play(1, kills=3)
    keeper.tick(server)
    assert keeper.tracking == 0


def test_players_with_nothing_to_save_are_not_saved(server, keeper):
    server.join(1, "Empty")
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    assert keeper.saved == {}


def test_a_failed_read_decides_nothing(server, keeper):
    server.join(1, "Steady")
    server.play(1, kills=4)
    keeper.tick(server)
    server.fail_reads = True
    keeper.tick(server)
    server.fail_reads = False
    assert keeper.saved == {} and keeper.tracking == 1
    keeper.tick(server)
    assert server.get(1, KILLS) == 4


def test_no_slot_table_mid_load_decides_nothing(server, keeper):
    server.join(1, "Steady")
    server.play(1, kills=4)
    keeper.tick(server)
    server._put(SLOTPTR_VA, struct.pack("<i", 0))
    keeper.tick(server)
    assert keeper.saved == {} and keeper.tracking == 1


def test_a_different_server_forgets_everything(server, keeper, clock):
    server.join(1, "Gone")
    server.play(1, kills=4)
    server.join(2, "Stays")
    server.play(2, kills=1)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    other = FakeServer()
    other.join(1, "Gone")
    keeper.tick(other)
    assert other.get(1, KILLS) == 0 and keeper.saved == {}


def test_a_write_that_fails_is_tried_again(server, keeper, told):
    server.join(1, "Retry")
    server.play(1, kills=4)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    server.join(2, "Retry")
    real_write = server.write
    server.write = lambda *_args: (_ for _ in ()).throw(OSError("denied"))
    keeper.tick(server)
    assert told == []
    server.write = real_write
    keeper.tick(server)
    assert server.get(2, KILLS) == 4 and told == [("Retry", 4, 0)]


def test_unticking_forgets_and_stops(server, keeper):
    server.join(1, "Gone")
    server.play(1, kills=4)
    keeper.tick(server)
    server.leave(1)
    keeper.tick(server)
    keeper.enabled = False
    keeper.tick(server)
    assert keeper.saved == {} and keeper.tracking == 0
    server.join(2, "Gone")
    server.writes.clear()
    keeper.tick(server)
    assert server.writes == []
    assert status_text(keeper).startswith("Off")


def test_status_text(server, keeper):
    keeper.tick(None)
    assert status_text(keeper).startswith("Waiting")
    server.join(1, "Gone")
    server.play(1, kills=4, deaths=2)
    server.join(2, "Stays")
    keeper.tick(server)
    assert "Watching 2 players" in status_text(keeper)
    server.leave(1)
    keeper.tick(server)
    assert "Gone (4/2)" in status_text(keeper)


# ---- the lifetime K/D numbers ---------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    from wolfrat import app
    instance = app.StatsStore(SimpleNamespace(path=lambda name: tmp_path / name))
    yield instance
    instance.close()


def _totals(store, name):
    row = store.get_player(name)
    return row["kills"], row["deaths"]


def test_lifetime_totals_do_not_count_the_returned_kills_twice(store):
    store.update_player("Oscar", 10, 2)
    store.update_player("Oscar", 30, 5)
    store.update_player("Oscar", 0, 0)              # rejoined at zero: looks like a map reset
    assert _totals(store, "Oscar") == (30, 5)
    store.note_restored("Oscar", 30, 5)
    store.update_player("Oscar", 30, 5)
    assert _totals(store, "Oscar") == (30, 5)
    store.update_player("Oscar", 31, 5)             # a real new kill still counts
    assert _totals(store, "Oscar") == (31, 5)


def test_lifetime_totals_when_the_list_never_saw_the_zero(store):
    store.update_player("Oscar", 30, 5)
    store.note_restored("Oscar", 30, 5)
    store.update_player("Oscar", 30, 5)
    assert _totals(store, "Oscar") == (30, 5)


def test_lifetime_totals_with_kills_from_earlier_maps(store):
    store.update_player("Oscar", 50, 10)
    store.update_player("Oscar", 0, 0)              # a real new map
    store.update_player("Oscar", 30, 5)
    assert _totals(store, "Oscar") == (80, 15)
    store.update_player("Oscar", 0, 0)              # dropped and rejoined
    store.note_restored("Oscar", 30, 5)
    store.update_player("Oscar", 31, 5)
    assert _totals(store, "Oscar") == (81, 15)


# ---- sprees and first blood ------------------------------------------------------------

def _spree_tab(store=None):
    return SimpleNamespace(
        messages_tab=SimpleNamespace(_kd_enabled=bool(store), stats_store=store),
        _spree_enabled=True, _spree_thresholds={5: "{player} spree"}, _first_blood_enabled=False,
        _first_blood_ready=False, _first_blood_pending=False, _player_stats={},
        _stats_restored_until={}, _new_map_since=None, log_text=None, server=None)


def _list(name, kills, deaths=0, pid="3"):
    return [{"name": name, "id": pid, "kills": str(kills), "deaths": str(deaths)}]


@pytest.fixture
def chat(monkeypatch):
    from wolfrat import app
    said = []
    monkeypatch.setattr(app, "submit_admin", lambda *args, **kwargs: said.append(args[1]))
    return said


def test_a_plain_kill_jump_still_makes_a_spree(chat):
    from wolfrat import app
    tab = _spree_tab()
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))
    app.SpreeTab.check_sprees(tab, _list("Oscar", 30))
    assert len(chat) == 1


def test_returned_kills_make_no_spree_even_on_a_stale_list(chat, monkeypatch):
    from wolfrat import app
    tab = _spree_tab()
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))
    app.SpreeTab.note_stats_restored(tab, "Oscar", 30, 5)
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))       # asked before the write
    app.SpreeTab.check_sprees(tab, _list("Oscar", 30, 5))
    assert chat == []
    later = app.time.time() + app.STATS_RESTORE_SETTLE_SECONDS + 1
    monkeypatch.setattr(app.time, "time", lambda: later)
    app.SpreeTab.check_sprees(tab, _list("Oscar", 31, 5))   # real kills count again
    assert tab._player_stats["3"]["streak"] == 1 and chat == []
    app.SpreeTab.check_sprees(tab, _list("Oscar", 35, 5))
    assert len(chat) == 1


def test_the_whole_lifetime_route_with_a_stale_list(chat, store, monkeypatch):
    from wolfrat import app
    tab = _spree_tab(store)
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))
    app.SpreeTab.check_sprees(tab, _list("Oscar", 30, 5))
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))       # rejoined, seen at zero
    assert _totals(store, "Oscar") == (30, 5)
    app.SpreeTab.note_stats_restored(tab, "Oscar", 30, 5)
    app.SpreeTab.check_sprees(tab, _list("Oscar", 0))       # stale
    later = app.time.time() + app.STATS_RESTORE_SETTLE_SECONDS + 1
    monkeypatch.setattr(app.time, "time", lambda: later)
    app.SpreeTab.check_sprees(tab, _list("Oscar", 30, 5))
    assert _totals(store, "Oscar") == (30, 5)


# ---- the box in the Players tab -------------------------------------------------------------

@pytest.fixture
def panel_class():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    keep_alive.append(QApplication.instance() or QApplication(sys.argv[:1]))
    from wolfrat.keep_stats_panel import KeepStatsPanel
    return KeepStatsPanel


keep_alive = []


def test_the_box_starts_ticked_and_remembers_being_unticked(panel_class, tmp_path):
    panel = panel_class(str(tmp_path))
    assert panel.save_box.isChecked() and panel.save_box.text() == "Restore disconnected stats"
    assert panel.title() == "Restore disconnected stats"
    assert panel.wants_write()
    panel.save_box.setChecked(False)
    assert not panel.wants_write()
    assert json.loads((tmp_path / "wolfrat_keep_stats.json").read_text()) == {"enabled": False}
    again = panel_class(str(tmp_path))
    assert not again.save_box.isChecked() and not again.keeper.enabled


def test_the_panel_only_writes_on_a_writable_handle(panel_class, tmp_path, server):
    panel = panel_class(str(tmp_path))
    server.join(1, "Gone")
    server.play(1, kills=4)
    panel.server_tick(server, False)
    panel._tick()
    assert panel.keeper.tracking == 0 and "Waiting" in panel.status_lbl.text()
    panel.server_tick(server, True)
    panel._tick()
    assert panel.keeper.tracking == 1 and "Watching 1 player." in panel.status_lbl.text()
    server.leave(1)
    panel._tick()
    server.join(2, "Gone")
    panel._tick()
    assert server.get(2, KILLS) == 4
    panel.no_server("gone")
    panel._tick()
    assert panel.keeper.tracking == 0
