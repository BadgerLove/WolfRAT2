"""Lifetime player stats, XP and ranks (Dale, 2026-11-15).

Replaces the kills/deaths-only !kd numbers.  Everything is read from the server's
own per-player stats record, the one the end-of-round board is built from, so a
player's XP here is the board's EXPERIENCEPOINTS column added up over every round.

Where each number comes from (kong.c, CPlayerStats_GetFieldByIndex @0x52D630: the
board's FIELD id -> the record's dword; score.ini lists the FIELD ids in order):
- EXPERIENCEPOINTS  FIELD 19 -> dword 29, as it stands (no sum, no scaling)
- NUMENEMYKILLS     FIELD 3  -> dword 5        NUMDEATHS       FIELD 4  -> dword 7
- NUMHEADSHOTKILLS  FIELD 16 -> dword 18       NUMKNIFEKILLS   FIELD 17 -> dword 19
- NUMMEDICSAVES     FIELD 10 -> dword 9 (a medic's revives: GameEvent_RevivePlayer
  @0x517CD0 scores event 6, CPlayerStats_RecordEvent 8)
- medic heals: dword 8 (GameEvent_HealPlayer @0x50DE30, RecordEvent 7; no FIELD id
  reads it, OpenNova novaworld-net-re.md, the scorer's cases 5 and 6)
- NUMPSPTAKEOVERS   FIELD 13 -> dword 16       NUMMULTIPLEKILLS FIELD 15 -> dword 17
- NUMSHOTSFIRED     FIELD 9  -> dword 2; hits: dword 3 (the scorer's HIT event)

The record is zeroed when a player changes team (Server_ChangeEntityTeam @0x518D70),
at a Zero Score and at every new round, and a slot is wiped when its player leaves,
so the totals are built from what each counter gained since the last look.  A
player keeps what they earned before a team change; one who stays on a team all
round gains exactly the board's number.

`keep_stats.StatsKeeper` reads the slots once a second and hands them on, with the
slots whose stats it has just written back (a rejoin): those are old numbers the
total already holds, so they move the baseline and add nothing.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, fields
from typing import Callable, Iterable, Optional, Sequence

SHOTS, HITS, KILLS, DEATHS, HEALS, REVIVES = 2, 3, 5, 7, 8, 9
ZONES, MULTI_KILLS, HEADSHOTS, KNIFE_KILLS, XP = 16, 17, 18, 19, 29
NOT_A_COUNT = 35

# Total column -> the record's dword.
COUNTED = {
    "xp": XP, "kills": KILLS, "deaths": DEATHS, "headshots": HEADSHOTS, "knife_kills": KNIFE_KILLS,
    "revives": REVIVES, "heals": HEALS, "zones": ZONES, "multi_kills": MULTI_KILLS,
    "shots": SHOTS, "hits": HITS,
}
IN_MATCH = (6, 7)                        # the scoreboard's own test (slot state 6/7)
LONGEST_GAP_SECONDS = 5.0                # a longer gap between looks is not play time
MAX_CHAT_TOKENS = 23                     # admin_commands.MAX_CHAT_TOKENS: more words than this and the line is split


# ---- ranks -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Rank:
    number: int
    name: str
    xp: int


DEFAULT_RANKS = (
    ("Recruit", 0), ("Private", 100), ("Private II", 250), ("Trooper", 450), ("Lance Corporal", 700),
    ("Corporal", 1000), ("Senior Corporal", 1400), ("Sergeant", 1900), ("Staff Sergeant", 2500),
    ("Gunnery Sergeant", 3200), ("Master Sergeant", 4000), ("First Sergeant", 5000),
    ("Sergeant Major", 6000), ("Warrant Officer", 7200), ("Chief Warrant", 8500), ("Cadet", 10000),
    ("Ensign", 12000), ("2nd Lieutenant", 14000), ("Lieutenant", 16500), ("1st Lieutenant", 19000),
    ("Captain", 22000), ("Staff Captain", 25000), ("Major", 28500), ("Senior Major", 32000),
    ("Lt Colonel", 36000), ("Colonel", 40000), ("Senior Colonel", 45000), ("Brigadier", 50000),
    ("Major General", 56000), ("Lt General", 62000), ("General", 69000), ("Field Marshal", 76000),
    ("Commander", 84000), ("High Commander", 92000), ("Operator", 100000), ("Elite Operator", 115000),
    ("Veteran", 130000), ("Elite Veteran", 150000), ("Warlord", 170000), ("Elite Warlord", 200000),
    ("Champion", 230000), ("Grand Champion", 270000), ("Hero", 310000), ("War Hero", 360000),
    ("Legend", 420000), ("War Legend", 500000), ("Mythic", 600000), ("Immortal", 720000),
    ("Godlike", 850000), ("Joint Ops Legend", 1000000),
)
MAX_RANK_NAME = 24


def chat_safe(text: str) -> str:
    """Chat is ASCII only (protocol._split_chat_message refuses anything else)."""
    return " ".join(str(text).encode("ascii", "replace").decode("ascii").split())


def build_ranks(rows: Iterable = DEFAULT_RANKS) -> tuple:
    """Rows of (name, xp) -> ranks numbered from 1 in XP order.  The first rank is
    always at 0 XP, so everyone has one.  Bad rows are dropped; no rows = the defaults."""
    cleaned = []
    for row in rows or ():
        try:
            name, xp = row[0], int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        name = chat_safe(name)[:MAX_RANK_NAME]
        if name and xp >= 0:
            cleaned.append((xp, name))
    if not cleaned:
        return build_ranks(DEFAULT_RANKS)
    cleaned.sort(key=lambda item: item[0])
    ranks, last_xp = [], None
    for xp, name in cleaned:
        if xp == last_xp:                # two ranks at the same XP: the first one wins
            continue
        last_xp = xp
        ranks.append(Rank(len(ranks) + 1, name, 0 if not ranks else xp))
    return tuple(ranks)


def rank_for(xp: int, ranks: Sequence[Rank]) -> Rank:
    current = ranks[0]
    for rank in ranks:
        if xp < rank.xp:
            break
        current = rank
    return current


def next_rank(xp: int, ranks: Sequence[Rank]) -> Optional[Rank]:
    for rank in ranks:
        if rank.xp > xp:
            return rank
    return None


# ---- one player's totals -----------------------------------------------------------------

@dataclass
class Career:
    name: str
    xp: int = 0
    kills: int = 0
    deaths: int = 0
    headshots: int = 0
    knife_kills: int = 0
    revives: int = 0
    heals: int = 0
    zones: int = 0
    multi_kills: int = 0
    shots: int = 0
    hits: int = 0
    best_streak: int = 0
    seconds: float = 0.0
    first_seen: str = ""
    last_seen: str = ""

    @property
    def headshot_percent(self) -> int:
        return round(100 * self.headshots / self.kills) if self.kills > 0 else 0


_COLUMNS = tuple(f.name for f in fields(Career))
_NUMBERS = tuple(c for c in _COLUMNS if c not in ("name", "first_seen", "last_seen"))


class CareerStore:
    """The totals on disk (SQLite), with the players being changed held in memory."""

    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS career (name_lower TEXT PRIMARY KEY, name TEXT NOT NULL, "
            + ", ".join(f"{c} {'REAL' if c == 'seconds' else 'INTEGER'} NOT NULL DEFAULT 0" for c in _NUMBERS)
            + ", first_seen TEXT NOT NULL DEFAULT '', last_seen TEXT NOT NULL DEFAULT '')")
        self._db.commit()
        self._held: dict = {}
        self._dirty: set = set()

    @staticmethod
    def key(name: str) -> str:
        return name.strip().lower()

    def _row(self, where: str, args: tuple) -> Optional[Career]:
        row = self._db.execute(f"SELECT {', '.join(_COLUMNS)} FROM career WHERE {where}", args).fetchone()
        return Career(*row) if row else None

    def hold(self, name: str) -> Career:
        """The player's totals to add to; a new player starts at zero."""
        key = self.key(name)
        career = self._held.get(key)
        if career is None:
            career = self._row("name_lower = ?", (key,)) or Career(name=name, first_seen=_stamp())
            self._held[key] = career
        career.name = name
        return career

    def touched(self, name: str) -> None:
        self._dirty.add(self.key(name))

    def release(self, names_playing: Iterable[str]) -> None:
        """Write, then let go of everyone who is no longer playing."""
        self.flush()
        playing = {self.key(n) for n in names_playing}
        for key in [k for k in self._held if k not in playing]:
            del self._held[key]

    def flush(self) -> None:
        if not self._dirty:
            return
        marks = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "first_seen")
        for key in self._dirty:
            career = self._held.get(key)
            if career is None:
                continue
            self._db.execute(
                f"INSERT INTO career (name_lower, {', '.join(_COLUMNS)}) VALUES (?, {marks}) "
                f"ON CONFLICT(name_lower) DO UPDATE SET {updates}",
                (key, *(getattr(career, c) for c in _COLUMNS)))
        self._db.commit()
        self._dirty.clear()

    def find(self, name: str) -> Optional[Career]:
        """Exact name first (any case), then the one player whose name holds the text."""
        self.flush()
        key = self.key(name)
        if not key:
            return None
        found = self._row("name_lower = ?", (key,))
        if found is not None:
            return found
        like = "%" + key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self._db.execute(
            "SELECT name_lower FROM career WHERE name_lower LIKE ? ESCAPE '\\' LIMIT 2", (like,)).fetchall()
        return self._row("name_lower = ?", rows[0]) if len(rows) == 1 else None

    def position(self, xp: int) -> tuple:
        """(place on the server by XP, players in the table)."""
        self.flush()
        above = self._db.execute("SELECT COUNT(*) FROM career WHERE xp > ?", (xp,)).fetchone()[0]
        return above + 1, self.count()

    def count(self) -> int:
        self.flush()
        return self._db.execute("SELECT COUNT(*) FROM career").fetchone()[0]

    def top(self, limit: int = 10) -> list:
        self.flush()
        rows = self._db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM career ORDER BY xp DESC, kills DESC LIMIT ?", (limit,)).fetchall()
        return [Career(*row) for row in rows]

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self._db.close()


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- adding up what the server counts ----------------------------------------------------

