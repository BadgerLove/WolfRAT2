"""Private welcome: writing our section of server.wac without hurting the rest."""
import os

import pytest

from wolfrat import server_wac as sw
from wolfrat import weather as w


def raw(folder):
    with open(os.path.join(str(folder), sw.FILENAME), "rb") as handle:
        return handle.read()


def assert_crlf_only(data: bytes):
    assert data.count(b"\n") == data.count(b"\r\n") and data.count(b"\r") == data.count(b"\r\n")


def test_added_after_the_lightning_addon_which_is_left_byte_for_byte(tmp_path):
    assert w.install_addon(tmp_path) == "installed"
    before = raw(tmp_path)
    assert sw.write_welcome(tmp_path, sw.Welcome("Welcome to Badger's server")) == "added"
    after = raw(tmp_path)
    assert after.startswith(before)
    assert_crlf_only(after)
    assert w.addon_installed(tmp_path)
    text = after.decode("ascii")
    assert 'ptext("<c00ff00>Welcome to Badger\'s server")' in text
    assert "if onptick(20) then" in text and "PLOOP" in text and "\r\nEND\r\n" in text
    assert " enter" not in text          # `enter` is per block, not per player


def test_lightning_can_still_be_installed_after_the_welcome(tmp_path):
    sw.write_welcome(tmp_path, sw.Welcome("Hello"))
    assert w.install_addon(tmp_path) == "installed"
    assert sw.read_welcome(tmp_path) == sw.Welcome("Hello")
    assert_crlf_only(raw(tmp_path))


def test_update_replaces_only_our_section_and_reads_back(tmp_path):
    path = os.path.join(str(tmp_path), sw.FILENAME)
    with open(path, "wb") as handle:                      # an admin's own LF-only script
        handle.write(b"// my script\nif never then\ntext(\"hi\")\nendif\n")
    sw.write_welcome(tmp_path, sw.Welcome("First", 15, "ff8800"))
    with open(path, "ab") as handle:
        handle.write(b"\r\n// added by hand later\r\n")
    assert sw.write_welcome(tmp_path, sw.Welcome("Second one", 30, "")) == "updated"
    text = raw(tmp_path).decode("ascii")
    assert "First" not in text and text.count("PLOOP") == 1
    assert text.index("// my script") < text.index("PLOOP") < text.index("// added by hand later")
    assert 'ptext("Second one")' in text
    assert sw.read_welcome(tmp_path) == sw.Welcome("Second one", 30, "")
    assert_crlf_only(raw(tmp_path))
    assert sw.write_welcome(tmp_path, sw.Welcome("Second one", 30, "")) == "unchanged"


def test_remove_takes_out_only_our_section(tmp_path):
    w.install_addon(tmp_path)
    before = raw(tmp_path)
    sw.write_welcome(tmp_path, sw.Welcome("Hello"))
    assert sw.remove_welcome(tmp_path) is True
    assert raw(tmp_path).rstrip() == before.rstrip()
    assert sw.read_welcome(tmp_path) is None
    assert sw.remove_welcome(tmp_path) is False


@pytest.mark.parametrize("text, word", [
    ("", "Type the welcome"),
    ("   ", "Type the welcome"),
    ('Say "hi"', "double quote"),
    ("a <b> c", "not a colour code"),
    ("café", "Plain English"),
    ("line\nbreak", "Plain English"),
    ("x" * 101, "Too long"),
])
def test_text_that_would_break_the_script_is_refused_and_nothing_is_written(tmp_path, text, word):
    assert word in sw.text_problem(text)
    with pytest.raises(sw.ServerWacError):
        sw.write_welcome(tmp_path, sw.Welcome(text))
    assert not os.path.exists(os.path.join(str(tmp_path), sw.FILENAME))


def test_good_text_passes_and_seconds_are_clamped(tmp_path):
    assert sw.text_problem("Welcome to Badger's server! Rules: !rules") is None
    sw.write_welcome(tmp_path, sw.Welcome("Hi", 9999))
    assert sw.read_welcome(tmp_path).seconds == sw.MAX_SECONDS
    with pytest.raises(sw.ServerWacError):
        sw.build_section(sw.Welcome("Hi", 20, "green"))


