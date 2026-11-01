"""Timed steps counted back from the end of a map (Dale, 2026-10-31), the
last-minute sky moved to "0 left", and !fog with a distance."""

import json

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make
from wolfrat import map_timeline as t
from wolfrat import sky
from wolfrat import weather as w

SALFORD = "TK-SulphurCreek.bms"


def steps(*rows):
    return [t.TimedStep(*row) for row in rows]


# ------------------------------------------------------------------ the plan
def test_the_latest_step_due_wins_for_weather_and_sky_separately():
    plan = steps((20, "overcast"), (15, "drizzle", None, "storm"), (10, "fog", 100))
    assert t.due(plan, 21) == (None, None)
    assert t.due(plan, 20)[0].weather == "overcast"         # "20 left" = 20:59
    weather_step, sky_step = t.due(plan, 12)
    assert weather_step.weather == "drizzle" and sky_step.sky == "storm"
    weather_step, sky_step = t.due(plan, 3)
    assert weather_step.weather == "fog" and sky_step.sky == "storm"   # the sky holds
    assert t.due(plan, None) == (None, None)


def test_a_step_with_a_fog_distance_overrides_the_preset():
    step = t.TimedStep(10, "fog", 80)
    assert step.weather_target().fog_metres == 80
    assert step.weather_target().overcast_percent == w.PRESETS["fog"].overcast_percent
    assert t.TimedStep(10, "fog").weather_target() == w.PRESETS["fog"]
    assert t.TimedStep(10, "clear").hands_back and t.TimedStep(10, "clear").weather_target() is None


def test_saved_and_loaded_and_bad_rows_dropped():
    data = {SALFORD: [{"at": 10, "weather": "fog", "fog": 5}, {"at": 20, "weather": "overcast"},
                      {"at": 15}, {"at": "x", "weather": "rain"}, {"at": 5, "weather": "lava"}],
            "empty": []}
    loaded = t.timelines_from_json(data)
    assert list(loaded) == [SALFORD]
    assert [s.minutes_left for s in loaded[SALFORD]] == [20, 10]
    assert loaded[SALFORD][1].fog_metres == t.FOG_MIN_METRES          # 5 m clamped up
    assert t.timelines_from_json(t.timelines_to_json(loaded)) == loaded


def test_map_names_match_like_the_other_map_rules():
    plan = {SALFORD: steps((10, "fog"))}
    assert t.steps_for("TK-SulphurCreek.bms", plan)
    assert t.steps_for("sulphurcreek", plan)
    assert t.steps_for("TD-Adale.bms", plan) == []
    assert t.steps_for(None, plan) == []


def test_runner_fires_once_catches_up_and_starts_over_on_a_new_map():
    plan = steps((15, "drizzle"), (10, "fog"))
    runner = t.Runner("weather")
    fired = []

    def tick(minutes, map_name=SALFORD, settled=True):
        event = runner.tick(map_name=map_name, minutes_left=minutes, steps=plan, settled=settled)
        if event.step:
            fired.append((map_name, event.step.weather))
        return event

    tick(30)
    tick(16)
    tick(15)
    tick(15)                                       # never twice
    tick(10, settled=False)                        # loading: waits
    tick(10)
    assert fired == [(SALFORD, "drizzle"), (SALFORD, "fog")]

    # WolfRAT started late: only the step that should be showing now goes on
    late = t.Runner("weather")
    event = late.tick(map_name=SALFORD, minutes_left=7, steps=plan, settled=True)
    assert event.step.weather == "fog"

    assert tick(25, map_name="TD-Adale.bms").restarted
    assert runner.applied is None
    # the same map again: the clock jumps back up
    tick(9, map_name="TD-Adale.bms")
    event = tick(30, map_name="TD-Adale.bms")
    assert event.restarted and runner.applied is None


def test_runner_stops_for_the_rest_of_the_map_only():
    plan = steps((15, "drizzle"), (10, "fog"))
    runner = t.Runner("weather")
    runner.tick(map_name=SALFORD, minutes_left=15, steps=plan, settled=True)
    runner.stop_for_this_map()
    assert runner.tick(map_name=SALFORD, minutes_left=10, steps=plan, settled=True).step is None
    assert runner.tick(map_name="Other", minutes_left=30, steps=plan, settled=True).step is None
    assert runner.tick(map_name="Other", minutes_left=10, steps=plan, settled=True).step.weather == "fog"