def _tally(counters: Sequence[int]) -> int:
    """Everything that only ever goes up (XP can go down, dword 35 isn't a count)."""
    return sum(v for i, v in enumerate(counters) if i not in (NOT_A_COUNT - 1, XP - 1))


class CareerTracker:
    """Call ``observe(slots, rebased)`` about once a second with every player in the server."""

    def __init__(self, store: CareerStore, clock: Callable[[], float] = time.monotonic,
                 ranks: Callable[[], Sequence[Rank]] = lambda: build_ranks(),
                 ranked_up: Callable[[str, Rank], None] = lambda _name, _rank: None) -> None:
        self.store = store
        self._clock = clock
        self._ranks = ranks
        self._ranked_up = ranked_up
        self._last: dict = {}            # slot key -> counters at the last look
        self._streak: dict = {}
        self._looked_at: Optional[float] = None
        self.seen = False                # has a server ever been read this session

    @property
    def tracking(self) -> int:
        return len(self._last)

    def observe(self, slots: Iterable, rebased: Iterable = ()) -> None:
        now = self._clock()
        gap = 0.0 if self._looked_at is None else min(max(now - self._looked_at, 0.0), LONGEST_GAP_SECONDS)
        self._looked_at = now
        self.seen = True
        rebased = set(rebased)
        ranks = self._ranks()
        live = {}
        for slot in slots:
            live[slot.key] = slot
            last = self._last.get(slot.key)
            self._last[slot.key] = slot.counters
            if last is None or slot.key in rebased:      # first look, or old stats just written back
                self._streak.setdefault(slot.key, 0)
                continue
            self._credit(slot, last, gap, ranks)
        gone = [key for key in self._last if key not in live]
        for key in gone:
            del self._last[key]
            self._streak.pop(key, None)
        if gone:
            self.store.release(slot.name for slot in live.values())

    def _credit(self, slot, last: Sequence[int], gap: float, ranks: Sequence[Rank]) -> None:
        now = slot.counters
        wiped = _tally(now) < _tally(last)               # a team change, a Zero Score or a new round
        gained = {}
        for column, field in COUNTED.items():
            before = 0 if wiped else last[field - 1]
            gained[column] = now[field - 1] - before
            if column != "xp" and gained[column] < 0:
                gained[column] = 0
        playing = slot.state in IN_MATCH
        if not any(gained.values()) and not (playing and gap):
            return
        career = self.store.hold(slot.name)
        rank_before = rank_for(career.xp, ranks)
        for column, amount in gained.items():
            setattr(career, column, max(0, getattr(career, column) + amount))
        streak = 0 if (wiped or gained["deaths"]) else self._streak.get(slot.key, 0) + gained["kills"]
        self._streak[slot.key] = streak
        career.best_streak = max(career.best_streak, streak)
        if playing:
            career.seconds += gap
        career.last_seen = _stamp()
        self.store.touched(slot.name)
        rank_now = rank_for(career.xp, ranks)
        if rank_now.number > rank_before.number:
            self._ranked_up(career.name, rank_now)


