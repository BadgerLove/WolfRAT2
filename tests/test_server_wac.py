"""server.wac since 2026-11-21: the old script private welcome is only read
(its lines are copied into the new private welcome once) and taken out (when
the new one is switched on); nothing writes it any more. The script's string
rules (text_problem) still guard whisper.py's lines."""
import os

import pytest

from wolfrat import server_wac as sw
from wolfrat import weather as w


def raw(folder):
    with open(os.path.join(str(folder), sw.FILENAME), "rb") as handle:
        return handle.read()


def assert_crlf_only(data: bytes):
    assert data.count(b"\n") == data.count(b"\r\n") and data.count(b"\r") == data.count(b"\r\n")


def old_block(text, seconds=20, colour="00ff00", text2="", colour2="ffff00", gap=3):
    """The section exactly as WolfRAT up to 2.10.25 wrote it."""
    lines = [sw.BEGIN, "// Only the player who just joined sees these lines.", "PLOOP",
             f"if onptick({seconds}) then", f'ptext("{sw.compose(text, colour)}")', "endif"]
    if text2:
        lines += [f"if onptick({seconds + gap}) then",
                  f'ptext("{sw.compose(text2, colour2)}")', "endif"]
    return "\n".join(lines + ["END", sw.END]) + "\n"


def add_old_block(folder, block):
    body = sw._read(folder)
    if body and not body.endswith("\n"):
        body += "\n"
    sw._write(folder, body + ("\n" if body else "") + block)


def test_an_old_one_line_welcome_reads_back(tmp_path):
    add_old_block(tmp_path, old_block("Welcome to Badger's server"))
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server")
    assert_crlf_only(raw(tmp_path))


def test_an_old_two_line_welcome_with_typed_codes_reads_back(tmp_path):
    typed = "<c40c0ff>Welcome<co> to Badger's 125hz server!"
    second = "<cffff00>Chat:<co> <c00ff00>!kd<co> <c40c0ff>!switch"
    add_old_block(tmp_path, old_block(typed, 20, "", second, "", 3))
    back = sw.read_welcome(tmp_path)
    assert sw.compose(back.text, back.colour) == typed
    assert sw.compose(back.text2, back.colour2) == second and back.gap == 3


def test_a_one_line_section_written_by_the_first_build_still_reads(tmp_path):
    old = ("// >>> WolfRAT private welcome (written by WolfRAT - change it in WolfRAT, not here)\r\n"
           "// Only the player who just joined sees this line.\r\nPLOOP\r\nif onptick(20) then\r\n"
           "ptext(\"<c00ff00>Welcome to Badger's server\")\r\nendif\r\nEND\r\n"
           "// <<< WolfRAT private welcome\r\n")
    with open(os.path.join(str(tmp_path), sw.FILENAME), "wb") as handle:
        handle.write(old.encode("ascii"))
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server")


def test_no_file_or_no_block_reads_as_none(tmp_path):
    assert sw.read_welcome(tmp_path) is None
    w.install_addon(tmp_path)
    assert sw.read_welcome(tmp_path) is None


def test_remove_takes_out_only_the_old_block_and_keeps_the_rest_byte_for_byte(tmp_path):
    w.install_addon(tmp_path)
    before = raw(tmp_path)
    add_old_block(tmp_path, old_block("Hello"))
    assert sw.remove_welcome(tmp_path) is True
    assert raw(tmp_path).rstrip() == before.rstrip()
    assert w.addon_installed(tmp_path) and sw.read_welcome(tmp_path) is None
    assert sw.remove_welcome(tmp_path) is False
    assert_crlf_only(raw(tmp_path))


def test_remove_keeps_an_admins_own_script_around_it(tmp_path):
    path = os.path.join(str(tmp_path), sw.FILENAME)
    with open(path, "wb") as handle:                      # an admin's own LF-only script
        handle.write(b"// my script\nif never then\ntext(\"hi\")\nendif\n")
    add_old_block(tmp_path, old_block("First", 15, "ff8800"))
    with open(path, "ab") as handle:
        handle.write(b"\r\n// added by hand later\r\n")
    assert sw.remove_welcome(tmp_path) is True
    text = raw(tmp_path).decode("ascii")
    assert "PLOOP" not in text and "First" not in text
    assert text.index("// my script") < text.index("// added by hand later")
    assert_crlf_only(raw(tmp_path))


@pytest.mark.parametrize("text, word", [
    ("", "Type the welcome"),
    ("   ", "Type the welcome"),
    ('Say "hi"', "double quote"),
    ("a <b> c", "not a colour code"),
    ("café", "Plain English"),
    ("line\nbreak", "Plain English"),
    ("x" * 101, "Too long"),
])
def test_text_that_would_break_the_script_is_named(text, word):
    assert word in sw.text_problem(text)


def test_good_script_text_passes():
    assert sw.text_problem("Welcome to Badger's server! Rules: !rules") is None


def test_a_folder_that_cannot_be_written_says_so_plainly(tmp_path):
    blocked = tmp_path / "nope" / "deeper"
    with pytest.raises(sw.ServerWacError) as err:
        sw._write(blocked, "x\n")
    assert "administrator" in str(err.value)


def test_nothing_writes_the_old_block_any_more():
    for gone in ("write_welcome", "build_section", "line_problem"):
        assert not hasattr(sw, gone), gone
