"""Contract v3: phone notifications by kind.  Every group of events goes to
the phones that switched it on (POST /api/alerts/prefs), joins keep their
"first on an empty server" rule, and "game server down" follows the link.

wolfrat/web_push.py is replaced by a fake: nothing is ever sent to a phone.
"""

import asyncio
import socket
import threading
import time
from typing import NamedTuple, Optional

import pytest
from aiohttp.test_utils import TestClient, TestServer

import wolfrat.web_server as web_server_module
from wolfrat.admin_commands import PlayerEntry
from wolfrat.web_server import WolfWebServer

HOST = PlayerEntry(server_id=0, name="Host", team=0)
TITLE = "Test Server"
PHONE_GROUPS = ("join", "leave", "map", "vote", "admin", "server", "objective")


class Manager:
    """The ServerManager snapshot the web server reads, with the two flags
    behind "game server down"."""

    def __init__(self):
        self.is_connected = True
        self.connect_refused = False
        self.disconnected_on_purpose = False
        self.players = []
        self.player_entries = (HOST,)
        self.chat_messages = []
        self.game_state = {"mode": "AAS"}
        self.mission_entries = ()
        self.available_maps_data = ""
        self.game_settings = {"servername": TITLE}

    def put(self, *names):
        self.player_entries = (HOST,) + tuple(
            PlayerEntry(server_id=number, name=name, team=1, kills=0, ping=50)
            for number, name in enumerate(names, 3))


class PushResult(NamedTuple):
    status: int
    ok: bool
    gone: bool
    error: Optional[str]


class FakePush:
    """Stands in for wolfrat/web_push.py: remembers what would have gone out."""

    PushResult = PushResult

    def __init__(self):
        self.sent = []
        self.answers = {}
        self.generated = 0
        self.sessions = []
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
        return None

    async def send(self, session, subscription, payload, keys, *, ttl=3600, urgency="high"):
        self.sent.append({
            "phone": subscription["endpoint"].rsplit("/", 1)[1], "payload": payload,
            "loop": asyncio.get_running_loop(), "thread": threading.get_ident(),
        })
        return self.answers.get(subscription["endpoint"], PushResult(201, True, False, None))

    def took(self):
        """(phone, body, tag) of everything sent so far, then forget it."""
        sent, self.sent = self.sent, []
        return sorted((item["phone"], item["payload"]["body"], item["payload"]["tag"])
                      for item in sent)


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def subscription(name):
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{name}",
            "expirationTime": None, "keys": {"p256dh": f"P256-{name}", "auth": f"AUTH-{name}"}}


@pytest.fixture
def push(monkeypatch):
    fake = FakePush()

    def new_session():
        fake.sessions.append(FakeSession())
        return fake.sessions[-1]

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
    client = TestClient(TestServer(service.application))
    await client.start_server()
    await settle()                                    # the server's own first look...
    service._reset_watch()                            # ...then the test's clock takes over
    try:
        yield service, manager, client
    finally:
        await client.close()


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def phone(client, name, subscribe=True, **switches):
    """Sign a phone in, turn its notifications on (endpoint .../<name>) and set
    its Phone switches; ``all=True`` / ``all=False`` sets every one first."""
    response = await client.post("/api/auth", json={
        "username": "operator", "token": "web-access-token", "device_name": name})
    body = await response.json()
    auth = {"Authorization": f"Bearer {body['key']}"}
    if subscribe:
        answer = await client.post("/api/push/subscribe", headers=auth,
                                   json={"subscription": subscription(name)})
        assert await answer.json() == {"ok": True}
    chosen = {}
    if "all" in switches:
        every = switches.pop("all")
        chosen = {group: every for group in PHONE_GROUPS}
    chosen.update(switches)
    if chosen:
        answer = await client.post("/api/alerts/prefs", headers=auth, json={"phone": chosen})
        assert answer.status == 200
    return auth, body["device_id"]


def note(body, tag, url="/#now"):
    return {"title": TITLE, "body": body, "tag": tag, "url": url}


# --- one notification per event, to the phones that asked for its group ---------------

