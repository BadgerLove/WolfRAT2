"""Contract v2 on the server: the bell's events (state, /api/events, WebSocket
pushes, the watch over players / link / map / vote), phone notifications
(/api/push*, "the first couple that join", subscriptions in wolfrat_web.json)
and /static/badge-96.png.

wolfrat/web_push.py is replaced by a fake everywhere except the last test,
which uses its real keys and subscription check with send() replaced: nothing
is ever sent.
"""

import asyncio
import base64
import os
import threading
from typing import NamedTuple, Optional

import pytest
from aiohttp.test_utils import TestClient, TestServer

import wolfrat.web_server as web_server_module
from wolfrat.admin_commands import MissionEntry, PlayerEntry
from wolfrat.web_server import WolfWebServer

HOST = PlayerEntry(server_id=0, name="Host", team=0)


def player(number, name):
    return PlayerEntry(server_id=number, name=name, team=1, kills=0, ping=50)


class Manager:
    """The ServerManager snapshot the web server reads."""

    def __init__(self):
        self.is_connected = True
        self.players = []
        self.player_entries = (HOST,)
        self.chat_messages = []
        self.game_state = {"mode": "AAS"}
        self.mission_entries = ()
        self.available_maps_data = ""
        self.game_settings = {"servername": "FMJ Squad"}

    def put(self, *names):
        self.player_entries = (HOST,) + tuple(
            player(number, name) for number, name in enumerate(names, 3))


class PushResult(NamedTuple):
    status: int
    ok: bool
    gone: bool
    error: Optional[str]


class FakePush:
    """Stands in for wolfrat/web_push.py."""

    PushResult = PushResult

    def __init__(self):
        self.sent = []
        self.answers = {}
        self.generated = 0
        fake = self

        class VapidKeys:
            def __init__(self, private_b64):
                self.private_b64 = private_b64
                self.public_b64 = "B" + private_b64

            @staticmethod
            def generate():
                fake.generated += 1
                return VapidKeys(f"{fake.generated:043d}")

            @staticmethod
            def from_private(private_b64):
                return VapidKeys(private_b64)

        self.VapidKeys = VapidKeys

    @staticmethod
    def valid_subscription(subscription):
        if not isinstance(subscription, dict):
            return "subscription must be an object"
        if not str(subscription.get("endpoint", "")).startswith("https://fcm.googleapis.com/"):
            return "subscription endpoint is not a known push service"
        keys = subscription.get("keys")
        if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
            return "subscription keys missing"
        return None

    async def send(self, session, subscription, payload, keys, *, ttl=3600, urgency="high"):
        self.sent.append({"session": session, "subscription": subscription, "payload": payload,
                          "public": keys.public_b64, "ttl": ttl, "urgency": urgency})
        return self.answers.get(subscription["endpoint"], PushResult(201, True, False, None))


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def subscription(name="a"):
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{name}",
            "expirationTime": None, "keys": {"p256dh": f"P256-{name}", "auth": f"AUTH-{name}"}}


@pytest.fixture
def push(monkeypatch):
    fake = FakePush()
    fake.sessions = []

    def new_session():
        session = FakeSession()
        fake.sessions.append(session)
        return session

    monkeypatch.setattr(web_server_module, "_web_push", lambda: fake)
    monkeypatch.setattr(web_server_module, "_new_push_session", new_session)
    # The tests drive the watch with their own clock.
    monkeypatch.setattr(web_server_module, "WATCH_SECONDS", 3600)
    return fake


@pytest.fixture
async def rig(push):
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    saves = []
    service.on_devices_changed = lambda: saves.append(1)
    client = TestClient(TestServer(service.application))
    await client.start_server()
    await settle()                                    # the server's own first look...
    service._reset_watch()                            # ...then the test's clock takes over
    try:
        yield service, manager, client, saves
    finally:
        await client.close()


