"""Server patch detection (2026-09-26) against fake exes built from the same
bytes the jo-server-patches scripts assert.  Real exes cannot go in the repo;
the checks were also run by hand on the live 125 Hz server exe and on older
builds on the FMJ box (live: '125 Hz · ~125 FPS · crash fixes in').  Speed and
mission memory also read the running server (2026-09-27)."""

import os
import struct

import pytest

from wolfrat import server_patches as sp

BASE = 0x400000
TEXT_VA, TEXT_SIZE = 0x1000, 0x394000          # .text 0x401000..0x795000
TEXT2_VA, TEXT2_SIZE = 0x395000, 0x2B000       # _text 0x795000..0x7C0000
RAW0 = 0x400

STOCK = {
    0x4056E6: "03c003c0",                          # 01 admin crash: off
    0x504C2B: "80e201",                            # 14 K: off
    0x4C4B13: "02",                                # 07: 64 Hz
    0x626954: "8b8fb8070000",                      # 09 hook: off
    0x52B85E: "33c0390544075502" "8935081f4e02" "0f95c0",
    0x52B89F: "c1e008", 0x52B8CA: "c1e208",        # 11: off
    0x52B8B0: "6a01ff15d4007c00",                  # Sleep intact
    0x4A7CE3: "0000000c",                          # 04: 192 MB
    0x516BBA: "c781240100006c020000",              # 03/12: stock 620 ticks
    0x5137C7: "80bed7880100",                      # 14 S: off
    0x49A93B: "e820e6ffff", 0x49A9DC: "8d4c240451",  # 15: off
}
LIVE = dict(STOCK)
LIVE.update({
    0x4056E6: "c1e00690", 0x504C2B: "80e200", 0x4C4B13: "01",
    0x626954: "e997e41600" "90",                   # jmp 0x794DF0
    0x794E0A: "06", 0x794E1A: "20",                # hole-skip settings: the 125 Hz version
    0x52B85E: "a144075502" "8935081f4e02" "909090909090",
    0x52B89F: "c1e004", 0x52B8CA: "c1e204",
    0x52B75C: "ff1554047c00", 0x52B798: "ff1554047c00", 0x52B8B8: "ff1554047c00",
    0x52BA7E: "ff1554047c00", 0x52BA95: "8b1d54047c00",
    0x516BBA: "e8180723009090909090",
    0x5137C7: "e954c22a00",
    0x49A93B: "e8c0533200", 0x49A9DC: "5690909090",
})


