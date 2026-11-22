"""!mycomp: the one chat line a player gets about their own lag compensation.

2026-11-22 (Dale): players were reading a minus of a few hits as lag
compensation hurting them when it was only luck.  The line now gives the plus
or minus only when it means something: at least 50 hits, and a difference
outside the larger of 10% of the plain JO hits and 2 x sqrt(saved + cost)."""

from wolfrat import lag_comp as lc


def _state(*players, on=True, observe=False, min_ping=80):
    """Players as (name, ping, rewind, hits, plain hits[, rewound bullet checks
    [, saved, cost]])."""
    stats = lc.Stats()
    for i, (name, ping, rewind, hits, plain, *rest) in enumerate(players):
        tests = rest[0] if rest else 100
        saved, cost = rest[1:3] if len(rest) >= 3 else (0, 0)
        stats.players.append(lc.PlayerStats(i, name, ping, rewind, tests, hits, plain,
                                            saved, cost))
    return lc.State(kind=lc.ON if on else "off", observe=observe,
                    settings=lc.Settings(observe=observe, min_ping_ms=min_ping),
                    stats=stats)


def test_the_rule_is_50_hits_and_10_percent():
    assert (lc.MYCOMP_MIN_HITS, lc.MYCOMP_EVEN_PERCENT) == (50, 10)


def test_the_full_line():
    # A clear plus: 28 more than 120 plain hits, saved 40 and cost 12.
    st = _state(("BadgerLove", 142, 212, 148, 120, 5000, 40, 12))
    assert lc.mycomp_line(st, "badgerlove") == "BadgerLove P142 R212 Hits 148 +28 comp (+23%)"


def test_comp_that_clearly_took_hits_away_still_says_so():
    # A clear minus keeps its number: 40 fewer than 200 is well outside luck
    # (saved 30, cost 70: twice the luck is 20).
    st = _state(("Bravo", 234, 284, 160, 200, 9000, 30, 70))
    assert lc.mycomp_line(st, "bravo") == "Bravo P234 R284 Hits 160 -40 comp (-20%)"


def test_no_plain_hits_leaves_the_percent_out():
    st = _state(("Elk", 160, 230, 60, 0, 4000, 60, 0))
    assert lc.mycomp_line(st, "elk") == "Elk P160 R230 Hits 60 +60 comp"


def test_under_50_hits_it_says_too_few_shots_and_gives_no_number():
    # The old lines for these two were "Hits 40 -3 comp (-7%)" and "Hits 48 +9 comp (+23%)".
    st = _state(("Bravo", 234, 284, 40, 43), ("Alpha", 142, 212, 48, 39),
                ("Edge", 90, 160, 49, 49), ("None", 160, 230, 0, 0))
    assert lc.mycomp_line(st, "bravo") == "Bravo P234 R284 Hits 40: too few shots yet"
    assert lc.mycomp_line(st, "alpha") == "Alpha P142 R212 Hits 48: too few shots yet"
    assert lc.mycomp_line(st, "edge") == "Edge P90 R160 Hits 49: too few shots yet"
    assert lc.mycomp_line(st, "none") == "None P160 R230 Hits 0: too few shots yet"


def test_50_hits_with_it_or_plain_is_enough_for_a_number():
    # The larger of the two counts, so a player comp cost a lot is not left at "too few".
    st = _state(("With", 142, 212, 50, 30), ("Plain", 142, 212, 30, 50))
    assert lc.mycomp_line(st, "with") == "With P142 R212 Hits 50 +20 comp (+67%)"
    assert lc.mycomp_line(st, "plain") == "Plain P142 R212 Hits 30 -20 comp (-40%)"


