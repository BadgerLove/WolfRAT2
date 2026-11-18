"""!setnext <map>: queue the next map from chat without skipping (Dale, 2026-11-18)."""
from tests.test_mod_ranks_tab import Rig
from wolfrat.admin_commands import MissionEntry
from wolfrat.mod_ranks import ADMIN


class CapturingRig(Rig):
    """Like Rig, but keeps the queued operations so the test can run them."""

    def build(self):
        tab = super().build()
        self.ops, self.calls = [], []
        tab._submit_mod_action = lambda op, **kw: (
            self.actions.append(kw["context"]),
            self.ops.append((op, kw)),
        )
        tab.server.set_next_mission = (
            lambda target, add_if_missing=False:
            self.calls.append((target, add_if_missing))
        )
        tab.missions_store._data["available"] = [
            {"name": "Crossroads", "file": "crossroads.bms"},
            {"name": "Rapid Strike", "file": "rapidstrike.bms"},
        ]
        return tab

    def queue(self, *files):
        self.tab.server.mission_entries = [
            MissionEntry(index, name) for index, name in enumerate(files)
        ]


def admin_rig(tmp_path):
    rig = CapturingRig(tmp_path, {"mods": ["Dale", "Ham"], "mod_ranks": {"Dale": ADMIN}})
    return rig


def run_ops(rig):
    for op, _kw in rig.ops:
        op()


def test_setnext_queues_a_map_already_in_the_rotation(tmp_path):
    rig = admin_rig(tmp_path)
    rig.queue("rapidstrike.bms", "crossroads.bms")
    rig.tab._check_mod_command("Dale: !setnext crossroads")
    assert rig.actions == ["Queue mission Crossroads as next"]
    run_ops(rig)
    assert rig.calls == [("crossroads.bms", False)]
    assert rig.ops[0][1]["announcement"] == "Next map: Crossroads"
    assert "set next map to Crossroads" in rig.ops[0][1]["log_message"]


def test_setnext_adds_a_map_that_is_not_in_the_rotation(tmp_path):
    rig = admin_rig(tmp_path)
    rig.queue("rapidstrike.bms")
    rig.tab._check_mod_command("Dale: !setnext crossroads")
    run_ops(rig)
    assert rig.calls == [("crossroads.bms", True)]
    assert rig.ops[0][1]["announcement"] == "Next map: Crossroads (added to rotation)"


def test_setnext_refuses_a_map_queued_twice(tmp_path):
    rig = admin_rig(tmp_path)
    rig.queue("crossroads.bms", "rapidstrike.bms", "crossroads.bms")
    rig.tab._check_mod_command("Dale: !setnext crossroads")
    assert rig.actions == []
    assert rig.said == ["Map is queued more than once: Crossroads"]


def test_setnext_without_a_map_shows_usage_and_unknown_map_says_so(tmp_path):
    rig = admin_rig(tmp_path)
    rig.tab._check_mod_command("Dale: !setnext")
    rig.tab._check_mod_command("Dale: !setnext zzzqqq")
    assert rig.actions == []
    assert rig.said[0] == "Usage: !setnext <map>"
    assert rig.said[1] == "Map not found: zzzqqq"


def test_setnext_is_admin_only_by_default(tmp_path):
    rig = admin_rig(tmp_path)
    rig.queue("crossroads.bms")
    rig.tab._check_mod_command("Ham: !setnext crossroads")
    assert rig.actions == []
    assert rig.said == ["!setnext is not allowed for Moderator."]
    rig.tab._check_mod_command("Griefer: !setnext crossroads")
    assert rig.actions == [] and len(rig.said) == 1


def test_setnext_is_listed_in_the_command_table(tmp_path):
    rig = admin_rig(tmp_path)
    assert rig.who_column("!setnext") == "Admin"
