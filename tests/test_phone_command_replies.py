"""A command typed on the phone that does not run: the phone hears why.

The reason comes back to the phone page and is not posted in game chat.
Typed in the game, the same refusal is a chat line, as before.  What a
command says when it does run (its announcement) stays in game chat for both.
"""
import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from tests.command_rig import CommandRig
from wolfrat import coop_guard
from wolfrat import sky
from wolfrat import start_game
from wolfrat import weather
from wolfrat.web_server import WolfWebServer

TIME_USAGE = "Usage: !time <hhmm> e.g. !time 9 or !time 1830"
WEATHER_USAGE = "Usage: !weather <" + "|".join(weather.PRESETS) + "> [minutes]"


@pytest.fixture
def rig(qtbot, tmp_path, monkeypatch):
    rig = CommandRig(qtbot, tmp_path, monkeypatch)
    rig.tab.messages_tab = SimpleNamespace(_kd_enabled=True, stats_store=SimpleNamespace(
        get_player=lambda name: None))
    return rig


def refused(rig, text, why):
    """The phone gets ``why``; nothing reaches game chat or the server."""
    answer = rig.phone(text)
    assert answer == {"ok": False, "command": text.split()[0].lower(), "error": why}, text
    assert rig.server.chat == [], text
    assert rig.server.calls == [], text


# what is typed -> the line a mod in the game is told, and the phone now gets
SAID_IN_CHAT = [
    ("!gametime", "Usage: !gametime <1-240> minutes"),
    ("!gametime soon", "Usage: !gametime <1-240> minutes"),
    ("!gametime 999", "Game time must be 1-240 minutes"),
    ("!gametime 0", "Game time must be 1-240 minutes"),
    ("!time", TIME_USAGE),
    ("!time 24", TIME_USAGE),
    ("!time noon", TIME_USAGE),
    ("!map zzzqqq", "Map not found: zzzqqq"),
    ("!setnext", "Usage: !setnext <map>"),
    ("!setnext zzzqqq", "Map not found: zzzqqq"),
    ("!add zzzqqq", "Map not found: zzzqqq"),
    ("!remove zzzqqq", "Map not found: zzzqqq"),
    ("!remove crossroads", "Crossroads is not in the rotation"),
    ("!unban", "Usage: !unban <name or IP>"),
    ("!unban Nobody", "Nobody is not on the ban list."),
    ("!vote", "Usage: !vote <map name>"),
    ("!vote zzzqqq", "Map not found: zzzqqq"),
    ("!stats Nobody", "No stats found for Nobody"),
    ("!kd Nobody", "No stats found for Nobody"),
    ("!storm soon", "Usage: !storm [minutes] [100m|thick|light]"),
    ("!storm 999", "Usage: !storm [1-240 minutes]"),
    ("!clear later", "Usage: !clear [minutes]"),
    ("!weather", WEATHER_USAGE),
    ("!weather lava", "Weather: " + ", ".join(weather.PRESETS)),
    ("!weather on sometimes", weather.DYNAMIC_USAGE),
    ("!weather on custom 5", "Usage: !weather on custom 5 20  (minutes of clear sky)"),
    ("!weather off", "Weather is already off."),
    ("!weather sky toxic", sky.CHAT_USAGE),
    ("!quake big", "Usage: !quake [1-40 seconds]"),
    ("!lightning mauve", sky.LIGHTNING_USAGE),
    ("!sky", sky.CHAT_USAGE),
    ("!sky lava", "No sky called 'lava'. Try !sky list"),
    ("!sky toxic 999", "!sky <scene> [1-240 minutes]"),
]


@pytest.mark.parametrize("text, why", SAID_IN_CHAT)
def test_the_phone_hears_why_and_game_chat_does_not(rig, text, why):
    refused(rig, text, why)


@pytest.mark.parametrize("text, why", SAID_IN_CHAT)
def test_a_mod_in_the_game_is_told_in_chat_as_before(rig, text, why):
    rig.game("Dale", text)
    assert rig.server.chat == [why]
    assert rig.server.calls == []


# ---- a player who is not there: the game says nothing, the phone is told ---------------

