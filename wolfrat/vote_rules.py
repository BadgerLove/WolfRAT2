"""When should the end-of-match map vote start?

Pure decision logic for the Map Voting tab, kept free of Qt so every rule can
be replayed against whole simulated matches in tests.

The original rule - start the vote N minutes before the timer runs out - is
always evaluated first and is never changed by anything in here. The extra
rules exist because some game modes end on a score, not on the clock: a team
deathmatch that reaches its kill limit at minute 25 of 40 never gets to
"3 minutes before the end", so no vote ever starts. Every extra rule is off
until an admin switches it on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

# Game-mode families, read from the leading letters of the map FILENAME
# (TD-AdaleCity.bms, TDH_C1A.BMS and TDM-Spanaird.bms are all team deathmatch).
MODE_AS = "as"
MODE_TDM = "tdm"
MODE_DM = "dm"
MODE_TKOTH = "tkoth"
MODE_CTF = "ctf"
MODE_FB = "fb"
MODE_OTHER = "other"

# Longest prefix first: "CTF" must win over a future "CT", "AAS" over "AS".
_PREFIX_FAMILIES = (
    ("CTF", MODE_CTF),
    ("AAS", MODE_AS),
    ("AS", MODE_AS),
    ("TD", MODE_TDM),
    ("TK", MODE_TKOTH),
    ("DM", MODE_DM),
    ("FB", MODE_FB),
)

MODE_LABELS = {
    MODE_AS: "Advance & Secure",
    MODE_TDM: "Team Deathmatch",
    MODE_DM: "Deathmatch",
    MODE_TKOTH: "Team King of the Hill",
    MODE_CTF: "Capture the Flag",
    MODE_FB: "Flagball",
    MODE_OTHER: "Other modes",
}

# Modes that can end on a score before the clock does, in display order.
SCORE_MODES = (MODE_TDM, MODE_DM, MODE_TKOTH, MODE_CTF, MODE_FB, MODE_OTHER)
# Modes whose score is the kill count the admin port reports per player.
KILL_MODES = (MODE_TDM, MODE_DM)
# Modes won on captures, read from the server on this PC (jo_players.caps_to_go).
CAPS_MODES = (MODE_CTF, MODE_FB)
CAPS_WORD = {MODE_CTF: "flags", MODE_FB: "goals"}


def caps_words(mode: str, count: int) -> str:
    """'1 flag', '2 flags', '1 goal'..."""
    word = CAPS_WORD[mode]
    return f"{count} {word[:-1] if count == 1 else word}"

DEFAULT_MINUTES_IN = 10
DEFAULT_KILLS_BEFORE_LIMIT = 15
DEFAULT_CAPS_BEFORE_WIN = 1

# The server's own game type (g_GameType, coop_guard.GAME_TYPES) -> family.
# 0 (Deathmatch) is left to the filename: it is also what an unloaded server reads.
_GAME_TYPE_FAMILIES = {
    0x10000: MODE_TDM,
    0x10001: MODE_TKOTH,
    0x10004: MODE_CTF,
    0x10008: MODE_FB,
    0x10010: MODE_AS,
    0x50010: MODE_AS,
}


def family_from_game_type(game_type) -> str | None:
    """The mode family the server itself reports, or None to fall back to the filename."""
    return _GAME_TYPE_FAMILIES.get(game_type) if game_type is not None else None


def game_mode(filename: str | None) -> str:
    """Game-mode family of a map filename; MODE_OTHER when unrecognised."""
    match = re.match(r"\s*([A-Za-z]+)", filename or "")
    if not match:
        return MODE_OTHER
    letters = match.group(1).upper()
    # A bare word such as DOSLIN.BMS or Damai_p1.bms is a map name, not a mode
    # code. Real mode codes are followed by a separator or a digit/variant
    # letter block of at most four letters (TD-, TDH_, AAS-).
    if len(letters) > 4:
        return MODE_OTHER
    for prefix, family in _PREFIX_FAMILIES:
        if letters.startswith(prefix):
            return family
    return MODE_OTHER


@dataclass(frozen=True)
class ModeRule:
    """The optional extra triggers for one game-mode family."""

    minutes_in_enabled: bool = False
    minutes_in: int = DEFAULT_MINUTES_IN
    kill_watch_enabled: bool = False
    kills_before_limit: int = DEFAULT_KILLS_BEFORE_LIMIT
    caps_watch_enabled: bool = False
    caps_before_win: int = DEFAULT_CAPS_BEFORE_WIN

    @classmethod
    def from_json(cls, data) -> "ModeRule":
        if not isinstance(data, Mapping):
            return cls()

        def number(key, default, low, high):
            try:
                return max(low, min(high, int(data.get(key, default))))
            except (TypeError, ValueError):
                return default

        return cls(
            minutes_in_enabled=bool(data.get("minutes_in_enabled", False)),
            minutes_in=number("minutes_in", DEFAULT_MINUTES_IN, 1, 240),
            kill_watch_enabled=bool(data.get("kill_watch_enabled", False)),
            kills_before_limit=number(
                "kills_before_limit", DEFAULT_KILLS_BEFORE_LIMIT, 1, 500
            ),
            caps_watch_enabled=bool(data.get("caps_watch_enabled", False)),
            caps_before_win=number("caps_before_win", DEFAULT_CAPS_BEFORE_WIN, 1, 50),
        )

    def to_json(self) -> dict:
        return {
            "minutes_in_enabled": self.minutes_in_enabled,
            "minutes_in": self.minutes_in,
            "kill_watch_enabled": self.kill_watch_enabled,
            "kills_before_limit": self.kills_before_limit,
            "caps_watch_enabled": self.caps_watch_enabled,
            "caps_before_win": self.caps_before_win,
        }


def rules_from_json(data) -> dict[str, ModeRule]:
    """Every score mode gets a rule; anything missing or malformed is 'off'."""
    source = data if isinstance(data, Mapping) else {}
    return {mode: ModeRule.from_json(source.get(mode)) for mode in SCORE_MODES}


def rules_to_json(rules: Mapping[str, ModeRule]) -> dict:
    return {mode: rules.get(mode, ModeRule()).to_json() for mode in SCORE_MODES}


def leading_kills(mode: str, players: Iterable) -> int | None:
    """The kill count the server compares with KillLimit, as best we can see it.

    Team deathmatch: the larger team's summed kills. Deathmatch: the top
    player. Players who left took their kills out of the list, so this can
    read LOW, never high - which is why the watch fires a margin early.
    """
    if mode not in KILL_MODES:
        return None
    teams: dict[int, int] = {}
    best = None
    for player in players:
        if getattr(player, "server_id", None) == 0:
            continue  # the host row is not a player
        kills = getattr(player, "kills", None)
        if kills is None:
            continue
        try:
            kills = int(kills)
        except (TypeError, ValueError):
            continue
        if mode == MODE_DM:
            best = kills if best is None else max(best, kills)
        else:
            team = getattr(player, "team", None)
            teams[team] = teams.get(team, 0) + kills
    if mode == MODE_DM:
        return best
    return max(teams.values()) if teams else None


def kill_limit(settings) -> int | None:
    """KillLimit from the server's game settings, or None when unusable."""
    if settings is None:
        return None
    items = settings.items() if hasattr(settings, "items") else ()
    for key, value in items:
        if str(key).casefold() == "killlimit":
            try:
                limit = int(float(str(value).strip()))
            except (TypeError, ValueError):
                return None
            return limit if limit > 0 else None
    return None


