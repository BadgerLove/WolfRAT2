"""Dynamic weather: random fronts that roll in, hold and clear, on their own clock.

No Qt and no game knowledge here - this module only decides *which sky should
be showing right now*.  ``wolfrat.weather`` puts it on the server.

The loop::

    clear spell -> front builds (overcast -> rain -> storm) -> holds at its peak
                -> eases back down the same ladder -> clear spell -> ...

The front's clock never looks at the map.  A map change only means the chosen
sky is written again once the server is back in game, so a storm with five
minutes left opens the next map with five minutes of storm.
"""

from __future__ import annotations

import difflib
import random
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from wolfrat.weather import CLEAR, WIND_MAX, Weather

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TYPES = ("overcast", "drizzle", "rain", "storm", "fog", "snow", "blizzard")

TYPE_LABELS = {
    "overcast": "Overcast", "drizzle": "Drizzle", "rain": "Rain", "storm": "Storm",
    "fog": "Fog", "snow": "Snow", "blizzard": "Blizzard",
}

# What a front passes through on its way to the peak (the peak is the last one).
LADDERS = {
    "overcast": ("overcast",),
    "drizzle": ("overcast", "drizzle"),
    "rain": ("overcast", "drizzle", "rain"),
    "storm": ("overcast", "rain", "storm"),
    "fog": ("fog",),
    "snow": ("overcast", "snow"),
    "blizzard": ("overcast", "snow", "blizzard"),
}

# (precipitation %, overcast %, brings fog, share of the front's wind) at full
# strength.  Wind builds with the front and dies with it; fog comes with still air.
_SHAPES = {
    "overcast": (0, 100, False, 0.5),
    "drizzle": (25, 55, False, 0.55),
    "rain": (60, 80, False, 0.75),
    "storm": (100, 100, True, 1.0),
    "fog": (0, 40, True, 0.15),
    "snow": (50, 70, False, 0.6),
    "blizzard": (100, 100, True, 1.0),
}
_SNOWY = {"snow", "blizzard"}

# clear spell min/max, peak hold min/max - all minutes
FREQUENCIES = {
    "rare": (25, 60, 4, 10),
    "normal": (10, 25, 5, 12),
    "frequent": (4, 10, 5, 12),
    "constant": (1, 2, 6, 15),
}
FREQUENCY_LABELS = {
    "rare": "Rare", "normal": "Normal", "frequent": "Frequent",
    "constant": "Almost always", "custom": "Custom",
}

# "auto" is the default and is never stored: WolfRAT reads the loaded map and
# decides rain or snow itself.  The other three are the admin taking over.
MAP_AUTO, MAP_NONE, MAP_NO_SNOW, MAP_SNOW = "auto", "none", "no_snow", "snow_instead"
MAP_NORMAL = MAP_AUTO          # older name, still used by callers and saved files
MAP_RULE_LABELS = {
    MAP_AUTO: "Auto", MAP_NO_SNOW: "Rain", MAP_SNOW: "Snow", MAP_NONE: "No weather",
}

# Terrains that are snow whatever the map's climate field says (mappers often
# leave that on Desert).  Judged by eye from the 3D previews in the JOTAC Map
# Building Kit terrain browser, 2026-09-21.  DFX55 / DFX58 look pale but sit
# under a sunset tint - left out until someone who knows them confirms.
DEFAULT_SNOW_TERRAINS = ("dfs1", "dfs2", "dfs3", "dfs4", "dfm13", "dfm15", "flatsnow",
                         "dfm9", "dfm6")


def terrain_key(name: str) -> str:
    text = (name or "").strip().lower()
    return text[:-4] if text.endswith(".trn") else text


def is_snow_map(terrain: str, says_snow: bool, snow_terrains) -> bool:
    """The map saying Snow is a trustworthy yes; Desert/Jungle is not a trustworthy no."""
    return bool(says_snow) or terrain_key(terrain) in {terrain_key(t) for t in snow_terrains}


