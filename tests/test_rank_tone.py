"""The rank-up sound for the promoted player only (Dale, 2026-11-16).

The helper DLL sends the line to one connection with the hidden sound code;
these tests cover WolfRAT's side: loading it, pointing the game's frame call at
it (only from the game's own call or an older copy of ours), the queue it
fills, and the career panel falling back to normal chat."""

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from wolfrat import rank_tone as rt  # noqa: E402

CALL, FUNC = 0x5266B6, 0x51D7E0
BLOCK, WRAP = 0x10003000, 0x10001000
OLD_BLOCK, OLD_WRAP = 0x20003000, 0x20001000
SLOTS = 0x30000000


class FakeMem:
    """Sparse server memory: a few regions, like the real process."""

    def __init__(self):
        self.pid = 4321
        self.regions = {}
        self.code_writes = []

    def map(self, address, size):
        self.regions[address] = bytearray(size)

    def _find(self, address, size):
        for base, buf in self.regions.items():
            if base <= address and address + size <= base + len(buf):
                return buf, address - base
        raise rt.RankToneError("unmapped %x" % address)

    def read(self, address, size):
        buf, off = self._find(address, size)
        return bytes(buf[off:off + size])

    def write(self, address, data):
        buf, off = self._find(address, len(data))
        buf[off:off + len(data)] = data

    def write_code(self, writes):
        for address, data in writes:
            self.code_writes.append((address, bytes(data)))
            self.write(address, data)


def block_bytes(wrap, status=1, error=b"", next_=FUNC):
    b = rt._Shared()
    b.magic, b.version, b.size, b.status = rt.MAGIC, 1, rt.SHARED_SIZE, status
    b.error = error
    b.call, b.func, b.wrap, b.next = CALL, FUNC, wrap, next_
    return bytes(b)


def next_of(mem, block=BLOCK):
    return struct.unpack("<I", mem.read(block + rt.NEXT_OFFSET, 4))[0]


class FakeProc:
    """lag_comp.ServerProcess stand-in: the module list and LoadLibrary."""

    def __init__(self, mem, helpers, loadable_status=1):
        self.mem, self.helpers, self.loads = mem, helpers, []
        self.loadable_status = loadable_status

    def __call__(self, pid):
        assert pid == self.mem.pid
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def modules(self):
        return {name: base for name, (base, _block) in self.helpers.items()}

    def _remote_export(self, base, name):
        assert name == rt.EXPORT_NAME
        for b, block in self.helpers.values():
            if b == base:
                return block
        return None

    def read(self, address, size):
        return self.mem.read(address, size)

    def load_library(self, path):
        self.loads.append(path)
        name = os.path.basename(path).lower()
        self.mem.map(BLOCK, rt.SHARED_SIZE)
        self.mem.write(BLOCK, block_bytes(WRAP, self.loadable_status,
                                          b"" if self.loadable_status == 1 else b"exe does not match"))
        self.helpers[name] = (0x10000000, BLOCK)
        return 0x10000000


def server(call_target=FUNC):
    mem = FakeMem()
    mem.map(CALL - 8, 16)
    mem.write(CALL, rt.call_bytes(CALL, call_target))
    # the player slot table: capacity 3, slot 0 = host, 1 = BadgerLove, 2 = Bob
    mem.map(rt.CAPACITY_VA, 8)
    mem.write(rt.CAPACITY_VA, struct.pack("<iI", 3, SLOTS))
    mem.map(SLOTS, rt.SLOT_STRIDE * 3)
    for index, name in ((0, b"Host"), (1, b"FMJ-BadgerLove"), (2, b"Bob")):
        slot = SLOTS + index * rt.SLOT_STRIDE
        mem.write(slot + rt.ACTIVE_OFFSET, b"\x01")
        mem.write(slot + rt.NAME_OFFSET, name.ljust(32, b"\0"))
    return mem


@pytest.fixture
def helper_dll(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "_bundled_bytes", b"MZ fake helper")
    return tmp_path


def tone(mem, proc, data_dir, **kw):
    t = rt.RankTone(str(data_dir), process_factory=proc, **kw)
    t.server_tick(mem, True)
    return t


def entry(mem, i=0):
    return rt._Entry.from_buffer_copy(mem.read(BLOCK + rt.QUEUE_OFFSET + i * rt.ENTRY_SIZE,
                                               rt.ENTRY_SIZE))


def test_the_helper_goes_in_and_the_games_own_call_is_pointed_at_it(helper_dll):
    mem = server()
    proc = FakeProc(mem, {})
    t = tone(mem, proc, helper_dll)
    assert len(proc.loads) == 1
    assert mem.code_writes == [(CALL, rt.call_bytes(CALL, WRAP))]
    assert rt.call_target(mem, CALL) == WRAP
    assert next_of(mem) == FUNC
    assert t.ready()


def test_a_ready_server_is_left_alone_on_the_next_tick(helper_dll):
    mem = server()
    proc = FakeProc(mem, {})
    t = tone(mem, proc, helper_dll)
    t.server_tick(mem, True)
    assert len(proc.loads) == 1 and len(mem.code_writes) == 1


def test_a_call_taken_by_something_else_is_left_alone(helper_dll):
    mem = server(call_target=0x7FFF0000)
    logs = []
    t = tone(mem, FakeProc(mem, {}), helper_dll, log=logs.append)
    assert not t.ready()
    assert mem.code_writes == []
    assert rt.call_target(mem, CALL) == 0x7FFF0000
    assert "left alone" in t.problem and logs


