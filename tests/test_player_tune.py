"""Auto-tune per player (2026-11-23, tmp/autotune2/SPEC.md): the tuner that
learns each player's own Extra from the helper's per-slot tune rows.

The helper side is faked here as the SPEC describes it: counters that only
grow (or start again from 0), and the centre the helper tests a player on."""

import json
import math
import random
import statistics

import pytest

from wolfrat import lag_comp as lc
from wolfrat import player_tune as pt

G = 70                       # the server Extra
T0 = 1_700_000_000.0         # wall-clock seconds


def _row(name="ez", slot=3, ping=60, center=G, **counts):
    return lc.PlayerTuneRow(slot, name, ping, 0x100 + slot, center_ms=center, **counts)


class Seat:
    """One player in one slot as the helper shows them.  ``look`` adds shots
    to the counters and lets the tuner read the row; ``follow`` = the helper
    takes the value WolfRAT wants on (Watch: at once).

    The look right after the helper's centre has moved is never learned from
    (the centre must be the same at both ends of a look), so a player who
    arrives with a learned value gets one quiet look first."""

    def __init__(self, tuner, name="ez", slot=3, ping=60, mode=pt.WATCH, g=G, now=T0):
        self.tuner, self.mode, self.g, self.now = tuner, mode, g, now
        self.row = _row(name, slot, ping, center=g)
        self.arrival = tuner.observe([self.row], g, mode, self.now)
        self.follow()
        if self.row.center_ms != g:
            self.look()

    def look(self, dw=0, dl=0, uw=0, ul=0, follow=True, seconds=1.0):
        r = self.row
        r.down_wins += dw
        r.down_losses += dl
        r.up_wins += uw
        r.up_losses += ul
        r.steps += 10 + dw + dl + uw + ul
        self.now += seconds
        moves = self.tuner.observe([r], self.g, self.mode, self.now)
        if follow:
            self.follow()
        return moves

    def follow(self):
        h, extra = self.tuner.wants([self.row], self.g, self.mode).get(self.row.slot, (0, self.g))
        self.row.want_hash, self.row.want_extra_ms = h, extra
        self.row.center_ms = lc.personal_extra(extra if h else self.g, self.g, self.row.ping)

    @property
    def entry(self):
        return self.tuner.entry(self.row.name)


@pytest.fixture
def tuner(tmp_path):
    return pt.PlayerTuner(tmp_path / pt.FILE_NAME)


# ------------------------------------------------------------ the scores
def test_the_constants_are_the_specs():
    assert (pt.PT_SPREAD_MS, pt.PT_STEP_MS, pt.PT_UP_LIMIT_MS, pt.PT_UP_TOTAL_MS) == (40, 20, 40, 250)
    assert (pt.PT_ADOPT_WAIT_S, pt.PT_MIN_BETWEEN, pt.PT_FADE_DAYS, pt.PT_FADE_PING_MS) == (
        120, 150, 14, 50)
    assert (pt.CUSUM_WIN, pt.CUSUM_LOSS, pt.CUSUM_DOWN_H, pt.CUSUM_UP_H) == (0.262, -0.357, 4.7, 6.0)
    assert pt.CUSUM_WIN == pytest.approx(math.log(0.65 / 0.5), abs=5e-4)
    assert pt.CUSUM_LOSS == pytest.approx(math.log(0.35 / 0.5), abs=5e-4)


def test_each_disagreeing_shot_moves_its_score_and_a_score_never_goes_below_0(tuner):
    s = Seat(tuner)
    s.look(dw=1)
    assert s.entry.down == pytest.approx(0.262)
    s.look(dl=1)
    assert s.entry.down == 0.0                          # max(0, 0.262 - 0.357)
    s.look(dl=3)
    assert s.entry.down == 0.0                          # floored, not -1.071
    s.look(dw=3)
    assert s.entry.down == pytest.approx(0.786)
    s.look(dl=1)
    assert s.entry.down == pytest.approx(0.429)
    # In one look the order is not known: spread evenly (win, loss, win).
    s.look(dw=2, dl=1)
    assert s.entry.down == pytest.approx(0.429 + 0.262 - 0.357 + 0.262)
    # The up score is its own; a tie in one look counts the win first.
    s.look(uw=1, ul=1)
    assert s.entry.up == 0.0 and s.entry.down == pytest.approx(0.596)
    assert (s.entry.down_wins, s.entry.down_losses, s.entry.up_wins, s.entry.up_losses) == (
        6, 6, 1, 1)
    assert s.entry.since_move == 14 and s.entry.offset == 0
    # Shots where the two answers agree change nothing.
    s.look()
    assert s.entry.since_move == 14


