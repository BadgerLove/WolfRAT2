"""Every chat command, typed in the phone page's chat box.

Each command the phone may use is typed through run_web_command with
sensible arguments and must reach its handler: the server call it makes, or
the line it says.  Each command that is about the player typing it is
refused with the reason.

A command added to WolfRAT must be added to PHONE (or PLAYERS_ONLY) below:
the lists are checked against the Mods tab's own, the ranks table, the
command reference, !mycmd and the phone page's command list.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.command_rig import rig, rigs  # noqa: F401  (fixtures)
from wolfrat import mod_ranks
from wolfrat import mycmd
from wolfrat import weather
from wolfrat.app import ModsTab
from wolfrat.mod_ranks import ADMIN, ALL_PERMISSIONS, Rank

VOTE_NUMBERS = {"!1", "!2", "!3", "!4", "!5"}
PAGE_SCRIPT = Path(__file__).resolve().parent.parent / "wolfrat" / "web_templates" / "app.js"


# ---- how each command shows it reached its handler ---------------------------------

def server_call(name, *rest):
    """The one admin call the game server got, after the player it is about."""
    def check(rig):
        assert rig.server.names() == [name], rig.server.calls
        assert rig.server.calls[0][1:] == rest
    return check


def player_call(name, player, *rest):
    def check(rig):
        assert rig.server.names() == [name], rig.server.calls
        assert rig.server.calls[0][1].name == player
        assert rig.server.calls[0][2:] == rest
    return check


def says(*lines):
    def check(rig):
        assert rig.server.chat == list(lines)
    return check


def both(*checks):
    def check(rig):
        for one in checks:
            one(rig)
    return check


def holds(name, minutes):
    """The Weather tab holds that weather for that long, and said so."""
    def check(rig):
        held, left = rig.weather_now()
        preset = weather.DRY if name == "clear" else weather.PRESETS[name]
        assert held == preset and left == minutes * 60
        said = (f"Clear skies for {minutes} min." if name == "clear"
                else f"Weather: {name} rolling in for {minutes} min.")
        assert rig.server.chat == [said] and rig.server.calls == []
    return check


def banned(rig):
    assert sorted((e.kind, e.value, e.reason) for e in rig.bans.bans.entries) == [
        ("ip", "5.6.7.8", "cheating"), ("name", "Griefer", "cheating")]
    player_call("punt_player", "Griefer", "cheating")(rig)
    assert rig.server.chat == ["Griefer was banned by a mod"]


def ban_first(rig):
    rig.bans.ban_now("Griefer", reason="cheating", kind="both")


def unbanned(rig):
    assert rig.bans.bans.entries == []
    assert rig.server.chat == ["Griefer unbanned."] and rig.server.calls == []


def stats_first(rig):
    rig.asked = []
    rig.tab.career_panel = SimpleNamespace(
        reply=lambda command, target, limit: rig.asked.append((command, target)) or ["a line"])


def asked(command):
    def check(rig):
        assert rig.asked == [(command, "Griefer")]
        assert rig.server.chat == ["a line"] and rig.server.calls == []
    return check


def vote_started(rig):
    assert rig.tab._vote_active and rig.tab._vote_map_file == "crossroads.bms"
    assert rig.server.chat == ["Map vote: Crossroads. Type !yes. 60 seconds."]
    rig.tab._vote_timer.stop()


def skip_started(rig):
    assert rig.tab._skip_active
    assert rig.server.chat == ["Skip current map? Type !yes. 60 seconds."]
    rig.tab._skip_timer.stop()


def forced_vote(rig):
    assert rig.map_vote.started == 1
    assert rig.server.chat == ["Mod Web Admin forced an early end-of-match map vote."]


def game_started(rig):
    assert rig.game_starts == 1
    assert rig.server.chat == ["Game starting in 5 seconds!"]


def changing_weather_on(rig):
    page = rig.weather.dynamic_page
    assert page.enabled_cb.isChecked() and page.frequency_combo.currentData() == "rare"
    assert rig.server.chat == ["Weather ON: rare - a front every 25-60 min"]


def quake(rig):
    assert rig.memory.peek(weather.Addr.QUAKE_TICKS) == 7 * weather.QUAKE_TICKS_PER_SECOND
    assert rig.server.chat == ["Earthquake!"]


def far_lightning(rig):
    assert rig.memory.flashes == ["farflash"] and rig.server.chat == []


def blood_moon(rig):
    assert rig.sky_now() == ("bloodmoon", 600)
    assert rig.server.chat == ["Sky: Blood Moon for 10 min."]


# command: (what is typed on the phone, how it shows it reached its handler[, set up first])
PHONE = {
    "!warn": ("!warn Griefer stop that", player_call("warn_player", "Griefer", "stop that")),
    "!kick": ("!kick Griefer bad language", both(
        player_call("punt_player", "Griefer", "bad language"), says("Griefer was kicked by a mod"))),
    "!ban": ("!ban Griefer cheating", banned),
    "!unban": ("!unban Griefer", unbanned, ban_first),
    "!swap": ("!swap Griefer", both(
        player_call("swap_and_kill", "Griefer", "Griefer"), says("Griefer was swapped by a mod"))),
    "!kill": ("!kill Griefer", both(
        player_call("kill_player", "Griefer"), says("Griefer was killed by a mod"))),
    "!next": ("!next", both(server_call("cycle_mission"), says("Skipping to next map..."))),
    "!map": ("!map rapid strike", both(
        server_call("switch_mission", "rapidstrike.bms", "add_if_missing=False"),
        says("Switching to Rapid Strike..."))),
    "!setnext": ("!setnext crossroads", both(
        server_call("set_next_mission", "crossroads.bms", "add_if_missing=True"),
        says("Next map: Crossroads (added to rotation)"))),
    "!add": ("!add crossroads", both(
        server_call("add_mission", "crossroads.bms", "auto_switch_sides=False"),
        says("Added Crossroads to rotation"))),
    "!remove": ("!remove rapid strike", both(
        server_call("remove_mission", 0), says("Removed Rapid Strike from rotation"))),
    "!startvote": ("!startvote", forced_vote),
    "!startgame": ("!startgame", game_started),
    "!mixteams": ("!mixteams", server_call("shuffle_teams")),
    "!balanceteams": ("!balanceteams", server_call("mix_teams")),
    "!time": ("!time 1830", both(
        server_call("set_time_of_day", "1830"), says("Time set to 18:30"))),
    "!gametime": ("!gametime 30", both(
        server_call("set_setting", "gameTime", "30"), says("Game time set to 30 minutes"))),
    "!weather": ("!weather on rare", changing_weather_on),
    "!storm": ("!storm 10", holds("storm", 10)),
    "!rain": ("!rain 5", holds("rain", 5)),
    "!snow": ("!snow 5", holds("snow", 5)),
    "!blizzard": ("!blizzard 5", holds("blizzard", 5)),
    "!fog": ("!fog 5", holds("fog", 5)),
    "!drizzle": ("!drizzle 5", holds("drizzle", 5)),
    "!overcast": ("!overcast 5", holds("overcast", 5)),
    "!clear": ("!clear 30", holds("clear", 30)),
    "!quake": ("!quake 7", quake),
    "!lightning": ("!lightning far", far_lightning),
    "!sky": ("!sky blood moon 10", blood_moon),
    "!stats": ("!stats Griefer", asked("!stats"), stats_first),
    "!rank": ("!rank Griefer", asked("!rank"), stats_first),
    "!kd": ("!kd Griefer", asked("!stats"), stats_first),
    "!list": ("!list", says("Rotation: 1.rapidstrike")),
    "!vote": ("!vote cross roads", vote_started),
    "!skip": ("!skip", skip_started),
}

# about the player typing them: refused on the phone, with the reason
PLAYERS_ONLY = ("!switch", "!mycomp", "!mycmd", "!yes", "!ping", "!1", "!2", "!3", "!4", "!5")


def phone_commands():
    """Every command the phone may use, from the Mods tab's own lists."""
    return (ModsTab._mod_commands() | ModsTab.PLAYER_COMMANDS) \
        - ModsTab.PLAYER_ONLY_COMMANDS - VOTE_NUMBERS