# ---- chat lines --------------------------------------------------------------------------

def compact(value: int) -> str:
    """9999 -> 9999, 41200 -> 41.2k, 412300 -> 412k, 1230000 -> 1.23M."""
    value = int(value)
    if abs(value) < 10000:
        return str(value)
    if abs(value) < 100000:
        return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    if abs(value) < 1000000:
        return f"{value // 1000}k"
    return f"{value / 1000000:.2f}".rstrip("0").rstrip(".") + "M"


def xp_text(xp: int) -> str:
    """XP is shown in thousands from 1k up: 950, 1.2k, 41.2k, 412k, 1.23M."""
    xp = int(xp)
    if 1000 <= abs(xp) < 10000:
        return f"{xp / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return compact(xp)


def played_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes}m" if minutes < 60 else f"{compact(minutes // 60)}h"


def fits(line: str, limit: int) -> bool:
    return len(line) <= limit and len(line.split()) <= MAX_CHAT_TOKENS


def _first_fit(candidates: Iterable[str], limit: int) -> str:
    last = ""
    for last in candidates:
        if fits(last, limit):
            return last
    return " ".join(last.split()[:MAX_CHAT_TOKENS])[:limit].rstrip()


def rank_line(name: str, place: int, players: int, xp: int, ranks: Sequence[Rank], limit: int) -> str:
    name = chat_safe(name)
    rank = rank_for(xp, ranks)
    after = next_rank(xp, ranks)
    title = f"Rank {rank.number} {rank.name}"
    points = f"{xp_text(xp)} XP"
    candidates = []
    if after is not None:
        candidates.append(f"{name}: #{place} of {players} | {title} | {points} | "
                          f"Next {after.name} in {xp_text(after.xp - xp)}")
    candidates += [
        f"{name}: #{place} of {players} | {title} | {points}",
        f"{name}: #{place} | {title} | {points}",
        f"{name}: #{place} {title} {points}",
        f"{name}: #{place} Rank {rank.number} {points}",
    ]
    return _first_fit(candidates, limit)


def _stat_parts(career: Career, short: bool, played: str = "Played ") -> tuple:
    n = compact if short else str
    first = (f"K/D {n(career.kills)}/{n(career.deaths)}",
             f"HS {n(career.headshots)} {career.headshot_percent}%",
             f"KK {n(career.knife_kills)}")
    second = (f"Streak {n(career.best_streak)}", f"Revives {n(career.revives)}", f"Heals {n(career.heals)}",
              f"Zones {n(career.zones)}", f"{played}{played_text(career.seconds)}")
    return first, second


def stats_lines(career: Career, limit: int) -> list:
    """One line when it fits (long chat), two when it doesn't.  Never more than two.
    Each bar is a word to the server, and a line holds 23 words: the one-line form
    uses commas when the bars would take it over."""
    name = chat_safe(career.name)
    for short, bar in ((False, " | "), (False, ", "), (True, ", ")):
        first, second = _stat_parts(career, short)
        whole = f"{name}: " + bar.join(first + second)
        if fits(whole, limit):
            return [whole]
    heads, tails = [], []
    for short, bar, played in ((False, " | ", "Played "), (False, " | ", ""), (True, " | ", ""), (True, " ", "")):
        first, second = _stat_parts(career, short, played)
        heads.append(f"{name}: " + bar.join(first))
        tails.append(bar.join(second))
    return [_first_fit(heads, limit), _first_fit(tails, limit)]


def rank_up_line(name: str, rank: Rank, limit: int) -> str:
    name = chat_safe(name)
    return _first_fit((f"{name} is now Rank {rank.number} {rank.name}!",
                       f"{name}: Rank {rank.number} {rank.name}!",
                       f"{name}: Rank {rank.number}!"), limit)


__all__ = ["Career", "CareerStore", "CareerTracker", "DEFAULT_RANKS", "Rank", "build_ranks", "chat_safe", "compact",
           "fits", "next_rank", "played_text", "rank_for", "rank_line", "rank_up_line", "stats_lines",
           "xp_text"]
