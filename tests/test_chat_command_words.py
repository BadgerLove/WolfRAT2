"""Chat commands with several words: every handler gets the words it expects.

Weather and sky commands read each word on its own ("!weather on custom 5
20", "!sky blood moon 10").  A reason or a map name stays one piece
("!warn Griefer stop it now", "!map rapid strike").  Typed in the game or
on the phone page, the result is the same.
"""
from types import SimpleNamespace

import pytest

from tests.command_rig import CommandRig
from wolfrat import sky
from wolfrat import weather


@pytest.fixture
def rig(qtbot, tmp_path, monkeypatch):
    return CommandRig(qtbot, tmp_path, monkeypatch)


def typed(rig, where, text):
    """Type ``text`` on the phone or, as the admin Dale, in the game."""
    if where == "phone":
        return rig.phone(text)
    rig.game("Dale", text)
    return None


BOTH = pytest.mark.parametrize("where", ["phone", "game"])


# ---- sky ----------------------------------------------------------------------

@BOTH
@pytest.mark.parametrize("text, scene, seconds", [
    ("!sky blood moon 10", "bloodmoon", 600),
    ("!sky blood moon", "bloodmoon", None),
    ("!sky Blood  Moon 5", "bloodmoon", 300),
    ("!sky bloodmoon 10", "bloodmoon", 600),
    ("!sky golden 3", "golden", 180),
    ("!sky ghost town 7", "ghosttown", 420),
    ("!sky toxic", "toxic", None),
])
def test_sky_scene_names_of_several_words_and_their_minutes(rig, where, text, scene, seconds):
    typed(rig, where, text)
    assert rig.sky_now() == (scene, seconds)
    label = next(s.label for s in sky.BUILTIN_SCENES if s.key == scene)
    span = f" for {seconds // 60} min" if seconds else " until the map changes"
    assert rig.server.chat == [f"Sky: {label}{span}."]


@BOTH
def test_sky_off_after_a_scene(rig, where):
    typed(rig, where, "!sky blood moon 10")
    typed(rig, where, "!sky off")
    assert rig.sky_now() is None
    assert rig.server.chat == ["Sky: back to normal."]


# ---- the weather that changes on its own -------------------------------------------

@BOTH
def test_weather_on_custom_takes_both_numbers(rig, where):
    typed(rig, where, "!weather on custom 5 20")
    page = rig.weather.dynamic_page
    assert page.enabled_cb.isChecked()
    assert page.frequency_combo.currentData() == "custom"
    assert (page.clear_min.value(), page.clear_max.value()) == (5, 20)
    assert rig.server.chat == ["Weather ON: custom - a front every 5-20 min"]


@BOTH
@pytest.mark.parametrize("text, frequency", [
    ("!weather on almost always", "constant"),
    ("!weather on always", "constant"),
    ("!weather on rare", "rare"),
    ("!weather ON Frequent", "frequent"),
])
def test_weather_on_how_often(rig, where, text, frequency):
    typed(rig, where, text)
    page = rig.weather.dynamic_page
    assert page.enabled_cb.isChecked() and page.frequency_combo.currentData() == frequency
    assert len(rig.server.chat) == 1 and rig.server.chat[0].startswith("Weather ON: ")


# ---- weather now: minutes and how close the fog comes, in either order ---------------

@BOTH
@pytest.mark.parametrize("text, name, fog, seconds", [
    ("!storm 10 80m", "storm", 80, 600),
    ("!storm 80m 10", "storm", 80, 600),
    ("!fog thick 10", "fog", 60, 600),
    ("!fog 10 thick", "fog", 60, 600),
    ("!weather storm 10 80m", "storm", 80, 600),
    ("!weather fog thick 10", "fog", 60, 600),
    ("!weather rain 15 light", "rain", 400, 900),
    ("!storm 10", "storm", 350, 600),
    ("!blizzard 4", "blizzard", 200, 240),
])
def test_weather_with_minutes_and_fog(rig, where, text, name, fog, seconds):
    typed(rig, where, text)
    held, left = rig.weather_now()
    preset = weather.PRESETS[name]
    assert held is not None
    assert (held.precip_percent, held.snow, held.overcast_percent) == (
        preset.precip_percent, preset.snow, preset.overcast_percent)
    assert held.fog_metres == fog and left == seconds
    assert rig.server.chat == [f"Weather: {name} rolling in for {seconds // 60} min."]


@BOTH
def test_clear_keeps_its_minutes(rig, where):
    typed(rig, where, "!clear 30")
    held, left = rig.weather_now()
    assert held == weather.DRY and left == 1800


# ---- lightning ------------------------------------------------------------------

@BOTH
@pytest.mark.parametrize("text, colour, far", [
    ("!lightning red far", (255, 0, 0), True),
    ("!lightning far red", (255, 0, 0), True),
    ("!lightning blue", (30, 60, 255), False),
    ("!weather lightning red far", (255, 0, 0), True),
    ("!lightning #00ff00 far", (0, 255, 0), True),
])
def test_coloured_lightning_gets_its_colour_and_far(rig, where, text, colour, far):
    typed(rig, where, text)
    strike = rig.weather.sky_page._strike
    assert strike is not None and strike["far"] is far
    assert [value for _slot, value in strike["command"].writes[:3]] == list(colour)
    assert rig.server.chat == []


