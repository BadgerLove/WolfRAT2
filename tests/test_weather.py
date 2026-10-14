"""Weather control against a fake server that ticks like the real engine."""

import struct

import pytest

from wolfrat import weather as w
from wolfrat.weather import Addr, Weather, WeatherController, WeatherError, WeatherSchedule


class FakeServer:
    """Sparse memory plus the server's own weather ramp."""

    def __init__(self, dedicated=1, map_fog_m=1000, fingerprint=True, addon=False):
        self.cells = {}
        self.writes = []
        self.addon = addon          # is server.wac loaded on this map?
        self.flashes = []           # what the script broadcast: "flash" / "farflash"
        if fingerprint:
            for address, data in w.FINGERPRINT:
                self.cells[address] = data
        put = self.poke
        put(Addr.IS_AUTHORITY, dedicated)
        put(Addr.FOG_REFERENCE, map_fog_m << 16)
        put(Addr.FOG_MAP, map_fog_m << 16)
        put(Addr.PRECIP_MAP, 0)
        put(Addr.OVERCAST_MAP, 0)
        put(Addr.FOG_CURRENT, map_fog_m << 16)
        put(Addr.FOG_TARGET, map_fog_m << 16)
        put(Addr.FOG_STEP, 0xFF0000)
        for cur, tgt, step in self.RAMPS[1:]:
            put(cur, 0), put(tgt, 0), put(step, 4096)
        put(Addr.PRECIP_IS_SNOW, 0)
        put(Addr.QUAKE_TICKS, 0)
        put(Addr.CLOUD_SPEED_TARGET, 15 << 10)
        put(Addr.TIME_OF_DAY, 9 << 24)
        put(w.ADDON_REQUEST, 0)
        self.set_map()

    def set_map(self, title="AS - Snake River Ruins TAC", terrain="dvxg1.trn", climate=0,
                weather_type=0, environment="full_03.env"):
        """The 616-byte BMS header the server keeps for the loaded map."""
        header = bytearray(0xF8)
        header[0:4] = b"BMS\x13"
        header[0x04:0x04 + len(title)] = title.encode("latin-1")
        header[0x44:0x44 + len(terrain)] = terrain.encode("latin-1")
        struct.pack_into("<i", header, 0x84, climate)
        struct.pack_into("<i", header, 0xB8, weather_type)
        header[0xDC:0xDC + len(environment)] = environment.encode("latin-1")
        self.cells[Addr.BMS_HEADER] = bytes(header)

    RAMPS = (
        (Addr.FOG_CURRENT, Addr.FOG_TARGET, Addr.FOG_STEP),
        (Addr.PRECIP_CURRENT, Addr.PRECIP_TARGET, Addr.PRECIP_STEP),
        (Addr.OVERCAST_CURRENT, Addr.OVERCAST_TARGET, Addr.OVERCAST_STEP),
    )

    def poke(self, address, value):
        self.cells[address] = struct.pack("<i", value)

    def peek(self, address):
        return struct.unpack("<i", self.cells[address])[0]

    def read(self, address, size):
        data = self.cells.get(address)
        if data is None or len(data) < size:
            raise WeatherError("unmapped")
        return data[:size]

    def write(self, address, data):
        self.writes.append(address)
        self.cells[address] = bytes(data)

    def run_script(self):
        """What server.wac does on a game tick."""
        if not self.addon:
            return
        request = self.peek(w.ADDON_REQUEST)
        if request == w.ADDON_FLASH:
            self.flashes.append("flash"), self.poke(w.ADDON_REQUEST, 0)
        elif request == w.ADDON_FARFLASH:
            self.flashes.append("farflash"), self.poke(w.ADDON_REQUEST, 0)
        elif request == w.ADDON_PING:
            self.poke(w.ADDON_REQUEST, w.ADDON_PONG)

    def tick(self, seconds):
        self.run_script()
        for _ in range(int(seconds * 62)):
            for cur, tgt, step in self.RAMPS:
                c, t, s = self.peek(cur), self.peek(tgt), self.peek(step)
                accel = (t - c + 31) >> 5
                accel = max(-s, min(s, accel))
                self.poke(cur, c + accel)
            if self.peek(Addr.QUAKE_TICKS):
                self.poke(Addr.QUAKE_TICKS, self.peek(Addr.QUAKE_TICKS) - 1)

    def client_sees(self):
        """The bytes NetPacket_WritePlayerState puts in the phase-2 block."""
        return {
            "fog_m": self.peek(Addr.FOG_TARGET) >> 16,
            "precip": min(255, self.peek(Addr.PRECIP_CURRENT) >> 8),
            "overcast": min(255, self.peek(Addr.OVERCAST_CURRENT) >> 8),
            "snow": self.peek(Addr.PRECIP_IS_SNOW),
            "quake": min(255, self.peek(Addr.QUAKE_TICKS)),
        }


