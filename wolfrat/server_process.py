"""Which process on this PC is the game server WolfRAT is connected to?

Every memory feature (IPs, idle kick, zones, caps, weather, wildlife, the
Patches readout) needs the server's process.  Looking for a process *named*
jointops.exe picks the wrong one when two servers run on one PC, and misses a
renamed exe.  Instead WolfRAT follows its own admin connection: Windows keeps a
table of every TCP connection with the process that owns each end (what
``netstat -ano`` prints, ``GetExtendedTcpTable``).  The row whose local end is
the server's admin port and whose remote end is our socket belongs to exactly
the server we are talking to.

No admin connection, or the server is on another PC: no process, and the
memory features say so instead of guessing.
"""
from __future__ import annotations

import ctypes
import os
import socket
import struct
import sys
import threading
from typing import Optional

_lock = threading.Lock()
# (our ip, our port, server ip, server port) of the last admin connection.
# Kept after the socket closes (map change, reconnect) - the rows simply vanish
# from the table until the next connection replaces it.
_endpoint: Optional[tuple] = None

_AF_INET = 2
_TCP_TABLE_OWNER_PID_ALL = 5
_ERROR_INSUFFICIENT_BUFFER = 122


def note_connection(sock: socket.socket) -> None:
    """Record the admin connection (called right after it connects)."""
    global _endpoint
    try:
        ours = sock.getsockname()
        theirs = sock.getpeername()
    except Exception:          # never let this break the admin connection
        return
    with _lock:
        _endpoint = (ours[0], int(ours[1]), theirs[0], int(theirs[1]))


def set_endpoint(endpoint: Optional[tuple]) -> None:
    """Tests: (our ip, our port, server ip, server port) or None."""
    global _endpoint
    with _lock:
        _endpoint = endpoint


def endpoint() -> Optional[tuple]:
    with _lock:
        return _endpoint


def tcp_rows() -> list[tuple]:
    """[(local ip, local port, remote ip, remote port, pid), ...] for IPv4 TCP."""
    if sys.platform != "win32":
        return []
    iphlpapi = ctypes.WinDLL("iphlpapi")
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                          ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    get_table.restype = ctypes.c_uint32
    size = ctypes.c_uint32(0)
    buffer = None
    for _ in range(5):                       # the table can grow between calls
        buffer = ctypes.create_string_buffer(size.value or 1)
        result = get_table(buffer, ctypes.byref(size), False, _AF_INET,
                           _TCP_TABLE_OWNER_PID_ALL, 0)
        if result == 0:
            break
        if result != _ERROR_INSUFFICIENT_BUFFER:
            return []
    else:
        return []
    raw = buffer.raw
    count = struct.unpack_from("<I", raw, 0)[0]
    rows = []
    for i in range(count):
        # MIB_TCPROW_OWNER_PID: state, local addr, local port, remote addr, remote port, pid
        _, laddr, lport, raddr, rport, pid = struct.unpack_from("<IIIIII", raw, 4 + i * 24)
        rows.append((socket.inet_ntoa(struct.pack("<I", laddr)), socket.ntohs(lport & 0xFFFF),
                     socket.inet_ntoa(struct.pack("<I", raddr)), socket.ntohs(rport & 0xFFFF),
                     int(pid)))
    return rows


def match_server(rows: list[tuple], ends: Optional[tuple], own_pid: int) -> Optional[int]:
    """The pid owning the server end of ``ends``, or None."""
    if not ends:
        return None
    our_ip, our_port, server_ip, server_port = ends
    pids = {pid for lip, lport, rip, rport, pid in rows
            if lip == server_ip and lport == server_port and rip == our_ip and rport == our_port
            and pid != own_pid and pid != 0}
    return pids.pop() if len(pids) == 1 else None


def server_pid() -> Optional[int]:
    """The connected server's process on this PC, or None."""
    ends = endpoint()
    if not ends:
        return None
    try:
        return match_server(tcp_rows(), ends, os.getpid())
    except Exception:                          # pragma: no cover - OS oddities
        return None


def server_pids() -> list[int]:
    pid = server_pid()
    return [pid] if pid is not None else []


def why_not() -> str:
    """Plain words for 'no server process' (shown in the tabs)."""
    if endpoint() is None:
        return ("Not connected to a game server yet. WolfRAT reads the server it is "
                "connected to, on this PC.")
    return ("The server WolfRAT is connected to is not running on this PC (or the "
            "connection just dropped). Server-memory features need WolfRAT on the "
            "same PC as that server.")
