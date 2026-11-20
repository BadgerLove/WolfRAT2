"""Lag compensation: switching the game's three calls on and off (2026-11-20).

From the 2026-11-19 code review: another WolfRAT build's helper being on must
read as on (B3), two WolfRATs must not take the calls from each other (P1),
a switch must be all or nothing (P2), and a helper that is slow to load must
not be treated as loaded (P3).

Everything here runs against a fake server process: a few bytes of "game
code" with the three calls, and one block per loaded helper."""

import struct

import pytest

from wolfrat import lag_comp as lc

# (call site, the game's own function), in the order lag_comp._sites uses:
# bone test, person ("leg") test, per-tick call.
BONE, LEG, TICK = (0x4E4C59, 0x4E4670), (0x4EA5BF, 0x4E4A30), (0x4C223A, 0x4EC020)
SITES = (BONE, LEG, TICK)
MINE = "wolfrat_lagcomp_mine0000.dll"          # this WolfRAT build's helper
OTHER = "wolfrat_lagcomp_other111.dll"         # another build's
MINE_BASE, OTHER_BASE = 0x10000000, 0x20000000
BLOCK_OFFSET = 0x8000
PID = 4321


def wraps(base):
    return [base + 0x1000, base + 0x2000, base + 0x3000]


def helper_block(base, version=4, status=1, **fields):
    b = lc._Shared()
    b.magic, b.version, b.status = lc.MAGIC, version, status
    b.size = {1: lc._OFF["targets"], 2: lc._V2_SIZE, 3: lc._V3_SIZE, 4: lc._V4_SIZE}[version]
    (b.bone_call, b.bone_func), (b.leg_call, b.leg_func), (b.tick_call, b.tick_func) = SITES
    b.bone_wrap, b.leg_wrap, b.tick_wrap = wraps(base)
    b.extra_ms, b.max_ms, b.min_ping_ms = 70, 400, 80
    b.tick_us = 16000
    for name, value in fields.items():
        setattr(b, name, value)
    return b


class FakeServer:
    """The server process, as lag_comp.ServerProcess AND as WolfRAT's
    ProcessMemory (``write_code``) - both look at the same bytes."""

    def __init__(self, pid=PID):
        self.pid = pid
        self.regions = {}
        self.helpers = {}                # file name -> base
        self.loadable = {}               # file name -> block to appear on load_library
        self.loads = []
        self.code_writes = []            # every write_code call: [(address, bytes)]
        self.fail_code_write = None      # index of the write that fails, in one write_code call
        self.restore_works = True
        for site, func in SITES:
            self.regions[site] = bytearray(lc._call_bytes(site, func))

    # -- test set-up
    def add_helper(self, name, base, block=None, **fields):
        block = block if block is not None else helper_block(base, **fields)
        self.helpers[name] = base
        self.regions[base + BLOCK_OFFSET] = bytearray(bytes(block)[:block.size])
        return base + BLOCK_OFFSET

    def point_calls_at(self, targets):
        for (site, _func), target in zip(SITES, targets):
            self.regions[site][:] = lc._call_bytes(site, target)

    def targets(self):
        return [lc._call_target(self, site) for site, _ in SITES]

    def block(self, name):
        raw = bytes(self.regions[self.helpers[name] + BLOCK_OFFSET])
        return lc._Shared.from_buffer_copy(raw + b"\0" * (lc.SHARED_SIZE - len(raw)))

    # -- lag_comp.ServerProcess
    def __call__(self, pid, read_only=False):
        assert pid == self.pid
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def modules(self):
        return dict(self.helpers)

    def _remote_export(self, base, name):
        assert name == lc.EXPORT_NAME
        return base + BLOCK_OFFSET

    def _find(self, address, size):
        for start, buf in self.regions.items():
            if start <= address and address + size <= start + len(buf):
                return buf, address - start
        raise lc.LagCompError("unmapped %x+%d" % (address, size))

    def read(self, address, size):
        buf, off = self._find(address, size)
        return bytes(buf[off:off + size])

    def write(self, address, data):
        buf, off = self._find(address, len(data))
        buf[off:off + len(data)] = data

    def load_library(self, path):
        import os
        name = os.path.basename(path).lower()
        self.loads.append(name)
        base = MINE_BASE
        self.add_helper(name, base, self.loadable.get(name))
        return base

    # -- weather.ProcessMemory
    def write_code(self, writes, restore=None):
        self.code_writes.append(list(writes))
        done = 0
        try:
            for i, (address, data) in enumerate(writes):
                if i == self.fail_code_write:
                    raise RuntimeError("Could not write the server's code.")
                self.write(address, data)
                done += 1
        except RuntimeError:
            if restore and self.restore_works:
                for address, data in restore[:done + 1]:
                    self.write(address, data)
            raise


