"""Map weapons: each map comes up with its own weapon list.

A rule is a map name (exact file name, or a part of it such as "western") and
a Yes / Armory / No for every weapon, saved from the server's live table with
"Save for this map". When the server's current map changes, WolfRAT sends
that map's rule; a map with no rule gets every weapon Yes (or is left alone,
if the admin picks that).

Why this works, tested 2026-10-14 on a test host + joined player
(details: WolfRAT vault, Development/Map Weapons.md): WEAPON SET changes the server's table at
once, and the server checks a spawn kit and the armoury against that table.
A map change does NOT reset it, so going back to a normal map needs the
"every weapon Yes" step.

BUT a player's weapon MENU is taken when they load into the map.  On Dale's
server (2026-10-15) a big TAC map kept the admin port silent for 20 s, the
drop + 30 s reconnect made WolfRAT 55 s late, and everyone loaded in with
the old map's list.  So the next map's weapons now go on BEFORE the load:
  * WolfRAT's own switches (!map, the vote, Missions tab, web admin): the
    ServerManager runs ``steps_before_cycle`` just before MISSION CYCLE;
  * a round that ends on its own (timer, objectives, kill limit): the main
    window watches the server's round-over flag and calls ``round_over``
    - the server waits about 20 s on the scoreboard before it loads.
The normal "new map" apply still runs after the load as the check.

Weapons are applied once per map, plus one check a few seconds later. After
that WolfRAT keeps its hands off, so an admin can still hand weapons back
mid-match from the Weapons tab.

Rules are keyed by weapon NAME, not the ADMDEF number, so a rule survives a
mod that renumbers its weapons.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from wolfrat.admin_commands import WeaponEntry, WeaponMode
from wolfrat.weather_dynamic import map_key

NO_RULE_ALL_YES = "all_yes"
NO_RULE_LEAVE = "leave"
NO_RULE_LABELS = {
    NO_RULE_ALL_YES: "Every weapon Yes",
    NO_RULE_LEAVE: "Leave them as they are",
}
MODE_WORD = {WeaponMode.ALWAYS: "Yes", WeaponMode.ARMORY: "Armory", WeaponMode.NEVER: "No"}

RECHECK_SECONDS = 8.0       # second look after an apply, for a server that resets on load
PREPARED_SECONDS = 90.0     # a pre-load set this recent covers the round-over call too
BEFORE_LOAD = "before the next map loads"
# Server memory (same PC): 0 while a round is played, 1 from the moment it ends
# (timer, objectives, kill limit or MISSION CYCLE) until the next map starts.
ROUND_OVER_FLAG = 0x024C1928
RETRY_SECONDS = 3.0
MAX_ATTEMPTS = 3
MAX_APPLIES_PER_MAP = 3   # failures + 'changed back' re-applies, then wait for the next map


@dataclass
class MapRule:
    match: str                                   # map file name, or part of a name
    weapons: dict = field(default_factory=dict)  # weapon name -> "ALWAYS" / "ARMORY" / "NEVER"
    label: str = ""                              # the map's display name when it was saved
    saved: str = ""

    def others(self) -> WeaponMode:
        """The mode for a weapon the rule has never seen (a new mod weapon):
        whatever most of the rule's weapons are."""
        counts = Counter(self.weapons.values())
        if not counts:
            return WeaponMode.ALWAYS
        return WeaponMode(max(sorted(counts), key=lambda word: counts[word]))

    def mode_for(self, weapon_name: str) -> WeaponMode:
        word = _lower_lookup(self.weapons).get(weapon_name.lower())
        return WeaponMode(word) if word else self.others()

    def summary(self) -> str:
        counts = Counter(self.weapons.values())
        return ", ".join(f"{counts[mode.value]} {MODE_WORD[mode]}"
                         for mode in (WeaponMode.ALWAYS, WeaponMode.ARMORY, WeaponMode.NEVER)
                         if counts[mode.value])

    def to_json(self) -> dict:
        return {"match": self.match, "label": self.label, "saved": self.saved,
                "weapons": dict(self.weapons)}

    @classmethod
    def from_json(cls, data: dict) -> Optional["MapRule"]:
        match = str(data.get("match") or "").strip()
        if not map_key(match):
            return None
        weapons = {}
        for name, word in (data.get("weapons") or {}).items():
            word = str(word).upper()
            if word in WeaponMode.__members__:
                weapons[str(name)] = word
        return cls(match=match, weapons=weapons, label=str(data.get("label") or ""),
                   saved=str(data.get("saved") or ""))


