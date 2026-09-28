"""Wildlife: make the TAC mod's sharks hunt.

The TAC mod (OscarMike247's Revx02 pack) places sharks as team 0 with a 100 m
attack distance.  Team 0 means they have nobody to target, and 100 m makes
them lunge from far away and miss.  Three numbers in the running server fix
that: the Berserk flag (attack anyone; what a map's "Berserk" tick sets when
a creature spawns), an attack distance of about 10 m, and a "start chasing"
distance below the sight distance (maps that cannot set it leave the two
equal, and then the game never lets the shark close in).  All three revert
when a map loads and when a shark respawns, so this keeps re-applying them
for as long as the switch is on.

Berserk, not team 3: sharks moved to team 3 look fine to players already in
the game, but anyone who joins afterwards sees them swim backwards until one
locks on (proven live 2026-09-27).  Berserk leaves the team alone.  Chasing a
swimmer also needs the server exe fix (jo-server-patches 16) or onHook's
water-creature chase; without it sharks only act inside the attack distance.

Retail Berserk also makes sharks attack each other.  Before the first shark
goes berserk, WolfRAT adds "a berserk creature ignores its own kind" to the
running server's code (the same bytes as the exe patch, proven live
2026-09-27); onHook's BerserkIgnoresOwnKind does the same thing, and when
either is already there WolfRAT leaves the code alone.  On a server whose code
it does not recognise it does not make sharks berserk at all.  The code change
lasts until the server restarts; a map load keeps it.

Everything here works through the same ``Memory`` the Weather tab holds
(read/write of the live server on this PC).  Addresses are bare constants
by design; where they came from is in the private vault.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

SHARK_NAME = b"Shark\0"
LEGACY_TEAM = 3             # what WolfRAT 2.8.4-2.8.7 wrote; put back to 0 if found
BERSERK = 0x200             # AI flags bit: attack anyone, team untouched
START_CHASE_M = 4           # min engagement when a map left it equal to the sight
DEFAULT_ATTACK_M = 10       # tested: circles, charges, bites.  25 misses; 6 too close
MIN_ATTACK_M = 4
MAX_ATTACK_M = 25
MAP_DEFAULT_ATTACK_M = 100


class Addr:
    ENTITY_BASE = 0xA892E0          # entity table: base, stride, used (3 dwords)
    ENTITY_STRIDE = 0xA892E4
    ENTITY_USED = 0xA892E8


ENT_NAME_PTR = 0x20      # -> item definition name, NUL-terminated
ENT_AI_PTR = 0x68        # -> AI slot (null for non-AI entities)
ENT_HP = 0x11E           # int16
ENT_TEAM = 0x162         # byte
AI_FLAGS = 0x04          # dword; BERSERK bit
AI_ATTACK_DIST = 0x3C    # 16.16 metres
AI_MIN_ENGAGE = 0x40     # 16.16 metres: start chasing (must be below the sight)
AI_MAX_ENGAGE = 0x44     # 16.16 metres: sight

_MAX_ENTITIES = 8192     # sanity: a real table is a few hundred
_MIN_STRIDE, _MAX_STRIDE = 0x180, 0x10000

# "Berserk ignores its own kind": the target filter's berserk test, and a small
# addition in free space, so a berserk creature skips others with the same
# item definition and the normal team rule decides instead.
KIN_LEG = 0x53A7EA
KIN_LEG_RETAIL = bytes.fromhex(
    "8b466885c07409f7400400020000753085d274108b426885c07409f7400400020000751c")
KIN_LEG_FIXED = bytes.fromhex(
    "8b466885c07406f6400502751185d274138b426885c0740cf64005027406e919b81e0090")
KIN_CAVE = 0x726026
KIN_CAVE_FIXED = bytes.fromhex("85d274108b462085c074093b42200f84d447e1ffe9eb47e1ff")
KIN_CAVE_FREE = b"\xcc" * len(KIN_CAVE_FIXED)

KIN_RETAIL = "retail"        # stock code: WolfRAT can add the fix
KIN_FIXED = "fixed"          # WolfRAT's fix or the same exe patch
KIN_ONHOOK = "onhook"        # onHook's hook (jump out of the leg, rest untouched)
KIN_UNKNOWN = "unknown"      # something else: leave it, and no berserk


class WildlifeError(Exception):
    pass


@dataclass
class Shark:
    address: int
    ai_slot: int
    team: int
    attack_m: float
    hp: int
    ai_flags: int = 0
    min_m: float = 0.0
    max_m: float = 0.0

    @property
    def hunting(self) -> bool:
        return bool(self.ai_flags & BERSERK)

    @property
    def can_close_in(self) -> bool:
        """The game only lets an AI approach when start-chasing < sight."""
        return self.min_m < self.max_m


@dataclass
class Status:
    text: str
    hooked: bool = False          # the green-dot state
    sharks: list = field(default_factory=list)
    reapplied: int = 0
    note: str = ""                # one-off line for the tab's log


def _u32(mem, address: int) -> int:
    return struct.unpack("<I", mem.read(address, 4))[0]


def scan(mem) -> list[Shark]:
    """Every live shark in the server.  Raises WildlifeError if the entity
    table cannot be read or does not look like one."""
    try:
        base = _u32(mem, Addr.ENTITY_BASE)
        stride = _u32(mem, Addr.ENTITY_STRIDE)
        used = _u32(mem, Addr.ENTITY_USED)
    except Exception as exc:
        raise WildlifeError("Could not read the server's entity list.") from exc
    if not base or not (_MIN_STRIDE <= stride <= _MAX_STRIDE) or used > _MAX_ENTITIES:
        raise WildlifeError("The server's entity list does not look right (different exe?).")
    sharks = []
    for i in range(used):
        ent = base + i * stride
        try:
            name_ptr = _u32(mem, ent + ENT_NAME_PTR)
            if not name_ptr or mem.read(name_ptr, len(SHARK_NAME)) != SHARK_NAME:
                continue
            ai = _u32(mem, ent + ENT_AI_PTR)
            if not ai:
                continue
            team = mem.read(ent + ENT_TEAM, 1)[0]
            flags = _u32(mem, ai + AI_FLAGS)
            attack = struct.unpack("<i", mem.read(ai + AI_ATTACK_DIST, 4))[0] / 65536
            lo = struct.unpack("<i", mem.read(ai + AI_MIN_ENGAGE, 4))[0] / 65536
            hi = struct.unpack("<i", mem.read(ai + AI_MAX_ENGAGE, 4))[0] / 65536
            hp = struct.unpack("<h", mem.read(ent + ENT_HP, 2))[0]
        except Exception:
            continue                      # a half-built slot; next poll sees it
        sharks.append(Shark(ent, ai, team, attack, hp, flags, lo, hi))
    return sharks


def hunt(mem, sharks: list[Shark], attack_m: float) -> int:
    """Berserk + attack distance (+ a start-chasing distance where the map left
    none) on every shark that has drifted from them.  Returns how many sharks
    were changed.  Raises WildlifeError on a failed write."""
    changed = 0
    packed = struct.pack("<i", int(attack_m * 65536))
    for shark in sharks:
        fix_team = shark.team == LEGACY_TEAM
        fix_chase = not shark.can_close_in and shark.max_m > START_CHASE_M
        needs = (not shark.hunting or abs(shark.attack_m - attack_m) > 0.01
                 or fix_team or fix_chase)
        if not needs:
            continue
        try:
            if fix_team:
                mem.write(shark.address + ENT_TEAM, b"\x00")
            mem.write(shark.ai_slot + AI_FLAGS, struct.pack("<I", shark.ai_flags | BERSERK))
            mem.write(shark.ai_slot + AI_ATTACK_DIST, packed)
            if fix_chase:
                mem.write(shark.ai_slot + AI_MIN_ENGAGE, struct.pack("<i", START_CHASE_M << 16))
        except Exception as exc:
            raise WildlifeError("Could not write to the server (run WolfRAT as administrator?).") from exc
        if fix_team:
            shark.team = 0
        if fix_chase:
            shark.min_m = float(START_CHASE_M)
        shark.ai_flags |= BERSERK
        shark.attack_m = attack_m
        changed += 1
    return changed


def kin_state(mem) -> str:
    """Which "ignores its own kind" code the running server has."""
    try:
        leg = mem.read(KIN_LEG, len(KIN_LEG_RETAIL))
        cave = mem.read(KIN_CAVE, len(KIN_CAVE_FIXED))
    except Exception:
        return KIN_UNKNOWN
    if leg == KIN_LEG_FIXED and cave == KIN_CAVE_FIXED:
        return KIN_FIXED
    if leg == KIN_LEG_RETAIL and cave == KIN_CAVE_FREE:
        return KIN_RETAIL
    if leg[0] == 0xE9 and leg[5:] == KIN_LEG_RETAIL[5:]:
        return KIN_ONHOOK
    return KIN_UNKNOWN


def add_kin_fix(mem) -> None:
    """Add the fix to stock code: the addition first (nothing jumps there
    yet), then the test that jumps to it.  Raises WildlifeError."""
    try:
        mem.write_code([(KIN_CAVE, KIN_CAVE_FIXED), (KIN_LEG, KIN_LEG_FIXED)])
    except Exception as exc:
        raise WildlifeError("Could not add the 'sharks ignore each other' fix to the "
                            "server (run WolfRAT as administrator?).") from exc
    if kin_state(mem) != KIN_FIXED:
        raise WildlifeError("The 'sharks ignore each other' fix did not stick in the server.")


class SharkKeeper:
    """Polled from the Weather tab.  Keeps sharks hunting while ``enabled``."""

    def __init__(self):
        self.reapplied = 0        # writes since the switch went on
        self._map = None

    def reset(self):
        self.reapplied = 0

    def tick(self, mem, *, enabled: bool, attack_m: float, writable: bool,
             map_name: Optional[str], linked: bool) -> Status:
        where = f" on {map_name}" if map_name else " on this map"
        if not linked:
            return Status("Not linked to a game server on this PC - see the Server tab.")
        if map_name != self._map:
            self._map = map_name
        try:
            sharks = scan(mem)
        except WildlifeError as exc:
            return Status(str(exc))
        if not sharks:
            return Status("No sharks" + where + ". Sharks only exist on TAC mod maps that place them.")
        if not enabled:
            return Status(f"{len(sharks)} shark{'s' if len(sharks) != 1 else ''}{where} - "
                          "hunting is off (they only bite if you touch them).", sharks=sharks)
        if not writable:
            return Status("Sharks found but WolfRAT cannot write to the server "
                          "(run WolfRAT as administrator?).", sharks=sharks)
        # Berserk sharks fight each other unless the server has the fix.
        note = ""
        kin = kin_state(mem)
        if kin == KIN_UNKNOWN:
            return Status("Sharks NOT set to hunt: WolfRAT does not recognise this server's "
                          "code, so it cannot stop berserk sharks killing each other.",
                          sharks=sharks)
        if kin == KIN_RETAIL:
            try:
                add_kin_fix(mem)
            except WildlifeError as exc:
                return Status(f"Sharks NOT set to hunt: {exc}", sharks=sharks)
            note = ("Sharks: added 'berserk sharks ignore each other' to the running "
                    "server (until it restarts).")
        try:
            changed = hunt(mem, sharks, attack_m)
        except WildlifeError as exc:
            return Status(str(exc), sharks=sharks, note=note)
        self.reapplied += changed
        n = len(sharks)
        text = (f"🟢 {n} shark{'s' if n != 1 else ''} hooked{where} - hunting "
                f"(berserk, {attack_m:g} m), ignoring each other.")
        if self.reapplied:
            text += f" Re-applied {self.reapplied}x after map loads/respawns."
        return Status(text, hooked=True, sharks=sharks, reapplied=self.reapplied, note=note)