# ---- the test ----------------------------------------------------------------------

def test_this_file_types_every_command_wolfrat_has():
    assert set(PHONE) == phone_commands()
    assert set(PLAYERS_ONLY) == set(ModsTab.PLAYER_ONLY_COMMANDS)
    assert VOTE_NUMBERS <= ModsTab.PLAYER_ONLY_COMMANDS


@pytest.mark.parametrize("command", sorted(PHONE))
def test_typed_on_the_phone_it_reaches_its_handler(rig, command):
    text, reached, *before = PHONE[command]
    assert text.split()[0] == command
    for step in before:
        step(rig)
    assert rig.phone(text) == {"ok": True, "command": command}
    reached(rig)


@pytest.mark.parametrize("command", sorted(PHONE))
def test_the_command_word_is_read_in_any_case(rig, command):
    text, reached, *before = PHONE[command]
    for step in before:
        step(rig)
    shouted = command.upper() + text[len(command):]
    assert rig.phone("  " + shouted + "  ") == {"ok": True, "command": command}
    reached(rig)


@pytest.mark.parametrize("command", PLAYERS_ONLY)
def test_a_players_own_command_is_refused_with_the_reason(rig, command):
    for text in (command, command + " crossroads", command.upper()):
        assert rig.phone(text) == {"ok": False, "command": command,
                                   "error": f"{command} is for players in the game"}
        assert rig.server.chat == [] and rig.server.calls == []
    assert not rig.tab._vote_active and not rig.tab._skip_active


