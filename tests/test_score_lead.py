"""Lead announcer for CTF / Flagball / TDM / DM (Dale, 2026-09-23).  A tie is
nobody's lead; CTF / Flagball say every new leader at once plus the first
flag / goal; TDM / DM need 30 s in front and at most one line a minute; every
line fits 62 characters."""

import json
import random
import struct
import sys

import pytest

from wolfrat import score_lead as sl
from wolfrat.score_lead import FirstScore, LeadChange, LeadWatch


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def watch():
    clock = Clock()
    return LeadWatch(clock), clock


# ---- scores --------------------------------------------------------------------------

def test_leader_and_ties():
    assert sl.leader_of({1: 3, 2: 1}) == (1, 3, 1)
    assert sl.leader_of({1: 2, 2: 2}) == (None, 2, 2)
    assert sl.leader_of({}) == (None, 0, 0)
    assert sl.leader_of({"Dale": 9, "Ham": 7, "Oscar": 9})[0] is None


def test_kills_from_the_player_list_skip_the_host():
    players = [{"id": "0", "name": "Host", "team": "1", "kills": "50"},
               {"id": "3", "name": "Dale", "team": "2", "kills": "7"},
               {"id": "4", "name": "Ham", "team": "1", "kills": "4"},
               {"id": "5", "name": "Oscar", "team": "2", "kills": "-"}]
    assert sl.team_kills(players) == {1: 4, 2: 7}
    assert sl.player_kills(players) == {"Dale": 7, "Ham": 4}


# ---- CTF / Flagball: straight away -------------------------------------------------------

def test_first_capture_then_lead_changes_at_once():
    w, clock = watch()
    assert w.update(sl.CTF, {1: 0, 2: 0}) == []                  # first look: remember only
    events = w.update(sl.CTF, {1: 0, 2: 1})
    assert FirstScore(sl.CTF, 2) in events and LeadChange(sl.CTF, 2, 1, 0) in events
    assert sl.announce(events, True, True) == [FirstScore(sl.CTF, 2)]      # first line says it all
    assert sl.announce(events, False, True) == [LeadChange(sl.CTF, 2, 1, 0)]
    assert w.update(sl.CTF, {1: 1, 2: 1}) == []                  # a tie is nobody's lead
    assert w.update(sl.CTF, {1: 2, 2: 1}) == [LeadChange(sl.CTF, 1, 2, 1)]
    assert w.update(sl.CTF, {1: 3, 2: 1}) == []                  # still Joint Ops - nothing new


def test_last_maps_scores_on_the_first_poll_are_ignored():
    w, _ = watch()
    assert w.update(sl.FB, {1: 5, 2: 2}) == []                   # stale: the map just changed
    assert w.update(sl.FB, {1: 0, 2: 0}) == []
    assert w.update(sl.FB, {1: 1, 2: 0})[0] == FirstScore(sl.FB, 1)


def test_joining_mid_map_never_claims_a_first_capture():
    w, _ = watch()
    w.update(sl.FB, {1: 2, 2: 1})
    assert w.update(sl.FB, {1: 2, 2: 3}) == [LeadChange(sl.FB, 2, 3, 2)]


# ---- TDM / DM: 30 s hold, one line a minute ----------------------------------------------

def test_tdm_leader_must_hold_30_seconds():
    w, clock = watch()
    w.update(sl.TDM, {1: 10, 2: 10})
    assert w.update(sl.TDM, {1: 12, 2: 10}) == []
    clock.now += 15
    assert w.update(sl.TDM, {1: 12, 2: 13}) == []                # swapped - Rebels start from zero
    clock.now += 20
    assert w.update(sl.TDM, {1: 13, 2: 15}) == []                # Rebels only 20 s in front
    clock.now += 10
    assert w.update(sl.TDM, {1: 14, 2: 16}) == [LeadChange(sl.TDM, 2, 16, 14)]


def test_tdm_at_most_one_line_a_minute():
    w, clock = watch()
    w.update(sl.TDM, {1: 0, 2: 0})
    w.update(sl.TDM, {1: 1, 2: 0})
    clock.now += 30
    assert w.update(sl.TDM, {1: 2, 2: 0})                        # Joint Ops announced
    w.update(sl.TDM, {1: 2, 2: 3})
    clock.now += 30                                              # Rebels held 30 s, but only 30 s since the last line
    assert w.update(sl.TDM, {1: 2, 2: 4}) == []
    clock.now += 30
    assert w.update(sl.TDM, {1: 3, 2: 5}) == [LeadChange(sl.TDM, 2, 5, 3)]


def test_deathmatch_names_the_player():
    w, clock = watch()
    w.update(sl.DM, {"Dale": 3, "Ham": 3})
    w.update(sl.DM, {"Dale": 5, "Ham": 3})
    clock.now += 31
    assert w.update(sl.DM, {"Dale": 6, "Ham": 3}) == [LeadChange(sl.DM, "Dale", 6, 3)]


def test_other_modes_do_nothing_and_a_mode_change_starts_over():
    w, _ = watch()
    assert w.update(vr_as := "as", {1: 1, 2: 0}) == []
    w.update(sl.CTF, {1: 0, 2: 0})
    assert w.update(sl.FB, {1: 1, 2: 0}) == []                   # new mode: first look again


# ---- lines -----------------------------------------------------------------------------

