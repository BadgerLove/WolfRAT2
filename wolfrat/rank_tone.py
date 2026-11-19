"""The rank-up sound, heard only by the player who ranked up (Dale, 2026-11-16).

Joint Ops still has a sound code in its chat: a line that starts with a hidden
control character and two digits plays a sound on every stock client (00 = the
"stat message" sound, 01/02 = the promotion sound), and the code never shows.
Nothing in the game sends one any more.  The admin port can only send a line to
everybody, so WolfRAT loads a small helper (``wolfrat_ranktone.dll``, bundled)
into the running server, the same way as lag compensation.  The helper sends a
line to one player at a time, the way the game sends team chat.

On a rank up the promoted player gets the line with the sound; every other
player gets the same line without it.  One line each, as before.  When the
helper is not available (server on another PC, an exe it does not know, the
call already taken by something else), the caller falls back to the normal
chat announcement.

Works on retail and TAC servers; players need nothing.  Addresses and how each
was found: WolfRAT vault, Development/Rank Tone.md.  No Qt here.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import sys
import time
from typing import Callable, Optional

from wolfrat import lag_comp

DLL_NAME = "wolfrat_ranktone.dll"
LOADED_PREFIX = "wolfrat_ranktone_"
EXPORT_NAME = b"WolfRatTone"
MAGIC = 0x31545257
QUEUE = 16
TEXT = 128

FREE, QUEUED, SENT, GONE = 0, 1, 2, 3
TONE_STAT, TONE_PROMOTION, NO_TONE = 0, 1, -1

CAPACITY_VA, SLOTPTR_VA, SLOT_STRIDE = 0x24C0CA4, 0x24C0CA8, 0x188E8
ACTIVE_OFFSET, NAME_OFFSET, NAME_LEN = 4, 40, 32
ARM_RETRY_SECONDS = 30.0


class _Entry(ctypes.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("state", ctypes.c_int32), ("slot", ctypes.c_int32),
                ("tone", ctypes.c_int32), ("everyone", ctypes.c_int32),
                ("seq", ctypes.c_uint32), ("delivered", ctypes.c_uint32),
                ("name", ctypes.c_char * 32), ("text", ctypes.c_char * TEXT)]


class _Shared(ctypes.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("magic", ctypes.c_uint32), ("version", ctypes.c_uint32),
                ("size", ctypes.c_uint32), ("status", ctypes.c_int32),
                ("error", ctypes.c_char * 64),
                ("call", ctypes.c_uint32), ("func", ctypes.c_uint32), ("wrap", ctypes.c_uint32),
                ("next", ctypes.c_uint32),
                ("heartbeat", ctypes.c_uint32), ("frames", ctypes.c_uint32),
                ("sent", ctypes.c_uint32), ("gone", ctypes.c_uint32),
                ("queue", _Entry * QUEUE)]


SHARED_SIZE = ctypes.sizeof(_Shared)
ENTRY_SIZE = ctypes.sizeof(_Entry)
QUEUE_OFFSET = _Shared.queue.offset
NEXT_OFFSET = _Shared.next.offset          # the thunk jumps through it (ranktone.c: 92)
_E = {name: getattr(_Entry, name).offset for name, _ in _Entry._fields_}


class RankToneError(Exception):
    pass


# --------------------------------------------------------------- the DLL file
def bundled_dll_path() -> Optional[str]:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            p = os.path.join(base, "wolfrat", DLL_NAME)
            if os.path.exists(p):
                return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), DLL_NAME)
    return p if os.path.exists(p) else None


_bundled_bytes: Optional[bytes] = None   # read once: the temp folder can vanish later


def _helper_bytes() -> bytes:
    global _bundled_bytes
    if _bundled_bytes is None:
        src = bundled_dll_path()
        if not src:
            raise RankToneError("the rank sound helper is missing from this build")
        with open(src, "rb") as fh:
            _bundled_bytes = fh.read()
    return _bundled_bytes


def materialize_dll(data_dir: str) -> tuple[str, str]:
    """Copy the bundled helper to data_dir/wolfrat_ranktone_<sha8>.dll."""
    data = _helper_bytes()
    name = LOADED_PREFIX + hashlib.sha256(data).hexdigest()[:8] + ".dll"
    dest = os.path.join(data_dir, name)
    if not os.path.exists(dest):
        os.makedirs(data_dir, exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    return dest, name


# ------------------------------------------------------------------ helpers
def call_target(mem, site: int) -> Optional[int]:
    b = mem.read(site, 5)
    if b[0] != 0xE8:
        return None
    return (site + 5 + struct.unpack("<i", b[1:])[0]) & 0xFFFFFFFF


def call_bytes(site: int, target: int) -> bytes:
    return b"\xe8" + struct.pack("<i", target - (site + 5))


def read_block(mem, address: int) -> _Shared:
    return _Shared.from_buffer_copy(mem.read(address, SHARED_SIZE))


def tone_line(text: str) -> bytes:
    """The chat text as the helper sends it: latin-1, at most TEXT-1 bytes."""
    raw = text.replace("\x07", "").encode("latin-1", "replace")
    return raw[:TEXT - 1]


def find_slot(mem, name: str) -> Optional[tuple[int, bytes]]:
    """(slot index, the slot's exact name bytes) of the player called ``name``."""
    capacity = struct.unpack("<i", mem.read(CAPACITY_VA, 4))[0]
    base = struct.unpack("<I", mem.read(SLOTPTR_VA, 4))[0]
    if not base or not 0 < capacity <= 256:
        return None
    wanted = name.strip().lower()
    for index in range(capacity):
        head = mem.read(base + index * SLOT_STRIDE, NAME_OFFSET + NAME_LEN)
        if not head[ACTIVE_OFFSET]:
            continue
        raw = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0]
        if raw.decode("latin-1", "replace").strip().lower() == wanted:
            return index, raw
    return None


