"""The Private welcome page (rebuilt 2026-11-21): live, through the
private-line helper, up to three lines of 118 characters."""

import json

import pytest

from wolfrat import manual_mode
from wolfrat import private_welcome as pw
from wolfrat import server_wac as sw
from wolfrat.private_welcome_panel import PrivateWelcomePanel


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def full_mode(monkeypatch):
    monkeypatch.setattr(manual_mode, "skip", lambda _feature: False)


def players(*names):
    return [{"name": name} for name in names]


def make(qtbot, tmp_path, server_dir="", settings=None):
    path = tmp_path / "wolfrat_private_welcome.json"
    if settings is not None:
        pw.save(path, settings)
    logs, sent = [], []
    panel = PrivateWelcomePanel(path, lambda: server_dir, log=logs.append, clock=Clock())
    qtbot.addWidget(panel)
    panel.send_private = lambda name, text: sent.append((name, text)) or True
    panel.helper_ready = lambda: True
    return panel, path, logs, sent


def old_block(text, text2=""):
    lines = [sw.BEGIN, "PLOOP", "if onptick(20) then", f'ptext("{text}")', "endif"]
    if text2:
        lines += ["if onptick(23) then", f'ptext("{text2}")', "endif"]
    return "\n".join(lines + ["END", sw.END]) + "\n"


def test_a_player_who_joins_gets_every_line_after_the_delay(qtbot, tmp_path):
    settings = pw.Settings(True, ["Welcome {player}!", "", "<cffff00>Commands:<co> !mycmd"], 5)
    panel, _path, logs, sent = make(qtbot, tmp_path, settings=settings)
    panel.players_updated(players("Host", "Dale"))          # already playing: learned only
    panel.players_updated(players("Host", "Dale", "Mudd"))
    assert sent == []                                       # not before the delay
    qtbot.waitUntil(lambda: len(sent) == 2, timeout=8000)
    assert sent == [("Mudd", "Welcome Mudd!"), ("Mudd", "<cffff00>Commands:<co> !mycmd")]
    assert any("Private welcome sent to Mudd (2 line(s))" in line for line in logs)
    assert "Sent to 1 player" in panel.state_lbl.text()


def test_nobody_gets_it_when_it_is_off_or_they_left_first(qtbot, tmp_path):
    settings = pw.Settings(False, ["Hi {player}", "", ""], 5)
    panel, _path, _logs, sent = make(qtbot, tmp_path, settings=settings)
    panel.players_updated(players())
    panel.players_updated(players("Ann"))
    qtbot.wait(5600)
    assert sent == []
    panel.enabled_cb.setChecked(True)
    panel.players_updated(players("Ann", "Bob"))
    panel.players_updated(players("Ann"))                   # Bob left before it was due
    qtbot.wait(5600)
    assert sent == []


def test_helper_not_ready_sends_nothing_publicly_and_says_why(qtbot, tmp_path):
    settings = pw.Settings(True, ["Hi {player}", "", ""], 5)
    panel, _path, logs, _sent = make(qtbot, tmp_path, settings=settings)
    panel.send_private = lambda name, text: False
    panel.helper_ready = lambda: False
    panel._edited()
    assert "can't send private lines right now" in panel.state_lbl.text()
    panel.players_updated(players())
    panel.players_updated(players("Ann"))
    qtbot.waitUntil(lambda: any("not sent to Ann" in line for line in logs), timeout=8000)


def test_try_it_sends_to_the_picked_player_now(qtbot, tmp_path):
    settings = pw.Settings(False, ["Hi {player}", "Line two", ""], 30)
    panel, _path, _logs, sent = make(qtbot, tmp_path, settings=settings)
    panel.players_updated(players("Dale", "Mudd"))
    panel.try_combo.setCurrentText("Mudd")
    assert panel.try_btn.isEnabled()
    panel.try_btn.click()
    assert sent == [("Mudd", "Hi Mudd"), ("Mudd", "Line two")]


def test_edits_are_saved_and_too_long_lines_are_named(qtbot, tmp_path):
    panel, path, _logs, _sent = make(qtbot, tmp_path)
    panel.enabled_cb.setChecked(True)
    panel.lines[0].text.setText("Welcome {player}")
    panel.seconds_spin.setValue(45)
    panel.lines[1].text.setText("x" * 119)
    assert "Line 2: Too long: 119 of 118" in panel.state_lbl.text()
    assert panel.lines[1].count.text() == "119/118"
    qtbot.waitUntil(lambda: path.exists() and json.loads(path.read_text())["seconds"] == 45,
                    timeout=3000)
    saved = json.loads(path.read_text())
    assert saved["enabled"] is True and saved["lines"][0] == "Welcome {player}"


