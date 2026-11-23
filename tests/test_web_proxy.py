"""Contract v2, behind a proxy on this PC (nginx for an https:// address): the
phone's address from X-Forwarded-For (loopback peers only) everywhere the
peer address was used, the https internet address for the pairing QR, and
server.secure_address."""

import types

import pytest
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

import wolfrat.web_server as web_server_module
from wolfrat.web_server import (
    WolfWebServer,
    clean_internet_address,
    client_address,
    forwarded_https,
    phone_address_url,
)

from tests.test_web_http_integration import FakeServerManager

GOOD = {"username": "operator", "token": "web-access-token"}


def request(remote, *headers):
    return types.SimpleNamespace(remote=remote, headers=CIMultiDict(headers))


@pytest.fixture
async def rig():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client
    finally:
        await client.close()


def via_proxy(address, *, https=False, host=None):
    headers = {"X-Forwarded-For": address}
    if https:
        headers["X-Forwarded-Proto"] = "https"
    if host:
        headers["Host"] = host
    return headers


async def sign_in(client, address="192.168.1.30"):
    response = await client.post("/api/auth", json=GOOD, headers=via_proxy(address))
    assert response.status == 200, await response.text()
    body = await response.json()
    return {"Authorization": f"Bearer {body['key']}"}, body["device_id"]


# --- the rule ------------------------------------------------------------------------


