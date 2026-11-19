"""!mycmd: the commands a player may use, sent to that player only, in colour (Dale, 2026-11-19)."""
import re
import time

from tests.test_mod_ranks_tab import Rig
from wolfrat import mycmd
from wolfrat.mod_ranks import ADMIN, ALL_PERMISSIONS, MODERATOR, Rank

CODE = re.compile(r"<c[0-9a-f]{6}>")


def read(lines):
    """What the player reads, all lines run together."""
    return " ".join(mycmd.plain(line) for line in lines)


# ---- the answer itself -------------------------------------------------------

def test_admin_gets_every_command_and_every_line_fits_with_the_codes_counted():
    admin = Rank(ADMIN, frozenset(ALL_PERMISSIONS))
    lines = mycmd.reply_lines(admin, [])
    assert mycmd.plain(lines[0]).startswith("Your commands (Admin): !startvote")
    assert all(len(line) <= mycmd.LINE_LIMIT for line in lines)     # codes included
    words = read(lines).split()
    for command in ("!setnext", "!ban", "!unban", "!storm", "!clear", "!sky", "!remove"):
        assert command in words
    assert len(lines) <= mycmd.MAX_LINES == 3                    # Dale: never more than 3


def test_every_line_starts_with_a_colour_and_codes_only_where_it_changes():
    admin = Rank(ADMIN, frozenset(ALL_PERMISSIONS))
    lines = mycmd.reply_lines(admin, [])
    assert lines[0].startswith("<cffff00>Your commands (Admin): <c40c0ff>!startvote")
    for line in lines:
        assert CODE.match(line)                       # a new chat line starts white
        codes = CODE.findall(line)
        assert all(a != b for a, b in zip(codes, codes[1:]))   # no wasted repeat
    joined = " ".join(lines)
    assert "<cff3030>!warn" in joined and "<c00ffff>!storm" in joined and "<cff80c0>!add" in joined


def test_a_tight_limit_still_counts_the_codes_and_stops_at_three_lines():
    admin = Rank(ADMIN, frozenset(ALL_PERMISSIONS))
    lines = mycmd.reply_lines(admin, [], 59)
    assert len(lines) == 3 and all(len(line) <= 59 for line in lines)
    full = read(mycmd.reply_lines(admin, [])).split()
    assert read(lines).split() == full[:len(read(lines).split())]   # words whole, in order


def test_moderator_gets_only_their_rank_and_not_admin_commands():
    mod = Rank(MODERATOR, frozenset({"next", "kick", "warn"}))
    lines = mycmd.reply_lines(mod, ["!stats"])
    assert lines == ["<cffff00>Your commands (Moderator): <c40c0ff>!next <cff3030>!warn !kick"]


def test_made_up_rank_gets_its_own_commands():
    guest = Rank("Guest", frozenset({"startvote", "lightning"}))
    assert read(mycmd.reply_lines(guest, [])) == "Your commands (Guest): !startvote !lightning"


def test_rank_with_nothing_ticked_says_so():
    empty = Rank("Trial", frozenset())
    assert mycmd.reply_lines(empty, ["!stats"]) == ["<cffff00>Your rank (Trial) has no commands ticked."]


def test_no_rank_gets_the_player_commands_this_server_has_on():
    on = mycmd.player_commands(vote=True, skip=True, switch="!switch")
    lines = mycmd.reply_lines(None, on)
    assert read(lines) == "Your commands: !stats !rank !ping !list !vote !yes !skip !switch !mycomp !mycmd"
    assert len(lines) == 1 and "<c00ff00>!vote" in lines[0] and "<c40c0ff>!mycomp" in lines[0]
    off = mycmd.player_commands(vote=False, skip=False, switch=None)
    assert off == ["!stats", "!rank", "!ping", "!list", "!mycomp", "!mycmd"]
    assert "!team" in mycmd.player_commands(switch="!team")


def test_long_words_wrap_whole_and_lines_never_pass_the_limit():
    words = [("40c0ff", "!" + "x" * 40)] * 12
    lines = mycmd.wrap(("ffff00", "Your commands:"), words)
    assert all(len(line) <= mycmd.LINE_LIMIT for line in lines)
    assert len(lines) <= mycmd.MAX_LINES
    assert all(w == "!" + "x" * 40 for w in read(lines).split()[2:])


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


def said_privately(rig):
    return read(text for _, text in rig.private)


def test_the_answer_goes_to_the_one_player_only_and_nothing_public(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Ham: !mycmd")
    assert rig.said == [] and rig.actions == []
    assert rig.private and all(name == "ham" for name, _ in rig.private)
    assert said_privately(rig).startswith("Your commands (Moderator): ")
    assert "!ban" not in said_privately(rig)


def test_admin_guest_and_player_each_get_their_own_list(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Dale: !mycmd")
    assert "!ban" in said_privately(rig)
    rig.private.clear()
    rig.tab._check_mod_command("Visitor: !mycmd")
    assert [name for name, _ in rig.private] == ["visitor"]
    assert said_privately(rig) == "Your commands (Guest): !startvote"
    rig.private.clear()
    rig.tab._check_mod_command("Griefer: !MYCMD")
    assert rig.private[0][0] == "griefer"
    assert said_privately(rig).startswith("Your commands: !stats")
    assert rig.said == []


def test_the_long_line_is_used_on_a_stock_chat_server_too(tmp_path):
    # Helper lines skip the admin path's 59 cut, so the stock chat setting does not shorten them.
    rig = rig_with(tmp_path)
    rig.tab._check_mod_command("Dale: !mycmd")
    assert len(rig.private) <= 3
    assert max(len(text) for _, text in rig.private) > 59


def test_a_rank_edit_shows_straight_away(tmp_path):
    rig = rig_with(tmp_path)
    rig.tab.roster.rank_of("Visitor").permissions = frozenset({"startvote", "kick"})
    rig.tab._check_mod_command("Visitor: !mycmd")
    assert said_privately(rig) == "Your commands (Guest): !startvote !kick"


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
