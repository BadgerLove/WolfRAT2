"""!mycmd: a player asks which chat commands they may use (Dale, 2026-11-19).

The answer goes to that player only, through the rank-sound helper
(rank_tone.py: one line to one slot, no sound), so nobody else sees who holds
which rank and nothing is spammed to the server.  It is built when it is asked
for, so a new rank or a newly ticked command shows at once: no map change.

A moderator gets their rank's commands; anyone without a rank gets the player
commands this server has switched on.  A rank with nothing ticked says so.
Lines are wrapped to the server's chat limit (59 stock, 118 long chat) and to
what the helper carries.  No Qt here.
"""

from __future__ import annotations

from typing import Iterable, Optional

from wolfrat import mod_ranks

COOLDOWN = 10        # seconds between one player's answers
MAX_LINES = 8        # the helper's queue holds 16; never fill it with one answer
HELPER_TEXT = 127    # rank_tone.TEXT - 1: the most one helper line carries

# What anyone may type, in the order the answer lists them.  The vote / skip /
# team switch commands are added by the caller only when this server has them on.
PLAYER_COMMANDS = ("!stats", "!rank", "!ping", "!list", "!mycomp", "!mycmd")


def rank_commands(rank: mod_ranks.Rank) -> list:
    """Every chat command the rank may use, in the Mods tab's order."""
    out: list = []
    for _section, perms in mod_ranks.PERMISSION_GROUPS:
        for key, _label, commands in perms:
            if key in rank.permissions:
                out.extend(c for c in commands if c not in out)
    return out


def player_commands(vote: bool = True, skip: bool = True,
                    switch: Optional[str] = "!switch") -> list:
    """The player commands this server answers, given what it has switched on."""
    out = list(PLAYER_COMMANDS[:4])
    if vote:
        out += ["!vote", "!yes"]
    if skip:
        out.append("!skip")
    if switch:
        out.append(switch.strip())
    out += list(PLAYER_COMMANDS[4:])
    return out


def wrap(head: str, words: Iterable[str], limit: int) -> list:
    """``head`` then the words, as few lines as fit ``limit`` (words never split)."""
    limit = max(20, min(int(limit), HELPER_TEXT))
    lines, line = [], head
    for word in words:
        candidate = f"{line} {word}" if line else word
        if len(candidate) <= limit:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines[:MAX_LINES]


def reply_lines(rank: Optional[mod_ranks.Rank], players: Iterable[str], limit: int) -> list:
    """The private answer to !mycmd."""
    if rank is None:
        return wrap("Your commands:", players, limit)
    commands = rank_commands(rank)
    if not commands:
        return wrap(f"Your rank ({rank.name}) has no commands ticked.", (), limit)
    return wrap(f"Your commands ({rank.name}):", commands, limit)