def test_about_even_up_to_10_percent_of_the_plain_hits():
    # Saved and cost left at 0 here, so only the 10% counts: 10 of 100 is the edge.
    st = _state(("Up", 142, 212, 110, 100), ("Over", 142, 212, 111, 100),
                ("Down", 142, 212, 90, 100), ("Under", 142, 212, 89, 100),
                ("Same", 142, 212, 100, 100))
    assert lc.mycomp_line(st, "up") == "Up P142 R212 Hits 110: about even with plain JO"
    assert lc.mycomp_line(st, "down") == "Down P142 R212 Hits 90: about even with plain JO"
    assert lc.mycomp_line(st, "same") == "Same P142 R212 Hits 100: about even with plain JO"
    # One hit past the edge, either way: the number.
    assert lc.mycomp_line(st, "over") == "Over P142 R212 Hits 111 +11 comp (+11%)"
    assert lc.mycomp_line(st, "under") == "Under P142 R212 Hits 89 -11 comp (-11%)"


def test_about_even_up_to_twice_the_luck_in_saved_and_cost():
    """The difference is saved - cost, and by luck alone it moves about
    sqrt(saved + cost): inside twice that it says nothing, even past 10%."""
    # Saved 40, cost 24: 2 x sqrt(64) = 16, so +16 of 100 is the edge.
    # Saved 41, cost 24: 2 x sqrt(65) = 16.1, and +17 is past it.
    st = _state(("Edge", 142, 212, 116, 100, 9000, 40, 24),
                ("Past", 142, 212, 117, 100, 9000, 41, 24),
                ("Minus", 142, 212, 84, 100, 9000, 24, 40),
                ("Worse", 142, 212, 83, 100, 9000, 24, 41))
    assert lc.mycomp_line(st, "edge") == "Edge P142 R212 Hits 116: about even with plain JO"
    assert lc.mycomp_line(st, "minus") == "Minus P142 R212 Hits 84: about even with plain JO"
    assert lc.mycomp_line(st, "past") == "Past P142 R212 Hits 117 +17 comp (+17%)"
    assert lc.mycomp_line(st, "worse") == "Worse P142 R212 Hits 83 -17 comp (-17%)"


def test_the_larger_of_the_two_edges_counts():
    # 10% of 1,000 plain hits is 100; twice the luck in saved 100 + cost 10 is only 21.
    st = _state(("Big", 142, 212, 1090, 1000, 90000, 100, 10),
                ("Clear", 142, 212, 1101, 1000, 90000, 111, 10))
    assert lc.mycomp_line(st, "big") == "Big P142 R212 Hits 1090: about even with plain JO"
    assert lc.mycomp_line(st, "clear") == "Clear P142 R212 Hits 1101 +101 comp (+10%)"


def test_low_ping_needs_no_comp():
    # Never rewound this session: no bullet checks, no rewind now.
    st = _state(("Nikolay", 45, 0, 0, 0, 0))
    assert lc.mycomp_line(st, "nikolay") == "Nikolay P45 No comp needed (under 80)"


def test_a_player_rewound_earlier_gets_their_numbers_when_their_ping_dips():
    """Review N5: a player hovering at the minimum ping was rewound for most of
    the session; in a second at 78 ms the line said "No comp needed"."""
    st = _state(("Hover", 78, 0, 148, 120, 5200))
    assert lc.mycomp_line(st, "hover") == "Hover P78 R0 Hits 148 +28 comp (+23%)"


def test_no_comp_needed_is_not_said_when_everyone_is_rewound():
    # Minimum ping 0 = nobody is "under" it: no "under 0" line, their hits instead.
    st = _state(("Elk", 0, 0, 0, 0, 0), min_ping=0)
    assert lc.mycomp_line(st, "elk") == "Elk P0 R0 Hits 0: too few shots yet"
    # Being rewound right now is never "no comp needed", bullets or not.
    st = _state(("New", 120, 190, 0, 0, 0))
    assert lc.mycomp_line(st, "new") == "New P120 R190 Hits 0: too few shots yet"


def test_off_and_not_loaded():
    assert lc.mycomp_line(None, "x") == lc.MYCOMP_OFF
    assert lc.mycomp_line(_state(("A", 100, 170, 1, 1), on=False), "a") == lc.MYCOMP_OFF


