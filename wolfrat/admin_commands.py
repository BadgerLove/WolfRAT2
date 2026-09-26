"""Typed retail Joint Operations admin command catalog.

This module is deliberately transport-free.  It is the one place where
WolfRAT's domain operations become retail command strings and where replies
become identity-bearing domain records.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence


MAX_COMMAND_LEN = 1015
MAX_COMMAND_TOKENS = 25
# Joint Ops cuts every chat line at 59 bytes, colour tags included (its flood
# check, proven live 2026-09-26).  A server running the long-chat patch
# (jo-server-patches 15) takes 118 and keeps colour tags; the Chat tab's tick
# box says which one this server is.  Off by default.
STOCK_CHAT_LEN = 59
EXTENDED_CHAT_LEN = 118
MAX_CHAT_LEN = STOCK_CHAT_LEN   # WolfRAT's own automatic lines always fit a stock server
MAX_CHAT_TOKENS = 23
MAX_CMD_ARGUMENT_LEN = 98

CANONICAL_SETTING_KEYS = (
    "AutoBalanceOnRecycle", "PuntVote", "VotePercent", "VoteNumPlayersReq",
    "ChangeTeam", "ChangeTeamInterval", "ChangeTeamPenalty", "ChangeTeamDelay",
    "StartDelay", "DoMinPingCheck", "MinPing", "DoMaxPingCheck", "MaxPing",
    "MaxFriendlyKills", "GameTime", "FriendlyFire", "FriendlyTags",
    "TeamTriggerClaymore", "Tracers", "KOTHLimit", "KillLimit", "MaxScore",
    "FatBullets", "OneShotKill", "ArmoryTimer", "ServerName",
    "ServerPassword", "SideAPassword", "SideBPassword",
)

_SETTING_CASE = {key.casefold(): key for key in CANONICAL_SETTING_KEYS}
_BOOLEAN_SETTINGS = frozenset({
    "AutoBalanceOnRecycle", "PuntVote", "ChangeTeam", "DoMinPingCheck",
    "DoMaxPingCheck", "FriendlyFire", "FriendlyTags", "TeamTriggerClaymore",
    "Tracers", "FatBullets", "OneShotKill",
})
_NUMBER_SETTINGS = frozenset({"VotePercent"})
_SECRET_SETTINGS = frozenset({"ServerPassword", "SideAPassword", "SideBPassword"})
_TEXT_SETTINGS = frozenset({"ServerName"})

# Public schema: callers can render controls without inventing wire names.
SETTING_SCHEMA: Mapping[str, str] = MappingProxyType({
    key: (
        "boolean" if key in _BOOLEAN_SETTINGS
        else "number" if key in _NUMBER_SETTINGS
        else "secret" if key in _SECRET_SETTINGS
        else "text" if key in _TEXT_SETTINGS
        else "integer"
    )
    for key in CANONICAL_SETTING_KEYS
})


class AdminOperation(Enum):
    GET_GAMESTATE = "get_gamestate"
    GET_GAMESETTINGS = "get_gamesettings"
    SET_SETTING = "set_setting"
    PLAYER_LIST = "player_list"
    PLAYER_PUNT = "player_punt"
    PLAYER_BAN = "player_ban"
    PLAYER_SWAPTEAM = "player_swapteam"
    PLAYER_KILL = "player_kill"
    PLAYER_ZEROSCORE = "player_zeroscore"
    MISSION_LIST = "mission_list"
    MISSION_AVAILABLE = "mission_available"
    MISSION_ADD = "mission_add"
    MISSION_REMOVE = "mission_remove"
    MISSION_CLEAR = "mission_clear"
    MISSION_CYCLE = "mission_cycle"
    MISSION_SETNEXT = "mission_setnext"
    WEAPON_LIST = "weapon_list"
    WEAPON_SET = "weapon_set"
    CHAT_GET = "chat_get"
    CHAT_SEND = "chat_send"
    TOD = "tod"
    TODRATE = "todrate"
    RAW = "raw"


class ReplyPolicy(Enum):
    """How the session recognizes completion of an ordered request."""

    ONE = "one"
    TWO = "two"
    TERMINAL = "terminal"

    def is_complete(self, responses: Sequence[str]) -> bool:
        if self is ReplyPolicy.ONE:
            return len(responses) >= 1
        if self is ReplyPolicy.TWO:
            return len(responses) >= 2
        if not responses:
            return False
        last = responses[-1].strip().upper()
        return (
            last == "OK - PLAYER BANNED."
            or last.startswith(("ERROR", "USAGE"))
        )


class WeaponMode(Enum):
    ALWAYS = "ALWAYS"
    NEVER = "NEVER"
    ARMORY = "ARMORY"


class IdentityCollection(Enum):
    PLAYERS = "players"
    MISSIONS = "missions"
    AVAILABLE_MISSIONS = "available_missions"
    WEAPONS = "weapons"


@dataclass(frozen=True)
class IdentityRef:
    collection: IdentityCollection
    key: int
    revision: int


@dataclass(frozen=True)
class PlayerEntry:
    server_id: int
    name: str
    team: int
    player_class: str = ""
    kills: Optional[int] = None
    deaths: Optional[int] = None
    ping: Optional[int] = None
    revision: int = 0


@dataclass(frozen=True)
class MissionEntry:
    queue_index: int
    filename: str
    is_current: bool = False
    is_next: bool = False
    one_shot: bool = False
    is_flipped: bool = False
    double_time: bool = False
    revision: int = 0


@dataclass(frozen=True)
class AvailableMission:
    catalog_index: int
    filename: str
    description: str = ""
    revision: int = 0


@dataclass(frozen=True)
class WeaponEntry:
    admdef_id: int
    name: str
    mode: WeaponMode
    revision: int = 0


@dataclass(frozen=True)
class GameSettings:
    values: Mapping[str, str] = field(default_factory=dict)
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def __getitem__(self, key: str) -> str:
        canonical = _SETTING_CASE.get(key.casefold(), key)
        return self.values[canonical]

    def get(self, key: str, default: Any = None) -> Any:
        canonical = _SETTING_CASE.get(key.casefold(), key)
        return self.values.get(canonical, default)

    def items(self):
        return self.values.items()


@dataclass(frozen=True)
class AdminSnapshot:
    revision: int = 0
    game_state: str = ""
    settings: GameSettings = field(default_factory=GameSettings)
    players: tuple[PlayerEntry, ...] = ()
    missions: tuple[MissionEntry, ...] = ()
    available_missions: tuple[AvailableMission, ...] = ()
    weapons: tuple[WeaponEntry, ...] = ()
    chat: tuple[str, ...] = ()

    def apply(self, operation: AdminOperation, parsed_value: Any) -> "AdminSnapshot":
        """Return a snapshot with one authoritative query result applied.

        Mutation acknowledgements intentionally do not alter cached state; a
        confirming query must do that.
        """

        attributes = {
            AdminOperation.GET_GAMESTATE: "game_state",
            AdminOperation.GET_GAMESETTINGS: "settings",
            AdminOperation.PLAYER_LIST: "players",
            AdminOperation.MISSION_LIST: "missions",
            AdminOperation.MISSION_AVAILABLE: "available_missions",
            AdminOperation.WEAPON_LIST: "weapons",
            AdminOperation.CHAT_GET: "chat",
        }
        attribute = attributes.get(operation)
        if attribute is None:
            return self
        parsed_revision = getattr(parsed_value, "revision", None)
        if parsed_revision is None and isinstance(parsed_value, tuple) and parsed_value:
            parsed_revision = getattr(parsed_value[0], "revision", None)
        revision = int(parsed_revision) if parsed_revision is not None else self.revision + 1
        return replace(self, revision=revision, **{attribute: parsed_value})


ReplyParser = Callable[[str, int], Any]
ReplyValidator = Callable[[Sequence[str]], bool]


@dataclass(frozen=True)
class CommandSpec:
    text: str
    operation: AdminOperation
    reply_policy: ReplyPolicy = ReplyPolicy.ONE
    parser: Optional[ReplyParser] = None
    mutating: bool = False
    identity: Optional[IdentityRef] = None
    reply_validator: Optional[ReplyValidator] = None

    def __post_init__(self) -> None:
        _validate_wire_text(self.text)

    def parse(self, payload: str, revision: int) -> Any:
        return self.parser(payload, revision) if self.parser else payload

    def accepts_replies(self, replies: Sequence[str]) -> bool:
        """Return whether replies satisfy this command's ACK contract."""

        if self.reply_validator is None:
            return True
        return bool(self.reply_validator(replies))


