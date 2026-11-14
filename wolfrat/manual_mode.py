"""Manual mode: this WolfRAT only does what its admin clicks.

Two WolfRATs on one server both answer the same chat commands, both welcome
the same player and both announce the same spree, because each one works from
the same player list and the same chat.  The admin protocol has no way for
one to see the other, so the admin tells us: a WolfRAT that connects from
another PC asks once per server whether another WolfRAT already looks after
it.  If so this one goes into Manual mode - it still shows chat, players,
logs and the map list, and every button still works, but nothing is sent to
the server unless the admin asked for it.

Three states:

``FULL``       everything works as it always has.
``MANUAL``     the automatic features are off.
``UNDECIDED``  connected from another PC and the admin has not answered yet:
               treated as Manual, so nothing fires while the question is on
               screen (or if it is closed without an answer).

The saved ticks on every tab are left alone; the automatic features look at
``quiet()`` before they act, so going back to Full brings everything back as
it was set up.

The answer is kept per server (``host:port``) in ``wolfrat_manual_mode.json``
next to the other WolfRAT settings.  Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable, Optional

FULL, MANUAL, UNDECIDED = "full", "manual", "undecided"
_SAVED = (FULL, MANUAL)

NAME = "Manual mode"


def server_key(host: str, port) -> str:
    return f"{str(host or '').strip().lower()}:{port}"


class ManualMode:
    """Which mode this WolfRAT is in for the server it is connected to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Optional[str] = None
        self._choices: dict = {}
        self._key = ""
        self._mode = FULL
        self._noted: set = set()
        self._listeners: list = []
        self._log: Optional[Callable[[str], None]] = None

    # ---- set-up -------------------------------------------------------------
    def configure(self, path, log: Optional[Callable[[str], None]] = None) -> None:
        """Where the answers are kept, and where 'skipped' lines are written."""
        with self._lock:
            self._path = str(path) if path else None
            self._log = log
            self._choices = self._read()

    def reset(self) -> None:
        """Back to a WolfRAT that has never connected (tests)."""
        with self._lock:
            self._path, self._log = None, None
            self._choices, self._key, self._mode = {}, "", FULL
            self._noted = set()
            self._listeners = []

    def _read(self) -> dict:
        if not self._path or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return {}
        return {str(k): v for k, v in servers.items() if v in _SAVED}

    def _write(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"servers": self._choices}, handle, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass                      # the mode still holds for this session

    def on_change(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                pass

    # ---- the connection -----------------------------------------------------
    def begin_connect(self, host: str, port, *, can_ask: bool = True) -> None:
        """A connection to ``host:port`` is about to start.

        Called BEFORE the socket opens: the server's first player list and
        chat arrive the moment it does, and nothing may act on them until we
        know the mode.  A saved answer applies at once.  With none, the mode
        is UNDECIDED (quiet) until ``resolve`` - unless WolfRAT cannot ask at
        all (``can_ask`` False: tests, smoke run), where it stays FULL.
        """
        with self._lock:
            key = server_key(host, port)
            same_server = key == self._key
            self._key = key
            saved = self._choices.get(key)
            if saved in _SAVED:
                self._mode = saved
            elif not can_ask:
                self._mode = FULL
            elif same_server and self._mode in _SAVED:
                pass                  # a reconnect to a local server: keep Full
            else:
                self._mode = UNDECIDED
            self._noted = set()
        self._changed()

    def resolve(self, server_on_this_pc: bool) -> bool:
        """Connected.  True when the admin has to be asked.

        A server on this PC needs no question: this WolfRAT is the local one.
        """
        ask = False
        with self._lock:
            if self._mode == UNDECIDED:
                if server_on_this_pc:
                    self._mode = FULL
                else:
                    ask = True
        self._changed()
        return ask

    def choose(self, mode: str) -> None:
        """The admin's answer (the question, or the switch on the Server tab)."""
        if mode not in _SAVED:
            raise ValueError(f"unknown mode {mode!r}")
        with self._lock:
            self._mode = mode
            self._noted = set()
            if self._key:
                self._choices[self._key] = mode
                self._write()
        self._changed()

    # ---- what the features ask ------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def key(self) -> str:
        return self._key

    def saved(self) -> Optional[str]:
        return self._choices.get(self._key)

    def quiet(self) -> bool:
        """True while automatic features must not act."""
        return self._mode != FULL

    def skip(self, feature: str) -> bool:
        """``quiet()``, and one console line per feature per connection saying
        it was held back, so an admin can see why nothing happened."""
        if self._mode == FULL:
            return False
        note = None
        with self._lock:
            if feature not in self._noted:
                self._noted.add(feature)
                note = feature
        if note and self._log is not None:
            why = ("waiting for your answer" if self._mode == UNDECIDED
                   else "this WolfRAT is in Manual mode")
            try:
                self._log(f"[MANUAL MODE] {note}: not done, {why}.")
            except Exception:
                pass
        return True

    def describe(self) -> str:
        """One line for the status bar and the Server tab."""
        if self._mode == MANUAL:
            return "Manual mode - automatic features are off on this server"
        if self._mode == UNDECIDED:
            return "Waiting for your answer - automatic features are paused"
        return "Full - automatic features work as normal"


state = ManualMode()


def quiet() -> bool:
    return state.quiet()


def skip(feature: str) -> bool:
    return state.skip(feature)
