"""!weather on / off / status - mods switching the changing weather from chat."""
import json

import pytest

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make
from wolfrat import weather as w

CHAT_LIMIT = 62


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("args, frequency", [
    (["on"], ""), (["ON"], ""),
    (["on", "rare"], "rare"), (["on", "Normal"], "normal"), (["on", "frequent"], "frequent"),
    (["on", "always"], "constant"), (["on", "almost", "always"], "constant"),
    (["on", "custom"], "custom"),
])
def test_on_with_every_frequency_word(args, frequency):
    request = w.parse_chat_command("!weather", args)
    assert (request.kind, request.switch, request.frequency) == ("dynamic", "on", frequency)


def test_off_status_custom_range_and_nonsense():
    assert w.parse_chat_command("!weather", ["off"]).switch == "off"
    assert w.parse_chat_command("!weather", ["status"]).switch == "status"
    custom = w.parse_chat_command("!weather", ["on", "custom", "20", "5"])
    assert custom.frequency == "custom" and custom.custom_range == (5, 20)
    for bad in (["on", "sometimes"], ["on", "custom", "5"], ["on", "custom", "a", "b"],
                ["on", "custom", "5", "9999"]):
        request = w.parse_chat_command("!weather", bad)
        assert request.kind == "usage" and len(request.message) <= CHAT_LIMIT


def test_the_old_weather_commands_still_parse_the_same():
    assert w.parse_chat_command("!weather", ["storm", "10"]).minutes == 10
    assert w.parse_chat_command("!storm", []).kind == "weather"
    assert w.parse_chat_command("!weather", ["clear"]).weather == w.DRY


# ------------------------------------------------------------- real widgets
def test_on_off_and_how_often_drive_the_same_switches_as_the_page(qtbot, tmp_path):
    server = FakeServer()
    tab, _said, attaches = make(qtbot, tmp_path, server)
    tab.mods_cb.setChecked(True)
    page = tab.dynamic_page
    replies = []

    def say(*args):
        reply = tab.on_mod_command("BadgerLove", "!weather", list(args))
        replies.append(reply)
        return reply

    assert "off" in say("status").lower()
    assert "ON" in say("on", "frequent") and "4-10" in replies[-1]
    assert page.enabled_cb.isChecked() and page.frequency_combo.currentData() == "frequent"
    assert attaches[-1] is True                                  # it really started

    assert "rare" in say("on", "rare") and replies[-1].startswith("Weather is now")
    assert page.frequency_combo.currentData() == "rare"

    assert "5-20" in say("on", "custom", "5", "20")
    assert page.frequency_combo.currentData() == "custom"
    assert (page.clear_min.value(), page.clear_max.value()) == (5, 20)

    say("on")                                                    # bare "on" keeps how often
    assert page.frequency_combo.currentData() == "custom"
    assert "is on" in say("status")

    assert "OFF" in say("off")
    assert not page.enabled_cb.isChecked()
    assert "already off" in say("off")

    assert all(reply and len(reply) <= CHAT_LIMIT for reply in replies), replies

    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())["dynamic"]
    assert saved["enabled"] is False and saved["frequency"] == "custom" and saved["clear_max"] == 20


def test_nothing_happens_unless_mod_weather_commands_are_switched_on(qtbot, tmp_path):
    tab, _said, _attaches = make(qtbot, tmp_path, FakeServer())
    reply = tab.on_mod_command("BadgerLove", "!weather", ["on"])
    assert "switched off in WolfRAT" in reply
    assert not tab.dynamic_page.enabled_cb.isChecked()
