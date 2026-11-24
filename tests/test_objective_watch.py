"""Zones, flags and the hill changing hands, for the phone page's bell (2026-11-24).

``ObjectiveWatch`` with made-up readings, then the Bans tab feeding it from
its poll and handing each line to the hook the main window sets.
"""

import os
import sys

import pytest

from wolfrat import manual_mode
from wolfrat.jo_players import (GAME_CTF, GAME_FB, GAME_KOTH, GAME_TKOTH, HillInfo, HillPlayer,
                                HillTeam, ZoneInfo)
from wolfrat.objective_watch import ObjectiveWatch

AAS, CAC, TDM = 0x10010, 0x50010, 0x10000
MAP = "3: AS-SnakeRiver.npj"
START = (1, 1, 0, 2, 2)


def zones(owners, shift=0):
    """Five zones in a row; ``shift`` moves them (another map)."""
    return [ZoneInfo(tier, team, (1000 * tier + shift, 500, 0))
            for tier, team in enumerate(owners, start=1)]


def hill(joint_ops, rebels):
    return HillInfo(420, 5, (HillTeam(1, 0, joint_ops), HillTeam(2, 0, rebels)))


def aas(watch, owners, map_name=MAP, game_type=AAS, shift=0):
    return watch.update(map_name, game_type, zones(owners, shift), None, None, None)


# ---- zones -------------------------------------------------------------------------

def test_the_first_reading_only_remembers():
    watch = ObjectiveWatch()
    assert aas(watch, (1, 1, 2, 2, 2)) == []
    assert aas(watch, (1, 1, 2, 2, 2)) == []


def test_a_zone_taken_says_who_which_and_how_many_they_hold_now():
    watch = ObjectiveWatch()
    aas(watch, START)
    assert aas(watch, (1, 1, 2, 2, 2)) == [("zone", "Rebels took zone C (3 of 5)")]
    assert aas(watch, (1, 1, 2, 2, 2)) == []                       # nothing new
    assert aas(watch, (1, 1, 1, 2, 2)) == [("zone", "Joint Ops took zone C (3 of 5)")]
    assert aas(watch, (1, 1, 1, 1, 2)) == [("zone", "Joint Ops took zone D (4 of 5)")]


def test_a_zone_going_back_to_nobody_is_not_an_event():
    watch = ObjectiveWatch()
    aas(watch, (1, 1, 1, 2, 2))
    assert aas(watch, (1, 1, 0, 2, 2)) == []
    assert aas(watch, (1, 1, 2, 2, 2)) == [("zone", "Rebels took zone C (3 of 5)")]


def test_two_zones_in_one_reading_are_two_events():
    watch = ObjectiveWatch()
    aas(watch, (1, 0, 0, 0, 2))
    assert aas(watch, (1, 1, 0, 2, 2)) == [("zone", "Joint Ops took zone B (2 of 5)"),
                                           ("zone", "Rebels took zone D (2 of 5)")]


def test_a_new_map_name_only_remembers():
    watch = ObjectiveWatch()
    aas(watch, (1, 1, 1, 1, 2))
    assert aas(watch, START, map_name="4: AS-Other.npj") == []
    assert aas(watch, (1, 1, 1, 2, 2), map_name="4: AS-Other.npj") == [
        ("zone", "Joint Ops took zone C (3 of 5)")]


def test_other_zones_under_the_old_map_name_are_a_new_map_too():
    """The zones of the next map can be read before the map list names it."""
    watch = ObjectiveWatch()
    aas(watch, (1, 1, 1, 2, 2))
    assert aas(watch, START, shift=70_000) == []                   # same count, other places
    assert aas(watch, (1, 1, 2, 2, 2), shift=70_000) == [("zone", "Rebels took zone C (3 of 5)")]
    assert watch.update(MAP, AAS, zones((1, 0, 2), shift=70_000), None, None, None) == []
    assert watch.update(MAP, AAS, [], None, None, None) == []      # a load: no zones for a moment
    assert aas(watch, START, shift=70_000) == []


