"""The end of a map (Dale, 2026-11-02): the old map's sky went onto the next map, and
its fog opened the next map too.  WolfRAT now follows the server's own map name and
round-over latch, and the last minute clears the weather as well as the sky."""

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make, only_storms
from wolfrat import map_timeline as t
from wolfrat import sky
from wolfrat import weather as w
from wolfrat.weather import Addr

SULPHUR = "TD-SulphurCreek.bms"
WATER_GAP = "AS-ColdWaterGapTAC.npj"
STALKER = "stalkerDM.bms"


class Rig:
    """A Weather tab on a fake server whose map and round state can be moved
    separately from what the admin list says."""

    def __init__(self, qtbot, tmp_path, monkeypatch, map_name=SULPHUR, box=True):
        self.clock = [1000.0]
        monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: self.clock[0])
        self.server = FakeServer()
        self.where = {"connected": True, "map": map_name, "players": 4}
        self.on_server(map_name)
        self.round_over(False)
        self.tab, self.said, _ = make(qtbot, tmp_path, self.server, context=lambda: dict(self.where))
        self.tab._dynamic._clock = lambda: self.clock[0]
        self.minutes = [12]
        self.page = self.tab.sky_page
        self.page.time_left = lambda: self.minutes[0]
        self.page.last_minute_cb.setChecked(box)
        self.poll(30)

    def on_server(self, name):
        self.server.cells[Addr.MAP_FILE_NAME] = name.encode("ascii").ljust(32, b"\x00")

    def round_over(self, over):
        self.server.poke(Addr.ROUND_OVER, int(over))

    def poll(self, seconds=5):
        self.clock[0] += seconds
        self.server.tick(seconds)
        self.tab._poll()

    def pump(self):
        """The Sky page's half-second tick, with nothing really sent."""
        self.page._memory, self.page._writable = object(), False
        self.page._pump()

    def looks_queued(self):
        return [c.label for c in self.page.sender.queued() if c.code in (sky.CMD_LOOK, sky.CMD_ALL_OFF)]

    def start_the_load(self, name, clock_says=30):
        """The next map starts: the server knows, the admin list doesn't yet, the
        new clock is already in."""
        self.round_over(False)
        self.on_server(name)
        self.minutes[0] = clock_says

    def load_finished(self, name):
        self.where["map"] = name


# --------------------------------------------------------------- the map watch
def test_a_new_map_is_one_load_however_the_latch_falls():
    watch = t.MapWatch()
    watch.update(SULPHUR, False)
    assert watch.loads == 0
    watch.update(SULPHUR, True)                    # round over
    assert watch.loads == 0
    watch.update(WATER_GAP, True)                  # next map named while the latch is up
    assert (watch.name, watch.loads) == (WATER_GAP, 1)
    watch.update(WATER_GAP, False)
    assert watch.loads == 1
    watch.update(STALKER, False)                   # both in one read
    assert watch.loads == 2


def test_the_same_map_loading_again_counts_as_a_load():
    watch = t.MapWatch()
    watch.update(SULPHUR, False)
    watch.update(SULPHUR, True)
    watch.update(SULPHUR, False)
    assert watch.loads == 1


def test_no_name_keeps_the_last_name():
    watch = t.MapWatch()
    watch.update(SULPHUR, False)
    watch.update(None, False)
    assert watch.name == SULPHUR and watch.loads == 0


def test_a_read_between_the_latch_and_the_name_still_lands_on_the_new_map():
    # Game_StartMission clears the latch (kong.c:277729) before it copies the name (277734)
    watch = t.MapWatch()
    watch.update(SULPHUR, False)
    watch.update(SULPHUR, True)
    watch.update(SULPHUR, False)
    watch.update(WATER_GAP, False)
    assert (watch.name, watch.round_over, watch.loads) == (WATER_GAP, False, 2)
    runner = t.Runner("sky")
    runner.tick(map_name=SULPHUR, minutes_left=9, steps=[], settled=True, load=0)
    assert runner.tick(map_name=SULPHUR, minutes_left=9, steps=[], settled=False, load=1).restarted
    event = runner.tick(map_name=WATER_GAP, minutes_left=-1, steps=[], settled=False, load=2)
    assert event.restarted and event.step is None


