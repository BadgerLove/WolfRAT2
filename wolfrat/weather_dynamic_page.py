"""The "Dynamic" page of the Weather tab: every widget maps onto DynamicConfig."""

from __future__ import annotations

from typing import Callable, Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QFrame, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from wolfrat import map_timeline as timeline
from wolfrat import weather
from wolfrat import weather_dynamic as dyn

_HELP = (
    "Weather that arrives on its own. Between clear spells a <b>front</b> rolls "
    "in - the sky clouds over, it builds to its peak, holds, then eases off the "
    "same way. Every front is a different strength, and the fronts keep their own "
    "clock: if a storm has five minutes left when the map changes, the next map "
    "opens with five minutes of storm."
    "<br><br>Pressing a button on the Manual page, or a mod's <b>!storm</b>, always "
    "wins - dynamic weather waits until that is over. <b>Clear the weather</b> "
    "ends the current front early."
)

_TIMELINE_HELP = (
    "Pick a map in the list above, then add steps counted back from the end of the map: "
    "<b>20 min left</b> overcast, <b>15</b> drizzle, <b>10</b> fog at 100 m with a grey sky. "
    "Each step stays on until the next one or the end of the map. The server counts whole "
    "minutes, so a step at 10 starts at 10:59.<br>"
    "Works whether the changing weather above is on or off, and wins over it. A mod's "
    "<b>!clear</b> (or Clear the weather) ends this map's weather steps; <b>!sky off</b> "
    "ends its sky steps. Maps without a time limit never get here."
)


def _spin(low, high, value, suffix="", width=78):
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(max(low, min(high, int(value))))
    if suffix:
        spin.setSuffix(suffix)
    spin.setMaximumWidth(width)
    spin.setMinimumHeight(26)
    return spin


