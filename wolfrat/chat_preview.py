"""A one-line preview that looks like the game's chat: dark background,
the colour codes drawn as colours (see chat_markup)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from wolfrat import chat_markup


class ChatPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setStyleSheet(
            "QLabel { background: #1b1f18; border: 1px solid #3a4030; padding: 4px 6px;"
            " font-family: Tahoma, Arial; font-weight: bold; font-size: 9pt; }")

    def show_line(self, text: str) -> None:
        self.setText(chat_markup.to_html(text) if text.strip() else
                     '<span style="color:#707070">(nothing to show)</span>')
