"""Build a patched COPY of a player's game exe.  No Qt.

Same rules as the server patcher (see server_patcher): the chosen exe is only
read, the copy is written beside it, every site is checked before anything is
written, and patches already in are left alone.  The bytes are the ones in the
jo-server-patches client scripts (02 vehicle weapon, 05 LAA, 10 grass, 13
spectator mode).

Dale 2026-09-27: the admin picks the exe with Choose exe..., whatever it is
called.  No mission memory here (a server thing).  Grass is offered, off by
default, with a fairness note.  An exe that already carries server patches is
fine to patch too; two pairs share the same spare bytes, and WolfRAT says so
instead of refusing the whole build:
  * client spectator mode (13) and server spectator chat (14): 0x7BFA20..
  * client grass (10) and server spawn protection from game.cfg (12): 0x7472D0
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Optional

from wolfrat import server_patches as sp
from wolfrat.server_patcher import Image, PatchError, _map_cave_tail, _write


def out_names(exe_path: str) -> tuple:
    """(patched copy, backup) names for whatever the exe is called."""
    stem, ext = os.path.splitext(os.path.basename(exe_path))
    return f"{stem}_wolfrat{ext or '.exe'}", f"{stem}_before_wolfrat{ext or '.exe'}"


# ---- the patches (bytes from jo-server-patches) --------------------------------------

_VEHICLE_VA, _VEHICLE_OFF, _VEHICLE_ON = 0x435640, bytes.fromhex("3bc2740c"), bytes.fromhex("eb0e9090")


def _vehicle(img: Image) -> None:                                     # 02
    img.put(_VEHICLE_VA, _VEHICLE_OFF, _VEHICLE_ON, "Vehicle weapon fix")


_SPEC_CAVE, _SPEC_END = 0x7BFA20, 0x7BFD00
_SPEC_TAGS_VA, _SPEC_BAR_VA, _SPEC_FMT_VA = 0x7BFA20, 0x7BFB20, 0x7BFCF0
_SPEC_TAGS = bytes.fromhex(
    "803dec60a800000f84ed000000833dbc204d02020f8de0000000f705341e4d02000400000f85d0000000"
    "60ff35c4184c02833dc4184c0200750ac705c4184c0202000000a1f460a800a310fa7b00c705f460a800"
    "000000008b35e092a8008b3de892a8008b2de492a80083ef01782f89f001eef740240001000075ee8a88"
    "62010000fec980f90177e13b05c85fb70074d96a0050e8f73edeff83c408ebcc8b1d4870a80085db7441"
    "8b7b2c85ff743a31f63b337d34807f0d0074268b472485c0741f8a9062010000feca80fa0177123b05c8"
    "5fb700740a5750e8b23edeff83c40883c60183c740ebc8a110fa7b00a3f460a8008f05c4184c0261c3"
)
_SPEC_BAR = bytes.fromhex(
    "83c418803dec60a800000f84a20100006083ec408b9c248801000053e85fbdc7ff83c4040fbfc883f901"
    "7d05b9010000000fbf831e01000085c07d0231c06bc06499f7f983f8647e05b8640000008904248bb424"
    "8000000083fe047d05be04000000897424288d3c76897c24040fafc7b96400000099f7f9894424088b84"
    "248400000089fad1fa29d08944240c8b84248800000001f0408944241089f099b905000000f7f983f802"
    "7d05b802000000894424148b44247025000000ff8944241889c2c1fa1889542424b900e00000833c2442"
    "7f10b900e0e000833c24217f05b90000e00009c1894c241cb92020200009c1894c242031ed3b6c24147d"
    "578b7c241001ef8b4424248b4c24208b54240c89d6037424045051575657526820144c02e88b3fe1ff83"
    "c41c8b74240885f67e218b54240c01d68b4424248b4c241c5051575657526820144c02e8623fe1ff83c4"
    "1c45eba33b1d10fa7b0075528b04248d4c24305068f0fc7b0051e855adfaff83c40c8b7424288b44240c"
    "0344240401f089f1d1f901c88b542414d1fa03542410d1fe29f28d4c2430ff74241c515250ffb424b400"
    "0000e8550adcff83c41483c4406185ede99945deff"
)
assert len(_SPEC_TAGS) == 251 and len(_SPEC_BAR) == 441
_SPEC_A_VA = 0x42E410
_SPEC_A_OFF, _SPEC_A_ON = (bytes.fromhex("6a03c705bc204d0203000000"),
                           bytes.fromhex("6a00c705bc204d0200000000"))
_SPEC_GUARDS = (0x49A6CA, 0x49A91A, 0x49B991)                       # chat senders + talk key


def _rel(src: int, dst: int) -> bytes:
    return struct.pack("<i", dst - (src + 5))


def spectator_room(img: Image) -> bool:
    return img.is_(_SPEC_CAVE - 0x10, bytes(_SPEC_END - _SPEC_CAVE + 0x10))


def _spectator(img: Image) -> None:                                   # 13
    what = "Spectator mode"
    if not spectator_room(img):
        raise PatchError(f"{what}: the space it needs in the exe is already used.")
    _map_cave_tail(img, what)
    for va, data in ((_SPEC_TAGS_VA, _SPEC_TAGS), (_SPEC_BAR_VA, _SPEC_BAR), (_SPEC_FMT_VA, b"%d%%\0")):
        img.put(va, bytes(len(data)), data, what)
    img.put(_SPEC_A_VA, _SPEC_A_OFF, _SPEC_A_ON, what)
    img.put(0x5CAB95, bytes.fromhex("e806f9e7ff"), b"\xe8" + _rel(0x5CAB95, _SPEC_TAGS_VA), what)
    img.put(0x5A426D, bytes.fromhex("83c41885ed"), b"\xe9" + _rel(0x5A426D, _SPEC_BAR_VA), what)
    for va in _SPEC_GUARDS:
        img.put(va, bytes.fromhex("740d833d28194c02"), bytes.fromhex("eb0d833d28194c02"), what)


_GRASS_RADIUS_VA, _GRASS_HOOK, _GRASS_BACK, _GRASS_CAVE = 0x7DEA3C, 0x601BC5, 0x601BD0, 0x7472D0


def grass_room(img: Image) -> bool:
    return img.is_(_GRASS_CAVE, b"\xcc" * 24)


def _grass(img: Image) -> None:                                       # 10
    what = "Longer grass"
    if not grass_room(img):
        raise PatchError(f"{what}: the space it needs in the exe is already used.")
    img.put(_GRASS_RADIUS_VA, struct.pack("<f", 42.0), struct.pack("<f", 128.0), what)
    if struct.unpack("<f", img.read(0x7C3DD0, 4))[0] != 64.0:
        raise PatchError(f"{what}: this exe is not the build WolfRAT knows.")
    img.put(0x60A45D, bytes.fromhex("d905608e7d00"), bytes.fromhex("d905") + struct.pack("<I", 0x7C3DD0), what)
    img.put(0x7DF1BC, struct.pack("<f", 1 / 22), struct.pack("<f", 1 / (128.0 - 64.0)), what)
    for va in (0x5FF963, 0x5FF973, 0x5FF983, 0x5FF993):
        img.put(va, bytes.fromhex("81f9ffff0000"), bytes.fromhex("81f9") + struct.pack("<I", 0x3FFFFF), what)
    if not img.is_(_GRASS_HOOK, bytes.fromhex("3bc57507897cac2083c501")):
        raise PatchError(f"{what}: this exe has different bytes there, so WolfRAT will not touch it.")
    cave = _grass_cave()
    img.put(_GRASS_CAVE, b"\xcc" * len(cave), cave, what)
    img.put(_GRASS_HOOK, bytes.fromhex("3bc5750789"), b"\xe9" + _rel(_GRASS_HOOK, _GRASS_CAVE), what)


def _grass_cave() -> bytes:
    cave = bytes.fromhex("3bc5750c83fd407d07897cac2083c501")
    return cave + b"\xe9" + _rel(_GRASS_CAVE + len(cave), _GRASS_BACK)


def _remove_grass(img: Image) -> None:                               # 10, undone
    """Every grass byte back to the game's own (Dale 2026-09-27: players must
    be able to untick grass).  Only an exe with exactly our grass is touched."""
    what = "Removing longer grass"
    img.put(_GRASS_RADIUS_VA, struct.pack("<f", 128.0), struct.pack("<f", 42.0), what)
    img.put(0x60A45D, bytes.fromhex("d905") + struct.pack("<I", 0x7C3DD0), bytes.fromhex("d905608e7d00"), what)
    img.put(0x7DF1BC, struct.pack("<f", 1 / (128.0 - 64.0)), struct.pack("<f", 1 / 22), what)
    for va in (0x5FF963, 0x5FF973, 0x5FF983, 0x5FF993):
        img.put(va, bytes.fromhex("81f9") + struct.pack("<I", 0x3FFFFF), bytes.fromhex("81f9ffff0000"), what)
    cave = _grass_cave()
    img.put(_GRASS_HOOK, b"\xe9" + _rel(_GRASS_HOOK, _GRASS_CAVE), bytes.fromhex("3bc5750789"), what)
    img.put(_GRASS_CAVE, cave, b"\xcc" * len(cave), what)


# ---- what the exe has now ---------------------------------------------------------

@dataclass
class State:
    exe_path: str
    laa: bool
    vehicle: Optional[bool]         # None = cannot tell
    spectator: Optional[bool]
    grass: Optional[bool]
    spectator_room: bool            # it can be added
    grass_room: bool
    server_spectator_chat: bool = False   # server patches sharing that space
    server_spawn_config: bool = False

    @property
    def known(self) -> bool:
        return self.vehicle is not None and self.spectator is not None


def _two_way(img: Image, va: int, off: bytes, on: bytes) -> Optional[bool]:
    return True if img.is_(va, on) else False if img.is_(va, off) else None


def state_of(exe_path: str, data: bytes) -> State:
    img = Image(data)
    radius = img.read(_GRASS_RADIUS_VA, 4)
    grass = {struct.pack("<f", 128.0): True, struct.pack("<f", 42.0): False}.get(radius)
    return State(exe_path=exe_path, laa=bool(img.characteristics & 0x20),
                 vehicle=_two_way(img, _VEHICLE_VA, _VEHICLE_OFF, _VEHICLE_ON),
                 spectator=_two_way(img, _SPEC_A_VA, _SPEC_A_OFF, _SPEC_A_ON),
                 grass=grass, spectator_room=spectator_room(img), grass_room=grass_room(img),
                 server_spectator_chat=img.is_(0x5137C7, bytes.fromhex("e9")),
                 server_spawn_config=img.is_(0x516BBA, bytes.fromhex("e8")))


def read_state(exe_path: str) -> State:
    try:
        with open(exe_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise PatchError(f"Could not read the exe ({exc}).") from None
    return state_of(exe_path, data)


_SEPARATE = "Patch a separate copy of the game exe for playing."


def why_not(state: State, which: str) -> str:
    """Why a patch cannot go in this exe ('' = it can, or it is already in)."""
    if which == "spectator" and state.spectator is False and not state.spectator_room:
        if state.server_spectator_chat:
            return f"This exe has the server's spectator chat patch, which uses the same space. {_SEPARATE}"
        return f"Something else in this exe already uses the space this needs. {_SEPARATE}"
    if which == "grass" and state.grass is False and not state.grass_room:
        if state.server_spawn_config:
            return f"This exe has the server's spawn protection patch, which uses the same space. {_SEPARATE}"
        return f"Something else in this exe already uses the space this needs. {_SEPARATE}"
    return ""


@dataclass
class Choice:
    laa: bool = True
    vehicle: bool = True
    spectator: bool = True
    grass: bool = False                # off unless the player wants it (Dale)


def default_choice(state: State) -> Choice:
    return Choice(laa=True, vehicle=True,
                  spectator=bool(state.spectator) or not why_not(state, "spectator"),
                  grass=bool(state.grass))


# ---- the build ---------------------------------------------------------------------

@dataclass
class Result:
    exe_path: str
    changed: list
    steps: list


def plan_exe(data: bytes, state: State, choice: Choice) -> tuple:
    if not state.known:
        raise PatchError("WolfRAT does not recognise this exe, so it cannot patch it.")
    img = Image(data)
    changed = []
    if choice.laa and not state.laa:
        img.set_laa()
        changed.append("4 GB fix (Large Address Aware)")
    if choice.vehicle and state.vehicle is False:
        _vehicle(img)
        changed.append("Vehicle weapon fix")
    if choice.spectator and state.spectator is False:
        if why_not(state, "spectator"):
            raise PatchError("Spectator mode: " + why_not(state, "spectator"))
        _spectator(img)
        changed.append("Spectator mode")
    if choice.grass and state.grass is False:
        if why_not(state, "grass"):
            raise PatchError("Longer grass: " + why_not(state, "grass"))
        _grass(img)
        changed.append("Longer grass (128 m)")
    elif not choice.grass and state.grass:
        _remove_grass(img)
        changed.append("Longer grass removed")
    return bytes(img.data), changed


def build(exe_path: str, choice: Choice) -> Result:
    """Write the patched copy beside the chosen exe.  Raises PatchError."""
    state = read_state(exe_path)
    with open(exe_path, "rb") as handle:
        data = handle.read()
    new, changed = plan_exe(data, state, choice)
    if not changed:
        raise PatchError("This exe already has everything ticked here - nothing to build.")
    copy_name, backup_name = out_names(exe_path)
    out = os.path.join(os.path.dirname(exe_path), copy_name)
    try:
        _write(out, new)
    except OSError as exc:
        raise PatchError(f"Could not write into the game folder ({exc}). "
                         "Run WolfRAT as administrator if the game is under Program Files.") from None
    name = os.path.basename(exe_path)
    steps = ["Close the game.",
             f"Rename {name} to {backup_name} (your backup).",
             f"Rename {copy_name} to {name}.",
             "Start the game."]
    return Result(out, changed, steps)
