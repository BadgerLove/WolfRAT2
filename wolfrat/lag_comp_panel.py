"""The "Lag compensation" page on the Server tab (Dale, 2026-10-18).

Switches lag compensation on or off in the RUNNING game server (see
lag_comp) and shows how it is doing, live: hits with it against hits plain
JO would have given, per player, the best rewind the shots point to, and the
past sessions.

Works on the Weather tab's server handle like the Live patches page: the
Weather tab calls ``server_tick`` every poll (5 s) and ``no_server`` when there
is none.  The numbers refresh every second on their own while the page shows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from wolfrat import lag_comp as lc
from wolfrat.weather_wildlife_page import scroll_column

BANNER = (
    "<b>\U0001F3AF LAG COMPENSATION</b> - goes into the game server <b>while it runs</b>."
    "<br>• Works on any JO server, retail or TAC. No onHook, no exe change."
    "<br>• <b>Players need nothing.</b> It is all on the server."
    "<br>• A map change keeps it. A server restart or crash takes it away."
)

NO_SERVER = ("Lag compensation needs WolfRAT on the same PC as the game server, connected "
             "to it (Server connection page).")

_HELP = (
    "<b>The problem.</b> The server decides every hit, against where people are on the "
    "server <i>right now</i>. A player with a high ping sees everyone a little in the past, "
    "so to hit someone running they have to aim ahead of them."
    "<br><br><b>The fix.</b> The server remembers where everyone was for the last second. "
    "When a higher-ping player fires, the bullet is checked against where people were "
    "<i>on that player's screen</i>. Shoot what you see and it counts."
    "<br><br><b>Rewind</b> = the player's ping + <b>Extra</b>. Players under the "
    "<b>Minimum ping</b> are not rewound at all (they play as normal JO). Nobody is ever "
    "rewound more than <b>Maximum rewind</b>."
    "<br><br><b>What it never does</b>"
    "<br>• Slow anyone down or add lag."
    "<br>• Move walls, buildings or vehicles: a wall still stops the bullet."
    "<br>• Rewind someone across a respawn."
    "<br><br><b>The numbers.</b> Every shot from a rewound player is checked both ways. "
    "<i>Hits</i> counted, against <i>Without</i> = what plain JO would have given. "
    "<i>Saved</i> = hits only the rewind gave; <i>Cost</i> = shots that would have hit "
    "before (the target ducked or turned in that split second - their pose is not rewound)."
    "<br><br><b>Best rewind</b> looks at every shot that passed close to someone and finds "
    "the rewind that lines it up best. Its middle value, minus the ping, is the <b>Extra</b> "
    "that suits your players. <b>Use suggested</b> sets it (after 300 close shots)."
    "<br><br><b>Watch only</b> counts everything but changes no hits - handy for a first "
    "night."
    "<br><br>Changing a setting starts the counting again, so each session row in "
    "<b>Past sessions</b> is one setting."
)

_PLAYER_COLUMNS = ["Player", "Ping", "Rewind", "Shots", "Hits", "Without", "Gain"]
_HISTORY_COLUMNS = ["Started", "Min", "Setting", "Hits", "Without", "Gain", "Best"]


def _gain_text(hits: int, plain: int) -> str:
    if not plain:
        return "-" if not hits else "new"
    return "%+d%%" % round((hits - plain) * 100.0 / plain)


def _signed(ms: Optional[int]) -> str:
    return "-" if ms is None else ("%+d ms" % ms)


class LagCompPanel(QWidget):
    def __init__(self, settings_file: Path, history_file: Path, data_dir: Path,
                 log: Callable[[str], None], poll_now: Optional[Callable[[], None]] = None,
                 parent=None):
        super().__init__(parent)
        self._file = Path(settings_file)
        self._data_dir = str(data_dir)
        self._log = log
        self._poll_now = poll_now
        self._history = lc.HistoryBook(str(history_file))
        self._helper_name = lc.loaded_name(self._data_dir)
        saved = self._load()
        self._wish_on = bool(saved.get("on", False))
        self._settings = lc.Settings(bool(saved.get("observe", False)),
                                     int(saved.get("extra_ms", lc.DEFAULT_EXTRA_MS)),
                                     int(saved.get("max_ms", lc.DEFAULT_MAX_MS)),
                                     int(saved.get("min_ping_ms", lc.DEFAULT_MIN_PING_MS))).clamp()
        self._reapply = bool(saved.get("reapply", False))
        self._dirty = False             # a click waits to be written into the server
        self._pid: Optional[int] = None
        self._state: Optional[lc.State] = None
        self._last_record = 0.0
        self._updating = False
        self._build()
        self._show_wish()
        self._show_history()
        self.no_server(NO_SERVER)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(900)
        self._settings_timer.timeout.connect(self._settings_settled)

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QHBoxLayout(self)
        left_scroll, left = scroll_column()
        right_scroll, right = scroll_column()
        root.addWidget(left_scroll, 3)
        root.addWidget(right_scroll, 3)

        banner = QLabel(BANNER)
        banner.setWordWrap(True)
        banner.setTextFormat(Qt.TextFormat.RichText)
        banner.setStyleSheet(
            "font-size: 10pt; color: #ffd700; background: #2a1a00; border: 2px solid #c08020; "
            "border-radius: 4px; padding: 8px;")
        left.addWidget(banner)

        self.server_lbl = QLabel()
        self.server_lbl.setWordWrap(True)
        self.server_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px;")
        left.addWidget(self.server_lbl)

        group = QGroupBox("\U0001F3AF Lag compensation")
        box = QVBoxLayout()
        self.on_cb = QCheckBox("On - check higher-ping players' shots against what they saw")
        box.addWidget(self.on_cb)
        self.observe_cb = QCheckBox("Watch only (count everything, change no hits)")
        box.addWidget(self.observe_cb)
        form = QFormLayout()
        self.extra_spin = QSpinBox()
        self.extra_spin.setRange(-200, 300)
        self.extra_spin.setSingleStep(10)
        self.extra_spin.setSuffix(" ms")
        form.addRow("Extra (added to each player's ping):", self.extra_spin)
        self.min_spin = QSpinBox()
        self.min_spin.setRange(0, 500)
        self.min_spin.setSingleStep(10)
        self.min_spin.setSuffix(" ms")
        form.addRow("Minimum ping (below it: no rewind):", self.min_spin)
        self.max_spin = QSpinBox()
        self.max_spin.setRange(50, 900)
        self.max_spin.setSingleStep(50)
        self.max_spin.setSuffix(" ms")
        form.addRow("Maximum rewind:", self.max_spin)
        box.addLayout(form)
        self.reapply_cb = QCheckBox("Put it back on by itself after a server restart")
        box.addWidget(self.reapply_cb)
        self.state_lbl = QLabel()
        self.state_lbl.setWordWrap(True)
        self.state_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px 0;")
        box.addWidget(self.state_lbl)
        group.setLayout(box)
        left.addWidget(group)

        stats_group = QGroupBox("\U0001F4CA This session")
        stats_box = QVBoxLayout()
        self.totals_lbl = QLabel()
        self.totals_lbl.setWordWrap(True)
        self.totals_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.totals_lbl.setStyleSheet("font-size: 11pt; color: #e0d070; padding: 4px;")
        stats_box.addWidget(self.totals_lbl)
        self.tune_lbl = QLabel()
        self.tune_lbl.setWordWrap(True)
        self.tune_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.tune_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px;")
        stats_box.addWidget(self.tune_lbl)
        buttons = QHBoxLayout()
        self.suggest_btn = QPushButton("Use suggested")
        self.suggest_btn.setEnabled(False)
        buttons.addWidget(self.suggest_btn)
        self.reset_btn = QPushButton("Start counting again")
        buttons.addWidget(self.reset_btn)
        buttons.addStretch()
        stats_box.addLayout(buttons)
        stats_group.setLayout(stats_box)
        left.addWidget(stats_group)
        left.addStretch()

        players_group = QGroupBox("\U0001F465 Players")
        players_box = QVBoxLayout()
        self.players_table = self._table(_PLAYER_COLUMNS)
        players_box.addWidget(self.players_table)
        players_group.setLayout(players_box)
        right.addWidget(players_group)

        history_group = QGroupBox("\U0001F4C5 Past sessions")
        history_box = QVBoxLayout()
        self.history_table = self._table(_HISTORY_COLUMNS, stretch=2)
        history_box.addWidget(self.history_table)
        history_group.setLayout(history_box)
        right.addWidget(history_group)

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
        right.addWidget(help_group)
        right.addStretch()

        self.on_cb.toggled.connect(self._on_clicked)
        self.observe_cb.toggled.connect(self._setting_changed)
        self.extra_spin.valueChanged.connect(self._setting_changed)
        self.min_spin.valueChanged.connect(self._setting_changed)
        self.max_spin.valueChanged.connect(self._setting_changed)
        self.reapply_cb.toggled.connect(self._reapply_clicked)
        self.suggest_btn.clicked.connect(self._use_suggested)
        self.reset_btn.clicked.connect(self._reset_clicked)

    @staticmethod
    def _table(columns, stretch: int = 0) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(24)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(230)
        return table

    def _show_wish(self):
        self._updating = True
        try:
            self.on_cb.setChecked(self._wish_on)
            self.observe_cb.setChecked(self._settings.observe)
            self.extra_spin.setValue(self._settings.extra_ms)
            self.min_spin.setValue(self._settings.min_ping_ms)
            self.max_spin.setValue(self._settings.max_ms)
            self.reapply_cb.setChecked(self._reapply)
        finally:
            self._updating = False

    def _set_available(self, available: bool):
        self.on_cb.setEnabled(available)

    # ------------------------------------------------------------ clicks
    def _on_clicked(self, on: bool):
        if self._updating:
            return
        self._wish_on = bool(on)
        self._dirty = True
        self._save()
        self.state_lbl.setText("Writing to the server...")
        if self._poll_now:
            self._poll_now()

    def _setting_changed(self, *_):
        if self._updating:
            return
        self._settings_timer.start()        # write once the spin box settles

    def _settings_settled(self):
        new = lc.Settings(self.observe_cb.isChecked(), self.extra_spin.value(),
                          self.max_spin.value(), self.min_spin.value()).clamp()
        if new == self._settings:
            return
        self._settings = new
        self._save()
        self._log("Lag compensation: " + lc.describe_setting(new) + ".")
        if self._wish_on:
            self._dirty = True
            if self._poll_now:
                self._poll_now()

    def _reapply_clicked(self, on: bool):
        if self._updating:
            return
        self._reapply = bool(on)
        self._save()

    def _use_suggested(self):
        st = self._state
        if not st or not st.stats:
            return
        value = st.stats.suggested_extra()
        if value is None:
            return
        self.extra_spin.setValue(max(-200, min(300, value)))
        self._settings_settled()

    def _reset_clicked(self):
        if not self._pid:
            return
        try:
            lc.reset_stats(self._pid, self._helper_name)
            self._log("Lag compensation: counting started again.")
        except lc.LagCompError as exc:
            self._log("Lag compensation: " + str(exc))
        self._refresh_stats()

    # ---------------------------------------------- called by the Weather tab
    def wants_write(self) -> bool:
        return self._dirty or (self._wish_on and self._reapply and
                               self._state is not None and not self._state.on)

    def no_server(self, text: str):
        self._pid = None
        self._state = None
        self._set_available(False)
        self.server_lbl.setText("⚠ " + text)
        self.state_lbl.setText("")

    def server_tick(self, memory, writable: bool):
        if memory is None:
            self.no_server(NO_SERVER)
            return
        pid = getattr(memory, "pid", None)
        if pid is None:
            self.no_server(NO_SERVER)
            return
        new_server = pid != self._pid
        self._pid = pid
        self.server_lbl.setText(f"\U0001F5A5 Game server on this PC (process {pid}).")
        self._set_available(True)
        try:
            state = lc.server_state(pid, self._helper_name)
        except lc.LagCompError as exc:
            self.state_lbl.setText("⚠ " + str(exc))
            return
        if new_server and self._wish_on and not state.on and not self._dirty:
            if self._reapply:
                self._dirty = True
                self._log("Lag compensation: new server - putting it back on.")
            else:
                # Restarted server, back to normal JO: say so and untick.
                self._wish_on = False
                self._save()
                self._show_wish()
                self._log("Lag compensation: the server restarted and it is off. "
                          "Tick it to put it back.")
        if self._dirty:
            if not writable:
                self.state_lbl.setText("Waiting for write access to the server "
                                       "(run WolfRAT as administrator if this does not clear)...")
                return
            state = self._write(pid, memory, state)
        self._state = state
        self._show_state(state)

    def _write(self, pid: int, memory, state: lc.State) -> lc.State:
        self._dirty = False
        try:
            if self._wish_on:
                was_on = state.on
                before = state.settings
                state = lc.turn_on(pid, memory, self._data_dir, self._settings)
                if not was_on:
                    lc.reset_stats(pid, self._helper_name)
                    self._log("Lag compensation ON: " + lc.describe_setting(self._settings) + ".")
                elif before != self._settings:
                    lc.reset_stats(pid, self._helper_name)
            elif state.on or state.kind == lc.OLD:
                state = lc.turn_off(pid, memory)
                self._log("Lag compensation OFF: the server is back to normal JO hits.")
        except lc.LagCompError as exc:
            self._log("Lag compensation: " + str(exc))
            self.state_lbl.setText("⚠ " + str(exc))
            if self._wish_on:
                self._wish_on = False
                self._save()
                self._show_wish()
            return state
        try:
            return lc.server_state(pid, self._helper_name)
        except lc.LagCompError:
            return state

    def _show_state(self, st: lc.State):
        if st.kind == lc.FOREIGN:
            text = ("⚠ Something else already changes the game's hit code in this server "
                    "(onHook's own lag compensation?). WolfRAT leaves it alone.")
            self._set_available(False)
        elif st.on:
            mode = "WATCH ONLY - counting, no hits changed" if st.observe else "ON"
            text = "\U0001F7E2 " + mode + " (" + lc.describe_setting(st.settings) + ")."
            if not st.alive:
                text += " Waiting for players - an empty server does not run it."
        elif st.kind == lc.OLD:
            text = ("\U0001F7E1 On, from an older WolfRAT. Tick the box to switch to this "
                    "version (the numbers start again).")
        else:
            text = "⚪ Off - normal JO hits."
        self.state_lbl.setText(text)

    # ------------------------------------------------------------- numbers
    def _refresh_stats(self):
        if not self._pid or not self.isVisible():
            return
        try:
            st = lc.server_state(self._pid, self._helper_name)
        except lc.LagCompError:
            return
        self._state = st
        if not st.stats:
            self.totals_lbl.setText("No numbers yet - switch it on.")
            self.tune_lbl.setText("")
            self.suggest_btn.setEnabled(False)
            self.players_table.setRowCount(0)
            return
        s = st.stats
        self._show_totals(st, s)
        self._show_players(s)
        import time
        now = time.time()
        if now - self._last_record >= 30 and st.settings and \
                self._history.record(self._pid, s, st.settings, now):
            self._last_record = now
            try:
                self._history.save()
            except Exception:
                pass
            self._show_history()

    def _show_totals(self, st: lc.State, s: lc.Stats):
        minutes = s.seconds // 60
        if not s.tests:
            self.totals_lbl.setText(
                f"Counting for {minutes} min. No shots from rewound players yet "
                f"(server tick {s.tick_us / 1000:.1f} ms).")
        else:
            gain = s.gain_percent
            word = "would have" if st.observe else "counted"
            self.totals_lbl.setText(
                f"<b>{s.hits:,}</b> hits {word} with it, <b>{s.plain_hits:,}</b> without "
                f"(<b>{'-' if gain is None else '%+.0f%%' % gain}</b>)"
                f"<br>Saved <b>{s.gained:,}</b> · cost <b>{s.lost:,}</b>"
                f"<br><span style='font-size:9pt'>{minutes} min · {s.tests:,} bullet "
                f"checks · server tick {s.tick_us / 1000:.1f} ms</span>")
        suggestion = s.suggested_extra()
        if s.cal_median is None:
            self.tune_lbl.setText("Best rewind: waiting for shots that pass close to someone.")
        else:
            text = (f"Best rewind: <b>ping {_signed(s.cal_median)}</b> "
                    f"(middle half {_signed(s.cal_low)} to {_signed(s.cal_high)}), "
                    f"from {s.cal_total:,} close shots.")
            if suggestion is None:
                text += " Suggestion after 300 close shots."
            else:
                text += f"<br>Suggested Extra: <b>{_signed(suggestion)}</b>"
                if st.settings and suggestion == st.settings.extra_ms:
                    text += " - that is what you have."
            self.tune_lbl.setText(text)
        self.suggest_btn.setEnabled(suggestion is not None and bool(st.settings) and
                                    suggestion != st.settings.extra_ms)

    def _show_players(self, s: lc.Stats):
        rows = sorted(s.players, key=lambda p: -p.ping)
        self.players_table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            rewind = f"{p.rewind_ms} ms" if p.rewind_ms else "none"
            cells = [p.name, str(p.ping), rewind, f"{p.tests:,}", f"{p.hits:,}",
                     f"{p.plain_hits:,}", _gain_text(p.hits, p.plain_hits)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.players_table.setItem(r, c, item)

    def _show_history(self):
        rows = list(reversed(self._history.rows))
        self.history_table.setRowCount(len(rows))
        for r, h in enumerate(rows):
            best = h.get("cal_median")
            cells = [h.get("started", ""), str(h.get("minutes", "")), h.get("setting", ""),
                     f"{h.get('hits', 0):,}", f"{h.get('plain_hits', 0):,}",
                     _gain_text(h.get("hits", 0), h.get("plain_hits", 0)),
                     "-" if best is None else "ping %+d" % best]
            for c, text in enumerate(cells):
                self.history_table.setItem(r, c, QTableWidgetItem(text))

    # ------------------------------------------------------------ settings
    def _load(self) -> dict:
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save(self):
        s = self._settings
        data = {"on": self._wish_on, "observe": s.observe, "extra_ms": s.extra_ms,
                "max_ms": s.max_ms, "min_ping_ms": s.min_ping_ms, "reapply": self._reapply}
        try:
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Lag compensation: could not save settings ({exc}).")
