"""Live weather control for a Joint Operations dedicated server.

How it works
------------
The server simulates the weather every tick and sends the numbers to every
player: fog distance, time of day, quake, cloud speed, rain/snow amount,
overcast and the rain-or-snow switch.  So changing them *on the server*
changes the weather for everybody, with stock clients and nothing installed
on the players' side.  This module writes exactly what the game's own mission
script weather actions (``rain``, ``snow``, ``overcast``, ``movefog``,
``skyspeed``, ``quake``) write, from outside the process.

That needs WolfRAT to run on the same PC as the server.  The admin port has no
weather command (``CMD`` reaches the key-binding parser, never the script
engine), so there is no remote route without patching the server exe.

Not reachable this way: lightning.  It is not in the synced block - the engine
broadcasts it as a net text command (``SETFLASH1``), which needs code running
inside the server (see the server.wac add-on below).

Addresses and how each was found: WolfRAT vault, Systems/Memory Map.md.

This module has no Qt in it, and everything except ``ProcessMemory`` runs
against the ``Memory`` protocol so tests use a fake.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional, Protocol

SERVER_IMAGE_NAME = "jointops.exe"

# The game simulates at 62 ticks a second as far as the script setters are
# concerned ("seconds * 62" is how the engine turns a fade time into a step).
TICKS_PER_SECOND = 62

FIXED_ONE = 0x10000            # 100 % for rain / snow / overcast
FOG_MIN = 0x20000              # the engine refuses fog closer than 2 m
QUAKE_MAX_SECONDS = 40         # same cap the engine's own host command uses
QUAKE_TICKS_PER_SECOND = 6     # WAC 'quake n' stores 6 * n

# Wind = how fast the clouds race across the sky (WAC 'skyspeed', the .env
# 'sky_speed').  Players get it as one unsigned byte, so 0-255, never negative:
# the clouds always drift the same way, only the speed can change.  Maps use
# anything from 15 to about 200 (Villa Valley TAC is 206).
WIND_MAX = 255


def wind_word(speed: int) -> str:
    if speed <= 0:
        return "still"
    if speed < 40:
        return "light"
    if speed < 100:
        return "breezy"
    if speed < 170:
        return "windy"
    return "gale"


def wind_text(speed: int) -> str:
    return f"{speed} ({wind_word(speed)})"


class Addr:
    """Globals in the retail 1.7.5.7 image (no ASLR; image base 0x400000)."""

    IS_AUTHORITY = 0x00B5CC28      # non-zero: this process hosts the game
    TIME_OF_DAY = 0x026C6448       # hours << 24, keeps advancing - liveness check
    FOG_CURRENT = 0x026C681C       # metres << 16
    FOG_TARGET = 0x026C6820
    FOG_STEP = 0x026C6828
    CLOUD_SPEED = 0x026C686C
    CLOUD_SPEED_TARGET = 0x026C6870    # skyspeed << 10; sent to players as one byte
    SKY_SPEED_MAP = 0x026C6874         # the map's own sky_speed << 10
    PRECIP_CURRENT = 0x026C6880        # 0 .. 0x10000, this is what clients are sent
    PRECIP_TARGET = 0x026C6884
    PRECIP_STEP = 0x026C688C
    OVERCAST_CURRENT = 0x026C6894
    OVERCAST_TARGET = 0x026C6898
    OVERCAST_STEP = 0x026C68A0
    FOG_REFERENCE = 0x026C68A8         # the furthest the map lets anyone see
    # What the map itself authored ("clear" means back to these).
    FOG_MAP = 0x026C6824
    PRECIP_MAP = 0x026C6888
    OVERCAST_MAP = 0x026C689C
    QUAKE_TICKS = 0x026C68AC
    PRECIP_IS_SNOW = 0x02C059D0        # 0 rain, 1 snow

    # The loaded map's 616-byte BMS header (OpenNova libs/mission bms.h).
    BMS_HEADER = 0x00A761D0

    # Lightning handshake slots: unused .data, zero until something writes them.
    LIGHTNING_MARKER = 0x0334BF00
    LIGHTNING_VERSION = 0x0334BF04
    LIGHTNING_MAILBOX = 0x0334BF08


LIGHTNING_MAGIC = 0x544C5257           # "WRLT"

# The server.wac add-on.  WAC script variables are plain memory (G# lives at
# 0xC6BA40 + 4n).  The add-on watches G250: 1 = flash, 2 = farflash,
# 3 = "are you there?" -> it answers 4.
ADDON_REQUEST = 0x00C6BA40 + 4 * 250
ADDON_FLASH, ADDON_FARFLASH, ADDON_PING, ADDON_PONG = 1, 2, 3, 4

ADDON_SCRIPT = """\
// WolfRAT weather add-on (lightning + thunder). Safe to leave in place:
// it does nothing unless WolfRAT asks. G250 is the request slot.
//   1 = lightning flash overhead   2 = distant lightning   3 = "are you there?"

