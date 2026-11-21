"""Load Preset can't be half-wiped any more (SgtMudd, 2026-11-21).

His log: he answered "Add to current rotation?" (the box said "Click Clear at
the bottom right to remove it"), then pressed Clear while the load was still
adding. Clear wiped the maps already added, the load carried on into the
empty list, and 177 of 219 maps were left (20 Nov 20:31:44, map 42). His
saved list then shrank because Save stores the half-loaded table.

1. Load asks Replace / Add to the end (Replace clears first, in order).
2. While a load or Edit Order is putting maps on the server, Clear, Load,
   Edit Order, Remove, x1/x2, Save and Save Auto Rotation wait for it.
3. Save asks before changing how many maps a saved list holds.
"""

import json
from concurrent.futures import Future

import pytest
from PyQt6.QtWidgets import QInputDialog

from wolfrat import app as app_module
from wolfrat.admin_commands import AvailableMission, MissionEntry
from wolfrat.admin_session import CommandResult
from wolfrat.app import MissionsStore, MissionsTab
from wolfrat.runtime import DesktopRuntime


def _done(value):
    future = Future()
    future.set_result(value)
    return future


def _ok(operation):
    return CommandResult(operation=operation, replies=("OK",), accepted=True, verified=True)


class FakeServer:
    def __init__(self, queue, catalog):
        self.is_connected = True
        self.available_mission_entries = tuple(
            AvailableMission(catalog_index=i, filename=f, description=f, revision=1)
            for i, f in enumerate(catalog, start=1)
        )
        self.queue = list(queue)
        self.calls = []
        self.logs = []
        self.hold_adds = False
        self.held = []

    @property
    def mission_entries(self):
        return tuple(
            MissionEntry(queue_index=i, filename=f, revision=1)
            for i, f in enumerate(self.queue)
        )

    def _log(self, message, *args, **kwargs):
        self.logs.append(message)

    def refresh_available_maps(self, quiet=False):
        return _done(CommandResult(
            operation="MISSION AVAILABLE", replies=(), accepted=True,
            value=self.available_mission_entries,
        ))

    def refresh_missions(self, quiet=False):
        return _done(None)

    def refresh_game_state(self, quiet=False):
        return _done(None)

    def clear_missions(self):
        self.calls.append(("CLEAR",))
        self.queue = []
        return _done(_ok("MISSION CLEAR"))

    def add_mission(self, filename, auto_switch_sides=False):
        self.calls.append(("ADD", filename))
        self.queue.append(filename)
        if self.hold_adds:
            future = Future()
            self.held.append(future)
            return future
        return _done(_ok("MISSION ADD"))

    def remove_mission(self, mission):
        self.calls.append(("REMOVE", mission.filename))
        return _done(_ok("MISSION REMOVE"))

    def set_mission_side_switch(self, mission, switch_sides):
        self.calls.append(("X2", mission.filename, switch_sides))
        return _done(_ok("MISSION ADD"))


OLD = ["OLD1.bms", "OLD2.bms", "OLD3.bms"]
PRESET = [{"file": "A.bms", "count": 1}, {"file": "B.bms", "count": 1},
          {"file": "C.bms", "count": 1}]


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for kind in ("information", "warning"):
        monkeypatch.setattr(
            app_module.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: shown.append((_kind, args[2])),
        )
    return shown


