"""The Manual mode question and its switch on the Server tab (manual_mode.py)."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from wolfrat import manual_mode

ASK = "ask"        # the panel's button while the question is unanswered

STAYS_ON = ("the chat log, the player list, connection logs and stats, the map list, and every "
            "button you press yourself (kick, ban, swap, chat, change map, settings, weapons, "
            "starting a map vote).")
GOES_QUIET = ("welcome and timed messages, killing sprees and first blood, lead and zone "
              "announcements, replies to chat commands (!kd, !switch, !vote, !skip and the "
              "moderator commands), the bad word filter, anti-spam, auto-balance, automatic map "
              "votes, Map Weapons, moderator entrances, connection checks and the ban list's "
              "automatic kicks.")
SHORT_OFF = ("Messages, sprees, chat commands, automatic votes, auto-balance and automatic "
             "kicks are off. Your ticks on the other tabs are kept.")


class ManualModeDialog(QDialog):
    """Asked once per server when WolfRAT connects from another PC."""

    def __init__(self, server: str, parent=None, by_hand: bool = False):
        super().__init__(parent)
        self.choice: Optional[str] = None
        self.setWindowTitle("WolfRAT - automatic features" if by_hand
                            else "WolfRAT - connecting from another PC")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(f"Automatic features on {server}" if by_hand
                      else f"This WolfRAT is not on the same PC as the server ({server}).")
        head.setWordWrap(True)
        head.setStyleSheet("font-size: 11pt; font-weight: bold; color: #e8c840;")
        layout.addWidget(head)

        ask = QLabel("Is another WolfRAT already running this server, for example one on "
                     "the server PC itself?")
        ask.setWordWrap(True)
        ask.setStyleSheet("font-size: 10pt;")
        layout.addWidget(ask)

        self.manual_btn = QPushButton("Yes - put this WolfRAT in Manual mode")
        self.manual_btn.setMinimumHeight(38)
        self.manual_btn.setStyleSheet("font-size: 10pt; font-weight: bold;")
        self.manual_btn.clicked.connect(lambda: self._pick(manual_mode.MANUAL))
        layout.addWidget(self.manual_btn)
        manual_note = QLabel(
            "This WolfRAT does nothing unless you click it, so it never doubles up with the "
            "other one.<br><br><b>Still works:</b> " + STAYS_ON
            + "<br><br><b>Switched off:</b> " + GOES_QUIET)
        manual_note.setTextFormat(Qt.TextFormat.RichText)
        manual_note.setWordWrap(True)
        manual_note.setStyleSheet("font-size: 9pt; color: #a89830; padding: 0 4px 6px 4px;")
        layout.addWidget(manual_note)

        self.full_btn = QPushButton("No - this is the only WolfRAT on this server")
        self.full_btn.setMinimumHeight(38)
        self.full_btn.setStyleSheet("font-size: 10pt; font-weight: bold;")
        self.full_btn.clicked.connect(lambda: self._pick(manual_mode.FULL))
        layout.addWidget(self.full_btn)
        full_note = QLabel("Everything works as normal: this WolfRAT runs the messages, votes, "
                           "chat commands and the rest.")
        full_note.setWordWrap(True)
        full_note.setStyleSheet("font-size: 9pt; color: #a89830; padding: 0 4px 6px 4px;")
        layout.addWidget(full_note)

        foot = QLabel("WolfRAT remembers your answer for this server. You can change it any "
                      "time on the Server tab."
                      + ("" if by_hand else " Until you answer, the automatic features stay paused."))
        foot.setWordWrap(True)
        foot.setStyleSheet("font-size: 9pt; color: #6a6a20;")
        layout.addWidget(foot)
        # No default button: Enter must not pick one by accident.
        for button in (self.manual_btn, self.full_btn):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _pick(self, mode: str) -> None:
        self.choice = mode
        self.accept()


class ManualModePanel(QWidget):
    """Server tab, one row in Server Status: the mode and a button to change it.

    One row only: the Server tab has no height to spare at 960x650.  The
    button opens the same question, which explains both choices.
    """

    def __init__(self, on_switch: Callable[[str], None], button_cls=QPushButton, parent=None):
        super().__init__(parent)
        self._on_switch = on_switch
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.state_lbl = QLabel("")
        row.addWidget(self.state_lbl, 1)
        self.switch_btn = QPushButton("Change...")
        self.switch_btn.setStyleSheet("font-size: 9pt; padding: 1px 10px;")
        self.switch_btn.clicked.connect(lambda: self._on_switch(ASK))
        row.addWidget(self.switch_btn)
        self.setToolTip("Manual mode is for a second WolfRAT on a server that another WolfRAT "
                        "already runs.\n\nManual mode switches off: " + GOES_QUIET
                        + "\n\nStill works: " + STAYS_ON)
        self.refresh()

    def refresh(self) -> None:
        state = manual_mode.state
        mode = state.mode
        self.switch_btn.setEnabled(bool(state.key))
        if not state.key:
            self.state_lbl.setText("-")
            self.state_lbl.setStyleSheet("")
        elif mode == manual_mode.MANUAL:
            self.state_lbl.setText("MANUAL MODE - only what you click")
            self.state_lbl.setStyleSheet("font-weight: bold; color: #ff9040;")
        elif mode == manual_mode.UNDECIDED:
            self.state_lbl.setText("PAUSED - waiting for your answer")
            self.state_lbl.setStyleSheet("font-weight: bold; color: #ff9040;")
        else:
            self.state_lbl.setText("Full - everything on")
            self.state_lbl.setStyleSheet("color: #50ff50;")
