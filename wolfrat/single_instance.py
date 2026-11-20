"""One WolfRAT per folder on a PC (Dale, 2026-11-20).

2026-11-19: two copies of the same WolfRAT ran against one server for hours
(the updater relaunched it and the admin also double-clicked it).  Every chat
line, vote and weather change went out twice, and both wrote the same
settings files.

Each WolfRAT holds a named Windows mutex keyed by its data folder (the folder
its settings files live in: ``DesktopRuntime.data_dir``).  A mutex and not a
lock file, because Windows lets go of it by itself when the process ends,
however it ends: a crash or a kill never leaves a stale lock behind.

The mutex only says "somebody has this folder".  It cannot tell a WolfRAT
that holds it whether a second one does too, and WolfRATs older than this
file hold none.  So the start-up check also looks at the process list for
any WolfRAT exe running from the same folder (``copy_running_from``): that
is what catches an older version left open (Dale, 2026-11-20: the first
test build said nothing about a 2.10.23 still running beside it).
``other_instance_running`` (any folder) is what lag compensation asks before
it takes the game's hit calls over from another WolfRAT's helper.

No PyQt and no network here, so the logic is unit-testable.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import time
from typing import Callable, Optional

MUTEX_PREFIX = "Global\\WolfRAT2-folder-"     # Global: seen from every Windows session
RECHECK_SECONDS = 2.0       # a copy that is just closing gets this long to go
RECHECK_STEP = 0.25

_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x102
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def folder_key(data_dir) -> str:
    """One spelling for every way of writing the same folder (case, slashes,
    dots, a trailing slash, a short 8.3 name, a link to it)."""
    path = os.path.normcase(os.path.realpath(str(data_dir)))
    return path.rstrip("\\/") or path


def mutex_name(data_dir) -> str:
    digest = hashlib.sha256(folder_key(data_dir).encode("utf-8", "surrogatepass")).hexdigest()
    return MUTEX_PREFIX + digest[:32]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    k.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
    k.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry))
    k.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry))
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    k.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                             ctypes.POINTER(ctypes.c_uint32))
    return k


class InstanceLock:
    """This process's hold on one folder's mutex.

    ``first`` is True when nobody held it before us.  Windows lets go when the
    process ends; ``release`` lets go sooner."""

    def __init__(self, name: str, handle: Optional[int], first: bool):
        self.name = name
        self.first = first
        self._handle = handle

    @property
    def held(self) -> bool:
        return bool(self._handle)

    def release(self) -> None:
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None


def acquire(data_dir) -> InstanceLock:
    """Take the folder's mutex, or share it when another WolfRAT has it
    (``first`` is then False).  Never raises: if Windows will not say, WolfRAT
    starts as if it were alone."""
    name = mutex_name(data_dir)
    if sys.platform != "win32":
        return InstanceLock(name, None, True)
    try:
        k = _kernel32()
        ctypes.set_last_error(0)
        handle = k.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
    except Exception:
        return InstanceLock(name, None, True)
    if not handle:
        # Refused = it is there, made by a WolfRAT running as another user or
        # as administrator.  Any other failure: carry on as if alone.
        return InstanceLock(name, None, error != _ERROR_ACCESS_DENIED)
    return InstanceLock(name, handle, error != _ERROR_ALREADY_EXISTS)


def held_elsewhere(data_dir) -> bool:
    """Is a WolfRAT holding this folder right now?  Only for a process that
    does not hold it itself (it would see its own hold)."""
    lock = acquire(data_dir)
    lock.release()
    return not lock.first


_held: Optional[InstanceLock] = None      # kept for as long as this WolfRAT runs


def claim(data_dir, ask: Callable[[], bool], wait_seconds: float = RECHECK_SECONDS,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic,
          folder_copy: Optional[Callable[[], bool]] = None) -> bool:
    """Called once when the desktop starts.  True = carry on starting; False =
    the admin chose to close this copy.

    ``ask()`` puts the question and returns True for "Open anyway".

    An update is not a second copy.  The update batch file can only swap the
    exe once the old WolfRAT has ended (Windows keeps a running exe locked),
    and the old one's mutex ends with it, before the new one is even started.
    The short look-again below is for a copy that is closing at this moment."""
    global _held
    # A copy of this version or newer shows in the mutex; an older one only
    # in the process list (``folder_copy``: any WolfRAT exe in this folder).
    if folder_copy is None:
        def folder_copy() -> bool:
            return copy_running_from(data_dir)
    lock = acquire(data_dir)
    deadline = clock() + max(0.0, wait_seconds)
    busy = (not lock.first) or folder_copy()
    while busy and clock() < deadline:
        lock.release()
        sleep(RECHECK_STEP)
        lock = acquire(data_dir)
        busy = (not lock.first) or folder_copy()
    if not busy or ask():
        _held = lock
        return True
    lock.release()
    return False


# --------------------------------------------------------- other WolfRATs
class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def process_list() -> list:
    """[(process id, parent's id, exe file name)] for every process on this PC."""
    if sys.platform != "win32":
        return []
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return []
    out = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID),
                        entry.szExeFile))
            more = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return out


