"""Loading a preset that names a map the server no longer has.

A map taken out of the JO folder disappears from MISSION AVAILABLE after a
restart but stays in the saved preset. The preset load used to stop at the
first refused MISSION ADD, so a missing first map left the rotation empty.
"""

import json
from concurrent.futures import Future

from wolfrat import app as app_module
from wolfrat.admin_commands import AvailableMission
from wolfrat.admin_session import CommandResult
from wolfrat.app import MissionsStore, MissionsTab
from wolfrat.runtime import DesktopRuntime


def _done(value):
    future = Future()
    future.set_result(value)
    return future


class FakeServer:
    def __init__(self, on_server):
        self.is_connected = True
        self.mission_entries = []
        self.available = tuple(
            AvailableMission(
                catalog_index=index, filename=name, description=name, revision=1
            )
            for index, name in enumerate(on_server, start=1)
        )
        self.available_mission_entries = ()  # not polled yet
        self.added = []
        self.logs = []

    def _log(self, message, *args, **kwargs):
        self.logs.append(message)

    def refresh_available_maps(self, quiet=False):
        self.available_mission_entries = self.available
        return _done(CommandResult(
            operation="MISSION AVAILABLE", replies=(), accepted=True,
            value=self.available,
        ))

    def add_mission(self, filename, auto_switch_sides=False):
        names = {m.filename.casefold() for m in self.available_mission_entries}
        if filename.casefold() not in names:
            raise ValueError(
                f"mission {filename!r} is not in MISSION AVAILABLE snapshot"
            )
        self.added.append(filename)
        return _done(CommandResult(
            operation="MISSION ADD", replies=("OK",), accepted=True,
            verified=True,
        ))

    def refresh_missions(self, quiet=False):
        return _done(None)

    def refresh_game_state(self, quiet=False):
        return _done(None)


PRESET = [
    {"file": "GONE.npj", "count": 1},
    {"file": "A.bms", "count": 1},
    {"file": "B.bms", "count": 2},
]


def _tab(qtbot, tmp_path, server, monkeypatch, choice=None, warnings=None):
    rt = DesktopRuntime.isolated(tmp_path)
    (tmp_path / "wolfrat_rotations.json").write_text(json.dumps({"mine": PRESET}))
    tab = MissionsTab(server, MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    tab.preset_combo.setCurrentText("mine")
    asked = []
    monkeypatch.setattr(
        tab, "_ask_about_missing_maps",
        lambda name, missing: asked.append(list(missing)) or choice,
    )
    sink = warnings if warnings is not None else []
    monkeypatch.setattr(
        app_module.QMessageBox, "warning",
        lambda *args, **kwargs: sink.append(args[2]),
    )
    monkeypatch.setattr(
        app_module.QMessageBox, "information",
        lambda *args, **kwargs: sink.append(args[2]),
    )
    monkeypatch.setattr(
        app_module.QMessageBox, "question",
        lambda *args, **kwargs: app_module.QMessageBox.StandardButton.Yes,
    )
    return tab, asked


def _saved(tmp_path):
    return json.loads((tmp_path / "wolfrat_rotations.json").read_text())["mine"]


def test_skip_and_load_adds_the_rest_and_keeps_the_preset(qtbot, tmp_path, monkeypatch):
    server = FakeServer(["A.bms", "B.bms"])
    tab, asked = _tab(qtbot, tmp_path, server, monkeypatch, choice="skip")
    tab._load_preset()
    qtbot.waitUntil(lambda: server.added == ["A.bms", "B.bms"], timeout=3000)
    assert asked == [["GONE.npj"]]
    assert _saved(tmp_path) == PRESET


def test_remove_and_load_drops_the_map_from_the_saved_preset(qtbot, tmp_path, monkeypatch):
    server = FakeServer(["A.bms", "B.bms"])
    tab, asked = _tab(qtbot, tmp_path, server, monkeypatch, choice="remove")
    tab._load_preset()
    qtbot.waitUntil(lambda: server.added == ["A.bms", "B.bms"], timeout=3000)
    assert _saved(tmp_path) == PRESET[1:]
    assert tab._presets["mine"] == PRESET[1:]


def test_cancel_adds_nothing_and_changes_nothing(qtbot, tmp_path, monkeypatch):
    server = FakeServer(["A.bms", "B.bms"])
    tab, asked = _tab(qtbot, tmp_path, server, monkeypatch, choice=None)
    tab._load_preset()
    qtbot.waitUntil(lambda: asked == [["GONE.npj"]], timeout=3000)
    qtbot.wait(200)
    assert server.added == []
    assert _saved(tmp_path) == PRESET


def test_matching_is_case_insensitive_and_no_prompt_when_all_present(qtbot, tmp_path, monkeypatch):
    server = FakeServer(["gone.NPJ", "A.bms", "B.bms"])
    tab, asked = _tab(qtbot, tmp_path, server, monkeypatch, choice="remove")
    tab._load_preset()
    qtbot.waitUntil(lambda: len(server.added) == 3, timeout=3000)
    assert asked == []
    assert _saved(tmp_path) == PRESET


def test_empty_map_list_never_offers_to_remove_anything(qtbot, tmp_path, monkeypatch):
    server = FakeServer([])  # server has not answered with any maps
    warnings = []
    tab, asked = _tab(
        qtbot, tmp_path, server, monkeypatch, choice="remove", warnings=warnings
    )
    tab._load_preset()
    qtbot.waitUntil(lambda: bool(warnings), timeout=3000)
    assert "has not received the server's map list" in warnings[0]
    assert asked == []
    assert server.added == []
    assert _saved(tmp_path) == PRESET


def test_a_refused_add_is_skipped_not_the_end_of_the_preset(qtbot, tmp_path, monkeypatch):
    # The map is on the list when checked, then refused when added.
    server = FakeServer(["GONE.npj", "A.bms", "B.bms"])
    real_add = server.add_mission

    def add(filename, auto_switch_sides=False):
        if filename == "GONE.npj":
            raise ValueError("File Not Found")
        return real_add(filename, auto_switch_sides)

    server.add_mission = add
    warnings = []
    tab, asked = _tab(
        qtbot, tmp_path, server, monkeypatch, choice="skip", warnings=warnings
    )
    tab._load_preset()
    qtbot.waitUntil(lambda: server.added == ["A.bms", "B.bms"], timeout=3000)
    qtbot.waitUntil(lambda: bool(warnings), timeout=3000)
    assert "GONE.npj" in warnings[0]


def test_not_connected_does_nothing(qtbot, tmp_path, monkeypatch):
    server = FakeServer(["A.bms", "B.bms"])
    server.is_connected = False
    warnings = []
    tab, asked = _tab(
        qtbot, tmp_path, server, monkeypatch, choice="skip", warnings=warnings
    )
    tab._load_preset()
    assert warnings == ["Connect to a server first."]
    assert server.added == []
