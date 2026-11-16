"""Lifetime stats, XP and ranks (Dale, 2026-11-15): the totals are what the server's
own counters gained, a player's XP is the end-of-round board's number added up, and
every chat line fits the chat limit."""

import pytest

from tests.test_keep_stats import Clock, FakeServer
from wolfrat import career
from wolfrat.career import (DEATHS, HEADSHOTS, HEALS, KILLS, KNIFE_KILLS, REVIVES, XP, ZONES, Career,
                            CareerStore, CareerTracker, build_ranks, rank_for)
from wolfrat.keep_stats import StatsKeeper


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path):
    made = CareerStore(str(tmp_path / "career.db"))
    yield made
    made.close()


@pytest.fixture
def ups():
    return []


@pytest.fixture
def tracker(store, clock, ups):
    return CareerTracker(store, clock=clock, ranked_up=lambda name, rank: ups.append((name, rank.number)))


@pytest.fixture
def keeper(clock, tracker):
    made = StatsKeeper(clock=clock)
    made.watch = tracker.observe
    return made


def second(keeper, server, clock, reader=None):
    clock.now += 1.0
    keeper.tick(server, reader=reader)


# ---- adding up ---------------------------------------------------------------------------

def test_xp_is_the_boards_number_for_a_player_who_stays_all_round(server, keeper, clock, store):
    server.join(1, "BadgerLove")
    second(keeper, server, clock)
    for kills, points in ((1, 10), (2, 15), (5, 62)):
        server.play(1, kills=kills, deaths=1, points=points)
        second(keeper, server, clock)
    found = store.find("badgerlove")
    assert (found.xp, found.kills, found.deaths) == (62, 5, 1)       # the board shows 62


def test_every_counter_goes_to_its_own_total(server, keeper, clock, store):
    server.join(1, "Medic")
    second(keeper, server, clock)
    for field, value in ((HEADSHOTS, 3), (KNIFE_KILLS, 2), (REVIVES, 7), (HEALS, 11), (ZONES, 4), (KILLS, 9)):
        server.set(1, field, value)
    second(keeper, server, clock)
    found = store.find("Medic")
    assert (found.headshots, found.knife_kills, found.revives, found.heals, found.zones, found.kills) == (3, 2, 7, 11, 4, 9)
    assert found.headshot_percent == 33


def test_what_a_player_already_had_when_wolfrat_started_is_not_counted(server, keeper, clock, store):
    server.join(1, "Early")
    server.play(1, kills=40, deaths=10, points=300)
    second(keeper, server, clock)                # first look: a baseline, not a gain
    server.play(1, kills=41, deaths=10, points=305)
    second(keeper, server, clock)
    assert (store.find("Early").kills, store.find("Early").xp) == (1, 5)


def test_a_team_change_keeps_what_was_earned(server, keeper, clock, store):
    server.join(1, "Switcher")
    second(keeper, server, clock)
    server.play(1, kills=10, deaths=2, points=80)
    second(keeper, server, clock)
    server.change_team(1, 2)                     # the server zeroes the record
    second(keeper, server, clock)
    server.play(1, kills=3, deaths=1, points=20)
    second(keeper, server, clock)
    found = store.find("Switcher")
    assert (found.xp, found.kills, found.deaths) == (100, 13, 3)


def test_a_new_round_carries_on_from_the_total(server, keeper, clock, store):
    server.join(1, "Regular")
    second(keeper, server, clock)
    server.play(1, kills=20, deaths=5, points=150)
    second(keeper, server, clock)
    server.round_over()
    second(keeper, server, clock)                # the board is up: nothing changes
    assert store.find("Regular").xp == 150
    server.zero_score(1)
    server.round_over(False)
    second(keeper, server, clock)
    server.play(1, kills=4, deaths=0, points=30)
    second(keeper, server, clock)
    found = store.find("Regular")
    assert (found.xp, found.kills, found.deaths) == (180, 24, 5)


