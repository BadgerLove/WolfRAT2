"""Co-op map vote (Dale, 2026-09-23): co-op has no round timer, so the normal
map vote starts once the second-last objective is done.  The vote itself uses
Map Voting's own configuration; the Co-op row (on by default, "[1] objective(s)
left") makes the rule visible and adjustable.  No co-op map has been played
live yet."""

import struct
import sys
import tempfile

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication

import wolfrat.app as app_module
from wolfrat.admin_commands import GameSettings
from wolfrat.bans_tab import BansTab
from wolfrat.jo_players import (LocalServerPlayers, OBJECTIVE_IDS_VA, OBJECTIVES_DONE_VA,
                                count_objectives)
from wolfrat.runtime import DesktopRuntime

_app = QApplication.instance() or QApplication(sys.argv[:1])
COOP, AAS = 0x10020, 0x10010


def ids(*objective_ids):
    """Slot 0 is unused; slots 1..8 hold objective ids, 0 or 255 ends the list."""
    return bytes([0, *objective_ids] + [0] * (8 - len(objective_ids)))


# ---- counting, the way the in-game objectives list does ----------------------

def test_counts_listed_objectives_and_their_done_bits():
    assert count_objectives(ids(3, 7, 9, 12), 0) == (0, 4)
    assert count_objectives(ids(3, 7, 9, 12), (1 << 1) | (1 << 3)) == (2, 4)
    assert count_objectives(ids(3, 7, 9, 12), 1 << 0) == (0, 4)        # bit 0 is not an objective
    assert count_objectives(ids(3, 255, 9), 1 << 3) == (0, 1)           # 255 ends the list
    assert count_objectives(ids(), 0xFFFF) == (0, 0)


class FakeMemory:
    def __init__(self, blobs):
        self.blobs = blobs

    def read(self, address, size):
        return self.blobs[address][:size]

    def close(self):
        pass


def test_reader_reads_both_places(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mem = FakeMemory({OBJECTIVE_IDS_VA: ids(4, 5, 6), OBJECTIVES_DONE_VA: struct.pack("<I", 1 << 2)})
    reader = LocalServerPlayers(find=lambda: [1], open_memory=lambda pid: mem)
    assert reader.read_objectives() == (1, 3)


class Reader:
    def __init__(self, game_type, objectives):
        self.game_type, self.objectives, self.available, self.status = game_type, objectives, True, ""
        self.asked = 0

    def read(self):
        return []

    def read_game_type(self):
        return self.game_type

    def read_objectives(self):
        self.asked += 1
        return self.objectives


def test_bans_tab_only_reads_objectives_on_co_op(tmp_path):
    reader = Reader(AAS, (1, 3))
    tab = BansTab(str(tmp_path), punt=lambda *a: None, announce=lambda *a: None, reader=reader)
    tab.on_players([])
    assert tab.objectives_now() is None and reader.asked == 0
    reader.game_type = COOP
    tab.on_players([])
    assert tab.objectives_now() == (1, 3)


# ---- Map Voting ------------------------------------------------------------------

class Server:
    def __init__(self):
        self.players = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.player_entries = ()
        self.game_settings = GameSettings({"KillLimit": "100"})

    def set_raw_chat_callback(self, cb):
        pass


class Missions:
    _rotation_maps = ["CO-One.bms", "CO-Two.bms"]

    def _find_display_name(self, f):
        return f

    def _mission_entry_at(self, i):
        return None


@pytest.fixture
def votes(monkeypatch):
    monkeypatch.setattr(app_module, "wire_log", lambda *_: None)
    tab = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(tempfile.mkdtemp()))
    tab._tick_timer.stop()
    tab.fired = []
    tab._start_vote = lambda: (tab.fired.append(tab._current_map), setattr(tab, "_vote_stage", "done"))
    tab.enable_cb.setChecked(True)
    tab.progress = {"now": (0, 4)}
    tab.coop_objectives = lambda: tab.progress["now"]
    return tab


def new_map(tab, filename):
    tab.on_missions_updated([f"3: {filename} - () () () <CURRENT MISSION> <>"])


