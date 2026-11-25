"""Edit Order / Shuffle: Delete and OK and Save (Dale, 2026-11-25).

Delete takes maps off the edited list. OK and Save puts the list on the
server like OK, and also writes it to the saved list (preset) the cycle came
from, so a shuffle survives a WolfRAT restart.
"""

import json
import random

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from wolfrat import app as app_module
from wolfrat.map_order_dialog import (
    EditedOrder,
    MapOrderDialog,
    OrderEntry,
    delete_rows,
)
from tests.test_map_order import FILES, FakeServer, _maps, _tab


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for kind in ("information", "warning"):
        monkeypatch.setattr(
            app_module.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: shown.append((_kind, args[2])),
        )
    return shown


# --------------------------------------------------------------------------
# Delete in the dialog
# --------------------------------------------------------------------------

def test_delete_rows_picks_the_next_map():
    items = list("ABCDEF")
    assert delete_rows(items, [1, 3]) == (list("ACEF"), [1])
    assert delete_rows(items, [5]) == (list("ABCDE"), [4])  # last row: new last
    assert delete_rows(items, []) == (items, [])


def _dialog(qtbot, maps, save_name=None):
    dialog = MapOrderDialog(maps, rng=random.Random(3), save_name=save_name)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_delete_button_takes_picked_maps_off_and_undo_brings_them_back(qtbot):
    maps = _maps(6)
    dialog = _dialog(qtbot, maps)
    assert not dialog.delete_button.isEnabled()  # nothing picked
    dialog.map_list.item(1).setSelected(True)
    dialog.map_list.item(4).setSelected(True)
    assert dialog.delete_button.isEnabled()
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    assert dialog.entries() == [maps[i] for i in (0, 2, 3, 5)]
    assert dialog.map_list.item(1).text().startswith("  2.  AS - Map 2")
    assert dialog.selected_rows() == [1]  # press again to keep deleting
    status = dialog.status_label.text()
    assert "4 maps" in status and "2 deleted" in status
    assert "order changed" not in status  # only deleted, order kept
    assert dialog.undo_button.isEnabled()
    qtbot.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
    assert dialog.entries() == maps
    assert "deleted" not in dialog.status_label.text()


def test_delete_key_works_in_the_list(qtbot):
    maps = _maps(4)
    dialog = _dialog(qtbot, maps)
    qtbot.waitExposed(dialog)
    dialog.map_list.setFocus()
    dialog.map_list.item(2).setSelected(True)
    QTest.keyClick(dialog.map_list, Qt.Key.Key_Delete)
    assert dialog.entries() == [maps[0], maps[1], maps[3]]


def test_the_list_can_never_be_emptied(qtbot):
    maps = _maps(3)
    dialog = _dialog(qtbot, maps)
    dialog.map_list.selectAll()
    assert not dialog.delete_button.isEnabled()
    dialog.delete_selected()
    assert dialog.entries() == maps


def test_delete_after_a_shuffle_counts_both(qtbot):
    maps = _maps(10)
    dialog = _dialog(qtbot, maps)
    dialog.shuffle()
    dialog.map_list.item(0).setSelected(True)
    dialog.delete_selected()
    status = dialog.status_label.text()
    assert "9 maps" in status and "1 deleted" in status
    assert "order changed" in status and "shuffled 1x" in status


def test_duplicate_maps_delete_one_copy(qtbot):
    twice = OrderEntry("A.BMS", "A")
    maps = [twice, OrderEntry("B.BMS", "B"), twice]
    dialog = _dialog(qtbot, maps)
    dialog.map_list.item(2).setSelected(True)
    dialog.delete_selected()
    assert dialog.entries() == [twice, OrderEntry("B.BMS", "B")]
    assert "order changed" not in dialog.status_label.text()


# --------------------------------------------------------------------------
# OK and Save in the dialog
# --------------------------------------------------------------------------

