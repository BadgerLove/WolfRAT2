"""The phone's "time left on this map": for this round only (no Qt).

Setting the game server's GameTime to N gives the running map N minutes and
also makes N the length of every round after it.  The phone's button means
this round only, so WolfRAT remembers the round length it found and sets it
again once the next round has started (the same setting, sent a second time).

What is still to be put back is kept per server ("host:port") in
``wolfrat_time_left.json`` next to the other WolfRAT settings, so a WolfRAT
restart in between does not leave the server on the short length.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

FILE_NAME = "wolfrat_time_left.json"
MIN_MINUTES, MAX_MINUTES = 1, 240
MAX_TRIES = 5                    # failed attempts to put the length back, then WolfRAT gives up


@dataclass
class Pending:
    map_file: str                # the map that was running when the phone set the time
    original: int                # the round length to put back, minutes
    minutes: int                 # what the phone set
    due: bool = False            # the next round has started: send it
    tries: int = 0               # sends that failed

    def to_json(self) -> dict:
        return {"map": self.map_file, "original": self.original, "minutes": self.minutes,
                "due": self.due, "tries": self.tries}

    @classmethod
    def from_json(cls, data) -> Optional["Pending"]:
        if not isinstance(data, dict):
            return None
        map_file, original, minutes = data.get("map"), data.get("original"), data.get("minutes")
        tries = data.get("tries", 0)
        if not isinstance(map_file, str) or not map_file:
            return None
        for number in (original, minutes, tries):
            if isinstance(number, bool) or not isinstance(number, int):
                return None
        if original < 1 or not MIN_MINUTES <= minutes <= MAX_MINUTES or tries < 0:
            return None
        return cls(map_file, original, minutes, bool(data.get("due", False)), tries)


def _same_map(one, other) -> bool:
    return str(one or "").casefold() == str(other or "").casefold()


class TimeRestore:
    """The round lengths waiting to be put back.  Every call is on the window's thread."""

    def __init__(self, path=None):
        self._path = str(path) if path else None
        self._pending: dict = self._read()
        self._last_left: dict = {}       # server -> minutes left at the last look (not saved)

    # ---- the file -----------------------------------------------------------
    def _read(self) -> dict:
        if not self._path or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return {}
        found = {str(key): Pending.from_json(value) for key, value in servers.items()}
        return {key: entry for key, entry in found.items() if entry is not None}

    def _write(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"servers": {key: entry.to_json()
                                       for key, entry in self._pending.items()}}, handle, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass                         # it still holds until WolfRAT closes

    # ---- what the bridge asks -------------------------------------------------
    def pending(self, server: str) -> Optional[Pending]:
        return self._pending.get(server)

    def note_set(self, server: str, map_file: str, limit: int, minutes: int) -> int:
        """The phone is setting ``minutes`` left on ``map_file``, whose round
        length reads ``limit`` now.  Returns the length that will be put back.

        While the server is still on a length the phone set earlier, the length
        found before that one is kept: ours must never become "the original"."""
        entry = self._pending.get(server)
        original = entry.original if entry is not None and limit == entry.minutes else int(limit)
        self._last_left.pop(server, None)
        if minutes == original:
            self._pending.pop(server, None)          # the normal length: nothing to put back
        else:
            self._pending[server] = Pending(str(map_file), original, int(minutes))
        self._write()
        return original

    def clock_restarted(self, server: str) -> None:
        """Our own change just restarted the clock: the minutes left before it
        say nothing about a new round."""
        self._last_left.pop(server, None)

    def reading(self, server: str, map_file, left: int, limit: int) -> Optional[int]:
        """One look after a poll: the running map's file, the minutes left and
        the round length.  Returns the round length to send now, or None.

        A new round has started when the map is another one, or when it is the
        same map and the minutes left went up while the length is still ours
        (the same map loaded again).  If the length is no longer ours by then,
        somebody changed it since: nothing is sent and it is forgotten."""
        entry = self._pending.get(server)
        if entry is None or not map_file:
            return None
        if not entry.due:
            if not _same_map(map_file, entry.map_file):
                entry.due = True
            elif limit == entry.minutes:
                before = self._last_left.get(server)
                self._last_left[server] = left
                entry.due = before is not None and left > before
            if not entry.due:
                return None
            self._write()
        if limit != entry.minutes:
            self.forget(server)
            return None
        return entry.original

    def sent(self, server: str) -> None:
        """The server took the old length back."""
        self.forget(server)

    def failed(self, server: str) -> bool:
        """A send failed.  True = try again at the next poll, False = given up."""
        entry = self._pending.get(server)
        if entry is None:
            return False
        entry.tries += 1
        if entry.tries >= MAX_TRIES:
            self.forget(server)
            return False
        self._write()
        return True

    def forget(self, server: str) -> None:
        self._last_left.pop(server, None)
        if self._pending.pop(server, None) is not None:
            self._write()