async def sign_in(client, name="phone"):
    response = await client.post("/api/auth", json={
        "username": "operator", "token": "web-access-token", "device_name": name})
    body = await response.json()
    return {"Authorization": f"Bearer {body['key']}"}, body["device_id"]


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


# --- events ------------------------------------------------------------------------


async def test_events_are_in_the_state_and_since_an_id(rig):
    service, _manager, client, _saves = rig
    auth, _device = await sign_in(client)
    first = service.add_event("kick", "Ali was kicked by Web Admin", "Ali")
    service.add_event("map", "New map: Snakebite")
    last = service.add_event("ban", "Ali was banned by Bob", "Ali")

    state = await (await client.get("/api/status", headers=auth)).json()
    assert [event["kind"] for event in state["events"]] == ["kick", "map", "ban"]
    assert state["events"][0] == first
    assert set(first) == {"id", "ts", "kind", "text", "name"}
    assert state["events_last_id"] == last["id"]

    since = await (await client.get(f"/api/events?after={first['id']}", headers=auth)).json()
    assert [event["kind"] for event in since["events"]] == ["map", "ban"]
    assert since["last_id"] == last["id"]
    assert (await (await client.get("/api/events", headers=auth)).json())["events"][0] == first
    assert (await client.get("/api/events?after=x", headers=auth)).status == 400
    assert (await client.get("/api/events")).status == 401


async def test_new_events_are_pushed_to_open_pages_from_any_thread(rig):
    service, _manager, client, _saves = rig
    auth, _device = await sign_in(client)
    key = auth["Authorization"].split()[1]
    ws = await client.ws_connect(f"/ws?key={key}")
    assert (await ws.receive_json())["type"] == "state"

    service.add_event("map", "New map: Snakebite")
    message = await asyncio.wait_for(ws.receive_json(), timeout=3)
    assert message["type"] == "event" and message["event"]["text"] == "New map: Snakebite"

    worker = threading.Thread(target=service.admin_event, args=("ban", "Ali", "Web Admin (Pixel)"))
    worker.start()
    worker.join()
    message = await asyncio.wait_for(ws.receive_json(), timeout=3)
    assert message["event"]["kind"] == "ban"
    assert message["event"]["text"] == "Ali was banned by Web Admin (Pixel)"
    assert message["event"]["name"] == "Ali"
    await ws.close()


async def test_admin_events_name_who_did_it(rig):
    service, *_ = rig
    assert service.admin_event("kick", "Ali", "Bob")["text"] == "Ali was kicked by Bob"
    assert service.admin_event("warn", "Ali")["text"] == "Ali was warned"
    assert service.admin_event("swap", "Ali", "Bob") is None


async def test_the_watch_turns_player_lists_into_events_without_the_host(rig):
    service, manager, client, _saves = rig
    auth, _device = await sign_in(client)
    manager.put("Ham")
    service._watch_tick(now=1000)                     # baseline
    manager.put("Ham", "Kestrel")
    service._watch_tick(now=1005)
    manager.put("Ham")
    for now in range(1010, 1140, 5):
        service._watch_tick(now=now)
    state = await (await client.get("/api/status", headers=auth)).json()
    assert [(e["kind"], e["name"], e["text"]) for e in state["events"]] == [
        ("join", "Kestrel", "Kestrel joined"), ("leave", "Kestrel", "Kestrel left")]
    assert state["events"][1]["ts"] == 1005           # the time last seen


