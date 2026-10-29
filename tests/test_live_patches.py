"""Live patches: the spacebar parachute written into a running server."""

import struct

import pytest

from wolfrat import live_patches as lp

# The cave running on our server since 2026-10-07 (proven in game: 9 chutes
# by space), read back from the process: base 0x36D0000, manual -6144, no net.
PROVEN_BASE = 0x36D0000
PROVEN_CODE = bytes.fromhex(
    "f6462c10744af6862c010000207441a900200000743a81bea000000000e8ffff7f2e"
    "ff0514006d03eb06ff0510006d03518b8ea0000000890d18006d038b8e2c010000"
    "890d1c006d035983c820894624e9887adefc")


class FakeServer:
    """Sparse memory of a server process: stock parachute code, room for caves."""

    def __init__(self, site_bytes=lp.CHUTE_STOCK, can_alloc=True, can_write=True):
        self.mem = {}
        self._put(lp.CHUTE_SITE, site_bytes)
        self.next_cave = 0x30000
        self.can_alloc = can_alloc
        self.can_write = can_write
        self.code_writes = []
        self.pid = 4242

    def _put(self, address, data):
        for i, b in enumerate(data):
            self.mem[address + i] = b

    def read(self, address, size):
        try:
            return bytes(self.mem[address + i] for i in range(size))
        except KeyError:
            raise OSError("unmapped") from None

    def write(self, address, data):
        if not self.can_write:
            raise OSError("access denied")
        self._put(address, data)

    def write_code(self, writes):
        if not self.can_write:
            raise OSError("access denied")
        for address, data in writes:
            self.code_writes.append((address, bytes(data)))
            self._put(address, data)

    def alloc_code(self, size):
        if not self.can_alloc:
            raise OSError("denied")
        address = self.next_cave
        self.next_cave += 0x10000
        self._put(address, b"\0" * size)
        return address

    def chute_opened(self, by_space=0, by_net=0):
        """What the server's own code does to the counters when chutes open."""
        state = lp.chute_state(self)
        self._put(state.cave + 16, struct.pack("<ii", by_net, by_space))


# ----------------------------------------------------------------- encoder
def test_cave_starts_with_tag_and_settings():
    blob, entry = lp.build_chute_cave(0x30000, -6144)
    assert blob[:8] == b"FMJCHUTE"
    assert struct.unpack("<ii", blob[8:16]) == (-6144, 0)
    assert entry == 0x30000 + 32


def test_cave_jumps_back_to_the_game():
    for base in (0x30000, 0x7FFE0000):
        blob, _ = lp.build_chute_cave(base, -6144)
        assert blob[-5] == 0xE9
        end = base + len(blob)
        assert end + struct.unpack("<i", blob[-4:])[0] == lp.CHUTE_BACK


def test_cave_matches_the_proven_bytes():
    """Same code as the tool that was proven in game (assembled by keystone at
    the cave address it really got, then read back from the live server)."""
    blob, _ = lp.build_chute_cave(PROVEN_BASE, -6144)
    assert blob[32:] == PROVEN_CODE


def test_cave_rejects_silly_settings():
    with pytest.raises(ValueError):
        lp.build_chute_cave(0x30000, 100)


# ------------------------------------------------------------- on / off
def test_stock_server_reads_stock():
    assert lp.chute_state(FakeServer()).kind == lp.STOCK


def test_on_then_off():
    server = FakeServer()
    state = lp.chute_on(server)
    assert state.on and state.manual == -6144 and state.safety_net == 0
    assert state.by_space == 0
    # the jump is the only code write, and it lands on our entry
    assert server.code_writes == [(lp.CHUTE_SITE, server.read(lp.CHUTE_SITE, 5))]
    assert lp.chute_off(server).kind == lp.STOCK
    assert server.read(lp.CHUTE_SITE, len(lp.CHUTE_STOCK)) == lp.CHUTE_STOCK


def test_resetting_uses_a_fresh_cave():
    server = FakeServer()
    first = lp.chute_on(server)
    second = lp.chute_on(server)
    assert second.cave != first.cave and second.on


def test_unknown_code_is_left_alone():
    server = FakeServer(site_bytes=b"\x90" * len(lp.CHUTE_STOCK))
    assert lp.chute_state(server).kind == lp.UNKNOWN
    with pytest.raises(lp.LivePatchError):
        lp.chute_on(server)
    assert server.code_writes == []


def test_a_foreign_jump_is_unknown():
    """onHook (or anything else) jumping out at the same spot: not ours."""
    jump = b"\xe9" + struct.pack("<i", 0x100) + lp.CHUTE_STOCK[5:]
    server = FakeServer(site_bytes=jump)
    assert lp.chute_state(server).kind == lp.UNKNOWN


def test_counters_are_read():
    server = FakeServer()
    lp.chute_on(server)
    server.chute_opened(by_space=9)
    state = lp.chute_state(server)
    assert state.by_space == 9
    assert "Opened so far: 9." in lp.describe_chute(state)


# --------------------------------------------------------------- keeper
ON = lp.ChuteWish(True)
OFF = lp.ChuteWish(False)


def test_ticking_the_box_switches_it_on():
    server, keeper = FakeServer(), lp.ChuteKeeper()
    keeper.changed()
    result = keeper.tick(server, ON, writable=True, reapply=False)
    assert result.state.on and result.wish == ON and "ON" in result.note
    assert not keeper.dirty