def _validate_wire_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        raise ValueError("admin command must be a non-empty string")
    if "\x00" in text or "\r" in text or "\n" in text:
        raise ValueError("admin command cannot contain NUL or line breaks")
    try:
        encoded = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("retail admin commands are ASCII only") from exc
    if len(encoded) > MAX_COMMAND_LEN:
        raise ValueError(f"admin command exceeds {MAX_COMMAND_LEN} bytes")
    if len(text.split()) > MAX_COMMAND_TOKENS:
        raise ValueError(
            f"admin command exceeds {MAX_COMMAND_TOKENS} retail tokens"
        )
    return text


def _optional_int(value: str) -> Optional[int]:
    value = value.strip()
    if value in {"", "-"}:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid numeric retail field {value!r}") from exc


def parse_game_state(payload: str, revision: int) -> str:
    for line in payload.splitlines():
        if "=" in line and "current state" in line.casefold():
            return line.partition("=")[2].strip()
    return payload.strip()


def parse_game_settings(payload: str, revision: int) -> GameSettings:
    values: dict[str, str] = {}
    for raw in payload.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"malformed GAMESETTINGS row: {raw!r}")
        key, _, value = line.partition("=")
        canonical = _SETTING_CASE.get(key.strip().casefold())
        if canonical is None:
            raise ValueError(f"unknown retail setting: {key.strip()!r}")
        values[canonical] = value.strip()
    return GameSettings(values, revision)


