"""Map Weapons: copy a saved rule onto other maps (Weapons tab).

A rule used to be saved only for the map being played. Here every map the
server has is listed, like the Sky page: pick the rule to copy, tick maps,
Copy. Each ticked map gets its own copy, so changing one later (play it,
change a weapon, Save for this map) leaves the others alone.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from wolfrat import map_weapons


class CopyRuleDialog(QDialog):
    """Pick a saved rule, tick maps, Copy. `maps` = [(file, display name)]."""

    def __init__(self, keeper, maps: Sequence[tuple], source: Optional[map_weapons.MapRule] = None,
                 display_name: Callable[[str], str] = lambda name: name, parent=None) -> None:
        super().__init__(parent)
        self.keeper = keeper
        self.made: list = []
        self._display_name = display_name
        self.setWindowTitle("Copy a map's weapons to other maps")
        self.resize(640, 560)
        col = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Copy the weapons of:"))
        self.source_combo = QComboBox()
        for rule in keeper.config.rules:
            self.source_combo.addItem(self._rule_name(rule) + f"   ({rule.summary()})", rule.match)
        if source is not None:
            self.source_combo.setCurrentIndex(max(self.source_combo.findData(source.match), 0))
        self.source_combo.currentIndexChanged.connect(self._fill)
        row.addWidget(self.source_combo, 1)
        col.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Part of a map name, e.g. sulphur, TD-, western")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        col.addLayout(row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Copy", "Map", "Weapons now"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(lambda _item: self._count())
        self.table.cellClicked.connect(self._toggle)
        col.addWidget(self.table, 1)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("Untick all")
        clear_btn.clicked.connect(self._untick_all)
        buttons.addWidget(clear_btn)
        buttons.addStretch()
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        buttons.addWidget(self.copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        col.addLayout(buttons)

        self._maps = self._map_rows(maps)
        self._fill()

    # ------------------------------------------------------------ helpers
    def _rule_name(self, rule) -> str:
        return f"{rule.label}  ({rule.match})" if rule.label else rule.match

    def _source(self) -> Optional[map_weapons.MapRule]:
        match = self.source_combo.currentData()
        for rule in self.keeper.config.rules:
            if rule.match == match:
                return rule
        return None

    def _map_rows(self, maps) -> list:
        """(file, shown text), every map once, by display name."""
        seen, rows = set(), []
        for file_name, title in maps or ():
            key = map_weapons.map_key(file_name)
            if not key or key in seen:
                continue
            seen.add(key)
            title = title or self._display_name(file_name)
            shown = file_name if not title or title == file_name else f"{title}   ({file_name})"
            rows.append((file_name, title or file_name, shown))
        rows.sort(key=lambda row: row[2].lower())
        return rows

    def _fill(self, *_):
        ticked = self._ticked_files()
        source = self._source()
        rules = self.keeper.config.rules
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for file_name, title, shown in self._maps:
            r = self.table.rowCount()
            self.table.insertRow(r)
            tick = QTableWidgetItem("")
            tick.setData(Qt.ItemDataRole.UserRole, (file_name, title))
            rule = map_weapons.rule_for(file_name, rules)
            is_source = source is not None and \
                map_weapons.map_key(source.match) == map_weapons.map_key(file_name)
            # the map the weapons come from cannot be ticked
            tick.setFlags(Qt.ItemFlag.ItemIsEnabled if is_source
                          else Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            tick.setCheckState(Qt.CheckState.Checked if file_name in ticked and not is_source
                               else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, tick)
            self.table.setItem(r, 1, QTableWidgetItem(shown))
            if is_source:
                now = "copying from this one"
            elif rule is None:
                now = "no rule: " + map_weapons.NO_RULE_LABELS[self.keeper.config.no_rule].lower()
            elif map_weapons.map_key(rule.match) == map_weapons.map_key(file_name):
                now = "own rule: " + rule.summary()
            else:
                now = f"covered by '{rule.match}': {rule.summary()}"
            self.table.setItem(r, 2, QTableWidgetItem(now))
        self.table.blockSignals(False)
        self._filter()

    def _filter(self, *_):
        needle = self.search.text().strip().lower()
        key = map_weapons.map_key(needle)
        for r in range(self.table.rowCount()):
            text = self.table.item(r, 1).text().lower()
            hide = bool(needle) and needle not in text and key not in map_weapons.map_key(text)
            self.table.setRowHidden(r, hide)
        self._count()

    def _toggle(self, row, column):
        """A click anywhere on the row ticks it (the box itself ticks on its own)."""
        if column != 0:
            item = self.table.item(row, 0)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                                   else Qt.CheckState.Checked)

    def _untick_all(self):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)

    def _ticked(self) -> list:
        return [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]

    def _ticked_files(self) -> set:
        if not hasattr(self, "table"):
            return set()
        return {file_name for file_name, _title in self._ticked()}

    def _count(self):
        if not self._maps:
            self.hint.setText("The map list fills in once WolfRAT has it from the server "
                              "(Missions tab, Refresh Maps from Server).")
            self.copy_btn.setEnabled(False)
            return
        if self._source() is None:
            self.hint.setText("No saved rule to copy yet: play a map, set its weapons, "
                              "click 'Save for this map' first.")
            self.copy_btn.setEnabled(False)
            return
        ticked = len(self._ticked())
        self.copy_btn.setEnabled(ticked > 0)
        self.hint.setText(f"{ticked} map(s) ticked. Each gets its own copy, so you can change one "
                          "later without touching the others.")

    def _copy(self):
        source = self._source()
        targets = self._ticked()
        if source is None or not targets:
            return
        rules = self.keeper.config.rules
        replacing = [title for file_name, title in targets
                     if (rule := map_weapons.rule_for(file_name, rules)) is not None
                     and map_weapons.map_key(rule.match) == map_weapons.map_key(file_name)]
        if replacing:
            shown = "\n".join(replacing[:10]) + ("\n..." if len(replacing) > 10 else "")
            answer = QMessageBox.question(
                self, "Map Weapons",
                f"{len(replacing)} of these maps already have their own weapons. Replace them?\n\n{shown}")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.made += self.keeper.copy_rule(source, targets)
        self.hint.setText(f"Copied '{self._rule_name(source)}' to {len(targets)} map(s).")
        self._untick_all()
        self._fill()