# --------------------------------------------------------------- the feature
class RankTone:
    """Owns the helper in one server.  ``server_tick`` (once a second, from the
    Weather tab's server loop) loads it and points the frame call at it;
    ``queue_line`` queues a line (not admin traffic: it goes through the helper).  ``enabled()`` says whether the admin wants it."""

    def __init__(self, data_dir: str, enabled: Callable[[], bool] = lambda: True,
                 quiet: Callable[[], bool] = lambda: False,
                 log: Callable[[str], None] = lambda _t: None,
                 clock: Callable[[], float] = time.monotonic,
                 process_factory=lag_comp.ServerProcess,
                 chain_foreign: bool = False):
        self._data_dir = data_dir
        self._enabled = enabled
        self._quiet = quiet
        self._log = log
        self._clock = clock
        self._process = process_factory
        # Test rigs only: go in front of another tool's hook on the call and
        # jump on to it.  WolfRAT itself leaves such a server alone.
        self._chain_foreign = chain_foreign
        self._memory = None
        self._pid: Optional[int] = None
        self._shared: Optional[int] = None
        self._block: Optional[_Shared] = None
        self._next_try = 0.0
        self._seq = 0
        self.problem = ""

    # ---- the Weather tab's ticker contract ----------------------------------
    def wants_write(self) -> bool:
        return bool(self._enabled())

    def no_server(self, _text: str) -> None:
        self._memory, self._pid, self._shared, self._block = None, None, None, None

    def server_tick(self, memory, writable: bool) -> None:
        if not writable or memory is None:
            self._memory = None
            return
        pid = getattr(memory, "pid", None)
        if pid != self._pid:
            self._pid, self._shared, self._block, self._next_try = pid, None, None, 0.0
        self._memory = memory
        if self._enabled() and not self._quiet():
            self.arm()

    # ---- load + hook ----------------------------------------------------------
    def ready(self) -> bool:
        """The helper is in this server and the frame call points at it."""
        mem = self._memory
        if mem is None or self._shared is None or self._block is None:
            return False
        try:
            return call_target(mem, self._block.call) == self._block.wrap
        except Exception:
            return False

    def arm(self) -> bool:
        if self.ready():
            return True
        now = self._clock()
        if now < self._next_try or self._memory is None:
            return False
        self._next_try = now + ARM_RETRY_SECONDS
        try:
            self._arm(self._memory)
            self.problem = ""
            return True
        except Exception as exc:                      # never takes anything else down
            text = str(exc) or exc.__class__.__name__
            if text != self.problem:
                self._log(f"rank sound unavailable: {text}")
            self.problem = text
            return False

    def _arm(self, mem) -> None:
        with self._process(mem.pid) as proc:
            mine = self._find(proc)
            if mine is None:
                path, _name = materialize_dll(self._data_dir)
                proc.load_library(path)
                mine = self._find(proc)
                if mine is None:
                    raise RankToneError("the helper loaded but its block was not found")
            name, shared, block = mine
            if block.status != 1:
                why = block.error.split(b"\0")[0].decode("latin-1") or "unknown reason"
                raise RankToneError(f"this server exe does not fit ({why})")
            target = call_target(mem, block.call)
            if target != block.wrap:
                older = {b.wrap: b.next for _n, _s, b in self._all(proc) if b.wrap != block.wrap}
                if target == block.func:
                    after = block.func                  # the game's own call
                elif target in older:
                    after = older[target]               # an older copy of this helper: keep its chain
                elif self._chain_foreign and target:
                    after = target
                else:
                    raise RankToneError("something else already uses the game's frame call - left alone")
                if not after:
                    raise RankToneError("the older helper has nowhere to jump on to - left alone")
                mem.write(shared + NEXT_OFFSET, struct.pack("<I", after))
                mem.write_code([(block.call, call_bytes(block.call, block.wrap))])
                if call_target(mem, block.call) != block.wrap:
                    raise RankToneError("the frame call did not change")
                self._log(f"rank sound ready ({name})")
            self._shared, self._block = shared, block

    def _all(self, proc) -> list:
        out = []
        for name, base in proc.modules().items():
            if not name.startswith(LOADED_PREFIX):
                continue
            try:
                address = proc._remote_export(base, EXPORT_NAME)
                if not address:
                    continue
                size = struct.unpack("<I", proc.read(address + 8, 4))[0]
                if size != SHARED_SIZE:
                    continue
                block = _Shared.from_buffer_copy(proc.read(address, SHARED_SIZE))
                if block.magic == MAGIC:
                    out.append((name, address, block))
            except Exception:
                continue
        return out

    def _find(self, proc):
        _path, wanted = materialize_dll(self._data_dir)
        return next((h for h in self._all(proc) if h[0] == wanted), None)

    # ---- sending ----------------------------------------------------------------
    def queue_line(self, name: str, text: str, tone: int = TONE_STAT, everyone: bool = True) -> bool:
        """Queue ``text``: with the sound for ``name``, plain for everyone else
        (``everyone``).  False = not sent; use the normal chat instead."""
        if not self._enabled() or not self.ready():
            return False
        mem = self._memory
        try:
            found = find_slot(mem, name)
            if found is None:
                return False
            slot, raw_name = found
            for i in range(QUEUE):
                entry = self._shared + QUEUE_OFFSET + i * ENTRY_SIZE
                state = struct.unpack("<i", mem.read(entry, 4))[0]
                if state == QUEUED:
                    continue
                self._seq = (self._seq + 1) & 0xFFFFFFFF
                body = struct.pack("<iiiII", slot, int(tone), 1 if everyone else 0, self._seq, 0)
                mem.write(entry + _E["slot"], body)
                mem.write(entry + _E["name"], raw_name[:NAME_LEN - 1].ljust(NAME_LEN, b"\0"))
                mem.write(entry + _E["text"], tone_line(text).ljust(TEXT, b"\0"))
                mem.write(entry + _E["state"], struct.pack("<i", QUEUED))   # last: hands it over
                return True
            return False                               # queue full
        except Exception as exc:
            self._log(f"rank sound not sent: {exc}")
            return False

    def stats(self) -> Optional[dict]:
        if not self.ready():
            return None
        try:
            block = read_block(self._memory, self._shared)
        except Exception:
            return None
        return {"sent": block.sent, "gone": block.gone, "frames": block.frames}
