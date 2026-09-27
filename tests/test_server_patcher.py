"""The Patcher (2026-09-27) against a fake stock exe carrying the bytes the
jo-server-patches scripts assert.  Real exes cannot go in the repo; on the FMJ
box the engine was checked byte for byte against the published scripts: the
live server exe rebuilt exactly (68de3776 + Kill List + spectator + long chat
colour = 273bc0ea), a stock+LAA exe with everything on matched the scripts'
chain, and the 1 GB DLL matched the one built by hand (64dfdb18)."""

import os
import struct

import pytest

from wolfrat import server_patcher as P
from wolfrat import server_patches as sp

BASE = 0x400000
SECTIONS = (                       # name, va, virtual size, raw size
    (b".text", 0x1000, 0x394000, 0x394000),
    (b"_text", 0x395000, 0x2AA10, 0x2B000),        # stock: the zero tail is unmapped
    (b".rdata", 0x3C0000, 0x40000, 0x40000),
)
RAW0 = 0x400
TICK = struct.pack("<I", 0x7C0208).hex()          # GetTickCount

STOCK = {
    0x4056E6: "03c003c0",
    0x4C4B12: "b802000000",                       # mov eax, 2 (the speed)
    0x626954: "8b8fb807000083c1",
    0x4A7CE3: "0000000c",
    0x52B85E: "33c0390544075502" "8935081f4e02" "0f95c0",
    0x52B89F: "c1e008", 0x52B8CA: "c1e208",
    0x52B75C: "ff15" + TICK, 0x52B798: "ff15" + TICK, 0x52B8B8: "ff15" + TICK,
    0x52BA7E: "ff15" + TICK, 0x52BA95: "8b1d" + TICK,
    0x52B8B0: "6a01ff15d4007c00", 0x52B8DF: "72c7",
    0x516BBA: "c781240100006c020000", 0x517937: "c780240100006c020000",
    0x517952: "c781240100006c020000", 0x517960: "c782240100006c020000",
    0x519FE7: "c780240100006c020000", 0x51A882: "c780240100006c020000",
    0x54D13B: "893544085502",
    0x726015: "cc" * 17, 0x7472C9: "cc" * 42,
    0x7D477C: b"enable_keyboardtips\0".hex(),
    0x7D3B90: b"enable_keyboardtips  = %i\n\0".hex(),
    0x504C2B: "80e201",
    0x5137C7: "80bed788010000740d833d28194c02000f8488090000",
    0x5047A0: "8b44240855", 0x42B910: "0fb6442404", 0x51416D: "5f33cce806692500",
    0x49A93B: "e820e6ffff", 0x49A971: "e87adaffff", 0x49A9DC: "8d4c240451",
}