@dataclass
class DynamicConfig:
    enabled: bool = False
    frequency: str = "normal"
    clear_min: int = 10            # minutes - only used when frequency == "custom"
    clear_max: int = 25
    hold_min: int = 5
    hold_max: int = 12
    fade_min: int = 60             # seconds per step of the ladder
    fade_max: int = 150
    weights: dict = field(default_factory=lambda: {
        "overcast": 20, "drizzle": 20, "rain": 25, "storm": 15,
        "fog": 10, "snow": 0, "blizzard": 0,
    })
    fog_min: int = 120             # metres, rolled per foggy front
    fog_max: int = 400
    wind: bool = True              # fronts bring their own wind (cloud speed)
    wind_min: int = 60             # 0-255, rolled per front, the peak's wind
    wind_max: int = 220
    quake_enabled: bool = False
    quake_every: int = 90          # about one every N minutes
    quake_max_seconds: int = 6
    announce: bool = True
    pause_when_empty: bool = True
    lightning: bool = True         # used only when the server has the lightning patch
    map_rules: dict = field(default_factory=dict)   # typed or real map name -> MAP_*
    snow_terrains: list = field(default_factory=lambda: list(DEFAULT_SNOW_TERRAINS))

    def spans(self) -> tuple[int, int, int, int]:
        if self.frequency in FREQUENCIES:
            return FREQUENCIES[self.frequency]
        clear_min = max(0, int(self.clear_min))
        hold_min = max(1, int(self.hold_min))
        return (clear_min, max(clear_min, int(self.clear_max)),
                hold_min, max(hold_min, int(self.hold_max)))

    def to_json(self) -> dict:
        return {
            "enabled": self.enabled, "frequency": self.frequency,
            "clear_min": self.clear_min, "clear_max": self.clear_max,
            "hold_min": self.hold_min, "hold_max": self.hold_max,
            "fade_min": self.fade_min, "fade_max": self.fade_max,
            "weights": dict(self.weights), "fog_min": self.fog_min, "fog_max": self.fog_max,
            "wind": self.wind, "wind_min": self.wind_min, "wind_max": self.wind_max,
            "quake_enabled": self.quake_enabled, "quake_every": self.quake_every,
            "quake_max_seconds": self.quake_max_seconds, "announce": self.announce,
            "pause_when_empty": self.pause_when_empty, "lightning": self.lightning,
            "map_rules": dict(self.map_rules),
            "snow_terrains": list(self.snow_terrains),
        }

    @classmethod
    def from_json(cls, data) -> "DynamicConfig":
        config = cls()
        if not isinstance(data, dict):
            return config
        for key, default in config.to_json().items():
            value = data.get(key, default)
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = default
            elif isinstance(default, dict):
                value = dict(value) if isinstance(value, dict) else default
            elif isinstance(default, list):
                value = ([terrain_key(str(v)) for v in value if str(v).strip()]
                         if isinstance(value, list) else default)
            elif not isinstance(value, str):
                value = default
            setattr(config, key, value)
        if config.frequency not in FREQUENCIES and config.frequency != "custom":
            config.frequency = "normal"
        weights = {}
        for name in TYPES:
            try:
                weights[name] = max(0, int(config.weights.get(name, 0) or 0))
            except (TypeError, ValueError):
                weights[name] = 0
        config.weights = weights
        config.map_rules = {
            str(k): v for k, v in config.map_rules.items() if v in MAP_RULE_LABELS and v != MAP_AUTO
        }
        return config


# ---------------------------------------------------------------------------
# Map names: "cod kill house" finds "TD-COD4_KillHouse.bms"
# ---------------------------------------------------------------------------

_EXTENSION = re.compile(r"\.(bms|npj|mis)$", re.IGNORECASE)
_MODE_PREFIX = re.compile(r"^[a-z]{1,4}\s*[-_]\s*")


def map_key(name: str) -> str:
    """Lower case, no extension, no game-mode prefix, letters and digits only."""
    text = _EXTENSION.sub("", (name or "").strip().lower())
    stripped = _MODE_PREFIX.sub("", text)
    if re.sub(r"[^a-z0-9]", "", stripped):
        text = stripped
    return re.sub(r"[^a-z0-9]", "", text)