def test_a_move_down_needs_a_score_of_4_7_and_150_disagreeing_shots(tuner):
    s = Seat(tuner)
    s.look(ul=132)                                      # the centre beat +40: up stays at 0
    s.look(dw=17)                                       # 17 x 0.262 = 4.454
    assert s.entry.offset == 0 and s.entry.since_move == 149
    moves = s.look(dw=1)                                # 4.716, and the 150th disagreeing shot
    assert s.entry.offset == -20 and s.row.want_extra_ms == 50
    assert [m["message"] for m in moves] == [
        "Auto-tune (watch): ez Extra +70 -> +50 - of 18 shots where -40 and the current "
        "answer disagreed, -40 hit 18"]
    m = moves[0]
    assert (m["why"], m["from"], m["to"], m["offset_from"], m["offset_to"]) == ("down", 70, 50, 0, -20)
    assert (m["shots"], m["wins"], m["ping"], m["server_extra"], m["mode"]) == (18, 18, 60, 70, "watch")
    assert m["score"] == pytest.approx(4.716)
    # Both scores and the counts start again after a move.
    e = s.entry
    assert (e.down, e.up, e.since_move, e.down_wins, e.up_losses) == (0.0, 0.0, 0, 0, 0)
    assert e.ping == 60 and len(e.moves) == 1


def test_a_score_over_its_line_waits_for_150_shots(tuner):
    s = Seat(tuner)
    s.look(dw=18)                                       # 4.716, but only 18 shots
    s.look(ul=131)
    assert s.entry.offset == 0 and s.entry.since_move == 149
    s.look(ul=1)                                        # the 150th: the score still stands
    assert s.entry.offset == -20


def test_the_spec_example_message(tuner):
    s = Seat(tuner)
    s.look(ul=32)
    moves = s.look(dw=79, dl=39)
    assert [m["message"] for m in moves] == [
        "Auto-tune (watch): ez Extra +70 -> +50 - of 118 shots where -40 and the current "
        "answer disagreed, -40 hit 79"]


def test_a_move_up_needs_the_stricter_score_of_6(tuner):
    s = Seat(tuner)
    s.look(dl=127)
    s.look(uw=22)                                       # 5.764
    assert s.entry.offset == 0 and s.entry.since_move == 149
    moves = s.look(uw=1)                                # 6.026
    assert s.entry.offset == 20 and s.row.center_ms == 90
    assert moves[0]["message"] == ("Auto-tune (watch): ez Extra +70 -> +90 - of 23 shots where "
                                   "+40 and the current answer disagreed, +40 hit 23")


def test_down_comes_first_when_both_scores_are_over_their_line(tuner):
    s = Seat(tuner)
    moves = s.look(dw=75, uw=75)                        # both far over at the 150th shot
    assert s.entry.offset == -20 and moves[0]["why"] == "down"


def test_moves_are_20_ms_and_150_shots_apart(tuner):
    s = Seat(tuner)
    s.look(ul=132)
    s.look(dw=18)
    assert s.entry.offset == -20
    s.look()                                            # the helper's centre moves: not fed
    s.look(dw=18)                                       # a full score again at once...
    assert s.entry.offset == -20 and s.entry.since_move == 18
    s.look(ul=131)
    assert s.entry.offset == -20
    s.look(ul=1)                                        # ...but only after 150 more shots
    assert s.entry.offset == -40 and s.row.center_ms == 30
    assert [(m["from"], m["to"]) for m in s.entry.moves] == [(70, 50), (50, 30)]


