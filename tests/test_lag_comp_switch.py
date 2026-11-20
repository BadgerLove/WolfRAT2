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


# --------------------------------------------------------------- P1 (W3)
def test_the_wolfrat_that_runs_it_says_so_in_a_version_4_block(server):
    address = server.add_helper(MINE, MINE_BASE)
    server.point_calls_at(wraps(MINE_BASE))
    state = lc.read_state(server, MINE)
    assert state.on and state.has_v4 and state.owner_pid == 0 and state.owner_age_ms is None
    assert lc.stamp_owner(server, state, pid=777, now_ticks=123456) is True
    block = server.block(MINE)
    assert (block.owner_pid, block.owner_heartbeat) == (777, 123456)
    assert server.read(address + lc._OFF["owner_pid"], 8) == struct.pack("<II", 777, 123456)
    again = lc.read_state(server, MINE)
    assert again.owner_pid == 777 and again.owner_age_ms is not None


def test_an_older_block_is_never_written_past_its_end(server):
    address = server.add_helper(MINE, MINE_BASE, version=3)
    server.point_calls_at(wraps(MINE_BASE))
    state = lc.read_state(server, MINE)
    assert state.on and not state.has_v4
    before = bytes(server.regions[address])
    assert lc.stamp_owner(server, state, pid=777, now_ticks=5) is False
    assert bytes(server.regions[address]) == before and len(before) == lc._V3_SIZE


def test_only_the_helper_that_is_on_is_stamped(server):
    server.add_helper(MINE, MINE_BASE)
    assert lc.stamp_owner(server, lc.read_state(server, MINE), pid=777) is False     # off
    server.add_helper(OTHER, OTHER_BASE)
    server.point_calls_at(wraps(OTHER_BASE))
    state = lc.read_state(server, MINE)                                             # theirs
    assert state.kind == lc.OLD and lc.stamp_owner(server, state, pid=777) is False
    assert server.block(OTHER).owner_pid == 0
    assert lc.stamp_owner(server, None) is False


def _theirs(**kw):
    kw.setdefault("has_v4", True)
    return lc.State(lc.OLD, base=OTHER_BASE, shared=OTHER_BASE + BLOCK_OFFSET, ready=True, **kw)


def test_a_version_4_helper_is_managed_while_its_wolfrat_is_alive_and_fresh():
    running = {4242}.__contains__
    fresh = _theirs(owner_pid=4242, owner_age_ms=4000)
    assert lc.managed_elsewhere(fresh, own_pid=1000, pid_running=running) is True
    assert lc.managed_elsewhere(fresh, own_pid=4242, pid_running=running) is False   # that is us
    stale = _theirs(owner_pid=4242, owner_age_ms=lc.OWNER_FRESH_MS)
    assert lc.managed_elsewhere(stale, own_pid=1000, pid_running=running) is False   # 30 s silent
    assert lc.managed_elsewhere(fresh, own_pid=1000, pid_running=lambda pid: False) is False
    nobody = _theirs(owner_pid=0, owner_age_ms=None)
    assert lc.managed_elsewhere(nobody, own_pid=1000, pid_running=running) is False
    never = _theirs(owner_pid=4242, owner_age_ms=None)
    assert lc.managed_elsewhere(never, own_pid=1000, pid_running=running) is False


def test_an_older_helper_is_managed_while_any_other_wolfrat_runs():
    old = _theirs(has_v4=False)
    assert lc.managed_elsewhere(old, other_wolfrat=lambda: True) is True
    assert lc.managed_elsewhere(old, other_wolfrat=lambda: False) is False


def test_only_another_builds_helper_can_be_managed_elsewhere():
    for kind in (lc.ON, lc.STOCK, lc.FOREIGN, lc.UNKNOWN):
        state = lc.State(kind, has_v4=True, owner_pid=4242, owner_age_ms=10)
        assert lc.managed_elsewhere(state, own_pid=1, pid_running=lambda pid: True) is False
    assert lc.managed_elsewhere(None) is False


def test_read_state_gives_the_owner_of_the_helper_that_is_on(server, monkeypatch):
    monkeypatch.setattr(lc, "_tick_count", lambda: 50000)
    server.add_helper(MINE, MINE_BASE)
    server.add_helper(OTHER, OTHER_BASE, owner_pid=4242, owner_heartbeat=46000)
    server.point_calls_at(wraps(OTHER_BASE))
    state = lc.read_state(server, MINE)
    assert (state.kind, state.has_v4, state.owner_pid, state.owner_age_ms) == (
        lc.OLD, True, 4242, 4000)
    assert lc.managed_elsewhere(state, own_pid=1000, pid_running=lambda pid: pid == 4242)