async def test_map_vote_and_link_events(rig):
    service, manager, _client, _saves = rig
    manager.mission_entries = (MissionEntry(0, "AAS_Doslin.bms", is_current=True),
                               MissionEntry(1, "TDM_X.bms"))
    service._watch_tick(now=1000)                     # baseline: no events
    manager.mission_entries = (MissionEntry(0, "AAS_Doslin.bms"),
                               MissionEntry(1, "TDM_X.bms", is_current=True))
    service.desktop_state = {"top": {"vote": {"kind": "map", "active": True, "map": "Snakebite"}}}
    service._watch_tick(now=1005)
    service.desktop_state = {"top": {"vote": None}}
    service._watch_tick(now=1010)
    manager.is_connected = False
    service._watch_tick(now=1100)
    service._watch_tick(now=1230)
    manager.is_connected = True
    service._watch_tick(now=1400)
    texts = [(event["kind"], event["text"]) for event in service._events.recent()]
    assert texts == [
        ("map", "New map: TDM_X"),
        ("vote", "Map vote started: Snakebite"),
        ("vote", "Map vote ended"),
        ("server", "Lost the link to the game server"),
        ("server", "Game server link is back (down 5 min)"),
    ]


async def test_a_restarted_web_server_takes_a_new_baseline(push):
    manager = Manager()
    manager.put("Ham")
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    for _ in range(2):
        client = TestClient(TestServer(service.application))
        await client.start_server()
        await settle()                                # its first look is the baseline
        manager.put("Ham", "Kestrel")                 # ...so the next one is a join
        service._watch_tick()
        await client.close()
        manager.put("Ham")
    assert [event["text"] for event in service._events.recent()] == [
        "Kestrel joined", "Kestrel joined"]


# --- notifications for the first joins ------------------------------------------------


async def subscribed_phones(client, count):
    phones = []
    for number in range(count):
        auth, device = await sign_in(client, f"phone {number}")
        response = await client.post("/api/push/subscribe", headers=auth,
                                     json={"subscription": subscription(str(number))})
        assert await response.json() == {"ok": True}
        phones.append((auth, device))
    return phones


async def test_the_first_two_joins_after_an_empty_spell_reach_every_subscribed_phone(rig, push):
    service, manager, client, _saves = rig
    await subscribed_phones(client, 2)
    await sign_in(client, "no notifications")
    service._watch_tick(now=1000)                     # baseline: empty
    service._watch_tick(now=1600)                     # empty for 10 minutes
    for names, now in ((("Kestrel",), 1605), (("Kestrel", "Ham"), 1610),
                       (("Kestrel", "Ham", "Ali"), 1615)):
        manager.put(*names)
        service._watch_tick(now=now)
        await settle()
    payloads = [(item["subscription"]["endpoint"][-1], item["payload"]) for item in push.sent]
    title = "FMJ Squad"
    assert payloads == [
        ("0", {"title": title, "body": "Kestrel joined (1 player on)", "tag": "join",
               "url": "/#players"}),
        ("1", {"title": title, "body": "Kestrel joined (1 player on)", "tag": "join",
               "url": "/#players"}),
        ("0", {"title": title, "body": "Ham joined (2 players on)", "tag": "join",
               "url": "/#players"}),
        ("1", {"title": title, "body": "Ham joined (2 players on)", "tag": "join",
               "url": "/#players"}),
    ]
    assert all(item["ttl"] == 3600 and item["urgency"] == "high" for item in push.sent)
    assert push.sent[0]["subscription"] == {
        "endpoint": "https://fcm.googleapis.com/fcm/send/0",
        "keys": {"p256dh": "P256-0", "auth": "AUTH-0"}}
    assert len(push.sessions) == 1                    # one session for every send


async def test_no_notifications_when_nobody_subscribed(rig, push):
    service, manager, client, _saves = rig
    await sign_in(client)
    service._watch_tick(now=1000)
    service._watch_tick(now=1600)
    manager.put("Kestrel")
    service._watch_tick(now=1605)
    await settle()
    assert push.sent == [] and push.generated == 0


