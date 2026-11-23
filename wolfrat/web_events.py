"""The phone page's alerts (the bell) and who counts for join notifications.

Plain Python, no Qt and no network, so every rule here can be tested with a
made-up clock:

* ``EventFeed`` keeps the last 100 events in memory (any thread).
* ``PlayerWatch`` turns the player lists into join and leave events and says
  which joins should go to the phones ("the first couple that join" a server
  that has been empty).
* ``LinkWatch``, ``MapWatch`` and ``VoteWatch`` turn the admin link, the
  current map and the vote into events.

The watchers are fed by the web server's own thread (``WolfWebServer``), one
reading at a time.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import NamedTuple, Optional

MAX_EVENTS = 100          # kept in memory, nothing saved
STATE_EVENTS = 50         # sent with every state
JOIN_GAP = 3 * 60         # a player counts as joined only if not seen for 3 minutes
LEAVE_AFTER = 2 * 60      # ...and as gone once missing for 2 minutes (map loads empty the list)
EMPTY_AFTER = 10 * 60     # no players for 10 minutes while the link is up = empty
LINK_LOST_AFTER = 2 * 60  # the admin link down this long is worth an alert

KINDS = ("join", "leave", "map", "vote", "kick", "ban", "warn", "server")


class Event(NamedTuple):
    """One event before it gets its id."""

    kind: str
    text: str
    name: Optional[str]
    ts: float


class Join(NamedTuple):
    """One join that should go to the phones."""

    name: str
    players_on: int


class EventFeed:
    """The last ``size`` events, each ``{id, ts, kind, text, name}``.

    Ids keep rising for as long as WolfRAT runs, and start from the clock (in
    milliseconds) so they also keep rising across a restart: a phone that
    remembers the last id it saw never mistakes new events for old ones.
    """

    def __init__(self, size=MAX_EVENTS, clock=time.time):
        self._lock = threading.Lock()
        self._events = deque(maxlen=size)
        self._last_id = int(clock() * 1000)

    def add(self, kind, text, name=None, ts=None):
        """Store one event; returns it as sent to the page."""
        with self._lock:
            self._last_id += 1
            event = {
                'id': self._last_id,
                'ts': int(time.time() if ts is None else ts),
                'kind': str(kind),
                'text': str(text),
                'name': None if name is None else str(name),
            }
            self._events.append(event)
            return dict(event)

    @property
    def last_id(self):
        with self._lock:
            return self._last_id

    def recent(self, count=STATE_EVENTS):
        """The newest ``count`` events, oldest first."""
        with self._lock:
            events = list(self._events)
        return [dict(event) for event in events[-count:]] if count > 0 else []

    def after(self, event_id):
        """Every kept event newer than ``event_id``, oldest first."""
        with self._lock:
            return [dict(event) for event in self._events if event['id'] > event_id]


def _player_key(name):
    return ' '.join(str(name or '').split()).casefold()


def _players_text(count):
    return f"{count} player{'' if count == 1 else 's'} on"


def join_body(join):
    """The notification line for one join: "Kestrel joined (1 player on)"."""
    return f"{join.name} joined ({_players_text(join.players_on)})"


class _Seen:
    __slots__ = ('name', 'last', 'clock', 'on')

    def __init__(self, name, now):
        self.name = name          # as the server shows it now
        self.last = now           # last time in a player list
        self.clock = now          # when the leave clock started (link time only)
        self.on = True            # joined and not recorded as gone


class PlayerWatch:
    """Join, leave and "empty" from the player lists.

    ``update(connected, names, now, first_joins)`` takes one reading and
    returns ``(events, joins_to_notify)``:

    * The first list after the watch is made (WolfRAT or its web server just
      started) is the baseline: no events, and no notifications until the
      server has been empty.
    * A player joins only when not seen in the last 3 minutes, and has left
      once missing for 2 minutes (the event carries the time last seen): a map
      load empties the list for a moment and gives nothing.
    * A reading with the admin link down says nothing (unknown, never empty).
      On the next reading with the link up the list is compared with the one
      from before; the 2 minutes for leaving only count link-up time.
    * Empty = no players for 10 minutes with the link up.  After that the next
      ``first_joins`` joins are notified, then none until it has been empty
      for 10 minutes again.
    """

    def __init__(self):
        self._seen = {}               # player key -> _Seen
        self._baseline = True
        self._link_up = False
        self._empty_since = None
        self._armed = False           # it has been empty: the next joins notify
        self._notified = 0

    @property
    def armed(self):
        return self._armed

    def update(self, connected, names, now, first_joins=2):
        events, notify = [], []
        if not connected:
            self._link_up = False
            self._empty_since = None
            return events, notify
        current = {}
        for name in names or ():
            key = _player_key(name)
            if key:
                current[key] = ' '.join(str(name).split())
        count = len(current)

        if self._baseline:
            self._baseline = False
            self._link_up = True
            self._seen = {key: _Seen(name, now) for key, name in current.items()}
            self._empty_since = None if current else now
            return events, notify

        if not self._link_up:
            # Back after the link was down: the time it was down is not
            # time gone.  The list from before stays as it was.
            self._link_up = True
            for seen in self._seen.values():
                seen.clock = max(seen.clock, now)

        # Empty for long enough (checked before this list's joins count).
        if self._empty_since is not None and now - self._empty_since >= EMPTY_AFTER:
            self._armed = True
            self._notified = 0

        for key, name in current.items():
            seen = self._seen.get(key)
            if seen is not None and seen.on:
                seen.name, seen.last, seen.clock = name, now, now
                continue
            joined = seen is None or now - seen.last >= JOIN_GAP
            if seen is None:
                seen = self._seen[key] = _Seen(name, now)
            else:
                seen.name, seen.last, seen.clock, seen.on = name, now, now, True
            if not joined:
                continue                  # back within 3 minutes: same visit
            events.append(Event('join', f"{name} joined", name, now))
            if self._armed:
                if self._notified < first_joins:
                    notify.append(Join(name, count))
                    self._notified += 1
                if self._notified >= first_joins:
                    self._armed = False

        for key, seen in self._seen.items():
            if key in current or not seen.on:
                continue
            if now - seen.clock >= LEAVE_AFTER:
                seen.on = False
                events.append(Event('leave', f"{seen.name} left", seen.name, seen.last))

        # Forget players long gone (they would count as joined anyway).
        for key in [key for key, seen in self._seen.items()
                    if not seen.on and now - seen.last >= JOIN_GAP]:
            del self._seen[key]

        if current:
            self._empty_since = None
        elif self._empty_since is None:
            self._empty_since = now
        elif now - self._empty_since >= EMPTY_AFTER:
            self._armed = True
            self._notified = 0
        return events, notify


class LinkWatch:
    """"Lost the link to the game server" after 2 minutes down, then "back".

    Nothing until the link has been up once (WolfRAT not connected yet is
    not a lost link).
    """

    def __init__(self):
        self._ever_up = False
        self._down_since = None
        self._lost = False

    def update(self, connected, now):
        if connected:
            events = []
            if self._lost:
                minutes = max(1, int((now - self._down_since) // 60))
                events.append(Event('server', f"Game server link is back (down {minutes} min)",
                                    None, now))
            self._ever_up = True
            self._down_since = None
            self._lost = False
            return events
        if not self._ever_up:
            return []
        if self._down_since is None:
            self._down_since = now
        if not self._lost and now - self._down_since >= LINK_LOST_AFTER:
            self._lost = True
            return [Event('server', "Lost the link to the game server", None,
                          self._down_since)]
        return []


class MapWatch:
    """"New map: <title>" when the current map changes (the first one seen is
    the baseline; an unknown map changes nothing)."""

    def __init__(self):
        self._map = None

    def update(self, map_file, title, now):
        if not map_file:
            return []
        key = str(map_file).casefold()
        previous, self._map = self._map, key
        if previous is None or previous == key:
            return []
        return [Event('map', f"New map: {title or map_file}", None, now)]


_VOTE_NAMES = {
    'map': 'Map vote',
    'skip': 'Vote to skip the map',
    'end_of_match': 'End-of-match map vote',
}


class VoteWatch:
    """"<vote> started" / "<vote> ended" from the desktop's vote state (the
    first state seen is the baseline)."""

    _UNSEEN = object()

    def __init__(self):
        self._kind = self._UNSEEN     # None = no vote running

    def update(self, vote, now):
        active = isinstance(vote, dict) and bool(vote.get('active'))
        kind = (str(vote.get('kind') or 'map') if active else None)
        previous, self._kind = self._kind, kind
        if previous is self._UNSEEN or previous == kind:
            return []
        events = []
        if previous is not None:
            events.append(Event('vote', f"{_VOTE_NAMES.get(previous, 'Vote')} ended", None, now))
        if kind is not None:
            label = _VOTE_NAMES.get(kind, 'Vote')
            target = vote.get('map') if kind == 'map' else None
            text = f"{label} started: {target}" if target else f"{label} started"
            events.append(Event('vote', text, None, now))
        return events