# ------------------------------------------------------------ the limits
def test_down_goes_as_far_as_no_rewind_at_all():
    # ping 30: an Extra of -30 is no rewind at all.
    assert pt.can_go_down(-80, G, 30) and not pt.can_go_down(-100, G, 30)
    # ping 35: -100 leaves 5 ms of rewind, so one more step (the helper stops it at -35).
    assert pt.can_go_down(-100, G, 35) and not pt.can_go_down(-120, G, 35)
    assert lc.personal_extra(G - 120, G, 35) == -35


def test_up_is_at_most_40_over_the_server_extra_and_250_ms_in_all():
    assert pt.can_go_up(0, G, 60) and pt.can_go_up(20, G, 60)
    assert not pt.can_go_up(40, G, 60)                  # +60 would be past +40
    assert pt.can_go_up(0, G, 150)                      # 150 + 90 = 240
    assert not pt.can_go_up(20, G, 150)                 # 150 + 110 = 260
    assert not pt.can_go_up(0, G, 200)                  # 200 + 90 = 290: no increase at all
    assert pt.can_go_up(-40, G, 200) and pt.can_go_up(-20, G, 200)    # back up to the server value
    assert pt.at_top_limit(40, G, 60) and pt.at_top_limit(20, G, 150)
    assert not pt.at_top_limit(0, G, 200) and not pt.at_top_limit(20, G, 60)


def test_a_score_held_at_a_limit_does_not_run_up(tuner):
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=-100, ping=30)
    s = Seat(tuner, ping=30)
    assert s.row.center_ms == -30                       # no rewind at all
    s.look(dw=200)
    assert s.entry.offset == -100 and s.entry.down == pt.CUSUM_DOWN_H
    tuner.players["A-99"] = pt.PlayerEntry("A-99", offset=40, ping=60)
    a = Seat(tuner, name="A-99", slot=9)
    a.look(uw=200)
    assert a.entry.offset == 40 and a.entry.up == pt.CUSUM_UP_H
    assert a.row.center_ms == 110


def test_halfway_back_to_a_20_ms_step():
    assert [pt.halve(v) for v in (100, 80, 60, 40, 20, 0, -20, -40, -60, -100, -140)] == [
        40, 40, 20, 20, 0, 0, 0, -20, -20, -40, -60]


# ---------------------------------------------- watch, on, off and the centre
def test_watch_and_on_write_the_same_values_off_writes_none(tuner):
    s = Seat(tuner)
    s.look(ul=132)
    s.look(dw=18)
    rows = [s.row, _row("Newbie", slot=5, ping=90)]
    want = {3: (lc.name_hash("ez"), 50), 5: (lc.name_hash("Newbie"), 70)}
    assert tuner.wants(rows, G, pt.WATCH) == want
    assert tuner.wants(rows, G, pt.ON) == want
    assert tuner.wants(rows, G, pt.OFF) == {}
    assert tuner.wants(rows, 80, pt.WATCH)[3] == (lc.name_hash("ez"), 60)   # follows the setting
    # Who is not on now gets nothing (the slot is cleared by sync_player_tune).
    gone = _row("Gone", slot=7)
    gone.entity = 0
    assert 7 not in tuner.wants([gone], G, pt.WATCH)


def test_on_says_so_and_a_value_waiting_to_be_taken_on_is_not_learned_from(tuner):
    s = Seat(tuner, mode=pt.ON)
    s.look(ul=132)
    moves = s.look(dw=18, follow=False)                 # the helper keeps the old one for now
    assert moves[0]["message"].startswith("Auto-tune (on): ez Extra +70 -> +50 - ")
    s.look(dw=30, follow=False)                         # tested at +70, WolfRAT expects +50
    assert s.entry.since_move == 0 and s.entry.down == 0.0
    s.follow()                                          # taken on (the player died)
    s.look(dw=5)                                        # the centre moved since the last look
    assert s.entry.since_move == 0
    s.look(dw=2)
    assert s.entry.since_move == 2 and s.entry.down == pytest.approx(0.524)


