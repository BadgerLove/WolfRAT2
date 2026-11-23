"""The phone page's new state fields and actions, on the web side (2026-11-23).

The WolfRAT window is faked here: on_desktop records what it was asked and
answers at once.  tests/test_web_bridge.py covers the window's side.
"""

from concurrent.futures import Future

import pytest
from aiohttp.test_utils import TestClient, TestServer

import wolfrat.web_server as web_server_module
from wolfrat import manual_mode
from wolfrat.admin_commands import MissionEntry, PlayerEntry
from wolfrat.admin_session import CommandResult
from wolfrat.web_server import WolfWebServer

ALICE = PlayerEntry(server_id=7, name="Alice", team=1, kills=4, deaths=2, ping=120, revision=6)
BOB = PlayerEntry(server_id=9, name="Bob", team=2, kills=1, deaths=5, ping=40, revision=6)


class Manager:
    is_connected = True
    game_state = {"mode": "Game"}
    available_maps_data = "0: AAS_Doslin.bms - (Doslin Oblast)\n1: TDM_X.bms"
    game_settings = {"servername": "FMJ Squad", "gametime": "12/30", "ServerPassword": "pw"}

    def __init__(self):
        self.players = []
        self.player_entries = (PlayerEntry(server_id=0, name="Host", team=0), ALICE, BOB)
        self.mission_entries = (
            MissionEntry(queue_index=0, filename="AAS_Doslin.bms", is_current=True),
            MissionEntry(queue_index=1, filename="TDM_X.bms", is_next=True),
        )
        self.chat_messages = [
            {"id": 1, "time": "", "text": "Alice: hi", "raw": "Alice: hi"},
            {"id": 2, "time": "", "text": "Alice was kicked by an admin", "raw": "x", "own": True},
            {"id": 3, "time": "", "text": "Map loading", "raw": "Map loading"},
        ]
        self.calls = []
        self.logged = []

    def _accept(self, *call):
        self.calls.append(call)
        done = Future()
        done.set_result(CommandResult(operation=call[0], replies=("OK",), accepted=True,
                                      verified=True))
        return done

    def kill_player(self, player):
        return self._accept("kill", player)

    def set_next_mission(self, mission, *, add_if_missing=False):
        return self._accept("set_next", mission, add_if_missing)

    def _log(self, message):
        self.logged.append(message)


class Desktop:
    """Stands in for MainWindow's bridge: records (action, payload) and answers."""

    def __init__(self):
        self.asked = []
        self.answers = {}

    def __call__(self, action, payload):
        self.asked.append((action, payload))
        done = Future()
        done.set_result(dict(self.answers.get(action, {"ok": True, "replies": [f"{action} done"]})))
        return done


@pytest.fixture
async def rig():
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    key, _device = service.create_device("Pixel 8")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, manager, client, {"Authorization": f"Bearer {key}"}
    finally:
        await client.close()


async def test_state_has_every_new_field_without_the_window(rig):
    service, _manager, client, auth = rig
    state = await (await client.get("/api/status", headers=auth)).json()
    for key in ("connected", "players", "chat", "game_state", "missions", "settings",
                "player_count", "game_mode", "chat_max", "timestamp"):
        assert key in state, key
    assert state["server"] == {
        "name": "FMJ Squad", "game_type": "AAS", "game_type_name": "Advance & Secure",
        "map_title": "Doslin Oblast", "map_file": "AAS_Doslin.bms",
        "time_left_min": 12, "time_limit_min": 30,
    }
    # Same display name as the Maps list (a map with no title keeps its file name).
    assert state["next_map"] == {"filename": "TDM_X.bms", "display_name": "TDM_X.bms",
                                 "queue_index": 1, "guessed": False}
    assert state["teams"] == [
        {"team": 1, "name": "Joint Ops", "kills": 4, "players": 1},
        {"team": 2, "name": "Rebels", "kills": 1, "players": 1},
    ]
    assert state["manual_mode"] in ("full", "manual", "undecided")
    assert state["vote"] is None and state["weather"] is None and state["lag_comp"] is None
    features = state["features"]
    assert features["pairing"] is True and features["set_next"] is True
    for name in ("bans", "warn", "private_message", "weather", "lag_comp", "manual_switch"):
        assert features[name] is False, name
    assert features["weather_presets"] == [] and features["ban_expiry"] == []
    alice = next(row for row in state["players"] if row["name"] == "Alice")
    assert alice["ping_band"] == 1
    assert (alice["rank"], alice["xp"], alice["country"], alice["time_on_min"]) == (None,) * 4
    kinds = [(row["kind"], row["name"]) for row in state["chat"]]
    assert kinds == [("player", "Alice"), ("wolfrat", None), ("console", None)]
    assert "pw" not in repr(state)


