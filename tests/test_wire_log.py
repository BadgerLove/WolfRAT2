"""The wire log keeps everything without growing a gigabyte in three days.

SgtMudd's 3-day log was 1 GB: every 5 s poll reply (the full 172-map list,
game settings, 500 lines of chat) was written, and every line twice. It was
also wiped on each start, which loses the history around a problem.
"""

from wolfrat import protocol
from wolfrat.admin_commands import AdminOperation
from wolfrat.admin_session import CommandResult


def _use_temp_log(monkeypatch, tmp_path):
    path = tmp_path / "wolfrat_wire.log"
    monkeypatch.setattr(protocol, "_wire_log_path", lambda: str(path))
    monkeypatch.setattr(protocol, "_wire_day", None)
    monkeypatch.setattr(protocol, "_wire_last", None)
    monkeypatch.setattr(protocol, "_wire_repeats", 0)
    return path


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_appends_to_an_existing_log_and_heads_each_day(monkeypatch, tmp_path):
    path = _use_temp_log(monkeypatch, tmp_path)
    path.write_text("[23:59:59] yesterday's line\n", encoding="utf-8")

    protocol.wire_log("today's line")

    lines = _lines(path)
    assert lines[0] == "[23:59:59] yesterday's line"
    assert lines[1].startswith("---------- ")
    assert lines[2].endswith("] today's line")


def test_back_to_back_repeats_fold_into_one_note(monkeypatch, tmp_path):
    path = _use_temp_log(monkeypatch, tmp_path)

    for _ in range(4):
        protocol.wire_log("Connection failed: refused")
    protocol.wire_log("Connected")

    body = [line for line in _lines(path) if not line.startswith("-")]
    assert body[0].endswith("] Connection failed: refused")
    assert body[1].endswith("] (line above repeated 3 more times)")
    assert body[2].endswith("] Connected")
    assert len(body) == 3


def _manager(monkeypatch, tmp_path):
    path = _use_temp_log(monkeypatch, tmp_path)
    manager = protocol.ServerManager()
    console = []
    manager._on_log = console.append
    return manager, path, console


def _poll(manager, operation, reply, value=()):
    manager._apply_result(
        CommandResult(operation, (reply,), True, value), quiet=True
    )


def test_quiet_polls_reach_the_file_only_when_they_change(monkeypatch, tmp_path):
    manager, path, console = _manager(monkeypatch, tmp_path)

    for state in ("Game", "Game", "Game", "Menus", "Menus"):
        _poll(manager, AdminOperation.GET_GAMESTATE,
              f"OK - Current State = {state}", state)

    written = [line for line in _lines(path) if "Current State" in line]
    assert len(written) == 2
    assert written[1].endswith("Current State = Menus")
    # The console still sees every poll.
    assert sum("Current State" in line for line in console) == 5


def test_poll_commands_are_not_written(monkeypatch, tmp_path):
    manager, path, console = _manager(monkeypatch, tmp_path)

    manager._log("__QUIET__>> get_gamestate", to_file=False)

    assert not path.exists() or "get_gamestate" not in path.read_text("utf-8")
    assert console == ["__QUIET__>> get_gamestate"]


def test_player_list_is_written_when_the_roster_changes(monkeypatch, tmp_path):
    manager, path, _ = _manager(monkeypatch, tmp_path)
    header = "NAME\t #\tTEAM\tClass\tKills\tDeaths\tPING"

    _poll(manager, AdminOperation.PLAYER_LIST, f"{header}\nBob\t 2\t 1\t7\t1\t0\t90")
    _poll(manager, AdminOperation.PLAYER_LIST, f"{header}\nBob\t 2\t 1\t7\t5\t2\t120")
    _poll(manager, AdminOperation.PLAYER_LIST,
          f"{header}\nBob\t 2\t 1\t7\t5\t2\t120\nAnn\t 3\t 2\t1\t0\t0\t60")

    assert sum(line.count("NAME") for line in _lines(path)) == 2


def test_chat_poll_replies_are_left_to_the_chat_lines(monkeypatch, tmp_path):
    manager, path, _ = _manager(monkeypatch, tmp_path)

    _poll(manager, AdminOperation.CHAT_GET, "Bob: hi\nAnn: hello", ("Bob: hi", "Ann: hello"))

    assert not path.exists() or "Bob: hi" not in path.read_text("utf-8")


def test_new_current_map_is_one_line_and_an_emptied_queue_is_in_full(monkeypatch, tmp_path):
    manager, path, _ = _manager(monkeypatch, tmp_path)
    first = "0: A.bms - () () () <CURRENT MISSION> <>\n1: B.npj - () () () <> <>"
    second = "0: A.bms - () () () <> <>\n1: B.npj - () () () <CURRENT MISSION> <>"

    _poll(manager, AdminOperation.MISSION_LIST, first)
    _poll(manager, AdminOperation.MISSION_LIST, first)
    _poll(manager, AdminOperation.MISSION_LIST, second)
    _poll(manager, AdminOperation.MISSION_LIST, "No missions in queue.")

    text = path.read_text("utf-8")
    assert text.count("0: A.bms") == 1
    assert "Mission queue unchanged (2 maps), current is now 1: B.npj" in text
    assert text.rstrip().endswith("No missions in queue.")


def test_rejected_poll_replies_are_deduplicated_per_command(monkeypatch, tmp_path):
    manager, path, _ = _manager(monkeypatch, tmp_path)

    for _ in range(3):
        for operation in (AdminOperation.PLAYER_LIST, AdminOperation.CHAT_GET):
            manager._apply_result(CommandResult(
                operation, ("ERROR - Not in Game State.",), False
            ), quiet=True)

    protocol.wire_log("next line")

    text = path.read_text("utf-8")
    # Once for player_list; chat's identical reply folds into a repeat note.
    assert text.count("Not in Game State") == 1
    assert "(line above repeated once more)" in text


def test_game_settings_ignore_the_minutes_left(monkeypatch, tmp_path):
    manager, path, _ = _manager(monkeypatch, tmp_path)

    for game_time in ("24/30", "23/30", "22/30", "22/45"):
        _poll(manager, AdminOperation.GET_GAMESETTINGS,
              f"AutoBalanceOnRecycle = 0\nGameTime             = {game_time}")

    text = path.read_text("utf-8")
    assert text.count("AutoBalanceOnRecycle") == 2
    assert "22/45" in text
