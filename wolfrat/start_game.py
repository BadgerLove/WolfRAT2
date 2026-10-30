"""!startgame: cut the start-of-map countdown short.

At every map start the server copies its Start Delay setting into a
whole-second countdown and holds the round until it reaches 0
(``Server_ResetRoundCounters @0x516C50``; the periodic block takes one off it
every second).  ``SET StartDelay`` only changes what the NEXT map copies, so
the running countdown is written directly: down to SECONDS_LEFT, and the
server's own countdown opens the round as it always does.

The address map is the retail jointops.exe's (the weather map's build): the
weather fingerprint plus the countdown store's own bytes are checked first.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

PREROUND_TIMER = 0x00C8D824     # g_PreRoundDelayTimer: seconds left before the round opens
SECONDS_LEFT = 5

# mov eax,[g_StartDelay 0x24D2160] / mov [g_PreRoundDelayTimer 0xC8D824],eax
COUNTDOWN_STORE = (0x00516C88, bytes.fromhex("a160214d02a324d8c800"))

STARTED = "started"             # countdown cut to SECONDS_LEFT
RUNNING = "running"             # the round is already open (countdown 0)
ALMOST = "almost"               # SECONDS_LEFT or less left already


@dataclass(frozen=True)
class Outcome:
    kind: str
    seconds_before: int


class StartGameError(Exception):
    """Plain-words reason the countdown could not be changed."""


def shorten_countdown(memory, seconds: int = SECONDS_LEFT) -> Outcome:
    """Bring a running countdown down to ``seconds``.  ``memory`` must already
    be the verified server (``weather.attach_local_server``)."""
    address, expected = COUNTDOWN_STORE
    if memory.read(address, len(expected)) != expected:
        raise StartGameError(
            "This jointops.exe is not the build !startgame was made for, "
            "so nothing was changed."
        )
    left = struct.unpack("<i", memory.read(PREROUND_TIMER, 4))[0]
    if left <= 0:
        return Outcome(RUNNING, left)
    if left <= seconds:
        return Outcome(ALMOST, left)
    memory.write(PREROUND_TIMER, struct.pack("<i", seconds))
    return Outcome(STARTED, left)


def start_local_server(seconds: int = SECONDS_LEFT, attach=None) -> Outcome:
    """Find this PC's server and shorten its countdown.  Raises StartGameError
    (or weather.WeatherError, whose text is already plain words)."""
    if attach is None:
        from wolfrat.weather import attach_local_server as attach
    controller, memory = attach(writable=True)
    try:
        return shorten_countdown(memory, seconds)
    finally:
        memory.close()


def reply_for(outcome: Outcome, seconds: int = SECONDS_LEFT) -> Optional[str]:
    """Chat line for an outcome (kept under JO's 62-character limit)."""
    if outcome.kind == STARTED:
        return f"Game starting in {seconds} seconds!"
    if outcome.kind == ALMOST:
        return "The game is about to start."
    return "The game has already started."


__all__ = [
    "ALMOST", "COUNTDOWN_STORE", "Outcome", "PREROUND_TIMER", "RUNNING",
    "SECONDS_LEFT", "STARTED", "StartGameError", "reply_for",
    "shorten_countdown", "start_local_server",
]
