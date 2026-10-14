"""Map weapons: rules, matching, the command plan and the keeper's apply."""

from __future__ import annotations

import pytest

from wolfrat import map_weapons as mw
from wolfrat.admin_commands import WeaponEntry, WeaponMode

A, R, N = WeaponMode.ALWAYS, WeaponMode.ARMORY, WeaponMode.NEVER
NAMES = ["WPN_colt45", "WPN_M9Beretta", "WPN_RemmingtonSG", "WPN_357", "WPN_M4AUTO",
         "WPN_M24", "WPN_Barret", "WPN_M249SAW", "WPN_AK47AUTO", "WPN_MP5SD"]


class FakeServer:
    """The server's weapon table; counts the commands WolfRAT sends."""

    def __init__(self, modes=None, *, resets_to=None, fail_times=0):
        self.table = {i + 3: (modes[i] if modes else A) for i in range(len(NAMES))}
        self.commands = []
        self.resets_to = resets_to       # a server that puts its own back once, after the first apply
        self.fail_times = fail_times

    def entries(self):
        return [WeaponEntry(i, NAMES[i - 3], self.table[i]) for i in sorted(self.table)]

    def list_weapons(self):
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("retail server closed")
        return self.entries()

    def set_all(self, mode):
        self.commands.append(("ALL", mode))
        for i in self.table:
            self.table[i] = mode

    def set_one(self, entry, mode):
        self.commands.append((entry.name, mode))
        self.table[entry.admdef_id] = mode


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(mw, "RETRY_SECONDS", 0)
    monkeypatch.setattr(mw, "RECHECK_SECONDS", 0)


def keeper(tmp_path, server, *, enabled=True, rules=(), no_rule=mw.NO_RULE_ALL_YES):
    lines = []
    k = mw.MapWeaponsKeeper(tmp_path / "wolfrat_map_weapons.json", list_weapons=server.list_weapons,
                            set_all=server.set_all, set_one=server.set_one, log=lines.append,
                            start_thread=False)
    k.config.enabled, k.config.no_rule, k.config.rules = enabled, no_rule, list(rules)
    k.lines = lines
    return k


def western_rule():
    weapons = {name: "NEVER" for name in NAMES}
    weapons.update(WPN_357="ALWAYS", WPN_RemmingtonSG="ARMORY", WPN_M24="ARMORY")
    return mw.MapRule(match="TD-Western.bms", weapons=weapons)


# ---- matching ----------------------------------------------------------------

def test_rule_matches_the_file_name_in_any_case_and_a_part_of_the_name():
    rules = [mw.MapRule(match="Mogadishu.bms"), mw.MapRule(match="western")]
    assert mw.rule_for("MOGADISHU.BMS", rules).match == "Mogadishu.bms"
    assert mw.rule_for("TD-Wild Western Town.bms", rules).match == "western"
    assert mw.rule_for("AS-Doslin.bms", rules) is None


def test_no_close_spelling_guess():
    """The weather rules accept a near spelling; weapons must not."""
    assert mw.rule_for("Mogadisha.bms", [mw.MapRule(match="Mogadishu.bms")]) is None


def test_exact_name_beats_a_longer_partial_one():
    rules = [mw.MapRule(match="western town"), mw.MapRule(match="TD-Western.bms")]
    assert mw.rule_for("td-western.bms", rules).match == "TD-Western.bms"


def test_current_map_from_mission_list():
    lines = ["0: 03TR.BMS - () () () <> <>", "1: MOGADISHU.BMS - (2x) () () <CURRENT MISSION> <>"]
    assert mw.current_map_from_missions(lines) == "MOGADISHU.BMS"
    assert mw.current_map_from_missions(["0: X.BMS - () () () <> <NEXT MISSION>"]) is None


# ---- the plan -----------------------------------------------------------------

def test_mostly_no_map_uses_set_all_then_the_exceptions():
    server = FakeServer()
    wanted = mw.targets(server.entries(), western_rule(), mw.NO_RULE_ALL_YES)
    steps = mw.plan(server.entries(), wanted)
    assert steps[0] == mw.Step(N)
    assert {(s.entry.name, s.mode) for s in steps[1:]} == {
        ("WPN_357", A), ("WPN_RemmingtonSG", R), ("WPN_M24", R)}


def test_one_wrong_weapon_is_one_command():
    server = FakeServer([A] * 9 + [N])
    wanted = mw.targets(server.entries(), None, mw.NO_RULE_ALL_YES)
    assert mw.plan(server.entries(), wanted) == [mw.Step(A, server.entries()[-1])]


def test_a_table_that_is_already_right_sends_nothing():
    server = FakeServer()
    assert mw.plan(server.entries(), mw.targets(server.entries(), None, mw.NO_RULE_ALL_YES)) == []