def test_every_kind_of_name_in_the_rotation_is_believed():
    server = FakeServer()
    controller = w.WeatherController(server)
    server.poke(Addr.ROUND_OVER, 0)
    for name in ("AS-Shin'LaoCity.bms", "TD-EXMDark&DeadsCrypt.bms", "ASB_G14B.BMS",
                 "AAS_E_Karo_Highlands749.npj", "hookah1.npj"):
        server.cells[Addr.MAP_FILE_NAME] = name.encode("ascii").ljust(32, b"\x00")
        assert controller.loaded_map()[0] == name


def test_a_controller_only_believes_a_real_looking_name():
    server = FakeServer()
    controller = w.WeatherController(server)
    server.poke(Addr.ROUND_OVER, 1)
    for raw, wanted in ((SULPHUR.encode("ascii"), SULPHUR), (b"\xff\xfe\x01rubbish", None),
                        (b"", None), (b"nodot", None)):
        server.cells[Addr.MAP_FILE_NAME] = raw.ljust(32, b"\x00")
        assert controller.loaded_map() == (wanted, True)


# ------------------------------------------------------------- the sky regression
def test_the_old_maps_sky_never_reaches_the_next_map(qtbot, tmp_path, monkeypatch):
    # Log, 2026-11-01 19:01:38: "Map rule: Sundown Showdown sky on TD-SulphurCreek.bms"
    # with the server already on AS-ColdWaterGapTAC.npj.
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.pump()
    assert rig.page.keeper.active.scene.key == "sundownshowdown"

    rig.minutes[0] = 0
    rig.pump()
    assert rig.page.keeper.active is None and rig.looks_queued() == ["off"]
    rig.page.sender.clear()

    rig.round_over(True)
    rig.poll(60)
    rig.pump()
    assert rig.looks_queued() == []

    rig.start_the_load(WATER_GAP)
    rig.pump()                                     # between two polls, as on the night
    assert rig.page.keeper.map_name == WATER_GAP
    assert rig.page.keeper.active is None and rig.looks_queued() == []

    rig.poll(5)
    rig.load_finished(WATER_GAP)
    for _ in range(12):
        rig.poll(5)
        rig.pump()
    assert rig.page.keeper.active is None and rig.looks_queued() == []
    assert rig.page.keeper.describe() == "The map's own sky."


