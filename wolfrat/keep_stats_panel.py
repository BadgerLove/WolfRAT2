"""The "Restore disconnected stats" box in the Players tab (was "Player Stats" /
"Save player stats" until 2.10.19: Dale renamed it so it is not confused with the
Stats, XP and ranks box beside it).

Owns the settings file and the `StatsKeeper`.  It borrows the Weather tab's server
handle as a server ticker (see ``WeatherTab._server_tickers``); the main window
gives it somewhere to log and who to tell when a player's stats go back.
"""

from __future__ import annotations

import json
import os
from typing import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout

from wolfrat.keep_stats import StatsKeeper, status_text

SETTINGS_FILE = "wolfrat_keep_stats.json"
TICK_MS = 1000


class KeepStatsPanel(QGroupBox):
    def __init__(self, folder: str, log: Callable[[str], None] = lambda _text: None,
                 restored: Callable[[str, int, int], None] = lambda _n, _k, _d: None,
                 parent=None) -> None:
        super().__init__("Restore disconnected stats", parent)
        self._path = os.path.join(folder, SETTINGS_FILE)
        self.keeper = StatsKeeper(log=log, restored=restored)
        self.keeper.enabled = self._load().get("enabled", True) is not False
        self._log = log
        self._memory = None
        self._reader = None                       # the same handle, writable or not
        self._build()
        self._timer = QTimer(self)                # the Weather tab only polls every 5 s
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

    # ---- settings file ----------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump({"enabled": self.keeper.enabled}, f, indent=2)
        except OSError:
            pass

    # ---- UI -------------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.save_box = QCheckBox("Restore disconnected stats")
        self.save_box.setChecked(self.keeper.enabled)
        self.save_box.setToolTip(
            "Everyone's kills, deaths and exp points are saved while they play.\n"
            "If they lose connection or crash, they get them back when they rejoin\n"
            "the same round. Saved stats end when the round ends.")
        self.save_box.toggled.connect(self._toggled)
        layout.addWidget(self.save_box)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)
        self.refresh_status()

    def _toggled(self, checked: bool) -> None:
        self.keeper.enabled = checked
        if not checked:
            self.keeper.forget()
        self._save()
        self.refresh_status()

    def refresh_status(self) -> None:
        self.status_lbl.setText(status_text(self.keeper))

    # ---- the Weather tab's server handle --------------------------------------------
    def wants_write(self) -> bool:
        return self.keeper.enabled

    def set_watcher(self, watch) -> None:
        """Hand every player's counters on once a second (the Stats, XP and ranks box)."""
        self.keeper.watch = watch

    def no_server(self, _text: str) -> None:
        self._memory = None
        self._reader = None

    def server_tick(self, memory, writable: bool) -> None:
        self._memory = memory if writable else None
        self._reader = memory

    def _tick(self) -> None:
        try:
            self.keeper.tick(self._memory, reader=self._reader)
        except Exception as exc:                 # never let the timer die
            self._log(f"tick failed: {exc}")
        self.refresh_status()
