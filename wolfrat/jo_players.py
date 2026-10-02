"""Each player's IP and position, read from the Joint Ops server process on this PC.

The admin port never sends an IP or a position, so this is the only way
WolfRAT can see them.  The read needs no elevation when WolfRAT runs as the
same Windows user as the server, and this module never opens the process for
writing.  Slot 0 is the host itself (no network player).

Also read here, for the vote and announcer rules: AAS zone owners, the game
type (values in coop_guard.GAME_TYPES), co-op objectives and AI left, and
CTF / Flagball captures against the win target.

Addresses and how each was found: WolfRAT vault, Systems/Memory Map.md.
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional

from wolfrat import server_process, weather

CAPACITY_VA = 0x24C0CA4
SLOTPTR_VA = 0x24C0CA8
SLOT_STRIDE = 0x188E8
NAME_OFFSET, NAME_LEN = 40, 32
NETPLAYER_OFFSET = 28
IP_OFFSET = 196
ENTITY_OFFSET = 0
POSITION_OFFSET = 4          # three int32: X, Y, Z(height)
MAX_CAPACITY = 256
RETRY_SECONDS = 10
CHECK_SECONDS = 2            # how often a held process is re-matched to the connection
ZONE_CHAIN_VA = 0x24D1EBC
ZONE_TEAM_OFFSET, ZONE_TIER_OFFSET = 354, 538
MAX_ZONES = 64
GAME_TYPE_VA = 0x24D2128
OBJECTIVE_IDS_VA = 0xA7628B     # ids at [1..8]
OBJECTIVES_DONE_VA = 0xAC86F4
MAX_OBJECTIVES = 8
TEAM_SCORE_VA = {1: 0xC87CA8, 2: 0xC87DFC}
CAPS_OFFSET = 4 * (11 + 1)
CTF_GOAL_VA = {1: 0xC8FF00, 2: 0xC8FEFC}
MAX_SCORE_VA = 0x24D2138
GAME_CTF, GAME_FB = 0x10004, 0x10008
NO_LIMIT = 65000
AI_GROUPS_VA = 0xA33FA4
AI_GROUP_STRIDE, AI_GROUP_COUNT = 48, 64
AI_INITIAL_OFFSET, AI_LIVE_OFFSET = 4, 8


@dataclass(frozen=True)
class SlotInfo:
    slot: int
    name: str
    ip: str
    pos: Optional[tuple] = None      # (x, y, z) engine units, None when unreadable


@dataclass(frozen=True)
class ZoneInfo:
    tier: int
    team: int
    pos: Optional[tuple] = None


class LocalServerPlayers:
    """``read()`` -> list of SlotInfo, or [] with ``status`` saying why."""

    def __init__(self, clock=time.time, find=None, open_memory=None):
        self._clock = clock
        self._find = find or weather.find_process_ids
        self._open = open_memory or (lambda pid: weather.ProcessMemory(pid, writable=False))
        self._memory = None
        self._pid: Optional[int] = None
        self._next_try = 0.0
        self._next_check = 0.0
        self.status = "Not looked yet."
        self.available = False

    def _attach(self) -> bool:
        now = self._clock()
        if self._memory is not None:
            if now < self._next_check:
                return True
            self._next_check = now + CHECK_SECONDS
            try:
                current = self._find()
            except Exception:                    # pragma: no cover - OS oddities
                return True
            # No match right now (map change, reconnecting): keep the process
            # we already had.  A DIFFERENT process means WolfRAT is now talking
            # to another server - never keep reading the old one.
            if not current or current[0] == self._pid:
                return True
            self._drop("Connected to a different server - switching.")
            self._next_try = 0.0
        if now < self._next_try:
            return False
        self._next_try = now + RETRY_SECONDS
        if sys.platform != "win32":
            self.status = "IP reading only works on Windows."
            return False
        try:
            pids = self._find()
        except Exception as exc:                 # pragma: no cover - OS oddities
            self.status = f"Could not list processes: {exc}"
            return False
        if not pids:
            self.status = server_process.why_not()
            return False
        try:
            self._memory = self._open(pids[0])
            self._pid = pids[0]
        except Exception as exc:
            self.status = str(exc)
            self._memory = None
            return False
        return True

    def _drop(self, why: str) -> None:
        try:
            if self._memory is not None:
                self._memory.close()
        except Exception:
            pass
        self._memory, self._pid = None, None
        self.status = why
        self.available = False

    def exe_path(self) -> str:
        if self._memory is None:
            return ""
        try:
            return self._memory.exe_path()
        except Exception:
            return ""

    def read(self) -> list:
        if not self._attach():
            self.available = False
            return []
        mem = self._memory
        try:
            capacity = struct.unpack("<I", mem.read(CAPACITY_VA, 4))[0]
            base = struct.unpack("<I", mem.read(SLOTPTR_VA, 4))[0]
        except Exception as exc:
            self._drop(f"Lost the server process ({exc}).")
            return []
        if not base or capacity == 0 or capacity > MAX_CAPACITY:
            self._drop("The server process does not look like Joint Ops (slot table not found).")
            return []
        found = []
        for index in range(capacity):
            slot = base + index * SLOT_STRIDE
            try:
                head = mem.read(slot, NAME_OFFSET + NAME_LEN)
            except Exception:
                continue
            if not head[4]:
                continue
            name = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0].decode("latin-1", "replace")
            netplayer = struct.unpack_from("<I", head, NETPLAYER_OFFSET)[0]
            ip = ""
            if netplayer:
                try:
                    raw = mem.read(netplayer + IP_OFFSET, 4)
                    if any(raw):
                        ip = socket.inet_ntoa(raw)
                except Exception:
                    ip = ""
            entity = struct.unpack_from("<I", head, ENTITY_OFFSET)[0]
            pos = None
            if entity:
                try:
                    pos = struct.unpack("<iii", mem.read(entity + POSITION_OFFSET, 12))
                except Exception:
                    pos = None
            found.append(SlotInfo(index, name, ip, pos))
        self.available = True
        self.status = f"Reading IPs from the connected server (process {self._pid})."
        return found


    def read_zones(self) -> list:
        """Every AAS zone on the current map, or [] (non-AAS map or no process)."""
        if not self._attach():
            return []
        mem = self._memory
        try:
            begin = struct.unpack("<I", mem.read(ZONE_CHAIN_VA + 44, 4))[0]
            end = struct.unpack("<I", mem.read(ZONE_CHAIN_VA + 48, 4))[0]
        except Exception:
            return []
        if not begin or end < begin or (end - begin) > 4 * MAX_ZONES:
            return []
        zones = []
        for address in range(begin, end, 4):
            try:
                entry = struct.unpack("<I", mem.read(address, 4))[0]
                entity = struct.unpack("<I", mem.read(entry, 4))[0] if entry else 0
                if not entity:
                    continue
                team = mem.read(entity + ZONE_TEAM_OFFSET, 1)[0]
                tier = mem.read(entity + ZONE_TIER_OFFSET, 1)[0]
                pos = struct.unpack("<iii", mem.read(entity + POSITION_OFFSET, 12))
            except Exception:
                continue
            zones.append(ZoneInfo(tier, team, pos))
        return zones


    def read_game_type(self) -> Optional[int]:
        """The running game type (coop_guard.GAME_TYPES), or None with no process."""
        if not self._attach():
            return None
        try:
            return struct.unpack("<I", self._memory.read(GAME_TYPE_VA, 4))[0]
        except Exception:
            return None


    def read_team_caps(self, game_type) -> Optional[dict]:
        """{1: Joint Ops captures, 2: Rebels captures} on CTF / Flagball, else None."""
        if game_type not in (GAME_CTF, GAME_FB) or not self._attach():
            return None
        try:
            return {t: struct.unpack("<i", self._memory.read(va + CAPS_OFFSET, 4))[0]
                    for t, va in TEAM_SCORE_VA.items()}
        except Exception:
            return None

    def read_caps_to_go(self, game_type) -> Optional[int]:
        """caps_to_go() from the live server, None off CTF/Flagball."""
        if game_type not in (GAME_CTF, GAME_FB) or not self._attach():
            return None
        dword = lambda va: struct.unpack("<i", self._memory.read(va, 4))[0]
        try:
            caps = {t: dword(va + CAPS_OFFSET) for t, va in TEAM_SCORE_VA.items()}
            goal = {t: dword(va) for t, va in CTF_GOAL_VA.items()}
            max_score = dword(MAX_SCORE_VA)
        except Exception:
            return None
        return caps_to_go(game_type, caps, goal, max_score)

    def read_ai_left(self) -> Optional[tuple]:
        """ai_left() from the live server."""
        if not self._attach():
            return None
        try:
            table = self._memory.read(AI_GROUPS_VA, AI_GROUP_STRIDE * AI_GROUP_COUNT)
        except Exception:
            return None
        return ai_left(table)

    def read_objectives(self) -> Optional[tuple]:
        """(done, total) for the co-op mission's objectives, or None."""
        if not self._attach():
            return None
        try:
            ids = self._memory.read(OBJECTIVE_IDS_VA, MAX_OBJECTIVES + 1)
            done_mask = struct.unpack("<I", self._memory.read(OBJECTIVES_DONE_VA, 4))[0]
        except Exception:
            return None
        return count_objectives(ids, done_mask)


