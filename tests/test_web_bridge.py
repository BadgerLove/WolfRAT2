"""The WolfRAT window's side of the phone page (wolfrat/web_bridge.py, 2026-11-23).

Each phone action runs through the desktop's own code on the Qt thread: the
Mods tab's kick/ban/warn path, the Bans tab's list, the Weather tab's
_start, the lag comp panel's own box and list, Manual mode's switch.  Real
tabs are used where the repo already has offscreen rigs for them.
"""

import os
import types
from concurrent.futures import Future

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import lag_comp as lc  # noqa: E402
from wolfrat import manual_mode  # noqa: E402
from wolfrat import web_bridge  # noqa: E402
from wolfrat.admin_commands import MissionEntry, PlayerEntry  # noqa: E402
from wolfrat.admin_session import CommandResult  # noqa: E402
from wolfrat.career import CareerStore, build_ranks  # noqa: E402
from wolfrat.ip_checks import Verdict  # noqa: E402

ALICE = PlayerEntry(server_id=7, name="Alice", team=1, kills=4, ping=120, revision=6)


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


def _done(operation="ok"):
    future = Future()
    future.set_result(CommandResult(operation=operation, replies=("OK",), accepted=True,
                                    verified=True))
    return future


class FakeServer:
    is_connected = True

    def __init__(self):
        self.calls = []
        self.private = []
        self.ready = True
        self.player_entries = (PlayerEntry(server_id=0, name="Host", team=0), ALICE)
        self.mission_entries = (
            MissionEntry(queue_index=0, filename="AAS_Doslin.bms", is_current=True),
            MissionEntry(queue_index=1, filename="TDM_X.bms", is_next=True),
        )
        self.game_settings = {"servername": "FMJ", "gametime": "12/30"}

    def punt_player(self, player, message=""):
        self.calls.append(("punt", player, message))
        return _done("punt")

    def warn_player(self, player, message="You have been warned!"):
        self.calls.append(("warn", player, message))
        return _done("warn")

    def _private_line(self, name, text):
        self.private.append((name, text))
        return name == "Alice"

    def _private_line_ready(self):
        return self.ready


class FakeLog:
    def __init__(self):
        self.lines = []

    def addItem(self, text):
        self.lines.append(text)


class FakeMods:
    """The Mods tab's _submit_mod_action, run at once (the real one queues
    the callbacks back to the Qt thread through QtAdminDispatcher)."""

    def __init__(self, fail=None):
        self.fail = fail
        self.submitted = []
        self.said = []
        self.mod_log = FakeLog()
        self._vote_active = False
        self._skip_active = False

    def _submit_mod_action(self, operation, *, context, announcement=None, log_message=None,
                           on_success=None, on_failure=None, policy=None):
        self.submitted.append({"context": context, "announcement": announcement,
                               "log_message": log_message})
        result = operation()
        if self.fail:
            on_failure(self.fail)
            return
        if log_message:
            self.mod_log.addItem(log_message)
        on_success(result.result())
        if announcement:
            self.said.append(announcement)


def make_bridge(qtbot=None, **parts):
    server = parts.pop("server", None) or FakeServer()
    window = types.SimpleNamespace(server=server, **parts)
    web = types.SimpleNamespace(on_desktop=None, desktop_state=None, pushes=0)
    web.broadcast_state = lambda: setattr(web, "pushes", web.pushes + 1)
    bridge = web_bridge.PhoneBridge(window, web)
    return bridge, window, web


def run(bridge, action, payload):
    answer = Future()
    bridge._run(action, payload, answer)
    assert answer.done(), "the answer should be settled by now"
    return answer.result()


# --- kick / ban / warn / message ------------------------------------------------


def test_kick_goes_through_the_mods_tab_and_announces_after(qtbot):
    mods = FakeMods()
    bridge, window, _web = make_bridge(mods_tab=mods)
    result = run(bridge, "kick", {"entry": ALICE, "reason": "camping", "device_name": "Pixel"})
    assert result == {"ok": True, "accepted": True, "replies": ["Alice was kicked"]}
    assert window.server.calls == [("punt", ALICE, "camping")]
    assert mods.said == ["Alice was kicked by an admin"]
    assert mods.submitted[0]["context"] == "Kick Alice"
    assert "Web Admin (Pixel) kicked Alice: camping" in mods.mod_log.lines[0]