def test_the_centre_check_takes_the_counts_but_learns_nothing_from_them(tuner):
    s = Seat(tuner)
    s.row.center_ms = 50                                # the helper tests another value
    s.look(dw=10, follow=False)
    assert s.entry.since_move == 0
    s.row.center_ms = 70                                # right again, from this look on
    s.look(dw=3, follow=False)
    assert s.entry.since_move == 0                      # the centre changed between the looks
    s.look(dw=2, follow=False)
    assert s.entry.since_move == 2                      # only the shots after it
    # The centre WolfRAT expects is the helper's PersonalExtra at the player's ping.
    tuner.players["Far"] = pt.PlayerEntry("Far", offset=40)
    far = Seat(tuner, name="Far", slot=8, ping=160)
    assert far.row.center_ms == 90                      # +110 is cut to 250 - 160
    far.look(uw=3)
    assert far.entry.since_move == 3
    # There the value follows the ping: a few ms between two looks is the same place.
    far.row.ping = 163
    far.follow()
    assert far.row.center_ms == 87
    far.look(uw=2)
    assert far.entry.since_move == 5


def test_off_learns_nothing_and_its_shots_are_not_learned_later(tuner):
    s = Seat(tuner, mode=pt.OFF)
    assert tuner.entry("ez") is None
    s.look(dw=200)
    assert tuner.entry("ez") is None and tuner.wants([s.row], G, pt.OFF) == {}
    s.mode = pt.WATCH                                   # switched to Watch while they play
    s.look(dw=1)
    assert s.entry.since_move == 1 and s.entry.offset == 0


def test_a_count_that_drops_or_a_new_player_in_the_slot_starts_again(tuner):
    s = Seat(tuner)
    s.look(dw=5, dl=1)
    assert s.entry.since_move == 6
    r = s.row                                           # Reset: the helper counts from 0
    r.steps = r.center_hits = r.down_wins = r.down_losses = 0
    r.down_wins = 2
    s.look()
    assert s.entry.since_move == 6                      # the 2 are not fed
    s.look(dw=1)
    assert s.entry.since_move == 7
    # Another player takes slot 3 between two looks: their counts are a start only.
    r.name, r.down_wins, r.up_wins = "Bob", 40, 40
    s.look()
    assert tuner.entry("Bob").since_move == 0
    s.look(dw=1)
    assert tuner.entry("Bob").since_move == 1 and tuner.entry("ez").since_move == 7
    # ez is back, in another slot: a new visit, the counts there are a start.
    ez = _row("ez", slot=11, center=70, down_wins=500)
    tuner.observe([r, ez], G, pt.WATCH, s.now + 1)
    ez.down_wins += 1
    tuner.observe([r, ez], G, pt.WATCH, s.now + 2)
    assert tuner.entry("ez").since_move == 8
    # Missing from one read (a respawn) and back in the same slot: the same visit.
    tuner.observe([r], G, pt.WATCH, s.now + 7)
    ez.down_wins += 2
    tuner.observe([r, ez], G, pt.WATCH, s.now + 12)
    assert tuner.entry("ez").since_move == 10
    # Gone for over a minute: a new visit, the counts are a start again.
    tuner.observe([r], G, pt.WATCH, s.now + 80)
    ez.down_wins += 4
    tuner.observe([r, ez], G, pt.WATCH, s.now + 90)
    assert tuner.entry("ez").since_move == 10


# ------------------------------------------------------------ fades
def test_back_after_14_days_away_keeps_half(tuner):
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=-60, ping=45, last_seen=T0 - 15 * 86400)
    tuner.players["ok"] = pt.PlayerEntry("ok", offset=-60, ping=45, last_seen=T0 - 13 * 86400)
    rows = [_row("ez", slot=1, ping=45, center=10), _row("ok", slot=2, ping=45, center=10)]
    moves = tuner.observe(rows, G, pt.WATCH, T0)
    assert [m["message"] for m in moves] == [
        "Auto-tune (watch): ez Extra +10 -> +50 - back after 15 days away: half way back to "
        "the server value"]
    assert tuner.entry("ez").offset == -20 and tuner.entry("ok").offset == -60
    assert tuner.entry("ez").last_seen == T0 and moves[0]["why"] == "fade_days"
    # Only on arrival: playing on does not fade again.
    tuner.observe(rows, G, pt.WATCH, T0 + 5)
    assert tuner.entry("ez").offset == -20