def test_stats_written_back_after_a_rejoin_are_not_counted_twice(server, keeper, clock, store):
    server.join(1, "OscarMike")
    second(keeper, server, clock)
    server.play(1, kills=30, deaths=5, points=240)
    second(keeper, server, clock)
    server.leave(1)
    second(keeper, server, clock)
    server.join(2, "OscarMike")
    second(keeper, server, clock)                # Save player stats puts 30/5/240 back
    assert server.get(2, KILLS) == 30
    second(keeper, server, clock)
    server.play(2, kills=31, deaths=5, points=245)
    second(keeper, server, clock)
    found = store.find("OscarMike")
    assert (found.xp, found.kills, found.deaths) == (245, 31, 5)


def test_it_still_counts_with_save_player_stats_off(server, keeper, clock, store):
    keeper.enabled = False
    server.join(1, "Solo")
    second(keeper, None, clock, reader=server)
    server.play(1, kills=2, deaths=0, points=10)
    second(keeper, None, clock, reader=server)
    assert store.find("Solo").xp == 10
    assert server.writes == []


def test_xp_the_server_takes_away_comes_off_but_never_below_zero(server, keeper, clock, store):
    server.join(1, "Clumsy")
    second(keeper, server, clock)
    server.play(1, kills=1, points=5)
    second(keeper, server, clock)
    server.play(1, kills=2, points=-20)          # kills went up, so this is no wipe
    second(keeper, server, clock)
    assert store.find("Clumsy").xp == 0


def test_best_streak_ends_at_a_death(server, keeper, clock, store):
    server.join(1, "Streaker")
    second(keeper, server, clock)
    for kills, deaths in ((3, 0), (7, 0), (7, 1), (9, 1)):
        server.play(1, kills=kills, deaths=deaths)
        second(keeper, server, clock)
    assert store.find("Streaker").best_streak == 7


def test_time_played_counts_only_in_the_match_and_not_across_a_gap(server, keeper, clock, store):
    server.join(1, "Player")
    server.join(2, "Lobby", state=3)
    second(keeper, server, clock)
    for _ in range(10):
        second(keeper, server, clock)
    clock.now += 600                             # WolfRAT lost the server for ten minutes
    second(keeper, server, clock)
    assert store.find("Player").seconds == pytest.approx(10 + career.LONGEST_GAP_SECONDS)
    assert store.find("Lobby") is None


def test_totals_are_still_there_after_a_restart(tmp_path, clock):
    path = str(tmp_path / "career.db")
    first = CareerStore(path)
    held = first.hold("BadgerLove")
    held.xp, held.kills = 412000, 6056
    first.touched("BadgerLove")
    first.close()
    again = CareerStore(path)
    assert (again.find("BADGERLOVE").xp, again.find("badger").kills) == (412000, 6056)
    again.close()


def test_find_wants_one_match(store):
    for name in ("FMJ-Badger", "FMJ-Wolf"):
        store.hold(name).kills = 1
        store.touched(name)
    assert store.find("wolf").name == "FMJ-Wolf"
    assert store.find("fmj") is None             # two players hold it
    assert store.find("100%") is None


def test_place_on_the_server_is_by_xp(store):
    for name, xp in (("A", 500), ("B", 9000), ("C", 40)):
        store.hold(name).xp = xp
        store.touched(name)
    assert store.position(500) == (2, 3)
    assert [c.name for c in store.top(2)] == ["B", "A"]


# ---- ranks -------------------------------------------------------------------------------

def test_the_default_ranks_are_fifty_numbered_in_order_and_top_out_at_a_million():
    ranks = build_ranks()
    assert [r.number for r in ranks] == list(range(1, 51))
    assert ranks[0].xp == 0 and ranks[-1].xp == 1000000
    assert all(a.xp < b.xp for a, b in zip(ranks, ranks[1:]))
    assert sum(1 for r in ranks if r.xp <= 100000) >= 30
    assert max(len(r.name) for r in ranks) <= 16