# ---- the arithmetic is the engine's own ----------------------------------

def engine_percent(pct):
    """The engine's own integer percent conversion."""
    value = (pct << 16) & 0xFFFFFFFF
    if value & 0x80000000:
        value -= 1 << 32
    edx = (value * 0x51EB851F) >> 32 >> 5
    edx += (edx >> 31) & 1
    return min(edx, 0x10000)


@pytest.mark.parametrize("pct", [0, 1, 25, 50, 60, 99, 100, 150])
def test_percent_matches_the_engine(pct):
    assert w.percent_to_fixed(pct) == engine_percent(pct)


@pytest.mark.parametrize("cur, tgt, secs", [(0, 0x10000, 30), (0x10000, 0, 20), (0x8000, 0x8000, 10), (0, 655, 0)])
def test_step_matches_the_engine(cur, tgt, secs):
    ticks = (secs * 31) * 2 or 1
    assert w.fade_step(cur, tgt, secs) == max(1, abs((ticks >> 1) - cur + tgt) // ticks)


# ---- refusing the wrong process -------------------------------------------

def test_refuses_a_different_build_and_writes_nothing():
    server = FakeServer(fingerprint=False)
    server.cells[w.FINGERPRINT[0][0]] = b"\x90" * 14
    server.cells[w.FINGERPRINT[1][0]] = b"\x90" * 6
    server.cells[w.FINGERPRINT[2][0]] = b"nope\x00"
    with pytest.raises(WeatherError, match="not the build"):
        WeatherController(server).apply(w.PRESETS["storm"])
    assert server.writes == []


def test_refuses_a_game_client():
    server = FakeServer(dedicated=0)
    with pytest.raises(WeatherError, match="game client"):
        WeatherController(server).apply(w.PRESETS["rain"])
    assert server.writes == []


# ---- a storm arrives, everyone sees it, and it clears ---------------------

def test_storm_ramps_in_and_clients_are_sent_it():
    server = FakeServer()
    control = WeatherController(server)
    storm = w.PRESETS["storm"]
    control.apply(storm)

    server.tick(2)
    early = server.client_sees()
    assert 0 < early["precip"] < 60, "should still be building after 2 s"

    server.tick(storm.fade_seconds + 5)
    seen = server.client_sees()
    assert seen["precip"] == 255 and seen["overcast"] == 255
    assert seen["fog_m"] == 350 and seen["snow"] == 0
    assert control.read().precip_percent == 100

    control.apply(w.CLEAR)
    server.tick(30)
    seen = server.client_sees()
    assert seen["precip"] == 0 and seen["overcast"] == 0 and seen["fog_m"] == 1000


def test_fog_never_exceeds_what_the_map_allows_or_goes_under_two_metres():
    server = FakeServer(map_fog_m=400)
    control = WeatherController(server)
    control.apply(Weather(fog_metres=5000))
    assert server.peek(Addr.FOG_TARGET) == 400 << 16
    control.apply(Weather(fog_metres=0))
    assert server.peek(Addr.FOG_TARGET) == w.FOG_MIN


def test_fog_is_left_alone_before_a_map_has_loaded():
    server = FakeServer(map_fog_m=0)
    WeatherController(server).apply(w.PRESETS["fog"])
    assert Addr.FOG_TARGET not in server.writes


def test_rain_does_not_turn_to_snow_in_mid_air():
    server = FakeServer()
    control = WeatherController(server)
    control.apply(w.PRESETS["rain"])
    server.tick(40)
    control.apply(w.PRESETS["snow"])
    assert server.peek(Addr.PRECIP_IS_SNOW) == 0, "still raining: kind must not flip"
    control.apply(w.CLEAR)
    server.tick(40)
    control.apply(w.PRESETS["snow"])
    assert server.peek(Addr.PRECIP_IS_SNOW) == 1


def test_a_step_is_never_zero():
    server = FakeServer()
    WeatherController(server).apply(Weather(precip_percent=0, fade_seconds=240))
    assert server.peek(Addr.PRECIP_STEP) >= 1


def test_quake_is_capped_like_the_engine_host_command():
    server = FakeServer()
    assert WeatherController(server).quake(999) == 40
    assert server.peek(Addr.QUAKE_TICKS) == 240
    assert server.client_sees()["quake"] == 240


# ---- timed weather ---------------------------------------------------------

def test_timed_storm_survives_a_map_change_then_clears_itself():
    now = [0.0]
    server = FakeServer()
    control = WeatherController(server)
    schedule = WeatherSchedule(clock=lambda: now[0])
    schedule.start(control, w.PRESETS["storm"], minutes=10)
    server.tick(40)

    # map change: the engine snaps every target back to what the map authored
    server.poke(Addr.PRECIP_TARGET, 0), server.poke(Addr.PRECIP_CURRENT, 0)
    server.poke(Addr.OVERCAST_TARGET, 0), server.poke(Addr.OVERCAST_CURRENT, 0)
    now[0] = 300
    assert schedule.tick(control) is None
    server.tick(40)
    assert server.client_sees()["precip"] == 255
    assert schedule.seconds_left() == 300

    now[0] = 601
    assert schedule.tick(control) == "ended"
    assert schedule.active is None
    server.tick(40)
    assert server.client_sees()["precip"] == 0


def test_clear_30_holds_a_dry_sky_over_a_rainy_map_then_hands_it_back():
    # Dale 2026-10-09: "!clear 30" did not last - it was a one-off hand-back
    # to the map's own sky, and a rainy map's script rained on it again.
    now = [0.0]
    server = FakeServer()
    server.poke(Addr.PRECIP_MAP, w.FIXED_ONE)            # map built with full rain
    server.poke(Addr.OVERCAST_MAP, w.FIXED_ONE)
    for addr in (Addr.PRECIP_TARGET, Addr.PRECIP_CURRENT, Addr.OVERCAST_TARGET, Addr.OVERCAST_CURRENT):
        server.poke(addr, w.FIXED_ONE)
    control = WeatherController(server)
    schedule = WeatherSchedule(clock=lambda: now[0])
    request = w.parse_chat_command("!clear", ["30"])
    schedule.start(control, request.weather, request.minutes)
    server.tick(400)
    assert server.client_sees()["precip"] == 0
    assert server.client_sees()["overcast"] == 0

    # the map's script turns the rain back on: the hold puts it out again
    server.poke(Addr.PRECIP_TARGET, w.FIXED_ONE)
    now[0] = 600
    assert schedule.tick(control) is None
    server.tick(400)
    assert server.client_sees()["precip"] == 0
    assert schedule.seconds_left() == 1200

    now[0] = 1801
    assert schedule.tick(control) == "ended"
    server.tick(400)
    assert server.client_sees()["precip"] == 255           # the map's own rain is back


def test_dry_keeps_the_maps_fog_and_says_what_it_is():
    server = FakeServer(map_fog_m=800)
    WeatherController(server).apply(w.PRESETS["fog"])
    server.tick(400)
    WeatherController(server).apply(w.DRY)
    assert server.peek(Addr.FOG_TARGET) == 800 << 16
    assert w.DRY.describe() == "dry sky"
    assert w.CLEAR.describe() == "clear"


def test_untimed_weather_holds_until_cleared():
    now = [0.0]
    server, schedule = FakeServer(), WeatherSchedule(clock=lambda: now[0])
    control = WeatherController(server)
    schedule.start(control, w.PRESETS["fog"], minutes=None)
    now[0] = 10 ** 6
    assert schedule.tick(control) is None and schedule.seconds_left() is None
    schedule.clear(control)
    assert schedule.tick(control) is None and schedule.active is None


# ---- chat ------------------------------------------------------------------

@pytest.mark.parametrize("cmd, args, name, minutes", [
    ("!storm", ["10"], "storm", 10),
    ("!STORM", [], "storm", None),
    ("!weather", ["blizzard", "5"], "blizzard", 5),
    ("!clear", [], "clear", None),
    ("!clear", ["30"], "clear", 30),
])
def test_chat_weather(cmd, args, name, minutes):
    request = w.parse_chat_command(cmd, args)
    assert (request.kind, request.name, request.minutes) == ("weather", name, minutes)
    assert request.weather is (w.DRY if name == "clear" else w.PRESETS[name])


@pytest.mark.parametrize("cmd, args", [
    ("!storm", ["soon"]), ("!storm", ["0"]), ("!storm", ["9999"]),
    ("!weather", []), ("!weather", ["lava"]), ("!quake", ["big"]),
])
def test_chat_usage(cmd, args):
    assert w.parse_chat_command(cmd, args).kind == "usage"


def test_chat_quake_defaults_and_caps():
    assert w.parse_chat_command("!quake", []).seconds == 5
    assert w.parse_chat_command("!quake", ["500"]).seconds == 40


def test_every_chat_command_parses():
    for cmd in w.CHAT_COMMANDS:
        assert w.parse_chat_command(cmd, ["storm"] if cmd == "!weather" else []).kind != "usage"


# ---- the real Windows write path, on a throwaway process we own -----------

@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
def test_process_memory_reads_and_writes_another_process():
    import subprocess
    import sys

    child = subprocess.Popen(
        [sys.executable, "-c",
         "import ctypes,sys\n"
         "b=ctypes.create_string_buffer(b'before!!',8)\n"
         "print(ctypes.addressof(b),flush=True)\n"
         "sys.stdin.readline()\n"
         "print(b.raw.decode(),flush=True)\n"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        address = int(child.stdout.readline())
        memory = w.ProcessMemory(child.pid, writable=True)
        assert memory.read(address, 8) == b"before!!"
        memory.write(address, b"after!!!")
        memory.close()
        child.stdin.write("\n"), child.stdin.flush()
        assert child.stdout.readline().strip() == "after!!!"

        readonly = w.ProcessMemory(child.pid, writable=False)
        with pytest.raises(WeatherError):
            readonly.write(address, b"nope....")
        readonly.close()
    finally:
        child.kill()


def test_clear_means_the_maps_own_sky_not_a_bare_one():
    server = FakeServer(map_fog_m=1024)
    server.poke(Addr.FOG_MAP, 300 << 16)             # a map built foggy and drizzly
    server.poke(Addr.PRECIP_MAP, w.percent_to_fixed(20))
    server.poke(Addr.OVERCAST_MAP, w.percent_to_fixed(60))
    control = WeatherController(server)
    control.apply(w.PRESETS["storm"])
    server.tick(40)
    control.apply(w.CLEAR)
    server.tick(40)
    seen = server.client_sees()
    assert seen["fog_m"] == 300
    assert abs(seen["precip"] - 51) <= 1 and abs(seen["overcast"] - 153) <= 1

    control.apply(w.PRESETS["rain"])                 # no fog of its own -> map fog stays
    assert server.peek(Addr.FOG_TARGET) == 300 << 16
