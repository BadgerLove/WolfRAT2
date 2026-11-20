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
from typing import Callable, Optional

from wolfrat import single_instance

DLL_NAME = "wolfrat_lagcomp.dll"
LOADED_PREFIX = "wolfrat_lagcomp_"
EXPORT_NAME = b"WolfRatLagComp"
MAGIC = 0x31434C57
VERSION = 4              # 4 = bots, dead bodies apart, what-if table, owner (3 = auto-tune)

SLOTS = 250
LEGACY_SLOTS = 64
BINS = 128
BIN_MS = 8
BIN_MIN = -400
BANDS = 3
BAND_LABELS = ("under 80", "80-149", "150+")   # target ping, ms (as in the helper)
TUNE_LOG = 64
TUNE_OFF = 0
TUNE_WATCH = 1
TUNE_APPLY = 2
TUNE_MODE_NAMES = {TUNE_OFF: "off", TUNE_WATCH: "watch", TUNE_APPLY: "apply"}
BOTS_LABEL = "Bots (AI)"
WHATIF_BANDS = 6         # SHOOTER ping bands: under 50, 50-79, 80-99, 100-149, 150-199, 200+
WHATIF_COLS = 8          # column 0 = plain JO; 1..7 = a rewind of ping + offset
WHATIF_PLAIN = 0x7FFFFFFF                      # the offset that means "plain JO"
WHATIF_OFFSETS = (WHATIF_PLAIN, -80, -40, 0, 40, 80, 120, 160)   # as the helper writes them
WHATIF_BAND_MIN_PING = (0, 50, 80, 100, 150, 200)
WHATIF_MIN_BULLETS = 300                       # fewer than this: "not enough yet"

# Retail 1.7.5.7 / TAC / FMJ call sites, so WolfRAT can spot onHook's own
# lag compensation before loading its helper DLL.
_RETAIL_SITES = (
    (0x4E4C59, 0x4E4670),
    (0x4EA5BF, 0x4E4A30),
    (0x4C223A, 0x4EC020),
)
_LEG_TAIL = (0x4EA5C4, b"\x83\xc4\x08\x85\xc0\x75\x2d")

DEFAULT_EXTRA_MS = 70
DEFAULT_MAX_MS = 400
DEFAULT_MIN_PING_MS = 80

STOCK = "stock"        # the game's own calls: lag compensation off
ON = "on"              # our helper's calls
OLD = "old"            # an older copy of the helper (another WolfRAT build)
FOREIGN = "foreign"    # someone else's code: left alone
UNKNOWN = "unknown"    # helper not loaded / not readable
PARTIAL = "partial"    # some calls a helper's, the rest the game's: a switch that did
                       # not finish (turn_on completes it, turn_off undoes it)


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


class _Target(ctypes.LittleEndianStructure):
    """The same slot as a target.  A close shot = a bullet that passed within 1 m
    of them or hit them; hit % = hits / close shots."""
    _pack_ = 4
    _fields_ = [("close_low", ctypes.c_uint32), ("hits_low", ctypes.c_uint32),
                ("close_lag", ctypes.c_uint32), ("hits_lag", ctypes.c_uint32),
                ("close_lag_plain", ctypes.c_uint32), ("plain_lag", ctypes.c_uint32),
                ("gained", ctypes.c_uint32), ("lost", ctypes.c_uint32)]


class _WhatIf(ctypes.LittleEndianStructure):
    """One shooter ping band of the what-if table (version 4): the same bullet
    steps judged at each rewind.  hits = on a live person; hits_human = on a
    live human player."""
    _pack_ = 4
    _fields_ = [("steps", ctypes.c_uint32),
                ("hits", ctypes.c_uint32 * WHATIF_COLS),
                ("hits_human", ctypes.c_uint32 * WHATIF_COLS)]


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
                ("players", _Player * SLOTS),
                # version 2, appended:
                ("targets", _Target * SLOTS),
                ("band_cal_total", ctypes.c_uint32 * BANDS),
                ("band_cal", (ctypes.c_uint32 * BINS) * BANDS),
                ("band_totals", _Target * BANDS),   # by the target's ping at each shot
                # version 3, appended (auto-tune of Extra + decision log):
                ("tune_mode", ctypes.c_int32),
                ("tune_value_ms", ctypes.c_int32),
                ("tune_median_ms", ctypes.c_int32),
                ("tune_have_median", ctypes.c_int32),
                ("tune_samples", ctypes.c_uint32),
                ("tune_window_mins", ctypes.c_uint32),
                ("tune_decisions", ctypes.c_uint32),
                ("tune_log", (ctypes.c_uint32 * 8) * TUNE_LOG),
                # version 4, appended (SPEC.md, 2026-11-20):
                ("bot_totals", _Target),            # AI targets, same columns as band_totals
                ("dead_hits", ctypes.c_uint32),     # decided answer hit a dead body (not in hits)
                ("dead_plain_hits", ctypes.c_uint32),
                ("near_steps", ctypes.c_uint32),    # bullet steps with a live person near
                ("tracked_persons", ctypes.c_uint32),
                ("tracked_bots", ctypes.c_uint32),
                ("low_tests", ctypes.c_uint32),     # steps from shooters under the minimum ping
                ("owner_pid", ctypes.c_uint32),     # WolfRAT writes these two: who runs it,
                ("owner_heartbeat", ctypes.c_uint32),   # and GetTickCount() each poll
                ("what_if_offset_ms", ctypes.c_int32 * WHATIF_COLS),
                ("what_if_band_min_ping", ctypes.c_int32 * WHATIF_BANDS),
                ("what_if", _WhatIf * WHATIF_BANDS)]


