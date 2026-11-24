"""WolfRAT's own ban list - the rules, no Qt.

Why this exists (2026-09-22): Joint Ops' built-in ban writes a CD-key id to
``banlist.txt``.  Nobody has a CD key any more, so that ban is worthless and
the admin port cannot even show the list.  Every other admin tool for these
games (Babstats, HawkSync, Anaconda's) does the same thing instead: keep its
own list of NAMES and IPs and punt a match the moment they appear.  This
module is that list.

Entries match a player by name (case-insensitive, optional trailing ``*``)
or by IP (exact, ``198.51.*`` wildcards the Babstats way, ``198.51.0.0/16``,
or ``198.51.100.1-198.51.100.99``).  A whitelist entry beats everything.
"""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

KIND_NAME = "name"
KIND_IP = "ip"
PUNT_COOLDOWN_SECONDS = 30       # never hammer the same player with punts
RULE_PREFIX = "WolfRAT ban "     # firewall rule DisplayName prefix

_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_WILD = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}\.\*$")
_CIDR = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
_RANGE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}\s*-\s*\d{1,3}(\.\d{1,3}){3}$")


@dataclass
class BanEntry:
    kind: str                      # KIND_NAME | KIND_IP
    value: str                     # the name, or the IP / wildcard / CIDR / range
    reason: str = ""
    added_by: str = ""
    added_at: float = 0.0
    expires_at: Optional[float] = None
    hits: int = 0                  # times this entry removed somebody
    last_hit_at: Optional[float] = None
    last_hit_name: str = ""
    last_hit_ip: str = ""
    linked: str = ""               # the IP this name was banned with, or the name this IP belonged to

    def key(self) -> tuple[str, str]:
        return self.kind, self.value.lower()

    def is_live(self, now: float) -> bool:
        return self.expires_at is None or now < self.expires_at

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "BanEntry":
        fields = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        entry = cls(kind=str(fields.pop("kind", KIND_NAME)), value=str(fields.pop("value", "")))
        for name, value in fields.items():
            setattr(entry, name, value)
        entry.kind = KIND_IP if entry.kind == KIND_IP else KIND_NAME
        entry.reason = str(entry.reason or "")
        entry.added_by = str(entry.added_by or "")
        entry.linked = str(entry.linked or "")
        try:
            entry.added_at = float(entry.added_at or 0.0)
        except (TypeError, ValueError):
            entry.added_at = 0.0
        try:
            entry.expires_at = None if entry.expires_at in (None, "") else float(entry.expires_at)
        except (TypeError, ValueError):
            entry.expires_at = None
        try:
            entry.hits = int(entry.hits or 0)
        except (TypeError, ValueError):
            entry.hits = 0
        return entry


# ---- matching ---------------------------------------------------------------

def looks_like_ip_pattern(text: str) -> bool:
    text = text.strip()
    return bool(_IPV4.match(text) or _WILD.match(text) or _CIDR.match(text) or _RANGE.match(text))


def ip_pattern_problem(pattern: str) -> Optional[str]:
    """Plain-words reason a pattern is unusable, or None when it is fine."""
    p = pattern.strip()
    try:
        if _IPV4.match(p):
            ipaddress.IPv4Address(p)
        elif _WILD.match(p):
            for part in p.split(".")[:-1]:
                if int(part) > 255:
                    return "a number in the address is over 255"
        elif _CIDR.match(p):
            ipaddress.IPv4Network(p, strict=False)
        elif _RANGE.match(p):
            lo, hi = (ipaddress.IPv4Address(x.strip()) for x in p.split("-"))
            if lo > hi:
                return "the range runs backwards"
        else:
            return "not an IP address, wildcard, CIDR or range"
    except ValueError:
        return "not a valid IP address"
    return None


def ip_matches(pattern: str, ip: str) -> bool:
    p = pattern.strip()
    if not ip or not p:
        return False
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    try:
        if _IPV4.match(p):
            return addr == ipaddress.IPv4Address(p)
        if _WILD.match(p):
            prefix = p[:-1]                      # "198.51." keeps the dot
            return (ip + ".").startswith(prefix)
        if _CIDR.match(p):
            return addr in ipaddress.IPv4Network(p, strict=False)
        if _RANGE.match(p):
            lo, hi = (ipaddress.IPv4Address(x.strip()) for x in p.split("-"))
            return lo <= addr <= hi
    except ValueError:
        return False
    return False


def name_matches(pattern: str, name: str) -> bool:
    p, n = pattern.strip().lower(), (name or "").strip().lower()
    if not p or not n:
        return False
    if p.endswith("*"):
        return n.startswith(p[:-1]) if p[:-1] else False
    return p == n


def entry_matches(entry: BanEntry, name: str, ip: str) -> bool:
    if entry.kind == KIND_IP:
        return ip_matches(entry.value, ip)
    return name_matches(entry.value, name)


def find_match(entries: Iterable[BanEntry], name: str, ip: str, now: float) -> Optional[BanEntry]:
    for entry in entries:
        if entry.is_live(now) and entry_matches(entry, name, ip):
            return entry
    return None