def test_save_button_sits_next_to_ok_and_the_name_is_in_the_hint(qtbot):
    dialog = _dialog(qtbot, _maps(3), save_name="1-coop")
    assert dialog.save_button.text() == "OK and Save"
    assert "preset" not in dialog.save_button.text().lower()
    assert "'1-coop'" in dialog.apply_hint.text()
    assert dialog.button_box.buttons().count(dialog.save_button) == 1
    plain = _dialog(qtbot, _maps(3))
    assert plain.save_button.text() == "OK and Save As..."


def test_ask_says_which_button_was_pressed(qtbot):
    maps = _maps(5)

    def press(save):
        dialog = QApplication.activeModalWidget()
        dialog.shuffle()
        if save:
            dialog.save_button.click()
        else:
            dialog.ok_button.click()

    QTimer.singleShot(0, lambda: press(True))
    order = MapOrderDialog.ask(maps, save_name="x")
    assert isinstance(order, EditedOrder) and order.save
    assert order != maps

    QTimer.singleShot(0, lambda: press(False))
    order = MapOrderDialog.ask(maps, save_name="x")
    assert isinstance(order, EditedOrder) and not order.save


# --------------------------------------------------------------------------
# The Missions tab: which saved list, and saving it
# --------------------------------------------------------------------------

def _preset(files, x2=()):
    return [{"file": f, "count": 2 if f in x2 else 1} for f in files]


def _saved(tab):
    with open(tab._preset_path()) as handle:
        return json.load(handle)


def _edit(order_of, save):
    def run(entries):
        order = EditedOrder(order_of(entries))
        order.save = save
        return order
    return run


def _with_presets(tab, presets):
    tab._presets.update(presets)
    for name in presets:
        tab.preset_combo.addItem(name)


def test_ok_and_save_writes_the_new_order_to_the_list_it_came_from(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES, x2={"C.bms"})
    tab = _tab(qtbot, tmp_path, server)
    # Same maps, saved in another order, plus an unrelated list.
    _with_presets(tab, {
        "other": _preset(["A.bms"]),
        "1-coop": _preset(["B.bms", "A.bms", "D.bms", "C.bms"], x2={"C.bms"}),
    })
    seen = []
    monkeypatch.setattr(
        tab, "_run_order_dialog",
        lambda entries: seen.append(tab._order_save_name)
        or _edit(lambda e: list(reversed(e)), True)(entries),
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert seen == ["1-coop"]
    expected = _preset(["D.bms", "C.bms", "B.bms", "A.bms"], x2={"C.bms"})
    assert tab._presets["1-coop"] == expected
    assert _saved(tab)["1-coop"] == expected
    assert _saved(tab)["other"] == _preset(["A.bms"])
    assert tab.preset_combo.currentText() == "1-coop"
    assert [c[1] for c in server.calls[1:]] == ["D.bms", "C.bms", "B.bms", "A.bms"]
    assert any("Saved the new list as '1-coop' (4 maps)" in m for m in server.logs)
    assert boxes == []


def test_deleted_maps_leave_the_saved_list_too(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"1-coop": _preset(FILES)})
    monkeypatch.setattr(
        tab, "_run_order_dialog", _edit(lambda e: [e[0], e[3]], True)
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert _saved(tab)["1-coop"] == _preset(["A.bms", "D.bms"])
    assert server.calls == [("CLEAR",), ("ADD", "A.bms", False), ("ADD", "D.bms", False)]
    assert any("2 maps deleted" in m for m in server.logs)


def test_plain_ok_never_touches_the_saved_list(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"1-coop": _preset(FILES)})
    tab._save_presets()
    monkeypatch.setattr(
        tab, "_run_order_dialog", _edit(lambda e: list(reversed(e)), False)
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert _saved(tab)["1-coop"] == _preset(FILES)
    assert server.calls[0] == ("CLEAR",)


def test_the_picked_preset_wins_over_another_with_the_same_maps(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"a-copy": _preset(FILES), "mine": _preset(FILES[::-1])})
    tab.preset_combo.setCurrentText("mine")
    assert tab._order_source_preset(tab._order_entries()) == "mine"
    tab.preset_combo.setCurrentText("(none)")
    tab._cycle_preset = "mine"  # the last one loaded
    assert tab._order_source_preset(tab._order_entries()) == "mine"
    tab._cycle_preset = None
    assert tab._order_source_preset(tab._order_entries()) == "a-copy"