def fake_exe(sites=STOCK, laa=False) -> bytes:
    pe = 0x80
    head = bytearray(RAW0)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe + 4, 0x14C, len(SECTIONS), 0, 0, 0, 0xE0,
                     0x0102 | (0x20 if laa else 0))
    struct.pack_into("<I", head, pe + 24 + 28, BASE)
    raw = RAW0
    body = bytearray()
    layout = []
    for i, (name, va, vs, rs) in enumerate(SECTIONS):
        o = pe + 24 + 0xE0 + i * 40
        head[o:o + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", head, o + 8, vs, va, rs, raw)
        struct.pack_into("<I", head, o + 36, 0xE0000020)
        layout.append((BASE + va, rs, len(body)))
        body += bytes(rs)
        raw += rs
    for va, hx in sites.items():
        data = bytes.fromhex(hx)
        for start, rs, at in layout:
            if start <= va < start + rs:
                body[at + va - start:at + va - start + len(data)] = data
    return bytes(head) + bytes(body)


def server(tmp_path, data=None, cfg="lock_framerate = 1\n", dll=None):
    exe = tmp_path / "jointops.exe"
    exe.write_bytes(data or fake_exe())
    if cfg:
        (tmp_path / "game.cfg").write_text(cfg)
    if dll is not None:
        (tmp_path / "binkw32.dll").write_bytes(dll)
    return str(exe)


def hook_dll(arena=0x20000000) -> bytes:
    size = struct.pack("<I", arena)
    body = bytes.fromhex("c785d8feffff") + size + bytes.fromhex("c7442410") + size
    head = bytearray(0x200)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    return bytes(head) + body + bytes(64)


def everything(**kw) -> P.Choice:
    choice = P.Choice(speed=64, memory=1024, laa=True, fps_lock=True, spawn=True,
                      kill_list=True, spectator=True, long_chat=True, chat_colour=True)
    for key, value in kw.items():
        setattr(choice, key, value)
    return choice


def test_stock_state(tmp_path):
    state = P.read_state(server(tmp_path))
    assert state.known and state.admin_fix is False and state.speed == 64 and state.memory == 192
    assert state.spawn == "stock" and state.long_chat == "off" and state.spectator is False
    assert not state.memory_in_dll and not state.laa and state.fps_lock is False


def test_everything_on_a_stock_exe_and_the_patches_page_agrees(tmp_path):
    path = server(tmp_path)
    before = open(path, "rb").read()
    result = P.build(path, everything())
    assert open(path, "rb").read() == before                      # the original is never touched
    assert result.exe_path == str(tmp_path / P.OUT_EXE) and result.dll_path == ""
    assert result.changed[0] == "Admin-port crash fix"
    by = {f.key: f for f in result.report.findings}
    assert by["admin_crash"].level == sp.GOOD and by["kill_list"].level == sp.GOOD
    assert by["memory"].detail == "1 GB." and by["laa"].level == sp.GOOD
    assert by["fps"].detail.endswith("~125 FPS.")                  # checked with the planned cfg line
    assert by["spawn"].detail.startswith("Set in game.cfg")         # the time is the admin's, in game.cfg
    assert by["spectator"].level == sp.GOOD and "with colours" in by["long_chat"].detail
    assert result.report.headline.startswith("64 Hz · 1 GB · ~125 FPS")
    cfg = [step for step in result.steps if "game.cfg in the server folder" in step]
    assert cfg == ["With the server still stopped (game.cfg cannot be changed while it runs), "
                   "open game.cfg in the server folder (next to jointops.exe) and set:  "
                   "lock_framerate = 7  (7 = ~125 FPS; without it one CPU core runs at 100%). "
                   "Add a line if it is not there."]
    assert not any("e_spawn_protection" in step for step in result.steps)
    assert result.steps[0] == "Stop the game server." and result.steps[-1].startswith("Start the server")
    assert f"Rename {P.OUT_EXE} to jointops.exe." in result.steps


def test_building_again_on_the_copy_changes_nothing(tmp_path):
    result = P.build(server(tmp_path), everything())
    data = open(result.exe_path, "rb").read()
    state = P.state_of("x", sp.Exe(data), {"lock_framerate": "7", "e_spawn_protection": "5"}, None)
    again, changed = P.plan_exe(data, state, everything())
    assert again == data and changed == []


def test_the_admin_fix_goes_in_even_with_nothing_ticked(tmp_path):
    none = P.Choice(speed=32, memory=192, laa=False, fps_lock=False, spawn=False,
                    kill_list=False, spectator=False, long_chat=False)
    result = P.build(server(tmp_path, cfg=""), none)
    assert result.changed == ["Admin-port crash fix", "Server speed 32 Hz"]
    assert {f.key: f for f in result.report.findings}["admin_crash"].level == sp.GOOD


def test_nothing_to_do_is_said_not_built(tmp_path):
    first = P.build(server(tmp_path), everything())
    folder = tmp_path / "done"
    folder.mkdir()
    path = server(folder, open(first.exe_path, "rb").read(),
                  cfg="lock_framerate = 7\ne_spawn_protection = 5\n")
    with pytest.raises(P.PatchError, match="nothing to build"):
        P.build(path, everything())
    assert not (folder / P.OUT_EXE).exists()


def hole(data: bytes) -> tuple:
    exe = sp.Exe(data)
    return sp.hole_skip(exe), sp.hole_skip_version(exe)


def test_hole_skip_comes_with_64_and_125_hz(tmp_path):
    """Dale 2026-09-27: 64 Hz does not work without it, 125 Hz needs the
    tuned version.  (On the FMJ box: 32 Hz v2 -> 64 Hz = the shipped 64 Hz v2
    exe, 32 Hz v2 -> 125 Hz + frame lock = the shipped 125 Hz v2 exe.)"""
    for hz, settings in ((64, (8, 64)), (125, (6, 32))):
        folder = tmp_path / str(hz)
        folder.mkdir()
        result = P.build(server(folder), everything(speed=hz))
        assert f"Hole-skip ({hz} Hz version)" in result.changed
        assert hole(open(result.exe_path, "rb").read()) == (True, settings)
        assert {f.key: f for f in result.report.findings}["hole_skip"].level == sp.GOOD
    folder = tmp_path / "32"
    folder.mkdir()
    result = P.build(server(folder), everything(speed=32))
    assert not any("Hole-skip" in c for c in result.changed)
    assert hole(open(result.exe_path, "rb").read()) == (False, None)


def test_changing_speed_switches_the_hole_skip_version(tmp_path):
    first = P.build(server(tmp_path), everything(speed=64))
    folder = tmp_path / "srv"
    folder.mkdir()
    path = server(folder, open(first.exe_path, "rb").read())
    result = P.build(path, everything(speed=125))
    assert "Hole-skip switched to the 125 Hz version" in result.changed
    assert hole(open(result.exe_path, "rb").read()) == (True, (6, 32))
    state = P.read_state(path)
    assert P.hole_plan(state, 32) == "" and P.hole_plan(state, 64) == ""


def test_custom_hole_skip_settings_are_left_alone(tmp_path):
    first = P.build(server(tmp_path), everything(speed=125))
    data = bytearray(open(first.exe_path, "rb").read())
    img = P.Image(bytes(data))
    data[img.offset(sp.HOLE_LAG_VA)] = 0x10                         # an admin's own tuning
    folder = tmp_path / "srv"
    folder.mkdir()
    state = P.read_state(server(folder, bytes(data)))
    assert state.hole_settings == (6, 16) and P.hole_plan(state, 64) == ""


def test_the_32_hz_joexefix_leftover_byte_still_leaves_room(tmp_path):
    img = P.Image(fake_exe())
    img.data[img.offset(0x794E40)] = 0xFF                            # the stray byte the 32 Hz exes carry
    assert P.hole_room(img)
    img.data[img.offset(0x794E00)] = 0x12                            # anything else: no room
    state = P.read_state(server(tmp_path, bytes(img.data)))
    assert not state.hole_room and not P.speed_allowed(state, 64)[0]
    assert P.speed_allowed(state, 32)[0]
    with pytest.raises(P.PatchError, match="hole-skip"):
        P.build(state.exe_path, everything(speed=64))


def test_a_speed_setting_without_a_name_can_still_be_patched(tmp_path):
    """Dale 2026-09-27: the game's own speed is not one of 32/64/125.  Whatever
    the number, the admin just picks 32, 64 or 125."""
    path = server(tmp_path, fake_exe({**STOCK, 0x4C4B12: "b80a000000"}))
    state = P.read_state(path)
    assert state.known and state.speed is None
    assert {f.key: f for f in sp.check(path).findings}["speed"].detail == "Unknown."
    result = P.build(path, everything(speed=125))
    assert "Server speed 125 Hz" in result.changed and result.report.headline.startswith("125 Hz")
    odd = server(tmp_path, fake_exe({**STOCK, 0x4C4B12: "b9020000"}))  # not the instruction
    assert not P.read_state(odd).known


def test_125_hz_always_gets_the_frame_rate_lock(tmp_path):
    """Dale 2026-09-27: 125 Hz without the lock runs the wrong frame rate."""
    result = P.build(server(tmp_path), everything(speed=125, fps_lock=False))
    assert "Frame rate lock" in result.changed
    cfg_steps = [step for step in result.steps if "lock_framerate = 7" in step]
    assert len(cfg_steps) == 1
    order = [result.steps.index(cfg_steps[0]), len(result.steps) - 1]
    assert result.steps[0] == "Stop the game server." and order[0] < order[1]   # set before the start
    folder = tmp_path / "64"
    folder.mkdir()
    result = P.build(server(folder), everything(speed=64, fps_lock=False))
    assert "Frame rate lock" not in result.changed
    assert not any("lock_framerate" in step for step in result.steps)


def test_1gb_brings_large_address_aware(tmp_path):
    result = P.build(server(tmp_path), everything(laa=False, memory=1024))
    assert "Large Address Aware" in result.changed
    result = P.build(server(tmp_path), everything(laa=False, memory=512))
    assert "Large Address Aware" not in result.changed


def test_memory_in_the_hook_dll_is_changed_there_and_the_exe_keeps_its_192(tmp_path):
    path = server(tmp_path, dll=hook_dll(0x20000000))
    state = P.read_state(path)
    assert state.memory_in_dll and state.memory == 512
    result = P.build(path, everything(memory=1024))
    assert result.dll_path == str(tmp_path / P.OUT_DLL)
    new_dll = open(result.dll_path, "rb").read()
    assert P.dll_memory(new_dll) == 0x40000000
    assert open(tmp_path / "binkw32.dll", "rb").read() == hook_dll(0x20000000)
    exe = sp.Exe(open(result.exe_path, "rb").read())
    assert exe.read(sp.ARENA_VA, 4) == bytes.fromhex("0000000c")   # a patched exe stops the DLL loading
    assert f"Rename {P.OUT_DLL} to binkw32.dll." in result.steps
    assert {f.key: f for f in result.report.findings}["memory"].detail == "1 GB."


def test_only_the_dll_changes_when_the_exe_already_has_everything(tmp_path):
    first = P.build(server(tmp_path), everything(memory=192))
    folder = tmp_path / "srv"
    folder.mkdir()
    path = server(folder, open(first.exe_path, "rb").read(),
                  cfg="lock_framerate = 7\ne_spawn_protection = 5\n", dll=hook_dll(0x20000000))
    result = P.build(path, everything(memory=1024))
    assert result.exe_path == "" and result.dll_path and result.changed == ["Mission memory 1 GB"]
    assert not any("jointops" in step for step in result.steps)


def test_a_dll_without_the_memory_setting_is_refused():
    with pytest.raises(P.PatchError):
        P.patch_dll_memory(hook_dll() + hook_dll()[0x200:], 1024)   # two matches: not ours
    with pytest.raises(P.PatchError):
        P.patch_dll_memory(b"MZ" + bytes(0x200), 1024)


def test_long_chat_colours_switch_without_rebuilding_it(tmp_path):
    plain = P.build(server(tmp_path), everything(chat_colour=False))
    folder = tmp_path / "srv"
    folder.mkdir()
    path = server(folder, open(plain.exe_path, "rb").read())
    assert P.read_state(path).long_chat == "plain"
    result = P.build(path, everything(chat_colour=True))
    assert "Long chat colours on" in result.changed
    assert P.read_state(result.exe_path).long_chat == "colour"


def test_a_fixed_spawn_time_cannot_become_the_cfg_one(tmp_path):
    fixed = {**STOCK, 0x516BBA: "c78124010000" + struct.pack("<i", 312).hex()}
    state = P.read_state(server(tmp_path, fake_exe(fixed)))
    assert state.spawn == "fixed" and P.default_choice(state).spawn is False
    result = P.build(state.exe_path, everything(spawn=True))        # ticked anyway: left alone
    assert "Spawn protection from game.cfg" not in result.changed
    assert not any("e_spawn_protection" in step for step in result.steps)


def test_an_exe_it_does_not_know_is_refused_untouched(tmp_path):
    odd = {**STOCK, 0x52B89F: "c1e009"}                            # one site differs
    path = server(tmp_path, fake_exe(odd))
    with pytest.raises(P.PatchError, match="Frame rate lock"):
        P.build(path, everything())
    assert not (tmp_path / P.OUT_EXE).exists()
    with pytest.raises(P.PatchError, match="does not recognise"):
        P.build(server(tmp_path, fake_exe({})), everything())
    (tmp_path / "junk.exe").write_bytes(b"not an exe at all" * 10)
    with pytest.raises(P.PatchError):
        P.read_state(str(tmp_path / "junk.exe"))


START_UP = {0x4056E6: bytes.fromhex("c1e00690"),                 # what onHook 0.6.0 does in memory
            0x516BBA: bytes.fromhex("c78124010000") + struct.pack("<i", 313)}


def running(address, size):
    return START_UP.get(address)


def test_fixes_added_at_start_up_are_seen_and_nothing_clashes(tmp_path):
    """The exe still gets the crash fix (it is there whatever starts the
    server; onHook accepts an exe that has it), but spawn protection already
    set at start-up is not taken over unless the admin ticks it."""
    path = server(tmp_path)
    state = P.read_state(path, running)
    assert state.admin_fix is False and state.admin_fix_live and state.spawn_live_ticks == 313
    choice = P.default_choice(state)
    assert choice.spawn is False
    result = P.build(path, choice, running)
    assert result.changed[0] == "Admin-port crash fix"
    assert "Spawn protection from game.cfg" not in result.changed
    assert not any("e_spawn_protection" in step for step in result.steps)


def test_old_unlimited_fps_hack_is_undone_by_the_lock(tmp_path):
    hacked = {**STOCK, 0x52B8B0: "90" * 8, 0x52B8DF: "9090"}
    result = P.build(server(tmp_path, fake_exe(hacked)), everything())
    exe = sp.Exe(open(result.exe_path, "rb").read())
    assert exe.is_(0x52B8B0, "6a01ff15d4007c00") and exe.is_(0x52B8DF, "72c7")


def test_dll_checksum_matches_pefile_style():
    img = P.Image(hook_dll())
    img.fix_checksum()
    first = bytes(img.data)
    img.fix_checksum()                                             # the old checksum is not summed in
    assert bytes(img.data) == first


# ---- the Patcher page ---------------------------------------------------------

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_page_without_a_server(qapp):
    from wolfrat.server_patcher_panel import NO_SERVER, ServerPatcherPanel
    panel = ServerPatcherPanel(lambda: "")
    assert panel.status_lbl.text() == NO_SERVER and not panel.build_btn.isEnabled()
    assert panel.admin_box.isChecked() and not panel.admin_box.isEnabled()


def test_page_on_a_stock_exe(qapp, tmp_path):
    from wolfrat.server_patcher_panel import ADMIN_FIX_MISSING, ServerPatcherPanel
    panel = ServerPatcherPanel(lambda: server(tmp_path))
    assert panel.admin_box.isChecked() and not panel.admin_box.isEnabled()
    assert panel.admin_note.text() == ADMIN_FIX_MISSING
    assert all(b.isEnabled() for b in panel.speed_btns.values()) and panel.speed_btns[64].isChecked()
    assert panel.hole_box.isChecked() and not panel.hole_box.isEnabled()
    assert panel.hole_box.text() == "Hole-skip  (64 Hz version)"
    assert "without hole-skip" in panel.speed_note.text() and "Hole-skip (64 Hz version)" in panel.speed_note.text()
    panel.fps_box.setChecked(False)
    panel.speed_btns[125].setChecked(True)
    assert panel.hole_box.text() == "Hole-skip  (125 Hz version)"
    assert panel.fps_box.isChecked() and not panel.fps_box.isEnabled()
    assert panel.fps_box.text().endswith("(needed for 125 Hz)")
    panel.speed_btns[32].setChecked(True)
    assert not panel.hole_box.isChecked()
    assert panel.fps_box.isEnabled() and panel.fps_box.text() == "Frame rate lock (~125 FPS)"
    panel.speed_btns[64].setChecked(True)
    assert panel.memory_btns[512].isChecked()
    panel.memory_btns[1024].setChecked(True)
    assert panel.laa_box.isChecked() and not panel.laa_box.isEnabled()
    panel.memory_btns[512].setChecked(True)
    assert panel.laa_box.isEnabled()
    panel.build()
    assert panel.result is not None and (tmp_path / P.OUT_EXE).exists()
    text = panel.result_lbl.text()
    assert "Built jointops_wolfrat.exe" in text and "Stop the game server." in text


def test_page_marks_what_is_already_in(qapp, tmp_path):
    from wolfrat.server_patcher_panel import ADMIN_FIX_HAVE, ServerPatcherPanel
    first = P.build(server(tmp_path), everything(spectator=False))
    folder = tmp_path / "srv"
    folder.mkdir()
    path = server(folder, open(first.exe_path, "rb").read(),
                  cfg="lock_framerate = 7\ne_spawn_protection = 3\n")
    panel = ServerPatcherPanel(lambda: path)
    assert panel.admin_note.text() == ADMIN_FIX_HAVE
    for box in (panel.fps_box, panel.kill_box, panel.chat_box, panel.laa_box):
        assert box.isChecked() and not box.isEnabled() and box.text().endswith("(already in)")
    assert panel.spawn_box.isChecked() and not panel.spawn_box.isEnabled()
    assert panel.spectator_box.isEnabled() and not panel.spectator_box.isChecked()
    assert panel.spawn_box.text() == "Spawn protection set in game.cfg  (already in: game.cfg says 3 seconds)"
    assert panel.colour_box.isEnabled()


def test_page_on_a_server_that_adds_the_fixes_at_start_up(qapp, tmp_path):
    from wolfrat.server_patcher_panel import ADMIN_FIX_AT_START, ServerPatcherPanel
    path = server(tmp_path)
    panel = ServerPatcherPanel(lambda: path, running)
    assert panel.admin_note.text() == ADMIN_FIX_AT_START
    assert panel.spawn_box.isEnabled() and not panel.spawn_box.isChecked()
    assert "already set to about 5.0 s when the server starts" in panel.extras_note.text()
    assert "MISSING: Admin" not in panel.status_lbl.text()
    panel._chosen = path                                # chosen by hand: not treated as the running server
    panel.refresh(force=True)
    assert "MISSING: Admin" in panel.status_lbl.text()


def test_spawn_line_reads_game_cfg_as_it_is(qapp, tmp_path):
    """Dale 2026-09-27: no seconds box; show what game.cfg says (the patched
    server writes 0 there at first); say nothing extra when the patch is not in."""
    from wolfrat.server_patcher_panel import SPAWN_TEXT, ServerPatcherPanel
    first = P.build(server(tmp_path), everything())
    for cfg, words in (("e_spawn_protection = 0", "game.cfg says 0 seconds"),
                       ("lock_framerate = 7", "not in game.cfg yet")):
        folder = tmp_path / words.replace(" ", "_")
        folder.mkdir()
        path = server(folder, open(first.exe_path, "rb").read(), cfg=cfg)
        panel = ServerPatcherPanel(lambda: path)
        assert panel.spawn_box.text() == f"{SPAWN_TEXT}  (already in: {words})"
    panel = ServerPatcherPanel(lambda: server(tmp_path))            # patch not in
    assert panel.spawn_box.text() == SPAWN_TEXT and panel.spawn_box.isEnabled()
    assert not hasattr(panel, "spawn_secs")


def test_frame_rate_lock_note_warns_about_the_cpu(qapp, tmp_path):
    """Dale 2026-09-27: without lock_framerate = 7 one core runs at 100%."""
    from wolfrat.server_patcher_panel import FPS_NOTE, ServerPatcherPanel
    panel = ServerPatcherPanel(lambda: server(tmp_path))            # game.cfg says 1
    assert panel.fps_box.isChecked() and panel.fps_note.text() == FPS_NOTE
    panel.fps_box.setChecked(False)
    assert panel.fps_note.text() == ""
    folder = tmp_path / "seven"
    folder.mkdir()
    panel = ServerPatcherPanel(lambda: server(folder, cfg="lock_framerate = 7"))
    assert panel.fps_note.text() == "game.cfg has lock_framerate = 7 (~125 FPS)."


def test_page_says_no_to_an_unknown_exe(qapp, tmp_path):
    from wolfrat.server_patcher_panel import NOT_KNOWN, ServerPatcherPanel
    panel = ServerPatcherPanel(lambda: server(tmp_path, fake_exe({})))
    assert panel.status_lbl.text() == NOT_KNOWN and not panel.build_btn.isEnabled()


def test_page_fits_1024x768(qapp, tmp_path):
    """The Server tab leaves about 540 px for a sub-page at 1024x768; the
    page scrolls rather than squashing its boxes."""
    from PyQt6.QtWidgets import QGroupBox, QTabWidget
    from wolfrat.server_patcher_panel import ServerPatcherPanel
    tabs = QTabWidget()
    panel = ServerPatcherPanel(lambda: server(tmp_path))
    tabs.addTab(panel, "Patcher")
    tabs.resize(1004, 540)
    tabs.show()
    for _ in range(20):
        qapp.processEvents()
    for group in panel.findChildren(QGroupBox):
        assert group.height() >= group.minimumSizeHint().height()
    tabs.hide()