# --------------------------------------------------------------- P2 (W4)
@pytest.mark.parametrize("failing", [0, 1, 2])
def test_a_failed_write_while_switching_on_leaves_all_three_calls_as_they_were(server, failing):
    server.add_helper(MINE, MINE_BASE)
    server.fail_code_write = failing
    with pytest.raises(lc.LagCompError, match="The game's hit code is as it was"):
        lc.turn_on(PID, server, "unused", lc.Settings())
    assert server.targets() == STOCK_TARGETS                    # none of the three changed
    assert lc.read_state(server, MINE).kind == lc.STOCK
    server.fail_code_write = None
    assert lc.turn_on(PID, server, "unused", lc.Settings()).on  # the retry works
    assert server.targets() == wraps(MINE_BASE)


@pytest.mark.parametrize("failing", [1, 2])
def test_a_failed_write_while_switching_off_leaves_it_on(server, failing):
    server.add_helper(MINE, MINE_BASE, enabled=1)
    server.point_calls_at(wraps(MINE_BASE))
    server.fail_code_write = failing
    with pytest.raises(lc.LagCompError, match="as it was"):
        lc.turn_off(PID, server)
    assert server.targets() == wraps(MINE_BASE)
    assert lc.read_state(server, MINE).on
    assert server.block(MINE).enabled == 1                      # still running, not half off


def test_a_takeover_that_fails_leaves_the_other_helper_fully_on(server):
    server.add_helper(MINE, MINE_BASE)
    server.add_helper(OTHER, OTHER_BASE, enabled=1)
    server.point_calls_at(wraps(OTHER_BASE))
    server.fail_code_write = 2
    with pytest.raises(lc.LagCompError, match="as it was"):
        lc.turn_on(PID, server, "unused", lc.Settings())
    assert server.targets() == wraps(OTHER_BASE)


def test_the_bytes_there_are_read_first_and_given_to_write_code(server):
    server.add_helper(MINE, MINE_BASE)
    seen = {}

    def write_code(writes, restore=None):
        seen["writes"], seen["restore"] = list(writes), list(restore)
        for address, data in writes:
            server.write(address, data)
    server.write_code = write_code
    lc.turn_on(PID, server, "unused", lc.Settings())
    assert [a for a, _ in seen["writes"]] == [site for site, _ in SITES]
    assert seen["restore"] == [(site, lc._call_bytes(site, func)) for site, func in SITES]


def _half(server, base, wrapped):
    """Some of the three calls at the helper's wrappers, the rest the game's own."""
    server.point_calls_at([w if i in wrapped else f
                           for i, (w, f) in enumerate(zip(wraps(base), STOCK_TARGETS))])


def test_when_the_undo_fails_too_it_says_half_done_and_reads_as_half_switched(server):
    server.add_helper(MINE, MINE_BASE)
    server.fail_code_write, server.restore_works = 1, False
    with pytest.raises(lc.LagCompError, match="half done; WolfRAT finishes it on the next try"):
        lc.turn_on(PID, server, "unused", lc.Settings())
    state = lc.read_state(server, MINE)
    assert state.kind == lc.PARTIAL and not state.on and state.shared


@pytest.mark.parametrize("wrapped", [{0}, {1}, {2}, {0, 1}, {1, 2}, {0, 2}])
def test_turn_on_finishes_a_half_done_switch(server, wrapped):
    """Before: a mix read as someone else's code and both on and off refused,
    leaving the person test wrapped without the bone test until a restart."""
    server.add_helper(MINE, MINE_BASE)
    _half(server, MINE_BASE, wrapped)
    assert lc.read_state(server, MINE).kind == lc.PARTIAL
    assert lc.turn_on(PID, server, "unused", lc.Settings()).on
    assert server.targets() == wraps(MINE_BASE)
    # Only the calls that were not there yet are written.
    assert len(server.code_writes[-1]) == 3 - len(wrapped)


@pytest.mark.parametrize("wrapped", [{0}, {1, 2}])
def test_turn_off_undoes_a_half_done_switch(server, wrapped):
    server.add_helper(MINE, MINE_BASE, enabled=1)
    _half(server, MINE_BASE, wrapped)
    assert lc.turn_off(PID, server).kind == lc.STOCK
    assert server.targets() == STOCK_TARGETS and server.block(MINE).enabled == 0


