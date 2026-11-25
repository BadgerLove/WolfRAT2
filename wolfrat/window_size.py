"""Remember the main window's size and place between runs.

WolfRAT used to open at 1000 x 720 every time, whatever size it was closed
at. The window's size, place and maximized state are now written to
``wolfrat_window.json`` (next to the other wolfrat_*.json files) when it
closes, and put back when it opens.

The file holds Qt's own ``saveGeometry()`` blob (base64): that is what puts
the window back properly, including maximized and which monitor it was on,
and Qt pulls it back onto a screen if that monitor is gone. The plain
width/height/x/y/maximized fields are there for a human reading the file and
as a fallback if the blob ever can't be read.

Nothing here may stop WolfRAT opening or closing: a missing, empty or broken
file just means the default size.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QByteArray

FILENAME = "wolfrat_window.json"

# Anything smaller than this in the plain fallback fields is taken as junk.
_MIN_SANE = 200
_MAX_SANE = 20000


def load(path: str | os.PathLike) -> dict:
    """Read the saved window state; ``{}`` if there is none or it is unreadable."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str | os.PathLike, state: dict) -> None:
    """Write the window state (temp file then replace, so a crash can't leave half a file)."""

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, path)


def capture(window) -> dict:
    """The window's current size, place and maximized state, ready for ``save``."""

    maximized = bool(window.isMaximized())
    # When maximized, remember the size it goes back to on un-maximize.
    rect = window.normalGeometry() if maximized else window.geometry()
    blob = bytes(window.saveGeometry().toBase64().data()).decode("ascii")
    return {
        "geometry": blob,
        "width": rect.width(),
        "height": rect.height(),
        "x": rect.x(),
        "y": rect.y(),
        "maximized": maximized,
    }


def _sane(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if _MIN_SANE <= value <= _MAX_SANE else None


def restore(window, state: dict) -> bool:
    """Put the window back as saved. Returns False (window untouched) if nothing usable."""

    if not state:
        return False

    blob = state.get("geometry")
    if isinstance(blob, str) and blob:
        try:
            raw = QByteArray.fromBase64(blob.encode("ascii"))
            if not raw.isEmpty() and window.restoreGeometry(raw):
                return True
        except Exception:
            pass

    # Fallback: the plain fields (size only; a bad place could put it off screen).
    width = _sane(state.get("width"))
    height = _sane(state.get("height"))
    if width is None or height is None:
        return False
    window.resize(width, height)
    if state.get("maximized") is True:
        window.setWindowState(window.windowState() | _maximized_flag())
    return True


def _maximized_flag():
    from PyQt6.QtCore import Qt

    return Qt.WindowState.WindowMaximized