async def test_a_phone_that_unsubscribed_at_its_push_service_is_dropped(rig, push, monkeypatch):
    service, manager, client, saves = rig
    (gone_auth, gone_id), (_auth, kept_id) = await subscribed_phones(client, 2)
    push.answers[subscription("0")["endpoint"]] = PushResult(410, False, True, "HTTP 410")
    push.answers[subscription("1")["endpoint"]] = PushResult(500, False, False, "HTTP 500")
    logged = []
    monkeypatch.setattr(web_server_module, "wire_log", logged.append)
    saves.clear()
    service._watch_tick(now=1000)
    service._watch_tick(now=1600)
    manager.put("Kestrel")
    service._watch_tick(now=1605)
    await settle()
    manager.put("Kestrel", "Ham")
    service._watch_tick(now=1610)
    await settle()
    records = {record["id"]: record for record in service.devices_for_config()}
    assert "push" not in records[gone_id]             # 410: dropped, and saved
    assert saves
    assert records[kept_id]["push"]["endpoint"].endswith("/1")   # 500: kept
    failures = [line for line in logged if "failed" in line and kept_id in line]
    assert len(failures) == 1                         # logged once, not on every send
    state = await (await client.get("/api/push", headers=gone_auth)).json()
    assert state["subscribed"] is False


# --- the /api/push routes ------------------------------------------------------------


async def test_the_key_is_made_once_and_saved(rig, push):
    service, _manager, client, saves = rig
    auth, _device = await sign_in(client)
    saves.clear()
    first = await (await client.get("/api/push", headers=auth)).json()
    assert first == {"public_key": "B" + "1".zfill(43), "subscribed": False, "first_joins": 2}
    assert saves                                      # the new key goes to wolfrat_web.json
    assert service.push_config() == {"notify_first_joins": 2,
                                     "web_push_vapid": "1".zfill(43)}
    again = await (await client.get("/api/push", headers=auth)).json()
    assert again["public_key"] == first["public_key"] and push.generated == 1