def test_a_test_rig_can_go_in_front_of_another_hook_and_jump_on_to_it(helper_dll):
    mem = server(call_target=0x7FFF0000)
    t = tone(mem, FakeProc(mem, {}), helper_dll, chain_foreign=True)
    assert t.ready()
    assert rt.call_target(mem, CALL) == WRAP and next_of(mem) == 0x7FFF0000


def test_an_older_copy_of_the_helper_is_taken_over(helper_dll):
    mem = server(call_target=OLD_WRAP)
    mem.map(OLD_BLOCK, rt.SHARED_SIZE)
    mem.write(OLD_BLOCK, block_bytes(OLD_WRAP, next_=0x7FFF0000))
    proc = FakeProc(mem, {"wolfrat_ranktone_00000000.dll": (0x20000000, OLD_BLOCK)})
    t = tone(mem, proc, helper_dll)
    assert rt.call_target(mem, CALL) == WRAP
    assert next_of(mem) == 0x7FFF0000          # the older copy's chain is kept
    assert t.ready()


def test_an_exe_the_helper_does_not_know_is_refused(helper_dll):
    mem = server()
    t = tone(mem, FakeProc(mem, {}, loadable_status=-1), helper_dll)
    assert not t.ready()
    assert mem.code_writes == []
    assert "exe does not match" in t.problem


def test_manual_mode_and_switched_off_never_touch_the_server(helper_dll):
    for kw in ({"quiet": lambda: True}, {"enabled": lambda: False}):
        mem = server()
        proc = FakeProc(mem, {})
        t = tone(mem, proc, helper_dll, **kw)
        assert proc.loads == [] and mem.code_writes == [] and not t.ready()


def test_a_rank_up_is_queued_for_the_player_with_the_sound(helper_dll):
    mem = server()
    t = tone(mem, FakeProc(mem, {}), helper_dll)
    assert t.queue_line("fmj-badgerlove", "FMJ-BadgerLove is now Rank 7 Corporal!")
    e = entry(mem)
    assert e.state == rt.QUEUED
    assert e.slot == 1 and e.tone == rt.TONE_STAT and e.everyone == 1
    assert e.name == b"FMJ-BadgerLove"
    assert e.text == b"FMJ-BadgerLove is now Rank 7 Corporal!"


def test_the_queue_hands_over_last_and_reuses_finished_entries(helper_dll):
    mem = server()
    t = tone(mem, FakeProc(mem, {}), helper_dll)
    assert t.queue_line("Bob", "one")
    assert t.queue_line("Bob", "two")
    assert entry(mem, 0).text == b"one" and entry(mem, 1).text == b"two"
    # the helper sent the first one: its entry is free again
    mem.write(BLOCK + rt.QUEUE_OFFSET, struct.pack("<i", rt.SENT))
    assert t.queue_line("Bob", "three")
    assert entry(mem, 0).text == b"three" and entry(mem, 0).state == rt.QUEUED


def test_no_send_means_normal_chat(helper_dll):
    mem = server()
    t = tone(mem, FakeProc(mem, {}), helper_dll)
    assert not t.queue_line("Nobody", "x")                        # not in the server
    for i in range(rt.QUEUE):
        mem.write(BLOCK + rt.QUEUE_OFFSET + i * rt.ENTRY_SIZE, struct.pack("<i", rt.QUEUED))
    assert not t.queue_line("Bob", "x")                           # queue full
    other = server(call_target=0x7FFF0000)
    assert not tone(other, FakeProc(other, {}), helper_dll).queue_line("Bob", "x")   # not ready
    assert not rt.RankTone(str(helper_dll)).queue_line("Bob", "x")                   # no server


def test_a_stray_code_character_in_the_text_is_dropped_and_long_text_cut():
    assert rt.tone_line("a\x07b") == b"ab"
    assert len(rt.tone_line("x" * 400)) == rt.TEXT - 1


def test_the_block_matches_the_helper():
    # ranktone.c: WrtEntry 184 bytes, WrtShared 112 + 16 entries, the thunk jumps through +92
    assert rt.ENTRY_SIZE == 184 and rt.QUEUE_OFFSET == 112 and rt.SHARED_SIZE == 3056
    assert rt.NEXT_OFFSET == 92


# ---- the career panel ---------------------------------------------------------
def _panel(qtbot, tmp_path, tone_result):
    from wolfrat.career import Rank
    from wolfrat.career_panel import CareerPanel
    said, toned = [], []

    def _tone(name, text):
        toned.append((name, text))
        return tone_result

    p = CareerPanel(str(tmp_path), announce=said.append, tone=_tone)
    qtbot.addWidget(p)
    return p, said, toned, Rank(20, "Sergeant", 100000)


def test_a_rank_up_with_the_sound_is_not_said_twice(qtbot, tmp_path):
    p, said, toned, rank = _panel(qtbot, tmp_path, True)
    p._ranked_up("Bob", rank)
    assert len(toned) == 1 and toned[0][0] == "Bob" and said == []


def test_a_rank_up_without_the_sound_goes_to_chat(qtbot, tmp_path):
    p, said, toned, rank = _panel(qtbot, tmp_path, False)
    p._ranked_up("Bob", rank)
    assert len(toned) == 1 and said == [toned[0][1]]


def test_the_sound_can_be_switched_off(qtbot, tmp_path):
    p, said, toned, rank = _panel(qtbot, tmp_path, True)
    p.tone_box.setChecked(False)
    p._ranked_up("Bob", rank)
    assert toned == [] and len(said) == 1
    assert not p.tone_wanted()
