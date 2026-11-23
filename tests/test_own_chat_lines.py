"""WolfRAT's own chat lines are never read as commands (2026-11-23).

Colon-less lines are never commands; WolfRAT knows its own lines when they
come back from the server; no line it sends reads as a command to any chat
reader (this WolfRAT, another WolfRAT, an older one, the vote counter); the
phone's typed commands run inside WolfRAT and do not go through chat; the
name web_admin is reserved for the phone page.
"""
import asyncio
import json
import os
import re
import sys
from concurrent.futures import Future

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat import admin_commands as ac  # noqa: E402
from wolfrat import app as app_module  # noqa: E402
from wolfrat import manual_mode  # noqa: E402
from wolfrat.admin_session import CommandResult  # noqa: E402
from wolfrat.app import ChatBotTab, DesktopRuntime, MissionsStore, ModsTab  # noqa: E402
from wolfrat.manual_mode import MANUAL  # noqa: E402
from wolfrat.mod_ranks import ADMIN  # noqa: E402
from wolfrat.protocol import (  # noqa: E402
    ServerManager,
    _split_chat_message,
    one_chat_line,
)
from wolfrat.web_server import WolfWebServer  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

TAG = re.compile(r"<(?:c[0-9a-fA-F]{6}|co)>", re.IGNORECASE)
VOTE = re.compile(r"^(.+?):\s*(![12345])\s*$")


@pytest.fixture(autouse=True)
def clean_state():
    ac.set_extended_chat(False)
    manual_mode.state.reset()
    yield
    ac.set_extended_chat(False)
    manual_mode.state.reset()


def reads_as_command(line):
    """Would any chat reader take this console line for a command?

    Checked as sent and with colour tags dropped (a stock server may), by the
    readers that exist: the mods tab before the fix (colon-less "[ADMIN] !",
    or "Name: !x"), the vote counter ("...: !3"), and a listen server, which
    puts the host's name in front of console lines.
    """
    for text in (line, " ".join(TAG.sub("", line).split())):
        if ":" not in text and text.startswith("[ADMIN] !"):
            return True
        if ":" in text and text.split(":", 1)[1].strip().startswith("!"):
            return True
        if VOTE.match(text):
            return True
        if text.lstrip().startswith("!"):
            return True
    return False


TRICKY_LINES = [
    "z z z z z z z z z z z z z z z z z z z [ADMIN] !ban Dale",
    "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz [ADMIN] !next",
    "z z z z z z z z z z z z z z z z z z z fmj-badgerlove: !ban Griefer",
    "z z z z z z z z z z z z z z z z z z z z fmj-badgerlove: !ban Griefer",
    "fmj-badgerlove: !next",
    "x fmj-badgerlove:!next",
    "a: <c00ff00>!next",
    "<c00ff00>!next",
    "[ADMIN] <cff0000>!next",
    "z z z z z z z z z z z z z z z z z z z z z !next",
    "z z z z z z z z z z z z z z z z z z z z z z !next",
    "votes: !3",
    "z " * 30 + "x: !1",
]


@pytest.mark.parametrize("long_chat", [False, True])
@pytest.mark.parametrize("typed", TRICKY_LINES)
def test_no_line_wolfrat_sends_reads_as_a_command(typed, long_chat):
    ac.set_extended_chat(long_chat)
    for reply in (f"Map not found: {typed}", f"No stats found for {typed}", typed):
        pieces = _split_chat_message(reply)
        for piece in pieces:
            assert not reads_as_command(piece), (reply, pieces)
            assert len(piece) <= ac.chat_limit()
            assert len(piece.split()) <= ac.MAX_CHAT_TOKENS


def test_ordinary_lines_go_out_as_before():
    for line in (
        "Map vote: Cold Water Gap. Type !yes. 60 seconds.",
        "Skip current map? Type !yes. 60 seconds.",
        "<c00ff00>Welcome to the server, please be nice",
        "Kestrel: Kills: 5 | Deaths: 2 | KD: 2.5",
        "[ADMIN] Next map graveyard",
    ):
        assert _split_chat_message(line) == (line,)