def test_a_failed_kick_says_why_and_announces_nothing(qtbot):
    mods = FakeMods(fail="Kick Alice was accepted but not verified by retail")
    bridge, _window, _web = make_bridge(mods_tab=mods)
    result = run(bridge, "kick", {"entry": ALICE})
    assert result["ok"] is False and "not verified" in result["error"]
    assert mods.said == []


def test_ban_uses_wolfrats_own_list_with_the_length_then_kicks(qtbot, tmp_path):
    from tests.test_bans_tab import Rig
    rig = Rig(tmp_path)
    rig.poll(("Alice", "82.68.58.92"))
    mods = FakeMods()
    bridge, window, _web = make_bridge(mods_tab=mods, bans_tab=rig.tab)

    result = run(bridge, "ban", {"entry": ALICE, "reason": "cheating", "expiry": "7d",
                                 "device_name": "Pixel"})

    assert result["ok"] is True
    assert result["replies"] == ["Alice banned (name and IP, for 7 days)"]
    entries = {(entry.kind, entry.value): entry for entry in rig.tab.bans.entries}
    assert set(entries) == {("name", "Alice"), ("ip", "82.68.58.92")}
    for entry in entries.values():
        assert entry.expires_at == rig.now + 7 * 86400
        assert entry.added_by == "Web Admin (Pixel)"
    assert entries[("name", "Alice")].reason == "cheating"
    assert window.server.calls == [("punt", ALICE, "cheating")]
    assert mods.said == ["Alice was banned by an admin"]
    # Noted as punted: the next poll does not remove (and announce) them again.
    rig.poll(("Alice", "82.68.58.92"))
    assert rig.punts == []


def test_ban_lengths_come_from_the_bans_tab_and_others_are_refused(qtbot, tmp_path):
    from tests.test_bans_tab import Rig
    rig = Rig(tmp_path)
    rig.poll(("Alice", "82.68.58.92"))
    bridge, window, _web = make_bridge(mods_tab=FakeMods(), bans_tab=rig.tab)
    assert [item["id"] for item in web_bridge._ban_lengths()] == ["", "1h", "1d", "7d", "30d", "90d"]
    assert web_bridge._ban_lengths()[0] == {"id": "", "label": "Never"}
    result = run(bridge, "ban", {"entry": ALICE, "expiry": "2w"})
    assert result["ok"] is False and result["status"] == 400
    assert rig.tab.bans.entries == [] and window.server.calls == []


def test_ban_with_no_ip_known_is_by_name(qtbot, tmp_path):
    from tests.test_bans_tab import Rig
    rig = Rig(tmp_path)
    bridge, _window, _web = make_bridge(mods_tab=FakeMods(), bans_tab=rig.tab)
    result = run(bridge, "ban", {"entry": ALICE, "expiry": ""})
    assert result["replies"] == ["Alice banned (name only, no IP known, for ever)"]
    assert [(e.kind, e.value, e.expires_at) for e in rig.tab.bans.entries] == [
        ("name", "Alice", None)]


def test_warn_is_the_public_warning_line(qtbot):
    mods = FakeMods()
    bridge, window, _web = make_bridge(mods_tab=mods)
    result = run(bridge, "warn", {"entry": ALICE, "reason": "language"})
    assert result["ok"] is True
    assert window.server.calls == [("warn", ALICE, "language")]
    assert mods.said == []                 # the warning is the announcement
    refused = run(bridge, "warn", {"entry": ALICE, "reason": "café"})
    assert refused["status"] == 400 and len(window.server.calls) == 1


