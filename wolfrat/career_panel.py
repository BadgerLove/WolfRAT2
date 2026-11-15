"""The "Stats, XP and ranks" box in the Players tab.

Owns the settings file, the totals database and the `CareerTracker`.  The Save
player stats box reads the server once a second and hands every player on
(``KeepStatsPanel.set_watcher``); the Mods tab asks this box for the !stats and
!rank replies; the main window gives it somewhere to log and to announce.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from wolfrat import career
from wolfrat.career import CareerStore, CareerTracker, Rank, build_ranks

SETTINGS_FILE = "wolfrat_career.json"
DATABASE_FILE = "wolfrat_career.db"
FLUSH_MS = 10000
LINKED_SECONDS = 5.0
DEFAULTS = {"track": True, "stats": True, "rank": True, "announce": True, "announce_from": 2}


class RanksDialog(QDialog):
    """Every rank: its name and the XP it needs.  The number is its place in XP order."""

    def __init__(self, ranks, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Player ranks")
        self.resize(420, 620)
        layout = QVBoxLayout(self)
        note = QLabel("Ranks are numbered in XP order. The first rank always starts at 0 XP.\n"
                      "XP is the game's own exp points, as on the end-of-round board.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Rank name", "XP needed"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self._fill((rank.name, rank.xp) for rank in ranks)
        row = QHBoxLayout()
        for text, slot in (("Add rank", self._add), ("Remove selected", self._remove),
                           ("Back to the default ranks", self._defaults)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fill(self, rows) -> None:
        self.table.setRowCount(0)
        for name, xp in rows:
            self._append(name, xp)

    def _append(self, name: str, xp: int) -> None:
        at = self.table.rowCount()
        self.table.insertRow(at)
        self.table.setItem(at, 0, QTableWidgetItem(str(name)))
        self.table.setItem(at, 1, QTableWidgetItem(str(xp)))

    def _add(self) -> None:
        rows = self.rows()
        top = max((xp for _name, xp in rows), default=0)
        self._append("New rank", top + 1000)
        self.table.scrollToBottom()

    def _remove(self) -> None:
        for at in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(at)

    def _defaults(self) -> None:
        self._fill(career.DEFAULT_RANKS)

    def rows(self) -> list:
        found = []
        for at in range(self.table.rowCount()):
            name = self.table.item(at, 0).text().strip() if self.table.item(at, 0) else ""
            xp = (self.table.item(at, 1).text() if self.table.item(at, 1) else "").replace(",", "").strip()
            if name and xp.isdigit():
                found.append((name, int(xp)))
        return found


class CareerPanel(QGroupBox):
    def __init__(self, folder: str, log: Callable[[str], None] = lambda _text: None,
                 announce: Callable[[str], None] = lambda _text: None,
                 chat_limit: Callable[[], int] = lambda: 59, parent=None) -> None:
        super().__init__("Stats, XP and ranks", parent)
        self._path = os.path.join(folder, SETTINGS_FILE)
        self._log = log
        self._announce = announce
        self._chat_limit = chat_limit
        saved = self._load()
        self._settings = {key: saved.get(key, default) for key, default in DEFAULTS.items()}
        self._ranks = build_ranks(saved.get("ranks") or career.DEFAULT_RANKS)
        self._observed_at: Optional[float] = None
        self.store: Optional[CareerStore] = None
        self.tracker: Optional[CareerTracker] = None
        try:
            self.store = CareerStore(os.path.join(folder, DATABASE_FILE))
            self.tracker = CareerTracker(self.store, ranks=lambda: self._ranks, ranked_up=self._ranked_up)
        except Exception as exc:                 # a locked or broken file: the rest of WolfRAT still runs
            self._log(f"could not open {DATABASE_FILE}: {exc}")
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._flush)
        self._timer.start(FLUSH_MS)

    # ---- settings file ----------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        data = dict(self._settings)
        data["ranks"] = [[rank.name, rank.xp] for rank in self._ranks]
        try:
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # ---- what the rest of WolfRAT asks ------------------------------------------
    @property
    def tracking(self) -> bool:
        return bool(self._settings["track"]) and self.tracker is not None

    def ranks(self) -> tuple:
        return self._ranks

    def observe(self, slots, rebased) -> None:
        """Every player in the server, once a second (keep_stats.StatsKeeper.watch)."""
        if not self.tracking:
            return
        self._observed_at = time.monotonic()
        self.tracker.observe(slots, rebased)

    def linked(self) -> bool:
        return self._observed_at is not None and time.monotonic() - self._observed_at < LINKED_SECONDS

    def reply(self, command: str, target: str, limit: int) -> Optional[list]:
        """The chat lines for !stats or !rank.  None = not answering: the command is
        switched off, or this WolfRAT has never read a server (not on the server's PC)."""
        if not self.tracking or not self._settings.get(command.lstrip("!"), False):
            return None
        if not self.tracker.seen and self.store.count() == 0:
            return None
        found = self.store.find(target)
        if found is None:
            line = f"No stats yet for {career.chat_safe(target)}"
            return [line if career.fits(line, limit) else "No stats yet for that player"]
        if command == "!rank":
            place, players = self.store.position(found.xp)
            return [career.rank_line(found.name, place, players, found.xp, self._ranks, limit)]
        return career.stats_lines(found, limit)

    def shutdown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store, self.tracker = None, None

    # ---- rank ups ---------------------------------------------------------------
    def _ranked_up(self, name: str, rank: Rank) -> None:
        self._log(f"{name} reached rank {rank.number} {rank.name}")
        if self._settings["announce"] and self._settings["rank"] and rank.number >= int(self._settings["announce_from"]):
            self._announce(career.rank_up_line(name, rank, self._chat_limit()))

    # ---- UI -------------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.track_box = self._tick_box(
            "Track player stats and XP", "track",
            "Adds up every player's kills, deaths, headshots, knife kills, medic revives and heals,\n"
            "zone takeovers, best kill streak, time played and XP, round after round.\n"
            "XP is the game's own exp points, the number on the end-of-round board.\n"
            "Needs the server running on this PC.")
        layout.addWidget(self.track_box)
        self.stats_box = self._tick_box(
            "!stats command", "stats",
            "Players type !stats (or !stats name) in chat to see their totals.\n!kd gives the same answer.")
        layout.addWidget(self.stats_box)
        self.rank_box = self._tick_box(
            "!rank command", "rank",
            "Players type !rank (or !rank name) to see their place on the server, their rank and their XP.")
        layout.addWidget(self.rank_box)
        row = QHBoxLayout()
        self.announce_box = self._tick_box(
            "Announce rank ups in chat, from rank", "announce",
            "A chat line when a player reaches a new rank. Lower ranks come quickly,\n"
            "so pick the first rank worth announcing.")
        row.addWidget(self.announce_box)
        self.from_spin = QSpinBox()
        self.from_spin.setRange(2, 999)
        self.from_spin.setValue(max(2, int(self._settings["announce_from"])))
        self.from_spin.valueChanged.connect(self._from_changed)
        row.addWidget(self.from_spin)
        self.ranks_btn = QPushButton("Edit ranks...")
        self.ranks_btn.clicked.connect(self._edit_ranks)
        row.addWidget(self.ranks_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)
        self._follow_track()
        self.refresh_status()

    def _tick_box(self, text: str, key: str, tip: str) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(self._settings[key]))
        box.setToolTip(tip)
        box.toggled.connect(lambda checked, key=key: self._toggled(key, checked))
        return box

    def _toggled(self, key: str, checked: bool) -> None:
        self._settings[key] = bool(checked)
        self._save()
        self._follow_track()
        self.refresh_status()

    def _follow_track(self) -> None:
        on = bool(self._settings["track"])
        for widget in (self.stats_box, self.rank_box, self.announce_box, self.from_spin):
            widget.setEnabled(on)

    def _from_changed(self, value: int) -> None:
        self._settings["announce_from"] = int(value)
        self._save()

    def _edit_ranks(self) -> None:
        dialog = RanksDialog(self._ranks, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._ranks = build_ranks(dialog.rows())
            self._save()
            self.refresh_status()

    def status_text(self) -> str:
        if self.tracker is None:
            return f"Off - could not open {DATABASE_FILE}."
        if not self._settings["track"]:
            return "Off - no stats or XP are being counted."
        players = self.store.count()
        table = f"{players} player{'' if players == 1 else 's'} in the table, {len(self._ranks)} ranks."
        if not self.linked():
            return f"Waiting - needs the server running on this PC. {table}"
        count = self.tracker.tracking
        return f"On - counting {count} player{'' if count == 1 else 's'}. {table}"

    def refresh_status(self) -> None:
        self.status_lbl.setText(self.status_text())

    def _flush(self) -> None:
        try:
            if self.store is not None:
                self.store.flush()
        except Exception as exc:                 # never let the timer die
            self._log(f"could not save stats: {exc}")
        self.refresh_status()