def test_a_click_waits_for_write_access():
    server, keeper = FakeServer(), lp.ChuteKeeper()
    keeper.changed()
    result = keeper.tick(server, ON, writable=False, reapply=False)
    assert result.waiting and not result.state.on and keeper.dirty
    result = keeper.tick(server, ON, writable=True, reapply=False)
    assert result.state.on


def test_unticking_puts_the_normal_parachute_back():
    server, keeper = FakeServer(), lp.ChuteKeeper()
    lp.chute_on(server)
    keeper.changed()
    result = keeper.tick(server, OFF, writable=True, reapply=False)
    assert result.state.kind == lp.STOCK and "OFF" in result.note


def test_restart_without_reapply_unticks():
    keeper = lp.ChuteKeeper()
    restarted = FakeServer()                          # new process, stock code
    result = keeper.tick(restarted, ON, writable=True, reapply=False)
    assert result.wish.on is False
    assert "no longer in the server" in result.note
    assert restarted.code_writes == []


def test_restart_with_reapply_puts_it_back():
    keeper = lp.ChuteKeeper()
    restarted = FakeServer()
    result = keeper.tick(restarted, ON, writable=True, reapply=True)
    assert result.state.on and result.wish.on and "put back" in result.note


def test_a_failed_put_back_after_a_restart_stays_ticked_and_retries(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(lp.time, "monotonic", lambda: clock[0])
    keeper = lp.ChuteKeeper()
    restarted = FakeServer(can_write=False)
    result = keeper.tick(restarted, ON, writable=True, reapply=True)
    assert result.wish.on and "Still ticked" in result.note
    assert keeper.tick(restarted, ON, writable=True, reapply=True).note == ""   # waits
    clock[0] += lp.ChuteKeeper.RETRY_SECONDS
    again = keeper.tick(restarted, ON, writable=True, reapply=True)
    assert again.wish.on and again.note == ""                 # same error: not repeated
    restarted.can_write = True
    clock[0] += lp.ChuteKeeper.RETRY_SECONDS
    back = keeper.tick(restarted, ON, writable=True, reapply=True)
    assert back.state.on and back.wish.on and "put back" in back.note


def test_reapply_waits_for_write_access():
    keeper = lp.ChuteKeeper()
    result = keeper.tick(FakeServer(), ON, writable=False, reapply=True)
    assert result.waiting and result.wish.on


def test_already_on_in_the_server_shows_ticked():
    server = FakeServer()
    lp.chute_on(server)                               # e.g. by the tool, or a closed WolfRAT
    result = lp.ChuteKeeper().tick(server, OFF, writable=False, reapply=False)
    assert result.wish == ON and "already on" in result.note


def test_steady_state_is_quiet():
    server, keeper = FakeServer(), lp.ChuteKeeper()
    lp.chute_on(server)
    result = keeper.tick(server, ON, writable=True, reapply=False)
    assert result.wish == ON and result.note == "" and not result.waiting


def test_unknown_server_keeps_the_tick_and_writes_nothing():
    server = FakeServer(site_bytes=b"\x90" * len(lp.CHUTE_STOCK))
    keeper = lp.ChuteKeeper()
    keeper.changed()
    result = keeper.tick(server, ON, writable=True, reapply=True)
    assert result.state.kind == lp.UNKNOWN and result.wish == ON
    assert server.code_writes == []


def test_failed_write_shows_the_server_as_it_is():
    server = FakeServer(can_write=False)
    keeper = lp.ChuteKeeper()
    keeper.changed()
    result = keeper.tick(server, ON, writable=True, reapply=False)
    assert result.wish.on is False and "administrator" in result.note
    assert not keeper.dirty                            # no retry storm every 5 s


def _leftover_net_cave(server, net):
    """A safety-net cave left in a server by a test build (2026-10-09)."""
    cave = server.alloc_code(lp.CHUTE_CAVE_SIZE)
    head = lp.CHUTE_TAG + struct.pack("<iiiiii", -6144, net, 0, 0, 0, 0)
    server._put(cave, head + b"\xc3")
    server.write_code([(lp.CHUTE_SITE, lp._jump_to(lp.CHUTE_SITE, cave + 32))])


@pytest.mark.parametrize("net", [-28672, 1])     # the 2-second test, the ground test
def test_a_leftover_safety_net_is_replaced_by_the_plain_one_once(net):
    server = FakeServer()
    _leftover_net_cave(server, net)
    assert "safety-net test version" in lp.describe_chute(lp.chute_state(server))
    keeper = lp.ChuteKeeper()
    result = keeper.tick(server, ON, writable=True, reapply=False)
    assert result.state.on and result.state.safety_net == 0 and "replaced" in result.note
    assert keeper.tick(server, ON, writable=True, reapply=False).note == ""


def test_a_failed_replace_is_not_retried_every_poll():
    server = FakeServer()
    _leftover_net_cave(server, -28672)
    server.can_alloc = False
    keeper = lp.ChuteKeeper()
    assert "administrator" in keeper.tick(server, ON, writable=True, reapply=False).note
    assert keeper.tick(server, ON, writable=True, reapply=False).note == ""


def test_there_is_no_safety_net_any_more():
    """Dale 2026-10-09: 'people should just learn to press spacebar'."""
    blob, _ = lp.build_chute_cave(0x30000)
    assert struct.unpack("<i", blob[12:16])[0] == 0
    assert not hasattr(lp.ChuteWish(), "safety_net")