def test_the_old_script_welcome_is_copied_in_once_and_taken_out_when_switched_on(qtbot, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    sw._write(server, old_block("<c40c0ff>Welcome<co> to FMJ", "<cffff00>Commands: !kd"))
    panel, path, logs, _sent = make(qtbot, tmp_path, server_dir=str(server))
    assert [line.value() for line in panel.lines] == [
        "<c40c0ff>Welcome<co> to FMJ", "<cffff00>Commands: !kd", ""]
    assert not panel.enabled_cb.isChecked()                  # copied, not switched on
    assert panel.old_lbl.isVisible() or "still in the server's script" in panel.old_lbl.text()
    assert sw.read_welcome(server) is not None               # untouched until switched on
    panel.enabled_cb.setChecked(True)
    assert sw.read_welcome(server) is None                   # taken out: nobody gets both
    assert any("taken out of server.wac" in line for line in logs)
    assert json.loads(path.read_text())["imported"] is True


def test_lines_already_typed_are_not_replaced_by_the_old_script(qtbot, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    sw._write(server, old_block("Old line"))
    settings = pw.Settings(False, ["My new line", "", ""], 30)
    panel, _path, _logs, _sent = make(qtbot, tmp_path, server_dir=str(server), settings=settings)
    assert panel.lines[0].value() == "My new line"


# 2026-11-21, Dale's welcome lost: the page is built before WolfRAT knows the
# server's folder, so the copy found nothing and was never tried again; ticking
# the box then took the old welcome out of server.wac with nothing copied over.

OLD_1 = "<c40c0ff>Welcome to <cffff00>Badger's <cff8000>125hz server!"
OLD_2 = "<c00ff00>For your commands list use<c40c0ff> !mycmd"


def late_folder_panel(qtbot, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    sw._write(server, old_block(OLD_1, OLD_2))
    found = [""]                                   # not known while the page is built
    path = tmp_path / "wolfrat_private_welcome.json"
    logs = []
    panel = PrivateWelcomePanel(path, lambda: found[0], log=logs.append, clock=Clock())
    qtbot.addWidget(panel)
    panel.send_private = lambda name, text: True
    panel.helper_ready = lambda: True
    assert [line.value() for line in panel.lines] == ["", "", ""]
    found[0] = str(server)
    return panel, server, path, logs


def test_the_old_welcome_comes_over_once_the_server_folder_is_known(qtbot, tmp_path):
    panel, server, path, logs = late_folder_panel(qtbot, tmp_path)
    panel.players_updated(players("Dale"))         # the next player list
    assert [line.value() for line in panel.lines] == [OLD_1, OLD_2, ""]
    assert not panel.enabled_cb.isChecked()         # copied, not switched on
    assert json.loads(path.read_text())["imported"] is True
    assert sw.read_welcome(server) is not None       # still running until switched on
    assert "copied in below" in panel.old_lbl.text()
    assert "bold" in panel.old_lbl.styleSheet()
    assert any("copied in" in line for line in logs)


def test_switching_on_before_the_copy_brings_the_lines_over_first(qtbot, tmp_path):
    panel, server, path, logs = late_folder_panel(qtbot, tmp_path)
    panel.enabled_cb.setChecked(True)               # what Dale did
    assert [line.value() for line in panel.lines] == [OLD_1, OLD_2, ""]
    assert sw.read_welcome(server) is None           # taken out only once they are safe
    assert any("taken out of server.wac" in line for line in logs)
    qtbot.waitUntil(lambda: bool(json.loads(path.read_text())["enabled"]), timeout=3000)
    saved = json.loads(path.read_text())
    assert saved["imported"] is True and saved["lines"][:2] == [OLD_1, OLD_2]


def test_the_old_welcome_stays_while_nothing_is_on_the_page(qtbot, tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    sw._write(server, old_block("Old line"))
    monkeypatch.setattr(pw, "old_script_lines", lambda _folder: None)   # could not be read
    panel, _path, _logs, _sent = make(qtbot, tmp_path, server_dir=str(server))
    panel.enabled_cb.setChecked(True)
    assert sw.read_welcome(server) is not None       # kept: nothing would replace it


def test_the_old_welcome_notice_is_the_first_thing_on_the_page(qtbot, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    sw._write(server, old_block(OLD_1))
    panel, _path, _logs, _sent = make(qtbot, tmp_path, server_dir=str(server))
    group_layout = panel.enabled_cb.parentWidget().layout()
    assert group_layout.itemAt(0).widget() is panel.old_lbl
    assert "still in the server's script" in panel.old_lbl.text()
