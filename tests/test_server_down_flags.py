"""The two plain flags behind the phone page's "game server down" alert:

* ``ServerManager.connect_refused``: the last connect attempt found nothing
  listening on the admin port (the game server is not running).
* ``ServerManager.disconnected_on_purpose``: the admin pressed Disconnect on
  the PC, which is never the server going down.

The web server's thread reads both (tests/test_web_notify_kinds.py).
"""

import errno
import socket
from concurrent.futures import Future

from wolfrat import protocol
from wolfrat.admin_commands import AdminOperation
from wolfrat.protocol import ServerManager
from tests.test_server_manager import FakeSession

REFUSED = ConnectionRefusedError(
    10061, "No connection could be made because the target machine actively refused it")


def failing(error):
    """A session factory whose first request fails with ``error``."""
    def factory(*args, **kwargs):
        session = FakeSession(*args, **kwargs)
        session.result_overrides[AdminOperation.GET_GAMESTATE] = [error]
        return session
    return factory


def test_what_counts_as_refused():
    class WinsockRefusal(Exception):
        winerror = 10061

    assert protocol._connect_refused(REFUSED)
    assert protocol._connect_refused(ConnectionRefusedError())
    assert protocol._connect_refused(OSError(errno.ECONNREFUSED, "refused"))
    assert protocol._connect_refused(WinsockRefusal())
    for other in (TimeoutError("timed out"), socket.timeout("timed out"),
                  ConnectionResetError(10054, "reset"), ConnectionAbortedError(),
                  OSError(errno.EHOSTUNREACH, "no route"), ConnectionError("closed"),
                  RuntimeError("server rejected the credentials"), ValueError("bad reply")):
        assert not protocol._connect_refused(other), other


def test_a_new_server_manager_has_both_flags_off():
    manager = ServerManager(session_factory=FakeSession)
    assert manager.connect_refused is False
    assert manager.disconnected_on_purpose is False


def test_a_refused_connect_sets_the_flag_and_a_good_one_clears_it():
    logged = []
    manager = ServerManager(session_factory=failing(REFUSED))
    manager.set_callbacks(on_log=logged.append)
    try:
        ok, message = manager.connect("127.0.0.1", 4000, "admin", "pw")
        assert ok is False and message.startswith("Connection failed: ")
        assert manager.connect_refused is True
        assert any(line.startswith("Connection failed: ") for line in logged)
        manager.connect("127.0.0.1", 4000, "admin", "pw")      # refused again: still set
        assert manager.connect_refused is True

        manager._session_factory = FakeSession                 # the server is back
        ok, _message = manager.connect("127.0.0.1", 4000, "admin", "pw")
        assert ok is True and manager.is_connected
        assert manager.connect_refused is False
    finally:
        manager.close()


def test_a_connect_that_fails_another_way_is_not_refused():
    for error in (TimeoutError("timed out"), ConnectionResetError(10054, "reset"),
                  RuntimeError("server rejected the credentials")):
        manager = ServerManager(session_factory=failing(REFUSED))
        try:
            manager.connect("127.0.0.1", 4000, "admin", "pw")
            assert manager.connect_refused is True
            # The program is starting again: the port is open, no answer yet.
            manager._session_factory = failing(error)
            ok, _message = manager.connect("127.0.0.1", 4000, "admin", "pw")
            assert ok is False and manager.connect_refused is False, error
        finally:
            manager.close()


def test_a_request_that_never_answers_is_not_refused():
    def factory(*args, **kwargs):
        session = FakeSession(*args, **kwargs)
        failed = Future()
        failed.set_exception(TimeoutError("timed out"))
        session.result_overrides[AdminOperation.GET_GAMESTATE] = [failed]
        return session

    manager = ServerManager(session_factory=factory)
    try:
        assert manager.connect("127.0.0.1", 4000, "admin", "pw")[0] is False
        assert manager.connect_refused is False
    finally:
        manager.close()