def caps_to_go(game_type, caps: dict, ctf_goal: dict, max_score: int):
    """Fewest flags (CTF) or goals (Flagball) any team still needs, or None."""
    if game_type == GAME_CTF:
        left = [ctf_goal[t] - caps[t] for t in (1, 2) if ctf_goal.get(t, 0) > 0]
        return max(0, min(left)) if left else None
    if game_type == GAME_FB:
        if not 0 < max_score < NO_LIMIT:
            return None
        return max(0, max_score - max(caps.values()))
    return None


def ai_left(table: bytes):
    """(alive, at start) summed over the mission's groups, or None without AI."""
    alive = start = 0
    for group in range(1, AI_GROUP_COUNT):
        base = group * AI_GROUP_STRIDE
        initial = struct.unpack_from("<i", table, base + AI_INITIAL_OFFSET)[0]
        live = struct.unpack_from("<i", table, base + AI_LIVE_OFFSET)[0]
        if initial > 0:
            start += initial
            alive += min(max(live, 0), initial)
    return (alive, start) if start else None


def count_objectives(ids: bytes, done_mask: int) -> tuple:
    """Walk slots 1..8 the way the in-game objectives list does."""
    total = done = 0
    for n in range(1, MAX_OBJECTIVES + 1):
        if ids[n] in (0, 255):
            break
        total += 1
        if done_mask & (1 << n):
            done += 1
    return done, total


def ips_by_name(slots) -> dict:
    """name -> ip for every slot that has both (the host has neither)."""
    return {s.name: s.ip for s in slots if s.name and s.ip}


def positions_by_name(slots) -> dict:
    """name -> (x, y, z) for every slot with a readable entity."""
    return {s.name: s.pos for s in slots if s.name and s.pos}
