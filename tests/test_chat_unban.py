"""!unban <name or IP>: typed in the game or on the phone, it takes a ban off
WolfRAT's list - the name and the address that was banned with it."""
import json

import pytest

from tests.command_rig import CommandRig
from wolfrat import ban_rules as br
from wolfrat.ban_rules import BanEntry, BanList, KIND_IP, KIND_NAME
from wolfrat.protocol import _split_chat_message


@pytest.fixture
def rig(qtbot, tmp_path, monkeypatch):
    return CommandRig(qtbot, tmp_path, monkeypatch)


def banned(rig):
    return sorted((entry.kind, entry.value) for entry in rig.bans.bans.entries)


def can_join(rig, name, ip):
    return rig.bans.bans.check(name, ip, rig.bans._clock()) is None


# ---- the list itself ----------------------------------------------------------

def test_a_name_comes_off_with_the_addresses_banned_with_it():
    bans = BanList()
    bans.add(BanEntry(KIND_NAME, "Griefer", linked="5.6.7.8"))
    bans.add(BanEntry(KIND_IP, "5.6.7.8", linked="Griefer"))
    bans.add(BanEntry(KIND_IP, "9.9.9.9", linked="griefer"))      # a later ban, new address
    bans.add(BanEntry(KIND_IP, "5.6.7.9", linked="Other"))
    bans.add(BanEntry(KIND_NAME, "Other"))
    gone = bans.remove_with_linked("GRIEFER")
    assert sorted((e.kind, e.value) for e in gone) == [
        ("ip", "5.6.7.8"), ("ip", "9.9.9.9"), ("name", "Griefer")]
    assert [(e.kind, e.value) for e in bans.entries] == [("ip", "5.6.7.9"), ("name", "Other")]
    assert bans.remove_with_linked("Griefer") == []


def test_an_address_comes_off_alone_and_the_name_stays_banned():
    bans = BanList()
    bans.add(BanEntry(KIND_NAME, "Griefer", linked="5.6.7.8"))
    bans.add(BanEntry(KIND_IP, "5.6.7.8", linked="Griefer"))
    gone = bans.remove_with_linked("5.6.7.8")
    assert [(e.kind, e.value) for e in gone] == [("ip", "5.6.7.8")]
    assert [(e.kind, e.value) for e in bans.entries] == [("name", "Griefer")]


def test_an_address_banned_with_nobody_and_a_shared_one_are_left_alone():
    bans = BanList()
    bans.add(BanEntry(KIND_NAME, "Griefer", linked="5.6.7.8"))
    bans.add(BanEntry(KIND_IP, "5.6.7.8", linked="Brother"))      # banned again with another name
    bans.add(BanEntry(KIND_IP, "82.68.*"))                         # typed in by hand
    assert [(e.kind, e.value) for e in bans.remove_with_linked("Griefer")] == [("name", "Griefer")]
    assert [(e.kind, e.value) for e in bans.entries] == [("ip", "5.6.7.8"), ("ip", "82.68.*")]
    assert bans.remove_with_linked("") == [] and len(bans.entries) == 2


def test_the_whitelist_is_never_touched():
    bans = BanList()
    bans.add(BanEntry(KIND_NAME, "Ham"), whitelist=True)
    bans.add(BanEntry(KIND_IP, "10.0.0.3", linked="Ham"), whitelist=True)
    assert bans.remove_with_linked("Ham") == []
    assert len(bans.whitelist) == 2


# ---- the Bans tab ----------------------------------------------------------------

