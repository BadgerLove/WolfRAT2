"""No team swaps on co-op maps (Dale, 2026-09-23: players used !switch to join
the bot team).  Every route into a swap is covered: !switch, mods' !swap /
!mixteams / !balanceteams, the Players tab buttons (Swap asks first), the web admin,
and ServerManager itself as the safety net."""

import asyncio
import json
import struct
import sys
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QMessageBox

import wolfrat.app as app_module
from wolfrat import coop_guard, web_server as web_server_module
from wolfrat.admin_commands import AdminOperation, PlayerEntry
from wolfrat.app import ChatBotTab, DesktopRuntime, MissionsStore, ModsTab
from wolfrat.bans_tab import BansTab
from wolfrat.coop_guard import CoopGuard, CoopSwapBlocked
from wolfrat.jo_players import GAME_TYPE_VA, LocalServerPlayers
from wolfrat.protocol import ServerManager
from wolfrat.web_server import WolfWebServer
from tests.test_server_manager import FakeSession
from tests.test_web_server import FakeRequest, FakeServerManager, FakeWeb

_app = QApplication.instance() or QApplication(sys.argv[:1])

COOP, COOP_ALT, AAS, TDM = 0x10020, 0x30020, 0x10010, 0x10000


# ---- the rule -----------------------------------------------------------------

def test_coop_values_come_from_the_engine_mask():
    assert coop_guard.is_coop(COOP) and coop_guard.is_coop(COOP_ALT)
    for value in (AAS, TDM, 0, 1, None):
        assert not coop_guard.is_coop(value)
    assert coop_guard.game_type_label(AAS) == "Advance & Secure"
    assert coop_guard.game_type_label(COOP_ALT) == "Co-op"
    assert coop_guard.game_type_label(0x12345) == "game type 0x12345"


def test_guard_is_on_by_default_and_blocks_only_co_op():
    mode = {"now": AAS}
    guard = CoopGuard(game_type_source=lambda: mode["now"])
    assert guard.enabled and not guard.blocks_swaps()
    guard.check()                                   # AAS: fine
    mode["now"] = COOP
    assert guard.blocks_swaps()
    with pytest.raises(CoopSwapBlocked):
        guard.check()
    guard.check(allow_coop=True)                    # the admin said yes
    guard.enabled = False
    assert not guard.blocks_swaps()


def test_server_on_another_pc_means_unknown_and_swaps_allowed():
    guard = CoopGuard()                             # no reader at all
    assert guard.game_type() is None and not guard.blocks_swaps()


def test_a_broken_reader_means_unknown_not_a_crash():
    def boom():
        raise OSError("process gone")
    guard = CoopGuard(game_type_source=boom)
    assert guard.game_type() is None and not guard.blocks_swaps()


# ---- the memory read ------------------------------------------------------------

class FakeMemory:
    def __init__(self, words):
        self.words = words

    def read(self, address, size):
        if address not in self.words:
            raise OSError("unreadable")
        return struct.pack("<I", self.words[address])

    def close(self):
        pass


