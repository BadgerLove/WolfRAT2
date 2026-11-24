"""The phone's "time left on this map" (2026-11-24).

The game server takes a new GameTime as the length of every round from then
on, and the phone's button means this round only: WolfRAT sets the time,
remembers the round length it found, and sets that again once the next round
has started.  Here: the rules (web_timeleft.TimeRestore), the bridge's action
and its check after every poll, and the web server's route.
"""

import json
import os
import types
from concurrent.futures import Future

import pytest
from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import manual_mode  # noqa: E402
from wolfrat import web_bridge  # noqa: E402
from wolfrat import web_timeleft  # noqa: E402
from wolfrat.admin_commands import MissionEntry  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402
from wolfrat.web_server import WolfWebServer  # noqa: E402
from wolfrat.web_timeleft import Pending, TimeRestore  # noqa: E402
from tests.test_web_bridge import FakeMods, FakeServer, _done, make_bridge, run  # noqa: E402
from tests.test_web_phone_actions import Desktop, Manager  # noqa: E402

SERVER = "77.68.4.198:4000"
MAP, NEXT = "AAS_Doslin.bms", "TDM_X.bms"


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


# ---- the rules -------------------------------------------------------------------------

def test_the_round_length_found_is_what_goes_back():
    restore = TimeRestore()
    assert restore.note_set(SERVER, MAP, 30, 10) == 30
    assert restore.pending(SERVER) == Pending(MAP, 30, 10)
    assert restore.pending("other:4000") is None


def test_setting_it_again_keeps_the_length_found_first():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.note_set(SERVER, MAP, 10, 5) == 30           # 10 was ours, not the server's
    assert restore.pending(SERVER) == Pending(MAP, 30, 5)
    assert restore.note_set(SERVER, NEXT, 5, 15) == 30          # the next map, still on ours
    assert restore.pending(SERVER) == Pending(NEXT, 30, 15)


def test_a_length_somebody_else_set_becomes_the_one_to_put_back():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.note_set(SERVER, MAP, 20, 5) == 20           # 20 is not ours: someone chose it
    assert restore.pending(SERVER) == Pending(MAP, 20, 5)


def test_the_normal_length_leaves_nothing_to_put_back():
    restore = TimeRestore()
    assert restore.note_set(SERVER, MAP, 30, 30) == 30
    assert restore.pending(SERVER) is None
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.note_set(SERVER, MAP, 10, 30) == 30          # back to it by hand
    assert restore.pending(SERVER) is None


def test_another_map_is_a_new_round():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.reading(SERVER, MAP, 10, 10) is None
    assert restore.reading(SERVER, MAP, 4, 10) is None
    assert restore.reading(SERVER, MAP.upper(), 3, 10) is None     # the same file, other letters
    assert restore.reading(SERVER, None, 3, 10) is None            # the map list says nothing
    assert restore.reading(SERVER, NEXT, 10, 10) == 30
    assert restore.reading(SERVER, NEXT, 9, 10) == 30              # until the server took it
    restore.sent(SERVER)
    assert restore.pending(SERVER) is None
    assert restore.reading(SERVER, NEXT, 9, 30) is None


def test_the_same_map_with_more_minutes_left_is_a_new_round():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    for left in (10, 9, 9, 5, 0, 0):
        assert restore.reading(SERVER, MAP, left, 10) is None
    assert restore.reading(SERVER, MAP, 9, 10) == 30
    assert restore.reading(SERVER, MAP, 9, 10) == 30               # it stays due


def test_our_own_change_restarting_the_clock_is_not_a_new_round():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.reading(SERVER, MAP, 10, 10) is None
    assert restore.reading(SERVER, MAP, 6, 10) is None
    restore.note_set(SERVER, MAP, 10, 10)                          # 10 again from the phone
    restore.clock_restarted(SERVER)
    assert restore.reading(SERVER, MAP, 10, 10) is None            # 6 -> 10 was us
    assert restore.reading(SERVER, MAP, 9, 10) is None
    assert restore.pending(SERVER) == Pending(MAP, 30, 10)


