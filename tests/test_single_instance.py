"""One WolfRAT per folder (2026-11-20).  The day before, two copies of the same
WolfRAT ran against one server for hours: the updater relaunched it and the
admin also double-clicked it.  Every chat line and vote went out twice."""

import os
import subprocess
import sys

import pytest

from wolfrat import single_instance as si

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="named mutexes are Windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@windows_only
def test_the_same_folder_twice_is_seen_and_letting_go_frees_it(tmp_path):
    first = si.acquire(tmp_path)
    assert first.first and first.held
    second = si.acquire(tmp_path)                 # the same key again, in one process
    assert second.held and not second.first
    second.release()
    assert si.held_elsewhere(tmp_path)            # the first still holds it
    first.release()
    assert not first.held
    again = si.acquire(tmp_path)                  # nobody holds it now
    assert again.first
    again.release()
    assert not si.held_elsewhere(tmp_path)
    again.release()                               # twice is harmless


@windows_only
def test_different_folders_do_not_see_each_other(tmp_path):
    (tmp_path / "live").mkdir()
    (tmp_path / "test").mkdir()
    live = si.acquire(tmp_path / "live")
    test = si.acquire(tmp_path / "test")
    try:
        assert live.first and test.first and live.name != test.name
    finally:
        live.release()
        test.release()


def test_one_folder_has_one_name_however_it_is_written(tmp_path):
    folder = tmp_path / "WolfRAT Data"
    folder.mkdir()
    name = si.mutex_name(folder)
    assert name.startswith(si.MUTEX_PREFIX) and len(name) == len(si.MUTEX_PREFIX) + 32
    assert "WolfRAT Data" not in name and str(tmp_path) not in name      # hashed, not the path
    assert si.mutex_name(str(folder) + os.sep) == name
    assert si.mutex_name(folder / "sub" / "..") == name
    assert si.mutex_name(str(folder).replace("\\", "/")) == name
    if sys.platform == "win32":
        assert si.mutex_name(str(folder).upper()) == name
    assert si.mutex_name(tmp_path) != name


@windows_only
def test_windows_frees_it_when_the_holder_is_killed(tmp_path):
    """Why a mutex and not a lock file: a WolfRAT that crashes or is killed
    leaves nothing behind."""
    code = ("import sys; from wolfrat import single_instance as si; "
            "lock = si.acquire(sys.argv[1]); print('held' if lock.first else 'not first', flush=True); "
            "sys.stdin.read()")
    holder = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert si.held_elsewhere(tmp_path)
        assert si.pid_running(holder.pid)
        holder.kill()                             # no clean-up code runs
        holder.wait(timeout=10)
        assert not si.held_elsewhere(tmp_path)
        assert not si.pid_running(holder.pid)
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.stdout.close()
        holder.stdin.close()


# ------------------------------------------------------------ the start-up question
@pytest.fixture
def no_kept_lock(monkeypatch):
    monkeypatch.setattr(si, "_held", None)
    yield
    if si._held is not None:
        si._held.release()


@windows_only
def test_a_lone_wolfrat_starts_without_a_question(tmp_path, no_kept_lock):
    asked = []
    assert si.claim(tmp_path, ask=lambda: asked.append(1) or False) is True
    assert asked == []
    assert si._held is not None and si._held.first      # kept for the life of the process
    assert not si.acquire(tmp_path).first


@windows_only
def test_second_copy_asks_and_close_this_one_lets_go(tmp_path, no_kept_lock):
    running = si.acquire(tmp_path)                # the WolfRAT that is already open
    slept = []
    asked = []
    assert si.claim(tmp_path, ask=lambda: asked.append(1) or False,
                    wait_seconds=1.0, sleep=slept.append,
                    clock=lambda: 0.3 * len(slept)) is False
    assert asked == [1]
    assert slept == [si.RECHECK_STEP] * 4          # it looked again for a second first
    assert si._held is None
    running.release()
    assert not si.held_elsewhere(tmp_path)        # the closed copy kept nothing


@windows_only
def test_second_copy_open_anyway_carries_on(tmp_path, no_kept_lock):
    running = si.acquire(tmp_path)
    try:
        assert si.claim(tmp_path, ask=lambda: True, wait_seconds=0) is True
        assert si._held is not None and not si._held.first
    finally:
        running.release()


@windows_only
def test_a_copy_that_is_just_closing_is_not_asked_about(tmp_path, no_kept_lock):
    """The update relaunch: by the time the new exe starts the old one has
    gone.  If it is still on its way out, wait for it, do not ask."""
    closing = si.acquire(tmp_path)
    asked = []
    ticks = [0.0]

    def sleep(seconds):
        ticks[0] += seconds
        closing.release()                         # it finished closing meanwhile

    assert si.claim(tmp_path, ask=lambda: asked.append(1) or True,
                    sleep=sleep, clock=lambda: ticks[0]) is True
    assert asked == [] and si._held.first


# ---------------------------------------------------------- other WolfRATs
def _rows(*rows):
    return lambda: list(rows)


def test_another_wolfrat_exe_counts():
    rows = _rows((10, 1, "explorer.exe"), (20, 10, "WolfRAT2.exe"), (21, 20, "WolfRAT2.exe"),
                 (30, 10, "python.exe"))
    assert si.other_instance_running(rows, own_pid=30, parent_pid=10) is True


def test_this_wolfrats_own_unpacker_does_not_count():
    # The released exe is two processes: the unpacker (20) and the program (21).
    rows = _rows((10, 1, "explorer.exe"), (20, 10, "WolfRAT2.exe"), (21, 20, "WolfRAT2.exe"))
    assert si.other_instance_running(rows, own_pid=21, parent_pid=20) is False


def test_a_second_copy_of_the_same_exe_counts():
    rows = _rows((20, 10, "WolfRAT2.exe"), (21, 20, "WolfRAT2.exe"),
                 (40, 10, "WolfRAT2.exe"), (41, 40, "WolfRAT2.exe"))
    assert si.other_instance_running(rows, own_pid=21, parent_pid=20) is True


def test_a_test_build_started_from_another_wolfrat_counts_its_parent():
    # Parent is a different exe name: it is another WolfRAT, not our unpacker.
    rows = _rows((20, 10, "WolfRAT2.exe"), (21, 20, "WolfRAT2-test.exe"))
    assert si.other_instance_running(rows, own_pid=21, parent_pid=20) is True


def test_no_wolfrat_but_us():
    rows = _rows((10, 1, "explorer.exe"), (30, 10, "python.exe"), (31, 10, "wolfrat_notes.txt"),
                 (32, 10, "jotacserver.exe"))
    assert si.other_instance_running(rows, own_pid=30, parent_pid=10) is False


def test_a_process_list_that_fails_means_no():
    def broken():
        raise OSError("no snapshot")
    assert si.other_instance_running(broken, own_pid=1, parent_pid=0) is False


@windows_only
def test_the_real_process_list_has_this_process():
    assert os.getpid() in {pid for pid, _parent, _name in si.process_list()}
    assert si.pid_running(os.getpid())
    assert not si.pid_running(0)
