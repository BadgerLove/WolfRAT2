"""The Patcher page on the Server tab: tick the patches, WolfRAT writes a
patched copy of the server's exe beside it (see server_patcher).  The running
exe is never touched; the admin stops the server and renames the copy."""

from __future__ import annotations

import html
import os
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout,
                             QLabel, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from wolfrat import server_patcher as P
from wolfrat import server_patches as sp
from wolfrat.protocol import wire_log

_TEXT = "#c8b040"
_NOTE = "color: #a89830; font-size: 8pt;"
_WARN = "color: #ffa030; font-size: 8pt;"

FPS_TEXT = "Frame rate lock (~125 FPS)"
SPAWN_TEXT = "Spawn protection set in game.cfg"
FPS_NOTE = ("Set lock_framerate = 7 in game.cfg (with the server stopped). Without it the "
            "server runs one CPU core at 100%.")


def spawn_in_cfg(cfg: dict) -> str:
    """What game.cfg says, as it says it (Dale: the patched server writes 0 at
    first, so an admin sees it and knows to change it)."""
    value = sp._cfg_int(cfg, "e_spawn_protection")
    return "not in game.cfg yet" if value is None else f"game.cfg says {value} seconds"
ADMIN_FIX_HAVE = ("Already in your server. WolfRAT keeps it in every exe it builds: the other "
                  "patches are no use on a server that falls over.")
ADMIN_FIX_MISSING = ("Your server does not have this yet, so it can crash when admin tools such as "
                     "WolfRAT connect (the 'empty server' SYSDUMP). WolfRAT always adds it and will "
                     "not build a server exe without it - the other patches are no use on a server "
                     "that falls over.")
ADMIN_FIX_AT_START = ("Your server adds this when it starts, but the exe itself does not carry it. "
                      "WolfRAT puts it in the exe too, so it is there whatever starts the server. "
                      "Nothing clashes: the start-up fix sees it is already in and leaves it.")
NOT_KNOWN = ("WolfRAT does not recognise this exe, so it will not patch it. It knows Joint Ops "
             "1.7.5.7 server exes (and mods that use the same exe).")
NO_SERVER = ("No game server found on this PC. Start the server (WolfRAT finds its exe), or press "
             "Choose exe... to pick a jointops.exe.")


def _note(text: str = "", style: str = _NOTE) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(style)
    return label


def _doc(name: str) -> QLabel:
    link = QLabel(f'<a href="{sp.DOCS}{name}" style="color:#5090ff">?</a>')
    link.setOpenExternalLinks(True)
    link.setToolTip("What is this?")
    return link


