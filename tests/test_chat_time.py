"""!time: the time of day a mod types is the time that is set.

"9" is 09:00, "930" is 09:30, "1830" and "18:30" are 18:30.  Anything that
is not a time gets the usage line and sets nothing.
"""
import pytest

from tests.command_rig import rig, rigs  # noqa: F401  (fixtures)
from wolfrat.admin_commands import AdminCommands
from wolfrat.app import ModsTab

USAGE = "Usage: !time <hhmm> e.g. !time 9 or !time 1830"


TIMES = [
    ("9", "0900"), ("09", "0900"), ("930", "0930"), ("0930", "0930"),
    ("1830", "1830"), ("18:30", "1830"), ("9:30", "0930"), ("09:05", "0905"),
    ("0", "0000"), ("00", "0000"), ("000", "0000"), ("0000", "0000"), ("0:00", "0000"),
    ("1", "0100"), ("5", "0500"), ("12", "1200"), ("23", "2300"), ("2359", "2359"),
    ("100", "0100"), ("1200", "1200"), ("2300", "2300"), ("23:59", "2359"),
]
NOT_TIMES = [
    "", "abc", "24", "2400", "24:00", "25", "99", "1260", "1160", "12:60", "9:5", "9:",
    ":30", "12345", "18:30:00", "-1", "+9", "9.30", "9h", "noon", "1 8", "٩", "²",
]


@pytest.mark.parametrize("text, hhmm", TIMES)
def test_what_is_typed_is_the_time(text, hhmm):
    hour, minute = ModsTab._time_of_day(text)
    assert f"{hour:02d}{minute:02d}" == hhmm
    assert AdminCommands.tod(hhmm).text == f"CMD TOD {hhmm}"       # the server command takes it


@pytest.mark.parametrize("text", NOT_TIMES)
def test_what_is_not_a_time_is_refused(text):
    assert ModsTab._time_of_day(text) is None


def test_every_hour_and_minute_comes_out_as_typed():
    for hour in range(24):
        for minute in (0, 1, 29, 30, 31, 59):
            assert ModsTab._time_of_day(f"{hour:02d}{minute:02d}") == (hour, minute)
            assert ModsTab._time_of_day(f"{hour}:{minute:02d}") == (hour, minute)
        assert ModsTab._time_of_day(str(hour)) == (hour, 0)


@pytest.mark.parametrize("text, hhmm", [
    ("9", "0900"), ("09", "0900"), ("930", "0930"), ("0930", "0930"),
    ("1830", "1830"), ("18:30", "1830"), ("0", "0000"), ("2359", "2359"),
])
def test_time_typed_in_the_game_and_on_the_phone(rig, text, hhmm):
    said = f"Time set to {hhmm[:2]}:{hhmm[2:]}"
    rig.game("Dale", f"!time {text}")
    assert rig.server.calls == [("set_time_of_day", hhmm)]
    assert rig.server.chat == [said]
    assert rig.last_log().endswith(f"dale requested time of day {hhmm[:2]}:{hhmm[2:]} (unverified)")
    assert rig.phone(f"!time {text}") == {"ok": True, "command": "!time"}
    assert rig.server.calls == [("set_time_of_day", hhmm)]
    assert rig.server.chat == [said]


@pytest.mark.parametrize("text", ["24", "abc", "", "1260", "12345", "9:5"])
def test_not_a_time_sets_nothing_and_says_how(rig, text):
    rig.game("Dale", f"!time {text}".strip())
    assert rig.server.calls == []
    assert rig.server.chat == [USAGE]
    rig.phone(f"!time {text}".strip())
    assert rig.server.calls == []


def test_only_the_first_word_is_read(rig):
    rig.game("Dale", "!time 18 30")
    assert rig.server.calls == [("set_time_of_day", "1800")]


def test_the_reference_and_the_usage_line_say_how_to_type_it(rig):
    row = next(text for section, commands in ModsTab.COMMAND_REFERENCE
               for command, text, _ in commands if command.startswith("!time"))
    assert "becomes" not in row and "18:30" in row
    assert len(USAGE) <= 59
