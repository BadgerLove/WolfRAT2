"""Moderator ranks: who may type which chat command.

Pure data + rules, no Qt.  The Mods tab owns one ``ModRoster``; the chat
command gate asks it ``allows(sender, "!ban")``.

Two ranks always exist:

* **Admin** - every command, always (including ones added by later
  releases).  Not editable, so there is always a rank that can do everything.
* **Moderator** - an editable set of commands.  Everyone on a pre-2.6.6 mods
  list lands here.

Owners can add any number of custom ranks ("Super Moderator", "Trial Mod")
and tick which of the built-in commands each one gets.

Saved inside ``wolfrat_mods.json`` next to the plain ``mods`` name list, which
is still written so an older WolfRAT keeps working after a roll-back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

ADMIN = "Admin"
MODERATOR = "Moderator"
MAX_RANK_NAME = 24

# (section, ((permission key, label shown beside the tick-box, commands),))
PERMISSION_GROUPS = (
    ("Match control", (
        ("startvote", "!startvote - start the map vote now", ("!startvote",)),
        ("next", "!next - skip to the next map", ("!next",)),
        ("map", "!map - switch to any map", ("!map",)),
        ("gametime", "!gametime - set the game time", ("!gametime",)),
        ("time", "!time - set the time of day", ("!time",)),
    )),
    ("Weather", (
        ("weather", "Sky commands - !storm !rain !snow !fog !clear ...",
         ("!storm", "!rain", "!drizzle", "!snow", "!blizzard", "!fog",
          "!overcast", "!clear")),
        ("quake", "!quake - earthquake", ("!quake",)),
        ("lightning", "!lightning - flash and thunder", ("!lightning",)),
        ("weather_dynamic", "!weather on / off - the changing weather", ("!weather",)),
    )),
    ("Teams", (
        ("swap", "!swap - move a player to the other team", ("!swap",)),
        ("mixteams", "!mixteams - shuffle everyone", ("!mixteams",)),
        ("balanceteams", "!balanceteams - even the teams up", ("!balanceteams",)),
    )),
    ("Players", (
        ("warn", "!warn - warn a player", ("!warn",)),
        ("kill", "!kill - kill a player", ("!kill",)),
        ("kick", "!kick - kick a player", ("!kick",)),
        ("ban", "!ban - ban a player", ("!ban",)),
    )),
    ("Rotation", (
        ("add", "!add - add a map to the rotation", ("!add",)),
        ("remove", "!remove - remove a map from the rotation", ("!remove",)),
    )),
)

ALL_PERMISSIONS = tuple(
    key for _, perms in PERMISSION_GROUPS for key, _, _ in perms
)
_COMMAND_PERMISSION = {
    command: key
    for _, perms in PERMISSION_GROUPS
    for key, _, commands in perms
    for command in commands
}

# What a Moderator can do out of the box: run the match day to day, but not
# ban, force a map, change the game time or edit the rotation.
DEFAULT_MODERATOR = frozenset({
    "startvote", "next", "weather", "quake", "lightning", "weather_dynamic",
    "swap", "mixteams", "balanceteams", "warn", "kill", "kick",
})


def permission_for(command: str) -> Optional[str]:
    """Permission key guarding a chat command, or None if it is not guarded."""
    return _COMMAND_PERMISSION.get(command.lower())


class RankError(ValueError):
    """A rank edit the owner should be told about, in plain words."""


@dataclass
class Rank:
    name: str
    permissions: frozenset

    @property
    def builtin(self) -> bool:
        return self.name in (ADMIN, MODERATOR)

    @property
    def locked(self) -> bool:
        """Admin always has everything."""
        return self.name == ADMIN


class ModRoster:
    """Ranks, and which rank each moderator holds."""

    def __init__(self) -> None:
        self._ranks: list[Rank] = [
            Rank(ADMIN, frozenset(ALL_PERMISSIONS)),
            Rank(MODERATOR, DEFAULT_MODERATOR),
        ]
        self._members: dict[str, tuple[str, str]] = {}  # lower -> (display, rank)

    # ---- load / save ----

    @classmethod
    def from_config(cls, cfg: Mapping) -> "ModRoster":
        roster = cls()
        for entry in cfg.get("ranks") or ():
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("name", "")).strip()
            if not name or name.casefold() == ADMIN.casefold():
                continue
            permissions = frozenset(
                key for key in entry.get("permissions") or ()
                if key in ALL_PERMISSIONS
            )
            existing = roster._find_rank(name)
            if existing is not None:
                existing.permissions = permissions
            else:
                roster._ranks.append(Rank(name[:MAX_RANK_NAME], permissions))
        assigned = cfg.get("mod_ranks")
        assigned = assigned if isinstance(assigned, Mapping) else {}
        assigned = {str(k).lower(): str(v) for k, v in assigned.items()}
        for display in cfg.get("mods") or ():
            display = str(display).strip()
            if not display:
                continue
            # pre-2.6.6 file, or a name with no/unknown rank: Moderator
            rank = roster._find_rank(assigned.get(display.lower(), ""))
            roster._members[display.lower()] = (
                display, rank.name if rank else MODERATOR
            )
        return roster

    def to_config(self) -> dict:
        """Keys to merge into wolfrat_mods.json."""
        return {
            "mods": sorted(display for display, _ in self._members.values()),
            "mod_ranks": {
                display: rank
                for display, rank in sorted(self._members.values())
            },
            "ranks": [
                {"name": rank.name, "permissions": sorted(rank.permissions)}
                for rank in self._ranks if not rank.locked
            ],
        }

    # ---- people ----

    def names(self) -> dict:
        """{lowercase name: display name} - the shape the tab has always used."""
        return {key: display for key, (display, _) in self._members.items()}

    def members(self) -> list:
        """[(display name, rank name)] sorted by name."""
        return sorted(self._members.values(), key=lambda m: m[0].casefold())

    def add_member(self, name: str, rank: str = MODERATOR) -> bool:
        name = name.strip()
        if not name or name.lower() in self._members:
            return False
        found = self._find_rank(rank)
        self._members[name.lower()] = (name, found.name if found else MODERATOR)
        return True

    def remove_member(self, name: str) -> bool:
        return self._members.pop(name.strip().lower(), None) is not None

    def set_member_rank(self, name: str, rank: str) -> bool:
        key = name.strip().lower()
        found = self._find_rank(rank)
        if key not in self._members or found is None:
            return False
        self._members[key] = (self._members[key][0], found.name)
        return True

    def rank_of(self, name: str) -> Optional[Rank]:
        member = self._members.get(name.strip().lower())
        return self._find_rank(member[1]) if member else None

    def allows(self, name: str, command: str) -> bool:
        """May this moderator type this command?  Unguarded commands: yes."""
        rank = self.rank_of(name)
        if rank is None:
            return False
        key = permission_for(command)
        return key is None or key in rank.permissions

    # ---- ranks ----

    def ranks(self) -> list:
        return list(self._ranks)

    def rank_names(self) -> list:
        return [rank.name for rank in self._ranks]

    def member_count(self, rank: str) -> int:
        found = self._find_rank(rank)
        if found is None:
            return 0
        return sum(1 for _, held in self._members.values() if held == found.name)

    def add_rank(self, name: str, copy_from: str = MODERATOR) -> Rank:
        name = self._checked_new_name(name)
        source = self._find_rank(copy_from)
        rank = Rank(name, source.permissions if source else frozenset())
        self._ranks.append(rank)
        return rank

    def rename_rank(self, old: str, new: str) -> Rank:
        rank = self._require_custom(old)
        if new.strip().casefold() != rank.name.casefold():
            new = self._checked_new_name(new)
        else:
            new = new.strip()
        for key, (display, held) in list(self._members.items()):
            if held == rank.name:
                self._members[key] = (display, new)
        rank.name = new
        return rank

    def delete_rank(self, name: str) -> None:
        rank = self._require_custom(name)
        count = self.member_count(rank.name)
        if count:
            # never move people silently: another rank may hold more power
            raise RankError(
                f"{count} {'person is' if count == 1 else 'people are'} still "
                f"in {rank.name}. Move them to another rank first."
            )
        self._ranks.remove(rank)

    def set_permission(self, rank: str, key: str, allowed: bool) -> None:
        found = self._find_rank(rank)
        if found is None:
            raise RankError(f"No rank called {rank}.")
        if found.locked:
            raise RankError(f"{ADMIN} always has every command.")
        if key not in ALL_PERMISSIONS:
            raise RankError(f"Unknown command permission: {key}")
        permissions = set(found.permissions)
        (permissions.add if allowed else permissions.discard)(key)
        found.permissions = frozenset(permissions)

    def ranks_allowing(self, command: str) -> list:
        """Rank names that may use a command ([] = not a guarded command)."""
        key = permission_for(command)
        if key is None:
            return []
        return [rank.name for rank in self._ranks if key in rank.permissions]

    # ---- internals ----

    def _find_rank(self, name: str) -> Optional[Rank]:
        folded = str(name).strip().casefold()
        return next(
            (rank for rank in self._ranks if rank.name.casefold() == folded), None
        )

    def _require_custom(self, name: str) -> Rank:
        rank = self._find_rank(name)
        if rank is None:
            raise RankError(f"No rank called {name}.")
        if rank.builtin:
            raise RankError(f"{rank.name} is built in and stays.")
        return rank

    def _checked_new_name(self, name: str) -> str:
        name = " ".join(str(name).split())
        if not name:
            raise RankError("Give the rank a name.")
        if len(name) > MAX_RANK_NAME:
            raise RankError(f"Rank names can be up to {MAX_RANK_NAME} characters.")
        if self._find_rank(name) is not None:
            raise RankError(f"There is already a rank called {name}.")
        return name


def commands_for(keys: Iterable[str]) -> list:
    """Chat commands unlocked by a set of permission keys, in menu order."""
    keys = set(keys)
    return [
        command
        for _, perms in PERMISSION_GROUPS
        for key, _, commands in perms if key in keys
        for command in commands
    ]


__all__ = [
    "ADMIN", "ALL_PERMISSIONS", "DEFAULT_MODERATOR", "MAX_RANK_NAME",
    "MODERATOR", "ModRoster", "PERMISSION_GROUPS", "Rank", "RankError",
    "commands_for", "permission_for",
]
