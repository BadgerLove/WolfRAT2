"""Web Admin sign-in (2026-11-23): one key per phone, pairing codes, the
"this network only" gate, security headers and the page's fixed files."""

import asyncio
import hashlib
import json
import re
import socket
import time

import pytest
from aiohttp import ClientSession, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import wolfrat.web_server as web_server_module
from wolfrat.web_server import (
    PAIR_ALPHABET,
    WolfWebServer,
    is_local_address,
    normalize_pair_code,
)

from tests.test_web_http_integration import FakeServerManager

GOOD = {"username": "operator", "token": "web-access-token"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; "
    "base-uri 'none'"
)


def make_service():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    return service


@pytest.fixture
async def rig():
    service = make_service()
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client
    finally:
        await client.close()


async def sign_in(client, **extra):
    response = await client.post("/api/auth", json={**GOOD, **extra})
    assert response.status == 200, await response.text()
    return await response.json()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# --- device keys -------------------------------------------------------------


async def test_each_sign_in_gets_its_own_key_kept_only_as_a_hash(rig):
    service, client = rig
    first = await sign_in(client, device_name="Dale's phone")
    second = await sign_in(client)

    assert first["key"] != second["key"]
    assert re.fullmatch(r"[0-9a-f]{8}", first["device_id"])
    saved = service.devices_for_config()
    assert {record["id"] for record in saved} == {first["device_id"], second["device_id"]}
    for record in saved:
        assert set(record) == {"id", "name", "key_sha256", "created", "last_seen", "last_ip"}
    record = next(r for r in saved if r["id"] == first["device_id"])
    assert record["key_sha256"] == hashlib.sha256(first["key"].encode()).hexdigest()
    assert record["name"] == "Dale's phone"
    assert record["last_ip"] == "127.0.0.1"
    assert first["key"] not in json.dumps(saved)
    assert "web-access-token" not in json.dumps(saved)


async def test_device_name_comes_from_the_browser_when_not_given(rig):
    _service, client = rig
    response = await client.post(
        "/api/auth", json=GOOD,
        headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120"},
    )
    body = await response.json()
    devices = await (await client.get("/api/devices", headers=bearer(body["key"]))).json()
    assert devices["devices"][0]["name"] == "Android phone"


async def test_the_access_token_opens_nothing_but_sign_in(rig):
    _service, client = rig
    for path in ("/api/status", "/api/devices", "/api/chat"):
        response = await client.get(path, headers=bearer("web-access-token"))
        assert response.status == 401, path
    for query in ("token=web-access-token", "key=web-access-token"):
        response = await client.get(f"/ws?{query}")
        assert response.status == 401


async def test_the_key_in_the_query_string_works_on_ws_only(rig):
    _service, client = rig
    key = (await sign_in(client))["key"]
    assert (await client.get(f"/api/status?key={key}")).status == 401
    assert (await client.get(f"/api/status?token={key}")).status == 401
    ws = await client.ws_connect(f"/ws?key={key}")
    assert (await ws.receive_json())["type"] == "state"
    await ws.close()


async def test_logout_ends_this_key_and_its_connections(rig):
    service, client = rig
    mine = await sign_in(client)
    other = await sign_in(client)
    my_socket = await client.ws_connect(f"/ws?key={mine['key']}")
    other_socket = await client.ws_connect(f"/ws?key={other['key']}")
    await my_socket.receive_json()
    await other_socket.receive_json()

    response = await client.post("/api/logout", headers=bearer(mine["key"]))
    assert await response.json() == {"ok": True}

    closing = await asyncio.wait_for(my_socket.receive(), timeout=3)
    assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
    assert my_socket.close_code == 4001
    assert (await client.get("/api/status", headers=bearer(mine["key"]))).status == 401
    # The other phone carries on.
    assert (await client.get("/api/status", headers=bearer(other["key"]))).status == 200
    assert not other_socket.closed
    await other_socket.close()


async def test_device_list_and_signing_out_another_device(rig):
    service, client = rig
    mine = await sign_in(client, device_name="Phone A")
    other = await sign_in(client, device_name="Phone B")
    other_socket = await client.ws_connect(f"/ws?key={other['key']}")
    await other_socket.receive_json()

    listing = await (await client.get("/api/devices", headers=bearer(mine["key"]))).json()
    rows = {row["id"]: row for row in listing["devices"]}
    assert set(rows) == {mine["device_id"], other["device_id"]}
    assert rows[mine["device_id"]]["this_device"] is True
    assert rows[other["device_id"]]["this_device"] is False
    assert set(rows[mine["device_id"]]) == {
        "id", "name", "created", "last_seen", "last_ip", "this_device"}
    assert "key_sha256" not in json.dumps(listing)

    response = await client.post(
        "/api/devices/signout", headers=bearer(mine["key"]), json={"id": other["device_id"]}
    )
    assert await response.json() == {"ok": True}
    closing = await asyncio.wait_for(other_socket.receive(), timeout=3)
    assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
    assert other_socket.close_code == 4001
    assert (await client.get("/api/status", headers=bearer(other["key"]))).status == 401

    missing = await client.post(
        "/api/devices/signout", headers=bearer(mine["key"]), json={"id": "deadbeef"}
    )
    assert missing.status == 404
    assert (await missing.json())["ok"] is False


