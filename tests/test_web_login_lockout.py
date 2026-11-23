"""Web Admin brute-force lockout (2026-09-21, Dale: 5 failed logins).

Five different wrong credentials from one address lock that address out for
15 minutes - on the login form and on every key-protected route, so a key
cannot be guessed through /api/status instead.  While locked, wrong
credentials get 429 + Retry-After; a right token or device key always gets in
(2026-11-23), so someone sharing the address cannot lock the admin out.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wolfrat.web_server import WolfWebServer

from tests.test_web_http_integration import FakeServerManager

GOOD = {"username": "operator", "token": "web-access-token"}


@pytest.fixture
async def service_and_client():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client
    finally:
        await client.close()


async def _bad_login(client, n):
    return await client.post(
        "/api/auth", json={"username": "operator", "token": f"guess-{n}"}
    )


@pytest.mark.asyncio
async def test_fifth_failed_login_locks_the_address_out(service_and_client):
    service, client = service_and_client
    for n in range(4):
        assert (await _bad_login(client, n)).status == 401

    fifth = await _bad_login(client, 4)
    assert fifth.status == 429
    body = await fifth.json()
    assert "Too many failed logins" in body["error"]
    assert body["retry_after"] == service.LOCKOUT_SECONDS
    assert int(fifth.headers["Retry-After"]) == service.LOCKOUT_SECONDS

    # Another wrong guess while locked: 429 again.
    assert (await _bad_login(client, 5)).status == 429

    # The right credentials still get in while the address is locked...
    signed_in = await client.post("/api/auth", json=GOOD)
    assert signed_in.status == 200
    key = (await signed_in.json())["key"]
    # ...and so does the device key on a protected route.
    status = await client.get("/api/status", headers={"Authorization": f"Bearer {key}"})
    assert status.status == 200
    # A wrong key on a protected route gets 429 + Retry-After, not 401, so
    # the page can say "locked, try again in N minutes" and keep its key.
    wrong = await client.get("/api/status", headers={"Authorization": "Bearer guess-x"})
    assert wrong.status == 429
    assert 0 < int(wrong.headers["Retry-After"]) <= service.LOCKOUT_SECONDS
    assert (await wrong.json())["retry_after"] == int(wrong.headers["Retry-After"])


@pytest.mark.asyncio
async def test_lockout_expires(service_and_client):
    service, client = service_and_client
    for n in range(5):
        await _bad_login(client, n)
    assert (await _bad_login(client, 5)).status == 429

    with service._auth_lock:
        for record in service._failed_logins.values():
            record["until"] = 1  # long past
    assert (await _bad_login(client, 6)).status == 401
    assert (await client.post("/api/auth", json=GOOD)).status == 200


@pytest.mark.asyncio
async def test_successful_login_resets_the_count(service_and_client):
    service, client = service_and_client
    for n in range(4):
        await _bad_login(client, n)
    assert (await client.post("/api/auth", json=GOOD)).status == 200
    for n in range(10, 14):
        assert (await _bad_login(client, n)).status == 401


@pytest.mark.asyncio
async def test_bearer_token_guessing_is_locked_out_too(service_and_client):
    service, client = service_and_client
    statuses = []
    for n in range(5):
        response = await client.get(
            "/api/status", headers={"Authorization": f"Bearer guess-{n}"}
        )
        statuses.append(response.status)
    assert statuses == [401, 401, 401, 401, 429]
    # The login form is locked for wrong guesses too...
    assert (await _bad_login(client, 9)).status == 429
    # ...but never for the right credentials.
    assert (await client.post("/api/auth", json=GOOD)).status == 200


@pytest.mark.asyncio
async def test_repeating_one_stale_token_never_locks_out(service_and_client):
    """A phone still holding the old token after a token change retries it."""
    service, client = service_and_client
    for _ in range(20):
        await client.get(
            "/api/status", headers={"Authorization": "Bearer old-stale-token"}
        )
    assert (await client.post("/api/auth", json=GOOD)).status == 200


@pytest.mark.asyncio
async def test_requests_without_credentials_are_not_counted(service_and_client):
    service, client = service_and_client
    for _ in range(20):
        assert (await client.get("/api/status")).status == 401
    assert (await client.post("/api/auth", json=GOOD)).status == 200


def test_guesses_are_not_stored_in_clear():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service._record_failed_login("1.2.3.4", "secret-guess")
    assert "secret-guess" not in repr(service._failed_logins)


def test_old_failures_age_out_of_the_window():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    for n in range(4):
        service._record_failed_login("1.2.3.4", f"g{n}", now=1000.0)
    later = 1000.0 + service.LOCKOUT_WINDOW + 1
    assert service._record_failed_login("1.2.3.4", "g4", now=later) is False
    assert service._lockout_remaining("1.2.3.4", now=later) == 0
