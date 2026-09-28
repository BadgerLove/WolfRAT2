"""Hunting sharks: entity-table scan, live writes, and the Wildlife page."""

import struct

import pytest

from tests.test_weather import FakeServer
from tests.test_weather_tab import make
from wolfrat import wildlife as wl
from wolfrat.weather import Addr as WAddr


REGION = 0xA80000
ENTITIES = 0xB00000
STRIDE = 0x200
NAMES = 0xB40000
AI = 0xB50000


ONHOOK_LEG = b"\xe9\x11\x22\x33\x44" + wl.KIN_LEG_RETAIL[5:]

CODE = {                                      # the two code spots, per server kind
    "retail": (wl.KIN_LEG_RETAIL, wl.KIN_CAVE_FREE),
    "fixed": (wl.KIN_LEG_FIXED, wl.KIN_CAVE_FIXED),
    "onhook": (ONHOOK_LEG, wl.KIN_CAVE_FREE),
    "cave_taken": (wl.KIN_LEG_RETAIL, b"\x90" * len(wl.KIN_CAVE_FREE)),
    "other_exe": (b"\x00" * len(wl.KIN_LEG_RETAIL), wl.KIN_CAVE_FREE),
}


class FlatMemory:
    """One flat byte region, so reads at any offset work like the real process,
    plus the two code spots of the berserk fix (``code=None``: unreadable)."""

    def __init__(self, code="retail"):
        self.buf = bytearray(0x100000)
        self.writes = []
        self.code_writes = []
        self.fail_code = False
        self.code = {}
        if code:
            leg, cave = CODE[code]
            self.code = {wl.KIN_LEG: bytearray(leg), wl.KIN_CAVE: bytearray(cave)}

    def _code(self, address, size):
        for base, data in self.code.items():
            if base <= address and address + size <= base + len(data):
                return data, address - base
        return None, 0

    def _off(self, address, size):
        off = address - REGION
        if off < 0 or off + size > len(self.buf):
            raise wl.WildlifeError("unmapped")
        return off

    def read(self, address, size):
        data, off = self._code(address, size)
        if data is not None:
            return bytes(data[off:off + size])
        off = self._off(address, size)
        return bytes(self.buf[off:off + size])

    def write(self, address, data):
        off = self._off(address, len(data))
        self.buf[off:off + len(data)] = data
        self.writes.append((address, bytes(data)))

    def write_code(self, writes):
        if self.fail_code:
            raise wl.WildlifeError("access denied")
        for address, new in writes:
            data, off = self._code(address, len(new))
            assert data is not None, hex(address)
            data[off:off + len(new)] = new
            self.code_writes.append((address, bytes(new)))

    def u32(self, address, value):
        self.write(address, struct.pack("<I", value))


def make_server(kinds=("Shark", "Shark", "Tiger", "Shark"), attack_m=100, team=0, engage_m=100,
                code="retail"):
    mem = FlatMemory(code)
    mem.u32(wl.Addr.ENTITY_BASE, ENTITIES)
    mem.u32(wl.Addr.ENTITY_STRIDE, STRIDE)
    mem.u32(wl.Addr.ENTITY_USED, len(kinds) + 1)          # +1: an empty slot
    for i, kind in enumerate(kinds):
        ent = ENTITIES + i * STRIDE
        name = NAMES + i * 16
        mem.write(name, kind.encode() + b"\0")
        mem.u32(ent + wl.ENT_NAME_PTR, name)
        ai = AI + i * 0x100
        mem.u32(ent + wl.ENT_AI_PTR, ai)
        mem.write(ent + wl.ENT_TEAM, bytes([team]))
        mem.write(ent + wl.ENT_HP, struct.pack("<h", 2100 - i))
        mem.u32(ai + wl.AI_ATTACK_DIST, attack_m << 16)
        mem.u32(ai + wl.AI_MIN_ENGAGE, engage_m << 16)       # an .npj map: min == max
        mem.u32(ai + wl.AI_MAX_ENGAGE, engage_m << 16)
        mem.u32(ai + wl.AI_FLAGS, 0x1)                        # some other AI bit, kept
    mem.writes.clear()
    return mem


def test_scan_finds_only_sharks_with_their_numbers():
    mem = make_server()
    sharks = wl.scan(mem)
    assert [s.address for s in sharks] == [ENTITIES, ENTITIES + STRIDE, ENTITIES + 3 * STRIDE]
    assert all(s.team == 0 and s.attack_m == 100 and not s.hunting and not s.can_close_in
               for s in sharks)
    assert [s.hp for s in sharks] == [2100, 2099, 2097]


def test_scan_rejects_a_table_that_does_not_look_right():
    mem = make_server()
    mem.u32(wl.Addr.ENTITY_STRIDE, 4)
    with pytest.raises(wl.WildlifeError, match="does not look right"):
        wl.scan(mem)
    with pytest.raises(wl.WildlifeError, match="Could not read"):
        wl.scan(FakeServer())                 # sparse fake: the entity table is unmapped


