"""The Bans tab: WolfRAT's own ban list, punt on sight, copy-paste firewall
lines, import/export, a whitelist, and the player history page.

The tab is fed by the main window: ``on_players`` every admin-port poll and
``on_chat`` for chat.  It punts through the ``punt`` callable it is given
(never the JO ``PLAYER BAN`` - see ban_rules.py for why) and announces
through ``announce``.  IPs come from ``jo_players.LocalServerPlayers`` when
the server runs on this PC; without them name bans still work.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                             QDialog, QDialogButtonBox, QFileDialog, QFrame, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                             QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                             QTabWidget, QVBoxLayout, QWidget)

from wolfrat import ban_rules as br
from wolfrat import idle_rules
from wolfrat import ip_checks
from wolfrat import zone_rules
from wolfrat.ban_rules import BanEntry, BanList, Enforcer, KIND_IP, KIND_NAME
from wolfrat import coop_guard
from wolfrat.jo_players import LocalServerPlayers, ips_by_name, positions_by_name
from wolfrat.player_history import PlayerHistory, describe_when
from wolfrat.admin_commands import MAX_CHAT_LEN

BANS_FILE = "wolfrat_bans.json"
HISTORY_FILE = "wolfrat_player_history.json"
CHECKS_FILE = "wolfrat_ip_checks.json"
IDLE_FILE = "wolfrat_idle.json"
EXPIRY_CHOICES = [("never", ""), ("1 hour", "1h"), ("1 day", "1d"), ("7 days", "7d"),
                  ("30 days", "30d"), ("90 days", "90d")]

_HELP = (
    "<b>How this works:</b> Joint Ops' own ban uses a CD-key id nobody has any "
    "more, so WolfRAT keeps its <b>own</b> list. Anyone on it is removed the "
    "moment WolfRAT sees them (every 5 seconds), by <b>name</b> or by <b>IP</b>. "
    "IPs are read from the server running on this PC - the admin port never sends them.<br>"
    "<b>Firewall:</b> next to every IP entry is a ready PowerShell line. Copy it, "
    "run it <b>as administrator on the PC hosting the server</b>, and that address "
    "cannot even reach the game. Unblock the same way. WolfRAT never touches the "
    "firewall itself, so it needs no admin rights.<br>"
    "<b>Whitelist</b> entries are never removed, whatever else matches."
)

_HELP_SHORT = "WolfRAT's own ban list - by name or IP, removed on sight. Hover here for how it works."

_TABLE_STYLE = "QTableWidget { background: #0a0a00; color: #d0c060; gridline-color: #2a2a00; }"


class ImportDialog(QDialog):
    """Paste a list, see exactly what will be added, then add it."""

    def __init__(self, now: float, added_by: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import a ban list")
        self.setMinimumSize(640, 480)
        self._now, self._by = now, added_by
        self.parsed: list = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "One entry per line: <b>value</b>, or <b>value | reason</b>, or <b>value | reason | expiry</b>.<br>"
            "Value = a name, an IP, <code>82.68.*</code>, <code>82.68.0.0/16</code> or <code>a.b.c.d-e.f.g.h</code>. "
            "Expiry = never, 7d, 12h, 30m or a date. Babstats <code>ip=reason</code> lines and JO "
            "<code>banlist.txt</code> lines are understood. Comments (#, //, ;) are skipped."))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("82.68.58.92 | griefing | 7d\nTroll\n10.0.0.*")
        self.edit.textChanged.connect(self._preview)
        layout.addWidget(self.edit, 2)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        row = QHBoxLayout()
        load = QPushButton("Load from file..."); load.clicked.connect(self._load)
        row.addWidget(load); row.addStretch()
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._preview()

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ban list file", "", "Text files (*.txt *.ini *.csv);;All files (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    self.edit.setPlainText(handle.read())
            except OSError as exc:
                QMessageBox.warning(self, "Could not read", str(exc))

    def _preview(self):
        self.parsed = br.parse_import(self.edit.toPlainText(), now=self._now, added_by=self._by)
        good = [p for p in self.parsed if p.entry]
        lines = [f"Will add {len(good)} entr{'y' if len(good) == 1 else 'ies'}:"]
        for p in good:
            e = p.entry
            lines.append(f"  {e.kind:4} {e.value}" + (f"  - {e.reason}" if e.reason else "")
                         + ("" if e.expires_at is None else f"  (until {br.format_expiry(e.expires_at)})"))
        skipped = [p for p in self.parsed if not p.entry]
        if skipped:
            lines.append(f"Skipping {len(skipped)}:")
            lines += [f"  {p.raw.strip()!r}: {p.problem}" for p in skipped]
        self.preview.setPlainText("\n".join(lines))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(good))

    def entries(self) -> list:
        return [p.entry for p in self.parsed if p.entry]


class BansTab(QWidget):
    _verdict_signal = pyqtSignal(object)     # worker thread -> Qt thread

    def __init__(self, settings_dir: str,
                 punt: Callable[[dict, str], None],
                 announce: Callable[[str], None],
                 log: Optional[Callable[[str], None]] = None,
                 reader: Optional[LocalServerPlayers] = None,
                 clock: Callable[[], float] = time.time,
                 button_cls=QPushButton,
                 admin_name: str = "admin",
                 checker=None,
                 mods: Optional[Callable[[], Iterable[str]]] = None,
                 parent=None):
        super().__init__(parent)
        self._dir = str(settings_dir)
        self._punt, self._announce = punt, announce
        self._log_out = log or (lambda _t: None)
        self._reader = reader if reader is not None else LocalServerPlayers(clock=clock)
        self._clock = clock
        self._button_cls = button_cls
        self.admin_name = admin_name
        self.bans = self._load_bans()
        self.history = self._load_history()
        self.enforcer = Enforcer()
        self.checks, self.verdicts = self._load_checks()
        self._checker = checker if checker is not None else ip_checks.Checker(self._verdict_signal.emit)
        self._verdict_signal.connect(self._on_verdict)
        self._check_acted: dict = {}          # name|ip -> when we last kicked for a connection check
        self._loading_checks = True
        self._mods = mods or (lambda: ())
        self.idle_cfg = self._load_idle()
        self.idle = idle_rules.IdleWatch()
        self._positions: dict = {}
        self._current_map: Optional[str] = None
        self.zones: list = []
        self.game_type: Optional[int] = None              # g_GameType, None when not on this PC
        self.objectives: Optional[tuple] = None           # (done, total) on a co-op map
        self.caps_to_go: Optional[int] = None             # CTF / Flagball: fewest a team still needs
        self.team_caps: Optional[dict] = None             # CTF / Flagball: {1: caps, 2: caps}
        self.ai_left: Optional[tuple] = None              # co-op: (AI alive, AI at mission start)
        self._zone_watch = zone_rules.ZoneWatch()
        self.zone_capture: Optional[Callable] = None      # set by the main window (Sprees tab)
        self._loading_idle = True
        self._ips: dict = {}
        self._online: list = []
        self._chat_initialized = False
        self._seen_chat: set = set()
        self._dirty = False
        self._build_ui()
        self._refresh_tables()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._housekeeping)
        self._timer.start(30_000)

    # ---- files ---------------------------------------------------------------
    def _load_bans(self) -> BanList:
        try:
            with open(os.path.join(self._dir, BANS_FILE), "r", encoding="utf-8") as handle:
                return BanList.from_json(json.load(handle))
        except (OSError, ValueError):
            return BanList()

    def _load_history(self) -> PlayerHistory:
        try:
            with open(os.path.join(self._dir, HISTORY_FILE), "r", encoding="utf-8") as handle:
                return PlayerHistory.from_json(json.load(handle))
        except (OSError, ValueError):
            return PlayerHistory()

    def _load_checks(self):
        try:
            with open(os.path.join(self._dir, CHECKS_FILE), "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return ip_checks.ChecksConfig.from_json(data.get("config")), ip_checks.VerdictCache.from_json(data.get("cache"))

    def _load_idle(self) -> idle_rules.IdleConfig:
        try:
            with open(os.path.join(self._dir, IDLE_FILE), "r", encoding="utf-8") as handle:
                return idle_rules.IdleConfig.from_json(json.load(handle))
        except (OSError, ValueError):
            return idle_rules.IdleConfig()

    def save(self) -> None:
        self.verdicts.dirty = False
        for name, payload in ((BANS_FILE, self.bans.to_json()), (HISTORY_FILE, self.history.to_json()),
                              (CHECKS_FILE, {"config": self.checks.to_json(), "cache": self.verdicts.to_json()}),
                              (IDLE_FILE, self.idle_cfg.to_json())):
            path = os.path.join(self._dir, name)
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                os.replace(tmp, path)
            except OSError as exc:
                self.log(f"Could not save {name}: {exc}")
        self._dirty = False
        self.history.dirty = False

    # ---- UI ------------------------------------------------------------------
    @staticmethod
    def _scrolling(page: QWidget) -> QScrollArea:
        """Small screens scroll the page instead of squashing it (like the other tabs)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self.pages = QTabWidget()
        root.addWidget(self.pages, 1)
        self.pages.addTab(self._scrolling(self._build_bans_page()), "Ban list")
        self.pages.addTab(self._scrolling(self._build_whitelist_page()), "Whitelist")
        self.pages.addTab(self._scrolling(self._build_history_page()), "Player history")
        self.pages.addTab(self._scrolling(self._build_checks_page()), "Connection checks")
        self.pages.addTab(self._scrolling(self._build_idle_page()), "Idle kick")
        self.log_text = QPlainTextEdit(); self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(56)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt; background-color: #0a0a00; "
                                    "color: #a89830; border: 1px solid #3a3a00;")
        root.addWidget(self.log_text)

    def _table(self, headers, stretch_col=None):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setStyleSheet(_TABLE_STYLE)
        # Tables ask for ~250 px each by default; on a 1024x768 desktop that forced
        # the whole page to scroll.  Let the layout give them whatever is left.
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if stretch_col is not None:
            header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
        return table

    def _build_bans_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 2); layout.setSpacing(3)
        help_lbl = QLabel(_HELP_SHORT); help_lbl.setWordWrap(True)
        help_lbl.setToolTip(_HELP)
        help_lbl.setStyleSheet("color: #a89830; font-size: 9pt; padding: 2px 4px;")
        layout.addWidget(help_lbl)
        self.status_lbl = QLabel("IPs: not looked yet.")
        self.status_lbl.setStyleSheet("color: #e8c840; padding: 2px 4px;")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        # -- on the server now
        online_box = QGroupBox("On the server now")
        online_layout = QVBoxLayout(online_box)
        self.online_table = self._table(["Name", "IP", "Connection", "Idle", "Visits", "Other names seen on this IP"], 5)
        self.online_table.setMinimumHeight(84)
        online_layout.addWidget(self.online_table)
        online_row = QHBoxLayout()
        self.ban_name_btn = self._button_cls("Ban name"); self.ban_name_btn.clicked.connect(lambda: self._ban_selected(KIND_NAME))
        self.ban_ip_btn = self._button_cls("Ban IP"); self.ban_ip_btn.clicked.connect(lambda: self._ban_selected(KIND_IP))
        self.ban_both_btn = self._button_cls("Ban name + IP"); self.ban_both_btn.clicked.connect(lambda: self._ban_selected("both"))
        self.online_reason = QLineEdit(); self.online_reason.setPlaceholderText("reason (optional)")
        self.online_expiry = QComboBox()
        for label, _code in EXPIRY_CHOICES:
            self.online_expiry.addItem(label)
        for widget in (self.ban_name_btn, self.ban_ip_btn, self.ban_both_btn):
            online_row.addWidget(widget)
        online_row.addWidget(QLabel("Reason:")); online_row.addWidget(self.online_reason, 1)
        online_row.addWidget(QLabel("For:")); online_row.addWidget(self.online_expiry)
        online_layout.addLayout(online_row)
        splitter.addWidget(online_box)

        # -- the list
        list_box = QGroupBox("Banned  (select a row, then use the buttons or right-click it)")
        list_layout = QVBoxLayout(list_box)
        btn_row = QHBoxLayout()
        self.remove_btn = self._button_cls("Remove from list"); self.remove_btn.clicked.connect(self._remove_selected)
        self.copy_block_btn = self._button_cls("Copy firewall block"); self.copy_block_btn.clicked.connect(lambda: self._copy_firewall(True))
        self.copy_unblock_btn = self._button_cls("Copy firewall unblock"); self.copy_unblock_btn.clicked.connect(lambda: self._copy_firewall(False))
        self.copy_all_btn = self._button_cls("Copy block for every IP"); self.copy_all_btn.clicked.connect(self._copy_all_blocks)
        self.import_btn = self._button_cls("Import..."); self.import_btn.clicked.connect(self._import)
        self.export_btn = self._button_cls("Export..."); self.export_btn.clicked.connect(self._export)
        for widget in (self.remove_btn, self.copy_block_btn, self.copy_unblock_btn, self.copy_all_btn, self.import_btn, self.export_btn):
            btn_row.addWidget(widget)
        btn_row.addStretch()
        list_layout.addLayout(btn_row)
        self.ban_table = self._table(["Kind", "Name / address", "Banned with", "Reason", "By", "Added", "Expires", "Hits", "Last seen trying"], 3)
        self.ban_table.setMinimumHeight(120)
        self.ban_table.itemSelectionChanged.connect(self._sync_buttons)
        self.ban_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ban_table.customContextMenuRequested.connect(self._ban_table_menu)
        list_layout.addWidget(self.ban_table)
        add_row = QHBoxLayout()
        self.add_value = QLineEdit(); self.add_value.setPlaceholderText("name, IP, 82.68.*, 82.68.0.0/16 or a.b.c.d-e.f.g.h")
        self.add_reason = QLineEdit(); self.add_reason.setPlaceholderText("reason")
        self.add_expiry = QComboBox()
        for label, _code in EXPIRY_CHOICES:
            self.add_expiry.addItem(label)
        self.add_btn = self._button_cls("Add"); self.add_btn.clicked.connect(self._add_typed)
        self.add_value.returnPressed.connect(self._add_typed)
        add_row.addWidget(self.add_value, 2); add_row.addWidget(self.add_reason, 2)
        add_row.addWidget(QLabel("For:")); add_row.addWidget(self.add_expiry); add_row.addWidget(self.add_btn)
        list_layout.addLayout(add_row)
        self.announce_cb = QCheckBox("Tell the server in chat when a banned player is removed")
        self.announce_cb.setChecked(True)
        list_layout.addWidget(self.announce_cb)
        splitter.addWidget(list_box)
        splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 2)
        self._sync_buttons()
        return page

    def _build_idle_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Idle kick - remove players who have stopped moving")
        grid = QVBoxLayout(box)
        note = QLabel(
            "Watches each player's position on the map (read from the server on this PC, like the IPs). "
            "Someone who has not moved for the time below gets one chat warning a minute before, then is "
            "kicked with a message everyone sees. Kills do not count - a sniper lying still is playing, "
            "but a sniper who has not moved at all for ten minutes is not. The clock does not run while "
            "a map is loading, and a map change restarts it for everyone.")
        note.setWordWrap(True); note.setStyleSheet("color: #a89830; font-size: 9pt;")
        grid.addWidget(note)
        row = QHBoxLayout()
        self.idle_cb = QCheckBox("Kick players who have not moved for")
        self.idle_minutes = QSpinBox(); self.idle_minutes.setRange(idle_rules.MIN_MINUTES, idle_rules.MAX_MINUTES)
        self.idle_minutes.setSuffix(" min"); self.idle_minutes.setMaximumWidth(90)
        row.addWidget(self.idle_cb); row.addWidget(self.idle_minutes); row.addStretch()
        grid.addLayout(row)
        self.idle_mods_cb = QCheckBox("Never kick names on the Mods tab (the Whitelist page is always exempt)")
        grid.addWidget(self.idle_mods_cb)
        self.idle_status = QLabel(); self.idle_status.setStyleSheet("color: #e8c840;"); self.idle_status.setWordWrap(True)
        grid.addWidget(self.idle_status)
        layout.addWidget(box); layout.addStretch(1)
        self.idle_cb.setChecked(self.idle_cfg.enabled)
        self.idle_minutes.setValue(self.idle_cfg.minutes)
        self.idle_mods_cb.setChecked(self.idle_cfg.exempt_mods)
        self.idle_cb.stateChanged.connect(self._idle_changed)
        self.idle_mods_cb.stateChanged.connect(self._idle_changed)
        self.idle_minutes.valueChanged.connect(self._idle_changed)
        self._loading_idle = False
        self._idle_changed()
        return page

    def _idle_changed(self):
        self.idle_cfg.enabled = self.idle_cb.isChecked()
        self.idle_cfg.minutes = self.idle_minutes.value()
        self.idle_cfg.exempt_mods = self.idle_mods_cb.isChecked()
        if not self.idle_cfg.enabled:
            self.idle_status.setText("Off. The 'Idle' column on the Ban list page still shows how long since each player moved.")
        else:
            self.idle_status.setText(f"On - warning at {self.idle_cfg.minutes - 1} min, kick at {self.idle_cfg.minutes} min"
                                     if self.idle_cfg.minutes > 1 else "On - warning straight away, kick at 1 min")
        if not self._loading_idle:
            self._dirty = True
            self.save()

    def _idle_exempt(self) -> set:
        names = {e.value.lower() for e in self.bans.whitelist if e.kind == KIND_NAME}
        if self.idle_cfg.exempt_mods:
            try:
                names |= {str(n).lower() for n in self._mods()}
            except Exception:
                pass
        return names

    def _idle_tick(self, now: float) -> None:
        for event in self.idle.tick(self.idle_cfg, self._online, self._positions, self._idle_exempt(), now):
            if event.kind == "warn":
                self.log(f"Idle warning to {event.name} ({event.idle_seconds // 60} min without moving)")
                for line in idle_rules.warn_lines(event.name, event.idle_seconds // 60):
                    self._announce(line)
            else:
                self.log(f"Kicked {event.name} for being idle {event.idle_seconds // 60} min")
                try:
                    self._punt(event.player, f"Idle for {event.idle_seconds // 60} min")
                except Exception as exc:
                    self.log(f"Punt failed for {event.name}: {exc}")
                self._announce(idle_rules.kick_text(event.name, self.idle_cfg.minutes))

    def on_missions_updated(self, missions) -> None:
        """A map change restarts every idle clock and the zone watch."""
        current = None
        for m in missions or ():
            if "<CURRENT MISSION>" in str(m):
                current = str(m).split(" - ")[0].strip()
                break
        if current and current != self._current_map:
            self._current_map = current
            self.idle.reset()
            self._zone_watch.reset()

    def ai_left_now(self) -> Optional[tuple]:
        """(alive, at start) co-op AI at the last poll, None off co-op."""
        return self.ai_left

    def team_caps_now(self) -> Optional[dict]:
        """Each team's CTF / Flagball captures at the last poll, None otherwise."""
        return self.team_caps

    def caps_to_go_now(self) -> Optional[int]:
        """CTF flags / Flagball goals the closest team still needs, at the last poll."""
        return self.caps_to_go

    def objectives_now(self) -> Optional[tuple]:
        """(done, total) co-op objectives at the last poll, None off co-op."""
        return self.objectives

    def game_type_now(self) -> Optional[int]:
        """Game type read at the last poll (coop_guard), or None."""
        return self.game_type

    def _ip_of(self, name: str) -> str:
        ip = self._ips.get(name, "")
        if not ip:
            wanted = (name or "").strip().lower()
            ip = next((v for k, v in self._ips.items() if str(k).strip().lower() == wanted), "")
        return ip or ""

    def connection_for(self, name: str) -> tuple:
        """(ip, cached Verdict or None) for someone online - never looks anything up."""
        ip = self._ip_of(name)
        return ip, (self.verdicts.get(ip, self._clock()) if ip else None)

    def look_up(self, name: str) -> None:
        """Queue one connection check for this player on the shared checker, only
        if nothing is cached or already queued (ip-api's 45/min is precious)."""
        ip = self._ip_of(name)
        if ip and not ip_checks.is_private(ip) and self.verdicts.get(ip, self._clock()) is None:
            self._checker.submit(ip, self.checks.provider, self.checks.api_key)

    def returning_player(self, name: str, min_age: float = 600) -> bool:
        """True when the player history already knew this name before this visit -
        a guard so a fresh welcome list doesn't treat regulars as first-timers."""
        rec = self.history.get(name)
        return rec is not None and (rec.visits >= 2 or self._clock() - rec.first_seen > min_age)

    def zones_left(self):
        """(leading team, zones it still needs) on an AAS map, else None."""
        return zone_rules.zones_left(self.zones)

    def _zone_tick(self) -> None:
        teams = {}
        for player in self._online:
            try:
                teams[str(player.get("name", ""))] = int(player.get("team", 0))
            except (TypeError, ValueError):
                pass
        for event in self._zone_watch.update(self.zones, self._positions, teams):
            if isinstance(event, zone_rules.LeadEvent):
                self.log(f"{zone_rules.team_name(event.team)} now lead {event.owned}-{event.total - event.owned}")
            else:
                self.log(f"{zone_rules.team_name(event.team)} took {event.name}"
                         + (f" ({event.player})" if event.player else "") + (" - first zone of the map" if event.first else ""))
            if self.zone_capture is not None:
                try:
                    self.zone_capture(event)
                except Exception as exc:
                    self.log(f"Capture announcement failed: {exc}")

    def _build_checks_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_checks_box())
        layout.addStretch(1)
        return page

    def _build_checks_box(self):
        box = QGroupBox("Connection checks - VPN, proxy, Tor, hosting, country")
        grid = QVBoxLayout(box)
        note = QLabel(
            "Every new address is looked up once (cached a week) and, if it is a VPN, proxy, Tor exit or a "
            "hosting range, WolfRAT can kick or ban it. Real players at home are on ordinary ISPs. Some mobile "
            "networks get flagged, so <b>kick</b> is the safe choice - the log says why, and the Whitelist page "
            "covers anyone you trust. Needs internet from this PC.")
        note.setWordWrap(True); note.setStyleSheet("color: #a89830; font-size: 9pt;")
        grid.addWidget(note)
        row1 = QHBoxLayout()
        self.checks_cb = QCheckBox("Check every connection")
        row1.addWidget(self.checks_cb)
        row1.addWidget(QLabel("Lookup service:"))
        self.provider_combo = QComboBox()
        for _code, label in ip_checks.PROVIDERS:
            self.provider_combo.addItem(label)
        row1.addWidget(self.provider_combo)
        self.key_lbl = QLabel("Key:")
        self.key_edit = QLineEdit(); self.key_edit.setPlaceholderText("proxycheck.io key (optional, 1000/day free)")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        row1.addWidget(self.key_lbl); row1.addWidget(self.key_edit, 1)
        grid.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("VPN / proxy / Tor:"))
        self.proxy_action = QComboBox(); self.proxy_action.addItems(ip_checks.ACTION_LABELS); row2.addWidget(self.proxy_action)
        row2.addWidget(QLabel("   Hosting / datacentre:"))
        self.hosting_action = QComboBox(); self.hosting_action.addItems(ip_checks.ACTION_LABELS); row2.addWidget(self.hosting_action)
        row2.addStretch()
        grid.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Countries:"))
        self.country_mode = QComboBox(); self.country_mode.addItems(["ignore", "block these", "allow only these"]); row3.addWidget(self.country_mode)
        self.countries_edit = QLineEdit(); self.countries_edit.setPlaceholderText("two-letter codes, e.g. GB, IE, US")
        row3.addWidget(self.countries_edit, 1)
        row3.addWidget(QLabel("then:"))
        self.country_action = QComboBox(); self.country_action.addItems(ip_checks.ACTION_LABELS); row3.addWidget(self.country_action)
        grid.addLayout(row3)
        row4 = QHBoxLayout()
        self.probe_edit = QLineEdit(); self.probe_edit.setPlaceholderText("try an address, e.g. 8.8.8.8")
        self.probe_btn = self._button_cls("Check it"); self.probe_btn.clicked.connect(self._probe)
        self.probe_edit.returnPressed.connect(self._probe)
        self.checks_status = QLabel("Off."); self.checks_status.setStyleSheet("color: #e8c840;")
        row4.addWidget(self.probe_edit, 1); row4.addWidget(self.probe_btn); row4.addWidget(self.checks_status, 2)
        grid.addLayout(row4)
        # load config into the widgets
        cfg = self.checks
        self.checks_cb.setChecked(cfg.enabled)
        self.provider_combo.setCurrentIndex([c for c, _l in ip_checks.PROVIDERS].index(cfg.provider))
        self.key_edit.setText(cfg.api_key)
        self.proxy_action.setCurrentIndex(cfg.action_proxy)
        self.hosting_action.setCurrentIndex(cfg.action_hosting)
        self.country_mode.setCurrentIndex({ip_checks.COUNTRY_OFF: 0, ip_checks.COUNTRY_BLOCK: 1, ip_checks.COUNTRY_ALLOW: 2}[cfg.country_mode])
        self.countries_edit.setText(", ".join(cfg.countries))
        self.country_action.setCurrentIndex(cfg.action_country)
        for widget in (self.checks_cb,):
            widget.stateChanged.connect(self._checks_changed)
        for widget in (self.provider_combo, self.proxy_action, self.hosting_action, self.country_mode, self.country_action):
            widget.currentIndexChanged.connect(self._checks_changed)
        for widget in (self.key_edit, self.countries_edit):
            widget.editingFinished.connect(self._checks_changed)
        self._loading_checks = False
        self._checks_changed()
        return box

    def _checks_changed(self):
        cfg = self.checks
        cfg.enabled = self.checks_cb.isChecked()
        cfg.provider = ip_checks.PROVIDERS[self.provider_combo.currentIndex()][0]
        cfg.api_key = self.key_edit.text().strip()
        cfg.action_proxy = self.proxy_action.currentIndex()
        cfg.action_hosting = self.hosting_action.currentIndex()
        cfg.country_mode = [ip_checks.COUNTRY_OFF, ip_checks.COUNTRY_BLOCK, ip_checks.COUNTRY_ALLOW][self.country_mode.currentIndex()]
        cfg.countries = ip_checks.parse_countries(self.countries_edit.text())
        cfg.action_country = self.country_action.currentIndex()
        uses_key = cfg.provider == ip_checks.PROVIDER_PROXYCHECK
        self.key_lbl.setVisible(uses_key); self.key_edit.setVisible(uses_key)
        self.countries_edit.setEnabled(cfg.country_mode != ip_checks.COUNTRY_OFF)
        self.country_action.setEnabled(cfg.country_mode != ip_checks.COUNTRY_OFF)
        self._update_checks_status()
        if not self._loading_checks:
            self._dirty = True
            self.save()

    def _update_checks_status(self):
        if not self.checks.enabled:
            self.checks_status.setText("Off.")
            return
        pending = self._checker.pending()
        self.checks_status.setText(f"On - {ip_checks.PROVIDERS[self.provider_combo.currentIndex()][1].split(' (')[0]}"
                                   + (f", {pending} lookup(s) waiting" if pending else ""))

    def _probe(self):
        ip = self.probe_edit.text().strip()
        if not ip:
            return
        if ip_checks.is_private(ip):
            self.log(f"{ip} is a private address - nothing to look up."); return
        self.verdicts.forget(ip)
        if self._checker.submit(ip, self.checks.provider, self.checks.api_key):
            self.log(f"Looking up {ip}...")
        self._update_checks_status()

    # ---- verdicts -------------------------------------------------------------
    def _on_verdict(self, verdict):
        self.verdicts.put(verdict)
        self.log(f"Connection check {verdict.ip}: {verdict.summary()}")
        self._act_on_checks()
        self._enforce(self._clock())          # a fresh connection-check ban removes them now, not next poll
        self._refresh_online()
        self._refresh_ban_table(); self._refresh_white_table()
        if self.pages.currentIndex() == 2:
            self._refresh_history()
        self._update_checks_status()
        self.save()

    def _with_country(self, ip: str) -> str:
        """'GB 82.68.58.92' when a connection check has told us the country."""
        if not ip:
            return "-"
        v = self.verdicts.get(ip, self._clock())
        return f"{v.country} {ip}" if v is not None and v.country else ip

    def _verdict_text(self, ip: str) -> str:
        if not ip:
            return "-"
        v = self.verdicts.get(ip, self._clock())
        if v is None:
            return "checking..." if self.checks.enabled else "-"
        return v.summary()

    def _act_on_checks(self):
        """Kick or ban anyone online whose cached verdict says so.  Runs on every
        poll and again when a lookup lands."""
        if not self.checks.enabled:
            return
        now = self._clock()
        present = set()
        for player in self._online:
            name = str(player.get("name", ""))
            ip = self._ips.get(name, "")
            if not ip or ip_checks.is_private(ip):
                continue
            mark = f"{name.lower()}|{ip}"
            present.add(mark)
            verdict = self.verdicts.get(ip, now)
            if verdict is None:
                self._checker.submit(ip, self.checks.provider, self.checks.api_key)
                continue
            outcome = ip_checks.decide(verdict, self.checks)
            if outcome.action == ip_checks.ACTION_NONE:
                continue
            if br.find_match(self.bans.whitelist, name, ip, now) is not None:
                continue
            if mark in self._check_acted and now - self._check_acted[mark] < br.PUNT_COOLDOWN_SECONDS:
                continue
            self._check_acted[mark] = now
            if outcome.action == ip_checks.ACTION_BAN:
                if self.bans.check(name, ip, now) is None:
                    self.bans.add(BanEntry(KIND_IP, ip, f"connection check: {outcome.reason}", "connection check",
                                           now, None, linked=name))
                    self.log(f"Banned ip {ip} ({name}) - {outcome.reason}")
                    self._dirty = True
                    self._refresh_ban_table()
                continue                     # the ban list removes them on this or the next poll
            self.log(f"Kicked {name} ({ip}) - {outcome.reason}")
            try:
                self._punt(dict(player), f"Kicked: {outcome.reason}"[:60])
            except Exception as exc:
                self.log(f"Punt failed for {name}: {exc}")
            if self.announce_cb.isChecked():
                self._announce(f"{name} removed - {outcome.reason}"[:MAX_CHAT_LEN])
        self._check_acted = {k: t for k, t in self._check_acted.items() if k in present}

    def _build_whitelist_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Names and addresses here are never removed by the ban list, even if a range covers them. "
                      "Put your admins and regulars here before adding wide ranges.")
        note.setWordWrap(True); note.setStyleSheet("color: #a89830; padding: 4px;")
        layout.addWidget(note)
        self.white_table = self._table(["Kind", "Name / address", "Note", "By", "Added"], 2)
        self.white_table.setMinimumHeight(220)
        layout.addWidget(self.white_table, 1)
        row = QHBoxLayout()
        self.white_value = QLineEdit(); self.white_value.setPlaceholderText("name or IP pattern")
        self.white_note = QLineEdit(); self.white_note.setPlaceholderText("note")
        add = self._button_cls("Add to whitelist"); add.clicked.connect(self._add_white)
        self.white_value.returnPressed.connect(self._add_white)
        rem = self._button_cls("Remove"); rem.clicked.connect(self._remove_white)
        row.addWidget(self.white_value, 2); row.addWidget(self.white_note, 2); row.addWidget(add); row.addWidget(rem)
        layout.addLayout(row)
        return page

    def _build_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("search a name or part of an IP")
        self.search.textChanged.connect(self._refresh_history)
        top.addWidget(self.search, 1)
        self.hist_ban_name_btn = self._button_cls("Ban this name"); self.hist_ban_name_btn.clicked.connect(lambda: self._ban_from_history(KIND_NAME))
        self.hist_ban_ip_btn = self._button_cls("Ban last IP"); self.hist_ban_ip_btn.clicked.connect(lambda: self._ban_from_history(KIND_IP))
        top.addWidget(self.hist_ban_name_btn); top.addWidget(self.hist_ban_ip_btn)
        layout.addLayout(top)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.hist_table = self._table(["Name", "Last IP", "IPs", "Visits", "First seen", "Last seen"], 0)
        self.hist_table.setMinimumHeight(260)
        self.hist_table.itemSelectionChanged.connect(self._show_history_detail)
        splitter.addWidget(self.hist_table)
        self.hist_detail = QPlainTextEdit(); self.hist_detail.setReadOnly(True)
        self.hist_detail.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt; background: #0a0a00; color: #d0c060;")
        splitter.addWidget(self.hist_detail)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        return page

    # ---- feeding --------------------------------------------------------------
    def on_players(self, players: list) -> None:
        """Every admin-port poll.  Reads IPs, records history, punts matches."""
        now = self._clock()
        slots = self._reader.read()
        self._ips = ips_by_name(slots)
        self._positions = positions_by_name(slots)
        self._online = list(players or [])
        self.zones = self._reader.read_zones() if hasattr(self._reader, "read_zones") else []
        self.game_type = (self._reader.read_game_type()
                          if hasattr(self._reader, "read_game_type") and self._reader.available else None)
        self.objectives = (self._reader.read_objectives()
                           if coop_guard.is_coop(self.game_type) and hasattr(self._reader, "read_objectives")
                           else None)
        self.ai_left = (self._reader.read_ai_left()
                        if coop_guard.is_coop(self.game_type) and hasattr(self._reader, "read_ai_left")
                        else None)
        self.team_caps = (self._reader.read_team_caps(self.game_type)
                          if self.game_type is not None and hasattr(self._reader, "read_team_caps")
                          else None)
        self.caps_to_go = (self._reader.read_caps_to_go(self.game_type)
                           if self.game_type is not None and hasattr(self._reader, "read_caps_to_go")
                           else None)
        self.status_lbl.setText(("IPs: " + self._reader.status) if not self._reader.available
                                else f"IPs: reading from the server on this PC ({len(self._ips)} known)")
        self.history.observe(self._online, self._ips, now)
        self._act_on_checks()
        self._enforce(now)
        self._idle_tick(now)
        self._zone_tick()
        self._refresh_online()
        self._update_checks_status()
        if self._dirty or self.history.dirty or self.verdicts.dirty:
            self._refresh_ban_table()
            self.save()

    def _enforce(self, now: float) -> None:
        """Punt everyone online who trips the ban list (once per visit)."""
        for removal in self.enforcer.decide(self.bans, self._online, self._ips, now):
            why = removal.why()
            self.log(f"Removed {why}")
            try:
                self._punt(removal.player, f"Banned: {removal.entry.reason or removal.entry.value}")
            except Exception as exc:
                self.log(f"Punt failed for {removal.name}: {exc}")
            if self.announce_cb.isChecked():
                text = f"{removal.name} removed - banned"
                if removal.entry.reason:
                    text += f": {removal.entry.reason}"
                self._announce(text[:MAX_CHAT_LEN])
            self._dirty = True

    def on_chat(self, messages: list) -> None:
        if not self._chat_initialized:
            self._chat_initialized = True
            self._seen_chat = {m.get("id", m.get("raw", "")) for m in messages}
            return
        now = self._clock()
        for msg in messages:
            key = msg.get("id", msg.get("raw", ""))
            if not key or key in self._seen_chat:
                continue
            self._seen_chat.add(key)
            text = str(msg.get("text", ""))
            if ":" in text:
                who, said = text.split(":", 1)
                self.history.chat(who.strip(), said.strip(), now)
        if len(self._seen_chat) > 1000:
            self._seen_chat = set(list(self._seen_chat)[-500:])

    # ---- public actions (mods' !ban, Players tab) -----------------------------
    def ip_of(self, name: str) -> str:
        return self._ips.get(name, "") or (self.history.get(name).last_ip() if self.history.get(name) else "")

    def ban_now(self, name: str, *, reason: str = "", added_by: str = "", kind: str = "both",
                expiry: str = "") -> list:
        """Add name and/or its IP to the list.  Returns the entries added.  The
        next poll removes them; callers wanting an instant punt do it themselves."""
        now = self._clock()
        expires = br.parse_expiry(expiry, now) if expiry else None
        added = []
        ip = self.ip_of(name)
        if kind in ("both", KIND_NAME) and name:
            entry = BanEntry(KIND_NAME, name, reason, added_by or self.admin_name, now, expires, linked=ip)
            self.bans.add(entry); added.append(entry)
        if kind in ("both", KIND_IP):
            if ip:
                entry = BanEntry(KIND_IP, ip, reason or f"IP of {name}", added_by or self.admin_name, now, expires,
                                 linked=name)
                self.bans.add(entry); added.append(entry)
            elif kind == KIND_IP:
                self.log(f"No IP known for {name} - cannot IP-ban (is the server on this PC?)")
        for entry in added:
            self.log(f"Banned {entry.kind} {entry.value}" + (f" ({entry.linked})" if entry.linked else "")
                     + (f" - {entry.reason}" if entry.reason else "")
                     + f" (by {entry.added_by}, until {br.format_expiry(entry.expires_at)})")
        self._dirty = True
        self._refresh_ban_table()
        self.save()
        return added

    def unban(self, value: str) -> int:
        """Remove every entry matching this name or address text.  Returns how many."""
        count = 0
        for kind in (KIND_NAME, KIND_IP):
            if self.bans.remove(kind, value):
                count += 1
        if count:
            self.log(f"Unbanned {value}")
            self._dirty = True
            self._refresh_ban_table(); self.save()
        return count

    # ---- button handlers ----------------------------------------------------------
    def _expiry_code(self, combo: QComboBox) -> str:
        return EXPIRY_CHOICES[combo.currentIndex()][1]

    def _selected_online(self) -> Optional[dict]:
        row = self.online_table.currentRow()
        if row < 0 or row >= len(self._online):
            return None
        return self._online[row]

    def _ban_selected(self, kind):
        player = self._selected_online()
        if player is None:
            self.log("Pick a player in the 'On the server now' table first.")
            return
        name = str(player.get("name", ""))
        added = self.ban_now(name, reason=self.online_reason.text().strip(), kind=kind,
                             expiry=self._expiry_code(self.online_expiry))
        if added:
            self.enforcer.note_punted(name, self.ip_of(name), self._clock())
            try:
                self._punt(player, f"Banned: {self.online_reason.text().strip() or name}")
            except Exception as exc:
                self.log(f"Punt failed for {name}: {exc}")

    def _add_typed(self):
        value = self.add_value.text().strip()
        if not value:
            return
        rows = br.parse_import(f"{value} | {self.add_reason.text().strip().replace('|', '/')} | "
                               f"{self._expiry_code(self.add_expiry) or 'never'}",
                               now=self._clock(), added_by=self.admin_name)
        if not rows or rows[0].entry is None:
            self.log(f"Not added: {rows[0].problem if rows else 'nothing to add'}")
            return
        self.bans.add(rows[0].entry)
        e = rows[0].entry
        self.log(f"Banned {e.kind} {e.value}" + (f" - {e.reason}" if e.reason else ""))
        self.add_value.clear(); self.add_reason.clear()
        self._dirty = True
        self._refresh_ban_table(); self.save()

    def _ban_table_menu(self, pos):
        row = self.ban_table.rowAt(pos.y())
        if row < 0:
            return
        self.ban_table.selectRow(row)
        entry = self._selected_entry()
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction(f"Remove {entry.value} from the ban list", self._remove_selected)
        if entry.kind == KIND_IP:
            menu.addAction("Copy firewall block line", lambda: self._copy_firewall(True))
            menu.addAction("Copy firewall unblock line", lambda: self._copy_firewall(False))
        menu.exec(self.ban_table.viewport().mapToGlobal(pos))

    def _selected_entry(self) -> Optional[BanEntry]:
        row = self.ban_table.currentRow()
        entries = self._sorted_entries()
        return entries[row] if 0 <= row < len(entries) else None

    def _remove_selected(self):
        entry = self._selected_entry()
        if entry is None:
            return
        self.bans.remove(entry.kind, entry.value)
        self.log(f"Removed {entry.kind} {entry.value} from the ban list")
        self._dirty = True
        self._refresh_ban_table(); self.save()

    def _copy_firewall(self, block: bool):
        entry = self._selected_entry()
        if entry is None or entry.kind != KIND_IP:
            self.log("Pick an IP entry first - names have no firewall line.")
            return
        text = br.firewall_block_command(entry.value) if block else br.firewall_unblock_command(entry.value)
        QApplication.clipboard().setText(text)
        self.log(f"Copied the firewall {'block' if block else 'unblock'} line for {entry.value} - "
                 "paste it into PowerShell run as administrator on the server PC.")

    def _copy_all_blocks(self):
        ips = [e for e in self.bans.entries if e.kind == KIND_IP and e.is_live(self._clock())]
        if not ips:
            self.log("No IP entries to block.")
            return
        QApplication.clipboard().setText("\n".join(br.firewall_block_command(e.value) for e in ips) + "\n")
        self.log(f"Copied firewall block lines for {len(ips)} address(es).")

    def _import(self):
        dialog = ImportDialog(self._clock(), self.admin_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = sum(1 for entry in dialog.entries() if self.bans.add(entry))
        total = len(dialog.entries())
        self.log(f"Imported {total} entr{'y' if total == 1 else 'ies'} ({new} new, {total - new} updated).")
        self._dirty = True
        self._refresh_ban_table(); self.save()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export ban list", "wolfrat_bans.txt", "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(br.export_lines(self.bans.entries))
            self.log(f"Exported {len(self.bans.entries)} entries to {path}")
        except OSError as exc:
            self.log(f"Export failed: {exc}")

    def _add_white(self):
        value = self.white_value.text().strip()
        if not value:
            return
        rows = br.parse_import(f"{value} | {self.white_note.text().strip().replace('|', '/')}",
                               now=self._clock(), added_by=self.admin_name)
        if not rows or rows[0].entry is None:
            self.log(f"Not added: {rows[0].problem if rows else 'nothing to add'}")
            return
        self.bans.add(rows[0].entry, whitelist=True)
        self.log(f"Whitelisted {rows[0].entry.kind} {rows[0].entry.value}")
        self.white_value.clear(); self.white_note.clear()
        self._dirty = True
        self._refresh_white_table(); self.save()

    def _remove_white(self):
        row = self.white_table.currentRow()
        if 0 <= row < len(self.bans.whitelist):
            entry = self.bans.whitelist[row]
            self.bans.remove(entry.kind, entry.value, whitelist=True)
            self.log(f"Removed {entry.value} from the whitelist")
            self._dirty = True
            self._refresh_white_table(); self.save()

    def _ban_from_history(self, kind):
        row = self.hist_table.currentRow()
        records = self.history.search(self.search.text())
        if not (0 <= row < len(records)):
            return
        rec = records[row]
        self.ban_now(rec.name, reason="from player history", kind=kind)

    # ---- tables -------------------------------------------------------------------
    def _sorted_entries(self) -> list:
        return sorted(self.bans.entries, key=lambda e: e.added_at, reverse=True)

    def _refresh_tables(self):
        self._refresh_online(); self._refresh_ban_table(); self._refresh_white_table(); self._refresh_history()

    def _refresh_online(self):
        table = self.online_table
        table.setRowCount(len(self._online))
        for row, player in enumerate(self._online):
            name = str(player.get("name", ""))
            ip = self._ips.get(name, "")
            rec = self.history.get(name)
            others = [r.name for r in self.history.by_ip(ip) if r.name.lower() != name.lower()] if ip else []
            verdict_text = self._verdict_text(ip)
            idle = self.idle.idle_seconds(name, self._clock())
            idle_text = "-" if idle is None else (f"{idle // 60}:{idle % 60:02d}" if idle >= 60 else f"{idle}s")
            cells = [name, self._with_country(ip), verdict_text, idle_text, str(rec.visits) if rec else "-",
                     ", ".join(others[:6]) or "-"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 5 and others:
                    item.setForeground(QColor("#ff9040"))
                if col == 2 and verdict_text not in ("-", "checking...") and not verdict_text.startswith("clear"):
                    item.setForeground(QColor("#ff6040"))
                if col == 3 and idle is not None and self.idle_cfg.enabled and idle >= (self.idle_cfg.minutes - 1) * 60:
                    item.setForeground(QColor("#ff6040"))
                table.setItem(row, col, item)

    def _refresh_ban_table(self):
        now = self._clock()
        entries = self._sorted_entries()
        table = self.ban_table
        table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            last = "-"
            if e.last_hit_at:
                last = f"{e.last_hit_name} {e.last_hit_ip} {describe_when(e.last_hit_at, now)}".strip()
            cells = [e.kind, self._with_country(e.value) if e.kind == KIND_IP else e.value, e.linked or "-", e.reason, e.added_by,
                     time.strftime("%Y-%m-%d", time.localtime(e.added_at)) if e.added_at else "-",
                     br.format_expiry(e.expires_at), str(e.hits), last]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if not e.is_live(now):
                    item.setForeground(QColor("#606040"))
                table.setItem(row, col, item)
        self._sync_buttons()

    def _refresh_white_table(self):
        table = self.white_table
        table.setRowCount(len(self.bans.whitelist))
        for row, e in enumerate(self.bans.whitelist):
            cells = [e.kind, self._with_country(e.value) if e.kind == KIND_IP else e.value, e.reason, e.added_by,
                     time.strftime("%Y-%m-%d", time.localtime(e.added_at)) if e.added_at else "-"]
            for col, text in enumerate(cells):
                table.setItem(row, col, QTableWidgetItem(text))

    def _refresh_history(self):
        now = self._clock()
        records = self.history.search(self.search.text())
        table = self.hist_table
        table.setRowCount(len(records))
        for row, rec in enumerate(records):
            cells = [rec.name, self._with_country(rec.last_ip()), str(len(rec.ips)), str(rec.visits),
                     describe_when(rec.first_seen, now), describe_when(rec.last_seen, now)]
            for col, text in enumerate(cells):
                table.setItem(row, col, QTableWidgetItem(text))

    def _show_history_detail(self):
        row = self.hist_table.currentRow()
        records = self.history.search(self.search.text())
        if not (0 <= row < len(records)):
            self.hist_detail.clear(); return
        rec = records[row]
        now = self._clock()
        lines = [f"{rec.name}", f"visits: {rec.visits}   first: {describe_when(rec.first_seen, now)}   last: {describe_when(rec.last_seen, now)}", ""]
        lines.append("IPs:")
        for ip, (count, seen) in sorted(rec.ips.items(), key=lambda kv: kv[1][1], reverse=True):
            others = [r.name for r in self.history.by_ip(ip) if r.name.lower() != rec.name.lower()]
            lines.append(f"  {self._with_country(ip)}  last {describe_when(seen, now)}" + (f"   also: {', '.join(others[:8])}" if others else ""))
        lines.append("")
        lines.append("Chat:" if rec.chat else "Chat: nothing recorded yet")
        for stamp, text in list(rec.chat):          # every kept line (200), Dale 2026-09-22
            lines.append(f"  [{time.strftime('%d %b %H:%M', time.localtime(stamp))}] {text}")
        self.hist_detail.setPlainText("\n".join(lines))

    def _sync_buttons(self):
        entry = self._selected_entry()
        is_ip = entry is not None and entry.kind == KIND_IP
        self.copy_block_btn.setEnabled(is_ip)
        self.copy_unblock_btn.setEnabled(is_ip)
        self.remove_btn.setEnabled(entry is not None)

    def _housekeeping(self):
        gone = self.bans.purge_expired(self._clock())
        if gone:
            self.log(f"{gone} expired ban(s) dropped off the list")
            self._dirty = True
            self._refresh_ban_table()
        if self._dirty or self.history.dirty or self.verdicts.dirty:
            self.save()
        if self.pages.currentIndex() == 2:
            self._refresh_history()

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(self._clock()))
        self.log_text.appendPlainText(f"[{stamp}] {text}")
        self._log_out(text)
