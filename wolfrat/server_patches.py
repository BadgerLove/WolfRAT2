"""Which of the jo-server-patches the game server's exe carries.  No Qt.

Reads the server's jointops.exe FILE (the one the running server was started
from) and game.cfg.  Every check looks at the same bytes the patch scripts in
github.com/BadgerLove/jo-server-patches assert, and answers on / off / "cannot
tell" (a build WolfRAT does not know).  Read-only: nothing is ever written.

Two answers also read the RUNNING server when WolfRAT has it open: the speed
and the mission memory.  A hook DLL (binkw32.dll) can set the memory at
start-up without touching the exe, and a swapped file only counts after a
restart, so the live value is the one in use.  Rows never name the DLL: an
admin only needs the number.

Dale 2026-09-26: "even I sometimes think 'are we on 125 hertz?'" - and most
admins are missing the admin-port crash fix.  2026-09-27: the FMJ server had
quietly dropped from 1 GB to 512 MB after a binkw32.dll swap.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from typing import Callable, Optional

# read(address, size) -> bytes, or None / an exception when it cannot
LiveRead = Callable[[int, int], Optional[bytes]]

DOCS = "https://github.com/BadgerLove/jo-server-patches/blob/main/docs/patches/"

GOOD, BAD, WARN, INFO, UNKNOWN = "good", "bad", "warn", "info", "unknown"


@dataclass(frozen=True)
class Finding:
    key: str
    title: str
    level: str          # GOOD / BAD / WARN / INFO / UNKNOWN
    detail: str         # one short line, the admin's words
    doc: str = ""       # "What is this?" page
    short: str = ""     # the headline's words, e.g. "125 Hz"


class Exe:
    """Byte reads by virtual address, from the PE section table."""

    def __init__(self, data: bytes):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("not a Windows exe")
        count = struct.unpack_from("<H", data, pe + 6)[0]
        first = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
        base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
        self.characteristics = struct.unpack_from("<H", data, pe + 22)[0]
        self._sections = []
        for i in range(count):
            vs, va, rs, ro = struct.unpack_from("<IIII", data, first + i * 40 + 8)
            self._sections.append((base + va, max(vs, rs), ro, rs))

    def read(self, va: int, size: int) -> Optional[bytes]:
        for start, span, raw_off, raw_size in self._sections:
            if start <= va and va + size <= start + span:
                rel = va - start
                if rel + size > raw_size:
                    return None
                return self.data[raw_off + rel: raw_off + rel + size]
        return None

    def is_(self, va: int, hex_bytes: str) -> bool:
        want = bytes.fromhex(hex_bytes)
        return self.read(va, len(want)) == want


def read_cfg(folder: str) -> dict:
    """game.cfg as {lower-case key: value text}; {} if missing."""
    try:
        with open(os.path.join(folder, "game.cfg"), encoding="latin-1") as handle:
            text = handle.read()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z_][\w]*)\s*=\s*\"?([^\"\r\n]*?)\"?\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def _cfg_int(cfg: dict, key: str) -> Optional[int]:
    try:
        return int(float(cfg[key]))
    except (KeyError, ValueError):
        return None


def _live(read: Optional[LiveRead], va: int, size: int) -> Optional[bytes]:
    if read is None:
        return None
    try:
        data = read(va, size)
    except Exception:
        return None
    return data if data is not None and len(data) == size else None


def _now_and_next(now: Optional[str], on_disk: Optional[str]) -> tuple:
    """(detail, value in use): the running server wins; files only count
    after a restart, so say so when they differ."""
    if now is None:
        return on_disk, on_disk
    if on_disk is None or on_disk == now:
        return now, now
    return f"{now} now - {on_disk} after the next server restart", now


# ---- the checks --------------------------------------------------------------

def _two_way(exe: Exe, va: int, off: str, on: str) -> Optional[bool]:
    if exe.is_(va, on):
        return True
    if exe.is_(va, off):
        return False
    return None


ADMIN_FIX_VA, ADMIN_FIX_ON = 0x4056E6, bytes.fromhex("c1e00690")


def admin_fix_live(live: Optional[LiveRead]) -> bool:
    """True when the running server has the fix in memory (a hook DLL can add
    it at start-up to an exe that does not carry it)."""
    return _live(live, ADMIN_FIX_VA, 4) == ADMIN_FIX_ON


def check_admin_crash(exe: Exe, live: Optional[LiveRead] = None) -> Finding:
    state = _two_way(exe, ADMIN_FIX_VA, "03c003c0", "c1e00690")
    doc = DOCS + "01-admin-port-crash-fix.md"
    title = "Admin-port crash fix"
    if state:
        return Finding("admin_crash", title, GOOD, "In. The server no longer crashes when admin tools connect.", doc)
    if state is False and admin_fix_live(live):
        return Finding("admin_crash", title, GOOD,
                       "In (added when the server starts). The server no longer crashes when "
                       "admin tools connect.", doc)
    if state is False:
        return Finding("admin_crash", title, BAD,
                       "MISSING. WolfRAT talks to this port all day; without the fix the server "
                       "can crash (the empty-server SYSDUMP).", doc)
    return Finding("admin_crash", title, UNKNOWN, "Cannot tell on this build.", doc)


def check_kill_list(exe: Exe) -> Finding:
    state = _two_way(exe, 0x504C2B, "80e201", "80e200")
    doc = DOCS + "14-spectator-chat-server.md"
    title = "Kill List crash fix"
    if state:
        return Finding("kill_list", title, GOOD, "In. Players do not crash when a spectator leaves.", doc)
    if state is False:
        return Finding("kill_list", title, WARN,
                       "Missing. Players with the Kill List open can crash when a spectator leaves.", doc)
    return Finding("kill_list", title, UNKNOWN, "Cannot tell on this build.", doc)


SPEED_VA = 0x4C4B13
RATES = {0x01: 125, 0x02: 64, 0x04: 32}
SPEED_WORDS = {125: "125 Hz", 64: "64 Hz", 32: "32 Hz"}   # no "normal speed": admins do not need it (Dale)


def server_speed(exe: Exe) -> Optional[int]:
    value = exe.read(SPEED_VA, 1)
    return RATES.get(value[0]) if value else None


def speed_site_ok(exe: Exe) -> bool:
    """The speed is `mov eax, N` (N = send hold-off) in every build.  True for
    any sane N, so an exe on a setting WolfRAT has no name for is still patchable."""
    site = exe.read(SPEED_VA - 1, 5)
    return bool(site) and site[0] == 0xB8 and site[2:] == bytes(3) and 1 <= site[1] <= 16


def hole_skip(exe: Exe) -> Optional[bool]:
    hook = exe.read(0x626954, 6)
    if hook is None:
        return None
    if hook == bytes.fromhex("8b8fb8070000"):
        return False
    if hook[0] == 0xE9:
        target = 0x626954 + 5 + struct.unpack("<i", hook[1:5])[0]
        return 0x794000 <= target < 0x795000
    return None


def check_speed(exe: Exe, live: Optional[LiveRead] = None) -> Finding:
    """Always one of 32 / 64 / 125 Hz or Unknown (Dale 2026-09-27)."""
    doc = DOCS + "07-packet-send-rate.md"
    byte = _live(live, SPEED_VA, 1)
    now = RATES.get(byte[0]) if byte else None
    on_disk = server_speed(exe)
    detail, hz = _now_and_next(SPEED_WORDS.get(now), SPEED_WORDS.get(on_disk))
    if hz is None:
        return Finding("speed", "Server speed", UNKNOWN, "Unknown.", doc, "Unknown Hz")
    level = GOOD if hz == SPEED_WORDS[125] else INFO
    return Finding("speed", "Server speed", level, detail + ".", doc, hz.split(" (")[0])


# Spaghetti's cave has two settings: queued packets before a skip, and how far
# behind the missing one must be.  8 / 64 is his original (the 64 Hz version);
# 6 / 32 is the FMJ tuning for 125 Hz (2026-07-11), where 64 packets is a 512 ms stall.
HOLE_REORDER_VA, HOLE_LAG_VA = 0x794E0A, 0x794E1A
HOLE_VERSIONS = {(8, 64): 64, (6, 32): 125}


def hole_skip_version(exe: Exe) -> Optional[tuple]:
    """(reorder, lag) settings of the hole-skip cave, None if it is not in."""
    if not hole_skip(exe):
        return None
    reorder, lag = exe.read(HOLE_REORDER_VA, 1), exe.read(HOLE_LAG_VA, 1)
    return (reorder[0], lag[0]) if reorder and lag else None


def check_hole_skip(exe: Exe) -> Finding:
    """Dale 2026-09-27: 64 Hz does not work without it, 125 Hz needs the 125 version."""
    state = hole_skip(exe)
    hz = server_speed(exe)
    doc = DOCS + "09-nwu-hole-skip.md"
    title = "Hole-skip (players do not freeze on a lost packet)"
    if state:
        settings = hole_skip_version(exe)
        made_for = HOLE_VERSIONS.get(settings)
        if made_for is None:
            return Finding("hole_skip", title, GOOD, "In (custom settings %d / %d)." % settings
                           if settings else "In.", doc)
        if hz in (64, 125) and made_for != hz:
            return Finding("hole_skip", title, WARN,
                           f"In, but the {made_for} Hz version on a {hz} Hz server. "
                           f"The Patcher can switch it to the {hz} Hz version.", doc)
        return Finding("hole_skip", title, GOOD, f"In ({made_for} Hz version).", doc)
    if state is False:
        if hz in (64, 125):
            return Finding("hole_skip", title, BAD,
                           f"MISSING at {hz} Hz. Players can freeze in place when a packet is lost.", doc)
        return Finding("hole_skip", title, INFO, "Not in - not needed at 32 Hz.", doc)
    return Finding("hole_skip", title, UNKNOWN, "Cannot tell on this build.", doc)


_FPS_ON = (
    (0x52B85E, "a1440755028935081f4e02909090909090"),
    (0x52B89F, "c1e004"),
    (0x52B8CA, "c1e204"),
    (0x52B75C, "ff1554047c00"), (0x52B798, "ff1554047c00"), (0x52B8B8, "ff1554047c00"),
    (0x52BA7E, "ff1554047c00"), (0x52BA95, "8b1d54047c00"),
)
_FPS_OFF = (
    (0x52B85E, "33c0390544075502" "8935081f4e02" "0f95c0"),
    (0x52B89F, "c1e008"),
    (0x52B8CA, "c1e208"),
)


KNOWN_FPS = {7: "~125 FPS", 8: "~112 FPS", 16: "62.5 FPS"}     # measured, patch 11's table


def fps_for(ms: int) -> str:
    if ms <= 0:
        return "no limit"
    return KNOWN_FPS.get(ms) or f"about {round(1000 / (ms + 0.9))} FPS"


def check_fps(exe: Exe, cfg: dict) -> Finding:
    doc = DOCS + "11-fps-lock-125.md"
    title = "Frame rate lock"
    lock = _cfg_int(cfg, "lock_framerate")
    unlimited = exe.is_(0x52B8B0, "9090909090909090")
    if all(exe.is_(va, b) for va, b in _FPS_ON) and not unlimited:
        # The lock reads lock_framerate as milliseconds: missing, 0 or 1 = the
        # server runs flat out and keeps one CPU core at 100% (Dale 2026-09-27).
        if lock is None:
            return Finding("fps", title, WARN, "Patched, but game.cfg has no lock_framerate line: one CPU "
                           "core runs at 100%. Set lock_framerate = 7 (~125 FPS).", doc)
        if lock <= 1:
            return Finding("fps", title, WARN, f"lock_framerate = {lock} in game.cfg: one CPU core runs "
                           "at 100%. Set lock_framerate = 7 (~125 FPS).", doc)
        return Finding("fps", title, GOOD, f"lock_framerate = {lock} in game.cfg: {fps_for(lock)}.", doc)
    if unlimited:
        return Finding("fps", title, WARN,
                       "Old 'unlimited FPS' hack: no limit, and it keeps a whole CPU core busy.", doc)
    if all(exe.is_(va, b) for va, b in _FPS_OFF):
        return Finding("fps", title, INFO, "Not patched: the game's normal 62.5 FPS.", doc)
    return Finding("fps", title, UNKNOWN, "Cannot tell on this build.", doc)


ARENA_VA = 0x4A7CE3          # the exe's start-up size (patch 04)
ARENA_SIZE_VA = 0x03342E7C    # the size in use, written once at start-up
ARENA = {0x0C000000: "192 MB (the game's normal)", 0x20000000: "512 MB", 0x40000000: "1 GB"}
# A hook DLL that sets the size at start-up: mov [ebp-128h], size; mov [esp+10h], size
_HOOK_ARENA = re.compile(rb"\xc7\x85\xd8\xfe\xff\xff(....)\xc7\x44\x24\x10\1", re.S)


def hook_arena(folder: str) -> Optional[int]:
    """The mission memory binkw32.dll sets when the server starts, None if it sets none."""
    try:
        with open(os.path.join(folder, "binkw32.dll"), "rb") as handle:
            found = _HOOK_ARENA.search(handle.read())
    except OSError:
        return None
    return struct.unpack("<I", found.group(1))[0] if found else None


def check_memory(exe: Exe, folder: str = "", live: Optional[LiveRead] = None) -> Finding:
    """192 MB / 512 MB / 1 GB or Unknown, from the running server when WolfRAT
    has it, else what the server folder will start with."""
    doc = DOCS + "04-memory-2gb.md"
    title = "Mission memory"
    raw = _live(live, ARENA_SIZE_VA, 4)
    now = struct.unpack("<I", raw)[0] if raw else None
    raw = exe.read(ARENA_VA, 4)
    on_disk = (folder and hook_arena(folder)) or (struct.unpack("<I", raw)[0] if raw else None)
    detail, size = _now_and_next(ARENA.get(now), ARENA.get(on_disk))
    if size is None:
        return Finding("memory", title, UNKNOWN, "Unknown.", doc, "Unknown memory")
    short = size.split(" (")[0]
    if size == ARENA[0x40000000] and not exe.characteristics & 0x20:
        return Finding("memory", title, WARN,
                       f"{detail}. 1 GB needs Large Address Aware, which this exe is missing.", doc, short)
    level = INFO if size == ARENA[0x0C000000] else GOOD
    return Finding("memory", title, level, detail + ".", doc, short)


def check_laa(exe: Exe) -> Finding:
    doc = DOCS + "05-large-address-aware.md"
    if exe.characteristics & 0x20:
        return Finding("laa", "Large Address Aware", GOOD, "In. The server can use more than 2 GB.", doc)
    return Finding("laa", "Large Address Aware", WARN, "Missing. The server is limited to 2 GB of memory.", doc)


SPAWN_VA = 0x516BBA


def spawn_live_ticks(live: Optional[LiveRead]) -> Optional[int]:
    """The running server's spawn protection when something other than the
    exe set it at start-up (a hook DLL rewrites the stock 620); None if not."""
    site = _live(live, SPAWN_VA, 10)
    if site is None or site[:6] != bytes.fromhex("c78124010000"):
        return None
    ticks = struct.unpack("<i", site[6:10])[0]
    return ticks if ticks != 620 and ticks > 0 else None


def check_spawn(exe: Exe, cfg: dict, live: Optional[LiveRead] = None) -> Finding:
    site = exe.read(SPAWN_VA, 10)
    title = "Spawn protection"
    if site is None:
        return Finding("spawn", title, UNKNOWN, "Cannot tell on this build.", DOCS + "03-spawn-protection.md")
    if site[0] == 0xE8:
        secs = _cfg_int(cfg, "e_spawn_protection")
        doc = DOCS + "12-spawn-protection-config.md"
        if not secs or secs <= 0:
            return Finding("spawn", title, INFO, "Set in game.cfg (e_spawn_protection), not set: the game's normal ~10 s.", doc)
        return Finding("spawn", title, GOOD, f"Set in game.cfg: e_spawn_protection = {secs} seconds.", doc)
    if site[:6] == bytes.fromhex("c78124010000"):
        ticks = struct.unpack("<i", site[6:10])[0]
        doc = DOCS + "03-spawn-protection.md"
        if ticks == 620:
            now = spawn_live_ticks(live)
            if now:
                return Finding("spawn", title, INFO,
                               f"About {now * 16 / 1000:.1f} seconds (set when the server starts).", doc)
            return Finding("spawn", title, INFO, "Not patched: the game's normal ~10 seconds.", doc)
        return Finding("spawn", title, INFO, f"Fixed in the exe: about {ticks * 16 / 1000:.1f} seconds.", doc)
    return Finding("spawn", title, UNKNOWN, "Cannot tell on this build.", DOCS + "03-spawn-protection.md")


def check_spectator(exe: Exe) -> Finding:
    doc = DOCS + "14-spectator-chat-server.md"
    first = exe.read(0x5137C7, 1)
    if first == b"\xe9":
        return Finding("spectator", "Spectator chat", GOOD, "In. Spectators' chat reaches the players.", doc)
    if first == b"\x80":
        return Finding("spectator", "Spectator chat", INFO, "Not in (optional).", doc)
    return Finding("spectator", "Spectator chat", UNKNOWN, "Cannot tell on this build.", doc)


def long_chat(exe: Exe) -> Optional[str]:
    """'colour', 'plain', 'off' or None (cannot tell)."""
    state = _two_way(exe, 0x49A93B, "e820e6ffff", "e8c0533200")
    if state is None:
        return None
    if not state:
        return "off"
    return "colour" if exe.is_(0x49A9DC, "5690909090") else "plain"


def check_long_chat(exe: Exe) -> Finding:
    doc = DOCS + "15-long-chat-server.md"
    state = long_chat(exe)
    words = {"colour": ("In, with colours: 118 characters.", GOOD),
             "plain": ("In, without colours: 118 characters.", GOOD),
             "off": ("Not in (optional): chat stops at 59 characters, no colours.", INFO)}
    if state is None:
        return Finding("long_chat", "Long chat", UNKNOWN, "Cannot tell on this build.", doc)
    text, level = words[state]
    return Finding("long_chat", "Long chat", level, text, doc)


# Keep claymores / AT mines after a respawn (19 / 20).  Each one turns a type
# test in the redeploy clean-up into a jump to a cave in the zero tail of
# `_text`: the device stays when its owner redeploys, and still goes when they
# leave the server.  (device: site, stock test, cave, cave bytes, doc, words)
KEEP_DEPLOY_CALL, KEEP_SWEEP = 0x5178D8, 0x546E00
KEEP = {
    "claymore": (0x546E8B, "3b15d07d4e02", 0x7BFF00,
                 "3b15d07d4e020f85906fd8ff817c2410dd7851000f84826fd8ffe9746fd8ff",
                 "19-keep-claymores.md", "claymores"),
    "at_mine": (0x546EAE, "3b05c07d4e02", 0x7BFF20,
                "3b05c07d4e020f85f46ed8ff817c2410dd7851000f84e66ed8ffe97b6fd8ff",
                "20-keep-at-mines.md", "AT mines"),
}
KEEP_TITLES = {"claymore": "Keep claymores", "at_mine": "Keep AT mines"}


def keep_device(exe: Exe, device: str) -> Optional[bool]:
    """True = patch 19/20 in, False = stock with room for it, None = it cannot go in
    (something else at the site, or the cave space used, as on client exes)."""
    site, stock, cave, code = KEEP[device][:4]
    here = exe.read(site, 6)
    if here == bytes.fromhex(stock):
        return False if exe.read(cave, len(code) // 2) == bytes(len(code) // 2) else None
    if (here and here[0] == 0xE9 and here[5] == 0x90
            and site + 5 + struct.unpack("<i", here[1:5])[0] == cave and exe.is_(cave, code)):
        return True
    return None


def keep_by_hook(live: Optional[LiveRead]) -> bool:
    """The running server's redeploy no longer calls the stock clean-up: a hook
    DLL (onHook's KeepClaymoresOnRespawn / KeepAvMinesOnRespawn) took it over."""
    call = _live(live, KEEP_DEPLOY_CALL, 5)
    return (call is not None and call[0] == 0xE8
            and (KEEP_DEPLOY_CALL + 5 + struct.unpack("<i", call[1:5])[0]) & 0xFFFFFFFF != KEEP_SWEEP)


def check_keep(exe: Exe, device: str, live: Optional[LiveRead] = None) -> Finding:
    doc, words = DOCS + KEEP[device][4], KEEP[device][5]
    key, title = "keep_" + device, KEEP_TITLES[device]
    state = keep_device(exe, device)
    if state:
        return Finding(key, title, GOOD, f"In. A player's {words} stay when they redeploy.", doc)
    if state is False and keep_by_hook(live):
        return Finding(key, title, INFO, f"Not in the exe. The hook DLL (onHook) handles the redeploy "
                       f"clean-up in this server, so its own setting decides about {words}.", doc)
    if state is False:
        return Finding(key, title, INFO, f"Not in (optional): {words} go when their owner redeploys.", doc)
    if exe.read(KEEP[device][0], 6) == bytes.fromhex(KEEP[device][1]):
        return Finding(key, title, INFO, f"Not in: {words} go when their owner redeploys. This exe has "
                       "no room for it (another patch uses the space).", doc)
    return Finding(key, title, UNKNOWN, "Cannot tell on this build.", doc)


# ---- live patches: in the RUNNING server, not the exe (Dale 2026-10-09) ---------

class _LiveMemory:
    """``read`` that raises instead of returning None (what the live-patch
    checks expect)."""

    def __init__(self, live: LiveRead):
        self._live = live

    def read(self, address: int, size: int) -> bytes:
        data = self._live(address, size)
        if data is None or len(data) != size:
            raise OSError("cannot read the server")
        return data


LIVE_MARK = "⚡ LIVE"


def check_live_parachute(live: Optional[LiveRead]) -> Optional[Finding]:
    """None when WolfRAT has no running server to look at."""
    from wolfrat import live_patches as lp
    if live is None or _live(live, lp.CHUTE_SITE, 1) is None:
        return None
    title = "Spacebar parachute"
    state = lp.chute_state(_LiveMemory(live))
    if state.on:
        return Finding("parachute", title, GOOD,
                       f"{LIVE_MARK}: players open it with space. "
                       "Gone when the server restarts (Live patches page).")
    if state.kind == lp.STOCK:
        return Finding("parachute", title, INFO,
                       "Not on (optional): the game's normal parachute. A live patch - "
                       "see the Live patches page.")
    return Finding("parachute", title, INFO,
                   "Changed by something else in this server (onHook / TAC?) - WolfRAT leaves it alone.")


def check_live_sharks(exe: Exe, live: Optional[LiveRead]) -> Optional[Finding]:
    """'Berserk sharks ignore each other': in the exe file, or added live."""
    from wolfrat import wildlife
    if live is None or _live(live, wildlife.KIN_LEG, 1) is None:
        return None
    title = "Sharks ignore each other"
    state = wildlife.kin_state(_LiveMemory(live))
    in_file = exe.is_(wildlife.KIN_LEG, wildlife.KIN_LEG_FIXED.hex())
    if state == wildlife.KIN_FIXED:
        if in_file:
            return Finding("sharks", title, GOOD, "In the exe.")
        return Finding("sharks", title, GOOD,
                       f"{LIVE_MARK}: added by the Wildlife sharks. Gone when the server restarts.")
    if state == wildlife.KIN_ONHOOK:
        return Finding("sharks", title, GOOD, "In (onHook).")
    if state == wildlife.KIN_RETAIL:
        return Finding("sharks", title, INFO,
                       "Not in (only needed for hunting sharks; the Wildlife page adds it live).")
    return Finding("sharks", title, UNKNOWN, "Cannot tell on this build.")


# ---- the whole report ----------------------------------------------------------

@dataclass(frozen=True)
class Report:
    exe_path: str
    known: bool
    findings: tuple
    headline: str


def live_stamp(live: Optional[LiveRead]) -> tuple:
    """The live values check() reads, so a restart is noticed without a file change."""
    from wolfrat import live_patches as lp, wildlife
    return (_live(live, SPEED_VA, 1), _live(live, ARENA_SIZE_VA, 4),
            _live(live, ADMIN_FIX_VA, 4), _live(live, SPAWN_VA, 10),
            _live(live, lp.CHUTE_SITE, 5), _live(live, wildlife.KIN_LEG, 8),
            _live(live, KEEP_DEPLOY_CALL, 5))


def check(exe_path: str, live: Optional[LiveRead] = None, cfg: Optional[dict] = None) -> Report:
    """Everything for the Server tab. Never raises on a bad file - says so.
    ``live`` reads the running server (speed + memory); None = files only.
    ``cfg`` stands in for game.cfg (the Patcher checks a copy with the lines
    the admin is about to set)."""
    try:
        with open(exe_path, "rb") as handle:
            exe = Exe(handle.read())
    except (OSError, ValueError, struct.error) as exc:
        return Report(exe_path, False, (), f"Could not read the server exe ({exc}).")
    folder = os.path.dirname(exe_path)
    cfg = read_cfg(folder) if cfg is None else cfg
    findings = (
        check_admin_crash(exe, live), check_speed(exe, live), check_hole_skip(exe), check_fps(exe, cfg),
        check_kill_list(exe), check_long_chat(exe), check_spectator(exe), check_spawn(exe, cfg, live),
        check_memory(exe, folder, live), check_laa(exe),
        check_keep(exe, "claymore", live), check_keep(exe, "at_mine", live),
    )
    known = sum(f.level != UNKNOWN for f in findings) >= 6
    # what was written into the running server (shown only when WolfRAT can read it)
    findings += tuple(f for f in (check_live_parachute(live), check_live_sharks(exe, live)) if f)
    return Report(exe_path, known, findings, headline(findings, known))


def headline(findings, known: bool = True) -> str:
    if not known:
        return "This is not a Joint Ops server exe WolfRAT recognises, so it cannot say which patches it has."
    by = {f.key: f for f in findings}
    parts = [by[key].short for key in ("speed", "memory") if key in by and by[key].short]
    fps = by.get("fps")
    if fps and fps.level == GOOD:
        parts.append(fps.detail.split(": ", 1)[-1].rstrip("."))
    bad = [f.title for f in findings if f.level == BAD]
    if bad:
        parts.append("MISSING: " + ", ".join(bad))
    elif by.get("admin_crash") and by["admin_crash"].level == GOOD:
        parts.append("crash fixes in")
    return " · ".join(parts)