def test_watch_only():
    st = _state(("A", 150, 220, 5, 5), observe=True)
    assert "watch-only" in lc.mycomp_line(st, "a")


def test_not_in_the_rows_yet():
    st = _state(("Someone", 100, 170, 1, 1))
    assert lc.mycomp_line(st, "newguy") == "newguy: no lag comp numbers yet"


def test_partial_name_only_when_one_fits():
    st = _state(("FMJ-BadgerLove", 142, 212, 48, 39), ("FMJ-Elk", 90, 160, 2, 2))
    assert lc.mycomp_line(st, "badger").startswith("FMJ-BadgerLove P142")
    assert lc.mycomp_line(st, "fmj") == "fmj: no lag comp numbers yet"


def test_stock_chat_limit_falls_back_to_the_short_form():
    name = "A" * 31
    st = _state((name, 142, 212, 1234, 1000))
    long = lc.mycomp_line(st, name, 118)
    short = lc.mycomp_line(st, name, 59)
    assert long == name + " P142 R212 Hits 1234 +234 comp (+23%)"
    # Stock chat: the name is cut first, the numbers stay whole.
    assert short == "A" * 22 + " P142 R212 Hits 1234 +234 comp (+23%)"
    # Tighter still: the short form.
    assert lc.mycomp_line(st, name, 34) == "A" * 6 + " P142 R212 1234h +234 (+23%)"


def test_too_few_and_about_even_fit_stock_chat_and_have_a_short_form():
    name = "A" * 31
    few = _state((name, 142, 212, 48, 39))
    assert lc.mycomp_line(few, name, 118) == name + " P142 R212 Hits 48: too few shots yet"
    # Stock chat (59): the name is cut first, the words stay whole.
    assert lc.mycomp_line(few, name, 59) == "A" * 22 + " P142 R212 Hits 48: too few shots yet"
    # Tighter still: the short form.
    assert lc.mycomp_line(few, name, 34) == "A" * 8 + " P142 R212 48h too few yet"
    even = _state((name, 142, 212, 1050, 1000))
    assert lc.mycomp_line(even, name, 118) == (
        name + " P142 R212 Hits 1050: about even with plain JO")
    assert lc.mycomp_line(even, name, 59) == (
        "A" * 13 + " P142 R212 Hits 1050: about even with plain JO")
    assert lc.mycomp_line(even, name, 34) == "A" * 7 + " P142 R212 1050h about even"
    for state in (few, even):
        assert len(lc.mycomp_line(state, name, 59)) == 59
        # Too tight even for the short form: the same last word as every other line.
        assert lc.mycomp_line(state, name, 20) == lc.MYCOMP_OFF


def test_at_59_only_impossible_numbers_need_the_short_form():
    """On a stock server the long wording always fits (the name gives way).  The
    short form at 59 takes a ping no player has; it is there for tighter limits."""
    name = "A" * 31
    st = _state((name, 2147483647, 900, 4000000000, 4000000000))
    assert lc.mycomp_line(st, name, 59) == (
        "A" * 19 + " P2147483647 R900 4000000000h about even")


def test_every_line_fits_both_limits():
    st = _state(("X" * 31, 999, 400, 99999, 99999), ("Y" * 31, 79, 0, 0, 0),
                ("W" * 31, 79, 0, 0, 0, 0), ("V" * 31, 79, 0, 99999, 88888, 999999),
                ("T" * 31, 999, 400, 49, 49), ("U" * 31, 999, 400, 99999, 9))
    for limit in (59, 118):
        for who in ("X" * 31, "Y" * 31, "Z" * 31, "W" * 31, "V" * 31, "T" * 31, "U" * 31):
            line = lc.mycomp_line(st, who, limit)
            assert len(line) <= limit
            assert line != lc.MYCOMP_OFF            # a real line, never "off" for lack of room
