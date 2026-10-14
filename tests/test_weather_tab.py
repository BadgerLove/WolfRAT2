"""The real Weather tab, driven through its widgets against the fake server."""

import json

import pytest

from tests.test_weather import FakeServer
from wolfrat import weather as w
from wolfrat.runtime import DesktopRuntime
from wolfrat.weather import Addr
from wolfrat.weather_tab import MOD_FALLBACK_MINUTES, WeatherTab


class FakeHandle:
    pid = 4242

    def close(self):
        pass


def make(qtbot, tmp_path, server=None, fail=None):
    attaches = []

    def attach(writable=True):
        attaches.append(writable)
        if fail:
            raise w.WeatherError(fail)
        controller = w.WeatherController(server)
        controller.verify()
        return controller, FakeHandle()

    said = []
    tab = WeatherTab(DesktopRuntime.isolated(tmp_path), send_chat=said.append, attach=attach)
    qtbot.addWidget(tab)
    tab._timer.stop()
    tab._poll()
    return tab, said, attaches


def test_no_server_greys_everything_and_says_why(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, fail="No jointops.exe is running on this PC.")
    assert "No jointops.exe" in tab.status_lbl.text()
    assert not any(widget.isEnabled() for widget in tab._action_widgets)
    assert tab.on_mod_command("Dale", "!storm", ["10"]) == "Weather commands are switched off in WolfRAT."
    tab.mods_cb.setChecked(True)
    assert "must run on the server PC" in tab.on_mod_command("Dale", "!storm", ["10"])


def test_looking_never_takes_a_writable_handle(qtbot, tmp_path):
    server = FakeServer()
    tab, _, attaches = make(qtbot, tmp_path, server)
    tab._poll(), tab._poll()
    assert attaches == [False] and server.writes == []
    assert "rain 0%" in tab.sky_lbl.text() and "1000 m" in tab.sky_lbl.text()


def test_storm_button_then_clear(qtbot, tmp_path):
    server = FakeServer()
    tab, said, attaches = make(qtbot, tmp_path, server)
    tab.minutes_spin.setValue(10)
    tab._start_preset("storm")
    assert attaches == [False, True]
    server.tick(40)
    tab._poll()
    assert server.client_sees()["precip"] == 255
    assert "rain 100%" in tab.sky_lbl.text() and "left" in tab.active_lbl.text()
    assert said == ["Weather: storm rolling in for 10 min."]

    tab._clear()
    server.tick(40)
    assert server.client_sees() == {"fog_m": 1000, "precip": 0, "overcast": 0, "snow": 0, "quake": 0}
    assert said[-1] == "The weather is clearing."


def test_custom_dials(qtbot, tmp_path):
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server)
    tab.minutes_spin.setValue(0)
    tab.precip_slider.setValue(40), tab.snow_cb.setChecked(True)
    tab.overcast_slider.setValue(30), tab.fog_cb.setChecked(True), tab.fog_slider.setValue(250)
    tab.fade_slider.setValue(5)
    tab._start_custom()
    server.tick(10)
    seen = server.client_sees()
    assert seen["snow"] == 1 and seen["fog_m"] == 250
    assert abs(seen["precip"] - 102) <= 1 and abs(seen["overcast"] - 76) <= 1
    assert "until cleared" in tab.active_lbl.text()
    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())
    assert saved["precip"] == 40 and saved["snow"] is True and saved["fog_metres"] == 250


def test_mod_without_minutes_uses_the_manual_hold_time(qtbot, tmp_path):
    """A bare !fog used to hold for ever and freeze dynamic weather with it."""
    server = FakeServer()
    tab, said, _ = make(qtbot, tmp_path, server)
    tab.mods_cb.setChecked(True)
    now = [1000.0]
    tab._schedule._clock = lambda: now[0]

    tab.minutes_spin.setValue(15)
    assert tab.on_mod_command("Mod", "!fog", []) is None
    assert said[-1] == "Weather: fog rolling in for 15 min."
    assert tab._schedule.seconds_left() == 15 * 60

    # a typed number still wins
    tab.on_mod_command("Mod", "!fog", ["3"])
    assert tab._schedule.seconds_left() == 3 * 60

    # "until I clear it" is for the tab only: a mod gets the fallback
    tab.minutes_spin.setValue(0)
    tab.on_mod_command("Mod", "!fog", [])
    assert tab._schedule.seconds_left() == MOD_FALLBACK_MINUTES * 60
    assert said[-1] == f"Weather: fog rolling in for {MOD_FALLBACK_MINUTES} min."

    # and it really ends by itself, which is what lets dynamic weather resume
    now[0] += MOD_FALLBACK_MINUTES * 60 + 1
    tab._poll()
    assert tab._schedule.active is None


def test_mod_clear_30_holds_a_dry_sky_then_gives_the_map_its_sky_back(qtbot, tmp_path):
    """Dale 2026-10-09: '!clear 30' did not last - the minutes were dropped."""
    server = FakeServer()
    server.poke(Addr.PRECIP_MAP, w.FIXED_ONE)          # a map built rainy
    tab, said, _ = make(qtbot, tmp_path, server)
    tab.mods_cb.setChecked(True)
    now = [1000.0]
    tab._schedule._clock = lambda: now[0]

    tab.on_mod_command("Mod", "!storm", ["10"])
    assert tab.on_mod_command("Mod", "!clear", ["30"]) is None
    assert said[-1] == "Clear skies for 30 min."
    assert tab._schedule.seconds_left() == 30 * 60
    server.tick(400)
    assert server.client_sees()["precip"] == 0 and server.client_sees()["overcast"] == 0
    tab._poll()
    assert "dry sky" in tab.active_lbl.text()

    # the map's script rains again; the next poll puts it out
    server.poke(Addr.PRECIP_TARGET, w.FIXED_ONE)
    now[0] += 60
    tab._poll()
    server.tick(400)
    assert server.client_sees()["precip"] == 0

    now[0] += 30 * 60
    tab._poll()
    assert tab._schedule.active is None
    assert said[-1] == "The map's own weather is back."
    server.tick(400)
    assert server.client_sees()["precip"] == 255

    # a bare !clear gets the Manual page time like any other mod weather
    tab.minutes_spin.setValue(0)
    tab.on_mod_command("Mod", "!clear", [])
    assert tab._schedule.seconds_left() == MOD_FALLBACK_MINUTES * 60


def test_mod_chat_and_server_restart(qtbot, tmp_path):
    server = FakeServer()
    tab, said, attaches = make(qtbot, tmp_path, server)
    tab.mods_cb.setChecked(True)
    assert tab.on_mod_command("Dale", "!storm", ["soon"]).startswith("Usage")
    assert tab.on_mod_command("Dale", "!storm", ["10"]) is None
    assert tab.on_mod_command("Dale", "!quake", []) is None
    assert server.peek(Addr.QUAKE_TICKS) == 30
    assert "Dale set storm for 10 min" in tab.log_list.item(0).text()

    # the server process goes away and comes back: the storm is put back
    tab._detach()
    server.poke(Addr.PRECIP_TARGET, 0)
    tab._poll()
    assert attaches[-1] is True
    assert server.peek(Addr.PRECIP_TARGET) == 0x10000
