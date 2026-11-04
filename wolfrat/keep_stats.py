"""Save a player's round stats when they leave, and give them back when they
rejoin (OscarMike's request, Dale 2026-11-04).

Joint Ops wipes a player's slot the moment they go and again when they join
(Server_HandlePlayerDisconnect @0x51B5C0, server_PlayerAdd), so there is nothing
to read afterwards.  WolfRAT reads every player's counters once a second, keeps
the last copy when a player goes, and adds it to their new slot when that name is
back in the match.  Saved stats end with the round: the server's round-over latch
goes up at every round end and down at the next map's start.

What is written and why (kong.c):
- The counters are dwords 1-42 of the stats record at slot+72
  (CPlayerStats_AccumulateFields @0x52C390).  Dword 0 is the entity pointer and
  dwords 46-64 are buffers: never touched.  Dword 35 is not a count (set to 1 at
  join by CPlayerStats_InitWeaponTracking, assigned by event 34): left alone.
- King of the Hill's score lives outside the record, at slot+94380 (the third
  argument of ScoreRules_GetPrimaryScoreField @0x52C850).
- Team scores have their own records, fed when the kill happens (GameEvent_ProcessScoring,
  kong.c 288427 + 288431), so a player's stats coming back does not count twice.
- Server_CheckPlayerViolations @0x51ABD0 punts over the friendly-kill limit or at
  more than 9 suicides, so stats over either are from a punt: not saved, or the
  player would be punted again straight away.
- Server_ChangeEntityTeam @0x518D70 zeroes the record.  A rejoining player can pick
  a team just after the stats go back, so for a while after a restore a wipe that
  comes with a team change is undone.  A wipe with no team change is an admin's
  Zero Score and stays.

Addresses and how each was found: WolfRAT vault, Systems/Memory Map.md.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from wolfrat.jo_players import CAPACITY_VA, MAX_CAPACITY, NAME_LEN, NAME_OFFSET, NETPLAYER_OFFSET, SLOT_STRIDE, SLOTPTR_VA
from wolfrat.map_weapons import ROUND_OVER_FLAG

FRIENDLY_KILL_LIMIT_VA = 0x24D2244      # numallowablefriendlykills, negative = no limit
SUICIDE_LIMIT = 9

ACTIVE_OFFSET, LOCAL_OFFSET = 4, 5
STATE_OFFSET = 32
IN_MATCH = (6, 7)                       # the scoreboard's own test (slot state 6/7)
COUNTERS_OFFSET = 72 + 4                # stats record dword 1
COUNTER_COUNT = 42
TEAM_OFFSET = 416
HEAD_SIZE = TEAM_OFFSET + 1
HILL_OFFSET = 94380
JOINED_OFFSET = 94384                   # GetTickCount at server_PlayerAdd: a new value = a new player

# Raw field number -> place in the counters tuple is number - 1.
FRIENDLY_KILLS, KILLS, SUICIDES, DEATHS, POINTS, NOT_A_COUNT = 4, 5, 6, 7, 29, 35

TEAM_PICK_SECONDS = 300.0


@dataclass(frozen=True)
class Slot:
    index: int
    address: int
    name: str
    state: int
    team: int
    joined: int
    counters: tuple
    hill: int

    @property
    def key(self) -> tuple:
        return self.index, self.joined

    @property
    def kills(self) -> int:
        return self.counters[KILLS - 1]

    @property
    def deaths(self) -> int:
        return self.counters[DEATHS - 1]


@dataclass(frozen=True)
class Saved:
    counters: tuple
    hill: int

    @property
    def kills(self) -> int:
        return self.counters[KILLS - 1]

    @property
    def deaths(self) -> int:
        return self.counters[DEATHS - 1]


@dataclass
class _Guard:
    saved: Saved
    team: int
    until: float


def _has_stats(counters: tuple, hill: int) -> bool:
    return bool(hill) or any(v for i, v in enumerate(counters) if i != NOT_A_COUNT - 1)


def _tally(counters: tuple) -> int:
    """Everything that only ever goes up (points can go down, dword 35 isn't a count)."""
    return sum(v for i, v in enumerate(counters) if i not in (NOT_A_COUNT - 1, POINTS - 1))


class StatsKeeper:
    """Call ``tick(memory)`` about once a second with a writable handle to the server, or None."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = lambda _text: None,
                 restored: Callable[[str, int, int], None] = lambda _n, _k, _d: None):
        self._clock = clock
        self._log = log
        self._restored = restored
        self.enabled = True
        self.linked = False
        self._handle = None
        self._tracked: dict = {}
        self._saved: dict = {}
        self._guards: dict = {}

    # ---- what the panel shows --------------------------------------------------------
    @property
    def tracking(self) -> int:
        return len(self._tracked)

    @property
    def saved(self) -> dict:
        return dict(self._saved)

    def forget(self) -> None:
        self._tracked, self._saved, self._guards = {}, {}, {}

    # ---- the once-a-second pass ------------------------------------------------------
    def tick(self, mem) -> None:
        if not self.enabled:
            self.forget()
            return
        self.linked = mem is not None
        if mem is not self._handle:              # a different server, or the link went
            self.forget()
            self._handle = mem
        if mem is None:
            return
        try:
            if self._dword(mem, ROUND_OVER_FLAG):
                self.forget()
                return
            limit = self._dword(mem, FRIENDLY_KILL_LIMIT_VA)
            live = self._read_slots(mem)
        except Exception:                        # mid map load or the server closed: decide nothing on this
            return
        self._step(mem, live, limit)

    @staticmethod
    def _dword(mem, address: int) -> int:
        return struct.unpack("<i", mem.read(address, 4))[0]

    def _read_slots(self, mem) -> list:
        capacity = self._dword(mem, CAPACITY_VA)
        base = self._dword(mem, SLOTPTR_VA)
        if not base or not 0 < capacity <= MAX_CAPACITY:
            raise ValueError("no slot table")
        found = []
        for index in range(capacity):
            address = base + index * SLOT_STRIDE
            head = mem.read(address, HEAD_SIZE)
            if not head[ACTIVE_OFFSET] or head[LOCAL_OFFSET]:
                continue
            if not struct.unpack_from("<I", head, NETPLAYER_OFFSET)[0]:   # the host and bots have no network player
                continue
            name = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0].decode("latin-1", "replace")
            if not name:
                continue
            hill, joined = struct.unpack("<iI", mem.read(address + HILL_OFFSET, 8))
            found.append(Slot(index, address, name, struct.unpack_from("<i", head, STATE_OFFSET)[0],
                              head[TEAM_OFFSET], joined,
                              struct.unpack_from(f"<{COUNTER_COUNT}i", head, COUNTERS_OFFSET), hill))
        return found

    def _step(self, mem, live: list, limit: int) -> None:
        now = self._clock()
        by_key = {slot.key: slot for slot in live}
        if self._everyone_wiped(by_key):
            self._saved.clear()
        for key in list(self._tracked):
            if key not in by_key:
                self._save(self._tracked.pop(key), limit)
                self._guards.pop(key, None)
        for slot in live:
            try:
                slot = self._check_guard(mem, slot, now)
                saved = self._saved.get(slot.name)
                if saved is not None and slot.state in IN_MATCH:
                    slot = self._put_back(mem, slot, saved)
                    del self._saved[slot.name]
                    self._guards[slot.key] = _Guard(saved, slot.team, now + TEAM_PICK_SECONDS)
                    self._log(f"{slot.name} rejoined: stats back ({slot.kills} kills, {slot.deaths} deaths)")
                    self._restored(slot.name, slot.kills, slot.deaths)
            except Exception as exc:             # try this player again next second
                self._log(f"could not write {slot.name}'s stats: {exc}")
            self._tracked[slot.key] = slot

    def _everyone_wiped(self, by_key: dict) -> bool:
        """A backstop for a new round the latch didn't show: every score still here just went to zero."""
        wiped = False
        for key, last in self._tracked.items():
            slot = by_key.get(key)
            if slot is None or key in self._guards or not _has_stats(last.counters, last.hill):
                continue
            if _has_stats(slot.counters, slot.hill):
                return False
            wiped = True
        return wiped

    def _save(self, last: Slot, limit: int) -> None:
        if not _has_stats(last.counters, last.hill):
            return
        friendly = last.counters[FRIENDLY_KILLS - 1]
        if (limit >= 0 and friendly > limit) or last.counters[SUICIDES - 1] > SUICIDE_LIMIT:
            self._log(f"{last.name} left over the friendly-kill or suicide limit: stats not saved")
            return
        self._saved[last.name] = Saved(last.counters, last.hill)
        self._log(f"{last.name} left: saved {last.kills} kills, {last.deaths} deaths")

    def _check_guard(self, mem, slot: Slot, now: float) -> Slot:
        guard = self._guards.get(slot.key)
        if guard is None:
            return slot
        if now >= guard.until:
            del self._guards[slot.key]
            return slot
        if _tally(slot.counters) >= _tally(guard.saved.counters):
            guard.team = slot.team
            return slot
        if slot.team == guard.team:              # no team change: an admin's Zero Score
            del self._guards[slot.key]
            return slot
        guard.team = slot.team
        slot = self._put_back(mem, slot, guard.saved)
        self._log(f"{slot.name} changed team after rejoining: stats back again ({slot.kills} kills, {slot.deaths} deaths)")
        self._restored(slot.name, slot.kills, slot.deaths)
        return slot

    @staticmethod
    def _put_back(mem, slot: Slot, saved: Saved) -> Slot:
        counters = tuple(now if i == NOT_A_COUNT - 1 else now + kept
                         for i, (now, kept) in enumerate(zip(slot.counters, saved.counters)))
        hill = slot.hill + saved.hill
        mem.write(slot.address + COUNTERS_OFFSET, struct.pack(f"<{COUNTER_COUNT}i", *counters))
        mem.write(slot.address + HILL_OFFSET, struct.pack("<i", hill))
        return replace(slot, counters=counters, hill=hill)


def status_text(keeper: StatsKeeper) -> str:
    if not keeper.enabled:
        return "Off - a player who leaves and rejoins starts from zero."
    if not keeper.linked:
        return "Waiting - needs the server running on this PC."
    saved = keeper.saved
    count = keeper.tracking
    text = f"On - tracking {count} player{'' if count == 1 else 's'}."
    if saved:
        names = ", ".join(f"{name} ({s.kills}/{s.deaths})" for name, s in sorted(saved.items()))
        text += f" Waiting to rejoin: {names}."
    return text


__all__ = ["Saved", "Slot", "StatsKeeper", "status_text"]