def test_leave_alone_has_no_target():
    assert mw.targets(FakeServer().entries(), None, mw.NO_RULE_LEAVE) is None


def test_a_weapon_the_rule_never_saw_follows_the_rules_majority():
    rule = western_rule()
    assert rule.mode_for("WPN_NewModGun") is N
    assert rule.mode_for("wpn_357") is A


# ---- the keeper --------------------------------------------------------------

def test_map_change_applies_the_rule_and_back_to_all_yes(tmp_path):
    server = FakeServer()
    k = keeper(tmp_path, server, rules=[western_rule()])
    assert k.apply("TD-WESTERN.BMS", "new map") is None
    assert server.table[6] is A and server.table[5] is R and server.table[7] is N
    assert "rule 'TD-Western.bms' set (1 Yes, 2 Armory, 7 No)" in k.last_status
    k.apply("MOGADISHU.BMS", "new map")              # no rule -> every weapon Yes
    assert set(server.table.values()) == {A}
    assert server.commands[-1] == ("ALL", A)


def test_on_map_only_asks_when_the_map_changes(tmp_path):
    k = keeper(tmp_path, FakeServer())
    asked = []
    k._ask = lambda m, why: asked.append((m, why))
    k.on_map("03TR.BMS")
    k.applied_map = "03TR.BMS"
    k.on_map("03TR.BMS")
    k.on_map("00TRG.BMS")
    assert asked == [("03TR.BMS", "new map"), ("00TRG.BMS", "new map")]


def test_off_does_nothing(tmp_path):
    k = keeper(tmp_path, FakeServer(), enabled=False)
    k._ask = lambda *_: pytest.fail("asked while off")
    k.on_map("03TR.BMS")


def test_reconnect_rechecks_the_same_map_but_not_after_a_manual_change(tmp_path):
    k = keeper(tmp_path, FakeServer())
    asked = []
    k._ask = lambda m, why: asked.append(why)
    k.current_map = k.applied_map = "MOGADISHU.BMS"
    k.on_connected()
    k.on_map("MOGADISHU.BMS")
    assert asked == ["reconnected"]
    k.note_manual_change()
    k.on_connected()
    k.on_map("MOGADISHU.BMS")
    assert asked == ["reconnected"]                   # the admin's hand-back is kept


def test_a_failed_list_is_retried(tmp_path):
    server = FakeServer([N] * 10, fail_times=2)
    k = keeper(tmp_path, server)
    k.apply("03TR.BMS", "new map")
    assert set(server.table.values()) == {A}


def test_giving_up_says_why_and_the_next_poll_tries_again(tmp_path):
    server = FakeServer([N] * 10, fail_times=99)
    k = keeper(tmp_path, server)
    k.apply("03TR.BMS", "new map")
    assert "FAILED after 3 tries - ConnectionError" in k.last_status
    assert k.applied_map is None


def test_a_server_that_changes_them_back_gets_them_set_again(tmp_path):
    server = FakeServer()
    k = keeper(tmp_path, server, rules=[western_rule()])
    k.current_map = "TD-WESTERN.BMS"
    original_recheck = k._recheck

    def recheck_after_server_reset(map_name, wanted, name):
        server.table = {i: A for i in server.table}      # the load put game.cfg's weapons back
        return original_recheck(map_name, wanted, name)
    k._recheck = recheck_after_server_reset
    again = k.apply("TD-WESTERN.BMS", "new map")
    assert again == ("TD-WESTERN.BMS", "changed back after the load")
    k._recheck = original_recheck
    k.apply(*again)
    assert server.table[7] is N


def test_save_rule_round_trips_and_replaces_the_old_one(tmp_path):
    server = FakeServer([A, A, R, A, N, N, N, N, N, N])
    k = keeper(tmp_path, server)
    k.current_map = "MOGADISHU.BMS"
    k.save_rule("MOGADISHU.BMS", server.entries(), label="Battle of Mogadishu - Part 1")
    k.save_rule("Mogadishu.bms", server.entries())
    again = mw.load_config(k.path)
    assert len(again.rules) == 1 and again.rules[0].weapons["WPN_RemmingtonSG"] == "ARMORY"
    assert again.rules[0].summary() == "3 Yes, 1 Armory, 6 No"
    assert k.applied_map == "MOGADISHU.BMS"              # saving does not re-send what is already there


def test_a_broken_file_loads_as_off(tmp_path):
    path = tmp_path / "wolfrat_map_weapons.json"
    path.write_text("{not json", encoding="utf-8")
    config = mw.load_config(str(path))
    assert config.enabled is False and config.rules == []
