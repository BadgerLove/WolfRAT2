"""!mycmd: a player asks which chat commands they may use (Dale, 2026-11-19).

The answer goes to that player only, through the rank-sound helper
(rank_tone.py: one line to one slot, no sound), so nobody else sees who holds
which rank and nothing is spammed to the server.  It is built when it is asked
for, so a new rank or a newly ticked command shows at once: no map change.

A moderator gets their rank's commands; anyone without a rank gets the player
commands this server has switched on.  A rank with nothing ticked says so.

In colour (Dale: "all white is a bit boring"): the heading in yellow and each
kind of command in its own colour.  Colour codes count towards the characters
(Dale, settled: 9 for a colour), so every code is counted when lines are
wrapped, and a wrapped line starts with its colour again (a new chat line
starts white).  The helper's lines skip the admin chat path that cuts at 59
and strips colours (vault Chat System.md), so the limit is the client's line,
not the server's chat setting.  No Qt here.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from wolfrat import mod_ranks

COOLDOWN = 10        # seconds between one player's answers
MAX_LINES = 8        # the helper's queue holds 16; never fill it with one answer
LINE_LIMIT = 118     # what one helper line carries to the player, colour codes included

# Proven colour codes (colour_codes.py).
HEADING = "ffff00"                      # yellow
GROUP_COLOURS = {                       # Mods tab section -> colour
    "Match control": "40c0ff",          # light blue
    "Weather": "00ffff",                # cyan
    "Sky": "8080ff",                    # purple
    "Teams": "00ff00",                  # green
    "Players": "ff3030",                # red
    "Rotation": "ff80c0",               # pink
}
PLAYER_INFO = "40c0ff"                  # !stats !rank !ping !list !mycomp !mycmd
PLAYER_VOTES = "00ff00"                 # !vote !yes !skip, the team switch

# What anyone may type, in the order the answer lists them.  The vote / skip /
# team switch commands are added by the caller only when this server has them on.
PLAYER_COMMANDS = ("!stats", "!rank", "!ping", "!list", "!mycomp", "!mycmd")

_CODE = re.compile(r"<c[0-9a-fA-F]{6}>|<co>")


def plain(line: str) -> str:
    """The line as the player reads it (colour codes taken out)."""
    return _CODE.sub("", line)


def rank_commands(rank: mod_ranks.Rank) -> list:
    """Every chat command the rank may use, in the Mods tab's order."""
    return [word for _colour, word in _rank_words(rank)]


def _rank_words(rank: mod_ranks.Rank) -> list:
    out: list = []
    seen: set = set()
    for section, perms in mod_ranks.PERMISSION_GROUPS:
        colour = GROUP_COLOURS.get(section, PLAYER_INFO)
        for key, _label, commands in perms:
            if key in rank.permissions:
                for command in commands:
                    if command not in seen:
                        seen.add(command)
                        out.append((colour, command))
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


def _player_colour(word: str) -> str:
    return PLAYER_INFO if word in PLAYER_COMMANDS else PLAYER_VOTES


def wrap(head: tuple, words: Iterable[tuple], limit: int = LINE_LIMIT) -> list:
    """``head`` then the words, each ``(colour, text)``, as few lines as fit
    ``limit`` with the colour codes counted.  Words are never split; a colour
    code goes in only where the colour changes, and at the start of each line."""
    limit = max(30, min(int(limit), LINE_LIMIT))
    lines: list = []
    line, current = "", None
    for colour, word in [head, *words]:
        piece = word if colour == current else f"<c{colour}>{word}"
        candidate = f"{line} {piece}" if line else piece
        if line and len(candidate) > limit:
            lines.append(line)
            line, current = f"<c{colour}>{word}", colour
        else:
            line, current = candidate, colour
    if line:
        lines.append(line)
    return lines[:MAX_LINES]


def reply_lines(rank: Optional[mod_ranks.Rank], players: Iterable[str],
                limit: int = LINE_LIMIT) -> list:
    """The private answer to !mycmd, in colour."""
    if rank is None:
        return wrap((HEADING, "Your commands:"),
                    [(_player_colour(w), w) for w in players], limit)
    words = _rank_words(rank)
    if not words:
        return wrap((HEADING, f"Your rank ({rank.name}) has no commands ticked."), (), limit)
    return wrap((HEADING, f"Your commands ({rank.name}):"), words, limit)
