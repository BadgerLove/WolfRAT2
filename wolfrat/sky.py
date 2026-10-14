"""Sky colours: fog, horizon, sunlight, sky light, ground light and lightning
colour for a running match (Dale, 2026-10-09; proven in game 2026-10-05).

How it gets to the players
--------------------------
Joint Ops already has mission-script actions for every colour (``fogcolor``,
``skyfogcolor``, ``sun``, ``sky``, ``ground``, ``lightning``), but on a stock
server they never reach the players and the day/night tables overwrite them
62 times a second.  Two things fix that, and WolfRAT needs both:

* a **sky-colour server exe** (and the same patch on the players' clients)
  that broadcasts those actions and holds a colour against day/night; and
* the **sky block** in the server's ``server.wac`` - a few lines that run the
  colour actions when WolfRAT asks.  WolfRAT asks the same way it asks for
  lightning: script variables are plain memory, so it writes the colours into
  G220-G237, the fade into G243 and a command number into G248.  The script
  runs once a second, does the command and puts G248 back to 0.

So everything here is one command per second at most.  A whole scene is ONE
command (110), which is why a scene fades in all at once.

Things that are true of the game and shaped this file
-----------------------------------------------------
* Fog and horizon are **half scale**: the engine doubles them when it draws.
  Everything WolfRAT shows is the colour players see; fog and horizon are
  halved on the way out.  Sun, sky and ground are full scale.
* Any value over 255 on fog, horizon, sun, sky or ground = back to the map's
  own colour (999 by convention).  That snaps; it does not fade.
* Lightning colour has NO "back to the map": the game stores whatever it is
  given until the map changes.  WolfRAT reads the map's own lightning colour
  before it first changes it and writes that back for "off".
* A map change puts every colour back to the map's own on its own.
* Players who join later miss a colour that was sent before they came, so
  WolfRAT sends the current look again when the player count goes up, and
  every RESEND_SECONDS while one is showing (no fade: nothing moves for the
  players who already have it).
* After a map load the script sits still until a player is connected, so a
  command can wait in G248 a long time.  Never write a new one over it.
"""

from __future__ import annotations

import os
import re
import struct
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Optional

# --------------------------------------------------------------------------
# Memory map (same exe build as weather.FINGERPRINT)
# --------------------------------------------------------------------------

G_BASE = 0x00C6BA40                 # WAC global G<n> lives at G_BASE + 4n


def g_address(n: int) -> int:
    return G_BASE + 4 * n


COMMAND_SLOT = 248                  # G248: what WolfRAT wants; the script resets it to 0
FADE_SLOT = 243                     # G243: fade seconds for the command
LOOK_FIRST_SLOT = 220               # G220..G237: 6 colours x R, G, B
SINGLE_SLOTS = (240, 241, 242)      # G240..G242: one colour (commands 101-107)

CMD_LOOK = 110                      # the whole look from G220-G237
CMD_ALL_OFF = 99                    # fog, horizon, sun, sky, ground back to the map
# Our own ping (96 -> 95).  The 2026-10-05 TEST block answered 98 -> 97 but
# knows nothing of 110, so its answer must not count as ours.
CMD_PING, CMD_PONG = 96, 95
CMD_FREE = (0, CMD_PONG, 97)        # G248 values that mean "nothing waiting"
PING_TIMEOUT = 5.0                  # seconds, with a player on the server

OFF = 999                           # "the map's own colour" for the held blocks

LIGHTNING_COLOUR = 0x026C646C       # packed 0xRRGGBB, written by the `lightning` action
ACTION_TABLE = 0x0082D290           # WAC action table, 44-byte entries, kind byte first
ACTION_SIZE = 44
BROADCAST_KIND = 0x0C               # kind byte of an action the server sends to players
# A sky-colour server exe broadcasts these (stock = 0x04, server only).
SKY_ACTIONS = (("fogcolor", 136), ("skyfogcolor", 138), ("sun", 130), ("sky", 131),
               ("ground", 132), ("lightning", 122))

RESEND_SECONDS = 30
DEFAULT_FADE = 3
MAX_MINUTES = 240
DEMO_STEP_SECONDS = 8
DEMO_FADE = 3


@dataclass(frozen=True)
class Element:
    key: str
    label: str
    help: str
    half_scale: bool
    action: str


