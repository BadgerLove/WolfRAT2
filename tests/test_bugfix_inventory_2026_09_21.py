"""Regression tests for the 2026-09-21 code-inventory bug sweep.

Each test pins one fixed defect so it cannot silently come back:

1. ChatBot bad-words list persists across restarts.
2. ChatBot anti-spam seconds window persists across restarts.
3. Missions far-right column: stub handlers gone, reorder reachable
   (Edit Order / Shuffle since 2026-11-21).
4. ServerTab: password masked, no duplicate saves, port default consistent.
5. Mods !vote: dead cooldown variable removed (no vote limit wanted).
6. Settings: ping boxes debounce, dead handlers removed.
7. Messages: log group visible, tracked-count hint refreshes.
8. Auto-updater batch no longer keys off a hard-coded exe image name.
"""

import json
import time

import pytest
from PyQt6.QtWidgets import QLineEdit

from wolfrat.app import (
    ChatBotTab,
    MessagesTab,
    MissionsStore,
    MissionsTab,
    ModsTab,
    ServerTab,
    SettingsTab,
    LogSignals,
    _build_update_batch,
)
from wolfrat.runtime import DesktopRuntime


class FakeServer:
    def __init__(self):
        self.players = []
        self.mission_entries = []
        self.logs = []
        self.settings_sent = []

    def _log(self, msg):
        self.logs.append(msg)

    def set_setting(self, key, val):
        self.settings_sent.append((key, val))

    def refresh_chat(self):
        pass

    def send_chat(self, msg):
        pass

    def announce(self, msg):
        pass

    # No-op hooks wired by MissionsTab during construction.
    def refresh_available_maps(self, *a, **k):
        pass

    def refresh_missions(self, *a, **k):
        pass

    def shuffle_teams(self, *a, **k):
        pass

    def add_mission(self, *a, **k):
        pass


class FakeStats:
    def __init__(self, count=0):
        self._count = count

    def get_player_count(self):
        return self._count

    def get_player(self, name):
        return None


# --------------------------------------------------------------------------
# Bugs 1 & 2 - ChatBot persistence
# --------------------------------------------------------------------------