def _is_wolfrat(exe_name: str) -> bool:
    name = (exe_name or "").lower()
    return name.startswith("wolfrat") and name.endswith(".exe")


def other_instance_running(processes: Optional[Callable[[], list]] = None,
                           own_pid: Optional[int] = None,
                           parent_pid: Optional[int] = None) -> bool:
    """True when another WolfRAT is running on this PC: any folder, any version.

    By exe name (WolfRAT*.exe), because WolfRATs older than this file hold no
    mutex.  A WolfRAT whose exe was renamed to something else, or one run from
    source, is not seen.  The released exe runs as two processes (an unpacker
    and the program): this WolfRAT's own unpacker does not count."""
    try:
        rows = (processes or process_list)()
    except Exception:
        return False
    me = os.getpid() if own_pid is None else own_pid
    parent = os.getppid() if parent_pid is None else parent_pid
    names = {pid: (name or "").lower() for pid, _parent, name in rows}
    mine = {me}
    if _is_wolfrat(names.get(parent, "")) and names.get(parent) == names.get(me):
        mine.add(parent)
    return any(_is_wolfrat(name) and pid not in mine for pid, name in names.items())


def exe_path_of(pid: int) -> Optional[str]:
    """The full path of a running process's exe, or None when Windows will not say."""
    if not pid or sys.platform != "win32":
        return None
    try:
        k = _kernel32()
        handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            size = ctypes.c_uint32(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value or None
        finally:
            k.CloseHandle(handle)
    except Exception:
        return None


def copy_running_from(data_dir, processes: Optional[Callable[[], list]] = None,
                      path_of: Optional[Callable[[int], Optional[str]]] = None,
                      own_pid: Optional[int] = None,
                      parent_pid: Optional[int] = None) -> bool:
    """True when another WolfRAT exe that lives in ``data_dir`` is running: any
    version, any file name starting with WolfRAT.

    A released WolfRAT keeps its settings beside its exe, so "an exe from this
    folder" is "a copy using these settings files".  This WolfRAT's own
    unpacker does not count.  A WolfRAT whose folder Windows will not tell us
    is not counted (no false alarm for an admin who runs one WolfRAT per
    server from different folders)."""
    try:
        rows = (processes or process_list)()
    except Exception:
        return False
    lookup = path_of or exe_path_of
    me = os.getpid() if own_pid is None else own_pid
    parent = os.getppid() if parent_pid is None else parent_pid
    names = {pid: (name or "").lower() for pid, _parent, name in rows}
    mine = {me}
    if _is_wolfrat(names.get(parent, "")) and names.get(parent) == names.get(me):
        mine.add(parent)
    folder = folder_key(data_dir)
    for pid, name in names.items():
        if pid in mine or not _is_wolfrat(name):
            continue
        try:
            path = lookup(pid)
        except Exception:
            path = None
        if path and folder_key(os.path.dirname(path)) == folder:
            return True
    return False


def pid_running(pid: int) -> bool:
    """True while a process with this id is running."""
    if not pid or sys.platform != "win32":
        return False
    try:
        k = _kernel32()
        handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, int(pid))
        if not handle:
            # Refused = it is there, but not ours to open.
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            return k.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            k.CloseHandle(handle)
    except Exception:
        return False