EVENTS = [
    # kind, text, name, the group it belongs to
    ("leave", "Kestrel left", "Kestrel", "leave"),
    ("map", "New map: Snakebite", None, "map"),
    ("vote", "Map vote started: Snakebite", None, "vote"),
    ("kick", "Ali was kicked by Bob", "Ali", "admin"),
    ("ban", "Ali was banned by Web Admin (Pixel)", "Ali", "admin"),
    ("warn", "Ali was warned", "Ali", "admin"),
    ("zone", "Rebels took zone C (3 of 5)", None, "objective"),
    ("zone", "Rebels took the hill", None, "objective"),
    ("flag", "Joint Ops captured a flag (2 of 5)", None, "objective"),
    ("flag", "Joint Ops scored", None, "objective"),
]


async def test_every_event_of_a_group_goes_to_the_phones_with_that_group_on(rig, push):
    service, _manager, client = rig
    await phone(client, "everything", all=True)
    await phone(client, "defaults")                   # join and server only
    await phone(client, "nothing", all=False)
    await phone(client, "nopush", subscribe=False, all=True)    # switches on, no subscription
    for group in ("leave", "map", "vote", "admin", "objective"):
        await phone(client, f"only-{group}", all=False, **{group: True})

    for kind, text, name, group in EVENTS:
        service.add_event(kind, text, name)
        await settle()
        sent, push.sent = push.sent, []
        assert sorted(item["phone"] for item in sent) == ["everything", f"only-{group}"], kind
        for item in sent:
            assert item["payload"] == note(text, group), kind
    assert len(push.sessions) == 1                    # one session for every send


async def test_join_and_server_events_and_unknown_kinds_send_nothing_by_themselves(rig, push):
    """Joins notify by the first-joins rule and the server group by the link
    rules; the bell's own events of those kinds are not notifications."""
    service, _manager, client = rig
    await phone(client, "everything", all=True)
    for kind, text in (("join", "Kestrel joined"), ("server", "Game server stopped"),
                       ("server", "Lost the link to the game server"),
                       ("swap", "Ali was swapped"), ("", "nothing")):
        service.add_event(kind, text)
        await settle()
    assert push.sent == []


async def test_nobody_on_nothing_sent(rig, push):
    service, _manager, client = rig
    await phone(client, "defaults")                   # admin, map, objective... are off
    await phone(client, "nothing", all=False)
    await phone(client, "nopush", subscribe=False, all=True)
    for kind, text, name, _group in EVENTS:
        service.add_event(kind, text, name)
    await settle()
    assert push.sent == []
    assert push.generated == 0 and push.sessions == []      # not even a key or a session made


async def test_an_event_added_from_another_thread_still_goes_out_on_the_servers_loop(rig, push):
    service, _manager, client = rig
    await phone(client, "pixel", admin=True, objective=True)
    await phone(client, "tablet")
    here = (asyncio.get_running_loop(), threading.get_ident())
    made = {}

    def window_thread():                               # the WolfRAT window's thread
        made["thread"] = threading.get_ident()
        service.admin_event("kick", "Ali", "Bob")      # the Mods tab's hook
        service.add_event("zone", "Rebels took zone C (3 of 5)")

    worker = threading.Thread(target=window_thread)
    worker.start()
    worker.join()
    assert push.sent == []                             # handed to the loop, not sent over there
    await settle()
    assert [(item["phone"], item["payload"]) for item in push.sent] == [
        ("pixel", note("Ali was kicked by Bob", "admin")),
        ("pixel", note("Rebels took zone C (3 of 5)", "objective")),
    ]
    assert made["thread"] != here[1]
    assert all((item["loop"], item["thread"]) == here for item in push.sent)


