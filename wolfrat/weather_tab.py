"""The Weather tab.  All the game knowledge lives in ``wolfrat.weather``."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from wolfrat import weather
from wolfrat import whisper
from wolfrat import server_link
from wolfrat import weather_dynamic
from wolfrat.weather_dynamic_page import DynamicPage
from wolfrat import wildlife
from wolfrat.weather_wildlife_page import WildlifePage
from wolfrat.protocol import wire_log
from wolfrat.runtime import DesktopRuntime
from wolfrat.admin_commands import MAX_CHAT_LEN

# A mod who types no minutes gets the Manual page's "hold for" time; when that
# is "until I clear it", this instead - only the tab itself can hold forever.
MOD_FALLBACK_MINUTES = 10

_PRESET_BUTTONS = (
    ("storm", "⛈️ Storm"), ("rain", "🌧️ Rain"), ("drizzle", "🌦️ Drizzle"),
    ("fog", "🌫️ Fog"), ("blizzard", "🌨️ Blizzard"), ("snow", "❄️ Snow"),
    ("overcast", "☁️ Overcast"),
)

_HELP = (
    "<b>What this does.</b> Joint Ops maps normally decide their own weather, "
    "and it plays the same way every time. This tab changes the sky on the "
    "<b>server</b>, and the server already tells every player what the sky is "
    "doing - so everybody sees the same rain, snow, fog and cloud, with the "
    "normal game. Nobody needs to download anything."
    "<br><br><b>Needs:</b> WolfRAT running on the same PC as the game server "
    "(it changes the numbers inside the running server). Over a remote "
    "connection the tab stays greyed out."
    "<br><br><b>Good to know</b>"
    "<br>• Weather fades in and out over the fade time - it is not instant."
    "<br>• A map change resets the sky; WolfRAT puts your weather back within "
    "a few seconds for as long as it is active."
    "<br>• Fog can only come closer than the map's own view distance, never "
    "further."
    "<br>• <b>Wind</b> is how fast the clouds race across the sky. The game has no wind "
    "direction players can see - the clouds always drift the same way, only faster or slower. "
    "Maps use 15 to about 200. Untick Wind and the map keeps its own."
    "<br>• Earthquake really does nudge players and vehicles about a little."
    "<br>• <b>Lightning and thunder</b> need one tiny script file on the server. Press "
    "<b>Install lightning add-on</b> below (or <b>Link WolfRAT to the server</b> on the Server "
    "tab) and WolfRAT puts it there; it switches on at the next map change. Players still "
    "download nothing."
)


def scroll_column() -> tuple:
    """(scroll area, layout to fill): a column that grows a scroll bar when
    the window is too short for it, and looks like plain layout when not."""
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 6, 0)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(host)
    return area, layout


class WeatherTab(QWidget):
    """Presets, custom dials, timed weather and mod chat commands."""

    POLL_MS = 5000
    SETTLE_SECONDS = 20      # after a (re)connect or map change, before writing

    link_changed = pyqtSignal()   # after every poll: the Server tab's link box redraws

    def __init__(
        self,
        runtime: DesktopRuntime | None = None,
        send_chat: Optional[Callable[[str], object]] = None,
        button_cls: type = QPushButton,
        attach: Callable[..., tuple] = weather.attach_local_server,
        context: Optional[Callable[[], dict]] = None,
        map_list: Optional[Callable[[], list]] = None,
    ):
        super().__init__()
        self.runtime = runtime or DesktopRuntime.production()
        self._send_chat = send_chat
        self._button_cls = button_cls
        self._attach = attach
        # context() -> {"connected": bool, "map": str | None, "players": int}
        self._context = context
        self._map_list = map_list
        self._dynamic = weather_dynamic.DynamicWeather(clock=time.monotonic)
        self._settled_key = None
        self._settled_since = 0.0
        self.current_map = None
        self._sky_before = None        # the sky as we found it, before our front
        self._sky_before_map = None

        self._controller: Optional[weather.WeatherController] = None
        self._memory = None
        self._writable = False
        self._schedule = weather.WeatherSchedule()
        self._last_problem = ""
        self._looked = False           # tried to find the server at least once
        self._server_missing = False   # last attempt: no jointops.exe at all
        self._script_answer = None     # the add-on's last answer (None = not asked)

        self._settings_file = self.runtime.path("wolfrat_weather.json")
        self._settings = {
            "mods_enabled": False, "announce": True, "minutes": 10,
            "precip": 60, "snow": False, "overcast": 80, "fog_on": False,
            "fog_metres": 300, "fade": 25, "wind_on": False, "wind": 150,
            "dynamic": {}, "seen_maps": {},
            "linked": False,
            "wildlife": {},
        }
        self._sharks = wildlife.SharkKeeper()
        try:
            if self._settings_file.exists():
                saved = json.loads(self._settings_file.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self._settings.update(
                        {k: v for k, v in saved.items() if k in self._settings}
                    )
        except Exception:
            pass

        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)
        QTimer.singleShot(0, self._poll)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        self.status_lbl = QLabel("Looking for a game server on this PC...")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px;")
        outer.addWidget(self.status_lbl)

        self.sky_lbl = QLabel("")
        self.sky_lbl.setStyleSheet("font-size: 9pt; color: #a89830; padding: 0 4px 6px 4px;")
        outer.addWidget(self.sky_lbl)

        self.pages = QTabWidget()
        outer.addWidget(self.pages, 1)
        manual_page = QWidget()
        self.pages.addTab(manual_page, "Manual")
        self.dynamic_page = DynamicPage(
            weather_dynamic.DynamicConfig.from_json(self._settings.get("dynamic")),
            button_cls=self._button_cls,
            map_list=self._map_list,
        )
        self.dynamic_page.load_seen(self._settings.get("seen_maps"))
        self.dynamic_page.changed.connect(self._dynamic_changed)
        self.pages.addTab(self.dynamic_page, "Dynamic")
        self._dynamic_was_on = self.dynamic_page.config().enabled
        self.wildlife_page = WildlifePage(self._settings.get("wildlife"))
        self.wildlife_page.changed.connect(self._wildlife_changed)
        self.pages.addTab(self.wildlife_page, "🦈 Wildlife")

        # Some servers sit on a 1024x768 desktop: each column scrolls on its
        # own instead of squeezing its buttons and cutting the help text off.
        root = QHBoxLayout(manual_page)
        left_scroll, left = scroll_column()
        right_scroll, right = scroll_column()
        root.addWidget(left_scroll, 3)
        root.addWidget(right_scroll, 2)

        # -- presets
        preset_group = QGroupBox("Weather now")
        preset_box = QVBoxLayout()
        grid = QGridLayout()
        self._action_widgets = []
        for index, (name, label) in enumerate(_PRESET_BUTTONS):
            button = self._button_cls(label)
            button.setToolTip(weather.PRESETS[name].describe())
            button.clicked.connect(lambda _=False, n=name: self._start_preset(n))
            button.setMinimumHeight(button.sizeHint().height())     # never squeezed into its neighbour
            grid.addWidget(button, index // 4, index % 4)
            self._action_widgets.append(button)
        preset_box.addLayout(grid)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Keep it for"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, weather.MAX_MINUTES)
        self.minutes_spin.setValue(int(self._settings["minutes"]))
        self.minutes_spin.setSpecialValueText("until I clear it")
        self.minutes_spin.setSuffix(" min")
        self.minutes_spin.setMinimumWidth(120)
        self.minutes_spin.valueChanged.connect(self._save)
        time_row.addWidget(self.minutes_spin)
        time_row.addStretch()
        self.clear_btn = self._button_cls("☀️ Clear the weather")
        self.clear_btn.clicked.connect(self._clear)
        time_row.addWidget(self.clear_btn)
        self._action_widgets.append(self.clear_btn)
        preset_box.addLayout(time_row)

        self.active_lbl = QLabel("No weather set by WolfRAT.")
        self.active_lbl.setStyleSheet("font-size: 9pt; color: #a89830;")
        preset_box.addWidget(self.active_lbl)
        preset_group.setLayout(preset_box)
        left.addWidget(preset_group)

        # -- custom
        custom_group = QGroupBox("Make your own")
        custom = QGridLayout()
        self.precip_slider, self.precip_val = self._slider(0, 100, self._settings["precip"], "%")
        self.overcast_slider, self.overcast_val = self._slider(0, 100, self._settings["overcast"], "%")
        self.fog_slider, self.fog_val = self._slider(50, 1500, self._settings["fog_metres"], " m")
        self.fade_slider, self.fade_val = self._slider(1, 120, self._settings["fade"], " s")
        self.wind_slider, self.wind_val = self._slider(0, weather.WIND_MAX, self._settings["wind"], "")
        self.wind_val.setMinimumWidth(96)
        self.wind_slider.valueChanged.connect(lambda v: self.wind_val.setText(weather.wind_text(v)))
        self.wind_val.setText(weather.wind_text(self.wind_slider.value()))
        self.wind_cb = QCheckBox("Wind")
        self.wind_cb.setToolTip("How fast the clouds race across the sky. Unticked = the map's own wind.")
        self.wind_cb.setChecked(bool(self._settings["wind_on"]))
        self.wind_cb.toggled.connect(self.wind_slider.setEnabled)
        self.wind_cb.toggled.connect(self._save)
        self.wind_slider.setEnabled(self.wind_cb.isChecked())
        self.snow_cb = QCheckBox("as snow")
        self.snow_cb.setChecked(bool(self._settings["snow"]))
        self.snow_cb.toggled.connect(self._save)
        self.fog_cb = QCheckBox("Fog at")
        self.fog_cb.setChecked(bool(self._settings["fog_on"]))
        self.fog_cb.toggled.connect(self.fog_slider.setEnabled)
        self.fog_cb.toggled.connect(self._save)
        self.fog_slider.setEnabled(self.fog_cb.isChecked())

        custom.addWidget(QLabel("Rain / snow"), 0, 0)
        custom.addWidget(self.precip_slider, 0, 1)
        custom.addWidget(self.precip_val, 0, 2)
        custom.addWidget(self.snow_cb, 0, 3)
        custom.addWidget(QLabel("Overcast"), 1, 0)
        custom.addWidget(self.overcast_slider, 1, 1)
        custom.addWidget(self.overcast_val, 1, 2)
        custom.addWidget(self.fog_cb, 2, 0)
        custom.addWidget(self.fog_slider, 2, 1)
        custom.addWidget(self.fog_val, 2, 2)
        custom.addWidget(self.wind_cb, 3, 0)
        custom.addWidget(self.wind_slider, 3, 1)
        custom.addWidget(self.wind_val, 3, 2, 1, 2)
        custom.addWidget(QLabel("Fade over"), 4, 0)
        custom.addWidget(self.fade_slider, 4, 1)
        custom.addWidget(self.fade_val, 4, 2)
        self.apply_btn = self._button_cls("Apply")
        self.apply_btn.clicked.connect(self._start_custom)
        custom.addWidget(self.apply_btn, 4, 3)
        self._action_widgets.append(self.apply_btn)
        custom.setColumnStretch(1, 1)
        custom_group.setLayout(custom)
        left.addWidget(custom_group)

        # -- quake
        quake_group = QGroupBox("Earthquake and lightning")
        quake_row = QHBoxLayout()
        self.quake_spin = QSpinBox()
        self.quake_spin.setRange(1, weather.QUAKE_MAX_SECONDS)
        self.quake_spin.setValue(5)
        self.quake_spin.setSuffix(" s")
        quake_row.addWidget(self.quake_spin)
        self.quake_btn = self._button_cls("🌋 Shake")
        self.quake_btn.clicked.connect(lambda: self._quake(self.quake_spin.value(), "WolfRAT"))
        quake_row.addWidget(self.quake_btn)
        self._action_widgets.append(self.quake_btn)
        self.flash_btn = self._button_cls("⚡ Lightning")
        self.flash_btn.setToolTip("One flash and a roll of thunder for everybody.")
        self.flash_btn.clicked.connect(lambda: self._flash("WolfRAT"))
        self.flash_btn.setEnabled(False)
        quake_row.addWidget(self.flash_btn)
        self.far_flash_btn = self._button_cls("🌩️ Distant")
        self.far_flash_btn.setToolTip("Lightning on the horizon, thunder a moment later.")
        self.far_flash_btn.clicked.connect(lambda: self._flash("WolfRAT", far=True))
        self.far_flash_btn.setEnabled(False)
        quake_row.addWidget(self.far_flash_btn)
        self.install_btn = self._button_cls("Install lightning add-on")
        self.install_btn.setToolTip(
            "Lightning is the one thing the game will not do on command without a tiny script "
            "on the server. This puts that script (server.wac) in your server folder for you. "
            "It does nothing unless WolfRAT asks, and players download nothing."
        )
        self.install_btn.clicked.connect(self._install_addon)
        self.install_btn.setVisible(False)
        quake_row.addWidget(self.install_btn)
        quake_row.addStretch()
        quake_box = QVBoxLayout()
        quake_box.addLayout(quake_row)
        self.flash_lbl = QLabel("")
        self.flash_lbl.setWordWrap(True)
        self.flash_lbl.setStyleSheet("font-size: 9pt; color: #a89830;")
        self.flash_lbl.setMinimumWidth(240)
        self.flash_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        quake_box.addWidget(self.flash_lbl)
        quake_group.setLayout(quake_box)
        left.addWidget(quake_group)
        left.addStretch()

        # -- right column
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

        chat_group = QGroupBox("In-game commands")
        chat_box = QVBoxLayout()
        self.mods_cb = QCheckBox("Let mods change the weather from chat")
        self.mods_cb.setChecked(bool(self._settings["mods_enabled"]))
        self.mods_cb.setToolTip(
            "!storm 10   !rain   !snow   !blizzard   !fog   !drizzle   !overcast\n"
            "!clear   !quake 5   !weather <name> [minutes]\n"
            "No minutes typed = the 'Keep it for' time on the Manual page "
            f"({MOD_FALLBACK_MINUTES} min when that says 'until I clear it')."
        )
        self.mods_cb.toggled.connect(self._save)
        chat_box.addWidget(self.mods_cb)
        self.announce_cb = QCheckBox("Announce weather changes in chat")
        self.announce_cb.setChecked(bool(self._settings["announce"]))
        self.announce_cb.toggled.connect(self._save)
        chat_box.addWidget(self.announce_cb)
        cmds = QLabel("!storm 10 &nbsp; !rain &nbsp; !drizzle &nbsp; !fog &nbsp; !overcast<br>"
                      "!snow &nbsp; !blizzard &nbsp; !clear &nbsp; !quake 5")
        cmds.setTextFormat(Qt.TextFormat.RichText)
        cmds.setWordWrap(True)
        cmds.setStyleSheet("font-size: 9pt; color: #a89830;")
        cmds.setMinimumWidth(240)
        cmds.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        chat_box.addWidget(cmds)
        chat_group.setLayout(chat_box)
        right.addWidget(chat_group)
        # the switches first, the reading matter under them: on a short
        # window the help is what should need scrolling to, not the tick boxes
        right.addWidget(help_group)

        log_group = QGroupBox("Weather log")
        log_box = QVBoxLayout()
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(110)
        log_box.addWidget(self.log_list)
        log_group.setLayout(log_box)
        right.addWidget(log_group, 1)

        self._set_available(False)

    def _slider(self, low, high, value, suffix):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(max(low, min(high, int(value))))
        label = QLabel(f"{slider.value()}{suffix}")
        label.setMinimumWidth(56)
        slider.valueChanged.connect(lambda v, l=label, s=suffix: l.setText(f"{v}{s}"))
        slider.sliderReleased.connect(self._save)
        return slider, label

    def _set_available(self, available: bool):
        for widget in self._action_widgets:
            widget.setEnabled(available)
        self.wildlife_page.set_available(available)

    # ------------------------------------------------------------ plumbing
    def log(self, message: str):
        self.log_list.addItem(f"[{time.strftime('%H:%M:%S')}] {message}")
        self.log_list.scrollToBottom()
        while self.log_list.count() > 200:
            self.log_list.takeItem(0)
        wire_log(f"[WEATHER] {message}")

    def _save(self, *_):
        self._settings.update({
            "mods_enabled": self.mods_cb.isChecked(),
            "announce": self.announce_cb.isChecked(),
            "minutes": self.minutes_spin.value(),
            "precip": self.precip_slider.value(),
            "snow": self.snow_cb.isChecked(),
            "overcast": self.overcast_slider.value(),
            "fog_on": self.fog_cb.isChecked(),
            "fog_metres": self.fog_slider.value(),
            "fade": self.fade_slider.value(),
            "wind_on": self.wind_cb.isChecked(),
            "wind": self.wind_slider.value(),
            "dynamic": self.dynamic_page.config().to_json(),
            "seen_maps": self.dynamic_page.seen(),
            "wildlife": self.wildlife_page.config(),
        })
        try:
            self._settings_file.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
        except Exception as exc:
            wire_log(f"[WEATHER] could not save settings: {exc}")

    def _detach(self):
        if self._memory is not None:
            try:
                self._memory.close()
            except Exception:
                pass
        self._controller = self._memory = None
        self._writable = False
        self._script_answer = None

    def _ensure(self, writable: bool) -> Optional[weather.WeatherController]:
        """Status polling only ever holds a read-only handle; the first real
        action swaps it for one that can write."""
        if self._controller is not None and (self._writable or not writable):
            return self._controller
        self._detach()
        self._looked = True
        try:
            self._controller, self._memory = self._attach(writable=writable)
            self._writable = writable
            self._last_problem = ""
            self._server_missing = False
        except weather.WeatherError as exc:
            self._server_missing = isinstance(exc, weather.ServerNotRunning)
            self._problem(str(exc))
        return self._controller

    def _problem(self, text: str):
        self._detach()
        self._set_available(False)
        self.status_lbl.setText("⚪ " + text)
        self.sky_lbl.setText("")
        self.wildlife_page.set_status("⚪ " + text)
        if text != self._last_problem:
            self._last_problem = text
            wire_log(f"[WEATHER] unavailable: {text}")

    def _poll(self):
        try:
            self._poll_once()
        finally:
            self.link_changed.emit()

    def _poll_once(self):
        # A held weather must keep being re-asserted even after the server
        # restarted under us, so it asks for the writable handle back.  Once
        # linked, the add-on check needs it too.
        controller = self._ensure(
            writable=bool(self._writable or self._schedule.active is not None
                          or self.dynamic_page.config().enabled or self._settings.get("linked")
                          or self.wildlife_page.enabled())
        )
        if controller is None:
            return
        try:
            outcome = self._schedule.tick(controller) if self._writable else None
            reading = controller.read()
        except weather.WeatherError as exc:
            self._problem(str(exc))
            return
        if outcome == "ended":
            self.log("Timed weather finished - clearing.")
            self._announce("The weather is clearing.")
        self._set_available(True)
        folder = self._server_dir()
        if not self._settings.get("linked") and folder and weather.addon_installed(folder):
            self._settings["linked"] = True       # installed from this tab before linking existed
            self._save()
        if self._settings.get("linked") and self._writable:
            self._ensure_whisper_block(folder)
        self.status_lbl.setText(f"🟢 Game server found on this PC (process {self._memory.pid}).")
        kind = "snow" if reading.snow else "rain"
        self.sky_lbl.setText(
            f"Sky right now:  {kind} {reading.precip_percent}%   ·   overcast "
            f"{reading.overcast_percent}%   ·   you can see {reading.fog_metres} m "
            f"(map allows {reading.map_fog_metres} m)   ·   wind {weather.wind_text(reading.cloud_speed)}"
            f"   ·   game clock {reading.clock}"
        )
        self.fog_slider.setMaximum(max(100, reading.map_fog_metres))
        active = self._schedule.active
        if active is None:
            self.active_lbl.setText("No weather set by WolfRAT.")
        else:
            left = self._schedule.seconds_left()
            tail = "until cleared" if left is None else f"{left // 60}:{left % 60:02d} left"
            self.active_lbl.setText(f"Holding: {active.describe()}  -  {tail}")
        self._dynamic_tick(controller, reading)
        self._wildlife_tick(controller)

    # ------------------------------------------------------------ wildlife
    def _wildlife_tick(self, controller):
        """Keep the sharks hunting.  Never raises: a wildlife problem must not
        take the weather down with it."""
        try:
            map_name, _players, settled = self._server_context()
            status = self._sharks.tick(
                controller._mem,
                enabled=self.wildlife_page.enabled(),
                attack_m=self.wildlife_page.attack_m(),
                writable=self._writable and settled,     # never write into a loading server
                map_name=map_name,
                linked=True,                             # a found server is enough for the sharks
            )
        except Exception as exc:                          # pragma: no cover - belt and braces
            self.wildlife_page.set_status(f"⚪ Wildlife check failed: {exc}")
            wire_log(f"[WILDLIFE] {exc}")
            return
        if status.reapplied != getattr(self, "_sharks_logged", 0):
            self._sharks_logged = status.reapplied
            self.log(f"Sharks: {len(status.sharks)} set to hunt "
                     f"(hostile, {self.wildlife_page.attack_m()} m) on {map_name or 'this map'}.")
        self.wildlife_page.set_status(status.text)

    def _wildlife_changed(self):
        self._save()
        config = self.wildlife_page.config()
        self._sharks.reset()
        self._sharks_logged = 0
        self.log("Hunting sharks switched " + ("on" if config["enabled"] else "off")
                 + f" (attack distance {config['attack_m']} m).")
        self._poll()

    # ------------------------------------------------------------- dynamic
    def _server_context(self) -> tuple[Optional[str], int, bool]:
        """(map, players, settled).  Settled = connected, and neither the
        connection nor the map has changed for SETTLE_SECONDS - a map load drops
        the admin connection, so this keeps us from writing into a loading server."""
        if self._context is None:
            return self.current_map, 1, True
        try:
            info = self._context() or {}
        except Exception:
            info = {}
        connected = bool(info.get("connected"))
        key = (connected, info.get("map"))
        now = time.monotonic()
        if key != self._settled_key:
            self._settled_key, self._settled_since = key, now
        settled = connected and now - self._settled_since >= self.SETTLE_SECONDS
        return info.get("map"), int(info.get("players") or 0), settled

    def _dynamic_tick(self, controller, reading):
        config = self.dynamic_page.config()
        try:
            map_name, players, settled = self._server_context()
            if self._writable and settled:          # never write into a loading server
                controller.probe_addon()
            can_flash = controller.lightning_available()
            self._script_answer = True if can_flash else controller._addon
            for button in (self.flash_btn, self.far_flash_btn):
                button.setEnabled(can_flash)
            text, offer_install = self._lightning_state(can_flash, controller._addon is not None)
            self.flash_lbl.setText(text)
            self.install_btn.setVisible(offer_install)
            self.dynamic_page.set_lightning_text(text if config.lightning else "")
            info = controller.map_info()
            snow = weather_dynamic.is_snow_map(info.terrain, info.says_snow, config.snow_terrains)
            rule = weather_dynamic.map_rule_for(map_name, config.map_rules)
            if self.dynamic_page.show_current_map(map_name, info, snow, rule):
                self._save()
            decision = self._dynamic.tick(
                config, map_name=map_name, players=players, in_game=settled,
                manual_active=self._schedule.active is not None,
                sky_is_dry=reading.precip_percent < 2, sky_is_snow=reading.snow,
                map_is_snow=snow if info.terrain else None,
            )
            status = decision.status
            if self._dynamic.front is None and config.enabled and (
                    reading.precip_percent >= 2 or reading.overcast_percent >= 2):
                kind = "snow" if reading.snow else "rain"
                parts = ([f"{kind} {reading.precip_percent}%"] if reading.precip_percent >= 2 else []) \
                    + ([f"overcast {reading.overcast_percent}%"] if reading.overcast_percent >= 2 else [])
                status += "  -  the map's own sky right now: " + ", ".join(parts) + "."
            self.dynamic_page.set_status(status)
            if not self._writable:
                return
            if self._sky_before is not None and self._sky_before_map != map_name:
                self._sky_before = None                     # new map: its load reset the sky
            if decision.sky is not None:
                handing_back = decision.sky.describe() == "clear"
                if not handing_back and self._sky_before is None:
                    self._sky_before, self._sky_before_map = controller.snapshot(), map_name
                if handing_back and self._sky_before is not None:
                    controller.restore(self._sky_before, decision.sky.fade_seconds)
                else:
                    controller.apply(decision.sky)
                if handing_back:
                    self._sky_before = None
            if settled:                              # a fade waits out a map load
                controller.step_wind()
            if decision.quake_seconds:
                used = controller.quake(decision.quake_seconds)
                self.log(f"Dynamic weather: {used} s earthquake.")
                if config.announce and self._send_chat:
                    self._send_chat("Earthquake!")
            if decision.lightning:
                controller.flash(far=decision.lightning_far)
        except weather.WeatherError as exc:
            self._problem(str(exc))
            return
        if decision.announce:
            self.log(f"Dynamic weather: {decision.announce}")
            if self._send_chat:
                try:
                    self._send_chat(decision.announce)
                except Exception as exc:
                    wire_log(f"[WEATHER] announce failed: {exc}")

    def _dynamic_changed(self):
        self._save()
        enabled = self.dynamic_page.config().enabled
        if enabled != self._dynamic_was_on:
            self._dynamic_was_on = enabled
            self.log("Dynamic weather switched " + ("on." if enabled else "off."))
            if not enabled and self._dynamic.front is not None and self._schedule.active is None:
                controller = self._ensure(writable=True)
                if controller is not None:
                    try:
                        controller.apply(weather.CLEAR)
                    except weather.WeatherError as exc:
                        self._problem(str(exc))
            if not enabled:
                self._dynamic.reset()
        self._poll()

    def _announce(self, message: str):
        if self._send_chat and self.announce_cb.isChecked():
            try:
                self._send_chat(message)
            except Exception as exc:
                wire_log(f"[WEATHER] announce failed: {exc}")

    # ------------------------------------------------------------- actions
    def _start(self, sky: weather.Weather, label: str, minutes: Optional[int], who: str) -> bool:
        controller = self._ensure(writable=True)
        if controller is None:
            return False
        try:
            if sky == weather.CLEAR:
                self._schedule.clear(controller)
            else:
                self._schedule.start(controller, sky, minutes)
        except weather.WeatherError as exc:
            self._problem(str(exc))
            return False
        if sky == weather.CLEAR:
            self.log(f"{who} cleared the weather.")
            self._announce("The weather is clearing.")
        else:
            span = f" for {minutes} min" if minutes else ""
            self.log(f"{who} set {label}{span} ({sky.describe()}).")
            self._announce(f"Weather: {label} rolling in{span}.")
        self._poll()
        return True

    def _start_preset(self, name: str):
        self._start(weather.PRESETS[name], name, self.minutes_spin.value() or None, "WolfRAT")

    def _start_custom(self):
        self._save()
        sky = weather.Weather(
            precip_percent=self.precip_slider.value(),
            snow=self.snow_cb.isChecked(),
            overcast_percent=self.overcast_slider.value(),
            fog_metres=self.fog_slider.value() if self.fog_cb.isChecked() else None,
            cloud_speed=self.wind_slider.value() if self.wind_cb.isChecked() else None,
            fade_seconds=self.fade_slider.value(),
        )
        self._start(sky, "custom weather", self.minutes_spin.value() or None, "WolfRAT")

    def _clear(self):
        self._dynamic.end_front_now()
        self._start(weather.CLEAR, "clear", None, "WolfRAT")

    def refresh_maps(self, *_):
        self.dynamic_page.refresh_maps()

    def on_missions_updated(self, missions):
        """Same <CURRENT MISSION> parse the Sprees tab uses."""
        for line in missions or ():
            if '<CURRENT MISSION>' in line:
                current = line.split(' - ')[0].strip()
                if ':' in current[:5]:
                    current = current.split(':', 1)[1].strip()
                current = re.sub(r'<[^>]*>', '', current).strip()
                if current and current != self.current_map:
                    self.current_map = current
                    wire_log(f"[WEATHER] map is now {current}")
                return

    def server_exe(self) -> str:
        """Full path of the running server's exe, "" if WolfRAT has none."""
        try:
            return self._memory.exe_path() if self._memory is not None else ""
        except Exception:
            return ""

    def read_server(self, address: int, size: int) -> Optional[bytes]:
        """Read-only peek at the running server, None if WolfRAT has none."""
        try:
            return self._memory.read(address, size) if self._memory is not None else None
        except Exception:
            return None

    def _server_dir(self) -> str:
        path = self.server_exe()
        return os.path.dirname(path) if path else ""

    def _lightning_state(self, can_flash: bool, answered: bool = True) -> tuple[str, bool]:
        """(what to tell the admin, whether to offer the Install button)."""
        if can_flash:
            return "Lightning is ready on this map.", False
        folder = self._server_dir()
        if not folder:
            return "Lightning: could not find the server's folder.", False
        if not weather.addon_installed(folder):
            return ("Lightning needs a tiny add-on in your server folder. Press "
                    "'Install lightning add-on' - WolfRAT does it for you."), True
        if not self._writable:
            return ("Lightning add-on is installed. WolfRAT checks it the first time you use "
                    "the weather."), False
        if not answered:
            return "Lightning add-on is installed - checking that it is running...", False
        return ("Lightning add-on is installed, but this map loaded before it was - it switches "
                "on at the next map change. Nothing else to do."), False

    def _install_addon(self):
        folder = self._server_dir()
        if not folder:
            self.log("Could not find the server's folder to install the lightning add-on.")
            return
        try:
            result = weather.install_addon(folder)
        except weather.WeatherError as exc:
            self.flash_lbl.setText(str(exc))
            self.log(str(exc))
            return
        self.log("Lightning add-on already installed." if result == "already"
                 else f"Lightning add-on installed in {folder}. It switches on at the next map change.")
        self._settings["linked"] = True
        self._save()
        self._poll()

    # ------------------------------------------------------ server link
    def writable_memory(self):
        """The writable handle to the server, if this tab holds one (the
        private replies borrow it - one owner, see whisper.py)."""
        return self._memory if (self._writable and self._controller is not None) else None

    def _ensure_whisper_block(self, folder: str) -> None:
        """Private replies need their few lines in server.wac; put them in once
        per session for a linked server (they switch on at the next map)."""
        if not folder or getattr(self, "_whisper_checked", None) == folder:
            return
        self._whisper_checked = folder
        try:
            result = whisper.install(folder)
        except Exception as exc:                       # server_wac.ServerWacError, OSError
            wire_log(f"[WHISPER] could not add the private-reply lines to server.wac: {exc}")
            return
        if result != "unchanged":
            wire_log(f"[WHISPER] private replies {result} in {folder}\server.wac - live from the next map")

    def link_state(self) -> server_link.LinkState:
        """What the Server tab's 'Link WolfRAT to the server' box shows."""
        folder = self._server_dir()
        connected = True
        if self._context is not None:
            try:
                connected = bool((self._context() or {}).get("connected"))
            except Exception:
                connected = False
        attached = self._controller is not None
        return server_link.LinkState(
            looked=self._looked,
            found=attached or (self._looked and not self._server_missing),
            reachable=attached,
            problem="" if attached else self._last_problem,
            pid=getattr(self._memory, "pid", None),
            folder=folder,
            script_installed=bool(folder) and weather.addon_installed(folder),
            writable=self._writable,
            script_running=self._script_answer if self._writable else None,
            connected=connected,
        )

    def link_to_server(self) -> tuple[bool, str]:
        """The one-click set-up: a handle that can talk to the server, the
        script in its folder, and the link remembered for next time."""
        try:
            controller = self._ensure(writable=True)
            if controller is None:
                return False, self._last_problem or "WolfRAT could not open the game server."
            folder = self._server_dir()
            if not folder:
                return False, ("WolfRAT found the server but could not work out which folder "
                               "it runs from.")
            try:
                result = weather.install_addon(folder)
            except weather.WeatherError as exc:
                self.log(str(exc))
                return False, str(exc)
            self._settings["linked"] = True
            self._save()
            self._ensure_whisper_block(folder)
            if result == "installed":
                self.log(f"Linked to the server - script added to server.wac in {folder}.")
                return True, (f"Linked. The server script is in {folder} and switches on at "
                              "the next map change.")
            self.log("Linked to the server - its script was already in place.")
            return True, "Linked. The server script was already in place."
        finally:
            self._poll()

    def _flash(self, who: str, far: bool = False) -> bool:
        controller = self._ensure(writable=True)
        if controller is None:
            return False
        try:
            fired = controller.flash(far=far)
        except weather.WeatherError as exc:
            self._problem(str(exc))
            return False
        if fired:
            self.log(f"{who} called down {'distant ' if far else ''}lightning.")
        return fired

    def _quake(self, seconds: int, who: str) -> bool:
        controller = self._ensure(writable=True)
        if controller is None:
            return False
        try:
            used = controller.quake(seconds)
        except weather.WeatherError as exc:
            self._problem(str(exc))
            return False
        self.log(f"{who} started a {used} s earthquake.")
        self._announce("Earthquake!")
        return True

    # ----------------------------------------------------------- mod chat
    def _dynamic_from_chat(self, sender: str, request) -> str:
        """!weather on [how often] / off / status - the same switch and 'How
        often' box as the Dynamic page, so the page, the saved settings and
        the hand-back of the sky all behave exactly as if the admin clicked."""
        page = self.dynamic_page
        if request.switch == "status":
            if not page.enabled_cb.isChecked():
                return "Weather is off. Mods can type !weather on"
            return f"Weather is on ({page.frequency_combo.currentText().lower()}). {self._dynamic_status_for_chat()}"[:MAX_CHAT_LEN]
        if request.switch == "off":
            if not page.enabled_cb.isChecked():
                return "Weather is already off."
            page.enabled_cb.setChecked(False)
            self.log(f"{sender} switched the changing weather off from chat.")
            return "Weather OFF - the sky goes back to the map's own."
        if request.custom_range is not None:
            page.clear_min.setValue(request.custom_range[0])
            page.clear_max.setValue(request.custom_range[1])
        if request.frequency:
            index = page.frequency_combo.findData(request.frequency)
            if index >= 0:
                page.frequency_combo.setCurrentIndex(index)
        was_on = page.enabled_cb.isChecked()
        page.enabled_cb.setChecked(True)
        how = page.frequency_combo.currentText().lower()
        config = page.config()
        clear_min, clear_max, _hold_min, _hold_max = config.spans()
        self.log(f"{sender} set the changing weather to {how} from chat.")
        lead = "Weather is now" if was_on else "Weather ON:"
        return f"{lead} {how} - a front every {clear_min}-{clear_max} min"

    def _dynamic_status_for_chat(self) -> str:
        text = self.dynamic_page.status_lbl.text().split("  -  ")[0].strip()
        return text

    def on_mod_command(self, sender: str, cmd: str, args: list[str]) -> Optional[str]:
        """Called by the Mods tab for an authorised mod.  Returns a chat reply
        for the mod, or None when the public announcement already says it."""
        if not self.mods_cb.isChecked():
            return "Weather commands are switched off in WolfRAT."
        request = weather.parse_chat_command(cmd, args)
        if request.kind == "usage":
            return request.message
        if request.kind == "dynamic":
            return self._dynamic_from_chat(sender, request)
        if request.kind == "lightning":
            return None if self._flash(sender) else "Lightning is not set up on this server."
        if request.kind == "quake":
            ok = self._quake(request.seconds, sender)
        else:
            if request.weather == weather.CLEAR:
                self._dynamic.end_front_now()
            minutes = request.minutes or self.minutes_spin.value() or MOD_FALLBACK_MINUTES
            ok = self._start(request.weather, request.name, minutes, sender)
        if not ok:
            return "No weather: WolfRAT must run on the server PC."
        return None if self.announce_cb.isChecked() else "Weather changed."
