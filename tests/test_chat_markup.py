"""Chat colour codes (proven in game 2026-09-26) and the moderator lines
dialog that broke when Dale pasted 44 ChatGPT lines into it."""

import os

import pytest

from wolfrat import admin_commands as ac
from wolfrat import chat_markup as cm
from wolfrat.mod_entrance import line_problem, render_line

DALE_LINES = os.path.join(os.path.dirname(__file__), "data", "mod_lines_dale_2026-09-26.txt")


@pytest.fixture(autouse=True)
def stock_chat():
    ac.set_extended_chat(False)
    yield
    ac.set_extended_chat(False)


def dale_lines():
    with open(DALE_LINES, encoding="utf-8") as handle:
        return [row for row in handle.read().splitlines() if row.strip()]


def test_segments_follow_the_game():
    assert cm.segments("<cFF4040>Red, <cffff00>yellow<co> normal") == [
        ("ff4040", "Red, "), ("ffff00", "yellow"), (None, " normal")]
    assert cm.segments("plain") == [(None, "plain")]
    assert cm.visible("<c00ff00>Hi <co>there") == "Hi there"


@pytest.mark.parametrize("text", ["a < b", "<b>bold</b>", "<cGGGGGG>x", "3 > 2", "<c12345>short"])
def test_stray_angle_brackets_are_named(text):
    assert "not a colour code" in cm.tag_problem(text)


def test_real_codes_pass():
    assert cm.tag_problem("<cFF8000>MAKE WAY!<co> <cFFFFFF>x<co>") is None


def test_names_cannot_open_a_colour_code():
    assert cm.safe_name("<GOD>") == "(GOD)"
    assert "(GOD)" in render_line("<c00FFFF>The storm answers to {player}.", "<GOD>")


def test_preview_html_is_escaped_and_coloured():
    out = cm.to_html("<cff0000>A & B<co> <x")
    assert 'color:#ff0000">A &amp; B' in out and "&lt;x" in out


def test_dales_lines_need_the_long_chat_box():
    lines = dale_lines()
    assert len(lines) == 44
    assert all(line_problem(row) for row in lines)            # stock: all too long
    ac.set_extended_chat(True)
    flagged = [n for n, row in enumerate(lines, 1) if line_problem(row)]
    assert flagged == [40, 41]                                # 121 and 119 of 118


def test_too_long_line_falls_back_to_a_short_welcome():
    ac.set_extended_chat(True)
    assert render_line(dale_lines()[39], "A" * 16) == "Welcome " + "A" * 16 + "!"


pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_pasting_44_lines_does_not_grow_the_dialog(qapp):
    from wolfrat.mod_entrance_panel import LinesDialog
    dialog = LinesDialog(["All rise for {player}."], sample_name="FMJ-BadgerLove")
    dialog.show()
    qapp.processEvents()
    before = dialog.size()
    dialog.edit.setPlainText("\n".join(dale_lines()))
    dialog._check()
    qapp.processEvents()
    assert dialog.size() == before
    hint = dialog.minimumSizeHint()
    assert hint.width() <= 1024 and hint.height() <= 700      # fits a 1024x768 desktop
    assert dialog.problem_list.maximumHeight() <= 110
    assert dialog.check_lbl.text().startswith("44 of 44 lines need a look")
    assert len(dialog.lines()) == 44                          # OK keeps every line


def test_preview_shows_the_row_under_the_cursor(qapp):
    from wolfrat.mod_entrance_panel import LinesDialog
    dialog = LinesDialog(dale_lines(), sample_name="FMJ-BadgerLove")
    cursor = dialog.edit.textCursor()
    cursor.setPosition(dialog.edit.document().findBlockByNumber(41).position())
    dialog.edit.setTextCursor(cursor)
    text = dialog.preview.text()
    assert 'color:#ffff00">ALL RISE FOR ' in text and 'color:#ffffff">FMJ-BadgerLove' in text