async def test_a_saved_key_is_used_and_not_remade(push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.load_push_config({"web_push_vapid": "s" * 43, "notify_first_joins": 4})
    assert service._vapid_keys().public_b64 == "B" + "s" * 43
    assert push.generated == 0 and service.notify_first_joins == 4


async def test_subscribe_checks_replaces_and_keeps_one_per_phone(rig, push):
    service, _manager, client, saves = rig
    auth, device = await sign_in(client)
    other_auth, other = await sign_in(client, "tablet")
    for body in ({"subscription": {"endpoint": "https://evil.example/x",
                                   "keys": {"p256dh": "p", "auth": "a"}}},
                 {"subscription": "nope"}, {}, []):
        response = await client.post("/api/push/subscribe", headers=auth, json=body)
        assert response.status == 400, body
        assert (await response.json())["ok"] is False
    bad = await client.post("/api/push/subscribe", headers=auth,
                            json={"subscription": {"endpoint": "https://evil.example/x",
                                                   "keys": {"p256dh": "p", "auth": "a"}}})
    assert (await bad.json())["error"] == "subscription endpoint is not a known push service"

    saves.clear()
    assert (await (await client.post("/api/push/subscribe", headers=auth,
                                     json={"subscription": subscription("a")})).json()) == {
        "ok": True}
    assert saves
    record = {r["id"]: r for r in service.devices_for_config()}[device]
    assert record["push"] == {"endpoint": "https://fcm.googleapis.com/fcm/send/a",
                              "p256dh": "P256-a", "auth": "AUTH-a"}
    assert (await (await client.get("/api/push", headers=auth)).json())["subscribed"] is True
    assert (await (await client.get("/api/push", headers=other_auth)).json())["subscribed"] is False

    await client.post("/api/push/subscribe", headers=auth, json={"subscription": subscription("b")})
    assert service._device_push(device)["endpoint"].endswith("/b")      # replaced
    # The same browser signed in under another key: it keeps one subscription.
    await client.post("/api/push/subscribe", headers=other_auth,
                      json={"subscription": subscription("b")})
    assert service._device_push(other)["endpoint"].endswith("/b")
    assert service._device_push(device) is None


async def test_unsubscribe(rig, push):
    service, _manager, client, _saves = rig
    auth, device = await sign_in(client)
    await client.post("/api/push/subscribe", headers=auth, json={"subscription": subscription()})
    assert await (await client.post("/api/push/unsubscribe", headers=auth)).json() == {"ok": True}
    assert service._device_push(device) is None
    assert await (await client.post("/api/push/unsubscribe", headers=auth)).json() == {"ok": True}


async def test_the_test_notification_goes_to_this_phone_only(rig, push):
    service, _manager, client, _saves = rig
    auth, device = await sign_in(client)
    other_auth, _other = await sign_in(client, "tablet")
    await client.post("/api/push/subscribe", headers=other_auth,
                      json={"subscription": subscription("other")})
    not_on = await (await client.post("/api/push/test", headers=auth)).json()
    assert not_on["ok"] is False and "not on" in not_on["error"]

    await client.post("/api/push/subscribe", headers=auth, json={"subscription": subscription()})
    assert await (await client.post("/api/push/test", headers=auth)).json() == {"ok": True}
    assert [item["subscription"]["endpoint"][-1] for item in push.sent] == ["a"]
    assert push.sent[0]["payload"] == {"title": "FMJ Squad", "body": "Notifications are on",
                                       "tag": "test", "url": "/#more"}

    push.answers[subscription()["endpoint"]] = PushResult(400, False, False, "HTTP 400 bad")
    failed = await (await client.post("/api/push/test", headers=auth)).json()
    assert failed == {"ok": False, "error": "HTTP 400 bad"}
    push.answers[subscription()["endpoint"]] = PushResult(404, False, True, "HTTP 404")
    gone = await (await client.post("/api/push/test", headers=auth)).json()
    assert gone["ok"] is False and "turn them on again" in gone["error"]
    assert service._device_push(device) is None


async def test_the_title_falls_back_to_wolfrat(rig, push):
    service, manager, client, _saves = rig
    manager.game_settings = {}
    auth, _device = await sign_in(client)
    await client.post("/api/push/subscribe", headers=auth, json={"subscription": subscription()})
    await client.post("/api/push/test", headers=auth)
    assert push.sent[-1]["payload"]["title"] == "WolfRAT"
    service.desktop_state = {"top": {"server": {"name": "Desktop Name"}}}
    await client.post("/api/push/test", headers=auth)
    assert push.sent[-1]["payload"]["title"] == "Desktop Name"


async def test_the_first_joins_setting(rig, push):
    service, _manager, client, saves = rig
    auth, _device = await sign_in(client)
    saves.clear()
    answer = await client.post("/api/push/settings", headers=auth, json={"first_joins": 3})
    assert await answer.json() == {"ok": True, "first_joins": 3}
    assert saves and service.push_config()["notify_first_joins"] == 3
    assert (await (await client.get("/api/push", headers=auth)).json())["first_joins"] == 3
    zero = await client.post("/api/push/settings", headers=auth, json={"first_joins": 0})
    assert (await zero.json())["first_joins"] == 0
    for value in (11, -1, "2", True, 2.5, None):
        response = await client.post("/api/push/settings", headers=auth,
                                     json={"first_joins": value})
        assert response.status == 400, value
    assert (await client.post("/api/push/settings", headers=auth, json={})).status == 400
    assert service.notify_first_joins == 0


async def test_every_push_route_needs_a_key(rig):
    _service, _manager, client, _saves = rig
    assert (await client.get("/api/push")).status == 401
    for path in ("subscribe", "unsubscribe", "test", "settings"):
        assert (await client.post(f"/api/push/{path}", json={})).status == 401, path


async def test_features_push_follows_web_push(rig, monkeypatch):
    service, _manager, client, _saves = rig
    auth, _device = await sign_in(client)
    assert (await (await client.get("/api/status", headers=auth)).json())["features"]["push"] is True

    def missing():
        raise ImportError("No module named 'cryptography'")

    monkeypatch.setattr(web_server_module, "_web_push", missing)
    service._push_ok = None                           # as a WolfRAT built without it
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["features"]["push"] is False
    response = await client.get("/api/push", headers=auth)
    assert response.status == 503
    assert (await response.json())["error"] == "Notifications are not available in this WolfRAT"


# --- storage ------------------------------------------------------------------------


def test_subscriptions_go_with_their_device(push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "token")
    _key, first = service.create_device("phone")
    _key, second = service.create_device("tablet")
    for device in (first, second):
        service._devices[device]["push"] = {"endpoint": "https://fcm.googleapis.com/x",
                                            "p256dh": "p", "auth": "a"}
    service.sign_out_device(first)
    assert [record["id"] for record in service.devices_for_config()] == [second]
    service.set_auth("operator", "new token")         # Regenerate Token
    assert service.devices_for_config() == []


def test_saved_subscriptions_and_settings_load_back(push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    _key, device = service.create_device("phone")
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/a", "p256dh": "p", "auth": "a"}
    service._devices[device]["push"] = good
    saved = service.devices_for_config()
    assert saved[0]["push"] == good
    bad_ones = [
        {"endpoint": "http://fcm.googleapis.com/x", "p256dh": "p", "auth": "a"},
        {"endpoint": "https://fcm.googleapis.com/x", "p256dh": "p"},
        {"endpoint": "https://fcm.googleapis.com/" + "x" * 3000, "p256dh": "p", "auth": "a"},
        "not a record",
    ]
    records = [dict(saved[0], id=f"0000000{n}", push=bad) for n, bad in enumerate(bad_ones)]

    again = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    again.load_devices(saved + records)
    loaded = {record["id"]: record for record in again.devices_for_config()}
    assert loaded[device]["push"] == good
    assert all("push" not in loaded[record["id"]] for record in records)

    again.load_push_config({"web_push_vapid": "v" * 43, "notify_first_joins": 5})
    assert again.push_config() == {"web_push_vapid": "v" * 43, "notify_first_joins": 5}
    for bad in ({"notify_first_joins": 11}, {"notify_first_joins": "3"},
                {"notify_first_joins": True}, {}):
        fresh = WolfWebServer(Manager(), host="127.0.0.1", port=0)
        fresh.load_push_config({**bad, "web_push_vapid": "not a key!"})
        assert fresh.push_config() == {"notify_first_joins": 2}


# --- the notification session, the badge ---------------------------------------------


async def test_the_session_is_made_once_and_closed_with_the_server(push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    auth, _device = await sign_in(client)
    await client.post("/api/push/subscribe", headers=auth, json={"subscription": subscription()})
    assert push.sessions == []                        # made when first needed
    await client.post("/api/push/test", headers=auth)
    await client.post("/api/push/test", headers=auth)
    assert len(push.sessions) == 1 and not push.sessions[0].closed
    await client.close()
    assert push.sessions[0].closed
    assert service._watch_task is None


async def test_the_badge_is_served_only_when_it_is_there(tmp_path, push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    service._template_path = str(tmp_path)
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        assert (await client.get("/static/badge-96.png")).status == 404
        (tmp_path / "badge-96.png").write_bytes(b"\x89PNG badge")
        response = await client.get("/static/badge-96.png")
        assert response.status == 200 and response.content_type == "image/png"
        assert await response.read() == b"\x89PNG badge"
        assert response.headers["Cache-Control"] == "no-cache"
    finally:
        await client.close()


# --- with the real web_push (keys and the subscription check; send() replaced) --------


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def test_real_keys_and_subscription_check(monkeypatch):
    web_push = pytest.importorskip("wolfrat.web_push")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    sent = []

    async def fake_send(session, sub, payload, keys, *, ttl=3600, urgency="high"):
        sent.append((sub, payload, ttl, urgency))
        return web_push.PushResult(201, True, False, None)

    monkeypatch.setattr(web_push, "send", fake_send)
    monkeypatch.setattr(web_server_module, "_new_push_session", FakeSession)
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        auth, _device = await sign_in(client)
        info = await (await client.get("/api/push", headers=auth)).json()
        text = info["public_key"]
        public = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        assert len(public) == 65 and public[0] == 4
        saved = service.push_config()["web_push_vapid"]
        assert web_push.VapidKeys.from_private(saved).public_b64 == info["public_key"]

        phone_key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        real = {"endpoint": "https://fcm.googleapis.com/fcm/send/real-phone",
                "expirationTime": None,
                "keys": {"p256dh": _b64url(phone_key), "auth": _b64url(os.urandom(16))}}
        stranger = dict(real, endpoint="https://push.example.net/x")
        refused = await client.post("/api/push/subscribe", headers=auth,
                                    json={"subscription": stranger})
        assert refused.status == 400
        assert (await refused.json())["error"] == \
            "subscription endpoint is not a known push service"
        accepted = await client.post("/api/push/subscribe", headers=auth,
                                     json={"subscription": real})
        assert await accepted.json() == {"ok": True}
        assert await (await client.post("/api/push/test", headers=auth)).json() == {"ok": True}
        sub, payload, ttl, urgency = sent[0]
        assert sub == {"endpoint": real["endpoint"], "keys": real["keys"]}
        assert web_push.valid_subscription(sub) is None
        assert payload["tag"] == "test" and (ttl, urgency) == (3600, "high")
    finally:
        await client.close()


# --- the Mods tab's hook (chat commands and the phone's buttons) ----------------------


def test_the_mods_tab_tells_the_page_once_the_server_took_it(qtbot, tmp_path, monkeypatch):
    import json

    from wolfrat import app as app_module
    from wolfrat import manual_mode
    from wolfrat.app import DesktopRuntime, MissionsStore, ModsTab
    from wolfrat.mod_ranks import ADMIN
    from tests.test_own_chat_lines import FakeServer

    manual_mode.state.reset()
    runtime = DesktopRuntime.isolated(tmp_path)
    with open(runtime.path("wolfrat_mods.json"), "w") as handle:
        json.dump({"mods": ["FMJ-BadgerLove"], "mod_ranks": {"FMJ-BadgerLove": ADMIN}}, handle)
    tab = ModsTab(FakeServer(), MissionsStore(runtime), runtime)
    qtbot.addWidget(tab)
    tab._send_mod_chat = lambda message, context="": None
    server_takes_it = {"ok": True}

    def submit_admin(owner, operation, accepted, context, on_failure=None, policy=None):
        if server_takes_it["ok"]:
            accepted(None)
        elif on_failure is not None:
            on_failure("refused")

    monkeypatch.setattr(app_module, "submit_admin", submit_admin)
    events = []
    tab.on_admin_event = lambda *event: events.append(event)
    tab.update_chat([])                                # the first batch is history

    assert tab.run_web_command("!kick Griefer")["ok"] is True
    tab.update_chat([{"id": 900, "text": "FMJ-BadgerLove: !ban Griefer",
                      "raw": "FMJ-BadgerLove: !ban Griefer"}])
    assert tab.run_web_command("!warn Griefer language")["ok"] is True
    assert events == [("kick", "Griefer", "Web Admin"),
                      ("ban", "Griefer", "FMJ-BadgerLove"),
                      ("warn", "Griefer", "Web Admin")]

    server_takes_it["ok"] = False
    tab.run_web_command("!kick Griefer")
    assert len(events) == 3                            # not taken: no alert
    server_takes_it["ok"] = True
    tab.on_admin_event = None
    tab.run_web_command("!kick Griefer")               # no hook: the kick still goes
    assert len(events) == 3
    manual_mode.state.reset()
