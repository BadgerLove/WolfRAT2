"""Edit Order / Shuffle on the Missions tab (SgtMudd's request, 2026-11-21).

The editor reorders a copy of the Mission Cycle (drag and drop, move buttons,
Shuffle as often as wanted); OK clears the server's list and adds the maps
back in the new order, the same feed Load Preset uses.
"""

import os
import random
import subprocess
import sys
import textwrap
from collections import Counter
from concurrent.futures import Future
from pathlib import Path

import pytest
from PyQt6.QtCore import QModelIndex, Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QApplication

from wolfrat import app as app_module
from wolfrat.admin_commands import AvailableMission, MissionEntry
from wolfrat.admin_session import CommandResult
from wolfrat.app import MissionsStore, MissionsTab
from wolfrat.map_order_dialog import (
    MapOrderDialog,
    OrderEntry,
    move_down,
    move_to_bottom,
    move_to_top,
    move_up,
    shuffled,
)
from wolfrat.runtime import DesktopRuntime

REPO = Path(__file__).resolve().parents[1]


def _maps(count):
    return [OrderEntry(f"MAP{i:03d}.BMS", f"AS - Map {i}") for i in range(count)]


# --------------------------------------------------------------------------
# Shuffle and the move helpers
# --------------------------------------------------------------------------

def test_shuffle_keeps_every_map_and_changes_the_order():
    maps = _maps(30)
    mixed = shuffled(maps, random.Random(1))
    assert Counter(mixed) == Counter(maps)
    assert mixed != maps


def test_two_maps_swap_on_every_press():
    two = _maps(2)
    rng = random.Random(7)
    for _ in range(20):
        assert shuffled(two, rng) == two[::-1]


def test_a_list_shuffle_cannot_change_is_left_alone():
    assert shuffled([]) == []
    one = _maps(1)
    assert shuffled(one) == one
    same = [OrderEntry("A.BMS")] * 3
    assert shuffled(same) == same


def test_shuffle_deals_like_a_deck_of_cards():
    """Every map lands in every place about as often (Fisher-Yates)."""
    maps = _maps(5)
    rng = random.Random(42)
    counts = [[0] * 5 for _ in maps]
    for _ in range(5000):
        for place, entry in enumerate(shuffled(maps, rng)):
            counts[maps.index(entry)][place] += 1
    # 1000 each on average (a little under on the map's own place, since the
    # unchanged order is never dealt).
    for row in counts:
        assert all(850 < value < 1150 for value in row), counts


def test_move_helpers_keep_picked_maps_together():
    items = list("ABCDEF")
    assert move_up(items, [2, 4]) == (list("ACBEDF"), [1, 3])
    assert move_up(items, [0, 1]) == (items, [0, 1])
    assert move_up(items, [0, 2]) == (list("ACBDEF"), [0, 1])
    assert move_down(items, [3, 4]) == (list("ABCFDE"), [4, 5])
    assert move_down(items, [5]) == (items, [5])
    assert move_to_top(items, [3, 1]) == (list("BDACEF"), [0, 1])
    assert move_to_bottom(items, [0, 2]) == (list("BDEFAC"), [4, 5])


# --------------------------------------------------------------------------
# The dialog
# --------------------------------------------------------------------------

def _dialog(qtbot, maps, seed=3):
    dialog = MapOrderDialog(maps, rng=random.Random(seed))
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_dialog_opens_with_the_server_order_numbered(qtbot):
    maps = _maps(5)
    dialog = _dialog(qtbot, maps)
    assert dialog.entries() == maps
    assert dialog.map_list.item(0).text().startswith("  1.  AS - Map 0")
    assert "5 maps" in dialog.status_label.text()
    assert not dialog.undo_button.isEnabled()
    assert not dialog.up_button.isEnabled()  # nothing picked yet


def test_shuffle_can_be_pressed_again_and_again(qtbot):
    maps = _maps(40)
    dialog = _dialog(qtbot, maps)
    seen = [maps]
    for _ in range(6):
        qtbot.mouseClick(dialog.shuffle_button, Qt.MouseButton.LeftButton)
        order = dialog.entries()
        assert Counter(order) == Counter(maps)
        assert order not in seen
        seen.append(order)
        assert dialog.map_list.item(0).text().startswith("  1.")
    assert "shuffled 6x" in dialog.status_label.text()
    assert "order changed" in dialog.status_label.text()
    qtbot.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
    assert dialog.entries() == maps
    assert "shuffled" not in dialog.status_label.text()