def test_reader_returns_g_game_type(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    reader = LocalServerPlayers(find=lambda: [4242], open_memory=lambda pid: FakeMemory({GAME_TYPE_VA: COOP}))
    assert reader.read_game_type() == COOP
    none_running = LocalServerPlayers(find=lambda: [])
    assert none_running.read_game_type() is None


class ModeReader:
    def __init__(self, game_type, available=True):
        self.game_type, self.available, self.status = game_type, available, "fake"

    def read(self):
        return []

    def read_game_type(self):
        return self.game_type


def test_bans_tab_publishes_the_game_type_each_poll(tmp_path):
    reader = ModeReader(COOP)
    tab = BansTab(str(tmp_path), punt=lambda *a: None, announce=lambda *a: None, reader=reader)
    tab.on_players([])
    assert tab.game_type_now() == COOP
    reader.available = False                        # server went away: unknown, not stale co-op
    tab.on_players([])
    assert tab.game_type_now() is None


# ---- the safety net in ServerManager -------------------------------------------

def _manager(guard):
    manager = ServerManager(session_factory=FakeSession, verification_attempts=3, verification_delay=0)
    ok, _ = manager.connect("127.0.0.1", 4000, "admin", "pw")
    assert ok
    manager.swap_guard = guard
    return manager, manager._session


def test_server_refuses_every_swap_on_co_op_and_sends_nothing():
    manager, session = _manager(CoopGuard(game_type_source=lambda: COOP))
    for future in (manager.swap_player(12), manager.swap_and_kill(12, "Alice")):
        with pytest.raises(CoopSwapBlocked):
            future.result()
    for workflow in (manager.mix_teams(), manager.shuffle_teams()):
        assert workflow.messages == (coop_guard.REFUSAL,)
    assert [s.text for s in session.specs if s.mutating] == []


def test_server_swaps_normally_off_co_op_and_when_the_admin_insists():
    manager, session = _manager(CoopGuard(game_type_source=lambda: AAS))
    assert manager.swap_player(12).result().accepted
    assert AdminOperation.PLAYER_SWAPTEAM in [s.operation for s in session.specs]

    manager, session = _manager(CoopGuard(game_type_source=lambda: COOP))
    assert manager.swap_player(12, allow_coop=True).result().accepted
    assert AdminOperation.PLAYER_SWAPTEAM in [s.operation for s in session.specs]


# ---- !switch (Chat Bot tab) -----------------------------------------------------

class ChatServer:
    def __init__(self):
        self.players = [{"name": "Griefer", "id": 5, "team": 1, "revision": 3}]
        self.mission_entries = []
        self.swap_guard = None

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


def _chat_rig(qtbot, tmp_path, monkeypatch, game_type):
    submitted = []
    monkeypatch.setattr(app_module, "submit_admin",
                        lambda owner, op, on_success=None, context="", on_failure=None, **kw: submitted.append(context))
    tab = ChatBotTab(ChatServer(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab.coop_guard.game_type_source = lambda: game_type
    tab.update_chat([])                             # first batch is history
    return tab, submitted


def _say(tab, n, text):
    tab.update_chat([{"id": n, "text": text, "time": "20:00"}])


def test_switch_on_co_op_is_refused_once_per_minute(qtbot, tmp_path, monkeypatch):
    tab, submitted = _chat_rig(qtbot, tmp_path, monkeypatch, COOP)
    assert tab.server.swap_guard is tab.coop_guard   # the safety net sees the same setting
    _say(tab, 1, "Griefer: !switch")
    _say(tab, 2, "Griefer: !switch")
    assert submitted == ["Send auto-moderation announcement"]   # one polite no, no swap
    assert "refused, co-op map" in tab.chat_display.toPlainText()


def test_switch_still_works_off_co_op(qtbot, tmp_path, monkeypatch):
    tab, submitted = _chat_rig(qtbot, tmp_path, monkeypatch, AAS)
    _say(tab, 1, "Griefer: !switch")
    assert submitted == ["Auto-swap Griefer"]


def test_switch_works_on_co_op_when_blocking_is_turned_off(qtbot, tmp_path, monkeypatch):
    tab, submitted = _chat_rig(qtbot, tmp_path, monkeypatch, COOP)
    tab.coop_block_cb.setChecked(False)
    _say(tab, 1, "Griefer: !switch")
    assert submitted == ["Auto-swap Griefer"]


def test_co_op_setting_defaults_on_and_persists(qtbot, tmp_path, monkeypatch):
    tab, _ = _chat_rig(qtbot, tmp_path, monkeypatch, None)
    assert tab.coop_block_cb.isChecked()
    assert not hasattr(tab, "coop_always_cb")      # Dale: pointless next to the !switch tick
    tab.coop_block_cb.setChecked(False)
    saved = json.loads((tmp_path / "wolfrat_chat.json").read_text())
    assert saved["coop_block_swaps"] is False
    again = ChatBotTab(ChatServer(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(again)
    assert not again.coop_guard.enabled


def test_team_swaps_box_has_no_game_mode_line(qtbot, tmp_path, monkeypatch):
    # Dale: the box is the !switch trigger + the co-op tick, nothing else.
    tab, _ = _chat_rig(qtbot, tmp_path, monkeypatch, COOP)
    assert not hasattr(tab, "coop_status_lbl")


# ---- mods' commands --------------------------------------------------------------

class ModServer:
    def __init__(self, blocked):
        self.players = [{"name": n, "id": i, "team": 1, "revision": 7}
                        for i, n in enumerate(("Ham", "Griefer"), 3)]
        self.missions, self.mission_entries = [], []
        self._blocked = blocked

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass

    def swaps_blocked(self):
        return self._blocked

    def shuffle_teams(self):
        pass

    def mix_teams(self):
        pass


def _mods(tmp_path, blocked):
    rt = DesktopRuntime.isolated(tmp_path)
    with open(rt.path("wolfrat_mods.json"), "w") as f:
        json.dump({"mods": ["Ham"]}, f)
    tab = ModsTab(ModServer(blocked), MissionsStore(rt), rt)
    said, actions = [], []
    tab._send_mod_chat = lambda msg, ctx="": said.append(msg)
    tab._submit_mod_action = lambda op, **kw: actions.append(kw["context"])
    return tab, said, actions


def test_mods_cannot_swap_mix_or_balance_on_co_op(tmp_path, monkeypatch):
    workflows = []
    monkeypatch.setattr(app_module, "submit_team_workflow", lambda *a, **k: workflows.append(a[2]))
    tab, said, actions = _mods(tmp_path, blocked=True)
    for line in ("Ham: !swap Griefer", "Ham: !mixteams", "Ham: !balanceteams"):
        tab._check_mod_command(line)
    assert tab.run_web_command("!mixteams")["ok"] is True
    assert actions == [] and workflows == []
    assert said == [coop_guard.REFUSAL] * 4
    assert "refused, co-op map" in tab.mod_log.item(tab.mod_log.count() - 1).text()


def test_mods_swap_as_before_off_co_op(tmp_path, monkeypatch):
    workflows = []
    monkeypatch.setattr(app_module, "submit_team_workflow", lambda *a, **k: workflows.append(a[3]))
    tab, said, actions = _mods(tmp_path, blocked=False)
    tab._check_mod_command("Ham: !swap Griefer")
    tab._check_mod_command("Ham: !mixteams")
    assert actions == ["Swap Griefer"] and workflows == ["Moderator team mix"]


# ---- Players tab buttons + web admin ------------------------------------------------

class ButtonServer(FakeServerManager):
    def __init__(self, blocked):
        super().__init__()
        self._blocked = blocked
        self.calls = []

    def swaps_blocked(self):
        return self._blocked

    def _log(self, msg):
        pass

    def swap_player(self, player, allow_coop=False):
        self.calls.append(allow_coop)
        return super().swap_player(player)


def test_web_admin_refuses_a_swap_on_co_op():
    for blocked, expected in ((True, 409), (False, 200)):
        manager = ButtonServer(blocked)
        manager.player_entries = (PlayerEntry(server_id=7, name="Alice", team=1, revision=8),)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True
        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_player_action(
                FakeRequest({"pid": 7, "name": "Alice", "action": "swap"})))
        assert response["status"] == expected, response["payload"]
        if blocked:
            assert "co-op" in response["payload"]["error"] and manager.semantic_actions == []


def _players_tab(qtbot, tmp_path, blocked):
    from wolfrat.app import PlayersTab
    server = ButtonServer(blocked)
    server.players = [{"name": "Alice", "id": 7, "team": 1, "revision": 8}]
    tab = PlayersTab(server)
    qtbot.addWidget(tab)
    tab._get_selected_player_target = lambda: PlayerEntry(server_id=7, name="Alice", team=1, revision=8)
    tab._admin_futures.submit = lambda op, **kw: op()
    return tab, server


def test_swap_button_asks_first_on_co_op(qtbot, tmp_path, monkeypatch):
    answers = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.pop(0))
    tab, server = _players_tab(qtbot, tmp_path, blocked=True)
    answers.append(QMessageBox.StandardButton.No)
    tab._admin_action("swap")
    assert server.calls == []                       # said no: nothing sent
    answers.append(QMessageBox.StandardButton.Yes)
    tab._admin_action("swap")
    assert server.calls == [True]                   # said yes: the server lets this one through


def test_swap_button_does_not_ask_off_co_op(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked on a non-co-op map"))
    tab, server = _players_tab(qtbot, tmp_path, blocked=False)
    tab._admin_action("swap")
    assert server.calls == [False]


def test_balance_and_mix_buttons_explain_instead_of_moving_anyone(qtbot, tmp_path, monkeypatch):
    told, workflows = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda _w, title, text: told.append(title))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("should not get as far as asking"))
    monkeypatch.setattr(app_module, "submit_team_workflow", lambda *a, **k: workflows.append(a))
    tab, _server = _players_tab(qtbot, tmp_path, blocked=True)
    tab._mix_teams()
    tab._shuffle_teams()
    assert told == ["Balance Teams", "Mix Teams"] and workflows == []
