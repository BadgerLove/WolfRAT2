"""First blood from the server's own memory (Dale, 2026-11-22: "read the servers
memory, thats better").

The admin player list comes every few seconds and cannot say who killed first
when two kills land in one list.  On 22 Nov (AS-ColdWaterGapTAC) elk road killed
FMJ-BadgerLove at 18:36:54.150 and BadgerLove killed elk road 0.2 s later; both
showed up in one list with a kill and a death each, the old check skipped a kill
seen next to the killer's own death, and first blood went to the 4th kill.

When WolfRAT runs on the server's PC it reads, read-only, what the server itself
keeps for every player slot:

* WHO scored a kill: the slot's enemy-kill counter, raw[5] of the CPlayerStats
  record at slot+72.  The admin list's Kills column is this same number.  It only
  goes up for a kill of an enemy: a suicide bumps raw[6] and a team kill raw[4]
  instead, so neither can ever be first blood.  A satchel that kills its owner and
  an enemy is a suicide for the owner AND an enemy kill for him: it counts.  A
  co-op player killing an enemy bot bumps it too, as the list showed it before.
* WHEN: a player's death stamps the server frame clock (62.5 a second, read live
  2026-11-22) into the victim's slot, slot+0x178CC, before the kill is scored; a
  spawn or a medic's revive sets it back to 0.
* BY WHOM: the victim's last attacker, entity+0x178, which the death scores the
  kill to.

A kill counter that went up is matched to the victim whose last attacker is that
player, and the victims' death clocks put the kills of one read in the order they
really happened.  A kill with no player victim seen (a co-op bot, or a victim who
left at once) has no clock: it goes after the timed ones, in slot order.  A kill
counter that jumps because Keep Stats wrote a rejoining player's old score back
is never a kill (``restored`` / the ``skip`` test).

Sources (Kong = wolfcontrol/wolfrat-vault/Jointops.exe.kong.c):

* raw[5] at slot+72+4*5: CPlayerStats_RecordEvent @0x52C8E0 case 4 ``++this[5]``
  (case 3 -> [4] team kill, 5 -> [6] suicide, 6 -> [7] death), called from
  GameEvent_ProcessScoring @0x52F550 case 3 (Kong L288482-288484 suicide,
  L288492-288494 team kill, L288513 enemy kill).  Admin list: PLAYER LIST
  (CAdminServer_HandlePlayer @0x403EF0, Kong L38945-38952) prints
  CPlayerStats_GetFieldPlusOne(stats at slot+72, 4) in the Kills column, STATUS
  (CAdminServer_HandleStatus, L38614-38622) the same with its locals named the
  other way round.  Same offsets as keep_stats.KILLS and the spectator recorder
  (live-proven 2026-11-05).
* slot+0x178CC death clock: Server_SendRandomSeedToPlayer @0x5101A0, disarm arm
  ``slot[96460] = g_ServerFrameClock`` (Kong L260610), called from
  GameEvent_PlayerDeath @0x516DD0 (Kong L266623) before both
  GameEvent_ProcessScoring calls (L266624, L266638); arm arm (spawn, revive)
  zeroes it (L260595).  OpenNova docs net/novaworld-net-re.md D-NET-885.  The
  round end stamps every player the same way without a death
  (Server_ProcessRoundEnd, Kong L266116-266120): no kill counter moves, so no kill.
  Read live 2026-11-22 (tmp/firstblood-memory/live_check.py): 4 kills in 75 s, each
  victim's new death clock + last attacker matched the slot whose raw[5] went up
  in the same read; a suicide moved no kill counter; spawns put it back to 0.
* entity+0x178 killer: GameEvent_PlayerDeath passes ``*((_DWORD *)victim + 94)``
  to GameEvent_ProcessScoring as the scorer (Kong L266631-266638); spectator
  recorder resolve_kills (live-proven 2026-11-05 and on 22 Nov's trade).
* entity+354 team, teams only in game types with 0x10000: GameEvent_ProcessScoring
  (``entity[354]``, ``*(char *)(a4 + 354)``, both only when gameType & 0x10000).
* Round-over latch (map_weapons.ROUND_OVER_FLAG, g_SpawnSuccessGate): Game_StartMission
  @0x524360 zeroes every slot's stats (Server_ResetAllPlayerSlotFrameState, Kong
  L277675) before it lowers the latch (L277729).  So after a map change, a read with
  the latch down shows the new map's counters; with it up, the old map's.
* Slot table, slot+0 entity, slot+4 active, slot+40 name: jo_players (live-proven).
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional

from wolfrat.jo_players import (
    CAPACITY_VA,
    ENTITY_OFFSET,
    GAME_TYPE_VA,
    MAX_CAPACITY,
    NAME_LEN,
    NAME_OFFSET,
    SLOT_STRIDE,
    SLOTPTR_VA,
    LocalServerPlayers,
)
from wolfrat.map_weapons import ROUND_OVER_FLAG

ACTIVE_OFFSET = 4                       # slot+4 active byte
STATS_OFFSET = 72                       # CPlayerStats record (keep_stats: COUNTERS_OFFSET = 72 + 4)
KILLS_OFFSET = STATS_OFFSET + 4 * 5     # raw[5] enemy kills = the admin list's Kills
HEAD_SIZE = KILLS_OFFSET + 4            # entity pointer .. kills, one read per slot
DEATH_CLOCK_OFFSET = 0x178CC            # frame clock at the last death, 0 while alive
KILLER_OFFSET = 0x178                   # entity: last attacker = who the death is scored to
TEAM_OFFSET = 354                       # entity: team byte
TEAMS = 0x10000                         # game types with teams
DEATH_KEEP_SECONDS = 2.0                # a death waits this long for its killer's counter


@dataclass(frozen=True)
class Kill:
    """One kill seen in the server's memory."""
    name: str
    slot: int
    clock: Optional[int] = None         # server frame of the victim's death, None = not known
    victim: str = ""


