"""Contract v3: what each phone hears about.  The groups and their defaults,
GET / POST /api/alerts/prefs, and the choices saved with the phone's own
record in wolfrat_web.json.  No network beyond the test's own web server."""

import copy

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wolfrat import web_events
from wolfrat.admin_commands import PlayerEntry
from wolfrat.web_server import WolfWebServer

GROUPS = ["join", "leave", "map", "vote", "admin", "server", "objective"]
# CONTRACT section 2, to the letter.
DEFAULTS = {
    "groups": GROUPS,
    "bell": {"join": True, "leave": False, "map": False, "vote": False,
             "admin": True, "server": True, "objective": False},
    "phone": {"join": True, "leave": False, "map": False, "vote": False,
              "admin": False, "server": True, "objective": False},
}


class Manager:
    """The ServerManager snapshot the web server reads."""

    def __init__(self):
        self.is_connected = True
        self.players = []
        self.player_entries = (PlayerEntry(server_id=0, name="Host", team=0),)
        self.chat_messages = []
        self.game_state = {"mode": "AAS"}
        self.mission_entries = ()
        self.available_maps_data = ""
        self.game_settings = {"servername": "Test Server"}


def new_service():
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    return service


@pytest.fixture
async def rig():
    service = new_service()
    saves = []
    service.on_devices_changed = lambda: saves.append(1)
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client, saves
    finally:
        await client.close()


async def sign_in(client, name="phone"):
    response = await client.post("/api/auth", json={
        "username": "operator", "token": "web-access-token", "device_name": name})
    body = await response.json()
    return {"Authorization": f"Bearer {body['key']}"}, body["device_id"]


async def prefs(client, auth):
    response = await client.get("/api/alerts/prefs", headers=auth)
    assert response.status == 200
    return await response.json()


def record_of(service, device_id):
    return {record["id"]: record for record in service.devices_for_config()}[device_id]


# --- the groups (plain data) ---------------------------------------------------------


def test_every_kind_has_its_group():
    assert list(web_events.GROUPS) == GROUPS
    assert {kind: web_events.group_of(kind) for kind in web_events.KINDS} == {
        "join": "join", "leave": "leave", "map": "map", "vote": "vote",
        "kick": "admin", "ban": "admin", "warn": "admin",
        "server": "server", "zone": "objective", "flag": "objective",
    }
    assert set(web_events.KIND_GROUPS.values()) == set(GROUPS)
    for unknown in ("swap", "", None, 7):
        assert web_events.group_of(unknown) is None


def test_the_defaults_are_the_contracts():
    assert web_events.prefs_reply(None) == DEFAULTS
    assert web_events.default_prefs() == {"bell": DEFAULTS["bell"], "phone": DEFAULTS["phone"]}
    assert web_events.clean_prefs(None) == web_events.default_prefs()
    # Zones and flags, map changes and players leaving are off until asked for.
    for channel in ("bell", "phone"):
        for group in ("leave", "map", "vote", "objective"):
            assert DEFAULTS[channel][group] is False
    made = web_events.default_prefs()
    made["bell"]["map"] = True                         # a copy: the defaults stay
    assert web_events.default_prefs()["bell"]["map"] is False


@pytest.mark.parametrize("damaged", [
    None, "on", 7, [], ["bell"], {"bell": "all"}, {"bell": None, "phone": 3},
    {"bell": {"map": "yes", "join": 1, "leave": None, "admin": 0}},
    {"phone": {"server": "false"}}, {"sms": {"map": True}}, {"bell": {"nope": True}},
])
def test_damaged_choices_fall_back_to_the_defaults(damaged):
    assert web_events.clean_prefs(damaged) == web_events.default_prefs()


