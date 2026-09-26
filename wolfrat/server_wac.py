"""The private welcome: a line of chat only the joining player sees.

The game has no whisper command, but its mission-script language does the job:
`PLOOP ... END` runs a block once per connected player, and `ptext("...")`
inside it sends a chat line to that one player. `onptick(n)` is true during
second n of the player's own time alive on the map. So the welcome is a few
lines in the server's `server.wac` - the same file the lightning add-on lives
in - and WolfRAT's job is only to write those lines safely.

Rules learned the hard way (see the vault, Weather Control 2e-bis):

* The script compiler is from 2004. The file MUST use Windows line endings;
  with bare LF the whole file reads as one comment and does nothing, silently.
* A compile error anywhere in the file takes the lightning add-on down with
  it, so the text is validated before it gets near the file.
* Scripts are compiled when a map loads: a change shows at the next map.
* Use `then`, never `enter`: `enter` remembers its last result per block, not
  per player, so the second player in the same pass would be skipped.

Our section sits between two marker comments and is the only part of the file
this module ever changes. Everything else - the lightning block, an admin's
own script - is kept byte for byte (apart from normalising line endings).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from wolfrat import chat_markup

FILENAME = "server.wac"
BEGIN = "// >>> WolfRAT private welcome (written by WolfRAT - change it in WolfRAT, not here)"
END = "// <<< WolfRAT private welcome"

# The game's script compiler reads a quoted string into a 72-byte buffer and
# stops at 64 characters counting the opening quote, so a ptext line holds 63
# (colour codes included).  Past that the rest of the line is read as script
# and the whole server.wac fails to compile - lightning and whispers with it.
WAC_STRING_MAX = 63
MAX_TEXT = WAC_STRING_MAX
MIN_SECONDS, MAX_SECONDS = 3, 300
DEFAULT_SECONDS = 20
DEFAULT_COLOUR = "00ff00"
DEFAULT_COLOUR2 = "ffff00"
MIN_GAP, MAX_GAP, DEFAULT_GAP = 1, 60, 3

_COLOUR_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_SECTION_RE = re.compile(
    re.escape(BEGIN) + r".*?" + re.escape(END) + r"[^\n]*\n?", re.DOTALL)
_LINE_RE = re.compile(
    r'onptick\((\d+)\)[^\n]*\nptext\("(?:<c([0-9a-fA-F]{6})>)?([^\n]*)"\)')


class ServerWacError(Exception):
    """Something to tell the admin in plain words."""


@dataclass(frozen=True)
class Welcome:
    text: str
    seconds: int = DEFAULT_SECONDS
    colour: str = DEFAULT_COLOUR     # "" = the game's normal system colour
    # Optional second line a few seconds later - the place to tell players
    # which chat commands they have (!kd, !switch...). "" = no second line.
    text2: str = ""
    colour2: str = DEFAULT_COLOUR2
    gap: int = DEFAULT_GAP           # seconds between the two lines


def text_problem(text: str) -> Optional[str]:
    """Why this text cannot go in the script, or None if it is fine."""
    if not text.strip():
        return "Type the welcome message first."
    if len(text) > MAX_TEXT:
        return (f"Too long: {len(text)} of {MAX_TEXT} characters, colour codes count "
                "(the game's script cannot hold more).")
    if '"' in text:
        return 'The message cannot contain a double quote ("). Use \' instead.'
    bad_code = chat_markup.tag_problem(text)
    if bad_code:
        return bad_code
    bad = sorted({ch for ch in text if not (32 <= ord(ch) < 127)})
    if bad:
        return ("Plain English letters, numbers and punctuation only - the game's script "
                f"compiler cannot read: {' '.join(bad)}")
    return None


def compose(text: str, colour: str = "") -> str:
    """The ptext string exactly as it goes in the script.  Colour codes typed
    in the text (<cRRGGBB>, <co>) go in as typed - Dale 2026-09-26: admins
    type the codes, a Colours... box lists them."""
    return _code(colour) + text.strip()


def line_problem(text: str, colour: str = "") -> Optional[str]:
    """Why this line cannot go in the script, or None if it is fine."""
    problem = text_problem(text)
    if problem:
        return problem
    if colour and not _COLOUR_RE.match(colour):
        return "The colour must be six hex digits, like 00ff00."
    length = len(compose(text, colour))
    if length > WAC_STRING_MAX:
        return (f"Too long: {length} of {WAC_STRING_MAX} characters, colour codes count "
                "(the game's script cannot hold more).")
    return None


def build_section(welcome: Welcome) -> str:
    problem = line_problem(welcome.text, welcome.colour)
    if problem:
        raise ServerWacError(problem)
    second = welcome.text2.strip()
    if second:
        problem = line_problem(second, welcome.colour2)
        if problem:
            raise ServerWacError(f"Second line: {problem}")
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, int(welcome.seconds)))
    lines = [
        BEGIN,
        "// Only the player who just joined sees these lines.",
        "PLOOP",
        f"if onptick({seconds}) then",
        f'ptext("{compose(welcome.text, welcome.colour)}")',
        "endif",
    ]
    if second:
        gap = max(MIN_GAP, min(MAX_GAP, int(welcome.gap)))
        lines += [
            f"if onptick({seconds + gap}) then",
            f'ptext("{compose(second, welcome.colour2)}")',
            "endif",
        ]
    return "\n".join(lines + ["END", END]) + "\n"


def _code(colour: str) -> str:
    return f"<c{colour.lower()}>" if colour else ""


def _path(server_dir) -> str:
    return os.path.join(str(server_dir), FILENAME)


def _read(server_dir) -> str:
    try:
        with open(_path(server_dir), "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ServerWacError(f"WolfRAT could not read server.wac ({exc}).") from exc
    return raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")


def _write(server_dir, text: str) -> None:
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("latin-1")
    path = _path(server_dir)
    temp = path + ".wolfrat-new"
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
        with open(temp, "rb") as handle:
            if handle.read() != data:
                raise OSError("read-back mismatch")
        os.replace(temp, path)
    except OSError as exc:
        raise ServerWacError(
            "WolfRAT could not write server.wac into the server folder "
            f"({exc}). Run WolfRAT as administrator."
        ) from exc


def read_welcome(server_dir) -> Optional[Welcome]:
    """The welcome currently in the file, or None."""
    match = _SECTION_RE.search(_read(server_dir))
    if not match:
        return None
    found = _LINE_RE.findall(match.group(0))
    if not found:
        return None
    seconds, colour, text = found[0]
    if len(found) == 1:
        return Welcome(text, int(seconds), colour.lower())
    seconds2, colour2, text2 = found[1]
    return Welcome(text, int(seconds), colour.lower(), text2, colour2.lower(),
                   max(MIN_GAP, int(seconds2) - int(seconds)))


def write_welcome(server_dir, welcome: Welcome) -> str:
    """Put (or replace) the welcome. Returns "unchanged", "updated" or "added"."""
    section = build_section(welcome)
    body = _read(server_dir)
    if _SECTION_RE.search(body):
        new = _SECTION_RE.sub(lambda _m: section, body, count=1)
        result = "updated"
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        new = body + ("\n" if body else "") + section
        result = "added"
    if new == body:
        return "unchanged"
    _write(server_dir, new)
    return result


def remove_welcome(server_dir) -> bool:
    """Take our section out. True if there was one."""
    body = _read(server_dir)
    if not _SECTION_RE.search(body):
        return False
    new = _SECTION_RE.sub("", body, count=1)
    new = re.sub(r"\n{3,}", "\n\n", new).rstrip("\n")
    _write(server_dir, new + "\n" if new else "")
    return True