@pytest.mark.parametrize("text, why", [
    ("!kick Nobody", "Player not found: Nobody"),
    ("!kick Nobody being rude", "Player not found: Nobody"),
    ("!warn Nobody stop it", "Player not found: Nobody"),
    ("!ban Nobody cheating", "Player not found: Nobody"),
    ("!swap Nobody", "Player not found: Nobody"),
    ("!kill Nobody", "Player not found: Nobody"),
    ("!kick", "Add a player name: !kick <player>"),
    ("!warn", "Add a player name: !warn <player>"),
    ("!ban", "Add a player name: !ban <player>"),
    ("!swap", "Add a player name: !swap <player>"),
    ("!kill", "Add a player name: !kill <player>"),
])
def test_no_such_player(rig, text, why):
    refused(rig, text, why)
    assert rig.bans.bans.entries == []
    rig.game("Dale", text)                       # in the game: a note in the mod log, no chat
    assert rig.server.chat == [] and rig.server.calls == []
    assert "player not found" in rig.last_log()


# ---- switched off, or not possible right now -----------------------------------------

def test_weather_and_sky_switched_off(rig):
    rig.weather.mods_cb.setChecked(False)
    rig.weather.sky_page.mods_cb.setChecked(False)
    refused(rig, "!storm 10", "Weather commands are switched off in WolfRAT.")
    refused(rig, "!weather on", "Weather commands are switched off in WolfRAT.")
    refused(rig, "!quake", "Weather commands are switched off in WolfRAT.")
    refused(rig, "!sky toxic", "Sky commands are switched off in WolfRAT.")
    assert rig.weather_now() == (None, None) and rig.sky_now() is None
    rig.game("Dale", "!storm 10")
    assert rig.server.chat == ["Weather commands are switched off in WolfRAT."]


def test_no_game_server_on_this_pc(qtbot, tmp_path, monkeypatch):
    rig = CommandRig(qtbot, tmp_path, monkeypatch, local_server=False)
    refused(rig, "!storm 10", "No weather: WolfRAT must run on the server PC.")
    refused(rig, "!quake 5", "No weather: WolfRAT must run on the server PC.")
    refused(rig, "!lightning", "Lightning is not set up on this server.")
    refused(rig, "!lightning red", "Lightning is not set up on this server.")
    refused(rig, "!sky toxic", "Sky: WolfRAT is not running on the server PC.")
    rig.start_outcome = weather.ServerNotRunning("No jointops.exe is running on this PC.")
    refused(rig, "!startgame",
            "Could not start the game early. No jointops.exe is running on this PC.")
    rig.game("Dale", "!startgame")
    assert rig.server.chat == ["Could not start the game early."]
    rig.game("Dale", "!sky toxic")
    assert rig.server.chat == ["Sky: WolfRAT is not running on the server PC."]


def test_weather_is_not_wired_at_all(rig):
    rig.server._weather_tab = None
    refused(rig, "!storm 10", "Weather is not available.")
    rig.game("Dale", "!storm 10")
    assert rig.server.chat == ["Weather is not available."]


@pytest.mark.parametrize("kind, line", [
    (start_game.RUNNING, "The game has already started."),
    (start_game.ALMOST, "The game is about to start."),
])
def test_startgame_with_nothing_to_cut_short(rig, kind, line):
    rig.start_outcome = start_game.Outcome(kind, 0)
    refused(rig, "!startgame", line)
    rig.game("Dale", "!startgame")
    assert rig.server.chat == [line]


def test_a_vote_that_is_already_running(rig):
    rig.map_vote._vote_active = True
    refused(rig, "!startvote", "Vote is already running.")
    assert rig.map_vote.started == 0
    rig.game("Dale", "!startvote")
    assert rig.server.chat == ["Vote is already running."]
    rig.server._map_voting_tab = None
    refused(rig, "!startvote", "Map voting tab not found.")


def test_team_moves_on_a_co_op_map(rig):
    rig.server.coop = True
    for text in ("!swap Griefer", "!mixteams", "!balanceteams"):
        refused(rig, text, coop_guard.REFUSAL)
        rig.game("Dale", text)
        assert rig.server.chat == [coop_guard.REFUSAL] and rig.server.calls == []


def test_map_votes(rig):
    rig.server.players = rig.server.players[:1]
    refused(rig, "!vote crossroads", "Need at least 2 players to vote")
    rig.server.players = []
    refused(rig, "!skip", "Need at least 1 player to vote")
    rig.tab._skip_cooldown_until = __import__("time").time() + 125
    answer = rig.phone("!skip")
    assert answer["ok"] is False and answer["error"].startswith("Skip on cooldown - 2m ")
    assert rig.server.chat == []
    rig.tab._vote_active = True
    refused(rig, "!vote crossroads", "Vote already in progress")
    rig.tab._vote_active = False
    rig.tab._skip_active = True
    refused(rig, "!skip", "Skip vote already in progress")