def fake_exe(sites: dict, laa: bool = True) -> bytes:
    pe = 0x80
    head = bytearray(RAW0)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe + 4, 0x14C, 2, 0, 0, 0, 0xE0, 0x0102 | (0x20 if laa else 0))
    struct.pack_into("<I", head, pe + 24 + 28, BASE)
    sec = pe + 24 + 0xE0
    raw2 = RAW0 + TEXT_SIZE
    for i, (name, va, size, raw) in enumerate(((b".text", TEXT_VA, TEXT_SIZE, RAW0),
                                                (b"_text", TEXT2_VA, TEXT2_SIZE, raw2))):
        o = sec + i * 40
        head[o:o + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", head, o + 8, size, va, size, raw)
        struct.pack_into("<I", head, o + 36, 0xE0000020)
    body = bytearray(TEXT_SIZE + TEXT2_SIZE)
    for va, hx in sites.items():
        data = bytes.fromhex(hx)
        off = va - BASE - TEXT_VA
        body[off:off + len(data)] = data
    return bytes(head) + bytes(body)


def write(tmp_path, sites, cfg="", laa=True):
    exe = tmp_path / "jointops.exe"
    exe.write_bytes(fake_exe(sites, laa))
    if cfg:
        (tmp_path / "game.cfg").write_text(cfg)
    return str(exe)


def levels(report):
    return {f.key: f.level for f in report.findings}


def test_our_live_server(tmp_path):
    r = sp.check(write(tmp_path, LIVE, "lock_framerate       = 7\ne_spawn_protection   = 5\n"))
    assert r.known
    assert r.headline == "125 Hz · 192 MB · ~125 FPS · crash fixes in"
    assert set(levels(r).values()) <= {sp.GOOD, sp.INFO}
    by = {f.key: f for f in r.findings}
    assert by["spawn"].detail == "Set in game.cfg: e_spawn_protection = 5 seconds."
    assert "with colours" in by["long_chat"].detail


def test_stock_server_is_missing_the_crash_fix(tmp_path):
    r = sp.check(write(tmp_path, STOCK, laa=False))
    lv = levels(r)
    assert r.known and lv["admin_crash"] == sp.BAD and lv["kill_list"] == sp.WARN
    assert lv["laa"] == sp.WARN and lv["hole_skip"] == sp.BAD       # 64 Hz needs it too (Dale)
    assert r.headline.startswith("64 Hz · 192 MB · MISSING: Admin-port crash fix")


def test_hole_skip_versions(tmp_path):
    """Dale 2026-09-27: 64 Hz does not work without hole-skip, and 125 Hz needs
    the tuned version (6 / 32); Spaghetti's original is 8 / 64."""
    def detail(sites):
        return {f.key: f for f in sp.check(write(tmp_path, sites)).findings}["hole_skip"]
    assert detail(LIVE).detail == "In (125 Hz version)."
    at64 = {**LIVE, 0x4C4B13: "02", 0x794E0A: "08", 0x794E1A: "40"}
    assert detail(at64).detail == "In (64 Hz version)." and detail(at64).level == sp.GOOD
    wrong = detail({**LIVE, 0x794E0A: "08", 0x794E1A: "40"})       # 64 Hz version at 125 Hz
    assert wrong.level == sp.WARN and "125 Hz version" in wrong.detail
    assert "custom settings 4 / 16" in detail({**LIVE, 0x794E0A: "04", 0x794E1A: "10"}).detail
    assert detail({**STOCK, 0x4C4B13: "04"}).level == sp.INFO       # 32 Hz: not needed
    assert detail(STOCK).level == sp.BAD and "MISSING at 64 Hz" in detail(STOCK).detail


def test_125hz_without_hole_skip_is_red(tmp_path):
    sites = {**STOCK, **{0x4C4B13: "01"}}
    r = sp.check(write(tmp_path, sites))
    assert levels(r)["hole_skip"] == sp.BAD
    assert "Hole-skip" in r.headline and r.headline.startswith("125 Hz")


@pytest.mark.parametrize("byte, words", [("04", "32 Hz"), ("02", "64 Hz"), ("01", "125 Hz")])
def test_speed(tmp_path, byte, words):
    r = sp.check(write(tmp_path, {**STOCK, **{0x4C4B13: byte}}))
    assert {f.key: f for f in r.findings}["speed"].detail.startswith(words)


def test_speed_never_names_a_normal_speed(tmp_path):
    for byte in ("01", "02", "04"):
        detail = {f.key: f for f in sp.check(write(tmp_path, {**STOCK, 0x4C4B12: "b8" + byte + "000000"})).findings}["speed"].detail
        assert "normal" not in detail


def test_speed_is_always_one_answer(tmp_path):
    by = {f.key: f for f in sp.check(write(tmp_path, {**LIVE, **{0x4C4B13: "03"}})).findings}
    assert by["speed"].level == sp.UNKNOWN and by["speed"].detail == "Unknown."
    by = {f.key: f for f in sp.check(write(tmp_path, LIVE)).findings}
    assert by["speed"].level == sp.GOOD and by["speed"].detail == "125 Hz."


def live_server(speed="01", arena=0x20000000):
    """read(address, size) over a pretend running server."""
    memory = {sp.SPEED_VA: bytes.fromhex(speed), sp.ARENA_SIZE_VA: struct.pack("<I", arena)}
    return lambda address, size: memory.get(address)


def hook_dll(tmp_path, arena):
    """A binkw32.dll that sets the mission memory at start-up (as onHook does)."""
    size = struct.pack("<I", arena)
    body = bytes.fromhex("c785d8feffff") + size + bytes.fromhex("c7442410") + size
    (tmp_path / "binkw32.dll").write_bytes(b"MZ" + bytes(500) + body + bytes(100))


@pytest.mark.parametrize("exe_arena, dll_arena, words", [
    ("0000000c", None, "192 MB (the game's normal)."),
    ("00000020", None, "512 MB."),
    ("00000040", None, "1 GB."),
    ("0000000c", 0x20000000, "512 MB."),          # the DLL raises it; the row never names it
    ("0000000c", 0x40000000, "1 GB."),
])
def test_mission_memory_from_the_files(tmp_path, exe_arena, dll_arena, words):
    if dll_arena:
        hook_dll(tmp_path, dll_arena)
    r = sp.check(write(tmp_path, {**LIVE, **{0x4A7CE3: exe_arena}}))
    memory = {f.key: f for f in r.findings}["memory"]
    assert memory.detail == words and "onhook" not in memory.detail.lower()
    assert r.headline.split(" · ")[1] == words.split(" (")[0].rstrip(".")


def test_a_plain_bink_dll_is_not_a_memory_setting(tmp_path):
    (tmp_path / "binkw32.dll").write_bytes(b"MZ" + bytes(4000))
    r = sp.check(write(tmp_path, LIVE))
    assert {f.key: f for f in r.findings}["memory"].detail == "192 MB (the game's normal)."


def test_the_running_server_wins_and_a_pending_restart_is_named(tmp_path):
    """2026-09-27: the FMJ server ran 512 MB while 1 GB sat in the folder."""
    hook_dll(tmp_path, 0x40000000)
    path = write(tmp_path, LIVE)
    r = sp.check(path, live_server(arena=0x20000000))
    by = {f.key: f for f in r.findings}
    assert by["memory"].detail == "512 MB now - 1 GB after the next server restart."
    assert r.headline.startswith("125 Hz · 512 MB ·")
    assert sp.check(path, live_server(arena=0x40000000)).headline.startswith("125 Hz · 1 GB ·")
    r = sp.check(path, live_server(speed="02", arena=0x40000000))
    by = {f.key: f for f in r.findings}
    assert by["speed"].detail == "64 Hz now - 125 Hz after the next server restart."
    assert by["speed"].level == sp.INFO and r.headline.startswith("64 Hz · 1 GB")


def test_fixes_added_at_start_up_count_as_in(tmp_path):
    """onHook 0.6.0 (PR #43) adds patch 01 and rewrites the spawn time in
    memory at start-up; the exe file has neither.  The page must not cry
    MISSING at a server that is protected."""
    path = write(tmp_path, STOCK)
    memory = {0x4056E6: bytes.fromhex("c1e00690"),
              0x516BBA: bytes.fromhex("c78124010000") + struct.pack("<i", 313)}
    r = sp.check(path, lambda address, size: memory.get(address))
    by = {f.key: f for f in r.findings}
    assert by["admin_crash"].level == sp.GOOD and "when the server starts" in by["admin_crash"].detail
    assert by["spawn"].detail == "About 5.0 seconds (set when the server starts)."
    assert "MISSING: Admin-port" not in r.headline
    plain = {f.key: f for f in sp.check(path).findings}           # no running server: the file
    assert plain["admin_crash"].level == sp.BAD
    assert plain["spawn"].detail == "Not patched: the game's normal ~10 seconds."
    stock_mem = {0x4056E6: bytes.fromhex("03c003c0"),
                 0x516BBA: bytes.fromhex("c781240100006c020000")}
    by = {f.key: f for f in sp.check(path, lambda a, s: stock_mem.get(a)).findings}
    assert by["admin_crash"].level == sp.BAD and by["spawn"].detail.startswith("Not patched")


def test_a_dead_or_odd_live_read_falls_back_to_the_files(tmp_path):
    hook_dll(tmp_path, 0x40000000)
    path = write(tmp_path, LIVE)

    def closed(address, size):
        raise OSError("gone")
    for live in (closed, lambda a, s: None, live_server(speed="07", arena=0x12345678)):
        by = {f.key: f for f in sp.check(path, live).findings}
        assert by["memory"].detail == "1 GB." and by["speed"].detail == "125 Hz."


def test_1gb_without_large_address_aware_is_a_warning(tmp_path):
    r = sp.check(write(tmp_path, {**LIVE, **{0x4A7CE3: "00000040"}}, laa=False))
    memory = {f.key: f for f in r.findings}["memory"]
    assert memory.level == sp.WARN and "Large Address Aware" in memory.detail


@pytest.mark.parametrize("cfg", ["", "lock_framerate = 0", "lock_framerate = 1"])
def test_fps_lock_without_7_warns_about_a_full_cpu_core(tmp_path, cfg):
    fps = {f.key: f for f in sp.check(write(tmp_path, LIVE, cfg)).findings}["fps"]
    assert fps.level == sp.WARN and "100%" in fps.detail and "lock_framerate = 7" in fps.detail


def test_fps_lock_needs_the_cfg_line_and_unlimited_is_a_warning(tmp_path):
    r = sp.check(write(tmp_path, LIVE))                             # no game.cfg
    assert levels(r)["fps"] == sp.WARN
    unlimited = {**STOCK, **{0x52B8B0: "9090909090909090"}}
    r = sp.check(write(tmp_path, unlimited))
    f = {x.key: x for x in r.findings}["fps"]
    assert f.level == sp.WARN and "CPU core" in f.detail
    assert sp.fps_for(8) == "~112 FPS" and sp.fps_for(16) == "62.5 FPS"


def test_spawn_protection_fixed_in_the_exe(tmp_path):
    sites = {**STOCK, **{0x516BBA: "c78124010000" + struct.pack("<i", 312).hex()}}
    r = sp.check(write(tmp_path, sites))
    assert {f.key: f for f in r.findings}["spawn"].detail == "Fixed in the exe: about 5.0 seconds."


def test_long_chat_without_colours(tmp_path):
    sites = {**STOCK, **{0x49A93B: "e8c0533200", 0x49A9DC: "6800fe7b00"}}
    r = sp.check(write(tmp_path, sites))
    assert "without colours" in {f.key: f for f in r.findings}["long_chat"].detail


def test_an_exe_we_do_not_know_says_so(tmp_path):
    r = sp.check(write(tmp_path, {}))
    assert not r.known and "not a Joint Ops server exe" in r.headline
    missing = sp.check(str(tmp_path / "nope.exe"))
    assert not missing.known and "Could not read" in missing.headline


def test_game_cfg_reading():
    cfg_lines = 'lock_framerate       = 7\nServerName = "[UK] Mixed Maps 125hz"\n'
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "game.cfg"), "w").write(cfg_lines)
        cfg = sp.read_cfg(d)
    assert cfg["lock_framerate"] == "7" and cfg["servername"] == "[UK] Mixed Maps 125hz"
    assert sp.read_cfg("C:/definitely/not/here") == {}