def test_chatbot_bad_words_persist_across_restart(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(tab)

    tab.bad_word_input.setText("badword")
    tab.bad_word_action.setCurrentText("Kick")
    tab._add_bad_word()

    saved = json.loads((tmp_path / "wolfrat_chat.json").read_text())
    assert saved["bad_words"] == {"badword": "Kick"}

    reopened = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(reopened)
    assert reopened.bad_words == {"badword": "Kick"}
    assert reopened.bad_words_list.count() == 1


def test_chatbot_remove_bad_word_persists(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(tab)
    tab.bad_word_input.setText("foo")
    tab._add_bad_word()
    tab.bad_words_list.setCurrentRow(0)
    tab._remove_bad_word()

    saved = json.loads((tmp_path / "wolfrat_chat.json").read_text())
    assert saved["bad_words"] == {}

    reopened = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(reopened)
    assert reopened.bad_words == {}


def test_chatbot_spam_seconds_window_persists(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(tab)
    tab.spam_time_spin.setValue(9)

    saved = json.loads((tmp_path / "wolfrat_chat.json").read_text())
    assert saved["spam_seconds"] == 9

    reopened = ChatBotTab(FakeServer(), rt)
    qtbot.addWidget(reopened)
    assert reopened.spam_time_spin.value() == 9


# --------------------------------------------------------------------------
# Bug 3 - Missions far-right column
# --------------------------------------------------------------------------

def test_missions_stub_handlers_removed():
    assert not hasattr(MissionsTab, "_cancel_clicked")
    assert not hasattr(MissionsTab, "_review_event_log")
    # Reorder is reachable: since 2026-11-21 through Edit Order / Shuffle,
    # which replaced the table-only Move Up / Move Down buttons (they never
    # reached the server). Full coverage: tests/test_map_order.py.
    assert hasattr(MissionsTab, "_open_order_editor")
    assert not hasattr(MissionsTab, "_move_up")
    assert not hasattr(MissionsTab, "_move_down")


def test_missions_edit_order_button_is_in_the_far_right_column(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = MissionsTab(FakeServer(), MissionsStore(rt), rt)
    qtbot.addWidget(tab)

    labels = [b.text() for b in tab.findChildren(type(tab.edit_order_btn))]
    assert "Edit Order /\nShuffle" in labels
    assert "Move Up" not in labels and "Move Down" not in labels


# --------------------------------------------------------------------------
# Bug 4 - ServerTab
# --------------------------------------------------------------------------

def test_servertab_password_is_masked(qtbot, tmp_path):
    tab = ServerTab(FakeServer(), LogSignals(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    assert tab.pass_input.echoMode() == QLineEdit.EchoMode.Password


def test_servertab_save_current_dedupes(qtbot, tmp_path):
    tab = ServerTab(FakeServer(), LogSignals(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab.host_input.setText("1.2.3.4")
    tab.port_input.setValue(4000)
    tab.user_input.setText("admin")
    tab._save_server()
    tab._save_server()
    assert tab.server_list.count() == 1


def test_servertab_autoconnect_port_default_matches_ui(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = ServerTab(FakeServer(), LogSignals(), rt)
    qtbot.addWidget(tab)
    # A saved record with no explicit port must fall back to the UI default 4000.
    (tmp_path / "wolfrat_servers.json").write_text(
        json.dumps({"last_server": {"host": "9.9.9.9", "user": "a", "password": "p"}})
    )
    assert tab._load_last_server() is True
    assert tab.port_input.value() == 4000


# --------------------------------------------------------------------------
# Bug 5 - Mods !vote has no cooldown
# --------------------------------------------------------------------------

def _mods_tab(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    tab = ModsTab(FakeServer(), MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    sent = []
    tab._send_mod_chat = lambda msg, ctx="": sent.append(msg)
    tab._vote_enabled = True
    tab._vote_active = False
    return tab, sent


def test_vote_has_no_cooldown(qtbot, tmp_path):
    """Dale's call (2026-09-21): !vote is NOT rate limited, unlike !skip.

    The old code wrote _vote_cooldown_until and never read it; the dead
    variable is gone rather than enforced.
    """
    tab, sent = _mods_tab(qtbot, tmp_path)
    tab.server.players = []  # fails the later 2-player gate, proving no earlier gate
    tab._check_mod_command("dale: !vote crossroads")
    assert not any("cooldown" in m.lower() for m in sent)
    assert any("2 players" in m for m in sent)
    tab._vote_transition_succeeded()
    assert not hasattr(tab, "_vote_cooldown_until")


# --------------------------------------------------------------------------
# Bug 6 - Settings tab
# --------------------------------------------------------------------------

def test_settings_dead_handlers_removed():
    assert not hasattr(SettingsTab, "_lock_server")
    assert not hasattr(SettingsTab, "_save_settings")
    assert not hasattr(SettingsTab, "_toggle_auto_refresh")


def test_settings_ping_boxes_debounce(qtbot, tmp_path):
    tab = SettingsTab(FakeServer(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._loading = False
    assert "MinPing" in tab._debounce_timers
    assert "MaxPing" in tab._debounce_timers

    tab.ping_min_val.setValue(250)
    # Debounced: queued for later send, not fired synchronously per keystroke.
    assert tab._pending_values.get("MinPing") == 250
    assert tab._debounce_timers["MinPing"].isActive()


# --------------------------------------------------------------------------
# Bug 7 - Messages tab
# --------------------------------------------------------------------------

def test_messages_log_is_visible(qtbot, tmp_path):
    tab = MessagesTab(FakeServer(), FakeStats(0), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    assert tab.log_text.isVisibleTo(tab)


def test_messages_tracked_count_refreshes(qtbot, tmp_path):
    stats = FakeStats(0)
    tab = MessagesTab(FakeServer(), stats, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    assert "Tracked: 0 players" in tab.kd_hint_label.text()
    stats._count = 7
    tab._refresh_kd_hint()
    assert "Tracked: 7 players" in tab.kd_hint_label.text()


# --------------------------------------------------------------------------
# Updater batch
# --------------------------------------------------------------------------

def test_update_batch_does_not_depend_on_exe_name():
    bat = _build_update_batch(r"C:\tmp\new.exe", r"C:\app\MyRenamed.exe", r"C:\app\_update.log")
    # The old hard-coded image-name wait is gone.
    assert "imagename eq WolfRAT2.exe" not in bat
    assert "WolfRAT2.exe" not in bat
    # It retries the move against the actual runtime path instead.
    assert ":retry" in bat and "goto retry" in bat
    assert r"move /y \"C:\tmp\new.exe\" \"C:\app\MyRenamed.exe\"".replace('\\"', '"') in bat
    # Bounded, so it can never loop forever.
    assert "geq 150" in bat
    # Still relaunches the (renamed) exe.
    assert r'start "" "C:\app\MyRenamed.exe"' in bat


# --------------------------------------------------------------------------
# Bug 8 - Web Admin shows the LAN address the phone needs
# --------------------------------------------------------------------------

def test_web_admin_urls_include_lan_address():
    from wolfrat.app import _web_admin_urls
    text = _web_admin_urls(8070, ["192.168.1.50"])
    assert "http://localhost:8070" in text
    assert "http://192.168.1.50:8070" in text


def test_web_admin_urls_without_lan_address():
    from wolfrat.app import _web_admin_urls
    text = _web_admin_urls(8070, [])
    assert "http://localhost:8070" in text
    assert "no network address" in text


def test_lan_ips_skip_loopback_and_link_local(qtbot):
    from wolfrat.app import _lan_ips
    for ip in _lan_ips():
        assert not ip.startswith(("127.", "169.254."))
