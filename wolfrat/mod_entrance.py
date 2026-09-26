"""A little fanfare when one of the server's moderators joins.

Pure decision logic, kept free of Qt (same idea as vote_rules.py): the tab
feeds in the player list every time it refreshes, and gets back the entrances
that are due - a chat line and whether to crack the lightning.

The rules, in the order they matter:

* Only names on the Mods list get an entrance, and only when the feature is on.
* A moderator must have been on the list for `delay_seconds` before anything
  is announced - they are still on the loading screen when their name first
  appears, and the thunder is for them as much as for everyone else.
* One entrance per moderator per `cooldown_minutes`. A shaky connection that
  drops and rejoins five times gets one fanfare, not five. The cooldown is
  remembered on disk by the caller (`ModEntrance.last_announced`), so
  restarting WolfRAT does not re-arm it.
* A map change is not a join. The admin connection drops during a map load and
  the player list comes back empty, then refills; `note_disconnected()` tells
  us the list cannot be trusted, and anybody who was present before the gap is
  treated as still present when the list returns.
* A moderator who leaves before the delay is up gets nothing (and keeps their
  cooldown unspent).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from wolfrat import chat_markup
from wolfrat.admin_commands import MAX_CHAT_LEN, MAX_CHAT_TOKENS, chat_limit, extended_chat

CHAT_MAX_LEN = MAX_CHAT_LEN   # 59: JO cuts every line there

DEFAULT_LINES = (
    "Please welcome {player}, your server moderator!",
    "Behave yourselves. {player} just walked in.",
    "Storm warning: {player} has arrived.",
    "The sky answers to {player}.",
    "All rise for {player}.",
    "{player} is watching. Play nice.",
)

# How long a name may be missing from the list and still count as "never
# left" once the admin connection has dropped (a heavy map can take minutes).
MAP_CHANGE_GRACE_SECONDS = 300


@dataclass
class EntranceConfig:
    enabled: bool = False
    lightning: bool = True
    cooldown_minutes: int = 30
    delay_seconds: int = 25
    lines: list = field(default_factory=lambda: list(DEFAULT_LINES))

    @classmethod
    def from_dict(cls, data) -> "EntranceConfig":
        data = data if isinstance(data, dict) else {}
        cfg = cls()
        cfg.enabled = bool(data.get("enabled", cfg.enabled))
        cfg.lightning = bool(data.get("lightning", cfg.lightning))
        cfg.cooldown_minutes = _clamp_int(data.get("cooldown_minutes"), 0, 24 * 60, cfg.cooldown_minutes)
        cfg.delay_seconds = _clamp_int(data.get("delay_seconds"), 0, 300, cfg.delay_seconds)
        lines = data.get("lines")
        if isinstance(lines, list):
            cleaned = [str(line).strip() for line in lines if str(line).strip()]
            if cleaned:
                cfg.lines = cleaned
        return cfg

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "lightning": self.lightning,
            "cooldown_minutes": self.cooldown_minutes,
            "delay_seconds": self.delay_seconds,
            "lines": list(self.lines),
        }


@dataclass(frozen=True)
class Entrance:
    """One fanfare that is due right now."""
    player: str
    message: str
    lightning: bool


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def render_line(template: str, player: str, limit: Optional[int] = None) -> str:
    """Fill in {player} and make the result fit one chat line.

    The name is never cut - a clipped name reads like a different player.
    If the line is too long with this name, fall back to the shortest honest
    form rather than send half a sentence.  The limit follows the Chat tab's
    long-chat tick box (59, or 118 on a server with the patch).
    """
    limit = chat_limit() if limit is None else limit
    player = chat_markup.safe_name(player)
    text = template.replace("{player}", player).strip()
    if len(text) <= limit and len(text.split()) <= MAX_CHAT_TOKENS:
        return text
    fallback = f"Welcome {player}!"
    if len(fallback) <= limit:
        return fallback
    return player[:limit]


def line_problem(template: str, longest_name: int = 16, limit: Optional[int] = None) -> Optional[str]:
    """Plain-words warning for the settings page, or None if the line is fine."""
    limit = chat_limit() if limit is None else limit
    template = template.strip()
    if not template:
        return "Empty line."
    if "{player}" not in template:
        return "No {player} in this line, so nobody will know who joined."
    bad_tag = chat_markup.tag_problem(template.replace("{player}", ""))
    if bad_tag:
        return bad_tag
    worst = len(template.replace("{player}", "x" * longest_name))
    if worst > limit:
        hint = ("" if extended_chat() else
                " If your server has the long chat patch, tick it on the Chat tab.")
        return (f"Too long for one chat line with a {longest_name}-letter name "
                f"({worst} of {limit}, colour codes count); a short welcome will be "
                f"sent instead.{hint}")
    if len(template.split()) > MAX_CHAT_TOKENS:
        return (f"{len(template.split())} words - the server takes {MAX_CHAT_TOKENS} "
                "at most; a short welcome will be sent instead.")
    if not extended_chat() and chat_markup.TAG_RE.search(template):
        return ("Colour codes only show on a server with the long chat patch "
                "(tick it on the Chat tab); here they are taken out.")
    return None


class ModEntrance:
    """Feed it player lists; it says when a moderator's entrance is due."""

    def __init__(self, config: Optional[EntranceConfig] = None,
                 last_announced: Optional[dict] = None,
                 clock: Optional[Callable[[], float]] = None,
                 rng: Optional[random.Random] = None):
        import time
        self.config = config or EntranceConfig()
        # lowercase name -> unix time of the last fanfare (persist this)
        self.last_announced = dict(last_announced or {})
        self._clock = clock or time.time
        self._rng = rng or random.Random()
        self._present = {}      # lowercase name -> first seen (this visit)
        self._pending = {}      # lowercase name -> (display name, due time)
        self._missing = {}      # lowercase name -> when it dropped off the list
        self._gap = False       # connection dropped and no real list seen since
        self._last_gap = None   # when the admin connection last dropped
        self._primed = False    # have we seen one trustworthy list yet?
        self._last_line = None

    # -- inputs ---------------------------------------------------------

    def note_disconnected(self) -> None:
        """The admin connection dropped (map load, network). The next list
        will be empty or partial; do not read departures or joins into it."""
        self._gap = True
        self._last_gap = self._clock()

    def update(self, player_names: Iterable[str], mods: Iterable[str]) -> list:
        """Give the current player list and the Mods list; returns the
        `Entrance`s that are due now (usually none)."""
        now = self._clock()
        mods_lower = {str(m).strip().lower() for m in mods if str(m).strip()}
        current = {}
        for name in player_names:
            name = str(name or "").strip()
            if name:
                current.setdefault(name.lower(), name)

        # WolfRAT was started (or connected) with people already playing:
        # they did not just join, so they get no fanfare.
        first_list = not self._primed
        if first_list and not current and self._gap:
            return []
        self._primed = True

        for key in list(self._present):
            if key not in current:
                self._missing.setdefault(key, now)
                del self._present[key]

        for key, display in current.items():
            if key in self._present:
                continue
            went = self._missing.pop(key, None)
            self._present[key] = now
            if first_list:
                continue
            if went is not None and self._blinked(went, now):
                # same visit, the list just blinked; an entrance that was
                # still owed waits for them to load in again
                if key in self._pending:
                    self._pending[key] = (display, now + self.config.delay_seconds)
                continue
            if key in mods_lower and self.config.enabled and self._cooled_down(key, now):
                self._pending[key] = (display, now + self.config.delay_seconds)

        if current:
            self._gap = False
        for key in [k for k, t in self._missing.items() if now - t > MAP_CHANGE_GRACE_SECONDS]:
            del self._missing[key]

        due = []
        for key, (display, when) in list(self._pending.items()):
            if now < when:
                continue
            if key not in current:
                # gone when their moment came: wait out a map load, otherwise
                # they left before the entrance and nothing is owed
                went = self._missing.get(key)
                if went is None or not self._blinked(went, now):
                    del self._pending[key]
                continue
            del self._pending[key]
            if not self.config.enabled or key not in mods_lower or not self._cooled_down(key, now):
                continue
            self.last_announced[key] = now
            due.append(Entrance(display, self._pick_line(display), self.config.lightning))
        return due

    # -- helpers --------------------------------------------------------

    def _blinked(self, went: float, now: float) -> bool:
        """Did this name vanish because the connection dropped (map load),
        rather than because the player left? Players trickle back over
        several refreshes, so this is judged per name, by time."""
        if self._last_gap is None or now - went > MAP_CHANGE_GRACE_SECONDS:
            return False
        # the list can blank a refresh before or after we hear of the drop
        return abs(self._last_gap - went) <= 60

    def _cooled_down(self, key: str, now: float) -> bool:
        last = self.last_announced.get(key)
        if last is None:
            return True
        return now - last >= self.config.cooldown_minutes * 60

    def _pick_line(self, player: str) -> str:
        lines = [l for l in self.config.lines if l.strip()] or list(DEFAULT_LINES)
        choices = [l for l in lines if l != self._last_line] or lines
        template = self._rng.choice(choices)
        self._last_line = template
        return render_line(template, player)

    def minutes_until_ready(self, name: str) -> int:
        """For the settings page: 0 = this moderator's next join is announced."""
        last = self.last_announced.get(str(name).strip().lower())
        if last is None:
            return 0
        left = self.config.cooldown_minutes * 60 - (self._clock() - last)
        return max(0, int((left + 59) // 60))

    def prune(self, keep_days: int = 7) -> None:
        """Forget cooldown stamps too old to matter (keeps the file small)."""
        cutoff = self._clock() - keep_days * 86400
        self.last_announced = {k: t for k, t in self.last_announced.items() if t >= cutoff}