@BOTH
@pytest.mark.parametrize("text, flash", [("!lightning far", "farflash"), ("!lightning", "flash")])
def test_plain_lightning(rig, where, text, flash):
    typed(rig, where, text)
    assert rig.memory.flashes == [flash]
    assert rig.server.chat == []


@BOTH
def test_quake_seconds(rig, where):
    typed(rig, where, "!quake 7")
    assert rig.memory.peek(weather.Addr.QUAKE_TICKS) == 7 * weather.QUAKE_TICKS_PER_SECOND


# ---- "the rest as one piece" stays one piece -------------------------------------------

def test_a_reason_stays_whole(rig):
    rig.phone("!warn Griefer stop it  now")
    assert rig.server.calls[0][0] == "warn_player" and rig.server.calls[0][2] == "stop it now"
    rig.game("Dale", "!warn Griefer stop it  now")            # a mod's own spacing is kept
    assert rig.server.calls[0][2] == "stop it  now"
    rig.phone("!kick Griefer team killing again")
    assert rig.server.calls[0][0] == "punt_player" and rig.server.calls[0][2] == "team killing again"
    rig.game("Dale", "!kick Griefer")
    assert rig.server.calls[0][2] == "Kicked by mod"
    rig.phone("!ban Griefer wall hack, third time")
    assert rig.server.calls[0][0] == "punt_player"
    assert rig.server.calls[0][2] == "wall hack, third time"
    assert {entry.reason for entry in rig.bans.bans.entries} == {"wall hack, third time"}


@pytest.mark.parametrize("text, call", [
    ("!map rapid strike", ("switch_mission", "rapidstrike.bms", "add_if_missing=False")),
    ("!map Cross Roads", ("switch_mission", "crossroads.bms", "add_if_missing=True")),
    ("!setnext rapid strike", ("set_next_mission", "rapidstrike.bms", "add_if_missing=False")),
    ("!setnext cross roads", ("set_next_mission", "crossroads.bms", "add_if_missing=True")),
    ("!add cross roads", ("add_mission", "crossroads.bms", "auto_switch_sides=False")),
    ("!remove rapid strike", ("remove_mission", 0)),
])
@BOTH
def test_a_map_name_of_several_words_stays_whole(rig, where, text, call):
    typed(rig, where, text)
    assert rig.server.calls == [call]


@BOTH
def test_a_player_name_of_several_words_stays_whole(rig, where):
    asked = []
    rig.tab.career_panel = SimpleNamespace(
        reply=lambda command, target, limit: asked.append((command, target)) or ["a line"])
    typed(rig, where, "!stats Old Man River")
    typed(rig, where, "!rank Old Man River")
    typed(rig, where, "!kd Old Man River")
    assert asked == [("!stats", "Old Man River"), ("!rank", "Old Man River"),
                     ("!stats", "Old Man River")]
    rig.tab.career_panel = None
    looked_up = []
    rig.tab.messages_tab = SimpleNamespace(_kd_enabled=True, stats_store=SimpleNamespace(
        get_player=lambda name: looked_up.append(name) or {
            "name": name, "kills": 3, "deaths": 1, "kd": 3.0}))
    typed(rig, where, "!kd Old Man River")
    assert looked_up == ["Old Man River"]
    assert rig.server.chat == ["Old Man River: Kills: 3 | Deaths: 1 | KD: 3.0"]


def test_a_vote_for_a_map_of_several_words(rig):
    rig.game("Griefer", "!vote cross roads")
    assert rig.server.chat == ["Map vote: Crossroads. Type !yes. 60 seconds."]
    assert rig.tab._vote_map_file == "crossroads.bms"
    rig.tab._vote_timer.stop()


# ---- "!weather sky ..." is not a command -------------------------------------------------

@pytest.mark.parametrize("text", [
    "!weather sky", "!weather sky toxic", "!weather sky blood moon 10", "!weather SKY off",
])
def test_weather_sky_answers_with_the_sky_usage_line(rig, text):
    rig.game("Dale", text)                               # never raises
    assert rig.server.chat == [sky.CHAT_USAGE]
    assert rig.sky_now() is None and rig.weather_now() == (None, None)
    rig.phone(text)
    assert rig.sky_now() is None and rig.weather_now() == (None, None)


def test_the_weather_tab_never_starts_weather_for_a_sky_request(rig):
    assert rig.weather.on_mod_command("Dale", "!weather", ["sky", "toxic"]) == sky.CHAT_USAGE
    assert rig.weather.mod_command("Dale", "!weather", ["sky"]) == (sky.CHAT_USAGE, True)
    assert rig.weather_now() == (None, None)
    assert len(sky.CHAT_USAGE) <= 59
