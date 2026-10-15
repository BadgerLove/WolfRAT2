"""Finding a map from what a mod types: !map, !add, !remove, !vote.

Dale, 2026-10-15: "td-sulphur", "td_sting", "TD-Sting", "td sulp" all came
back "Map not found" - the old lookup looked for the typed text letter for
letter (spaces and dashes too) inside the display name only ("TD - Sulphur
Creek"), never the file name ("TD-SulphurCreek.bms"). And "!map stinger"
quietly loaded "Training: Stinger" because it took the first of several hits.

Now:
  * spaces, dashes, underscores, dots and case never matter, on either side;
  * the display name AND the file name are both tried;
  * a game-mode prefix may be left off ("sulphur"); with a wrong one
    ("ts-sul") the rest is tried, and anything but a single map is asked;
  * a better hit wins: whole name > start of a name > anywhere in it;
    training maps come last (nobody hosts them); then the old tie-breaks
    (TAC first when that box is ticked, a map in the rotation first);
  * still more than one map: nothing is loaded and the mod is told the
    choices, so a guess never changes the map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

EXTENSIONS = (".bms", ".npj", ".npz")
# Game-mode prefixes seen on map names ("TD - Sulphur Creek", "AS-Villa", "AAS_E_Karo").
MODE_PREFIXES = ("aas", "as", "ad", "ctf", "ct", "cp", "dm", "fb", "koth", "sd", "tdm", "td", "tk", "wp")
_PREFIX_RE = re.compile(r"^(?:%s)\s*[-_:]\s*" % "|".join(MODE_PREFIXES), re.IGNORECASE)
_QUERY_PREFIX_RE = re.compile(r"^[a-z]{2,4}\s*[-_:]\s*|^[a-z]{2,4}\s+", re.IGNORECASE)
_TRAINING_FILE_RE = re.compile(r"^\d\dtr", re.IGNORECASE)

EXACT, START, ANYWHERE = 3, 2, 1


def strip_ext(text: str) -> str:
    low = text.lower()
    for ext in EXTENSIONS:
        if low.endswith(ext):
            return text[:-len(ext)]
    return text


def squash(text: str) -> str:
    """'TD - Sulphur Creek' / 'TD-SulphurCreek.bms' / 'td_sulphur' -> 'tdsulphurcreek' ..."""
    return re.sub(r"[^a-z0-9]", "", strip_ext(text or "").lower())


def without_mode(text: str) -> str:
    return _PREFIX_RE.sub("", strip_ext(text or "").strip())


@dataclass(frozen=True)
class MapEntry:
    name: str        # what players see: "TD - Sulphur Creek"
    file: str        # "TD-SulphurCreek.bms"
    source: str      # "rotation" | "available"


@dataclass
class MapMatch:
    entry: Optional[MapEntry] = None
    choices: list = field(default_factory=list)    # several equally good maps (nothing chosen)

    @property
    def found(self) -> bool:
        return self.entry is not None


def _keys(entry: MapEntry) -> list:
    """(squashed text, is it the no-prefix form) for the name and the file."""
    keys = []
    for text in (entry.name, entry.file):
        full = squash(text)
        bare = squash(without_mode(text))
        if full:
            keys.append((full, False))
        if bare and bare != full:
            keys.append((bare, True))
    return keys


def _level(query: str, entry: MapEntry, bare_only: bool = False) -> int:
    best = 0
    for key, bare in _keys(entry):
        if bare_only and not bare:
            continue
        if query == key:
            level = EXACT
        elif key.startswith(query):
            level = START
        elif query in key:
            level = ANYWHERE
        else:
            continue
        best = max(best, level)
    return best


def is_training(entry: MapEntry) -> bool:
    return entry.name.lower().startswith("training") or bool(_TRAINING_FILE_RE.match(entry.file))


def find(query: str, entries: Iterable[MapEntry], prefer_tac: bool = True) -> MapMatch:
    """The map a mod meant, or the choices when it is not clear."""
    q = squash(query)
    if not q:
        return MapMatch()
    unique: dict = {}
    for entry in entries:                       # rotation first: it wins a duplicate file
        unique.setdefault(entry.file.casefold(), entry)
    pool = list(unique.values())

    scored = [(_level(q, e), e) for e in pool]
    scored = [(lvl, e) for lvl, e in scored if lvl]
    if not scored:
        # A wrong or extra mode prefix: "ts-sul", "td sul" for a DM map ... try the
        # rest. The prefix was wrong, so nothing is guessed: one map or the choices.
        rest = squash(_QUERY_PREFIX_RE.sub("", (query or "").strip(), count=1))
        if rest and rest != q and len(rest) >= 3:
            hits = [e for e in pool if _level(rest, e, bare_only=True)]
            hits.sort(key=lambda e: (is_training(e), e.name.lower()))
            if len(hits) == 1:
                return MapMatch(entry=hits[0])
            return MapMatch(choices=hits)
        return MapMatch()

    def rank(item):
        lvl, e = item
        tac = prefer_tac and squash(e.name).endswith("tac")
        return (lvl, not is_training(e), tac, e.source == "rotation")

    scored.sort(key=rank, reverse=True)
    top = rank(scored[0])
    best = [e for item in scored for e in [item[1]] if rank(item) == top]
    if len(best) == 1:
        return MapMatch(entry=best[0])
    return MapMatch(choices=best)


def choices_lines(typed: str, choices: list, limit: int = 59, max_lines: int = 2) -> list:
    """'Which map? TD - Sulphur Creek, DM - Sulphur Creek, ...' in chat-sized lines."""
    names = []
    for entry in choices:
        if entry.name not in names:
            names.append(entry.name)
    lines, line = [], "Which one? "
    for index, name in enumerate(names):
        piece = name if line.endswith("? ") else ", " + name
        if len(line) + len(piece) > limit:
            if len(lines) + 1 >= max_lines:
                more = f" +{len(names) - index} more"
                line = line if len(line) + len(more) <= limit else line[:limit - len(more)]
                lines.append(line + more)
                return lines
            lines.append(line + ",")
            line = name
        else:
            line += piece
    lines.append(line)
    return lines