class _TuneStep(ctypes.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [("minute", ctypes.c_uint32), ("tick_count", ctypes.c_uint32),
                ("before_ms", ctypes.c_int32), ("after_ms", ctypes.c_int32),
                ("median_ms", ctypes.c_int32), ("have_median", ctypes.c_int32),
                ("samples", ctypes.c_uint32), ("window_mins", ctypes.c_uint32)]


SHARED_SIZE = ctypes.sizeof(_Shared)
_OFF = {name: getattr(_Shared, name).offset for name, _ in _Shared._fields_}
_MIN_SHARED_SIZE = _OFF["players"] + LEGACY_SLOTS * ctypes.sizeof(_Player)
_V2_SIZE = _OFF["tune_mode"]               # 26,344: a block this big has the target rows
_V3_SIZE = _OFF["bot_totals"]              # 28,420: a block this big has the auto-tuner
_V4_SIZE = SHARED_SIZE                     # 28,948: bots, what-if table, owner
# The helper checks the same two numbers when it is built (SPEC.md).
assert ctypes.sizeof(_WhatIf) == 68
assert _V3_SIZE == 28420, "the version 3 part of the block must not move"
assert ctypes.sizeof(_Shared) == 28948, "version 4 block is 28,948 bytes"
assert _OFF["owner_heartbeat"] == _OFF["owner_pid"] + 4


@dataclass
class Settings:
    observe: bool = False
    extra_ms: int = DEFAULT_EXTRA_MS
    max_ms: int = DEFAULT_MAX_MS
    min_ping_ms: int = DEFAULT_MIN_PING_MS
    tune_mode: int = TUNE_OFF

    def clamp(self) -> "Settings":
        return Settings(bool(self.observe), max(-200, min(300, int(self.extra_ms))),
                        max(50, min(900, int(self.max_ms))),
                        max(0, min(500, int(self.min_ping_ms))),
                        max(TUNE_OFF, min(TUNE_APPLY, int(self.tune_mode))))

    def same_core(self, other: Optional["Settings"]) -> bool:
        """True when only tune_mode changed (so counters do not need to restart)."""
        if other is None:
            return False
        return (self.observe, self.extra_ms, self.max_ms, self.min_ping_ms) == (
            other.observe, other.extra_ms, other.max_ms, other.min_ping_ms)


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
class TargetStats:
    slot: int
    name: str
    ping: int
    close_low: int          # shots from players under the minimum ping (plain JO)
    hits_low: int
    close_lag: int          # shots from rewound players, rewound answer
    hits_lag: int
    close_lag_plain: int    # the same shots, plain JO's answer
    plain_lag: int
    gained: int
    lost: int


@dataclass
class BandStats:
    """Everyone whose ping is in one band, as targets."""
    label: str
    players: int = 0
    close_low: int = 0
    hits_low: int = 0
    close_lag: int = 0
    hits_lag: int = 0
    close_lag_plain: int = 0
    plain_lag: int = 0
    gained: int = 0
    lost: int = 0
    cal_total: int = 0
    cal_median: Optional[int] = None

    @staticmethod
    def percent(hits: int, close: int) -> Optional[float]:
        return hits * 100.0 / close if close else None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "label", "players", "close_low", "hits_low", "close_lag", "hits_lag",
            "close_lag_plain", "plain_lag", "gained", "lost", "cal_total", "cal_median")}


def band_of(ping: int) -> int:
    return 0 if ping < 80 else 1 if ping < 150 else 2


@dataclass
class WhatIfRow:
    """One SHOOTER ping band of the what-if table: how many of the same bullet
    steps would have hit at each rewind (see Stats.what_if_offsets)."""
    label: str              # "under 50", "50-79", ... "200+"
    min_ping: int           # the band's lowest ping
    steps: int              # bullet steps judged (a live person was near)
    hits: list              # per column: hit a live person (player or bot)
    hits_human: list        # per column: hit a live human player

    def as_dict(self) -> dict:
        return {"band": self.label, "min_ping": self.min_ping, "steps": self.steps,
                "hits": list(self.hits), "human": list(self.hits_human)}


def what_if_band_labels(min_pings) -> list:
    """Band names from their lowest pings: (0, 50, 80) -> under 50, 50-79, 80+."""
    lows = [int(p) for p in min_pings]
    if any(b <= a for a, b in zip(lows, lows[1:])):
        lows = list(WHATIF_BAND_MIN_PING[:len(lows)])    # not filled in: the helper's own
    out = []
    for i, low in enumerate(lows):
        if i + 1 == len(lows):
            out.append(f"{low}+")
        elif i == 0 and low <= 0:
            out.append(f"under {lows[1]}")
        else:
            out.append(f"{low}-{lows[i + 1] - 1}")
    return out


def what_if_column_label(offset_ms: int) -> str:
    return "Plain JO" if offset_ms == WHATIF_PLAIN else "ping %+d" % offset_ms


