"""The map-vote trigger rules, replayed against whole simulated matches."""

from types import SimpleNamespace as P

import pytest

from wolfrat import vote_rules as vr
from wolfrat.vote_rules import KillWatch, MatchView, ModeRule, decide

OFF = vr.rules_from_json(None)


def on(mode, **kw):
    rules = dict(OFF)
    rules[mode] = ModeRule(**kw)
    return rules


# ---- mode detection: every filename shape on Dale's server (2026-09-20) ----

@pytest.mark.parametrize("filename, mode", [
    ("AS-DoslinOblast.bms", vr.MODE_AS),
    ("AS - Black Rock TAC.npj", vr.MODE_AS),
    ("AAS-Spanaird.bms", vr.MODE_AS),
    ("AAS_E_Karo_Highlands749.npj", vr.MODE_AS),
    ("ASB_G14B.BMS", vr.MODE_AS),
    ("ASH_C1A.BMS", vr.MODE_AS),
    ("TD-AdaleCity.bms", vr.MODE_TDM),
    ("TDH_C1A.BMS", vr.MODE_TDM),
    ("TDM-Spanaird.bms", vr.MODE_TDM),
    ("td-lowercase.bms", vr.MODE_TDM),
    ("TK-AQUA.bms", vr.MODE_TKOTH),
    ("TKX_G12A.BMS", vr.MODE_TKOTH),
    ("TKP_C2A.NPZ", vr.MODE_TKOTH),
    ("DM-COD4Killhouse.npj", vr.MODE_DM),
    ("CTF-TheBarrens.bms", vr.MODE_CTF),
    ("FB-ScrimmageLine.bms", vr.MODE_FB),
    ("CT-BadJuju.bms", vr.MODE_OTHER),
    ("AD-Aquaduct.bms", vr.MODE_OTHER),
    ("SD-StadiumFeud.bms", vr.MODE_OTHER),
    ("CP01.BMS", vr.MODE_OTHER),
    ("00TRA.BMS", vr.MODE_OTHER),
    # bare map names that merely START with mode letters are not that mode
    ("DOSLIN.BMS", vr.MODE_OTHER),
    ("Damai_p1.bms", vr.MODE_OTHER),
    ("NOSTRIVA.BMS", vr.MODE_OTHER),
    ("", vr.MODE_OTHER),
    (None, vr.MODE_OTHER),
])
def test_game_mode_from_filename(filename, mode):
    assert vr.game_mode(filename) == mode


# ---- the promise to Dale: nothing changes until a rule is switched on ------

@pytest.mark.parametrize("filename", [
    "AS-DoslinOblast.bms", "TD-AdaleCity.bms", "TK-AQUA.bms",
    "DM-Graveyard.npj", "CTF-TheBarrens.bms", "FB-ScrimmageLine.bms",
    "CT-BadJuju.bms",
])
def test_with_every_rule_off_the_vote_fires_exactly_as_before(filename):
    for total in (10, 30, 40, 120):
        for trigger in (1, 3, 20):
            for remaining in range(total, -1, -1):
                for kills in (None, 0, 50, 99, 100, 500):
                    decision = decide(
                        MatchView(filename, remaining, total, kills, 100),
                        trigger, OFF, KillWatch(),
                    )
                    assert decision.fire == (remaining <= trigger)


def test_status_text_with_rules_off_is_the_original_wording():
    waiting = decide(MatchView("TD-X.bms", 26, 30), 3, OFF, KillWatch())
    assert waiting.status == "Status: 26m / 30m remaining. Auto-vote in 23m"
    pending = decide(MatchView("TD-X.bms", 3, 30), 3, OFF, KillWatch())
    assert pending.status == "Status: 3m remaining. Auto-vote pending..."


def test_advance_and_secure_never_uses_an_extra_rule():
    rules = {mode: ModeRule(True, 1, True, 1) for mode in vr.SCORE_MODES}
    rules[vr.MODE_AS] = ModeRule(True, 1, True, 1)
    watch = KillWatch(armed=True)
    decision = decide(MatchView("AS-Doslin.bms", 20, 30, 99, 100), 3, rules, watch)
    assert not decision.fire


# ---- minutes-in ------------------------------------------------------------

def play(filename, total, trigger, rules, kills_at=lambda elapsed: None, limit=100,
         ends_at=None):
    """Replay a match minute by minute; return (minute the vote fired, reason)."""
    watch = KillWatch()
    for elapsed in range(0, total + 1):
        if ends_at is not None and elapsed >= ends_at:
            return None, "map ended with no vote"
        view = MatchView(filename, total - elapsed, total, kills_at(elapsed), limit)
        decision = decide(view, trigger, rules, watch)
        if decision.fire:
            return elapsed, decision.reason
    return None, "never fired"


def test_koth_votes_ten_minutes_in_when_the_rule_is_on():
    fired, reason = play("TK-AQUA.bms", 30, 3, on(vr.MODE_TKOTH, minutes_in_enabled=True, minutes_in=10))
    assert fired == 10
    assert "10m in" in reason and "King of the Hill" in reason


