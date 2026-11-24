"""The phone page's "game server down" rules (contract v3): what the bell lists
and what the phones are told when the admin link drops.  Plain Python with a
made-up clock.

A map load drops the link for a few seconds and the admin port stays open; a
stopped server program refuses WolfRAT's tries to connect."""

from wolfrat.web_events import LinkWatch, duration_text

T0 = 50_000.0


class Link:
    """One LinkWatch, read every 5 s like the web server's own check."""

    def __init__(self, start=T0):
        self.watch = LinkWatch()
        self.now = start
        self.events = []
        self.notices = []

    def read(self, connected, refused=False, on_purpose=False):
        events, notices = self.watch.update(connected, self.now, refused, on_purpose)
        self.events.extend(events)
        self.notices.extend(notices)
        return events, notices

    def run(self, seconds, connected, refused=False, on_purpose=False, step=5):
        """The same reading for ``seconds`` more (one every ``step`` s)."""
        end = self.now + seconds
        while self.now < end:
            self.now += step
            self.read(connected, refused, on_purpose)

    def up(self, seconds=60):
        self.run(seconds, True)
        return self

    def drop(self):
        """The first reading with the link down; returns its time."""
        self.now += 5
        self.read(False)
        return self.now

    def bell(self):
        return [(event.kind, event.text) for event in self.events]


def test_nothing_until_the_link_has_been_up_once():
    link = Link()
    link.run(600, False)                               # WolfRAT is still connecting
    link.run(600, False, refused=True)                 # ...to a server that is not running
    assert link.events == [] and link.notices == []
    link.run(10, True)                                 # connected for the first time
    assert link.events == [] and link.notices == []


def test_a_map_load_says_nothing():
    link = Link().up()
    for seconds in (5, 10, 30, 115):                   # back inside 2 minutes, never refused
        link.drop()
        link.run(seconds, False)
        link.run(60, True)
    assert link.events == [] and link.notices == []


def test_a_quick_restart_is_stopped_then_back_on_the_bell_and_one_line_on_the_phone():
    link = Link().up()
    dropped = link.drop()                              # the link drops...
    assert link.events == []                           # ...nothing known yet
    link.run(5, False, refused=True)                   # the first try is refused
    assert [(e.kind, e.text, e.name, e.ts) for e in link.events] == [
        ("server", "Game server stopped", None, dropped)]   # its time: when the link dropped
    link.run(130, False, refused=True)                 # still refused: said once
    assert len(link.events) == 1 and link.notices == []
    link.run(5, True)                                  # back 2 min 20 s after the drop
    assert link.bell() == [
        ("server", "Game server stopped"),
        ("server", "Game server is back (down 2 min 20 s)"),
    ]
    assert link.events[1].ts == link.now
    assert link.notices == ["Game server restarted (down 2 min 20 s)"]
    link.run(600, True)                                # and nothing more
    assert len(link.events) == 2 and len(link.notices) == 1


def test_the_contract_example_down_2_min_24_s():
    watch = LinkWatch()
    assert watch.update(True, 1000) == ([], [])
    assert watch.update(False, 1010) == ([], [])
    events, notices = watch.update(False, 1015, refused=True)
    assert [event.text for event in events] == ["Game server stopped"] and notices == []
    events, notices = watch.update(True, 1010 + 144)
    assert [event.text for event in events] == ["Game server is back (down 2 min 24 s)"]
    assert notices == ["Game server restarted (down 2 min 24 s)"]
    assert watch.update(True, 1200) == ([], [])


def test_back_at_exactly_3_minutes_is_still_one_line():
    watch = LinkWatch()
    watch.update(True, 1000)
    watch.update(False, 1010)
    watch.update(False, 1015, refused=True)
    assert watch.update(False, 1010 + 179, refused=True) == ([], [])
    _events, notices = watch.update(True, 1010 + 180)
    assert notices == ["Game server restarted (down 3 min)"]


def test_a_long_stop_tells_the_phone_at_3_minutes_and_again_when_back():
    link = Link().up()
    link.drop()
    link.run(175, False, refused=True)
    assert link.bell() == [("server", "Game server stopped")] and link.notices == []
    link.run(5, False, refused=True)                   # 3 minutes down
    assert link.notices == ["Game server is down"]
    link.run(33 * 60 + 55, False, refused=True)        # said once, however long
    assert link.notices == ["Game server is down"] and len(link.events) == 1
    link.run(5, True)                                  # back 37 minutes after the drop
    assert link.bell() == [("server", "Game server stopped"),
                           ("server", "Game server is back (down 37 min)")]
    assert link.notices == ["Game server is down", "Game server is back (down 37 min)"]


def test_a_stop_stays_a_stop_when_later_tries_are_not_refused():
    """The program is starting again: the port opens before it answers."""
    link = Link().up()
    link.drop()
    link.run(60, False, refused=True)
    link.run(150, False)                               # tries now time out instead
    assert link.bell() == [("server", "Game server stopped")]
    assert link.notices == ["Game server is down"]     # not "lost the link"
    link.run(5, True)
    assert link.bell()[-1] == ("server", "Game server is back (down 3 min 35 s)")
    assert link.notices[-1] == "Game server is back (down 3 min 35 s)"


