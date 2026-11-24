"""A Mods tab that types chat commands against a made-up game server.

The real Mods, Weather (with its Sky page) and Bans tabs, wired the way the
main window wires them.  The game server only writes down what it is asked to
do: nothing here talks to a real one.
"""
import json
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tests.test_sky import FakeSkyServer  # noqa: E402
from tests.test_weather import FakeServer as WeatherMemory  # noqa: E402
from wolfrat import app as app_module  # noqa: E402
from wolfrat import sky  # noqa: E402
from wolfrat import weather  # noqa: E402
from wolfrat.admin_commands import MissionEntry  # noqa: E402
from wolfrat.app import DesktopRuntime, MissionsStore, ModsTab  # noqa: E402
from wolfrat.bans_tab import BansTab  # noqa: E402
from wolfrat.jo_players import SlotInfo  # noqa: E402
from wolfrat.mod_ranks import ADMIN  # noqa: E402
from wolfrat.weather_tab import WeatherTab  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

PLAYERS = (("Ham", "10.0.0.3"), ("Dale", "10.0.0.4"), ("Griefer", "5.6.7.8"))
MAPS = (("Crossroads", "crossroads.bms"), ("Rapid Strike", "rapidstrike.bms"))
ROTATION = ("rapidstrike.bms",)


class GameServer:
    """Writes down every admin call; ``chat`` is what went to game chat."""

    ADMIN_CALLS = (
        "warn_player", "punt_player", "kill_player", "swap_and_kill", "cycle_mission",
        "switch_mission", "set_next_mission", "add_mission", "remove_mission",
        "set_setting", "set_time_of_day", "shuffle_teams", "mix_teams",
    )

    def __init__(self):
        self.players = [
            {"name": name, "id": number, "team": 1, "revision": 7, "ping": "40"}
            for number, (name, _ip) in enumerate(PLAYERS, 3)
        ]
        self.missions = []
        self.mission_entries = [MissionEntry(index, name) for index, name in enumerate(ROTATION)]
        self.chat, self.calls = [], []
        self.coop = False
        for name in self.ADMIN_CALLS:
            setattr(self, name, self._recorder(name))

    def _recorder(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, *args, *(f"{k}={v}" for k, v in kwargs.items())))
        return call

    def _log(self, message):
        pass

    def send_chat(self, message):
        self.chat.append(message)

    def swaps_blocked(self):
        return self.coop

    def names(self):
        """The admin calls made, by name."""
        return [call[0] for call in self.calls]


class ServerMemory(WeatherMemory):
    """The server's memory as the Weather tab and its Sky page read it."""

    pid = 77

    def __init__(self, folder):
        super().__init__(addon=True)
        self.cells.update(FakeSkyServer().cells)
        self.folder = folder

    def exe_path(self):
        return str(self.folder / "jointops.exe")

    def close(self):
        pass


class SlotReader:
    """Where the Bans tab gets players' addresses from."""

    status, available = "fake", True

    def read(self):
        return [SlotInfo(0, "Host", "")] + [
            SlotInfo(index, name, ip) for index, (name, ip) in enumerate(PLAYERS, 1)
        ]


class CommandRig:
    """``phone(text)`` types on the phone page, ``game(name, text)`` in the game."""

    def __init__(self, qtbot, tmp_path, monkeypatch, *, local_server=True):
        self.folder = tmp_path
        self.rt = DesktopRuntime.isolated(tmp_path)
        with open(self.rt.path("wolfrat_mods.json"), "w") as handle:
            json.dump({"mods": ["Dale", "Ham"], "mod_ranks": {"Dale": ADMIN}}, handle)
        self.server = GameServer()
        self.tab = ModsTab(self.server, MissionsStore(self.rt), self.rt)
        qtbot.addWidget(self.tab)
        self.tab.missions_store._data["available"] = [
            {"name": name, "file": file} for name, file in MAPS
        ]
        self.tab.missions_store._data["rotation"] = [
            {"name": file[:-4], "file": file} for file in ROTATION
        ]

        # The server takes every command at once: no admin connection here.
        def submit_admin(owner, operation, on_success=None, context="", on_failure=None,
                         policy=None):
            result = operation()
            if on_success is not None:
                on_success(result)

        def submit_team_workflow(owner, operation, on_success, context):
            operation()
            on_success(())

        monkeypatch.setattr(app_module, "submit_admin", submit_admin)
        monkeypatch.setattr(app_module, "submit_team_workflow", submit_team_workflow)
        monkeypatch.setattr(app_module.start_game, "start_local_server", self._start_game)
        self.start_outcome = app_module.start_game.Outcome(app_module.start_game.STARTED, 30)

        folder = tmp_path / "server"
        folder.mkdir()
        self.memory = ServerMemory(folder) if local_server else None
        if local_server:
            sky.install_block(folder)

        def attach(writable=True):
            if self.memory is None:
                raise weather.ServerNotRunning("No jointops.exe is running on this PC.")
            controller = weather.WeatherController(self.memory)
            controller.verify()
            return controller, self.memory

        self.weather = WeatherTab(self.rt, send_chat=self.server.send_chat, attach=attach,
                                  server_pids=lambda: [])
        qtbot.addWidget(self.weather)
        self.weather._timer.stop()
        self.weather.sky_page._timer.stop()
        self.weather._poll()
        self.weather.mods_cb.setChecked(True)
        self.weather.sky_page.mods_cb.setChecked(True)
        self.server._weather_tab = self.weather
        if local_server:
            # the lightning add-on answers "are you there?"
            controller = self.weather._ensure(writable=True)
            controller.probe_addon()
            self.memory.run_script()
            controller.probe_addon()
            self.weather._poll()

        self.ban_log = []
        self.bans = BansTab(str(tmp_path), punt=lambda player, why: None,
                            announce=self.server.send_chat, log=self.ban_log.append,
                            reader=SlotReader(), admin_name="Dale")
        qtbot.addWidget(self.bans)
        self.bans._timer.stop()
        self.bans.on_players(list(self.server.players))
        self.server._bans_tab = self.bans

        class MapVote:
            _vote_active = False
            started = 0

            def _start_vote(self):
                self.started += 1

        self.map_vote = MapVote()
        self.server._map_voting_tab = self.map_vote

    def _start_game(self):
        if isinstance(self.start_outcome, Exception):
            raise self.start_outcome
        return self.start_outcome

    def forget(self):
        self.server.chat.clear()
        self.server.calls.clear()

    def phone(self, text):
        """Type ``text`` in the phone page's chat box; what the phone gets back."""
        self.forget()
        return self.tab.run_web_command(text)

    def game(self, name, text):
        """``name`` types ``text`` in the game's chat."""
        self.forget()
        self.tab._check_mod_command(f"{name}: {text}")

    def weather_now(self):
        """(the sky WolfRAT holds, seconds it has left) from the Weather tab."""
        schedule = self.weather._schedule
        return schedule.active, schedule.seconds_left()

    def sky_now(self):
        """(scene key, seconds it lasts or None) of the sky the Sky page holds."""
        active = self.weather.sky_page.keeper.active
        if active is None:
            return None
        left = None if active.until is None else round(active.until - self.weather.sky_page._clock())
        return active.scene.key, left

    def last_log(self):
        log = self.tab.mod_log
        return log.item(log.count() - 1).text() if log.count() else ""