def test_a_ping_50_ms_from_the_learned_one_keeps_half(tuner):
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=40, ping=60, last_seen=T0 - 3600)
    tuner.players["ok"] = pt.PlayerEntry("ok", offset=40, ping=60, last_seen=T0 - 3600)
    ez = _row("ez", slot=1, ping=120, center=110)
    ok = _row("ok", slot=2, ping=110, center=110)
    tuner.observe([ez, ok], G, pt.WATCH, T0)
    assert tuner.observe([ez, ok], G, pt.WATCH, T0 + 29) == []       # not settled yet
    moves = tuner.observe([ez, ok], G, pt.WATCH, T0 + 30)
    assert [m["message"] for m in moves] == [
        "Auto-tune (watch): ez Extra +110 -> +90 - ping now 120, learned at 60: half way "
        "back to the server value"]
    assert tuner.entry("ez").offset == 20 and tuner.entry("ez").ping == 120
    assert tuner.entry("ok").offset == 40                 # 50 ms away: kept
    ez.ping = 300
    assert tuner.observe([ez, ok], G, pt.WATCH, T0 + 60) == []        # once a visit


def test_off_does_not_fade_or_count_as_away(tuner):
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=-60, ping=45, last_seen=T0 - 30 * 86400)
    row = _row("ez", ping=200, center=G)
    assert tuner.observe([row], G, pt.OFF, T0) == []
    assert tuner.observe([row], G, pt.OFF, T0 + 60) == []
    assert tuner.entry("ez").offset == -60 and tuner.entry("ez").last_seen == T0 + 60


# ------------------------------------------------------- lock and forget
def test_lock_learn_and_forget(tuner):
    s = Seat(tuner)
    s.look(ul=132)
    s.look(dw=18)
    assert s.entry.offset == -20
    assert tuner.lock("ez", now=T0) == ("Auto-tune: ez uses the server value now, set by the "
                                        "admin (learned -20 ms is kept)")
    s.follow()
    assert s.row.want_hash == 0 and s.row.center_ms == G       # the server value
    assert tuner.own_text(s.row, G, pt.WATCH) == "locked"
    s.look(dw=300)
    assert s.entry.since_move == 0 and s.entry.offset == -20   # nothing learned
    assert tuner.unlock("ez") == "Auto-tune: ez learns again, from -20 ms, set by the admin"
    assert tuner.wants([s.row], G, pt.WATCH) == {3: (lc.name_hash("ez"), 50)}
    assert tuner.forget("ez") == ("Auto-tune: forgot ez's learned Extra (-20 ms), set by "
                                  "the admin")
    assert tuner.entry("ez") is None
    assert tuner.wants([s.row], G, pt.WATCH) == {3: (lc.name_hash("ez"), 70)}
    assert tuner.forget("ez") == "Auto-tune: nothing learned for ez"
    # A player not seen yet can be locked too.
    tuner.lock("Later", now=T0)
    assert tuner.entry("Later").locked and tuner.locked_names() == ["Later"]


def test_the_top_limit_list_needs_30_minutes_on_the_server_there(tuner):
    tuner.players["A-99"] = pt.PlayerEntry("A-99", offset=40, ping=60)
    row = _row("A-99", slot=9, ping=60, center=110)
    tuner.observe([row], G, pt.WATCH, T0)
    for i in range(1, 29 * 12 + 1):                     # 29 minutes, a look every 5 s
        tuner.observe([row], G, pt.WATCH, T0 + 5 * i)
    assert tuner.top_limit_names() == []
    tuner.observe([], G, pt.WATCH, T0 + 3000)           # gone for a while
    tuner.observe([row], G, pt.WATCH, T0 + 7000)        # back: the gap does not count
    for i in range(1, 12 + 1):
        tuner.observe([row], G, pt.WATCH, T0 + 7000 + 5 * i)
    assert tuner.entry("A-99").top_seconds == pytest.approx(30 * 60)
    assert tuner.top_limit_names() == ["A-99"]
    assert "At the top limit for 30+ min: A-99 +110." in tuner.status_lines(G, pt.WATCH)
    # One gap between two looks of a visit counts at most 30 s...
    tuner.observe([row], G, pt.WATCH, T0 + 7060 + 50)
    assert tuner.entry("A-99").top_seconds == pytest.approx(30 * 60 + 30)
    # ...and over a minute away is a new visit: nothing.
    tuner.observe([row], G, pt.WATCH, T0 + 99999)
    assert tuner.entry("A-99").top_seconds == pytest.approx(30 * 60 + 30)


