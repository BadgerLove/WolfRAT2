"""Manual mode (Dale, 2026-11-14).

A complaint of double messages: a WolfRAT on the server PC and a second one
connected from another PC both announced the same things.  The admin protocol
has no way for one WolfRAT to see another, so a WolfRAT that connects from
another PC asks once per server, and in Manual mode it sends nothing the
admin did not click.  Admins who were afraid to start WolfRAT on someone
else's server ("it fires up and starts doing stuff") get the same switch.
"""

import json
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wolfrat.app as app_module  # noqa: E402
from wolfrat import ban_rules as br  # noqa: E402
from wolfrat import manual_mode  # noqa: E402
from wolfrat import map_weapons as mw  # noqa: E402
from wolfrat import vote_rules  # noqa: E402
from wolfrat.admin_commands import GameSettings  # noqa: E402
from wolfrat.manual_mode import FULL, MANUAL, UNDECIDED, ManualMode  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402

from tests.test_bans_tab import Rig  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture(autouse=True)
def clean_state():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


def go_manual():
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)
    manual_mode.state.choose(MANUAL)


# ---- the state ----------------------------------------------------------------

def test_a_wolfrat_that_never_connected_is_full():
    assert ManualMode().mode == FULL and not ManualMode().quiet()


def test_remote_server_is_quiet_until_the_admin_answers(tmp_path):
    state = ManualMode()
    state.configure(tmp_path / "wolfrat_manual_mode.json")
    state.begin_connect("77.68.4.198", 4000)
    assert state.mode == UNDECIDED and state.quiet()         # before the socket opens
    assert state.resolve(server_on_this_pc=False) is True    # ask
    assert state.quiet()                                     # still quiet while the question is up
    state.choose(FULL)
    assert not state.quiet()


def test_server_on_this_pc_is_never_asked(tmp_path):
    state = ManualMode()
    state.configure(tmp_path / "wolfrat_manual_mode.json")
    state.begin_connect("127.0.0.1", 4000)
    assert state.resolve(server_on_this_pc=True) is False
    assert state.mode == FULL
    assert not (tmp_path / "wolfrat_manual_mode.json").exists()      # nothing saved, nothing to undo
    state.begin_connect("127.0.0.1", 4000)                   # map change: reconnect
    assert state.mode == FULL                                # no quiet gap on a reconnect


def test_the_answer_is_kept_per_server(tmp_path):
    path = tmp_path / "wolfrat_manual_mode.json"
    state = ManualMode()
    state.configure(path)
    state.begin_connect("Game.Example.com", 4000)
    state.resolve(False)
    state.choose(MANUAL)
    assert json.loads(path.read_text()) == {"servers": {"game.example.com:4000": "manual"}}

    again = ManualMode()                                     # WolfRAT restarted
    again.configure(path)
    again.begin_connect("game.example.com", 4000)
    assert again.mode == MANUAL                              # applied before the socket opens
    assert again.resolve(False) is False                     # not asked twice
    again.begin_connect("game.example.com", 4001)            # another server on that host
    assert again.mode == UNDECIDED
    again.begin_connect("game.example.com", 4000)
    again.choose(FULL)
    assert ManualMode_from(path).saved_for("game.example.com", 4000) == FULL


def ManualMode_from(path):
    class Reader(ManualMode):
        def saved_for(self, host, port):
            self.begin_connect(host, port)
            return self.saved()
    state = Reader()
    state.configure(path)
    return state


def test_tests_and_the_smoke_run_are_never_left_waiting(tmp_path):
    state = ManualMode()
    state.configure(tmp_path / "m.json")
    state.begin_connect("77.68.4.198", 4000, can_ask=False)
    assert state.mode == FULL