@dataclass
class TuneStep:
    minute: int
    tick_count: int
    before_ms: int
    after_ms: int
    median_ms: Optional[int]
    samples: int
    window_mins: int

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "minute", "tick_count", "before_ms", "after_ms",
            "median_ms", "samples", "window_mins")}


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
    has_targets: bool = False              # helper version 2 or later
    targets: list = field(default_factory=list)
    bands: list = field(default_factory=list)
    has_tuner: bool = False                # helper version 3 or later
    tune_mode: int = TUNE_OFF
    tune_value_ms: int = DEFAULT_EXTRA_MS
    tune_median_ms: Optional[int] = None
    tune_samples: int = 0
    tune_window_mins: int = 0
    tune_decisions: int = 0
    tune_log: list = field(default_factory=list)
    has_v4: bool = False                   # helper version 4 or later
    bot_totals: Optional[BandStats] = None     # AI soldiers as targets (label "Bots (AI)")
    dead_hits: int = 0                     # hits on dead bodies: counted apart, not in hits
    dead_plain_hits: int = 0
    near_steps: int = 0
    low_tests: int = 0
    tracked_persons: int = 0               # people the helper follows now (players + bots)
    tracked_bots: int = 0
    what_if: list = field(default_factory=list)            # WhatIfRow, one per shooter band
    what_if_offsets: list = field(default_factory=list)    # ms per column; WHATIF_PLAIN = plain JO

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

    def what_if_best(self, min_ping_ms: int = 0) -> tuple:
        """(bullets, best columns) over the shooter bands at or above the
        minimum ping: the rewinds that would have scored the most hits.  Under
        WHATIF_MIN_BULLETS bullets the columns are empty (not enough yet)."""
        rows = [r for r in self.what_if if r.min_ping >= min_ping_ms] or self.what_if[-1:]
        bullets = sum(r.steps for r in rows)
        totals = [sum(r.hits[c] for r in rows) for c in range(len(self.what_if_offsets))]
        if bullets < WHATIF_MIN_BULLETS or not totals or not max(totals):
            return bullets, []
        return bullets, [c for c, n in enumerate(totals) if n == max(totals)]

    def what_if_compact(self) -> dict:
        """The what-if table for the history and the event log: bands with
        bullets only; offset None = plain JO."""
        return {"offsets": [None if o == WHATIF_PLAIN else o for o in self.what_if_offsets],
                "rows": [r.as_dict() for r in self.what_if if r.steps]}


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
    has_v4: bool = False              # this helper's block is version 4 or later
    owner_pid: int = 0                # the WolfRAT that says it runs it (v4; 0 = nobody yet)
    owner_age_ms: Optional[int] = None    # how long ago it last said so (None = never)
    helper_dir: str = ""              # OLD only: the folder that helper was loaded from

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
    ver, sz = struct.unpack_from("<II", raw, 4)
    has_targets = len(raw) >= _V2_SIZE and ver >= 2 and sz >= _V2_SIZE
    has_tuner = len(raw) >= _V3_SIZE and ver >= 3 and sz >= _V3_SIZE
    has_v4 = len(raw) >= _V4_SIZE and ver >= 4 and sz >= _V4_SIZE
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
        saved_mode = shared.tune_mode
        ctypes.memset(ctypes.addressof(shared) + _OFF["targets"], 0,
                      _V3_SIZE - _OFF["targets"])
        shared.tune_mode = saved_mode
        shared.tune_value_ms = shared.extra_ms
        shared.stats_since = now_ticks
        # Version 4: the counters go; the owner, the column offsets, the band
        # pings and who is tracked now stay (as the helper's own reset does).
        ctypes.memset(ctypes.addressof(shared) + _OFF["bot_totals"], 0, ctypes.sizeof(_Target))
        shared.dead_hits = shared.dead_plain_hits = shared.near_steps = shared.low_tests = 0
        ctypes.memset(ctypes.addressof(shared) + _OFF["what_if"], 0,
                      ctypes.sizeof(_WhatIf) * WHATIF_BANDS)
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
    stats.has_targets = has_targets
    if has_targets:
        bands = [BandStats(label) for label in BAND_LABELS]
        for p in stats.players:
            t = shared.targets[p.slot]
            stats.targets.append(TargetStats(p.slot, p.name, p.ping, t.close_low, t.hits_low,
                                             t.close_lag, t.hits_lag, t.close_lag_plain,
                                             t.plain_lag, t.gained, t.lost))
            bands[band_of(p.ping)].players += 1        # on now
        for i, b in enumerate(bands):
            # The helper's own totals: kept when a player leaves.
            t = shared.band_totals[i]
            for k, _ in _Target._fields_:
                setattr(b, k, getattr(t, k))
            b.cal_total = shared.band_cal_total[i]
            b.cal_median = histogram_quantiles(list(shared.band_cal[i]))[1]
        stats.bands = bands
    stats.has_tuner = has_tuner
    if has_tuner:
        stats.tune_mode = int(shared.tune_mode)
        stats.tune_value_ms = int(shared.tune_value_ms)
        stats.tune_median_ms = int(shared.tune_median_ms) if shared.tune_have_median else None
        stats.tune_samples = int(shared.tune_samples)
        stats.tune_window_mins = int(shared.tune_window_mins)
        stats.tune_decisions = int(shared.tune_decisions)
        steps = []
        raw_log = bytes(shared.tune_log)
        step_sz = ctypes.sizeof(_TuneStep)
        for i in range(TUNE_LOG):
            st = _TuneStep.from_buffer_copy(raw_log[i * step_sz:(i + 1) * step_sz])
            if st.minute > 0:
                steps.append(TuneStep(
                    int(st.minute), int(st.tick_count), int(st.before_ms), int(st.after_ms),
                    int(st.median_ms) if st.have_median else None,
                    int(st.samples), int(st.window_mins)))
        steps.sort(key=lambda x: x.minute)
        stats.tune_log = steps
    stats.has_v4 = has_v4
    if has_v4:
        bots = BandStats(BOTS_LABEL, players=int(shared.tracked_bots))
        for k, _ in _Target._fields_:
            setattr(bots, k, getattr(shared.bot_totals, k))
        stats.bot_totals = bots
        stats.dead_hits = int(shared.dead_hits)
        stats.dead_plain_hits = int(shared.dead_plain_hits)
        stats.near_steps = int(shared.near_steps)
        stats.low_tests = int(shared.low_tests)
        stats.tracked_persons = int(shared.tracked_persons)
        stats.tracked_bots = int(shared.tracked_bots)
        stats.what_if_offsets = [int(o) for o in shared.what_if_offset_ms]
        if len(set(stats.what_if_offsets)) < WHATIF_COLS:
            stats.what_if_offsets = list(WHATIF_OFFSETS)      # not filled in: the helper's own
        lows = [int(p) for p in shared.what_if_band_min_ping]
        if any(b <= a for a, b in zip(lows, lows[1:])):
            lows = list(WHATIF_BAND_MIN_PING)
        for label, low, w in zip(what_if_band_labels(lows), lows, shared.what_if):
            stats.what_if.append(WhatIfRow(label, low, int(w.steps), [int(n) for n in w.hits],
                                           [int(n) for n in w.hits_human]))
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