def _u(mem, address):
    return struct.unpack("<I", mem.read(address, 4))[0]


def test_hunt_writes_berserk_attack_and_start_chasing_only_where_needed():
    mem = make_server()
    sharks = wl.scan(mem)
    assert wl.hunt(mem, sharks, 10) == 3
    assert len(mem.writes) == 9                                   # flags + attack + min, x3
    assert mem.read(ENTITIES + wl.ENT_TEAM, 1) == b"\x00"          # team left alone
    assert _u(mem, AI + wl.AI_FLAGS) == 0x1 | wl.BERSERK             # other bits kept
    assert _u(mem, AI + wl.AI_ATTACK_DIST) == 10 << 16
    assert _u(mem, AI + wl.AI_MIN_ENGAGE) == wl.START_CHASE_M << 16
    assert _u(mem, AI + wl.AI_MAX_ENGAGE) == 100 << 16              # sight untouched
    assert _u(mem, AI + 2 * 0x100 + wl.AI_FLAGS) == 0x1              # the tiger
    rescanned = wl.scan(mem)
    assert all(s.hunting and s.can_close_in for s in rescanned)
    mem.writes.clear()
    assert wl.hunt(mem, rescanned, 10) == 0 and mem.writes == []


def test_hunt_leaves_a_map_start_chasing_distance_alone():
    mem = make_server()
    for i in (0, 1, 3):
        mem.u32(AI + i * 0x100 + wl.AI_MIN_ENGAGE, 6 << 16)          # the map set one
    mem.writes.clear()
    wl.hunt(mem, wl.scan(mem), 10)
    assert _u(mem, AI + wl.AI_MIN_ENGAGE) == 6 << 16
    assert not any(a == AI + wl.AI_MIN_ENGAGE for a, _ in mem.writes)


def test_hunt_puts_sharks_left_on_team_3_back_to_their_team():
    mem = make_server(team=wl.LEGACY_TEAM)                         # an older WolfRAT did this
    wl.hunt(mem, wl.scan(mem), 10)
    assert mem.read(ENTITIES + wl.ENT_TEAM, 1) == b"\x00"
    assert all(s.team == 0 and s.hunting for s in wl.scan(mem))


def test_keeper_states_and_reapply_after_respawn():
    mem = make_server()
    keeper = wl.SharkKeeper()
    tick = lambda **kw: keeper.tick(mem, map_name="hookah1", linked=True, attack_m=10,
                                    **{"enabled": True, "writable": True, **kw})
    assert "Not linked" in keeper.tick(mem, enabled=True, attack_m=10, writable=True,
                                       map_name=None, linked=False).text
    off = tick(enabled=False)
    assert "3 sharks on hookah1" in off.text and "hunting is off" in off.text and not off.hooked
    assert mem.writes == []
    assert "cannot write" in tick(writable=False).text
    on = tick()
    assert on.hooked and on.text.startswith("🟢 3 sharks hooked on hookah1")
    assert "Re-applied 3x" in on.text and on.reapplied == 3
    again = tick()
    assert again.hooked and again.reapplied == 3            # nothing drifted
    # a respawn puts the map's numbers back on one shark
    mem.u32(AI + wl.AI_FLAGS, 0x1)
    mem.u32(AI + wl.AI_ATTACK_DIST, 100 << 16)
    mem.u32(AI + wl.AI_MIN_ENGAGE, 100 << 16)
    assert tick().reapplied == 4
    # the slider moved: every shark gets the new distance
    moved = keeper.tick(mem, enabled=True, attack_m=12, writable=True, map_name="hookah1", linked=True)
    assert moved.reapplied == 7 and "12 m" in moved.text


def test_the_fix_bytes_are_one_jump_and_a_small_addition():
    # same length as the code they replace; the addition fits the free space
    assert len(wl.KIN_LEG_FIXED) == len(wl.KIN_LEG_RETAIL) == 36
    assert len(wl.KIN_CAVE_FIXED) == 25
    # the leg's jump lands on the addition, whose exits land back in the filter
    jmp = wl.KIN_LEG + 30
    assert wl.KIN_LEG_FIXED[30] == 0xE9
    assert jmp + 5 + struct.unpack("<i", wl.KIN_LEG_FIXED[31:35])[0] == wl.KIN_CAVE
    team_filter, target = wl.KIN_LEG + 36, wl.KIN_LEG + 64
    je = wl.KIN_CAVE + 14
    assert je + 6 + struct.unpack("<i", wl.KIN_CAVE_FIXED[16:20])[0] == team_filter
    assert wl.KIN_CAVE + 25 + struct.unpack("<i", wl.KIN_CAVE_FIXED[21:25])[0] == target


@pytest.mark.parametrize("code, state", [
    ("retail", wl.KIN_RETAIL), ("fixed", wl.KIN_FIXED), ("onhook", wl.KIN_ONHOOK),
    ("cave_taken", wl.KIN_UNKNOWN), ("other_exe", wl.KIN_UNKNOWN), (None, wl.KIN_UNKNOWN),
])
def test_kin_state(code, state):
    assert wl.kin_state(make_server(code=code)) == state


