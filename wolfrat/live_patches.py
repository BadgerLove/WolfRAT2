"""Live patches: small changes WolfRAT writes into the RUNNING game server.

Nothing on disk changes and players need nothing: the server decides these
rules and tells every player over the network.  A map change keeps them (same
server process); a server restart or crash loses them.  The Live patches page
on the Server tab drives this module.

Spacebar parachute: the stock game opens a carried parachute by itself after
about a second of free fall.  With this on, the player opens it by holding
space while falling (after about half a second, so an ordinary jump never
wastes it).  No safety net (Dale 2026-10-09: "people should just learn to
press spacebar").  Proven in game on our server, 2026-10-07.

Rules every live patch follows:
- read the stock bytes first; anything else (another exe, onHook) = leave it;
- write the new code first and read it back, the jump into it last, with the
  server paused for the moment it takes (``write_code``);
- off = the stock bytes back.

Addresses are bare constants by design; where they came from is in the
private vault.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, replace
from typing import Optional

# --------------------------------------------------------------- parachute
CHUTE_SITE = 0x4B7AE5
CHUTE_BACK = 0x4B7AFD
CHUTE_STOCK = bytes.fromhex("81bea000000000c8ffff7f0cf6462c107406")
CHUTE_TAG = b"FMJCHUTE"
CHUTE_HEAD = 32                 # tag, manual, safety net, 4 counters
CHUTE_OLD_HEAD = 16             # first proof build: tag, manual, safety net
CHUTE_CAVE_SIZE = 0x100
CHUTE_MANUAL = -6144            # space works after ~0.5 s of fall
CHUTE_STOCK_AUTO = -14336       # the game's own: by itself after ~1.1 s
# A server may still hold a safety-net cave from a WolfRAT test build (net field
# != 0 in the head): it reads as ON, and WolfRAT puts the plain one back.

STOCK = "stock"                 # the game's own automatic parachute
ON = "on"                       # WolfRAT's spacebar parachute
UNKNOWN = "unknown"             # code WolfRAT does not recognise: left alone


class LivePatchError(Exception):
    pass


@dataclass
class ChuteState:
    kind: str
    cave: int = 0
    manual: int = 0
    safety_net: int = 0             # 0 = none (non-zero only in old test caves)
    by_safety_net: Optional[int] = None
    by_space: Optional[int] = None
    last_fall: Optional[int] = None

    @property
    def on(self) -> bool:
        return self.kind == ON


def _rel8(source_end: int, target: int) -> bytes:
    delta = target - source_end
    if not -128 <= delta <= 127:
        raise ValueError("short jump out of range")
    return struct.pack("<b", delta)


def build_chute_cave(base: int, manual: int = CHUTE_MANUAL) -> tuple[bytes, int]:
    """The parachute code for a cave at ``base``.  Returns (bytes, entry).

    Layout: tag(8) manual(4) net(4, always 0) two counters(8) last fall
    speed(4) last keys(4), then the code.  The counters say how many chutes
    opened (the page shows them).  Same bytes as the tool proven in game
    2026-10-07 (Jointops_64Hz/parachute)."""
    if not -32768 < manual < 0:
        raise ValueError("manual must be between -32768 and 0")
    a_auto, a_space, a_vel, a_keys = base + 16, base + 20, base + 24, base + 28
    entry = base + CHUTE_HEAD
    # Two passes: the first finds where the labels land, the second fills them.
    labels: dict = {}
    for _ in range(2):
        code = bytearray()

        def here() -> int:
            return entry + len(code)

        def jcc(op: int, label: str):
            code.append(op)
            code.extend(_rel8(here() + 1, labels.get(label, here() + 1)))

        code += bytes.fromhex("f6462c10")                       # test byte [esi+2Ch],10h  carrying a chute
        jcc(0x74, "back")                                       # je back
        code += bytes.fromhex("f6862c01000020")                 # test byte [esi+12Ch],20h  space held
        jcc(0x74, "back")
        code += bytes.fromhex("a900200000")                     # test eax,2000h  airborne
        jcc(0x74, "back")
        code += bytes.fromhex("81bea0000000") + struct.pack("<i", manual)          # cmp vel_z,manual
        jcc(0x7F, "back")                                       # jg back
        code += bytes.fromhex("ff05") + struct.pack("<I", a_space)      # inc [opened by space]
        jcc(0xEB, "open")                                       # jmp open
        code += bytes.fromhex("ff05") + struct.pack("<I", a_auto)       # never reached; kept so the bytes
                                                                # match the build proven in game
        labels["open"] = len(code) + entry
        code += bytes.fromhex("51")                             # push ecx
        code += bytes.fromhex("8b8ea0000000")                   # mov ecx,[esi+A0h]
        code += bytes.fromhex("890d") + struct.pack("<I", a_vel)        # mov [last fall],ecx
        code += bytes.fromhex("8b8e2c010000")                   # mov ecx,[esi+12Ch]
        code += bytes.fromhex("890d") + struct.pack("<I", a_keys)       # mov [last keys],ecx
        code += bytes.fromhex("59")                             # pop ecx
        code += bytes.fromhex("83c820")                         # or eax,20h   chute open
        code += bytes.fromhex("894624")                         # mov [esi+24h],eax
        labels["back"] = here()
        code += bytes.fromhex("e9") + struct.pack("<i", CHUTE_BACK - (here() + 5))
    head = CHUTE_TAG + struct.pack("<iiiiii", manual, 0, 0, 0, 0, 0)
    return head + bytes(code), entry


def _jump_to(site: int, target: int) -> bytes:
    return b"\xe9" + struct.pack("<i", target - (site + 5))


def chute_state(mem) -> ChuteState:
    """What the running server has at the parachute rule."""
    try:
        current = mem.read(CHUTE_SITE, len(CHUTE_STOCK))
    except Exception:
        return ChuteState(UNKNOWN)
    if current == CHUTE_STOCK:
        return ChuteState(STOCK)
    if current[0] != 0xE9 or current[5:] != CHUTE_STOCK[5:]:
        return ChuteState(UNKNOWN)
    entry = (CHUTE_SITE + 5 + struct.unpack("<i", current[1:5])[0]) & 0xFFFFFFFF
    try:
        head = mem.read(entry - CHUTE_HEAD, CHUTE_HEAD)
        if head[:8] == CHUTE_TAG:
            manual, net, n_auto, n_space, vel, _keys = struct.unpack("<iiiiii", head[8:])
            return ChuteState(ON, entry - CHUTE_HEAD, manual, net, n_auto, n_space, vel)
        head = mem.read(entry - CHUTE_OLD_HEAD, CHUTE_OLD_HEAD)
        if head[:8] == CHUTE_TAG:
            manual, net = struct.unpack("<ii", head[8:])
            return ChuteState(ON, entry - CHUTE_OLD_HEAD, manual, net)
    except Exception:
        pass
    return ChuteState(UNKNOWN)       # someone else's jump (onHook?): not ours


def chute_on(mem, manual: int = CHUTE_MANUAL) -> ChuteState:
    """Spacebar parachute on (or re-set).  A fresh cave every time: the old one
    may still have a server thread inside it, so it is never rewritten.
    Raises LivePatchError."""
    before = chute_state(mem)
    if before.kind == UNKNOWN:
        raise LivePatchError("WolfRAT does not recognise this server's parachute code "
                             "(different exe, or onHook changed it) - left alone.")
    try:
        cave = mem.alloc_code(CHUTE_CAVE_SIZE)
    except Exception as exc:
        raise LivePatchError("Could not make room in the server for the parachute "
                             "(run WolfRAT as administrator?).") from exc
    if not cave or cave + CHUTE_CAVE_SIZE > 0xFFFFFFFF:
        raise LivePatchError("The server gave WolfRAT unusable memory for the parachute.")
    blob, entry = build_chute_cave(cave, manual)
    try:
        mem.write(cave, blob)                       # nothing jumps here yet
        if mem.read(cave, len(blob)) != blob:
            raise LivePatchError("The parachute code did not read back right - not switched on.")
        mem.write_code([(CHUTE_SITE, _jump_to(CHUTE_SITE, entry))])
    except LivePatchError:
        raise
    except Exception as exc:
        raise LivePatchError("Could not write the parachute into the server "
                             "(run WolfRAT as administrator?).") from exc
    after = chute_state(mem)
    if not after.on or after.cave != cave:
        raise LivePatchError("The spacebar parachute did not stick in the server.")
    return after


def chute_off(mem) -> ChuteState:
    """The game's own automatic parachute back.  Raises LivePatchError."""
    before = chute_state(mem)
    if before.kind == STOCK:
        return before
    if before.kind == UNKNOWN:
        raise LivePatchError("WolfRAT does not recognise this server's parachute code - left alone.")
    try:
        mem.write_code([(CHUTE_SITE, CHUTE_STOCK[:5])])
    except Exception as exc:
        raise LivePatchError("Could not put the normal parachute back "
                             "(run WolfRAT as administrator?).") from exc
    after = chute_state(mem)
    if after.kind != STOCK:
        raise LivePatchError("The normal parachute did not go back in.")
    return after