def test_every_default_line_fits_62_with_big_numbers():
    rng = random.Random(1)
    for mode, lines in sl.DEFAULT_LINES.items():
        leader = 1 if mode in sl.TEAM_MODES else "SixteenCharName!"
        for template in lines:
            text = sl.fill(template, LeadChange(mode, leader, 999, 998), mode)
            assert len(text) <= 62 and "{" not in text, text
    for mode, template in sl.DEFAULT_FIRST.items():
        assert len(sl.fill(template, FirstScore(mode, 2), mode)) <= 62


def test_one_flag_is_singular():
    assert sl.fill("{team} take the lead, {score} flags to {other}", LeadChange(sl.CTF, 2, 1, 0), sl.CTF) == \
        "Rebels take the lead, 1 flag to 0"


def test_too_long_lines_are_skipped_or_cut():
    rng = random.Random(3)
    event = LeadChange(sl.DM, "A" * 40, 12, 9)
    templates = ["{player} takes the lead with {score} kills in a thrilling finish", "{player} leads"]
    assert sl.pick_line(templates, event, sl.DM, rng) == "A" * 40 + " leads"
    only_long = ["{player} takes the lead with {score} kills in a thrilling finish"]
    assert len(sl.pick_line(only_long, event, sl.DM, rng)) == sl.CHAT_LIMIT == 59


# ---- the Sprees tab ------------------------------------------------------------------------

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wolfrat.app as app_module  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class Server:
    players = []

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


class Messages:
    _kd_enabled = False


@pytest.fixture
def sprees(qtbot, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(app_module, "submit_admin",
                        lambda owner, op, on_success=None, context="", *a, **k: said.append(op.__defaults__[0]))
    tab = app_module.SpreeTab(Server(), Messages(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    clock = Clock()
    tab._lead_watch = LeadWatch(clock)
    tab.said, tab.clock, tab.folder = said, clock, tmp_path
    return tab


def test_ctf_poll_says_the_first_flag(sprees):
    caps = {"now": {1: 0, 2: 0}}
    sprees.score_mode_source = lambda: sl.CTF
    sprees.team_caps_source = lambda: caps["now"]
    sprees.score_tick([])
    caps["now"] = {1: 0, 2: 1}
    sprees.score_tick([])
    assert sprees.said == ["Rebels capture the first flag of the map!"]
    caps["now"] = {1: 2, 2: 1}
    sprees.score_tick([])
    assert len(sprees.said) == 2 and "Joint Ops" in sprees.said[1] and len(sprees.said[1]) <= 62


def test_ctf_needs_the_server_on_this_pc(sprees):
    sprees.score_mode_source = lambda: sl.FB
    sprees.team_caps_source = lambda: None
    for _ in range(3):
        sprees.score_tick([])
    assert sprees.said == []


def test_tdm_from_the_player_list(sprees):
    sprees.score_mode_source = lambda: sl.TDM
    players = lambda a, b: [{"id": "3", "name": "Dale", "team": "1", "kills": str(a)},
                            {"id": "4", "name": "Ham", "team": "2", "kills": str(b)}]
    sprees.score_tick(players(0, 0))
    sprees.score_tick(players(3, 1))
    sprees.clock.now += 30
    sprees.score_tick(players(4, 1))
    assert sprees.said and "Joint Ops" in sprees.said[0]


def test_switched_off_per_mode(sprees):
    sprees._score_mode_boxes[sl.DM].setChecked(False)
    sprees.score_mode_source = lambda: sl.DM
    sprees.score_tick([{"id": "3", "name": "Dale", "kills": "0"}])
    sprees.score_tick([{"id": "3", "name": "Dale", "kills": "5"}])
    sprees.clock.now += 40
    sprees.score_tick([{"id": "3", "name": "Dale", "kills": "6"}])
    assert sprees.said == []


def test_map_change_starts_the_watch_again(sprees):
    caps = {"now": {1: 3, 2: 0}}
    sprees.score_mode_source = lambda: sl.FB
    sprees.team_caps_source = lambda: caps["now"]
    sprees.on_missions_updated(["3: FB-Line.bms - () () () <CURRENT MISSION> <>"])
    sprees.score_tick([])
    sprees.on_missions_updated(["4: FB-Other.bms - () () () <CURRENT MISSION> <>"])
    caps["now"] = {1: 0, 2: 0}
    sprees.score_tick([])
    caps["now"] = {1: 0, 2: 1}
    sprees.score_tick([])
    assert sprees.said == ["Rebels score the first goal of the map!"]


def test_settings_saved_and_reloaded(qtbot, sprees):
    sprees._score_mode_boxes[sl.TDM].setChecked(False)
    sprees.score_lines_mode.setCurrentIndex(sprees.score_lines_mode.findData(sl.FB))
    sprees.score_lines_edit.setPlainText("{team} lead {score}-{other}")
    sprees.score_first_checkbox.setChecked(False)
    saved = json.loads((sprees.folder / "wolfrat_sprees.json").read_text())
    assert saved["score_lead_enabled"]["tdm"] is False
    assert saved["score_lead_lines"]["fb"] == ["{team} lead {score}-{other}"]
    again = app_module.SpreeTab(Server(), Messages(), DesktopRuntime.isolated(sprees.folder))
    qtbot.addWidget(again)
    assert not again._score_mode_boxes[sl.TDM].isChecked()
    assert again._score_lead_lines[sl.FB] == ["{team} lead {score}-{other}"]
    assert again._score_lead_lines[sl.CTF] == sl.DEFAULT_LINES[sl.CTF]   # others untouched
    assert not again.score_first_checkbox.isChecked()