def test_move_buttons_work_on_several_picked_maps(qtbot):
    maps = _maps(6)
    dialog = _dialog(qtbot, maps)
    dialog.map_list.item(2).setSelected(True)
    dialog.map_list.item(4).setSelected(True)
    assert dialog.up_button.isEnabled()

    qtbot.mouseClick(dialog.up_button, Qt.MouseButton.LeftButton)
    assert dialog.entries() == [maps[i] for i in (0, 2, 1, 4, 3, 5)]
    assert dialog.selected_rows() == [1, 3]

    qtbot.mouseClick(dialog.top_button, Qt.MouseButton.LeftButton)
    assert dialog.entries()[:2] == [maps[2], maps[4]]
    qtbot.mouseClick(dialog.bottom_button, Qt.MouseButton.LeftButton)
    assert dialog.entries()[-2:] == [maps[2], maps[4]]
    assert dialog.selected_rows() == [4, 5]
    qtbot.mouseClick(dialog.down_button, Qt.MouseButton.LeftButton)
    assert dialog.entries()[-2:] == [maps[2], maps[4]]  # already at the bottom


def test_list_is_set_up_for_mouse_drag_and_drop(qtbot):
    dialog = _dialog(qtbot, _maps(3))
    lst = dialog.map_list
    assert lst.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert lst.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert lst.defaultDropAction() == Qt.DropAction.MoveAction
    assert lst.hasAutoScroll() and lst.autoScrollMargin() >= 24
    flags = lst.item(0).flags()
    assert flags & Qt.ItemFlag.ItemIsDragEnabled
    # Not drop-enabled, so a drop lands between maps instead of on one.
    assert not flags & Qt.ItemFlag.ItemIsDropEnabled


def test_a_drop_moves_the_map_and_renumbers_the_list(qtbot):
    """QListWidget's InternalMove drop calls model().moveRow (Qt 6 source)."""
    maps = _maps(5)
    dialog = _dialog(qtbot, maps)
    assert dialog.map_list.model().moveRow(QModelIndex(), 4, QModelIndex(), 0)
    assert dialog.entries() == [maps[4]] + maps[:4]
    assert dialog.map_list.item(0).text().startswith("  1.  AS - Map 4")
    assert dialog.map_list.item(4).text().startswith("  5.  AS - Map 3")
    assert "order changed" in dialog.status_label.text()


def test_labels_show_x2_the_running_map_and_odd_filenames(qtbot):
    maps = [
        OrderEntry("ASY_G1A.BMS", "AS - Flooded Village", double_time=True),
        OrderEntry("TDM_DUST.BMS", "TDM_DUST", is_current=True),
    ]
    dialog = _dialog(qtbot, maps)
    first = dialog.map_list.item(0).text()
    second = dialog.map_list.item(1).text()
    assert "AS - Flooded Village" in first and "(ASY_G1A.BMS)" in first
    assert first.endswith("x2")
    assert "TDM_DUST" in second and "(TDM_DUST.BMS)" not in second
    assert "running" in second


def test_dialog_fits_a_small_screen_and_scrolls_a_long_list(qtbot):
    dialog = _dialog(qtbot, _maps(214))
    qtbot.waitExposed(dialog)
    area = dialog.screen().availableGeometry()
    assert dialog.width() <= max(area.width(), dialog.minimumSizeHint().width())
    assert dialog.height() <= max(area.height(), dialog.minimumSizeHint().height())
    lst = dialog.map_list
    assert lst.verticalScrollBar().maximum() > 0
    last = lst.item(213)
    lst.scrollToItem(last)
    assert lst.viewport().rect().intersects(lst.visualItemRect(last))


def test_ask_returns_the_new_order_on_ok_and_none_on_cancel(qtbot):
    maps = _maps(8)

    def press(button_name):
        dialog = QApplication.activeModalWidget()
        if button_name == "ok":
            dialog.shuffle()
            dialog.accept()
        else:
            dialog.reject()

    QTimer.singleShot(0, lambda: press("cancel"))
    assert MapOrderDialog.ask(maps) is None

    QTimer.singleShot(0, lambda: press("ok"))
    order = MapOrderDialog.ask(maps)
    assert order is not None and order != maps
    assert Counter(order) == Counter(maps)