def test_minutes_left_while_the_length_is_not_ours_are_not_compared():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.reading(SERVER, MAP, 5, 30) is None             # not on the server yet
    assert restore.reading(SERVER, MAP, 10, 10) is None            # now it is: 5 -> 10 is no round
    assert restore.reading(SERVER, MAP, 9, 10) is None


def test_a_changed_length_at_the_new_round_is_left_alone_and_forgotten():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.reading(SERVER, MAP, 18, 20) is None            # someone set 20 meanwhile
    assert restore.pending(SERVER) is not None
    assert restore.reading(SERVER, NEXT, 20, 20) is None
    assert restore.pending(SERVER) is None


def test_five_failed_sends_and_it_gives_up():
    restore = TimeRestore()
    restore.note_set(SERVER, MAP, 30, 10)
    assert restore.reading(SERVER, NEXT, 10, 10) == 30
    assert [restore.failed(SERVER) for _ in range(web_timeleft.MAX_TRIES)] == [
        True, True, True, True, False]
    assert restore.pending(SERVER) is None
    assert restore.failed(SERVER) is False


def test_it_is_kept_in_a_file_per_server(tmp_path):
    path = tmp_path / web_timeleft.FILE_NAME
    restore = TimeRestore(path)
    assert not path.exists()                                        # nothing written at start
    restore.note_set(SERVER, MAP, 30, 10)
    restore.note_set("10.0.0.5:4000", NEXT, 45, 20)
    assert json.loads(path.read_text(encoding="utf-8")) == {"servers": {
        SERVER: {"map": MAP, "original": 30, "minutes": 10, "due": False, "tries": 0},
        "10.0.0.5:4000": {"map": NEXT, "original": 45, "minutes": 20, "due": False, "tries": 0},
    }}
    again = TimeRestore(path)
    assert again.pending(SERVER) == Pending(MAP, 30, 10)
    assert again.reading(SERVER, NEXT, 10, 10) == 30
    assert again.failed(SERVER) is True
    third = TimeRestore(path)                                       # due and the tries are kept too
    assert third.pending(SERVER) == Pending(MAP, 30, 10, due=True, tries=1)
    third.sent(SERVER)
    assert list(json.loads(path.read_text(encoding="utf-8"))["servers"]) == ["10.0.0.5:4000"]


@pytest.mark.parametrize("text", [
    "", "not json", "[]", '{"servers": 5}',
    '{"servers": {"a:1": {"map": "", "original": 30, "minutes": 10}}}',
    '{"servers": {"a:1": {"map": "M.bms", "original": "30", "minutes": 10}}}',
    '{"servers": {"a:1": {"map": "M.bms", "original": 30, "minutes": 999}}}',
    '{"servers": {"a:1": {"map": "M.bms", "original": 0, "minutes": 10}}}',
    '{"servers": {"a:1": {"map": "M.bms", "original": 30, "minutes": true}}}',
    '{"servers": {"a:1": "soon"}}',
])
def test_a_broken_file_is_read_as_nothing(tmp_path, text):
    path = tmp_path / web_timeleft.FILE_NAME
    path.write_text(text, encoding="utf-8")
    assert TimeRestore(path).pending("a:1") is None


def test_a_folder_that_cannot_be_written_does_not_stop_it(tmp_path):
    restore = TimeRestore(tmp_path / "no such folder" / web_timeleft.FILE_NAME)
    assert restore.note_set(SERVER, MAP, 30, 10) == 30
    assert restore.reading(SERVER, NEXT, 10, 10) == 30


# ---- the bridge ----------------------------------------------------------------------------

class GameServer(FakeServer):
    """The fake server of the bridge tests, with a GameTime that behaves like
    the game's: a new value is the round length and restarts the clock."""

    def __init__(self):
        super().__init__()
        self.game_settings = {"servername": "Test Server", "gametime": "12/30"}
        self.takes_it = True

    def set_setting(self, key, value):
        self.calls.append((key, value))
        if self.takes_it:
            self.clock(int(value), int(value))
        return _done("set")

    def clock(self, left, limit):
        self.game_settings = {**self.game_settings, "gametime": f"{left}/{limit}"}

    def load(self, filename, left, limit):
        self.mission_entries = (MissionEntry(queue_index=0, filename=filename, is_current=True),)
        self.clock(left, limit)


