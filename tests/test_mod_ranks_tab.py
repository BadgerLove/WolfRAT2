"""Mods tab with ranks: real widgets, real chat gate, fake server."""
import json
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QComboBox

from wolfrat.app import DesktopRuntime, MissionsStore, ModsTab
from wolfrat.mod_ranks import ADMIN, MODERATOR

_app = QApplication.instance() or QApplication(sys.argv[:1])


class FakeServer:
    def __init__(self):
        self.players = [
            {"name": name, "id": number, "team": 1, "revision": 7}
            for number, name in enumerate(("Ham", "Dale", "Griefer"), 3)
        ]
        self.missions = []
        self.mission_entries = []

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


class Rig:
    def __init__(self, folder, mods_file=None):
        self.rt = DesktopRuntime.isolated(folder)
        self.path = str(self.rt.path("wolfrat_mods.json"))
        if mods_file is not None:
            with open(self.path, "w") as f:
                json.dump(mods_file, f)
        self.tab = self.build()

    def build(self):
        tab = ModsTab(FakeServer(), MissionsStore(self.rt), self.rt)
        self.said, self.actions = [], []
        tab._send_mod_chat = lambda msg, ctx="": self.said.append(msg)
        tab._submit_mod_action = lambda op, **kw: self.actions.append(kw["context"])
        panel = tab.ranks_panel
        self.warned, self.answers = [], []
        panel._warn = self.warned.append
        panel._ask_name = lambda *a: self.answers.pop(0)
        return tab

    def saved(self):
        with open(self.path) as f:
            return json.load(f)

    def who_column(self, command):
        table = self.tab.cmd_table
        for row in range(table.rowCount()):
            if table.item(row, 0).text().startswith(command):
                return table.item(row, 1).text().rpartition("Ranks: ")[2]
        raise AssertionError(command)


def test_upgrade_keeps_old_mods_as_moderators_and_their_vote_settings(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Ham", "Dale"], "vote_enabled": False, "vote_threshold": 70})
    assert rig.tab.roster.members() == [("Dale", MODERATOR), ("Ham", MODERATOR)]
    assert rig.tab._vote_enabled is False and rig.tab._vote_threshold == 70
    assert rig.tab.ranks_panel.people_table.rowCount() == 2


def test_gate_moderator_can_kick_but_not_ban_and_is_told_why(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Ham"]})
    rig.tab._check_mod_command("Ham: !kick Griefer")
    assert rig.actions == ["Kick Griefer"]
    rig.tab._check_mod_command("Ham: !ban Griefer")
    assert rig.actions == ["Kick Griefer"]
    assert rig.said == ["!ban is not allowed for Moderator."]
    assert "not allowed" in rig.tab.mod_log.item(rig.tab.mod_log.count() - 1).text()
    # strangers are still ignored silently, web admin still does everything
    rig.tab._check_mod_command("Griefer: !ban Ham")
    assert len(rig.said) == 1 and len(rig.actions) == 1
    rig.tab._check_mod_command("[ADMIN] !ban Griefer")
    assert rig.actions[-1] == "Ban Griefer"


def test_promote_to_admin_from_the_people_page_saves_and_unlocks_ban(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Dale", "Ham"]})
    table = rig.tab.ranks_panel.people_table
    assert table.item(0, 0).text() == "Dale"
    combo = table.cellWidget(0, 1)
    assert isinstance(combo, QComboBox)
    combo.setCurrentText(ADMIN)

    assert rig.saved()["mod_ranks"] == {"Dale": ADMIN, "Ham": MODERATOR}
    assert rig.saved()["mods"] == ["Dale", "Ham"]
    rig.tab._check_mod_command("Dale: !ban Griefer")
    assert rig.actions == ["Ban Griefer"]
    assert rig.tab.ranks_panel.rank_list.item(0).text() == "Admin  (1)"

    again = Rig(tmp_path)  # restart
    assert again.tab.roster.rank_of("dale").name == ADMIN


def test_custom_rank_gets_some_admin_commands(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Ham"]})
    panel = rig.tab.ranks_panel
    rig.answers = ["Super Moderator"]
    panel._new_rank()
    assert panel.rank_list.currentItem().text() == "Super Moderator  (0)"
    assert panel._perm_boxes["kick"].isChecked()      # copy of Moderator
    assert not panel._perm_boxes["ban"].isChecked()
    panel._perm_boxes["ban"].setChecked(True)
    assert rig.who_column("!ban") == "Admin, Super Moderator"

    panel.people_table.cellWidget(0, 1).setCurrentText("Super Moderator")
    rig.tab._check_mod_command("Ham: !ban Griefer")
    rig.tab._check_mod_command("Ham: !map crossroads")
    assert rig.actions == ["Ban Griefer"]
    assert rig.said == ["!map is not allowed for Super Moderator."]

    again = Rig(tmp_path)
    assert again.tab.roster.allows("ham", "!ban")
    assert not again.tab.roster.allows("ham", "!map")


def test_admin_boxes_are_locked_and_builtins_cannot_be_deleted(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Ham"]})
    panel = rig.tab.ranks_panel
    panel.rank_list.setCurrentRow(0)
    assert all(box.isChecked() and not box.isEnabled() for box in panel._perm_boxes.values())
    assert not panel.delete_rank_btn.isEnabled() and not panel.rename_rank_btn.isEnabled()
    panel.rank_list.setCurrentRow(1)
    assert all(box.isEnabled() for box in panel._perm_boxes.values())
    assert not panel.delete_rank_btn.isEnabled()


def test_delete_rank_with_people_in_it_is_refused_in_plain_words(tmp_path):
    rig = Rig(tmp_path, {"mods": ["Ham"]})
    panel = rig.tab.ranks_panel
    rig.answers = ["Trial", "admin"]
    panel._new_rank()
    panel.people_table.cellWidget(0, 1).setCurrentText("Trial")
    panel._delete_rank()
    assert "Move them to another rank first" in rig.warned[0]
    panel._new_rank()  # name clash with a built-in
    assert "already a rank" in rig.warned[1]
    panel.people_table.cellWidget(0, 1).setCurrentText(MODERATOR)
    panel.rank_list.setCurrentRow(2)
    panel._delete_rank()
    assert rig.tab.roster.rank_names() == [ADMIN, MODERATOR]
    assert [r["name"] for r in rig.saved()["ranks"]] == [MODERATOR]


def test_add_and_remove_people(tmp_path):
    rig = Rig(tmp_path)
    panel = rig.tab.ranks_panel
    panel.set_online_players(["Ham", "", "Dale"])
    assert panel.player_combo.count() == 2
    panel.player_combo.setCurrentText("Dale")
    panel.new_rank_combo.setCurrentText(ADMIN)
    panel._add_online()
    panel.mod_input.setText("  Ham ")
    panel.new_rank_combo.setCurrentText(MODERATOR)
    panel._add_typed()
    panel._add_typed()  # empty box: nothing
    assert rig.saved()["mod_ranks"] == {"Dale": ADMIN, "Ham": MODERATOR}
    assert rig.tab.mods == {"dale": "Dale", "ham": "Ham"}  # entrance panel still fed
    panel.people_table.selectRow(1)
    panel._remove_selected()
    assert rig.saved()["mods"] == ["Dale"]
