"""Private replies (Dale, 2026-09-23): "unknown command" and "not allowed for
your rank" go only to the player who typed it; public chat stays the fallback
when the server is elsewhere or the script is not loaded yet."""

import json
import struct
import sys

import pytest

from wolfrat import server_wac, whisper, weather
from wolfrat.whisper import (CAPACITY_VA, FLAGS_OFFSET, MESSAGES, PING, PING_VAR, PONG,
                             SLOT_STRIDE, SLOTPTR_VA, Whisperer)


# ---- the script block ---------------------------------------------------------------

def test_block_uses_only_one_number_forms_and_safe_text():
    section = whisper.build_section()
    assert section.startswith(whisper.BEGIN) and whisper.END in section
    assert "PLOOP" in section and section.count("endif") == 3
    assert "if eq(G249, 3) then" in section and "set(G249, 4)" in section
    for say, done, colour, text in MESSAGES.values():
        assert f"if pisvar({say}) and not pisvar({done}) then" in section
        assert f"psetvar({done})" in section and f"psetvar({done}," not in section
        assert server_wac.text_problem(text) is None and len(text) <= 62
        assert 0 <= say <= 16 and 0 <= done <= 16


def test_install_keeps_everything_else_and_is_crlf(tmp_path):
    weather.install_addon(tmp_path)                            # the lightning block
    # An old script welcome, as WolfRAT up to 2.10.25 wrote it.
    old_welcome = "\n".join([
        server_wac.BEGIN, "PLOOP", "if onptick(20) then",
        'ptext("<c00ff00>Welcome to FMJ")', "endif", "END", server_wac.END]) + "\n"
    server_wac._write(tmp_path, server_wac._read(tmp_path) + "\n" + old_welcome)
    before = server_wac._read(tmp_path)
    assert whisper.install(tmp_path) == "added"
    raw = (tmp_path / "server.wac").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = server_wac._read(tmp_path)
    assert text.startswith(before) and whisper.installed(tmp_path)
    assert weather.addon_installed(tmp_path) and server_wac.read_welcome(tmp_path).text == "Welcome to FMJ"
    assert whisper.install(tmp_path) == "unchanged"
    assert text.count(whisper.BEGIN) == 1


def test_install_on_an_empty_folder(tmp_path):
    assert whisper.install(tmp_path) == "added"
    assert server_wac._read(tmp_path) == whisper.build_section()


# ---- the send / answer / clear dance -------------------------------------------------------

BASE = 0x10000000


class FakeServer:
    """A slot table and G249 in a dict of bytes; ``script_run()`` plays server.wac."""

    def __init__(self, names):
        self.mem = {}
        self.names = names
        self._dword(CAPACITY_VA, len(names))
        self._dword(SLOTPTR_VA, BASE)
        for i, name in enumerate(names):
            slot = BASE + i * SLOT_STRIDE
            self.mem[slot + 4] = 1 if name else 0
            for j, ch in enumerate((name or "").encode()):
                self.mem[slot + 40 + j] = ch
        self.loaded = True                                     # our block compiled on this map
        self.said = []

    def _dword(self, address, value):
        for j, b in enumerate(struct.pack("<i", value)):
            self.mem[address + j] = b

    def read(self, address, size):
        return bytes(self.mem.get(address + j, 0) for j in range(size))

    def write(self, address, data):
        for j, b in enumerate(data):
            self.mem[address + j] = b

    def flag(self, index, i):
        return self.mem.get(BASE + index * SLOT_STRIDE + FLAGS_OFFSET + i, 0)

    def script_run(self, alive=True):
        if not self.loaded:
            return
        if self.read(PING_VAR, 4) == struct.pack("<i", PING):
            self._dword(PING_VAR, PONG)
        for index, name in enumerate(self.names):
            if not name or not alive:
                continue
            for kind, (say, done, _c, text) in MESSAGES.items():
                if self.flag(index, say) and not self.flag(index, done):
                    self.said.append((name, text))
                    self.mem[BASE + index * SLOT_STRIDE + FLAGS_OFFSET + done] = 1


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def ready(server):
    clock = Clock()
    w = Whisperer(lambda: server, clock)
    w.tick()                                                  # asks "are you there?"
    server.script_run()
    clock.now += whisper.PROBE_SECONDS
    w.tick()                                                  # reads the answer
    return w, clock