DRAG_SCRIPT = textwrap.dedent(
    """
    import sys
    from PyQt6.QtCore import QPoint, Qt, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from wolfrat.map_order_dialog import MapOrderDialog, OrderEntry

    app = QApplication(sys.argv[:1])
    LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    dialog = MapOrderDialog([OrderEntry(f"M{i}.BMS", f"Map {i}") for i in range(10)])
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)
    dialog.resize(600, 648)
    QTest.qWait(50)
    lst = dialog.map_list
    vp = lst.viewport()

    def at(row, top=False):
        rect = lst.visualItemRect(lst.item(row))
        return QPoint(20, rect.top() + 1 if top else rect.center().y())

    def drag(src, dst):
        def drop():
            QTest.mouseMove(vp, dst + QPoint(0, 1))
            QTest.mouseMove(vp, dst)
            QTest.mouseRelease(vp, LEFT, NONE, dst)
        QTest.mousePress(vp, LEFT, NONE, src)
        QTimer.singleShot(200, drop)
        QTest.mouseMove(vp, src + QPoint(0, 4))
        QTest.mouseMove(vp, src + QPoint(0, 40))
        QTest.qWait(300)

    drag(at(7), at(1, top=True))
    print("ONE", " ".join(e.filename for e in dialog.entries()))
    QTest.mouseClick(vp, LEFT, NONE, at(4))
    QTest.mouseClick(vp, LEFT, Qt.KeyboardModifier.ControlModifier, at(8))
    drag(at(8), at(0, top=True))
    print("TWO", " ".join(e.filename for e in dialog.entries()))
    print("LABEL", lst.item(0).text())
    """
)


def test_real_mouse_drags_move_maps(tmp_path):
    """A real drag through Qt's own drag code (the 'minimal' platform runs
    QSimpleDrag from mouse events; 'offscreen' turns every drag into a no-op).
    """
    script = tmp_path / "drag.py"
    script.write_text(DRAG_SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="minimal", PYTHONPATH=str(REPO))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=25,
    )
    assert done.returncode == 0, done.stderr
    lines = dict(
        line.split(" ", 1) for line in done.stdout.splitlines() if " " in line
    )
    # Map 7 dragged above map 1.
    assert lines["ONE"].split()[:4] == ["M0.BMS", "M7.BMS", "M1.BMS", "M2.BMS"]
    # Rows 4 and 8 (M3 and M8 after the first drag) picked and dragged to the top.
    assert lines["TWO"].split()[:3] == ["M3.BMS", "M8.BMS", "M0.BMS"]
    assert lines["LABEL"].startswith("  1.  Map 3")


# --------------------------------------------------------------------------
# The Missions tab: Edit Order / Shuffle button -> server
# --------------------------------------------------------------------------

def _done(value):
    future = Future()
    future.set_result(value)
    return future


def _ok(operation):
    return CommandResult(
        operation=operation, replies=("OK",), accepted=True, verified=True
    )


class FakeServer:
    """Records CLEAR/ADD like retail's MISSION verbs (5.70.8)."""

    def __init__(self, files, x2=()):
        self.is_connected = True
        self.available_mission_entries = tuple(
            AvailableMission(
                catalog_index=i, filename=f, description=f"AS - {f}", revision=1
            )
            for i, f in enumerate(files, start=1)
        )
        self.queue = [(f, f in x2) for f in files]
        self.calls = []
        self.logs = []
        self.refuse = set()
        self.hold_adds = False
        self.held = []

    @property
    def mission_entries(self):
        return tuple(
            MissionEntry(
                queue_index=i, filename=f, double_time=x2, is_current=(i == 1)
            )
            for i, (f, x2) in enumerate(self.queue)
        )

    def _log(self, message, *args, **kwargs):
        self.logs.append(message)

    def clear_missions(self):
        self.calls.append(("CLEAR",))
        self.queue = []
        return _done(_ok("MISSION CLEAR"))

    def add_mission(self, filename, auto_switch_sides=False):
        if filename in self.refuse:
            raise ValueError(f"mission {filename!r} is not in MISSION AVAILABLE")
        self.calls.append(("ADD", filename, bool(auto_switch_sides)))
        self.queue.append((filename, bool(auto_switch_sides)))
        if self.hold_adds:
            future = Future()
            self.held.append(future)
            return future
        return _done(_ok("MISSION ADD"))

    def refresh_missions(self, quiet=False):
        return _done(None)

    def refresh_available_maps(self, quiet=False):
        return _done(None)

    def refresh_game_state(self, quiet=False):
        return _done(None)


FILES = ["A.bms", "B.bms", "C.bms", "D.bms"]


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for kind in ("information", "warning"):
        monkeypatch.setattr(
            app_module.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: shown.append((_kind, args[2])),
        )
    return shown


