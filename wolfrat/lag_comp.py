"""Lag compensation for the game server WolfRAT runs next to (Dale, 2026-10-18).

The game decides every hit on the server, against where people are on the
server right now.  A player with a high ping sees everyone a little in the
past, so they have to aim ahead of anyone moving.  Lag compensation checks
each of their bullets against where people were on THEIR screen instead.

This works on any Joint Operations server, retail or TAC: no onHook, no
change to the exe on disk, and players need nothing.  WolfRAT loads a small
helper (``wolfrat_lagcomp.dll``, bundled) into the running server.  Loading
it changes nothing.  Switching on points three calls in the game at the
helper (with the server paused for the moment it takes); switching off puts
them back.  The helper keeps its settings and the stats in one block that
WolfRAT reads and writes.  It stays loaded until the server closes.

Rules, as for the live patches:
- read the game's own bytes first; anything else at those calls (another
  tool, onHook's own lag compensation) = leave it alone and say so;
- the server is paused while the three calls change;
- a server restart or crash takes it away (WolfRAT can put it back).

Addresses and how each was found: WolfRAT vault, Systems/Memory Map.md.
No PyQt here, so the logic is unit-testable.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional

DLL_NAME = "wolfrat_lagcomp.dll"
LOADED_PREFIX = "wolfrat_lagcomp_"
EXPORT_NAME = b"WolfRatLagComp"
MAGIC = 0x31434C57
VERSION = 1

SLOTS = 250
LEGACY_SLOTS = 64
BINS = 128
BIN_MS = 8
BIN_MIN = -400

# Retail 1.7.5.7 / TAC / FMJ call sites, so WolfRAT can spot onHook's own
# lag compensation before loading its helper DLL.
_RETAIL_SITES = (
    (0x4E4C59, 0x4E4670),
    (0x4EA5BF, 0x4E4A30),
    (0x4C223A, 0x4EC020),
)
_LEG_TAIL = (0x4EA5C4, b"\x83\xc4\x08\x85\xc0\x75\x2d")

DEFAULT_EXTRA_MS = 40
DEFAULT_MAX_MS = 400
DEFAULT_MIN_PING_MS = 80

STOCK = "stock"        # the game's own calls: lag compensation off
ON = "on"              # our helper's calls
OLD = "old"            # an older copy of the helper (another WolfRAT build)
FOREIGN = "foreign"    # someone else's code: left alone
UNKNOWN = "unknown"    # helper not loaded / not readable


class LagCompError(Exception):
    pass


# ----------------------------------------------------------------- the block
class _Player(ctypes.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("entity", ctypes.c_uint32), ("ping", ctypes.c_int32),
                ("rewind_ms", ctypes.c_int32), ("tests", ctypes.c_uint32),
                ("hits", ctypes.c_uint32), ("plain_hits", ctypes.c_uint32),
                ("gained", ctypes.c_uint32), ("lost", ctypes.c_uint32),
                ("name", ctypes.c_char * 32)]


class _Shared(ctypes.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("magic", ctypes.c_uint32), ("version", ctypes.c_uint32),
                ("size", ctypes.c_uint32), ("status", ctypes.c_int32),
                ("error", ctypes.c_char * 64),
                ("leg_call", ctypes.c_uint32), ("bone_call", ctypes.c_uint32),
                ("tick_call", ctypes.c_uint32),
                ("leg_func", ctypes.c_uint32), ("bone_func", ctypes.c_uint32),
                ("tick_func", ctypes.c_uint32),
                ("leg_wrap", ctypes.c_uint32), ("bone_wrap", ctypes.c_uint32),
                ("tick_wrap", ctypes.c_uint32),
                ("enabled", ctypes.c_int32), ("observe", ctypes.c_int32),
                ("extra_ms", ctypes.c_int32), ("max_ms", ctypes.c_int32),
                ("min_ping_ms", ctypes.c_int32),
                ("reset_seq", ctypes.c_uint32), ("seen_reset_seq", ctypes.c_uint32),
                ("tick_us", ctypes.c_int32), ("ticks", ctypes.c_uint32),
                ("heartbeat", ctypes.c_uint32), ("history_live", ctypes.c_uint32),
                ("stats_since", ctypes.c_uint32),
                ("tests", ctypes.c_uint32), ("hits", ctypes.c_uint32),
                ("plain_hits", ctypes.c_uint32), ("gained", ctypes.c_uint32),
                ("lost", ctypes.c_uint32),
                ("cal_total", ctypes.c_uint32), ("cal", ctypes.c_uint32 * BINS),
                ("players", _Player * SLOTS)]


SHARED_SIZE = ctypes.sizeof(_Shared)
_OFF = {name: getattr(_Shared, name).offset for name, _ in _Shared._fields_}
_MIN_SHARED_SIZE = _OFF["players"] + LEGACY_SLOTS * ctypes.sizeof(_Player)


@dataclass
class Settings:
    observe: bool = False
    extra_ms: int = DEFAULT_EXTRA_MS
    max_ms: int = DEFAULT_MAX_MS
    min_ping_ms: int = DEFAULT_MIN_PING_MS

    def clamp(self) -> "Settings":
        return Settings(bool(self.observe), max(-200, min(300, int(self.extra_ms))),
                        max(50, min(900, int(self.max_ms))),
                        max(0, min(500, int(self.min_ping_ms))))


@dataclass
class PlayerStats:
    slot: int
    name: str
    ping: int
    rewind_ms: int
    tests: int
    hits: int
    plain_hits: int
    gained: int
    lost: int


@dataclass
class Stats:
    tests: int = 0
    hits: int = 0
    plain_hits: int = 0
    gained: int = 0
    lost: int = 0
    seconds: int = 0
    tick_us: int = 16000
    cal_total: int = 0
    cal_median: Optional[int] = None       # best rewind - ping, ms
    cal_low: Optional[int] = None          # quartiles
    cal_high: Optional[int] = None
    players: list = field(default_factory=list)
    since: int = 0                         # the server's tick count when counting began

    @property
    def gain_percent(self) -> Optional[float]:
        if not self.plain_hits:
            return None
        return (self.hits - self.plain_hits) * 100.0 / self.plain_hits

    def suggested_extra(self, minimum_shots: int = 300) -> Optional[int]:
        """The calibration median rounded to 10 ms, once there are enough shots."""
        if self.cal_median is None or self.cal_total < minimum_shots:
            return None
        return int(round(self.cal_median / 10.0)) * 10


@dataclass
class State:
    kind: str
    base: int = 0                     # helper module base in the server
    shared: int = 0                   # address of its block
    ready: bool = False
    error: str = ""
    observe: bool = False
    settings: Optional[Settings] = None
    stats: Optional[Stats] = None
    alive: bool = False               # the server ticked in the last 3 s

    @property
    def on(self) -> bool:
        return self.kind == ON


def histogram_quantiles(cal) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(lower quartile, median, upper quartile) of best rewind - ping, ms."""
    total = sum(cal)
    if not total:
        return None, None, None
    out = []
    for q in (0.25, 0.5, 0.75):
        target, run = q * total, 0
        for i, n in enumerate(cal):
            run += n
            if run >= target:
                out.append(BIN_MIN + i * BIN_MS + BIN_MS // 2)
                break
    return out[0], out[1], out[2]


def parse_stats(raw: bytes, now_ticks: Optional[int] = None) -> tuple[_Shared, Stats]:
    if len(raw) < _MIN_SHARED_SIZE:
        raise LagCompError("short read of the lag compensation block")
    if len(raw) < SHARED_SIZE:
        raw = raw + b"\0" * (SHARED_SIZE - len(raw))
    shared = _Shared.from_buffer_copy(raw[:SHARED_SIZE])
    if now_ticks is None:
        now_ticks = _tick_count()
    if shared.reset_seq != shared.seen_reset_seq:
        # Cleared, but the server applies it on its next tick (an empty server
        # does not tick): show it cleared now.
        shared.tests = shared.hits = shared.plain_hits = shared.gained = shared.lost = 0
        shared.cal_total = 0
        for i in range(BINS):
            shared.cal[i] = 0
        for p in shared.players:
            p.tests = p.hits = p.plain_hits = p.gained = p.lost = 0
        shared.stats_since = now_ticks
    cal = list(shared.cal)
    low, mid, high = histogram_quantiles(cal)
    stats = Stats(shared.tests, shared.hits, shared.plain_hits, shared.gained,
                  shared.lost, max(0, ((now_ticks - shared.stats_since) & 0xFFFFFFFF) // 1000),
                  shared.tick_us or 16000, shared.cal_total, mid, low, high,
                  since=shared.stats_since)
    for i, p in enumerate(shared.players):
        if not p.entity:
            continue
        name = p.name.split(b"\0")[0].decode("latin-1", "replace")
        stats.players.append(PlayerStats(i, name, p.ping, p.rewind_ms, p.tests, p.hits,
                                         p.plain_hits, p.gained, p.lost))
    return shared, stats


def _tick_count() -> int:
    if sys.platform == "win32":
        return ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return 0


# ------------------------------------------------------------ the helper DLL
def bundled_dll_path() -> Optional[str]:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            p = os.path.join(base, "wolfrat", DLL_NAME)
            if os.path.exists(p):
                return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), DLL_NAME)
    return p if os.path.exists(p) else None


