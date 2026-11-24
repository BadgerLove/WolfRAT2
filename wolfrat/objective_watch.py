"""Zones, flags and the hill changing hands, for the phone page's bell (no Qt).

The Bans tab reads the zone owners, the flag counts and the hill clocks from
the game server every poll (jo_players) and hands them to ``ObjectiveWatch``,
which turns a change into one line for the bell: "Rebels took zone C (3 of
5)", "Joint Ops captured a flag (2 of 5)", "Rebels took the hill".  Nothing
here is sent to the game; the Sprees tab's own zone announcements
(zone_rules.ZoneWatch) are separate and unchanged.
"""

from __future__ import annotations

from typing import Optional

from wolfrat import vote_rules
from wolfrat.jo_players import GAME_CTF, GAME_FB, GAME_TKOTH
from wolfrat.zone_rules import team_name, zone_letter

KIND_ZONE, KIND_FLAG = "zone", "flag"
TEAMS = (1, 2)


class ObjectiveWatch:
    """``update`` once per poll -> [(kind, text), ...] for what changed since
    the last one.  The first reading, a new map name or another game type only
    remembers what is there."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._seen = False
        self._map: Optional[str] = None
        self._game_type: Optional[int] = None
        self._zones: Optional[tuple] = None      # ((tier, position), ...): which zones these are
        self._owners: dict = {}                  # tier -> team
        self._round_won = False                  # a team was seen taking the last zone
        self._caps: Optional[dict] = None        # team -> captures
        self._hill_team: Optional[int] = None    # the last team that held the hill alone

    def update(self, map_name, game_type, zones, caps, goals, hill) -> list:
        """zones: .tier/.team/.pos; caps and goals: {1: n, 2: n} or None;
        hill: jo_players.HillInfo or None."""
        first = not self._seen or map_name != self._map or game_type != self._game_type
        self._seen, self._map, self._game_type = True, map_name, game_type
        return (self._zone_events(game_type, zones, first)
                + self._flag_events(game_type, caps, goals, first)
                + self._hill_events(game_type, hill, first))

    def _zone_events(self, game_type, zones, first: bool) -> list:
        if vote_rules.family_from_game_type(game_type) != vote_rules.MODE_AS:
            self._zones, self._owners, self._round_won = None, {}, False
            return []
        zones = sorted(zones or (), key=lambda zone: int(zone.tier))
        layout = tuple((int(zone.tier), getattr(zone, "pos", None)) for zone in zones)
        owners = {int(zone.tier): int(zone.team) for zone in zones}
        known, before = self._zones, self._owners
        self._zones, self._owners = layout, owners
        if first or layout != known:
            self._round_won = False
            return []                  # other zones than last time (another map): just remember
        if owners == before:
            return []
        if self._round_won:
            self._round_won = False
            return []                  # the first change after a win: the same map starting again
        events = []
        for tier, team in owners.items():
            if team in TEAMS and team != before.get(tier):
                held = sum(1 for owner in owners.values() if owner == team)
                events.append((KIND_ZONE, f"{team_name(team)} took zone {zone_letter(tier)} "
                                          f"({held} of {len(owners)})"))
        holders = set(owners.values())
        self._round_won = len(holders) == 1 and holders <= set(TEAMS)
        return events

    def _flag_events(self, game_type, caps, goals, first: bool) -> list:
        if game_type not in (GAME_CTF, GAME_FB) or not caps:
            self._caps = None
            return []
        now = {team: int(caps.get(team, 0)) for team in TEAMS}
        before, self._caps = self._caps, now
        if first or before is None or any(now[team] < before[team] for team in TEAMS):
            return []                  # a count that went down is a new round
        did = "captured a flag" if game_type == GAME_CTF else "scored"
        events = []
        for team in TEAMS:
            if now[team] > before[team]:
                goal = (goals or {}).get(team)
                events.append((KIND_FLAG, f"{team_name(team)} {did}"
                                          + (f" ({now[team]} of {goal})" if goal else "")))
        return events

    def _hill_events(self, game_type, hill, first: bool) -> list:
        if game_type != GAME_TKOTH:
            self._hill_team = None
            return []
        if hill is None or not hill.teams:
            return []                  # no reading this time: keep what was known
        holding = [team.team for team in hill.teams if team.holders > 0]
        alone = holding[0] if len(holding) == 1 else None
        if first:
            self._hill_team = alone
            return []
        if alone is None or alone == self._hill_team:
            return []
        self._hill_team = alone
        return [(KIND_ZONE, f"{team_name(alone)} took the hill")]