def describe_chute(state: ChuteState) -> str:
    if state.kind == STOCK:
        return "Off - the game's normal parachute (opens by itself)."
    if state.kind == UNKNOWN:
        return ("WolfRAT does not recognise this server's parachute code (different exe, "
                "or onHook changed it), so it leaves it alone.")
    text = "On - players open the parachute with the space bar."
    if state.safety_net:
        text += " (Still the safety-net test version - WolfRAT puts the plain one back.)"
    if state.by_space is not None:
        text += f" Opened so far: {state.by_space}."
    return text


# ------------------------------------------------------------ the keeper
@dataclass(frozen=True)
class ChuteWish:
    """What the admin ticked."""
    on: bool = False


@dataclass
class KeeperResult:
    state: ChuteState
    wish: ChuteWish                 # may differ from the one passed in: show it
    note: str = ""                  # one line for the log, when something happened
    waiting: bool = False           # a change is queued until WolfRAT can write


class ChuteKeeper:
    """Called every poll (5 s) with the server's memory.

    The server is the truth.  A change the admin made (``changed()``) is
    written as soon as WolfRAT can write.  Otherwise:
    - ticked but the server has the normal parachute = the server restarted
      (or someone took it out): put it back if ``reapply``, else untick;
    - unticked but the server has the spacebar parachute (switched on before
      this WolfRAT started): show it ticked;
    - a safety-net cave left by a test build: the plain one goes back, once.
    """

    RETRY_SECONDS = 60

    def __init__(self):
        self.dirty = False
        self._gave_up_on = None          # cave a rewrite failed on: no retry storm
        self._reapply_retry_at = 0.0     # a failed put-back tries again from then
        self._reapply_error = ""         # reported once, not every retry

    def changed(self):
        self.dirty = True
        self._gave_up_on = None
        self._reapply_retry_at = 0.0
        self._reapply_error = ""

    def tick(self, mem, wish: ChuteWish, *, writable: bool, reapply: bool) -> KeeperResult:
        state = chute_state(mem)
        if state.kind == UNKNOWN:              # not ours to touch; keep the admin's tick
            self.dirty = False
            return KeeperResult(state, wish)
        plain = state.on and state.manual == CHUTE_MANUAL and state.safety_net == 0
        if self.dirty:
            if wish.on == state.on and (plain or not wish.on):
                self.dirty = False
                return KeeperResult(state, wish)
            if not writable:
                return KeeperResult(state, wish, waiting=True)
            self.dirty = False
            try:
                if wish.on:
                    state = chute_on(mem)
                    return KeeperResult(state, wish, "Spacebar parachute switched ON in the server.")
                state = chute_off(mem)
                return KeeperResult(state, wish, "Spacebar parachute switched OFF - "
                                    "the normal parachute is back.")
            except LivePatchError as exc:
                return KeeperResult(state, ChuteWish(state.on), f"Spacebar parachute: {exc}")
        if wish.on and state.kind == STOCK:
            if not reapply:
                return KeeperResult(state, ChuteWish(False),
                                    "The spacebar parachute is no longer in the server "
                                    "(it restarted?). Tick it to put it back.")
            if not writable:
                return KeeperResult(state, wish, waiting=True)
            if time.monotonic() < self._reapply_retry_at:
                return KeeperResult(state, wish)
            try:
                state = chute_on(mem)
            except LivePatchError as exc:
                # Stays ticked: a failed put-back is retried, never unticked.
                self._reapply_retry_at = time.monotonic() + self.RETRY_SECONDS
                note = "" if str(exc) == self._reapply_error else (
                    f"Spacebar parachute: {exc} Still ticked - WolfRAT tries again "
                    f"every {self.RETRY_SECONDS} s.")
                self._reapply_error = str(exc)
                return KeeperResult(state, wish, note)
            self._reapply_retry_at = 0.0
            self._reapply_error = ""
            return KeeperResult(state, wish, "Server restarted: spacebar parachute put back on.")
        if state.on and not wish.on:
            return KeeperResult(state, ChuteWish(True), "The spacebar parachute is already on in "
                                "the server - showing it ticked.")
        if state.on and not plain and state.cave != self._gave_up_on:
            if not writable:
                return KeeperResult(state, wish, waiting=True)
            try:
                state = chute_on(mem)
            except LivePatchError as exc:
                self._gave_up_on = state.cave
                return KeeperResult(state, wish, f"Spacebar parachute: {exc}")
            return KeeperResult(state, wish, "Spacebar parachute: the safety-net test version "
                                "was replaced by the plain one.")
        return KeeperResult(state, wish)
