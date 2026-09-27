"""The "Server patches" box on the Server tab: which jo-server-patches the
game server's exe carries (see server_patches).  Read-only."""

from __future__ import annotations

import html
import os
from typing import Callable, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout)

from wolfrat import server_patches as sp

_MARKS = {
    sp.GOOD: ("✔", "#40d040"),
    sp.BAD: ("✖", "#ff5040"),
    sp.WARN: ("!", "#ffa030"),
    sp.INFO: ("•", "#a89830"),
    sp.UNKNOWN: ("?", "#707070"),
}
_TEXT = "#c8b040"
# Problems first: on a 1024x768 screen only the top rows show without scrolling.
_ORDER = {sp.BAD: 0, sp.WARN: 1}


class _Rows(QScrollArea):
    """Scrolls the rows and asks for little height of its own, so on a
    1024x768 screen it takes the space left over, never the top half's."""

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 90)


NO_SERVER_HEADLINE = "No game server on this PC"
NO_SERVER = ("WolfRAT can check this when it runs on the same PC as the game server - "
             "link it on the Server connection page.")


def rows_html(findings) -> str:
    """The rows as one rich-text table.  One wrapping label draws reliably;
    a grid of separate wrapping labels in a scroll area left blank gaps
    under the '?' links (Dale's screenshots, 2026-09-26)."""
    parts = ['<table width="100%" cellspacing="0" cellpadding="3">']
    for finding in sorted(findings, key=lambda f: _ORDER.get(f.level, 2)):
        symbol, colour = _MARKS[finding.level]
        alert = finding.level in (sp.BAD, sp.WARN)
        text = f"{html.escape(finding.title)} - {html.escape(finding.detail)}"
        link = (f'<a href="{html.escape(finding.doc)}" style="color:#5090ff">?</a>'
                if finding.doc else "")
        parts.append(
            f'<tr><td width="18" valign="top" style="color:{colour}; font-weight:bold">{symbol}</td>'
            f'<td valign="top" style="color:{colour if alert else _TEXT}">{text}</td>'
            f'<td width="14" valign="top" align="right">{link}</td></tr>')
    parts.append("</table>")
    return "".join(parts)


class ServerPatchesPanel(QGroupBox):
    """``exe_path()`` -> the running server's jointops.exe, or "" if none.
    ``live(address, size)`` peeks at that running server (speed + memory)."""

    def __init__(self, exe_path: Callable[[], str], live: Optional[sp.LiveRead] = None,
                 parent=None):
        super().__init__("Server patches (read from the server)", parent)
        self._exe_path = exe_path
        self._live = live
        self._seen = None                 # (path, sizes, mtimes) of the last check
        self.report: Optional[sp.Report] = None

        box = QVBoxLayout(self)
        box.setSpacing(6)
        # One line, never wrapped: a wrapping label here made Qt squash the
        # Server tab's top half on a 1024x768 screen.  Long text goes in rows.
        self.headline_lbl = QLabel()
        self.headline_lbl.setWordWrap(False)
        self.headline_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.headline_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(self.headline_lbl)

        self.rows_lbl = QLabel()
        self.rows_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.rows_lbl.setWordWrap(True)
        self.rows_lbl.setOpenExternalLinks(True)
        self.rows_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.rows_lbl.setStyleSheet("font-size: 9pt;")
        scroll = _Rows()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.rows_lbl)
        scroll.setMinimumHeight(40)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        self.path_lbl = QLabel()
        self.path_lbl.setStyleSheet("color: #6a6a30; font-size: 8pt;")
        self.path_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.path_lbl, 1)
        self.recheck_btn = QPushButton("Check again")
        self.recheck_btn.setStyleSheet("font-size: 9pt; padding: 2px 10px; min-height: 16px;")
        self.recheck_btn.clicked.connect(lambda: self.refresh(force=True))
        bottom.addWidget(self.recheck_btn)
        box.addLayout(bottom)

        # A swapped exe / binkw32.dll, an edited game.cfg or a restart shows up by itself.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(15000)
        self.refresh(force=True)

    def _stamp(self, path: str):
        stamp = [path, sp.live_stamp(self._live)]
        folder = os.path.dirname(path)
        for name in (path, os.path.join(folder, "game.cfg"), os.path.join(folder, "binkw32.dll")):
            try:
                st = os.stat(name)
                stamp += [st.st_size, st.st_mtime]
            except OSError:
                stamp += [None, None]
        return tuple(stamp)

    def refresh(self, force: bool = False):
        try:
            path = self._exe_path() or ""
        except Exception:
            path = ""
        if not path:
            self._seen = None
            self.report = None
            self._show_message(NO_SERVER)
            return
        stamp = self._stamp(path)
        if not force and stamp == self._seen:
            return
        self._seen = stamp
        self.report = sp.check(path, self._live)
        self._show(self.report)

    # ------------------------------------------------------------------
    def _show_message(self, text: str):
        self.headline_lbl.setText(NO_SERVER_HEADLINE)
        self.headline_lbl.setToolTip("")
        self.headline_lbl.setStyleSheet("color: #a89830; font-weight: bold;")
        self.rows_lbl.setText(f'<span style="color:#a89830">{html.escape(text)}</span>')
        self.path_lbl.setText("")

    def _show(self, report: sp.Report):
        bad = any(f.level == sp.BAD for f in report.findings)
        self.headline_lbl.setText(report.headline)
        self.headline_lbl.setToolTip(report.headline)
        self.headline_lbl.setStyleSheet(
            f"color: {'#ff6050' if bad else '#e8c840'}; font-weight: bold; font-size: 11pt;")
        self.path_lbl.setText(report.exe_path)
        self.path_lbl.setToolTip(report.exe_path)
        self.rows_lbl.setText(rows_html(report.findings) if report.findings else "")
