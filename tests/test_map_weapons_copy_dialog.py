"""Map Weapons: the 'Copy rule to maps...' window."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from wolfrat import map_weapons as mw
from wolfrat.map_weapons_copy_dialog import CopyRuleDialog

_app = QApplication.instance() or QApplication(sys.argv[:1])

MAPS = [("DM-SulphurCreek.bms", "DM - Sulphur Creek"), ("TD-SulphurCreek.bms", "TD - Sulphur Creek"),
        ("TK-SulphurCreek.bms", "TK - Sulphur Creek"), ("Mogadishu.bms", "Battle of Mogadishu - Part 1"),
        ("TD-SulphurCreek.bms", "TD - Sulphur Creek")]                    # a duplicate row is shown once


def keeper(tmp_path):
    k = mw.MapWeaponsKeeper(tmp_path / "wolfrat_map_weapons.json", list_weapons=list,
                            set_all=lambda mode: None, set_one=lambda entry, mode: None,
                            start_thread=False)
    k.config.rules = [mw.MapRule(match="Mogadishu.bms", weapons={"WPN_M4AUTO": "ALWAYS"}),
                      mw.MapRule(match="DM-SulphurCreek.bms", label="DM - Sulphur Creek",
                                 weapons={"WPN_357": "ALWAYS", "WPN_M4AUTO": "NEVER"})]
    return k


def rows(dialog):
    return {dialog.table.item(r, 1).text(): r for r in range(dialog.table.rowCount())}


def test_lists_every_map_once_and_the_source_cannot_be_ticked(tmp_path):
    k = keeper(tmp_path)
    dialog = CopyRuleDialog(k, MAPS, source=k.config.rules[1])
    shown = rows(dialog)
    assert len(shown) == 4
    dm = dialog.table.item(shown["DM - Sulphur Creek   (DM-SulphurCreek.bms)"], 0)
    assert not dm.flags() & Qt.ItemFlag.ItemIsUserCheckable
    td = shown["TD - Sulphur Creek   (TD-SulphurCreek.bms)"]
    assert dialog.table.item(td, 2).text().startswith("no rule")


def test_tick_and_copy(tmp_path, monkeypatch):
    k = keeper(tmp_path)
    dialog = CopyRuleDialog(k, MAPS, source=k.config.rules[1])
    assert not dialog.copy_btn.isEnabled()
    shown = rows(dialog)
    for name in ("TD - Sulphur Creek   (TD-SulphurCreek.bms)", "TK - Sulphur Creek   (TK-SulphurCreek.bms)"):
        dialog.table.item(shown[name], 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.copy_btn.isEnabled()
    dialog._copy()
    saved = {r.match: r for r in mw.load_config(k.path).rules}
    assert set(saved) == {"Mogadishu.bms", "DM-SulphurCreek.bms", "TD-SulphurCreek.bms", "TK-SulphurCreek.bms"}
    assert saved["TK-SulphurCreek.bms"].weapons == {"WPN_357": "ALWAYS", "WPN_M4AUTO": "NEVER"}
    assert len(dialog.made) == 2 and dialog._ticked() == []


def test_search_hides_other_maps_but_keeps_ticks(tmp_path):
    k = keeper(tmp_path)
    dialog = CopyRuleDialog(k, MAPS, source=k.config.rules[0])
    shown = rows(dialog)
    dialog.table.item(shown["TK - Sulphur Creek   (TK-SulphurCreek.bms)"], 0).setCheckState(Qt.CheckState.Checked)
    dialog.search.setText("TD-")
    visible = [r for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r)]
    assert [dialog.table.item(r, 1).text() for r in visible] == ["TD - Sulphur Creek   (TD-SulphurCreek.bms)"]
    assert len(dialog._ticked()) == 1