async def test_the_windows_snapshot_fills_in_the_rest(rig):
    service, _manager, client, auth = rig
    service.on_desktop = Desktop()
    service.desktop_state = {
        "top": {
            "server": {"name": "FMJ Squad", "game_type": "TDM", "game_type_name": "Team Deathmatch",
                       "map_title": "Doslin Oblast", "map_file": "AAS_Doslin.bms",
                       "time_left_min": None, "time_limit_min": None},
            "next_map": None,
            "manual_mode": "manual",
            "vote": {"kind": "map", "active": True, "map": "Snakebite", "votes": 2,
                     "needed": 3, "seconds_left": 41},
            "weather": {"summary": "Storm, 9 min left", "fog_m": 350, "dynamic": False},
            "lag_comp": {"enabled": True, "tune_mode": "watch", "summary": "ON", "running": True},
            "features": {"bans": True, "warn": True, "private_message": True, "weather": True,
                         "weather_presets": [{"id": "clear", "label": "Clear"}],
                         "lag_comp": True, "manual_switch": True,
                         "ban_expiry": [{"id": "", "label": "Never"}]},
        },
        "players": {"alice": {"rank": "Sergeant", "xp": 1200, "country": "GB", "time_on_min": 17}},
    }
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["server"]["game_type"] == "TDM"
    assert state["next_map"] is None
    assert state["manual_mode"] == "manual"
    assert state["vote"]["needed"] == 3
    assert state["weather"]["fog_m"] == 350
    assert state["lag_comp"]["tune_mode"] == "watch"
    assert state["features"]["bans"] is True
    assert state["features"]["set_next"] is True          # always worked out live
    alice = next(row for row in state["players"] if row["name"] == "Alice")
    assert (alice["rank"], alice["xp"], alice["country"], alice["time_on_min"]) == (
        "Sergeant", 1200, "GB", 17)
    players = await (await client.get("/api/players", headers=auth)).json()
    assert next(r for r in players["players"] if r["name"] == "Alice")["rank"] == "Sergeant"


@pytest.mark.parametrize("action, body", [
    ("kick", {"reason": "camping"}),
    ("ban", {"reason": "cheating", "expiry": "7d"}),
    ("warn", {"reason": "language"}),
    ("message", {"message": "please swap"}),
])
async def test_player_actions_the_window_does(rig, action, body):
    service, manager, client, auth = rig
    desktop = service.on_desktop = Desktop()
    response = await client.post("/api/player/action", headers=auth, json={
        "pid": 7, "name": "Alice", "action": action, **body})
    payload = await response.json()
    assert response.status == 200, payload
    assert payload["ok"] is True and payload["replies"] == [f"{action} done"]
    assert (payload["action"], payload["pid"], payload["name"]) == (action, 7, "Alice")
    [(asked, sent)] = desktop.asked
    assert asked == action
    assert sent["entry"] is ALICE
    assert sent["device_name"] == "Pixel 8"
    for field, value in body.items():
        assert sent[field] == value
    assert manager.calls == []           # nothing went straight to the server


async def test_kill_swap_zero_still_go_straight_to_the_server(rig):
    service, manager, client, auth = rig
    desktop = service.on_desktop = Desktop()
    response = await client.post("/api/player/action", headers=auth,
                                 json={"pid": 7, "name": "Alice", "action": "kill"})
    assert (await response.json())["ok"] is True
    assert manager.calls == [("kill", ALICE)] and desktop.asked == []


async def test_the_windows_status_and_refusals_reach_the_phone(rig):
    service, _manager, client, auth = rig
    desktop = service.on_desktop = Desktop()
    desktop.answers["message"] = {"ok": False, "error": "helper not ready", "status": 409}
    response = await client.post("/api/player/action", headers=auth, json={
        "pid": 7, "name": "Alice", "action": "message", "message": "hi"})
    payload = await response.json()
    assert response.status == 409
    assert payload["ok"] is False and payload["error"] == "helper not ready"
    assert "status" not in payload