def parse_players(payload: str, revision: int) -> tuple[PlayerEntry, ...]:
    result: list[PlayerEntry] = []
    for raw in payload.splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-", "\t", " "}:
            continue
        columns = [part.strip() for part in line.split("\t")]
        if (
            len(columns) >= 2
            and columns[0].casefold() == "name"
            and columns[1] == "#"
        ):
            continue
        if len(columns) < 3 or not columns[1].isdigit():
            raise ValueError(f"malformed PLAYER LIST row: {raw!r}")
        try:
            server_id = int(columns[1])
            team = int(columns[2])
        except ValueError as exc:
            raise ValueError(f"malformed PLAYER LIST identity: {raw!r}") from exc
        if not (0 <= server_id <= 250):
            raise ValueError(f"player id outside retail roster range: {server_id}")
        result.append(PlayerEntry(
            server_id=server_id,
            name=columns[0],
            team=team,
            player_class=columns[3] if len(columns) > 3 else "",
            kills=_optional_int(columns[4]) if len(columns) > 4 else None,
            deaths=_optional_int(columns[5]) if len(columns) > 5 else None,
            ping=_optional_int(columns[6]) if len(columns) > 6 else None,
            revision=revision,
        ))
    return tuple(result)


_MISSION_RE = re.compile(
    # Filenames may contain spaces (e.g. "AS - Black Rock TAC.npj"), so match
    # lazily up to the extension rather than requiring a single \S+ token.
    r"^\s*(?P<index>\d+):\s+(?P<file>.+?\.(?:BMS|NPJ|NPZ))\s+-\s+"
    r"(?P<double>\(2x\)|\(\))\s+"
    r"(?P<flipped>\(IS FLIPPED\)|\(\))\s+"
    r"(?P<oneshot>\(ONE_SHOT\)|\(\))\s+"
    r"(?P<current><CURRENT MISSION>|<>)\s+"
    r"(?P<next><NEXT MISSION>|<>)\s*$",
    re.IGNORECASE,
)


def parse_missions(payload: str, revision: int) -> tuple[MissionEntry, ...]:
    if payload.strip().casefold() == "no missions in queue.":
        return ()
    result: list[MissionEntry] = []
    for raw in payload.splitlines():
        if not raw.strip():
            continue
        match = _MISSION_RE.match(raw)
        if not match:
            raise ValueError(f"malformed MISSION LIST row: {raw!r}")
        flags = {key: match.group(key).upper() for key in (
            "double", "flipped", "oneshot", "current", "next"
        )}
        result.append(MissionEntry(
            queue_index=int(match.group("index")),
            filename=match.group("file"),
            double_time=flags["double"] == "(2X)",
            is_flipped=flags["flipped"] == "(IS FLIPPED)",
            one_shot=flags["oneshot"] == "(ONE_SHOT)",
            is_current=flags["current"] == "<CURRENT MISSION>",
            is_next=flags["next"] == "<NEXT MISSION>",
            revision=revision,
        ))
    return tuple(result)


_AVAILABLE_RE = re.compile(
    r"^\s*(?P<index>\d+)\.\s+(?P<file>.+\.(?:BMS|NPJ|NPZ))\s+\((?P<description>.*)\)\s*$",
    re.IGNORECASE,
)