def test_private_message_through_the_helper(qtbot):
    mods = FakeMods()
    bridge, window, _web = make_bridge(mods_tab=mods)
    result = run(bridge, "message", {"entry": ALICE, "message": "please  swap", "device_name": "P"})
    assert result == {"ok": True, "accepted": True, "replies": ["Sent to Alice only"]}
    assert window.server.private == [("Alice", "[ADMIN] please swap")]
    assert "sent Alice a private line" in mods.mod_log.lines[0]

    assert run(bridge, "message", {"entry": ALICE, "message": "x" * 111})["status"] == 400
    assert run(bridge, "message", {"entry": ALICE, "message": "x" * 110})["ok"] is True
    assert run(bridge, "message", {"entry": ALICE, "message": "☃"})["status"] == 400
    assert run(bridge, "message", {"entry": ALICE, "message": ""})["status"] == 400
    gone = PlayerEntry(server_id=8, name="Ghost", team=1)
    assert run(bridge, "message", {"entry": gone, "message": "hi"})["status"] == 409
    window.server.ready = False
    assert run(bridge, "message", {"entry": ALICE, "message": "hi"})["status"] == 409


# --- weather -------------------------------------------------------------------


def _weather(qtbot, tmp_path, fail=None):
    from tests.test_weather import FakeServer as FakeGame
    from tests.test_weather_tab import make
    game = None if fail else FakeGame()
    tab, said, _attaches = make(qtbot, tmp_path, game, fail=fail)
    bridge, _window, _web = make_bridge(weather_tab=tab)
    return bridge, tab, said


def test_weather_preset_then_clear_like_the_buttons(qtbot, tmp_path):
    bridge, tab, said = _weather(qtbot, tmp_path)
    result = run(bridge, "weather", {"preset": "storm", "minutes": 5, "device_name": "Pixel"})
    assert result == {"ok": True, "accepted": True, "replies": ["Storm for 5 min."]}
    assert said == ["Weather: storm rolling in for 5 min."]
    state, usable, lightning = bridge._weather_state()
    assert usable is True and lightning is False
    assert state["summary"] == "Storm, 5 min left" and state["dynamic"] is False
    assert state["fog_m"] is not None

    result = run(bridge, "weather", {"preset": "clear"})
    assert result["ok"] is True and said[-1] == "The weather is clearing."
    assert tab._schedule.active is None
    assert bridge._weather_state()[0]["summary"] == "Map's own sky"


def test_fog_distance_and_holding_until_cleared(qtbot, tmp_path):
    bridge, tab, said = _weather(qtbot, tmp_path)
    result = run(bridge, "weather", {"fog_m": 80, "minutes": 0})
    assert result["replies"] == ["Fog 80 m until cleared."]
    assert tab._schedule.active.fog_metres == 80
    assert bridge._weather_state()[0]["summary"] == "Fog 80 m, until cleared"
    result = run(bridge, "weather", {"preset": "rain", "fog_m": 5000})
    assert tab._schedule.active.fog_metres == 1500          # clamped like !fog
    assert result["replies"][0].startswith("Rain with 1500 m fog")


def test_changing_weather_switch_and_bad_input(qtbot, tmp_path):
    bridge, tab, _said = _weather(qtbot, tmp_path)
    assert run(bridge, "weather", {"dynamic": True})["replies"] == ["Changing weather on."]
    assert tab.dynamic_page.enabled_cb.isChecked()
    assert bridge._weather_state()[0]["dynamic"] is True
    for payload in ({}, {"preset": "tornado"}, {"preset": "clear", "fog_m": 80},
                    {"fog_m": "thick"}, {"minutes": 999, "preset": "rain"},
                    {"dynamic": "yes"}, {"lightning": "sideways"}):
        assert run(bridge, "weather", payload)["status"] == 400, payload


def test_weather_without_the_server_on_this_pc(qtbot, tmp_path):
    bridge, tab, said = _weather(qtbot, tmp_path, fail="No jointops.exe is running on this PC.")
    result = run(bridge, "weather", {"preset": "storm"})
    assert result["ok"] is False and result["status"] == 409
    assert "No jointops.exe" in result["error"] and said == []
    assert bridge._weather_state() == (None, False, False)
    # The changing-weather switch is a setting: it works without a server.
    assert run(bridge, "weather", {"dynamic": True})["ok"] is True


