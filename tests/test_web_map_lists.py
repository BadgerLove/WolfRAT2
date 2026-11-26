"""Saved map lists on the phone page (Dale, 2026-11-26).

The phone's Maps screen offers WolfRAT's saved rotations (the Missions tab's
Preset box) in a drop-down, with "Add to the end" and "Clear and add".  It is
the Missions tab's own Load: the same feed, the same lock and the same checks,
but nothing may open a box on the PC, because nobody is sitting at it.
"""

import json
import os
import types

import pytest
from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import manual_mode  # noqa: E402
from wolfrat import web_map_lists  # noqa: E402
from wolfrat.web_server import WolfWebServer  # noqa: E402
from tests.test_preset_load_guard import (  # noqa: E402,F401  (boxes is a fixture)
    OLD, PRESET, _ok, _tab, boxes,
)
from tests.test_web_bridge import FakeMods, make_bridge, run  # noqa: E402
from tests.test_web_phone_actions import Desktop, Manager  # noqa: E402

ABC = ["A.bms", "B.bms", "C.bms"]
WITH_GONE = PRESET + [{"file": "GONE.bms", "count": 1}, {"file": "LOST.bms", "count": 2}]
LISTS = {"1-coop": PRESET, "weekend": WITH_GONE, "nothing here": [{"file": "GONE.bms", "count": 1}]}


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


def _idle(qtbot, tab):
    qtbot.waitUntil(lambda: not tab._feed_active, timeout=3000)


def _saved(tmp_path):
    with open(tmp_path / "wolfrat_rotations.json", encoding="utf-8") as handle:
        return json.load(handle)


# ---- the drop-down's lists ------------------------------------------------------------------

def test_the_lists_are_the_saved_ones_in_the_preset_boxs_order(qtbot, tmp_path, boxes):
    tab, _server = _tab(qtbot, tmp_path, presets=LISTS)
    assert tab.phone_lists() == [
        {"name": "1-coop", "maps": 3, "missing": 0},
        {"name": "weekend", "maps": 5, "missing": 2},
        {"name": "nothing here", "maps": 1, "missing": 1},
    ]


def test_missing_is_unknown_until_the_servers_maps_are_read(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path, presets=LISTS)
    server.available_mission_entries = ()
    assert [row["missing"] for row in tab.phone_lists()] == [None, None, None]
    server.is_connected = False
    assert [row["maps"] for row in tab.phone_lists()] == [3, 5, 1]


def test_a_list_saved_on_the_pc_shows_up(qtbot, tmp_path, monkeypatch, boxes):
    from PyQt6.QtWidgets import QInputDialog
    tab, _server = _tab(qtbot, tmp_path)
    tab.rotation_table.setRowCount(0)
    tab._add_rotation_row("A.bms", 1)
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("fresh", True)))
    tab._save_preset()
    assert {"name": "fresh", "maps": 1, "missing": 0} in tab.phone_lists()


# ---- Add to the end / Clear and add ---------------------------------------------------------