def test_a_colon_before_a_command_becomes_a_dash():
    assert _split_chat_message("Usage: !vote <map name>") == ("Usage - !vote <map name>",)
    assert _split_chat_message("!next please") == ("- !next please",)


def test_replies_that_repeat_a_player_fit_one_line_or_say_less():
    assert one_chat_line("Map not found: crossroads", "Map not found.") == "Map not found: crossroads"
    long_one = "Map not found: " + "z " * 25
    assert one_chat_line(long_one, "Map not found.") == "Map not found."
    assert one_chat_line("No stats for café", "No stats.") == "No stats."


# ---- the server manager knows its own lines ---------------------------------

def test_own_lines_are_marked_and_kept_from_the_vote_counter():
    manager = ServerManager()
    raw = []
    manager.set_raw_chat_callback(raw.append)
    manager._remember_own_chat(("fmj-badgerlove: !1", "Welcome Kestrel"))
    manager._apply_chat(("Kestrel: hello", "fmj-badgerlove: !1", "Welcome Kestrel"))
    marks = [(m["text"], bool(m.get("own"))) for m in manager.chat_messages]
    assert marks == [
        ("Kestrel: hello", False),
        ("fmj-badgerlove: !1", True),
        ("Welcome Kestrel", True),
    ]
    assert raw == ["Kestrel: hello"]
    # the next poll still has them: same marks, no new messages
    manager._apply_chat(("Kestrel: hello", "fmj-badgerlove: !1", "Welcome Kestrel", "Kestrel: !1"))
    assert raw[-1] == "Kestrel: hello\nKestrel: !1"
    assert [bool(m.get("own")) for m in manager.chat_messages] == [False, True, True, False]


def test_a_record_is_used_once_and_a_players_line_never_matches_it():
    manager = ServerManager()
    manager._remember_own_chat(("hello there",))
    manager._apply_chat(("Bob: hello there", "hello there", "hello there"))
    assert [bool(m.get("own")) for m in manager.chat_messages] == [False, True, False]


def test_colour_tags_and_spacing_do_not_hide_an_own_line():
    manager = ServerManager()
    manager._remember_own_chat(("<c00ff00>Welcome  Kestrel",))
    manager._apply_chat(("Welcome Kestrel",))
    assert manager.chat_messages[-1].get("own") is True


def test_send_chat_and_raw_chat_send_are_remembered():
    manager = ServerManager()
    try:
        manager.send_chat("Map not found: " + "z " * 25 + "fmj-badgerlove: !ban x")
    except Exception:
        pass                       # not connected: only the record matters here
    try:
        manager.execute_raw("CHAT SEND Hello from the console")
    except Exception:
        pass
    remembered = [text for text, _ in manager._own_chat]
    assert remembered[-1] == "Hello from the console"
    assert len(remembered) == 3                 # two pieces of the reply + the raw line
    assert remembered[1].endswith("fmj-badgerlove - !ban x")


# ---- the mods tab -------------------------------------------------------------

class FakeServer:
    def __init__(self):
        self.players = [
            {"name": name, "id": number, "team": 1, "revision": 7}
            for number, name in enumerate(("Ham", "FMJ-BadgerLove", "Griefer"), 3)
        ]
        self.missions = []
        self.mission_entries = []

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


class Rig:
    def __init__(self, folder):
        self.rt = DesktopRuntime.isolated(folder)
        with open(self.rt.path("wolfrat_mods.json"), "w") as f:
            json.dump({"mods": ["FMJ-BadgerLove", "Ham"],
                       "mod_ranks": {"FMJ-BadgerLove": ADMIN}}, f)
        self.tab = ModsTab(FakeServer(), MissionsStore(self.rt), self.rt)
        self.said, self.actions = [], []
        self.tab._send_mod_chat = lambda msg, ctx="": self.said.append(msg)
        self.tab._submit_mod_action = lambda op, **kw: self.actions.append(kw["context"])
        self.tab.update_chat([])           # the first batch is history

    def chat(self, *lines, own=False):
        start = len(self.tab._seen_chat_ids) + 100
        batch = [{"id": start + n, "text": line, "raw": line} for n, line in enumerate(lines)]
        if own:
            for message in batch:
                message["own"] = True
        self.tab.update_chat(batch)