def test_whisper_reaches_only_that_player_once():
    server = FakeServer(["Dale", "Griefer", "Ham"])
    w, clock = ready(server)
    assert w.available()
    assert w.whisper_to("griefer", whisper.UNKNOWN)                   # any case
    assert server.flag(1, 13) == 1 and server.flag(0, 13) == 0
    server.script_run()
    server.script_run()                                       # a second run before WolfRAT tidies up
    assert server.said == [("Griefer", MESSAGES[whisper.UNKNOWN][3])]
    clock.now += 0.25
    w.tick()
    assert server.flag(1, 13) == 0 and server.flag(1, 14) == 0  # cleared for next time
    assert w.whisper_to("Griefer", whisper.UNKNOWN)
    server.script_run()
    assert len(server.said) == 2


def test_both_messages_can_be_pending_for_one_player():
    server = FakeServer(["Dale"])
    w, clock = ready(server)
    w.whisper_to("Dale", whisper.UNKNOWN)
    w.whisper_to("Dale", whisper.DENIED)
    server.script_run()
    assert [t for _n, t in server.said] == [MESSAGES[whisper.UNKNOWN][3], MESSAGES[whisper.DENIED][3]]


def test_not_loaded_on_this_map_means_public_chat():
    server = FakeServer(["Dale"])
    server.loaded = False
    w, _ = ready(server)
    assert not w.available()
    assert not w.whisper_to("Dale", whisper.UNKNOWN)
    assert server.flag(0, 13) == 0


def test_server_on_another_pc_means_public_chat():
    w = Whisperer(lambda: None, Clock())
    w.tick()
    assert not w.available() and not w.whisper_to("Dale", whisper.DENIED)


def test_unknown_player_means_public_chat():
    server = FakeServer(["Dale"])
    w, _ = ready(server)
    assert not w.whisper_to("Nobody", whisper.UNKNOWN)


def test_a_player_the_script_never_reaches_is_let_go():
    server = FakeServer(["Dale"])
    w, clock = ready(server)
    w.whisper_to("Dale", whisper.DENIED)
    server.script_run(alive=False)                             # dead, in the spawn screen
    clock.now += whisper.GIVE_UP_SECONDS
    w.tick()
    assert server.flag(0, 15) == 0 and server.said == []


def test_block_vanishing_after_a_map_change_is_noticed():
    server = FakeServer(["Dale"])
    w, clock = ready(server)
    assert w.available()
    server.loaded = False
    server._dword(PING_VAR, 0)                                 # G vars are cleared with the map
    for _ in range(3):
        clock.now += whisper.PROBE_SECONDS
        w.tick()
    assert not w.available()


# ---- the Mods tab -----------------------------------------------------------------------------

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat.app import DesktopRuntime, MissionsStore, ModsTab  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class ModServer:
    def __init__(self):
        self.players = [{"name": n, "id": i, "team": 1, "revision": 7}
                        for i, n in enumerate(("Ham", "Griefer"), 3)]
        self.missions, self.mission_entries = [], []

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


class FakeWhisperer:
    def __init__(self, works):
        self.works, self.sent = works, []

    def whisper_to(self, name, kind):
        self.sent.append((name, kind))
        return self.works


def mods(tmp_path, works):
    rt = DesktopRuntime.isolated(tmp_path)
    with open(rt.path("wolfrat_mods.json"), "w") as f:
        json.dump({"mods": ["Ham"]}, f)
    tab = ModsTab(ModServer(), MissionsStore(rt), rt)
    tab.said = []
    tab._send_mod_chat = lambda msg, ctx="": tab.said.append(msg)
    tab.whisperer = FakeWhisperer(works)
    return tab


def test_unknown_command_is_whispered(tmp_path):
    tab = mods(tmp_path, works=True)
    tab._check_mod_command("Griefer: !medipack")
    assert tab.whisperer.sent == [("griefer", whisper.UNKNOWN)] and tab.said == []


def test_rank_refusal_is_whispered(tmp_path):
    tab = mods(tmp_path, works=True)
    tab._check_mod_command("Ham: !ban Griefer")
    assert tab.whisperer.sent == [("ham", whisper.DENIED)] and tab.said == []


def test_falls_back_to_public_chat_as_before(tmp_path):
    tab = mods(tmp_path, works=False)
    tab._check_mod_command("Griefer: !medipack")
    tab._check_mod_command("Ham: !ban Griefer")
    assert tab.said == ["Unknown command: !medipack", "!ban is not allowed for Moderator."]


def test_no_whisperer_at_all_is_public(tmp_path):
    tab = mods(tmp_path, works=True)
    tab.whisperer = None
    tab._check_mod_command("Griefer: !medipack")
    assert tab.said == ["Unknown command: !medipack"]