@pytest.mark.parametrize("remote, headers, expected", [
    ("127.0.0.1", [("X-Forwarded-For", "203.0.113.9")], "203.0.113.9"),
    ("127.0.0.1", [("X-Forwarded-For", "10.0.0.1, 203.0.113.9")], "203.0.113.9"),
    ("127.0.0.1", [("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "1.2.3.4, 203.0.113.9")],
     "203.0.113.9"),
    ("127.0.0.1", [("X-Forwarded-For", " 203.0.113.9:5555 ")], "203.0.113.9"),
    ("127.0.0.1", [("X-Forwarded-For", "[2001:db8::1]:443")], "2001:db8::1"),
    ("::1", [("X-Forwarded-For", "2001:db8::7")], "2001:db8::7"),
    ("::ffff:127.0.0.1", [("X-Forwarded-For", "203.0.113.9")], "203.0.113.9"),
    ("127.0.0.1", [], "127.0.0.1"),
    ("127.0.0.1", [("X-Forwarded-For", "not an address")], "unknown"),
    ("127.0.0.1", [("X-Forwarded-For", "")], "unknown"),
    # Never from any other peer, wherever it is.
    ("192.168.1.5", [("X-Forwarded-For", "127.0.0.1")], "192.168.1.5"),
    ("203.0.113.7", [("X-Forwarded-For", "192.168.1.2")], "203.0.113.7"),
    ("10.0.0.8", [("X-Forwarded-For", "203.0.113.9")], "10.0.0.8"),
])
def test_the_phones_address(remote, headers, expected):
    assert client_address(request(remote, *headers)) == expected


@pytest.mark.parametrize("remote, proto, expected", [
    ("127.0.0.1", "https", True),
    ("127.0.0.1", "HTTPS", True),
    ("127.0.0.1", "http", False),
    ("127.0.0.1", None, False),
    ("192.168.1.5", "https", False),        # only a proxy on this PC is believed
    ("203.0.113.7", "https", False),
])
def test_https_is_believed_from_this_pc_only(remote, proto, expected):
    headers = [("X-Forwarded-Proto", proto)] if proto else []
    assert forwarded_https(request(remote, *headers)) is expected


# --- used everywhere the peer address was ---------------------------------------------


async def test_the_network_gate_uses_the_forwarded_address(rig):
    service, client = rig
    assert service.allow_internet is False
    refused = await client.get("/", headers=via_proxy("203.0.113.9"))
    assert refused.status == 403
    assert (await client.get("/api/status", headers=via_proxy("203.0.113.9"))).status == 403
    assert (await client.get("/", headers=via_proxy("192.168.1.40"))).status == 200
    assert (await client.get("/", headers=via_proxy("garbage"))).status == 403
    service.allow_internet = True
    assert (await client.get("/", headers=via_proxy("203.0.113.9"))).status == 200


async def test_devices_and_recent_use_record_the_forwarded_address(rig):
    service, client = rig
    service.allow_internet = True
    auth, device = await sign_in(client, "198.51.100.7")
    record = {r["id"]: r for r in service.devices_for_config()}[device]
    assert record["last_ip"] == "198.51.100.7"
    assert [row["ip"] for row in service.get_login_ips()] == ["198.51.100.7"]
    service._devices[device]["last_seen"] = 0          # due for its once-a-minute note
    await client.get("/api/status", headers={**auth, **via_proxy("198.51.100.8")})
    record = {r["id"]: r for r in service.devices_for_config()}[device]
    assert record["last_ip"] == "198.51.100.8"


async def test_the_lockout_counts_per_forwarded_address(rig):
    service, client = rig
    statuses = []
    for number in range(5):
        response = await client.post(
            "/api/auth", json={"username": "operator", "token": f"wrong-{number}"},
            headers=via_proxy("192.168.1.66"))
        statuses.append(response.status)
    assert statuses == [401, 401, 401, 401, 429]
    assert service._lockout_remaining("192.168.1.66") > 0
    other = await client.post("/api/auth", json={"username": "operator", "token": "wrong"},
                              headers=via_proxy("192.168.1.67"))
    assert other.status == 401                         # someone else is not locked
    assert service._lockout_remaining("127.0.0.1") == 0


async def test_an_outside_phones_socket_closes_when_internet_is_turned_off(rig):
    service, client = rig
    service.allow_internet = True
    auth, _device = await sign_in(client, "203.0.113.9")
    key = auth["Authorization"].split()[1]
    outside = await client.ws_connect(f"/ws?key={key}", headers=via_proxy("203.0.113.9"))
    await outside.receive_json()
    home = await client.ws_connect(f"/ws?key={key}", headers=via_proxy("192.168.1.30"))
    await home.receive_json()
    service.allow_internet = False
    await outside.receive()
    assert outside.close_code == 4001
    assert not home.closed
    await home.close()


# --- the https address for the pairing QR ---------------------------------------------


async def test_an_https_name_is_remembered_with_its_scheme(rig):
    service, client = rig
    service.allow_internet = True
    auth, _device = await sign_in(client, "203.0.113.9")
    await client.get("/api/status", headers={
        **auth, **via_proxy("203.0.113.9", https=True, host="wolfrat.example.com")})
    assert service.internet_address == "https://wolfrat.example.com"
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["server"]["secure_address"] == "https://wolfrat.example.com"

    # A plain-http visit does not replace it; an https visit by IP is not one.
    await client.get("/api/status", headers={**auth, **via_proxy("203.0.113.9",
                                                                  host="203.0.113.1:8070")})
    await client.get("/api/status", headers={**auth, **via_proxy(
        "203.0.113.9", https=True, host="203.0.113.1")})
    assert service.internet_address == "https://wolfrat.example.com"


async def test_a_plain_address_is_still_learned_and_the_proxys_own_is_not(rig):
    service, client = rig
    service.allow_internet = True
    auth, _device = await sign_in(client, "203.0.113.9")
    # A proxy that passes its own Host: nothing learned.
    await client.get("/api/status", headers={**auth, **via_proxy("203.0.113.9",
                                                                  host="127.0.0.1:8070")})
    assert service.internet_address is None
    await client.get("/api/status", headers={**auth, **via_proxy("203.0.113.9", https=True,
                                                                  host="localhost:8070")})
    assert service.internet_address is None
    await client.get("/api/status", headers={**auth, **via_proxy("203.0.113.9",
                                                                  host="203.0.113.1:8070")})
    assert service.internet_address == "203.0.113.1:8070"
    # From home: nothing learned (that is the LAN address).
    service.load_internet_address(None)
    await client.get("/api/status", headers={**auth, **via_proxy("192.168.1.30", https=True,
                                                                  host="wolfrat.example.com")})
    assert service.internet_address is None


def test_secure_address_prefers_the_tabs_https_address():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    assert service.secure_address() is None
    service.load_internet_address("203.0.113.1:8070")
    assert service.secure_address() is None             # plain http is not secure
    service.load_internet_address("https://seen.example.com")
    assert service.secure_address() == "https://seen.example.com"
    service.phone_address = "http://203.0.113.1:8070"
    assert service.secure_address() == "https://seen.example.com"
    service.phone_address = "wolfrat.example.com"
    assert service.phone_address == "https://wolfrat.example.com"
    assert service.secure_address() == "https://wolfrat.example.com"


@pytest.mark.parametrize("typed, url", [
    ("https://wolfrat.example.com", "https://wolfrat.example.com"),
    ("https://wolfrat.example.com/", "https://wolfrat.example.com"),
    ("HTTPS://Wolfrat.Example.com", "https://Wolfrat.Example.com"),
    ("wolfrat.example.com", "https://wolfrat.example.com"),
    ("  wolfrat.example.com:8443 ", "https://wolfrat.example.com:8443"),
    ("203.0.113.7:8070", "http://203.0.113.7:8070"),
    ("http://wolfrat.example.com:8070", "http://wolfrat.example.com:8070"),
    ("[2001:db8::1]:8070", "http://[2001:db8::1]:8070"),
    ("", None),
    ("   ", None),
    ("https://wolfrat.example.com/admin", None),       # the page lives at the root
    ("ftp://wolfrat.example.com", None),
    ("not a host!", None),
    ("https://", None),
])
def test_the_address_for_phones(typed, url):
    assert phone_address_url(typed) == url


@pytest.mark.parametrize("saved, kept", [
    ("203.0.113.7:8070", "203.0.113.7:8070"),
    ("https://wolfrat.example.com", "https://wolfrat.example.com"),
    ("https://wolfrat.example.com/", "https://wolfrat.example.com"),
    ("http://wolfrat.example.com", "wolfrat.example.com"),
    ("bad host/<x>", None),
    (None, None),
    ("", None),
])
def test_saved_internet_addresses(saved, kept):
    assert clean_internet_address(saved) == kept


def test_is_local_address_is_left_for_the_older_tests_to_patch(monkeypatch):
    """The gate and the internet address go through is_local_address, so the
    older tests that make every peer look outside still work."""
    monkeypatch.setattr(web_server_module, "is_local_address", lambda _address: False)
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service.allow_internet = True
    probe = types.SimpleNamespace(remote="127.0.0.1",
                                  headers=CIMultiDict({"Host": "203.0.113.7:8070"}))
    service._note_internet_address(probe)
    assert service.internet_address == "203.0.113.7:8070"