_bundled_bytes: Optional[bytes] = None   # read once: the temp folder can vanish later


def _helper_bytes() -> bytes:
    """The bundled helper, read on first use and kept.  A long-running WolfRAT
    cannot count on its own temp folder: a temp clean-up deleted the helper from
    a live one (2026-10-23) and the next server restart could not load it."""
    global _bundled_bytes
    if _bundled_bytes is None:
        src = bundled_dll_path()
        if not src:
            raise LagCompError("WolfRAT's lag compensation helper is missing from this build.")
        with open(src, "rb") as fh:
            _bundled_bytes = fh.read()
    return _bundled_bytes


def materialize_dll(data_dir: str) -> tuple[str, str, bytes]:
    """Copy the bundled helper to data_dir/wolfrat_lagcomp_<sha8>.dll (a copy the
    server still holds is never in the way).  Returns (path, file name, bytes)."""
    data = _helper_bytes()
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
_WAIT_OBJECT_0 = 0
LOAD_WAIT_MS = 15000     # how long the server gets to load the helper


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
        k.K32GetModuleFileNameExW.argtypes = k.K32GetModuleBaseNameW.argtypes
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

    def module_dir(self, base: int) -> str:
        """The folder a loaded module's file is in ('' when Windows will not say).
        A helper sits in the data folder of the WolfRAT that loaded it."""
        name = ctypes.create_unicode_buffer(1024)
        if not self._k.K32GetModuleFileNameExW(self._h, ctypes.c_void_p(base), name, 1024):
            return ""
        return os.path.dirname(name.value)

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
        free_path = True
        try:
            self.write(int(mem), data)
            thread = self._k.CreateRemoteThread(self._h, None, 0, ctypes.c_void_p(load),
                                                ctypes.c_void_p(mem), 0, None)
            if not thread:
                raise LagCompError("Could not start the helper in the server "
                                   "(error %d; run WolfRAT as administrator?)." % ctypes.get_last_error())
            try:
                if self._k.WaitForSingleObject(thread, LOAD_WAIT_MS) != _WAIT_OBJECT_0:
                    # Not finished (a frozen or very busy server; review P3).  The
                    # exit code would read 259 = "still running", which looked like
                    # a loaded helper.  And the path must stay where it is: the
                    # server may not have read it yet (a few bytes, left behind).
                    free_path = False
                    raise LagCompError("The server took too long to load WolfRAT's lag "
                                       "compensation helper (is it frozen or very busy?).")
                code = ctypes.c_uint32()
                self._k.GetExitCodeThread(thread, ctypes.byref(code))
            finally:
                self._k.CloseHandle(thread)
        finally:
            if free_path:
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


def _read_block(proc: ServerProcess, address: int) -> bytes:
    """The helper's block, as big as it says it is (an older helper's block is
    smaller, and reading past it can fail), padded to SHARED_SIZE."""
    try:
        return proc.read(address, SHARED_SIZE)
    except LagCompError:
        pass
    size = struct.unpack("<I", proc.read(address + 8, 4))[0]
    size = max(_MIN_SHARED_SIZE, min(size, SHARED_SIZE))
    try:
        raw = proc.read(address, size)
    except LagCompError:
        raw = proc.read(address, _MIN_SHARED_SIZE)
    return raw + b"\0" * (SHARED_SIZE - len(raw))


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
            block = _Shared.from_buffer_copy(_read_block(proc, address))
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


def _calls(proc: ServerProcess, mine: Optional[_Helper], helpers: list) -> tuple:
    """(kind, owner): what the three calls point at now, and the loaded helper
    whose wrappers they are (None for the game's own calls or someone else's).

    Any helper that fits this exe knows where the calls are, so this works when
    this build's own helper is not loaded (2026-11-19 review, B3)."""
    usable = [h for h in helpers if h.block.status == 1]
    ref = mine if (mine is not None and mine.block.status == 1) else (usable[0] if usable else None)
    if ref is None:
        try:
            if proc.read(_LEG_TAIL[0], len(_LEG_TAIL[1])) == _LEG_TAIL[1]:
                targets = [_call_target(proc, site) for site, _ in _RETAIL_SITES]
                if None not in targets and targets != [func for _, func in _RETAIL_SITES]:
                    return FOREIGN, None
        except LagCompError:
            pass
        return UNKNOWN, None
    targets = [_call_target(proc, site) for site, _, _ in _sites(ref.block)]
    if targets == [f for _, f, _ in _sites(ref.block)]:
        return STOCK, None
    # This build's helper first, then the others.
    ordered = sorted(usable, key=lambda h: h is not mine)
    for h in ordered:
        if targets == [w for _, _, w in _sites(h.block)]:
            return (ON if h is mine else OLD), h
    # Half switched (review P2): every call is either the game's own or ONE
    # helper's wrapper.  A person test wrapped without the bone test loses
    # hits, so this must be repairable, not refused as someone else's code.
    for h in ordered:
        if all(t in (f, w) for t, (_, f, w) in zip(targets, _sites(h.block))):
            return PARTIAL, h
    return FOREIGN, None


def _classify(proc: ServerProcess, mine: Optional[_Helper], helpers: list) -> str:
    """What the three calls point at now."""
    return _calls(proc, mine, helpers)[0]


def _call_bytes(site: int, target: int) -> bytes:
    return b"\xe8" + struct.pack("<i", target - (site + 5))