# --------------------------------------------------------------- !fog 80m
def test_fog_from_chat_with_a_distance():
    request = w.parse_chat_command("!fog", ["80m"])
    assert request.weather.fog_metres == 80 and request.minutes is None
    request = w.parse_chat_command("!fog", ["thick", "10"])
    assert request.weather.fog_metres == w.CHAT_FOG_WORDS["thick"] and request.minutes == 10
    request = w.parse_chat_command("!fog", ["10", "light"])
    assert request.weather.fog_metres == w.CHAT_FOG_WORDS["light"] and request.minutes == 10
    assert w.parse_chat_command("!fog", ["10"]).weather == w.PRESETS["fog"]   # as before
    assert w.parse_chat_command("!fog", ["5m"]).weather.fog_metres == w.CHAT_FOG_MIN
    assert w.parse_chat_command("!weather", ["storm", "200m"]).weather.fog_metres == 200
    assert w.parse_chat_command("!fog", ["soup"]).kind == "usage"
    assert w.parse_chat_command("!clear", ["80m"]).kind == "usage"
    for request in (w.parse_chat_command("!blizzard", ["x"]), w.parse_chat_command("!clear", ["x"])):
        assert len(request.message) <= 59


# ------------------------------------------------------------------ the sky
SUNDOWN = next(s for s in sky.BUILTIN_SCENES if s.key == "sundownshowdown")
GREY = next(s for s in sky.BUILTIN_SCENES if s.key == "ghosttown")


def sky_tick(keeper, minutes=None, timed=None, last=True):
    return keeper.tick(map_name=SALFORD, players=4, settled=True, map_scene=SUNDOWN,
                       minutes_left=minutes, end_last_minute=last, timed=timed)


def test_last_minute_sky_goes_at_zero_and_the_map_rule_leaves_it_off():
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    sky_tick(keeper, 5)
    assert keeper.active.scene is SUNDOWN
    assert not [s for s in sky_tick(keeper, 1) if s.announce]       # 1:59 left: still on
    steps_ = sky_tick(keeper, 0)
    assert keeper.active is None and "last minute" in steps_[0].announce
    for _ in range(3):                                              # and it stays off
        assert sky_tick(keeper, 0) == []
    assert keeper.active is None


def test_a_timed_sky_takes_over_from_the_map_rule_and_can_hand_back():
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    sky_tick(keeper, 15)
    sky_tick(keeper, 10, timed=(GREY, 10))
    assert keeper.active.scene is GREY and keeper.active.source == "timed"
    assert "timed step" in keeper.describe()
    sky_tick(keeper, 9)
    assert keeper.active.scene is GREY                              # the map rule stays out
    steps_ = sky_tick(keeper, 5, timed=(None, 5))
    assert keeper.active is None and steps_[0].command is not None
    sky_tick(keeper, 4)
    assert keeper.active is None


def test_sky_off_ends_the_timed_skies_until_the_next_round():
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    sky_tick(keeper, 15)
    keeper.off("mod")
    sky_tick(keeper, 10, timed=(GREY, 10))
    assert keeper.active is None
    keeper.new_round()
    sky_tick(keeper, 30)
    assert keeper.active.scene is SUNDOWN


# ------------------------------------------------------- the Weather tab
def test_weather_tab_runs_the_steps_and_drops_them_with_the_map(qtbot, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    where = {"connected": True, "map": SALFORD, "players": 4}
    server = FakeServer()
    tab, said, _ = make(qtbot, tmp_path, server, context=lambda: dict(where))
    minutes = [12]
    tab.sky_page.time_left = lambda: minutes[0]
    page = tab.dynamic_page
    page._timeline_map = SALFORD
    page.step_minutes.setValue(10)
    page.step_weather.setCurrentIndex(page.step_weather.findData("fog"))
    page.step_fog.setValue(90)
    page.step_sky.setCurrentIndex(page.step_sky.findData("ghosttown"))
    page._add_step()
    assert page.steps_for(SALFORD) == [t.TimedStep(10, "fog", 90, "ghosttown")]
    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())["timelines"]
    assert saved == {SALFORD: [{"at": 10, "weather": "fog", "fog": 90, "sky": "ghosttown"}]}

    def poll(seconds=5):
        clock[0] += seconds
        server.tick(seconds)
        tab._poll()

    poll(30)                                       # settled
    assert tab._schedule.active is None
    minutes[0] = 10
    poll()
    assert tab._schedule.active.fog_metres == 90
    assert server.client_sees()["fog_m"] == 90
    assert any("fog rolling in" in line for line in said)
    poll()
    assert sum("fog rolling in" in line for line in said) == 1

    # the Sky page's half-second pump puts the step's colours on
    pump = tab.sky_page
    pump._context, pump._memory, pump._writable = (SALFORD, 4, True), object(), False
    pump._pump()
    assert pump.keeper.active.scene.key == "ghosttown"
    assert pump.keeper.active.source == "timed"

    where["map"] = "TD-Adale.bms"                  # next map: the fog goes with the old one
    minutes[0] = 25
    poll()
    assert tab._schedule.active is None and tab._timed_weather is None