async def test_desktop_sign_out_closes_that_phones_socket_on_its_next_message(rig):
    service, client = rig
    phone = await sign_in(client)
    ws = await client.ws_connect(f"/ws?key={phone['key']}")
    await ws.receive_json()
    assert service.sign_out_device(phone["device_id"]) is True
    await ws.send_json({"type": "chat", "message": "hello"})
    closing = await asyncio.wait_for(ws.receive(), timeout=3)
    assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
    assert ws.close_code == 4001
    assert service.sign_out_device(phone["device_id"]) is False


async def test_a_new_token_forgets_every_device(rig):
    service, client = rig
    changes = []
    service.on_devices_changed = lambda: changes.append(len(service.devices_for_config()))
    phone = await sign_in(client)
    service.set_auth("operator", "web-access-token")      # same token: nothing happens
    assert (await client.get("/api/status", headers=bearer(phone["key"]))).status == 200

    service.set_auth("operator", "new-token")              # Regenerate Token
    assert service.devices_for_config() == []
    assert (await client.get("/api/status", headers=bearer(phone["key"]))).status == 401
    assert changes[-1] == 0


def test_last_seen_is_written_at_most_once_a_minute():
    service = make_service()
    calls = []
    service.on_devices_changed = lambda: calls.append(1)
    _key, device_id = service.create_device("phone", "10.0.0.2", now=1000)
    calls.clear()

    service._touch_device(device_id, "10.0.0.3", now=1030)
    record = service.devices_for_config()[0]
    assert (record["last_seen"], record["last_ip"]) == (1000, "10.0.0.2")
    assert calls == []

    service._touch_device(device_id, "10.0.0.3", now=1061)
    record = service.devices_for_config()[0]
    assert (record["last_seen"], record["last_ip"]) == (1061, "10.0.0.3")
    assert calls == [1]


def test_saved_devices_load_back_and_bad_records_are_skipped():
    service = make_service()
    key, device_id = service.create_device("phone", "10.0.0.2", now=1000)
    saved = service.devices_for_config()

    again = make_service()
    again.load_devices(saved + [
        {"id": "nothex!!", "key_sha256": "0" * 64},
        {"id": "abcdef01", "key_sha256": "short"},
        "not a record",
    ])
    assert [record["id"] for record in again.devices_for_config()] == [device_id]
    assert again._device_for_key(key)["id"] == device_id
    assert again._device_for_key("some-other-key") is None


def test_the_oldest_unused_device_goes_when_the_list_is_full(monkeypatch):
    monkeypatch.setattr(web_server_module, "MAX_DEVICES", 3)
    service = make_service()
    ids = [service.create_device(f"p{n}", now=1000 + n)[1] for n in range(3)]
    newest = service.create_device("p3", now=2000)[1]
    kept = {record["id"] for record in service.devices_for_config()}
    assert kept == {ids[1], ids[2], newest}


def test_no_access_token_means_no_key_works():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    key, _device_id = service.create_device("phone")
    assert service._device_for_key(key) is None


# --- pairing -------------------------------------------------------------------


def test_pairing_codes_are_six_easy_characters():
    service = make_service()
    for _ in range(50):
        code, expires_at = service.start_pairing(now=100.0)
        assert len(code) == 6 and set(code) <= set(PAIR_ALPHABET)
        assert expires_at == 100.0 + 300
    assert normalize_pair_code(" k7m-3qx ") == "K7M3QX"


async def test_a_pairing_code_works_once(rig):
    service, client = rig
    paired = []
    service.on_paired = lambda device_id, name: paired.append((device_id, name))
    code, _expires = service.start_pairing()

    typed = f" {code[:3].lower()} {code[3:].lower()} "
    response = await client.post("/api/pair", json={"code": typed, "device_name": "Tablet"})
    assert response.status == 200
    body = await response.json()
    assert set(body) == {"ok", "key", "device_id"} and body["ok"] is True
    assert paired == [(body["device_id"], "Tablet")]
    assert (await client.get("/api/status", headers=bearer(body["key"]))).status == 200

    again = await client.post("/api/pair", json={"code": code})
    assert again.status == 401
    assert service.pairing_code() is None