def test_the_same_map_starting_again_after_a_win_only_remembers():
    watch = ObjectiveWatch()
    aas(watch, (1, 1, 1, 1, 2))
    assert aas(watch, (1, 1, 1, 1, 1)) == [("zone", "Joint Ops took zone E (5 of 5)")]
    assert aas(watch, (1, 1, 1, 1, 1)) == []                       # the end of the round
    assert aas(watch, START) == []                                 # the last zone was taken: a new round
    assert aas(watch, (1, 1, 2, 2, 2)) == [("zone", "Rebels took zone C (3 of 5)")]
    assert aas(watch, (1, 1, 1, 2, 2)) == [("zone", "Joint Ops took zone C (3 of 5)")]


def test_a_team_that_starts_with_every_zone_can_lose_one():
    """Holding them all at the first reading is a map's layout, not a win."""
    watch = ObjectiveWatch()
    aas(watch, (2, 2, 2, 2, 2))
    assert aas(watch, (1, 2, 2, 2, 2)) == [("zone", "Joint Ops took zone A (1 of 5)")]
    assert aas(watch, (1, 1, 2, 2, 2)) == [("zone", "Joint Ops took zone B (2 of 5)")]


def test_zones_need_the_advance_and_secure_family():
    watch = ObjectiveWatch()
    aas(watch, START, game_type=TDM)
    assert aas(watch, (1, 1, 2, 2, 2), game_type=TDM) == []
    aas(watch, START, game_type=CAC)
    assert aas(watch, (1, 1, 2, 2, 2), game_type=CAC) == [("zone", "Rebels took zone C (3 of 5)")]
    assert aas(watch, (1, 1, 1, 2, 2), game_type=AAS) == []        # another game type: a new map
    assert aas(watch, (1, 1, 2, 2, 2), game_type=None) == []       # the server is not read any more
    assert aas(watch, (1, 1, 1, 2, 2), game_type=AAS) == []


def test_letters_past_z_are_numbers():
    watch = ObjectiveWatch()
    many = [ZoneInfo(tier, 1) for tier in range(1, 29)]
    watch.update(MAP, AAS, many, None, None, None)
    many[26] = ZoneInfo(27, 2)
    many[25] = ZoneInfo(26, 2)
    assert watch.update(MAP, AAS, many, None, None, None) == [
        ("zone", "Rebels took zone Z (2 of 28)"), ("zone", "Rebels took zone 27 (2 of 28)")]
    many[27] = ZoneInfo(28, 2)
    assert watch.update(MAP, AAS, many, None, None, None) == [("zone", "Rebels took zone 28 (3 of 28)")]


# ---- flags ---------------------------------------------------------------------------

def flags(watch, joint_ops, rebels, goals={1: 5, 2: 5}, game_type=GAME_CTF, map_name=MAP):
    return watch.update(map_name, game_type, [], {1: joint_ops, 2: rebels}, goals, None)


def test_a_flag_captured_says_who_and_how_far_along():
    watch = ObjectiveWatch()
    assert flags(watch, 1, 0) == []                                # the first reading
    assert flags(watch, 2, 0) == [("flag", "Joint Ops captured a flag (2 of 5)")]
    assert flags(watch, 2, 0) == []
    assert flags(watch, 2, 1) == [("flag", "Rebels captured a flag (1 of 5)")]
    assert flags(watch, 3, 2) == [("flag", "Joint Ops captured a flag (3 of 5)"),
                                  ("flag", "Rebels captured a flag (2 of 5)")]


def test_flagball_scores_and_no_target_means_no_brackets():
    watch = ObjectiveWatch()
    flags(watch, 0, 0, game_type=GAME_FB)
    assert flags(watch, 0, 1, game_type=GAME_FB) == [("flag", "Rebels scored (1 of 5)")]
    assert flags(watch, 1, 1, goals=None, game_type=GAME_FB) == [("flag", "Joint Ops scored")]
    ctf = ObjectiveWatch()
    flags(ctf, 0, 0, goals=None)
    assert flags(ctf, 1, 0, goals=None) == [("flag", "Joint Ops captured a flag")]
    assert flags(ctf, 1, 1, goals={1: 4, 2: 6}) == [("flag", "Rebels captured a flag (1 of 6)")]


