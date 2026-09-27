"""Build a patched COPY of the game server's exe.  No Qt.

The same byte changes as the scripts in github.com/BadgerLove/jo-server-patches,
carried inside WolfRAT (nothing is downloaded).  The admin's own jointops.exe
is read, never changed: WolfRAT writes ``jointops_wolfrat.exe`` beside it (and
``binkw32_wolfrat.dll`` when the mission memory lives in a hook DLL), then the
admin stops the server and renames them.  Every site is checked for the bytes
the patch expects before anything is written, so a build WolfRAT does not know
is refused, never mangled.  Patches already in the exe are left as they are.

The admin-port crash fix is always applied (Dale 2026-09-27: "I'm not letting
anyone use any of our patches without it").  Hole-skip comes with 64 and
125 Hz, in the version made for that speed (Dale 2026-09-27: 64 Hz does not
work without it, 125 Hz needs the tuned one).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

from wolfrat import server_patches as sp

OUT_EXE = "jointops_wolfrat.exe"
OUT_DLL = "binkw32_wolfrat.dll"
BACKUP_EXE = "jointops_before_wolfrat.exe"
BACKUP_DLL = "binkw32_before_wolfrat.dll"

SPEEDS = (32, 64, 125)
SPEED_BYTE = {32: 0x04, 64: 0x02, 125: 0x01}
MEMORY = {192: 0x0C000000, 512: 0x20000000, 1024: 0x40000000}
MEMORY_WORDS = {192: "192 MB", 512: "512 MB", 1024: "1 GB"}


class PatchError(Exception):
    """The exe is not one WolfRAT can patch; the message is for the admin."""


class Image:
    """A mutable exe or DLL, addressed by virtual address."""

    def __init__(self, data: bytes):
        self.data = bytearray(data)
        try:
            pe = struct.unpack_from("<I", self.data, 0x3C)[0]
            if self.data[pe:pe + 4] != b"PE\0\0":
                raise PatchError("This is not a Windows exe.")
            self.pe = pe
            count = struct.unpack_from("<H", self.data, pe + 6)[0]
            first = pe + 24 + struct.unpack_from("<H", self.data, pe + 20)[0]
            self.base = struct.unpack_from("<I", self.data, pe + 24 + 28)[0]
            self.sections = []           # (name, header offset, va, vsize, raw size, raw offset)
            for i in range(count):
                hdr = first + i * 40
                vs, va, rs, ro = struct.unpack_from("<IIII", self.data, hdr + 8)
                self.sections.append((bytes(self.data[hdr:hdr + 8]).rstrip(b"\0"), hdr,
                                      self.base + va, vs, rs, ro))
        except struct.error:
            raise PatchError("This is not a Windows exe.") from None

    def offset(self, va: int, size: int = 1) -> Optional[int]:
        for _name, _hdr, start, vs, rs, ro in self.sections:
            if start <= va and va + size <= start + max(vs, rs):
                return ro + va - start if va - start + size <= rs else None
        return None

    def read(self, va: int, size: int) -> Optional[bytes]:
        off = self.offset(va, size)
        return bytes(self.data[off:off + size]) if off is not None else None

    def is_(self, va: int, want: bytes) -> bool:
        return self.read(va, len(want)) == want

    def put(self, va: int, old: bytes, new: bytes, what: str) -> None:
        if self.read(va, len(old)) != old:
            raise PatchError(f"{what}: this exe has different bytes there, so WolfRAT will not "
                             "touch it (a build it does not know, or patched another way).")
        off = self.offset(va, len(new))
        self.data[off:off + len(new)] = new

    def section(self, name: bytes):
        found = [s for s in self.sections if s[0] == name]
        return found[0] if len(found) == 1 else None

    @property
    def characteristics(self) -> int:
        return struct.unpack_from("<H", self.data, self.pe + 22)[0]

    def set_laa(self) -> None:
        struct.pack_into("<H", self.data, self.pe + 22, self.characteristics | 0x20)

    def fix_checksum(self) -> None:
        """PE checksum, as pefile.generate_checksum() makes it."""
        at = self.pe + 24 + 64
        total = 0
        data = bytes(self.data) + b"\0" * (-len(self.data) % 4)
        for (word,) in struct.iter_unpack("<I", data):
            total += word
        total -= struct.unpack_from("<I", data, at)[0]
        while total >> 32:
            total = (total & 0xFFFFFFFF) + (total >> 32)
        total = (total & 0xFFFF) + (total >> 16)
        total = (total + (total >> 16)) & 0xFFFF
        struct.pack_into("<I", self.data, at, total + len(self.data))

    def as_exe(self) -> sp.Exe:
        return sp.Exe(bytes(self.data))


# ---- the patches (bytes from jo-server-patches, same order the scripts use) ------

def _admin_fix(img: Image) -> None:                                   # 01
    img.put(0x4056E6, bytes.fromhex("03c003c0"), bytes.fromhex("c1e00690"), "Admin-port crash fix")


def _speed(img: Image, hz: int) -> None:                              # 07
    current = img.read(sp.SPEED_VA, 1)
    if not current or not sp.speed_site_ok(img.as_exe()):
        raise PatchError("Server speed: WolfRAT does not know this exe's speed setting.")
    img.put(sp.SPEED_VA, current, bytes([SPEED_BYTE[hz]]), "Server speed")


# Spaghetti's hole-skip (09): the drain loop jumps to a cave that skips a lost
# packet instead of waiting for it.  Same bytes in every build that has it;
# only the two settings differ (see sp.HOLE_VERSIONS).
_HOLE_HOOK_VA, _HOLE_CAVE_VA = 0x626954, 0x794DF0
_HOLE_HOOK_OLD, _HOLE_HOOK_NEW = bytes.fromhex("8b8fb807000083c1"), bytes.fromhex("e997e41600909090")
_HOLE_CAVE = bytes.fromhex(
    "8b8fb807000083c101394e140f84601be9ff8b8fa807000083f9087c2f8b87b40700002b87b8070000"
    "3d4000000076168b4e1449898fb8070000c6877401000001e90a1be9ff8b87a0070000e97a1be9ff"
)
assert len(_HOLE_CAVE) == 0x51
assert _HOLE_CAVE_VA + 0x1A == sp.HOLE_REORDER_VA and _HOLE_CAVE_VA + 0x2A == sp.HOLE_LAG_VA
_HOLE_SETTINGS = {hz: settings for settings, hz in sp.HOLE_VERSIONS.items()}


def hole_room(img: Image) -> bool:
    """The cave's space is free.  (The 32 Hz JOexeFIX exes keep one stray
    byte of an earlier cave at its very end - the same byte the cave writes.)"""
    space = img.read(_HOLE_CAVE_VA, len(_HOLE_CAVE))
    return (img.is_(_HOLE_HOOK_VA, _HOLE_HOOK_OLD) and space is not None
            and space[:-1] == bytes(len(space) - 1) and space[-1] in (0x00, _HOLE_CAVE[-1]))


def _hole_skip(img: Image, hz: int) -> None:                          # 09
    what = "Hole-skip"
    if not hole_room(img):
        raise PatchError(f"{what}: the space it needs in the exe is already used.")
    cave = bytearray(_HOLE_CAVE)
    cave[0x1A], cave[0x2A] = _HOLE_SETTINGS[hz]
    off = img.offset(_HOLE_CAVE_VA, len(cave))
    img.data[off:off + len(cave)] = cave
    img.put(_HOLE_HOOK_VA, _HOLE_HOOK_OLD, _HOLE_HOOK_NEW, what)


def _hole_tune(img: Image, old: tuple, hz: int) -> None:
    new = _HOLE_SETTINGS[hz]
    img.put(sp.HOLE_REORDER_VA, bytes([old[0]]), bytes([new[0]]), "Hole-skip")
    img.put(sp.HOLE_LAG_VA, bytes([old[1]]), bytes([new[1]]), "Hole-skip")


def _exe_memory(img: Image, mb: int) -> None:                         # 04
    current = img.read(sp.ARENA_VA, 4)
    if current is None or struct.unpack("<I", current)[0] not in MEMORY.values():
        raise PatchError("Mission memory: WolfRAT does not know this exe's memory setting.")
    img.put(sp.ARENA_VA, current, struct.pack("<I", MEMORY[mb]), "Mission memory")


_GETTICKCOUNT, _TIMEGETTIME = 0x7C0208, 0x7C0454
_FPS_FIXED = (
    (0x52B85E, "33c0390544075502" "8935081f4e02" "0f95c0", "a144075502" "8935081f4e02" "909090909090"),
    (0x52B89F, "c1e008", "c1e004"),
    (0x52B8CA, "c1e208", "c1e204"),
)
_FPS_CLOCKS = ((0x52B75C, b"\xff\x15"), (0x52B798, b"\xff\x15"), (0x52B8B8, b"\xff\x15"),
               (0x52BA7E, b"\xff\x15"), (0x52BA95, b"\x8b\x1d"))
_FPS_RESTORE = ((0x52B8B0, bytes.fromhex("6a01ff15d4007c00")), (0x52B8DF, bytes.fromhex("72c7")))


def _fps_lock(img: Image) -> None:                                    # 11 (+ undoes 06)
    what = "Frame rate lock"
    for va, intact in _FPS_RESTORE:
        if img.is_(va, b"\x90" * len(intact)):
            img.put(va, b"\x90" * len(intact), intact, what)
        elif not img.is_(va, intact):
            raise PatchError(f"{what}: this exe has different bytes there, so WolfRAT will not touch it.")
    for va, old, new in _FPS_FIXED:
        img.put(va, bytes.fromhex(old), bytes.fromhex(new), what)
    for va, prefix in _FPS_CLOCKS:
        img.put(va, prefix + struct.pack("<I", _GETTICKCOUNT), prefix + struct.pack("<I", _TIMEGETTIME), what)


_SPAWN_GLOBAL, _SPAWN_GET, _SPAWN_STUB = 0x2550844, 0x726015, 0x7472C9
_SPAWN_SITES = {0x516BBA: "ecx", 0x517937: "eax", 0x517952: "ecx",
                0x517960: "edx", 0x519FE7: "eax", 0x51A882: "eax"}
_SPAWN_MODRM = {"eax": 0x80, "ecx": 0x81, "edx": 0x82}
_SPAWN_STORE = {"eax": "89b824010000", "ecx": "89b924010000", "edx": "89ba24010000"}


def _rel(src: int, dst: int, length: int = 5) -> bytes:
    return struct.pack("<i", dst - (src + length))


def _spawn_config(img: Image) -> None:                                # 12
    what = "Spawn protection from game.cfg"
    old_key = b"enable_keyboardtips"
    new_key = b"e_spawn_protection"          # must start with 'e' (the parser switches on it)
    img.put(0x7D477C, old_key + b"\0", new_key.ljust(len(old_key), b"\0") + b"\0", what)
    img.put(0x7D3B90, old_key + b"  = %i\n\0",
            new_key + b" " * (len(old_key) + 2 - len(new_key)) + b"= %i\n\0", what)
    img.put(0x54D13B, bytes.fromhex("8935") + struct.pack("<I", _SPAWN_GLOBAL), b"\x90" * 6, what)
    get = (bytes.fromhex("6b3d") + struct.pack("<I", _SPAWN_GLOBAL) + bytes([62])
           + bytes.fromhex("85ff7f05") + b"\xbf" + struct.pack("<I", 620) + b"\xc3")
    img.put(_SPAWN_GET, b"\xcc" * len(get), get, what)
    stubs, at = {}, _SPAWN_STUB
    for reg in ("eax", "ecx", "edx"):
        stub = (b"\x57\xe8" + _rel(at + 1, _SPAWN_GET) + bytes.fromhex(_SPAWN_STORE[reg])
                + b"\x5f\xc3")
        img.put(at, b"\xcc" * len(stub), stub, what)
        stubs[reg] = at
        at += len(stub)
    for va, reg in _SPAWN_SITES.items():
        old = b"\xc7" + bytes([_SPAWN_MODRM[reg]]) + bytes.fromhex("24010000") + struct.pack("<I", 620)
        img.put(va, old, b"\xe8" + _rel(va, stubs[reg]) + b"\x90" * 5, what)


def _kill_list(img: Image) -> None:                                   # 14 K
    img.put(0x504C2B, bytes.fromhex("80e201"), bytes.fromhex("80e200"), "Kill List crash fix")


_TEXT2_VA, _TEXT2_RAW, _TEXT2_STOCK = 0x795000, 0x2B000, 0x2AA10


def _map_cave_tail(img: Image, what: str) -> None:
    """Patches 14 and 15 live in the zero tail of `_text`; map it once."""
    sec = img.section(b"_text")
    if sec is None:
        raise PatchError(f"{what}: this exe is not the build WolfRAT knows.")
    _name, hdr, va, vs, rs, _ro = sec
    flags = struct.unpack_from("<I", img.data, hdr + 36)[0]
    if va != _TEXT2_VA or rs != _TEXT2_RAW or flags & 0xE0000000 != 0xE0000000:
        raise PatchError(f"{what}: this exe is not the build WolfRAT knows.")
    if vs == _TEXT2_STOCK:
        struct.pack_into("<I", img.data, hdr + 8, _TEXT2_RAW)
    elif vs != _TEXT2_RAW:
        raise PatchError(f"{what}: this exe is not the build WolfRAT knows.")


_SPEC_CAVE, _SPEC_END = 0x7BFA20, 0x7BFD00
_SPEC_STUBS = bytes.fromhex(
    "80bed7880100007439833d28194c0200753083bc243c050000017c2b8a073c0174163c0275218b0685c0"
    "741b8a8062010000fec83c01760fc705f0fc7b0001000000e9763dd5ffe9f946d5ff00000000"
    "833df0fc7b00007405c644240cff8b44240855e91d4dd4ff0000000000000000"
    "833df0fc7b00007405c6442404ff0fb6442404e96dbec6ff0000000000000000"
    "c705f0fc7b00000000005f31e1e8b9affaffe9ae46d5ff"
)
assert len(_SPEC_STUBS) == 0xA7


def _jmp(frm: int, to: int) -> bytes:
    return b"\xe9" + _rel(frm, to)


def _spectator_chat(img: Image) -> None:                              # 14 S
    what = "Spectator chat"
    _map_cave_tail(img, what)
    if not img.is_(_SPEC_CAVE - 0x10, bytes(_SPEC_END - _SPEC_CAVE + 0x10)):
        raise PatchError(f"{what}: the space it needs in the exe is already used.")
    img.put(_SPEC_CAVE, bytes(len(_SPEC_STUBS)), _SPEC_STUBS, what)
    img.put(0x5137C7, bytes.fromhex("80bed788010000740d833d28194c02000f8488090000"),
            _jmp(0x5137C7, 0x7BFA20).ljust(22, b"\x90"), what)
    img.put(0x5047A0, bytes.fromhex("8b44240855"), _jmp(0x5047A0, 0x7BFA70), what)
    img.put(0x42B910, bytes.fromhex("0fb6442404"), _jmp(0x42B910, 0x7BFA90), what)
    img.put(0x51416D, bytes.fromhex("5f33cce806692500"), _jmp(0x51416D, 0x7BFAB0).ljust(8, b"\x90"), what)


_CHAT_FLOOD = bytes.fromhex(
    "56538b74240c89f1803900740341ebf829f183f9777604c646770031db83f93b76058a5e3b"
    "b70156e83392cdff83c40484ff7403885e3b5b5ec3"
)
_CHAT_STRIP = bytes.fromhex(
    "56578b442410506800fe7b00e89f86cdff83c408be00fe7b008b7c240cb93f0000008a0688"
    "0784c0740846474975f3c607005f5ec3"
)
assert len(_CHAT_FLOOD) == 58 and len(_CHAT_STRIP) == 53
_CHAT_SEND_VA = 0x49A9DC
_CHAT_STRIPPED, _CHAT_PLAIN, _CHAT_COLOUR = (bytes.fromhex("8d4c240451"), bytes.fromhex("6800fe7b00"),
                                             bytes.fromhex("5690909090"))


def _long_chat(img: Image, colour: bool) -> None:                     # 15
    what = "Long chat"
    _map_cave_tail(img, what)
    img.put(0x7BFD00, bytes(len(_CHAT_FLOOD)), _CHAT_FLOOD, what)
    img.put(0x7BFD40, bytes(len(_CHAT_STRIP)), _CHAT_STRIP, what)
    if not img.is_(0x7BFE00, bytes(0x100)):
        raise PatchError(f"{what}: the space it needs in the exe is already used.")
    img.put(0x49A93B, bytes.fromhex("e820e6ffff"), bytes.fromhex("e8c0533200"), what)
    img.put(0x49A971, bytes.fromhex("e87adaffff"), bytes.fromhex("e8ca533200"), what)
    img.put(_CHAT_SEND_VA, _CHAT_STRIPPED, _CHAT_COLOUR if colour else _CHAT_PLAIN, what)


def _chat_colour(img: Image, colour: bool) -> None:
    """Long chat is already in: only switch colours on or off."""
    old, new = (_CHAT_PLAIN, _CHAT_COLOUR) if colour else (_CHAT_COLOUR, _CHAT_PLAIN)
    img.put(_CHAT_SEND_VA, old, new, "Long chat colours")


# ---- the hook DLL's memory (onHook sets it at start-up; never named to admins) ----

def dll_memory(data: bytes) -> Optional[int]:
    found = sp._HOOK_ARENA.search(data)
    return struct.unpack("<I", found.group(1))[0] if found else None


def patch_dll_memory(data: bytes, mb: int) -> bytes:
    found = list(sp._HOOK_ARENA.finditer(data))
    if len(found) != 1:
        raise PatchError("Mission memory: WolfRAT does not know this server's binkw32.dll.")
    img = Image(data)
    start = found[0].start()
    size = struct.pack("<I", MEMORY[mb])
    img.data[start + 6:start + 10] = size          # mov [ebp-128h], size
    img.data[start + 14:start + 18] = size         # mov [esp+10h], size (its log line)
    img.fix_checksum()
    return bytes(img.data)


# ---- what the exe has now, and what the admin may choose ----------------------------

@dataclass
class State:
    """What the exe (and its folder) has now.  None = cannot tell."""
    exe_path: str
    admin_fix: Optional[bool]
    speed: Optional[int]
    speed_site: bool                # the speed can be set (even if its value has no name)
    hole_skip: Optional[bool]
    hole_settings: Optional[tuple]  # (reorder, lag) when it is in
    hole_room: bool                 # it can be added
    memory: Optional[int]           # MB, from the hook DLL when there is one
    memory_in_dll: bool
    laa: bool
    fps_lock: Optional[bool]        # False = off or the old unlimited hack
    spawn: str                      # "config" / "stock" / "fixed" / "unknown"
    kill_list: Optional[bool]
    spectator: Optional[bool]
    long_chat: Optional[str]        # "off" / "plain" / "colour"
    cfg: dict = field(default_factory=dict)
    # the running server, when it differs from the file (a hook DLL at start-up)
    admin_fix_live: bool = False
    spawn_live_ticks: Optional[int] = None

    @property
    def known(self) -> bool:
        return self.admin_fix is not None and self.speed_site


def _mb(value: Optional[int]) -> Optional[int]:
    return {v: k for k, v in MEMORY.items()}.get(value)


def read_state(exe_path: str, live: Optional[sp.LiveRead] = None) -> State:
    """Raises PatchError if the file cannot be read or is not an exe.
    ``live`` peeks at the running server so start-up additions are seen."""
    try:
        with open(exe_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise PatchError(f"Could not read the server exe ({exc}).") from None
    Image(data)                                    # a PatchError if it is not an exe
    state = state_of(exe_path, sp.Exe(data), sp.read_cfg(os.path.dirname(exe_path)),
                     sp.hook_arena(os.path.dirname(exe_path)))
    state.admin_fix_live = sp.admin_fix_live(live)
    state.spawn_live_ticks = sp.spawn_live_ticks(live)
    return state


def state_of(exe_path: str, exe: sp.Exe, cfg: dict, dll_value: Optional[int]) -> State:
    admin = sp._two_way(exe, 0x4056E6, "03c003c0", "c1e00690")
    fps = None
    if all(exe.is_(va, b) for va, b in sp._FPS_ON) and not exe.is_(0x52B8B0, "9090909090909090"):
        fps = True
    elif all(exe.is_(va, b) for va, b in sp._FPS_OFF):
        fps = False
    site = exe.read(0x516BBA, 10)
    if site and site[0] == 0xE8:
        spawn = "config"
    elif site and site[:6] == bytes.fromhex("c78124010000"):
        spawn = "stock" if struct.unpack("<i", site[6:10])[0] == 620 else "fixed"
    else:
        spawn = "unknown"
    first = exe.read(0x5137C7, 1)
    raw = exe.read(sp.ARENA_VA, 4)
    exe_mb = _mb(struct.unpack("<I", raw)[0]) if raw else None
    return State(
        exe_path=exe_path, admin_fix=admin, speed=sp.server_speed(exe),
        speed_site=sp.speed_site_ok(exe), hole_skip=sp.hole_skip(exe),
        hole_settings=sp.hole_skip_version(exe), hole_room=hole_room(Image(exe.data)),
        memory=_mb(dll_value) if dll_value is not None else exe_mb,
        memory_in_dll=dll_value is not None, laa=bool(exe.characteristics & 0x20),
        fps_lock=fps, spawn=spawn,
        kill_list=sp._two_way(exe, 0x504C2B, "80e201", "80e200"),
        spectator={b"\xe9": True, b"\x80": False}.get(first),
        long_chat=sp.long_chat(exe), cfg=cfg)


@dataclass
class Choice:
    speed: int = 64
    memory: int = 512
    laa: bool = True
    fps_lock: bool = True
    spawn: bool = True
    kill_list: bool = True
    spectator: bool = False
    long_chat: bool = False
    chat_colour: bool = True


def wants_fps_lock(choice: Choice) -> bool:
    """125 Hz always gets the frame rate lock (Dale 2026-09-27): without it the
    server runs the wrong frame rate for its tick rate."""
    return choice.fps_lock or choice.speed == 125


def speed_allowed(state: State, hz: int) -> tuple:
    """(allowed, reason shown when it is not).  64 and 125 Hz need hole-skip."""
    if hz == 32 or state.hole_skip or state.hole_room:
        return True, ""
    return False, (f"{hz} Hz needs hole-skip, and this exe has something else where hole-skip "
                   "goes, so WolfRAT cannot add it.")


def hole_plan(state: State, hz: int) -> str:
    """What the build will do about hole-skip at this speed, admin's words ('' = nothing)."""
    if hz == 32:
        return ""
    if not state.hole_skip:
        return f"Hole-skip ({hz} Hz version)" if state.hole_room else ""
    made_for = sp.HOLE_VERSIONS.get(state.hole_settings)
    if made_for is not None and made_for != hz:
        return f"Hole-skip switched to the {hz} Hz version"
    return ""