def test_rank_for_and_next_rank():
    ranks = build_ranks()
    assert rank_for(0, ranks).number == 1
    assert rank_for(99, ranks).number == 1
    assert rank_for(100, ranks).number == 2
    assert rank_for(5000000, ranks).number == 50
    assert career.next_rank(99, ranks).number == 2
    assert career.next_rank(1000000, ranks) is None


def test_edited_ranks_are_sorted_cleaned_and_start_at_zero():
    ranks = build_ranks([["Boss", 500], ["Newbie", 50], ["", 70], ["Bad", "x"], ["Twin", 500], ["Café", 900]])
    assert [(r.number, r.name, r.xp) for r in ranks] == [(1, "Newbie", 0), (2, "Boss", 500), (3, "Caf?", 900)]
    assert build_ranks([]) == build_ranks()


def test_a_rank_up_is_told_once_when_the_xp_crosses_it(server, keeper, clock, ups):
    server.join(1, "Climber")
    second(keeper, server, clock)
    for points in (60, 99, 100, 180, 260):
        server.play(1, kills=points, points=points)
        second(keeper, server, clock)
    assert ups == [("Climber", 2), ("Climber", 3)]


# ---- chat lines --------------------------------------------------------------------------

BIG = Career(name="FMJ-BadgerLove123", xp=1234567, kills=123456, deaths=98765, headshots=45678, knife_kills=12345,
             revives=23456, heals=98765, zones=12345, best_streak=123, seconds=5000 * 3600)
DALE = Career(name="BadgerLove", xp=412000, kills=6056, deaths=5000, headshots=605, knife_kills=400,
              revives=310, heals=900, zones=40, best_streak=23, seconds=142 * 3600)


def test_kd_ratio_works_like_the_old_kd_line():
    # kills / deaths to 2 places; no deaths = the kill count (app.py PlayerStatsStore)
    assert DALE.kd == 1.21 and career.kd_text(DALE) == "1.21"
    assert career.kd_text(Career(name="a", kills=1, deaths=2)) == "0.50"
    assert career.kd_text(Career(name="a", kills=7)) == "7.00"
    assert career.kd_text(Career(name="a")) == "0.00"
    assert career.kd_text(Career(name="a", kills=12345)) == "12.3k"
    assert career.stats_lines(Career(name="Rookie", kills=7), 59)[0] == "Rookie: K/D 7.00 (7/0) | HS 0 0% | KK 0"


def test_xp_is_written_in_thousands():
    assert [career.xp_text(v) for v in (0, 950, 1000, 1234, 41200, 412300, 1000000, 1234567)] == [
        "0", "950", "1k", "1.2k", "41.2k", "412k", "1M", "1.23M"]


def test_the_stats_reply_is_two_lines_on_a_stock_server():
    assert career.stats_lines(DALE, 59) == [
        "BadgerLove: K/D 1.21 (6056/5000) | HS 605 10% | KK 400",
        "Streak 23 | Revives 310 | Heals 900 | Zones 40 | 142h",
    ]


def test_the_stats_reply_is_one_line_on_long_chat():
    # with bars it is 25 words, over the server's 23 a line, so commas
    assert career.stats_lines(DALE, 118) == [
        "BadgerLove: K/D 1.21 (6056/5000), HS 605 10%, KK 400, Streak 23, Revives 310, Heals 900, Zones 40, "
        "Played 142h"]


def test_big_numbers_are_shortened_to_stay_on_two_lines():
    # no room for the kills/deaths counts: the ratio stays
    assert career.stats_lines(BIG, 59) == [
        "FMJ-BadgerLove123: K/D 1.25 | HS 45.7k 37% | KK 12.3k",
        "Streak 123 Revives 23.5k Heals 98.8k Zones 12.3k 5000h",
    ]


