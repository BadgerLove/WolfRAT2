"""The phone page's alerts (the bell), each phone's choices and what goes to
the phones as notifications.

Plain Python, no Qt and no network, so every rule here can be tested with a
made-up clock:

* ``EventFeed`` keeps the last 100 events in memory (any thread).
* ``GROUPS``, ``group_of`` and ``clean_prefs``: the kinds of alert a phone
  can switch on and off, for its bell and for its notifications.
* ``PlayerWatch`` turns the player lists into join and leave events and says
  which joins should go to the phones ("the first couple that join" a server
  that has been empty).
* ``LinkWatch`` turns the admin link into "the game server stopped", "lost
  the link" and "back", for the bell and for the phones.
* ``MapWatch`` and ``VoteWatch`` turn the current map and the vote into
  events.

The watchers are fed by the web server's own thread (``WolfWebServer``), one
reading at a time.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import NamedTuple, Optional

MAX_EVENTS = 100          # kept in memory, nothing saved
STATE_EVENTS = 50         # sent with every state
JOIN_GAP = 3 * 60         # a player counts as joined only if not seen for 3 minutes
LEAVE_AFTER = 2 * 60      # ...and as gone once missing for 2 minutes (map loads empty the list)
EMPTY_AFTER = 10 * 60     # no players for 10 minutes while the link is up = empty
LINK_LOST_AFTER = 2 * 60  # the admin link down this long is worth an alert
DOWN_NOTICE_AFTER = 3 * 60  # ...and down this long is worth a notification

KINDS = ("join", "leave", "map", "vote", "kick", "ban", "warn", "server",
         "zone", "flag")

# --- What each phone hears about ---
# Every kind of event belongs to one group; a phone switches each group on or
# off twice: for its bell (the list on the page) and for its notifications.
GROUPS = ("join", "leave", "map", "vote", "admin", "server", "objective")
KIND_GROUPS = {
    "join": "join",
    "leave": "leave",
    "map": "map",
    "vote": "vote",
    "kick": "admin",
    "ban": "admin",
    "warn": "admin",
    "server": "server",
    "zone": "objective",
    "flag": "objective",
}
CHANNELS = ("bell", "phone")
# What a phone gets until it chooses.
DEFAULT_PREFS = {
    "bell": {"join": True, "leave": False, "map": False, "vote": False,
             "admin": True, "server": True, "objective": False},
    "phone": {"join": True, "leave": False, "map": False, "vote": False,
              "admin": False, "server": True, "objective": False},
}
# Groups whose every event is one notification.  Not "join" (only the first
# joins on an empty server notify, see PlayerWatch) and not "server" (the
# phones get their own lines, see LinkWatch).
EVENT_NOTICE_GROUPS = frozenset({"leave", "map", "vote", "admin", "objective"})


def group_of(kind):
    """The group an event kind belongs to, or None for a kind nobody listed."""
    return KIND_GROUPS.get(str(kind))


def default_prefs():
    """A phone's choices before it made any."""
    return {channel: dict(DEFAULT_PREFS[channel]) for channel in CHANNELS}


def clean_prefs(value):
    """A full, valid set of choices from whatever was saved: every group in
    both lists, anything missing or damaged at its default."""
    saved = value if isinstance(value, Mapping) else {}
    prefs = {}
    for channel in CHANNELS:
        chosen = saved.get(channel)
        chosen = chosen if isinstance(chosen, Mapping) else {}
        prefs[channel] = {
            group: (chosen[group] if isinstance(chosen.get(group), bool)
                    else DEFAULT_PREFS[channel][group])
            for group in GROUPS
        }
    return prefs


