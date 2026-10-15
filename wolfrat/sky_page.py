"""The "Sky" page of the Weather tab (Dale, 2026-10-09): scenes, a make-your-own
editor, map-by-map skies, a demo and the !sky chat command.  The work is in
``sky``; this page draws it and sends the commands.

Works on the Weather tab's server handle: the Weather tab calls
``server_tick`` every poll (5 s) with the map, the players and whether the
server has settled, and ``no_server`` when there is none.  The page's own
half-second timer sends one command at a time and runs the demo.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from wolfrat import sky
from wolfrat import weather_dynamic as dyn
from wolfrat.weather_wildlife_page import scroll_column

BANNER = (
    "<b>🎨 SKY COLOURS - TAC mod / client patch required.</b> Players see the colours only "
    "with the sky-colour patch in their game; everyone else sees the map's normal sky (it does "
    "them no harm). The server needs the sky-colour exe and WolfRAT's lines in server.wac "
    "(the button below)."
)

NO_SERVER = "The Sky page needs WolfRAT on the same PC as the game server, connected to it."

HELP = (
    "<b>Scenes</b> change fog, horizon, sunlight, sky light, ground light, lightning and the "
    "light inside buildings together, with a fade. <b>Keep it for</b> 0 = until the map "
    "changes.<br><br>"
    "<b>Make your own:</b> tick what to change and pick its colour. Unticked = the map's own "
    "colour. <b>Try it</b> shows it on the server; <b>Save as a scene</b> keeps it with a "
    "name, and mods can then use that name in chat too.<br><br>"
    "<b>Indoors: ceiling / floor</b> light the inside of buildings (stock maps keep them a "
    "dim grey). <b>Lightning colour</b> only shows when the scene is dark. WolfRAT remembers "
    "the map's own lightning and indoor colours and puts them back.<br><br>"
    "<b>Good to know</b><br>"
    "• A map change puts the map's own sky back. A map rule (right) then puts its scene on.<br>"
    "• Players who join later get the sky sent again (when someone joins, and every 30 s).<br>"
    "• Back to the map's own sky is instant (the game cannot fade that way)."
)

COMMANDS_HELP = (
    "!sky toxic &nbsp; - until the map changes<br>"
    "!sky toxic 10 &nbsp; - for 10 minutes<br>"
    "!sky off &nbsp; - the map's own sky<br>"
    "!sky demo &nbsp; - every sky, 8 s each<br>"
    "!sky list &nbsp; - the names in chat<br>"
    "!lightning red &nbsp; - a red flash (yellow, green, blue, purple, pink, orange, "
    "cyan, white, blood or #ff00ff), then the colour goes back. Add far for a distant one"
)


def _note(text=""):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setMinimumWidth(120)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setStyleSheet("font-size: 9pt; color: #a89830;")
    return label


def _swatch_style(rgb) -> str:
    r, g, b = rgb
    text = "#000000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#ffffff"
    return (f"background: rgb({r},{g},{b}); color: {text}; border: 1px solid #806020; "
            "border-radius: 3px; padding: 3px 8px;")


class SkyPage(QWidget):
    """``log(text)``: the Weather tab's log.  ``send_chat(text)``: public chat.
    ``flash()``: lightning from the weather add-on, False when it cannot.
    ``poll_now()``: ask the Weather tab to look at the server straight away.
    ``time_left()``: whole minutes left on the map, None when unknown."""

    PUMP_MS = 500

    def __init__(self, settings_file: Path, *, log: Callable[[str], None] = lambda _t: None,
                 send_chat: Optional[Callable[[str], object]] = None,
                 flash: Optional[Callable[[], bool]] = None,
                 poll_now: Optional[Callable[[], None]] = None,
                 map_list: Optional[Callable[[], list]] = None,
                 server_dir: Optional[Callable[[], str]] = None,
                 button_cls: type = QPushButton,
                 clock: Callable[[], float] = time.monotonic, parent=None):
        super().__init__(parent)
        self._file = Path(settings_file)
        self._log = log
        self._send_chat = send_chat
        self._flash = flash
        self._poll_now = poll_now
        self._map_list = map_list
        self._server_dir = server_dir or (lambda: "")
        self._button_cls = button_cls
        self._clock = clock
        self.time_left: Callable[[], Optional[int]] = lambda: None
        self.keeper = sky.SkyKeeper(clock=clock)
        self.sender = sky.Sender(clock=clock)

        settings = self._load()
        self._own: dict = {}
        for key, data in (settings.get("own") or {}).items():
            if isinstance(data, dict) and sky.scene_key(key):
                self._own[sky.scene_key(key)] = sky.Scene(
                    sky.scene_key(key), str(data.get("label") or key)[:24],
                    sky.Look.from_json(data), builtin=False)
        self._map_rules = {str(k): sky.scene_key(v) for k, v in (settings.get("maps") or {}).items()
                           if isinstance(v, str)}
        self._maps: list = []

        # what the server can do, refreshed by server_tick
        self._memory = None
        self._writable = False
        self._capable = False
        self._installed = False
        self._current = False              # the block in server.wac is this WolfRAT's
        self._old_block = False
        self._script = "unknown"           # unknown | asked | yes | no
        self._ping_sent_at: Optional[float] = None
        self._ping_map: Optional[str] = None
        self._context = (None, 0, False)  # map, players, settled
        self._strike = None                # a coloured lightning strike in progress

        self._build(settings)
        self.no_server(NO_SERVER)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pump)
        self._timer.start(self.PUMP_MS)

    # ---------------------------------------------------------------- scenes
    def scenes(self) -> list:
        return list(sky.BUILTIN_SCENES) + sorted(self._own.values(), key=lambda s: s.label.lower())

    def scene(self, key: str) -> Optional[sky.Scene]:
        key = sky.scene_key(key)
        for scene in self.scenes():
            if scene.key == key:
                return scene
        return None

    # -------------------------------------------------------------------- UI
    def _build(self, settings: dict):
        root = QHBoxLayout(self)
        left_scroll, left = scroll_column()
        right_scroll, right = scroll_column()
        root.addWidget(left_scroll, 3)
        root.addWidget(right_scroll, 2)

        banner = QLabel(BANNER)
        banner.setWordWrap(True)
        banner.setTextFormat(Qt.TextFormat.RichText)
        banner.setMinimumWidth(200)
        banner.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        banner.setStyleSheet(
            "font-size: 10pt; color: #ffd700; background: #1a1430; border: 2px solid #8060c0; "
            "border-radius: 4px; padding: 8px;")
        left.addWidget(banner)

        self.server_lbl = QLabel()
        self.server_lbl.setWordWrap(True)
        self.server_lbl.setMinimumWidth(200)
        self.server_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.server_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px;")
        left.addWidget(self.server_lbl)
        self.install_btn = self._button_cls("Install the sky lines in server.wac")
        self.install_btn.clicked.connect(self._install)
        self.install_btn.setVisible(False)
        left.addWidget(self.install_btn)

        self.now_lbl = QLabel("Now: the map's own sky.")
        self.now_lbl.setWordWrap(True)
        self.now_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.now_lbl.setStyleSheet("font-size: 10pt; color: #e0d060; padding: 2px 4px;")
        left.addWidget(self.now_lbl)

        # -- scenes
        scenes_group = QGroupBox("Scenes")
        scenes_box = QVBoxLayout()
        self.scene_grid = QGridLayout()
        scenes_box.addLayout(self.scene_grid)
        row = QHBoxLayout()
        row.addWidget(QLabel("Keep it for"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, sky.MAX_MINUTES)
        self.minutes_spin.setSuffix(" min")
        self.minutes_spin.setSpecialValueText("until the map changes")
        self.minutes_spin.setValue(int(settings.get("minutes", 0) or 0))
        row.addWidget(self.minutes_spin, 1)
        scenes_box.addLayout(row)
        row = QHBoxLayout()
        self.off_btn = self._button_cls("☀️ Map's own sky")
        self.off_btn.clicked.connect(self._off_clicked)
        row.addWidget(self.off_btn)
        self.demo_btn = self._button_cls("▶ Run the demo")
        self.demo_btn.clicked.connect(self._demo_clicked)
        row.addWidget(self.demo_btn)
        scenes_box.addLayout(row)
        scenes_group.setLayout(scenes_box)
        left.addWidget(scenes_group)

        # -- make your own
        own_group = QGroupBox("Make your own")
        own = QGridLayout()
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start from"))
        self.start_combo = QComboBox()
        start_row.addWidget(self.start_combo, 1)
        load_btn = self._button_cls("Load")
        load_btn.clicked.connect(self._load_into_editor)
        start_row.addWidget(load_btn)
        own.addLayout(start_row, 0, 0, 1, 4)
        self._use: dict = {}
        self._swatch: dict = {}
        self._hex: dict = {}
        self._colour: dict = {}
        editor = sky.Look.from_json(settings.get("editor"))
        for index, element in enumerate(sky.ELEMENTS, start=1):
            use = QCheckBox(element.label)
            use.setToolTip(element.help)
            swatch = QPushButton("pick")
            swatch.setToolTip(element.help)
            swatch.clicked.connect(lambda _=False, k=element.key: self._pick(k))
            hex_edit = QLineEdit()
            hex_edit.setMaximumWidth(80)
            hex_edit.setPlaceholderText("#rrggbb")
            hex_edit.editingFinished.connect(lambda k=element.key: self._hex_typed(k))
            own.addWidget(use, index, 0)
            own.addWidget(swatch, index, 1)
            own.addWidget(hex_edit, index, 2)
            self._use[element.key], self._swatch[element.key], self._hex[element.key] = use, swatch, hex_edit
            rgb = editor.colours.get(element.key)
            self._colour[element.key] = rgb or (128, 128, 128)
            use.setChecked(rgb is not None)
            use.toggled.connect(self._editor_changed)
            self._show_colour(element.key)
        n = len(sky.ELEMENTS) + 1
        fade_row = QHBoxLayout()
        fade_row.addWidget(QLabel("Fade"))
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(0, 60)
        self.fade_spin.setSuffix(" s")
        self.fade_spin.setValue(editor.fade)
        self.fade_spin.valueChanged.connect(self._editor_changed)
        fade_row.addWidget(self.fade_spin)
        fade_row.addStretch()
        self.try_btn = self._button_cls("👁 Try it on the server")
        self.try_btn.clicked.connect(self._try_clicked)
        fade_row.addWidget(self.try_btn)
        own.addLayout(fade_row, n, 0, 1, 4)
        save_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name, e.g. Swamp Gas")
        self.name_edit.setMaxLength(24)
        save_row.addWidget(self.name_edit, 1)
        save_btn = self._button_cls("💾 Save as a scene")
        save_btn.clicked.connect(self._save_scene)
        save_row.addWidget(save_btn)
        self.delete_btn = self._button_cls("🗑 Delete")
        self.delete_btn.setToolTip("Delete the scene named on the left (your own scenes only).")
        self.delete_btn.clicked.connect(self._delete_scene)
        save_row.addWidget(self.delete_btn)
        own.addLayout(save_row, n + 1, 0, 1, 4)
        self.editor_lbl = _note()
        own.addWidget(self.editor_lbl, n + 2, 0, 1, 4)
        own.setColumnStretch(1, 1)
        own_group.setLayout(own)
        left.addWidget(own_group)

        # -- options
        options_group = QGroupBox("Chat and timing")
        options = QVBoxLayout()
        self.mods_cb = QCheckBox("Let mods use !sky from chat (their rank needs 'Sky' ticked)")
        self.mods_cb.setChecked(bool(settings.get("mods", False)))
        self.announce_cb = QCheckBox("Tell players in chat when the sky changes")
        self.announce_cb.setChecked(bool(settings.get("announce", True)))
        self.last_minute_cb = QCheckBox("Give the map its own sky back with 1 minute left "
                                        "(maps with a timer)")
        self.last_minute_cb.setChecked(bool(settings.get("last_minute", False)))
        for box in (self.mods_cb, self.announce_cb, self.last_minute_cb):
            box.toggled.connect(self._save)
            options.addWidget(box)
        options_group.setLayout(options)
        left.addWidget(options_group)
        left.addStretch()

        # -- right: maps
        maps_group = QGroupBox("Map by map: this map always gets this sky")
        maps = QVBoxLayout()
        self.map_search = QLineEdit()
        self.map_search.setPlaceholderText("Find a map, or type a name")
        self.map_search.setClearButtonEnabled(True)
        self.map_search.textChanged.connect(self._fill_maps)
        maps.addWidget(self.map_search)
        self.map_table = QTableWidget(0, 2)
        self.map_table.setHorizontalHeaderLabels(["Map", "Sky"])
        self.map_table.verticalHeader().setVisible(False)
        self.map_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.map_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.map_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.map_table.setMinimumHeight(200)
        maps.addWidget(self.map_table, 1)
        rule_row = QHBoxLayout()
        self.rule_combo = QComboBox()
        rule_row.addWidget(self.rule_combo, 1)
        set_btn = self._button_cls("Give it this sky")
        set_btn.clicked.connect(self._set_rule)
        rule_row.addWidget(set_btn)
        clear_btn = self._button_cls("Normal sky")
        clear_btn.clicked.connect(lambda: self._set_rule(clear=True))
        rule_row.addWidget(clear_btn)
        maps.addLayout(rule_row)
        self.map_hint = _note()
        maps.addWidget(self.map_hint)
        maps_group.setLayout(maps)
        right.addWidget(maps_group, 1)

        cmds_group = QGroupBox("In-game commands")
        cmds_box = QVBoxLayout()
        cmds = QLabel(COMMANDS_HELP)
        cmds.setTextFormat(Qt.TextFormat.RichText)
        cmds.setWordWrap(True)
        cmds.setStyleSheet("font-size: 9pt; color: #c8b040;")
        cmds.setMinimumWidth(200)
        cmds.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cmds_box.addWidget(cmds)
        cmds_group.setLayout(cmds_box)
        right.addWidget(cmds_group)

        help_group = QGroupBox("How it works")
        help_box = QVBoxLayout()
        help_lbl = QLabel(HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        help_lbl.setStyleSheet("font-size: 9pt; color: #c8b040;")
        help_lbl.setMinimumWidth(240)
        help_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        help_box.addWidget(help_lbl)
        help_group.setLayout(help_box)
        right.addWidget(help_group)
        right.addStretch()

        self._fill_scene_lists()
        self._fill_maps()

    def _fill_scene_lists(self):
        while self.scene_grid.count():
            item = self.scene_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.scene_buttons = {}
        for index, scene in enumerate(self.scenes()):
            button = self._button_cls(scene.label if scene.builtin else f"★ {scene.label}")
            button.setToolTip(f"!sky {scene.key}")
            button.clicked.connect(lambda _=False, k=scene.key: self._scene_clicked(k))
            self.scene_grid.addWidget(button, index // 3, index % 3)
            self.scene_buttons[scene.key] = button
        for combo in (self.start_combo, self.rule_combo):
            keep = combo.currentData()
            combo.clear()
            for scene in self.scenes():
                combo.addItem(scene.label + ("" if scene.builtin else "  (yours)"), scene.key)
            if keep is not None and combo.findData(keep) >= 0:
                combo.setCurrentIndex(combo.findData(keep))

    # ---------------------------------------------------------- the editor
    def _show_colour(self, key):
        rgb = self._colour[key]
        on = self._use[key].isChecked()
        self._swatch[key].setStyleSheet(_swatch_style(rgb) if on else "")
        self._swatch[key].setText(sky.to_hex(rgb) if on else "map's own")
        self._hex[key].setText(sky.to_hex(rgb))

    def set_colour(self, key: str, rgb) -> None:
        self._colour[key] = tuple(int(c) for c in rgb)
        self._use[key].setChecked(True)
        self._show_colour(key)
        self._editor_changed()

    def _pick(self, key):
        r, g, b = self._colour[key]
        label = next(e.label for e in sky.ELEMENTS if e.key == key)
        chosen = QColorDialog.getColor(QColor(r, g, b), self, f"{label} colour")
        if chosen.isValid():
            self.set_colour(key, (chosen.red(), chosen.green(), chosen.blue()))

    def _hex_typed(self, key):
        rgb = sky.parse_colour(self._hex[key].text())
        if rgb is None:
            self._show_colour(key)
            self.editor_lbl.setText("Colours are typed as #rrggbb, e.g. #ff2200.")
            return
        self.set_colour(key, rgb)

    def editor_look(self) -> sky.Look:
        return sky.Look({k: self._colour[k] for k in sky.ELEMENT_KEYS if self._use[k].isChecked()},
                        self.fade_spin.value())

    def _editor_changed(self, *_):
        for key in sky.ELEMENT_KEYS:
            self._show_colour(key)
        self._save()

    def _load_into_editor(self):
        scene = self.scene(self.start_combo.currentData() or "")
        if scene is None:
            return
        for key in sky.ELEMENT_KEYS:
            rgb = scene.look.colours.get(key)
            self._use[key].blockSignals(True)
            self._use[key].setChecked(rgb is not None)
            self._use[key].blockSignals(False)
            if rgb is not None:
                self._colour[key] = rgb
        self.fade_spin.setValue(scene.look.fade)
        self.name_edit.setText("" if scene.builtin else scene.label)
        self._editor_changed()
        self.editor_lbl.setText(f"Loaded {scene.label}. Change it, try it, and save it under "
                                "a new name.")

    def _save_scene(self):
        name = self.name_edit.text().strip()
        key = sky.scene_key(name)
        if not key:
            self.editor_lbl.setText("Give your scene a name first.")
            return
        if key in sky.RESERVED_WORDS or any(s.key == key for s in sky.BUILTIN_SCENES):
            self.editor_lbl.setText(f"'{name}' is taken by a built-in scene or a command word - "
                                    "pick another name.")
            return
        look = self.editor_look()
        if look.is_empty():
            self.editor_lbl.setText("Tick at least one colour first.")
            return
        replaced = key in self._own
        self._own[key] = sky.Scene(key, name, look, builtin=False)
        self._fill_scene_lists()
        self._fill_maps()
        self._save()
        self.editor_lbl.setText(f"{'Updated' if replaced else 'Saved'} {name}. Mods can type "
                                f"!sky {key}")
        self._log(f"Sky: scene {name} {'updated' if replaced else 'saved'}.")

    def _delete_scene(self):
        key = sky.scene_key(self.name_edit.text())
        if key not in self._own:
            self.editor_lbl.setText("Type the name of one of your own scenes to delete it "
                                    "(built-in scenes stay).")
            return
        label = self._own.pop(key).label
        self._map_rules = {m: s for m, s in self._map_rules.items() if s != key}
        self._fill_scene_lists()
        self._fill_maps()
        self._save()
        self.editor_lbl.setText(f"Deleted {label}.")

    # -------------------------------------------------------------- clicks
    def _ready_text(self) -> Optional[str]:
        """Why nothing can be sent right now, or None."""
        if self._memory is None:
            return NO_SERVER
        if not self._capable:
            return "This server's exe cannot send sky colours (it needs the sky-colour patch)."
        if not self._installed:
            return "Install the sky lines in server.wac first (button above)."
        return None

    def _do(self, step: sky.Step, who: str = "WolfRAT"):
        if step.command is not None:
            self.sender.push(step.command)
        if step.log:
            self._log(step.log)
        if step.announce and self.announce_cb.isChecked() and self._send_chat:
            try:
                self._send_chat(step.announce)
            except Exception:
                pass
        if step.flash and self._flash:
            try:
                self._flash(False)
            except Exception:
                pass
        self._show_now()
        if self._poll_now:
            self._poll_now()

    def _scene_clicked(self, key):
        scene = self.scene(key)
        problem = self._ready_text()
        if problem:
            self.now_lbl.setText("⚠️ " + problem)
            return
        if scene is not None:
            self._do(self.keeper.show(scene, "page", self.minutes_spin.value() or None, "WolfRAT"))
            self._save()

    def _off_clicked(self):
        self._do(self.keeper.off("WolfRAT"))

    def _demo_clicked(self):
        if self.keeper.demo_running():
            self._do(self.keeper.stop_demo())
            return
        problem = self._ready_text()
        if problem:
            self.now_lbl.setText("⚠️ " + problem)
            return
        self._do(self.keeper.start_demo(self.scenes(), "WolfRAT"))

    def _try_clicked(self):
        problem = self._ready_text()
        if problem:
            self.editor_lbl.setText("⚠️ " + problem)
            return
        look = self.editor_look()
        if look.is_empty():
            self.editor_lbl.setText("Tick at least one colour first.")
            return
        name = self.name_edit.text().strip() or "Custom sky"
        self._do(self.keeper.preview(look, name))
        self.editor_lbl.setText("On the server until the map changes (or ☀️ Map's own sky).")

    def _install(self):
        folder = self._server_dir()
        if not folder:
            self.server_lbl.setText("⚠️ WolfRAT does not know where the server's folder is yet.")
            return
        try:
            result = sky.install_block(folder)
        except sky.SkyError as exc:
            self.server_lbl.setText("⚠️ " + str(exc))
            return
        self._installed = True
        self._current = True
        self._script = "unknown"
        self._log(f"Sky: server.wac {result} ({os.path.join(folder, sky.FILENAME)}). "
                  "It starts working at the next map load.")
        self._show_server()

    # ------------------------------------------------------------- map rules
    def refresh_maps(self):
        if self._map_list is None:
            return
        try:
            self._maps = [(str(f), str(n or "")) for f, n in self._map_list() if f]
        except Exception:
            self._maps = []
        self._fill_maps()

    def map_scene(self, map_name: Optional[str]) -> Optional[sky.Scene]:
        if not map_name or not self._map_rules:
            return None
        rule = dyn.map_rule_for(map_name, self._map_rules)
        return self.scene(rule) if rule in self._map_rules.values() else None

    def _fill_maps(self, *_):
        needle = self.map_search.text().strip()
        key = dyn.map_key(needle)
        real = {dyn.map_key(f) for f, _ in self._maps}
        rows = [(typed, f"{typed}   (typed name)") for typed in sorted(self._map_rules)
                if dyn.map_key(typed) not in real]
        rows += [(f, f if not t or t == f else f"{f}   -   {t}") for f, t in self._maps]
        shown = []
        for name, label in rows:
            if key and key not in dyn.map_key(label) and dyn.map_rule_for(name, {needle: "x"}) != "x":
                continue
            scene = self.map_scene(name)
            shown.append((name, label, scene.label if scene else ""))
        shown.sort(key=lambda r: (r[2] == "", r[1].lower()))
        self.map_table.setRowCount(len(shown))
        for row, (name, label, sky_label) in enumerate(shown):
            item = QTableWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.map_table.setItem(row, 0, item)
            self.map_table.setItem(row, 1, QTableWidgetItem(sky_label or "normal"))
        if needle and not shown:
            self.map_hint.setText(f"No map matches. Press a button to keep a rule for \"{needle}\".")
        elif not self._maps:
            self.map_hint.setText("The map list fills in once WolfRAT has it from the server. "
                                  "Until then, type a map name and press a button.")
        else:
            self.map_hint.setText(f"{len(self._map_rules)} map(s) with their own sky. A mod's "
                                  "!sky beats the map's sky; !sky off keeps it off for that map.")

    def _set_rule(self, clear: bool = False):
        names = [self.map_table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
                 for i in self.map_table.selectionModel().selectedRows()]
        typed = self.map_search.text().strip()
        if not names and typed and self.map_table.rowCount() == 0:
            names = [typed]
        if not names:
            self.map_hint.setText("Select one or more maps first (or type a name that is not in "
                                  "the list).")
            return
        key = self.rule_combo.currentData()
        for name in names:
            for existing in [k for k in self._map_rules if dyn.map_rule_for(name, {k: "x"}) == "x"]:
                del self._map_rules[existing]
            if not clear and key:
                self._map_rules[name] = key
        self._fill_maps()
        self._save()

    # ----------------------------------------------------------------- chat
    def on_chat(self, sender: str, args: list) -> Optional[str]:
        """``!sky ...`` from an authorised mod.  Returns a reply for the mod, or
        None when the public announcement says it already."""
        if not self.mods_cb.isChecked():
            return "Sky commands are switched off in WolfRAT."
        request = sky.parse_chat(args, self.scenes())
        if request.kind == "usage":
            return request.message
        if request.kind == "list":
            for line in sky.chat_lines("Skies:", [s.key for s in self.scenes()]):
                if self._send_chat:
                    self._send_chat(line)
            return None
        if request.kind == "off":
            self._do(self.keeper.off(sender))
            return None if self.announce_cb.isChecked() else "Sky back to normal."
        problem = self.chat_problem()
        if problem:
            return problem
        if request.kind == "demo":
            self._do(self.keeper.start_demo(self.scenes(), sender))
            return None if self.announce_cb.isChecked() else "Sky demo started."
        self._do(self.keeper.show(request.scene, "mod", request.minutes, sender))
        return None if self.announce_cb.isChecked() else f"Sky: {request.scene.label}."

    def chat_problem(self) -> Optional[str]:
        """Why a sky command cannot work, short enough for chat (59), or None."""
        if self._memory is None:
            return "Sky: WolfRAT is not running on the server PC."
        if not self._capable:
            return "Sky: this server exe has no sky-colour patch."
        if not self._installed:
            return "Sky: press Install on WolfRAT's Sky page first."
        if self._script == "no":
            return "Sky: ready after the next map change."
        return None

    def coloured_strike(self, sender: str, rgb, far: bool = False) -> Optional[str]:
        """``!lightning red``: colour, strike, colour back (Dale's spec 2026-10-05:
        "check lightning's white, request is red, slap red down, return to white")."""
        problem = self.chat_problem()
        if problem:
            return problem
        if self._strike is not None:
            return "Lightning: wait for the last strike to finish."
        memory = self._memory
        try:
            home = self.keeper.home_lightning(memory)
        except Exception:
            return "Sky: could not read the server."
        active = self.keeper.active
        showing = active.scene.look.colours.get("lightning") if active else None
        back = tuple(showing) if showing is not None else home
        command = sky.lightning_command(rgb, label="strike")
        self.sender.push(command, replace_looks=False)
        self._strike = {"stage": "colour", "command": command, "back": back, "far": far,
                        "map": self.keeper.map_name, "at": None}
        self._log(f"{sender} called down {sky.to_hex(rgb)} lightning"
                  f"{' (distant)' if far else ''}.")
        if self._poll_now:
            self._poll_now()
        return None

    def _strike_tick(self, memory):
        strike = self._strike
        if strike is None:
            return
        if strike["map"] != self.keeper.map_name:
            self._strike = None             # the map load put the lightning back by itself
            return
        now = self._clock()
        if strike["stage"] == "colour":
            if self.sender.last_sent is strike["command"] and self.sender.answered(memory):
                fired = False
                if self._flash:
                    try:
                        fired = bool(self._flash(strike["far"]))
                    except Exception:
                        fired = False
                strike["stage"], strike["at"] = "back", now + (sky.STRIKE_HOLD if fired else 0)
        elif strike["stage"] == "back" and now >= strike["at"]:
            self.sender.push(sky.lightning_command(strike["back"], label="strike back"),
                             replace_looks=False)
            self._strike = None

    # --------------------------------------------- called by the Weather tab
    def wants_write(self) -> bool:
        return (self.sender.busy() or self.keeper.active is not None or self.keeper.demo_running()
                or bool(self._map_rules) or self._strike is not None)

    def no_server(self, text: str):
        self._memory = None
        self._capable = False
        self.server_lbl.setText("⚪ " + text)
        self.install_btn.setVisible(False)

    def server_tick(self, memory, writable: bool, map_name: Optional[str] = None,
                    players: int = 0, settled: bool = False):
        if memory is None:
            self.no_server(NO_SERVER)
            return
        if memory is not self._memory:
            self._script = "unknown"
        self._memory, self._writable = memory, writable
        self._context = (map_name, int(players or 0), bool(settled))
        self._capable = sky.server_can_do_sky(memory)
        folder = self._server_dir()
        self._installed = bool(folder) and sky.block_installed(folder)
        self._current = self._installed and sky.block_current(folder)
        self._old_block = bool(folder) and sky.old_test_block(folder)
        if self._ping_map != map_name:
            self._ping_map = map_name
            if self._script != "yes":
                self._script = "unknown"        # a new map compiled server.wac again
        self._show_server()

    def _show_server(self):
        pid = getattr(self._memory, "pid", "?")
        if self._memory is None:
            return
        if not self._capable:
            text = (f"⚠️ Game server found (process {pid}), but its exe cannot send sky colours. "
                    "It needs the sky-colour patch.")
        elif not self._installed:
            text = ("🟡 The server exe can do sky colours. WolfRAT's lines are not in server.wac "
                    "yet" + (" (an old sky TEST block is - Install replaces it)" if self._old_block
                             else "") + ".")
        elif not self._current:
            text = ("🟡 The sky lines in server.wac are from an older WolfRAT: indoor colours "
                    "(ceiling / floor) will not show. Press Update - it starts working at the "
                    "next map load.")
        elif self._script == "yes":
            text = f"🟢 Sky colours ready (server process {pid})."
        elif self._script == "no":
            text = ("🟡 The lines are in server.wac but the server has not loaded them yet: they "
                    "start working at the next map load.")
        else:
            text = "🟡 Checking the server's script... (it only runs while someone is on the server)"
        self.server_lbl.setText(text)
        self.install_btn.setText("Update the sky lines in server.wac" if self._installed
                                 else "Install the sky lines in server.wac")
        self.install_btn.setVisible(self._capable and not (self._installed and self._current))
        self._show_now()

    def _show_now(self):
        self.now_lbl.setText("Now: " + self.keeper.describe())
        self.demo_btn.setText("■ Stop the demo" if self.keeper.demo_running() else "▶ Run the demo")

    # ------------------------------------------------------------ the pump
    def _pump(self):
        """Every half second: run the keeper and send one command if the script is free."""
        memory = self._memory
        map_name, players, settled = self._context
        try:
            minutes_left = self.time_left()
        except Exception:
            minutes_left = None
        steps = self.keeper.tick(
            map_name=map_name, players=players, settled=settled and memory is not None,
            map_scene=self.map_scene(map_name), minutes_left=minutes_left,
            end_last_minute=self.last_minute_cb.isChecked())
        for step in steps:
            self._do_quiet(step)
        if memory is None or not self._writable or not self._capable or not self._installed:
            return
        if not settled:
            return
        try:
            self._script_check(memory, players)
            sent = self.sender.pump(memory, lambda key: self.keeper.home_colour(memory, key),
                                    only_ping=self._script != "yes")
        except Exception as exc:
            self.server_lbl.setText(f"⚠️ Sky: {exc}")
            return
        if sent is not None and sent.code == sky.CMD_PING:
            self._script, self._ping_sent_at = "asked", self._clock()
        try:
            self._strike_tick(memory)
        except Exception as exc:
            self._strike = None
            self._log(f"Sky: lightning strike stopped ({exc}).")
        self._show_now()

    def _do_quiet(self, step: sky.Step):
        """Keeper steps from the tick (no poll_now: we are inside one)."""
        if step.command is not None:
            self.sender.push(step.command)
        if step.log:
            self._log(step.log)
        if step.announce and self.announce_cb.isChecked() and self._send_chat:
            try:
                self._send_chat(step.announce)
            except Exception:
                pass
        if step.flash and self._flash:
            try:
                self._flash(False)
            except Exception:
                pass

    def _script_check(self, memory, players: int):
        slot = self.sender.slot(memory)
        if self._script == "asked":
            if slot == sky.CMD_PONG:
                self._script = "yes"
                self._show_server()
            elif players > 0 and self._ping_sent_at is not None \
                    and self._clock() - self._ping_sent_at > sky.PING_TIMEOUT:
                self.sender.take_back(memory, sky.CMD_PING)
                self._script = "no"
                self._show_server()
        elif self._script == "unknown" and not any(c.code == sky.CMD_PING
                                                   for c in self.sender.queued()):
            self.sender.push(sky.Command(sky.CMD_PING, label="ping"), replace_looks=False)
        elif self._script == "yes" and slot not in sky.CMD_FREE and self.sender.waiting_since \
                and players > 0 and self._clock() - self.sender.waiting_since > 10:
            # our command has sat unread for 10 s with players on: the script is gone
            self._script = "unknown"
        elif self._script == "no" and players > 0 and self._ping_sent_at is not None \
                and self._clock() - self._ping_sent_at > 60:
            self._script = "unknown"            # ask again now and then

    # ------------------------------------------------------------- settings
    def _load(self) -> dict:
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save(self, *_):
        if not hasattr(self, "fade_spin"):
            return
        data = {
            "mods": self.mods_cb.isChecked(),
            "announce": self.announce_cb.isChecked(),
            "last_minute": self.last_minute_cb.isChecked(),
            "minutes": self.minutes_spin.value(),
            "editor": self.editor_look().to_json(),
            "own": {k: {"label": s.label, **s.look.to_json()} for k, s in self._own.items()},
            "maps": dict(self._map_rules),
        }
        try:
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Sky: could not save settings ({exc}).")
