"""Joint Ops chat colour codes, no Qt.

The game's chat box (and WAC ``ptext``) draws ``<cRRGGBB>`` as "colour from
here on" and ``<co>`` as "back to the normal chat colour" - proven in game
2026-09-26, including several colour changes on one line.  Public chat only
keeps them on a server with the long chat patch; a stock server strips them.

Used for the previews and for checking lines people paste in (Dale's
moderator lines came from ChatGPT, 45 at a time).
"""

from __future__ import annotations

import html
import re
from typing import Optional

TAG_RE = re.compile(r"<(c[0-9a-fA-F]{6}|co)>", re.IGNORECASE)
NORMAL_COLOUR = "e8e8e8"     # what the preview shows for the game's own colour


def segments(text: str) -> list:
    """[(colour or None, text), ...] - None = the normal chat colour."""
    out = []
    colour = None
    pos = 0
    for match in TAG_RE.finditer(text):
        if match.start() > pos:
            out.append((colour, text[pos:match.start()]))
        tag = match.group(1).lower()
        colour = None if tag == "co" else tag[1:]
        pos = match.end()
    if pos < len(text):
        out.append((colour, text[pos:]))
    return out


def visible(text: str) -> str:
    """The words a player reads - colour codes taken out."""
    return TAG_RE.sub("", text)


def tag_problem(text: str) -> Optional[str]:
    """A < or > that is not part of a colour code, in plain words, or None."""
    rest = TAG_RE.sub("", text)
    if "<" not in rest and ">" not in rest:
        return None
    bad = re.search(r"<[^<>]{0,12}>?", rest)
    shown = bad.group(0) if bad else ">"
    return (f"'{shown}' is not a colour code. Colours look like <cFF8000> "
            "(six hex digits) and <co> goes back to the normal colour.")


def safe_name(name: str) -> str:
    """A player name can hold < or >; inside a coloured line the game would
    read them as the start of a colour code."""
    return name.replace("<", "(").replace(">", ")")


def to_html(text: str, normal: str = NORMAL_COLOUR) -> str:
    """Rich text for a QLabel that looks like the in-game chat line."""
    parts = []
    for colour, chunk in segments(text):
        parts.append(f'<span style="color:#{colour or normal}">{html.escape(chunk)}</span>')
    return "".join(parts)