def test_what_is_not_a_command_is_refused(rig):
    assert rig.phone("!nope") == {"ok": False, "command": "!nope",
                                  "error": "Unknown command: !nope"}
    assert rig.phone("hello")["error"] == "A command starts with !"
    assert rig.phone("")["ok"] is False
    for command in ("!stats", "!rank", "!kd"):
        assert rig.phone(command)["error"] == f"Add a player name: {command} <name>"
    assert rig.server.chat == [] and rig.server.calls == []


# ---- WolfRAT's other lists of commands agree with the one that runs them ----------------

def test_every_command_a_rank_can_be_given_runs():
    guarded = {command for _section, perms in mod_ranks.PERMISSION_GROUPS
               for _key, _label, commands in perms for command in commands}
    assert guarded == ModsTab._mod_commands() - VOTE_NUMBERS
    assert all(mod_ranks.permission_for(command) for command in guarded)
    assert not any(mod_ranks.permission_for(command) for command in ModsTab.PLAYER_COMMANDS)


def test_every_command_in_the_command_reference_runs():
    known = ModsTab._mod_commands() | ModsTab.PLAYER_COMMANDS
    listed = {word for _section, commands in ModsTab.COMMAND_REFERENCE
              for command, _text, _lit in commands
              for word in command.split() if word.startswith("!")}
    assert listed <= known, listed - known
    assert "!unban" in listed and "!time" in listed


def test_every_command_mycmd_lists_runs():
    known = ModsTab._mod_commands() | ModsTab.PLAYER_COMMANDS
    admin = mycmd.rank_commands(Rank(ADMIN, frozenset(ALL_PERMISSIONS)))
    assert set(admin) == ModsTab._mod_commands() - VOTE_NUMBERS
    assert set(mycmd.player_commands()) <= known


def test_every_command_on_the_phone_pages_list_may_be_typed_there():
    script = PAGE_SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"const COMMANDS = \[(.*?)\n\];", script, re.DOTALL).group(1)
    listed = re.findall(r"^\s*\['(![a-z0-9]+)'", block, re.MULTILINE)
    assert len(listed) >= 20 and len(set(listed)) == len(listed)
    assert set(listed) <= phone_commands(), set(listed) - phone_commands()