def test_add_puts_the_list_after_the_maps_that_are_there(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    started, text, status = tab.phone_load_list("1-coop", False, "Web Admin (Pixel)")
    assert (started, status) == (True, None)
    assert text == "Adding '1-coop' to the end of the rotation: 3 maps"
    _idle(qtbot, tab)
    assert server.calls == [("ADD", "A.bms"), ("ADD", "B.bms"), ("ADD", "C.bms")]
    assert server.queue == OLD + ABC
    assert tab._phone_last["state"] == "done" and tab._phone_last["mode"] == "add"
    assert any("from the phone (Web Admin (Pixel)) - adding 3 maps" in m for m in server.logs)
    assert getattr(tab, "_cycle_preset", None) != "1-coop"   # Add mixes lists
    assert boxes == []


def test_clear_and_add_clears_first_then_adds_in_order(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    started, text, _status = tab.phone_load_list("1-coop", True, "Web Admin")
    assert started and text == "Clearing the rotation and adding '1-coop': 3 maps"
    _idle(qtbot, tab)
    assert server.calls == [("CLEAR",), ("ADD", "A.bms"), ("ADD", "B.bms"), ("ADD", "C.bms")]
    assert server.queue == ABC
    assert tab._phone_last["mode"] == "replace" and tab._phone_last["state"] == "done"
    assert tab._cycle_preset == "1-coop"       # Edit Order's OK and Save knows the list
    assert boxes == []


def test_clear_and_add_on_an_empty_rotation_sends_no_clear(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path, queue=[])
    started, text, _status = tab.phone_load_list("1-coop", True)
    assert started and text.startswith("Adding '1-coop'")
    _idle(qtbot, tab)
    assert server.calls == [("ADD", "A.bms"), ("ADD", "B.bms"), ("ADD", "C.bms")]
    assert tab._cycle_preset == "1-coop"


def test_x2_maps_keep_their_x2(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path, presets={"twice": [{"file": "A.bms", "count": 2},
                                                             {"file": "B.bms", "count": 1}]})
    sides = []
    original = server.add_mission
    server.add_mission = lambda filename, auto_switch_sides=False: (
        sides.append((filename, auto_switch_sides)) or original(filename, auto_switch_sides))
    assert tab.phone_load_list("twice", False)[0]
    _idle(qtbot, tab)
    assert sides == [("A.bms", True), ("B.bms", False)]


def test_maps_the_server_no_longer_has_are_left_out_and_stay_saved(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path, presets=LISTS)
    started, text, _status = tab.phone_load_list("weekend", True)
    assert started
    assert text == ("Clearing the rotation and adding 'weekend': 3 maps "
                    "(2 left out, not on the server)")
    _idle(qtbot, tab)
    assert server.queue == ABC
    assert tab._phone_last["missing"] == ["GONE.bms", "LOST.bms"]
    assert _saved(tmp_path)["weekend"] == WITH_GONE      # the saved list is not touched
    assert boxes == []


# ---- refusals: the reason goes to the phone, nothing is sent, no box -------------------------

def test_refusals_say_why_and_send_nothing(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path, presets=LISTS)

    assert tab.phone_load_list("no such list", True) == (
        False, "That list is no longer saved in WolfRAT", 409)
    assert tab.phone_load_list("(none)", True)[0] is False
    assert tab.phone_load_list(None, True)[0] is False
    assert tab.phone_load_list("nothing here", False) == (
        False, "None of the maps in 'nothing here' are on the server", 409)

    catalog = server.available_mission_entries
    server.available_mission_entries = ()
    started, text, status = tab.phone_load_list("1-coop", False)
    assert (started, status) == (False, 409) and "has not received the server's map list" in text
    server.available_mission_entries = catalog

    server.is_connected = False
    assert tab.phone_load_list("1-coop", False) == (
        False, "Not connected to the game server", 409)
    server.is_connected = True

    tab.minutes_left = lambda: 1
    started, text, status = tab.phone_load_list("1-coop", True)
    assert (started, status) == (False, 409)
    assert "about 1 minute left on this map" in text and "\n" not in text
    assert "Wait until the next map has started" in text

    assert server.calls == [] and not tab._feed_active
    assert tab._phone_last is None and boxes == []


def test_a_second_load_waits_for_the_one_that_is_running(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    server.hold_adds = True
    assert tab.phone_load_list("1-coop", True)[0]
    qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
    sent = list(server.calls)
    started, text, status = tab.phone_load_list("1-coop", False)
    assert (started, status) == (False, 409)
    assert "still putting maps on the server (loading '1-coop': 0 of 3 done)" in text
    assert server.calls == sent and tab._phone_last["id"] == 1
    # The PC's own buttons wait for the phone's load too.
    tab._clear_rotation()
    assert "still putting maps on the server" in boxes[-1][1]
    for _ in range(3):
        qtbot.waitUntil(lambda: bool(server.held), timeout=2000)
        server.held.pop(0).set_result(_ok("MISSION ADD"))
    _idle(qtbot, tab)
    server.hold_adds = False
    assert tab.phone_load_list("1-coop", False)[0]
    _idle(qtbot, tab)
    assert tab._phone_last["id"] == 2


# ---- how it went: kept for the page, never a box on the PC -----------------------------------

def test_a_map_the_server_refuses_is_told_to_the_phone_not_boxed(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    original = server.add_mission

    def add(filename, auto_switch_sides=False):
        if filename == "B.bms":
            raise RuntimeError("server said no")
        return original(filename, auto_switch_sides)

    server.add_mission = add
    assert tab.phone_load_list("1-coop", False)[0]
    _idle(qtbot, tab)
    assert tab._phone_last["failed"] == ["B.bms"] and tab._phone_last["state"] == "done"
    assert server.queue == OLD + ["A.bms", "C.bms"]
    assert boxes == []
    assert web_map_lists.summary(tab._phone_last) == {
        "id": 1, "name": "1-coop", "mode": "add", "state": "done", "tone": "warn",
        "text": "'1-coop' was added to the end: 2 maps. The server would not add B.bms.",
    }


def test_a_lost_connection_frees_the_list_and_is_told_to_the_phone(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    original = server.add_mission

    def add(filename, auto_switch_sides=False):
        if filename == "B.bms":
            server.is_connected = False
            raise ConnectionError("connection lost")
        return original(filename, auto_switch_sides)

    server.add_mission = add
    assert tab.phone_load_list("1-coop", False)[0]
    _idle(qtbot, tab)
    last = tab._phone_last
    assert last["state"] == "stopped" and last["lost"] is True
    assert last["failed"] == ["B.bms", "C.bms"]
    assert boxes == []
    assert web_map_lists.summary(last)["text"] == (
        "The connection to the server was lost: 1 of 3 maps from '1-coop' went on.")
    assert web_map_lists.summary(last)["tone"] == "bad"


def test_a_clear_the_server_refuses_adds_nothing(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)

    def refuse():
        raise RuntimeError("server said no")

    server.clear_missions = refuse
    assert tab.phone_load_list("1-coop", True)[0]
    _idle(qtbot, tab)
    last = tab._phone_last
    assert last["state"] == "stopped" and last["lost"] is False and "server said no" in last["error"]
    assert server.queue == OLD and not any(call[0] == "ADD" for call in server.calls)
    assert web_map_lists.summary(last)["text"].startswith("'1-coop' was not loaded: ")
    assert boxes == []


def test_the_pcs_own_load_still_gets_its_box_after_a_phone_load(qtbot, tmp_path, monkeypatch, boxes):
    tab, server = _tab(qtbot, tmp_path)
    assert tab.phone_load_list("1-coop", False)[0]
    _idle(qtbot, tab)
    assert tab._feed_report is None
    original = server.add_mission

    def add(filename, auto_switch_sides=False):
        if filename == "B.bms":
            raise RuntimeError("server said no")
        return original(filename, auto_switch_sides)

    server.add_mission = add
    monkeypatch.setattr(tab, "_ask_replace_or_add", lambda name, count: "add")
    tab._start_preset_load("1-coop", PRESET)
    _idle(qtbot, tab)
    assert any("The server would not add these maps" in text for _kind, text in boxes)
    assert tab._phone_last["failed"] == []      # the phone's last result is not the PC's


def test_every_step_of_a_load_is_passed_on_for_the_page(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    seen = []
    tab.on_feed_changed = lambda: seen.append(dict(tab._feed) if tab._feed else None)
    assert tab.phone_load_list("1-coop", True)[0]
    _idle(qtbot, tab)
    assert seen[0] == {"label": "loading '1-coop'", "done": 0, "total": 3}
    assert [step["done"] for step in seen if step] == [0, 1, 2, 3]
    assert seen[-1] is None
    # A PC load is passed on as well: the phone's buttons wait for it.
    seen.clear()
    tab._run_feed(PRESET, "the new map order", replace=True)
    _idle(qtbot, tab)
    assert seen[0]["label"] == "the new map order" and seen[-1] is None


# ---- the text the page shows ------------------------------------------------------------------

def _last(**changes):
    last = {"id": 4, "name": "1-coop", "mode": "replace", "total": 221, "missing": [],
            "failed": [], "lost": False, "state": "done", "error": "", "who": "Web Admin"}
    last.update(changes)
    return last


@pytest.mark.parametrize("last, text, tone", [
    (_last(), "The rotation is now '1-coop': 221 maps.", "ok"),
    (_last(mode="add", total=1), "'1-coop' was added to the end: 1 map.", "ok"),
    (_last(missing=["X.bms", "Y.npj"]),
     "The rotation is now '1-coop': 221 maps. Left out, not on the server: X.bms, Y.npj.", "warn"),
    (_last(total=7, failed=["A.bms", "B.bms", "C.bms", "D.bms", "E.bms", "F.bms"]),
     "The rotation is now '1-coop': 1 map. The server would not add "
     "A.bms, B.bms, C.bms, D.bms and 2 more.", "warn"),
    (_last(state="stopped", error="Clear rotation before loading '1-coop' timed out."),
     "'1-coop' was not loaded: Clear rotation before loading '1-coop' timed out.", "bad"),
    (_last(state="stopped"), "'1-coop' was not loaded.", "bad"),
    (_last(state="running"), "", "info"),
])
def test_the_summary_says_how_the_load_went(last, text, tone):
    said = web_map_lists.summary(last)
    assert (said["text"], said["tone"]) == (text, tone)
    assert said["id"] == 4 and said["name"] == "1-coop"


def test_the_block_for_the_page():
    assert web_map_lists.summary(None) is None
    assert web_map_lists.block([], None, None) == {"lists": [], "busy": None, "last": None}
    block = web_map_lists.block(
        [{"name": "1-coop", "maps": 221, "missing": 0}],
        {"label": "loading '1-coop'", "done": 42, "total": 221, "extra": object()},
        _last(state="running"),
    )
    assert block["busy"] == {"label": "loading '1-coop'", "done": 42, "total": 221}
    assert block["last"]["state"] == "running"
    json.dumps(block)                           # plain data for the page


# ---- the bridge: the phone's action and the snapshot --------------------------------------------

class FakeMissions:
    def __init__(self, answer=(True, "Adding '1-coop' to the end of the rotation: 3 maps", None)):
        self.answer = answer
        self.asked = []
        self._feed = None
        self._phone_last = None

    def phone_lists(self):
        return [{"name": "1-coop", "maps": 3, "missing": 0}]

    def phone_load_list(self, name, replace, who="Web Admin"):
        self.asked.append((name, replace, who))
        return self.answer

    def _find_display_name(self, filename):
        return filename


def test_the_maplist_action_runs_the_missions_tabs_load(qtbot):
    missions, mods = FakeMissions(), FakeMods()
    bridge, _window, _web = make_bridge(missions_tab=missions, mods_tab=mods)
    result = run(bridge, "maplist", {"name": "1-coop", "mode": "add", "device_name": "Pixel"})
    assert result == {"ok": True, "accepted": True,
                      "replies": ["Adding '1-coop' to the end of the rotation: 3 maps"]}
    assert missions.asked == [("1-coop", False, "Web Admin (Pixel)")]
    assert mods.mod_log.lines[-1].endswith(
        "Web Admin (Pixel) added the map list '1-coop' to the end of the rotation")

    run(bridge, "maplist", {"name": "1-coop", "mode": "replace"})
    assert missions.asked[-1] == ("1-coop", True, "Web Admin")
    assert mods.mod_log.lines[-1].endswith(
        "Web Admin cleared the rotation and loaded the map list '1-coop'")


def test_a_refused_load_goes_back_with_its_reason_and_no_log_line(qtbot):
    missions = FakeMissions(answer=(False, "Not connected to the game server", 409))
    mods = FakeMods()
    bridge, _window, _web = make_bridge(missions_tab=missions, mods_tab=mods)
    result = run(bridge, "maplist", {"name": "1-coop", "mode": "replace"})
    assert result["ok"] is False and result["status"] == 409
    assert result["error"] == "Not connected to the game server"
    assert mods.mod_log.lines == []


@pytest.mark.parametrize("payload", [
    {"mode": "add"}, {"name": "", "mode": "add"}, {"name": 7, "mode": "add"},
    {"name": "1-coop"}, {"name": "1-coop", "mode": "clear"}, {"name": "1-coop", "mode": True},
])
def test_a_load_without_a_list_or_a_known_mode_is_bad_input(qtbot, payload):
    missions = FakeMissions()
    bridge, _window, _web = make_bridge(missions_tab=missions)
    result = run(bridge, "maplist", payload)
    assert result["ok"] is False and result["status"] == 400
    assert missions.asked == []


def test_without_a_missions_tab_the_page_is_not_offered_lists(qtbot):
    bridge, _window, _web = make_bridge()
    assert run(bridge, "maplist", {"name": "1-coop", "mode": "add"})["status"] == 409
    top = bridge.build_snapshot()["top"]
    assert top["map_lists"] is None and top["features"]["map_lists"] is False


def test_the_snapshot_carries_the_lists_the_feed_and_the_last_result(qtbot):
    missions = FakeMissions()
    bridge, _window, _web = make_bridge(missions_tab=missions)
    top = bridge.build_snapshot()["top"]
    assert top["features"]["map_lists"] is True
    assert top["map_lists"] == {"lists": [{"name": "1-coop", "maps": 3, "missing": 0}],
                                "busy": None, "last": None}
    missions._feed = {"label": "loading '1-coop'", "done": 1, "total": 3}
    missions._phone_last = _last(state="running", total=3)
    top = bridge.build_snapshot()["top"]
    assert top["map_lists"]["busy"] == {"label": "loading '1-coop'", "done": 1, "total": 3}
    assert top["map_lists"]["last"]["state"] == "running"


def test_progress_is_published_while_the_maps_keep_coming(qtbot):
    bridge, _window, web = make_bridge(missions_tab=FakeMissions())
    for _ in range(40):                 # a map every 10 ms, quicker than the publish delay
        bridge.publish_soon()
        qtbot.wait(10)
    assert web.pushes >= 1              # schedule_publish alone would still be waiting
    assert web.desktop_state["top"]["map_lists"]["lists"][0]["name"] == "1-coop"


def test_the_real_tab_through_the_bridge(qtbot, tmp_path, boxes):
    tab, server = _tab(qtbot, tmp_path)
    mods = FakeMods()
    window = types.SimpleNamespace()
    bridge, window, web = make_bridge(server=server, missions_tab=tab, mods_tab=mods)
    tab.on_feed_changed = bridge.publish_soon
    result = run(bridge, "maplist", {"name": "1-coop", "mode": "replace", "device_name": "Pixel"})
    assert result["ok"] is True
    assert result["replies"] == ["Clearing the rotation and adding '1-coop': 3 maps"]
    _idle(qtbot, tab)
    assert server.queue == ABC and boxes == []
    block = bridge._map_lists_state()
    assert block["busy"] is None
    assert block["last"] == {"id": 1, "name": "1-coop", "mode": "replace", "state": "done",
                             "text": "The rotation is now '1-coop': 3 maps.", "tone": "ok"}
    qtbot.waitUntil(lambda: web.pushes >= 1, timeout=2000)


# ---- the web server's side ------------------------------------------------------------------------

@pytest.fixture
async def rig():
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


async def test_the_maplist_route_hands_its_fields_to_the_window(rig):
    service, client, auth = rig
    desktop = service.on_desktop = Desktop()
    desktop.answers["maplist"] = {"ok": True,
                                  "replies": ["Clearing the rotation and adding '1-coop': 221 maps"]}
    response = await client.post("/api/maplist", headers=auth,
                                 json={"name": "1-coop", "mode": "replace", "junk": 1})
    assert response.status == 200
    assert await response.json() == {
        "ok": True, "accepted": True,
        "replies": ["Clearing the rotation and adding '1-coop': 221 maps"]}
    assert desktop.asked == [("maplist", {"name": "1-coop", "mode": "replace",
                                          "device_name": "Pixel 8"})]


async def test_the_maplist_route_passes_the_windows_refusal_on(rig):
    service, client, auth = rig
    desktop = service.on_desktop = Desktop()
    desktop.answers["maplist"] = {"ok": False, "status": 409,
                                  "error": "That list is no longer saved in WolfRAT"}
    response = await client.post("/api/maplist", headers=auth,
                                 json={"name": "gone", "mode": "add"})
    payload = await response.json()
    assert response.status == 409 and payload["ok"] is False
    assert payload["error"] == "That list is no longer saved in WolfRAT"


async def test_the_maplist_route_needs_a_key_a_json_object_and_the_window(rig):
    service, client, auth = rig
    body = {"name": "1-coop", "mode": "add"}
    assert (await client.post("/api/maplist", json=body)).status == 401
    assert (await client.post("/api/maplist", headers=auth, json=body)).status == 503
    service.on_desktop = Desktop()
    assert (await client.post("/api/maplist", headers=auth, json=["1-coop"])).status == 400


async def test_the_state_has_the_map_lists_with_and_without_the_window(rig):
    service, client, auth = rig
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["map_lists"] is None and state["features"]["map_lists"] is False
    service.on_desktop = Desktop()
    block = {"lists": [{"name": "1-coop", "maps": 221, "missing": 0}], "busy": None, "last": None}
    service.desktop_state = {"top": {"map_lists": block, "features": {"map_lists": True}},
                             "players": {}}
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["map_lists"] == block and state["features"]["map_lists"] is True
