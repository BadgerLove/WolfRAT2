"""The Client patcher page on the Server tab: a player picks their game exe
(any name) and WolfRAT writes a patched copy beside it (see client_patcher)."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from wolfrat import client_patcher as C
from wolfrat import server_patches as sp
from wolfrat.protocol import wire_log
from wolfrat.server_patcher import PatchError
from wolfrat.server_patcher_panel import _NOTE, _TEXT, _WARN, _doc, _note

INTRO = ("For players: pick your game's exe (whatever it is called). WolfRAT writes a patched "
         "copy beside it and never changes the one you picked.")
NO_EXE = "No exe chosen yet. Press Choose exe... and pick your game's jointops.exe."
NOT_KNOWN = "WolfRAT does not recognise this exe, so it will not patch it."

TICKS = (   # key, label, doc page, what it does
    ("laa", "4 GB fix (Large Address Aware)", "05-large-address-aware.md",
     "Lets the game use up to 4 GB of memory on 64-bit Windows."),
    ("vehicle", "Vehicle weapon fix", "02-vehicle-weapon-restore.md",
     "Getting out of a vehicle gives you back the weapon you were holding, not the one you "
     "last died with."),
    ("spectator", "Spectator mode", "13-spectator-hud-client.md",
     "When spectating: chat, blue/red name tags and a health bar over every player, and T/Y "
     "chat works. Your chat only reaches players on servers with the spectator chat patch."),
    ("grass", "Longer grass (128 m)", "10-grass-128m.md",
     "Extends grass out to 128 m. If other players do not have it, it can put you at a "
     "disadvantage: they see past grass that you cannot."),
)


class ClientPatcherPanel(QWidget):
    """``settings_file`` remembers the last exe picked (None = do not remember)."""

    def __init__(self, settings_file: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self._settings_file = settings_file
        self.path = self._load_path()
        self.state: Optional[C.State] = None
        self.result: Optional[C.Result] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        page = QVBoxLayout(body)

        page.addWidget(_note(INTRO))
        top = QHBoxLayout()
        self.path_lbl = QLabel()
        self.path_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.path_lbl.setStyleSheet(f"color: {_TEXT};")
        top.addWidget(self.path_lbl, 1)
        self.choose_btn = QPushButton("Choose exe...")
        self.choose_btn.clicked.connect(self._choose)
        top.addWidget(self.choose_btn)
        page.addLayout(top)
        self.status_lbl = _note()
        page.addWidget(self.status_lbl)

        group = QGroupBox("Client patches")
        box = QVBoxLayout(group)
        self.boxes, self.notes = {}, {}
        for key, label, doc, what in TICKS:
            row = QHBoxLayout()
            check = QCheckBox(label)
            row.addWidget(check)
            row.addStretch(1)
            row.addWidget(_doc(doc))
            box.addLayout(row)
            note = _note(what)
            box.addWidget(note)
            self.boxes[key], self.notes[key] = check, note
        page.addWidget(group)

        bottom = QHBoxLayout()
        self.build_btn = QPushButton("Build patched copy")
        self.build_btn.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.build_btn.clicked.connect(self.build)
        bottom.addWidget(self.build_btn)
        bottom.addStretch(1)
        page.addLayout(bottom)
        self.result_lbl = QLabel()
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.result_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        page.addWidget(self.result_lbl)
        page.addStretch(1)

        self.refresh()

    # ------------------------------------------------------------------
    def _load_path(self) -> str:
        try:
            return json.loads(Path(self._settings_file).read_text(encoding="utf-8")).get("exe", "")
        except (OSError, ValueError, TypeError, AttributeError):
            return ""

    def _save_path(self):
        if self._settings_file is None:
            return
        try:
            Path(self._settings_file).write_text(json.dumps({"exe": self.path}), encoding="utf-8")
        except OSError as exc:
            wire_log(f"[PATCHER] could not remember the client exe: {exc}")

    def _choose(self):
        start = os.path.dirname(self.path) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Choose your game's exe", start, "Programs (*.exe)")
        if path:
            self.choose(os.path.normpath(path))

    def choose(self, path: str):
        self.path = path
        self._save_path()
        self.refresh()

    def refresh(self):
        self.result = None
        self.result_lbl.setText("")
        self.state = None
        if not self.path:
            self.path_lbl.setText("Game exe: (none)")
            self._status(NO_EXE, warn=False)
            self._enable(False)
            return
        self.path_lbl.setText(f"Game exe: {self.path}")
        self.path_lbl.setToolTip(self.path)
        try:
            self.state = C.read_state(self.path)
        except PatchError as exc:
            self._status(str(exc), warn=True)
            self._enable(False)
            return
        if not self.state.known:
            self._status(NOT_KNOWN, warn=True)
            self._enable(False)
            return
        self._status("", warn=False)
        self._enable(True)
        self._show(self.state, C.default_choice(self.state))

    def _status(self, text: str, warn: bool):
        self.status_lbl.setText(text)
        self.status_lbl.setVisible(bool(text))
        self.status_lbl.setStyleSheet(_WARN if warn else _NOTE)

    def _enable(self, on: bool):
        for check in self.boxes.values():
            check.setEnabled(on)
        self.build_btn.setEnabled(on)

    def _show(self, state: C.State, choice: C.Choice):
        have = {"laa": state.laa, "vehicle": bool(state.vehicle),
                "spectator": bool(state.spectator), "grass": bool(state.grass)}
        for key, label, _doc_page, what in TICKS:
            check, note = self.boxes[key], self.notes[key]
            blocked = C.why_not(state, key) if key in ("spectator", "grass") else ""
            removable = key == "grass"            # grass can be taken out again (Dale)
            check.setChecked(have[key] or (getattr(choice, key) and not blocked))
            check.setEnabled((removable or not have[key]) and not blocked)
            check.setText(label + ("  (already in - untick to remove)" if have[key] and removable
                                   else "  (already in)" if have[key] else ""))
            note.setText(blocked or what)
            note.setStyleSheet(_WARN if blocked else _NOTE)

    def choice(self) -> C.Choice:
        return C.Choice(**{key: self.boxes[key].isChecked() for key in self.boxes})

    # ------------------------------------------------------------------
    def build(self):
        if not self.path or self.state is None:
            return
        try:
            self.result = C.build(self.path, self.choice())
        except PatchError as exc:
            self.result = None
            wire_log(f"[PATCHER] client copy not built: {exc}")
            self.result_lbl.setText(f'<span style="color:#ff6050">{html.escape(str(exc))}</span>')
            return
        result = self.result
        wire_log(f"[PATCHER] built client {result.exe_path} changes: {', '.join(result.changed)}")
        steps = "".join(f"<li>{html.escape(step)}</li>" for step in result.steps)
        self.result_lbl.setText(
            f'<b style="color:#40d040">Built {html.escape(os.path.basename(result.exe_path))}</b> '
            f'<span style="color:{_TEXT}">beside your exe. Changes: '
            f'{html.escape(", ".join(result.changed))}.</span><br>'
            f'<span style="color:{_TEXT}">To use it:</span>'
            f'<ol style="color:{_TEXT}; margin-top:2px">{steps}</ol>')
