"""Weapons tab, saved maps in the grid (Dale 2026-11-16): clicking a map in Map Weapons
shows that map's saved weapons in Weapons Availability, and changing them there
saves the rule (and sends it to the server when it is the map being played).  Before,
the grid only ever showed the server, so a saved map's weapons could not be seen and
an M16 'added to Mogadishu' went to the server's current map instead.

Also: Map Weapons takes the current map from server memory first; the admin list only
tags <CURRENT MISSION> when the running map is in the rotation."""

import os
import sys
from concurrent.futures import Future

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat import app as wapp  # noqa: E402
from wolfrat import map_weapons  # noqa: E402
from wolfrat.admin_commands import WeaponEntry, WeaponMode as M  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

NAMES = ["WPN_colt45", "WPN_M16BURST", "WPN_M4AUTO", "WPN_AK47AUTO", "WPN_M24", "WPN_GRENADEHE"]


def live(changes=None):
    changes = changes or {}
    return [WeaponEntry(i + 3, name, changes.get(name, M.ALWAYS), 1) for i, name in enumerate(NAMES)]


class FakeServer:
    def __init__(self):
        self.weapon_entries = tuple(live())
        self.sent = []

    def _log(self, _message):
        pass

    def refresh_weapons(self, *_a, **_k):
        return Future()

    def set_weapon(self, admdef_id, mode):
        self.sent.append(("one", admdef_id, mode))
        return Future()

    def set_all_weapons(self, mode):
        self.sent.append(("all", mode))
        return Future()


def make(tmp_path, current="TD-SulphurCreek.bms", enabled=True, loaded=None):
    server = FakeServer()
    keeper = map_weapons.MapWeaponsKeeper(str(tmp_path / "mw.json"), list_weapons=lambda: [],
                                          set_all=lambda m: None, set_one=lambda e, m: None,
                                          start_thread=False)
    keeper.config.enabled = enabled
    keeper.config.rules = [
        map_weapons.MapRule("TD-SulphurCreek.bms", {n: ("ALWAYS" if n in ("WPN_M4AUTO", "WPN_M24") else "NEVER")
                                                    for n in NAMES}, label="TD - Sulphur Creek"),
        map_weapons.MapRule("Mogadishu.bms", {n: ("NEVER" if n in ("WPN_AK47AUTO", "WPN_M16BURST") else "ALWAYS")
                                              for n in NAMES}, label="Battle of Mogadishu - Part 1"),
    ]
    tab = wapp.WeaponsTab(server)
    tab.attach_map_weapons(keeper, loaded_map=(lambda: loaded) if loaded is not None else None)
    tab.update_weapons(live())                     # the server: everything Yes
    keeper.current_map = current
    tab._refresh_map_rules()
    return tab, keeper, server


def ticked(tab):
    column = {1: M.ALWAYS, 2: M.ARMORY, 3: M.NEVER}
    return {w.name: column[group.checkedId()] for w, group in zip(tab._weapons_by_row, tab._row_groups)}


def click_rule(tab, row):
    tab.map_rules_table.selectRow(row)
    _app.processEvents()


def test_clicking_a_saved_map_shows_its_weapons_and_back(tmp_path):
    tab, _keeper, _server = make(tmp_path)
    assert set(ticked(tab).values()) == {M.ALWAYS}                 # the server
    click_rule(tab, 1)
    assert ticked(tab)["WPN_M16BURST"] is M.NEVER and ticked(tab)["WPN_colt45"] is M.ALWAYS
    assert "Mogadishu" in tab.weapon_group.title() and not tab.show_server_btn.isHidden()
    click_rule(tab, 0)
    assert ticked(tab)["WPN_M4AUTO"] is M.ALWAYS and ticked(tab)["WPN_colt45"] is M.NEVER
    tab._show_server_weapons()
    assert set(ticked(tab).values()) == {M.ALWAYS} and tab.show_server_btn.isHidden()


def test_adding_a_weapon_to_a_map_not_playing_saves_the_rule_only(tmp_path):
    tab, keeper, server = make(tmp_path)
    click_rule(tab, 1)                                              # Mogadishu; TD is playing
    tab._row_groups[NAMES.index("WPN_M16BURST")].button(1).setChecked(True)
    assert keeper.config.rules[1].mode_for("WPN_M16BURST") is M.ALWAYS
    saved = map_weapons.load_config(str(tmp_path / "mw.json"))
    assert map_weapons.rule_for("Mogadishu.bms", saved.rules).mode_for("WPN_M16BURST") is M.ALWAYS
    assert server.sent == []                                        # not the map being played
    assert tab._rule_view == "Mogadishu.bms" and tab.map_rules_table.currentRow() == 1   # still shown


def test_changing_the_map_being_played_saves_and_sends(tmp_path):
    tab, keeper, server = make(tmp_path)
    click_rule(tab, 0)                                              # TD-SulphurCreek is playing
    tab._row_groups[NAMES.index("WPN_AK47AUTO")].button(1).setChecked(True)
    assert keeper.config.rules[0].mode_for("WPN_AK47AUTO") is M.ALWAYS
    assert server.sent == [("one", 3 + NAMES.index("WPN_AK47AUTO"), M.ALWAYS)]
    tab._set_all_weapons(M.NEVER)
    assert set(keeper.config.rules[0].weapons.values()) == {"NEVER"} and server.sent[-1] == ("all", M.NEVER)


def test_a_server_refresh_does_not_overwrite_the_saved_map_shown(tmp_path):
    tab, _keeper, _server = make(tmp_path)
    click_rule(tab, 1)
    tab.update_weapons(live({"WPN_M16BURST": M.ARMORY}))           # the 5 s list, or a set's readback
    assert ticked(tab)["WPN_M16BURST"] is M.NEVER                   # still Mogadishu's rule
    tab._show_server_weapons()
    assert ticked(tab)["WPN_M16BURST"] is M.ARMORY                  # the server's newest list


def test_with_map_weapons_off_nothing_is_sent(tmp_path):
    tab, keeper, server = make(tmp_path, enabled=False)
    click_rule(tab, 0)
    tab._row_groups[NAMES.index("WPN_colt45")].button(1).setChecked(True)
    assert keeper.config.rules[0].mode_for("WPN_colt45") is M.ALWAYS and server.sent == []


def test_save_for_this_map_goes_back_to_the_server_view(tmp_path):
    tab, _keeper, _server = make(tmp_path)
    click_rule(tab, 1)
    tab._show_server_weapons()
    assert tab._rule_view is None and tab.map_rules_table.selectionModel().selectedRows() == []


def test_the_current_map_comes_from_server_memory_first(tmp_path):
    # 2026-11-16 16:16: DM-Nuke130 ran but the rotation was only DM-SulphurCreek, no tags
    tab, keeper, _server = make(tmp_path, current=None, loaded="DM-Nuke130.bms")
    tab.on_missions_updated(["0: DM-SulphurCreek.bms - () () () <> <>"])
    assert keeper.current_map == "DM-Nuke130.bms"
    assert "DM-Nuke130.bms" in tab.map_current_label.text()


def test_without_server_memory_the_admin_list_still_works(tmp_path):
    tab, keeper, _server = make(tmp_path, current=None)
    tab.on_missions_updated(["0: Mogadishu.bms - () () () <CURRENT MISSION> <>"])
    assert keeper.current_map == "Mogadishu.bms"