def test_a_lost_link_without_a_refusal_is_reported_as_the_link():
    link = Link().up()
    dropped = link.drop()
    link.run(115, False)
    assert link.events == [] and link.notices == []
    link.run(5, False)                                 # 2 minutes: the bell
    assert [(e.kind, e.text, e.ts) for e in link.events] == [
        ("server", "Lost the link to the game server", dropped)]
    assert link.notices == []
    link.run(55, False)
    assert link.notices == []
    link.run(5, False)                                 # 3 minutes: the phones
    assert link.notices == ["Lost the link to the game server"]
    link.run(120, False)
    assert len(link.events) == 1 and len(link.notices) == 1
    link.run(5, True)                                  # back 5 min 5 s after the drop
    assert link.bell()[-1] == ("server", "Game server link is back (down 5 min)")
    assert link.notices[-1] == "Link to the game server is back (down 5 min 5 s)"


def test_a_lost_link_back_before_3_minutes_never_reaches_the_phones():
    link = Link().up()
    link.drop()
    link.run(150, False)
    link.run(5, True)
    assert link.bell() == [("server", "Lost the link to the game server"),
                           ("server", "Game server link is back (down 2 min)")]
    assert link.notices == []


def test_a_refusal_after_the_link_was_reported_lost_becomes_a_stop():
    """The server's PC was away and came back without the game server running."""
    link = Link().up()
    link.drop()
    link.run(200, False)
    assert link.bell() == [("server", "Lost the link to the game server")]
    assert link.notices == ["Lost the link to the game server"]
    link.run(100, False, refused=True)
    assert link.bell() == [("server", "Lost the link to the game server"),
                           ("server", "Game server stopped")]
    assert link.notices == ["Lost the link to the game server"]     # told once it is down
    link.run(5, True)
    assert link.bell()[-1] == ("server", "Game server is back (down 5 min 5 s)")
    assert link.notices[-1] == "Game server is back (down 5 min 5 s)"


def test_a_refusal_first_seen_after_3_minutes_tells_the_phone_it_is_down():
    watch = LinkWatch()
    watch.update(True, 1000)
    assert watch.update(False, 1010) == ([], [])
    events, notices = watch.update(False, 1300, refused=True)   # the checks were held up
    assert [event.text for event in events] == ["Game server stopped"]
    assert notices == ["Game server is down"]


def test_the_admin_disconnecting_on_purpose_says_nothing():
    link = Link().up()
    link.run(20 * 60, False, on_purpose=True)          # he pressed Disconnect
    link.run(60, False, refused=True, on_purpose=True)  # ...and the server stopped meanwhile
    assert link.events == [] and link.notices == []
    # He presses Connect while the server is still not running: it starts
    # fresh, so this is "not connected yet", not a server going down.
    link.run(10 * 60, False, refused=True)
    assert link.events == [] and link.notices == []
    link.run(60, True)
    assert link.events == [] and link.notices == []
    link.drop()
    link.run(5, False, refused=True)                   # a real stop afterwards is reported
    assert link.bell() == [("server", "Game server stopped")]


def test_disconnecting_on_purpose_in_the_middle_of_a_stop_forgets_it():
    link = Link().up()
    link.drop()
    link.run(200, False, refused=True)
    assert link.bell() == [("server", "Game server stopped")]
    assert link.notices == ["Game server is down"]
    link.run(60, False, refused=True, on_purpose=True)
    link.run(60, False, refused=True)                  # he presses Connect again
    link.run(60, True)
    assert link.bell() == [("server", "Game server stopped")]       # no "back"
    assert link.notices == ["Game server is down"]


def test_disconnecting_on_purpose_while_still_connected_says_nothing():
    """The flag can be seen a moment before the link closes."""
    watch = LinkWatch()
    watch.update(True, 1000)
    assert watch.update(True, 1005, on_purpose=True) == ([], [])
    assert watch.update(False, 1010, on_purpose=True) == ([], [])
    assert watch.update(False, 2000) == ([], [])


def test_every_stop_is_reported_on_its_own():
    link = Link().up()
    for _ in range(3):
        link.drop()
        link.run(55, False, refused=True)
        link.run(60, True)                             # back 1 minute after the drop
    assert link.bell() == [("server", "Game server stopped"),
                           ("server", "Game server is back (down 1 min)")] * 3
    assert link.notices == ["Game server restarted (down 1 min)"] * 3
    link.drop()                                        # then a map load: nothing more
    link.run(30, False)
    link.run(60, True)
    assert len(link.events) == 6 and len(link.notices) == 3


def test_durations_read_as_a_person_says_them():
    assert [duration_text(seconds) for seconds in (0, 1, 45, 59)] == [
        "0 s", "1 s", "45 s", "59 s"]
    assert [duration_text(seconds) for seconds in (60, 61, 144, 37 * 60, 3599)] == [
        "1 min", "1 min 1 s", "2 min 24 s", "37 min", "59 min 59 s"]
    assert [duration_text(seconds) for seconds in (3600, 3659, 3660, 4320, 4379, 90000)] == [
        "1 h", "1 h", "1 h 1 min", "1 h 12 min", "1 h 12 min", "25 h"]
    assert duration_text(144.9) == "2 min 24 s" and duration_text(-5) == "0 s"