class Rig:
    def __init__(self, folder=None, game_type=0x10010):
        self.server = GameServer()
        self.mods = FakeMods()
        self.bans = types.SimpleNamespace(game_type=game_type)
        self.bans.game_type_now = lambda: self.bans.game_type
        self.folder = folder
        self.bridge, self.web = self.start()

    def start(self):
        """A WolfRAT starting on this server and settings folder."""
        parts = {"server": self.server, "mods_tab": self.mods, "bans_tab": self.bans}
        if self.folder is not None:
            parts["runtime"] = DesktopRuntime.isolated(self.folder)
        bridge, _window, web = make_bridge(**parts)
        return bridge, web

    def press(self, minutes, **more):
        return run(self.bridge, "timeleft", {"minutes": minutes, **more})

    def poll(self, left=None, limit=None):
        if left is not None:
            self.server.clock(left, limit)
        self.bridge.publish()
        return self.web.desktop_state["top"]

    def sets(self):
        return [value for key, value in self.server.calls if key == "gameTime"]

    def pending(self):
        return self.bridge._time.pending(manual_mode.state.key)


@pytest.fixture
def rig(qtbot, tmp_path):
    manual_mode.state.begin_connect("77.68.4.198", 4000, can_ask=False)
    return Rig(tmp_path)


def test_the_phone_sets_the_time_left_and_the_window_remembers_the_length(rig):
    assert rig.poll()["time_restore"] is None
    result = rig.press(10, device_name="Pixel")
    assert result == {"ok": True, "accepted": True, "replies": [
        "10 minutes left on this map. The next map goes back to 30 minutes."]}
    assert rig.server.calls == [("gameTime", "10")]
    assert rig.mods.said == ["10 minutes left on this map"]
    assert rig.mods.submitted[0]["context"] == "Set the time left"
    assert rig.mods.mod_log.lines[0].endswith("Web Admin (Pixel) set 10 minutes left on this map")
    assert rig.pending() == Pending(MAP, 30, 10)
    saved = json.loads((rig.folder / web_timeleft.FILE_NAME).read_text(encoding="utf-8"))
    assert saved == {"servers": {SERVER: {"map": MAP, "original": 30, "minutes": 10,
                                          "due": False, "tries": 0}}}
    top = rig.poll()
    assert top["time_restore"] == {"limit_min": 30}
    assert top["features"]["time_left"] is True
    assert (top["server"]["time_left_min"], top["server"]["time_limit_min"]) == (10, 10)


def test_one_minute_is_said_properly(rig):
    assert rig.press(1)["replies"] == [
        "1 minute left on this map. The next map goes back to 30 minutes."]
    assert rig.mods.said == ["1 minute left on this map"]


@pytest.mark.parametrize("payload", [
    {}, {"minutes": None}, {"minutes": 0}, {"minutes": 241}, {"minutes": -3},
    {"minutes": 2.5}, {"minutes": 10.0}, {"minutes": "10"}, {"minutes": True}, {"minutes": [10]},
])
def test_minutes_must_be_a_whole_number_from_1_to_240(rig, payload):
    result = run(rig.bridge, "timeleft", payload)
    assert result["ok"] is False and result["status"] == 400, payload
    assert rig.server.calls == [] and rig.pending() is None


def test_the_edges_are_taken(rig):
    assert rig.press(240)["ok"] is True
    assert rig.press(1)["ok"] is True
    assert rig.sets() == ["240", "1"]


def refused(rig, status=409):
    result = rig.press(10)
    assert result["ok"] is False and result["status"] == status, result
    assert rig.server.calls == [] and rig.pending() is None and rig.mods.said == []
    assert rig.poll()["features"]["time_left"] is False
    return result["error"]


def test_refused_when_not_connected(rig):
    rig.server.is_connected = False
    assert refused(rig) == "Not connected to the game server"