async def test_the_watchs_own_events_notify_too(rig, push):
    """Leaves, maps and votes come from the web thread's look at the server."""
    from wolfrat.admin_commands import MissionEntry

    service, manager, client = rig
    await phone(client, "pixel", leave=True, map=True, vote=True, join=False)
    manager.put("Ham", "Kestrel")
    manager.mission_entries = (MissionEntry(0, "AAS_Doslin.bms", is_current=True),
                               MissionEntry(1, "TDM_X.bms"))
    service._watch_tick(now=1000)                      # baseline
    manager.put("Ham")
    manager.mission_entries = (MissionEntry(0, "AAS_Doslin.bms"),
                               MissionEntry(1, "TDM_X.bms", is_current=True))
    service.desktop_state = {"top": {"vote": {"kind": "skip", "active": True}}}
    for now in range(1005, 1140, 5):
        service._watch_tick(now=now)
    await settle()
    assert push.took() == [
        ("pixel", "Kestrel left", "leave"),
        ("pixel", "New map: TDM_X", "map"),
        ("pixel", "Vote to skip the map started", "vote"),
    ]


# --- joins: the rule of before, only to phones with joins on ---------------------------


async def test_the_first_joins_only_reach_phones_with_joins_on(rig, push):
    service, manager, client = rig
    await phone(client, "defaults")
    await phone(client, "nojoins", join=False)
    await phone(client, "everything", all=True)
    await phone(client, "nopush", subscribe=False)
    service._watch_tick(now=1000)                      # baseline: empty
    service._watch_tick(now=1600)                      # empty for 10 minutes
    for names, now in ((("Kestrel",), 1605), (("Kestrel", "Ham"), 1610),
                       (("Kestrel", "Ham", "Ali"), 1615)):
        manager.put(*names)
        service._watch_tick(now=now)
        await settle()
    sent = sorted((item["phone"], item["payload"]["body"]) for item in push.sent)
    assert sent == [                                   # the first two, not the third
        ("defaults", "Ham joined (2 players on)"),
        ("defaults", "Kestrel joined (1 player on)"),
        ("everything", "Ham joined (2 players on)"),
        ("everything", "Kestrel joined (1 player on)"),
    ]
    assert all(item["payload"] == note(item["payload"]["body"], "join", "/#players")
               for item in push.sent)
    # The bell still lists every join for every page.
    assert [event["text"] for event in service._events.recent()] == [
        "Kestrel joined", "Ham joined", "Ali joined"]


async def test_no_join_notifications_when_every_phone_switched_joins_off(rig, push):
    service, manager, client = rig
    await phone(client, "a", join=False)
    await phone(client, "b", all=False)
    service._watch_tick(now=1000)
    service._watch_tick(now=1600)
    manager.put("Kestrel")
    service._watch_tick(now=1605)
    await settle()
    assert push.sent == [] and push.generated == 0


# --- "game server down" -----------------------------------------------------------------


def bell(service):
    return [(event["kind"], event["text"]) for event in service._events.recent()]


async def test_a_quick_restart_is_one_notification_when_it_is_back(rig, push):
    service, manager, client = rig
    await phone(client, "defaults")                    # server is on by default
    await phone(client, "noserver", server=False)
    await phone(client, "everything", all=True)
    service._watch_tick(now=1000)                      # the link is up
    manager.is_connected = False                       # the server program stops
    service._watch_tick(now=1005)
    manager.connect_refused = True                     # WolfRAT's next try is refused
    for now in range(1010, 1145, 5):
        service._watch_tick(now=now)
    await settle()
    assert bell(service) == [("server", "Game server stopped")]
    assert service._events.recent()[0]["ts"] == 1005   # when the link dropped
    assert push.sent == []                             # nothing yet: it may be a restart
    manager.is_connected, manager.connect_refused = True, False
    service._watch_tick(now=1005 + 144)
    await settle()
    assert bell(service) == [("server", "Game server stopped"),
                             ("server", "Game server is back (down 2 min 24 s)")]
    assert [(item["phone"], item["payload"]) for item in sorted(
        push.sent, key=lambda item: item["phone"])] == [
        ("defaults", note("Game server restarted (down 2 min 24 s)", "server")),
        ("everything", note("Game server restarted (down 2 min 24 s)", "server")),
    ]


