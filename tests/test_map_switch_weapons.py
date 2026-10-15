"""The next map's weapons go on BEFORE it loads (Dale, 2026-10-15): a player's
weapon menu is fixed when they load in, and WolfRAT was 55 s late after a big
TAC map load.  Covers WolfRAT's own switches (before MISSION CYCLE), rounds that
end by themselves (round_over) and the quicker reconnect."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wolfrat import map_weapons as mw
from wolfrat.admin_commands import AdminOperation, WeaponEntry, WeaponMode
from wolfrat.app import ServerTab
from wolfrat.protocol import ServerManager

from tests.test_map_weapons import FakeServer, NAMES, keeper, western_rule  # noqa: F401 (fixture)
from tests.test_server_manager import FakeSession

A, R, N = WeaponMode.ALWAYS, WeaponMode.ARMORY, WeaponMode.NEVER

MISSIONS = [
    "0: TD-SulphurCreek.bms - () () () <CURRENT MISSION> <>",
    "1: AS-KutuTowerAssault.BMS - () () () <> <NEXT MISSION>",
    "2: TD-Western.bms - () () () <> <>",
]


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(mw, "RETRY_SECONDS", 0)
    monkeypatch.setattr(mw, "RECHECK_SECONDS", 0)


# ---- reading the mission list --------------------------------------------------

def test_next_map_is_the_next_mission_row_else_the_one_after_current():
    assert mw.next_map_from_missions(MISSIONS) == "AS-KutuTowerAssault.BMS"
    no_next = [line.replace("<NEXT MISSION>", "<>") for line in MISSIONS]
    assert mw.next_map_from_missions(no_next) == "AS-KutuTowerAssault.BMS"
    last = ["0: A.bms - () () () <> <>", "1: B.bms - () () () <CURRENT MISSION> <>"]
    assert mw.next_map_from_missions(last) == "A.bms"                 # wraps round
    assert mw.next_map_from_missions(["0: Only.bms - () () () <CURRENT MISSION> <>"]) == "Only.bms"
    assert mw.next_map_from_missions([]) is None


def test_full_plan_never_trusts_the_table_it_holds():
    server = FakeServer()                                   # all Yes...
    entries = server.entries()
    wanted = mw.targets(entries, None, mw.NO_RULE_ALL_YES)   # ...and Yes wanted
    assert mw.plan(entries, wanted) == []                    # the normal plan sends nothing
    steps = mw.full_plan(entries, wanted)                    # before a load: send it anyway
    assert steps == [mw.Step(A)]


# ---- before WolfRAT's own MISSION CYCLE -----------------------------------------

def test_steps_before_cycle_for_a_rule_map_and_a_no_rule_map(tmp_path):
    server = FakeServer()
    k = keeper(tmp_path, server, rules=[western_rule()])
    steps = k.steps_before_cycle("TD-Western.bms", server.entries())
    assert steps[0] == (N, None)                                   # SET ALL No, then the 3 allowed
    assert {(e.name, m) for m, e in steps[1:]} == {("WPN_357", A), ("WPN_RemmingtonSG", R),
                                                    ("WPN_M24", R)}
    assert k.steps_before_cycle("AS-KutuTowerAssault.BMS", server.entries()) == [(A, None)]
    assert any("set before the switch" in line for line in k.lines)


def test_nothing_before_the_cycle_when_off_or_left_alone(tmp_path):
    server = FakeServer()
    assert keeper(tmp_path, server, enabled=False).steps_before_cycle("X.bms", server.entries()) == []
    left = keeper(tmp_path, server, no_rule=mw.NO_RULE_LEAVE)
    assert left.steps_before_cycle("X.bms", server.entries()) == []
    assert keeper(tmp_path, server).steps_before_cycle(None, server.entries()) == []


class FakeKeeperSession(FakeSession):
    """FakeSession + a second weapon, so a plan has a SET ALL and a SET."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from dataclasses import replace
        self.snapshot = replace(self.snapshot, weapons=(
            WeaponEntry(37, "M4", WeaponMode.ARMORY, revision=6),
            WeaponEntry(40, "WPN_357", WeaponMode.ARMORY, revision=6),
        ))