async def test_without_the_window(rig):
    service, _manager, client, auth = rig
    assert service.on_desktop is None
    kick = await client.post("/api/player/action", headers=auth,
                             json={"pid": 7, "name": "Alice", "action": "kick"})
    assert kick.status == 503
    message = await client.post("/api/player/action", headers=auth,
                                json={"pid": 7, "name": "Alice", "action": "message",
                                      "message": "hi"})
    assert message.status == 409
    weather = await client.post("/api/weather", headers=auth, json={"preset": "storm"})
    assert weather.status == 503


async def test_bad_input_and_stale_players_never_reach_the_window(rig):
    service, _manager, client, auth = rig
    desktop = service.on_desktop = Desktop()
    for body, status in (
        ({"pid": 7, "name": "Alice", "action": "kick", "reason": 5}, 400),
        ({"pid": 7, "name": "Alice", "action": "ban", "expiry": 7}, 400),
        ({"pid": 7, "name": "Alice", "action": "warn", "reason": "x" * 500}, 400),
        ({"pid": 7, "name": "Somebody", "action": "ban"}, 409),
        ({"pid": 7, "name": "Alice", "action": "explode"}, 400),
    ):
        response = await client.post("/api/player/action", headers=auth, json=body)
        assert response.status == status, (body, await response.text())
    assert desktop.asked == []


async def test_a_busy_window_answers_busy(rig, monkeypatch):
    service, _manager, client, auth = rig
    monkeypatch.setattr(web_server_module, "DESKTOP_TIMEOUT", 0.05)
    service.on_desktop = lambda action, payload: Future()      # never answers
    response = await client.post("/api/player/action", headers=auth,
                                 json={"pid": 7, "name": "Alice", "action": "kick"})
    payload = await response.json()
    assert payload["ok"] is False and payload["error"].startswith("WolfRAT is busy")
    assert web_server_module.DESKTOP_TIMEOUTS["lagcomp"] >= 20


@pytest.mark.parametrize("path, action, body, passed", [
    ("/api/weather", "weather",
     {"preset": "storm", "fog_m": 80, "minutes": 5, "dynamic": True, "lightning": "far",
      "junk": 1},
     {"preset": "storm", "fog_m": 80, "minutes": 5, "dynamic": True, "lightning": "far"}),
    ("/api/lagcomp", "lagcomp", {"enabled": True, "tune_mode": "on", "x": 2},
     {"enabled": True, "tune_mode": "on"}),
    ("/api/mode", "mode", {"mode": "manual"}, {"mode": "manual"}),
])
async def test_desktop_routes_pass_their_fields_only(rig, path, action, body, passed):
    service, _manager, client, auth = rig
    desktop = service.on_desktop = Desktop()
    response = await client.post(path, headers=auth, json=body)
    assert response.status == 200
    assert (await response.json())["ok"] is True
    [(asked, sent)] = desktop.asked
    assert asked == action
    assert sent == {**passed, "device_name": "Pixel 8"}


async def test_desktop_routes_need_a_key_and_a_json_object(rig):
    service, _manager, client, auth = rig
    service.on_desktop = Desktop()
    for path in ("/api/weather", "/api/lagcomp", "/api/mode", "/api/map/setnext"):
        assert (await client.post(path, json={})).status == 401
        assert (await client.post(path, headers=auth, json=[1])).status == 400


async def test_set_next_is_the_desktops_queue_next(rig):
    service, manager, client, auth = rig
    response = await client.post("/api/map/setnext", headers=auth,
                                 json={"filename": "TDM_X.bms", "index": 1})
    payload = await response.json()
    assert response.status == 200 and payload["ok"] is True
    assert (payload["map"], payload["index"]) == ("TDM_X.bms", 1)
    assert manager.calls == [("set_next", manager.mission_entries[1], False)]
    assert manager.logged and "TDM_X.bms" in manager.logged[0]

    stale = await client.post("/api/map/setnext", headers=auth,
                              json={"filename": "GONE.bms", "index": 4})
    assert stale.status == 409
    manager.is_connected = False
    offline = await client.post("/api/map/setnext", headers=auth,
                                json={"filename": "TDM_X.bms", "index": 1})
    assert offline.status == 503


def test_manual_switch_needs_a_server_key(monkeypatch):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.on_desktop = Desktop()
    monkeypatch.setattr(manual_mode.state, "_key", "", raising=False)
    assert service._build_state()["features"]["manual_switch"] is False
    monkeypatch.setattr(manual_mode.state, "_key", "77.68.4.198:4000", raising=False)
    assert service._build_state()["features"]["manual_switch"] is True