def _switch_calls(proc: ServerProcess, memory, changes: list) -> None:
    """Point call sites at new targets (``[(site, target), ...]``): all of them
    or none (review P2).

    The bytes there now are read first.  ``memory.write_code`` pauses the
    server for all the writes together and, given those bytes, puts back the
    sites it had already changed if a later one fails - before the server runs
    again.  Raises LagCompError saying which of the two it ended as."""
    before = [(site, proc.read(site, 5)) for site, _ in changes]
    wanted = [(site, _call_bytes(site, target)) for site, target in changes]
    todo = [(new, old) for new, old in zip(wanted, before) if new[1] != old[1]]
    if not todo:
        return
    try:
        memory.write_code([new for new, _ in todo], restore=[old for _, old in todo])
    except LagCompError:
        raise
    except Exception as exc:
        try:
            as_it_was = all(proc.read(site, 5) == old for site, old in before)
        except LagCompError:
            as_it_was = False
        raise LagCompError(
            "%s %s" % (str(exc) or "Could not change the game's hit code.",
                       "The game's hit code is as it was." if as_it_was else
                       "The change is half done; WolfRAT finishes it on the next try.")) from exc


def read_state(proc: ServerProcess, wanted_name: Optional[str] = None) -> State:
    helpers = _helpers(proc)
    mine = next((h for h in helpers if h.name == wanted_name), None) if wanted_name else None
    if mine is None and helpers and wanted_name is None:
        mine = next((h for h in helpers if _classify(proc, h, helpers) == ON), helpers[-1])
    kind, owner = _calls(proc, mine, helpers)
    # Another build's helper has the calls: lag compensation is ON, from that
    # helper.  Show its block (its settings and numbers are the live ones), so
    # the page says so and unticking the box can switch it off.  Before, with
    # this build's helper not loaded, this read as "off" (review B3).
    if kind in (OLD, PARTIAL) and owner is not None:
        mine = owner
    if mine is None:
        return State(kind if kind in (FOREIGN, STOCK) else UNKNOWN)
    block, stats = parse_stats(_read_block(proc, mine.shared))
    alive = ((_tick_count() - block.heartbeat) & 0xFFFFFFFF) < 3000 if block.heartbeat else False
    settings = Settings(bool(block.observe), block.extra_ms, block.max_ms, block.min_ping_ms,
                        block.tune_mode if stats.has_tuner else TUNE_OFF)
    owner_pid, owner_age = 0, None
    if stats.has_v4:
        owner_pid = int(block.owner_pid)
        if block.owner_heartbeat:
            owner_age = (_tick_count() - block.owner_heartbeat) & 0xFFFFFFFF
    helper_dir = ""
    if kind == OLD:
        try:
            helper_dir = proc.module_dir(mine.base)
        except Exception:
            helper_dir = ""
    return State(kind, mine.base, mine.shared, block.status == 1,
                 block.error.split(b"\0")[0].decode("latin-1"), bool(block.observe),
                 settings, stats, alive, stats.has_v4, owner_pid, owner_age, helper_dir)


# ------------------------------------------------- one WolfRAT runs it (P1)
# Two WolfRATs with different helper builds on one server each saw the other's
# helper as "another version" and took the calls back, restarting the counters
# every time.  From version 4 the WolfRAT that runs a helper writes its process
# id and the time into the block on every poll; another WolfRAT never takes
# over BY ITSELF from one that said so in the last 30 s and is still running.
OWNER_FRESH_MS = 30000


def stamp_owner(memory, state: Optional[State], pid: Optional[int] = None,
                now_ticks: Optional[int] = None) -> bool:
    """Say in the helper's block that this WolfRAT runs it (process id + tick
    count, one 8-byte write).  Only this build's helper while it is on, and only
    a version 4 block: an older block has no room for it."""
    if state is None or not state.on or not state.shared or not state.has_v4:
        return False
    ticks = _tick_count() if now_ticks is None else now_ticks
    try:
        memory.write(state.shared + _OFF["owner_pid"],
                     struct.pack("<II", os.getpid() if pid is None else pid,
                                 ticks & 0xFFFFFFFF))
    except Exception:
        return False
    return True


def managed_elsewhere(state: Optional[State], own_pid: Optional[int] = None,
                      pid_running: Optional[Callable[[int], bool]] = None,
                      other_wolfrat: Optional[Callable[[], bool]] = None,
                      own_dir: Optional[str] = None) -> bool:
    """True when another WolfRAT that is running right now looks after the
    helper that has the calls (``state.kind`` OLD).

    Version 4 block: its WolfRAT's process id is not ours, it said so less than
    30 s ago, and that process is still running.

    Older block (no owner in it): any other WolfRAT running on this PC counts -
    unless that helper was loaded from this WolfRAT's own data folder
    (``own_dir``).  Then this WolfRAT put it there before it was updated, and
    another WolfRAT open on the PC (for another server) has nothing to do with
    it: the normal case after an update on a PC that runs two servers."""
    if state is None or state.kind != OLD:
        return False
    if not state.has_v4:
        if own_dir and state.helper_dir and (
                single_instance.folder_key(state.helper_dir)
                == single_instance.folder_key(own_dir)):
            return False
        return bool((other_wolfrat or single_instance.other_instance_running)())
    me = os.getpid() if own_pid is None else own_pid
    if not state.owner_pid or state.owner_pid == me:
        return False
    if state.owner_age_ms is None or state.owner_age_ms >= OWNER_FRESH_MS:
        return False
    return bool((pid_running or single_instance.pid_running)(state.owner_pid))