ELEMENTS = (
    Element("fog", "Fog", "The fog itself - the colour things fade into in the distance.",
            True, "fogcolor"),
    Element("horizon", "Horizon", "The band of sky along the horizon.", True, "skyfogcolor"),
    Element("sun", "Sunlight", "Sunlight on the ground, buildings and players.", False, "sun"),
    Element("sky", "Sky light", "Light from above - strongest in shadows and on shaded sides.",
            False, "sky"),
    Element("ground", "Ground light", "Light bouncing up from the ground: a glow on walls "
            "and undersides.", False, "ground"),
    Element("lightning", "Lightning", "The colour of a lightning flash. Shows best when the "
            "scene is dark.", False, "lightning"),
)
ELEMENT_KEYS = tuple(e.key for e in ELEMENTS)
HELD_KEYS = ELEMENT_KEYS[:5]        # the ones a value over 255 hands back to the map

RGB = tuple  # (r, g, b), 0..255 each, as players see it


# --------------------------------------------------------------------------
# Looks (scenes)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    """What each element should be.  A missing element = the map's own colour."""

    colours: dict = field(default_factory=dict)      # element key -> (r, g, b) as seen
    fade: int = DEFAULT_FADE

    def is_empty(self) -> bool:
        return not any(self.colours.get(k) is not None for k in ELEMENT_KEYS)

    def to_json(self) -> dict:
        return {"colours": {k: to_hex(v) for k, v in self.colours.items() if v is not None},
                "fade": int(self.fade)}

    @classmethod
    def from_json(cls, data) -> "Look":
        if not isinstance(data, dict):
            return cls()
        colours = {}
        for key, value in (data.get("colours") or {}).items():
            rgb = parse_colour(value) if isinstance(value, str) else None
            if key in ELEMENT_KEYS and rgb is not None:
                colours[key] = rgb
        return cls(colours, clamp(int(data.get("fade", DEFAULT_FADE) or 0), 0, 60))


@dataclass(frozen=True)
class Scene:
    key: str            # what a mod types: "!sky bloodmoon"
    label: str          # what the page and the chat show: "Blood Moon"
    look: Look
    builtin: bool = True


def _scene(key, label, fade=DEFAULT_FADE, **colours) -> Scene:
    return Scene(key, label, Look({k: tuple(v) for k, v in colours.items()}, fade))