def _lower_lookup(weapons: dict) -> dict:
    return {str(name).lower(): word for name, word in weapons.items()}


@dataclass
class MapWeaponsConfig:
    enabled: bool = False          # off until the admin ticks it: it overrides game.cfg weapons
    no_rule: str = NO_RULE_ALL_YES
    rules: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"enabled": self.enabled, "no_rule": self.no_rule,
                "rules": [rule.to_json() for rule in self.rules]}

    @classmethod
    def from_json(cls, data: dict) -> "MapWeaponsConfig":
        config = cls(enabled=bool(data.get("enabled", False)))
        no_rule = data.get("no_rule")
        config.no_rule = no_rule if no_rule in NO_RULE_LABELS else NO_RULE_ALL_YES
        for item in data.get("rules") or ():
            rule = MapRule.from_json(item) if isinstance(item, dict) else None
            if rule is not None:
                config.rules.append(rule)
        return config


def load_config(path: str) -> MapWeaponsConfig:
    try:
        with open(path, encoding="utf-8") as handle:
            return MapWeaponsConfig.from_json(json.load(handle))
    except (OSError, ValueError, TypeError, AttributeError):
        return MapWeaponsConfig()


def save_config(path: str, config: MapWeaponsConfig) -> None:
    """Write the new file first, then swap it in."""
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(config.to_json(), handle, indent=1)
    os.replace(temp, path)


def rule_for(map_name: Optional[str], rules: Sequence[MapRule]) -> Optional[MapRule]:
    """Exact name first, then the longest rule name found inside the map's name
    (or the map's inside the rule's). No "close spelling" guess: the wrong
    guess here would hand a map somebody else's weapons."""
    target = map_key(map_name or "")
    if not target:
        return None
    for rule in rules:
        if map_key(rule.match) == target:
            return rule
    contained = [(len(map_key(rule.match)), index, rule) for index, rule in enumerate(rules)
                 if len(map_key(rule.match)) >= 4
                 and (map_key(rule.match) in target or target in map_key(rule.match))]
    if contained:
        return max(contained, key=lambda item: (item[0], -item[1]))[2]
    return None


def targets(entries: Sequence[WeaponEntry], rule: Optional[MapRule],
            no_rule: str) -> Optional[dict]:
    """ADMDEF id -> wanted mode for every weapon on the server, or None for
    "leave this map alone"."""
    if rule is None:
        if no_rule == NO_RULE_LEAVE:
            return None
        return {entry.admdef_id: WeaponMode.ALWAYS for entry in entries}
    return {entry.admdef_id: rule.mode_for(entry.name) for entry in entries}


@dataclass(frozen=True)
class Step:
    mode: WeaponMode
    entry: Optional[WeaponEntry] = None          # None = WEAPON SET ALL

    def describe(self) -> str:
        what = "every weapon" if self.entry is None else self.entry.name
        return f"{what} -> {MODE_WORD[self.mode]}"


def plan(entries: Sequence[WeaponEntry], wanted: dict) -> list:
    """The fewest commands that turn `entries` into `wanted`: either one SET
    per weapon that is wrong, or SET ALL to the commonest wanted mode then
    the exceptions, whichever is shorter. A table that is already right
    gives no commands at all."""
    wrong = [entry for entry in entries if wanted.get(entry.admdef_id, entry.mode) is not entry.mode]
    if not wrong:
        return []
    counts = Counter(wanted[entry.admdef_id] for entry in entries if entry.admdef_id in wanted)
    base = max(sorted(counts, key=lambda mode: mode.value), key=lambda mode: counts[mode])
    exceptions = [entry for entry in entries
                  if entry.admdef_id in wanted and wanted[entry.admdef_id] is not base]
    if 1 + len(exceptions) < len(wrong):
        return [Step(base)] + [Step(wanted[entry.admdef_id], entry) for entry in exceptions]
    return [Step(wanted[entry.admdef_id], entry) for entry in wrong]


