"""Timed steps for a map, counted back from its end (Dale, 2026-10-31).

"With 20 minutes left bring in overcast, with 15 drizzle, with 10 thick fog
and a grey sky."  Each map can keep a list of steps; each step can change the
weather, the sky colours, or both.

The clock is the server's own GameTime, which comes in whole minutes: "10
left" is shown from 10:59 down to 10:00, so a step at 10 fires at 10:59.

No Qt and no game knowledge here.  The Weather tab puts the weather on and
the Sky page puts the colours on; this module only says which step is due.

Steps are a state, not a list of one-off events: at any moment the weather
that should be showing is the one from the latest step that has come due.
So a WolfRAT started (or restarted) with 7 minutes left puts the 10-minute
step on at once, and a step is never sent twice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from wolfrat import weather
from wolfrat import weather_dynamic as dyn

# "" = leave it alone.  "clear" = give the map its own weather back.
WEATHER_CHOICES = ("", "overcast", "drizzle", "rain", "storm", "fog", "snow", "blizzard", "clear")
WEATHER_LABELS = {
    "": "-", "overcast": "Overcast", "drizzle": "Drizzle", "rain": "Rain", "storm": "Storm",
    "fog": "Fog", "snow": "Snow", "blizzard": "Blizzard", "clear": "Map's own weather",
}
SKY_OFF = "off"                 # the map's own sky colours
FOG_MIN_METRES, FOG_MAX_METRES = 20, 1500
MAX_MINUTES = weather.MAX_MINUTES


@dataclass(frozen=True)
class TimedStep:
    minutes_left: int                    # fires when the server says this many minutes are left
    weather: str = ""                    # WEATHER_CHOICES
    fog_metres: Optional[int] = None     # with a weather: this fog instead of the preset's
    sky: str = ""                        # "" = leave the sky, a scene key, or SKY_OFF

    def is_empty(self) -> bool:
        return not self.weather and not self.sky

    def to_json(self) -> dict:
        data = {"at": self.minutes_left}
        if self.weather:
            data["weather"] = self.weather
        if self.fog_metres is not None:
            data["fog"] = self.fog_metres
        if self.sky:
            data["sky"] = self.sky
        return data

    @classmethod
    def from_json(cls, data) -> Optional["TimedStep"]:
        if not isinstance(data, dict):
            return None
        try:
            minutes = int(data.get("at"))
        except (TypeError, ValueError):
            return None
        kind = str(data.get("weather") or "")
        if kind not in WEATHER_CHOICES:
            kind = ""
        fog = data.get("fog")
        try:
            fog = None if fog is None else clamp_fog(int(fog))
        except (TypeError, ValueError):
            fog = None
        step = cls(max(0, min(MAX_MINUTES, minutes)), kind, fog, str(data.get("sky") or ""))
        return None if step.is_empty() else step

    def weather_target(self) -> Optional[weather.Weather]:
        """The weather this step puts on; None when it leaves the weather alone
        or hands the map its own back (see ``hands_back``)."""
        if not self.weather or self.weather == "clear":
            return None
        target = weather.PRESETS[self.weather]
        if self.fog_metres is not None:
            target = replace(target, fog_metres=self.fog_metres)
        return target

    @property
    def hands_back(self) -> bool:
        return self.weather == "clear"

    def describe_weather(self) -> str:
        if not self.weather:
            return ""
        text = WEATHER_LABELS[self.weather]
        if self.fog_metres is not None and self.weather != "clear":
            text += f", fog {self.fog_metres} m"
        return text


def clamp_fog(metres: int) -> int:
    return max(FOG_MIN_METRES, min(FOG_MAX_METRES, int(metres)))


def sort_steps(steps) -> list:
    """Earliest first: the most minutes left at the top (20, 15, 10...)."""
    return sorted(steps, key=lambda s: -s.minutes_left)


def steps_for(map_name: Optional[str], timelines: dict) -> list:
    """The steps kept for this map, matched the same way as the other map
    rules (exact name, then one inside the other, then a close spelling)."""
    if not map_name or not timelines:
        return []
    key = dyn.map_rule_for(map_name, {name: name for name in timelines})
    return list(timelines.get(key) or ())


def due(steps, minutes_left: Optional[int]) -> tuple:
    """(weather step, sky step): the latest step that has come due for each,
    None when nothing has yet.  Two steps at the same minute: the lower one
    in the list wins."""
    if minutes_left is None:
        return None, None
    weather_step = sky_step = None
    for step in sort_steps(steps):             # stable: same-minute steps keep their order
        if minutes_left > step.minutes_left:
            continue
        if step.weather:
            weather_step = step
        if step.sky:
            sky_step = step
    return weather_step, sky_step


def timelines_from_json(data) -> dict:
    out = {}
    if not isinstance(data, dict):
        return out
    for name, rows in data.items():
        steps = [s for s in (TimedStep.from_json(r) for r in (rows or ())) if s is not None]
        if str(name).strip() and steps:
            out[str(name)] = sort_steps(steps)
    return out


def timelines_to_json(timelines: dict) -> dict:
    return {name: [s.to_json() for s in sort_steps(steps)]
            for name, steps in timelines.items() if steps}


@dataclass(frozen=True)
class Event:
    """What ``Runner.tick`` wants done.  ``restarted``: a new map or a new
    round of the same map - anything the timeline put on is gone with the load."""
    step: Optional[TimedStep] = None
    restarted: bool = False


class Runner:
    """Follows one kind of step (``which`` = "weather" or "sky") through a map."""

    def __init__(self, which: str):
        if which not in ("weather", "sky"):
            raise ValueError(which)
        self._which = which
        self.map_name: Optional[str] = None
        self.applied: Optional[TimedStep] = None
        self.stopped = False                     # a mod's !clear / the admin's Clear: done for this map
        self._load = 0

    def stop_for_this_map(self) -> None:
        self.stopped = True

    def tick(self, *, map_name: Optional[str], minutes_left: Optional[int], steps,
             settled: bool, load: int = 0) -> Event:
        """``load``: MapWatch.loads, which goes up when the same map loads again."""
        restarted = False
        if map_name != self.map_name:
            restarted = self.map_name is not None
            self.map_name = map_name
            self.applied, self.stopped = None, False
        elif load != self._load:
            restarted = True
            self.applied, self.stopped = None, False
        self._load = load
        if not settled or self.stopped or minutes_left is None:
            return Event(restarted=restarted)
        weather_step, sky_step = due(steps, minutes_left)
        step = weather_step if self._which == "weather" else sky_step
        if step is None or step == self.applied:
            return Event(restarted=restarted)
        self.applied = step
        return Event(step=step, restarted=restarted)


class MapClock:
    """Minutes left as the timed steps and the last-minute sky may use them.

    A co-op map has no timer, but the server still says "0/30" all game,
    which read as the last minute: every !sky was undone in the same second
    and every timed step came due at once (Dale, 2026-11-01).  So a 0 only
    counts once this map's clock has been seen above 0; until then the time
    is unknown (None).  A reading from the first ``GRACE`` seconds of a map may
    still be the old map's, so it doesn't count as seen."""

    GRACE = 15.0

    def __init__(self):
        self.map_name: Optional[str] = None
        self._map_at: Optional[float] = None
        self._running = False

    def minutes_left(self, *, map_name: Optional[str], remaining: Optional[int],
                     read_at: float, now: float) -> Optional[int]:
        if map_name != self.map_name:
            self.map_name, self._map_at, self._running = map_name, now, False
        if remaining is None or read_at < self._map_at:
            return None                 # a reading from before this map says nothing about it
        if remaining > 0 and read_at >= self._map_at + self.GRACE:
            self._running = True
        if remaining <= 0 and not self._running:
            return None
        return remaining


class MapWatch:
    """Which map the server really has loaded, from its own memory.

    The admin list only names a new map once the load is over, and the game's
    clock can arrive first: on 2026-11-01 the old map's sky went onto the next
    map every time because the two disagreed.  The server's own map name changes
    when the next map starts and its round-over latch goes up when a round ends
    (timer, objectives, kill limit or MISSION CYCLE) and down at the next
    Game_StartMission, so nothing here has to be guessed from the clock.

    ``loads`` goes up once per map load, the same map loading again included."""

    def __init__(self):
        self.name: Optional[str] = None
        self.round_over = False
        self.loads = 0
        self._counted = False           # this round's end has had its load counted

    def update(self, name: Optional[str], round_over: bool) -> None:
        changed = bool(name) and self.name is not None and name != self.name
        same_again = self.round_over and not round_over and not self._counted
        if changed or same_again:
            self.loads += 1
        if name:
            self.name = name
        self._counted = round_over and (self._counted or changed)
        self.round_over = round_over


__all__ = [
    "Event", "MapClock", "MapWatch", "Runner", "SKY_OFF", "TimedStep", "WEATHER_CHOICES", "WEATHER_LABELS", "clamp_fog",
    "due", "sort_steps", "steps_for", "timelines_from_json", "timelines_to_json",
]