def test_a_bad_file_is_ignored(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json")
    state = ManualMode()
    state.configure(path)
    state.begin_connect("a", 1)
    assert state.mode == UNDECIDED
    path.write_text(json.dumps({"servers": {"a:1": "sideways"}}))
    state.configure(path)
    state.begin_connect("a", 1)
    assert state.mode == UNDECIDED


def test_skip_says_why_once_per_feature_per_connection(tmp_path):
    lines = []
    state = ManualMode()
    state.configure(tmp_path / "m.json", log=lines.append)
    assert state.skip("Welcome messages") is False and lines == []      # Full: nothing held back
    state.begin_connect("a", 1)
    state.resolve(False)
    assert state.skip("Welcome messages") and state.skip("Welcome messages")
    assert lines == ["[MANUAL MODE] Welcome messages: not done, waiting for your answer."]
    state.choose(MANUAL)
    assert state.skip("Welcome messages") and state.skip("Killing sprees")
    assert lines[1:] == ["[MANUAL MODE] Welcome messages: not done, this WolfRAT is in Manual mode.",
                         "[MANUAL MODE] Killing sprees: not done, this WolfRAT is in Manual mode."]


# ---- sprees and first blood -----------------------------------------------------

class SpreeServer:
    players = []
    is_connected = True

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass

    def refresh_players(self, quiet=False):
        pass


class Messages:
    _kd_enabled = False


@pytest.fixture
def sprees(qtbot, tmp_path, monkeypatch):
    said = []

    def submit(owner, op, on_success=None, context="", *a, **k):
        said.append(op.__defaults__[0])
        if on_success is not None:
            on_success(None)

    monkeypatch.setattr(app_module, "submit_admin", submit)
    tab = app_module.SpreeTab(SpreeServer(), Messages(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._first_blood_enabled = True
    tab._spree_enabled = True
    tab._spree_thresholds = {3: "{player} is on a spree"}
    tab.said = said
    return tab


def p(pid, name, kills, deaths=0):
    return {"id": str(pid), "name": name, "kills": str(kills), "deaths": str(deaths)}


def test_manual_mode_announces_no_spree_and_no_first_blood(sprees):
    go_manual()
    sprees.on_missions_updated(["0: TD-Pink.bms - (1x) () () <> <CURRENT MISSION>"])
    sprees.check_sprees([p(3, "Dale", 0), p(4, "Ham", 0)])
    sprees.check_sprees([p(3, "Dale", 1), p(4, "Ham", 0, 1)])
    sprees.check_sprees([p(3, "Dale", 4), p(4, "Ham", 0, 4)])
    assert sprees.said == []
    assert sprees._player_stats["3"]["streak"] == 4          # still counted: the tab keeps its picture
    assert not sprees._first_blood_pending                   # nothing left hanging


def test_switching_back_to_full_does_not_say_a_stale_first_blood(sprees):
    go_manual()
    sprees.on_missions_updated(["0: TD-Pink.bms - (1x) () () <> <CURRENT MISSION>"])
    sprees.check_sprees([p(3, "Dale", 0), p(4, "Ham", 0)])
    sprees.check_sprees([p(3, "Dale", 1), p(4, "Ham", 0, 1)])           # first blood happened, unsaid
    manual_mode.state.choose(FULL)
    sprees.check_sprees([p(3, "Dale", 1), p(4, "Ham", 1, 1)])           # Ham's first kill, the map's second
    assert [line for line in sprees.said if "blood" in line.lower()] == []
    sprees.check_sprees([p(3, "Dale", 1, 1), p(4, "Ham", 4, 1)])        # sprees are back
    assert sprees.said == ["Ham is on a spree"]


# ---- welcome and timed messages -------------------------------------------------

class MessageServer:
    def _log(self, m):
        pass

    def announce(self, m):
        pass


@pytest.fixture
def messages(qtbot, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "submit_admin",
                        lambda owner, op, on_success=None, context="", *a, **k: sent.append(context))
    tab = app_module.MessagesTab(MessageServer(), None, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._welcome_enabled = True
    tab.sent = sent
    return tab


def test_manual_mode_welcomes_nobody_now_or_after_switching_back(messages):
    go_manual()
    messages.check_new_players([{"name": "Dale"}, {"name": "Ham"}])
    assert messages.welcome_log.count() == 0                  # nothing queued
    manual_mode.state.choose(FULL)
    messages.check_new_players([{"name": "Dale"}, {"name": "Ham"}])
    assert messages.welcome_log.count() == 0                  # they were already here
    messages.check_new_players([{"name": "Dale"}, {"name": "Ham"}, {"name": "Newbie"}])
    assert messages.welcome_log.count() == 1                  # a real new joiner in Full mode


def test_players_seen_while_the_question_is_up_are_welcomed_if_the_answer_is_full(messages):
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)                           # the question is on screen
    messages.check_new_players([{"name": "Dale"}])
    assert messages.welcome_log.count() == 0 and "Dale" not in messages._seen_players
    manual_mode.state.choose(FULL)                            # 'this is the only WolfRAT'
    messages.check_new_players([{"name": "Dale"}])
    assert messages.welcome_log.count() == 1                  # as a remote-only admin has today


def test_a_welcome_already_waiting_is_dropped_when_manual_mode_starts(messages):
    messages._send_welcome("Welcome Dale", "Dale")
    assert messages.sent == ["Send welcome to Dale"]
    go_manual()
    messages._send_welcome("Welcome Ham", "Ham")
    assert messages.sent == ["Send welcome to Dale"]


def test_timed_messages_and_the_where_from_line_stay_unsent(messages):
    messages._recurring_messages = ["Visit fmj-squad.com"]
    go_manual()
    messages._send_next_recurring()
    messages._send_country("Dale joined from Scotland")
    assert messages.sent == []
    manual_mode.state.choose(FULL)
    messages._send_next_recurring()
    assert messages.sent == ["Send recurring message"]


# ---- chat bot ----------------------------------------------------------------------

class ChatServer:
    def __init__(self):
        self.players = [{"name": "Griefer", "id": 5, "team": 1, "revision": 3}]
        self.mission_entries = []
        self.swap_guard = None

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass


@pytest.fixture
def chatbot(qtbot, tmp_path, monkeypatch):
    submitted = []
    monkeypatch.setattr(app_module, "submit_admin",
                        lambda owner, op, on_success=None, context="", on_failure=None, **kw:
                        submitted.append(context))
    tab = app_module.ChatBotTab(ChatServer(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab.auto_swap_cb.setChecked(True)
    tab.trigger_input.setText("!switch")
    tab.bad_words = {"badword": "Kick"}
    tab.spam_cb.setChecked(True)
    tab.spam_msg_spin.setValue(tab.spam_msg_spin.minimum())
    tab.update_chat([])                             # first batch is history
    tab.submitted = submitted
    return tab


def test_manual_mode_shows_chat_but_swaps_filters_and_kicks_nobody(chatbot):
    go_manual()
    chatbot.update_chat([{"id": 1, "text": "Griefer: !switch", "time": "20:00"},
                         {"id": 2, "text": "Griefer: badword", "time": "20:00"},
                         {"id": 3, "text": "Griefer: spam", "time": "20:00"},
                         {"id": 4, "text": "Griefer: spam", "time": "20:00"},
                         {"id": 5, "text": "Griefer: spam", "time": "20:00"}])
    assert chatbot.submitted == []
    shown = chatbot.chat_display.toPlainText()
    assert "Griefer: !switch" in shown and "Griefer: badword" in shown      # the log still works
    chatbot._send_automod_announcement("Griefer was kicked")
    assert chatbot.submitted == []


def test_full_mode_chat_bot_still_acts(chatbot):
    chatbot.update_chat([{"id": 1, "text": "Griefer: !switch", "time": "20:00"}])
    assert "Auto-swap Griefer" in chatbot.submitted


def test_the_chat_box_still_sends_what_the_admin_types(chatbot):
    go_manual()
    chatbot.chat_input.setText("hello from the remote admin")
    chatbot._send_chat()
    assert chatbot.submitted == ["Send admin chat"]


# ---- moderator chat commands --------------------------------------------------------

class ModServer:
    def __init__(self):
        self.players = [{"name": n, "id": i, "team": 1, "revision": 7}
                        for i, n in enumerate(("Ham", "Griefer"), 3)]
        self.missions, self.mission_entries = [], []

    def _log(self, msg):
        pass

    def send_chat(self, msg):
        pass

    def swaps_blocked(self):
        return False


@pytest.fixture
def mods(qtbot, tmp_path):
    rt = DesktopRuntime.isolated(tmp_path)
    with open(rt.path("wolfrat_mods.json"), "w") as f:
        json.dump({"mods": ["Ham"]}, f)
    tab = app_module.ModsTab(ModServer(), app_module.MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    tab.ran = []
    tab._check_mod_command = tab.ran.append
    tab.update_chat([])                             # first batch is history
    return tab


def test_manual_mode_leaves_chat_commands_to_the_other_wolfrat(mods):
    go_manual()
    line = {"id": 1, "text": "Ham: !kick Griefer"}
    mods.update_chat([line])
    assert mods.ran == []
    manual_mode.state.choose(FULL)
    mods.update_chat([line])                                  # same line, still in the chat window
    assert mods.ran == []                                     # an old command is not run late
    mods.update_chat([line, {"id": 2, "text": "Ham: !kick Griefer"}])
    assert mods.ran == ["Ham: !kick Griefer"]


def test_a_player_vote_running_when_manual_mode_starts_changes_no_map(mods):
    sent = []
    mods._submit_mod_action = lambda op, **kw: sent.append(kw["context"])
    mods._skip_active = mods._vote_active = True
    go_manual()
    mods._start_skip_transition("Skipping map...", "skip passed")
    mods._start_vote_transition("Switching...", "vote passed")
    assert sent == []
    assert not mods._skip_active and not mods._vote_active


# ---- map voting ---------------------------------------------------------------------------

class VoteServer:
    def __init__(self):
        self.players = [{"id": i, "name": f"P{i}"} for i in range(1, 9)]
        self.player_entries = ()
        self.game_settings = GameSettings({})

    def set_raw_chat_callback(self, cb):
        pass


class Missions:
    _rotation_maps = ["TK-One.bms", "AS-Two.npj", "FB-Three.npj", "DM-Four.bms", "CTF-Five.bms"]

    def _find_display_name(self, f):
        return f

    def _mission_entry_at(self, i):
        return None


@pytest.fixture
def voting(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "wire_log", lambda *_: None)
    tab = app_module.MapVotingTab(VoteServer(), Missions(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    tab._tick_timer.stop()
    tab.enable_cb.setChecked(True)
    tab.min_players_spin.setValue(tab.min_players_spin.minimum())
    tab.started = []
    tab._start_vote = lambda: tab.started.append(True)
    return tab


def fire():
    return vote_rules.Decision(True, "5 minutes left", "Status: vote due")


def test_full_mode_starts_the_automatic_vote(voting):
    assert voting._act_on_auto_decision(fire()) is True and voting.started == [True]


def test_manual_mode_starts_no_automatic_vote_and_says_so(voting):
    go_manual()
    assert voting._act_on_auto_decision(fire()) is False
    assert voting.started == []
    assert "Manual mode" in voting.status_lbl.text()
    assert voting.enable_cb.isChecked()                       # the admin's tick is left alone


# ---- bans -------------------------------------------------------------------------------

def test_manual_mode_kicks_nobody_off_the_ban_list_and_says_so_on_a_new_ban(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=rig.now))
    go_manual()
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    assert rig.punts == [] and rig.said == []
    rig.tab.ban_now("Griefer", reason="griefing")
    assert any("Manual mode" in line for line in rig.logged)
    manual_mode.state.choose(FULL)
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    assert [name for name, _why in rig.punts] == ["Troll"]    # Full again: the list is kept


def test_manual_mode_ban_button_still_kicks_the_player_you_picked(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    go_manual()
    rig.tab.online_table.selectRow(1)
    rig.tab.online_reason.setText("griefing")
    rig.tab._ban_selected("both")
    assert rig.punts == [("Troll", "Banned: griefing")]       # your click, done


# ---- map weapons ----------------------------------------------------------------------

def test_manual_mode_leaves_the_weapons_alone(tmp_path):
    asked = []
    keeper = mw.MapWeaponsKeeper(tmp_path / "wolfrat_map_weapons.json",
                                 list_weapons=lambda: [], set_all=lambda mode: None,
                                 set_one=lambda entry, mode: None)
    keeper.config.enabled = True
    keeper._ask = lambda name, reason: asked.append((name, reason))
    try:
        go_manual()
        keeper.on_map("DM-Nuke130.bms")
        keeper.next_map = "TD-Pink.bms"
        keeper.round_over()
        assert asked == [] and keeper.current_map == "DM-Nuke130.bms"
        assert keeper.steps_before_cycle("TD-Pink.bms", [object()]) == []
        manual_mode.state.choose(FULL)
        keeper.on_map("DM-Nuke130.bms")
        assert asked == [("DM-Nuke130.bms", "new map")]
    finally:
        keeper.close()