def test_folder_that_cannot_be_written_says_so_plainly(tmp_path):
    blocked = tmp_path / "nope" / "deeper"
    with pytest.raises(sw.ServerWacError) as err:
        sw.write_welcome(blocked, sw.Welcome("Hi"))
    assert "administrator" in str(err.value)


def test_second_line_with_its_own_colour_a_few_seconds_later(tmp_path):
    w.install_addon(tmp_path)
    both = sw.Welcome("Welcome to Badger's server", 20, "00ff00",
                      "Chat commands: !kd  !switch  !vote mapname  !skip", "ffff00", 3)
    assert sw.write_welcome(tmp_path, both) == "added"
    text = raw(tmp_path).decode("ascii")
    assert_crlf_only(raw(tmp_path))
    assert text.count("PLOOP") == 1 and text.count("\r\nEND\r\n") == 1      # one loop, two blocks
    assert "if onptick(20) then\r\nptext(\"<c00ff00>Welcome to Badger's server\")\r\nendif" in text
    assert 'if onptick(23) then\r\nptext("<cffff00>Chat commands: !kd  !switch  !vote mapname  !skip")\r\nendif' in text
    assert sw.read_welcome(tmp_path) == both

    # dropping the second line again leaves a plain one-line welcome
    assert sw.write_welcome(tmp_path, sw.Welcome("Welcome to Badger's server")) == "updated"
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server")
    assert "onptick(23)" not in raw(tmp_path).decode("ascii")


def test_a_one_line_section_written_by_the_first_build_still_reads(tmp_path):
    old = ("// >>> WolfRAT private welcome (written by WolfRAT - change it in WolfRAT, not here)\r\n"
           "// Only the player who just joined sees this line.\r\nPLOOP\r\nif onptick(20) then\r\n"
           "ptext(\"<c00ff00>Welcome to Badger's server\")\r\nendif\r\nEND\r\n"
           "// <<< WolfRAT private welcome\r\n")
    with open(os.path.join(str(tmp_path), sw.FILENAME), "wb") as handle:
        handle.write(old.encode("ascii"))
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server")


def test_a_bad_second_line_is_refused_and_named(tmp_path):
    with pytest.raises(sw.ServerWacError) as err:
        sw.write_welcome(tmp_path, sw.Welcome("Hi", text2='type "!kd"'))
    assert "Second line" in str(err.value)
    assert not os.path.exists(os.path.join(str(tmp_path), sw.FILENAME))


# ---- typed colour codes and the script's 63-character string (2026-09-26) ----

def test_typed_codes_go_in_as_typed_and_read_back(tmp_path):
    typed = "<c40c0ff>Welcome<co> to Badger's 125hz server!"
    sw.write_welcome(tmp_path, sw.Welcome(typed, 20, ""))
    text = open(os.path.join(str(tmp_path), sw.FILENAME), encoding="latin-1").read()
    assert "ptext(\"<c40c0ff>Welcome<co> to Badger's 125hz server!\")" in text
    back = sw.read_welcome(tmp_path)
    assert sw.compose(back.text, back.colour) == typed


def test_several_colours_on_one_line(tmp_path):
    typed = "<cffff00>Chat:<co> <c00ff00>!kd<co> <c40c0ff>!switch"
    sw.write_welcome(tmp_path, sw.Welcome("Hi", 20, "", typed, "", 3))
    back = sw.read_welcome(tmp_path)
    assert sw.compose(back.text2, back.colour2) == typed


def test_63_fits_64_is_refused_with_colour_codes_counted(tmp_path):
    ok = "<c00ff00>" + "x" * (63 - 9)
    assert sw.line_problem(ok) is None
    assert "Too long: 64 of 63" in sw.line_problem(ok + "y")
    with pytest.raises(sw.ServerWacError):
        sw.write_welcome(tmp_path, sw.Welcome(ok + "y", 20, ""))
    assert not os.path.exists(os.path.join(str(tmp_path), sw.FILENAME))


def test_the_live_welcome_lines_still_pass():
    for line in ("<c40c0ff>Welcome to Badger's 125hz server!",
                 "<cffff00>Chat commands: !ping !kd  !switch  !vote !skip"):
        assert sw.line_problem(line) is None
