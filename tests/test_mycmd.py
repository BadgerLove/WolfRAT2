"""!mycmd: the commands a player may use, sent to that player only (Dale, 2026-11-19)."""
import time

from tests.test_mod_ranks_tab import Rig
from wolfrat import mycmd
from wolfrat.mod_ranks import ADMIN, ALL_PERMISSIONS, MODERATOR, Rank


# ---- the answer itself -------------------------------------------------------

def test_admin_gets_every_command_and_every_line_fits_both_limits():
    admin = Rank(ADMIN, frozenset(ALL_PERMISSIONS))
    for limit in (59, 118):
        lines = mycmd.reply_lines(admin, [], limit)
        assert lines[0].startswith("Your commands (Admin): !startvote")
        assert all(len(line) <= limit for line in lines)
        words = " ".join(lines).split()
        for command in ("!setnext", "!ban", "!unban", "!storm", "!clear", "!remove"):
            assert command in words
    # Long chat needs fewer lines than stock chat.
    assert len(mycmd.reply_lines(admin, [], 118)) < len(mycmd.reply_lines(admin, [], 59))
    assert len(mycmd.reply_lines(admin, [], 59)) <= mycmd.MAX_LINES


def test_moderator_gets_only_their_rank_and_not_admin_commands():
    mod = Rank(MODERATOR, frozenset({"next", "kick", "warn"}))
    lines = mycmd.reply_lines(mod, ["!stats"], 59)
    assert lines == ["Your commands (Moderator): !next !warn !kick"]


def test_made_up_rank_gets_its_own_commands():
    guest = Rank("Guest", frozenset({"startvote", "lightning"}))
    assert mycmd.reply_lines(guest, [], 118) == ["Your commands (Guest): !startvote !lightning"]


def test_rank_with_nothing_ticked_says_so():
    empty = Rank("Trial", frozenset())
    assert mycmd.reply_lines(empty, ["!stats"], 59) == ["Your rank (Trial) has no commands ticked."]


def test_no_rank_gets_the_player_commands_this_server_has_on():
    on = mycmd.player_commands(vote=True, skip=True, switch="!switch")
    assert mycmd.reply_lines(None, on, 118) == [
        "Your commands: !stats !rank !ping !list !vote !yes !skip !switch !mycomp !mycmd"]
    off = mycmd.player_commands(vote=False, skip=False, switch=None)
    assert off == ["!stats", "!rank", "!ping", "!list", "!mycomp", "!mycmd"]
    assert "!team" in mycmd.player_commands(switch="!team")


def test_words_are_never_split_and_lines_never_pass_the_helper():
    words = ["!" + "x" * 30] * 10
    for limit in (59, 118, 500):
        lines = mycmd.wrap("Your commands:", words, limit)
        assert all(len(line) <= min(limit, mycmd.HELPER_TEXT) for line in lines)
        assert " ".join(lines).split()[2:] == words[:len(" ".join(lines).split()) - 2]


# ---- the chat command --------------------------------------------------------

class PrivateRig(Rig):
    def build(self):
        tab = super().build()
        self.private = []
        tab.private_line = lambda name, text: self.private.append((name, text)) or True
        return tab


def rig_with(tmp_path, ranks=None):
    cfg = {"mods": ["Dale", "Ham", "Visitor"],
           "mod_ranks": {"Dale": ADMIN, "Visitor": "Guest"},
           "ranks": ranks or [{"name": "Guest", "permissions": ["startvote"]}]}
    return PrivateRig(tmp_path, cfg)


def test_the_answer_goes_to_the_one_player_only_and_nothing_public(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Ham: !mycmd")
    assert rig.said == [] and rig.actions == []
    assert rig.private and all(name == "ham" for name, _ in rig.private)
    assert rig.private[0][1].startswith("Your commands (Moderator): ")
    assert "!ban" not in " ".join(text for _, text in rig.private)


def test_admin_guest_and_player_each_get_their_own_list(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Dale: !mycmd")
    assert "!ban" in " ".join(text for _, text in rig.private)
    rig.private.clear()
    rig.tab._check_mod_command("Visitor: !mycmd")
    assert rig.private == [("visitor", "Your commands (Guest): !startvote")]
    rig.private.clear()
    rig.tab._check_mod_command("Griefer: !MYCMD")
    assert rig.private[0][0] == "griefer"
    assert rig.private[0][1].startswith("Your commands: !stats")
    assert rig.said == []


def test_a_rank_edit_shows_straight_away(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab.roster.rank_of("Visitor").permissions = frozenset({"startvote", "kick"})
    rig.tab._check_mod_command("Visitor: !mycmd")
    assert rig.private == [("visitor", "Your commands (Guest): !startvote !kick")]


def test_no_helper_means_nothing_is_said(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab.private_line = lambda name, text: False
    rig.tab._check_mod_command("Dale: !mycmd")
    rig.tab.private_line = None
    rig.tab._mycmd_last.clear()
    rig.tab._check_mod_command("Ham: !mycmd")
    assert rig.said == [] and rig.actions == []


def test_cooldown_per_player(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Ham: !mycmd")
    first = len(rig.private)
    rig.tab._check_mod_command("Ham: !mycmd")
    assert len(rig.private) == first
    rig.tab._check_mod_command("Dale: !mycmd")
    assert len(rig.private) > first
    rig.tab._mycmd_last["ham"] = time.time() - mycmd.COOLDOWN - 1
    rig.tab._check_mod_command("Ham: !mycmd")
    assert len(rig.private) > first + 1


def test_web_admin_gets_nothing(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("[ADMIN] !mycmd")
    assert rig.private == [] and rig.said == []


def test_mycmd_is_listed_for_everyone_in_the_command_table(tmp_path):
    rig = rig_with(tmp_path)
    table = rig.tab.cmd_table
    rows = [table.item(r, 0).text() for r in range(table.rowCount()) if table.item(r, 0)]
    assert "!mycmd" in rows