def test_console_lines_and_own_lines_are_never_commands(tmp_path):
    rig = Rig(tmp_path)
    rig.chat("[ADMIN] !ban Griefer", "[ADMIN] !next")
    rig.chat("fmj-badgerlove: !ban Griefer", own=True)
    assert rig.actions == []
    rig.chat("FMJ-BadgerLove: !ban Griefer")           # the real Dale still can
    assert rig.actions == ["Ban Griefer"]


def test_a_player_called_web_admin_gets_nothing(tmp_path):
    rig = Rig(tmp_path)
    for name in ("web_admin", "Web_Admin", "WEB_ADMIN"):
        rig.chat(f"{name}: !ban Ham", f"{name}: !next")
    assert rig.actions == [] and rig.said == []


def test_no_piece_of_a_reply_runs_anything(tmp_path):
    """Even when a line is not recognised as WolfRAT's own (another WolfRAT
    reading it, say), no piece of a reply is a command."""
    rig = Rig(tmp_path)
    for typed in TRICKY_LINES:
        for piece in _split_chat_message(f"Map not found: {typed}"):
            rig.tab._check_mod_command(piece)
    assert rig.actions == []


def test_phone_commands_run_as_web_admin_even_in_manual_mode(tmp_path):
    rig = Rig(tmp_path)
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)
    manual_mode.state.choose(MANUAL)
    assert manual_mode.quiet()
    rig.chat("FMJ-BadgerLove: !kick Griefer")          # chat commands: off in Manual
    assert rig.actions == []
    assert rig.tab.run_web_command("!kick Griefer") == {"ok": True, "command": "!kick"}
    assert rig.actions == ["Kick Griefer"]


def test_phone_command_answers(tmp_path):
    rig = Rig(tmp_path)
    assert rig.tab.run_web_command("!nope")["error"] == "Unknown command: !nope"
    assert "players in the game" in rig.tab.run_web_command("!yes")["error"]
    assert "player name" in rig.tab.run_web_command("!kd")["error"]
    assert rig.tab.run_web_command("hello")["ok"] is False
    assert rig.actions == [] and rig.said == []