def test_refused_on_a_co_op_map(rig):
    rig.bans.game_type = 0x10020
    assert refused(rig) == "A co-op map has no round timer"


@pytest.mark.parametrize("gametime", ["0/0", "", None, "soon", "5/0"])
def test_refused_when_the_map_has_no_round_timer(rig, gametime):
    rig.server.game_settings = {"servername": "Test Server", "gametime": gametime}
    assert refused(rig) == "This map has no round timer"


def test_refused_until_the_running_map_is_known(rig):
    rig.server.mission_entries = ()
    assert "map list" in refused(rig)


def test_refused_while_the_window_is_still_starting(qtbot):
    manual_mode.state.begin_connect("77.68.4.198", 4000, can_ask=False)
    bridge, window, _web = make_bridge(server=GameServer())
    result = run(bridge, "timeleft", {"minutes": 10})
    assert result["status"] == 503 and window.server.calls == []
    assert bridge._time.pending(SERVER) is None


def test_the_length_goes_back_when_the_next_map_starts(rig):
    rig.press(10)
    for left in (10, 9, 5, 1, 0):
        assert rig.poll(left, 10)["time_restore"] == {"limit_min": 30}
    assert rig.sets() == ["10"]                          # nothing more while the map runs
    rig.server.load(NEXT, 10, 10)
    top = rig.poll()
    assert rig.sets() == ["10", "30"]
    assert rig.mods.said == ["10 minutes left on this map"]          # no chat line for it
    assert rig.mods.submitted[-1]["announcement"] is None
    assert rig.mods.submitted[-1]["context"] == "Put the round length back"
    assert rig.mods.mod_log.lines[-1].endswith(
        "WolfRAT put the round length back to 30 minutes (the phone's 10 minutes was for one map)")
    assert rig.pending() is None and top["time_restore"] is None
    assert json.loads((rig.folder / web_timeleft.FILE_NAME).read_text(encoding="utf-8")) == {
        "servers": {}}
    assert rig.server.game_settings["gametime"] == "30/30"
    rig.poll(29, 30)
    assert rig.sets() == ["10", "30"]                    # once


def test_the_length_goes_back_when_the_same_map_is_loaded_again(rig):
    rig.press(10)
    for left in (10, 8, 3, 0, 0):
        rig.poll(left, 10)
    assert rig.sets() == ["10"]
    rig.poll(9, 10)                                      # the same file, the clock is back up
    assert rig.sets() == ["10", "30"] and rig.pending() is None


def test_setting_the_time_itself_is_never_taken_for_a_new_round(rig):
    rig.poll(5, 30)
    rig.press(10)                                        # 5 left -> 10 left: that was the phone
    for left in (10, 10, 9, 7):
        rig.poll(left, 10)
    assert rig.press(10)["replies"] == [                 # 7 left -> 10 left again
        "10 minutes left on this map. The next map goes back to 30 minutes."]
    for left in (10, 9, 6):
        rig.poll(left, 10)
    assert rig.press(8)["replies"] == [
        "8 minutes left on this map. The next map goes back to 30 minutes."]
    for left in (8, 7, 2):
        rig.poll(left, 8)
    assert rig.sets() == ["10", "10", "8"] and rig.pending() == Pending(MAP, 30, 8)
    rig.server.load(NEXT, 8, 8)
    rig.poll()
    assert rig.sets() == ["10", "10", "8", "30"]


def test_nothing_is_put_back_when_somebody_changed_the_length_since(rig):
    rig.press(10)
    top = rig.poll(18, 20)                               # e.g. a mod's !gametime 20
    assert top["time_restore"] is None                   # the server is not on the phone's length
    rig.server.load(NEXT, 20, 20)
    rig.poll()
    assert rig.sets() == ["10"] and rig.pending() is None
    rig.server.load(MAP, 20, 20)
    rig.poll()
    assert rig.sets() == ["10"]