@dataclass
class KillWatch:
    """Per-map state for the kill watch.

    Right after a map change the player list can still show the PREVIOUS map's
    kills for a poll or two. If that map ended on the kill limit, those stale
    numbers would start a vote in the first seconds of the new map. So the
    watch only arms once it has seen the count sitting safely below the
    trigger point on this map.
    """

    armed: bool = False

    def reset(self) -> None:
        self.armed = False


@dataclass(frozen=True)
class Decision:
    fire: bool
    reason: str = ""      # why the vote starts - logged and shown
    status: str = ""      # status-line text while waiting


@dataclass(frozen=True)
class MatchView:
    """Everything the decision needs about the match right now."""

    filename: str | None
    remaining_mins: int
    total_mins: int
    leading_kills: int | None = None
    kill_limit: int | None = None
    mode: str = field(default="")
    caps_to_go: int | None = None     # fewest flags/goals any team still needs (CTF/FB)

    def family(self) -> str:
        return self.mode or game_mode(self.filename)


def decide(
    view: MatchView,
    trigger_mins: int,
    rules: Mapping[str, ModeRule],
    watch: KillWatch,
    caps_watch: KillWatch | None = None,
) -> Decision:
    """Should the vote start on this tick?

    Rule order is fixed: the original time-before-end rule, then minutes-in,
    then the kill watch. With every extra rule off this returns exactly what
    the original code computed: fire when remaining <= trigger_mins.
    """
    remaining = view.remaining_mins
    total = view.total_mins

    if remaining <= trigger_mins:
        return Decision(
            True,
            f"{remaining}m remaining (vote starts {trigger_mins}m before the end)",
            f"Status: {remaining}m remaining. Auto-vote pending...",
        )

    base_status = (
        f"Status: {remaining}m / {total}m remaining. "
        f"Auto-vote in {remaining - trigger_mins}m"
    )

    mode = view.family()
    rule = rules.get(mode)
    if rule is None or mode == MODE_AS:
        return Decision(False, status=base_status)

    label = MODE_LABELS.get(mode, mode)
    notes = []

    if rule.minutes_in_enabled and total > 0:
        elapsed = total - remaining
        if elapsed >= rule.minutes_in:
            return Decision(
                True,
                f"{label}: {elapsed}m into the match "
                f"(rule: start the vote {rule.minutes_in}m in)",
                base_status,
            )
        notes.append(f"in {rule.minutes_in - elapsed}m ({rule.minutes_in}m into the map)")

    if (
        rule.kill_watch_enabled
        and mode in KILL_MODES
        and view.kill_limit
        and view.leading_kills is not None
    ):
        threshold = max(1, view.kill_limit - rule.kills_before_limit)
        if view.leading_kills < threshold:
            watch.armed = True
            notes.append(
                f"at {threshold} kills (now {view.leading_kills} of {view.kill_limit})"
            )
        elif watch.armed:
            return Decision(
                True,
                f"{label}: {view.leading_kills} kills, within "
                f"{rule.kills_before_limit} of the {view.kill_limit} kill limit",
                base_status,
            )
        else:
            notes.append("kill watch waiting for this map's scores to reset")

    if (
        rule.caps_watch_enabled
        and mode in CAPS_MODES
        and view.caps_to_go is not None
    ):
        caps_watch = caps_watch if caps_watch is not None else KillWatch()
        if view.caps_to_go > rule.caps_before_win:
            caps_watch.armed = True
            notes.append(
                f"when a team is {caps_words(mode, rule.caps_before_win)} from winning "
                f"(closest now: {view.caps_to_go})"
            )
        elif caps_watch.armed:
            return Decision(
                True,
                f"{label}: a team is {caps_words(mode, view.caps_to_go)} from winning "
                f"(rule: within {rule.caps_before_win})",
                base_status,
            )
        else:
            notes.append(f"{CAPS_WORD[mode]} watch waiting for this map's scores to reset")

    status = base_status
    if notes:
        status += f"   |   {label} early vote: " + " or ".join(notes)
    return Decision(False, status=status)