def test_lightning_needs_the_server_add_on(qtbot, tmp_path):
    bridge, _tab, _said = _weather(qtbot, tmp_path)
    result = run(bridge, "weather", {"lightning": "near"})
    assert result["status"] == 409 and "Lightning is not set up" in result["error"]


# --- lag comp --------------------------------------------------------------------


def _lag_panel(qtbot, tmp_path, monkeypatch, saved=None):
    from tests.test_lag_comp_panel import _Mem, _switchable
    panel, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                          saved or {"on": False}, lc.State(lc.STOCK))
    panel._poll_now = lambda: panel.server_tick(_Mem(), writable=True)
    return panel, calls


def test_lag_comp_is_refused_without_a_server(qtbot, tmp_path, monkeypatch):
    panel, calls = _lag_panel(qtbot, tmp_path, monkeypatch)
    bridge, _window, _web = make_bridge(lag_comp_panel=panel)
    result = run(bridge, "lagcomp", {"enabled": True})
    assert result["status"] == 409 and calls == [] and panel._wish_on is False
    assert bridge._lag_comp_usable() is False


def test_lag_comp_switch_and_auto_tune_through_the_panel(qtbot, tmp_path, monkeypatch):
    panel, calls = _lag_panel(qtbot, tmp_path, monkeypatch)
    panel._poll_now()                      # a server is seen: the box comes alive
    bridge, _window, _web = make_bridge(lag_comp_panel=panel)
    assert bridge._lag_comp_usable() is True

    result = run(bridge, "lagcomp", {"enabled": True, "tune_mode": "on"})
    assert result["ok"] is True
    assert calls == ["on"] and panel._wish_on is True and panel.on_cb.isChecked()
    assert panel._settings.tune_mode == lc.TUNE_APPLY
    assert "Auto-tune per player: on." in result["replies"]
    state = bridge._lag_comp_state()
    assert state["enabled"] is True and state["tune_mode"] == "on" and state["running"] is True
    assert state["summary"]

    again = run(bridge, "lagcomp", {"enabled": True})
    assert "Lag compensation is already on." in again["replies"] and calls == ["on"]
    off = run(bridge, "lagcomp", {"enabled": False, "tune_mode": "watch"})
    assert off["ok"] is True and calls == ["on", "off"]
    assert panel._settings.tune_mode == lc.TUNE_WATCH and panel._wish_on is False
    for payload in ({}, {"enabled": "yes"}, {"tune_mode": "apply"}):
        assert run(bridge, "lagcomp", payload)["status"] == 400, payload


# --- Manual mode ------------------------------------------------------------------


def test_manual_mode_switch(qtbot):
    switched = []
    bridge, _window, _web = make_bridge(
        _manual_mode_switch=lambda mode: (switched.append(mode),
                                          manual_mode.state.choose(mode)))
    assert run(bridge, "mode", {"mode": "manual"})["status"] == 409      # not connected yet
    manual_mode.state.begin_connect("77.68.4.198", 4000)
    manual_mode.state.resolve(False)
    result = run(bridge, "mode", {"mode": "manual"})
    assert result["ok"] is True and result["mode"] == "manual" and switched == ["manual"]
    assert manual_mode.state.mode == manual_mode.MANUAL
    assert run(bridge, "mode", {"mode": "manual"})["ok"] is True and switched == ["manual"]
    assert run(bridge, "mode", {"mode": "full"})["mode"] == "full"
    assert run(bridge, "mode", {"mode": "undecided"})["status"] == 400


# --- the queued signal and the snapshot -------------------------------------------


def test_submit_runs_on_the_window_thread_and_answers(qtbot):
    bridge, _window, web = make_bridge()
    bridge.attach()
    assert web.on_desktop == bridge.submit
    answer = bridge.submit("nonsense", {})
    assert not answer.done()                # queued, not run inside the caller
    qtbot.waitUntil(answer.done, timeout=2000)
    assert answer.result()["status"] == 400