class ServerPatcherPanel(QWidget):
    """``exe_path()`` -> the running server's jointops.exe, or "" if none."""

    def __init__(self, exe_path: Callable[[], str], live: Optional[sp.LiveRead] = None, parent=None):
        super().__init__(parent)
        self._exe_path = exe_path
        self._live = live
        self._chosen = ""                 # picked with Choose exe..., wins over the running one
        self.path = ""
        self.state: Optional[P.State] = None
        self.result: Optional[P.Result] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        page = QVBoxLayout(body)

        # which exe
        top = QHBoxLayout()
        self.path_lbl = QLabel()
        self.path_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.path_lbl.setStyleSheet(f"color: {_TEXT};")
        top.addWidget(self.path_lbl, 1)
        self.choose_btn = QPushButton("Choose exe...")
        self.choose_btn.clicked.connect(self._choose)
        top.addWidget(self.choose_btn)
        self.running_btn = QPushButton("Use the running server")
        self.running_btn.clicked.connect(self._use_running)
        top.addWidget(self.running_btn)
        page.addLayout(top)
        self.status_lbl = _note()
        page.addWidget(self.status_lbl)

        columns = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        page.addLayout(columns)

        always = QGroupBox("Always included")
        box = QVBoxLayout(always)
        row = QHBoxLayout()
        self.admin_box = QCheckBox("Admin-port crash fix")
        self.admin_box.setChecked(True)
        self.admin_box.setEnabled(False)
        row.addWidget(self.admin_box)
        row.addStretch(1)
        row.addWidget(_doc("01-admin-port-crash-fix.md"))
        box.addLayout(row)
        self.admin_note = _note()
        box.addWidget(self.admin_note)
        left.addWidget(always)

        speed = QGroupBox("Server speed")
        box = QVBoxLayout(speed)
        row = QHBoxLayout()
        self.speed_group = QButtonGroup(self)
        self.speed_btns = {}
        for hz in P.SPEEDS:
            btn = QRadioButton(f"{hz} Hz")
            self.speed_group.addButton(btn, hz)
            self.speed_btns[hz] = btn
            row.addWidget(btn)
        row.addStretch(1)
        row.addWidget(_doc("07-packet-send-rate.md"))
        box.addLayout(row)
        row = QHBoxLayout()
        self.hole_box = QCheckBox("Hole-skip")
        self.hole_box.setEnabled(False)            # follows the speed: in at 64 and 125 Hz
        row.addWidget(self.hole_box)
        row.addStretch(1)
        row.addWidget(_doc("09-nwu-hole-skip.md"))
        box.addLayout(row)
        self.speed_note = _note()
        box.addWidget(self.speed_note)
        self.speed_group.idToggled.connect(self._speed_toggled)
        left.addWidget(speed)

        memory = QGroupBox("Mission memory")
        box = QVBoxLayout(memory)
        row = QHBoxLayout()
        self.memory_group = QButtonGroup(self)
        self.memory_btns = {}
        for mb in P.MEMORY:
            btn = QRadioButton(P.MEMORY_WORDS[mb])
            self.memory_group.addButton(btn, mb)
            self.memory_btns[mb] = btn
            row.addWidget(btn)
        row.addStretch(1)
        row.addWidget(_doc("04-memory-2gb.md"))
        box.addLayout(row)
        box.addWidget(_note("192 MB is the game's normal. Big maps need 512 MB; 1 GB for the largest."))
        self.memory_group.idToggled.connect(self._memory_toggled)
        left.addWidget(memory)
        left.addStretch(1)

        extras = QGroupBox("Patches")
        box = QVBoxLayout(extras)

        def tick(text: str, doc: str) -> QCheckBox:
            line = QHBoxLayout()
            check = QCheckBox(text)
            line.addWidget(check)
            line.addStretch(1)
            line.addWidget(_doc(doc))
            box.addLayout(line)
            return check

        self.fps_box = tick(FPS_TEXT, "11-fps-lock-125.md")
        self.fps_note = _note()
        box.addWidget(self.fps_note)
        self.fps_box.toggled.connect(self._fps_note_follow)
        self.laa_box = tick("Large Address Aware (more than 2 GB)", "05-large-address-aware.md")
        self.kill_box = tick("Kill List crash fix", "14-spectator-chat-server.md")
        # No seconds box: the time lives in game.cfg, which only the admin edits
        # (server stopped).  When the patch is in, the line reads game.cfg back.
        self.spawn_box = tick(SPAWN_TEXT, "12-spawn-protection-config.md")
        self.spectator_box = tick("Spectator chat", "14-spectator-chat-server.md")
        self.chat_box = tick("Long chat (118 characters)", "15-long-chat-server.md")
        line = QHBoxLayout()
        line.addSpacing(22)
        self.colour_box = QCheckBox("with colours")
        line.addWidget(self.colour_box)
        line.addStretch(1)
        box.addLayout(line)
        self.chat_box.toggled.connect(self.colour_box.setEnabled)
        self.extras_note = _note()
        box.addWidget(self.extras_note)
        right.addWidget(extras)
        right.addStretch(1)

        bottom = QHBoxLayout()
        self.build_btn = QPushButton("Build patched copy")
        self.build_btn.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.build_btn.clicked.connect(self.build)
        bottom.addWidget(self.build_btn)
        bottom.addWidget(_note("Your server keeps running: WolfRAT writes a new exe beside it "
                               "and tells you how to swap it in."), 1)
        page.addLayout(bottom)
        self.result_lbl = QLabel()
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.result_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        page.addWidget(self.result_lbl)
        page.addStretch(1)

        self.refresh(force=True)

    # ------------------------------------------------------------------
    def _choose(self):
        start = os.path.dirname(self.path) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Choose the server's exe", start,
                                              "Programs (*.exe)")
        if path:
            self._chosen = os.path.normpath(path)
            self.refresh(force=True)

    def _use_running(self):
        self._chosen = ""
        self.refresh(force=True)

    def _target(self) -> str:
        if self._chosen:
            return self._chosen
        try:
            return self._exe_path() or ""
        except Exception:
            return ""

    def refresh(self, force: bool = False):
        """Re-read the exe.  Keeps the admin's ticks unless the exe changed."""
        path = self._target()
        if path == self.path and not force:
            return
        self.path = path
        self.result = None
        self.result_lbl.setText("")
        self.running_btn.setVisible(bool(self._chosen))
        if not path:
            self.state = None
            self.path_lbl.setText("Server exe: (none)")
            self._set_status(NO_SERVER, warn=True)
            self._set_enabled(False)
            return
        self.path_lbl.setText(f"Server exe: {path}")
        self.path_lbl.setToolTip(path)
        try:
            self.state = P.read_state(path, self._live_for(path))
        except P.PatchError as exc:
            self.state = None
            self._set_status(str(exc), warn=True)
            self._set_enabled(False)
            return
        if not self.state.known:
            self._set_status(NOT_KNOWN, warn=True)
            self._set_enabled(False)
            return
        self._set_status(f"Now: {sp.check(path, self._live_for(path)).headline}", warn=False)
        self._set_enabled(True)
        self._show_state(self.state, P.default_choice(self.state))

    def _live_for(self, path: str) -> Optional[sp.LiveRead]:
        """The running server only counts for its own exe, not a chosen one."""
        return None if self._chosen else self._live

    def _set_status(self, text: str, warn: bool):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(_WARN if warn else _NOTE)

    def _set_enabled(self, on: bool):
        for widget in (list(self.speed_btns.values()) + list(self.memory_btns.values())
                       + [self.fps_box, self.laa_box, self.kill_box, self.spawn_box,
                          self.spectator_box, self.chat_box, self.colour_box,
                          self.build_btn]):
            widget.setEnabled(on)
        if not on:
            self.admin_note.setText(ADMIN_FIX_MISSING)
            self.speed_note.setText("")
            self.extras_note.setText("")

    @staticmethod
    def _already(box: QCheckBox, text: str, have: bool):
        box.setText(text + ("  (already in)" if have else ""))
        if have:
            box.setChecked(True)
        box.setEnabled(not have)

    def _show_state(self, state: P.State, choice: P.Choice):
        have = state.admin_fix or state.admin_fix_live
        self.admin_note.setText(ADMIN_FIX_HAVE if state.admin_fix else
                                ADMIN_FIX_AT_START if have else ADMIN_FIX_MISSING)
        self.admin_note.setStyleSheet(_NOTE if have else _WARN)

        for hz, btn in self.speed_btns.items():
            ok, why = P.speed_allowed(state, hz)
            btn.setEnabled(ok)
            btn.setToolTip(why)
        self.speed_btns[choice.speed].setChecked(True)
        self._speed_toggled(choice.speed, True)

        self.memory_btns[choice.memory].setChecked(True)

        self.fps_box.setChecked(choice.fps_lock)
        self._already(self.fps_box, FPS_TEXT, bool(state.fps_lock))
        self._fps_follow_speed(choice.speed)
        self._fps_note_follow()
        self.laa_box.setChecked(choice.laa)
        self._already(self.laa_box, "Large Address Aware (more than 2 GB)", state.laa)
        self.kill_box.setChecked(choice.kill_list)
        self._already(self.kill_box, "Kill List crash fix", bool(state.kill_list))
        self.spectator_box.setChecked(choice.spectator)
        self._already(self.spectator_box, "Spectator chat", bool(state.spectator))
        self.chat_box.setChecked(choice.long_chat)
        self._already(self.chat_box, "Long chat (118 characters)", state.long_chat in ("plain", "colour"))
        self.colour_box.setChecked(choice.chat_colour)
        self.colour_box.setEnabled(choice.long_chat)

        self.spawn_box.setChecked(choice.spawn)
        self._already(self.spawn_box, SPAWN_TEXT, state.spawn == "config")
        if state.spawn == "config":
            self.spawn_box.setText(f"{SPAWN_TEXT}  (already in: {spawn_in_cfg(state.cfg)})")
        notes = []
        if state.spawn == "fixed":
            self.spawn_box.setChecked(False)
            self.spawn_box.setEnabled(False)
            notes.append("Spawn protection is fixed inside this exe, so it cannot be moved to "
                         "game.cfg. Start from an unpatched exe for that.")
        elif state.spawn == "stock" and state.spawn_live_ticks:
            notes.append(f"Spawn protection is already set to about "
                         f"{state.spawn_live_ticks * 16 / 1000:.1f} s when the server starts. Tick "
                         "this only to move it to game.cfg instead.")
        elif state.spawn == "unknown":
            self.spawn_box.setChecked(False)
            self.spawn_box.setEnabled(False)
        self._memory_toggled(choice.memory, True)
        self.extras_note.setText(" ".join(notes))

    def _fps_note_follow(self, *_):
        """The lock needs lock_framerate = 7 in game.cfg, or the server runs
        flat out on one core (Dale 2026-09-27)."""
        state = self.state
        if state is None or not self.fps_box.isChecked():
            self.fps_note.setText("")
            self.fps_note.setVisible(False)
            return
        self.fps_note.setVisible(True)
        if sp._cfg_int(state.cfg, "lock_framerate") == 7:
            self.fps_note.setText("game.cfg has lock_framerate = 7 (~125 FPS).")
            self.fps_note.setStyleSheet(_NOTE)
        else:
            self.fps_note.setText(FPS_NOTE)
            self.fps_note.setStyleSheet(_WARN)

    def _fps_follow_speed(self, hz: int):
        """125 Hz locks the frame rate lock on (Dale 2026-09-27)."""
        if self.state is None or self.state.fps_lock:
            return                                  # already in: shown as such
        if hz == 125:
            self.fps_box.setChecked(True)
            self.fps_box.setEnabled(False)
            self.fps_box.setText(FPS_TEXT + "  (needed for 125 Hz)")
        else:
            self.fps_box.setEnabled(True)
            self.fps_box.setText(FPS_TEXT)

    def _speed_toggled(self, hz: int, on: bool):
        """Hole-skip comes with 64 and 125 Hz, in the version made for that speed."""
        state = self.state
        if not on or state is None:
            return
        self._fps_follow_speed(hz)
        made_for = sp.HOLE_VERSIONS.get(state.hole_settings) if state.hole_skip else None
        if hz == 32:
            self.hole_box.setChecked(bool(state.hole_skip))
            self.hole_box.setText("Hole-skip  (not needed at 32 Hz)" if not state.hole_skip
                                  else "Hole-skip  (already in, left as it is)")
        else:
            self.hole_box.setChecked(True)
            self.hole_box.setText(f"Hole-skip  ({hz} Hz version)")
        plan = P.hole_plan(state, hz)
        notes = []
        if state.speed in (64, 125) and state.hole_skip is False:
            notes.append(f"Your server is on {state.speed} Hz without hole-skip: players can freeze "
                         "in place when a packet is lost.")
        if plan:
            notes.append(f"WolfRAT adds: {plan}.")
        elif hz != 32 and state.hole_skip and made_for is None:
            notes.append("Hole-skip is in with custom settings; WolfRAT leaves them alone.")
        if not P.speed_allowed(state, hz)[0]:
            notes.append(P.speed_allowed(state, hz)[1])
        if not notes:
            notes.append("Hole-skip stops players freezing in place when a packet is lost "
                         "(64 and 125 Hz need it; 32 Hz does not).")
        self.speed_note.setText(" ".join(notes))
        self.speed_note.setStyleSheet(_WARN if state.hole_skip is False and state.speed in (64, 125)
                                      else _NOTE)

    def _memory_toggled(self, mb: int, on: bool):
        if not on or self.state is None or self.state.laa:
            return
        if mb == 1024:
            self.laa_box.setChecked(True)
        self.laa_box.setEnabled(mb != 1024)

    def choice(self) -> P.Choice:
        return P.Choice(
            speed=self.speed_group.checkedId(),
            memory=self.memory_group.checkedId(),
            laa=self.laa_box.isChecked(),
            fps_lock=self.fps_box.isChecked(),
            spawn=self.spawn_box.isChecked(),
            kill_list=self.kill_box.isChecked(),
            spectator=self.spectator_box.isChecked(),
            long_chat=self.chat_box.isChecked(),
            chat_colour=self.colour_box.isChecked(),
        )

    # ------------------------------------------------------------------
    def build(self):
        if not self.path or self.state is None:
            return
        try:
            self.result = P.build(self.path, self.choice(), self._live_for(self.path))
        except P.PatchError as exc:
            self.result = None
            wire_log(f"[PATCHER] not built: {exc}")
            self.result_lbl.setText(f'<span style="color:#ff6050">{html.escape(str(exc))}</span>')
            return
        result = self.result
        wire_log(f"[PATCHER] built {result.exe_path or '(exe unchanged)'} "
                 f"{result.dll_path} changes: {', '.join(result.changed) or 'game.cfg only'}")
        built = [os.path.basename(p) for p in (result.exe_path, result.dll_path) if p]
        parts = []
        if built:
            parts.append(f'<b style="color:#40d040">Built {html.escape(" and ".join(built))}</b> '
                         f'<span style="color:{_TEXT}">in the server folder. Checked it: '
                         f'{html.escape(result.report.headline)}</span>')
        if result.changed:
            parts.append(f'<span style="color:{_TEXT}">Changes: '
                         f'{html.escape(", ".join(result.changed))}.</span>')
        steps = "".join(f"<li>{html.escape(step)}</li>" for step in result.steps)
        parts.append(f'<span style="color:{_TEXT}">To swap it in:</span>'
                     f'<ol style="color:{_TEXT}; margin-top:2px">{steps}</ol>')
        self.result_lbl.setText("<br>".join(parts))
