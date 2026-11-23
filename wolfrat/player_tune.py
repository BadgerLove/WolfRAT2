"""Auto-tune per player (Dale, 2026-11-23: "is there anything we can do to make
auto-tune more intelligent and useful ... would be good to see the server kind
of manage itself").  The contract with the helper: tmp/autotune2/SPEC.md.

One Extra for the whole server scored flat - the hit curve was the same from
+40 to +80, so the old server-wide auto-tune could only wander - but players
differ: one hits most with +120, another with -40.  So each player gets their
own Extra, learned from their own bullets:

- The helper (version 6) tests every close bullet step of a player at their
  Extra (the centre), 40 ms lower and 40 ms higher, and counts the steps where
  two answers disagree: a "down win" (-40 hit, the centre missed), a "down
  loss" (the centre hit, -40 missed), and the same for +40.
- WolfRAT reads those counts every poll and keeps two scores per player (two
  one-sided CUSUM): a disagreeing shot the other side won adds 0.262, one the
  centre won takes 0.357 off, and a score never goes below 0.  A score that
  reaches 4.7 (lower) or 6.0 (higher: stricter, because more rewind affects
  the targets) moves the player's Extra 20 ms that way.  At most one move per
  150 disagreeing shots, and only inside the limits: down to no rewind at all
  (only the shooter is affected), up at most 40 ms over the server Extra and
  never past 250 ms of ping + Extra.  Research: a false move about once per
  3,000 (down) / 10,000 (up) disagreeing shots; a real 65:35 edge is found
  after about 90 / 115 of them (tmp/autotune-research/REPORT.md).
- Watch learns and shows it, On also uses it (the helper takes a new value on
  when the player dies, or after 120 s), Off learns nothing.
- Players are remembered by name across nights (wolfrat_player_tune.json); a
  player back after 14+ days away, or whose ping is 50+ ms from the one their
  value was learned at, keeps half of it (to a 20 ms step).  The admin can lock
  a player to the server value, let them learn again, or forget them.

No PyQt here, so it is unit-testable.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from wolfrat import lag_comp as lc

FILE_NAME = "wolfrat_player_tune.json"
FILE_VERSION = 1

OFF, WATCH, ON = lc.TUNE_OFF, lc.TUNE_WATCH, lc.TUNE_APPLY
MODE_WORDS = {OFF: "off", WATCH: "watch", ON: "on"}

# The SPEC's constants: the helper uses the same names and values.
PT_SPREAD_MS = lc.PT_SPREAD_MS        # 40: the helper tests centre - 40, centre, centre + 40
PT_STEP_MS = 20                       # WolfRAT moves a player's Extra 20 ms at a time
PT_UP_LIMIT_MS = lc.PT_UP_LIMIT_MS    # 40: a player's Extra is at most the server Extra + 40
PT_UP_TOTAL_MS = lc.PT_UP_TOTAL_MS    # 250: above the server Extra, ping + Extra stays <= 250
PT_ADOPT_WAIT_S = lc.PT_ADOPT_WAIT_S  # 120: On - taken on at death, or after 120 s alive
CUSUM_WIN = 0.262                     # ln(0.65 / 0.5): the other side hit, the centre missed
CUSUM_LOSS = -0.357                   # ln(0.35 / 0.5): the centre hit, the other side missed
CUSUM_DOWN_H = 4.7                    # move down (-20) at this score
CUSUM_UP_H = 6.0                      # move up (+20) at this score
PT_MIN_BETWEEN = 150                  # disagreeing shots (down + up, wins + losses) between moves
PT_FADE_DAYS = 14                     # back after this long away: half the learned difference
PT_FADE_PING_MS = 50                  # the same when the ping moved more than this

TOP_LIMIT_S = 30 * 60                 # this long at the top limit (while on): listed for the admin
TOP_LOOK_MAX_S = 30                   # one gap between two looks counts at most this much
PING_SETTLE_S = 30                    # the ping fade looks at a ping 30 s after the player arrived
SEAT_GAP_S = 60                       # missing from the rows longer than this = a new visit
MOVES_KEPT = 10
SAVE_EVERY_S = 60
FORGET_DAYS = 60                      # a player on the server value, unseen this long, is dropped


# ------------------------------------------------------------ the limits
def can_go_down(offset: int, server_extra_ms: int, ping: int) -> bool:
    """A 20 ms move down is allowed while the player still has some rewind:
    the lowest is no rewind at all (plain JO), which only affects the shooter."""
    return ping + lc.personal_extra(server_extra_ms + offset, server_extra_ms, ping) > 0


def can_go_up(offset: int, server_extra_ms: int, ping: int) -> bool:
    """A 20 ms move up is allowed up to the server Extra + 40, and above the
    server Extra only while ping + Extra stays within 250 ms (more rewind lets
    a shot reach a target who was already behind cover)."""
    new = offset + PT_STEP_MS
    if new > PT_UP_LIMIT_MS:
        return False
    return new <= 0 or ping + server_extra_ms + new <= PT_UP_TOTAL_MS


def at_top_limit(offset: int, server_extra_ms: int, ping: int) -> bool:
    """Learned above the server Extra and no higher allowed: the admin is told
    about a player who sits here for 30+ minutes (a player who aims behind
    their targets could train their own rewind up)."""
    return offset > 0 and not can_go_up(offset, server_extra_ms, ping)


def halve(offset: int) -> int:
    """Half the way back to the server Extra, to a 20 ms step (toward 0)."""
    sign = -1 if offset < 0 else 1
    return sign * (abs(int(offset)) // (2 * PT_STEP_MS)) * PT_STEP_MS


def signed(ms: int) -> str:
    return "%+d" % ms


def _interleave(kinds: Iterable[tuple]) -> list:
    """The disagreeing shots of one look, in an even mix.  Their real order
    inside a look is not known and a score floored at 0 depends on it, so each
    kind is spread evenly over the look (no kind all first); at a tie a win
    comes before a loss (the lower score of the two orders)."""
    events = []
    for k, (kind, n) in enumerate(kinds):
        for i in range(n):
            events.append(((i + 0.5) / n, k, kind))
    events.sort()
    return [kind for _, _, kind in events]


# ------------------------------------------------------------ one player
@dataclass
class PlayerEntry:
    """What WolfRAT knows of one player (by name), kept across nights."""
    name: str
    offset: int = 0                 # ms vs the server Extra, a multiple of 20
    down: float = 0.0               # the two scores (CUSUM), never below 0
    up: float = 0.0
    since_move: int = 0             # disagreeing shots (down + up) since the last move
    down_wins: int = 0              # since the last move: the evidence for a message
    down_losses: int = 0
    up_wins: int = 0
    up_losses: int = 0
    ping: Optional[int] = None      # the ping the value was learned at (the last move)
    first_seen: float = 0.0
    last_seen: float = 0.0
    locked: bool = False            # the admin: the server value, no learning
    top_seconds: float = 0.0        # time on the server sitting at the top limit
    moves: list = field(default_factory=list)   # the last 10: time, from, to, why, evidence

    _INTS = ("offset", "since_move", "down_wins", "down_losses", "up_wins", "up_losses")
    _FLOATS = ("down", "up", "first_seen", "last_seen", "top_seconds")

    def to_json(self) -> dict:
        out = {k: getattr(self, k) for k in self._INTS + self._FLOATS}
        for k in ("down", "up", "top_seconds"):
            out[k] = round(out[k], 4)
        out.update(ping=self.ping, locked=self.locked, moves=list(self.moves))
        return out

    @classmethod
    def from_json(cls, name: str, data: dict) -> "PlayerEntry":
        """Strict: a value of the wrong kind raises (the whole file is then
        treated as broken)."""
        if not isinstance(data, dict):
            raise ValueError(f"{name!r} is not a table")
        e = cls(name)
        for k in cls._INTS:
            if k in data:
                v = data[k]
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(f"{name!r}: {k} is not a whole number")
                setattr(e, k, v)
        for k in cls._FLOATS:
            if k in data:
                v = data[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(f"{name!r}: {k} is not a number")
                setattr(e, k, float(v))
        if e.offset % PT_STEP_MS:
            raise ValueError(f"{name!r}: offset {e.offset} is not a 20 ms step")
        ping = data.get("ping")
        if ping is not None and (isinstance(ping, bool) or not isinstance(ping, int)):
            raise ValueError(f"{name!r}: ping is not a whole number")
        e.ping = ping
        locked = data.get("locked", False)
        if not isinstance(locked, bool):
            raise ValueError(f"{name!r}: locked is not true or false")
        e.locked = locked
        moves = data.get("moves", [])
        if not isinstance(moves, list) or not all(isinstance(m, dict) for m in moves):
            raise ValueError(f"{name!r}: moves is not a list")
        e.moves = moves[-MOVES_KEPT:]
        e.down, e.up = max(0.0, e.down), max(0.0, e.up)
        return e


@dataclass
class _Seat:
    """One slot's occupant as last seen: the helper's counters then (to make
    deltas) and the centre the helper had for them."""
    name: str
    counters: tuple
    center: int
    arrived: float
    last_look: float
    ping_checked: bool = False


# ------------------------------------------------------------ the tuner
class PlayerTuner:
    """Learns each player's own Extra from the helper's per-slot tune rows.

    ``observe`` once per read of the block, ``wants`` for what to write back
    (lag_comp.sync_player_tune), ``save_if_due`` every poll and ``save`` on exit.
    Times are wall-clock seconds (``time.time()``)."""

    def __init__(self, path, log: Optional[Callable[[str], None]] = None):
        self.path = str(path)
        self._log = log or (lambda _text: None)
        self.players: dict = {}
        self._seats: dict = {}
        self._dirty = False
        self._last_save = 0.0
        self._save_error = ""
        self.problem = ""               # said once when the file could not be read
        self.load()

    # -------------------------------------------------------------- learning
    def observe(self, rows, server_extra_ms: int, mode: int,
                now: Optional[float] = None) -> list:
        """One read of the helper's tune rows (lag_comp.PlayerTuneRow, any
        order).  Returns the moves made, oldest first: dicts with the plain
        words in ``message`` and the evidence beside it."""
        now = time.time() if now is None else float(now)
        g = int(server_extra_ms)
        moves: list = []
        seen = set()
        for row in rows:
            if not row.present or not row.name:
                continue
            seen.add(row.slot)
            moves.extend(self._look(row, g, mode, now))
        for slot in [s for s, seat in self._seats.items()
                     if s not in seen and now - seat.last_look > SEAT_GAP_S]:
            del self._seats[slot]          # gone: a new visit next time
        return moves

    def _look(self, row, g: int, mode: int, now: float) -> list:
        counters = row.counters()
        seat = self._seats.get(row.slot)
        if seat is None or seat.name != row.name or now - seat.last_look > SEAT_GAP_S:
            # A new occupant of the slot, back after a while, or WolfRAT has just
            # started: the counts so far are only the starting point, none is fed.
            # (Missing from one read and back in the same slot is the same visit.)
            self._seats[row.slot] = _Seat(row.name, counters, row.center_ms, now, now)
            return self._arrive(row, g, mode, now)
        if any(c < p for c, p in zip(counters, seat.counters)):
            # A count went down: the helper started counting again (Reset, new
            # settings, a new occupant between two looks).  Start from these.
            seat.counters, seat.center, seat.last_look = counters, row.center_ms, now
            return []
        delta = tuple(c - p for c, p in zip(counters, seat.counters))
        centre_before, elapsed = seat.center, now - seat.last_look
        seat.counters, seat.center, seat.last_look = counters, row.center_ms, now
        entry = self.players.get(row.name)
        if mode == OFF:
            if entry is not None:
                entry.last_seen = now       # here, not away (the 14-day fade)
                self._dirty = True
            return []
        if entry is None:
            entry = self._new_entry(row.name, now)
        entry.last_seen = now
        self._dirty = True
        if entry.locked:
            return []
        fade = self._ping_fade(entry, seat, row, g, mode, now)
        if fade is not None:
            return [fade]                   # the centre moves: these shots are not fed
        if at_top_limit(entry.offset, g, row.ping):
            entry.top_seconds += max(0.0, min(elapsed, TOP_LOOK_MAX_S))
        # The centre check: only shots tested around the value WolfRAT gave this
        # player count.  Any other centre = a new value waiting to be taken on
        # (On: at death or after 120 s).  And a look in which the centre moved
        # by a step or more (a value taken on meanwhile) is mixed: not fed.  A
        # few ms at a limit, where the value follows the ping, is the same place.
        expected = lc.personal_extra(g + entry.offset, g, row.ping)
        if row.center_ms != expected or abs(centre_before - row.center_ms) >= PT_STEP_MS:
            return []
        return self._feed(entry, delta, row, g, mode, now)

    def _new_entry(self, name: str, now: float) -> PlayerEntry:
        entry = PlayerEntry(name, first_seen=now, last_seen=now)
        self.players[name] = entry
        self._dirty = True
        return entry

    def _arrive(self, row, g: int, mode: int, now: float) -> list:
        entry = self.players.get(row.name)
        if mode == OFF:
            if entry is not None:
                entry.last_seen = now
                self._dirty = True
            return []
        if entry is None:
            self._new_entry(row.name, now)
            return []
        away = now - entry.last_seen if entry.last_seen else 0.0
        entry.last_seen = now
        self._dirty = True
        if entry.locked or not entry.offset or away < PT_FADE_DAYS * 86400:
            return []
        return [self._move(entry, row, g, mode, now, halve(entry.offset), "fade_days",
                           f"back after {int(away // 86400)} days away: half way back to "
                           f"the server value")]

    def _ping_fade(self, entry: PlayerEntry, seat: _Seat, row, g: int, mode: int,
                   now: float) -> Optional[dict]:
        """Once a visit, when the player has been on for 30 s (a ping just after
        joining is not settled yet)."""
        if seat.ping_checked or now - seat.arrived < PING_SETTLE_S or row.ping <= 0:
            return None
        seat.ping_checked = True
        if not entry.offset or entry.ping is None or abs(row.ping - entry.ping) <= PT_FADE_PING_MS:
            return None
        return self._move(entry, row, g, mode, now, halve(entry.offset), "fade_ping",
                          f"ping now {row.ping}, learned at {entry.ping}: half way back to "
                          f"the server value")

    def _feed(self, entry: PlayerEntry, delta: tuple, row, g: int, mode: int,
              now: float) -> list:
        _steps, _hits, dw, dl, uw, ul = delta
        for kind in _interleave((("dw", dw), ("dl", dl), ("uw", uw), ("ul", ul))):
            entry.since_move += 1
            if kind == "dw":
                entry.down_wins += 1
                entry.down = max(0.0, entry.down + CUSUM_WIN)
            elif kind == "dl":
                entry.down_losses += 1
                entry.down = max(0.0, entry.down + CUSUM_LOSS)
            elif kind == "uw":
                entry.up_wins += 1
                entry.up = max(0.0, entry.up + CUSUM_WIN)
            else:
                entry.up_losses += 1
                entry.up = max(0.0, entry.up + CUSUM_LOSS)
            move = self._decide(entry, row, g, mode, now)
            if move is not None:
                return [move]               # the rest of this look was at the old centre
        return []

    def _decide(self, entry: PlayerEntry, row, g: int, mode: int, now: float) -> Optional[dict]:
        if entry.since_move < PT_MIN_BETWEEN:
            return None                     # a score may stand above its line meanwhile
        # Down first: less rewind only ever affects the shooter.
        if entry.down >= CUSUM_DOWN_H:
            if can_go_down(entry.offset, g, row.ping):
                shots, wins = entry.down_wins + entry.down_losses, entry.down_wins
                return self._move(entry, row, g, mode, now, entry.offset - PT_STEP_MS, "down",
                                  f"of {shots} shots where -{PT_SPREAD_MS} and the current "
                                  f"answer disagreed, -{PT_SPREAD_MS} hit {wins}",
                                  shots, wins, entry.down)
            entry.down = CUSUM_DOWN_H       # no rewind left: it cannot go lower
        if entry.up >= CUSUM_UP_H:
            if can_go_up(entry.offset, g, row.ping):
                shots, wins = entry.up_wins + entry.up_losses, entry.up_wins
                return self._move(entry, row, g, mode, now, entry.offset + PT_STEP_MS, "up",
                                  f"of {shots} shots where +{PT_SPREAD_MS} and the current "
                                  f"answer disagreed, +{PT_SPREAD_MS} hit {wins}",
                                  shots, wins, entry.up)
            entry.up = CUSUM_UP_H           # at the top limit: held there, not run up
        return None

    def _move(self, entry: PlayerEntry, row, g: int, mode: int, now: float, new_offset: int,
              why: str, evidence: str, shots: int = 0, wins: int = 0,
              score: Optional[float] = None) -> dict:
        old = entry.offset
        mode_word = MODE_WORDS.get(mode, "watch")
        message = (f"Auto-tune ({mode_word}): {entry.name} Extra {signed(g + old)} -> "
                   f"{signed(g + new_offset)} - {evidence}")
        move = {"time": round(now, 1), "name": entry.name, "mode": mode_word, "why": why,
                "from": g + old, "to": g + new_offset, "offset_from": old, "offset_to": new_offset,
                "server_extra": g, "ping": row.ping, "shots": shots, "wins": wins,
                "score": None if score is None else round(score, 3),
                "evidence": evidence, "message": message}
        entry.offset = new_offset
        entry.down = entry.up = 0.0          # the old value must win again through the same gate
        entry.since_move = 0
        entry.down_wins = entry.down_losses = entry.up_wins = entry.up_losses = 0
        entry.top_seconds = 0.0
        if row.ping > 0:
            entry.ping = row.ping
        entry.moves = (entry.moves + [{k: v for k, v in move.items() if k != "message"}])[-MOVES_KEPT:]
        self._dirty = True
        return move

    # ------------------------------------------------------- what to write
    def wants(self, rows, server_extra_ms: int, mode: int) -> dict:
        """{slot: (name hash, Extra ms)} for the players on now who get an own
        value: everyone in Watch and On, except a locked player.  Off: nobody."""
        if mode == OFF:
            return {}
        g = int(server_extra_ms)
        out = {}
        for row in rows:
            if not row.present or not row.name:
                continue
            entry = self.players.get(row.name)
            if entry is not None and entry.locked:
                continue
            out[row.slot] = (lc.name_hash(row.name), g + (entry.offset if entry else 0))
        return out

    # ------------------------------------------------------------ the admin
    def lock(self, name: str, now: Optional[float] = None) -> str:
        """The server value for this player, and no learning, until 'learn'."""
        now = time.time() if now is None else now
        entry = self.players.get(name) or self._new_entry(name, now)
        entry.locked = True
        entry.down = entry.up = 0.0
        entry.since_move = entry.down_wins = entry.down_losses = entry.up_wins = entry.up_losses = 0
        self._dirty = True
        kept = f" (learned {signed(entry.offset)} ms is kept)" if entry.offset else ""
        return f"Auto-tune: {name} uses the server value now, set by the admin{kept}"

    def unlock(self, name: str) -> str:
        entry = self.players.get(name)
        if entry is None or not entry.locked:
            return f"Auto-tune: {name} is learning already"
        entry.locked = False
        entry.down = entry.up = 0.0
        entry.since_move = entry.down_wins = entry.down_losses = entry.up_wins = entry.up_losses = 0
        self._dirty = True
        start = f"from {signed(entry.offset)} ms" if entry.offset else "from the server value"
        return f"Auto-tune: {name} learns again, {start}, set by the admin"

    def forget(self, name: str) -> str:
        entry = self.players.pop(name, None)
        self._dirty = True
        if entry is None:
            return f"Auto-tune: nothing learned for {name}"
        what = f"{signed(entry.offset)} ms" if entry.offset else "the server value"
        return f"Auto-tune: forgot {name}'s learned Extra ({what}), set by the admin"

    # ---------------------------------------------------------- the page
    def entry(self, name: str) -> Optional[PlayerEntry]:
        return self.players.get(name)

    def own_text(self, row, server_extra_ms: int, mode: int) -> str:
        """The players table's "Own Extra" cell: "server", "+50", "+50 (watch)",
        "+50 (waiting)" (On, not taken on yet) or "locked"."""
        entry = self.players.get(row.name)
        if entry is not None and entry.locked:
            return "locked"
        if mode == OFF:
            return "server"
        g = int(server_extra_ms)
        offset = entry.offset if entry else 0
        value = lc.personal_extra(g + offset, g, row.ping)
        text = "server" if value == g else signed(value)
        if mode == WATCH:
            return text if text == "server" else text + " (watch)"
        if not self.taken_on(row, g):
            return text + " (waiting)"
        return text

    def taken_on(self, row, server_extra_ms: int) -> bool:
        """On: the helper uses the value WolfRAT wants for this player now."""
        entry = self.players.get(row.name)
        want = int(server_extra_ms) + (entry.offset if entry else 0)
        return bool(row.use_on and row.want_hash == lc.name_hash(row.name)
                    and row.want_extra_ms == want and row.adopted)

    def tooltip(self, row, server_extra_ms: int, mode: int) -> str:
        """The evidence behind a player's Own Extra, in plain words."""
        g = int(server_extra_ms)
        entry = self.players.get(row.name)
        lines = []
        if entry is None:
            lines.append(f"{row.name}: nothing learned yet - the server value ({signed(g)}).")
        else:
            value = lc.personal_extra(g + entry.offset, g, row.ping)
            head = f"{row.name}: learned {signed(g + entry.offset)}"
            head += " (the server value)." if not entry.offset else \
                f" ({signed(entry.offset)} on the server's {signed(g)})."
            lines.append(head)
            if value != g + entry.offset:
                lines.append(f"At ping {row.ping} the limits make it {signed(value)}.")
            if entry.locked:
                lines.append("Locked by the admin: the server value, no learning.")
            elif mode == OFF:
                lines.append("Auto-tune is off: the server value, nothing learned.")
            elif mode == WATCH:
                lines.append("Watch: shown only, the server value is used.")
            elif not self.taken_on(row, g):
                lines.append(f"Waiting: taken on when the player dies, or after "
                             f"{PT_ADOPT_WAIT_S} s.")
            if entry.ping is not None:
                lines.append(f"Learned at ping {entry.ping}; ping now {row.ping}.")
            lines.append(
                f"Since the last move: -{PT_SPREAD_MS} disagreed with the current answer "
                f"{entry.down_wins + entry.down_losses} times (-{PT_SPREAD_MS} hit "
                f"{entry.down_wins}); +{PT_SPREAD_MS} {entry.up_wins + entry.up_losses} times "
                f"(+{PT_SPREAD_MS} hit {entry.up_wins}).")
            lines.append(
                f"Scores: lower {entry.down:.1f} of {CUSUM_DOWN_H}, higher {entry.up:.1f} of "
                f"{CUSUM_UP_H}; {entry.since_move} of {PT_MIN_BETWEEN} disagreeing shots "
                f"before a move.")
            if entry.top_seconds:
                lines.append(f"At the top limit for {int(entry.top_seconds // 60)} min.")
            for m in reversed(entry.moves[-3:]):
                when = time.strftime("%d %b %H:%M", time.localtime(m.get("time", 0)))
                lines.append(f"{when}: {signed(m.get('from', g))} -> {signed(m.get('to', g))} - "
                             f"{m.get('evidence', m.get('why', ''))}")
        if row.steps:
            lines.append(f"This session: {row.steps:,} close bullet steps tested, the current "
                         f"answer hit {row.center_hits:,}.")
        return "\n".join(lines)

    def differing(self) -> list:
        """(name, offset) of every remembered player away from the server
        value (not locked), biggest difference first."""
        out = [(e.name, e.offset) for e in self.players.values() if e.offset and not e.locked]
        return sorted(out, key=lambda x: (-abs(x[1]), x[0].lower()))

    def top_limit_names(self) -> list:
        """Players who have sat at the top limit for 30+ minutes on the server."""
        return sorted((e.name for e in self.players.values()
                       if not e.locked and e.offset > 0 and e.top_seconds >= TOP_LIMIT_S),
                      key=str.lower)

    def locked_names(self) -> list:
        return sorted((e.name for e in self.players.values() if e.locked), key=str.lower)

    def status_lines(self, server_extra_ms: int, mode: int, has_v6: bool = True,
                     on_now: Iterable[str] = (), most: int = 8) -> list:
        """The page's status, in plain words (one item per line)."""
        g = int(server_extra_ms)
        if mode == OFF:
            return ["Auto-tune per player: off - every player uses the Extra above."]
        word = MODE_WORDS.get(mode, "watch")
        if not has_v6:
            return [f"Auto-tune per player ({word}): needs lag compensation helper version 6 - "
                    f"this server runs an older one, so every player uses the Extra above. "
                    f"Tick the box again to load the new one."]
        here = set(on_now)
        differ = sorted(self.differing(), key=lambda x: (x[0] not in here, -abs(x[1]),
                                                         x[0].lower()))
        if not differ:
            first = f"Auto-tune per player ({word}): every player is on the server value so far."
        else:
            names = ", ".join(f"{n} {signed(g + o)}" for n, o in differ[:most])
            more = f" and {len(differ) - most} more" if len(differ) > most else ""
            many = "1 player differs" if len(differ) == 1 else f"{len(differ)} players differ"
            first = f"Auto-tune per player ({word}): {many} from the server value: {names}{more}."
        lines = [first]
        top = self.top_limit_names()
        if top:
            lines.append("At the top limit for 30+ min: " + ", ".join(
                f"{n} {signed(g + self.players[n].offset)}" for n in top) + ".")
        locked = self.locked_names()
        if locked:
            lines.append("Locked to the server value: " + ", ".join(locked) + ".")
        return lines

    def summary(self, mode: int, server_extra_ms: int, since: float = 0.0) -> dict:
        """The state for a history row: who differs, who is locked or at the top
        limit, and the moves made since ``since``."""
        g = int(server_extra_ms)
        moves = sorted((dict(m, name=e.name) for e in self.players.values() for m in e.moves
                        if m.get("time", 0) >= since), key=lambda m: m.get("time", 0))
        return {"mode": MODE_WORDS.get(mode, "off"), "server_extra": g,
                "differ": {n: g + o for n, o in self.differing()},
                "locked": self.locked_names(), "top_limit": self.top_limit_names(),
                "moves": [{k: m.get(k) for k in ("time", "name", "why", "from", "to", "shots", "wins")}
                          for m in moves]}

    # ------------------------------------------------------------ the file
    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except Exception as exc:                        # unreadable or not JSON
            self._broken(exc)
            return
        try:
            if not isinstance(data, dict) or not isinstance(data.get("players"), dict):
                raise ValueError("no players table")
            self.players = {str(n): PlayerEntry.from_json(str(n), d)
                            for n, d in data["players"].items()}
        except Exception as exc:
            self._broken(exc)

    def _broken(self, exc: Exception) -> None:
        """Start empty, and keep the file that could not be read as .bad."""
        self.players = {}
        bad = self.path + ".bad"
        try:
            os.replace(self.path, bad)
        except OSError:
            pass
        self.problem = (f"{os.path.basename(self.path)} could not be read ({exc}) - kept as "
                        f"{os.path.basename(bad)}, starting with nothing learned.")
        self._log("Auto-tune per player: " + self.problem)

    def save(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        # Passers-by: on the server value, not locked and not seen for 60 days -
        # nothing worth keeping (the file would grow with every name ever seen).
        self.players = {n: e for n, e in self.players.items()
                        if e.offset or e.locked or now - e.last_seen < FORGET_DAYS * 86400}
        data = {"version": FILE_VERSION,
                "saved": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "players": {n: e.to_json() for n, e in sorted(self.players.items())}}
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError as exc:
            if str(exc) != self._save_error:
                self._save_error = str(exc)
                self._log(f"Auto-tune per player: could not save {FILE_NAME} ({exc}).")
            return False
        self._save_error = ""
        self._dirty = False
        self._last_save = now
        return True

    def save_if_due(self, now: Optional[float] = None) -> bool:
        """Save when something changed, at most once a minute."""
        now = time.time() if now is None else now
        if self._dirty and now - self._last_save >= SAVE_EVERY_S:
            return self.save(now)
        return False

    @property
    def dirty(self) -> bool:
        return self._dirty