def test_votes_and_stats_switched_off_say_so_on_the_phone_only(rig):
    rig.tab._vote_enabled = rig.tab._skip_enabled = False
    refused(rig, "!vote crossroads", "Map voting is switched off in WolfRAT.")
    refused(rig, "!skip", "Skip voting is switched off in WolfRAT.")
    rig.tab.messages_tab._kd_enabled = False
    refused(rig, "!stats Griefer", "Stats are switched off in WolfRAT.")
    refused(rig, "!rank Griefer", "No rank to show: !rank is off or there are no stats yet.")
    rig.tab.messages_tab = None
    refused(rig, "!kd Griefer", "WolfRAT has no stats to show.")
    for text in ("!vote crossroads", "!skip", "!stats Griefer", "!rank Griefer"):
        rig.game("Griefer", text)                # in the game these stay silent
        assert rig.server.chat == []


def test_stats_from_the_career_panel(rig, qtbot, tmp_path):
    from wolfrat.career_panel import CareerPanel

    folder = tmp_path / "career"
    folder.mkdir()
    panel = CareerPanel(str(folder))
    qtbot.addWidget(panel)
    panel._timer.stop()
    panel.tracker.seen = True                    # it has read a server
    rig.tab.career_panel = panel
    refused(rig, "!stats Nobody", "No stats yet for Nobody")
    refused(rig, "!rank Nobody", "No stats yet for Nobody")
    rig.game("Dale", "!stats Nobody")
    assert rig.server.chat == ["No stats yet for Nobody"]
    rig.tab.career_panel = SimpleNamespace(reply=lambda command, target, limit: ["Kestrel: 5 kills"])
    assert rig.phone("!stats Kestrel") == {"ok": True, "command": "!stats"}
    assert rig.server.chat == ["Kestrel: 5 kills"]             # the answer is public, as before
    panel.shutdown()


def test_several_maps_fit_the_name(rig):
    rig.tab.missions_store._data["available"] += [
        {"name": "TD - Sulphur Creek", "file": "TD-SulphurCreek.bms"},
        {"name": "DM - Sulphur Creek", "file": "DM-SulphurCreek.bms"},
    ]
    refused(rig, "!map sulphur", "Which one? TD - Sulphur Creek, DM - Sulphur Creek")
    rig.game("Dale", "!map sulphur")
    assert rig.server.chat == ["Which one? TD - Sulphur Creek, DM - Sulphur Creek"]


# ---- what happened is still said in game chat -------------------------------------------

@pytest.mark.parametrize("text, said, call", [
    ("!kick Griefer", ["Griefer was kicked by a mod"], "punt_player"),
    ("!ban Griefer", ["Griefer was banned by a mod"], "punt_player"),
    ("!kill Griefer", ["Griefer was killed by a mod"], "kill_player"),
    ("!swap Griefer", ["Griefer was swapped by a mod"], "swap_and_kill"),
    ("!gametime 30", ["Game time set to 30 minutes"], "set_setting"),
    ("!time 1830", ["Time set to 18:30"], "set_time_of_day"),
    ("!next", ["Skipping to next map..."], "cycle_mission"),
    ("!map rapid strike", ["Switching to Rapid Strike..."], "switch_mission"),
    ("!setnext crossroads", ["Next map: Crossroads (added to rotation)"], "set_next_mission"),
    ("!add crossroads", ["Added Crossroads to rotation"], "add_mission"),
    ("!remove rapidstrike", ["Removed Rapid Strike from rotation"], "remove_mission"),
    ("!storm 10", ["Weather: storm rolling in for 10 min."], None),
    ("!clear 30", ["Clear skies for 30 min."], None),
    ("!quake 5", ["Earthquake!"], None),
    ("!sky toxic", ["Sky: Toxic until the map changes."], None),
    ("!weather on rare", ["Weather ON: rare - a front every 25-60 min"], None),
    ("!weather status", ["Weather is off. Mods can type !weather on"], None),
    ("!list", ["Rotation: 1.rapidstrike"], None),
    ("!startgame", ["Game starting in 5 seconds!"], None),
    ("!startvote", ["Mod Web Admin forced an early end-of-match map vote."], None),
])
def test_what_happened_is_still_said_in_game_chat(rig, text, said, call):
    assert rig.phone(text) == {"ok": True, "command": text.split()[0]}
    assert rig.server.chat == said
    assert rig.server.names() == ([call] if call else [])


def test_a_weather_change_with_announcements_off_still_gets_its_short_line(rig):
    rig.weather.announce_cb.setChecked(False)
    rig.weather.sky_page.announce_cb.setChecked(False)
    assert rig.phone("!storm 10")["ok"] is True
    assert rig.server.chat == ["Weather changed."]
    assert rig.phone("!sky toxic")["ok"] is True
    assert rig.server.chat == ["Sky: Toxic."]
    assert rig.phone("!sky off")["ok"] is True
    assert rig.server.chat == ["Sky back to normal."]


