import asyncio
from concurrent.futures import Future
import socket
import threading
import time

from aiohttp import ClientConnectionError, ClientSession
from aiohttp.test_utils import TestClient, TestServer
import pytest

from wolfrat.admin_session import CommandResult, RawResult
from wolfrat.web_server import WolfWebServer


class FakeServerManager:
    """In-process stand-in for the retail server boundary."""

    is_connected = True
    players = []
    player_entries = ()
    chat_messages = []
    game_state = {"mode": "AAS"}
    missions = []
    mission_entries = ()
    available_maps_data = ""
    game_settings = {
        "ServerName": "Integration Server",
        "ServerPassword": "server-secret",
        "SideAPassword": "alpha-secret",
        "SideBPassword": "bravo-secret",
    }

    def __init__(self):
        self.raw_commands = []
        self.semantic_actions = []

    @staticmethod
    def _completed(result):
        future = Future()
        future.set_result(result)
        return future

    def execute_raw(self, command):
        self.raw_commands.append(command)
        return self._completed(RawResult(command, ("OK",), True))

    def cycle_mission(self):
        self.semantic_actions.append("next_map")
        return self._completed(
            CommandResult(
                operation="next_map",
                replies=("OK",),
                accepted=True,
                verified=True,
            )
        )

    def refresh_players(self):
        raise AssertionError("the requested action was not refresh_players")

    def refresh_chat(self):
        raise AssertionError("the requested action was not refresh_chat")


@pytest.fixture
def server_manager():
    return FakeServerManager()