def default_choice(state: State) -> Choice:
    """Start from what the server has, with the safe extras ticked."""
    speed = state.speed if state.speed in SPEEDS else 64
    return Choice(
        speed=speed,
        memory=max(state.memory or 512, 512),
        laa=True,
        fps_lock=True,
        # set at start-up already: patch 12 would take that over, so only on request
        spawn=state.spawn == "config" or (state.spawn == "stock" and not state.spawn_live_ticks),
        kill_list=True,
        spectator=bool(state.spectator),
        long_chat=state.long_chat in ("plain", "colour"),
        chat_colour=state.long_chat != "plain",
    )


# ---- the build ---------------------------------------------------------------------

@dataclass
class Result:
    exe_path: str                   # the patched copy
    dll_path: str                   # "" if the DLL was not needed
    changed: list                   # what WolfRAT changed, admin's words
    steps: list                     # what the admin does next
    report: sp.Report               # the Patches page's check of the copy


def plan_exe(data: bytes, state: State, choice: Choice) -> tuple:
    """(patched exe bytes, list of changes).  Raises PatchError."""
    img = Image(data)
    changed = []
    if state.admin_fix is None:
        raise PatchError("WolfRAT does not recognise this exe, so it cannot patch it.")
    if not state.admin_fix:
        _admin_fix(img)
        changed.append("Admin-port crash fix")
    ok, why = speed_allowed(state, choice.speed)
    if not ok:
        raise PatchError(why)
    if choice.speed != state.speed:
        _speed(img, choice.speed)
        changed.append(f"Server speed {choice.speed} Hz")
    hole = hole_plan(state, choice.speed)
    if hole and not state.hole_skip:
        _hole_skip(img, choice.speed)
    elif hole:
        _hole_tune(img, state.hole_settings, choice.speed)
    if hole:
        changed.append(hole)
    if not state.memory_in_dll and choice.memory != state.memory:
        _exe_memory(img, choice.memory)
        changed.append(f"Mission memory {MEMORY_WORDS[choice.memory]}")
    if (choice.laa or choice.memory == 1024) and not state.laa:
        img.set_laa()
        changed.append("Large Address Aware")
    if wants_fps_lock(choice) and not state.fps_lock:
        _fps_lock(img)
        changed.append("Frame rate lock")
    if choice.spawn and state.spawn == "stock":
        _spawn_config(img)
        changed.append("Spawn protection from game.cfg")
    if choice.kill_list and state.kill_list is False:
        _kill_list(img)
        changed.append("Kill List crash fix")
    if choice.spectator and state.spectator is False:
        _spectator_chat(img)
        changed.append("Spectator chat")
    if choice.long_chat and state.long_chat == "off":
        _long_chat(img, choice.chat_colour)
        changed.append("Long chat" + (" with colours" if choice.chat_colour else ""))
    elif choice.long_chat and state.long_chat in ("plain", "colour"):
        want = "colour" if choice.chat_colour else "plain"
        if want != state.long_chat:
            _chat_colour(img, choice.chat_colour)
            changed.append("Long chat colours " + ("on" if choice.chat_colour else "off"))
    return bytes(img.data), changed