def test_setting_the_normal_length_again_leaves_nothing_to_put_back(rig):
    rig.press(10)
    assert rig.press(30)["replies"] == ["30 minutes left on this map."]
    assert rig.pending() is None and rig.poll()["time_restore"] is None
    rig.server.load(NEXT, 30, 30)
    rig.poll()
    assert rig.sets() == ["10", "30"]                    # only the two the phone pressed


def test_it_survives_a_wolfrat_restart_in_the_middle(rig):
    rig.press(10)
    rig.poll(7, 10)
    rig.bridge, rig.web = rig.start()                    # WolfRAT closed and opened again
    assert rig.pending() == Pending(MAP, 30, 10)
    assert rig.poll(6, 10)["time_restore"] == {"limit_min": 30}
    assert rig.sets() == ["10"]
    rig.server.load(NEXT, 10, 10)
    rig.poll()
    assert rig.sets() == ["10", "30"] and rig.pending() is None


def test_a_failed_send_is_tried_again_at_the_next_poll(rig):
    rig.press(10)
    rig.server.load(NEXT, 10, 10)
    rig.mods.fail = "Put the round length back was accepted but not verified by retail"
    rig.server.takes_it = False
    rig.poll()
    assert rig.sets() == ["10", "30"] and rig.pending().tries == 1
    rig.poll(9, 10)
    assert rig.sets() == ["10", "30", "30"] and rig.pending().tries == 2
    rig.mods.fail, rig.server.takes_it = None, True
    rig.poll(9, 10)
    assert rig.sets() == ["10", "30", "30", "30"] and rig.pending() is None
    assert rig.server.game_settings["gametime"] == "30/30"


def test_after_five_failed_sends_it_gives_up_and_says_so(rig):
    rig.press(10)
    rig.server.load(NEXT, 10, 10)
    rig.mods.fail, rig.server.takes_it = "no answer", False
    lines = len(rig.mods.mod_log.lines)
    for _ in range(8):
        rig.poll(9, 10)
    assert rig.sets() == ["10"] + ["30"] * 5 and rig.pending() is None
    assert len(rig.mods.mod_log.lines) == lines + 1
    assert "could not put the round length back to 30 minutes" in rig.mods.mod_log.lines[-1]


def test_a_send_the_server_took_after_all_is_not_sent_twice(rig):
    rig.press(10)
    rig.server.load(NEXT, 10, 10)
    rig.mods.fail = "the answer was lost"                # but the server did take it
    rig.poll()
    assert rig.server.game_settings["gametime"] == "30/30"
    rig.poll(29, 30)
    assert rig.sets() == ["10", "30"] and rig.pending() is None


def test_it_goes_back_in_manual_mode_too(rig):
    rig.press(10)
    manual_mode.state.choose(manual_mode.MANUAL)
    assert manual_mode.quiet()
    rig.server.load(NEXT, 10, 10)
    rig.poll()
    assert rig.sets() == ["10", "30"]                    # it finishes what the admin pressed


def test_it_waits_for_the_link_and_a_clock_reading(rig):
    rig.press(10)
    rig.server.load(NEXT, 0, 0)                          # loading: no clock yet
    rig.poll()
    rig.server.is_connected = False
    rig.server.clock(10, 10)
    assert rig.poll()["time_restore"] is None
    assert rig.sets() == ["10"] and rig.pending() is not None
    rig.server.is_connected = True
    rig.poll()
    assert rig.sets() == ["10", "30"]


def test_a_failed_press_keeps_the_note_but_promises_nothing(rig):
    rig.mods.fail, rig.server.takes_it = "Set the time left rejected: no", False
    result = rig.press(10)
    assert result["ok"] is False and "rejected" in result["error"]
    assert rig.mods.said == []
    assert rig.pending() == Pending(MAP, 30, 10)         # in case the server took it after all
    assert rig.poll()["time_restore"] is None            # the server is still on 30
    rig.mods.fail = None
    rig.server.load(NEXT, 30, 30)
    rig.poll()
    assert rig.sets() == ["10"] and rig.pending() is None    # 30 is not ours: nothing sent