def test_a_half_done_switch_of_another_builds_helper_is_repairable_too(server):
    server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1)
    _half(server, OTHER_BASE, {1})
    state = lc.read_state(server, MINE)
    assert state.kind == lc.PARTIAL and state.base == OTHER_BASE
    assert lc.turn_off(PID, server).kind == lc.STOCK and server.targets() == STOCK_TARGETS
    _half(server, OTHER_BASE, {0, 2})
    assert lc.turn_on(PID, server, "unused", lc.Settings()).on
    assert server.targets() == wraps(MINE_BASE)


def test_a_mix_with_someone_elses_code_is_still_left_alone(server):
    server.add_helper(MINE, MINE_BASE)
    mixed = [wraps(MINE_BASE)[0], 0x67200100, STOCK_TARGETS[2]]
    server.point_calls_at(mixed)
    assert lc.read_state(server, MINE).kind == lc.FOREIGN
    with pytest.raises(lc.LagCompError, match="left it alone"):
        lc.turn_on(PID, server, "unused", lc.Settings())
    with pytest.raises(lc.LagCompError, match="left it alone"):
        lc.turn_off(PID, server)
    assert server.targets() == mixed
    # Two different helpers' wrappers mixed is not ours to guess at either.
    server.add_helper(OTHER, OTHER_BASE)
    server.point_calls_at([wraps(MINE_BASE)[0], wraps(OTHER_BASE)[1], STOCK_TARGETS[2]])
    assert lc.read_state(server, MINE).kind == lc.FOREIGN


# ---- the real write_code (weather.ProcessMemory) against a fake Windows
class _FakeWin:
    """kernel32 + ntdll as write_code uses them, over a dict of bytes."""

    def __init__(self, memory, fail_at=None, fail_restore=False):
        self.memory, self.fail_at, self.fail_restore = memory, fail_at, fail_restore
        self.calls, self.writes, self.paused = [], 0, False
        self.log = []                    # (address, bytes, paused at the time)

    # kernel32
    class _Fn:
        def __init__(self, fn):
            self.fn, self.argtypes, self.restype = fn, None, None

        def __call__(self, *args):
            return self.fn(*args)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        fn = self._Fn(getattr(self, "_" + name))
        setattr(self, name, fn)
        return fn

    def _OpenProcess(self, access, inherit, pid):
        return 99

    def _CloseHandle(self, handle):
        self.calls.append("close")
        return 1

    def _VirtualProtectEx(self, handle, address, size, protect, old):
        return 1

    def _FlushInstructionCache(self, handle, address, size):
        return 1

    def _WriteProcessMemory(self, handle, address, data, size, done):
        index = self.writes
        self.writes += 1
        failing = index == self.fail_at or (self.fail_restore and self.fail_at is not None
                                             and index > self.fail_at)
        if failing:
            return 0
        self.memory[address.value] = bytes(data[:size])
        self.log.append((address.value, bytes(data[:size]), self.paused))
        done._obj.value = size
        return 1

    # ntdll
    def _NtSuspendProcess(self, handle):
        self.paused = True
        self.calls.append("pause")
        return 0

    def _NtResumeProcess(self, handle):
        self.paused = False
        self.calls.append("resume")
        return 0


def _process_memory(monkeypatch, win):
    from wolfrat import weather
    monkeypatch.setattr(weather.ctypes, "WinDLL", lambda name, **kw: win)
    mem = weather.ProcessMemory.__new__(weather.ProcessMemory)
    mem.pid, mem._k, mem._handle = PID, win, None
    return weather, mem


WRITES = [(0x1000, b"AAAAA"), (0x2000, b"BBBBB"), (0x3000, b"CCCCC")]
BEFORE = [(0x1000, b"aaaaa"), (0x2000, b"bbbbb"), (0x3000, b"ccccc")]


def test_write_code_without_restore_is_exactly_as_before(monkeypatch):
    """Live patches, rank tone and wildlife call it this way: a failure is
    raised with the earlier writes left in, as it always was."""
    win = _FakeWin(dict(BEFORE))
    weather, mem = _process_memory(monkeypatch, win)
    mem.write_code(WRITES)
    assert win.memory == dict(WRITES) and win.calls == ["pause", "resume", "close"]
    assert all(paused for _a, _d, paused in win.log)            # all three in one pause

    win = _FakeWin(dict(BEFORE), fail_at=1)
    weather, mem = _process_memory(monkeypatch, win)
    with pytest.raises(weather.WeatherError, match="Could not write the server's code"):
        mem.write_code(WRITES)
    assert win.memory == {0x1000: b"AAAAA", 0x2000: b"bbbbb", 0x3000: b"ccccc"}
    assert win.calls == ["pause", "resume", "close"] and win.writes == 2