def test_clear_ends_the_steps_for_this_map(qtbot, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server,
                     context=lambda: {"connected": True, "map": SALFORD, "players": 4})
    minutes = [16]
    tab.sky_page.time_left = lambda: minutes[0]
    page = tab.dynamic_page
    page._timeline_map = SALFORD
    for at, kind in ((15, "drizzle"), (10, "fog")):
        page.step_minutes.setValue(at)
        page.step_weather.setCurrentIndex(page.step_weather.findData(kind))
        page.step_sky.setCurrentIndex(0)
        page._add_step()
    clock[0] += 30
    tab._poll()
    minutes[0] = 15
    clock[0] += 5
    tab._poll()
    assert tab._schedule.active == w.PRESETS["drizzle"]
    tab.mods_cb.setChecked(True)
    tab.on_mod_command("Mod", "!clear", [])
    minutes[0] = 10
    clock[0] += 5
    tab._poll()
    assert tab._schedule.active is None or tab._schedule.active.dry


def test_the_dynamic_page_lists_timed_maps_and_removes_steps(qtbot, tmp_path):
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server)
    page = tab.dynamic_page
    page._timeline_map = "TD-Adale.bms"
    page.step_minutes.setValue(20)
    page.step_weather.setCurrentIndex(page.step_weather.findData("overcast"))
    page._add_step()
    page.step_minutes.setValue(20)                 # same minute: replaces
    page.step_weather.setCurrentIndex(page.step_weather.findData("rain"))
    page._add_step()
    assert [s.weather for s in page.steps_for("TD-Adale.bms")] == ["rain"]
    page.only_rules_cb.setChecked(True)
    rows = [page.map_table.item(r, 0).data(256) for r in range(page.map_table.rowCount())]
    assert "TD-Adale.bms" in rows
    page.step_table.selectRow(0)
    page._remove_steps()
    assert page.steps_for("TD-Adale.bms") == [] and page.timelines() == {}
    # sky choices come from the Sky page
    assert page.step_sky.findData("sundownshowdown") >= 0


# ------------------------------------------------------------- the map clock
def test_a_coop_map_saying_0_left_all_game_is_not_the_last_minute():
    # Dale, 2026-11-01: zombie.bms says "0/30" from the start; every !sky was undone
    clock = t.MapClock()
    for now in (0, 30, 600):
        assert clock.minutes_left(map_name="zombie.bms", remaining=0,
                                  read_at=now, now=now) is None


def test_a_timed_map_counting_down_to_0_is_the_last_minute():
    clock = t.MapClock()
    assert clock.minutes_left(map_name=SALFORD, remaining=30, read_at=20, now=20) == 30
    assert clock.minutes_left(map_name=SALFORD, remaining=1, read_at=1700, now=1700) == 1
    assert clock.minutes_left(map_name=SALFORD, remaining=0, read_at=1750, now=1750) == 0


def test_the_old_maps_clock_read_just_after_a_switch_does_not_count():
    clock = t.MapClock()
    clock.minutes_left(map_name=SALFORD, remaining=12, read_at=100, now=100)
    # !map to the co-op map: the first poll still has the old map's 12 left
    assert clock.minutes_left(map_name="zombie.bms", remaining=12, read_at=103, now=105) == 12
    assert clock.minutes_left(map_name="zombie.bms", remaining=0, read_at=110, now=110) is None
    assert clock.minutes_left(map_name="zombie.bms", remaining=0, read_at=900, now=900) is None


def test_a_new_map_forgets_the_old_clock_and_unknown_stays_unknown():
    clock = t.MapClock()
    clock.minutes_left(map_name=SALFORD, remaining=5, read_at=100, now=100)
    assert clock.minutes_left(map_name=SALFORD, remaining=None, read_at=0, now=200) is None
    assert clock.minutes_left(map_name="zombie.bms", remaining=0, read_at=300, now=300) is None


def test_no_timed_step_fires_on_a_coop_map():
    clock, runner = t.MapClock(), t.Runner("weather")
    plan = steps((20, "overcast"), (10, "fog", 100), (0, "clear"))
    for now in (0, 30, 60):
        left = clock.minutes_left(map_name="zombie.bms", remaining=0, read_at=now, now=now)
        event = runner.tick(map_name="zombie.bms", minutes_left=left, steps=plan, settled=True)
        assert event.step is None
