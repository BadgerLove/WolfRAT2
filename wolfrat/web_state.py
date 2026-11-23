"""Small pure helpers for the phone page's state (no Qt, any thread).

The web server uses them on its own thread for what it can work out from the
ServerManager snapshot alone (teams, ping bands, chat kinds, next map, time
left); the desktop bridge uses the same ones when it builds its fuller
snapshot on the Qt thread.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from wolfrat import coop_guard
from wolfrat import vote_rules
from wolfrat.lag_comp import band_of

TEAM_NAMES = {1: "Joint Ops", 2: "Rebels"}

# Short codes for the game types the server reports (coop_guard.GAME_TYPES has
# the long names). "FM" and "CAC" are the engine's own short names.
GAME_TYPE_CODES = {
    0x00000: "DM",
    0x00001: "KOTH",
    0x00008: "FM",
    0x10000: "TDM",
    0x10001: "TKOTH",
    0x10002: "AD",
    0x10004: "CTF",
    0x10008: "FB",
    0x10010: "AAS",
    0x50010: "CAC",
}
# The same codes for the game-mode family a map's file name gives
# (vote_rules.game_mode), used when the server is not on this PC.
FAMILY_CODES = {
    vote_rules.MODE_AS: "AAS",
    vote_rules.MODE_TDM: "TDM",
    vote_rules.MODE_DM: "DM",
    vote_rules.MODE_TKOTH: "TKOTH",
    vote_rules.MODE_CTF: "CTF",
    vote_rules.MODE_FB: "FB",
}

_MAP_SUFFIX = re.compile(r"(?i)\.(?:bms|npj|npz)$")


def ping_band(ping) -> Optional[int]:
    """0 under 80 ms, 1 for 80-149, 2 for 150+ (lag_comp.BAND_LABELS); None without one."""
    if ping is None or isinstance(ping, bool):
        return None
    try:
        value = int(ping)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return band_of(value)


def chat_kind(message) -> tuple[str, Optional[str]]:
    """("player", name) for "Name: text", ("wolfrat", None) for WolfRAT's own
    lines, ("console", None) for anything else - the same rule the Mods tab
    uses to tell a player's line from a console line."""
    if message.get("own"):
        return "wolfrat", None
    text = str(message.get("text", "") or "")
    if text.strip().startswith("Unknown command:"):
        return "console", None
    sender, colon, _rest = text.partition(":")
    sender = sender.strip()
    if colon and sender and len(sender) <= 40:
        return "player", sender
    return "console", None


def chat_rows(messages: Iterable) -> list:
    """Copies of the chat messages with "kind" and "name" added."""
    rows = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        row = dict(message)
        row["kind"], row["name"] = chat_kind(message)
        rows.append(row)
    return rows


def team_rows(entries) -> list:
    """Kills and head count per team, from the players on the server now."""
    rows = {
        team: {"team": team, "name": name, "kills": 0, "players": 0}
        for team, name in TEAM_NAMES.items()
    }
    for player in entries or ():
        if getattr(player, "server_id", 0) == 0:
            continue
        row = rows.get(getattr(player, "team", None))
        if row is None:
            continue
        row["players"] += 1
        row["kills"] += getattr(player, "kills", None) or 0
    return [rows[1], rows[2]]


def current_entry(entries):
    return next((entry for entry in entries or () if entry.is_current), None)


def next_entry(entries):
    """(entry, guessed): the queue's NEXT row, else the row after the current
    one (WolfRAT's guess, as ServerManager._next_mission_filename makes it)."""
    entries = list(entries or ())
    marked = next((entry for entry in entries if entry.is_next), None)
    if marked is not None:
        return marked, False
    for index, entry in enumerate(entries):
        if entry.is_current:
            return entries[(index + 1) % len(entries)], True
    return None, False


def plain_map_name(filename) -> str:
    return _MAP_SUFFIX.sub("", str(filename or ""))


def parse_gametime(raw) -> tuple[Optional[int], Optional[int]]:
    """GameTime "remaining/limit" in whole minutes; (None, None) when it says
    nothing (missing, malformed, or 0/0 = no match running)."""
    left, slash, limit = str(raw or "").partition("/")
    if not slash:
        return None, None
    try:
        left_min, limit_min = int(float(left)), int(float(limit))
    except ValueError:
        return None, None
    if limit_min <= 0:
        return None, None
    return max(0, left_min), limit_min


def game_type_of(game_type, map_file) -> tuple[Optional[str], Optional[str]]:
    """(short code, long name) for the game type, or (None, None).

    The server's own value when WolfRAT reads it (server on this PC); 0 and
    None go to the map file name's family, as the desktop does.
    """
    if game_type:
        if coop_guard.is_coop(game_type):
            return "COOP", "Co-op"
        return GAME_TYPE_CODES.get(game_type), coop_guard.game_type_label(game_type)
    if not map_file:
        return None, None
    family = vote_rules.game_mode(map_file)
    if family not in FAMILY_CODES:
        return None, None
    return FAMILY_CODES[family], vote_rules.MODE_LABELS[family]


def server_block(sm, map_titles=None, *, game_type=None, time_left=None) -> dict:
    """The page's "server" object from the ServerManager snapshot.

    ``map_titles`` maps an upper-case file name to its title; ``game_type``
    is the server's own value when known; ``time_left`` = (left, limit) when
    the caller knows better than the raw GameTime setting.
    """
    settings = sm.game_settings or {}
    name = str(settings.get("servername") or "").strip() or None
    current = current_entry(tuple(sm.mission_entries or ()))
    map_file = current.filename if current is not None else None
    title = None
    if map_file:
        title = (map_titles or {}).get(map_file.upper()) or plain_map_name(map_file)
    code, label = game_type_of(game_type, map_file)
    if time_left is None:
        time_left = parse_gametime(settings.get("gametime"))
    left, limit = time_left
    return {
        "name": name,
        "game_type": code,
        "game_type_name": label,
        "map_title": title,
        "map_file": map_file,
        "time_left_min": left,
        "time_limit_min": limit,
    }


def next_map_block(sm, map_titles=None) -> Optional[dict]:
    entry, guessed = next_entry(tuple(sm.mission_entries or ()))
    if entry is None:
        return None
    return {
        "filename": entry.filename,
        "display_name": (map_titles or {}).get(entry.filename.upper())
        or plain_map_name(entry.filename),
        "queue_index": entry.queue_index,
        # Added: True when the queue has no NEXT mark and this is WolfRAT's
        # guess (the row after the current one).
        "guessed": guessed,
    }
