"""The "Live patches" page on the Server tab (Dale, 2026-10-09).

Changes WolfRAT writes into the RUNNING game server (see live_patches).  The
page says plainly at the top what that means: the exe on disk is untouched,
players need nothing, and a server restart or crash takes them away.  The
permanent versions live on the Server patcher page.

Works on the Weather tab's server handle: the Weather tab calls
``server_tick`` every poll (5 s) and ``no_server`` when there is none.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from wolfrat import live_patches as lp
from wolfrat.weather_wildlife_page import scroll_column

BANNER = (
    "<b>⚡ LIVE PATCHES</b> - these go into the game server <b>while it runs</b>."
    "<br>• Your server exe on disk is <b>not touched</b>, and players need nothing."
    "<br>• A map change keeps them."
    "<br>• They are <b>gone when the server restarts or crashes</b>."
    "<br>Each patch below says whether a permanent version exists (Server patcher page)."
    "<br>The <b>Patches</b> page lists them too, marked ⚡ LIVE."
)

NO_SERVER = ("Live patches need WolfRAT on the same PC as the game server, connected to it "
             "(Server connection page).")

_HELP = (
    "<b>Spacebar parachute.</b> In the normal game a parachute opens by itself after about "
    "a second of free fall. With this on, the player opens it: <b>hold space while "
    "falling</b>. It works after about half a second of fall, so an ordinary jump with "
    "space held never wastes the chute."
    "<br><br><b>Players still need a parachute in their kit</b>, the same as today. No "
    "parachute, nothing to open."
    "<br><br><b>No safety net.</b> No space, no parachute: players learn to press it."
    "<br><br><b>Nobody downloads anything.</b> The server decides when a parachute opens and "
    "tells every player, so normal game clients see it straight away."
    "<br><br><b>Good to know</b>"
    "<br>• The counter shows how many chutes opened since it was switched on."
    "<br>• Close WolfRAT and the parachute stays on in the server until it restarts. "
    "Open WolfRAT again and the box shows it ticked."
    "<br>• If the server's code at that spot is not what WolfRAT expects (a different exe, "
    "or onHook changed it), WolfRAT leaves it alone and says so."
)


class LivePatchesPanel(QWidget):
    """``log(text)`` writes a line to the Server tab's log; ``poll_now()`` asks
    the Weather tab to look at the server straight away (after a click)."""

    def __init__(self, settings_file: Path, log: Callable[[str], None],
                 poll_now: Optional[Callable[[], None]] = None, parent=None):
        super().__init__(parent)
        self._file = Path(settings_file)
        self._log = log
        self._poll_now = poll_now
        self._chute = lp.ChuteKeeper()
        self._updating = False
        settings = self._load()
        chute = settings.get("parachute") or {}
        self._wish = lp.ChuteWish(bool(chute.get("on", False)))
        self._reapply = bool(settings.get("reapply", False))
        self._waiting_logged = False
        self._waiting = False
        self._build()
        self._show_wish()
        self.no_server(NO_SERVER)

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QHBoxLayout(self)
        left_scroll, left = scroll_column()
        right_scroll, right = scroll_column()
        root.addWidget(left_scroll, 3)
        root.addWidget(right_scroll, 2)

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

        group = QGroupBox("🪂 Spacebar parachute")
        box = QVBoxLayout()
        self.chute_cb = QCheckBox("Players open their parachute with the space bar "
                                  "(instead of it opening by itself)")
        box.addWidget(self.chute_cb)
        only_live = QLabel("Live only - there is no permanent version in the Server patcher.")
        only_live.setStyleSheet("font-size: 9pt; color: #d09030;")
        box.addWidget(only_live)
        self.chute_lbl = QLabel()
        self.chute_lbl.setWordWrap(True)
        self.chute_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px 0;")
        box.addWidget(self.chute_lbl)
        group.setLayout(box)
        left.addWidget(group)

        restart_group = QGroupBox("After a server restart")
        restart_box = QVBoxLayout()
        self.reapply_cb = QCheckBox("Put the live patches back on by themselves")
        restart_box.addWidget(self.reapply_cb)
        hint = QLabel("Off: after a restart the server is back to normal and the boxes "
                      "untick - tick them again to put them back. On: WolfRAT puts them back "
                      "as soon as it finds the new server (WolfRAT must be running).")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 9pt; color: #a89830;")
        restart_box.addWidget(hint)
        restart_group.setLayout(restart_box)
        left.addWidget(restart_group)
        left.addStretch()

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

        self.chute_cb.toggled.connect(self._chute_clicked)
        self.reapply_cb.toggled.connect(self._reapply_clicked)

    def _show_wish(self):
        self._updating = True
        try:
            self.chute_cb.setChecked(self._wish.on)
            self.reapply_cb.setChecked(self._reapply)
        finally:
            self._updating = False

    def _set_available(self, available: bool):
        self.chute_cb.setEnabled(available)

    # ------------------------------------------------------------ clicks
    def _chute_clicked(self, *_):
        if self._updating:
            return
        self._wish = lp.ChuteWish(self.chute_cb.isChecked())
        self._chute.changed()
        self._waiting_logged = False
        self._save()
        self.chute_lbl.setText("Writing to the server...")
        if self._poll_now:
            self._poll_now()

    def _reapply_clicked(self, on: bool):
        if self._updating:
            return
        self._reapply = bool(on)
        self._save()
        self._log("Live patches: " + ("put back by themselves after a server restart."
                                      if on else "stay off after a server restart."))

    # ---------------------------------------------- called by the Weather tab
    def wants_write(self) -> bool:
        return self._chute.dirty or self._waiting or (self._wish.on and self._reapply)

    def no_server(self, text: str):
        self._set_available(False)
        self.server_lbl.setText("⚪ " + text)
        self.chute_lbl.setText("")

    def server_tick(self, memory, writable: bool):
        if memory is None:
            self.no_server(NO_SERVER)
            return
        pid = getattr(memory, "pid", "?")
        result = self._chute.tick(memory, self._wish, writable=writable, reapply=self._reapply)
        self._waiting = result.waiting
        recognised = result.state.kind != lp.UNKNOWN
        self._set_available(recognised)
        self.server_lbl.setText(f"🟢 Game server on this PC (process {pid})."
                                + ("" if writable else " WolfRAT can read it; it asks for write "
                                   "access when you tick something."))
        if result.wish != self._wish:
            self._wish = result.wish
            self._save()
            self._show_wish()
        if result.note:
            self._log(result.note)
        mark = "🟢 " if result.state.on else ("⚪ " if recognised else "⚠️ ")
        text = mark + lp.describe_chute(result.state)
        if result.waiting:
            text += "  (waiting for write access to the server...)"
            if not self._waiting_logged:
                self._waiting_logged = True
                self._log("Live patches: waiting for write access to the server "
                          "(run WolfRAT as administrator if this does not clear).")
        self.chute_lbl.setText(text)

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
        data = {"parachute": {"on": self._wish.on},
                "reapply": self._reapply}
        try:
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Live patches: could not save settings ({exc}).")