def _hunting(mem):
    return [s.hunting for s in wl.scan(mem)]


def test_keeper_adds_the_ignore_each_other_fix_once_before_berserk():
    mem = make_server(code="retail")
    keeper = wl.SharkKeeper()
    tick = lambda: keeper.tick(mem, enabled=True, attack_m=10, writable=True,
                               map_name="hookah1", linked=True)
    first = tick()
    assert first.hooked and "ignoring each other" in first.text
    assert "ignore each other" in first.note and "restarts" in first.note
    # the addition goes in before the jump to it
    assert mem.code_writes == [(wl.KIN_CAVE, wl.KIN_CAVE_FIXED), (wl.KIN_LEG, wl.KIN_LEG_FIXED)]
    assert wl.kin_state(mem) == wl.KIN_FIXED and all(_hunting(mem))
    second = tick()
    assert second.hooked and second.note == "" and len(mem.code_writes) == 2


@pytest.mark.parametrize("code", ["fixed", "onhook"])
def test_keeper_leaves_an_existing_fix_alone(code):
    mem = make_server(code=code)
    status = wl.SharkKeeper().tick(mem, enabled=True, attack_m=10, writable=True,
                                   map_name="hookah1", linked=True)
    assert status.hooked and status.note == "" and mem.code_writes == []
    assert all(_hunting(mem))


@pytest.mark.parametrize("code", ["cave_taken", "other_exe", None])
def test_keeper_will_not_make_sharks_berserk_without_the_fix(code):
    mem = make_server(code=code)
    status = wl.SharkKeeper().tick(mem, enabled=True, attack_m=10, writable=True,
                                   map_name="hookah1", linked=True)
    assert not status.hooked and "NOT set to hunt" in status.text
    assert mem.code_writes == [] and mem.writes == [] and not any(_hunting(mem))


def test_keeper_will_not_make_sharks_berserk_if_the_fix_cannot_be_written():
    mem = make_server(code="retail")
    mem.fail_code = True
    status = wl.SharkKeeper().tick(mem, enabled=True, attack_m=10, writable=True,
                                   map_name="hookah1", linked=True)
    assert not status.hooked and "NOT set to hunt" in status.text and "administrator" in status.text
    assert mem.writes == [] and not any(_hunting(mem))


def test_keeper_switched_off_never_touches_the_code():
    mem = make_server(code="retail")
    wl.SharkKeeper().tick(mem, enabled=False, attack_m=10, writable=True,
                          map_name="hookah1", linked=True)
    assert mem.code_writes == [] and wl.kin_state(mem) == wl.KIN_RETAIL


def test_keeper_no_sharks_and_unreadable_table():
    keeper = wl.SharkKeeper()
    mem = make_server(kinds=("Tiger",))
    text = keeper.tick(mem, enabled=True, attack_m=10, writable=True, map_name="x", linked=True).text
    assert text.startswith("No sharks on x")
    text = keeper.tick(FakeServer(), enabled=True, attack_m=10, writable=True, map_name="x", linked=True).text
    assert "Could not read" in text


# ---------------------------------------------------------------- the page

def _table_with_no_sharks(server):
    server.poke(wl.Addr.ENTITY_BASE, ENTITIES)
    server.poke(wl.Addr.ENTITY_STRIDE, STRIDE)
    server.poke(wl.Addr.ENTITY_USED, 0)
    return server


def test_wildlife_is_the_third_page_and_says_tac_only(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    assert tab.pages.tabText(2) == "🦈 Wildlife" and tab.pages.widget(2) is tab.wildlife_page
    assert "OscarMike247" in tab.wildlife_page.banner_lbl.text()
    assert "TAC mod only" in tab.wildlife_page.banner_lbl.text()
    assert tab.wildlife_page.status_lbl.text().startswith("No sharks")
    assert tab.wildlife_page.enabled_cb.isEnabled()
    assert not tab.wildlife_page.enabled()                   # off by default


def test_no_server_greys_the_wildlife_page_too(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, fail="No jointops.exe is running on this PC.")
    assert "No jointops.exe" in tab.wildlife_page.status_lbl.text()
    assert not tab.wildlife_page.enabled_cb.isEnabled()


def test_switching_on_asks_for_a_writable_handle_and_saves(qtbot, tmp_path):
    tab, _, attaches = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    assert attaches == [False]
    tab.wildlife_page.enabled_cb.setChecked(True)
    assert attaches == [False, True]
    saved = (tmp_path / "wolfrat_weather.json").read_text(encoding="utf-8")
    assert '"enabled": true' in saved and '"attack_m": 10' in saved
    assert any("Hunting sharks switched on" in tab.log_list.item(i).text()
               for i in range(tab.log_list.count()))


def test_wildlife_page_has_two_scrolling_columns(qtbot, tmp_path):
    from PyQt6.QtWidgets import QScrollArea
    tab, _, _ = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    columns = [a for a in tab.wildlife_page.findChildren(QScrollArea) if a.widgetResizable()]
    assert len(columns) == 2