def parse_available_missions(payload: str, revision: int) -> tuple[AvailableMission, ...]:
    result: list[AvailableMission] = []
    for raw in payload.splitlines():
        if not raw.strip():
            continue
        match = _AVAILABLE_RE.match(raw)
        if not match:
            raise ValueError(f"malformed MISSION AVAILABLE row: {raw!r}")
        result.append(AvailableMission(
            catalog_index=int(match.group("index")),
            filename=match.group("file"),
            description=match.group("description"),
            revision=revision,
        ))
    return tuple(result)


_WEAPON_RE = re.compile(
    r"^\s*(?P<id>\d+)\.\s+(?P<mode>ALWAYS|NEVER|ARMORY)"
    r"[ \t]+(?P<name>.+?)\s*$",
    re.IGNORECASE,
)


def parse_weapons(payload: str, revision: int) -> tuple[WeaponEntry, ...]:
    result: list[WeaponEntry] = []
    for raw in payload.splitlines():
        if not raw.strip():
            continue
        match = _WEAPON_RE.match(raw)
        if not match:
            raise ValueError(f"malformed WEAPON LIST row: {raw!r}")
        admdef_id = int(match.group("id"))
        if not (0 <= admdef_id <= 254):
            raise ValueError(f"weapon id outside retail table range: {admdef_id}")
        result.append(WeaponEntry(
            admdef_id=admdef_id,
            name=match.group("name"),
            mode=WeaponMode[match.group("mode").upper()],
            revision=revision,
        ))
    return tuple(result)


def parse_chat(payload: str, revision: int) -> tuple[str, ...]:
    return tuple(line.rstrip("\r") for line in payload.split("\n") if line.rstrip("\r"))


def _spec(
    text: str,
    operation: AdminOperation,
    *,
    parser: Optional[ReplyParser] = None,
    mutating: bool = False,
    reply_policy: ReplyPolicy = ReplyPolicy.ONE,
    identity: Optional[IdentityRef] = None,
    reply_validator: Optional[ReplyValidator] = None,
) -> CommandSpec:
    return CommandSpec(
        text,
        operation,
        reply_policy,
        parser,
        mutating,
        identity,
        reply_validator,
    )


def _require_player(player: PlayerEntry) -> PlayerEntry:
    if not isinstance(player, PlayerEntry):
        raise TypeError("player operation requires a PlayerEntry from PLAYER LIST")
    if not (1 <= player.server_id <= 250):
        raise ValueError(
            "player target must be in the non-host retail range 1-250"
        )
    if player.revision <= 0:
        raise ValueError("player target has no authoritative snapshot revision")
    return player


def _require_mission(mission: MissionEntry) -> MissionEntry:
    if not isinstance(mission, MissionEntry):
        raise TypeError("mission operation requires a MissionEntry from MISSION LIST")
    if mission.queue_index < 0:
        raise ValueError("mission queue index must be non-negative")
    if mission.revision <= 0:
        raise ValueError("mission target has no authoritative snapshot revision")
    return mission


def _coerce_mode(mode: WeaponMode) -> WeaponMode:
    if not isinstance(mode, WeaponMode):
        raise TypeError("weapon mode must be a WeaponMode")
    return mode


def _setting_value(key: str, value: Any) -> str:
    if key in _BOOLEAN_SETTINGS:
        if isinstance(value, bool):
            return "1" if value else "0"
        if value in (0, 1, "0", "1"):
            return str(value)
        raise ValueError(f"{key} accepts only 0 or 1")
    if key in _NUMBER_SETTINGS:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} requires a number") from exc
        if not math.isfinite(number) or not (0 <= number <= 1):
            raise ValueError(f"{key} must be between 0 and 1")
        return str(value)
    if key in _SECRET_SETTINGS:
        text = str(value)
        if len(text) > 16:
            raise ValueError(f"{key} cannot exceed 16 characters")
        _validate_argument(text, allow_empty=True)
        if any(ch.isspace() for ch in text):
            raise ValueError(f"{key} cannot contain whitespace")
        return text
    if key in _TEXT_SETTINGS:
        text = str(value)
        if not text:
            raise ValueError("ServerName cannot be cleared")
        if len(text) > 27:
            raise ValueError("ServerName cannot exceed 27 characters")
        _validate_argument(text)
        return text
    if isinstance(value, bool):
        value = int(value)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} requires an integer") from exc
    if number < 0:
        raise ValueError(f"{key} requires a non-negative integer")
    return str(number)


_extended_chat = False
_chat_limit_listeners: list[Callable[[int], None]] = []


def extended_chat() -> bool:
    return _extended_chat


def chat_limit() -> int:
    """Longest line WolfRAT may send right now."""
    return EXTENDED_CHAT_LEN if _extended_chat else STOCK_CHAT_LEN