# ---- the Server tab box ---------------------------------------------------

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_panel_without_a_server(qapp):
    from wolfrat.server_patches_panel import NO_SERVER, NO_SERVER_HEADLINE, ServerPatchesPanel
    panel = ServerPatchesPanel(lambda: "")
    assert panel.headline_lbl.text() == NO_SERVER_HEADLINE and panel.report is None
    assert NO_SERVER.split(" - ")[0] in panel.rows_lbl.text()
    assert not panel.headline_lbl.wordWrap()         # a wrapping headline squashed the tab


def test_panel_shows_the_report_and_notices_a_swapped_exe(qapp, tmp_path):
    from wolfrat.server_patches_panel import ServerPatchesPanel
    path = write(tmp_path, STOCK)
    panel = ServerPatchesPanel(lambda: path)
    assert panel.headline_lbl.text().startswith("64 Hz · 192 MB · MISSING")
    rows = panel.rows_lbl.text()
    assert rows.count("<tr>") == len(panel.report.findings)
    assert rows.index("Admin-port crash fix - MISSING") < rows.index("Server speed")   # problems on top
    assert rows.count('href="https://github.com/BadgerLove/jo-server-patches') == len(panel.report.findings)
    first = panel.report
    panel.refresh()                                  # nothing changed: same report
    assert panel.report is first
    with open(path, "wb") as handle:                 # the admin swaps in a patched exe
        handle.write(fake_exe(LIVE) + b"\0")         # (size differs, so it is noticed)
    (tmp_path / "game.cfg").write_text("lock_framerate = 7\n")
    panel.refresh()
    assert panel.report is not first
    assert panel.headline_lbl.text() == "125 Hz · 192 MB · ~125 FPS · crash fixes in"