# ------------------------------------------------------------- on and off
def _write_settings(proc: ServerProcess, shared: int, settings: Settings, enabled: bool) -> None:
    s = settings.clamp()
    proc.write(shared + _OFF["observe"], struct.pack("<iiii", int(s.observe), s.extra_ms,
                                                     s.max_ms, s.min_ping_ms))
    try:
        ver, sz = struct.unpack("<II", proc.read(shared + 4, 8))
        if ver >= 3 and sz >= _V3_SIZE:
            proc.write(shared + _OFF["tune_mode"], struct.pack("<i", int(s.tune_mode)))
    except LagCompError:
        pass
    proc.write(shared + _OFF["enabled"], struct.pack("<i", 1 if enabled else 0))


def apply_settings(pid: int, settings: Settings, wanted_name: Optional[str] = None) -> State:
    """New settings for a helper that is already on.  Takes effect at once."""
    with ServerProcess(pid) as proc:
        state = read_state(proc, wanted_name)
        if not state.shared:
            raise LagCompError("Lag compensation is not loaded in this server.")
        # OLD = another build's helper has the calls and state.shared is ITS
        # block: it is running, so it must stay enabled.
        _write_settings(proc, state.shared, settings, state.on or state.kind == OLD)
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
            # Another helper's calls go straight over to this one, and a half
            # done switch is completed: all three calls or none.
            _switch_calls(proc, memory, [(site, wrap) for site, _, wrap in _sites(mine.block)])
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
        # The helper whose wrappers are in the calls says where the game's own
        # functions are - whichever WolfRAT build loaded it.
        kind, owner = _calls(proc, None, helpers)
        if kind == FOREIGN:
            raise LagCompError("Something else has changed the game's hit code in this server "
                               "- WolfRAT left it alone.")
        if owner is None:
            # The game's own calls already (or no helper that fits this exe).
            for h in helpers:
                _write_settings(proc, h.shared, Settings(), False)
            return read_state(proc)
        # On, or half switched: the game's own three calls back, all or none.
        _switch_calls(proc, memory, [(site, func) for site, func, _ in _sites(owner.block)])
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


def describe_setting(s: Settings, live_extra_ms: Optional[int] = None) -> str:
    sign = "+" if s.extra_ms >= 0 else ""
    if s.tune_mode == TUNE_APPLY:
        if live_extra_ms is not None:
            lsign = "+" if live_extra_ms >= 0 else ""
            extra_part = f"ping {lsign}{live_extra_ms} ms (auto from {sign}{s.extra_ms})"
        else:
            extra_part = f"ping {sign}{s.extra_ms} ms (auto-tune)"
    elif s.tune_mode == TUNE_WATCH:
        if live_extra_ms is not None:
            lsign = "+" if live_extra_ms >= 0 else ""
            extra_part = f"ping {sign}{s.extra_ms} ms (auto-watch: {lsign}{live_extra_ms})"
        else:
            extra_part = f"ping {sign}{s.extra_ms} ms (auto-watch)"
    else:
        extra_part = f"ping {sign}{s.extra_ms} ms"
    text = f"{extra_part}, none under {s.min_ping_ms}, max {s.max_ms}"
    return ("watch only, " + text) if s.observe else text


# ------------------------------------------------------------- !mycomp
MYCOMP_OFF = "Lag comp is off on this server"


def find_player_stats(stats: Optional[Stats], name: str) -> Optional[PlayerStats]:
    """The helper's row for this player name: exact (any case) first, then the
    only row whose name contains it."""
    if not stats or not name:
        return None
    want = name.strip().lower()
    for p in stats.players:
        if p.name.strip().lower() == want:
            return p
    partial = [p for p in stats.players if want in p.name.lower()]
    return partial[0] if len(partial) == 1 else None


def _fit_name(name: str, rest: str, limit: int) -> Optional[str]:
    """name + rest within limit, cutting the name if it must (never below 6)."""
    room = limit - len(rest)
    if room >= len(name):
        return name + rest
    if room >= 6:
        return name[:room] + rest
    return None


def mycomp_line(state: Optional[State], name: str, limit: int = 118) -> str:
    """One chat line for !mycomp: what lag compensation is doing for this player.

    Counts are the helper's own row for them: since they joined (a row starts
    afresh when they leave or change name), or since the counters last started
    again (switched on, settings changed, the tab's Reset).  A map change does
    not clear it (2026-10-31..11-10 snapshots: 3 drops in 51,285 carried pairs).

        BadgerLove P142 R212 Hits 48 +9 comp (+23%)
        BadgerLove P45 No comp needed (under 80)

    "No comp needed" only for a player with no rewound bullets this session, a
    rewind of 0 now and a minimum ping above 0.  A player near the minimum ping
    who was rewound for most of the session gets their numbers, even in a
    second when their ping is under it (review N5).
    """
    # ON, or on from another WolfRAT version's helper (its numbers are the live ones).
    if state is None or state.kind not in (ON, OLD) or not state.stats or not state.settings:
        return MYCOMP_OFF
    row = find_player_stats(state.stats, name)
    shown = (row.name if row else name).strip() or name
    if row is None:
        return _fit_name(shown, ": no lag comp numbers yet", limit) or MYCOMP_OFF
    if state.observe or state.settings.observe:
        return _fit_name(shown, f" P{row.ping} Lag comp is watch-only (no hits changed)",
                         limit) or _fit_name(shown, f" P{row.ping} watch-only", limit) or MYCOMP_OFF
    minimum = state.settings.min_ping_ms
    if row.tests <= 0 and row.rewind_ms <= 0 and minimum > 0:
        return (_fit_name(shown, f" P{row.ping} No comp needed (under {minimum})", limit)
                or _fit_name(shown, f" P{row.ping} no comp", limit) or MYCOMP_OFF)
    net = row.hits - row.plain_hits
    pct = f" ({net * 100.0 / row.plain_hits:+.0f}%)" if row.plain_hits else ""
    long = f" P{row.ping} R{row.rewind_ms} Hits {row.hits} {net:+d} comp{pct}"
    short = f" P{row.ping} R{row.rewind_ms} {row.hits}h {net:+d}{pct}"
    return _fit_name(shown, long, limit) or _fit_name(shown, short, limit) or MYCOMP_OFF


