"""Edit Order / Shuffle for the Mission Cycle (SgtMudd's request, 2026-11-21).

The admin reorders a COPY of the rotation here: drag and drop with the mouse,
the move buttons, or Shuffle (a deck-of-cards mix, pressed as often as they
like). Nothing reaches the server until OK.

Retail has no reorder verb: the MISSION sub-verbs are LIST, AVAILABLE, ADD,
REMOVE, CLEAR, CYCLE and SETNEXT (OpenNova docs, net/novaworld-net-re.md
5.70.8). So MissionsTab applies the new order as MISSION CLEAR, then every
map added back in order (the same serial feed Load Preset uses).

Dale, 2026-11-25: Delete takes maps off the list here, and "OK and Save"
also writes the edited list back to the saved list it came from, so a
shuffle is still there the next time WolfRAT starts.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Sequence

from PyQt6.QtCore import QEvent, QItemSelectionModel, Qt
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

_RNG = random.SystemRandom()

# The dialog never asks for more than this; small screens get 90% of theirs.
_PREFERRED_WIDTH = 640
_PREFERRED_HEIGHT = 700


@dataclass(frozen=True)
class OrderEntry:
    """One map in the cycle, as the order editor shows and returns it."""

    filename: str
    title: str = ""
    double_time: bool = False
    is_current: bool = False

    def as_preset(self) -> dict:
        """The {"file", "count"} form MissionsTab feeds to the server."""
        return {"file": self.filename, "count": 2 if self.double_time else 1}


class EditedOrder(list):
    """What the editor returns on OK: the maps in order (a plain list), plus
    ``save``, True when the admin pressed OK and Save."""

    save = False


def delete_rows(entries: Sequence, rows: Iterable[int]) -> tuple[list, list[int]]:
    """Take the selected rows out. The row now at the first removed place
    (or the new last row) is picked next, so Delete can be pressed again."""
    gone = set(rows)
    items = [item for row, item in enumerate(entries) if row not in gone]
    if not gone or not items:
        return items, []
    return items, [min(min(gone), len(items) - 1)]


def shuffled(entries: Sequence[OrderEntry], rng=None) -> list[OrderEntry]:
    """A fresh deck-of-cards mix of ``entries`` (Fisher-Yates).

    When the list holds at least two different maps the result always differs
    from the order it was given, so every press of Shuffle visibly changes it.
    """
    rng = rng or _RNG
    items = list(entries)
    if len(set(items)) < 2:
        return items
    mixed = items[:]
    for _ in range(64):
        mixed = items[:]
        rng.shuffle(mixed)
        if mixed != items:
            break
    return mixed


def move_up(entries: Sequence, rows: Iterable[int]) -> tuple[list, list[int]]:
    """Move the selected rows up one place; a block at the top stays put."""
    items = list(entries)
    moved = []
    first_free = 0
    for row in sorted(set(rows)):
        if row > first_free:
            items[row - 1], items[row] = items[row], items[row - 1]
            moved.append(row - 1)
            first_free = row
        else:
            moved.append(row)
            first_free = row + 1
    return items, moved


def move_down(entries: Sequence, rows: Iterable[int]) -> tuple[list, list[int]]:
    """Move the selected rows down one place; a block at the bottom stays."""
    items = list(entries)
    moved = []
    last_free = len(items) - 1
    for row in sorted(set(rows), reverse=True):
        if row < last_free:
            items[row + 1], items[row] = items[row], items[row + 1]
            moved.append(row + 1)
            last_free = row
        else:
            moved.append(row)
            last_free = row - 1
    return items, sorted(moved)


def move_to_top(entries: Sequence, rows: Iterable[int]) -> tuple[list, list[int]]:
    """The selected rows first (keeping their order), then the rest."""
    chosen = sorted(set(rows))
    picked = [entries[row] for row in chosen]
    rest = [item for row, item in enumerate(entries) if row not in set(chosen)]
    return picked + rest, list(range(len(picked)))


def move_to_bottom(
    entries: Sequence, rows: Iterable[int]
) -> tuple[list, list[int]]:
    """The rest first, then the selected rows (keeping their order)."""
    chosen = sorted(set(rows))
    picked = [entries[row] for row in chosen]
    rest = [item for row, item in enumerate(entries) if row not in set(chosen)]
    start = len(rest)
    return rest + picked, list(range(start, start + len(picked)))


def _stem(filename: str) -> str:
    for extension in (".bms", ".npj", ".npz"):
        if filename.lower().endswith(extension):
            return filename[: -len(extension)]
    return filename


class MapOrderDialog(QDialog):
    """Reorder a copy of the Mission Cycle; ``entries()`` is the result."""

    def __init__(
        self, entries: Sequence[OrderEntry], parent=None, rng=None,
        save_name: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Map Order")
        self.setModal(True)
        self._original = list(entries)
        self._rng = rng
        self._save_name = save_name
        self._save_chosen = False
        self._shuffles = 0
        self._store: list[OrderEntry] = []
        self._rendering = False
        self._build_ui()
        self._set_entries(self._original)
        self._fit_to_screen()

    @classmethod
    def ask(
        cls, entries: Sequence[OrderEntry], parent=None,
        save_name: str | None = None,
    ):
        """Show the editor; an EditedOrder on OK / OK and Save, None on
        Cancel. ``save_name`` is the saved list OK and Save writes to (None:
        the button says "OK and Save As..." and the caller asks a name)."""
        dialog = cls(entries, parent, save_name=save_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result_order()

    def result_order(self) -> EditedOrder:
        order = EditedOrder(self.entries())
        order.save = self._save_chosen
        return order

    def accept_and_save(self) -> None:
        self._save_chosen = True
        self.accept()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        hint = QLabel(
            "Drag maps with the mouse to change the order (Ctrl or Shift + "
            "click picks several). Shuffle mixes the whole list like a deck "
            "of cards: press it as often as you like. Delete takes the "
            "picked maps off the list."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(6)

        self.map_list = QListWidget()
        lst = self.map_list
        lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        lst.setDefaultDropAction(Qt.DropAction.MoveAction)
        lst.setDragDropOverwriteMode(False)
        lst.setDropIndicatorShown(True)
        lst.setAutoScroll(True)
        lst.setAutoScrollMargin(32)  # drag near the edge to scroll a long list
        lst.setAlternatingRowColors(True)
        lst.setUniformItemSizes(True)
        # Per item, not per pixel: while a map is dragged against the edge,
        # every mouse wiggle restarts Qt's auto-scroll from its slowest step,
        # which per pixel is 1 px per tick (useless on a 200-map list).
        lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        lst.setStyleSheet(
            "QListWidget { alternate-background-color: #0e0e03; }"
            "QListWidget::item { padding: 2px 4px; }"
            "QListWidget::item:selected { background-color: #6a5600;"
            " color: #fff3b0; }"
        )
        model = lst.model()
        model.rowsMoved.connect(self._after_drop)
        model.rowsInserted.connect(self._after_drop)
        model.rowsRemoved.connect(self._after_drop)
        lst.itemSelectionChanged.connect(self._update_status)
        body.addWidget(lst, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.shuffle_button = self._side_button(
            "Shuffle",
            "Mix the whole list like a deck of cards. Press it again for a "
            "new mix.",
            self.shuffle,
        )
        self.up_button = self._side_button(
            "Move Up", "Move the picked maps up one place.",
            self.move_selected_up,
        )
        self.down_button = self._side_button(
            "Move Down", "Move the picked maps down one place.",
            self.move_selected_down,
        )
        self.top_button = self._side_button(
            "To Top", "Move the picked maps to the top of the list.",
            self.move_selected_to_top,
        )
        self.bottom_button = self._side_button(
            "To Bottom", "Move the picked maps to the bottom of the list.",
            self.move_selected_to_bottom,
        )
        self.delete_button = self._side_button(
            "Delete",
            "Take the picked maps off this list (Delete key works too). "
            "Undo Changes brings them back.",
            self.delete_selected,
        )
        self.undo_button = self._side_button(
            "Undo Changes",
            "Put the list back the way the server has it now: the old order "
            "and any deleted maps.",
            self.undo_changes,
        )
        side.addWidget(self.shuffle_button)
        side.addSpacing(8)
        for button in (
            self.up_button, self.down_button,
            self.top_button, self.bottom_button,
        ):
            side.addWidget(button)
        side.addSpacing(8)
        side.addWidget(self.delete_button)
        side.addSpacing(8)
        side.addWidget(self.undo_button)
        side.addStretch(1)
        lst.installEventFilter(self)  # the Delete key in the list
        body.addLayout(side)
        layout.addLayout(body, 1)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        if self._save_name:
            save_text = (
                f"OK and Save also saves this list as your preset "
                f"'{self._save_name}', so it is there the next time you "
                "load it, even after WolfRAT restarts."
            )
        else:
            save_text = (
                "OK and Save As also saves this list as a preset: WolfRAT "
                "asks for its name."
            )
        self.apply_hint = QLabel(
            "OK puts this list on the server: it clears the map list and "
            "adds these maps back in this order (a few seconds). " + save_text
        )
        self.apply_hint.setWordWrap(True)
        self.apply_hint.setStyleSheet("color: #a89a50; font-size: 8pt;")
        layout.addWidget(self.apply_hint)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.save_button = QPushButton(
            "OK and Save" if self._save_name else "OK and Save As..."
        )
        self.save_button.setAutoDefault(False)
        self.save_button.setToolTip(
            f"Put this list on the server and save it as '{self._save_name}'."
            if self._save_name else
            "Put this list on the server and save it under a name you choose."
        )
        self.button_box.addButton(
            self.save_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        self.save_button.clicked.connect(self.accept_and_save)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def eventFilter(self, watched, event):
        if (
            watched is self.map_list
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Delete
        ):
            self.delete_selected()
            return True
        return super().eventFilter(watched, event)

    def _side_button(self, text, tip, slot) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setMinimumWidth(112)
        button.setAutoDefault(False)  # Enter in the list must not press it
        button.clicked.connect(slot)
        return button

    def _fit_to_screen(self) -> None:
        """Never bigger than 90% of the screen (SgtMudd plays on ~720p)."""
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            screen = parent.screen()
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(_PREFERRED_WIDTH, _PREFERRED_HEIGHT)
            return
        area = screen.availableGeometry()
        width = min(_PREFERRED_WIDTH, int(area.width() * 0.9))
        height = min(_PREFERRED_HEIGHT, int(area.height() * 0.9))
        self.resize(max(width, self.minimumSizeHint().width()),
                    max(height, self.minimumSizeHint().height()))

    # -------------------------------------------------------------- the list

    def entries(self) -> list[OrderEntry]:
        """The maps in the order shown, top first."""
        return [
            self._store[self.map_list.item(row).data(Qt.ItemDataRole.UserRole)]
            for row in range(self.map_list.count())
        ]

    def changed(self) -> bool:
        return self.entries() != self._original

    def selected_rows(self) -> list[int]:
        return sorted(
            self.map_list.row(item) for item in self.map_list.selectedItems()
        )

    def _label(self, row: int, entry: OrderEntry) -> str:
        title = entry.title or _stem(entry.filename)
        text = f"{row + 1:>3}.  {title}"
        if title != _stem(entry.filename):
            text += f"   ({entry.filename})"
        if entry.double_time:
            text += "   x2"
        if entry.is_current:
            text += "   ▶ running"
        return text

    def _set_entries(self, entries, selected_rows=(), keep_scroll=False):
        bar = self.map_list.verticalScrollBar()
        scroll = bar.value()
        self._rendering = True
        try:
            self.map_list.clear()
            self._store = list(entries)
            for key, entry in enumerate(self._store):
                item = QListWidgetItem(self._label(key, entry))
                item.setData(Qt.ItemDataRole.UserRole, key)
                # Not drop-enabled: a drop always lands BETWEEN two maps.
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                if entry.is_current:
                    item.setForeground(QColor("#9dff70"))
                self.map_list.addItem(item)
            for row in selected_rows:
                self.map_list.item(row).setSelected(True)
            if selected_rows:
                self.map_list.setCurrentRow(
                    selected_rows[0],
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
        finally:
            self._rendering = False
        if keep_scroll:
            bar.setValue(scroll)
        if selected_rows:
            self.map_list.scrollToItem(
                self.map_list.item(selected_rows[0]),
                QAbstractItemView.ScrollHint.EnsureVisible,
            )
        self._update_status()

    def _after_drop(self, *_args) -> None:
        """A drag moved rows: renumber them in their new order."""
        if self._rendering:
            return
        for row in range(self.map_list.count()):
            item = self.map_list.item(row)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key is None or not 0 <= key < len(self._store):
                continue
            item.setText(self._label(row, self._store[key]))
        self._update_status()

    def _update_status(self) -> None:
        if self._rendering:
            return
        count = self.map_list.count()
        parts = [f"{count} map" + ("" if count == 1 else "s")]
        changed = self.changed()
        removed = len(self._original) - count
        if removed > 0:
            parts.append(f"{removed} deleted")
        if changed and self._order_changed():
            parts.append("order changed")
        if self._shuffles:
            parts.append(f"shuffled {self._shuffles}x")
        self.status_label.setText("   |   ".join(parts))
        picked_count = len(self.map_list.selectedItems())
        picked = picked_count > 0
        for button in (
            self.up_button, self.down_button,
            self.top_button, self.bottom_button,
        ):
            button.setEnabled(picked)
        # The list can never be emptied here: OK would clear the server's
        # rotation and add nothing back. Clear does that on purpose.
        self.delete_button.setEnabled(picked and picked_count < count)
        self.undo_button.setEnabled(changed)
        self.shuffle_button.setEnabled(len(set(self.entries())) > 1)

    # -------------------------------------------------------------- actions

    def shuffle(self) -> None:
        self._shuffles += 1
        self._set_entries(shuffled(self.entries(), self._rng))
        self.map_list.scrollToTop()

    def _move(self, mover) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        items, new_rows = mover(self.entries(), rows)
        self._set_entries(items, new_rows, keep_scroll=True)

    def move_selected_up(self) -> None:
        self._move(move_up)

    def move_selected_down(self) -> None:
        self._move(move_down)

    def move_selected_to_top(self) -> None:
        self._move(move_to_top)

    def move_selected_to_bottom(self) -> None:
        self._move(move_to_bottom)

    def _order_changed(self) -> bool:
        """True when the maps still on the list are not in their old order."""
        from collections import Counter

        left = Counter(self.entries())
        kept = []
        for entry in self._original:
            if left[entry] > 0:
                kept.append(entry)
                left[entry] -= 1
        return kept != self.entries()

    def delete_selected(self) -> None:
        rows = self.selected_rows()
        if not rows or len(rows) >= self.map_list.count():
            return
        items, new_rows = delete_rows(self.entries(), rows)
        self._set_entries(items, new_rows, keep_scroll=True)

    def undo_changes(self) -> None:
        self._shuffles = 0
        self._set_entries(self._original)
