"""Automatic team balance during the round, with volunteers.

Dale's design (2026-09-23): when one team has 2+ more players, the server
warns 30 seconds ahead and asks for volunteers (``!1``).  When the time is up
WolfRAT moves the players the gap needs - picked at random from the
volunteers on the bigger team first, then at random from everyone else on it.
No exemptions: an owner who turns this on wants it automatic.

Joint Ops has its own balance (``AutoBalanceOnRecycle``) but it only runs as a
new map starts, never mid-round.  The panel offers one or the other.

Every line WolfRAT says fits the 59-character chat limit.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

from wolfrat import coop_guard, vote_rules
from wolfrat.admin_commands import MAX_CHAT_LEN

MODE_OFF, MODE_JOINTOPS, MODE_WOLFRAT = "off", "jointops", "wolfrat"
MODE_LABELS = {
    MODE_OFF: "Off",
    MODE_JOINTOPS: "Joint Ops' own (only as each new map starts)",
    MODE_WOLFRAT: "WolfRAT (during the round, asks for volunteers)",
}
TEAM_NAMES = {1: "Joint Ops", 2: "Rebels"}
VOLUNTEER = "!1"
DEFAULT_GAP = 2
SETTLE_SECONDS = 20        # the gap must hold this long first (someone may be rejoining)
COUNTDOWN_SECONDS = 30     # warning -> move, as Dale asked
COOLDOWN_SECONDS = 120     # after a balance, let the moves settle before looking again
CHAT_LIMIT = MAX_CHAT_LEN   # 59: JO cuts every line there
CANCEL_LINE = "Teams are even again - auto-balance cancelled"
TEAM_FAMILIES = (vote_rules.MODE_AS, vote_rules.MODE_TDM, vote_rules.MODE_TKOTH,
                 vote_rules.MODE_CTF, vote_rules.MODE_FB)


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Move:
    player: dict
    to_team: int


def is_team_game(game_type: Optional[int], map_filename: Optional[str]) -> bool:
    """Team modes only, never co-op.  The server's own game type when WolfRAT
    can read it (same PC), otherwise the map filename (as Map Voting does)."""
    if game_type is not None:
        return bool(game_type & 0x10000) and not coop_guard.is_coop(game_type)
    return vote_rules.game_mode(map_filename) in TEAM_FAMILIES


def teams(players) -> dict:
    """{1: [...], 2: [...]} from the admin-port player list (host excluded)."""
    out = {1: [], 2: []}
    for player in players or []:
        if str(player.get("id", "")) == "0":
            continue
        team = str(player.get("team", ""))
        if team in ("1", "2"):
            out[int(team)].append(player)
    return out


def fit(text: str) -> str:
    return text[:CHAT_LIMIT]


def warning_lines(bigger: int, gap: int, seconds: int = COUNTDOWN_SECONDS) -> list:
    team, other = TEAM_NAMES[bigger], TEAM_NAMES[3 - bigger]
    return [fit(f"Auto-balance: {team} have {gap} more players than {other}"),
            fit(f"Balancing in {seconds}s - {team} players type {VOLUNTEER} to volunteer")]


def moved_lines(names, to_team: int) -> list:
    """'Moved to Rebels: A, B' split over as many 59-char lines as it takes."""
    prefix = f"Moved to {TEAM_NAMES[to_team]}: "
    lines, current = [], ""
    for name in names:
        name = name[:CHAT_LIMIT - len(prefix)]
        candidate = f"{current}, {name}" if current else name
        if current and len(prefix) + len(candidate) > CHAT_LIMIT:
            lines.append(prefix + current)
            candidate = name
        current = candidate
    if current:
        lines.append(prefix + current)
    return lines


def volunteer_line(name: str, to_team: int) -> str:
    return fit(f"{name} volunteered to join {TEAM_NAMES[to_team]}")


class Balancer:
    """Feed ``tick`` every player poll and ``volunteer`` for each ``!1``.
    Both return the Say/Move actions to carry out."""

    def __init__(self, gap: int = DEFAULT_GAP, clock: Callable[[], float] = time.time,
                 rng: Optional[random.Random] = None) -> None:
        self.gap = gap
        self._clock = clock
        self._rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.state = "idle"
        self._since = 0.0
        self.deadline = 0.0
        self._cooldown_until = 0.0
        self.volunteers: dict = {}          # lower name -> display name
        self._players: list = []
        self.bigger: Optional[int] = None

    # ---- polling ---------------------------------------------------------------
    def tick(self, players, allowed: bool) -> list:
        now = self._clock()
        self._players = list(players or [])
        if not allowed:
            if self.state != "cooldown":
                self.state, self.volunteers = "idle", {}
            return []
        sides = teams(self._players)
        gap = abs(len(sides[1]) - len(sides[2]))
        bigger = 1 if len(sides[1]) > len(sides[2]) else 2

        if self.state == "cooldown":
            if now < self._cooldown_until:
                return []
            self.state = "idle"
        if self.state == "idle":
            if gap >= self.gap:
                self.state, self._since = "settling", now
            return []
        if self.state == "settling":
            if gap < self.gap:
                self.state = "idle"
                return []
            if now - self._since < SETTLE_SECONDS:
                return []
            self.state, self.bigger = "countdown", bigger
            self.deadline, self.volunteers = now + COUNTDOWN_SECONDS, {}
            return [Say(line) for line in warning_lines(bigger, gap)]
        # countdown
        if gap < self.gap:
            self.state, self.volunteers = "idle", {}
            return [Say(CANCEL_LINE)]
        self.bigger = bigger
        if now < self.deadline:
            return []
        return self._balance(sides, bigger, gap, now)

    def _balance(self, sides, bigger, gap, now) -> list:
        needed = gap // 2
        pool = sides[bigger]
        keen = [p for p in pool if str(p.get("name", "")).lower() in self.volunteers]
        rest = [p for p in pool if p not in keen]
        chosen = self._rng.sample(keen, min(needed, len(keen)))
        if len(chosen) < needed:
            chosen += self._rng.sample(rest, min(needed - len(chosen), len(rest)))
        to_team = 3 - bigger
        self.state, self._cooldown_until, self.volunteers = "cooldown", now + COOLDOWN_SECONDS, {}
        actions = [Move(p, to_team) for p in chosen]
        actions += [Say(line) for line in moved_lines([str(p.get("name", "")) for p in chosen], to_team)]
        return actions

    # ---- chat --------------------------------------------------------------------
    def volunteer(self, name: str) -> list:
        """A player typed !1.  Counts only during a countdown, only from the
        bigger team, and only once each."""
        if self.state != "countdown" or self.bigger is None:
            return []
        key = name.strip().lower()
        player = next((p for p in teams(self._players)[self.bigger]
                       if str(p.get("name", "")).lower() == key), None)
        if player is None or key in self.volunteers:
            return []
        self.volunteers[key] = display = str(player.get("name", ""))
        return [Say(volunteer_line(display, 3 - self.bigger))]

    def seconds_left(self) -> int:
        return max(0, int(round(self.deadline - self._clock()))) if self.state == "countdown" else 0