def v4_record(stats: Stats) -> dict:
    """What a version 4 helper adds, for a history row or a snapshot line: the
    bots row, dead bodies, near steps and the what-if table.  Older rows and
    lines simply do not have these keys."""
    return {"bots": stats.bot_totals.as_dict() if stats.bot_totals else None,
            "dead_hits": stats.dead_hits, "dead_plain_hits": stats.dead_plain_hits,
            "near_steps": stats.near_steps, "low_tests": stats.low_tests,
            "tracked_persons": stats.tracked_persons, "tracked_bots": stats.tracked_bots,
            "what_if": stats.what_if_compact()}


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
        live_extra = stats.tune_value_ms if stats.has_tuner else None
        row = {"key": key,
               "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(now - stats.seconds)),
               "minutes": round(stats.seconds / 60.0),
               "setting": describe_setting(settings, live_extra),
               "hits": stats.hits, "plain_hits": stats.plain_hits,
               "gained": stats.gained, "lost": stats.lost,
               "cal_total": stats.cal_total, "cal_median": stats.cal_median}
        if stats.has_tuner:
            row["tune_mode"] = TUNE_MODE_NAMES.get(stats.tune_mode, "off")
            row["tune_value_ms"] = stats.tune_value_ms
            row["tune_median_ms"] = stats.tune_median_ms
            row["tune_samples"] = stats.tune_samples
            row["tune_decisions"] = stats.tune_decisions
            row["tune_log"] = [st.as_dict() for st in stats.tune_log]
        if stats.has_v4:
            row.update(v4_record(stats))
        if stats.has_targets:
            row["bands"] = [b.as_dict() for b in stats.bands]
            # Players who left keep their last numbers (the helper drops their row).
            targets = {t.name: t.__dict__.copy() for t in stats.targets}
            for old in self.rows:
                if old.get("key") == key:
                    for t in old.get("targets", []):
                        name = t.get("name")
                        if name in targets:
                            self._carry(targets[name], t)
                        else:
                            targets[name] = dict(t, away=True)
            row["targets"] = sorted(targets.values(),
                                    key=lambda t: -(t["close_low"] + t["close_lag"]))[:64]
        for i, old in enumerate(self.rows):
            if old.get("key") == key:
                self.rows[i] = row
                break
        else:
            self.rows.append(row)
            self.rows = self.rows[-self.KEEP:]
        return True

    _COUNTERS = tuple(name for name, _ in _Target._fields_)

    @classmethod
    def _carry(cls, new: dict, old: dict) -> None:
        """A player who left and came back within one session keeps the SUM of
        their numbers (review N5): the helper starts their row from zero, and
        the newest, smaller numbers used to replace the old ones.

        ``new`` is the helper's row now, ``old`` what the session row holds.
        ``earlier`` in a stored row = the part from their earlier visits.
        They came back if they were away at the last look, or a number went
        down."""
        earlier = old.get("earlier") or {}
        visit = {k: old.get(k, 0) - earlier.get(k, 0) for k in cls._COUNTERS}
        if old.get("away") or any(new.get(k, 0) < visit[k] for k in cls._COUNTERS):
            earlier = {k: old.get(k, 0) for k in cls._COUNTERS}
        if any(earlier.values()):
            for k in cls._COUNTERS:
                new[k] = new.get(k, 0) + earlier.get(k, 0)
            new["earlier"] = earlier

    def save(self) -> None:
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            self._json.dump(self.rows, fh, indent=1)
        os.replace(tmp, self.path)