def set_extended_chat(enabled: bool) -> None:
    global _extended_chat
    enabled = bool(enabled)
    if enabled == _extended_chat:
        return
    _extended_chat = enabled
    for listener in list(_chat_limit_listeners):
        try:
            listener(chat_limit())
        except Exception:
            pass


def on_chat_limit_changed(listener: Callable[[int], None]) -> None:
    _chat_limit_listeners.append(listener)


def _validate_argument(text: str, *, allow_empty: bool = False) -> str:
    if not text and not allow_empty:
        raise ValueError("command argument cannot be empty")
    if any(ch in text for ch in "\x00\r\n"):
        raise ValueError("command argument cannot contain NUL or line breaks")
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("retail command arguments are ASCII only") from exc
    return text


class AdminCommands:
    @staticmethod
    def game_state() -> CommandSpec:
        return _spec("GET GAMESTATE", AdminOperation.GET_GAMESTATE, parser=parse_game_state)

    @staticmethod
    def game_settings() -> CommandSpec:
        return _spec("GET GAMESETTINGS", AdminOperation.GET_GAMESETTINGS, parser=parse_game_settings)

    @staticmethod
    def players() -> CommandSpec:
        return _spec("PLAYER LIST", AdminOperation.PLAYER_LIST, parser=parse_players)

    @staticmethod
    def missions() -> CommandSpec:
        return _spec("MISSION LIST", AdminOperation.MISSION_LIST, parser=parse_missions)

    @staticmethod
    def available_missions() -> CommandSpec:
        return _spec("MISSION AVAILABLE", AdminOperation.MISSION_AVAILABLE, parser=parse_available_missions)

    @staticmethod
    def weapons() -> CommandSpec:
        return _spec("WEAPON LIST", AdminOperation.WEAPON_LIST, parser=parse_weapons)

    @staticmethod
    def chat() -> CommandSpec:
        return _spec("CHAT GET", AdminOperation.CHAT_GET, parser=parse_chat)

    @staticmethod
    def set_setting(key: str, value: Any) -> CommandSpec:
        canonical = _SETTING_CASE.get(str(key).casefold())
        if canonical is None:
            raise ValueError(f"unsupported retail setting {key!r}")
        serialized = _setting_value(canonical, value)
        suffix = f" {serialized}" if serialized else ""
        return _spec(
            f"SET {canonical}{suffix}",
            AdminOperation.SET_SETTING,
            mutating=True,
        )

    @staticmethod
    def add_mission(
        mission: AvailableMission,
        *,
        auto_switch_sides: Optional[bool] = None,
        insert_at: Optional[int] = None,
        one_shot: bool = False,
    ) -> CommandSpec:
        if not isinstance(mission, AvailableMission):
            raise TypeError("mission add requires an AvailableMission")
        if mission.revision <= 0:
            raise ValueError("available mission has no authoritative snapshot revision")
        filename = _validate_mission_filename(mission.filename)
        if insert_at is None and one_shot:
            raise ValueError("retail requires INSERT_AT before ONESHOT")
        if insert_at is not None and insert_at < 0:
            raise ValueError("INSERT_AT must be non-negative")
        parts = ["MISSION", "ADD", filename]
        if auto_switch_sides is not None or insert_at is not None:
            parts.append("1" if auto_switch_sides else "0")
        if insert_at is not None:
            parts.append(str(insert_at))
        if one_shot:
            parts.extend(("ONESHOT", "1"))
        return _spec(
            " ".join(parts),
            AdminOperation.MISSION_ADD,
            mutating=True,
            identity=IdentityRef(
                IdentityCollection.AVAILABLE_MISSIONS,
                mission.catalog_index,
                mission.revision,
            ),
        )

    @staticmethod
    def remove_mission(mission: MissionEntry) -> CommandSpec:
        mission = _require_mission(mission)
        return _spec(
            f"MISSION REMOVE {mission.queue_index}",
            AdminOperation.MISSION_REMOVE,
            mutating=True,
            identity=IdentityRef(
                IdentityCollection.MISSIONS,
                mission.queue_index,
                mission.revision,
            ),
        )

    @staticmethod
    def clear_missions() -> CommandSpec:
        return _spec("MISSION CLEAR", AdminOperation.MISSION_CLEAR, mutating=True)

    @staticmethod
    def cycle_mission() -> CommandSpec:
        return _spec("MISSION CYCLE", AdminOperation.MISSION_CYCLE, mutating=True)

    @staticmethod
    def set_next_mission(mission: MissionEntry) -> CommandSpec:
        mission = _require_mission(mission)
        return _spec(
            f"MISSION SETNEXT {mission.queue_index}",
            AdminOperation.MISSION_SETNEXT,
            mutating=True,
            identity=IdentityRef(
                IdentityCollection.MISSIONS,
                mission.queue_index,
                mission.revision,
            ),
        )

    @staticmethod
    def punt(player: PlayerEntry) -> CommandSpec:
        return _player_spec("PUNT", AdminOperation.PLAYER_PUNT, player)

    @staticmethod
    def ban(player: PlayerEntry) -> CommandSpec:
        return _player_spec("BAN", AdminOperation.PLAYER_BAN, player, ReplyPolicy.TERMINAL)

    @staticmethod
    def swap_team(player: PlayerEntry) -> CommandSpec:
        return _player_spec("SWAPTEAM", AdminOperation.PLAYER_SWAPTEAM, player)

    @staticmethod
    def kill(player: PlayerEntry) -> CommandSpec:
        return _player_spec("KILL", AdminOperation.PLAYER_KILL, player)

    @staticmethod
    def zero_score(player: PlayerEntry) -> CommandSpec:
        return _player_spec("ZEROSCORE", AdminOperation.PLAYER_ZEROSCORE, player)

    @staticmethod
    def set_weapon(weapon: WeaponEntry, mode: WeaponMode) -> CommandSpec:
        if not isinstance(weapon, WeaponEntry):
            raise TypeError("weapon operation requires a WeaponEntry from WEAPON LIST")
        if weapon.revision <= 0:
            raise ValueError("weapon target has no authoritative snapshot revision")
        mode = _coerce_mode(mode)
        if not (0 <= weapon.admdef_id <= 254):
            raise ValueError("weapon AdmDef id is outside retail table range")
        return _spec(
            f"WEAPON SET {weapon.admdef_id} {mode.value}",
            AdminOperation.WEAPON_SET,
            mutating=True,
            identity=IdentityRef(
                IdentityCollection.WEAPONS,
                weapon.admdef_id,
                weapon.revision,
            ),
        )

    @staticmethod
    def set_all_weapons(mode: WeaponMode) -> CommandSpec:
        mode = _coerce_mode(mode)
        return _spec(f"WEAPON SET ALL {mode.value}", AdminOperation.WEAPON_SET, mutating=True)

    @staticmethod
    def send_chat(message: str) -> CommandSpec:
        message = _validate_argument(message)
        if len(message) > chat_limit():
            raise ValueError(f"chat message cannot exceed {chat_limit()} characters")
        if len(message.split()) > MAX_CHAT_TOKENS:
            raise ValueError(
                f"chat message cannot exceed {MAX_CHAT_TOKENS} retail tokens"
            )
        return _spec(f"CHAT SEND {message}", AdminOperation.CHAT_SEND, mutating=True)

    @staticmethod
    def tod(value: str) -> CommandSpec:
        value = str(value)
        if not re.fullmatch(r"\d{4}", value):
            raise ValueError("TOD must be four digits in HHMM form")
        hour, minute = int(value[:2]), int(value[2:])
        if hour > 23 or minute > 59:
            raise ValueError("TOD must be a valid 24-hour time")
        return _cmd_spec(f"TOD {value}", AdminOperation.TOD)

    @staticmethod
    def tod_rate(minutes: int) -> CommandSpec:
        if isinstance(minutes, bool):
            raise ValueError("TODRATE requires a positive integer")
        try:
            minutes = int(minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("TODRATE requires a positive integer") from exc
        if minutes <= 0:
            raise ValueError("TODRATE requires a positive integer")
        return _cmd_spec(f"TODRATE {minutes}", AdminOperation.TODRATE)


def _validate_mission_filename(filename: str) -> str:
    filename = _validate_argument(filename)
    if any(ch.isspace() for ch in filename):
        # The JO admin port splits every command on whitespace with no quote
        # handling, so a filename containing spaces can never reach the server
        # intact ("AS - Black Rock TAC.npj" arrives as "AS").  Such maps can
        # still be listed/managed once in the rotation (via game.cfg or the
        # launcher), but cannot be added remotely by any admin tool.
        raise ValueError(
            f"'{filename}' has spaces in its filename; the JO admin port cannot "
            "add it remotely. Use a copy without spaces (e.g. AS-MapName.npj) "
            "or add it in game.cfg"
        )
    if "/" in filename or "\\" in filename:
        raise ValueError("mission filename must be one retail catalog basename")
    if not re.search(r"\.(?:bms|npj|npz)$", filename, re.IGNORECASE):
        raise ValueError("mission filename must end in .bms, .npj, or .npz")
    return filename


def _player_spec(
    verb: str,
    operation: AdminOperation,
    player: PlayerEntry,
    reply_policy: ReplyPolicy = ReplyPolicy.ONE,
) -> CommandSpec:
    player = _require_player(player)
    return _spec(
        f"PLAYER {verb} {player.server_id}",
        operation,
        mutating=True,
        reply_policy=reply_policy,
        identity=IdentityRef(
            IdentityCollection.PLAYERS,
            player.server_id,
            player.revision,
        ),
    )


def _cmd_spec(arguments: str, operation: AdminOperation) -> CommandSpec:
    _validate_argument(arguments)
    if len(arguments) > MAX_CMD_ARGUMENT_LEN:
        raise ValueError(f"CMD arguments cannot exceed {MAX_CMD_ARGUMENT_LEN} characters")
    return _spec(
        f"CMD {arguments}",
        operation,
        mutating=True,
        reply_validator=_accepts_cmd_reply,
    )


def _accepts_cmd_reply(replies: Sequence[str]) -> bool:
    return tuple(replies) == (
        "OK - Command executed.",
    )


def raw_command(text: str) -> CommandSpec:
    """Build an explicitly raw command while retaining transport safety.

    Known no-terminal/destructive forms are refused.  Feature code must use
    ``AdminCommands`` instead; this exists only for an explicit raw console.
    """

    text = _validate_wire_text(text.strip())
    upper = " ".join(text.upper().split())
    tokens = upper.split()
    if not tokens:
        raise ValueError("raw command cannot be empty")
    if tokens[0] in {"QUIT", "PETERRABBIT"}:
        raise ValueError(f"{tokens[0]} is not safe on a managed session")
    if tokens[0] == "GOTO":
        raise ValueError(
            "GOTO is blocked; use the typed mission-cycle operation"
        )
    if tokens[0] == "GET" and (
        len(tokens) != 2
        or tokens[1] not in {"GAMESTATE", "GAMESETTINGS"}
    ):
        raise ValueError(
            "raw GET supports exactly GAMESTATE or GAMESETTINGS"
        )
    if tokens[0] == "PLAYER":
        if len(tokens) < 2:
            raise ValueError("raw PLAYER command requires a documented verb")
        verb = tokens[1]
        if verb == "LIST":
            if len(tokens) != 2:
                raise ValueError("PLAYER LIST accepts no arguments")
        elif verb in {
            "PUNT", "BAN", "SWAPTEAM", "KILL", "ZEROSCORE",
        }:
            if len(tokens) != 3:
                raise ValueError(
                    "raw player mutation requires exactly one target"
                )
            if tokens[2] == "ALL":
                if verb != "PUNT":
                    raise ValueError(
                        "raw PLAYER ALL mutation can target host ID 0 or "
                        "has no reliable terminal reply"
                    )
            elif not tokens[2].isdigit():
                raise ValueError(
                    "raw player target must be a numeric retail id"
                )
            elif not 1 <= int(tokens[2]) <= 250:
                raise ValueError(
                    "raw player target must be in the non-host range 1-250"
                )
        else:
            raise ValueError(f"unsupported raw PLAYER verb: {verb}")
    if tokens[0] == "MISSION":
        if len(tokens) < 2:
            raise ValueError("raw MISSION command requires a documented verb")
        verb = tokens[1]
        if verb in {"LIST", "AVAILABLE", "CLEAR", "CYCLE"}:
            if len(tokens) != 2:
                raise ValueError(f"MISSION {verb} accepts no arguments")
        elif verb in {"REMOVE", "SETNEXT"}:
            if len(tokens) != 3 or not tokens[2].isdigit():
                raise ValueError(
                    f"MISSION {verb} requires one non-negative queue index"
                )
        elif verb == "ADD":
            if len(tokens) not in {3, 4, 5, 7}:
                raise ValueError("MISSION ADD arguments do not match retail grammar")
            original_tokens = text.split()
            _validate_mission_filename(original_tokens[2])
            if len(tokens) >= 4 and tokens[3] not in {"0", "1"}:
                raise ValueError("MISSION ADD auto-switch flag must be 0 or 1")
            if len(tokens) >= 5 and not tokens[4].isdigit():
                raise ValueError(
                    "MISSION ADD insertion index must be non-negative"
                )
            if len(tokens) == 7 and (
                tokens[5] != "ONESHOT" or tokens[6] != "1"
            ):
                raise ValueError(
                    "MISSION ADD ONESHOT must be followed by 1; retail "
                    "ignores a zero value and still enables one-shot"
                )
        else:
            raise ValueError(f"unsupported raw MISSION verb: {verb}")
    if tokens[0] == "WEAPON":
        if len(tokens) < 2:
            raise ValueError("raw WEAPON command requires a documented verb")
        verb = tokens[1]
        if verb == "LIST" and len(tokens) != 2:
            raise ValueError("WEAPON LIST accepts no arguments")
        if verb == "SET":
            if len(tokens) != 4:
                raise ValueError("WEAPON SET requires one target and one mode")
            target, mode = tokens[2], tokens[3]
            if target != "ALL" and (
                not target.isdigit() or not 0 <= int(target) <= 254
            ):
                raise ValueError(
                    "WEAPON SET target must be ALL or a retail table id 0-254"
                )
            if mode not in WeaponMode.__members__:
                raise ValueError(
                    "WEAPON SET mode must be ALWAYS, NEVER, or ARMORY"
                )
        elif verb != "LIST":
            raise ValueError(f"unsupported raw WEAPON verb: {verb}")
    if tokens[0] == "CHAT":
        if len(tokens) < 2:
            raise ValueError("raw CHAT command requires GET or SEND")
        if tokens[1] == "GET":
            if len(tokens) != 2:
                raise ValueError("CHAT GET accepts no arguments")
        elif tokens[1] != "SEND":
            raise ValueError(f"unsupported raw CHAT verb: {tokens[1]}")

    chat_match = re.fullmatch(
        r"CHAT[ \t]+SEND(?:[ \t]+(.*))?", text, re.IGNORECASE
    )
    if chat_match:
        # Reuse the typed builder so the retail 64-byte stack buffer cannot be
        # overrun through the explicit raw console.
        AdminCommands.send_chat(chat_match.group(1) or "")

    cmd_match = re.fullmatch(
        r"CMD(?:[ \t]+(.*))?", text, re.IGNORECASE
    )
    if cmd_match and cmd_match.group(1) is not None:
        arguments = cmd_match.group(1)
        _validate_argument(arguments)
        if len(arguments) > MAX_CMD_ARGUMENT_LEN:
            raise ValueError(
                f"CMD arguments cannot exceed "
                f"{MAX_CMD_ARGUMENT_LEN} characters"
            )

    set_match = re.fullmatch(
        r"SET[ \t]+(\S+)(?:[ \t]+(.*))?", text, re.IGNORECASE
    )
    if set_match and set_match.group(1).casefold() in _SETTING_CASE:
        AdminCommands.set_setting(
            set_match.group(1), set_match.group(2) or ""
        )

    policy = ReplyPolicy.ONE
    if len(tokens) == 3 and tokens[:2] == ["PLAYER", "BAN"] and tokens[2].isdigit():
        policy = ReplyPolicy.TERMINAL
    elif tokens[0] == "SET":
        key = tokens[1] if len(tokens) > 1 else ""
        if key.casefold() not in _SETTING_CASE and len(tokens) >= 3:
            policy = ReplyPolicy.TWO
    read_only = upper in {
        "GET GAMESTATE",
        "GET GAMESETTINGS",
        "PLAYER LIST",
        "MISSION LIST",
        "MISSION AVAILABLE",
        "WEAPON LIST",
        "CHAT GET",
        "QUERY",
        "EVENTLOG",
    }
    return _spec(
        text,
        AdminOperation.RAW,
        mutating=not read_only,
        reply_policy=policy,
    )


__all__ = [
    "AdminCommands", "AdminOperation", "AdminSnapshot", "AvailableMission",
    "CANONICAL_SETTING_KEYS", "CommandSpec", "GameSettings",
    "IdentityCollection", "IdentityRef", "MAX_CHAT_LEN", "MAX_CHAT_TOKENS",
    "STOCK_CHAT_LEN", "EXTENDED_CHAT_LEN", "chat_limit", "extended_chat",
    "set_extended_chat", "on_chat_limit_changed",
    "MAX_COMMAND_TOKENS", "MissionEntry",
    "PlayerEntry", "ReplyPolicy", "SETTING_SCHEMA", "WeaponEntry", "WeaponMode",
    "parse_available_missions", "parse_chat", "parse_game_settings",
    "parse_game_state", "parse_missions", "parse_players", "parse_weapons",
    "raw_command",
]