async def test_a_long_stop_is_down_at_3_minutes_then_back(rig, push):
    service, manager, client = rig
    await phone(client, "pixel")
    service._watch_tick(now=1000)
    manager.is_connected, manager.connect_refused = False, True
    for now in range(1005, 1185, 5):
        service._watch_tick(now=now)
    await settle()
    assert push.sent == []
    service._watch_tick(now=1185)                      # 3 minutes after the drop
    await settle()
    assert push.took() == [("pixel", "Game server is down", "server")]
    for now in range(1190, 3000, 5):
        service._watch_tick(now=now)
    await settle()
    assert push.sent == []                             # said once
    manager.is_connected, manager.connect_refused = True, False
    service._watch_tick(now=1005 + 37 * 60)
    await settle()
    assert push.took() == [("pixel", "Game server is back (down 37 min)", "server")]
    assert bell(service) == [("server", "Game server stopped"),
                             ("server", "Game server is back (down 37 min)")]


async def test_a_lost_link_without_a_refusal(rig, push):
    service, manager, client = rig
    await phone(client, "pixel")
    service._watch_tick(now=1000)
    manager.is_connected = False                       # a network fault: tries time out
    for now in range(1005, 1185, 5):
        service._watch_tick(now=now)
    await settle()
    assert bell(service) == [("server", "Lost the link to the game server")]
    assert push.sent == []
    service._watch_tick(now=1185)
    await settle()
    assert push.took() == [("pixel", "Lost the link to the game server", "server")]
    manager.is_connected = True
    service._watch_tick(now=1005 + 300)
    await settle()
    assert push.took() == [("pixel", "Link to the game server is back (down 5 min)", "server")]
    assert bell(service) == [("server", "Lost the link to the game server"),
                             ("server", "Game server link is back (down 5 min)")]


async def test_a_map_load_says_nothing(rig, push):
    service, manager, client = rig
    await phone(client, "everything", all=True)
    service._watch_tick(now=1000)
    for start in (1100, 1300):
        manager.is_connected = False
        for now in range(start, start + 30, 5):
            service._watch_tick(now=now)
        manager.is_connected = True
        service._watch_tick(now=start + 30)
    await settle()
    assert bell(service) == [] and push.sent == []


async def test_the_admin_disconnecting_on_the_pc_is_never_a_server_going_down(rig, push):
    service, manager, client = rig
    await phone(client, "everything", all=True)
    service._watch_tick(now=1000)
    manager.is_connected, manager.disconnected_on_purpose = False, True
    manager.connect_refused = True                     # whatever the last try said
    for now in range(1005, 2000, 5):
        service._watch_tick(now=now)
    manager.disconnected_on_purpose = False            # he presses Connect...
    for now in range(2000, 2400, 5):
        service._watch_tick(now=now)                   # ...and the server is not running
    manager.is_connected, manager.connect_refused = True, False
    service._watch_tick(now=2400)
    await settle()
    assert bell(service) == [] and push.sent == []


async def test_a_server_manager_without_the_flags_is_a_plain_lost_link(rig, push):
    """An older stand-in for the server manager: no refusals are ever seen."""
    service, manager, client = rig
    await phone(client, "pixel")
    del manager.connect_refused, manager.disconnected_on_purpose
    service._watch_tick(now=1000)
    manager.is_connected = False
    for now in range(1005, 1200, 5):
        service._watch_tick(now=now)
    await settle()
    assert bell(service) == [("server", "Lost the link to the game server")]
    assert push.took() == [("pixel", "Lost the link to the game server", "server")]


# --- what stays as it was ----------------------------------------------------------------


async def test_a_signed_out_phone_and_a_gone_subscription_get_nothing_more(rig, push):
    service, _manager, client = rig
    out_auth, _out = await phone(client, "out", all=True)
    _gone_auth, gone = await phone(client, "gone", all=True)
    await phone(client, "kept", all=True)
    push.answers[subscription("gone")["endpoint"]] = PushResult(410, False, True, "HTTP 410")
    assert (await client.post("/api/logout", headers=out_auth)).status == 200
    service.add_event("map", "New map: Snakebite")
    await settle()
    assert push.took() == [("gone", "New map: Snakebite", "map"),
                           ("kept", "New map: Snakebite", "map")]
    records = {record["id"]: record for record in service.devices_for_config()}
    assert "push" not in records[gone]                 # its push service said it left
    assert records[gone]["alerts"]["phone"]["map"] is True     # its choices stay
    service.add_event("map", "New map: Doslin Oblast")
    await settle()
    assert push.took() == [("kept", "New map: Doslin Oblast", "map")]