def test_the_snapshot_reaches_the_web_server(qtbot, tmp_path):
    store = CareerStore(str(tmp_path / "career.db"))
    store.hold("Alice").xp = 1500
    store.touched("Alice")
    career_panel = types.SimpleNamespace(store=store, ranks=build_ranks)
    bans = types.SimpleNamespace(
        game_type_now=lambda: 0x10000,
        connection_for=lambda name: ("82.68.58.92", Verdict("82.68.58.92", 0.0, country="gb")),
    )
    missions = types.SimpleNamespace(_find_display_name=lambda f: f"Title of {f}")
    mods = FakeMods()
    mods._vote_active = True
    mods._vote_map_name = "Snakebite"
    mods._vote_voters = {"alice", "bob"}
    mods._vote_total = 4
    mods._vote_threshold = 51
    mods._vote_timer = types.SimpleNamespace(remainingTime=lambda: 40_500)
    bridge, _window, web = make_bridge(career_panel=career_panel, bans_tab=bans,
                                       missions_tab=missions, mods_tab=mods)
    try:
        bridge.publish()
        top, players = web.desktop_state["top"], web.desktop_state["players"]
        assert web.pushes == 1
        assert top["server"]["game_type"] == "TDM"
        assert top["server"]["map_title"] == "Title of AAS_Doslin.bms"
        assert top["next_map"]["display_name"] == "Title of TDM_X.bms"
        assert top["vote"] == {"kind": "map", "active": True, "map": "Snakebite", "votes": 2,
                               "needed": 3, "seconds_left": 41}
        assert top["weather"] is None and top["lag_comp"] is None
        features = top["features"]
        assert features["bans"] is True and features["warn"] is True
        assert features["private_message"] is True
        assert features["weather"] is False and features["lag_comp"] is False
        assert features["ban_expiry"][3] == {"id": "7d", "label": "7 days"}
        alice = players["alice"]
        assert alice["xp"] == 1500
        from wolfrat.career import rank_for
        assert alice["rank"] == rank_for(1500, build_ranks()).name
        assert alice["country"] == "GB" and alice["time_on_min"] == 0
    finally:
        store.close()


def test_career_peek_reads_an_exact_name_without_writing(tmp_path):
    store = CareerStore(str(tmp_path / "career.db"))
    try:
        store.hold("Bobby").xp = 900
        store.touched("Bobby")
        store.flush()
        store.release([])                  # Bobby left: only the saved row is left
        assert store.peek("bobby").xp == 900
        assert store.peek("Bob") is None   # find() would take the part of a name
        store.hold("Bobby").xp = 950       # live, not saved yet
        assert store.peek("BOBBY").xp == 950
        assert store.peek("") is None
    finally:
        store.close()


def test_time_on_the_server_counts_a_visit(qtbot):
    bridge, _window, _web = make_bridge()
    start = 1000
    for second in range(0, 17 * 60 + 1, 5):                # one look per 5 s poll
        minutes = bridge._time_on(["Alice"], now=start + second)
    assert minutes == {"alice": 17}
    assert bridge._time_on(["Alice", "Bob"], now=start + 17 * 60 + 5) == {"alice": 17, "bob": 0}
    # Gone for 9 minutes (a map change, a quick reconnect): the same visit.
    left = start + 17 * 60 + 5
    for second in range(5, 9 * 60 + 1, 5):
        bridge._time_on(["Bob"], now=left + second)
    assert bridge._time_on(["Alice"], now=left + 9 * 60 + 5)["alice"] == 26
    # Gone for over 10 minutes: a new visit.
    back = left + 9 * 60 + 5
    assert bridge._time_on(["Alice"], now=back + 11 * 60)["alice"] == 0


def test_a_broken_part_leaves_the_rest_of_the_snapshot(qtbot):
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("tab exploded")
    bridge, _window, web = make_bridge(map_voting_tab=types.SimpleNamespace(_vote_stage="voting"),
                                       weather_tab=Broken())
    bridge.publish()
    top = web.desktop_state["top"]
    assert top["vote"] is None and top["weather"] is None
    assert top["server"]["name"] == "FMJ"
