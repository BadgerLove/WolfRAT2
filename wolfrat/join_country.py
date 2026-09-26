"""Where a first-time joiner is from - one public chat line after the welcome.

Never looks an address up itself: it reads the Bans tab's connection-check
cache (one lookup per IP, shared, cached a week), so the ip-api 45/min budget
is spent once per player however many features want the answer.  No Qt.

Rules (Dale, 2026-09-23):
  * first-time joiners only, optional, off by default;
  * public (the point is telling the server);
  * VPN / proxy / hosting addresses: silent by default - Russian regulars can
    only play through one - or an optional "joined on a VPN" line instead;
  * UK players by nation (Scotland, England, Wales, Northern Ireland), others
    "Region, Country" when it fits the 59-character chat line, else country.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from wolfrat.ip_checks import Verdict, is_private
from wolfrat.admin_commands import MAX_CHAT_LEN

CHAT_MAX_LEN = MAX_CHAT_LEN   # 59: JO cuts every line there
DEFAULT_TEMPLATE = "{player} joined from {place}"
DEFAULT_VPN_TEMPLATE = "{player} joined on a VPN"
GIVE_UP_SECONDS = 60          # no answer this long after the welcome: say nothing
UK_NATIONS = ("England", "Scotland", "Wales", "Northern Ireland")

WAIT, SAY, SKIP = "wait", "say", "skip"


@dataclass(frozen=True)
class Decision:
    state: str                # WAIT / SAY / SKIP
    text: str = ""            # the chat line when SAY
    reason: str = ""          # why, for the log and the 'Last:' line


def ascii_only(text: str) -> str:
    """'Baden-Württemberg' -> 'Baden-Wurttemberg'.  Game chat is 8-bit."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in folded if ord(ch) < 128).strip()


def fill(template: str, player: str, place: str = "", country: str = "", region: str = "") -> str:
    return (str(template or "").replace("{player}", player).replace("{place}", place)
            .replace("{country}", country).replace("{region}", region)).strip()


def place_options(verdict: Verdict) -> list:
    """Most specific first.  The caller picks the first that fits the line."""
    country = ascii_only(verdict.country_name) or verdict.country
    region = ascii_only(verdict.region)
    if verdict.country == "GB" and region in UK_NATIONS:
        return [region, country]
    options = []
    if region and country and region.lower() != country.lower():
        options.append(f"{region}, {country}")
    if country:
        options.append(country)
    return options


def decide(player: str, ip: str, verdict: Optional[Verdict], template: str,
           mention_vpn: bool = False, vpn_template: str = DEFAULT_VPN_TEMPLATE) -> Decision:
    if not ip:
        return Decision(WAIT, reason="no IP yet (the server must be on this PC)")
    if is_private(ip):
        return Decision(SKIP, reason="local network address")
    if verdict is None:
        return Decision(WAIT, reason="waiting for the connection check")
    if verdict.error:
        return Decision(SKIP, reason=f"lookup failed ({verdict.error})")
    if verdict.proxy or verdict.hosting:
        if not mention_vpn:
            return Decision(SKIP, reason="on a VPN - kept quiet")
        return Decision(SAY, fill(vpn_template or DEFAULT_VPN_TEMPLATE, player)[:CHAT_MAX_LEN], "on a VPN")
    options = place_options(verdict)
    if not options:
        return Decision(SKIP, reason="lookup gave no country")
    country = ascii_only(verdict.country_name) or verdict.country
    region = ascii_only(verdict.region)
    for place in options:
        line = fill(template or DEFAULT_TEMPLATE, player, place, country, region)
        if len(line) <= CHAT_MAX_LEN:
            return Decision(SAY, line, place)
    return Decision(SAY, line[:CHAT_MAX_LEN], place)     # very long name: the shortest, cut


@dataclass
class _Pending:
    name: str
    due: float                # earliest time to say it (after the welcome)
    give_up: float


class CountryAnnouncer:
    """Queue of first-time joiners waiting for their line.

    connection(name) -> (ip, verdict or None)   the Bans tab's cache, no lookup
    request(name)                               ask the Bans tab's one checker
                                                (it skips cached / queued IPs)
    say(text), log(text)
    """

    def __init__(self, connection: Callable[[str], tuple], request: Callable[[str], None],
                 say: Callable[[str], None], log: Callable[[str], None] = lambda _t: None,
                 clock: Callable[[], float] = time.time):
        self._connection, self._request = connection, request
        self._say, self._log, self._clock = say, log, clock
        self._pending: dict[str, _Pending] = {}
        self.template = DEFAULT_TEMPLATE
        self.mention_vpn = False
        self.vpn_template = DEFAULT_VPN_TEMPLATE
        self.last = ""            # "Name - what happened", shown under the settings

    def queue(self, name: str, delay: float) -> None:
        now = self._clock()
        self._pending[name.lower()] = _Pending(name, now + delay, now + delay + GIVE_UP_SECONDS)
        self._request(name)

    def pending(self) -> int:
        return len(self._pending)

    def clear(self) -> None:
        self._pending.clear()

    def tick(self) -> None:
        now = self._clock()
        for key, item in list(self._pending.items()):
            ip, verdict = self._connection(item.name)
            decision = decide(item.name, ip, verdict, self.template, self.mention_vpn, self.vpn_template)
            if decision.state == WAIT:
                if now >= item.give_up:
                    self._finish(key, item.name, f"skipped - {decision.reason}, gave up")
                else:
                    self._request(item.name)
                continue
            if now < item.due:
                continue                   # answer's in, but the welcome goes first
            if decision.state == SKIP:
                self._finish(key, item.name, f"skipped - {decision.reason}")
                continue
            self._say(decision.text)
            self._finish(key, item.name, f'said "{decision.text}"')

    def _finish(self, key: str, name: str, what: str) -> None:
        self._pending.pop(key, None)
        self.last = f"{name} - {what}"
        self._log(f"Where from: {self.last}")
