"""The "Automatic" row in the Players tab's Team Balance box.

Owns the settings file and the `Balancer`.  The main window gives it
``say(text)``, ``move(player, to_team)`` and ``set_jo_balance(bool)``, points
the three ``*_source`` callables at the Bans tab (game type), Map Voting
(current map, vote running) and feeds it players, chat and server settings.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from wolfrat import coop_guard
from wolfrat import manual_mode
from wolfrat.auto_balance import (DEFAULT_GAP, MODE_JOINTOPS, MODE_LABELS, MODE_OFF, MODE_WOLFRAT,
                                  TEAM_NAMES, VOLUNTEER, Balancer, Move, Say, is_team_game, teams)

SETTINGS_FILE = "wolfrat_balance.json"


class AutoBalancePanel(QWidget):
    def __init__(self, folder: str, say: Callable[[str], None],
                 move: Callable[[dict, int], None], set_jo_balance: Callable[[bool], None],
                 clock: Callable[[], float] = time.time, rng=None, parent=None) -> None:
        super().__init__(parent)
        self._path = os.path.join(folder, SETTINGS_FILE)
        self._say, self._move, self._set_jo_balance = say, move, set_jo_balance
        cfg = self._load()
        self.mode = cfg.get("mode", MODE_OFF) if cfg.get("mode") in MODE_LABELS else MODE_OFF
        self.balancer = Balancer(gap=int(cfg.get("gap", DEFAULT_GAP)), clock=clock, rng=rng)
        self.game_type_source: Callable[[], Optional[int]] = lambda: None
        self.current_map_source: Callable[[], Optional[str]] = lambda: None
        self.vote_active_source: Callable[[], bool] = lambda: False
        self._server_jo_balance: Optional[bool] = None
        self._players: list = []
        self._seen_chat: set = set()
        self._chat_initialized = False
        self._build()
        self.refresh_status()

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
                json.dump({"mode": self.mode, "gap": self.balancer.gap}, f, indent=2)
        except OSError:
            pass

    # ---- UI -------------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Automatic:"))
        self.mode_combo = QComboBox()
        for key in (MODE_OFF, MODE_JOINTOPS, MODE_WOLFRAT):
            self.mode_combo.addItem(MODE_LABELS[key], key)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(self.mode))
        self.mode_combo.setToolTip(
            "Joint Ops' own balance only runs as a new map starts.\n"
            f"WolfRAT's warns 30 s ahead, lets players volunteer with {VOLUNTEER},\n"
            "then moves volunteers first and random players after that.")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode_combo, 1)
        row.addWidget(QLabel("when a team has"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(2, 6)
        self.gap_spin.setValue(self.balancer.gap)
        self.gap_spin.valueChanged.connect(self._gap_changed)
        row.addWidget(self.gap_spin)
        row.addWidget(QLabel("more players"))
        layout.addLayout(row)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

    def _mode_changed(self) -> None:
        old, new = self.mode, self.mode_combo.currentData()
        if new == old:
            return
        self.mode = new
        self.balancer.reset()
        self._save()
        if new == MODE_JOINTOPS:
            self._set_jo_balance(True)
        elif old == MODE_JOINTOPS:
            self._set_jo_balance(False)
        self.refresh_status()

    def _gap_changed(self, value: int) -> None:
        self.balancer.gap = value
        self._save()
        self.refresh_status()

    # ---- feeding --------------------------------------------------------------------
    def _team_game(self) -> bool:
        return is_team_game(self.game_type_source(), self.current_map_source())

    def on_players(self, players) -> None:
        self._players = list(players or [])
        allowed = (self.mode == MODE_WOLFRAT and self._team_game()
                   and not self.vote_active_source()
                   and not manual_mode.quiet())
        self._do(self.balancer.tick(self._players, allowed))
        self.refresh_status()

    def on_chat(self, messages) -> None:
        if not self._chat_initialized:            # first batch = history from before we connected
            self._chat_initialized = True
            self._seen_chat = {m.get("id", m.get("raw", "")) for m in messages or []}
            return
        for msg in messages or []:
            key = msg.get("id", msg.get("raw", ""))
            if not key or key in self._seen_chat:
                continue
            self._seen_chat.add(key)
            if msg.get("own"):
                continue                 # WolfRAT's own line, not a volunteer
            text = str(msg.get("text", ""))
            if ":" not in text:
                continue
            who, said = text.split(":", 1)
            if said.strip().lower() == VOLUNTEER:
                if manual_mode.skip("Auto-balance"):
                    continue
                self._do(self.balancer.volunteer(who.strip()))
        if len(self._seen_chat) > 1000:
            self._seen_chat = set(list(self._seen_chat)[-500:])
        self.refresh_status()

    def reset_chat(self) -> None:
        self._chat_initialized = False

    def on_settings(self, settings: dict) -> None:
        lower = {str(k).lower(): v for k, v in (settings or {}).items()}
        value = lower.get("autobalanceonrecycle")
        self._server_jo_balance = None if value is None else str(value).lower() in ("1", "true", "on")
        self.refresh_status()

    def _do(self, actions) -> None:
        for action in actions:
            if isinstance(action, Move):
                self._move(action.player, action.to_team)
            elif isinstance(action, Say):
                self._say(action.text)

    # ---- status line ----------------------------------------------------------------
    def status_text(self) -> str:
        jo = {True: "on", False: "off", None: "not read yet"}[self._server_jo_balance]
        if self.mode == MODE_OFF:
            extra = " (Joint Ops' own balance is still ON on the server)" if self._server_jo_balance else ""
            return "Automatic balance is off." + extra
        if self.mode == MODE_JOINTOPS:
            return f"Joint Ops balances teams as each new map starts. Server setting: {jo}."
        game_type = self.game_type_source()
        if game_type is not None and coop_guard.is_coop(game_type):
            return "Paused - co-op map."
        if not self._team_game():
            return "Paused - not a team game."
        if self.vote_active_source():
            return "Paused - map vote running."
        sides = teams(self._players)
        score = f"{TEAM_NAMES[1]} {len(sides[1])} v {TEAM_NAMES[2]} {len(sides[2])}"
        state = self.balancer.state
        if state == "countdown":
            n = len(self.balancer.volunteers)
            return (f"Balancing in {self.balancer.seconds_left()}s - "
                    f"{n} volunteer{'s' if n != 1 else ''} ({score}).")
        if state == "settling":
            return f"Teams uneven ({score}) - warning the players shortly."
        if state == "cooldown":
            return f"Just balanced ({score}) - watching again in a moment."
        return f"Watching: {score}."

    def refresh_status(self) -> None:
        self.status_lbl.setText(self.status_text())