def test_koth_with_the_rule_off_still_waits_for_the_end():
    fired, _ = play("TK-AQUA.bms", 30, 3, OFF)
    assert fired == 27


def test_minutes_in_for_one_mode_does_not_leak_into_another():
    rules = on(vr.MODE_TKOTH, minutes_in_enabled=True, minutes_in=10)
    assert play("CTF-TheBarrens.bms", 30, 3, rules)[0] == 27
    assert play("TD-AdaleCity.bms", 30, 3, rules)[0] == 27


def test_minutes_in_longer_than_the_match_falls_back_to_the_end_rule():
    rules = on(vr.MODE_CTF, minutes_in_enabled=True, minutes_in=45)
    assert play("CTF-TheBarrens.bms", 30, 3, rules)[0] == 27


def test_minutes_in_needs_a_real_total():
    rules = on(vr.MODE_FB, minutes_in_enabled=True, minutes_in=1)
    decision = decide(MatchView("FB-X.bms", 10, 0), 3, rules, KillWatch())
    assert not decision.fire


# ---- kill watch ------------------------------------------------------------

def test_tdm_that_ends_on_kills_used_to_get_no_vote_at_all():
    # 4 kills a minute: limit of 100 reached at minute 25 of 40.
    fired, why = play("TD-AdaleCity.bms", 40, 3, OFF, lambda m: m * 4, ends_at=25)
    assert fired is None and why == "map ended with no vote"


def test_tdm_kill_watch_starts_the_vote_before_the_limit():
    rules = on(vr.MODE_TDM, kill_watch_enabled=True, kills_before_limit=15)
    fired, reason = play("TD-AdaleCity.bms", 40, 3, rules, lambda m: m * 4, ends_at=25)
    assert fired == 22          # 88 kills >= 100 - 15
    assert "kill limit" in reason


def test_stale_scores_from_the_previous_map_cannot_start_a_vote():
    # New map detected, but the player list still shows last map's 100 kills.
    rules = on(vr.MODE_TDM, kill_watch_enabled=True, kills_before_limit=15)
    watch = KillWatch()
    for stale in (100, 100, 97):
        assert not decide(MatchView("TD-X.bms", 40, 40, stale, 100), 3, rules, watch).fire
    assert not watch.armed
    # scores reset -> watch arms -> a genuine climb fires it
    assert not decide(MatchView("TD-X.bms", 39, 40, 2, 100), 3, rules, watch).fire
    assert watch.armed
    assert decide(MatchView("TD-X.bms", 20, 40, 86, 100), 3, rules, watch).fire


def test_kill_watch_ignores_modes_without_a_kill_score():
    rules = on(vr.MODE_TKOTH, kill_watch_enabled=True, kills_before_limit=15)
    watch = KillWatch(armed=True)
    assert not decide(MatchView("TK-AQUA.bms", 20, 30, 99, 100), 3, rules, watch).fire


@pytest.mark.parametrize("kills, limit", [(None, 100), (50, None), (50, 0)])
def test_kill_watch_needs_both_numbers(kills, limit):
    rules = on(vr.MODE_TDM, kill_watch_enabled=True, kills_before_limit=15)
    watch = KillWatch(armed=True)
    assert not decide(MatchView("TD-X.bms", 20, 40, kills, limit), 3, rules, watch).fire


def test_kill_margin_larger_than_the_limit_is_survivable():
    rules = on(vr.MODE_TDM, kill_watch_enabled=True, kills_before_limit=500)
    watch = KillWatch()
    assert not decide(MatchView("TD-X.bms", 39, 40, 0, 100), 3, rules, watch).fire
    assert watch.armed
    assert decide(MatchView("TD-X.bms", 38, 40, 1, 100), 3, rules, watch).fire


def test_whichever_rule_is_first_wins():
    rules = on(vr.MODE_TDM, minutes_in_enabled=True, minutes_in=10,
               kill_watch_enabled=True, kills_before_limit=15)
    fast = play("TD-X.bms", 40, 3, rules, lambda m: m * 12)   # 88 kills by minute 8
    assert fast[0] == 8 and "kill limit" in fast[1]
    slow = play("TD-X.bms", 40, 3, rules, lambda m: m)
    assert slow[0] == 10 and "10m in" in slow[1]


# ---- reading the server ----------------------------------------------------

def test_leading_kills_team_deathmatch_sums_the_bigger_team():
    players = [
        P(server_id=0, team=1, kills=0),          # host row
        P(server_id=1, team=1, kills=30), P(server_id=2, team=1, kills=25),
        P(server_id=3, team=2, kills=40), P(server_id=4, team=2, kills=None),
    ]
    assert vr.leading_kills(vr.MODE_TDM, players) == 55
    assert vr.leading_kills(vr.MODE_DM, players) == 40
    assert vr.leading_kills(vr.MODE_TKOTH, players) is None
    assert vr.leading_kills(vr.MODE_TDM, []) is None