def map_rule_for(map_name: Optional[str], rules: dict) -> str:
    """Exact name first, then one name inside the other, then a close spelling."""
    target = map_key(map_name or "")
    if not target or not rules:
        return MAP_NORMAL
    keyed = [(map_key(name), rule) for name, rule in rules.items() if map_key(name)]
    for key, rule in keyed:
        if key == target:
            return rule
    contained = [(len(key), rule) for key, rule in keyed
                 if len(key) >= 4 and (key in target or target in key)]
    if contained:
        return max(contained)[1]
    best, best_rule = 0.0, MAP_NORMAL
    for key, rule in keyed:
        ratio = difflib.SequenceMatcher(None, key, target).ratio()
        if ratio > best:
            best, best_rule = ratio, rule
    return best_rule if best >= 0.82 else MAP_NORMAL


_AS_RAIN = {"snow": "rain", "blizzard": "storm"}
_AS_SNOW = {"drizzle": "snow", "rain": "snow", "storm": "blizzard"}


def kind_on_map(kind: str, rule: str) -> str:
    """What a front of `kind` turns into under this map's rule - a rolled 'blizzard'
    is a storm on a jungle map, and the admin should be told 'storm'."""
    if rule == MAP_NO_SNOW:
        return _AS_RAIN.get(kind, kind)
    if rule == MAP_SNOW:
        return _AS_SNOW.get(kind, kind)
    return kind


def adjust_for_map(sky: Weather, rule: str) -> Weather:
    if rule == MAP_NONE:
        return CLEAR
    if sky.precip_percent > 0:
        if rule == MAP_NO_SNOW and sky.snow:
            return replace(sky, snow=False)
        if rule == MAP_SNOW and not sky.snow:
            return replace(sky, snow=True)
    return sky


# ---------------------------------------------------------------------------
# A front
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    sky: Weather
    seconds: int
    label: str
    peak: bool = False


@dataclass(frozen=True)
class Front:
    kind: str
    strength: float
    stages: tuple

    @property
    def seconds(self) -> int:
        return sum(stage.seconds for stage in self.stages)

    @property
    def snowy(self) -> bool:
        return self.kind in _SNOWY


def _sky(name: str, strength: float, fog_metres: int, fade: int,
         wind: Optional[int] = None) -> Weather:
    precip, overcast, foggy, wind_share = _SHAPES[name]
    return Weather(
        precip_percent=round(precip * strength),
        snow=name in _SNOWY,
        overcast_percent=round(overcast * (0.6 + 0.4 * strength)),
        fog_metres=fog_metres if foggy else None,
        cloud_speed=None if wind is None else round(wind * wind_share),
        fade_seconds=fade,
    )


def build_front(kind: str, config: DynamicConfig, rng: random.Random) -> Front:
    _, _, hold_min, hold_max = config.spans()
    strength = rng.uniform(0.55, 1.0)
    fog_low, fog_high = sorted((max(20, config.fog_min), max(20, config.fog_max)))
    fog = rng.randint(fog_low, fog_high)
    fade_low, fade_high = sorted((max(10, config.fade_min), max(10, config.fade_max)))
    ladder = LADDERS[kind]

    def step(name, seconds=None, peak=False):
        fade = rng.randint(fade_low, fade_high)
        # The fog distance is only ever sent twice per front (in with the peak,
        # out with the clear): the server compares every player's fog with its
        # own, so it should not be a moving target.
        return Stage(_sky(name, strength, fog, fade), seconds or fade + rng.randint(20, 60),
                     TYPE_LABELS[name], peak)

    climb = [step(name) for name in ladder[:-1]]
    peak = step(ladder[-1], rng.randint(hold_min * 60, hold_max * 60), peak=True)
    descent = [step(name) for name in reversed(ladder[:-1])]
    stages = climb + [peak] + descent
    if config.wind:
        # rolled last, so the rest of the front is what it always was
        wind_low, wind_high = sorted((max(0, min(WIND_MAX, config.wind_min)),
                                      max(0, min(WIND_MAX, config.wind_max))))
        wind = rng.randint(wind_low, wind_high)
        stages = [replace(stage, sky=_sky(stage.label.lower(), strength, fog,
                                           stage.sky.fade_seconds, wind))
                  for stage in stages]
    return Front(kind, strength, tuple(stages))