@pytest.fixture
def server(monkeypatch, tmp_path):
    fake = FakeServer()
    monkeypatch.setattr(lc, "ServerProcess", fake)
    monkeypatch.setattr(lc, "_block_at", {})
    monkeypatch.setattr(lc, "materialize_dll",
                        lambda data_dir: (str(tmp_path / MINE), MINE, b"MZ"))
    return fake


STOCK_TARGETS = [func for _site, func in SITES]


# --------------------------------------------------------------- B3 (W2)
def test_another_builds_helper_being_on_reads_as_on_from_it(server):
    """This build's helper is not loaded; another build's has the three calls.
    Before: UNKNOWN, the page said "Off - normal JO hits" and had no off switch."""
    address = server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1, observe=1,
                                extra_ms=55, tests=900, hits=200, plain_hits=150)
    server.point_calls_at(wraps(OTHER_BASE))
    state = lc.read_state(server, MINE)
    assert state.kind == lc.OLD and not state.on
    assert (state.base, state.shared) == (OTHER_BASE, address)      # THAT helper's block
    assert state.ready and state.observe and state.settings.extra_ms == 55
    assert (state.stats.tests, state.stats.hits, state.stats.plain_hits) == (900, 200, 150)


def test_this_builds_helper_loaded_but_idle_still_shows_the_one_that_is_on(server):
    server.add_helper(MINE, MINE_BASE, tests=0)
    other = server.add_helper(OTHER, OTHER_BASE, version=3, tests=900, extra_ms=55)
    server.point_calls_at(wraps(OTHER_BASE))
    state = lc.read_state(server, MINE)
    assert state.kind == lc.OLD and state.shared == other and state.stats.tests == 900


def test_the_other_cases_are_unchanged(server):
    assert lc.read_state(server, MINE).kind == lc.UNKNOWN            # no helper at all
    server.add_helper(OTHER, OTHER_BASE, version=3)
    state = lc.read_state(server, MINE)                             # loaded, calls untouched
    assert state.kind == lc.STOCK and not state.shared
    server.point_calls_at([0x67200000, 0x67200100, 0x67200200])     # someone else's code
    assert lc.read_state(server, MINE).kind == lc.FOREIGN
    mine = server.add_helper(MINE, MINE_BASE)
    server.point_calls_at(wraps(MINE_BASE))
    state = lc.read_state(server, MINE)
    assert state.on and state.shared == mine


def test_turn_off_restores_the_calls_from_the_helper_that_owns_them(server):
    server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1)
    server.point_calls_at(wraps(OTHER_BASE))
    after = lc.turn_off(PID, server)
    assert after.kind == lc.STOCK and server.targets() == STOCK_TARGETS
    assert server.block(OTHER).enabled == 0


def test_turn_off_finds_the_owner_among_several_helpers(server):
    """Three copies in the server; the one that is on is neither the first nor
    the last, and one of the others does not fit this exe (its block has no
    addresses)."""
    unfit = helper_block(0x30000000, status=0)
    unfit.bone_call = unfit.leg_call = unfit.tick_call = 0
    server.add_helper("wolfrat_lagcomp_aaaaaaaa.dll", 0x30000000, unfit)
    server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1)
    server.add_helper("wolfrat_lagcomp_zzzzzzzz.dll", 0x40000000, unfit)
    server.point_calls_at(wraps(OTHER_BASE))
    assert lc.read_state(server, MINE).kind == lc.OLD
    after = lc.turn_off(PID, server)
    assert after.kind == lc.STOCK and server.targets() == STOCK_TARGETS
    assert all(address in {site for site, _ in SITES}
               for call in server.code_writes for address, _ in call)


def test_turn_off_leaves_someone_elses_code_alone(server):
    server.add_helper(OTHER, OTHER_BASE, version=3)
    foreign = [0x67200000, 0x67200100, 0x67200200]
    server.point_calls_at(foreign)
    with pytest.raises(lc.LagCompError, match="left it alone"):
        lc.turn_off(PID, server)
    assert server.targets() == foreign and server.code_writes == []


def test_turn_on_takes_over_from_another_builds_helper(server):
    server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1)
    server.point_calls_at(wraps(OTHER_BASE))
    after = lc.turn_on(PID, server, "unused", lc.Settings(False, 60, 400, 80))
    assert after.on and server.targets() == wraps(MINE_BASE)
    assert server.loads == [MINE] and server.block(MINE).enabled == 1
    assert server.block(MINE).extra_ms == 60