async def test_the_test_notification_ignores_the_switches(rig, push):
    _service, _manager, client = rig
    auth, _device = await phone(client, "pixel", all=False)
    assert await (await client.post("/api/push/test", headers=auth)).json() == {"ok": True}
    assert [(item["phone"], item["payload"]) for item in push.sent] == [
        ("pixel", {"title": TITLE, "body": "Notifications are on", "tag": "test",
                   "url": "/#more"})]


async def test_a_failing_send_is_logged_and_nothing_else_breaks(rig, push, monkeypatch):
    service, _manager, client = rig
    await phone(client, "pixel", all=True)
    logged = []
    monkeypatch.setattr(web_server_module, "wire_log", logged.append)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("no route")

    monkeypatch.setattr(push, "send", broken)
    event = service.add_event("map", "New map: Snakebite")
    await settle()
    assert event["text"] == "New map: Snakebite"       # the bell has it all the same
    assert any("failed" in line for line in logged)


async def test_nothing_is_sent_once_the_web_server_has_stopped(push):
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    await phone(client, "pixel", all=True)
    service.add_event("map", "New map: Snakebite")
    await settle()
    assert push.took() == [("pixel", "New map: Snakebite", "map")]
    await client.close()                               # Web Admin is switched off
    assert service._notice_loop is None and push.sessions[0].closed
    service.add_event("map", "New map: Doslin Oblast")  # the bell keeps it for later
    worker = threading.Thread(target=service.admin_event, args=("kick", "Ali", "Bob"))
    worker.start()
    worker.join()
    await settle()
    assert push.sent == [] and len(push.sessions) == 1
    assert [event["kind"] for event in service._events.recent()] == ["map", "map", "kick"]


def test_the_running_web_server_sends_on_its_own_thread(push):
    """The real thing: the web server on its own thread and loop (start()),
    events added from this thread as the WolfRAT window does."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=port)
    service.set_auth("operator", "web-access-token")
    _key, device = service.create_device("pixel")
    service._devices[device]["push"] = {"endpoint": subscription("pixel")["endpoint"],
                                        "p256dh": "P256", "auth": "AUTH"}
    service._devices[device]["alerts"] = {"phone": {"admin": True, "objective": True}}

    def sent_bodies(count):
        deadline = time.monotonic() + 5
        while len(push.sent) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return [item["payload"]["body"] for item in push.sent]

    assert service.start().result(timeout=5).ok
    try:
        service.admin_event("warn", "Ali", "Bob")
        service.add_event("flag", "Joint Ops captured a flag (2 of 5)")
        service.add_event("map", "New map: Snakebite")       # switched off: not sent
        assert sent_bodies(2) == ["Ali was warned by Bob", "Joint Ops captured a flag (2 of 5)"]
        server_thread = service._thread.ident
        assert {item["thread"] for item in push.sent} == {server_thread}
        assert server_thread != threading.get_ident()
        assert {item["loop"] for item in push.sent} == {service._loop}
    finally:
        service.stop()
    assert push.sessions[0].closed and service._notice_loop is None
    service.add_event("flag", "Rebels captured a flag")     # stopped: kept for the bell only
    time.sleep(0.05)
    assert len(push.sent) == 2


def test_events_before_the_web_server_ever_started_are_only_kept(push):
    service = WolfWebServer(Manager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    _key, device = service.create_device("phone")
    service._devices[device]["push"] = {"endpoint": "https://fcm.googleapis.com/fcm/send/x",
                                        "p256dh": "p", "auth": "a"}
    service._devices[device]["alerts"] = {"phone": {"admin": True}}
    assert service.admin_event("ban", "Ali", "Bob")["text"] == "Ali was banned by Bob"
    assert push.sent == []