def _note(text=""):
    """A wrapped hint that always has room for two lines."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("font-size: 9pt; color: #a89830;")
    label.setMinimumWidth(240)
    label.setMinimumHeight(label.fontMetrics().lineSpacing() * 2 + 6)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    return label


class DynamicPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, config: dyn.DynamicConfig, button_cls: type = QPushButton,
                 map_list: Callable[[], Iterable[tuple[str, str]]] | None = None,
                 timelines: dict | None = None):
        super().__init__()
        self._button_cls = button_cls
        self._map_list = map_list or (lambda: ())
        self._map_rules = dict(config.map_rules)
        # map name -> [TimedStep]; saved by the Weather tab beside the config
        self._timelines = timeline.timelines_from_json(timelines)
        # [(scene key, label)] from the Sky page, set by the Weather tab
        self.sky_choices: Callable[[], list] = lambda: []
        self._timeline_map = ""
        self._seen = {}            # map file -> "terrain, climate" learned as maps get played
        self._loading = True
        self._build(config)
        self._loading = False
        self.refresh_maps()

    # ------------------------------------------------------------------ UI
    def _build(self, config: dyn.DynamicConfig):
        root = QHBoxLayout(self)
        # The left column is taller than a small window, so it scrolls.
        left_host = QWidget()
        left = QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 6, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left_host)
        # ... and so does the right one on a 1024x768 desktop.
        right_host = QWidget()
        right = QVBoxLayout(right_host)
        right.setContentsMargins(0, 0, 6, 0)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_host)
        root.addWidget(scroll, 3)
        root.addWidget(right_scroll, 2)

        # -- help first: the column scrolls, so it costs nothing
        help_group = QGroupBox("How it works")
        help_box = QVBoxLayout()
        help_lbl = QLabel(_HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        help_lbl.setStyleSheet("font-size: 9pt; color: #c8b040;")
        help_lbl.setMinimumWidth(240)
        help_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        help_box.addWidget(help_lbl)
        help_group.setLayout(help_box)
        left.addWidget(help_group)

        # -- master
        main_group = QGroupBox("Dynamic weather")
        main = QVBoxLayout()
        top = QHBoxLayout()
        self.enabled_cb = QCheckBox("Let the weather change on its own")
        self.enabled_cb.setChecked(config.enabled)
        top.addWidget(self.enabled_cb)
        top.addStretch()
        top.addWidget(QLabel("How often:"))
        self.frequency_combo = QComboBox()
        for key, label in dyn.FREQUENCY_LABELS.items():
            self.frequency_combo.addItem(label, key)
        self.frequency_combo.setCurrentIndex(max(0, self.frequency_combo.findData(config.frequency)))
        top.addWidget(self.frequency_combo)
        main.addLayout(top)

        self.status_lbl = QLabel("Dynamic weather is off.")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px 0;")
        self.status_lbl.setMinimumWidth(240)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        main.addWidget(self.status_lbl)

        spans = QGridLayout()
        self.clear_min = _spin(0, 600, config.clear_min, " min")
        self.clear_max = _spin(0, 600, config.clear_max, " min")
        self.hold_min = _spin(1, 240, config.hold_min, " min")
        self.hold_max = _spin(1, 240, config.hold_max, " min")
        self.fade_min = _spin(10, 600, config.fade_min, " s")
        self.fade_max = _spin(10, 600, config.fade_max, " s")
        self._span_label = _note()
        spans.addWidget(QLabel("Clear spells last"), 0, 0)
        spans.addWidget(self.clear_min, 0, 1)
        spans.addWidget(QLabel("to"), 0, 2)
        spans.addWidget(self.clear_max, 0, 3)
        spans.addWidget(QLabel("A front holds its peak"), 1, 0)
        spans.addWidget(self.hold_min, 1, 1)
        spans.addWidget(QLabel("to"), 1, 2)
        spans.addWidget(self.hold_max, 1, 3)
        spans.addWidget(QLabel("Each change fades over"), 2, 0)
        spans.addWidget(self.fade_min, 2, 1)
        spans.addWidget(QLabel("to"), 2, 2)
        spans.addWidget(self.fade_max, 2, 3)
        spans.addWidget(self._span_label, 3, 0, 1, 5)
        spans.setColumnStretch(4, 1)
        main.addLayout(spans)
        main_group.setLayout(main)
        left.addWidget(main_group)

        # -- types
        types_group = QGroupBox("What can roll in  (bigger number = more often)")
        types = QGridLayout()
        self._type_widgets = {}
        for index, name in enumerate(dyn.TYPES):
            weight = int(config.weights.get(name, 0))
            box = QCheckBox(dyn.TYPE_LABELS[name])
            box.setChecked(weight > 0)
            tip = "Builds up through: " + " -> ".join(dyn.TYPE_LABELS[n] for n in dyn.LADDERS[name])
            if name in ("snow", "blizzard"):
                tip += ("\nOn a map that is not a snow map (Auto or Rain) this falls as "
                        + ("rain" if name == "snow" else "a storm") + " instead.")
            elif name in ("drizzle", "rain", "storm"):
                tip += "\nOn a snow map this falls as snow instead."
            box.setToolTip(tip)
            spin = _spin(1, 100, weight or 10, width=64)
            spin.setEnabled(weight > 0)
            box.toggled.connect(spin.setEnabled)
            row, column = divmod(index, 2)
            types.addWidget(box, row, column * 3)
            types.addWidget(spin, row, column * 3 + 1)
            self._type_widgets[name] = (box, spin)
        types.setColumnStretch(2, 1)
        types.setColumnStretch(5, 1)
        self.mix_lbl = _note()
        types.addWidget(self.mix_lbl, 4, 0, 1, 6)
        types_group.setLayout(types)
        left.addWidget(types_group)

        # -- extras
        extras_group = QGroupBox("Fog, wind, earthquakes and chat")
        extras = QGridLayout()
        self.fog_min = _spin(20, 2000, config.fog_min, " m")
        self.fog_max = _spin(20, 2000, config.fog_max, " m")
        extras.addWidget(QLabel("Foggy fronts pick a fog setting from"), 0, 0)
        extras.addWidget(self.fog_min, 0, 1)
        extras.addWidget(QLabel("to"), 0, 2)
        extras.addWidget(self.fog_max, 0, 3)
        fog_note = _note("Players see further than the number: at 50 you can still make things "
                         "out at 150-200 m. Every front rolls its own.")
        extras.addWidget(fog_note, 1, 0, 1, 5)

        self.wind_cb = QCheckBox("Fronts bring wind, from")
        self.wind_cb.setChecked(config.wind)
        self.wind_cb.setToolTip(
            "Wind in Joint Ops is how fast the clouds race across the sky. Every front rolls "
            "its own: it picks up as the front arrives, is strongest at the peak and dies "
            "down with it. Fog comes with still air. Off = every map keeps its own wind."
        )
        self.wind_min = _spin(0, weather.WIND_MAX, config.wind_min)
        self.wind_max = _spin(0, weather.WIND_MAX, config.wind_max)
        extras.addWidget(self.wind_cb, 2, 0)
        extras.addWidget(self.wind_min, 2, 1)
        extras.addWidget(QLabel("to"), 2, 2)
        extras.addWidget(self.wind_max, 2, 3)
        for widget in (self.wind_min, self.wind_max):
            widget.setEnabled(config.wind)
            self.wind_cb.toggled.connect(widget.setEnabled)
        self.wind_lbl = _note()
        extras.addWidget(self.wind_lbl, 3, 0, 1, 5)

        self.quake_cb = QCheckBox("Rare earthquakes, about one every")
        self.quake_cb.setChecked(config.quake_enabled)
        self.quake_cb.setToolTip("They really shake the screen and nudge players and vehicles.")
        self.quake_every = _spin(5, 1440, config.quake_every, " min")
        self.quake_max = _spin(2, 40, config.quake_max_seconds, " s")
        extras.addWidget(self.quake_cb, 4, 0)
        extras.addWidget(self.quake_every, 4, 1)
        extras.addWidget(QLabel("up to"), 4, 2)
        extras.addWidget(self.quake_max, 4, 3)
        for widget in (self.quake_every, self.quake_max):
            widget.setEnabled(config.quake_enabled)
            self.quake_cb.toggled.connect(widget.setEnabled)

        self.announce_cb = QCheckBox("Tell players in chat when a front moves in")
        self.announce_cb.setChecked(config.announce)
        extras.addWidget(self.announce_cb, 5, 0, 1, 4)
        self.empty_cb = QCheckBox("Pause while nobody is on the server")
        self.empty_cb.setChecked(config.pause_when_empty)
        extras.addWidget(self.empty_cb, 6, 0, 1, 4)
        self.lightning_cb = QCheckBox("Lightning and thunder in storms")
        self.lightning_cb.setChecked(config.lightning)
        extras.addWidget(self.lightning_cb, 7, 0, 1, 4)
        self.lightning_lbl = _note()
        extras.addWidget(self.lightning_lbl, 8, 0, 1, 5)
        extras.setColumnStretch(4, 1)
        extras_group.setLayout(extras)
        left.addWidget(extras_group)
        left.addStretch()

        # -- right: maps
        maps_group = QGroupBox("Rain or snow, map by map")
        maps = QVBoxLayout()
        search_row = QHBoxLayout()
        self.map_search = QLineEdit()
        self.map_search.setPlaceholderText("Find a map, or type a name - e.g. cod kill house")
        self.map_search.setClearButtonEnabled(True)
        self.map_search.textChanged.connect(self._fill_maps)
        search_row.addWidget(self.map_search)
        self.only_rules_cb = QCheckBox("only changed")
        self.only_rules_cb.toggled.connect(self._fill_maps)
        search_row.addWidget(self.only_rules_cb)
        maps.addLayout(search_row)

        self.this_map_lbl = _note("Auto: WolfRAT reads each map as it loads and picks rain or snow.")
        maps.addWidget(self.this_map_lbl)

        self.map_table = QTableWidget(0, 4)
        self.map_table.setHorizontalHeaderLabels(["Map", "Seen as", "Weather", "Timed steps"])
        self.map_table.verticalHeader().setVisible(False)
        self.map_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.map_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.map_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.map_table.setMinimumHeight(220)
        self.map_table.itemSelectionChanged.connect(self._map_selected)
        maps.addWidget(self.map_table, 1)

        maps.addWidget(QLabel("Set the selected maps to:"))
        rule_grid = QGridLayout()
        for index, rule in enumerate((dyn.MAP_AUTO, dyn.MAP_NO_SNOW, dyn.MAP_SNOW, dyn.MAP_NONE)):
            button = self._button_cls(dyn.MAP_RULE_LABELS[rule])
            button.clicked.connect(lambda _=False, r=rule: self._set_rule(r))
            rule_grid.addWidget(button, index // 2, index % 2)
        maps.addLayout(rule_grid)
        self.map_hint = _note()
        snow_row = QHBoxLayout()
        snow_row.addWidget(QLabel("Snow terrains:"))
        self.snow_edit = QLineEdit(", ".join(config.snow_terrains))
        self.snow_edit.setToolTip(
            "Auto treats a map as snow when the map itself says Snow, or when its terrain is "
            "in this list (mappers often leave a snow map set to Desert). Comma separated."
        )
        self.snow_edit.editingFinished.connect(self._emit)
        snow_row.addWidget(self.snow_edit)
        maps.addWidget(self.map_hint)
        maps.addLayout(snow_row)
        maps_group.setLayout(maps)
        right.addWidget(maps_group, 1)
        right.addWidget(self._build_timeline())

        # -- wiring
        for box in (self.enabled_cb, self.quake_cb, self.announce_cb, self.empty_cb, self.lightning_cb,
                    self.wind_cb):
            box.toggled.connect(self._emit)
        for spin in (self.clear_min, self.clear_max, self.hold_min, self.hold_max, self.fade_min,
                     self.fade_max, self.fog_min, self.fog_max, self.quake_every, self.quake_max,
                     self.wind_min, self.wind_max):
            spin.valueChanged.connect(self._emit)
        for box, spin in self._type_widgets.values():
            box.toggled.connect(self._emit)
            spin.valueChanged.connect(self._emit)
        self.frequency_combo.currentIndexChanged.connect(self._emit)
        self._refresh_labels()

    def _build_timeline(self) -> QGroupBox:
        group = QGroupBox("Timed steps - counted back from the end of the map")
        box = QVBoxLayout()
        help_lbl = _note(_TIMELINE_HELP)
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(help_lbl)
        self.timeline_map_lbl = QLabel("Pick a map in the list above.")
        self.timeline_map_lbl.setWordWrap(True)
        self.timeline_map_lbl.setStyleSheet("font-weight: bold; color: #c8b040;")
        box.addWidget(self.timeline_map_lbl)

        self.step_table = QTableWidget(0, 3)
        self.step_table.setHorizontalHeaderLabels(["Minutes left", "Weather", "Sky colours"])
        self.step_table.verticalHeader().setVisible(False)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.step_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.step_table.setMinimumHeight(120)
        box.addWidget(self.step_table)

        grid = QGridLayout()
        self.step_minutes = _spin(0, timeline.MAX_MINUTES, 10, " min left", width=110)
        self.step_weather = QComboBox()
        for key in timeline.WEATHER_CHOICES:
            self.step_weather.addItem("Weather: leave it" if not key else timeline.WEATHER_LABELS[key], key)
        self.step_weather.setCurrentIndex(max(0, self.step_weather.findData("fog")))
        self.step_fog = _spin(0, timeline.FOG_MAX_METRES, 0, " m", width=96)
        self.step_fog.setSpecialValueText("normal")
        self.step_fog.setToolTip(
            "How far players can see in this step. 'normal' = the weather's own (fog 150 m, "
            f"storm 350 m...). {timeline.FOG_MIN_METRES} m is the closest; it can never be "
            "further than the map itself allows."
        )
        self.step_sky = QComboBox()
        self.step_sky.setMinimumWidth(140)
        self.refresh_sky_choices()
        grid.addWidget(QLabel("At"), 0, 0)
        grid.addWidget(self.step_minutes, 0, 1)
        grid.addWidget(self.step_weather, 0, 2)
        grid.addWidget(QLabel("fog"), 0, 3)
        grid.addWidget(self.step_fog, 0, 4)
        grid.addWidget(self.step_sky, 1, 2, 1, 3)
        grid.setColumnStretch(2, 1)
        box.addLayout(grid)

        buttons = QHBoxLayout()
        self.step_add_btn = self._button_cls("Add step")
        self.step_add_btn.clicked.connect(self._add_step)
        self.step_remove_btn = self._button_cls("Remove selected")
        self.step_remove_btn.clicked.connect(self._remove_steps)
        self.step_clear_btn = self._button_cls("Remove all for this map")
        self.step_clear_btn.clicked.connect(self._clear_steps)
        for button in (self.step_add_btn, self.step_remove_btn, self.step_clear_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        box.addLayout(buttons)
        self.timeline_hint = _note()
        box.addWidget(self.timeline_hint)
        group.setLayout(box)
        self._show_timeline()
        return group

    # ------------------------------------------------------- timed steps
    def timelines(self) -> dict:
        return {name: list(steps) for name, steps in self._timelines.items() if steps}

    def steps_for(self, map_name) -> list:
        return timeline.steps_for(map_name, self._timelines)

    def refresh_sky_choices(self):
        current = self.step_sky.currentData() if self.step_sky.count() else ""
        self.step_sky.clear()
        self.step_sky.addItem("Sky: leave it", "")
        self.step_sky.addItem("Sky: the map's own", timeline.SKY_OFF)
        try:
            choices = list(self.sky_choices())
        except Exception:
            choices = []
        for key, label in choices:
            self.step_sky.addItem(f"Sky: {label}", key)
        self.step_sky.setCurrentIndex(max(0, self.step_sky.findData(current)))

    def _sky_label(self, key: str) -> str:
        if key == timeline.SKY_OFF:
            return "the map's own"
        try:
            labels = dict(self.sky_choices())
        except Exception:
            labels = {}
        return labels.get(key, f"{key} (not on the Sky page)")

    def _timeline_key(self, name: str) -> str:
        """The name this map's steps are kept under (an older typed name that
        matches it, or the map's own name for a new list)."""
        if self._timelines:
            found = dyn.map_rule_for(name, {k: k for k in self._timelines})
            if found in self._timelines:
                return found
        return name

    def _map_selected(self):
        rows = self.map_table.selectionModel().selectedRows()
        if rows:
            item = self.map_table.item(rows[0].row(), 0)
            if item is not None:
                self._timeline_map = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._show_timeline()

    def _show_timeline(self):
        name = self._timeline_map
        steps = timeline.sort_steps(self._timelines.get(self._timeline_key(name), [])) if name else []
        self.timeline_map_lbl.setText(f"Map: {name}" if name else "Pick a map in the list above.")
        for widget in (self.step_add_btn, self.step_remove_btn, self.step_clear_btn):
            widget.setEnabled(bool(name))
        self.step_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            self.step_table.setItem(row, 0, QTableWidgetItem(f"{step.minutes_left} min"))
            self.step_table.setItem(row, 1, QTableWidgetItem(step.describe_weather() or "-"))
            self.step_table.setItem(row, 2, QTableWidgetItem(
                self._sky_label(step.sky) if step.sky else "-"))
        if name and not steps:
            self.timeline_hint.setText("No steps for this map yet.")
        elif steps:
            self.timeline_hint.setText(f"{len(steps)} step(s). Each one holds until the next, "
                                       "or the end of the map.")
        else:
            self.timeline_hint.setText("")

    def _add_step(self):
        name = self._timeline_map
        if not name:
            return
        kind = self.step_weather.currentData() or ""
        fog = self.step_fog.value()
        step = timeline.TimedStep(
            self.step_minutes.value(), kind,
            timeline.clamp_fog(fog) if fog and kind and kind != "clear" else None,
            self.step_sky.currentData() or "")
        if step.is_empty():
            self.timeline_hint.setText("That step changes nothing: pick a weather, a sky, or both.")
            return
        key = self._timeline_key(name)
        # one step per minute: a new one at the same minute replaces the old
        steps = [s for s in self._timelines.get(key, []) if s.minutes_left != step.minutes_left]
        self._timelines[key] = timeline.sort_steps(steps + [step])
        self._timelines_changed()

    def _remove_steps(self):
        key = self._timeline_key(self._timeline_map)
        steps = timeline.sort_steps(self._timelines.get(key, []))
        gone = {index.row() for index in self.step_table.selectionModel().selectedRows()}
        if not gone:
            self.timeline_hint.setText("Select the step(s) to remove first.")
            return
        keep = [s for row, s in enumerate(steps) if row not in gone]
        if keep:
            self._timelines[key] = keep
        else:
            self._timelines.pop(key, None)
        self._timelines_changed()

    def _clear_steps(self):
        self._timelines.pop(self._timeline_key(self._timeline_map), None)
        self._timelines_changed()

    def _timelines_changed(self):
        self._show_timeline()
        self._fill_maps()
        if not self._loading:
            self.changed.emit()

    def _timeline_summary(self, name: str) -> str:
        steps = self.steps_for(name)
        if not steps:
            return ""
        return ", ".join(str(s.minutes_left) for s in timeline.sort_steps(steps)) + " min"

    # ------------------------------------------------------------ config
    def config(self) -> dyn.DynamicConfig:
        return dyn.DynamicConfig(
            enabled=self.enabled_cb.isChecked(),
            frequency=self.frequency_combo.currentData() or "normal",
            clear_min=self.clear_min.value(), clear_max=self.clear_max.value(),
            hold_min=self.hold_min.value(), hold_max=self.hold_max.value(),
            fade_min=self.fade_min.value(), fade_max=self.fade_max.value(),
            weights={name: (spin.value() if box.isChecked() else 0)
                     for name, (box, spin) in self._type_widgets.items()},
            fog_min=self.fog_min.value(), fog_max=self.fog_max.value(),
            wind=self.wind_cb.isChecked(), wind_min=self.wind_min.value(),
            wind_max=self.wind_max.value(),
            quake_enabled=self.quake_cb.isChecked(), quake_every=self.quake_every.value(),
            quake_max_seconds=self.quake_max.value(), announce=self.announce_cb.isChecked(),
            pause_when_empty=self.empty_cb.isChecked(), lightning=self.lightning_cb.isChecked(),
            map_rules=dict(self._map_rules),
            snow_terrains=[dyn.terrain_key(t) for t in self.snow_edit.text().split(",") if t.strip()],
        )

    def _emit(self, *_):
        if self._loading:
            return
        self._refresh_labels()
        self.changed.emit()

    def _refresh_labels(self):
        config = self.config()
        custom = config.frequency == "custom"
        for spin in (self.clear_min, self.clear_max, self.hold_min, self.hold_max):
            spin.setEnabled(custom)
        if custom:
            self._span_label.setText("")
        else:
            clear_min, clear_max, hold_min, hold_max = config.spans()
            self._span_label.setText(
                f"{dyn.FREQUENCY_LABELS[config.frequency]} = clear spells {clear_min}-{clear_max} min, "
                f"peaks {hold_min}-{hold_max} min. Pick Custom to set your own."
            )
        if config.wind:
            low, high = sorted((config.wind_min, config.wind_max))
            self.wind_lbl.setText(
                f"The peak's wind: {weather.wind_text(low)} to {weather.wind_text(high)}. Maps use "
                "15 to about 200. Players see the clouds speed up; they always drift the same way."
            )
        else:
            self.wind_lbl.setText("Off: every map keeps its own wind.")
        total = sum(config.weights.values())
        if total <= 0:
            self.mix_lbl.setText("Nothing is ticked, so the sky will stay clear.")
        else:
            parts = [f"{dyn.TYPE_LABELS[name].lower()} {round(100 * weight / total)}%"
                     for name, weight in config.weights.items() if weight > 0]
            self.mix_lbl.setText("Out of 100 fronts, roughly: " + ", ".join(parts) + ".")

    def set_status(self, text: str):
        self.status_lbl.setText(text)

    def set_lightning_text(self, text: str):
        self.lightning_lbl.setText(text)

    def set_lightning_status(self, available: bool | None):
        if available is None:
            self.lightning_lbl.setText("")
        elif available:
            self.lightning_lbl.setText("Ready - this server has the lightning add-on.")
        else:
            self.lightning_lbl.setText(
                "Not on this server yet - lightning needs a small server add-on that is "
                "still being finished. Storms run without flashes until then."
            )

    # -------------------------------------------------------------- maps
    def seen(self) -> dict:
        return dict(self._seen)

    def load_seen(self, seen):
        if isinstance(seen, dict):
            self._seen = {str(k): str(v) for k, v in seen.items()}

    def show_current_map(self, map_name, info, snow: bool, rule: str):
        """Explain what Auto made of the map that is loaded right now."""
        verdict = "snow" if snow else "rain"
        if rule == dyn.MAP_AUTO:
            why = f"Auto picked {verdict}"
        elif rule == dyn.MAP_NONE:
            why = "set by hand: no weather"
        else:
            why = f"set by hand: {dyn.MAP_RULE_LABELS[rule].lower()} (Auto would pick {verdict})"
        self.this_map_lbl.setText(f"Now: {info.title or map_name or '?'}  -  {info.describe()}.  {why}.")
        key = dyn.map_key(map_name or "")
        label = f"{info.terrain}, {'snow' if snow else 'not snow'}"
        if key and info.terrain and self._seen.get(key) != label:
            self._seen[key] = label
            self._fill_maps()
            return True
        return False

    def refresh_maps(self):
        self._maps = [(str(f), str(n or "")) for f, n in self._map_list() if f]
        self._fill_maps()

    def _fill_maps(self, *_):
        needle = self.map_search.text().strip()
        key = dyn.map_key(needle)
        only = self.only_rules_cb.isChecked()
        real_keys = {dyn.map_key(f) for f, _ in self._maps}
        rows = []
        typed_names = sorted(set(self._map_rules) | set(self._timelines))
        for typed in typed_names:
            if dyn.map_key(typed) not in real_keys:
                rule = dyn.map_rule_for(typed, self._map_rules)
                rows.append((typed, f"{typed}   (typed name)", rule))
        for file_name, title in self._maps:
            rule = dyn.map_rule_for(file_name, self._map_rules)
            label = file_name if not title or title == file_name else f"{file_name}   -   {title}"
            rows.append((file_name, label, rule))
        shown = []
        for name, label, rule in rows:
            if only and rule == dyn.MAP_NORMAL and not self.steps_for(name):
                continue
            if key and key not in dyn.map_key(label) and dyn.map_rule_for(name, {needle: "x"}) != "x":
                continue
            shown.append((name, label, rule))

        picked = self._timeline_map
        self.map_table.blockSignals(True)
        self.map_table.setRowCount(len(shown))
        for row, (name, label, rule) in enumerate(shown):
            item = QTableWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.map_table.setItem(row, 0, item)
            self.map_table.setItem(row, 1, QTableWidgetItem(self._seen.get(dyn.map_key(name), "")))
            self.map_table.setItem(row, 2, QTableWidgetItem(dyn.MAP_RULE_LABELS[rule]))
            self.map_table.setItem(row, 3, QTableWidgetItem(self._timeline_summary(name)))
            if picked and name == picked:
                self.map_table.selectRow(row)       # a refill keeps the map being edited
        self.map_table.blockSignals(False)
        changed = sum(1 for _ in self._map_rules)
        if needle and not shown:
            self.map_hint.setText(
                f"No map matches yet. Press a button to keep a rule for \"{needle}\" - it will "
                "match the map by name when it comes up."
            )
        elif not self._maps:
            self.map_hint.setText(
                "The map list fills in once WolfRAT has fetched it from the server (Missions tab). "
                "Until then, type a map name and press a button."
            )
        else:
            self.map_hint.setText(f"{changed} map(s) set by hand. Everything else is on Auto.")

    def _set_rule(self, rule: str):
        names = [self.map_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                 for index in self.map_table.selectionModel().selectedRows()]
        typed = self.map_search.text().strip()
        if not names and typed and self.map_table.rowCount() == 0:
            names = [typed]
        if not names:
            self.map_hint.setText("Select one or more maps first (or type a name that is not in the list).")
            return
        for name in names:
            # drop any older rule that was steering this map, then set the new one
            for existing in [k for k in self._map_rules
                             if dyn.map_rule_for(name, {k: "x"}) == "x"]:
                del self._map_rules[existing]
            if rule != dyn.MAP_NORMAL:
                self._map_rules[name] = rule
        self._fill_maps()
        self._emit()