@dataclass(frozen=True)
class _Seen:
    name: str
    entity: int
    kills: int
    died_at: int


@dataclass(frozen=True)
class _Death:
    clock: int
    killer: int                         # the killer's slot
    victim: str
    seen: float


def _u32(mem, address: int) -> int:
    return struct.unpack("<I", mem.read(address, 4))[0]


class FirstBloodMemory:
    """``poll()`` about twice a second while first blood is due on the map:
    the kills since the last poll in the order they happened ([] = none yet), or
    None when the server's memory can't be read (WolfRAT on another PC, or a read
    failed) - then the player list decides first blood."""

    def __init__(self, reader=None, clock: Callable[[], float] = time.monotonic,
                 timer: Callable[[], float] = time.perf_counter):
        self._reader = reader if reader is not None else LocalServerPlayers()
        self._clock = clock
        self._timer = timer
        self._seen: dict = {}            # slot -> _Seen at the last poll
        self._deaths: dict = {}          # victim slot -> _Death not yet matched to a kill
        self._counting = False           # a first look has been taken
        self._new_map = False            # counting starts from 0 once the map is running
        self._pid = None
        self.last_cost = 0.0             # seconds the last poll's reads took
        self.status = "Not started."

    def start(self, new_map: bool = False) -> None:
        """Start counting again.  ``new_map``: a real map change - every counter
        started at 0 when it loaded, so kills already there are this map's (a
        poll during the old map's round-over wait counts nothing)."""
        self._seen, self._deaths = {}, {}
        self._counting = False
        self._new_map = new_map

    def stop(self) -> None:
        """First blood is said (or off): forget everything and close the handle."""
        self.start()
        try:
            self._reader.release("First blood is not due.")
        except Exception:
            pass
        self._pid = None

    def restored(self, name: str, kills: int) -> None:
        """Keep Stats just wrote ``name``'s old score back: count from ``kills``."""
        name = name.strip()
        for slot, seen in list(self._seen.items()):
            if seen.name.strip() == name:
                self._seen[slot] = _Seen(seen.name, seen.entity, kills, seen.died_at)

    def poll(self, skip: Callable[[str], bool] = lambda _name: False) -> Optional[list]:
        """``skip(name)`` True: that player's score just came back (Keep Stats),
        a jump in it is not a kill."""
        mem = self._reader.handle()
        if mem is None:
            self.status = self._reader.status
            return None
        pid = getattr(self._reader, "pid", None)
        if pid != self._pid:                     # another server process: start over
            if self._pid is not None:
                self.start()
            self._pid = pid
        new_map = self._new_map
        started = self._timer()
        try:
            kills = self._step(mem, skip)
        except Exception as exc:                 # closed, mid-load, or not Joint Ops: the list decides
            self.status = f"Could not read the server's memory ({exc})."
            self._reader.release(self.status)
            self._pid = None
            self.start(new_map)
            return None
        finally:
            self.last_cost = self._timer() - started
        self.status = f"Reading first blood from the server's memory (process {pid})."
        return kills

    # ---- one poll ------------------------------------------------------------------
    def _step(self, mem, skip) -> list:
        if _u32(mem, ROUND_OVER_FLAG):
            # The round is over: no first blood until the next map is running, and
            # that map starts every counter at 0 (the latch only comes down in
            # Game_StartMission, after the stats reset).
            self._seen, self._deaths = {}, {}
            self._counting, self._new_map = False, True
            return []
        rows = self._read_slots(mem)
        now = self._clock()
        if not self._counting:
            self._counting = True
            if self._new_map:
                # The map loaded with every counter at 0.  Deaths already there are
                # not used: a player who has not spawned yet still carries the old
                # map's round-end stamp and last attacker.
                self._seen = {slot: _Seen(r.name, r.entity, 0, r.died_at) for slot, r in rows.items()}
            else:
                self._seen = dict(rows)
            self._new_map = False
        before, self._seen = self._seen, rows
        self._note_deaths(mem, rows, before, now)
        found = []
        for slot, row in rows.items():
            last = before.get(slot)
            if last is None or last.name != row.name:
                continue                         # someone new in this slot: count from what they have
            gained = row.kills - last.kills
            if gained <= 0:
                continue                         # same, or lower (team change, Zero Score): count from here
            if skip(row.name.strip()):
                continue                         # Keep Stats gave them their score back
            mine = sorted(((victim, death) for victim, death in self._deaths.items()
                           if death.killer == slot), key=lambda item: item[1].clock)[:gained]
            for victim, _death in mine:
                del self._deaths[victim]
            first = mine[0][1] if mine else None
            found.append(Kill(row.name.strip(), slot, first.clock if first else None,
                              first.victim.strip() if first else ""))
        self._deaths = {v: d for v, d in self._deaths.items()
                        if now - d.seen < DEATH_KEEP_SECONDS and v in rows}
        # In the order they happened.  Kills with no clock (a bot, a victim who left)
        # go after: the server keeps no time for them, so their order is slot order.
        found.sort(key=lambda k: (k.clock is None, k.clock or 0, k.slot))
        return found

    @staticmethod
    def _read_slots(mem) -> dict:
        capacity = _u32(mem, CAPACITY_VA)
        base = _u32(mem, SLOTPTR_VA)
        if not base or not 0 < capacity <= MAX_CAPACITY:
            raise ValueError("no slot table")
        rows = {}
        for index in range(capacity):
            address = base + index * SLOT_STRIDE
            head = mem.read(address, HEAD_SIZE)
            if not head[ACTIVE_OFFSET]:
                continue
            name = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0].decode("latin-1", "replace")
            if not name:
                continue
            rows[index] = _Seen(name,
                                struct.unpack_from("<I", head, ENTITY_OFFSET)[0],
                                struct.unpack_from("<i", head, KILLS_OFFSET)[0],
                                _u32(mem, address + DEATH_CLOCK_OFFSET))
        return rows

    def _note_deaths(self, mem, rows: dict, before: dict, now: float) -> None:
        """Every player who died since the last poll and was killed by an enemy
        player: (death clock, killer's slot), kept until that killer's counter
        shows the kill (the two can fall either side of a read)."""
        by_entity = {r.entity: slot for slot, r in rows.items() if r.entity}
        game_type = None
        for slot, row in rows.items():
            last = before.get(slot)
            if (not row.died_at or not row.entity or last is None or last.name != row.name
                    or last.died_at == row.died_at):
                continue
            attacker = _u32(mem, row.entity + KILLER_OFFSET)
            killer = by_entity.get(attacker)
            if killer is None or killer == slot:
                continue                         # no player killed them, or a suicide
            if game_type is None:
                game_type = _u32(mem, GAME_TYPE_VA)
            if game_type & TEAMS and (mem.read(attacker + TEAM_OFFSET, 1)
                                      == mem.read(row.entity + TEAM_OFFSET, 1)):
                continue                         # a team kill
            self._deaths[slot] = _Death(row.died_at, killer, row.name, now)


__all__ = ["FirstBloodMemory", "Kill"]
