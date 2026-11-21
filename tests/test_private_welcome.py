"""The private welcome's rules (private_welcome.py, Dale 2026-11-21): lines of
up to 118 characters through the private-line helper, once per visit."""

from wolfrat import private_welcome as pw
from wolfrat import server_wac as sw


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_first_list_after_wolfrat_starts_welcomes_nobody():
    watch = pw.JoinWatch(Clock())
    assert watch.update(["Host", "Dale", "Mudd"]) == []
    assert watch.update(["Host", "Dale", "Mudd", "Newbie"]) == ["Newbie"]


def test_once_per_visit_and_again_after_ten_minutes_away():
    clock = Clock()
    watch = pw.JoinWatch(clock)
    watch.update([])
    assert watch.update(["Ann"]) == ["Ann"]
    clock.now += 5
    assert watch.update(["Ann"]) == []          # still here
    clock.now += 30
    assert watch.update([]) == []               # left (or a map change: no list)
    clock.now += 200
    assert watch.update(["Ann"]) == []          # back within 10 minutes: same visit
    clock.now += 601
    assert watch.update([]) == []
    clock.now += 1
    assert watch.update(["Ann"]) == ["Ann"]     # away more than 10 minutes: welcome again


def test_a_name_seen_continuously_is_never_welcomed_twice():
    clock = Clock()
    watch = pw.JoinWatch(clock)
    watch.update([])
    assert watch.update(["Bob"]) == ["Bob"]
    for _ in range(300):                        # 25 minutes of 5 s lists
        clock.now += 5
        assert watch.update(["Bob"]) == []


def test_line_limits_count_colour_codes_and_a_16_letter_name():
    code = "<c40c0ff>"
    assert pw.line_problem(code + "x" * (118 - 9)) is None
    assert "Too long: 119 of 118" in pw.line_problem(code + "x" * (118 - 8))
    name_line = "Hi {player}" + "x" * (118 - 3 - 16)
    assert pw.line_problem(name_line) is None
    assert "with a 16-letter name" in pw.line_problem(name_line + "x")
    assert pw.line_problem("") is None and pw.line_problem("   ") is None
    assert "can't show" in pw.line_problem("Welcome ☃")


def test_render_puts_the_name_in_and_cuts_to_one_helper_line():
    assert pw.render("  Welcome {player}!  ", "Mudd") == "Welcome Mudd!"
    assert len(pw.render("x" * 300, "A")) == pw.LINE_LIMIT


def test_settings_round_trip_and_bad_values(tmp_path):
    path = tmp_path / "pw.json"
    assert pw.load(path) is None
    pw.save(path, pw.Settings(True, ["one", "", "three"], 45, True))
    back = pw.load(path)
    assert back == pw.Settings(True, ["one", "", "three"], 45, True)
    assert back.used_lines() == ["one", "three"]
    path.write_text('{"enabled": true, "lines": ["a"], "seconds": "nonsense"}', encoding="utf-8")
    back = pw.load(path)
    assert back.lines == ["a", "", ""] and back.seconds == pw.DEFAULT_SECONDS
    path.write_text('{"seconds": 9999}', encoding="utf-8")
    assert pw.load(path).seconds == pw.MAX_SECONDS


def test_the_old_script_welcome_lines_come_over_with_their_colours(tmp_path):
    block = "\n".join([
        sw.BEGIN, "PLOOP", "if onptick(20) then", 'ptext("<c40c0ff>Welcome<co> to FMJ")', "endif",
        "if onptick(23) then", 'ptext("<cffff00>Commands: !kd !switch")', "endif", "END", sw.END]) + "\n"
    sw._write(tmp_path, block)
    assert pw.old_script_lines(tmp_path) == ["<c40c0ff>Welcome<co> to FMJ",
                                             "<cffff00>Commands: !kd !switch"]
    assert pw.old_script_lines("") is None
    assert pw.old_script_lines(tmp_path / "empty") is None