def test_a_command_run_straight_on_the_tab_still_answers_in_chat(rig):
    """Without the phone's reply list a refusal goes where it always went."""
    rig.forget()
    rig.tab._check_mod_command("!gametime 999", typed_on_phone=True)
    assert rig.server.chat == ["Game time must be 1-240 minutes"]


# ---- the Weather tab says which replies are refusals --------------------------------------

def test_the_weather_tab_marks_refusals(rig):
    tab = rig.weather
    assert tab.mod_command("Dale", "!storm", ["soon"]) == (
        "Usage: !storm [minutes] [100m|thick|light]", True)
    assert tab.mod_command("Dale", "!storm", ["10"]) == (None, False)
    assert tab.mod_command("Dale", "!weather", ["status"]) == (
        "Weather is off. Mods can type !weather on", False)
    assert tab.mod_command("Dale", "!weather", ["off"]) == ("Weather is already off.", True)
    assert tab.mod_command("Dale", "!weather", ["on"])[1] is False
    assert tab.mod_command("Dale", "!weather", ["off"]) == (
        "Weather OFF - the sky goes back to the map's own.", False)
    assert tab.mod_command("Dale", "!lightning", ["mauve"]) == (sky.LIGHTNING_USAGE, True)
    assert tab.mod_command("Dale", "!lightning", ["far"]) == (None, False)
    assert tab.mod_command("Dale", "!lightning", ["red"]) == (None, False)
    assert tab.mod_command("Dale", "!lightning", ["blue"]) == (
        "Lightning: wait for the last strike to finish.", True)
    tab.announce_cb.setChecked(False)
    assert tab.mod_command("Dale", "!quake", ["3"]) == ("Weather changed.", False)


@pytest.mark.parametrize("announce", [True, False])
@pytest.mark.parametrize("ready", [True, False])
def test_the_weather_tab_reads_sky_replies_the_way_the_sky_page_gives_them(
        qtbot, tmp_path, monkeypatch, announce, ready):
    rig = CommandRig(qtbot, tmp_path, monkeypatch, local_server=ready)
    page = rig.weather.sky_page
    page.announce_cb.setChecked(announce)
    # with announcements off the Sky page gives the mod a short line when it worked
    worked = set() if announce else {"Sky back to normal.", "Sky demo started.", "Sky: Toxic.",
                                     "Sky: Blood Moon."}
    seen = set()
    for args in ([], ["help"], ["lava"], ["toxic", "999"], ["list"], ["off"], ["toxic"],
                 ["blood", "moon", "5"], ["demo"]):
        reply, was_refused = rig.weather.mod_command("Dale", "!sky", list(args))
        assert was_refused is (reply is not None and reply not in worked), (args, reply)
        seen.add(was_refused)
    assert seen == {True, False}
    page.mods_cb.setChecked(False)
    assert rig.weather.mod_command("Dale", "!sky", ["off"]) == (
        "Sky commands are switched off in WolfRAT.", True)
    page.keeper.stop_demo(quiet=True)


# ---- through the web server, the way the page sends it ---------------------------------------

class Manager:
    is_connected = True
    players = []
    player_entries = ()
    chat_messages = []
    game_state = {}
    missions = []
    mission_entries = ()
    available_maps_data = ""
    game_settings = {}

    def __init__(self):
        self.sent = []

    def send_chat(self, line):
        self.sent.append(line)


def test_the_reason_reaches_the_phone_page(rig):
    manager = Manager()
    web = WolfWebServer(manager, host="127.0.0.1", port=0)

    def runner(text):                       # the main window runs it on its own thread
        done = Future()
        done.set_result(rig.tab.run_web_command(text))
        return done

    web.on_command = runner
    rig.forget()
    payload = asyncio.run(web._phone_message("!gametime 999"))
    assert payload == {"ok": False, "accepted": False, "replies": [], "command": "!gametime",
                       "error": "Game time must be 1-240 minutes"}
    payload = asyncio.run(web._phone_message("!kick Nobody"))
    assert payload["ok"] is False and payload["error"] == "Player not found: Nobody"
    assert rig.server.chat == [] and manager.sent == []
    payload = asyncio.run(web._phone_message("!gametime 30"))
    assert payload == {"ok": True, "accepted": True, "replies": ["Run by WolfRAT"],
                       "command": "!gametime"}
    assert rig.server.chat == ["Game time set to 30 minutes"] and manager.sent == []