def test_clean_keeps_what_is_valid_and_fills_the_rest():
    cleaned = web_events.clean_prefs({
        "bell": {"map": True, "join": False, "vote": "yes", "nope": True},
        "phone": {"server": False},
        "extra": 1,
    })
    assert cleaned == {
        "bell": {**DEFAULTS["bell"], "map": True, "join": False},
        "phone": {**DEFAULTS["phone"], "server": False},
    }
    assert list(cleaned["bell"]) == GROUPS and list(cleaned["phone"]) == GROUPS


def test_changes_are_all_or_nothing():
    current = web_events.default_prefs()
    updated, reason = web_events.changed_prefs(
        current, {"bell": {"map": True}, "phone": {"server": False}})
    assert reason is None
    assert updated["bell"]["map"] is True and updated["phone"]["server"] is False
    assert current == web_events.default_prefs()       # the one given is not changed
    assert web_events.changed_prefs(current, {}) == (current, None)
    assert web_events.changed_prefs(current, {"bell": {}}) == (current, None)
    for bad in ({"bell": {"map": True, "nope": True}}, {"bell": {"map": "yes"}},
                {"bell": {"map": 1}}, {"phone": {"server": None}}, {"bell": True},
                {"sms": {"map": True}}, {"bell": {"map": True}, "phone": {"zone": True}},
                ["bell"], "bell", None):
        updated, reason = web_events.changed_prefs(current, bad)
        assert updated is None and isinstance(reason, str) and reason, bad
    _updated, reason = web_events.changed_prefs(current, {"bell": {"x" * 500: True}})
    assert len(reason) < 80                            # what was sent is cut short


# --- GET / POST /api/alerts/prefs ----------------------------------------------------


async def test_a_new_phone_gets_the_defaults(rig):
    service, client, _saves = rig
    auth, device = await sign_in(client)
    assert await prefs(client, auth) == DEFAULTS
    assert "alerts" not in record_of(service, device)  # nothing saved until it chooses


async def test_a_partial_update_answers_with_everything(rig):
    service, client, saves = rig
    auth, device = await sign_in(client)
    saves.clear()
    response = await client.post("/api/alerts/prefs", headers=auth,
                                 json={"bell": {"map": True}, "phone": {"server": False}})
    assert response.status == 200
    expected = {"groups": GROUPS,
                "bell": {**DEFAULTS["bell"], "map": True},
                "phone": {**DEFAULTS["phone"], "server": False}}
    assert await response.json() == expected
    assert await prefs(client, auth) == expected
    assert saves                                       # wolfrat_web.json is written
    assert record_of(service, device)["alerts"] == {
        "bell": expected["bell"], "phone": expected["phone"]}

    one = await client.post("/api/alerts/prefs", headers=auth,
                            json={"phone": {"objective": True}})
    expected["phone"]["objective"] = True              # the earlier choices stay
    assert await one.json() == expected

    back = await client.post("/api/alerts/prefs", headers=auth, json={"bell": {"map": False}})
    expected["bell"]["map"] = False
    assert await back.json() == expected


async def test_nothing_is_saved_when_nothing_changes(rig):
    service, client, saves = rig
    auth, device = await sign_in(client)
    saves.clear()
    for body in ({}, {"bell": {}}, {"bell": {"join": True}, "phone": {"leave": False}}):
        response = await client.post("/api/alerts/prefs", headers=auth, json=body)
        assert response.status == 200 and await response.json() == DEFAULTS
    assert saves == [] and "alerts" not in record_of(service, device)


