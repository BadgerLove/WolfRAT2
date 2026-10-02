"""Two servers on one PC: each WolfRAT must read the server it is connected to.

Memory features used to take the first process named jointops.exe.  They now
follow WolfRAT's own admin connection through Windows' TCP owner table.
"""

import subprocess
import sys
import time

import pytest

from tests.test_weather import FakeServer
from tests.test_weather_tab import FakeHandle
from wolfrat import server_process as sp
from wolfrat import weather as w
from wolfrat.admin_session import SocketTransport
from wolfrat.jo_players import LocalServerPlayers
from wolfrat.runtime import DesktopRuntime
from wolfrat.weather_tab import WeatherTab

US = 50
TAC, OTHER = 100, 200
ROWS = [
    ("0.0.0.0", 4000, "0.0.0.0", 0, TAC),             # TAC listening
    ("0.0.0.0", 4001, "0.0.0.0", 0, OTHER),           # second server listening
    ("127.0.0.1", 4000, "127.0.0.1", 57850, TAC),     # TAC's end of WolfRAT A
    ("127.0.0.1", 57850, "127.0.0.1", 4000, US),      # WolfRAT A's end
    ("127.0.0.1", 4001, "127.0.0.1", 57900, OTHER),   # second server's end of WolfRAT B
    ("127.0.0.1", 57900, "127.0.0.1", 4001, 60),      # WolfRAT B's end
]


@pytest.fixture(autouse=True)
def no_endpoint():
    sp.set_endpoint(None)
    yield
    sp.set_endpoint(None)


def test_each_connection_leads_to_its_own_server():
    assert sp.match_server(ROWS, ("127.0.0.1", 57850, "127.0.0.1", 4000), US) == TAC
    assert sp.match_server(ROWS, ("127.0.0.1", 57900, "127.0.0.1", 4001), 60) == OTHER


def test_no_connection_or_remote_server_means_no_process():
    assert sp.match_server(ROWS, None, US) is None
    # connected to a server on another PC: no row on this PC owns its end
    assert sp.match_server(ROWS, ("10.0.0.5", 51000, "77.68.4.198", 4000), US) is None


def test_never_picks_our_own_end_or_an_ambiguous_match():
    mirror = [("127.0.0.1", 4000, "127.0.0.1", 57850, US)]
    assert sp.match_server(mirror, ("127.0.0.1", 57850, "127.0.0.1", 4000), US) is None
    two = [("127.0.0.1", 4000, "127.0.0.1", 57850, TAC), ("127.0.0.1", 4000, "127.0.0.1", 57850, OTHER)]
    assert sp.match_server(two, ("127.0.0.1", 57850, "127.0.0.1", 4000), US) is None


def test_reason_says_connect_first_then_same_pc():
    assert "Not connected" in sp.why_not()
    sp.set_endpoint(("127.0.0.1", 1, "127.0.0.1", 2))
    assert "not running on this PC" in sp.why_not()


def test_player_reader_switches_when_the_connection_leads_elsewhere():
    clock = [0.0]
    leads = [[TAC]]
    opened = []

    class Mem:
        def __init__(self, pid):
            self.pid = pid
            opened.append(pid)

        def close(self):
            pass

    reader = LocalServerPlayers(clock=lambda: clock[0], find=lambda: leads[0], open_memory=Mem)
    assert reader._attach() and reader._pid == TAC
    leads[0] = []                     # map change: connection briefly gone
    clock[0] += 5
    assert reader._attach() and reader._pid == TAC and opened == [TAC]
    leads[0] = [OTHER]                # now connected to the other server
    clock[0] += 5
    assert reader._attach() and reader._pid == OTHER and opened == [TAC, OTHER]


def test_weather_never_keeps_writing_to_the_old_server(qtbot, tmp_path):
    leads = [[FakeHandle.pid]]
    attaches = []
    server = FakeServer()

    def attach(writable=True):
        attaches.append(writable)
        controller = w.WeatherController(server)
        controller.verify()
        return controller, FakeHandle()

    tab = WeatherTab(DesktopRuntime.isolated(tmp_path), attach=attach,
                     server_pids=lambda: leads[0])
    qtbot.addWidget(tab)
    tab._timer.stop()
    tab._poll()
    leads[0] = []                     # reconnecting: keep the held process
    tab._poll()
    assert attaches == [False]
    leads[0] = [FakeHandle.pid + 1]   # a different server now
    tab._poll()
    assert attaches == [False, False]


SERVER = (
    "import socket,sys,time\n"
    "s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1)\n"
    "print(s.getsockname()[1],flush=True)\n"
    "c,_=s.accept();time.sleep(30)\n"
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows TCP owner table")
def test_real_two_servers_on_this_pc():
    """Two real listening processes; each real admin connection finds its own."""
    children = [subprocess.Popen([sys.executable, "-c", SERVER], stdout=subprocess.PIPE, text=True)
                for _ in range(2)]
    transports = []
    try:
        ports = [int(child.stdout.readline()) for child in children]
        for child, port in zip(children, ports):
            transport = SocketTransport()
            transport.connect("127.0.0.1", port, 5)
            transports.append(transport)
            deadline = time.time() + 5
            while sp.server_pid() is None and time.time() < deadline:
                time.sleep(0.05)
            assert sp.server_pid() == child.pid
            assert w.find_process_ids() == [child.pid]
    finally:
        for transport in transports:
            transport.close()
        for child in children:
            child.kill()
            child.wait()