def test_chat_bot_does_not_pin_own_lines_on_players(qtbot, tmp_path, monkeypatch):
    submitted = []
    monkeypatch.setattr(app_module, "submit_admin", lambda owner, op, *a, **k: submitted.append(a))

    class BotServer(FakeServer):
        def set_setting(self, *a):
            pass

        def refresh_chat(self):
            pass

        def announce(self, msg):
            pass

    tab = ChatBotTab(BotServer(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab.bad_words = {"badword": "Warn"}
    tab.update_chat([])
    tab.update_chat([{"id": 1, "text": "Griefer badword", "raw": "x", "own": True}])
    assert submitted == []
    tab.update_chat([{"id": 2, "text": "Griefer: badword", "raw": "y"}])
    assert len(submitted) == 1


# ---- the web server -------------------------------------------------------------

class WebManager:
    is_connected = True
    players = []
    player_entries = ()
    chat_messages = []
    game_state = {}
    missions = []
    mission_entries = ()
    available_maps_data = ""
    game_settings = {}

    def __init__(self):
        self.sent = []

    def send_chat(self, line):
        self.sent.append(line)
        done = Future()
        done.set_result(CommandResult(operation="chat", replies=("OK - Chat sent.",),
                                      accepted=True, verified=True))
        return done


def web_with(answer=None):
    manager = WebManager()
    server = WolfWebServer(manager, host="127.0.0.1", port=0)
    asked = []

    def runner(text):
        asked.append(text)
        done = Future()
        done.set_result(answer or {"ok": True, "command": text.split()[0]})
        return done

    server.on_command = runner
    return server, manager, asked


def test_typed_commands_go_to_wolfrat_not_to_chat():
    server, manager, asked = web_with()
    for typed in ("!next", "[ADMIN] !next", "  [ADMIN]   !next  "):
        payload = asyncio.run(server._phone_message(typed))
        assert payload == {"ok": True, "accepted": True, "replies": ["Run by WolfRAT"],
                           "command": "!next"}
    assert asked == ["!next"] * 3
    assert manager.sent == []


def test_a_refused_command_tells_the_phone_why():
    server, manager, _ = web_with({"ok": False, "command": "!nope", "error": "Unknown command: !nope"})
    payload = asyncio.run(server._phone_message("!nope"))
    assert payload["ok"] is False and payload["error"] == "Unknown command: !nope"
    server.on_command = None
    assert "WolfRAT window" in asyncio.run(server._phone_message("!next"))["error"]
    assert manager.sent == []


def test_phone_chat_gets_the_prefix_once_and_must_fit_one_line():
    server, manager, asked = web_with()
    asyncio.run(server._phone_message("hello all"))
    asyncio.run(server._phone_message("[ADMIN] hello again"))
    asyncio.run(server._phone_message("[ADMIN]s rock"))
    assert manager.sent == ["[ADMIN] hello all", "[ADMIN] hello again", "[ADMIN] [ADMIN]s rock"]
    manager.sent.clear()
    room = ac.chat_limit() - len("[ADMIN] ")
    assert room == 51
    asyncio.run(server._phone_message("x" * room))
    with pytest.raises(ValueError, match="51 characters"):
        asyncio.run(server._phone_message("x" * (room + 1)))
    ac.set_extended_chat(True)
    asyncio.run(server._phone_message("y" * 110))
    assert manager.sent[-1] == "[ADMIN] " + "y" * 110
    assert asked == []
    assert server._build_state()["chat_max"] == 110


def test_new_token_ends_a_websocket_on_its_next_message():
    from aiohttp import WSMsgType
    from aiohttp.test_utils import TestClient, TestServer

    async def scenario():
        server, manager, asked = web_with()
        server.set_auth("admin", "token-A")
        key, _device = server.create_device("test phone")
        client = TestClient(TestServer(server.application))
        await client.start_server()
        try:
            ws = await client.ws_connect(f"/ws?key={key}")
            first = await ws.receive_json()
            assert first["type"] == "state"
            await ws.send_json({"type": "command", "command": "SET ServerPassword x"})
            refused = await ws.receive_json()
            assert refused["type"] == "error"
            await ws.send_json({"type": "chat", "message": "!next"})
            ran = await ws.receive_json()
            assert ran["type"] == "chat_result" and ran["ok"] is True
            server.set_auth("admin", "token-B")          # Regenerate Token
            await ws.send_json({"type": "chat", "message": "!next"})
            closing = await ws.receive()
            assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
            assert ws.close_code == 4001
            assert asked == ["!next"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_regenerate_closes_connections_on_a_running_server():
    import socket
    import time
    from aiohttp import ClientSession

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = WolfWebServer(WebManager(), host="127.0.0.1", port=port)
    server.set_auth("admin", "token-A")
    key, _device = server.create_device("test phone")
    assert server.start().result(timeout=5).ok

    async def scenario():
        async with ClientSession() as session:
            ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?key={key}")
            assert (await ws.receive_json())["type"] == "state"
            await asyncio.get_running_loop().run_in_executor(
                None, server.set_auth, "admin", "token-B")
            message = await asyncio.wait_for(ws.receive(), timeout=5)
            return message

    try:
        deadline = time.monotonic() + 5
        while not server.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
        message = asyncio.run(scenario())
    finally:
        server.stop()
    from aiohttp import WSMsgType
    assert message.type == WSMsgType.CLOSE and message.data == 4001, message
    assert message.extra == "token changed"