def test_one_event_a_team_however_many_it_went_up():
    watch = ObjectiveWatch()
    flags(watch, 0, 0)
    assert flags(watch, 3, 0) == [("flag", "Joint Ops captured a flag (3 of 5)")]


def test_a_count_that_went_down_is_a_new_round_and_only_resets():
    watch = ObjectiveWatch()
    flags(watch, 4, 2)
    assert flags(watch, 0, 0) == []
    assert flags(watch, 1, 0) == [("flag", "Joint Ops captured a flag (1 of 5)")]
    flags(watch, 4, 2)
    assert flags(watch, 0, 3) == []                                # down for one: nothing for either
    assert flags(watch, 0, 4) == [("flag", "Rebels captured a flag (4 of 5)")]


def test_flags_need_ctf_or_flagball_a_reading_and_the_same_map():
    watch = ObjectiveWatch()
    flags(watch, 0, 0, game_type=TDM)
    assert flags(watch, 1, 0, game_type=TDM) == []
    flags(watch, 1, 0)
    assert watch.update(MAP, GAME_CTF, [], None, None, None) == []     # the read failed once
    assert flags(watch, 2, 0) == []                                    # so this one only remembers
    assert flags(watch, 3, 0) == [("flag", "Joint Ops captured a flag (3 of 5)")]
    assert flags(watch, 4, 0, map_name="9: CTF-Other.npj") == []
    assert flags(watch, 4, 1, map_name="9: CTF-Other.npj") == [
        ("flag", "Rebels captured a flag (1 of 5)")]


# ---- the hill --------------------------------------------------------------------------

def tkoth(watch, joint_ops, rebels, map_name=MAP):
    return watch.update(map_name, GAME_TKOTH, [], None, None, hill(joint_ops, rebels))


def test_the_hill_is_taken_when_one_team_alone_is_on_it():
    watch = ObjectiveWatch()
    assert tkoth(watch, 0, 0) == []
    assert tkoth(watch, 0, 2) == [("zone", "Rebels took the hill")]
    assert tkoth(watch, 0, 1) == []                    # still theirs
    assert tkoth(watch, 1, 1) == []                    # both on it: nobody's
    assert tkoth(watch, 0, 1) == []                    # the same team alone again: not news
    assert tkoth(watch, 0, 0) == []
    assert tkoth(watch, 0, 3) == []                    # back on an empty hill they held last
    assert tkoth(watch, 2, 3) == []
    assert tkoth(watch, 2, 0) == [("zone", "Joint Ops took the hill")]


def test_who_holds_the_hill_at_the_first_reading_is_not_news():
    watch = ObjectiveWatch()
    assert tkoth(watch, 2, 0) == []
    assert tkoth(watch, 3, 0) == []
    assert tkoth(watch, 0, 1) == [("zone", "Rebels took the hill")]
    assert tkoth(watch, 0, 1, map_name="5: TKOTH-Other.npj") == []     # a new map: remember
    assert tkoth(watch, 1, 0, map_name="5: TKOTH-Other.npj") == [("zone", "Joint Ops took the hill")]


def test_the_hill_needs_team_king_of_the_hill_and_a_reading():
    watch = ObjectiveWatch()
    solo = HillInfo(420, 5, players=(HillPlayer("Ghost-Six", 259, True),))
    assert watch.update(MAP, GAME_KOTH, [], None, None, solo) == []
    assert watch.update(MAP, GAME_KOTH, [], None, None, solo) == []
    tkoth(watch, 0, 0)
    tkoth(watch, 0, 2)
    assert watch.update(MAP, GAME_TKOTH, [], None, None, None) == []       # the read failed once
    assert tkoth(watch, 0, 2) == []                                        # still the Rebels'
    assert tkoth(watch, 1, 0) == [("zone", "Joint Ops took the hill")]


def test_reset_starts_over():
    watch = ObjectiveWatch()
    aas(watch, START)
    watch.reset()
    assert aas(watch, (1, 1, 2, 2, 2)) == []


# ---- the Bans tab feeds it ---------------------------------------------------------------

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tests.test_bans_tab import Rig  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