# ---- the player gate --------------------------------------------------------
#
# The player count is the admin-port PLAYER list WolfRAT polls every few
# seconds - the same list the Server tab shows. No memory reading, nothing to
# do with the weather scripts. Slot 0 (the server itself) is already dropped
# before the list reaches us.

DEFAULT_MIN_PLAYERS = 2
MIN_PLAYERS_RANGE = (1, 32)


def clamp_min_players(value) -> int:
    lo, hi = MIN_PLAYERS_RANGE
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MIN_PLAYERS


def hold_for_players(players: int, minimum: int) -> str | None:
    """Why the auto-vote must wait, or None when enough players are on.

    Returned text is the status line - the point is that a held vote SAYS so
    instead of sitting on 'Auto-vote pending...' for ever.
    """
    minimum = clamp_min_players(minimum)
    if players >= minimum:
        return None
    if players <= 0:
        who = "nobody is on the server"
    elif players == 1:
        who = "only 1 player is on the server"
    else:
        who = f"only {players} players are on the server"
    return f"Status: Paused - {who}. Votes start with {minimum} or more."


# ---- the join grace ---------------------------------------------------------
#
# A player shows up in the PLAYER list while still on the loading screen. When
# that join is what lets a held vote start, the map list used to go out at
# once and scroll past before the joiner could read it (Dale 2026-09-26: his
# own join released a held vote on an empty server). So a vote the gate held
# back waits this long once enough players are on.

JOIN_GRACE_SECS = 40


class JoinGrace:
    """Remembers a vote the player gate held back and releases it
    JOIN_GRACE_SECS after enough players are on.

    The held decision is kept because some rules fire only once per map (the
    AAS zone rule, co-op) - the vote must not be lost if that rule has gone
    quiet by the time the grace runs out. A vote that was never held starts
    straight away, as before.
    """

    def __init__(self, secs: float = JOIN_GRACE_SECS):
        self.secs = secs
        self.reset()

    def reset(self) -> None:
        self.held: Decision | None = None
        self.release_at: float | None = None

    @property
    def waiting(self) -> bool:
        return self.release_at is not None

    def hold(self, decision: Decision) -> None:
        """The gate is closed. Players who left again restart the clock."""
        if decision.fire:
            self.held = decision
        self.release_at = None

    def step(self, decision: Decision, now: float) -> tuple[Decision | None, int | None]:
        """Enough players are on. Returns (the decision to start now or None,
        whole seconds still to wait or None)."""
        if self.held is None:
            return (decision if decision.fire else None), None
        if self.release_at is None:
            self.release_at = now + self.secs
        left = self.release_at - now
        if left > 0:
            return None, int(-(-left // 1))
        start = decision if decision.fire else self.held
        self.reset()
        return start, None