async def test_an_expired_or_cancelled_code_is_refused(rig):
    service, client = rig
    code, _expires = service.start_pairing(now=time.time() - 301)
    assert (await client.post("/api/pair", json={"code": code})).status == 401
    code, _expires = service.start_pairing()
    service.cancel_pairing()
    assert (await client.post("/api/pair", json={"code": code})).status == 401


async def test_wrong_codes_count_with_the_login_lockout(rig):
    service, client = rig
    code, _expires = service.start_pairing()
    wrong = [c * 6 for c in PAIR_ALPHABET if c * 6 != code][:5]
    statuses = [
        (await client.post("/api/pair", json={"code": guess})).status for guess in wrong
    ]
    assert statuses == [401, 401, 401, 401, 429]
    locked = await client.post("/api/auth", json={"username": "operator", "token": "nope"})
    assert locked.status == 429
    assert "Retry-After" in locked.headers
    # The right code still works while the address is locked.
    assert (await client.post("/api/pair", json={"code": code})).status == 200


async def test_a_malformed_code_is_a_400_and_not_counted(rig):
    service, client = rig
    service.start_pairing()
    for body in ({"code": ""}, {"code": 12}, {"code": "ABC"}, {"code": "ABCDE0"}, {}):
        response = await client.post("/api/pair", json=body)
        assert response.status == 400, body
    assert service._lockout_remaining("127.0.0.1") == 0
    assert service._failed_logins == {}


# --- the network gate --------------------------------------------------------


@pytest.mark.parametrize("address, local", [
    ("127.0.0.1", True),
    ("10.1.2.3", True),
    ("172.16.0.1", True),
    ("172.31.255.255", True),
    ("192.168.1.20", True),
    ("169.254.10.10", True),
    ("100.64.0.1", True),
    ("100.127.255.255", True),
    ("::1", True),
    ("fd12:3456::1", True),
    ("fe80::1%12", True),
    ("::ffff:192.168.1.5", True),
    ("8.8.8.8", False),
    ("172.32.0.1", False),
    ("100.128.0.1", False),
    ("203.0.113.7", False),
    ("2001:4860:4860::8888", False),
    ("::ffff:8.8.8.8", False),
    ("", False),
    (None, False),
    ("not-an-address", False),
])
def test_what_counts_as_this_network(address, local):
    assert is_local_address(address) is local


async def test_internet_phones_are_refused_unless_allowed(rig, monkeypatch):
    service, client = rig
    key = (await sign_in(client))["key"]
    monkeypatch.setattr(web_server_module, "is_local_address", lambda _address: False)
    assert service.allow_internet is False

    api = await client.get("/api/status", headers=bearer(key))
    assert api.status == 403
    assert (await api.json())["error"].startswith(
        "This WolfRAT only takes phones on its own network.")
    page = await client.get("/")
    assert page.status == 403
    assert page.content_type == "text/html"
    assert "own network" in await page.text()
    for path in ("/static/app.js", "/sw.js", "/api/auth", "/no-such-page"):
        assert (await client.get(path)).status == 403, path
    assert (await client.post("/api/auth", json=GOOD)).status == 403
    assert (await client.get(f"/ws?key={key}")).status == 403
    assert page.headers["X-Frame-Options"] == "DENY"

    service.allow_internet = True
    assert (await client.get("/api/status", headers=bearer(key))).status == 200
    assert (await client.get("/")).status == 200


async def test_turning_internet_off_closes_internet_sockets_only(rig, monkeypatch):
    service, client = rig
    service.allow_internet = True
    key = (await sign_in(client))["key"]
    local_ws = await client.ws_connect(f"/ws?key={key}")
    await local_ws.receive_json()
    internet_ws = await client.ws_connect(f"/ws?key={key}")
    await internet_ws.receive_json()
    # Make the second connection look like it came from the internet.
    with service._ws_lock:
        last = list(service._ws_info)[-1]
        info = service._ws_info[last]
        service._ws_info[last] = type(info)(info.device_id, "203.0.113.7", info.loop)

    service.allow_internet = False
    closing = await asyncio.wait_for(internet_ws.receive(), timeout=3)
    assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
    assert not local_ws.closed
    await local_ws.close()


async def test_the_internet_address_is_learned_from_signed_in_phones_only(rig, monkeypatch):
    service, client = rig
    key = (await sign_in(client))["key"]
    service.allow_internet = True
    monkeypatch.setattr(web_server_module, "is_local_address", lambda _address: False)

    await client.get("/api/status", headers={"Host": "evil.example:8070"})
    await client.get("/api/status", headers={**bearer("wrong"), "Host": "evil.example:8070"})
    assert service.internet_address is None

    await client.get("/api/status", headers={**bearer(key), "Host": "203.0.113.7:8070"})
    assert service.internet_address == "203.0.113.7:8070"
    await client.get("/api/status", headers={**bearer(key), "Host": "bad host/<x>"})
    assert service.internet_address == "203.0.113.7:8070"