class Game:
    """What the reader gives the Bans tab; change the fields between polls."""

    def __init__(self, rig, game_type):
        self.game_type, self.zones, self.caps, self.goals, self.hill = game_type, [], None, None, None
        self.hill_asked = []
        reader = rig.reader
        reader.read_game_type = lambda: self.game_type
        reader.read_zones = lambda: self.zones
        reader.read_team_caps = lambda game_type: self.caps
        reader.read_cap_goals = lambda game_type: self.goals
        reader.read_hill = lambda game_type: (self.hill_asked.append(game_type), self.hill)[1]


def bell(rig):
    got = []
    rig.tab.on_objective_event = lambda kind, text: got.append((kind, text))
    return got


def test_the_bans_tab_reports_a_zone_taken_to_the_bell(tmp_path):
    rig = Rig(tmp_path)
    game, got = Game(rig, AAS), bell(rig)
    rig.tab.on_missions_updated([f"{MAP} - () () () <CURRENT MISSION> <>"])
    game.zones = zones(START)
    rig.poll()
    assert got == [] and rig.tab.zones_now() == game.zones
    game.zones = zones((1, 1, 2, 2, 2))
    rig.poll()
    assert got == [("zone", "Rebels took zone C (3 of 5)")]
    rig.tab.on_missions_updated(["4: AS-Other.npj - () () () <CURRENT MISSION> <>"])
    game.zones = zones((1, 1, 1, 2, 2))
    rig.poll()
    assert len(got) == 1                                # the first reading of another map
    assert game.hill_asked == [] and rig.tab.hill_now() is None     # the hill is not read on AAS


def test_the_bans_tab_reads_the_hill_only_on_the_hill_game_types(tmp_path):
    rig = Rig(tmp_path)
    game, got = Game(rig, GAME_TKOTH), bell(rig)
    game.hill = hill(0, 0)
    rig.poll()
    assert rig.tab.hill_now() == game.hill and game.hill_asked == [GAME_TKOTH]
    game.hill = hill(0, 2)
    rig.poll()
    assert got == [("zone", "Rebels took the hill")]
    game.game_type = GAME_KOTH
    rig.poll()
    assert game.hill_asked == [GAME_TKOTH, GAME_TKOTH, GAME_KOTH]
    game.game_type = TDM
    rig.poll()
    assert rig.tab.hill_now() is None and len(game.hill_asked) == 3


def test_the_bans_tab_reports_flags_with_the_target_in_manual_mode_too(tmp_path):
    rig = Rig(tmp_path)
    game, got = Game(rig, GAME_CTF), bell(rig)
    game.caps, game.goals = {1: 0, 2: 0}, {1: 5, 2: 5}
    rig.poll()
    assert rig.tab.cap_goals_now() == {1: 5, 2: 5} and rig.tab.team_caps_now() == {1: 0, 2: 0}
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)
    manual_mode.state.choose(manual_mode.MANUAL)        # nothing here goes to the game
    game.caps = {1: 0, 2: 1}
    rig.poll()
    assert got == [("flag", "Rebels captured a flag (1 of 5)")]
    assert rig.said == []                               # the bell only: no chat line


def test_no_hook_and_a_failing_hook_never_break_the_poll(tmp_path):
    rig = Rig(tmp_path)
    game = Game(rig, AAS)
    game.zones = zones(START)
    rig.poll()
    game.zones = zones((1, 1, 2, 2, 2))
    rig.poll()                                          # no hook set: nothing happens
    rig.tab.on_objective_event = lambda kind, text: 1 / 0
    game.zones = zones((1, 1, 1, 2, 2))
    rig.poll(("Alice", "198.51.100.92"))
    assert any("Alert not shown (Joint Ops took zone C (3 of 5))" in line for line in rig.logged)
    assert rig.tab.online_table.rowCount() == 1         # the rest of the poll still ran


def test_a_reader_without_the_new_reads_still_polls(tmp_path):
    """Older stand-in readers only have read(): nothing is read, nothing breaks."""
    rig = Rig(tmp_path)
    got = bell(rig)
    rig.poll(("Alice", "198.51.100.92"))
    assert got == [] and rig.tab.hill_now() is None and rig.tab.cap_goals_now() is None
    assert rig.tab.zones_now() == []