def test_unban_by_name_lets_the_player_back_in(rig):
    rig.bans.ban_now("Griefer", reason="griefing", added_by="Dale", kind="both")
    assert banned(rig) == [("ip", "5.6.7.8"), ("name", "Griefer")]
    assert not can_join(rig, "Griefer", "5.6.7.8") and not can_join(rig, "NewName", "5.6.7.8")
    assert rig.bans.unban("griefer") == 2
    assert banned(rig) == []
    assert can_join(rig, "Griefer", "5.6.7.8")
    assert rig.ban_log[-1] == "Unbanned griefer (and the address 5.6.7.8)"
    assert rig.bans.ban_table.rowCount() == 0
    saved = json.loads((rig.folder / "wolfrat_bans.json").read_text(encoding="utf-8"))
    assert br.BanList.from_json(saved).entries == []


def test_an_address_banned_by_the_connection_check_comes_off_with_the_name(rig):
    rig.bans.bans.add(BanEntry(KIND_IP, "5.6.7.8", "connection check: VPN", "connection check",
                               linked="Griefer"))
    assert rig.bans.unban("Griefer") == 1 and banned(rig) == []


def test_the_pc_button_still_takes_off_the_one_row_picked(rig):
    rig.bans.ban_now("Griefer", kind="both")
    rows = {rig.bans.ban_table.item(r, 0).text(): r for r in range(rig.bans.ban_table.rowCount())}
    rig.bans.ban_table.selectRow(rows["name"])
    rig.bans._remove_selected()
    assert banned(rig) == [("ip", "5.6.7.8")]


# ---- typed by a mod, typed on the phone --------------------------------------------

def test_unban_is_a_command_in_the_game_and_on_the_phone(rig):
    rig.bans.ban_now("Griefer", kind="both")
    rig.game("Dale", "!unban Griefer")
    assert banned(rig) == []
    assert rig.server.chat == ["Griefer unbanned."]
    assert rig.last_log().endswith("dale unbanned Griefer")

    rig.bans.ban_now("Griefer", kind="both")
    assert rig.phone("!unban griefer") == {"ok": True, "command": "!unban"}
    assert banned(rig) == []
    assert rig.server.chat == ["griefer unbanned."]
    assert rig.last_log().endswith("web_admin unbanned griefer")


def test_a_ban_typed_on_the_phone_is_lifted_by_unban_typed_on_the_phone(rig):
    assert rig.phone("!ban Griefer wall hack")["ok"] is True
    assert banned(rig) == [("ip", "5.6.7.8"), ("name", "Griefer")]
    assert rig.phone("!unban Griefer")["ok"] is True
    assert banned(rig) == [] and can_join(rig, "Griefer", "5.6.7.8")


def test_unban_by_address(rig):
    rig.bans.ban_now("Griefer", kind="both")
    assert rig.phone("!unban 5.6.7.8")["ok"] is True
    assert banned(rig) == [("name", "Griefer")]


def test_unban_follows_the_ban_rank(rig):
    rig.bans.ban_now("Griefer", kind="both")
    rig.game("Ham", "!unban Griefer")                    # a Moderator: no ban, no unban
    assert rig.server.chat == ["!unban is not allowed for Moderator."]
    rig.game("Griefer", "!unban Griefer")                # not a mod: nothing at all
    assert rig.server.chat == []
    assert banned(rig) == [("ip", "5.6.7.8"), ("name", "Griefer")]


def test_what_a_mod_is_told_when_it_cannot_be_done(rig):
    rig.game("Dale", "!unban")
    assert rig.server.chat == ["Usage: !unban <name or IP>"]
    rig.game("Dale", "!unban Nobody")
    assert rig.server.chat == ["Nobody is not on the ban list."]
    assert rig.last_log().endswith("dale tried to unban Nobody - not on the list")


def test_a_long_name_never_makes_a_second_chat_line(rig):
    typed = "z " * 22 + "Dale: !ban Ham"
    rig.game("Dale", "!unban " + typed)
    assert rig.server.chat == ["That is not on the ban list."]
    rig.bans.bans.add(BanEntry(KIND_NAME, " ".join(typed.split())))
    rig.game("Dale", "!unban " + typed)
    assert rig.server.chat == ["Unbanned."]
    for line in rig.server.chat:
        assert len(_split_chat_message(line)) == 1