def changed_prefs(current, changes):
    """``current`` with the switches named in ``changes`` set:
    ``{"bell": {"map": True}, "phone": {"server": False}}``, any part of it.

    Returns ``(prefs, None)``, or ``(None, reason)`` when a list, a group or a
    value is not one of ours (then nothing is to change).
    """
    if not isinstance(changes, Mapping):
        return None, "The choices must be an object"
    prefs = clean_prefs(current)
    for channel, switches in changes.items():
        if channel not in CHANNELS:
            return None, f"Unknown list of choices: {str(channel)[:40]}"
        if not isinstance(switches, Mapping):
            return None, f"{channel} must be an object of switches"
        for group, value in switches.items():
            if group not in GROUPS:
                return None, f"Unknown kind of alert: {str(group)[:40]}"
            if not isinstance(value, bool):
                return None, f"{channel}.{group} must be true or false"
            prefs[channel][group] = value
    return prefs, None


def prefs_reply(prefs):
    """A phone's choices as the page gets them."""
    prefs = clean_prefs(prefs)
    return {"groups": list(GROUPS), "bell": prefs["bell"], "phone": prefs["phone"]}


def duration_text(seconds):
    """How long something lasted: "45 s", "2 min 24 s", "1 h 12 min"."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes} min {rest} s" if rest else f"{minutes} min"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


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
    """The admin link: "the game server stopped", "lost the link", "back".

    ``update(connected, now, refused, on_purpose)`` takes one reading and
    returns ``(events, notices)``: events for the bell, and lines for the
    phones that have the "server" group on.

    * ``refused``: WolfRAT's last try to connect found nothing listening on
      the admin port.  That is the server program itself not running; a map
      load only drops the link for a few seconds and the port stays open.
    * ``on_purpose``: the admin pressed Disconnect on the PC.  Nothing is
      said and everything is forgotten; it starts fresh when he connects.
    * Nothing until the link has been up once (WolfRAT not connected yet is
      not a lost link), and nothing for a drop that is back before a rule
      below fires.

    The bell: "Game server stopped" as soon as a try is refused (its time is
    when the link dropped), or "Lost the link to the game server" after 2
    minutes down without one; then "Game server is back (down 2 min 24 s)" or
    "Game server link is back (down 3 min)".

    The phones: a stop that is over within 3 minutes is one line when it is
    back, "Game server restarted (down 2 min 24 s)".  Still down at 3
    minutes: "Game server is down" ("Lost the link to the game server" when
    no try was refused), then "Game server is back (down 37 min)" ("Link to
    the game server is back (down 5 min)").
    """

    def __init__(self):
        self._forget()

    def _forget(self):
        self._ever_up = False
        self._down_since = None
        self._stopped = False         # a try was refused: the bell said "stopped"
        self._lost = False            # the bell said "lost the link"
        self._told = False            # the phones were told it is down

    def update(self, connected, now, refused=False, on_purpose=False):
        events, notices = [], []
        if on_purpose:
            self._forget()
            return events, notices
        if connected:
            if self._down_since is not None:
                down = now - self._down_since
                if self._stopped:
                    events.append(Event(
                        'server', f"Game server is back (down {duration_text(down)})",
                        None, now))
                    notices.append(
                        f"Game server is back (down {duration_text(down)})" if self._told
                        else f"Game server restarted (down {duration_text(down)})")
                elif self._lost:
                    minutes = max(1, int(down // 60))
                    events.append(Event(
                        'server', f"Game server link is back (down {minutes} min)",
                        None, now))
                    if self._told:
                        notices.append(
                            f"Link to the game server is back (down {duration_text(down)})")
            self._forget()
            self._ever_up = True
            return events, notices
        if not self._ever_up:
            return events, notices
        if self._down_since is None:
            self._down_since = now
        down = now - self._down_since
        if refused and not self._stopped:
            self._stopped = True
            events.append(Event('server', "Game server stopped", None, self._down_since))
        elif not self._stopped and not self._lost and down >= LINK_LOST_AFTER:
            self._lost = True
            events.append(Event('server', "Lost the link to the game server", None,
                                self._down_since))
        if not self._told and down >= DOWN_NOTICE_AFTER:
            self._told = True
            notices.append("Game server is down" if self._stopped
                           else "Lost the link to the game server")
        return events, notices


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