@pytest.mark.parametrize("body", [
    {"bell": {"nope": True}},                          # an unknown group
    {"phone": {"zone": True}},                         # a kind is not a group
    {"bell": {"map": "yes"}},                          # not true / false
    {"bell": {"map": 1}},
    {"bell": {"map": 0}},
    {"phone": {"server": None}},
    {"phone": {"server": "false"}},
    {"bell": True},
    {"phone": ["server"]},
    {"sms": {"map": True}},                            # neither bell nor phone
    {"bell": {"map": True, "leave": True, "nope": False}},   # one bad value spoils it all
    {"bell": {"map": True}, "phone": {"server": "off"}},
    ["bell"],
    "bell",
])
async def test_bad_input_is_refused_and_changes_nothing(rig, body):
    service, client, saves = rig
    auth, device = await sign_in(client)
    await client.post("/api/alerts/prefs", headers=auth, json={"bell": {"vote": True}})
    before = await prefs(client, auth)
    saved = copy.deepcopy(record_of(service, device))
    saves.clear()
    response = await client.post("/api/alerts/prefs", headers=auth, json=body)
    assert response.status == 400
    answer = await response.json()
    assert set(answer) == {"error"} and answer["error"]
    assert await prefs(client, auth) == before
    assert record_of(service, device) == saved and saves == []


async def test_a_body_that_is_not_json_is_refused(rig):
    _service, client, _saves = rig
    auth, _device = await sign_in(client)
    response = await client.post("/api/alerts/prefs", headers=auth, data=b"{nope")
    assert response.status == 400 and "error" in await response.json()


async def test_both_routes_need_a_key(rig):
    _service, client, _saves = rig
    assert (await client.get("/api/alerts/prefs")).status == 401
    assert (await client.post("/api/alerts/prefs", json={"bell": {"map": True}})).status == 401
    wrong = {"Authorization": "Bearer not-a-key"}
    assert (await client.get("/api/alerts/prefs", headers=wrong)).status == 401


async def test_two_phones_have_their_own_choices(rig):
    service, client, _saves = rig
    first_auth, first = await sign_in(client, "phone")
    second_auth, second = await sign_in(client, "tablet")
    await client.post("/api/alerts/prefs", headers=first_auth,
                      json={"bell": {"map": True, "join": False}, "phone": {"admin": True}})
    await client.post("/api/alerts/prefs", headers=second_auth,
                      json={"phone": {"server": False, "objective": True}})
    assert await prefs(client, first_auth) == {
        "groups": GROUPS,
        "bell": {**DEFAULTS["bell"], "map": True, "join": False},
        "phone": {**DEFAULTS["phone"], "admin": True}}
    assert await prefs(client, second_auth) == {
        "groups": GROUPS,
        "bell": DEFAULTS["bell"],
        "phone": {**DEFAULTS["phone"], "server": False, "objective": True}}
    third_auth, _third = await sign_in(client, "laptop")
    assert await prefs(client, third_auth) == DEFAULTS
    assert record_of(service, first)["alerts"] != record_of(service, second)["alerts"]


async def test_signing_out_ends_the_choices_with_the_sign_in(rig):
    service, client, _saves = rig
    auth, device = await sign_in(client)
    await client.post("/api/alerts/prefs", headers=auth, json={"bell": {"map": True}})
    assert (await client.post("/api/logout", headers=auth)).status == 200
    assert device not in {record["id"] for record in service.devices_for_config()}
    assert (await client.get("/api/alerts/prefs", headers=auth)).status == 401
    again, _device = await sign_in(client)
    assert await prefs(client, again) == DEFAULTS


async def test_the_server_still_sends_every_event_whatever_the_bell_choices(rig):
    """The page filters; the state, /api/events and the pushes carry them all."""
    service, client, _saves = rig
    auth, _device = await sign_in(client)
    off = {group: False for group in GROUPS}
    await client.post("/api/alerts/prefs", headers=auth, json={"bell": off, "phone": off})
    key = auth["Authorization"].split()[1]
    ws = await client.ws_connect(f"/ws?key={key}")
    assert (await ws.receive_json())["type"] == "state"
    texts = {"join": "Kestrel joined", "leave": "Kestrel left", "map": "New map: Snakebite",
             "vote": "Map vote started", "kick": "Ali was kicked", "ban": "Ali was banned",
             "warn": "Ali was warned", "server": "Game server stopped",
             "zone": "Rebels took zone C (3 of 5)", "flag": "Joint Ops captured a flag (2 of 5)"}
    assert set(texts) == set(web_events.KINDS)
    for kind, text in texts.items():
        added = service.add_event(kind, text)          # zone and flag are taken like the rest
        assert added["kind"] == kind and added["text"] == text
        pushed = await ws.receive_json()
        assert pushed == {"type": "event", "event": added}
    await ws.close()
    state = await (await client.get("/api/status", headers=auth)).json()
    assert [(event["kind"], event["text"]) for event in state["events"]] == list(texts.items())
    listed = await (await client.get("/api/events", headers=auth)).json()
    assert [event["kind"] for event in listed["events"]] == list(texts)