def cfg_lines(state: State, choice: Choice) -> list:
    """game.cfg lines the chosen patches need.  WolfRAT never writes game.cfg:
    the admin adds them while the server is stopped (Dale: a running server
    holds game.cfg and it cannot be changed)."""
    lines = []
    if wants_fps_lock(choice) and sp._cfg_int(state.cfg, "lock_framerate") != 7:
        lines.append("lock_framerate = 7")
    return lines


CFG_WHY = {
    "lock_framerate": "7 = ~125 FPS; without it one CPU core runs at 100%",
}


def build(exe_path: str, choice: Choice, live: Optional[sp.LiveRead] = None) -> Result:
    """Write the patched copy (and DLL) beside the server's exe.  Raises PatchError."""
    state = read_state(exe_path, live)
    folder = os.path.dirname(exe_path)
    with open(exe_path, "rb") as handle:
        data = handle.read()
    new_exe, changed = plan_exe(data, state, choice)

    new_dll = b""
    if state.memory_in_dll and choice.memory != state.memory:
        with open(os.path.join(folder, "binkw32.dll"), "rb") as handle:
            new_dll = patch_dll_memory(handle.read(), choice.memory)
        changed.append(f"Mission memory {MEMORY_WORDS[choice.memory]}")

    lines = cfg_lines(state, choice)
    if not changed and not lines:
        raise PatchError("Your server already has everything ticked here - nothing to build.")

    exe_changed = new_exe != data
    out_exe = os.path.join(folder, OUT_EXE) if exe_changed else ""
    out_dll = os.path.join(folder, OUT_DLL) if new_dll else ""
    try:
        if out_exe:
            _write(out_exe, new_exe)
        if out_dll:
            _write(out_dll, new_dll)
    except OSError as exc:
        raise PatchError(f"Could not write into the server folder ({exc}). "
                         "Run WolfRAT as administrator if the server is under Program Files.") from None

    planned = dict(state.cfg)
    for line in lines:
        key, value = (part.strip() for part in line.split("=", 1))
        planned[key.lower()] = value
    report = _check_copy(out_exe or exe_path, new_dll, planned)
    exe_name = os.path.basename(exe_path)
    steps = ["Stop the game server."]
    if out_exe:
        steps.append(f"Rename {exe_name} to {BACKUP_EXE} (your backup).")
        steps.append(f"Rename {OUT_EXE} to {exe_name}.")
    if out_dll:
        steps.append(f"Rename binkw32.dll to {BACKUP_DLL} (your backup).")
        steps.append(f"Rename {OUT_DLL} to binkw32.dll.")
    if lines:
        said = "  and  ".join(f"{line}  ({CFG_WHY[line.split(' =')[0]]})" for line in lines)
        steps.append(f"With the server still stopped (game.cfg cannot be changed while it runs), "
                     f"open game.cfg in the server folder (next to {exe_name}) and set:  {said}. "
                     "Add a line if it is not there.")
    steps.append("Start the server. The Patches page will show the new patches.")
    return Result(out_exe, out_dll, changed, steps, report)


def _write(path: str, data: bytes) -> None:
    """Write beside, then move into place, so a half-written copy never has the name."""
    temp = path + ".part"
    with open(temp, "wb") as handle:
        handle.write(data)
    os.replace(temp, path)


def _check_copy(exe_path: str, new_dll: bytes, cfg: dict) -> sp.Report:
    """The Patches page's own check, run on the copy WolfRAT just wrote,
    with game.cfg as it will be once the admin has set the lines."""
    report = sp.check(exe_path, cfg=cfg)
    if not new_dll:
        return report
    value = dll_memory(new_dll)
    findings = tuple(
        sp.Finding(f.key, f.title, f.level, sp.ARENA[value] + ".", f.doc,
                   sp.ARENA[value].split(" (")[0]) if f.key == "memory" and value in sp.ARENA else f
        for f in report.findings)
    return sp.Report(report.exe_path, report.known, findings, sp.headline(findings, report.known))
