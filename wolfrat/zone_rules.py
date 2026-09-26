"""Advance-and-Secure zones, no Qt (2026-09-22).

The server keeps every AAS zone in ``g_zone_slot_chain`` (jo_players reads
it): each zone has a tier (its order along the chain, 1..N) and an owning
team (0 nobody, 1 Joint Ops, 2 Rebels).  Capture messages never reach the
admin port - they are HUD events - so this is the only way to see them.

Two things come out of it:
* ``zones_left`` - how many zones the leading team still needs.  One left =
  the map is about to end, which is when the AAS map vote should start.
* ``ZoneWatch`` - the first capture of a map, for the Sprees tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from wolfrat.admin_commands import MAX_CHAT_LEN

TEAM_NAMES = {1: "Joint Ops", 2: "Rebels"}
NATO = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet",
        "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo", "Sierra", "Tango"]


def zone_name(tier: int) -> str:
    """Tier 1 = Alpha ... the way the game labels them.  Past Tango: 'Zone 21'."""
    return NATO[tier - 1] if 1 <= tier <= len(NATO) else f"Zone {tier}"


def team_name(team: int) -> str:
    return TEAM_NAMES.get(int(team), f"team {team}")


def zones_left(zones: Iterable) -> Optional[tuple]:
    """(leading team, zones it still needs) or None when there are no zones.
    Zones = objects with .tier and .team.  A tie goes to the lower team number."""
    zs = list(zones)
    if not zs:
        return None
    owned = {1: 0, 2: 0}
    for z in zs:
        if int(z.team) in owned:
            owned[int(z.team)] += 1
    leader = 1 if owned[1] >= owned[2] else 2
    return leader, len(zs) - owned[leader]


def _dist2(a, b) -> float:
    return sum((float(x) - float(y)) ** 2 for x, y in zip(a[:2], b[:2]))     # ground distance, ignore height


@dataclass(frozen=True)
class CaptureEvent:
    team: int
    tier: int
    name: str            # zone name
    player: str          # best guess at who took it ('' when nobody was near)
    first: bool          # first capture of this map


@dataclass(frozen=True)
class LeadEvent:
    """The team holding MORE zones changed (a tie is nobody's lead)."""
    team: int
    owned: int
    total: int


def leader(zones: Iterable) -> int:
    """1 or 2 when that team holds strictly more zones, else 0."""
    owned = {1: 0, 2: 0}
    for z in zones:
        if int(z.team) in owned:
            owned[int(z.team)] += 1
    if owned[1] > owned[2]:
        return 1
    if owned[2] > owned[1]:
        return 2
    return 0


class ZoneWatch:
    """Remembers each zone's owner and reports flips and lead changes.
    ``reset()`` on a map change."""

    NEAR = 6_000_000.0   # engine units; a 20 s run was ~1.5-3.5 million on one axis

    def __init__(self):
        self._owner: dict[int, int] = {}
        self._seen_capture = False
        self._leader: Optional[int] = None

    def reset(self) -> None:
        self._owner.clear()
        self._seen_capture = False
        self._leader = None

    def update(self, zones: Iterable, positions: dict, teams: dict) -> list:
        """zones: .tier/.team/.pos; positions: name -> (x, y, z); teams: name -> team int.
        Returns CaptureEvents for zones whose owner changed to a team, then a
        LeadEvent when the team holding more zones changed."""
        events = []
        current = {}
        zones = list(zones)
        for z in zones:
            tier, team = int(z.tier), int(z.team)
            current[tier] = team
            before = self._owner.get(tier)
            if before is None:
                continue                       # first sight of this zone: just remember it
            if team != before and team in (1, 2):
                who = ""
                if getattr(z, "pos", None):
                    best = None
                    for name, pos in positions.items():
                        if teams.get(name) != team or not pos:
                            continue
                        d = _dist2(pos, z.pos)
                        if d <= self.NEAR ** 2 and (best is None or d < best[0]):
                            best = (d, name)
                    who = best[1] if best else ""
                events.append(CaptureEvent(team, tier, zone_name(tier), who, not self._seen_capture))
                self._seen_capture = True
        self._owner = current
        lead = leader(zones)
        if self._leader is None:
            self._leader = lead                # first sight of the map: just remember
        elif lead != self._leader:
            self._leader = lead
            if lead in (1, 2):
                owned = sum(1 for z in zones if int(z.team) == lead)
                events.append(LeadEvent(lead, owned, len(zones)))
        return events


def lead_line(event: LeadEvent, template: str) -> str:
    return (template.replace("{team}", team_name(event.team))
                    .replace("{owned}", str(event.owned))
                    .replace("{total}", str(event.total)))[:MAX_CHAT_LEN]


def capture_line(event: CaptureEvent, template_with_player: str, template_without: str) -> str:
    """Fill a template; if it wants {player} and we have nobody, use the other."""
    template = template_with_player if event.player else template_without
    return (template.replace("{team}", team_name(event.team))
                    .replace("{zone}", event.name)
                    .replace("{player}", event.player))[:MAX_CHAT_LEN]