if eq(G250, 1) then
flash
set(G250, 0)
endif

if eq(G250, 2) then
farflash
set(G250, 0)
endif

if eq(G250, 3) then
set(G250, 4)
endif
"""


# Proof that the process is the build this address map belongs to.  Every
# community patched exe (tick rate, LAA, spawn protection...) leaves these alone.
FINGERPRINT = (
    (0x004EDF60, bytes.fromhex("8b4c2404c1e110b81f85eb51f7e9")),
    (0x004EDFB0, bytes.fromhex("893584686c02")),
    (0x0082E2E5, b"rain\x00"),
)


ADDON_FILENAME = "server.wac"
ADDON_MARK = "WolfRAT weather add-on"


def addon_installed(server_dir) -> bool:
    """Is our block in the server's server.wac?"""
    try:
        path = os.path.join(str(server_dir), ADDON_FILENAME)
        with open(path, "rb") as handle:
            return ADDON_MARK.encode("ascii") in handle.read()
    except OSError:
        return False


def install_addon(server_dir) -> str:
    """Put the lightning add-on in the server folder.  Appends to an existing
    server.wac instead of replacing it.  The game's script compiler is from
    2004: it needs Windows line endings, or the whole file reads as one long
    comment and silently does nothing."""
    path = os.path.join(str(server_dir), ADDON_FILENAME)
    block = ADDON_SCRIPT.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")
    existing = b""
    if os.path.exists(path):
        with open(path, "rb") as handle:
            existing = handle.read()
        if ADDON_MARK.encode("ascii") in existing:
            return "already"
        existing = existing.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
        if existing and not existing.endswith(b"\r\n"):
            existing += b"\r\n"
        existing += b"\r\n"
    temp = path + ".wolfrat-new"
    try:
        with open(temp, "wb") as handle:
            handle.write(existing + block)
        with open(temp, "rb") as handle:
            if handle.read() != existing + block:
                raise OSError("read-back mismatch")
        os.replace(temp, path)
    except OSError as exc:
        raise WeatherError(
            "WolfRAT could not write server.wac into the server folder "
            f"({exc}). Run WolfRAT as administrator, or copy the file in by hand."
        ) from exc
    return "installed"


class WeatherError(Exception):
    """Something the admin should be told about in plain words."""


class ServerNotRunning(WeatherError):
    """No jointops.exe on this PC at all - as opposed to one we cannot use."""


class Memory(Protocol):
    def read(self, address: int, size: int) -> bytes: ...
    def write(self, address: int, data: bytes) -> None: ...


# ---------------------------------------------------------------------------
# Pure arithmetic - identical to the engine's setters
# ---------------------------------------------------------------------------