def _tab(qtbot, tmp_path, server):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = MissionsTab(server, MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    return tab


def test_ok_clears_the_list_and_adds_the_maps_back_in_the_new_order(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES, x2={"C.bms"})
    tab = _tab(qtbot, tmp_path, server)
    opened = []
    monkeypatch.setattr(
        tab, "_run_order_dialog",
        lambda entries: opened.append(list(entries)) or list(reversed(entries)),
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert [e.filename for e in opened[0]] == FILES
    assert opened[0][1].is_current and opened[0][2].double_time
    assert server.calls == [
        ("CLEAR",),
        ("ADD", "D.bms", False),
        ("ADD", "C.bms", True),  # x2 kept
        ("ADD", "B.bms", False),
        ("ADD", "A.bms", False),
    ]
    assert any("New map order is on the server: 4 maps" in m for m in server.logs)
    assert boxes == []


def test_the_button_opens_the_editor(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    opened = []
    monkeypatch.setattr(
        tab, "_run_order_dialog", lambda entries: opened.append(entries)
    )
    qtbot.mouseClick(tab.edit_order_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(opened), timeout=2000)  # SatisfyingButton holds 120 ms


def test_cancel_or_an_unchanged_order_sends_nothing(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    monkeypatch.setattr(tab, "_run_order_dialog", lambda entries: None)
    tab._open_order_editor()
    monkeypatch.setattr(tab, "_run_order_dialog", lambda entries: list(entries))
    tab._open_order_editor()
    assert server.calls == []
    assert any("unchanged" in m for m in server.logs)


def test_editor_needs_a_connection_and_some_maps(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    opened = []
    monkeypatch.setattr(tab, "_run_order_dialog", lambda e: opened.append(e))
    server.is_connected = False
    tab._open_order_editor()
    server.is_connected = True
    server.queue = []
    tab._open_order_editor()
    assert opened == []
    assert [kind for kind, _ in boxes] == ["information", "information"]
    assert "Connect to a server first" in boxes[0][1]
    assert "empty" in boxes[1][1]


def test_a_list_changed_while_editing_asks_first(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    answers = []

    def edit(entries):
        server.queue.append(("E.bms", False))  # someone adds a map meanwhile
        return list(reversed(entries))

    monkeypatch.setattr(tab, "_run_order_dialog", edit)
    monkeypatch.setattr(
        app_module.QMessageBox, "question",
        lambda *args, **kwargs: answers.pop(0),
    )
    answers.append(app_module.QMessageBox.StandardButton.No)
    tab._open_order_editor()
    assert server.calls == []

    server.queue.pop()
    answers.append(app_module.QMessageBox.StandardButton.Yes)
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert server.calls[0] == ("CLEAR",)
    added = [call[1] for call in server.calls[1:]]
    assert added == list(reversed(FILES))  # the edited order; E.bms dropped


def test_a_second_edit_waits_for_the_first_feed(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    server.hold_adds = True
    tab = _tab(qtbot, tmp_path, server)
    opened = []
    monkeypatch.setattr(
        tab, "_run_order_dialog",
        lambda entries: opened.append(1) or list(reversed(entries)),
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
    assert tab._feed_active
    tab._open_order_editor()
    assert opened == [1]
    assert boxes and "still putting maps on the server" in boxes[-1][1]
    for _ in FILES:  # let the feed finish
        qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
        server.held.pop(0).set_result(_ok("MISSION ADD"))
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)


def test_a_refused_map_is_skipped_and_the_rest_still_go_on(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    server.refuse = {"C.bms"}
    tab = _tab(qtbot, tmp_path, server)
    monkeypatch.setattr(
        tab, "_run_order_dialog", lambda entries: list(reversed(entries))
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert [c[1] for c in server.calls[1:]] == ["D.bms", "B.bms", "A.bms"]
    assert boxes and boxes[-1][0] == "warning" and "C.bms" in boxes[-1][1]


def test_a_lost_connection_part_way_frees_the_editor(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    original_add = server.add_mission

    def add(filename, auto_switch_sides=False):
        if len(server.calls) == 2:  # CLEAR + one map, then the link drops
            server.is_connected = False
            raise ConnectionError("connection lost")
        return original_add(filename, auto_switch_sides)

    server.add_mission = add
    monkeypatch.setattr(
        tab, "_run_order_dialog", lambda entries: list(reversed(entries))
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert boxes and "connection to the server was lost" in boxes[-1][1]
    assert not any("New map order is on the server" in m for m in server.logs)
