"""Lead announcements for Capture the Flag, Flagball, Team Deathmatch and
Deathmatch (Dale, 2026-09-23).  No Qt.

Scores:
* CTF / Flagball - each team's captures, read from the server on this PC
  (jo_players.read_team_caps; proven live 2026-09-23).
* Team Deathmatch - each team's kills, summed from the admin-port player list.
* Deathmatch - each player's kills from the same list.

Rules (Dale): a tie is nobody's lead.  CTF / Flagball change slowly, so every
new leader is announced at once, plus the first flag / goal of the map.  In
TDM / DM the lead can swap every few seconds, so a new leader must hold it
for 30 seconds and there is at most one lead line a minute.  The first
reading after a map change is only remembered (it can still be the last
map's scores).  Every line fits JO's 59-character chat.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Optional

from wolfrat import vote_rules
from wolfrat.admin_commands import MAX_CHAT_LEN

CTF, FB, TDM, DM = vote_rules.MODE_CTF, vote_rules.MODE_FB, vote_rules.MODE_TDM, vote_rules.MODE_DM
MODES = (CTF, FB, TDM, DM)
TEAM_MODES = (CTF, FB, TDM)
CAPS_MODES = (CTF, FB)
LABELS = {CTF: "Capture the Flag", FB: "Flagball", TDM: "Team Deathmatch", DM: "Deathmatch"}
TEAM_NAMES = {1: "Joint Ops", 2: "Rebels"}
CHAT_LIMIT = MAX_CHAT_LEN   # 59: JO cuts every line there
HOLD_SECONDS = {TDM: 30.0, DM: 30.0}
MIN_GAP_SECONDS = {TDM: 60.0, DM: 60.0}

DEFAULT_LINES = {
    CTF: ["{team} take the lead, {score} flags to {other}",
          "Lead change! {team} ahead {score}-{other} on flags",
          "{team} go in front - {score} flags to {other}"],
    FB: ["{team} take the lead, {score} goals to {other}",
         "Lead change! {team} ahead {score}-{other}",
         "{team} go in front - {score} goals to {other}"],
    TDM: ["{team} take the lead, {score} kills to {other}",
          "Lead change! {team} ahead {score}-{other} on kills",
          "{team} push in front - {score} kills to {other}"],
    DM: ["{player} takes the lead with {score} kills",
         "{player} is top of the table on {score} kills",
         "New leader: {player} with {score} kills"],
}
DEFAULT_FIRST = {CTF: "{team} capture the first flag of the map!",
                 FB: "{team} score the first goal of the map!"}


@dataclass(frozen=True)
class LeadChange:
    mode: str
    leader: object          # team number (1/2) or player name (DM)
    score: int
    other: int              # best of the rest


@dataclass(frozen=True)
class FirstScore:
    mode: str
    team: int


def leader_of(scores: dict):
    """(leader, top score, best of the rest); leader None on a tie or no scores."""
    if not scores:
        return None, 0, 0
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    return (None if top == second else top_key), top, second


def team_kills(players) -> dict:
    out = {1: 0, 2: 0}
    for p in players or []:
        if str(p.get("id", "")) == "0":
            continue
        try:
            team, kills = int(p.get("team", 0)), int(p.get("kills", 0))
        except (TypeError, ValueError):
            continue
        if team in out:
            out[team] += kills
    return out


def player_kills(players) -> dict:
    out = {}
    for p in players or []:
        name = str(p.get("name", "")).strip()
        if not name or str(p.get("id", "")) == "0":
            continue
        try:
            out[name] = int(p.get("kills", 0))
        except (TypeError, ValueError):
            continue
    return out


def fill(template: str, event, mode: str) -> str:
    if isinstance(event, FirstScore):
        who, score, other = TEAM_NAMES.get(event.team, ""), 0, 0
    else:
        who = TEAM_NAMES.get(event.leader, "") if mode in TEAM_MODES else str(event.leader)
        score, other = event.score, event.other
    line = (template.replace("{team}", who).replace("{player}", who)
                    .replace("{score}", str(score)).replace("{other}", str(other)))
    return re.sub(r"\b1 (flag|goal|kill)s\b", r"1 \1", line)      # "1 flags" -> "1 flag"


def announce(events, first_on: bool, lead_on: bool) -> list:
    """The events worth saying.  When the first capture is announced, the lead
    change it caused in the same poll is left out - the first line says it."""
    firsts = [e for e in events if isinstance(e, FirstScore)] if first_on else []
    said_teams = {e.team for e in firsts}
    leads = [e for e in events if isinstance(e, LeadChange) and lead_on and e.leader not in said_teams]
    return firsts + leads


def pick_line(templates, event, mode: str, rng: random.Random) -> str:
    """A random template that fits 59 characters once filled; the shortest,
    cut to 59, if none does (a very long player name)."""
    filled = [fill(t, event, mode) for t in templates if t.strip()]
    if not filled:
        return ""
    fitting = [line for line in filled if len(line) <= CHAT_LIMIT]
    return rng.choice(fitting) if fitting else min(filled, key=len)[:CHAT_LIMIT]


class LeadWatch:
    """One per map; ``reset()`` on a map change."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._mode: Optional[str] = None
        self._scores: Optional[dict] = None
        self._announced = None
        self._candidate = None
        self._since = 0.0
        self._last_said = float("-inf")
        self._first_done = False

    def update(self, mode: str, scores: dict) -> list:
        now = self._clock()
        if mode not in MODES:
            return []
        if mode != self._mode:                         # new mode (or first look): just remember
            self.reset()
            self._mode = mode
        scores = dict(scores)
        leader, top, second = leader_of(scores)
        if self._scores is None:
            self._scores, self._announced = scores, leader
            self._first_done = sum(scores.values()) > 0
            return []
        events = []
        if sum(scores.values()) == 0:
            self._first_done = False                   # scores never fall mid-match: back to 0-0 = a new map
        if mode in CAPS_MODES and not self._first_done and sum(scores.values()) > 0:
            self._first_done = True
            gained = [t for t, v in scores.items() if v > self._scores.get(t, 0)]
            if gained:
                events.append(FirstScore(mode, gained[0]))
        self._scores = scores
        if leader is None:
            self._candidate = None
            self._announced = None                     # a tie is nobody's lead
            return events
        if leader == self._announced:
            self._candidate = None
            return events
        if leader != self._candidate:
            self._candidate, self._since = leader, now
        hold, gap = HOLD_SECONDS.get(mode, 0.0), MIN_GAP_SECONDS.get(mode, 0.0)
        if now - self._since >= hold and now - self._last_said >= gap:
            self._announced, self._candidate, self._last_said = leader, None, now
            events.append(LeadChange(mode, leader, top, second))
        return events
