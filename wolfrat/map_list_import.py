"""Import map lists saved by the original WolfRAT (v0.95, 2005).

Most admins still keep their rotations in the old WolfRAT (Dale,
2026-11-21). Its list files hold one map per line,

    <title>[FILENAME]<file>[ROUNDS]<n>

after a template first line, FILE_TEXT[FILENAME]FILE_NAME.EXT[ROUNDS]0(1)
(SgtMudd's 1-coop.txt: 221 maps, CRLF, plain ASCII). The title is only a
label, the file name is what the server needs. ROUNDS is the old Mission
Cycle's "r" column, which WolfRAT 2 copied: 1 = played once (x1),
2 = played twice, swapping sides (x2). A plain text file with one map file
name per line is read as well.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field

_OLD_LINE = re.compile(
    r"^(?P<title>.*?)\[FILENAME\](?P<file>.*?)(?:\[ROUNDS\](?P<rounds>.*))?$",
    re.IGNORECASE,
)
_MAP_FILE = re.compile(r"\.(?:bms|npj|npz)$", re.IGNORECASE)


@dataclass
class ImportedList:
    """One imported file: the maps in order and the lines left out."""

    name: str
    maps: list = field(default_factory=list)      # [{"file", "count"}]
    left_out: list = field(default_factory=list)  # [(line number, text, why)]

    @property
    def twice(self) -> int:
        """How many maps are listed more than once (kept, the server allows it)."""
        counts = Counter(entry["file"].casefold() for entry in self.maps)
        return sum(1 for n in counts.values() if n > 1)

    @property
    def x2(self) -> int:
        return sum(1 for entry in self.maps if entry["count"] == 2)


def read_text(path) -> str:
    """The file's text: UTF-8 (with or without a BOM), else Windows-1252."""
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def list_name(path) -> str:
    """The saved-list name for a file: its name without the extension."""
    name = os.path.splitext(os.path.basename(path))[0].strip()
    return name or "imported"


def _rounds_to_count(rounds: str) -> int:
    value = rounds.split("(", 1)[0].strip()
    return 2 if value == "2" else 1


def parse_map_list(text: str, name: str = "imported") -> ImportedList:
    """Read an old WolfRAT list (or one file name per line) into maps."""
    result = ImportedList(name)
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _OLD_LINE.match(line)
        if match:
            title = match.group("title").strip()
            filename = match.group("file").strip().strip('"').strip()
            rounds = (match.group("rounds") or "").strip()
            if filename.upper() == "FILE_NAME.EXT" or title.upper() == "FILE_TEXT":
                continue  # the old WolfRAT's template line
        elif _MAP_FILE.search(line.strip('"').strip()):
            filename, rounds = line.strip('"').strip(), ""
        else:
            result.left_out.append((number, line, "not a map line"))
            continue
        why = _refusal(filename)
        if why:
            result.left_out.append((number, line, why))
            continue
        result.maps.append({"file": filename, "count": _rounds_to_count(rounds)})
    return result


def _refusal(filename: str) -> str:
    """Why the admin port could never add ``filename``, or '' if it can."""
    if not filename:
        return "no file name"
    if not _MAP_FILE.search(filename):
        return "the file name doesn't end in .bms, .npj or .npz"
    if any(ch.isspace() for ch in filename):
        # The server splits every admin command at spaces (Kong.c
        # CAdminServer_DispatchCommand @0x406720), so no tool can add it.
        return "a space in the file name: no admin tool can add it"
    if "/" in filename or "\\" in filename:
        return "a folder in the file name"
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        return "letters the admin port can't send"
    return ""


def not_on_server(maps, server_files) -> list:
    """Files in ``maps`` the server's catalog doesn't have (first time each)."""
    known = {name.casefold() for name in server_files}
    missing, seen = [], set()
    for entry in maps:
        filename = entry["file"]
        key = filename.casefold()
        if key not in known and key not in seen:
            seen.add(key)
            missing.append(filename)
    return missing