def percent_to_fixed(percent: int) -> int:
    """WAC ``rain``/``snow``/``overcast``: pct * 65536 / 100, capped at 100 %."""
    percent = max(0, int(percent))
    return min((percent << 16) // 100, FIXED_ONE)


def fixed_to_percent(fixed: int) -> int:
    return max(0, min(100, round(fixed * 100 / FIXED_ONE)))


def fade_step(current: int, target: int, seconds: float) -> int:
    """Per-tick step so `current` reaches `target` in about `seconds`.

    The engine computes ``abs(ticks/2 + target - current) / ticks`` with
    ``ticks = seconds * 62`` (1 when zero).  Never 0: a zero step would freeze
    the ramp for good.
    """
    ticks = int(seconds * TICKS_PER_SECOND) or 1
    return max(1, abs(ticks // 2 + target - current) // ticks)


def metres_to_fixed(metres: float) -> int:
    return int(metres * FIXED_ONE)


def fixed_to_metres(fixed: int) -> int:
    return fixed >> 16


# ---------------------------------------------------------------------------
# What the admin asks for
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Weather:
    """A target sky.  ``None`` means "leave that dial alone"."""

    precip_percent: int = 0
    snow: bool = False
    overcast_percent: int = 0
    fog_metres: Optional[int] = None     # None = the map's own distance
    cloud_speed: Optional[int] = None    # wind, 0-255 (WAC 'skyspeed'); None = the map's own
    fade_seconds: int = 20

    def describe(self) -> str:
        if self.precip_percent <= 0 and self.overcast_percent <= 0 and self.fog_metres is None \
                and self.cloud_speed is None:
            return "clear"
        parts = []
        if self.precip_percent > 0:
            parts.append(f"{'snow' if self.snow else 'rain'} {self.precip_percent}%")
        if self.overcast_percent > 0:
            parts.append(f"overcast {self.overcast_percent}%")
        if self.fog_metres is not None:
            parts.append(f"fog {self.fog_metres} m")
        if self.cloud_speed is not None:
            parts.append(f"wind {wind_text(self.cloud_speed)}")
        return ", ".join(parts)


CLEAR = Weather()

PRESETS: dict[str, Weather] = {
    "clear": CLEAR,
    "drizzle": Weather(precip_percent=25, overcast_percent=50, fade_seconds=30),
    "rain": Weather(precip_percent=60, overcast_percent=80, fade_seconds=30),
    # Storm winds used to be 60, which CALMED windy maps (Villa Valley TAC is 206).
    "storm": Weather(precip_percent=100, overcast_percent=100, fog_metres=350,
                     cloud_speed=230, fade_seconds=25),
    "snow": Weather(precip_percent=50, snow=True, overcast_percent=70, fade_seconds=30),
    "blizzard": Weather(precip_percent=100, snow=True, overcast_percent=100,
                        fog_metres=200, cloud_speed=245, fade_seconds=25),
    "fog": Weather(overcast_percent=40, fog_metres=150, fade_seconds=40),
    "overcast": Weather(overcast_percent=100, fade_seconds=40),
}


CLIMATES = {0: "desert", 1: "jungle", 2: "snow"}
WEATHER_TYPES = {0: "nice day", 1: "rainy", 2: "snow"}


@dataclass(frozen=True)
class MapInfo:
    """What the loaded map says about itself."""

    title: str = ""
    terrain: str = ""          # lower case, no extension: "dvxg1"
    environment: str = ""
    climate: int = -1          # 0 desert, 1 jungle, 2 snow
    weather_type: int = -1     # 0 nice day, 1 rainy, 2 snow

    @property
    def says_snow(self) -> bool:
        return self.climate == 2 or self.weather_type == 2

    def describe(self) -> str:
        if not self.terrain:
            return "no map loaded"
        return (f"terrain {self.terrain}, climate {CLIMATES.get(self.climate, '?')}, "
                f"weather {WEATHER_TYPES.get(self.weather_type, '?')}")


@dataclass(frozen=True)
class Reading:
    """What the server's sky is doing right now."""

    precip_percent: int
    snow: bool
    overcast_percent: int
    fog_metres: int
    map_fog_metres: int
    cloud_speed: int
    quake_ticks: int
    time_of_day_raw: int

    @property
    def clock(self) -> str:
        minutes = (self.time_of_day_raw * 60) >> 24
        return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

class WeatherController:
    """Reads and sets the sky through a ``Memory``.  Verifies before any write."""

    def __init__(self, memory: Memory, clock: Optional[Callable[[], float]] = None):
        self._mem = memory
        self._verified = False
        self._addon: Optional[bool] = None     # None = not asked yet
        self._ping_pending = False
        # time.monotonic looked up per call, so a test patching it reaches us too
        self._clock = clock or (lambda: time.monotonic())
        # Wind.  The engine glides the clouds to a new speed in about half a
        # second, so a fade has to be walked by us: step_wind() every poll.
        self._wind_ramp: Optional[tuple] = None    # (from, to, started, seconds), raw << 10
        self._wind_ours = False        # True while the wind showing is one WE chose
        self._wind_home: Optional[int] = None      # the map's wind before we touched it
        self._wind_written: Optional[int] = None   # what we last wrote

    # -- plumbing ----------------------------------------------------------
    def _get(self, address: int) -> int:
        return struct.unpack("<i", self._mem.read(address, 4))[0]

    def _put(self, address: int, value: int) -> None:
        self._mem.write(address, struct.pack("<i", int(value)))

    def verify(self) -> None:
        """Refuse anything that is not the known build hosting a game."""
        for address, expected in FINGERPRINT:
            if self._mem.read(address, len(expected)) != expected:
                raise WeatherError(
                    "This jointops.exe is not the build WolfRAT's weather map was "
                    "made for, so nothing was changed."
                )
        if self._get(Addr.IS_AUTHORITY) == 0:
            raise WeatherError("That jointops.exe is a game client, not a server hosting a game.")
        self._verified = True

    # -- reading -----------------------------------------------------------
    def read(self) -> Reading:
        if not self._verified:
            self.verify()
        return Reading(
            precip_percent=fixed_to_percent(self._get(Addr.PRECIP_CURRENT)),
            snow=self._get(Addr.PRECIP_IS_SNOW) == 1,
            overcast_percent=fixed_to_percent(self._get(Addr.OVERCAST_CURRENT)),
            fog_metres=fixed_to_metres(self._get(Addr.FOG_CURRENT)),
            map_fog_metres=fixed_to_metres(self._get(Addr.FOG_REFERENCE)),
            cloud_speed=self._get(Addr.CLOUD_SPEED_TARGET) >> 10,
            quake_ticks=self._get(Addr.QUAKE_TICKS),
            time_of_day_raw=self._get(Addr.TIME_OF_DAY),
        )

    # -- writing -----------------------------------------------------------
    def _ramp(self, current_addr: int, target_addr: int, step_addr: int,
              target: int, seconds: float) -> None:
        if self._get(target_addr) == target:
            # Already heading there.  Re-writing the step from where the ramp
            # has got to would shrink it every time and the fade would never
            # finish on schedule; after a map change the target has been
            # snapped back, so this is skipped and the fade starts afresh.
            return
        current = self._get(current_addr)
        # Step first: with the old step and the new target the engine could
        # take one oversized stride in the tick between the two writes.
        self._put(step_addr, fade_step(current, target, seconds))
        self._put(target_addr, target)

    def apply(self, weather: Weather) -> None:
        if not self._verified:
            self.verify()
        seconds = max(0, weather.fade_seconds)
        clear = weather.precip_percent <= 0 and weather.overcast_percent <= 0 \
            and weather.fog_metres is None
        precip_target = percent_to_fixed(weather.precip_percent)
        overcast_target = percent_to_fixed(weather.overcast_percent)
        if clear:
            # Back to the sky the map was built with, not to a bare blue one.
            precip_target = max(0, min(FIXED_ONE, self._get(Addr.PRECIP_MAP)))
            overcast_target = max(0, min(FIXED_ONE, self._get(Addr.OVERCAST_MAP)))

        if weather.precip_percent > 0:
            # Rain and snow share one amount; only flip the kind while it is
            # (nearly) dry, or falling rain would turn to snow mid-air.
            if self._get(Addr.PRECIP_CURRENT) < FIXED_ONE // 50:
                self._put(Addr.PRECIP_IS_SNOW, 1 if weather.snow else 0)
        self._ramp(Addr.PRECIP_CURRENT, Addr.PRECIP_TARGET, Addr.PRECIP_STEP,
                   precip_target, seconds)
        self._ramp(Addr.OVERCAST_CURRENT, Addr.OVERCAST_TARGET, Addr.OVERCAST_STEP,
                   overcast_target, seconds)

        reference = self._get(Addr.FOG_REFERENCE)
        if reference >= FOG_MIN:            # 0 before the first map has loaded
            if weather.fog_metres is None:
                fog = self._get(Addr.FOG_MAP)          # the map's own distance
                if fog < FOG_MIN:
                    fog = reference
            else:
                fog = metres_to_fixed(weather.fog_metres)
            fog = max(FOG_MIN, min(fog, reference))   # never further than the map allows
            self._ramp(Addr.FOG_CURRENT, Addr.FOG_TARGET, Addr.FOG_STEP, fog, seconds)

        if weather.cloud_speed is not None:
            self._wind_to(max(0, min(WIND_MAX, int(weather.cloud_speed))) << 10, seconds, ours=True)
        elif self._wind_ours:
            self._wind_to(self._wind_home_value(), seconds, ours=False)
        self.step_wind()

    # -- wind ----------------------------------------------------------------
    def _wind_home_value(self) -> int:
        if self._wind_home is not None:
            return self._wind_home
        return max(0, min(WIND_MAX << 10, self._get(Addr.SKY_SPEED_MAP)))

    def _wind_value(self, now: float) -> int:
        start, goal, started, seconds = self._wind_ramp
        if seconds <= 0 or now - started >= seconds:
            return goal
        fraction = max(0.0, (now - started) / seconds)
        return round((start >> 10) + ((goal >> 10) - (start >> 10)) * fraction) << 10

    def _wind_to(self, goal: int, seconds: float, ours: bool) -> None:
        """Start a fade to `goal` (raw, speed << 10).  ours=False = on the way
        back to the map's own wind; once there, WolfRAT stops writing it."""
        if ours and not self._wind_ours:
            self._wind_home = self._get(Addr.CLOUD_SPEED_TARGET)   # as we found it
        self._wind_ours = ours
        if self._wind_ramp is not None and self._wind_ramp[1] == goal:
            return                     # already heading there: re-asserting must not restart it
        now = self._clock()
        start = self._wind_value(now) if self._wind_ramp is not None \
            else self._get(Addr.CLOUD_SPEED_TARGET)
        self._wind_ramp = (start, goal, now, max(0.0, float(seconds)))

    def step_wind(self) -> None:
        """Walk the wind fade one step.  Call every poll: a fade outlives the
        weather call that started it (a clear is a single call)."""
        if self._wind_ramp is None:
            return
        live = self._get(Addr.CLOUD_SPEED_TARGET)
        if self._wind_written is not None and live != self._wind_written:
            # Someone else moved it - a map load, or the map's own script
            # (Trench Warfare: skyspeed(50)).  That is the map's wind now.
            self._wind_home = live
            if not self._wind_ours:
                self._wind_ramp = self._wind_written = None     # it is home already
                return
        now = self._clock()
        value = self._wind_value(now)
        if value != live:
            self._put(Addr.CLOUD_SPEED_TARGET, value)
        self._wind_written = value
        start, goal, started, seconds = self._wind_ramp
        if not self._wind_ours and now - started >= seconds:
            # Home: hands off, the map (and its script) own the wind again.
            self._wind_ramp = self._wind_written = self._wind_home = None

    def snapshot(self) -> dict:
        """Where the sky is HEADING right now - taken before our first write so the
        sky can be put back as we found it (a map's own script may have set it)."""
        if not self._verified:
            self.verify()
        return {
            "precip": self._get(Addr.PRECIP_TARGET),
            "snow": self._get(Addr.PRECIP_IS_SNOW),
            "overcast": self._get(Addr.OVERCAST_TARGET),
            "fog": self._get(Addr.FOG_TARGET),
            # the map's wind, not one of ours still fading out
            "cloud": self._wind_home if self._wind_ramp is not None and self._wind_home is not None
            else self._get(Addr.CLOUD_SPEED_TARGET),
        }

    def restore(self, saved: dict, fade_seconds: int = 90) -> None:
        """Fade back to a `snapshot`."""
        if not self._verified:
            self.verify()
        seconds = max(0, fade_seconds)
        precip = max(0, min(FIXED_ONE, int(saved["precip"])))
        if self._get(Addr.PRECIP_CURRENT) < FIXED_ONE // 50:
            self._put(Addr.PRECIP_IS_SNOW, 1 if saved.get("snow") else 0)
        self._ramp(Addr.PRECIP_CURRENT, Addr.PRECIP_TARGET, Addr.PRECIP_STEP, precip, seconds)
        self._ramp(Addr.OVERCAST_CURRENT, Addr.OVERCAST_TARGET, Addr.OVERCAST_STEP,
                   max(0, min(FIXED_ONE, int(saved["overcast"]))), seconds)
        reference = self._get(Addr.FOG_REFERENCE)
        if reference >= FOG_MIN:
            fog = max(FOG_MIN, min(int(saved["fog"]), reference))
            self._ramp(Addr.FOG_CURRENT, Addr.FOG_TARGET, Addr.FOG_STEP, fog, seconds)
        cloud = max(0, min(WIND_MAX << 10, int(saved["cloud"])))
        if self._wind_ramp is not None or self._get(Addr.CLOUD_SPEED_TARGET) != cloud:
            self._wind_home = cloud
            self._wind_to(cloud, seconds, ours=False)
            self.step_wind()

    def map_info(self) -> MapInfo:
        if not self._verified:
            self.verify()
        header = self._mem.read(Addr.BMS_HEADER, 0xF8)

        def text(offset: int, size: int) -> str:
            return header[offset:offset + size].split(b"\x00")[0].decode("latin-1").strip()

        terrain = text(0x44, 48).lower()
        if terrain.endswith(".trn"):
            terrain = terrain[:-4]
        return MapInfo(
            title=text(0x04, 32),
            terrain=terrain,
            environment=text(0xDC, 26).lower(),
            climate=struct.unpack_from("<i", header, 0x84)[0],
            weather_type=struct.unpack_from("<i", header, 0xB8)[0],
        )

    def _cave_running(self) -> bool:
        try:
            return self._get(Addr.LIGHTNING_MARKER) == LIGHTNING_MAGIC
        except WeatherError:
            return False

    def probe_addon(self) -> Optional[bool]:
        """Ask the server.wac add-on whether it is running.  Needs a writable
        handle.  Never blocks: the question goes out on one call and the answer
        is read on the next, so call it once per poll."""
        if not self._verified:
            self.verify()
        value = self._get(ADDON_REQUEST)
        if self._ping_pending:
            if value == ADDON_PONG:
                self._addon = True
            elif value == ADDON_PING:
                self._addon = False            # nobody picked it up: not loaded on this map
            self._ping_pending = False
        if value in (0, ADDON_PING, ADDON_PONG):   # never stamp on a flash still waiting
            self._put(ADDON_REQUEST, ADDON_PING)
            self._ping_pending = True
        return self._addon

    def lightning_available(self) -> bool:
        """True when the server can flash: the add-on answered, or a lightning cave is running."""
        if not self._verified:
            self.verify()
        return self._addon is True or self._cave_running()

    def flash(self, far: bool = False) -> bool:
        """Lightning + thunder for everyone.  False when the server cannot do it."""
        if not self._verified:
            self.verify()
        if self._addon is True:
            self._put(ADDON_REQUEST, ADDON_FARFLASH if far else ADDON_FLASH)
            self._ping_pending = False
            return True
        if self._cave_running():
            self._put(Addr.LIGHTNING_MAILBOX, 1)
            return True
        return False

    def quake(self, seconds: int) -> int:
        """Shake the ground.  Returns the seconds actually used."""
        if not self._verified:
            self.verify()
        seconds = max(1, min(QUAKE_MAX_SECONDS, int(seconds)))
        self._put(Addr.QUAKE_TICKS, seconds * QUAKE_TICKS_PER_SECOND)
        return seconds


# ---------------------------------------------------------------------------
# Timed weather: "!storm 10" = hold for ten minutes, then clear
# ---------------------------------------------------------------------------

class WeatherSchedule:
    """Holds one active weather, re-asserts it, and clears it when time is up.

    A map change resets the sky to whatever the map authored, and a mission
    script can move it too, so the chosen weather is written again on every
    ``tick`` rather than once.  Call ``tick`` every few seconds.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._active: Optional[Weather] = None
        self._until: Optional[float] = None

    @property
    def active(self) -> Optional[Weather]:
        return self._active

    def seconds_left(self) -> Optional[int]:
        if self._active is None or self._until is None:
            return None
        return max(0, int(self._until - self._clock()))

    def start(self, controller: WeatherController, weather: Weather,
              minutes: Optional[float]) -> None:
        controller.apply(weather)
        self._active = weather
        self._until = None if not minutes else self._clock() + minutes * 60

    def clear(self, controller: WeatherController, fade_seconds: int = 20) -> None:
        self._active = None
        self._until = None
        controller.apply(replace(CLEAR, fade_seconds=fade_seconds))

    def forget(self) -> None:
        """Drop the active weather without touching the server (it went away)."""
        self._active = None
        self._until = None

    def tick(self, controller: WeatherController) -> Optional[str]:
        """Returns "ended" when a timed weather just ran out, else None."""
        if self._active is None:
            return None
        if self._until is not None and self._clock() >= self._until:
            self.clear(controller)
            return "ended"
        controller.apply(self._active)
        return None


# ---------------------------------------------------------------------------
# Chat commands
# ---------------------------------------------------------------------------

CHAT_COMMANDS = ("!weather", "!storm", "!rain", "!snow", "!blizzard", "!fog",
                 "!drizzle", "!overcast", "!clear", "!quake", "!lightning")

MAX_MINUTES = 240


@dataclass(frozen=True)
class ChatRequest:
    kind: str                      # 'weather' | 'quake' | 'lightning' | 'dynamic' | 'usage'
    weather: Optional[Weather] = None
    name: str = ""
    minutes: Optional[int] = None
    seconds: int = 0
    message: str = ""
    # kind == 'dynamic' (!weather on / off / status): the weather that changes
    # on its own.  frequency "" = leave it as it is set in WolfRAT.
    switch: str = ""               # 'on' | 'off' | 'status'
    frequency: str = ""            # 'rare' | 'normal' | 'frequent' | 'constant' | 'custom'
    custom_range: Optional[tuple] = None   # (min, max) minutes of clear sky, custom only


# what a mod may type after "!weather on" -> the Dynamic page's frequency key
DYNAMIC_FREQUENCY_WORDS = {
    "rare": "rare", "normal": "normal", "frequent": "frequent", "often": "frequent",
    "always": "constant", "constant": "constant", "almostalways": "constant",
    "custom": "custom",
}
# replies go out as one chat line: 59 characters at most (JO's cut)
DYNAMIC_USAGE = "!weather on rare|normal|frequent|always|custom, or off"


def _parse_dynamic(switch: str, args: list) -> ChatRequest:
    if switch != "on":
        return ChatRequest("dynamic", switch=switch)
    words = [a.lower() for a in args]
    if words[:2] == ["almost", "always"]:
        words[:2] = ["almostalways"]
    if not words:
        return ChatRequest("dynamic", switch="on")
    frequency = DYNAMIC_FREQUENCY_WORDS.get(words[0])
    if frequency is None:
        return ChatRequest("usage", message=DYNAMIC_USAGE)
    custom_range = None
    if frequency == "custom" and len(words) > 1:
        numbers = words[1:3]
        if len(numbers) != 2 or not all(n.isdigit() for n in numbers):
            return ChatRequest("usage", message="Usage: !weather on custom 5 20  (minutes of clear sky)")
        low, high = sorted(int(n) for n in numbers)
        if high > 600:
            return ChatRequest("usage", message="!weather on custom: 600 minutes at most")
        custom_range = (low, high)
    return ChatRequest("dynamic", switch="on", frequency=frequency, custom_range=custom_range)


def parse_chat_command(cmd: str, args: list[str]) -> ChatRequest:
    """``!storm 10`` / ``!weather storm 10`` / ``!clear`` / ``!quake 5``."""
    cmd = cmd.lower()
    args = list(args)
    if cmd == "!weather":
        if not args:
            return ChatRequest("usage", message="Usage: !weather <" + "|".join(PRESETS) + "> [minutes]")
        first = args.pop(0).lower()
        if first in ("on", "off", "status"):
            return _parse_dynamic(first, args)
        cmd = "!" + first
    name = cmd[1:]
    if name == "lightning":
        return ChatRequest("lightning")
    if name == "quake":
        seconds = 5
        if args:
            if not args[0].isdigit():
                return ChatRequest("usage", message=f"Usage: !quake [1-{QUAKE_MAX_SECONDS} seconds]")
            seconds = int(args[0])
        return ChatRequest("quake", seconds=max(1, min(QUAKE_MAX_SECONDS, seconds)))
    if name not in PRESETS:
        return ChatRequest("usage", message="Weather: " + ", ".join(PRESETS))
    minutes = None
    if args:
        if not args[0].isdigit() or not 1 <= int(args[0]) <= MAX_MINUTES:
            return ChatRequest("usage", message=f"Usage: !{name} [1-{MAX_MINUTES} minutes]")
        minutes = int(args[0])
    return ChatRequest("weather", weather=PRESETS[name], name=name, minutes=minutes)


# ---------------------------------------------------------------------------
# Windows process access
# ---------------------------------------------------------------------------

_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_char * 260),
    ]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
    k.Process32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32))
    k.Process32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32))
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    k.ReadProcessMemory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    k.WriteProcessMemory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    return k


def find_process_ids(image_name: str = SERVER_IMAGE_NAME) -> list[int]:
    if sys.platform != "win32":
        return []
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    found = []
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        ok = k.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode("mbcs", "replace").lower() == image_name.lower():
                found.append(int(entry.th32ProcessID))
            ok = k.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return found


class ProcessMemory:
    """``Memory`` over a live process.  ``writable=False`` cannot write at all."""

    def __init__(self, pid: int, writable: bool = True):
        self.pid = pid
        self._k = _kernel32()
        access = _PROCESS_VM_READ | _PROCESS_QUERY_LIMITED_INFORMATION
        if writable:
            access |= _PROCESS_VM_WRITE | _PROCESS_VM_OPERATION
        self._handle = self._k.OpenProcess(access, False, pid)
        if not self._handle:
            raise WeatherError(
                "Windows would not let WolfRAT open the server process. If the "
                "server runs as administrator, run WolfRAT as administrator too."
            )

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not self._k.ReadProcessMemory(self._handle, ctypes.c_void_p(address), buffer,
                                         size, ctypes.byref(done)) or done.value != size:
            raise WeatherError("Could not read the server's memory (has it closed?).")
        return buffer.raw

    def write(self, address: int, data: bytes) -> None:
        done = ctypes.c_size_t()
        if not self._k.WriteProcessMemory(self._handle, ctypes.c_void_p(address), data,
                                          len(data), ctypes.byref(done)) or done.value != len(data):
            raise WeatherError("Could not write to the server's memory (has it closed?).")

    def exe_path(self) -> str:
        """Full path of the server's exe - its folder is where server.wac goes."""
        size = ctypes.c_uint32(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        query = self._k.QueryFullProcessImageNameW
        query.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                          ctypes.POINTER(ctypes.c_uint32))
        if not query(self._handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None


def attach_local_server(writable: bool = True) -> tuple[WeatherController, ProcessMemory]:
    """Find the dedicated server on this PC.  Raises WeatherError with the reason."""
    if sys.platform != "win32":
        raise WeatherError("Weather control needs Windows.")
    pids = find_process_ids()
    if not pids:
        raise ServerNotRunning(
            "No jointops.exe is running on this PC. Weather works when WolfRAT "
            "runs on the same machine as the game server."
        )
    last: Optional[WeatherError] = None
    for pid in pids:
        try:
            memory = ProcessMemory(pid, writable=writable)
        except WeatherError as exc:
            last = exc
            continue
        controller = WeatherController(memory)
        try:
            controller.verify()
            return controller, memory
        except WeatherError as exc:
            last = exc
            memory.close()
    raise last or WeatherError("No usable server process found.")
