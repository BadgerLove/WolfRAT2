"""Moderator ranks: permissions, migration from a flat mods list, rank edits."""
import json

import pytest

from wolfrat import weather
from wolfrat.mod_ranks import (
    ADMIN, ALL_PERMISSIONS, DEFAULT_MODERATOR, MODERATOR, ModRoster,
    RankError, permission_for,
)

GUARDED = {
    '!warn', '!kick', '!ban', '!swap', '!kill', '!next', '!map', '!add',
    '!remove', '!startvote', '!mixteams', '!balanceteams', '!time',
    '!gametime', *weather.CHAT_COMMANDS,
}


def test_every_mod_command_has_a_permission_and_nothing_else_does():
    assert all(permission_for(cmd) for cmd in GUARDED)
    for everyone in ('!kd', '!ping', '!list', '!vote', '!skip', '!yes', '!1', '!switch'):
        assert permission_for(everyone) is None


def test_old_flat_mods_list_becomes_moderators():
    roster = ModRoster.from_config({"mods": ["BadgerLove", "Hamshop"], "vote_enabled": True})
    assert roster.members() == [("BadgerLove", MODERATOR), ("Hamshop", MODERATOR)]
    assert roster.names() == {"badgerlove": "BadgerLove", "hamshop": "Hamshop"}
    assert roster.allows("badgerlove", "!kick")
    assert not roster.allows("badgerlove", "!ban")
    assert not roster.allows("badgerlove", "!map")
    assert not roster.allows("stranger", "!warn")


def test_admin_has_everything_and_cannot_be_trimmed():
    roster = ModRoster.from_config({"mods": ["Dale"]})
    roster.set_member_rank("dale", ADMIN)
    assert all(roster.allows("DALE", cmd) for cmd in GUARDED)
    with pytest.raises(RankError):
        roster.set_permission(ADMIN, "ban", False)
    # a hand-edited file cannot trim Admin either
    sneaky = ModRoster.from_config({
        "mods": ["Dale"], "mod_ranks": {"Dale": "Admin"},
        "ranks": [{"name": "admin", "permissions": []}],
    })
    assert sneaky.allows("dale", "!ban")


def test_custom_rank_with_some_admin_commands_round_trips():
    roster = ModRoster.from_config({"mods": ["Dale", "Ham"]})
    rank = roster.add_rank("Super Moderator")
    assert rank.permissions == DEFAULT_MODERATOR  # starts as a copy of Moderator
    roster.set_permission("Super Moderator", "ban", True)
    roster.set_permission("super moderator", "map", True)
    roster.set_member_rank("Ham", "Super Moderator")

    saved = json.loads(json.dumps(roster.to_config()))
    assert saved["mods"] == ["Dale", "Ham"]  # older WolfRAT still reads this
    again = ModRoster.from_config(saved)
    assert again.rank_of("ham").name == "Super Moderator"
    assert again.allows("ham", "!ban") and again.allows("ham", "!map")
    assert not again.allows("ham", "!gametime")
    assert not again.allows("dale", "!ban")
    assert again.ranks_allowing("!ban") == [ADMIN, "Super Moderator"]


def test_moderator_is_editable():
    roster = ModRoster.from_config({"mods": ["Ham"]})
    roster.set_permission(MODERATOR, "kick", False)
    roster.set_permission(MODERATOR, "ban", True)
    again = ModRoster.from_config(roster.to_config())
    assert not again.allows("ham", "!kick") and again.allows("ham", "!ban")


def test_weather_permissions_are_split():
    roster = ModRoster.from_config({"mods": ["Ham"]})
    roster.add_rank("Weatherman", copy_from="nothing")
    roster.set_permission("Weatherman", "weather", True)
    roster.set_member_rank("Ham", "Weatherman")
    assert roster.allows("ham", "!storm") and roster.allows("ham", "!clear")
    assert not roster.allows("ham", "!quake")
    assert not roster.allows("ham", "!weather")
    assert not roster.allows("ham", "!warn")


def test_rank_name_rules():
    roster = ModRoster()
    roster.add_rank("Trial Mod")
    for bad in ("", "   ", "admin", "MODERATOR", "trial  mod", "x" * 25):
        with pytest.raises(RankError):
            roster.add_rank(bad)


def test_rename_carries_members_and_builtins_stay():
    roster = ModRoster.from_config({"mods": ["Ham"]})
    roster.add_rank("Trial")
    roster.set_member_rank("Ham", "Trial")
    roster.rename_rank("trial", "Trial Mod")
    assert roster.rank_of("ham").name == "Trial Mod"
    roster.rename_rank("Trial Mod", "TRIAL MOD")  # case-only rename is fine
    assert roster.rank_of("ham").name == "TRIAL MOD"
    for builtin in (ADMIN, MODERATOR):
        with pytest.raises(RankError):
            roster.rename_rank(builtin, "Boss")
        with pytest.raises(RankError):
            roster.delete_rank(builtin)


def test_delete_refuses_while_people_hold_the_rank():
    roster = ModRoster.from_config({"mods": ["Ham"]})
    roster.add_rank("Trial")
    roster.set_member_rank("Ham", "Trial")
    with pytest.raises(RankError, match="1 person is still"):
        roster.delete_rank("Trial")
    roster.set_member_rank("Ham", MODERATOR)
    roster.delete_rank("Trial")
    assert roster.rank_names() == [ADMIN, MODERATOR]


def test_unknown_rank_or_permission_in_file_is_harmless():
    roster = ModRoster.from_config({
        "mods": ["Ham", "Ghost"],
        "mod_ranks": {"Ham": "Deleted Rank", "Nobody": "Admin"},
        "ranks": [{"name": "Odd", "permissions": ["ban", "launch_nukes"]}, "junk"],
    })
    assert roster.rank_of("ham").name == MODERATOR
    assert roster.rank_of("nobody") is None  # not on the mods list = not a mod
    assert roster.ranks()[-1].permissions == frozenset({"ban"})
    assert set(ALL_PERMISSIONS) >= DEFAULT_MODERATOR