@pytest.mark.parametrize("failing", [0, 1, 2])
def test_write_code_with_restore_puts_the_earlier_writes_back_before_resuming(
        monkeypatch, failing):
    win = _FakeWin(dict(BEFORE), fail_at=failing)
    weather, mem = _process_memory(monkeypatch, win)
    with pytest.raises(weather.WeatherError, match="Could not write the server's code"):
        mem.write_code(WRITES, restore=BEFORE)
    assert win.memory == dict(BEFORE)                           # as it was
    assert win.calls == ["pause", "resume", "close"]            # one pause, always resumed
    assert all(paused for _a, _d, paused in win.log)            # the undo was inside it


def test_write_code_resumes_the_server_even_when_the_undo_fails(monkeypatch):
    win = _FakeWin(dict(BEFORE), fail_at=1, fail_restore=True)
    weather, mem = _process_memory(monkeypatch, win)
    with pytest.raises(weather.WeatherError):
        mem.write_code(WRITES, restore=BEFORE)
    assert win.calls == ["pause", "resume", "close"]


# --------------------------------------------------------------- P3 (W5)
class _LoadKernel:
    """kernel32 as ServerProcess.load_library uses it."""
    PATH_BUFFER, THREAD = 0x7000000, 55

    def __init__(self, wait_result, exit_code=MINE_BASE):
        self.wait_result, self.exit_code = wait_result, exit_code
        self.freed, self.closed, self.waits = [], [], []

    def VirtualAllocEx(self, handle, address, size, kind, protect):
        return self.PATH_BUFFER

    def CreateRemoteThread(self, *args):
        return self.THREAD

    def WaitForSingleObject(self, thread, milliseconds):
        self.waits.append(milliseconds)
        return self.wait_result

    def GetExitCodeThread(self, thread, code):
        code._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1

    def VirtualFreeEx(self, handle, address, size, kind):
        self.freed.append(address.value)
        return 1


def _loader(kernel):
    proc = lc.ServerProcess.__new__(lc.ServerProcess)
    proc.pid, proc._h, proc._k = PID, 1, kernel
    proc.modules = lambda: {"kernel32.dll": 0x75000000}
    proc._remote_export = lambda base, name: 0x75001000
    proc.written = []
    proc.write = lambda address, data: proc.written.append((address, data))
    return proc


def test_a_helper_that_loads_in_time_is_loaded_and_the_path_is_freed(tmp_path):
    kernel = _LoadKernel(wait_result=0)
    proc = _loader(kernel)
    assert proc.load_library(str(tmp_path / MINE)) == MINE_BASE
    assert kernel.waits == [lc.LOAD_WAIT_MS] and kernel.closed == [kernel.THREAD]
    assert kernel.freed == [kernel.PATH_BUFFER]
    (address, data), = proc.written
    assert address == kernel.PATH_BUFFER and data.endswith(MINE.encode() + b"\0")


@pytest.mark.parametrize("wait_result", [0x102, -1])           # timed out / the wait failed
def test_a_helper_still_loading_after_the_wait_is_not_called_loaded(tmp_path, wait_result):
    """Before: the result of the wait was ignored.  After 15 s the thread's exit
    code read 259 (still running), which counted as a module address, and the
    path was freed under a server that might not have read it yet."""
    kernel = _LoadKernel(wait_result=wait_result, exit_code=259)
    proc = _loader(kernel)
    with pytest.raises(lc.LagCompError, match="took too long to load"):
        proc.load_library(str(tmp_path / MINE))
    assert kernel.freed == []                                   # the path stays for the server
    assert kernel.closed == [kernel.THREAD]                     # our handle on the thread goes


def test_a_helper_the_server_refuses_still_says_so_and_frees_the_path(tmp_path):
    kernel = _LoadKernel(wait_result=0, exit_code=0)            # LoadLibrary returned 0
    with pytest.raises(lc.LagCompError, match="would not load"):
        _loader(kernel).load_library(str(tmp_path / MINE))
    assert kernel.freed == [kernel.PATH_BUFFER]


def test_a_slow_load_is_an_error_the_page_retries(server):
    """turn_on passes the timeout on as LagCompError: the page keeps the box
    ticked and tries again (by then the helper has usually finished loading)."""
    def slow(path):
        raise lc.LagCompError("The server took too long to load WolfRAT's lag compensation "
                              "helper (is it frozen or very busy?).")
    real_load = server.load_library
    server.load_library = slow
    with pytest.raises(lc.LagCompError, match="took too long"):
        lc.turn_on(PID, server, "unused", lc.Settings())
    assert server.targets() == STOCK_TARGETS
    real_load("x/" + MINE)                                      # it finished loading meanwhile
    server.load_library = slow                                  # must not be asked again
    assert lc.turn_on(PID, server, "unused", lc.Settings()).on