def test_a_real_connect_to_a_port_nothing_listens_on_is_refused():
    """The real admin session and socket against this PC, on a port taken and
    given back a moment ago: nothing listens there."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    manager = ServerManager()
    try:
        ok, message = manager.connect("127.0.0.1", port, "admin", "pw")
        assert ok is False and message.startswith("Connection failed: ")
        assert manager.connect_refused is True
        assert not manager.is_connected
    finally:
        manager.close()


# --- the Disconnect button ------------------------------------------------------------


class TabServer:
    """What the Server tab asks of the server manager here."""

    def __init__(self):
        self.is_connected = False
        self.disconnected_on_purpose = False
        self.connects = []

    def connect(self, host, port, username, password):
        self.connects.append((host, port))
        return False, "Connection failed: test"

    def disconnect(self):
        return 0

    def stop_polling(self):
        pass

    def close(self):
        pass


def test_the_disconnect_button_marks_the_link_closed_on_purpose(qtbot, tmp_path):
    from wolfrat.app import DesktopRuntime, LogSignals, ServerTab

    server = TabServer()
    tab = ServerTab(server, LogSignals(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    try:
        tab.auto_reconnect_cb.setChecked(False)        # one try, no timers left behind
        assert server.disconnected_on_purpose is False

        tab._do_disconnect()                           # the Disconnect button
        assert server.disconnected_on_purpose is True

        tab.host_input.setText("127.0.0.1")
        tab._do_connect()                              # the Connect button
        assert server.disconnected_on_purpose is False
        tab._connect_thread.join(timeout=5)
        qtbot.waitUntil(lambda: tab._connect_thread is None, timeout=5000)
        assert server.connects == [("127.0.0.1", tab.port_input.value())]
        assert server.disconnected_on_purpose is False  # a failed try is not "on purpose"
    finally:
        tab.shutdown()


def test_the_real_server_manager_server_tab_and_web_server_together(qtbot, tmp_path):
    """The three real pieces share the two flags by name: a stop and a restart
    reach the bell, the Disconnect button says nothing."""
    from wolfrat.app import DesktopRuntime, LogSignals, ServerTab
    from wolfrat.web_server import WolfWebServer

    manager = ServerManager(session_factory=FakeSession)
    tab = ServerTab(manager, LogSignals(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    service = WolfWebServer(manager, host="127.0.0.1", port=0)

    def bell():
        return [(event["kind"], event["text"]) for event in service._events.recent()]

    try:
        tab.auto_reconnect_cb.setChecked(False)
        assert manager.connect("127.0.0.1", 4000, "admin", "pw")[0] is True
        service._watch_tick(now=1000)                  # the link is up
        manager.disconnect()                           # ...and drops
        service._watch_tick(now=1005)
        assert bell() == []
        manager._session_factory = failing(REFUSED)    # the server program is not running
        assert manager.connect("127.0.0.1", 4000, "admin", "pw")[0] is False
        service._watch_tick(now=1010)
        assert bell() == [("server", "Game server stopped")]
        manager._session_factory = FakeSession         # it is back
        assert manager.connect("127.0.0.1", 4000, "admin", "pw")[0] is True
        service._watch_tick(now=1005 + 144)
        assert bell() == [("server", "Game server stopped"),
                          ("server", "Game server is back (down 2 min 24 s)")]

        tab._do_disconnect()                           # the admin presses Disconnect
        assert manager.disconnected_on_purpose is True and not manager.is_connected
        for now in range(1200, 2000, 5):
            service._watch_tick(now=now)
        assert len(bell()) == 2                        # never a server going down
    finally:
        tab.shutdown()


def test_a_dropped_link_is_not_on_purpose(qtbot, tmp_path):
    from wolfrat.app import DesktopRuntime, LogSignals, ServerTab

    server = TabServer()
    tab = ServerTab(server, LogSignals(), DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    try:
        tab.auto_reconnect_cb.setChecked(False)
        tab.handle_disconnect_ui()                     # the link dropped by itself
        assert server.disconnected_on_purpose is False
    finally:
        tab.shutdown()