# --- headers, static files, limits --------------------------------------------


async def test_every_response_carries_the_security_headers(rig):
    service, client = rig
    key = (await sign_in(client))["key"]
    responses = {
        "/": await client.get("/"),
        "/static/app.js": await client.get("/static/app.js"),
        "/api/status": await client.get("/api/status", headers=bearer(key)),
        "/api/status (401)": await client.get("/api/status"),
        "/api/auth (400)": await client.post("/api/auth", data="{"),
        "/missing (404)": await client.get("/missing"),
        "/sw.js (404 when absent)": await client.get("/sw.js"),
    }
    for label, response in responses.items():
        headers = response.headers
        assert headers["X-Frame-Options"] == "DENY", label
        assert headers["X-Content-Type-Options"] == "nosniff", label
        assert headers["Referrer-Policy"] == "no-referrer", label
        assert headers["Content-Security-Policy"] == CSP, label
    assert responses["/api/status"].headers["Cache-Control"] == "no-store"
    assert responses["/api/status (401)"].headers["Cache-Control"] == "no-store"
    assert responses["/"].headers["Cache-Control"] == "no-cache"
    assert responses["/static/app.js"].headers["Cache-Control"] == "no-cache"


async def test_the_websocket_handshake_carries_the_headers_too(rig):
    _service, client = rig
    key = (await sign_in(client))["key"]
    ws = await client.ws_connect(f"/ws?key={key}")
    assert ws._response.headers["X-Frame-Options"] == "DENY"
    assert ws._response.headers["Content-Security-Policy"] == CSP
    await ws.close()


async def test_the_new_page_files_are_served_only_when_they_exist(tmp_path):
    service = make_service()
    service._template_path = str(tmp_path)
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        names = {
            "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/sw.js": ("sw.js", "application/javascript"),
            "/static/icon-192.png": ("icon-192.png", "image/png"),
            "/static/icon-512.png": ("icon-512.png", "image/png"),
            "/static/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
        }
        for path in names:
            assert (await client.get(path)).status == 404, path
        for path, (filename, _type) in names.items():
            (tmp_path / filename).write_bytes(b"\x89PNG-or-text " + filename.encode())
        for path, (filename, content_type) in names.items():
            response = await client.get(path)
            assert response.status == 200, path
            assert response.content_type == content_type
            assert await response.read() == b"\x89PNG-or-text " + filename.encode()
        # Fixed names only: nothing else in the folder is reachable.
        (tmp_path / "secret.txt").write_text("x")
        assert (await client.get("/static/secret.txt")).status == 404
        assert (await client.get("/static/../secret.txt")).status in (403, 404)
    finally:
        await client.close()


async def test_oversized_bodies_are_refused(rig):
    _service, client = rig
    response = await client.post(
        "/api/auth", data=json.dumps({"username": "x" * 100_000, "token": "y"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 413


async def test_non_ascii_credentials_are_a_clean_refusal(rig):
    _service, client = rig
    response = await client.post("/api/auth", json={"username": "operätor", "token": "tökén"})
    assert response.status == 401
    response = await client.get("/api/status", headers={"Authorization": "Bearer clé"})
    assert response.status == 401


async def test_every_route_but_the_page_and_sign_in_needs_a_key(rig):
    service, client = rig
    public = {"/api/auth", "/api/pair", *web_server_module.STATIC_FILES}
    for route in service.application.router.routes():
        path = route.resource.canonical
        if path in public or route.method == "HEAD":
            continue
        response = await client.request(route.method, path)
        assert response.status == 401, (route.method, path)


# --- pushes to open pages ------------------------------------------------------


def test_a_push_asked_for_inside_the_second_still_goes_out():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=port)
    service.set_auth("operator", "web-access-token")
    key, _device_id = service.create_device("phone")
    assert service.start().result(timeout=5).ok

    async def scenario():
        async with ClientSession() as session:
            ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?key={key}")
            assert (await ws.receive_json())["type"] == "state"
            service.broadcast_state()
            first = await asyncio.wait_for(ws.receive_json(), timeout=3)
            service.broadcast_state()      # inside the same second: sent at its end
            second = await asyncio.wait_for(ws.receive_json(), timeout=3)
            await ws.close()
            return first, second

    try:
        first, second = asyncio.run(scenario())
    finally:
        service.stop()
    assert first["type"] == second["type"] == "state"
    assert second["timestamp"] - first["timestamp"] >= 0.9
