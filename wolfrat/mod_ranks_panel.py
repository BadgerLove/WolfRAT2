"""The People / Ranks pages at the top of the Mods tab.

Owns the widgets only.  The rules live in `mod_ranks.ModRoster`; the tab
gives this panel the roster plus two callbacks - `changed()` (save, refresh
the command reference) and `log(text)` (the Mod activity list).
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QMessageBox, QPushButton,
                             QSizePolicy,
                             QScrollArea, QTableWidget, QTableWidgetItem,
                             QTabWidget, QVBoxLayout, QWidget)

from wolfrat.mod_ranks import (ADMIN, MODERATOR, PERMISSION_GROUPS, ModRoster,
                               RankError)

_LIST_STYLE = """
    QListWidget, QTableWidget {
        background-color: #0a0a00;
        color: #e8c840;
        border: 1px solid #3a3a00;
        font-size: 11pt;
    }
"""


def _let_shrink(combo: QComboBox) -> None:
    """Long names must not force the column wider than a 1024x768 desktop."""
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(8)


class ModRanksPanel(QTabWidget):
    """Who is a mod and at what rank; what each rank may type."""

    def __init__(
        self,
        roster: ModRoster,
        changed: Callable[[], None],
        log: Callable[[str], None],
        parent=None,
    ):
        super().__init__(parent)
        self.roster = roster
        self._changed = changed
        self._log = log
        self._filling = False
        self._perm_boxes: dict[str, QCheckBox] = {}
        self.addTab(self._build_people_page(), "People")
        self.addTab(self._build_ranks_page(), "Ranks")
        self.refresh()

    # ------------------------------------------------------------------ UI

    def _build_people_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.people_table = QTableWidget(0, 2)
        self.people_table.setHorizontalHeaderLabels(["Player", "Rank"])
        self.people_table.verticalHeader().setVisible(False)
        self.people_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.people_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.people_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.people_table.setShowGrid(False)
        header = self.people_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.people_table.setStyleSheet(_LIST_STYLE)
        # takes whatever height is left over, never less than a few rows
        self.people_table.setMinimumHeight(150)
        self.people_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout.addWidget(self.people_table)

        add_row = QHBoxLayout()
        self.mod_input = QLineEdit()
        self.mod_input.setPlaceholderText("Player name (exact, case-insensitive)")
        self.mod_input.returnPressed.connect(self._add_typed)
        add_row.addWidget(self.mod_input, 1)
        layout.addLayout(add_row)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Add as:"))
        self.new_rank_combo = QComboBox()
        self.new_rank_combo.setToolTip("The rank new people are added at")
        _let_shrink(self.new_rank_combo)
        add_row.addWidget(self.new_rank_combo, 1)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_typed)
        add_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        add_row.addWidget(remove_btn)
        layout.addLayout(add_row)

        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("Quick add:"))
        self.player_combo = QComboBox()
        self.player_combo.setPlaceholderText("Select online player...")
        _let_shrink(self.player_combo)
        quick_row.addWidget(self.player_combo, 1)
        quick_btn = QPushButton("+Add")
        quick_btn.setMinimumWidth(70)
        quick_btn.clicked.connect(self._add_online)
        quick_row.addWidget(quick_btn)
        layout.addLayout(quick_row)
        return page

    def _build_ranks_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.rank_list = QListWidget()
        self.rank_list.setStyleSheet(_LIST_STYLE)
        self.rank_list.setMaximumHeight(120)
        self.rank_list.currentRowChanged.connect(lambda _row: self._show_rank())
        layout.addWidget(self.rank_list)

        buttons = QHBoxLayout()
        self.new_rank_btn = QPushButton("New rank...")
        self.new_rank_btn.clicked.connect(self._new_rank)
        buttons.addWidget(self.new_rank_btn)
        self.rename_rank_btn = QPushButton("Rename...")
        self.rename_rank_btn.clicked.connect(self._rename_rank)
        buttons.addWidget(self.rename_rank_btn)
        self.delete_rank_btn = QPushButton("Delete")
        self.delete_rank_btn.clicked.connect(self._delete_rank)
        buttons.addWidget(self.delete_rank_btn)
        layout.addLayout(buttons)

        self.rank_hint = QLabel()
        self.rank_hint.setWordWrap(True)
        self.rank_hint.setStyleSheet("font-size: 9pt; color: #a89830;")
        layout.addWidget(self.rank_hint)

        host = QWidget()
        boxes = QVBoxLayout(host)
        boxes.setContentsMargins(0, 0, 6, 0)
        for section, perms in PERMISSION_GROUPS:
            heading = QLabel(section)
            heading.setStyleSheet("font-weight: bold; color: #e8c840; margin-top: 4px;")
            boxes.addWidget(heading)
            for key, label, commands in perms:
                box = QCheckBox(label)
                box.setToolTip("  ".join(commands))
                box.toggled.connect(lambda on, k=key: self._permission_toggled(k, on))
                self._perm_boxes[key] = box
                boxes.addWidget(box)
        boxes.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setMinimumHeight(140)
        area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        area.setWidget(host)
        layout.addWidget(area, 1)
        return page

    # ------------------------------------------------------------- refresh

    def refresh(self, select_rank: Optional[str] = None) -> None:
        """Redraw both pages from the roster."""
        self._filling = True
        try:
            rank_names = self.roster.rank_names()

            keep = self.new_rank_combo.currentText() or MODERATOR
            self.new_rank_combo.clear()
            self.new_rank_combo.addItems(rank_names)
            self.new_rank_combo.setCurrentText(
                keep if keep in rank_names else MODERATOR
            )

            selected = self._selected_person()
            self.people_table.setRowCount(0)
            for name, rank in self.roster.members():
                row = self.people_table.rowCount()
                self.people_table.insertRow(row)
                self.people_table.setItem(row, 0, QTableWidgetItem(name))
                combo = QComboBox()
                combo.addItems(rank_names)
                combo.setCurrentText(rank)
                # the theme's padding is not in Qt's own size hint
                combo.setMinimumWidth(
                    max(combo.fontMetrics().horizontalAdvance(r) for r in rank_names) + 44
                )
                combo.currentTextChanged.connect(
                    lambda new, who=name: self._rank_picked(who, new)
                )
                self.people_table.setCellWidget(row, 1, combo)
                if name == selected:
                    self.people_table.selectRow(row)

        finally:
            self._filling = False
        self._refresh_rank_list(select_rank)

    def _refresh_rank_list(self, select_rank: Optional[str] = None) -> None:
        rank_names = self.roster.rank_names()
        current = select_rank or self._selected_rank() or MODERATOR
        self._filling = True
        try:
            self.rank_list.clear()
            for rank in rank_names:
                count = self.roster.member_count(rank)
                self.rank_list.addItem(f"{rank}  ({count})")
            self.rank_list.setCurrentRow(
                rank_names.index(current) if current in rank_names
                else rank_names.index(MODERATOR)
            )
        finally:
            self._filling = False
        self._show_rank()

    def _show_rank(self) -> None:
        name = self._selected_rank()
        rank = next((r for r in self.roster.ranks() if r.name == name), None)
        if rank is None:
            return
        self._filling = True
        try:
            for key, box in self._perm_boxes.items():
                box.setChecked(key in rank.permissions)
                box.setEnabled(not rank.locked)
        finally:
            self._filling = False
        self.rename_rank_btn.setEnabled(not rank.builtin)
        self.delete_rank_btn.setEnabled(not rank.builtin)
        if rank.locked:
            self.rank_hint.setText(f"{ADMIN} always has every command.")
        else:
            self.rank_hint.setText(
                f"Tick the commands a {rank.name} may type in game chat. "
                "Saved as you tick."
            )

    def set_online_players(self, names: Iterable[str]) -> None:
        current = self.player_combo.currentText()
        self.player_combo.clear()
        for name in names:
            if name:
                self.player_combo.addItem(name)
        index = self.player_combo.findText(current)
        if index >= 0:
            self.player_combo.setCurrentIndex(index)

    # ------------------------------------------------------------- actions

    def _selected_person(self) -> Optional[str]:
        row = self.people_table.currentRow()
        item = self.people_table.item(row, 0) if row >= 0 else None
        return item.text() if item else None

    def _selected_rank(self) -> Optional[str]:
        row = self.rank_list.currentRow()
        names = self.roster.rank_names()
        return names[row] if 0 <= row < len(names) else None

    def _add(self, name: str) -> bool:
        rank = self.new_rank_combo.currentText() or MODERATOR
        if not self.roster.add_member(name, rank):
            return False
        self._log(f"Mod added: {name.strip()} ({rank})")
        self._commit()
        return True

    def _add_typed(self) -> None:
        if self._add(self.mod_input.text()):
            self.mod_input.clear()

    def _add_online(self) -> None:
        self._add(self.player_combo.currentText())

    def _remove_selected(self) -> None:
        name = self._selected_person()
        if name and self.roster.remove_member(name):
            self._log(f"Mod removed: {name}")
            self._commit()

    def _rank_picked(self, name: str, rank: str) -> None:
        if self._filling or not rank:
            return
        if self.roster.set_member_rank(name, rank):
            self._log(f"{name} is now {rank}")
            self._changed()
            # not refresh(): that would delete the combo box that is calling us
            self._refresh_rank_list()

    def _permission_toggled(self, key: str, allowed: bool) -> None:
        if self._filling:
            return
        rank = self._selected_rank()
        try:
            self.roster.set_permission(rank, key, allowed)
        except RankError as problem:
            self._warn(str(problem))
            self._show_rank()
            return
        self._changed()

    def _new_rank(self) -> None:
        name = self._ask_name("New rank", "Rank name (e.g. Super Moderator):", "")
        if name is None:
            return
        try:
            rank = self.roster.add_rank(name)
        except RankError as problem:
            self._warn(str(problem))
            return
        self._log(f"Rank added: {rank.name} (starts with Moderator's commands)")
        self._commit(select_rank=rank.name)

    def _rename_rank(self) -> None:
        old = self._selected_rank()
        if not old:
            return
        name = self._ask_name("Rename rank", "New name:", old)
        if name is None:
            return
        try:
            rank = self.roster.rename_rank(old, name)
        except RankError as problem:
            self._warn(str(problem))
            return
        self._log(f"Rank renamed: {old} -> {rank.name}")
        self._commit(select_rank=rank.name)

    def _delete_rank(self) -> None:
        name = self._selected_rank()
        if not name:
            return
        try:
            self.roster.delete_rank(name)
        except RankError as problem:
            self._warn(str(problem))
            return
        self._log(f"Rank deleted: {name}")
        self._commit(select_rank=MODERATOR)

    def _commit(self, select_rank: Optional[str] = None) -> None:
        self._changed()
        self.refresh(select_rank)

    # Replaced in tests: modal dialogs would block them.
    def _ask_name(self, title: str, label: str, initial: str) -> Optional[str]:
        text, ok = QInputDialog.getText(self, title, label, text=initial)
        return text if ok else None

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "Ranks", text)