# ------------------------------------------------------------ the page's words
def test_own_extra_words(tuner):
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=-20)
    tuner.players["Lock"] = pt.PlayerEntry("Lock", offset=40, locked=True)
    ez = _row("ez", ping=60, center=50)
    assert tuner.own_text(ez, G, pt.WATCH) == "+50 (watch)"
    assert tuner.own_text(ez, G, pt.OFF) == "server"
    assert tuner.own_text(ez, G, pt.ON) == "+50 (waiting)"     # not taken on yet
    ez.want_hash, ez.want_extra_ms, ez.want_seq = lc.name_hash("ez"), 50, 4
    ez.use_extra_ms, ez.use_seq, ez.use_on = 50, 4, 1
    assert tuner.own_text(ez, G, pt.ON) == "+50"
    ez.want_extra_ms, ez.want_seq = 30, 5                       # a newer value written
    assert tuner.own_text(ez, G, pt.ON) == "+50 (waiting)"
    assert tuner.own_text(_row("Lock"), G, pt.ON) == "locked"
    assert tuner.own_text(_row("New"), G, pt.WATCH) == "server"
    tuner.players["Far"] = pt.PlayerEntry("Far", offset=20)
    assert tuner.own_text(_row("Far", ping=200), G, pt.WATCH) == "server"   # cut to the server value
    tip = tuner.tooltip(ez, G, pt.ON)
    assert "ez: learned +50 (-20 on the server's +70)." in tip
    assert "Scores: lower 0.0 of 4.7, higher 0.0 of 6.0" in tip


def test_status_lines(tuner):
    assert tuner.status_lines(G, pt.OFF) == [
        "Auto-tune per player: off - every player uses the Extra above."]
    assert tuner.status_lines(G, pt.WATCH) == [
        "Auto-tune per player (watch): every player is on the server value so far."]
    tuner.players["ez"] = pt.PlayerEntry("ez", offset=-20)
    tuner.players["A-99"] = pt.PlayerEntry("A-99", offset=40)
    tuner.players["Lock"] = pt.PlayerEntry("Lock", offset=20, locked=True)
    assert tuner.status_lines(G, pt.WATCH) == [
        "Auto-tune per player (watch): 2 players differ from the server value: A-99 +110, ez +50.",
        "Locked to the server value: Lock."]
    assert tuner.status_lines(G, pt.ON, on_now=["ez"])[0] == (
        "Auto-tune per player (on): 2 players differ from the server value: ez +50, A-99 +110.")
    assert tuner.status_lines(G, pt.WATCH, most=1)[0].endswith("A-99 +110 and 1 more.")
    assert "needs lag compensation helper version 6" in tuner.status_lines(
        G, pt.WATCH, has_v6=False)[0]
    del tuner.players["A-99"]
    assert tuner.status_lines(G, pt.ON)[0] == (
        "Auto-tune per player (on): 1 player differs from the server value: ez +50.")


# ------------------------------------------------------------ the file
def test_what_was_learned_is_saved_and_read_back(tmp_path):
    path = tmp_path / pt.FILE_NAME
    t = pt.PlayerTuner(path)
    s = Seat(t)
    s.look(ul=132)
    s.look(dw=18)
    s.look()                                            # the centre moves: not learned from
    s.look(dw=3, uw=1)
    t.lock("Locky", now=T0)
    t.entry("Locky").top_seconds = 75.5
    assert t.save(now=T0 + 10)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and sorted(data["players"]) == ["Locky", "ez"]
    again = pt.PlayerTuner(path)
    assert {n: e.to_json() for n, e in again.players.items()} == \
        {n: e.to_json() for n, e in t.players.items()}
    ez = again.entry("ez")
    assert (ez.offset, ez.since_move, ez.down_wins, ez.ping) == (-20, 4, 3, 60)
    assert ez.down == pytest.approx(0.786) and ez.moves[0]["to"] == 50
    assert again.entry("Locky").locked and not again.problem