class EventLogger:
    """Timestamped event and auto-tune log for WolfRAT2 (human .log + structured .jsonl)."""

    MAX_LINES = 200
    TAIL_BYTES = 8 * 1024 * 1024     # how much of the .jsonl is read back for _logged_steps

    def __init__(self, base_dir: str):
        import json
        self._json = json
        self.log_path = os.path.join(base_dir, "wolfrat_lag_comp_events.log")
        self.jsonl_path = os.path.join(base_dir, "wolfrat_lag_comp_events.jsonl")
        self.lines: list[str] = []
        self._last_session_key: Optional[str] = None
        self._last_tune_minute: int = 0
        self._logged: set = set()        # (minute, tick_count) already in the .jsonl
        try:
            if os.path.exists(self.log_path):
                with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                    self.lines = [ln.rstrip("\r\n") for ln in fh.readlines()[-self.MAX_LINES:] if ln.strip()]
        except Exception:
            self.lines = []

    @staticmethod
    def _stamp(now: Optional[float] = None) -> str:
        import time
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if now is None else now))

    def _append(self, line: str, payload: dict) -> None:
        self.lines.append(line)
        if len(self.lines) > self.MAX_LINES:
            self.lines = self.lines[-self.MAX_LINES:]
        try:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(self._json.dumps(payload) + "\n")
        except Exception:
            pass

    def note(self, message: str, kind: str = "event", now: Optional[float] = None, **extra) -> str:
        ts = self._stamp(now)
        line = f"[{ts}] {message}"
        payload = {"time": ts, "kind": kind, "message": message}
        payload.update(extra)
        self._append(line, payload)
        return line

    def _logged_steps(self, pid: int) -> set:
        """(minute, tick_count) of the auto-tune steps already written to the
        .jsonl for this server process, read from the end of the file.

        The helper keeps its last 64 decisions.  A WolfRAT that has just started
        knows nothing of what the one before it logged, and wrote all of them
        again as new lines stamped "now" (review B4).  The tick count is the
        server's own clock at the decision, so the same decision is recognised
        and a later session's minute 1 is not."""
        seen: set = set()
        try:
            with open(self.jsonl_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - self.TAIL_BYTES))
                data = fh.read()
        except OSError:
            return seen
        for raw in data.split(b"\n"):
            if b'"tune_step"' not in raw:
                continue
            try:
                row = self._json.loads(raw.decode("utf-8"))
                if row.get("kind") == "tune_step" and row.get("pid") == pid:
                    seen.add((int(row["step"]["minute"]), int(row["step"]["tick_count"])))
            except Exception:
                continue                 # a line cut in half by the seek, or not ours
        return seen

    def record_poll(self, pid: int, stats: Stats, settings: Settings,
                    include_snapshot: bool = False, now: Optional[float] = None) -> list[str]:
        """Log any new per-minute auto-tune decisions, plus an optional periodic snapshot."""
        added: list[str] = []
        session_key = f"{pid}-{stats.since:08x}"
        if session_key != self._last_session_key:
            self._last_session_key = session_key
            self._last_tune_minute = 0
            self._logged = self._logged_steps(pid)
        mode_name = TUNE_MODE_NAMES.get(stats.tune_mode if stats.has_tuner else settings.tune_mode, "off")
        if stats.has_tuner and stats.tune_log:
            for st in stats.tune_log:
                if st.minute <= self._last_tune_minute:
                    continue
                self._last_tune_minute = st.minute
                if (st.minute, st.tick_count) in self._logged:
                    continue             # logged before WolfRAT restarted
                if st.median_ms is None:
                    msg = (f"Auto-tune min #{st.minute} [{mode_name}]: holding Extra {st.after_ms:+d} ms "
                           f"(waiting for 450 close shots in 30 min: {st.samples} in {st.window_mins} min)")
                elif st.after_ms != st.before_ms:
                    msg = (f"Auto-tune min #{st.minute} [{mode_name}]: Extra {st.before_ms:+d} -> {st.after_ms:+d} ms "
                           f"(rolling best {st.median_ms:+d} ms from {st.samples} shots / {st.window_mins} min)")
                else:
                    msg = (f"Auto-tune min #{st.minute} [{mode_name}]: kept Extra {st.after_ms:+d} ms "
                           f"(rolling best {st.median_ms:+d} ms from {st.samples} shots / {st.window_mins} min)")
                added.append(self.note(msg, kind="tune_step", now=now, pid=pid,
                                       tune_mode=mode_name, step=st.as_dict()))
        if include_snapshot and stats.tests > 0:
            gain = stats.gain_percent
            gain_str = "-" if gain is None else f"{gain:+.0f}%"
            best_str = "-" if stats.cal_median is None else f"{stats.cal_median:+d} ms"
            live_extra = stats.tune_value_ms if (stats.has_tuner and settings.tune_mode == TUNE_APPLY) else settings.extra_ms
            band_parts = []
            for b in stats.bands:
                lag_pct = b.percent(b.hits_lag, b.close_lag)
                plain_pct = b.percent(b.plain_lag, b.close_lag_plain)
                b_best = "-" if b.cal_median is None else f"{b.cal_median:+d}"
                if lag_pct is not None or b.close_low:
                    l_str = "-" if lag_pct is None else f"{lag_pct:.0f}%"
                    p_str = "-" if plain_pct is None else f"{plain_pct:.0f}%"
                    band_parts.append(f"{b.label}: lag {l_str}/plain {p_str} (best {b_best})")
            bands_summary = (" | Bands " + ", ".join(band_parts)) if band_parts else ""
            bots = stats.bot_totals if stats.has_v4 else None
            if bots and (bots.close_lag or bots.close_low):
                lag_pct = bots.percent(bots.hits_lag, bots.close_lag)
                plain_pct = bots.percent(bots.plain_lag, bots.close_lag_plain)
                bands_summary += (" | bots: lag " + ("-" if lag_pct is None else f"{lag_pct:.0f}%")
                                  + "/plain " + ("-" if plain_pct is None else f"{plain_pct:.0f}%"))
            msg = (f"Snapshot ({stats.seconds // 60}m): {stats.hits} hits vs {stats.plain_hits} plain ({gain_str}), "
                   f"saved {stats.gained} / cost {stats.lost}, best {best_str} ({stats.cal_total} shots), "
                   f"active Extra {live_extra:+d} ms [{mode_name}]{bands_summary}")
            added.append(self.note(
                msg, kind="snapshot", now=now, pid=pid,
                setting=describe_setting(settings, stats.tune_value_ms if stats.has_tuner else None),
                tick_ms=round(stats.tick_us / 1000.0, 2),
                seconds=stats.seconds, tests=stats.tests,
                hits=stats.hits, plain_hits=stats.plain_hits,
                gained=stats.gained, lost=stats.lost,
                cal_total=stats.cal_total, cal_median=stats.cal_median,
                cal_low=stats.cal_low, cal_high=stats.cal_high,
                tune_mode=mode_name,
                tune_value_ms=stats.tune_value_ms if stats.has_tuner else settings.extra_ms,
                tune_median_ms=stats.tune_median_ms if stats.has_tuner else None,
                tune_samples=stats.tune_samples if stats.has_tuner else 0,
                tune_decisions=stats.tune_decisions if stats.has_tuner else 0,
                bands=[b.as_dict() for b in stats.bands] if stats.has_targets else [],
                players=[p.__dict__ for p in stats.players],
                targets=[t.__dict__ for t in stats.targets] if stats.has_targets else [],
                **(v4_record(stats) if stats.has_v4 else {}),
            ))
        return added


def loaded_name(data_dir: str) -> Optional[str]:
    """The file name this WolfRAT build loads (for read_state)."""
    try:
        _, name, _ = materialize_dll(data_dir)
        return name
    except LagCompError:
        return None