def test_one_change_at_a_time_and_a_lost_answer_does_not_block_for_ever(rig):
    now = [1000.0]
    rig.bridge._monotonic = lambda: now[0]
    rig.mods._submit_mod_action = lambda operation, **_kwargs: operation()   # never answers
    waiting = Future()
    rig.bridge._run("timeleft", {"minutes": 10}, waiting)
    assert not waiting.done() and rig.sets() == ["10"]
    busy = rig.press(5)
    assert busy["status"] == 409 and "still changing" in busy["error"]
    rig.server.load(NEXT, 10, 10)
    rig.poll()
    assert rig.sets() == ["10"]                          # not while the first is on its way
    now[0] += web_bridge.TIME_BUSY_SECONDS + 1
    rig.mods._submit_mod_action = FakeMods()._submit_mod_action
    rig.poll()
    assert rig.sets() == ["10", "30"]


def test_another_server_is_left_alone(rig):
    rig.press(10)
    manual_mode.state.begin_connect("10.0.0.5", 4000, can_ask=False)
    rig.server.load(NEXT, 10, 10)
    assert rig.poll()["time_restore"] is None
    assert rig.sets() == ["10"]
    manual_mode.state.begin_connect("77.68.4.198", 4000, can_ask=False)
    rig.poll()
    assert rig.sets() == ["10", "30"]


def test_a_window_without_a_settings_folder_still_works(qtbot):
    manual_mode.state.begin_connect("77.68.4.198", 4000, can_ask=False)
    rig = Rig(folder=None)
    rig.press(10)
    rig.server.load(NEXT, 10, 10)
    rig.poll()
    assert rig.sets() == ["10", "30"]


def test_a_broken_check_never_stops_the_snapshot(rig):
    rig.press(10)
    rig.bridge._time.reading = lambda *args: 1 / 0
    rig.server.load(NEXT, 10, 10)
    assert rig.poll()["server"]["name"] == "Test Server"


# ---- the web server's side -------------------------------------------------------------------

@pytest.fixture
async def web_rig():
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    key, _device = service.create_device("Pixel 8")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client, {"Authorization": f"Bearer {key}"}
    finally:
        await client.close()


async def test_the_route_hands_the_minutes_to_the_window(web_rig):
    service, client, auth = web_rig
    desktop = service.on_desktop = Desktop()
    reply = "10 minutes left on this map. The next map goes back to 30 minutes."
    desktop.answers["timeleft"] = {"ok": True, "replies": [reply]}
    response = await client.post("/api/timeleft", headers=auth, json={"minutes": 10, "junk": 1})
    assert response.status == 200
    assert await response.json() == {"ok": True, "accepted": True, "replies": [reply]}
    assert desktop.asked == [("timeleft", {"minutes": 10, "device_name": "Pixel 8"})]


async def test_the_route_passes_the_windows_refusals_on(web_rig):
    service, client, auth = web_rig
    desktop = service.on_desktop = Desktop()
    for status, error in ((400, "minutes must be a whole number from 1 to 240"),
                          (409, "A co-op map has no round timer")):
        desktop.answers["timeleft"] = {"ok": False, "error": error, "status": status}
        response = await client.post("/api/timeleft", headers=auth, json={"minutes": 10})
        payload = await response.json()
        assert response.status == status and payload["error"] == error
        assert payload["ok"] is False and "status" not in payload


async def test_the_route_needs_a_key_a_json_object_and_the_window(web_rig):
    service, client, auth = web_rig
    assert (await client.post("/api/timeleft", json={"minutes": 10})).status == 401
    assert (await client.post("/api/timeleft", headers=auth, json={"minutes": 10})).status == 503
    service.on_desktop = Desktop()
    assert (await client.post("/api/timeleft", headers=auth, json=[10])).status == 400


async def test_the_state_has_the_time_keys_with_and_without_the_window(web_rig):
    service, client, auth = web_rig
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["time_restore"] is None and state["features"]["time_left"] is False
    service.on_desktop = Desktop()
    service.desktop_state = {"top": {"time_restore": {"limit_min": 30},
                                     "features": {"time_left": True}}, "players": {}}
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["time_restore"] == {"limit_min": 30}
    assert state["features"]["time_left"] is True
