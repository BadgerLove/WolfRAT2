"""The "Moderator entrance" box on the Mods tab.

Owns the settings file, the `ModEntrance` engine and the little bit of UI.
The tab gives it two ways to act - `announce(text)` and `flash() -> bool` -
and feeds it the player list and connection drops.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGroupBox,
                             QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from wolfrat import chat_markup
from wolfrat.chat_preview import ChatPreview
from wolfrat.colour_codes import show_colour_codes
from wolfrat.mod_entrance import (DEFAULT_LINES, EntranceConfig, ModEntrance,
                                  line_problem, render_line)

SETTINGS_FILE = "wolfrat_mod_entrance.json"
# The name shown in the preview and used by "Try it now" - never a real
# moderator's (Dale 2026-09-26: the first on the list was A-99).
SAMPLE_NAME = "Badger"


class LinesDialog(QDialog):
    """One announcement per row. Problems go in a short scrolling list and a
    preview shows the row under the cursor in the game's colours.

    The old version listed every problem in a label under the box: pasting
    45 long lines (Dale 2026-09-26) grew the dialog past the screen, OK went
    out of reach and the window jumped on every key press.
    """

    def __init__(self, lines: Iterable[str], parent=None, sample_name: str = SAMPLE_NAME):
        super().__init__(parent)
        self.setWindowTitle("Moderator entrance lines")
        self._sample = sample_name or SAMPLE_NAME
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "One line per row. WolfRAT picks one at random. "
            "{player} becomes the moderator's name. Colours: <cFF8000>text, <co> = normal colour.")
        intro.setTextFormat(Qt.TextFormat.PlainText)     # or Qt reads <cFF8000> as HTML
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.edit = QPlainTextEdit("\n".join(lines))
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.textChanged.connect(self._schedule_check)
        self.edit.cursorPositionChanged.connect(self._show_preview)
        layout.addWidget(self.edit, 1)

        caption = QLabel(f"Preview of the line under the cursor, as {self._sample}:")
        caption.setTextFormat(Qt.TextFormat.PlainText)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.preview = ChatPreview()
        layout.addWidget(self.preview)

        self.check_lbl = QLabel()
        self.check_lbl.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.check_lbl)
        self.problem_list = QListWidget()
        self.problem_list.setMaximumHeight(110)
        self.problem_list.setWordWrap(True)
        self.problem_list.itemClicked.connect(self._go_to_problem)
        layout.addWidget(self.problem_list)

        row = QHBoxLayout()
        defaults = QPushButton("Put the standard lines back")
        defaults.clicked.connect(lambda: self.edit.setPlainText("\n".join(DEFAULT_LINES)))
        row.addWidget(defaults)
        colours = QPushButton("Colours...")
        colours.setToolTip("The colour codes, ready to copy into a line.")
        colours.clicked.connect(lambda: show_colour_codes(self))
        row.addWidget(colours)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._check)
        self._check()
        self._show_preview()

    def lines(self) -> list:
        return [row.strip() for row in self.edit.toPlainText().splitlines() if row.strip()]

    def _schedule_check(self):
        self._timer.start()
        self._show_preview()

    def _check(self):
        self.problem_list.clear()
        rows = self.edit.toPlainText().splitlines()
        count = 0
        for number, row in enumerate(rows, 1):
            if not row.strip():
                continue
            count += 1
            problem = line_problem(row)
            if problem:
                item = QListWidgetItem(f"Row {number}: {problem}")
                item.setData(Qt.ItemDataRole.UserRole, number - 1)
                self.problem_list.addItem(item)
        found = self.problem_list.count()
        if not count:
            self.check_lbl.setText("No lines - the standard ones will be used.")
        elif found:
            self.check_lbl.setText(f"{found} of {count} lines need a look (click one to jump to it):")
        else:
            self.check_lbl.setText(f"All {count} lines fit in one chat message.")
        self.problem_list.setVisible(bool(found))

    def _go_to_problem(self, item):
        block = self.edit.document().findBlockByNumber(int(item.data(Qt.ItemDataRole.UserRole)))
        cursor = self.edit.textCursor()
        cursor.setPosition(block.position())
        self.edit.setTextCursor(cursor)
        self.edit.setFocus()

    def _show_preview(self):
        row = self.edit.textCursor().block().text()
        self.preview.show_line(row.replace("{player}", chat_markup.safe_name(self._sample)))


class ModEntrancePanel(QGroupBox):
    def __init__(self, settings_dir: str,
                 announce: Callable[[str], None],
                 flash: Callable[[], bool],
                 log: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.time,
                 parent=None):
        super().__init__("Moderator entrance", parent)
        self._path = os.path.join(str(settings_dir), SETTINGS_FILE)
        self._announce = announce
        self._flash = flash
        self._log = log or (lambda _text: None)
        self._loading = True
        self._known_mods = []
        config, last = self._load()
        self.engine = ModEntrance(config, last_announced=last, clock=clock)
        self._build()
        self._loading = False
        self._refresh_status()

    # ------------------------------------------------------------ settings
    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        last = data.get("last_announced") if isinstance(data, dict) else None
        if not isinstance(last, dict):
            last = {}
        last = {str(k).lower(): float(v) for k, v in last.items() if isinstance(v, (int, float))}
        return EntranceConfig.from_dict(data), last

    def _save(self):
        self.engine.prune()
        data = self.engine.config.to_dict()
        data["last_announced"] = self.engine.last_announced
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self._path)
        except OSError as exc:
            self._log(f"Could not save the moderator entrance settings: {exc}")

    # ------------------------------------------------------------------ UI
    def _build(self):
        cfg = self.engine.config
        layout = QVBoxLayout(self)

        self.enabled_cb = QCheckBox("Announce moderators when they join")
        self.enabled_cb.setChecked(cfg.enabled)
        self.enabled_cb.toggled.connect(self._changed)
        layout.addWidget(self.enabled_cb)

        self.lightning_cb = QCheckBox("...with a crack of lightning")
        self.lightning_cb.setChecked(cfg.lightning)
        self.lightning_cb.setToolTip("Uses the lightning add-on from the Weather tab. "
                                     "Without it the announcement still goes out, just without the thunder.")
        self.lightning_cb.toggled.connect(self._changed)
        layout.addWidget(self.lightning_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Same moderator again after"))
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 24 * 60)
        self.cooldown_spin.setSuffix(" min")
        self.cooldown_spin.setValue(cfg.cooldown_minutes)
        self.cooldown_spin.setToolTip("A moderator who drops and rejoins inside this time is not announced again.")
        self.cooldown_spin.valueChanged.connect(self._changed)
        row.addWidget(self.cooldown_spin)
        row.addStretch()
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Wait after they appear"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 300)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setValue(cfg.delay_seconds)
        self.delay_spin.setToolTip("Their name shows up while they are still on the loading screen. "
                                   "Waiting lets them see and hear their own entrance.")
        self.delay_spin.valueChanged.connect(self._changed)
        row.addWidget(self.delay_spin)
        row.addStretch()
        layout.addLayout(row)

        row = QHBoxLayout()
        self.lines_btn = QPushButton("Edit the lines...")
        self.lines_btn.clicked.connect(self._edit_lines)
        row.addWidget(self.lines_btn)
        self.try_btn = QPushButton("Try it now")
        self.try_btn.setToolTip(f"Sends the first line (as {SAMPLE_NAME}) and the lightning, right now.")
        self.try_btn.clicked.connect(self._try_now)
        row.addWidget(self.try_btn)
        layout.addLayout(row)

        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

    def _changed(self, *_):
        if self._loading:
            return
        cfg = self.engine.config
        cfg.enabled = self.enabled_cb.isChecked()
        cfg.lightning = self.lightning_cb.isChecked()
        cfg.cooldown_minutes = self.cooldown_spin.value()
        cfg.delay_seconds = self.delay_spin.value()
        self._save()
        self._refresh_status()

    def _edit_lines(self):
        dialog = LinesDialog(self.engine.config.lines, self,
                             sample_name=SAMPLE_NAME)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.engine.config.lines = dialog.lines() or list(DEFAULT_LINES)
            self._save()
            self._refresh_status()

    def _try_now(self):
        lines = self.engine.config.lines or list(DEFAULT_LINES)
        name = SAMPLE_NAME
        self._perform("Try it now", render_line(lines[0], name), self.lightning_cb.isChecked())

    def _refresh_status(self, last: str = ""):
        cfg = self.engine.config
        if not cfg.enabled:
            text = "Off. Moderators join quietly."
        else:
            text = (f"On - {len(cfg.lines)} line{'s' if len(cfg.lines) != 1 else ''}, "
                    f"{'with' if cfg.lightning else 'no'} lightning, "
                    f"once per moderator every {cfg.cooldown_minutes} min.")
        if last:
            text += f"\nLast: {last}"
        self.status_lbl.setText(text)

    # -------------------------------------------------------------- inputs
    def on_disconnected(self):
        self.engine.note_disconnected()

    def on_players(self, players, mods: Iterable[str]):
        names = [(p.get("name", "") if isinstance(p, dict) else str(p)) for p in (players or [])]
        self._known_mods = sorted(str(m) for m in mods)
        due = self.engine.update(names, self._known_mods)
        for entrance in due:
            self._perform(entrance.player, entrance.message, entrance.lightning)
        if due:
            self._save()

    def _perform(self, who: str, message: str, lightning: bool):
        stamp = time.strftime("%H:%M:%S")
        try:
            self._announce(message)
        except Exception as exc:                      # never let a fanfare break the player list
            self._log(f"Moderator entrance for {who} could not be sent: {exc}")
            self._refresh_status(f"{stamp} {who} - could not send the announcement")
            return
        thunder = ""
        if lightning:
            try:
                fired = bool(self._flash())
            except Exception as exc:
                self._log(f"Moderator entrance lightning failed: {exc}")
                fired = False
            thunder = " + lightning" if fired else " (no lightning - the add-on is not ready on this map)"
        self._log(f"Moderator entrance: {message}{thunder}")
        self._refresh_status(f"{stamp} {who} - announced{thunder}")