def test_it_saves_when_something_changed_at_most_once_a_minute(tmp_path):
    path = tmp_path / pt.FILE_NAME
    t = pt.PlayerTuner(path)
    assert not t.save_if_due(T0) and not path.exists()          # nothing to save
    t.lock("ez", now=T0)
    assert t.save_if_due(T0) and path.exists()
    t.unlock("ez")
    assert not t.save_if_due(T0 + 59)
    assert t.save_if_due(T0 + 60)
    assert not t.save_if_due(T0 + 200)                          # saved, nothing new


def test_passers_by_on_the_server_value_are_dropped_after_60_days(tmp_path):
    t = pt.PlayerTuner(tmp_path / pt.FILE_NAME)
    old = T0 - 61 * 86400
    t.players.update({"Passer": pt.PlayerEntry("Passer", last_seen=old),
                      "Recent": pt.PlayerEntry("Recent", last_seen=T0 - 59 * 86400),
                      "Learned": pt.PlayerEntry("Learned", offset=-20, last_seen=old),
                      "Locked": pt.PlayerEntry("Locked", locked=True, last_seen=old)})
    assert t.save(now=T0)
    assert sorted(pt.PlayerTuner(tmp_path / pt.FILE_NAME).players) == ["Learned", "Locked", "Recent"]


def test_a_broken_file_is_kept_as_bad_and_it_starts_empty(tmp_path):
    path = tmp_path / pt.FILE_NAME
    bad = tmp_path / (pt.FILE_NAME + ".bad")
    path.write_text("{not json", encoding="utf-8")
    logs = []
    t = pt.PlayerTuner(path, log=logs.append)
    assert t.players == {} and not path.exists() and bad.read_text(encoding="utf-8") == "{not json"
    assert "could not be read" in t.problem and "kept as wolfrat_player_tune.json.bad" in t.problem
    assert len(logs) == 1 and logs[0].startswith("Auto-tune per player: ")
    for broken in ({"version": 1, "players": {"ez": {"offset": "lots"}}},
                   {"version": 1, "players": {"ez": {"offset": 30}}},          # not a 20 ms step
                   {"version": 1, "players": {"ez": {"locked": "yes"}}},
                   {"version": 1, "players": ["ez"]},
                   ["not", "a", "table"]):
        path.write_text(json.dumps(broken), encoding="utf-8")
        t = pt.PlayerTuner(path)
        assert t.players == {} and t.problem and not path.exists()
        assert json.loads(bad.read_text(encoding="utf-8")) == broken
    # It works on from empty and writes a good file again.
    t.lock("ez", now=T0)
    assert t.save(now=T0)
    assert pt.PlayerTuner(path).entry("ez").locked


def test_history_summary(tuner):
    s = Seat(tuner)
    s.look(ul=132)
    s.look(dw=18)
    tuner.lock("Locky", now=T0)
    summary = tuner.summary(pt.WATCH, G, since=T0)
    assert summary["mode"] == "watch" and summary["differ"] == {"ez": 50}
    assert summary["locked"] == ["Locky"] and summary["top_limit"] == []
    assert [(m["name"], m["from"], m["to"], m["shots"], m["wins"]) for m in summary["moves"]] == [
        ("ez", 70, 50, 18, 18)]
    assert tuner.summary(pt.WATCH, G, since=s.now + 1)["moves"] == []


# ------------------------------------------------- synthetic replays
# A simulated player whose best Extra is ``best``: every disagreeing shot is won
# by the side nearer ``best`` with an edge of 60:40 per 40 ms (50:50 when both
# sides are as near).  One look a second, 0-2 disagreeing shots per side.