# The first six are the 2026-10-05 scenes exactly as Dale saw them (fog and
# horizon doubled back to what is drawn; sun/sky/ground were sent at the
# values below).  Zombie is his green sky light + black fog from the showcase.
# The rest are new on 2026-10-09 and unseen in game - tune them on the page.
BUILTIN_SCENES = (
    _scene("zombie", "Zombie", fog=(0, 0, 0), horizon=(0, 0, 0), sky=(40, 220, 60),
           lightning=(120, 255, 80)),
    _scene("bloodmoon", "Blood Moon", fog=(180, 20, 20), horizon=(220, 30, 30), sun=(100, 30, 30),
           sky=(60, 10, 10), ground=(40, 5, 5), lightning=(255, 0, 0)),
    _scene("toxic", "Toxic", fog=(60, 180, 40), horizon=(80, 220, 60), sun=(70, 110, 40),
           sky=(20, 60, 15), ground=(15, 40, 10), lightning=(60, 255, 40)),
    _scene("arctic", "Arctic", fog=(200, 230, 254), horizon=(220, 240, 254), sun=(90, 105, 127),
           sky=(70, 85, 110), ground=(60, 70, 90)),
    _scene("sandstorm", "Sandstorm", fog=(240, 170, 80), horizon=(250, 190, 100),
           sun=(120, 95, 60), sky=(80, 60, 30), ground=(60, 45, 20)),
    _scene("night", "Night", fog=(10, 16, 50), horizon=(16, 24, 70), sun=(15, 20, 45),
           sky=(8, 10, 30), ground=(4, 5, 15)),
    _scene("inferno", "Inferno", fog=(240, 100, 10), horizon=(254, 140, 20), sun=(127, 70, 20),
           sky=(90, 30, 5), ground=(100, 30, 0), lightning=(255, 120, 0)),
    _scene("golden", "Golden Hour", fog=(230, 160, 90), horizon=(255, 170, 80),
           sun=(255, 180, 110), sky=(120, 90, 70), ground=(90, 60, 40)),
    _scene("dawn", "Dawn", fog=(200, 150, 170), horizon=(255, 180, 150), sun=(255, 200, 170),
           sky=(110, 100, 140), ground=(70, 60, 80)),
    _scene("dusk", "Dusk", fog=(70, 50, 110), horizon=(140, 70, 120), sun=(150, 90, 120),
           sky=(50, 40, 90), ground=(30, 25, 50)),
    _scene("eclipse", "Eclipse", fog=(20, 15, 20), horizon=(255, 140, 40), sun=(40, 30, 30),
           sky=(20, 20, 30), ground=(10, 8, 10), lightning=(255, 200, 120)),
    _scene("mars", "Mars", fog=(190, 90, 50), horizon=(220, 120, 70), sun=(230, 140, 100),
           sky=(110, 50, 30), ground=(90, 40, 20)),
    _scene("alien", "Alien", fog=(110, 30, 150), horizon=(160, 60, 220), sun=(150, 90, 220),
           sky=(60, 200, 180), ground=(80, 20, 100), lightning=(180, 60, 255)),
    _scene("underwater", "Underwater", fog=(10, 70, 90), horizon=(20, 110, 130),
           sun=(60, 150, 170), sky=(20, 80, 110), ground=(10, 50, 60)),
    _scene("haunted", "Haunted", fog=(90, 70, 120), horizon=(60, 50, 90), sun=(90, 90, 120),
           sky=(60, 40, 90), ground=(40, 120, 60), lightning=(180, 120, 255)),
    _scene("aurora", "Aurora", fog=(10, 40, 50), horizon=(40, 200, 150), sun=(60, 90, 140),
           sky=(30, 160, 120), ground=(20, 40, 60), lightning=(80, 255, 200)),
    _scene("storm", "Storm Front", fog=(70, 80, 95), horizon=(90, 100, 115), sun=(110, 115, 130),
           sky=(60, 70, 85), ground=(40, 45, 55), lightning=(200, 220, 255)),
    _scene("winter", "Nuclear Winter", fog=(150, 150, 140), horizon=(170, 165, 150),
           sun=(140, 140, 130), sky=(90, 90, 90), ground=(60, 60, 55)),
    _scene("smog", "Smog", fog=(130, 110, 70), horizon=(150, 130, 90), sun=(170, 150, 110),
           sky=(90, 80, 60), ground=(60, 50, 35)),
    _scene("jungle", "Jungle Mist", fog=(110, 140, 100), horizon=(140, 170, 130),
           sun=(190, 210, 160), sky=(80, 110, 70), ground=(50, 70, 40)),
    _scene("ghost", "Ghost", fog=(230, 230, 240), horizon=(240, 240, 250), sun=(220, 220, 235),
           sky=(180, 180, 200), ground=(150, 150, 170)),
    _scene("matrix", "Matrix", fog=(0, 60, 10), horizon=(0, 120, 30), sun=(60, 200, 80),
           sky=(0, 120, 30), ground=(0, 60, 10), lightning=(0, 255, 60)),
    _scene("synthwave", "Synthwave", fog=(120, 20, 120), horizon=(255, 60, 180), sun=(255, 80, 200),
           sky=(40, 200, 255), ground=(120, 0, 160), lightning=(255, 40, 200)),
)

# words a mod types that are not scene names
RESERVED_WORDS = ("off", "demo", "stop", "list", "help", "normal", "map", "clear")


