"""The "Private welcome" box on the Messages tab.

Shows what is in the server's script right now, lets the admin change it, and
says in one sentence what state things are in. All file work is in
`server_wac`; this is only the form.

One box per line, colour codes typed straight in (Dale 2026-09-26: a
"Colours..." pop-up lists them to copy), a counter against the script's
63-character limit and a preview in the game's colours.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from wolfrat import server_wac
from wolfrat.chat_preview import ChatPreview
from wolfrat.colour_codes import show_colour_codes

FIRST_LINE_HINT = "<c40c0ff>Welcome<co> to my server!"
SUGGESTED_SECOND_LINE = "<cffff00>Chat commands:<co> !kd  !switch  !vote mapname  !skip"


class _Line:
    """The widgets for one whisper line: the text (codes and all), a
    Colours... button, the counter and the preview."""

    def __init__(self, placeholder: str, on_edit, owner):
        self.text = QLineEdit()
        self.text.setMaxLength(server_wac.WAC_STRING_MAX)
        self.text.setPlaceholderText(placeholder)
        self.text.textChanged.connect(on_edit)
        self.colours_btn = QPushButton("Colours...")
        self.colours_btn.setToolTip("The colour codes, ready to copy into the message.")
        self.colours_btn.clicked.connect(lambda: show_colour_codes(owner))
        self.count = QLabel()
        self.count.setStyleSheet("color: #666;")
        self.preview = ChatPreview()

    def add_to(self, layout, timing_row: QHBoxLayout):
        row = QHBoxLayout()
        row.addWidget(self.text, 1)
        row.addWidget(self.colours_btn)
        layout.addLayout(row)
        timing_row.addStretch()
        timing_row.addWidget(self.count)
        layout.addLayout(timing_row)
        layout.addWidget(self.preview)

    def widgets(self):
        return (self.text, self.colours_btn)

    def value(self) -> str:
        return self.text.text().strip()

    def refresh(self) -> Optional[str]:
        """Update the counter and preview; returns the line's problem."""
        line = self.value()
        self.count.setText(f"{len(line)}/{server_wac.WAC_STRING_MAX} characters")
        self.preview.show_line(line)
        return server_wac.line_problem(line)


def _shown(welcome: Optional[server_wac.Welcome]):
    """What the form shows for a saved welcome - the lines exactly as they
    sit in the script, so saved and typed compare like for like."""
    if welcome is None:
        return None
    second = server_wac.compose(welcome.text2, welcome.colour2) if welcome.text2 else ""
    return (server_wac.compose(welcome.text, welcome.colour), welcome.seconds,
            second, welcome.gap if second else None)