# ---- the list -----------------------------------------------------------------

@dataclass
class BanList:
    entries: list = field(default_factory=list)
    whitelist: list = field(default_factory=list)

    def add(self, entry: BanEntry, *, whitelist: bool = False) -> bool:
        """Add or replace (same kind + value).  Returns True when it was new."""
        target = self.whitelist if whitelist else self.entries
        for index, existing in enumerate(target):
            if existing.key() == entry.key():
                entry.hits, entry.last_hit_at = existing.hits, existing.last_hit_at
                entry.last_hit_name, entry.last_hit_ip = existing.last_hit_name, existing.last_hit_ip
                target[index] = entry
                return False
        target.append(entry)
        return True

    def remove(self, kind: str, value: str, *, whitelist: bool = False) -> bool:
        target = self.whitelist if whitelist else self.entries
        before = len(target)
        target[:] = [e for e in target if e.key() != (kind, value.lower())]
        return len(target) != before

    def remove_with_linked(self, value: str) -> list:
        """Unban a name or an address.  Returns the entries taken off.

        A ban by name + IP is two entries, and the address entry says which
        name it was banned with.  Taking only the name off would leave that
        player locked out, so the address entries banned with the name go
        too.  An address banned with another name, or typed in by hand, stays.
        """
        wanted = value.strip().lower()
        if not wanted:
            return []
        gone = [e for e in self.entries
                if e.value.lower() == wanted
                or (e.kind == KIND_IP and e.linked.strip().lower() == wanted)]
        self.entries[:] = [e for e in self.entries if not any(e is g for g in gone)]
        return gone

    def purge_expired(self, now: float) -> int:
        before = len(self.entries)
        self.entries[:] = [e for e in self.entries if e.is_live(now)]
        return before - len(self.entries)

    def check(self, name: str, ip: str, now: float) -> Optional[BanEntry]:
        """The ban entry this player trips, or None (whitelist wins)."""
        if find_match(self.whitelist, name, ip, now) is not None:
            return None
        return find_match(self.entries, name, ip, now)

    def to_json(self) -> dict:
        return {"entries": [e.to_json() for e in self.entries],
                "whitelist": [e.to_json() for e in self.whitelist]}

    @classmethod
    def from_json(cls, data) -> "BanList":
        result = cls()
        if not isinstance(data, dict):
            return result
        for key in ("entries", "whitelist"):
            for item in data.get(key) or []:
                if isinstance(item, dict) and item.get("value"):
                    getattr(result, key).append(BanEntry.from_json(item))
        return result


# ---- punt on sight ----------------------------------------------------------------

@dataclass(frozen=True)
class Removal:
    player_id: str
    name: str
    ip: str
    entry: BanEntry
    player: dict = field(default_factory=dict)   # the FULL admin-port record - punting needs all of it

    def why(self) -> str:
        what = f"IP {self.entry.value}" if self.entry.kind == KIND_IP else f"name {self.entry.value}"
        return f"{self.name} ({self.ip or 'IP unknown'}) matches banned {what}" + (
            f" - {self.entry.reason}" if self.entry.reason else "")


class Enforcer:
    """Decides who to punt this tick.  Remembers who it punted recently so a
    player who takes a few seconds to leave is not punted five times - but
    only while they are STILL there.  The moment they drop off the list the
    memory is cleared, so a rejoin is punted on the very next poll.

    Live 2026-09-22: a 30 s cooldown keyed on the name alone let Dale rejoin
    after a punt and sit on the server for up to 30 s, chatting."""

    def __init__(self, cooldown: float = PUNT_COOLDOWN_SECONDS):
        self._cooldown = cooldown
        self._recent: dict[str, float] = {}

    def note_punted(self, name: str, ip: str, now: float) -> None:
        """A punt was just sent by hand (button / !ban) - do not double up this tick."""
        self._recent[f"{name.lower()}|{ip}"] = now

    def decide(self, bans: BanList, players: Iterable[dict], ips: dict, now: float) -> list:
        """players = admin-port dicts (id, name); ips = name -> ip from the process."""
        removals = []
        present = set()
        for player in players:
            name = str(player.get("name", ""))
            present.add(f"{name.lower()}|{ips.get(name, '') or ''}")
        # forget anyone who has left (the punt worked) or whose cooldown ran out
        self._recent = {k: t for k, t in self._recent.items()
                        if k in present and now - t < self._cooldown}
        for player in players:
            name = str(player.get("name", ""))
            ip = str(ips.get(name, "") or "")
            entry = bans.check(name, ip, now)
            if entry is None:
                continue
            mark = f"{name.lower()}|{ip}"
            if mark in self._recent:
                continue
            self._recent[mark] = now
            entry.hits += 1
            entry.last_hit_at, entry.last_hit_name, entry.last_hit_ip = now, name, ip
            removals.append(Removal(str(player.get("id", "")), name, ip, entry, dict(player)))
        return removals


# ---- import / export ----------------------------------------------------------------