def _tab(qtbot, tmp_path, queue=OLD, presets=None):
    rt = DesktopRuntime.isolated(tmp_path)
    (tmp_path / "wolfrat_rotations.json").write_text(
        json.dumps(presets if presets is not None else {"1-coop": PRESET}), encoding="utf-8"
    )
    server = FakeServer(queue, OLD + ["A.bms", "B.bms", "C.bms"])
    tab = MissionsTab(server, MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    tab.preset_combo.setCurrentText("1-coop")
    return tab, server


def _choose(monkeypatch, tab, choice):
    asked = []
    monkeypatch.setattr(
        tab, "_ask_replace_or_add",
        lambda name, count: asked.append((name, count)) or choice,
    )
    return asked


# ---- 1. Replace / Add to the end ---------------------------------------

def test_replace_clears_first_then_adds_the_list_in_order(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    asked = _choose(monkeypatch, tab, "replace")
    tab._load_preset()
    qtbot.waitUntil(lambda: not tab._feed_active and len(server.calls) == 4, timeout=3000)
    assert asked == [("1-coop", 3)]
    assert server.calls == [("CLEAR",), ("ADD", "A.bms"), ("ADD", "B.bms"), ("ADD", "C.bms")]
    assert server.queue == ["A.bms", "B.bms", "C.bms"]
    assert any("replacing the cycle, adding 3 maps" in m for m in server.logs)
    assert any(m == "Preset '1-coop' loaded." for m in server.logs)


def test_add_to_the_end_keeps_the_old_maps(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _choose(monkeypatch, tab, "add")
    tab._load_preset()
    qtbot.waitUntil(lambda: not tab._feed_active and len(server.calls) == 3, timeout=3000)
    assert ("CLEAR",) not in server.calls
    assert server.queue == OLD + ["A.bms", "B.bms", "C.bms"]


def test_cancel_loads_nothing(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _choose(monkeypatch, tab, None)
    tab._load_preset()
    qtbot.wait(50)
    assert server.calls == [] and not tab._feed_active


def test_an_empty_cycle_is_not_asked_about(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path, queue=[])
    asked = _choose(monkeypatch, tab, "replace")
    tab._load_preset()
    qtbot.waitUntil(lambda: not tab._feed_active and len(server.calls) == 3, timeout=3000)
    assert asked == []
    assert server.calls == [("ADD", "A.bms"), ("ADD", "B.bms"), ("ADD", "C.bms")]


# ---- 2. Nothing changes the list while a feed runs ---------------------

def _start_held_load(qtbot, tab, server, monkeypatch):
    server.hold_adds = True
    _choose(monkeypatch, tab, "replace")
    tab._load_preset()
    qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
    assert tab._feed_active


def test_every_list_change_waits_while_a_load_is_running(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _start_held_load(qtbot, tab, server, monkeypatch)
    sent = list(server.calls)
    mission = MissionEntry(queue_index=0, filename="A.bms", revision=1)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("1-coop", True)))
    monkeypatch.setattr(app_module.QMessageBox, "question",
                        staticmethod(lambda *a, **k: app_module.QMessageBox.StandardButton.Yes))

    tab._clear_rotation()
    tab._load_preset()
    tab._open_order_editor()
    tab._remove_mission(mission)
    tab._set_side_switch(mission, True)
    tab._save_preset()
    tab._ok_clicked()

    assert server.calls == sent  # nothing more went to the server
    assert len(boxes) == 7
    assert all("still putting maps on the server (loading '1-coop': 0 of 3 done)" in text
               for _kind, text in boxes)
    with open(tmp_path / "wolfrat_rotations.json", encoding="utf-8") as handle:
        assert json.load(handle) == {"1-coop": PRESET}  # Save and Save Auto did nothing


def test_the_guard_shows_progress_and_frees_up_when_the_load_ends(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _start_held_load(qtbot, tab, server, monkeypatch)
    server.held.pop(0).set_result(_ok("MISSION ADD"))
    qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
    tab._clear_rotation()
    assert "1 of 3 done" in boxes[-1][1]
    for _ in range(2):
        qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
        server.held.pop(0).set_result(_ok("MISSION ADD"))
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=2000)

    monkeypatch.setattr(app_module.QMessageBox, "question",
                        staticmethod(lambda *a, **k: app_module.QMessageBox.StandardButton.Yes))
    tab._clear_rotation()
    assert server.calls[-1] == ("CLEAR",)


def test_a_dropped_connection_or_a_failed_clear_frees_the_list(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _choose(monkeypatch, tab, "add")
    original = server.add_mission

    def add(filename, auto_switch_sides=False):
        if filename == "B.bms":
            server.is_connected = False
            raise ConnectionError("connection lost")
        return original(filename, auto_switch_sides)

    server.add_mission = add
    # Start the load itself (Load first reads the catalog asynchronously,
    # so waiting for the lock to be free could pass before it was taken).
    tab._start_preset_load("1-coop", PRESET)
    assert tab._feed_active
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert any("connection to the server was lost" in text for _kind, text in boxes)

    server.is_connected = True

    def refuse():
        raise RuntimeError("server said no")

    server.clear_missions = refuse
    _choose(monkeypatch, tab, "replace")
    tab._start_preset_load("1-coop", PRESET)
    assert tab._feed_active
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert server.queue == OLD + ["A.bms"]  # the failed Replace added nothing


def test_edit_order_takes_the_same_lock(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    server.hold_adds = True
    monkeypatch.setattr(tab, "_run_order_dialog", lambda entries: list(reversed(entries)))
    tab._open_order_editor()
    qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
    tab._load_preset()
    assert "still putting maps on the server (the new map order: 0 of 3 done)" in boxes[-1][1]


# ---- 3. Save asks before changing a saved list's size ------------------

def _save_as(monkeypatch, tab, name, answer):
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
    monkeypatch.setattr(tab, "_confirm_overwrite",
                        lambda n, old, new: asked.append((n, old, new)) or answer)
    tab._save_preset()
    return asked


def _fill_table(tab, files):
    tab.rotation_table.setRowCount(0)
    for f in files:
        tab._add_rotation_row(f, 1)


def _saved(tmp_path):
    with open(tmp_path / "wolfrat_rotations.json", encoding="utf-8") as handle:
        return json.load(handle)


def test_save_asks_before_shrinking_a_saved_list_and_no_keeps_it(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _fill_table(tab, ["A.bms", "B.bms"])
    asked = _save_as(monkeypatch, tab, "1-coop", False)
    assert asked == [("1-coop", 3, 2)]
    assert _saved(tmp_path)["1-coop"] == PRESET
    assert any("Kept the saved '1-coop' (3 maps)" in m for m in server.logs)

    asked = _save_as(monkeypatch, tab, "1-coop", True)
    assert [e["file"] for e in _saved(tmp_path)["1-coop"]] == ["A.bms", "B.bms"]


def test_save_does_not_ask_for_a_new_name_or_the_same_size(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    _fill_table(tab, ["C.bms", "B.bms", "A.bms"])
    assert _save_as(monkeypatch, tab, "1-coop", False) == []  # same count: a reorder
    assert _save_as(monkeypatch, tab, "brand new", False) == []
    saved = _saved(tmp_path)
    assert [e["file"] for e in saved["1-coop"]] == ["C.bms", "B.bms", "A.bms"]
    assert "brand new" in saved