class PrivateWelcomePanel(QGroupBox):
    def __init__(self, server_dir: Callable[[], str],
                 log: Optional[Callable[[str], None]] = None, parent=None):
        super().__init__("Private welcome (only the player who joined sees it)", parent)
        self._server_dir = server_dir
        self._log = log or (lambda _text: None)
        self._ready = False
        self._build()
        self._ready = True
        self.reload()

    def _build(self):
        layout = QVBoxLayout(self)

        self.enabled_cb = QCheckBox("Whisper a welcome to every player who joins")
        self.enabled_cb.toggled.connect(self._edited)
        layout.addWidget(self.enabled_cb)

        self.line1 = _Line(FIRST_LINE_HINT, self._edited, self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show it"))
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(server_wac.MIN_SECONDS, server_wac.MAX_SECONDS)
        self.seconds_spin.setSuffix(" s after they spawn")
        self.seconds_spin.setValue(server_wac.DEFAULT_SECONDS)
        self.seconds_spin.valueChanged.connect(self._edited)
        row.addWidget(self.seconds_spin)
        self.line1.add_to(layout, row)

        self.second_cb = QCheckBox("Then a second line - tell them the chat commands they can use")
        self.second_cb.toggled.connect(self._second_toggled)
        layout.addWidget(self.second_cb)

        self.line2 = _Line(SUGGESTED_SECOND_LINE, self._edited, self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show it"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(server_wac.MIN_GAP, server_wac.MAX_GAP)
        self.gap_spin.setSuffix(" s after the first line")
        self.gap_spin.setValue(server_wac.DEFAULT_GAP)
        self.gap_spin.valueChanged.connect(self._edited)
        row.addWidget(self.gap_spin)
        self.line2.add_to(layout, row)

        row = QHBoxLayout()
        row.addStretch()
        self.save_btn = QPushButton("Save to the server")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        self.state_lbl = QLabel()
        self.state_lbl.setWordWrap(True)
        layout.addWidget(self.state_lbl)

        self.text_input, self.text2_input = self.line1.text, self.line2.text

    # ------------------------------------------------------------- state
    def _form(self) -> Optional[server_wac.Welcome]:
        if not self.enabled_cb.isChecked():
            return None
        # Colour "" : the codes are in the text itself.
        if not self.second_cb.isChecked():
            return server_wac.Welcome(self.line1.value(), self.seconds_spin.value(), "")
        return server_wac.Welcome(self.line1.value(), self.seconds_spin.value(), "",
                                  self.line2.value(), "", self.gap_spin.value())

    def _second_toggled(self, on: bool):
        if on and not self.text2_input.text().strip():
            self.text2_input.setText(SUGGESTED_SECOND_LINE)     # a start; edit to suit the server
        self._edited()

    def _on_server(self) -> Optional[server_wac.Welcome]:
        folder = self._server_dir()
        if not folder:
            return None
        try:
            return server_wac.read_welcome(folder)
        except server_wac.ServerWacError:
            return None

    def showEvent(self, event):
        super().showEvent(event)
        if not self.save_btn.isEnabled():      # nothing unsaved in the form
            self.reload()

    def reload(self):
        """Fill the form from what the server's script holds."""
        current = self._on_server()
        widgets = (self.enabled_cb, self.seconds_spin, self.second_cb, self.gap_spin,
                   *self.line1.widgets(), *self.line2.widgets())
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled_cb.setChecked(current is not None)
        if current is not None:
            self.text_input.setText(server_wac.compose(current.text, current.colour))
            self.seconds_spin.setValue(current.seconds)
            self.second_cb.setChecked(bool(current.text2))
            if current.text2:
                self.text2_input.setText(server_wac.compose(current.text2, current.colour2))
                self.gap_spin.setValue(current.gap)
        for widget in widgets:
            widget.blockSignals(False)
        self._edited()

    def _edited(self, *_):
        if not self._ready:          # widgets still being built
            return
        on = self.enabled_cb.isChecked()
        second = on and self.second_cb.isChecked()
        for widget in (self.seconds_spin, self.second_cb, *self.line1.widgets()):
            widget.setEnabled(on)
        for widget in (self.gap_spin, *self.line2.widgets()):
            widget.setEnabled(second)
        problem = self.line1.refresh()
        problem2 = self.line2.refresh()
        self.line2.preview.setVisible(second)
        folder = self._server_dir()
        if not folder:
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(
                "WolfRAT cannot see a game server on this PC yet. The private welcome is written "
                "into the server's folder, so WolfRAT has to run on the same PC as the server.")
            return
        wanted, current = self._form(), self._on_server()
        if not wanted:
            problem = None
        elif not problem and second:
            if not self.text2_input.text().strip():
                problem = "Type the second line, or untick it."
            elif problem2:
                problem = f"Second line: {problem2}"
        if problem:
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(problem)
        elif _shown(wanted) == _shown(current):
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(
                "Saved on the server. New players see it a few seconds after they spawn "
                "(once on each map)." if current else "Off. Nothing is whispered to new players.")
        else:
            self.save_btn.setEnabled(True)
            self.state_lbl.setText("Not saved yet - press 'Save to the server'.")

    # ------------------------------------------------------------- action
    def save(self):
        folder = self._server_dir()
        if not folder:
            self._edited()
            return
        wanted = self._form()
        try:
            if wanted is None:
                server_wac.remove_welcome(folder)
                self._log("Private welcome switched off (from the next map change).")
            else:
                server_wac.write_welcome(folder, wanted)
                self._log(f"Private welcome saved: {wanted.text}"
                          + (f"  /  {wanted.text2}" if wanted.text2 else ""))
        except server_wac.ServerWacError as exc:
            self.state_lbl.setText(str(exc))
            self._log(str(exc))
            return
        self._edited()
        self.state_lbl.setText(self.state_lbl.text().split(" New players")[0]
                               + " It takes effect at the next map change - the game only reads "
                                 "its scripts when a map loads.")