def test_vote_starts_when_the_second_last_objective_is_done(votes):
    new_map(votes, "CO-Harbour.bms")
    for done in (0, 1, 2):
        votes.progress["now"] = (done, 4)
        votes._tick()
        assert votes.fired == []
        assert f"{done} of 4 objectives done" in votes.status_lbl.text()
    votes.progress["now"] = (3, 4)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_no_game_time_from_the_server_does_not_stop_it(votes):
    # Co-op has no round timer, so the time checks must not stand in the way.
    new_map(votes, "CO-Harbour.bms")
    assert not votes._server_time_updated
    votes.progress["now"] = (1, 2)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_held_for_players_then_starts_when_they_arrive(votes, monkeypatch):
    import time
    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    new_map(votes, "CO-Harbour.bms")
    votes.server.players = [{"id": 1}]
    votes.progress["now"] = (3, 4)
    votes._tick()
    assert votes.fired == [] and "Paused" in votes.status_lbl.text()
    # The join that opens the gate is still loading: the vote waits for them.
    votes.server.players = [{"id": 1}, {"id": 2}]
    votes._tick()
    assert votes.fired == [] and "just joined" in votes.status_lbl.text()
    clock["now"] += 39
    votes._tick()
    assert votes.fired == []
    clock["now"] += 1
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_once_per_mission_and_again_on_the_next(votes):
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (3, 4)
    votes._tick()
    votes._vote_stage = "idle"                      # vote over, same mission still running
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]
    new_map(votes, "CO-Docks.bms")
    votes.progress["now"] = (0, 3)
    votes._tick()
    votes.progress["now"] = (2, 3)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms", "CO-Docks.bms"]


def test_single_objective_missions_never_fire_early(votes):
    new_map(votes, "CO-Tiny.bms")
    for progress in ((0, 1), (1, 1)):
        votes.progress["now"] = progress
        votes._tick()
    assert votes.fired == []


def test_auto_voting_off_means_off(votes):
    votes.enable_cb.setChecked(False)
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (3, 4)
    votes._tick()
    assert votes.fired == []


def test_not_co_op_goes_through_the_normal_rules_untouched(votes):
    votes.progress["now"] = None                    # AAS / TDM / server on another PC
    new_map(votes, "AS-Doslin.bms")
    votes._tick()
    assert "Waiting for server data" in votes.status_lbl.text()
    votes.update_game_time(30, 2)
    votes.trigger_spin.setValue(3)
    votes._tick()
    assert votes.fired == ["AS-Doslin.bms"]


# ---- the Co-op row on the tab (Dale: "put co-op in the tab so people know it's there")

def test_co_op_row_is_on_by_default_and_shows_co_op(votes):
    assert votes.coop_rule_cb.isChecked() and votes.coop_left_spin.value() == 1
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (1, 4)
    votes._tick()
    assert "Co-op (1 of 4 objectives done)" in votes.current_mode_lbl.text()


def test_unticked_means_the_normal_rules_as_before(votes):
    votes.coop_rule_cb.setChecked(False)
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (3, 4)
    votes._tick()
    assert votes.fired == [] and "Waiting for server data" in votes.status_lbl.text()


def test_objectives_left_is_adjustable(votes):
    votes.coop_left_spin.setValue(2)
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (1, 4)
    votes._tick()
    assert votes.fired == [] and "Vote starts when 2 are left" in votes.status_lbl.text()
    votes.progress["now"] = (2, 4)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_never_fires_before_any_objective_is_done(votes):
    votes.coop_left_spin.setValue(3)
    new_map(votes, "CO-Pair.bms")
    votes.progress["now"] = (0, 2)                  # 2 left <= 3, but nothing done yet
    votes._tick()
    assert votes.fired == []
    votes.progress["now"] = (1, 2)
    votes._tick()
    assert votes.fired == ["CO-Pair.bms"]


def test_co_op_settings_are_saved_and_reloaded(votes):
    votes.coop_rule_cb.setChecked(False)
    votes.coop_left_spin.setValue(3)
    folder = votes._recently_played_file.rsplit("wolfrat_recently_played.json", 1)[0]
    again = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(folder))
    again._tick_timer.stop()
    assert not again.coop_rule_cb.isChecked() and again.coop_left_spin.value() == 3


# ---- Co-op row under Flagball: minutes in / objectives left / AI left (Dale 2026-09-23)

from wolfrat.jo_players import AI_GROUP_COUNT, AI_GROUP_STRIDE, AI_GROUPS_VA, ai_left  # noqa: E402


def groups(*rows):
    """rows = (group, initial, live); everything else zero."""
    table = bytearray(AI_GROUP_STRIDE * AI_GROUP_COUNT)
    for group, initial, live in rows:
        struct.pack_into("<ii", table, group * AI_GROUP_STRIDE + 4, initial, live)
    return bytes(table)


def test_ai_left_sums_the_mission_groups():
    assert ai_left(groups((1, 10, 10), (2, 6, 3))) == (13, 16)
    assert ai_left(groups((0, 50, 50), (3, 4, 1))) == (1, 4)       # group 0 is not a mission group
    assert ai_left(groups((1, 0, 1))) is None                      # spawned later (a player) - ignored
    assert ai_left(groups((1, 4, 9))) == (4, 4)                    # never above its start
    assert ai_left(groups()) is None