@pytest.fixture
def manager():
    sessions = []

    def factory(*args, **kwargs):
        session = FakeKeeperSession(*args, **kwargs)
        sessions.append(session)
        return session

    sm = ServerManager(session_factory=factory, verification_attempts=3, verification_delay=0)
    ok, _ = sm.connect("127.0.0.1", 4000, "admin", "pw")
    assert ok
    sm.refresh_weapons().result()
    sm.session = sessions[-1]
    sm.session.specs.clear()
    yield sm
    sm.stop_polling()
    sm.disconnect()


def test_switch_sends_the_next_maps_weapons_before_mission_cycle(manager):
    seen = []

    def hook(next_map, entries):
        seen.append((next_map, [e.name for e in entries]))
        return [(N, None), (A, next(e for e in entries if e.name == "WPN_357"))]

    manager.before_cycle = hook
    manager.switch_mission(4).result()
    ops = [spec.operation for spec in manager.session.specs]
    assert seen == [("CP08.BMS", ["M4", "WPN_357"])]                # told which map comes next
    cycle = ops.index(AdminOperation.MISSION_CYCLE)
    weapon_sets = [i for i, op in enumerate(ops) if op is AdminOperation.WEAPON_SET]
    assert len(weapon_sets) == 2 and all(i < cycle for i in weapon_sets)
    texts = [spec.text for spec in manager.session.specs if spec.operation is AdminOperation.WEAPON_SET]
    assert texts[0].startswith("WEAPON SET ALL") and "40" in texts[1]


def test_plain_cycle_asks_for_the_mission_lists_next_map(manager):
    seen = []
    manager.before_cycle = lambda next_map, entries: seen.append(next_map) or []
    manager.cycle_mission().result()
    assert seen == ["CP08.BMS"]                                     # one map: it comes round again
    assert [s.operation for s in manager.session.specs] == [AdminOperation.MISSION_CYCLE]


def test_a_broken_hook_never_stops_the_switch(manager):
    def hook(next_map, entries):
        raise RuntimeError("boom")

    manager.before_cycle = hook
    manager.cycle_mission().result()
    assert [s.operation for s in manager.session.specs] == [AdminOperation.MISSION_CYCLE]


# ---- a round that ends by itself ---------------------------------------------------

def test_round_over_puts_the_next_maps_weapons_on_and_the_load_still_checks(tmp_path):
    server = FakeServer()                                  # Sulphur Creek's... all Yes here
    k = keeper(tmp_path, server, rules=[western_rule()])
    k.on_next("TD-Western.bms")
    k.on_map("TD-SulphurCreek.bms")
    k.apply(*k._take(0))                                   # the current map's own apply
    server.commands.clear()
    k.round_over()
    request = k._take(0)
    assert request == ("TD-Western.bms", mw.BEFORE_LOAD)
    k.apply(*request)
    assert server.table[3 + NAMES.index("WPN_357")] is A and server.table[3] is N   # on before the load
    assert k.applied_map == "TD-SulphurCreek.bms"          # not marked done: the load re-checks
    k.on_map("TD-Western.bms")
    assert k._take(0) == ("TD-Western.bms", "new map")
    server.commands.clear()
    k.apply("TD-Western.bms", "new map")
    assert server.commands == []                           # already right after the load
    assert any("already right" in line for line in k.lines)


def test_round_over_after_wolfrats_own_switch_does_nothing_more(tmp_path):
    server = FakeServer()
    k = keeper(tmp_path, server, rules=[western_rule()])
    k.on_next("TD-Western.bms")
    k.steps_before_cycle(None, server.entries())           # the switch already sent them
    k.round_over()                                         # the CYCLE ends the round too
    assert k._take(0) is None


def test_round_over_when_off_does_nothing(tmp_path):
    k = keeper(tmp_path, FakeServer(), enabled=False)
    k.on_next("TD-Western.bms")
    k.round_over()
    assert k._take(0) is None


# ---- reconnect --------------------------------------------------------------------

@pytest.mark.parametrize("attempt, seconds", [(1, 3), (2, 5), (3, 10), (4, 30), (9, 30)])
def test_reconnect_comes_quickly_after_a_map_load(attempt, seconds):
    tab = SimpleNamespace(_reconnect_attempts=attempt, RECONNECT_STEPS_MS=ServerTab.RECONNECT_STEPS_MS,
                          RECONNECT_SLOW_MS=ServerTab.RECONNECT_SLOW_MS)
    assert ServerTab._reconnect_delay_ms(tab) == seconds * 1000