def test_kill_limit_reads_either_settings_shape():
    from wolfrat.admin_commands import GameSettings
    assert vr.kill_limit(GameSettings({"KillLimit": "100"})) == 100
    assert vr.kill_limit({"killlimit": "75"}) == 75
    assert vr.kill_limit({"KillLimit": "0"}) is None
    assert vr.kill_limit({"KillLimit": "lots"}) is None
    assert vr.kill_limit({}) is None
    assert vr.kill_limit(None) is None


# ---- saved settings --------------------------------------------------------

def test_rules_survive_a_save_and_load():
    rules = on(vr.MODE_CTF, minutes_in_enabled=True, minutes_in=12)
    assert vr.rules_from_json(vr.rules_to_json(rules)) == rules


@pytest.mark.parametrize("junk", [None, [], "x", {"tdm": "x"}, {"tdm": {"minutes_in": "abc"}}])
def test_a_missing_or_damaged_settings_file_means_every_rule_off(junk):
    rules = vr.rules_from_json(junk)
    assert set(rules) == set(vr.SCORE_MODES)
    assert not any(r.minutes_in_enabled or r.kill_watch_enabled for r in rules.values())


def test_saved_numbers_are_clamped():
    rule = ModeRule.from_json({"minutes_in": 9999, "kills_before_limit": -5})
    assert rule.minutes_in == 240 and rule.kills_before_limit == 1


# ---- the player gate (2026-09-22): a held vote must SAY it is held ----------

def test_enough_players_means_no_hold():
    assert vr.hold_for_players(2, 2) is None
    assert vr.hold_for_players(9, 2) is None
    assert vr.hold_for_players(1, 1) is None


@pytest.mark.parametrize("players, minimum, text", [
    (0, 2, "Status: Paused - nobody is on the server. Votes start with 2 or more."),
    (1, 2, "Status: Paused - only 1 player is on the server. Votes start with 2 or more."),
    (3, 6, "Status: Paused - only 3 players are on the server. Votes start with 6 or more."),
    (0, 1, "Status: Paused - nobody is on the server. Votes start with 1 or more."),
])
def test_held_status_says_why(players, minimum, text):
    assert vr.hold_for_players(players, minimum) == text


@pytest.mark.parametrize("junk, expected", [
    (None, vr.DEFAULT_MIN_PLAYERS), ("x", vr.DEFAULT_MIN_PLAYERS),
    (0, 1), (-5, 1), (99, 32), ("4", 4), (2.9, 2),
])
def test_min_players_is_clamped(junk, expected):
    assert vr.clamp_min_players(junk) == expected


# ---- the join grace (Dale 2026-09-26) ---------------------------------------

FIRE = vr.Decision(True, "3m remaining (vote starts 3m before the end)")
QUIET = vr.Decision(False, status="Status: 5m / 30m remaining.")


def test_a_vote_that_was_never_held_starts_at_once():
    g = vr.JoinGrace()
    assert g.step(FIRE, 100.0) == (FIRE, None)
    assert g.step(QUIET, 105.0) == (None, None)


def test_join_after_a_held_vote_waits_40s_then_starts():
    # 19:47 replay: rule firing on an empty server, Dale appears while loading.
    g = vr.JoinGrace()
    g.hold(FIRE)
    g.hold(FIRE)
    assert g.step(FIRE, 1000.0) == (None, 40)
    assert g.step(FIRE, 1005.0) == (None, 35)
    assert g.step(FIRE, 1039.5) == (None, 1)
    assert g.step(FIRE, 1040.0) == (FIRE, None)
    assert not g.waiting and g.held is None


def test_one_shot_rule_is_not_lost_while_waiting():
    # The AAS zone rule fires once per map - it has gone quiet by the time
    # the grace runs out, so the held decision is used.
    zone = vr.Decision(True, "AAS: one zone left")
    g = vr.JoinGrace()
    g.hold(zone)
    g.hold(QUIET)
    assert g.step(QUIET, 0.0) == (None, 40)
    assert g.step(QUIET, 41.0) == (zone, None)


def test_joiner_leaving_again_restarts_the_clock():
    g = vr.JoinGrace()
    g.hold(FIRE)
    assert g.step(FIRE, 0.0) == (None, 40)
    g.hold(FIRE)                       # back below the minimum
    assert not g.waiting
    assert g.step(FIRE, 30.0) == (None, 40)
    assert g.step(FIRE, 70.0) == (FIRE, None)


def test_nothing_held_means_no_wait_when_players_arrive():
    g = vr.JoinGrace()
    g.hold(QUIET)                      # empty, but no rule wanted a vote yet
    assert g.step(QUIET, 0.0) == (None, None)
    assert g.step(FIRE, 5.0) == (FIRE, None)


def test_reset_on_new_map_drops_the_held_vote():
    g = vr.JoinGrace()
    g.hold(FIRE)
    g.reset()
    assert g.step(QUIET, 0.0) == (None, None)