def materialize_dll(data_dir: str) -> tuple[str, str, bytes]:
    """Copy the bundled helper to data_dir/wolfrat_lagcomp_<sha8>.dll (a copy the
    server still holds is never in the way).  Returns (path, file name, bytes)."""
    src = bundled_dll_path()
    if not src:
        raise LagCompError("WolfRAT's lag compensation helper is missing from this build.")
    with open(src, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()[:8]
    name = LOADED_PREFIX + sha + ".dll"
    dest = os.path.join(data_dir, name)
    if not os.path.exists(dest):
        os.makedirs(data_dir, exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    return dest, name, data


def export_rva(image: bytes, name: bytes, mapped: bool = False) -> Optional[int]:
    """RVA of an export in a PE (file bytes, or a mapped image when mapped=True)."""
    try:
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        if image[pe:pe + 4] != b"PE\0\0":
            return None
        opt = pe + 24
        magic = struct.unpack_from("<H", image, opt)[0]
        dd = opt + (96 if magic == 0x10B else 112)
        exp_rva, exp_size = struct.unpack_from("<II", image, dd)
        if not exp_rva:
            return None
        sections = []
        nsec = struct.unpack_from("<H", image, pe + 6)[0]
        sec = opt + struct.unpack_from("<H", image, pe + 20)[0]
        for i in range(nsec):
            vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", image, sec + i * 40 + 8)
            sections.append((va, max(vsize, raw_size), raw_ptr))

        def off(rva: int) -> int:
            if mapped:
                return rva
            for va, size, raw in sections:
                if va <= rva < va + size:
                    return rva - va + raw
            raise ValueError("rva outside sections")

        e = off(exp_rva)
        n_names, f_rva, n_rva, o_rva = struct.unpack_from("<I", image, e + 24)[0], *struct.unpack_from("<III", image, e + 28)
        for i in range(n_names):
            name_rva = struct.unpack_from("<I", image, off(n_rva) + 4 * i)[0]
            n0 = off(name_rva)
            end = image.index(b"\0", n0)
            if image[n0:end] == name:
                ordinal = struct.unpack_from("<H", image, off(o_rva) + 2 * i)[0]
                return struct.unpack_from("<I", image, off(f_rva) + 4 * ordinal)[0]
    except Exception:
        return None
    return None


# ------------------------------------------------------- the server process
_PROCESS_CREATE_THREAD = 0x0002
_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_PROCESS_QUERY_INFORMATION = 0x0400
_LIST_MODULES_32BIT = 0x01


class ServerProcess:
    """Module list, reads and helper loading in a 32-bit server process."""

    def __init__(self, pid: int, read_only: bool = False):
        self.pid = pid
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k.ReadProcessMemory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
        k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
        k.VirtualAllocEx.restype = ctypes.c_void_p
        k.VirtualAllocEx.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                     ctypes.c_uint32, ctypes.c_uint32)
        k.VirtualFreeEx.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.c_uint32)
        k.CreateRemoteThread.restype = ctypes.c_void_p
        k.CreateRemoteThread.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_void_p)
        k.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        k.GetExitCodeThread.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        k.CloseHandle.argtypes = (ctypes.c_void_p,)
        k.K32EnumProcessModulesEx.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32)
        k.K32GetModuleBaseNameW.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p,
                                            ctypes.c_uint32)
        self._k = k
        access = _PROCESS_VM_READ | _PROCESS_QUERY_INFORMATION
        if not read_only:
            access |= _PROCESS_CREATE_THREAD | _PROCESS_VM_OPERATION | _PROCESS_VM_WRITE
        self._h = k.OpenProcess(access, False, pid)
        if not self._h:
            raise LagCompError("Windows would not let WolfRAT open the server process "
                               "(run WolfRAT as administrator?).")

    def close(self) -> None:
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not self._k.ReadProcessMemory(self._h, ctypes.c_void_p(address), buf, size,
                                         ctypes.byref(done)) or done.value != size:
            raise LagCompError("Could not read the server's memory (has it closed?).")
        return buf.raw

    def write(self, address: int, data: bytes) -> None:
        done = ctypes.c_size_t()
        if not self._k.WriteProcessMemory(self._h, ctypes.c_void_p(address), data, len(data),
                                          ctypes.byref(done)) or done.value != len(data):
            raise LagCompError("Could not write to the server's memory (has it closed?).")

    def modules(self) -> dict:
        """32-bit modules: lower-case name -> base."""
        arr = (ctypes.c_uint64 * 1024)()
        needed = ctypes.c_uint32()
        if not self._k.K32EnumProcessModulesEx(self._h, arr, ctypes.sizeof(arr),
                                               ctypes.byref(needed), _LIST_MODULES_32BIT):
            raise LagCompError("Could not list the server's modules.")
        out = {}
        name = ctypes.create_unicode_buffer(260)
        for i in range(min(needed.value // 8, 1024)):
            base = arr[i]
            if not base:
                continue
            if self._k.K32GetModuleBaseNameW(self._h, ctypes.c_void_p(base), name, 260):
                out[name.value.lower()] = int(base)
        return out

    def _remote_export(self, base: int, name: bytes) -> Optional[int]:
        """Address of an export of a module loaded in the server.  Reads only
        the export tables (a whole image has gaps that cannot be read)."""
        head = self.read(base, 0x400)
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe:pe + 4] != b"PE\0\0":
            return None
        exp_rva, exp_size = struct.unpack_from("<II", head, pe + 24 + 96)
        if not exp_rva:
            return None
        exp = self.read(base + exp_rva, 40)
        n_names, f_rva, n_rva, o_rva = struct.unpack_from("<IIII", exp, 24)
        names = struct.unpack("<%dI" % n_names, self.read(base + n_rva, 4 * n_names))
        want = name + b"\0"
        for i, name_rva in enumerate(names):
            if self.read(base + name_rva, len(want)) != want:
                continue
            ordinal = struct.unpack("<H", self.read(base + o_rva + 2 * i, 2))[0]
            rva = struct.unpack("<I", self.read(base + f_rva + 4 * ordinal, 4))[0]
            return base + rva
        return None

    def load_library(self, path: str) -> int:
        """LoadLibraryA(path) inside the server.  Returns the module base."""
        mods = self.modules()
        kernel = mods.get("kernel32.dll")
        if not kernel:
            raise LagCompError("Could not find kernel32 in the server.")
        load = self._remote_export(kernel, b"LoadLibraryA")
        if not load:
            raise LagCompError("Could not find LoadLibraryA in the server.")
        data = os.path.abspath(path).encode("mbcs") + b"\0"
        mem = self._k.VirtualAllocEx(self._h, None, len(data), 0x3000, 0x04)
        if not mem:
            raise LagCompError("Could not make room in the server (run WolfRAT as administrator?).")
        try:
            self.write(int(mem), data)
            thread = self._k.CreateRemoteThread(self._h, None, 0, ctypes.c_void_p(load),
                                                ctypes.c_void_p(mem), 0, None)
            if not thread:
                raise LagCompError("Could not start the helper in the server "
                                   "(error %d; run WolfRAT as administrator?)." % ctypes.get_last_error())
            try:
                self._k.WaitForSingleObject(thread, 15000)
                code = ctypes.c_uint32()
                self._k.GetExitCodeThread(thread, ctypes.byref(code))
            finally:
                self._k.CloseHandle(thread)
        finally:
            self._k.VirtualFreeEx(self._h, ctypes.c_void_p(mem), 0, 0x8000)
        if not code.value:
            raise LagCompError("The server would not load WolfRAT's lag compensation helper.")
        return int(code.value)


# --------------------------------------------------------------- reading
@dataclass
class _Helper:
    name: str
    base: int
    shared: int
    block: _Shared


_block_at: dict = {}     # (pid, name, base) -> address of the helper's block


def _helpers(proc: ServerProcess) -> list:
    """Every copy of the helper loaded in the server; the caller picks by name."""
    out = []
    for name, base in proc.modules().items():
        if not name.startswith(LOADED_PREFIX):
            continue
        try:
            key = (proc.pid, name, base)
            address = _block_at.get(key)
            if address is None:
                address = proc._remote_export(base, EXPORT_NAME)
                if not address:
                    continue
                _block_at[key] = address
            try:
                raw = proc.read(address, SHARED_SIZE)
            except LagCompError:
                raw = proc.read(address, _MIN_SHARED_SIZE) + b"\0" * (SHARED_SIZE - _MIN_SHARED_SIZE)
            block = _Shared.from_buffer_copy(raw)
            if block.magic == MAGIC:
                out.append(_Helper(name, base, address, block))
        except LagCompError:
            continue
    return out


def _call_target(proc: ServerProcess, site: int) -> Optional[int]:
    b = proc.read(site, 5)
    if b[0] != 0xE8:
        return None
    return (site + 5 + struct.unpack("<i", b[1:])[0]) & 0xFFFFFFFF


def _sites(block: _Shared) -> list:
    return [(block.bone_call, block.bone_func, block.bone_wrap),
            (block.leg_call, block.leg_func, block.leg_wrap),
            (block.tick_call, block.tick_func, block.tick_wrap)]


def _classify(proc: ServerProcess, mine: Optional[_Helper], helpers: list) -> str:
    """What the three calls point at now."""
    ref = mine or (helpers[0] if helpers else None)
    if ref is None or ref.block.status != 1:
        try:
            if proc.read(_LEG_TAIL[0], len(_LEG_TAIL[1])) == _LEG_TAIL[1]:
                targets = [_call_target(proc, site) for site, _ in _RETAIL_SITES]
                if None not in targets and targets != [func for _, func in _RETAIL_SITES]:
                    return FOREIGN
        except LagCompError:
            pass
        return UNKNOWN
    targets = [_call_target(proc, site) for site, _, _ in _sites(ref.block)]
    if targets == [f for _, f, _ in _sites(ref.block)]:
        return STOCK
    if mine and targets == [w for _, _, w in _sites(mine.block)]:
        return ON
    for h in helpers:
        if targets == [w for _, _, w in _sites(h.block)]:
            return OLD
    return FOREIGN


def _call_bytes(site: int, target: int) -> bytes:
    return b"\xe8" + struct.pack("<i", target - (site + 5))


def read_state(proc: ServerProcess, wanted_name: Optional[str] = None) -> State:
    helpers = _helpers(proc)
    mine = next((h for h in helpers if h.name == wanted_name), None) if wanted_name else None
    if mine is None and helpers and wanted_name is None:
        mine = helpers[-1]
    kind = _classify(proc, mine, helpers)
    if mine is None:
        return State(kind if kind in (FOREIGN, STOCK) else UNKNOWN)
    try:
        raw = proc.read(mine.shared, SHARED_SIZE)
    except LagCompError:
        raw = proc.read(mine.shared, _MIN_SHARED_SIZE)
    block, stats = parse_stats(raw)
    alive = ((_tick_count() - block.heartbeat) & 0xFFFFFFFF) < 3000 if block.heartbeat else False
    settings = Settings(bool(block.observe), block.extra_ms, block.max_ms, block.min_ping_ms)
    return State(kind, mine.base, mine.shared, block.status == 1,
                 block.error.split(b"\0")[0].decode("latin-1"), bool(block.observe),
                 settings, stats, alive)


# ------------------------------------------------------------- on and off
def _write_settings(proc: ServerProcess, shared: int, settings: Settings, enabled: bool) -> None:
    s = settings.clamp()
    proc.write(shared + _OFF["observe"], struct.pack("<iiii", int(s.observe), s.extra_ms,
                                                     s.max_ms, s.min_ping_ms))
    proc.write(shared + _OFF["enabled"], struct.pack("<i", 1 if enabled else 0))


def apply_settings(pid: int, settings: Settings, wanted_name: Optional[str] = None) -> State:
    """New settings for a helper that is already on.  Takes effect at once."""
    with ServerProcess(pid) as proc:
        state = read_state(proc, wanted_name)
        if not state.shared:
            raise LagCompError("Lag compensation is not loaded in this server.")
        _write_settings(proc, state.shared, settings, state.on)
        return read_state(proc, wanted_name)


def reset_stats(pid: int, wanted_name: Optional[str] = None) -> bool:
    """Clear the numbers.  False when the helper is not in this server."""
    with ServerProcess(pid) as proc:
        state = read_state(proc, wanted_name)
        if not state.shared:
            return False
        seq = struct.unpack("<I", proc.read(state.shared + _OFF["reset_seq"], 4))[0]
        proc.write(state.shared + _OFF["reset_seq"], struct.pack("<I", (seq + 1) & 0xFFFFFFFF))
        return True


def turn_on(pid: int, memory, data_dir: str, settings: Settings) -> State:
    """Load the helper if needed and point the three calls at it.

    ``memory`` is WolfRAT's ProcessMemory for the server (its ``write_code``
    pauses the server while the calls change).  Raises LagCompError."""
    path, name, _ = materialize_dll(data_dir)
    with ServerProcess(pid) as proc:
        helpers = _helpers(proc)
        mine = next((h for h in helpers if h.name == name), None)
        kind = _classify(proc, mine, helpers)
        if kind == FOREIGN:
            raise LagCompError("Something else has changed the game's hit code in this server "
                               "(onHook's own lag compensation?) - WolfRAT left it alone.")
        if mine is None:
            proc.load_library(path)
            helpers = _helpers(proc)
            mine = next((h for h in helpers if h.name == name), None)
            if mine is None:
                raise LagCompError("The helper loaded but WolfRAT cannot find its settings block.")
        if mine.block.status != 1:
            why = mine.block.error.split(b"\0")[0].decode("latin-1") or "unknown reason"
            raise LagCompError("Lag compensation does not fit this server exe (%s)." % why)
        kind = _classify(proc, mine, helpers)
        if kind == FOREIGN:
            raise LagCompError("Something else has changed the game's hit code in this server "
                               "- WolfRAT left it alone.")
        _write_settings(proc, mine.shared, settings, True)
        if kind != ON:
            # An older helper's calls go straight over to the new one.
            memory.write_code([(site, _call_bytes(site, wrap))
                               for site, _, wrap in _sites(mine.block)])
        after = read_state(proc, name)
        if not after.on:
            raise LagCompError("Lag compensation did not stick in the server.")
        return after


def turn_off(pid: int, memory) -> State:
    """The game's own three calls back.  Raises LagCompError."""
    with ServerProcess(pid) as proc:
        helpers = _helpers(proc)
        if not helpers:
            return State(STOCK)
        ref = helpers[-1]
        kind = _classify(proc, ref, helpers)
        if kind == STOCK:
            for h in helpers:
                _write_settings(proc, h.shared, Settings(), False)
            return read_state(proc)
        if kind == FOREIGN:
            raise LagCompError("Something else has changed the game's hit code in this server "
                               "- WolfRAT left it alone.")
        memory.write_code([(site, _call_bytes(site, func))
                           for site, func, _ in _sites(ref.block)])
        for h in helpers:
            _write_settings(proc, h.shared, Settings(), False)
        after = read_state(proc)
        if after.kind != STOCK:
            raise LagCompError("The game's own hit code did not go back in.")
        return after


def server_state(pid: int, wanted_name: Optional[str] = None) -> State:
    """Read-only look at the server (needs no write access)."""
    with ServerProcess(pid, read_only=True) as proc:
        return read_state(proc, wanted_name)


def describe_setting(s: Settings) -> str:
    sign = "+" if s.extra_ms >= 0 else ""
    text = f"ping {sign}{s.extra_ms} ms, none under {s.min_ping_ms}, max {s.max_ms}"
    return ("watch only, " + text) if s.observe else text


class HistoryBook:
    """One row per counting session (a server run, or since the last restart
    of the counters), kept in a small JSON file.  A row is updated in place
    while it runs, so a crash loses nothing."""

    KEEP = 60

    def __init__(self, path):
        import json
        self._json = json
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.rows = rows if isinstance(rows, list) else []
        except Exception:
            self.rows = []

    def record(self, pid: int, stats: Stats, settings: Settings, now: Optional[float] = None) -> bool:
        """Note the session's latest numbers.  False when there is nothing yet."""
        import time
        if not stats.tests:
            return False
        now = time.time() if now is None else now
        key = f"{pid}-{stats.since:08x}"
        row = {"key": key,
               "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(now - stats.seconds)),
               "minutes": round(stats.seconds / 60.0),
               "setting": describe_setting(settings),
               "hits": stats.hits, "plain_hits": stats.plain_hits,
               "gained": stats.gained, "lost": stats.lost,
               "cal_total": stats.cal_total, "cal_median": stats.cal_median}
        for i, old in enumerate(self.rows):
            if old.get("key") == key:
                self.rows[i] = row
                break
        else:
            self.rows.append(row)
            self.rows = self.rows[-self.KEEP:]
        return True

    def save(self) -> None:
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            self._json.dump(self.rows, fh, indent=1)
        os.replace(tmp, self.path)


def loaded_name(data_dir: str) -> Optional[str]:
    """The file name this WolfRAT build loads (for read_state)."""
    try:
        _, name, _ = materialize_dll(data_dir)
        return name
    except LagCompError:
        return None
