"""The "Colours..." pop-up: the game's colour codes to copy into a message
(Dale 2026-09-26: admins type the codes, this lists them)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QDialog, QGridLayout, QLabel,
                             QPushButton, QVBoxLayout)

# (name, hex) - lower case, the form proven in game.
COLOURS = (
    ("Green", "00ff00"),
    ("Yellow", "ffff00"),
    ("Orange", "ff8000"),
    ("Red", "ff3030"),
    ("Pink", "ff80c0"),
    ("Purple", "8080ff"),
    ("Light blue", "40c0ff"),
    ("Cyan", "00ffff"),
    ("White", "ffffff"),
    ("Grey", "c0c0c0"),
)
BACK_TO_NORMAL = "<co>"

HELP = ("Put a code in front of the words you want coloured. "
        "<co> ends the colour and goes back to the normal chat colour - "
        "after it you can start another colour. Codes count towards the "
        "character limit: a colour is 9 characters, <co> is 4.\n"
        "Example:  <c40c0ff>Welcome<co> to my server!")


def code(hex_colour: str) -> str:
    return f"<c{hex_colour}>"


class ColourCodesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Colour codes")
        layout = QVBoxLayout(self)
        intro = QLabel(HELP)
        intro.setTextFormat(Qt.TextFormat.PlainText)    # or Qt reads the codes as HTML
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        rows = [(name, code(hex_colour), hex_colour) for name, hex_colour in COLOURS]
        rows.append(("Back to normal", BACK_TO_NORMAL, "e8e8e8"))
        self.copy_buttons = {}
        for row, (name, text, swatch) in enumerate(rows):
            label = QLabel(name)
            label.setStyleSheet(f"QLabel {{ background: #1b1f18; color: #{swatch};"
                                " font-weight: bold; padding: 2px 8px; }")
            grid.addWidget(label, row, 0)
            shown = QLabel(text)
            shown.setTextFormat(Qt.TextFormat.PlainText)
            shown.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            shown.setStyleSheet("font-family: Consolas, monospace;")
            grid.addWidget(shown, row, 1)
            button = QPushButton("Copy")
            button.clicked.connect(lambda _=False, t=text, b=button: self._copy(t, b))
            grid.addWidget(button, row, 2)
            self.copy_buttons[text] = button
        layout.addLayout(grid)

        self.status = QLabel("Copy a code, then paste it into the message (Ctrl+V).")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

    def _copy(self, text: str, button: QPushButton):
        QApplication.clipboard().setText(text)
        for other in self.copy_buttons.values():
            other.setText("Copy")
        button.setText("Copied")
        self.status.setText(f"{text} copied - paste it into the message (Ctrl+V).")


def show_colour_codes(parent) -> ColourCodesDialog:
    """One pop-up per parent, left open beside the form while typing."""
    dialog = getattr(parent, "_colour_codes_dialog", None)
    if dialog is None:
        dialog = ColourCodesDialog(parent)
        parent._colour_codes_dialog = dialog
    dialog.show()
    dialog.raise_()
    return dialog