@pytest.mark.parametrize("limit", (59, 118))
@pytest.mark.parametrize("who", (DALE, BIG, Career(name="x")))
def test_no_reply_is_ever_over_the_chat_limit_or_more_than_two_lines(limit, who):
    ranks = build_ranks()
    lines = career.stats_lines(who, limit)
    lines.append(career.rank_line(who.name, 12345, 123456, who.xp, ranks, limit))
    lines.append(career.rank_up_line(who.name, ranks[-1], limit))
    assert len(career.stats_lines(who, limit)) <= 2
    for line in lines:
        assert career.fits(line, limit), line
        line.encode("ascii")


def test_the_rank_reply():
    ranks = build_ranks()
    assert career.rank_line("BadgerLove", 12, 340, 41200, ranks, 59) == "BadgerLove: #12 of 340 | Rank 26 Colonel | 41.2k XP"
    assert career.rank_line("BadgerLove", 12, 340, 41200, ranks, 118) == (
        "BadgerLove: #12 of 340 | Rank 26 Colonel | 41.2k XP | Next Senior Colonel in 3.8k")
    assert career.rank_up_line("BadgerLove", ranks[27], 59) == "BadgerLove is now Rank 28 Brigadier!"

# ---- colours (long chat servers) ---------------------------------------------------------

def test_the_rank_reply_in_colour():
    ranks = build_ranks()
    assert career.rank_line("BadgerLove", 12, 340, 41200, ranks, 118, colour=True) == (
        "<cffff00>BadgerLove <c40c0ff>#12 <c8080ff>Rank 26 Colonel <c00ff00>41.2k XP "
        "<c8080ff>Next Senior Colonel in 3.8k")
    assert career.rank_line("Bo", 1, 340, 1000000, ranks, 118, colour=True) == (
        "<cffff00>Bo <c40c0ff>#1 of 340 <c808080>| <cffd700>Rank 50 Joint Ops Legend <c808080>| <c00ff00>1M XP")
    assert career.rank_up_line("BadgerLove", ranks[27], 118, colour=True, ranks=ranks) == (
        "<cffff00>BadgerLove <c808080>is now <c8080ff>Rank 28 Brigadier!")


def test_the_stats_reply_in_colour_is_two_lines():
    assert career.stats_lines(DALE, 118, colour=True) == [
        "<cffff00>BadgerLove <c00ff00>K/D 1.21 (6056/5000) <cff3030>HS 605 10% <cff8000>KK 400",
        "<cff80c0>Streak 23 <c00ffff>Revives 310 <c40c0ff>Heals 900 <c8080ff>Zones 40 <cc0c0c0>Played 142h",
    ]


def test_the_rank_colour_climbs_from_grey_to_gold_whatever_the_number_of_ranks():
    for ranks in (build_ranks(), build_ranks([["A", 0], ["B", 10], ["C", 20]]), build_ranks([["Only", 0]])):
        shades = [career.tier_colour(rank, ranks) for rank in ranks]
        assert shades[-1] == "ffd700"
        assert [career.TIER_COLOURS.index(s) for s in shades] == sorted(career.TIER_COLOURS.index(s) for s in shades)
    assert career.tier_colour(build_ranks()[0], build_ranks()) == "c0c0c0"


@pytest.mark.parametrize("who", (DALE, BIG, Career(name="x"), Career(name="A<c123456>B<co>", xp=99)))
def test_coloured_replies_fit_long_chat_and_a_name_cannot_carry_a_colour_code(who):
    from wolfrat import chat_markup
    ranks = build_ranks()
    lines = career.stats_lines(who, 118, colour=True)
    assert len(lines) == 2
    for place, players in ((1, 1), (12345, 123456)):
        lines.append(career.rank_line(who.name, place, players, who.xp, ranks, 118, colour=True))
    for rank in ranks:
        lines.append(career.rank_up_line(who.name, rank, 118, colour=True, ranks=ranks))
    for line in lines:
        assert career.fits(line, 118), line
        assert line.startswith("<c")
        assert chat_markup.tag_problem(line) is None, line
        assert "123456>" not in chat_markup.visible(line) or who.name == "x"
        line.encode("ascii")