def test_a_late_admin_list_does_not_bring_the_old_map_back(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.minutes[0] = 0
    rig.pump()
    rig.page.sender.clear()
    rig.start_the_load(WATER_GAP)
    for _ in range(6):                             # the admin list still says Sulphur Creek
        rig.poll(5)
        rig.pump()
        assert rig.tab._server_context()[0] == WATER_GAP
        assert rig.looks_queued() == []


def test_a_sky_queued_for_the_old_map_is_dropped_at_the_load(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    look = sky.look_command(rig.page.scene("haunted").look, fade=0, label="Haunted")
    rig.pump()
    rig.page.sender.push(look)
    rig.page.sender.push(sky.Command(sky.CMD_PING, label="ping"), replace_looks=False)
    rig.start_the_load(WATER_GAP)
    rig.pump()
    assert rig.looks_queued() == []
    assert any(c.code == sky.CMD_PING for c in rig.page.sender.queued())


def test_drop_looks_takes_back_a_look_the_script_has_not_read():
    class Slot:
        def __init__(self, value):
            self.cells = {sky.g_address(sky.COMMAND_SLOT): value}

        def read(self, address, size):
            return self.cells[address].to_bytes(4, "little", signed=True)

        def write(self, address, data):
            self.cells[address] = int.from_bytes(data, "little", signed=True)

    sender = sky.Sender()
    unread, ping = Slot(sky.CMD_LOOK), Slot(sky.CMD_PING)
    sender.drop_looks(unread)
    sender.drop_looks(ping)
    assert unread.cells[sky.g_address(sky.COMMAND_SLOT)] == 0
    assert ping.cells[sky.g_address(sky.COMMAND_SLOT)] == sky.CMD_PING


def test_the_next_maps_own_rule_goes_on_once_it_has_settled(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.page._map_rules[STALKER] = "haunted"
    rig.minutes[0] = 0
    rig.pump()
    rig.page.sender.clear()
    rig.start_the_load(STALKER)
    rig.pump()
    assert rig.page.keeper.active is None
    rig.poll(5)
    rig.load_finished(STALKER)
    rig.poll(5)
    rig.pump()
    assert rig.page.keeper.active is None           # settling
    for _ in range(5):
        rig.poll(5)
    rig.pump()
    assert rig.page.keeper.active.scene.key == "haunted"


def test_the_same_map_again_starts_its_sky_over(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.pump()
    rig.minutes[0] = 0
    rig.pump()
    assert rig.page.keeper.off_for_this_map
    rig.page.sender.clear()
    rig.round_over(True)
    rig.poll(5)
    rig.round_over(False)
    rig.minutes[0] = 30
    rig.pump()
    assert rig.page.keeper.active is None and rig.looks_queued() == []     # still loading
    for _ in range(5):
        rig.poll(5)
    rig.pump()
    assert rig.page.keeper.active.scene.key == "sundownshowdown"


def test_more_time_from_a_mod_is_not_a_new_round(qtbot, tmp_path, monkeypatch):
    # !gametime with the map rule's sky off: the clock going up used to read as a reload
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.pump()
    rig.minutes[0] = 0
    rig.pump()
    rig.page.sender.clear()
    rig.minutes[0] = 30
    rig.pump()
    assert rig.page.keeper.active is None and rig.looks_queued() == []


def test_a_map_rule_does_not_start_in_the_last_minute(qtbot, tmp_path, monkeypatch):
    # WolfRAT started with under a minute to go: on then straight off would be two chat lines
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.page._map_rules[SULPHUR] = "sundownshowdown"
    rig.minutes[0] = 0
    rig.pump()
    assert rig.page.keeper.active is None and rig.looks_queued() == []


# ------------------------------------------------------------- the weather clear
def test_the_last_minute_clears_timed_fog_and_the_next_map_opens_clean(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    page = rig.tab.dynamic_page
    page._timeline_map = SULPHUR
    page.step_minutes.setValue(10)
    page.step_weather.setCurrentIndex(page.step_weather.findData("fog"))
    page.step_fog.setValue(100)
    page.step_sky.setCurrentIndex(0)
    page._add_step()
    rig.minutes[0] = 10
    rig.poll()
    assert rig.server.client_sees()["fog_m"] == 100
    rig.poll(60)

    rig.minutes[0] = 0
    rig.poll()
    assert rig.tab._schedule.active is None
    assert rig.server.client_sees()["fog_m"] == 1000
    assert sum("clearing" in line for line in rig.said) == 1
    rig.poll(20)
    assert rig.server.peek(Addr.FOG_CURRENT) >> 16 >= 990         # gone before the map ends
    rig.poll()
    assert sum("clearing" in line for line in rig.said) == 1      # once

    # and nothing writes it back through the end of the round and the load
    rig.round_over(True)
    rig.poll(55)
    writes = len(rig.server.writes)
    rig.start_the_load(WATER_GAP)
    rig.poll()
    rig.load_finished(WATER_GAP)
    for _ in range(8):
        rig.poll()
    assert Addr.FOG_TARGET not in rig.server.writes[writes:]
    assert rig.server.client_sees()["fog_m"] == 1000


def test_a_mods_fog_is_cleared_by_the_last_minute_too(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.tab.mods_cb.setChecked(True)
    rig.tab.on_mod_command("Mod", "!fog", ["30", "100m"])
    assert rig.tab._schedule.active.fog_metres == 100
    rig.minutes[0] = 0
    rig.poll()
    assert rig.tab._schedule.active is None
    assert rig.server.client_sees()["fog_m"] == 1000


def test_the_box_off_leaves_the_weather_alone(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch, box=False)
    rig.tab.mods_cb.setChecked(True)
    rig.tab.on_mod_command("Mod", "!fog", ["30", "100m"])
    rig.minutes[0] = 0
    rig.poll()
    assert rig.tab._schedule.active.fog_metres == 100


def test_a_maps_own_scripted_rain_is_not_touched(qtbot, tmp_path, monkeypatch):
    # Trench Warfare: its script makes the rain, nothing of ours is on
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.server.poke(Addr.PRECIP_TARGET, 0x10000)
    rig.server.poke(Addr.PRECIP_CURRENT, 0x10000)
    before = len(rig.server.writes)
    rig.minutes[0] = 0
    rig.poll()
    rig.poll()
    assert Addr.PRECIP_TARGET not in rig.server.writes[before:]
    assert not any("clearing" in line for line in rig.said)


def test_a_round_ended_early_clears_it_too(qtbot, tmp_path, monkeypatch):
    # objectives, a kill limit or !next: there is no "0 left"
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.tab.mods_cb.setChecked(True)
    rig.tab.on_mod_command("Mod", "!fog", ["30", "100m"])
    rig.round_over(True)
    rig.poll()
    assert rig.tab._schedule.active is None
    assert rig.server.client_sees()["fog_m"] == 1000


def test_a_dynamic_front_ends_with_the_map_and_none_starts_until_the_next(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    only_storms(rig.tab.dynamic_page)
    rig.tab.dynamic_page.enabled_cb.setChecked(True)
    for _ in range(600):
        rig.poll()
        if rig.tab._dynamic.front is not None and rig.tab._dynamic.hands_on:
            break
    assert rig.tab._dynamic.hands_on

    rig.minutes[0] = 0
    rig.poll()
    assert rig.tab._dynamic.front is None and not rig.tab._dynamic.hands_on
    assert rig.server.peek(Addr.PRECIP_TARGET) == 0
    assert rig.server.peek(Addr.FOG_TARGET) == 1000 << 16
    before = len(rig.server.writes)
    rig.poll(3 * 60)                               # long past when the next one was due
    rig.poll()
    assert rig.tab._dynamic.front is None
    assert Addr.PRECIP_TARGET not in rig.server.writes[before:]

    rig.start_the_load(WATER_GAP)
    rig.poll()
    rig.load_finished(WATER_GAP)
    for _ in range(40):
        rig.poll()
        if rig.tab._dynamic.front is not None:
            break
    assert rig.tab._dynamic.front is not None      # weather carries on as normal


def test_timed_steps_run_on_the_next_map_after_a_clear(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    page = rig.tab.dynamic_page
    page._timeline_map = WATER_GAP
    page.step_minutes.setValue(10)
    page.step_weather.setCurrentIndex(page.step_weather.findData("fog"))
    page.step_sky.setCurrentIndex(0)
    page._add_step()
    rig.minutes[0] = 0
    rig.poll()
    rig.round_over(True)
    rig.poll()
    # the name changes while the latch is still up: the end-of-map clear must not
    # count against the new map
    rig.on_server(WATER_GAP)
    rig.minutes[0] = None
    rig.poll()
    rig.start_the_load(WATER_GAP, clock_says=30)
    rig.poll()
    rig.load_finished(WATER_GAP)
    for _ in range(6):
        rig.poll()
    assert rig.tab._schedule.active is None
    rig.minutes[0] = 10
    rig.poll()
    assert rig.tab._schedule.active is not None and rig.tab._schedule.active.fog_metres == 150


# ------------------------------------------------------------- map changes
def test_held_weather_waits_out_a_load_then_goes_back_on(qtbot, tmp_path, monkeypatch):
    # Box off: a mod's !storm 30 is meant to outlast the map, but not to be written
    # into a server that is loading
    rig = Rig(qtbot, tmp_path, monkeypatch, box=False)
    rig.tab.mods_cb.setChecked(True)
    rig.tab.on_mod_command("Mod", "!fog", ["30", "100m"])
    rig.poll()
    rig.round_over(True)
    before = len(rig.server.writes)
    rig.poll()
    rig.start_the_load(WATER_GAP)
    rig.server.poke(Addr.FOG_TARGET, 1000 << 16)    # the load's reset
    rig.poll()
    rig.load_finished(WATER_GAP)
    rig.poll()
    assert Addr.FOG_TARGET not in rig.server.writes[before:]
    for _ in range(6):
        rig.poll()
    assert rig.server.client_sees()["fog_m"] == 100


def test_an_admin_list_that_never_agrees_cannot_hold_everything_up(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    rig.on_server("somemap.bms")
    rig.where["map"] = "Some Map"
    assert rig.tab._server_context()[2] is False
    rig.poll(rig.tab.ADMIN_NAME_WAIT + 1)
    assert rig.tab._server_context() == ("somemap.bms", 4, True)


def test_a_server_without_the_name_falls_back_to_the_admin_list(qtbot, tmp_path, monkeypatch):
    rig = Rig(qtbot, tmp_path, monkeypatch)
    del rig.server.cells[Addr.MAP_FILE_NAME]
    rig.tab._map_watch = t.MapWatch()
    assert rig.tab._server_context()[0] == SULPHUR