@pytest.fixture
async def web_client(server_manager):
    service = WolfWebServer(server_manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_static_web_resources_are_served_by_the_real_http_stack(web_client):
    expected_resources = {
        "/": ("text/html", "<title>WolfRAT</title>"),
        "/static/style.css": ("text/css", "WolfRAT Mobile Web UI"),
        "/static/app.js": ("application/javascript", "function doLogin"),
    }

    for path, (content_type, identifying_text) in expected_resources.items():
        response = await web_client.get(path)

        assert response.status == 200
        assert response.content_type == content_type
        assert identifying_text in await response.text()


@pytest.mark.asyncio
async def test_login_rejects_malformed_json_as_a_client_error(web_client):
    response = await web_client.post(
        "/api/auth",
        data="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "Request body must be valid JSON"}


@pytest.mark.asyncio
async def test_login_requires_a_json_object(web_client):
    response = await web_client.post("/api/auth", json=["operator", "token"])

    assert response.status == 400
    assert await response.json() == {"error": "Request body must be a JSON object"}


@pytest.mark.asyncio
async def test_login_grants_bearer_access_to_protected_routes(web_client):
    unauthorized = await web_client.get("/api/status")
    assert unauthorized.status == 401
    assert await unauthorized.json() == {"error": "Unauthorized"}

    rejected = await web_client.post(
        "/api/auth",
        json={"username": "operator", "token": "wrong-token"},
    )
    assert rejected.status == 401
    assert await rejected.json() == {"error": "Invalid username or token"}

    login = await web_client.post(
        "/api/auth",
        json={"username": "operator", "token": "web-access-token"},
    )
    assert login.status == 200
    assert await login.json() == {
        "ok": True,
        "token": "web-access-token",
    }

    status = await web_client.get(
        "/api/status",
        headers={"Authorization": "Bearer web-access-token"},
    )
    assert status.status == 200
    state = await status.json()
    assert state["connected"] is True
    assert state["game_mode"] == "AAS"


@pytest.mark.asyncio
async def test_retail_passwords_never_cross_the_http_boundary(
    web_client,
    server_manager,
):
    headers = {"Authorization": "Bearer web-access-token"}

    settings_response = await web_client.get("/api/settings", headers=headers)
    status_response = await web_client.get("/api/status", headers=headers)
    # The raw-command route is gone (2026-11-23): one token could change the
    # server password through it.
    command_response = await web_client.post(
        "/api/command",
        headers=headers,
        json={"command": "SET ServerPassword hijacked"},
    )

    assert settings_response.status == 200
    assert await settings_response.json() == {
        "settings": {"ServerName": "Integration Server"}
    }

    state = await status_response.json()
    assert state["settings"] == {"ServerName": "Integration Server"}

    assert command_response.status in (404, 405)
    assert server_manager.raw_commands == []
    assert "server-secret" not in repr(state)


@pytest.mark.asyncio
async def test_typed_action_returns_its_verified_retail_outcome(
    web_client,
    server_manager,
):
    response = await web_client.post(
        "/api/action",
        headers={"Authorization": "Bearer web-access-token"},
        json={"action": "next_map"},
    )

    payload = await response.json()
    assert response.status == 200, payload
    assert payload == {
        "ok": True,
        "accepted": True,
        "replies": ["OK"],
        "action": "next_map",
        "verified": True,
        "verification_error": None,
    }
    assert server_manager.semantic_actions == ["next_map"]


@pytest.mark.asyncio
async def test_stop_waits_for_the_wolfweb_thread_and_is_idempotent(
    server_manager,
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    existing_workers = {
        worker.ident
        for worker in threading.enumerate()
        if worker.name == "WolfWeb"
    }
    service = WolfWebServer(server_manager, host="127.0.0.1", port=port)
    outcome = service.start().result(timeout=3)
    assert outcome.ok is True
    assert service.is_running is True

    try:
        deadline = asyncio.get_running_loop().time() + 3
        async with ClientSession() as client:
            while True:
                try:
                    async with client.get(
                        f"http://127.0.0.1:{port}/"
                    ) as response:
                        assert response.status == 200
                        break
                except ClientConnectionError:
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("WolfWeb did not begin listening")
                    await asyncio.sleep(0.01)

        worker = next(
            candidate
            for candidate in threading.enumerate()
            if (
                candidate.name == "WolfWeb"
                and candidate.ident not in existing_workers
            )
        )

        started = time.monotonic()
        service.stop()
        elapsed = time.monotonic() - started

        assert elapsed < 2
        assert not worker.is_alive()
        assert service.is_running is False

        service.stop()
        assert not worker.is_alive()
    finally:
        service.stop()


def test_immediate_stop_cannot_leave_a_startup_race_worker(server_manager):
    service = WolfWebServer(server_manager, host="127.0.0.1", port=0)

    service.start()
    service.stop()

    assert service.is_running is False
    assert not any(
        worker.name == "WolfWeb" and worker.is_alive()
        for worker in threading.enumerate()
    )


def test_start_reports_bind_failure_without_claiming_to_be_running(
    server_manager,
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        reservation.listen()
        port = reservation.getsockname()[1]
        service = WolfWebServer(
            server_manager,
            host="127.0.0.1",
            port=port,
        )

        outcome = service.start().result(timeout=3)

    assert outcome.ok is False
    assert str(port) in outcome.error
    assert service.is_running is False
    service.stop()


def test_overlapping_stop_and_start_keep_the_new_listener_owned(
    server_manager,
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    existing_workers = {
        worker.ident
        for worker in threading.enumerate()
        if worker.name == "WolfWeb"
    }
    service = WolfWebServer(server_manager, host="127.0.0.1", port=port)
    join_entered = threading.Event()
    release_join = threading.Event()
    stop_errors = []
    start_state = []

    def wait_until_listening():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    ("127.0.0.1", port), timeout=0.1
                ):
                    return
            except OSError:
                time.sleep(0.01)
        pytest.fail("WolfWeb did not begin listening")

    try:
        service.start()
        wait_until_listening()
        old_worker = service._thread
        original_join = old_worker.join

        def paused_join(timeout=None):
            join_entered.set()
            assert release_join.wait(timeout=2)
            return original_join(timeout)

        old_worker.join = paused_join

        stop_worker = threading.Thread(
            target=lambda: _capture_failure(service.stop, stop_errors)
        )
        stop_worker.start()
        assert join_entered.wait(timeout=2)

        def start_again():
            service.start()
            start_state.append((service._loop, service._thread))

        start_worker = threading.Thread(target=start_again)
        start_worker.start()
        time.sleep(0.05)
        release_join.set()
        stop_worker.join(timeout=3)
        start_worker.join(timeout=3)

        assert not stop_worker.is_alive()
        assert not start_worker.is_alive()
        assert stop_errors == []
        wait_until_listening()

        service.stop()

        assert service.is_running is False
        assert not any(
            worker.name == "WolfWeb"
            and worker.ident not in existing_workers
            and worker.is_alive()
            for worker in threading.enumerate()
        )
    finally:
        release_join.set()
        try:
            service.stop()
        except RuntimeError:
            if start_state:
                loop, worker = start_state[-1]
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
                if worker is not None:
                    worker.join(timeout=3)


def _capture_failure(operation, errors):
    try:
        operation()
    except BaseException as error:
        errors.append(error)


@pytest.mark.asyncio
async def test_restart_serves_http_from_a_fresh_aiohttp_lifecycle(
    server_manager,
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    service = WolfWebServer(server_manager, host="127.0.0.1", port=port)

    async def wait_until_listening():
        deadline = asyncio.get_running_loop().time() + 3
        async with ClientSession() as client:
            while True:
                try:
                    async with client.get(
                        f"http://127.0.0.1:{port}/"
                    ) as response:
                        assert response.status == 200
                        return await response.text()
                except ClientConnectionError:
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("WolfWeb did not begin listening")
                    await asyncio.sleep(0.01)

    try:
        service.start()
        assert "<title>WolfRAT</title>" in await wait_until_listening()

        outcome = service.restart().result(timeout=3)

        assert outcome.ok is True
        assert "<title>WolfRAT</title>" in await wait_until_listening()
        assert service.is_running is True
    finally:
        service.stop()
