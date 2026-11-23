"""The phone page's alerts (contract v2): the event feed, join / leave / empty,
"the first couple that join", the game server link, maps and votes.
Plain Python with a made-up clock."""

from wolfrat import web_events
from wolfrat.web_events import (
    EMPTY_AFTER,
    EventFeed,
    Join,
    LinkWatch,
    MapWatch,
    PlayerWatch,
    VoteWatch,
    join_body,
)

MIN = 60


def kinds(events):
    return [(event.kind, event.name) for event in events]


class Clock:
    """Readings every 5 s, as WolfRAT's poll gives them."""

    def __init__(self, watch, start=10_000.0, first_joins=2):
        self.watch = watch
        self.now = start
        self.first_joins = first_joins
        self.events = []
        self.notified = []

    def read(self, names, connected=True, first_joins=None):
        events, joins = self.watch.update(
            connected, names, self.now,
            self.first_joins if first_joins is None else first_joins,
        )
        self.events.extend(events)
        self.notified.extend(joins)
        return events, joins

    def run(self, seconds, names, connected=True, step=5):
        """The same list for ``seconds`` (one reading every ``step`` s)."""
        end = self.now + seconds
        while self.now < end:
            self.now += step
            self.read(names, connected)


# --- the feed ------------------------------------------------------------------


def test_the_feed_keeps_the_last_100_with_rising_ids():
    feed = EventFeed(clock=lambda: 1_000.0)
    first = feed.add("join", "Kestrel joined", "Kestrel", ts=1234.9)
    assert first == {"id": 1_000_001, "ts": 1234, "kind": "join",
                     "text": "Kestrel joined", "name": "Kestrel"}
    for number in range(120):
        feed.add("leave", f"P{number} left", f"P{number}")
    assert feed.last_id == 1_000_121
    kept = feed.after(0)
    assert len(kept) == 100 and kept[0]["id"] == 1_000_022 and kept[-1]["id"] == 1_000_121
    assert [event["id"] for event in feed.recent(50)] == list(range(1_000_072, 1_000_122))
    assert [event["id"] for event in feed.after(1_000_119)] == [1_000_120, 1_000_121]
    assert feed.after(feed.last_id) == []


def test_ids_keep_rising_after_a_restart():
    before = EventFeed(clock=lambda: 1_000.0)
    for _ in range(5):
        before.add("map", "New map: X")
    after = EventFeed(clock=lambda: 1_001.0)          # started a second later
    assert after.add("map", "New map: Y")["id"] > before.last_id


def test_events_are_copies():
    feed = EventFeed()
    feed.add("map", "New map: X")
    feed.recent()[0]["text"] = "changed"
    assert feed.recent()[0]["text"] == "New map: X"


# --- join / leave --------------------------------------------------------------


def test_the_first_list_is_a_baseline():
    clock = Clock(PlayerWatch())
    assert clock.read(["Kestrel", "Ham"]) == ([], [])
    clock.run(60, ["Kestrel", "Ham"])
    assert clock.events == []


def test_a_map_load_that_drops_the_list_gives_nothing():
    clock = Clock(PlayerWatch())
    clock.read(["Kestrel", "Ham"])
    clock.run(60, ["Kestrel", "Ham"])
    clock.run(90, [])                                  # the map loads: nobody listed
    clock.run(60, ["Kestrel", "Ham"])
    assert clock.events == []


def test_leaving_counts_after_2_minutes_with_the_time_last_seen():
    clock = Clock(PlayerWatch())
    clock.read(["Kestrel", "Ham"])
    clock.run(30, ["Kestrel", "Ham"])
    last_seen = clock.now
    clock.run(115, ["Ham"])
    assert clock.events == []
    clock.run(10, ["Ham"])
    assert kinds(clock.events) == [("leave", "Kestrel")]
    assert clock.events[0].ts == last_seen
    assert clock.events[0].text == "Kestrel left"


def test_joining_counts_only_when_not_seen_for_3_minutes():
    clock = Clock(PlayerWatch())
    clock.read(["Ham"])
    clock.run(20, ["Ham", "Kestrel"])
    assert kinds(clock.events) == [("join", "Kestrel")]
    assert clock.events[0].text == "Kestrel joined"
    clock.events.clear()
    clock.run(150, ["Ham"])                            # gone 2.5 min: left...
    assert kinds(clock.events) == [("leave", "Kestrel")]
    clock.run(5, ["Ham", "Kestrel"])                   # ...back inside 3 minutes: same visit
    assert kinds(clock.events) == [("leave", "Kestrel")]
    clock.run(200, ["Ham"])
    clock.run(5, ["Ham", "Kestrel"])                   # gone over 3 minutes: joins again
    assert kinds(clock.events) == [("leave", "Kestrel"), ("leave", "Kestrel"),
                                   ("join", "Kestrel")]