def test_reader_reads_the_group_table(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mem = FakeMemory({AI_GROUPS_VA: groups((5, 20, 5))})
    reader = LocalServerPlayers(find=lambda: [1], open_memory=lambda pid: mem)
    assert reader.read_ai_left() == (5, 20)


def test_co_op_rows_sit_under_flagball(votes):
    grid_labels = [w.text() for w in votes.findChildren(app_module.QLabel)]
    order = [i for i, t in enumerate(grid_labels) if t.startswith(("Flagball", "Co-op  ", "Other modes"))]
    names = [grid_labels[i].split()[0] for i in order]
    assert names == ["Flagball", "Co-op", "Other"]
    assert not votes.coop_minutes_cb.isChecked() and not votes.coop_ai_cb.isChecked()
    assert votes.coop_rule_cb.isChecked()                          # objectives stays on by default


def test_minutes_in_uses_the_mission_clock(votes, monkeypatch):
    votes.coop_rule_cb.setChecked(False)
    votes.coop_minutes_cb.setChecked(True)
    votes.coop_minutes_spin.setValue(10)
    new_map(votes, "CO-Harbour.bms")
    start = votes._match_start_time
    monkeypatch.setattr(app_module.time, "time", lambda: start + 9 * 60)
    votes._tick()
    assert votes.fired == [] and "at 10m in" in votes.status_lbl.text()
    monkeypatch.setattr(app_module.time, "time", lambda: start + 10 * 60)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_ai_left_below_percent(votes):
    votes.coop_rule_cb.setChecked(False)
    votes.coop_ai_cb.setChecked(True)
    votes.coop_ai_spin.setValue(25)
    ai = {"now": (40, 40)}
    votes.coop_ai_left = lambda: ai["now"]
    new_map(votes, "CO-Harbour.bms")
    votes._tick()
    assert votes.fired == [] and "100% AI left" in votes.current_mode_lbl.text()
    ai["now"] = (11, 40)                                          # 28%
    votes._tick()
    assert votes.fired == []
    ai["now"] = (9, 40)                                           # 22%
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_ai_rule_ignores_a_low_first_reading(votes):
    # The last mission's low count can still be showing on the first poll.
    votes.coop_rule_cb.setChecked(False)
    votes.coop_ai_cb.setChecked(True)
    ai = {"now": (2, 40)}
    votes.coop_ai_left = lambda: ai["now"]
    new_map(votes, "CO-Harbour.bms")
    votes._tick()
    assert votes.fired == [] and "waiting for this mission" in votes.status_lbl.text()
    ai["now"] = (40, 40)
    votes._tick()
    ai["now"] = (5, 40)
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_first_co_op_rule_reached_starts_it(votes):
    votes.coop_ai_cb.setChecked(True)
    ai = {"now": (40, 40)}
    votes.coop_ai_left = lambda: ai["now"]
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (1, 5)
    votes._tick()
    ai["now"] = (3, 40)                                           # AI gone before the objectives
    votes._tick()
    assert votes.fired == ["CO-Harbour.bms"]


def test_all_co_op_boxes_off_means_the_normal_rules(votes):
    votes.coop_rule_cb.setChecked(False)
    new_map(votes, "CO-Harbour.bms")
    votes.progress["now"] = (3, 4)
    votes._tick()
    assert votes.fired == [] and "Waiting for server data" in votes.status_lbl.text()


def test_new_co_op_settings_saved_and_reloaded(votes):
    votes.coop_minutes_cb.setChecked(True)
    votes.coop_minutes_spin.setValue(35)
    votes.coop_ai_cb.setChecked(True)
    votes.coop_ai_spin.setValue(15)
    folder = votes._recently_played_file.rsplit("wolfrat_recently_played.json", 1)[0]
    again = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(folder))
    again._tick_timer.stop()
    assert again.coop_minutes_cb.isChecked() and again.coop_minutes_spin.value() == 35
    assert again.coop_ai_cb.isChecked() and again.coop_ai_spin.value() == 15


def test_bans_tab_reads_ai_only_on_co_op(tmp_path):
    class R(Reader):
        def read_ai_left(self):
            return (3, 10)
    reader = R(AAS, (0, 2))
    tab = BansTab(str(tmp_path), punt=lambda *a: None, announce=lambda *a: None, reader=reader)
    tab.on_players([])
    assert tab.ai_left_now() is None
    reader.game_type = COOP
    tab.on_players([])
    assert tab.ai_left_now() == (3, 10)
