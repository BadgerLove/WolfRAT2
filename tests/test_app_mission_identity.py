"""Behavioral contract for identity-bearing desktop mission rows.

PyQt is optional in the deterministic test environment.  This test loads the
desktop module with inert Qt types, then exercises the same row-action methods
that the three Mission Cycle controls invoke.
"""

from concurrent.futures import Future
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from wolfrat.admin_commands import MissionEntry


ROOT = Path(__file__).resolve().parents[1]


class _QtStub:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _QtStub()

    def __getattr__(self, _name):
        return _QtStub()

    def __or__(self, _other):
        return self

    def __ror__(self, _other):
        return self

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False


class _SignalStub(_QtStub):
    def connect(self, *args, **kwargs):
        pass

    def emit(self, *args, **kwargs):
        pass


class _QtModule(types.ModuleType):
    def __getattr__(self, name):
        if name == "pyqtSignal":
            return lambda *args, **kwargs: _SignalStub()
        if name == "Qt":
            return _QtStub()
        return type(name, (_QtStub,), {})


def _load_desktop_module():
    modules = {
        name: _QtModule(name)
        for name in (
            "PyQt6",
            "PyQt6.QtWidgets",
            "PyQt6.QtCore",
            "PyQt6.QtGui",
            "PyQt6.QtMultimedia",
        )
    }
    sounds = types.ModuleType("wolfrat.sounds")
    sounds.generate_all_sounds = lambda: {}
    modules["wolfrat.sounds"] = sounds

    protocol = types.ModuleType("wolfrat.protocol")
    protocol.ServerManager = type("ServerManager", (), {})
    protocol.wire_log = lambda _message: None
    protocol.CHAT_MAX_LEN = 62
    protocol.player_entry_from_legacy = lambda player: player
    protocol.one_chat_line = lambda line, fallback: line
    modules["wolfrat.protocol"] = protocol

    web_server = types.ModuleType("wolfrat.web_server")
    web_server.WolfWebServer = type("WolfWebServer", (), {})
    web_server.generate_token = lambda: "test-token"
    modules["wolfrat.web_server"] = web_server

    spec = importlib.util.spec_from_file_location(
        "_wolfrat_app_mission_identity_test",
        ROOT / "wolfrat" / "app.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class _FakeServer:
    def __init__(self, missions):
        self.mission_entries = tuple(missions)
        self.calls = []

    def _accepted(self, operation, mission):
        self.calls.append((operation, mission))
        future = Future()
        future.set_result(object())
        return future

    def switch_mission(self, mission):
        return self._accepted("switch", mission)

    def set_next_mission(self, mission, *, add_if_missing=False):
        if add_if_missing:
            raise AssertionError("an existing queue identity must not be re-added")
        return self._accepted("set_next", mission)

    def remove_mission(self, mission):
        return self._accepted("remove", mission)

    def _log(self, _message):
        pass

    def refresh_missions(self):
        raise AssertionError("an authoritative row must not need re-resolution")


class _FakeRotationTable:
    def __init__(self, row_count):
        self.row_count = row_count

    def currentRow(self):
        return -1

    def rowCount(self):
        return self.row_count

    def setRowCount(self, count):
        self.row_count = count


class DesktopMissionIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load_desktop_module()

    def test_selected_second_duplicate_targets_its_identity_for_every_action(self):
        first = MissionEntry(
            queue_index=4, filename="CP08.BMS", revision=7
        )
        second = MissionEntry(
            queue_index=9, filename="CP08.BMS", revision=7
        )
        server = _FakeServer((first, second))
        tab = object.__new__(self.app.MissionsTab)
        tab.server = server
        tab._rotation_maps = ["CP08.BMS", "CP08.BMS"]
        tab._rotation_entries = [first, second]

        def submit(_owner, operation, on_success, _context, *args):
            future = operation()
            on_success(future.result())
            return future

        with patch.object(self.app, "submit_admin", submit):
            tab._switch_to_map(1)
            tab._set_next_mission(1)
            tab._remove_from_rotation_at(1)

        self.assertEqual(
            [
                ("switch", second),
                ("set_next", second),
                ("remove", second),
            ],
            server.calls,
        )

    def test_ambiguous_filename_compatibility_does_not_guess(self):
        first = MissionEntry(queue_index=4, filename="CP08.BMS", revision=7)
        second = MissionEntry(queue_index=9, filename="CP08.BMS", revision=7)
        server = _FakeServer((first, second))
        tab = object.__new__(self.app.MissionsTab)
        tab.server = server
        tab._rotation_maps = ["CP08.BMS", "CP08.BMS"]
        tab._rotation_entries = [first, second]

        with patch.object(
            self.app,
            "submit_admin",
            side_effect=AssertionError("ambiguous input must be rejected"),
        ):
            tab._switch_to_map_by_name("CP08.BMS")
            tab._set_next_mission_by_name("CP08.BMS")
            tab._remove_from_rotation_by_name("CP08.BMS")

        self.assertEqual([], server.calls)

    def test_verified_empty_rotation_clears_stale_rows(self):
        server = _FakeServer(())
        tab = object.__new__(self.app.MissionsTab)
        tab.server = server
        tab.rotation_table = _FakeRotationTable(row_count=2)
        tab._rotation_maps = ["CP08.BMS", "CP09.BMS"]
        tab._rotation_entries = [
            MissionEntry(queue_index=4, filename="CP08.BMS", revision=7),
            MissionEntry(queue_index=9, filename="CP09.BMS", revision=7),
        ]

        tab.update_missions([])

        self.assertEqual([], tab._rotation_maps)
        self.assertEqual([], tab._rotation_entries)
        self.assertEqual(0, tab.rotation_table.rowCount())


if __name__ == "__main__":
    unittest.main()