def still_wrong(entries: Sequence[WeaponEntry], wanted: dict) -> list:
    return [entry for entry in entries if wanted.get(entry.admdef_id, entry.mode) is not entry.mode]


def full_plan(entries: Sequence[WeaponEntry], wanted: dict) -> list:
    """Every weapon to its wanted mode WITHOUT trusting the table we hold
    (it can be minutes old): SET ALL to the commonest mode, then the rest."""
    counts = Counter(wanted[entry.admdef_id] for entry in entries if entry.admdef_id in wanted)
    if not counts:
        return []
    base = max(sorted(counts, key=lambda mode: mode.value), key=lambda mode: counts[mode])
    return [Step(base)] + [Step(wanted[entry.admdef_id], entry) for entry in entries
                           if entry.admdef_id in wanted and wanted[entry.admdef_id] is not base]


def _mission_rows(lines) -> list:
    """(file name, current?, next?) for each MISSION LIST line."""
    import re
    rows = []
    for line in lines or ():
        if " - " not in line:
            continue
        name = line.split(" - ")[0].strip()
        if ":" in name[:5]:
            name = name.split(":", 1)[1].strip()
        name = re.sub(r"<[^>]*>", "", name).strip()
        if name:
            rows.append((name, "<CURRENT MISSION>" in line, "<NEXT MISSION>" in line))
    return rows


def next_map_from_missions(lines) -> Optional[str]:
    """The map the server loads next: the <NEXT MISSION> row, else the row
    after <CURRENT MISSION> (wrapping), else None."""
    rows = _mission_rows(lines)
    for name, _current, is_next in rows:
        if is_next:
            return name
    for index, (_name, current, _next) in enumerate(rows):
        if current:
            return rows[(index + 1) % len(rows)][0]
    return None


def current_map_from_missions(lines) -> Optional[str]:
    """The <CURRENT MISSION> file name from MISSION LIST lines (the parse the
    Weather and Sprees tabs use)."""
    import re
    for line in lines or ():
        if "<CURRENT MISSION>" in line:
            current = line.split(" - ")[0].strip()
            if ":" in current[:5]:
                current = current.split(":", 1)[1].strip()
            current = re.sub(r"<[^>]*>", "", current).strip()
            return current or None
    return None


class ServerWeapons:
    """The keeper's three server calls on a ServerManager. They run on the
    keeper's worker thread and WAIT for each future (.result()), so a failed
    or unverified set raises into MapWeaponsKeeper.apply, which retries and
    puts the reason on the Weapons tab's "Last:" line. Nothing is dropped."""

    def __init__(self, server, timeout: float = 20.0) -> None:
        self.server = server
        self.timeout = timeout

    def list_weapons(self) -> Sequence[WeaponEntry]:
        self.server.refresh_weapons(quiet=True).result(timeout=self.timeout)
        return self.server.weapon_entries

    def set_all(self, mode: WeaponMode) -> object:
        return self.server.set_all_weapons(mode).result(timeout=self.timeout)

    def set_one(self, entry: WeaponEntry, mode: WeaponMode) -> object:
        return self.server.set_weapon(entry, mode).result(timeout=self.timeout)