def scene_key(name: str) -> str:
    """'Blood Moon' -> 'bloodmoon': what a mod types after !sky."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(clamp(c, 0, 255) for c in rgb)


def parse_colour(text: str) -> Optional[RGB]:
    text = (text or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return None
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def unpack_rgb(packed: int) -> RGB:
    return ((packed >> 16) & 255, (packed >> 8) & 255, packed & 255)


def sent_value(element: str, rgb: Optional[RGB]) -> tuple:
    """What the script gets for one element: half scale for fog/horizon, 999 = map's own."""
    if rgb is None:
        return (OFF, OFF, OFF)
    half = element in ("fog", "horizon")
    return tuple(clamp((c + 1) // 2 if half else c, 0, 127 if half else 255) for c in rgb)


# --------------------------------------------------------------------------
# The server.wac block
# --------------------------------------------------------------------------

FILENAME = "server.wac"
BEGIN = "// >>> WolfRAT sky colours (written by WolfRAT - change it in WolfRAT, not here)"
END = "// <<< WolfRAT sky colours"
# Test blocks from the 2026-10-05 work: replaced by ours on install.
OLD_BLOCKS = (
    ("// >>> Sky colour TEST", "// <<< Sky colour TEST"),
    ("// >>> Fog colour TEST", "// <<< Fog colour TEST"),
)
_SINGLE = ((101, ("fogcolor", "skyfogcolor")), (102, ("fogcolor",)), (103, ("skyfogcolor",)),
           (104, ("sun",)), (105, ("sky",)), (106, ("ground",)), (107, ("lightning",)))


def block_lines() -> list:
    slots = iter(range(LOOK_FIRST_SLOT, LOOK_FIRST_SLOT + 3 * len(ELEMENTS)))
    lines = [
        BEGIN,
        "// Sky colours for WolfRAT. Needs a sky-colour server exe AND patched clients.",
        "// Does nothing unless WolfRAT asks. G248 = what to do (back to 0 when done):",
        "//   110 whole look from G220-G237 (fog, horizon, sun, sky, ground, lightning)",
        "//   101-107 one colour from G240-G242, 99 all back to the map, 96 ping -> 95",
        "//   G243 = fade seconds. 999 = the map's own colour.",
        f"if eq(G{COMMAND_SLOT}, {CMD_LOOK}) then",
        f"colorfade(G{FADE_SLOT})",
    ]
    for element in ELEMENTS:
        r, g, b = next(slots), next(slots), next(slots)
        lines.append(f"{element.action}(G{r}, G{g}, G{b})")
    lines += [f"set(G{COMMAND_SLOT}, 0)", "endif"]
    single = ", ".join(f"G{n}" for n in SINGLE_SLOTS)
    for code, actions in _SINGLE:
        lines += [f"if eq(G{COMMAND_SLOT}, {code}) then", f"colorfade(G{FADE_SLOT})"]
        lines += [f"{action}({single})" for action in actions]
        lines += [f"set(G{COMMAND_SLOT}, 0)", "endif"]
    lines += [f"if eq(G{COMMAND_SLOT}, {CMD_ALL_OFF}) then"]
    lines += [f"{e.action}({OFF},{OFF},{OFF})" for e in ELEMENTS if e.key in HELD_KEYS]
    lines += [f"set(G{COMMAND_SLOT}, 0)", "endif",
              f"if eq(G{COMMAND_SLOT}, {CMD_PING}) then", f"set(G{COMMAND_SLOT}, {CMD_PONG})", "endif",
              END]
    return lines


def block_text() -> str:
    return "\r\n".join(block_lines()) + "\r\n"


class SkyError(Exception):
    """Something to tell the admin in plain words."""


def _strip(text: str, begin: str, end: str) -> str:
    while begin in text:
        head, rest = text.split(begin, 1)
        tail = rest.split(end, 1)[1] if end in rest else ""
        tail = tail.split("\n", 1)[1] if "\n" in tail else ""      # rest of the end-marker line
        text = head.rstrip("\r\n") + ("\n\n" if head.strip() else "") + tail.lstrip("\r\n")
    return text


def block_installed(server_dir) -> bool:
    try:
        with open(os.path.join(str(server_dir), FILENAME), "rb") as handle:
            return BEGIN.encode("ascii") in handle.read()
    except OSError:
        return False


def old_test_block(server_dir) -> bool:
    try:
        with open(os.path.join(str(server_dir), FILENAME), "rb") as handle:
            data = handle.read()
        return any(begin.encode("ascii") in data for begin, _ in OLD_BLOCKS)
    except OSError:
        return False


def install_block(server_dir, remove: bool = False) -> str:
    """Write (or take out) our block in server.wac.  Everything else in the
    file is kept; the old fog/sky TEST blocks are replaced.  Windows line
    endings throughout - with bare LF the 2004 compiler reads the whole file as
    one comment.  Returns "installed", "updated", "removed" or "already"."""
    path = os.path.join(str(server_dir), FILENAME)
    original = b""
    if os.path.exists(path):
        with open(path, "rb") as handle:
            original = handle.read()
    text = original.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
    had_ours = BEGIN in text
    base = _strip(text, BEGIN, END)
    for begin, end in OLD_BLOCKS:
        base = _strip(base, begin, end)
    base = base.rstrip("\n")
    if remove:
        new_text = base + ("\n" if base else "")
    else:
        new_text = (base + "\n\n" if base else "") + block_text().replace("\r\n", "\n")
    raw = new_text.replace("\n", "\r\n").encode("latin-1")
    if raw == original:
        return "already" if not remove else "removed"
    if original:
        backup = path + ".bak-wolfrat-sky"
        if not os.path.exists(backup):
            try:
                with open(backup, "wb") as handle:
                    handle.write(original)
            except OSError:
                pass
    temp = path + ".wolfrat-new"
    try:
        with open(temp, "wb") as handle:
            handle.write(raw)
        with open(temp, "rb") as handle:
            if handle.read() != raw:
                raise OSError("read-back mismatch")
        os.replace(temp, path)
    except OSError as exc:
        raise SkyError("WolfRAT could not write server.wac into the server folder "
                       f"({exc}). Run WolfRAT as administrator.") from exc
    if remove:
        return "removed"
    return "updated" if had_ours or raw != original and BEGIN not in original.decode("latin-1") \
        and any(b.encode() in original for b, _ in OLD_BLOCKS) else "installed"


# --------------------------------------------------------------------------
# Talking to the running server
# --------------------------------------------------------------------------

def _get(memory, address: int) -> int:
    return struct.unpack("<i", memory.read(address, 4))[0]


def _put(memory, address: int, value: int) -> None:
    memory.write(address, struct.pack("<i", int(value)))


def server_can_do_sky(memory) -> bool:
    """True when the running exe broadcasts every colour action (a sky-colour build)."""
    try:
        return all(memory.read(ACTION_TABLE + ACTION_SIZE * index, 1)[0] == BROADCAST_KIND
                   for _, index in SKY_ACTIONS)
    except Exception:
        return False


@dataclass(frozen=True)
class Command:
    code: int
    writes: tuple = ()              # ((G number, value), ...) written before the code
    label: str = ""
    # CMD_LOOK only: the lightning colour, or None = the map's own (read from
    # the server before WolfRAT first changes it on this map)
    lightning: Optional[RGB] = None


def look_command(look: Look, fade: Optional[int] = None, label: str = "") -> Command:
    writes = []
    slot = LOOK_FIRST_SLOT
    for element in ELEMENTS:
        if element.key != "lightning":
            writes += list(zip(range(slot, slot + 3), sent_value(element.key,
                                                                 look.colours.get(element.key))))
        slot += 3
    writes.append((FADE_SLOT, clamp(look.fade if fade is None else fade, 0, 60)))
    lightning = look.colours.get("lightning")
    return Command(CMD_LOOK, tuple(writes), label,
                   tuple(lightning) if lightning is not None else None)


LIGHTNING_SLOTS = tuple(range(LOOK_FIRST_SLOT + 15, LOOK_FIRST_SLOT + 18))


class Sender:
    """One command at a time, only when the script has finished the last one."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._queue: list = []
        self.waiting_since: Optional[float] = None   # our command sits unread in G248
        self.last_sent: Optional[Command] = None

    def busy(self) -> bool:
        return bool(self._queue) or self.waiting_since is not None

    def clear(self) -> None:
        self._queue.clear()

    def push(self, command: Command, replace_looks: bool = True) -> None:
        if replace_looks and command.code in (CMD_LOOK, CMD_ALL_OFF):
            # only the newest look matters
            self._queue = [c for c in self._queue if c.code not in (CMD_LOOK, CMD_ALL_OFF)]
        self._queue.append(command)

    def queued(self) -> list:
        return list(self._queue)

    def pump(self, memory, lightning_home: Callable[[], RGB],
             only_ping: bool = False) -> Optional[Command]:
        """Send the next command if G248 is free.  Returns what was sent.
        ``lightning_home()`` = the map's own lightning colour (it may read the
        server, so it is only called when a look is actually going out)."""
        slot = g_address(COMMAND_SLOT)
        current = _get(memory, slot)
        if current not in CMD_FREE:
            if self.waiting_since is None:
                self.waiting_since = self._clock()
            return None
        self.waiting_since = None
        ready = [c for c in self._queue if c.code == CMD_PING] if only_ping else self._queue
        if not ready:
            return None
        command = ready[0]
        self._queue.remove(command)
        writes = list(command.writes)
        if command.code == CMD_LOOK:
            home = lightning_home()      # always first: it must be read before we change it
            rgb = command.lightning if command.lightning is not None else home
            writes += list(zip(LIGHTNING_SLOTS, (clamp(c, 0, 255) for c in rgb)))
        for n, value in writes:
            _put(memory, g_address(n), value)
        _put(memory, slot, command.code)       # the command last: the script acts on it
        self.waiting_since = self._clock()
        self.last_sent = command
        return command

    def slot(self, memory) -> int:
        return _get(memory, g_address(COMMAND_SLOT))

    def take_back(self, memory, code: int) -> bool:
        """Our command was never read (no script to read it): clear the slot."""
        if self.slot(memory) == code:
            _put(memory, g_address(COMMAND_SLOT), 0)
            self.waiting_since = None
            return True
        return False

    def answered(self, memory) -> bool:
        """True once the script has taken the last command (G248 back to 0 / 97)."""
        try:
            return _get(memory, g_address(COMMAND_SLOT)) in CMD_FREE
        except Exception:
            return False


# --------------------------------------------------------------------------
# Coloured lightning: "!lightning red"
# --------------------------------------------------------------------------

# The ten colours of the 2026-10-07 coloured storm, seen in game by Dale.
LIGHTNING_WORDS = {
    "red": (255, 0, 0), "orange": (255, 110, 0), "yellow": (255, 230, 0),
    "green": (0, 255, 40), "cyan": (0, 230, 255), "blue": (30, 60, 255),
    "purple": (170, 0, 255), "pink": (255, 40, 190), "white": (255, 255, 255),
    "blood": (140, 0, 0),
}
CMD_LIGHTNING = 107                 # one colour from G240-G242 into `lightning`
STRIKE_HOLD = 2.5                   # seconds the colour stays before it goes back
LIGHTNING_USAGE = "!lightning [red|yellow|green|blue|#ff00ff] [far]"


@dataclass(frozen=True)
class LightningRequest:
    colour: Optional[RGB] = None    # None = the colour that is showing (a plain flash)
    far: bool = False
    message: str = ""               # set = a usage reply, nothing happens


def parse_lightning(args: list) -> LightningRequest:
    colour, far = None, False
    for word in (str(a).lower() for a in args if str(a).strip()):
        if word in ("far", "distant"):
            far = True
        elif word in LIGHTNING_WORDS and colour is None:
            colour = LIGHTNING_WORDS[word]
        elif parse_colour(word) is not None and colour is None:
            colour = parse_colour(word)
        else:
            return LightningRequest(message=LIGHTNING_USAGE)
    return LightningRequest(colour, far)


def lightning_command(rgb: RGB, label: str = "") -> Command:
    r, g, b = (clamp(c, 0, 255) for c in rgb)
    return Command(CMD_LIGHTNING, ((SINGLE_SLOTS[0], r), (SINGLE_SLOTS[1], g),
                                   (SINGLE_SLOTS[2], b), (FADE_SLOT, 0)), label)


# --------------------------------------------------------------------------
# Chat commands
# --------------------------------------------------------------------------

CHAT_USAGE = "!sky <scene> [minutes], !sky off, !sky demo, !sky list"


@dataclass(frozen=True)
class SkyRequest:
    kind: str                       # scene | off | demo | list | usage
    scene: Optional[Scene] = None
    minutes: Optional[int] = None   # None = until the map changes
    message: str = ""


def parse_chat(args: list, scenes: Iterable[Scene]) -> SkyRequest:
    words = [str(a) for a in args if str(a).strip()]
    if not words or words[0].lower() in ("help", "?"):
        return SkyRequest("usage", message=CHAT_USAGE)
    first = words[0].lower()
    if first in ("off", "normal", "map", "clear", "stop"):
        return SkyRequest("off")
    if first == "demo":
        return SkyRequest("demo")
    if first == "list":
        return SkyRequest("list")
    by_key = {s.key: s for s in scenes}
    # "!sky blood moon 10": glue the words back together until one matches
    minutes = None
    if len(words) > 1 and words[-1].isdigit():
        minutes = int(words[-1])
        words = words[:-1]
        if not 1 <= minutes <= MAX_MINUTES:
            return SkyRequest("usage", message=f"!sky <scene> [1-{MAX_MINUTES} minutes]")
    wanted = scene_key("".join(words))
    scene = by_key.get(wanted)
    if scene is None:
        starts = [s for s in by_key.values() if s.key.startswith(wanted)] if len(wanted) >= 3 else []
        if len(starts) == 1:
            scene = starts[0]
    if scene is None:
        return SkyRequest("usage", message=f"No sky called '{' '.join(words)[:20]}'. Try !sky list")
    return SkyRequest("scene", scene=scene, minutes=minutes)


def chat_lines(prefix: str, names: list, limit: int = 59) -> list:
    """Split a list of names into chat lines of at most `limit` characters."""
    lines, line = [], prefix
    for name in names:
        piece = (" " if line.endswith(":") else ", ") + name if line else name
        if len(line) + len(piece) > limit:
            lines.append(line + ",")
            line = name
        else:
            line += piece
    if line:
        lines.append(line)
    return lines


# --------------------------------------------------------------------------
# What should be showing: manual > map rule > the map's own
# --------------------------------------------------------------------------

@dataclass
class Active:
    scene: Scene
    source: str                     # "page" | "mod" | "map" | "demo"
    until: Optional[float] = None   # monotonic; None = until the map changes
    who: str = ""


@dataclass(frozen=True)
class Step:
    """What SkyKeeper.tick wants done."""
    command: Optional[Command] = None
    announce: str = ""
    log: str = ""
    flash: bool = False


class SkyKeeper:
    """Decides the look; the page sends it.  No Qt, no memory: testable."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.active: Optional[Active] = None
        self.map_name: Optional[str] = None
        self.lightning_home: Optional[RGB] = None      # the map's own, read before we change it
        self.off_for_this_map = False                 # "off" / last minute: no map rule till next map
        self._last_sent_at: Optional[float] = None
        self._last_players = 0
        self._showing = False                         # we have a look on the server
        self._demo: list = []
        self._demo_index = -1
        self._demo_next: Optional[float] = None
        self._demo_flash_at: Optional[float] = None
        self._demo_before: Optional[Active] = None
        self._pending: list = []

    # -- requests ----------------------------------------------------------
    def show(self, scene: Scene, source: str, minutes: Optional[int] = None, who: str = "") -> Step:
        self.stop_demo(quiet=True)
        until = self._clock() + minutes * 60 if minutes else None
        self.active = Active(scene, source, until, who)
        self.off_for_this_map = False
        span = f" for {minutes} min" if minutes else " until the map changes"
        return self._send_active(announce=f"Sky: {scene.label}{span}.",
                                 log=f"{who or 'WolfRAT'} set the sky to {scene.label}{span}.")

    def off(self, who: str = "", quiet: bool = False) -> Step:
        self.stop_demo(quiet=True)
        self.active = None
        self.off_for_this_map = True
        return self._send_off(announce="" if quiet else "Sky: back to normal.",
                              log=f"{who or 'WolfRAT'} put the map's own sky back.")

    def preview(self, look: Look, label: str = "custom sky") -> Step:
        """The page's "Try it" button: shown until the map changes."""
        return self.show(Scene("custom", label, look, builtin=False), "page")

    def start_demo(self, scenes: list, who: str = "") -> Step:
        if not scenes:
            return Step()
        self._demo_before = self.active if self.active and self.active.source != "demo" else None
        self._demo = list(scenes)
        self._demo_index = -1
        self._demo_next = self._clock()
        return Step(log=f"{who or 'WolfRAT'} started the sky demo ({len(scenes)} skies).",
                    announce=f"Sky demo: {len(scenes)} skies, "
                             f"{DEMO_STEP_SECONDS} s each.")

    def demo_running(self) -> bool:
        return bool(self._demo)

    def demo_progress(self) -> tuple:
        return (self._demo_index + 1, len(self._demo))

    def stop_demo(self, quiet: bool = False) -> Step:
        if not self._demo:
            return Step()
        self._demo = []
        self._demo_index = -1
        self._demo_next = self._demo_flash_at = None
        before, self._demo_before = self._demo_before, None
        if quiet:
            return Step()
        self.active = before
        if before is not None:
            return self._send_active(log="Sky demo finished.")
        self.active = None
        return self._send_off(announce="Sky demo over: back to normal.", log="Sky demo finished.")

    # -- the tick ------------------------------------------------------------
    def tick(self, *, map_name: Optional[str], players: int, settled: bool,
             map_scene: Optional[Scene], minutes_left: Optional[int] = None,
             end_last_minute: bool = False) -> list:
        """Returns a list of Steps.  Call every second or so."""
        now = self._clock()
        steps = []
        if map_name != self.map_name:
            # The load put every colour back by itself: nothing to undo.
            first = self.map_name is None
            self.map_name = map_name
            self.lightning_home = None
            self.off_for_this_map = False
            self._showing = False
            self._last_sent_at = None
            if self._demo:
                self.stop_demo(quiet=True)
            if self.active is not None and not first:
                gone = self.active
                self.active = None
                steps.append(Step(log=f"Map changed: {gone.scene.label} sky ended."))
        if not settled:
            return steps

        if self._demo:
            steps += self._demo_tick(now)
            return steps

        if self.active and self.active.until is not None and now >= self.active.until:
            label = self.active.scene.label
            self.active = None
            steps.append(self._send_off(announce="Sky: back to normal.",
                                        log=f"Timed sky {label} finished."))
            self.off_for_this_map = True       # the mod's time is up; no map rule till next map

        if end_last_minute and minutes_left == 1 and (self.active or self._showing) \
                and not self.off_for_this_map:
            self.active = None
            steps.append(self._send_off(announce="Sky: back to normal for the last minute.",
                                        log="Last minute of the map: sky back to normal."))

        if self.active is None and map_scene is not None and not self.off_for_this_map:
            self.active = Active(map_scene, "map")
            steps.append(self._send_active(announce=f"Sky: {map_scene.label}.",
                                           log=f"Map rule: {map_scene.label} sky on {map_name}."))

        if self.active is not None and self._showing:
            joined = players > self._last_players
            stale = self._last_sent_at is None or now - self._last_sent_at >= RESEND_SECONDS
            if joined or stale:
                # late joiners: same look again, no fade (nothing moves for the others)
                steps.append(Step(command=self._command_for(self.active.scene.look, fade=0,
                                                            label="again")))
                self._last_sent_at = now
        self._last_players = players
        return steps

    def _demo_tick(self, now: float) -> list:
        steps = []
        if self._demo_flash_at is not None and now >= self._demo_flash_at:
            self._demo_flash_at = None
            steps.append(Step(flash=True))
        if self._demo_next is None or now < self._demo_next:
            return steps
        self._demo_index += 1
        if self._demo_index >= len(self._demo):
            steps.append(self.stop_demo())
            return steps
        scene = self._demo[self._demo_index]
        self.active = Active(scene, "demo")
        self._demo_next = now + DEMO_STEP_SECONDS
        if scene.look.colours.get("lightning") is not None:
            self._demo_flash_at = now + DEMO_STEP_SECONDS / 2
        steps.append(self._send_active(
            fade=DEMO_FADE,
            announce=f"Sky demo {self._demo_index + 1}/{len(self._demo)}: {scene.label}",
            log=f"Sky demo: {scene.label}."))
        return steps

    # -- helpers -------------------------------------------------------------
    def _command_for(self, look: Look, fade: Optional[int] = None, label: str = "") -> Command:
        return look_command(look, fade=fade, label=label)

    def home_lightning(self, memory) -> RGB:
        """The map's own lightning colour: read once per map, before WolfRAT's
        first look goes out (the game keeps whatever it is given)."""
        if self.lightning_home is None:
            self.lightning_home = unpack_rgb(_get(memory, LIGHTNING_COLOUR))
        return self.lightning_home

    def _send_active(self, announce: str = "", log: str = "", fade: Optional[int] = None) -> Step:
        self._showing = True
        self._last_sent_at = self._clock()
        return Step(command=self._command_for(self.active.scene.look, fade=fade,
                                              label=self.active.scene.label),
                    announce=announce, log=log)

    def _send_off(self, announce: str = "", log: str = "") -> Step:
        self._showing = False
        self._last_sent_at = None
        # every held colour back to the map; lightning back to what it was
        return Step(command=self._command_for(Look({}), fade=0, label="off"),
                    announce=announce, log=log)

    def describe(self) -> str:
        if self._demo:
            done, total = self.demo_progress()
            name = self.active.scene.label if self.active else "..."
            return f"Demo {done}/{total}: {name}"
        if self.active is None:
            return "The map's own sky."
        tail = ""
        if self.active.until is not None:
            left = max(0, int(self.active.until - self._clock()))
            tail = f" - {left // 60}:{left % 60:02d} left"
        elif self.active.source == "map":
            tail = " - this map's rule"
        else:
            tail = " - until the map changes"
        return f"{self.active.scene.label}{tail}"


__all__ = [
    "BUILTIN_SCENES", "CHAT_USAGE", "Command", "ELEMENTS", "ELEMENT_KEYS", "Look", "Scene",
    "Sender", "SkyError", "SkyKeeper", "SkyRequest", "Step", "block_installed", "block_text",
    "chat_lines", "install_block", "look_command", "old_test_block", "parse_chat",
    "parse_colour", "scene_key", "sent_value", "server_can_do_sky", "to_hex",
]