def test_panel_notices_a_dll_swap_and_a_restart(qapp, tmp_path):
    from wolfrat.server_patches_panel import ServerPatchesPanel
    path = write(tmp_path, LIVE)
    hook_dll(tmp_path, 0x20000000)
    running = {"arena": 0x20000000}
    panel = ServerPatchesPanel(lambda: path, lambda a, s: live_server(arena=running["arena"])(a, s))
    assert panel.headline_lbl.text().startswith("125 Hz · 512 MB")
    hook_dll(tmp_path, 0x40000000)                   # same size: the mtime/stat may not move,
    panel.refresh(force=True)                        # so the admin presses Check again
    assert "512 MB now - 1 GB after the next server restart" in panel.rows_lbl.text()
    running["arena"] = 0x40000000                    # the server restarts on the new DLL
    panel.refresh()
    assert panel.headline_lbl.text().startswith("125 Hz · 1 GB")
    assert panel.title() == "Server patches (read from the server)"


def test_box_never_squashes_the_server_tab_top_half(qapp, tmp_path):
    """The Server tab at 1024x768 leaves 574 px. The first build of this box
    made Qt split the height 275/275 (a word-wrapping headline next to the
    word-wrapping link box confused its height maths) and the Connection box
    was crushed to 135 of its 224 px.  Same shape here, real link box."""
    from PyQt6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLineEdit,
                                 QListWidget, QPushButton, QVBoxLayout, QWidget)
    from wolfrat import server_link as sl
    from wolfrat.server_link_panel import ServerLinkPanel
    from wolfrat.server_patches_panel import ServerPatchesPanel
    path = write(tmp_path, STOCK)                        # a long, red headline
    page = QWidget()
    layout = QVBoxLayout(page)
    top = QHBoxLayout()
    left = QVBoxLayout()
    conn = QGroupBox("Server Connection")
    grid = QGridLayout(conn)
    for row in range(4):
        grid.addWidget(QLineEdit(), row, 1)
    grid.addWidget(QPushButton("Connect"), 4, 1)
    status = QGroupBox("Server Status")
    sgrid = QGridLayout(status)
    for row in range(4):
        sgrid.addWidget(QLineEdit(), row, 0)
    left.addWidget(conn)
    left.addWidget(status)
    top.addLayout(left, 3)
    state = sl.LinkState(looked=True, found=True, reachable=True, pid=1, folder="C:/GAMES/JO",
                         script_installed=True, writable=True)
    top.addWidget(ServerLinkPanel(lambda: state, lambda: (True, ""), lambda: None), 2)
    layout.addLayout(top)
    saved = QGroupBox("Saved Servers")
    QVBoxLayout(saved).addWidget(QListWidget())
    bottom = QHBoxLayout()
    bottom.addWidget(saved, 3)
    panel = ServerPatchesPanel(lambda: path)
    bottom.addWidget(panel, 2)
    layout.addLayout(bottom)
    page.resize(1004, 574)
    page.show()
    for _ in range(20):
        qapp.processEvents()
    assert conn.height() >= conn.minimumSizeHint().height()
    assert status.height() >= status.minimumSizeHint().height()
    page.hide()


def test_rows_are_escaped():
    f = sp.Finding("x", "A <b> & C", sp.BAD, "say <co>", "https://example.com/?a=1&b=2")
    from wolfrat.server_patches_panel import rows_html
    out = rows_html([f])
    assert "A &lt;b&gt; &amp; C" in out and "say &lt;co&gt;" in out and "a=1&amp;b=2" in out