# ------------------------------------------- follow-ups to B3 (W2)
def test_new_settings_never_switch_another_builds_running_helper_off(server):
    """read_state now hands back the block of the helper that is on.  Writing
    settings into it must leave it enabled (it used to get this build's idle
    block here, where 'enabled' did not matter)."""
    server.add_helper(OTHER, OTHER_BASE, version=3, enabled=1, extra_ms=55)
    server.point_calls_at(wraps(OTHER_BASE))
    after = lc.apply_settings(PID, lc.Settings(False, 60, 400, 80), MINE)
    assert after.kind == lc.OLD and server.block(OTHER).extra_ms == 60
    assert server.block(OTHER).enabled == 1
    assert server.targets() == wraps(OTHER_BASE)


def test_mycomp_answers_when_it_is_on_from_another_wolfrat_version(server):
    block = helper_block(OTHER_BASE, version=3, enabled=1)
    row = block.players[4]
    row.entity, row.ping, row.rewind_ms, row.tests = 0x1234, 142, 212, 5000
    row.hits, row.plain_hits, row.name = 48, 39, b"BadgerLove"
    server.add_helper(OTHER, OTHER_BASE, block)
    assert lc.mycomp_line(lc.read_state(server, MINE), "badgerlove") == lc.MYCOMP_OFF   # not on yet
    server.point_calls_at(wraps(OTHER_BASE))
    state = lc.read_state(server, MINE)
    assert state.kind == lc.OLD
    assert lc.mycomp_line(state, "badgerlove") == "BadgerLove P142 R212 Hits 48 +9 comp (+23%)"


# ------------------- an older helper from this WolfRAT's own folder (W3)
def test_an_older_helper_from_this_wolfrats_own_folder_is_not_someone_elses(tmp_path):
    """The normal update on a PC that runs two servers: this WolfRAT's older
    build loaded the helper (from this folder) and has gone.  The other WolfRAT
    open on the PC looks after another server and has nothing to do with it."""
    mine = tmp_path / "Live WolfRAT"
    mine.mkdir()
    old = _theirs(has_v4=False, helper_dir=str(mine).upper() + "\\")
    assert lc.managed_elsewhere(old, other_wolfrat=lambda: True, own_dir=str(mine)) is False
    # Loaded from another WolfRAT's folder (a test build next to the live one).
    other = _theirs(has_v4=False, helper_dir=str(tmp_path / "Test WolfRAT"))
    assert lc.managed_elsewhere(other, other_wolfrat=lambda: True, own_dir=str(mine)) is True
    assert lc.managed_elsewhere(other, other_wolfrat=lambda: False, own_dir=str(mine)) is False
    # Windows would not say where it was loaded from: any other WolfRAT counts.
    unknown = _theirs(has_v4=False, helper_dir="")
    assert lc.managed_elsewhere(unknown, other_wolfrat=lambda: True, own_dir=str(mine)) is True
    assert lc.managed_elsewhere(old, other_wolfrat=lambda: True) is True     # folder not given
    # A version 4 helper goes by its owner, wherever it was loaded from.
    v4 = _theirs(owner_pid=4242, owner_age_ms=100, helper_dir=str(mine))
    assert lc.managed_elsewhere(v4, own_pid=1, pid_running=lambda pid: True,
                                own_dir=str(mine)) is True


def test_read_state_says_where_another_builds_helper_was_loaded_from(server):
    server.add_helper(OTHER, OTHER_BASE, version=3)
    server.point_calls_at(wraps(OTHER_BASE))
    assert lc.read_state(server, MINE).helper_dir == ""            # this server cannot say
    server.module_dir = lambda base: r"D:\WolfRAT Live" if base == OTHER_BASE else "wrong"
    assert lc.read_state(server, MINE).helper_dir == r"D:\WolfRAT Live"
    server.add_helper(MINE, MINE_BASE)
    server.point_calls_at(wraps(MINE_BASE))
    assert lc.read_state(server, MINE).helper_dir == ""            # only for another build's


def test_module_dir_asks_windows_for_the_helpers_file():
    class Kernel:
        def K32GetModuleFileNameExW(self, handle, base, buffer, size):
            if base.value != OTHER_BASE:
                return 0
            buffer.value = "D:\\WolfRAT Live\\" + OTHER
            return len(buffer.value)

    proc = lc.ServerProcess.__new__(lc.ServerProcess)
    proc._h, proc._k = 1, Kernel()
    assert proc.module_dir(OTHER_BASE) == r"D:\WolfRAT Live"
    assert proc.module_dir(MINE_BASE) == ""
