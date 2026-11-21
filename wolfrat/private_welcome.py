"""The private welcome: lines only the player who joined sees (Dale, 2026-11-21).

Rebuilt on the private-line helper that already carries !mycmd and the rank-up
sound (rank_tone.py: one line to one player's slot). The old version wrote two
lines into the server's script (server.wac): 63 characters each, and a change
only showed at the next map. Now each line holds what one helper line carries
to the player (118 characters, colour codes included, the same limit as
!mycmd), up to three lines (the chat shows three), and a change is live for the
next player who joins: no script, no map change. It needs WolfRAT running on
the same PC as the server, like the helper itself.

A player is welcomed once per visit: a name that turns up in the 5 s player
list and was not in it during the last REVISIT_SECONDS. The first list after
WolfRAT starts only learns who is already playing, so a restart of WolfRAT
does not welcome everyone again. No Qt here.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from wolfrat import mycmd, server_wac

LINE_LIMIT = mycmd.LINE_LIMIT      # 118: one helper line, colour codes included
MAX_LINES = 3                      # the chat shows three; a 4th only in J history
NAME_ROOM = 16                     # JO names are up to 16 characters ("%-16s")
MIN_SECONDS, MAX_SECONDS, DEFAULT_SECONDS = 5, 300, 30
REVISIT_SECONDS = 600              # back within 10 minutes = the same visit
PLACEHOLDER = "{player}"

FIRST_LINE_HINT = "<c40c0ff>Welcome<co> to the server, {player}!"
SECOND_LINE_HINT = "<cffff00>Commands:<co> !stats !rank !mycmd !vote mapname !skip"


@dataclass
class Settings:
    enabled: bool = False
    lines: list = field(default_factory=lambda: ["", "", ""])
    seconds: int = DEFAULT_SECONDS
    imported: bool = False         # the old script welcome has been read in once

    def used_lines(self) -> list:
        return [line.strip() for line in self.lines[:MAX_LINES] if line.strip()]


def load(path) -> Optional[Settings]:
    """The saved settings, or None if there are none yet."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    lines = [str(line) for line in data.get("lines", [])][:MAX_LINES]
    lines += [""] * (MAX_LINES - len(lines))
    seconds = data.get("seconds", DEFAULT_SECONDS)
    try:
        seconds = max(MIN_SECONDS, min(MAX_SECONDS, int(seconds)))
    except (TypeError, ValueError):
        seconds = DEFAULT_SECONDS
    return Settings(bool(data.get("enabled", False)), lines, seconds,
                    bool(data.get("imported", False)))


def save(path, settings: Settings) -> None:
    data = {"enabled": settings.enabled, "lines": list(settings.lines[:MAX_LINES]),
            "seconds": settings.seconds, "imported": settings.imported}
    temp = str(path) + ".new"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temp, path)


def line_problem(line: str) -> Optional[str]:
    """Why a line can't go out as typed, or None (blank lines are skipped)."""
    text = line.strip()
    if not text:
        return None
    room = len(text.replace(PLACEHOLDER, "x" * NAME_ROOM))
    if room > LINE_LIMIT:
        named = " with a 16-letter name" if PLACEHOLDER in text else ""
        return f"Too long: {room} of {LINE_LIMIT} characters{named}, colour codes count."
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return "It has letters the game can't show."
    return None


def render(line: str, name: str) -> str:
    """The line as one player gets it: their name in, cut to one helper line."""
    return line.strip().replace(PLACEHOLDER, name)[:LINE_LIMIT]


def old_script_lines(server_dir) -> Optional[list]:
    """The lines of the old script welcome in server.wac, codes and all, or None."""
    if not server_dir:
        return None
    try:
        old = server_wac.read_welcome(server_dir)
    except server_wac.ServerWacError:
        return None
    if old is None:
        return None
    lines = [server_wac.compose(old.text, old.colour)]
    if old.text2:
        lines.append(server_wac.compose(old.text2, old.colour2))
    return lines


class JoinWatch:
    """Who just joined, from the player list WolfRAT reads every 5 s."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 revisit: float = REVISIT_SECONDS):
        self._clock = clock
        self._revisit = revisit
        self._last_seen: dict = {}
        self._learned = False

    def update(self, names: Iterable[str]) -> list:
        now = self._clock()
        present = [name for name in names if name]
        if not self._learned:
            # The first list after WolfRAT starts: these were already playing.
            self._learned = True
            for name in present:
                self._last_seen[name] = now
            return []
        joined = []
        for name in present:
            last = self._last_seen.get(name)
            if last is None or now - last > self._revisit:
                joined.append(name)
            self._last_seen[name] = now
        return joined
