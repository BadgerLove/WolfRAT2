"""First blood on a new map (Dale, 2026-11-08).

The bug: the player list of a poll was read before the mission list, so the
first poll of a new map compared the first kill against the old map's totals,
saw a drop, and swallowed it.  The next killer then got first blood, a poll
late.  Now the mission list comes first, and after a map change the first
list showing reset scores counts from 0.
"""

import sys

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wolfrat.app as app_module  # noqa: E402
from wolfrat import protocol  # noqa: E402
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


@pytest.fixture
def sprees(qtbot, tmp_path, monkeypatch):
    said = []

    def submit(owner, op, on_success=None, context="", *a, **k):
        message = op.__defaults__[0]
        said.append(message)
        if on_success is not None:
            on_success(None)

    monkeypatch.setattr(app_module, "submit_admin", submit)
    tab = app_module.SpreeTab(Server(), Messages(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._first_blood_enabled = True
    tab._spree_enabled = True
    tab.said = said
    return tab


def p(pid, name, kills, deaths=0):
    return {"id": str(pid), "name": name, "kills": str(kills), "deaths": str(deaths)}


def missions(name):
    return [f"0: {name} - (1x) () () <> <CURRENT MISSION>"]


def first_blood(said):
    return [line for line in said if "first blood" in line.lower() or "FIRST BLOOD" in line]


def play_old_map(tab):
    tab.on_missions_updated(missions("NASA_headquarters_TDM.bms"))
    tab.check_sprees([p(3, "Dale", 0), p(4, "Ham", 0), p(5, "Angel", 0)])
    tab.check_sprees([p(3, "Dale", 1), p(4, "Ham", 0, 1), p(5, "Angel", 0)])
    tab.check_sprees([p(3, "Dale", 12, 4), p(4, "Ham", 7, 9), p(5, "Angel", 9, 6)])
    assert first_blood(tab.said) and "Dale" in first_blood(tab.said)[0]
    tab.said.clear()


def test_first_kill_of_a_new_map_gets_first_blood(sprees):
    play_old_map(sprees)
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    # first poll of the new map: Ham has the first kill already
    sprees.check_sprees([p(3, "Dale", 0, 1), p(4, "Ham", 1), p(5, "Angel", 0)])
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "Ham" in lines[0]
    sprees.check_sprees([p(3, "Dale", 1, 1), p(4, "Ham", 1), p(5, "Angel", 0, 1)])
    assert len(first_blood(sprees.said)) == 1                 # said once


def test_old_scores_still_showing_after_the_change_say_nothing(sprees):
    play_old_map(sprees)
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    # the list still has the old map's scores for a poll
    sprees.check_sprees([p(3, "Dale", 12, 4), p(4, "Ham", 7, 9), p(5, "Angel", 9, 6)])
    assert first_blood(sprees.said) == []
    sprees.check_sprees([p(3, "Dale", 0), p(4, "Ham", 0), p(5, "Angel", 1)])
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "Angel" in lines[0]


def test_a_player_leaving_is_not_a_new_map(sprees):
    play_old_map(sprees)
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    # Dale (top scorer) leaves; the others still show the old map's scores
    sprees.check_sprees([p(4, "Ham", 7, 9), p(5, "Angel", 9, 6)])
    assert first_blood(sprees.said) == []


def test_someone_new_on_the_list_counts_from_zero_on_a_new_map(sprees):
    play_old_map(sprees)
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    sprees.check_sprees([p(3, "Dale", 0), p(4, "Ham", 0), p(5, "Angel", 0), p(9, "Newbie", 1)])
    lines = first_blood(sprees.said)
    assert len(lines) == 1 and "Newbie" in lines[0]


def test_wolfrat_starting_mid_map_says_nothing_at_once(sprees):
    sprees.on_missions_updated(missions("NASA_headquarters_TDM.bms"))
    sprees.check_sprees([p(3, "Dale", 12, 4), p(4, "Ham", 7, 9)])
    assert sprees.said == []


def test_reads_the_list_fast_until_first_blood(sprees, qtbot):
    play_old_map(sprees)
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    assert sprees._fast_poll_timer.isActive()
    sprees._fast_poll()
    sprees._fast_poll()
    assert sprees.server.refreshes == 2
    sprees.check_sprees([p(3, "Dale", 0), p(4, "Ham", 1), p(5, "Angel", 0)])
    sprees._fast_poll()                                       # first blood said: back to normal
    assert sprees.server.refreshes == 2
    assert not sprees._fast_poll_timer.isActive()


def test_no_fast_reads_when_first_blood_is_off(sprees):
    play_old_map(sprees)
    sprees._first_blood_enabled = False
    sprees.on_missions_updated(missions("TD-Pink_Elephants.bms"))
    assert not sprees._fast_poll_timer.isActive()


def test_the_poll_asks_for_the_mission_list_before_the_player_list():
    import inspect
    text = inspect.getsource(protocol.ServerManager._poll_tick)
    assert text.index("AdminCommands.missions()") < text.index("AdminCommands.players()")