def test_names_match_whatever_their_case_or_spacing():
    clock = Clock(PlayerWatch())
    clock.read(["Kestrel"])
    clock.run(30, ["kestrel "])
    clock.run(300, [" KESTREL"])
    assert clock.events == []


def test_a_lost_link_is_unknown_and_the_list_is_compared_with_the_one_before():
    clock = Clock(PlayerWatch())
    clock.read(["Kestrel", "Ham"])
    clock.run(30, ["Kestrel", "Ham"])
    last_seen = clock.now
    clock.run(20 * MIN, [], connected=False)           # link down: says nothing
    assert clock.events == []
    clock.run(5, ["Ham", "Newbie"])                    # back: Newbie joined
    assert kinds(clock.events) == [("join", "Newbie")]
    clock.run(110, ["Ham", "Newbie"])                  # Kestrel: 2 minutes of link time
    assert kinds(clock.events) == [("join", "Newbie")]
    clock.run(10, ["Ham", "Newbie"])
    assert kinds(clock.events) == [("join", "Newbie"), ("leave", "Kestrel")]
    assert clock.events[1].ts == last_seen


def test_the_baseline_waits_for_the_link():
    clock = Clock(PlayerWatch())
    clock.read([], connected=False)
    clock.read(["Kestrel"])                            # first list with the link up
    assert clock.events == []


# --- "the first couple that join" ----------------------------------------------


def empty_server(first_joins=2):
    clock = Clock(PlayerWatch(), first_joins=first_joins)
    clock.read(["Ham"])
    clock.run(30, ["Ham"])
    clock.run(EMPTY_AFTER + 200, [])                   # Ham leaves; empty 10 minutes
    clock.events.clear()
    return clock


def test_the_first_two_joins_after_10_minutes_empty_notify_the_third_does_not():
    clock = empty_server()
    assert clock.watch.armed
    clock.run(5, ["Kestrel"])
    clock.run(30, ["Kestrel", "Ham"])
    clock.run(30, ["Kestrel", "Ham", "Ali"])
    assert kinds(clock.events) == [("join", "Kestrel"), ("join", "Ham"), ("join", "Ali")]
    assert clock.notified == [Join("Kestrel", 1), Join("Ham", 2)]
    assert join_body(clock.notified[0]) == "Kestrel joined (1 player on)"
    assert join_body(clock.notified[1]) == "Ham joined (2 players on)"
    assert not clock.watch.armed


def test_then_none_until_empty_for_10_minutes_again():
    clock = empty_server()
    clock.run(5, ["Kestrel"])
    clock.run(5, ["Kestrel", "Ham"])
    clock.run(5 * MIN, [])                             # empty only 5 minutes
    clock.run(5, ["Ali"])
    assert [join.name for join in clock.notified] == ["Kestrel", "Ham"]
    clock.run(EMPTY_AFTER + 200, [])                   # Ali goes, empty 10 minutes
    clock.run(5, ["Zed"])
    assert [join.name for join in clock.notified] == ["Kestrel", "Ham", "Zed"]


def test_several_joins_in_one_reading_notify_up_to_the_setting():
    clock = empty_server()
    clock.run(5, ["A", "B", "C"])
    assert clock.notified == [Join("A", 3), Join("B", 3)]


def test_the_setting_is_read_at_each_join():
    clock = empty_server(first_joins=1)
    clock.run(5, ["A"])
    clock.run(5, ["A", "B"])
    assert [join.name for join in clock.notified] == ["A"]
    clock = empty_server(first_joins=0)                # 0 = no join notifications
    clock.run(5, ["A"])
    assert clock.notified == [] and not clock.watch.armed


def test_at_start_with_players_on_nothing_notifies_until_it_empties():
    clock = Clock(PlayerWatch())
    clock.read(["Ham", "Kestrel"])                     # WolfRAT starts mid-game
    clock.run(30 * MIN, ["Ham", "Kestrel"])
    clock.run(5, ["Ham", "Kestrel", "Ali"])
    assert kinds(clock.events) == [("join", "Ali")]
    assert clock.notified == []


def test_at_start_on_an_empty_server_10_minutes_still_count():
    clock = Clock(PlayerWatch())
    clock.read([])
    clock.run(9 * MIN, [])
    clock.run(5, ["Early"])
    assert clock.notified == []
    clock = Clock(PlayerWatch())
    clock.read([])
    clock.run(EMPTY_AFTER, [])
    clock.run(5, ["Kestrel"])
    assert clock.notified == [Join("Kestrel", 1)]