def _replay(tmp_path, best, ping=60, shots=3000, edge=0.1, seed=1, mode=pt.WATCH,
            flat=False, adopt_after=0):
    rng = random.Random(seed)
    tuner = pt.PlayerTuner(tmp_path / ("replay%d.json" % seed))
    row = _row("Sim", slot=0, ping=ping, center=G)
    now = T0
    tuner.observe([row], G, mode, now)
    pending, pending_since = None, 0.0
    total, first_within, moves = 0, None, []

    def p_alt(c, a):
        if flat:
            return 0.5
        d = (abs(c - best) - abs(a - best)) / 40.0 * edge
        return 0.5 + max(-edge, min(edge, d))

    def helper_takes(extra):
        row.want_hash, row.want_extra_ms = lc.name_hash("Sim"), extra
        row.center_ms = lc.personal_extra(extra, G, ping)

    helper_takes(G)
    while total < shots:
        now += 1.0
        c = row.center_ms
        for alt, wins, losses in ((c - 40, "down_wins", "down_losses"),
                                  (c + 40, "up_wins", "up_losses")):
            for _ in range(rng.choice((0, 1, 1, 2))):
                if total < shots:
                    total += 1
                    key = wins if rng.random() < p_alt(c, alt) else losses
                    setattr(row, key, getattr(row, key) + 1)
        moves += tuner.observe([row], G, mode, now)
        _h, extra = tuner.wants([row], G, mode)[0]
        if extra != row.want_extra_ms and pending != extra:
            pending, pending_since = extra, now
        if pending is not None and now - pending_since >= adopt_after:
            helper_takes(pending)                       # On: at death or after 120 s
            pending = None
        if first_within is None and abs(row.center_ms - best) <= 20:
            first_within = total
    return first_within, moves, row.center_ms


@pytest.mark.parametrize("best", [G - 60, G + 40])
def test_replay_a_player_with_their_own_best_extra_gets_there(tmp_path, best):
    """60 ms under the server value (ez-like) and 40 over it (A-99-like)."""
    firsts, finals = [], []
    for seed in range(12):
        first, moves, final = _replay(tmp_path, best, seed=seed)
        assert first is not None, f"seed {seed} never came within 20 ms of {best}"
        firsts.append(first)
        finals.append(final)
        assert all(abs(m["to"] - m["from"]) == 20 for m in moves)
    # Within 20 ms, and still there at the end (no wandering off).
    assert all(abs(f - best) <= 20 for f in finals)
    # Disagreeing shots (down + up) it took: a few busy nights at most.
    assert statistics.median(firsts) <= 1000 and max(firsts) <= 2500


def test_replay_on_mode_with_values_taken_on_two_minutes_later(tmp_path):
    for seed in range(4):
        first, moves, final = _replay(tmp_path, G - 60, seed=seed, mode=pt.ON, shots=4000,
                                      adopt_after=pt.PT_ADOPT_WAIT_S)
        assert first is not None and first <= 3000 and abs(final - (G - 60)) <= 20
        assert all(m["mode"] == "on" for m in moves)


def test_replay_a_flat_player_is_left_alone(tmp_path):
    """50:50 on both sides: about one move per 5,000 disagreeing shots by the
    research's numbers (a false move per ~3,000 down / ~10,000 up)."""
    counts = [len(_replay(tmp_path, G, shots=5000, seed=seed, flat=True)[1]) for seed in range(20)]
    assert sum(counts) <= 30 and max(counts) <= 5


@pytest.mark.parametrize("ping, top", [(60, G + 40), (150, G + 20), (200, G)])
def test_replay_up_moves_stop_at_the_limits(tmp_path, ping, top):
    """A player whose best is far above: +40 at most, 250 ms of ping + Extra."""
    first, moves, final = _replay(tmp_path, G + 300, ping=ping, shots=4000, edge=0.25, seed=3)
    assert final == top
    assert all(m["to"] <= G + 40 and ping + m["to"] <= 250 for m in moves if m["to"] > G)


def test_replay_down_stops_at_no_rewind(tmp_path):
    first, moves, final = _replay(tmp_path, -300, ping=30, shots=6000, edge=0.25, seed=5)
    assert final == -30                                 # rewind 0: plain JO for this shooter
