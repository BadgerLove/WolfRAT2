"""!mycomp: the one chat line a player gets about their own lag compensation."""

from wolfrat import lag_comp as lc


def _state(*players, on=True, observe=False, min_ping=80):
    stats = lc.Stats()
    for i, (name, ping, rewind, hits, plain) in enumerate(players):
        stats.players.append(lc.PlayerStats(i, name, ping, rewind, 100, hits, plain, 0, 0))
    return lc.State(kind=lc.ON if on else "off", observe=observe,
                    settings=lc.Settings(observe=observe, min_ping_ms=min_ping),
                    stats=stats)


def test_the_full_line():
    st = _state(("BadgerLove", 142, 212, 48, 39))
    assert lc.mycomp_line(st, "badgerlove") == "BadgerLove P142 R212 Hits 48 +9 comp (+23%)"


def test_comp_that_took_hits_away_says_so():
    st = _state(("Bravo", 234, 284, 40, 43))
    assert lc.mycomp_line(st, "bravo") == "Bravo P234 R284 Hits 40 -3 comp (-7%)"


def test_no_plain_hits_leaves_the_percent_out():
    st = _state(("Elk", 160, 230, 0, 0))
    assert lc.mycomp_line(st, "elk") == "Elk P160 R230 Hits 0 +0 comp"


def test_low_ping_needs_no_comp():
    st = _state(("Nikolay", 45, 0, 10, 10))
    assert lc.mycomp_line(st, "nikolay") == "Nikolay P45 No comp needed (under 80)"


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


def test_every_line_fits_both_limits():
    st = _state(("X" * 31, 999, 400, 99999, 99999), ("Y" * 31, 79, 0, 0, 0))
    for limit in (59, 118):
        for who in ("X" * 31, "Y" * 31, "Z" * 31):
            assert len(lc.mycomp_line(st, who, limit)) <= limit