def test_a_restart_is_a_new_baseline():
    clock = empty_server()
    clock.watch = PlayerWatch()                        # WolfRAT (or its web server) restarted
    clock.read(["Kestrel"])
    clock.run(60, ["Kestrel", "Ham"])
    assert kinds(clock.events) == [("join", "Ham")]
    assert clock.notified == []


def test_a_disconnected_link_never_counts_as_empty():
    clock = Clock(PlayerWatch())
    clock.read(["Ham"])
    clock.run(30, ["Ham"])
    clock.run(6 * MIN, [])                             # empty 6 minutes...
    clock.run(60 * MIN, [], connected=False)           # ...then the link is down an hour
    clock.run(5 * MIN, [])                             # back: only 5 more minutes empty
    assert not clock.watch.armed
    clock.run(5, ["Kestrel"])
    assert clock.notified == []
    clock = Clock(PlayerWatch())
    clock.read([], connected=False)
    clock.run(60 * MIN, [], connected=False)           # never connected: never empty
    clock.run(5, ["Kestrel"])
    assert clock.notified == [] and clock.events == []


def test_being_armed_survives_a_lost_link():
    clock = empty_server()
    clock.run(10 * MIN, [], connected=False)
    clock.run(5, ["Kestrel"])
    assert clock.notified == [Join("Kestrel", 1)]


def test_a_map_load_after_the_first_join_does_not_notify_twice():
    clock = empty_server()
    clock.run(5, ["Kestrel"])
    clock.run(150, [])                                 # a long map load: Kestrel "left"
    clock.run(5, ["Kestrel"])                          # back inside 3 minutes: no join
    assert [join.name for join in clock.notified] == ["Kestrel"]
    clock.run(5, ["Kestrel", "Ham"])
    assert [join.name for join in clock.notified] == ["Kestrel", "Ham"]


# --- the link, the map, the vote -------------------------------------------------


def test_the_link_is_reported_after_2_minutes_down_and_when_back():
    watch = LinkWatch()
    assert watch.update(False, 0) == []                # never up yet: not lost
    assert watch.update(False, 500) == []
    assert watch.update(True, 600) == []
    assert watch.update(False, 1000) == []
    assert watch.update(False, 1100) == []             # a short drop: nothing
    lost = watch.update(False, 1120)
    assert [(event.kind, event.text, event.ts) for event in lost] == [
        ("server", "Lost the link to the game server", 1000)]
    assert watch.update(False, 1500) == []             # once
    back = watch.update(True, 1300 + 1000)
    assert [(event.kind, event.text) for event in back] == [
        ("server", "Game server link is back (down 21 min)")]
    assert watch.update(False, 3000) == [] and watch.update(True, 3060) == []


def test_a_new_map_is_an_event_the_first_one_is_not():
    watch = MapWatch()
    assert watch.update("AAS_Doslin.bms", "Doslin Oblast", 1) == []
    assert watch.update("aas_doslin.BMS", "Doslin Oblast", 2) == []
    assert watch.update(None, None, 3) == []           # unknown changes nothing
    events = watch.update("TDM_X.bms", "Snakebite", 4)
    assert [(event.kind, event.text, event.name) for event in events] == [
        ("map", "New map: Snakebite", None)]
    assert watch.update("TDM_Y.bms", None, 5)[0].text == "New map: TDM_Y.bms"


def test_votes_start_and_end():
    watch = VoteWatch()
    running = {"kind": "map", "active": True, "map": "Snakebite", "votes": 1, "needed": 3}
    assert watch.update(running, 1) == []              # baseline: already running
    assert watch.update(running, 2) == []
    assert [event.text for event in watch.update(None, 3)] == ["Map vote ended"]
    assert [event.text for event in watch.update(
        {"kind": "skip", "active": True}, 4)] == ["Vote to skip the map started"]
    assert [event.text for event in watch.update(
        {"kind": "end_of_match", "active": True, "map": "X"}, 5)] == [
        "Vote to skip the map ended", "End-of-match map vote started"]
    assert [event.text for event in watch.update(None, 6)] == ["End-of-match map vote ended"]
    started = watch.update(running, 7)
    assert [(event.kind, event.text) for event in started] == [
        ("vote", "Map vote started: Snakebite")]


def test_every_kind_is_in_the_contract():
    assert set(web_events.KINDS) == {
        "join", "leave", "map", "vote", "kick", "ban", "warn", "server"}
