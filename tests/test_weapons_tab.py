"""Weapons tab: the search box, and a refresh no longer throws the list back to the top (Dale, 2026-10-14)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat import app as wapp  # noqa: E402
from wolfrat.admin_commands import WeaponEntry, WeaponMode as M  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

NAMES = ["WPN_colt45", "WPN_M9Beretta", "WPN_RemmingtonSG", "WPN_357", "WPN_M4AUTO", "WPN_M4M203AUTO",
         "WPN_M249SAW", "WPN_M60", "WPN_DRAGUNOV", "WPN_L115A", "WPN_M21", "WPN_M24", "WPN_Barret",
         "WPN_GRENADEFB", "WPN_GRENADEHE"] + [f"WPN_FILLER{i}" for i in range(40)]


class FakeServer:
    def _log(self, _message):
        pass

    def refresh_weapons(self, *_):
        pass


def weapons(changes=None):
    changes = changes or {}
    return [WeaponEntry(i + 3, name, changes.get(name, M.ALWAYS), 1) for i, name in enumerate(NAMES)]


def make_tab():
    tab = wapp.WeaponsTab(FakeServer())
    tab.resize(700, 400)
    tab.show()
    tab.update_weapons(weapons())
    _app.processEvents()
    return tab


def shown(tab):
    return [w.name for row, w in enumerate(tab._weapons_by_row) if not tab.weapon_table.isRowHidden(row)]


def test_a_refresh_keeps_the_scroll_and_moves_the_tick():
    tab = make_tab()
    bar = tab.weapon_table.verticalScrollBar()
    bar.setValue(bar.maximum())
    where = bar.value()
    assert where > 0
    tab.update_weapons(weapons({"WPN_M24": M.ARMORY}))
    _app.processEvents()
    assert bar.value() == where
    assert tab._row_groups[NAMES.index("WPN_M24")].button(2).isChecked()


def test_search_by_name_and_by_kind():
    tab = make_tab()
    tab.weapon_search.setText("357")
    assert shown(tab) == ["WPN_357"]
    tab.weapon_search.setText("m4")
    assert shown(tab) == ["WPN_M4AUTO", "WPN_M4M203AUTO"]
    tab.weapon_search.setText("sniper")
    assert shown(tab) == ["WPN_DRAGUNOV", "WPN_L115A", "WPN_M21", "WPN_M24", "WPN_Barret"]   # not the M249
    tab.weapon_search.setText("pistol")
    assert shown(tab) == ["WPN_colt45", "WPN_M9Beretta", "WPN_357"]
    tab.weapon_search.setText("gren")
    assert shown(tab) == ["WPN_GRENADEFB", "WPN_GRENADEHE"]
    tab.weapon_search.setText("")
    assert len(shown(tab)) == len(NAMES)


def test_the_search_survives_a_refresh_and_a_new_list():
    tab = make_tab()
    tab.weapon_search.setText("shot")
    tab.update_weapons(weapons({"WPN_colt45": M.NEVER}))
    assert shown(tab) == ["WPN_RemmingtonSG"]
    tab.update_weapons(weapons()[:-5])                     # a different list rebuilds the grid
    assert shown(tab) == ["WPN_RemmingtonSG"]