def test_a_preset_with_different_maps_is_not_the_source(qtbot, tmp_path, boxes):
    server = FakeServer(FILES, x2={"C.bms"})
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {
        "short": _preset(FILES[:3]),
        "x1-only": _preset(FILES),  # C.bms is x2 on the server
    })
    tab.preset_combo.setCurrentText("short")
    assert tab._order_source_preset(tab._order_entries()) is None


def test_no_source_asks_for_a_name(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    asked = []
    monkeypatch.setattr(
        tab, "_ask_preset_name", lambda suggestion="": asked.append(suggestion) or "new list"
    )
    monkeypatch.setattr(
        tab, "_run_order_dialog", _edit(lambda e: list(reversed(e)), True)
    )
    tab._open_order_editor()
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert asked == [""]
    assert _saved(tab)["new list"] == _preset(FILES[::-1])
    assert tab.preset_combo.findText("new list") >= 0


def test_backing_out_of_the_name_sends_nothing(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    monkeypatch.setattr(tab, "_ask_preset_name", lambda suggestion="": None)
    monkeypatch.setattr(
        tab, "_run_order_dialog", _edit(lambda e: list(reversed(e)), True)
    )
    tab._open_order_editor()
    assert server.calls == []


def test_save_as_over_a_different_list_asks_first(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"weekend": _preset(["A.bms"])})
    tab._save_presets()
    monkeypatch.setattr(tab, "_ask_preset_name", lambda suggestion="": "weekend")
    answers = [app_module.QMessageBox.StandardButton.No]
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *a, **k: answers.pop(0)
    )
    monkeypatch.setattr(
        tab, "_run_order_dialog", _edit(lambda e: list(reversed(e)), True)
    )
    tab._open_order_editor()
    assert server.calls == []
    assert _saved(tab)["weekend"] == _preset(["A.bms"])


def test_unchanged_list_with_save_only_saves(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"1-coop": _preset(FILES[::-1])})
    monkeypatch.setattr(tab, "_run_order_dialog", _edit(list, True))
    tab._open_order_editor()
    assert server.calls == []
    assert _saved(tab)["1-coop"] == _preset(FILES)  # the server's order now


def test_list_changed_while_editing_and_no_saves_nothing(
    qtbot, tmp_path, monkeypatch, boxes
):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    _with_presets(tab, {"1-coop": _preset(FILES)})
    tab._save_presets()

    def edit(entries):
        server.queue.append(("E.bms", False))
        order = EditedOrder(reversed(entries))
        order.save = True
        return order

    monkeypatch.setattr(tab, "_run_order_dialog", edit)
    monkeypatch.setattr(
        app_module.QMessageBox, "question",
        lambda *a, **k: app_module.QMessageBox.StandardButton.No,
    )
    tab._open_order_editor()
    assert server.calls == []
    assert _saved(tab)["1-coop"] == _preset(FILES)


def test_a_replace_load_remembers_the_preset(qtbot, tmp_path, monkeypatch, boxes):
    server = FakeServer(FILES)
    tab = _tab(qtbot, tmp_path, server)
    monkeypatch.setattr(tab, "_ask_replace_or_add", lambda name, count: "replace")
    tab._start_preset_load("weekend", _preset(["B.bms", "A.bms"]))
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert tab._cycle_preset == "weekend"
    monkeypatch.setattr(tab, "_ask_replace_or_add", lambda name, count: "add")
    tab._start_preset_load("extra", _preset(["C.bms"]))
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)
    assert tab._cycle_preset == "weekend"  # Add mixes lists: not its source
