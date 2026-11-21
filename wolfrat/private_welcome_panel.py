"""The Messages tab's "Private welcome" page (rebuilt 2026-11-21).

Up to three lines that only the player who joined sees, sent through the
private-line helper (private_welcome.py explains the rules). One box per line
with colour codes typed straight in (a Colours... list to copy from), a counter
against the helper's 118 characters and a preview in the game's colours.
Changes are live for the next player who joins: no script, no map change.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from wolfrat import manual_mode, server_wac
from wolfrat import private_welcome as pw
from wolfrat.chat_preview import ChatPreview
from wolfrat.colour_codes import show_colour_codes


class _Line:
    """One line: the text (codes and all), a Colours... button, the counter
    and the preview."""

    def __init__(self, number: int, placeholder: str, on_edit, owner):
        self.label = QLabel(f"Line {number}:")
        self.text = QLineEdit()
        self.text.setMaxLength(pw.LINE_LIMIT + 40)   # room to see it is too long
        self.text.setPlaceholderText(placeholder)
        self.text.textChanged.connect(on_edit)
        self.colours_btn = QPushButton("Colours...")
        self.colours_btn.setToolTip("The colour codes, ready to copy into the line.")
        self.colours_btn.clicked.connect(lambda: show_colour_codes(owner))
        self.count = QLabel()
        self.count.setStyleSheet("color: #666;")
        self.preview = ChatPreview()

    def add_to(self, layout):
        row = QHBoxLayout()
        row.addWidget(self.label)
        row.addWidget(self.text, 1)
        row.addWidget(self.colours_btn)
        layout.addLayout(row)
        under = QHBoxLayout()
        under.addWidget(self.preview, 1)
        under.addWidget(self.count)
        layout.addLayout(under)

    def value(self) -> str:
        return self.text.text().strip()

    def refresh(self) -> Optional[str]:
        line = self.value()
        room = len(line.replace(pw.PLACEHOLDER, "x" * pw.NAME_ROOM))
        self.count.setText(f"{room}/{pw.LINE_LIMIT}")
        self.preview.show_line(line.replace(pw.PLACEHOLDER, "PlayerName"))
        self.preview.setVisible(bool(line))
        return pw.line_problem(line)


class PrivateWelcomePanel(QWidget):
    """The page. MainWindow plugs in ``send_private(name, text) -> bool`` and
    ``helper_ready() -> bool`` from the rank-sound helper; MessagesTab feeds
    it every player list through ``players_updated``."""

    def __init__(self, config_path, server_dir: Callable[[], str],
                 log: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.monotonic, parent=None):
        super().__init__(parent)
        self._path = str(config_path)
        self._server_dir = server_dir
        self._log = log or (lambda _text: None)
        self.send_private: Callable[[str, str], bool] = lambda _name, _text: False
        self.helper_ready: Callable[[], bool] = lambda: False
        self._watch = pw.JoinWatch(clock)
        self._players: list = []
        self._pending: dict = {}
        self._sent = 0
        self._ready = False
        self.settings = pw.load(self._path) or pw.Settings()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_now)
        self._build()
        self._import_old_script_lines()
        self._fill()
        self._ready = True
        self._edited()

    # ------------------------------------------------------------- form
    def _build(self):
        outer = QVBoxLayout(self)
        intro = QLabel(
            "Only the player who joined sees these lines. Changes work straight away "
            "for the next player who joins (no map change). Needs WolfRAT on the same "
            "PC as the server."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #a89a50;")
        outer.addWidget(intro)

        group = QGroupBox("Private welcome")
        layout = QVBoxLayout(group)
        self.enabled_cb = QCheckBox("Whisper a welcome to every player who joins")
        self.enabled_cb.toggled.connect(self._edited)
        layout.addWidget(self.enabled_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Send it"))
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(pw.MIN_SECONDS, pw.MAX_SECONDS)
        self.seconds_spin.setSuffix(" s after they join")
        self.seconds_spin.valueChanged.connect(self._edited)
        row.addWidget(self.seconds_spin)
        row.addStretch()
        layout.addLayout(row)

        hint = QLabel(
            f"Up to {pw.MAX_LINES} lines of {pw.LINE_LIMIT} characters, colour codes count. "
            f"{pw.PLACEHOLDER} = their name. Leave a line empty to skip it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6a6a30; font-size: 8pt;")
        layout.addWidget(hint)

        placeholders = (pw.FIRST_LINE_HINT, pw.SECOND_LINE_HINT, "")
        self.lines = [_Line(i + 1, placeholders[i], self._edited, self)
                      for i in range(pw.MAX_LINES)]
        for line in self.lines:
            line.add_to(layout)

        row = QHBoxLayout()
        row.addWidget(QLabel("Try it on:"))
        self.try_combo = QComboBox()
        self.try_combo.setMinimumWidth(160)
        row.addWidget(self.try_combo)
        self.try_btn = QPushButton("Send now")
        self.try_btn.setToolTip("Send the lines to that player now (only they see them).")
        self.try_btn.clicked.connect(self._try_now)
        row.addWidget(self.try_btn)
        row.addStretch()
        layout.addLayout(row)

        self.state_lbl = QLabel()
        self.state_lbl.setWordWrap(True)
        layout.addWidget(self.state_lbl)
        self.old_lbl = QLabel()
        self.old_lbl.setWordWrap(True)
        self.old_lbl.setStyleSheet("color: #e8c840;")
        layout.addWidget(self.old_lbl)

        outer.addWidget(group)
        outer.addStretch(1)

    def _fill(self):
        self.enabled_cb.setChecked(self.settings.enabled)
        self.seconds_spin.setValue(self.settings.seconds)
        for line, text in zip(self.lines, self.settings.lines):
            line.text.setText(text)

    def _import_old_script_lines(self):
        """Once: bring the old script welcome's text over, so nothing is retyped."""
        if self.settings.imported or any(self.settings.lines):
            return
        old = pw.old_script_lines(self._server_dir())
        if not old:
            return
        lines = (old + ["", "", ""])[:pw.MAX_LINES]
        self.settings = pw.Settings(False, lines, self.settings.seconds, True)
        self._save_now()
        self._log("Private welcome: the old script welcome's lines were copied in "
                  "(tick the box to use them).")

    def _edited(self, *_):
        if not self._ready:
            return
        problems = []
        for number, line in enumerate(self.lines, start=1):
            problem = line.refresh()
            if problem:
                problems.append(f"Line {number}: {problem}")
        self.settings.enabled = self.enabled_cb.isChecked()
        self.settings.seconds = self.seconds_spin.value()
        self.settings.lines = [line.value() for line in self.lines]
        self._save_timer.start(400)
        if self.settings.enabled:
            self._retire_old_script_welcome()
        self._show_state(problems)

    def _show_state(self, problems=()):
        if problems:
            self.state_lbl.setText(" ".join(problems) + " Fix it before it can be sent.")
        elif not self.settings.enabled:
            self.state_lbl.setText("Off. Nothing is whispered to players who join.")
        elif not self.settings.used_lines():
            self.state_lbl.setText("On, but every line is empty: type a line.")
        elif not self.helper_ready():
            self.state_lbl.setText(
                "On, but WolfRAT can't send private lines right now: it needs to run on the "
                "same PC as the server and be connected. It starts sending as soon as it can.")
        else:
            sent = f" Sent to {self._sent} player(s) since WolfRAT started." if self._sent else ""
            self.state_lbl.setText(
                f"On. Players get it {self.settings.seconds} s after they join.{sent}")
        folder = self._server_dir()
        old = pw.old_script_lines(folder) if folder else None
        self.old_lbl.setVisible(bool(old))
        if old:
            self.old_lbl.setText(
                "The old script welcome is still in the server's script. It is taken out "
                "when you switch this on (the server stops showing it at the next map change).")
        self.try_btn.setEnabled(bool(self.settings.used_lines()) and not problems
                                and self.try_combo.count() > 0)

    def _retire_old_script_welcome(self):
        """Switching this on takes the old script welcome out, so nobody gets both."""
        folder = self._server_dir()
        if not folder:
            return
        try:
            if server_wac.remove_welcome(folder):
                self._log("Private welcome: the old script welcome was taken out of "
                          "server.wac (it stops at the next map change).")
        except server_wac.ServerWacError as exc:
            self._log(f"Private welcome: could not take the old script welcome out: {exc}")

    def _save_now(self):
        try:
            pw.save(self._path, self.settings)
        except OSError as exc:
            self._log(f"Private welcome: could not save the settings ({exc}).")

    # ------------------------------------------------------------- players
    def players_updated(self, players):
        """Every player list (5 s): note who joined and queue their welcome."""
        names = [str(p.get("name", "")).strip() for p in players]
        self._players = [name for name in names if name]
        current = self.try_combo.currentText()
        self.try_combo.blockSignals(True)
        self.try_combo.clear()
        self.try_combo.addItems(self._players)
        if current in self._players:
            self.try_combo.setCurrentText(current)
        self.try_combo.blockSignals(False)
        joined = self._watch.update(self._players)
        if self._ready:
            self._show_state([p for p in (pw.line_problem(line) for line in
                                          self.settings.lines) if p])
        if not self.settings.enabled:
            return
        for name in joined:
            if name in self._pending:
                continue
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda n=name: self._send_due(n))
            timer.timeout.connect(timer.deleteLater)
            self._pending[name] = timer
            timer.start(self.settings.seconds * 1000)

    def _send_due(self, name):
        self._pending.pop(name, None)
        if not self.settings.enabled:
            return
        if name not in self._players:
            return                                   # left before it was due
        if manual_mode.skip("Private welcome"):
            return
        if self._send_to(name):
            self._sent += 1
            self._show_state()

    def _send_to(self, name) -> bool:
        lines = [pw.render(line, name) for line in self.settings.used_lines()
                 if not pw.line_problem(line)]
        if not lines:
            return False
        for line in lines:
            if not self.send_private(name, line):
                self._log(f"Private welcome not sent to {name}: WolfRAT can't send private "
                          "lines right now (it needs to run on the same PC as the server).")
                return False
        self._log(f"Private welcome sent to {name} ({len(lines)} line(s)).")
        return True

    def _try_now(self):
        name = self.try_combo.currentText().strip()
        if name and self._send_to(name):
            self._show_state()


__all__ = ["PrivateWelcomePanel"]