class MapWeaponsKeeper:
    """Watches the current map and applies its weapons, on a worker thread.

    Every server call is injected so the logic tests without a server:
      list_weapons() -> fresh WeaponEntry list (blocking, raises on failure)
      set_all(mode) / set_one(entry, mode)    (blocking, raise on failure)
      log(text)       the WolfRAT log
      status(text)    the "Last:" line on the Weapons tab
    """

    def __init__(self, path: str, *, list_weapons: Callable[[], Sequence[WeaponEntry]],
                 set_all: Callable[[WeaponMode], object],
                 set_one: Callable[[WeaponEntry, WeaponMode], object],
                 log: Callable[[str], None] = lambda _text: None,
                 status: Callable[[str], None] = lambda _text: None,
                 clock: Callable[[], float] = time.monotonic,
                 start_thread: bool = True) -> None:
        self.path = os.fspath(path)
        self.config = load_config(self.path)
        self._list = list_weapons
        self._set_all = set_all
        self._set_one = set_one
        self._log = log
        self._status = status
        self._clock = clock
        self._wake = threading.Condition()
        self._request: Optional[tuple] = None    # (map name, reason)
        self._closed = False
        self.current_map: Optional[str] = None
        self.next_map: Optional[str] = None      # what the server loads next (mission list)
        self.applied_map: Optional[str] = None   # the map the last apply was for
        self._prepared: Optional[tuple] = None   # (map key, clock) of the last pre-load set
        self._recheck_after_connect = False
        self._manual = False                     # the admin changed weapons by hand on this map
        self._applies_this_map = 0
        self.last_status = "Not run yet."
        if start_thread:
            threading.Thread(target=self._run, name="map-weapons", daemon=True).start()

    # ------------------------------------------------------------- settings
    def save(self) -> None:
        save_config(self.path, self.config)

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = bool(enabled)
        self.save()
        if enabled and self.current_map:
            self.applied_map = None
            self._ask(self.current_map, "turned on")

    def set_no_rule(self, choice: str) -> None:
        if choice in NO_RULE_LABELS:
            self.config.no_rule = choice
            self.save()

    def save_rule(self, map_name: str, entries: Sequence[WeaponEntry], label: str = "",
                  match: Optional[str] = None) -> MapRule:
        """Store the live table as the rule for this map (replacing its old one)."""
        match = (match or map_name or "").strip()
        rule = MapRule(match=match, label=label,
                       weapons={entry.name: entry.mode.value for entry in entries},
                       saved=time.strftime("%Y-%m-%d %H:%M"))
        self.config.rules = [old for old in self.config.rules if map_key(old.match) != map_key(match)]
        self.config.rules.append(rule)
        self.save()
        if map_key(match) and map_key(match) == map_key(self.current_map or ""):
            self.applied_map = self.current_map   # it IS what the server has right now
            self._manual = False
        return rule

    def remove_rule(self, match: str) -> None:
        self.config.rules = [rule for rule in self.config.rules if map_key(rule.match) != map_key(match)]
        self.save()

    # ------------------------------------------------------------- triggers
    def on_map(self, map_name: Optional[str]) -> None:
        """Called on every mission poll with the server's current map."""
        if not map_name:
            return
        changed = map_name != self.current_map
        self.current_map = map_name
        if changed:
            self._manual = False
            self._applies_this_map = 0
        if not self.config.enabled:
            return
        if map_name != self.applied_map:
            self._ask(map_name, "new map")
        elif self._recheck_after_connect and not self._manual:
            self._ask(map_name, "reconnected")
        self._recheck_after_connect = False

    def on_next(self, map_name: Optional[str]) -> None:
        """Called on every mission poll with the map the server loads next."""
        self.next_map = map_name or None

    def steps_before_cycle(self, next_map: Optional[str], entries: Sequence[WeaponEntry]) -> list:
        """WolfRAT is about to send MISSION CYCLE: the next map's weapons as
        (mode, entry or None for SET ALL) pairs, sent first in the same queue.
        Nothing when Map Weapons is off, the map is unknown, or "leave alone"."""
        next_map = next_map or self.next_map
        if not self.config.enabled or not next_map or not entries:
            return []
        rule = rule_for(next_map, self.config.rules)
        wanted = targets(entries, rule, self.config.no_rule)
        if wanted is None:
            return []
        steps = full_plan(entries, wanted)
        self._prepared = (map_key(next_map), self._clock())
        name = f"rule '{rule.match}'" if rule else "no rule"
        self._say(f"{next_map}: {name} set before the switch ({len(steps)} command(s)).")
        return [(step.mode, step.entry) for step in steps]

    def round_over(self) -> None:
        """The server's round just ended (it loads the next map in ~20 s):
        put the next map's weapons on now, so players load in with them."""
        if not self.config.enabled or not self.next_map:
            return
        recent = self._prepared
        if recent and recent[0] == map_key(self.next_map) and \
                self._clock() - recent[1] < PREPARED_SECONDS:
            return                                # WolfRAT's own switch already did it
        self._prepared = (map_key(self.next_map), self._clock())
        self._ask(self.next_map, BEFORE_LOAD)

    def on_connected(self) -> None:
        """A (re)connect: the next mission poll checks the weapons again, in
        case the server put its own back while it loaded."""
        self._recheck_after_connect = True

    def note_manual_change(self) -> None:
        """The admin set weapons by hand: leave this map alone until it changes."""
        self._manual = True

    def apply_now(self) -> None:
        if self.current_map:
            self._manual = False
            self._applies_this_map = 0
            self._ask(self.current_map, "Apply now")
        else:
            self._say("No current map yet - connect to the server first.")

    def close(self) -> None:
        with self._wake:
            self._closed = True
            self._wake.notify_all()

    # ------------------------------------------------------------- worker
    def _ask(self, map_name: str, reason: str) -> None:
        with self._wake:
            self._request = (map_name, reason)
            self._wake.notify_all()

    def _take(self, timeout: Optional[float] = None) -> Optional[tuple]:
        with self._wake:
            if self._request is None and not self._closed:
                self._wake.wait(timeout)
            request, self._request = self._request, None
            return request

    def _run(self) -> None:
        while not self._closed:
            request = self._take()
            while request is not None and not self._closed:
                request = self.apply(*request)

    def apply(self, map_name: str, reason: str) -> Optional[tuple]:
        """One map's apply + its later check. Returns a newer request that
        arrived meanwhile (the worker runs it next), else None."""
        started = self._clock()
        rule = rule_for(map_name, self.config.rules)
        name = f"rule '{rule.match}'" if rule else "no rule"
        early = reason == BEFORE_LOAD             # for the NEXT map: the after-load apply still checks it
        if not early:
            self.applied_map = map_name
            self._applies_this_map += 1
        wanted, done = None, []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                entries = list(self._list())
                if not entries:
                    raise RuntimeError("the server sent an empty weapon list")
                wanted = targets(entries, rule, self.config.no_rule)
                if wanted is None:
                    self._say(f"{map_name}: no rule - weapons left as they are ({reason}).")
                    return None
                steps = plan(entries, wanted)
                for step in steps:
                    if step.entry is None:
                        self._set_all(step.mode)
                    else:
                        self._set_one(step.entry, step.mode)
                    done.append(step)
                wrong = still_wrong(list(self._list()), wanted) if steps else []
                if not wrong:
                    break
                problem = f"{len(wrong)} weapon(s) did not take: " + ", ".join(e.name for e in wrong[:4])
            except Exception as error:  # noqa: BLE001 - every failure goes on the status line
                problem = f"{type(error).__name__}: {error}"
            if attempt == MAX_ATTEMPTS:
                self._say(f"{map_name}: {name} FAILED after {attempt} tries - {problem}")
                if not early and self._applies_this_map < MAX_APPLIES_PER_MAP:
                    self.applied_map = None          # the next mission poll tries again
                return None
            newer = self._take(RETRY_SECONDS)
            if newer is not None:
                return newer
        took = self._clock() - started
        counts = Counter(wanted.values())
        mix = ", ".join(f"{counts[mode]} {MODE_WORD[mode]}"
                        for mode in (WeaponMode.ALWAYS, WeaponMode.ARMORY, WeaponMode.NEVER) if counts[mode])
        if done:
            self._say(f"{map_name}: {name} set ({mix}) with {len(done)} command(s) in {took:.1f} s ({reason}).")
        else:
            self._say(f"{map_name}: {name} - already right ({mix}) ({reason}).")
        if early:
            return None
        return self._recheck(map_name, wanted, name)

    def _recheck(self, map_name: str, wanted: dict, name: str) -> Optional[tuple]:
        """One look a few seconds later; a server that reloads its own weapon
        settings during the load would show up here."""
        newer = self._take(RECHECK_SECONDS)
        if newer is not None or self._closed:
            return newer
        if self._manual or map_name != self.current_map:
            return None
        try:
            wrong = still_wrong(list(self._list()), wanted)
        except Exception:  # noqa: BLE001 - the next poll or map change tries again
            return None
        if wrong and self._applies_this_map >= MAX_APPLIES_PER_MAP:
            self._say(f"{map_name}: the server keeps changing {len(wrong)} weapon(s) back - "
                      f"stopped trying until the next map (Apply now tries again).")
            return None
        if wrong:
            self._log(f"[MAP WEAPONS] {map_name}: {len(wrong)} weapon(s) changed back after the load - setting again")
            return (map_name, "changed back after the load")
        return None

    def _say(self, text: str) -> None:
        self.last_status = text
        self._log(f"[MAP WEAPONS] {text}")
        self._status(text)