# --- saved in wolfrat_web.json, with the phone's own record ----------------------------


async def test_choices_are_saved_and_loaded_again(rig):
    service, client, _saves = rig
    auth, device = await sign_in(client)
    other_auth, other = await sign_in(client, "tablet")
    await client.post("/api/alerts/prefs", headers=auth,
                      json={"bell": {"map": True, "admin": False}, "phone": {"objective": True}})
    chosen = await prefs(client, auth)
    saved = service.devices_for_config()               # what wolfrat_web.json keeps
    assert "alerts" in record_of(service, device) and "alerts" not in record_of(service, other)

    restarted = new_service()                          # WolfRAT starts again
    restarted.load_devices(copy.deepcopy(saved))
    again = TestClient(TestServer(restarted.application))
    await again.start_server()
    try:
        assert await prefs(again, auth) == chosen      # the same key, the same choices
        assert await prefs(again, other_auth) == DEFAULTS
        assert restarted.devices_for_config() == saved
    finally:
        await again.close()


def test_an_old_settings_file_loads_unchanged():
    """Records written before the choices existed: nothing is added to them."""
    old = [
        {"id": "0a0b0c0d", "name": "Pixel", "key_sha256": "a" * 64, "created": 1700000000,
         "last_seen": 1700000500, "last_ip": "192.168.1.20"},
        {"id": "11223344", "name": "iPad", "key_sha256": "b" * 64, "created": 1700000100,
         "last_seen": 1700000600, "last_ip": None,
         "push": {"endpoint": "https://fcm.googleapis.com/fcm/send/x", "p256dh": "p",
                  "auth": "a"}},
    ]
    service = new_service()
    service.load_devices(copy.deepcopy(old))
    assert service.devices_for_config() == old
    for record in old:
        assert service._alert_prefs(record["id"]) == web_events.default_prefs()
    assert service._alert_prefs("no such device") == web_events.default_prefs()
    assert service._alert_prefs(None) == web_events.default_prefs()


def test_damaged_saved_choices_fall_back_to_the_defaults():
    def record(number, alerts):
        return {"id": f"0000000{number}", "name": "Phone", "key_sha256": f"{number}" * 64,
                "created": 1, "last_seen": 2, "last_ip": None, "alerts": alerts}

    service = new_service()
    service.load_devices([
        record(1, "everything"),
        record(2, ["bell"]),
        record(3, None),
        record(4, {"bell": {"map": "yes", "join": False}, "phone": 5}),
        record(5, {"bell": {"map": True}, "phone": {"server": False, "nope": True}}),
    ])
    loaded = {item["id"]: item for item in service.devices_for_config()}
    assert len(loaded) == 5                            # the phones stay signed in
    for number in (1, 2, 3):                           # not choices at all: none kept
        assert "alerts" not in loaded[f"0000000{number}"]
        assert service._alert_prefs(f"0000000{number}") == web_events.default_prefs()
    assert service._alert_prefs("00000004") == {        # the good switch stays, the rest default
        "bell": {**DEFAULTS["bell"], "join": False}, "phone": DEFAULTS["phone"]}
    assert service._alert_prefs("00000005") == {
        "bell": {**DEFAULTS["bell"], "map": True},
        "phone": {**DEFAULTS["phone"], "server": False}}
    assert loaded["00000005"]["alerts"] == service._alert_prefs("00000005")