@dataclass(frozen=True)
class ParsedLine:
    raw: str
    entry: Optional[BanEntry]
    problem: str = ""          # why it was skipped, in plain words


_DURATION = re.compile(r"^(\d+)\s*([mhdw])$", re.I)
_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_expiry(text: str, now: float) -> Optional[float]:
    """'never'/'' -> None; '7d' / '12h' / '30m' / '2w' -> absolute time;
    'YYYY-MM-DD' -> midnight that day (local)."""
    t = (text or "").strip().lower()
    if not t or t in ("never", "permanent", "perm", "-", "none"):
        return None
    m = _DURATION.match(t)
    if m:
        return now + int(m.group(1)) * _UNITS[m.group(2).lower()]
    try:
        return time.mktime(time.strptime(t[:10], "%Y-%m-%d"))
    except ValueError:
        raise ValueError(f"'{text}' is not an expiry - use 7d, 12h, 30m or a date like 2026-12-31")


def format_expiry(expires_at: Optional[float]) -> str:
    if expires_at is None:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at))


def _split_fields(line: str) -> list[str]:
    for sep in ("|", "\t", ","):
        if sep in line:
            return [part.strip() for part in line.split(sep)]
    if "=" in line:                       # Babstats-style ini: value=reason
        left, right = line.split("=", 1)
        return [left.strip(), right.strip()]
    return [line.strip()]


def parse_import(text: str, *, now: float, added_by: str = "import") -> list[ParsedLine]:
    """One entry per line: ``value``, ``value | reason``, ``value | reason | expiry``.
    Value = a name, an IP, ``198.51.*``, ``198.51.0.0/16`` or ``a.b.c.d-e.f.g.h``.
    Blank lines, ``#``/``//``/``;`` comments and ``[sections]`` are ignored."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//", ";", "[")):
            continue
        if line.upper().startswith("BAN "):          # JO's own banlist.txt: BAN "pcid" "name"
            quoted = re.findall(r'"([^"]*)"', line)
            if len(quoted) >= 2 and quoted[1].strip():
                line = quoted[1].strip()
            else:
                out.append(ParsedLine(raw, None, "JO banlist line without a name")); continue
        fields = _split_fields(line)
        value = fields[0].strip().strip('"')
        if not value:
            out.append(ParsedLine(raw, None, "no name or address on this line")); continue
        reason = fields[1].strip() if len(fields) > 1 else ""
        expiry_text = fields[2].strip() if len(fields) > 2 else ""
        if looks_like_ip_pattern(value):
            problem = ip_pattern_problem(value)
            if problem:
                out.append(ParsedLine(raw, None, problem)); continue
            kind = KIND_IP
        else:
            if len(value) > 32:
                out.append(ParsedLine(raw, None, "a name longer than 32 characters cannot be a JO name")); continue
            kind = KIND_NAME
        try:
            expires = parse_expiry(expiry_text, now)
        except ValueError as exc:
            out.append(ParsedLine(raw, None, str(exc))); continue
        out.append(ParsedLine(raw, BanEntry(kind, value, reason, added_by, now, expires)))
    return out


def export_lines(entries: Iterable[BanEntry]) -> str:
    """The same shape parse_import reads, so a file round-trips."""
    rows = ["# WolfRAT ban list - one per line: value | reason | expiry (never, 7d, 12h or a date)"]
    for e in entries:
        expiry = "never" if e.expires_at is None else time.strftime("%Y-%m-%d", time.localtime(e.expires_at))
        rows.append(f"{e.value} | {e.reason.replace('|', '/')} | {expiry}")
    return "\n".join(rows) + "\n"


# ---- firewall copy-paste ------------------------------------------------------------

def firewall_address(pattern: str) -> str:
    """What Windows' New-NetFirewallRule accepts for our pattern."""
    p = pattern.strip()
    if _WILD.match(p):
        parts = p.split(".")[:-1]
        lo = ".".join(parts + ["0"] * (4 - len(parts)))
        hi = ".".join(parts + ["255"] * (4 - len(parts)))
        return f"{lo}-{hi}"
    if _RANGE.match(p):
        return "-".join(x.strip() for x in p.split("-"))
    return p


def firewall_rule_name(pattern: str) -> str:
    return RULE_PREFIX + pattern.strip().replace('"', "")


def firewall_block_command(pattern: str) -> str:
    """One PowerShell line, run as administrator on the machine hosting the
    server.  Blocks every UDP packet from the address, which is all Joint Ops
    speaks, and nothing else."""
    return (f'New-NetFirewallRule -DisplayName "{firewall_rule_name(pattern)}" -Direction Inbound '
            f'-Action Block -Protocol UDP -RemoteAddress {firewall_address(pattern)} '
            f'-Profile Any -Enabled True | Out-Null; "Blocked {pattern.strip()}"')


def firewall_unblock_command(pattern: str) -> str:
    return (f'Remove-NetFirewallRule -DisplayName "{firewall_rule_name(pattern)}" '
            f'-ErrorAction SilentlyContinue; "Unblocked {pattern.strip()}"')