def pick_kind(config: DynamicConfig, rng: random.Random) -> Optional[str]:
    names = [name for name in TYPES if config.weights.get(name, 0) > 0]
    if not names:
        return None
    return rng.choices(names, weights=[config.weights[name] for name in names])[0]


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """What the tab should do this tick."""

    sky: Optional[Weather]          # None = leave the server alone
    announce: str = ""
    quake_seconds: int = 0
    lightning: bool = False
    status: str = ""
    lightning_far: bool = False


class DynamicWeather:
    """Call ``tick`` every few seconds.  Pure: time and dice are injected."""

    def __init__(self, clock: Callable[[], float], rng: Optional[random.Random] = None):
        self._clock = clock
        self._rng = rng or random.Random()
        self._front: Optional[Front] = None
        self._front_started = 0.0
        self._next_front_at: Optional[float] = None
        self._next_kind: Optional[str] = None
        self._announced_stage = -1
        self._next_quake_at: Optional[float] = None
        self._next_flash_at: Optional[float] = None
        self._paused_by = ""
        # True while the sky on the server is one WE put there.  A clear spell is
        # not ours: the map (and any weather script it has) owns the sky then.
        self._hands_on = False
        self._last_map: Optional[str] = None

    # -- inspection ---------------------------------------------------------
    @property
    def front(self) -> Optional[Front]:
        return self._front

    @property
    def hands_on(self) -> bool:
        return self._hands_on

    def reset(self) -> None:
        self._front = None
        self._next_front_at = self._next_kind = None
        self._announced_stage = -1
        self._next_quake_at = self._next_flash_at = None

    def end_front_now(self) -> None:
        """'Clear the weather' during dynamic mode: this front is over."""
        self._hands_on = False         # the caller clears the sky itself
        self._front = None
        self._next_front_at = self._next_kind = None
        self._announced_stage = -1

    def _schedule_next(self, config: DynamicConfig, now: float) -> None:
        clear_min, clear_max, _, _ = config.spans()
        self._next_front_at = now + self._rng.uniform(clear_min * 60, max(clear_min, clear_max) * 60)
        self._next_kind = pick_kind(config, self._rng)

    def _release(self, fade_seconds: int = 90) -> Optional[Weather]:
        """Hand the sky back to the map once, then keep our hands off it."""
        if self._hands_on:
            self._hands_on = False
            return replace(CLEAR, fade_seconds=fade_seconds)
        return None

    def _stage_at(self, now: float) -> tuple[int, Optional[Stage], float]:
        elapsed = now - self._front_started
        for index, stage in enumerate(self._front.stages):
            if elapsed < stage.seconds:
                return index, stage, stage.seconds - elapsed
            elapsed -= stage.seconds
        return -1, None, 0.0

    # -- the tick -----------------------------------------------------------
    def tick(self, config: DynamicConfig, *, map_name: Optional[str], players: int,
             in_game: bool, manual_active: bool, sky_is_dry: bool = True,
             sky_is_snow: bool = False, map_is_snow: Optional[bool] = None) -> Decision:
        now = self._clock()
        if not config.enabled:
            self.reset()
            return Decision(None, status="Dynamic weather is off.")

        if self._next_front_at is None and self._front is None:
            self._schedule_next(config, now)

        # The front's clock keeps running through all of these; only the
        # writing stops.
        if manual_active:
            return Decision(None, status="Paused - manual weather is active.")
        if not in_game:
            return Decision(None, status="Waiting for the server to be in game.")
        if config.pause_when_empty and players <= 0:
            return Decision(None, status="Paused - nobody is on the server.")

        if map_name != self._last_map:
            # A map load puts the map's own sky back by itself - nothing to hand back,
            # and a late "clear" would stamp on the new map's opening weather.
            self._last_map = map_name
            self._hands_on = False
        rule = map_rule_for(map_name, config.map_rules)
        if rule == MAP_AUTO and map_is_snow is not None:
            # Auto: the map decides.  Snow maps get snow, everything else rain.
            rule = MAP_SNOW if map_is_snow else MAP_NO_SNOW
        announce = ""
        quake = 0

        if self._front is None and now >= (self._next_front_at or 0):
            if self._next_kind is None:
                self._schedule_next(config, now)
                return Decision(self._release(), status="No weather types are ticked.")
            wants_snow = self._next_kind in _SNOWY
            if rule == MAP_NO_SNOW:
                wants_snow = False
            elif rule == MAP_SNOW and _SHAPES[self._next_kind][0] > 0:
                wants_snow = True
            if not sky_is_dry and wants_snow != sky_is_snow:
                return Decision(self._release(), status="Waiting for the sky to dry before the next front.")
            self._front = build_front(self._next_kind, config, self._rng)
            self._front_started = now
            self._announced_stage = -1
            self._next_flash_at = None

        if self._front is not None:
            index, stage, left = self._stage_at(now)
            if stage is None:
                self._front = None
                self._announced_stage = -1
                self._schedule_next(config, now)
                if rule != MAP_NONE:
                    announce = "The weather is clearing."
                sky = self._release(self._rng.randint(60, 120))
                status = self._clear_status(now, rule)
            elif rule == MAP_NONE:
                sky = self._release()
                status = ("This map is set to 'No weather' - WolfRAT is leaving its sky alone "
                          f"(a {TYPE_LABELS[self._front.kind].lower()} front is passing by).")
            else:
                sky = adjust_for_map(stage.sky, rule)
                self._hands_on = True
                if index != self._announced_stage:
                    first = self._announced_stage == -1
                    self._announced_stage = index
                    if rule != MAP_NONE and (first or stage.peak):
                        announce = self._announcement(stage, sky, first)
                total_left = int(self._front.seconds - (now - self._front_started))
                status = (f"{TYPE_LABELS[kind_on_map(self._front.kind, rule)]} front: "
                          f"{TYPE_LABELS[kind_on_map(stage.label.lower(), rule)].lower()}"
                          f"{' (peak)' if stage.peak else ''}, "
                          f"{total_left // 60}:{total_left % 60:02d} until clear")
        else:
            sky = self._release()
            status = self._clear_status(now, rule)
            if rule == MAP_NONE:
                status = "This map is set to 'No weather' - WolfRAT is leaving its sky alone."

        lightning = False
        if (config.lightning and self._front is not None and self._front.kind == "storm"
                and rule != MAP_NONE):
            _, stage, _ = self._stage_at(now)
            if stage is not None and stage.peak:
                if self._next_flash_at is None:
                    self._next_flash_at = now + self._rng.uniform(8, 25)
                elif now >= self._next_flash_at:
                    lightning = True
                    self._next_flash_at = now + self._rng.uniform(6, 40)

        if config.quake_enabled and rule != MAP_NONE:
            if self._next_quake_at is None:
                self._next_quake_at = now + self._rng.expovariate(1 / (max(5, config.quake_every) * 60))
            elif now >= self._next_quake_at:
                quake = self._rng.randint(2, max(2, config.quake_max_seconds))
                self._next_quake_at = None
        else:
            self._next_quake_at = None

        return Decision(sky, announce if config.announce else "", quake, lightning, status,
                        lightning_far=lightning and self._rng.random() < 0.4)

    def _clear_status(self, now: float, rule: str = MAP_AUTO) -> str:
        if self._next_front_at is None:
            return "Clear."
        left = max(0, int(self._next_front_at - now))
        if not self._next_kind:
            return "No WolfRAT front right now - nothing is ticked, so none is coming."
        kind = TYPE_LABELS[kind_on_map(self._next_kind, rule)].lower()
        return (f"No WolfRAT front right now - next one in about {max(1, round(left / 60))} min "
                f"({kind} on this map)")

    @staticmethod
    def _announcement(stage: Stage, sky: Weather, first: bool) -> str:
        if first and not stage.peak:
            return "Weather: the sky is clouding over..."
        name = stage.label
        if sky.precip_percent > 0 and sky.snow and name in ("Drizzle", "Rain"):
            name = "Snow"
        elif sky.precip_percent > 0 and sky.snow and name == "Storm":
            name = "Blizzard"
        elif sky.precip_percent > 0 and not sky.snow and name == "Snow":
            name = "Rain"
        elif sky.precip_percent > 0 and not sky.snow and name == "Blizzard":
            name = "Storm"
        return f"Weather: {name.lower()} moving in."
