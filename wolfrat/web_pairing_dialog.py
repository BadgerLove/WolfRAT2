"""The desktop's "Pair a phone" window (Web Admin tab).

Shows a one-use code from the web server (valid 5 minutes) in big letters, and
a QR code of this PC's address with the code in it: a phone camera opens the
page and the page signs in with the code by itself.
"""

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from wolfrat import qr_code
from wolfrat.web_server import is_local_address


def lan_pair_url(address, port, code):
    """The page on this PC's network address, with the code for the page."""
    return f"http://{address}:{port}/#pair={code}"


def internet_pair_url(host, code):
    """The page on the address a phone used from the internet (host[:port])."""
    return f"http://{host}/#pair={code}"


def qr_pixmap(text, scale=5, border=4):
    """A black-on-white QR code of text, with the usual 4-module margin."""
    matrix = qr_code.encode(text)
    side = (len(matrix) + 2 * border) * scale
    image = QImage(side, side, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    black = QColor(0, 0, 0)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                painter.fillRect((x + border) * scale, (y + border) * scale, scale, scale, black)
    painter.end()
    return QPixmap.fromImage(image)


class PairPhoneDialog(QDialog):
    """One pairing code: big, as a QR code, with a countdown."""

    def __init__(
        self,
        web_server,
        *,
        lan_address,
        port,
        internet_address=None,
        web_running=True,
        parent=None,
        clock=time.time,
    ):
        super().__init__(parent)
        self.ws = web_server
        self._lan = lan_address
        self._port = port
        self._internet = internet_address
        self._clock = clock
        self._code = None
        self._expires_at = 0.0
        self._paired = False
        self.setWindowTitle("Pair a phone")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Scan the QR code with the phone's camera, or open the address below "
            "on the phone and type the code. The code works once, for 5 minutes."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.code_label = QLabel()
        font = QFont()
        font.setPointSize(34)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        self.code_label.setFont(font)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_label.setStyleSheet("color: #e8c840; padding: 6px;")
        layout.addWidget(self.code_label)

        self.countdown_label = QLabel()
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_label.setStyleSheet("color: #888;")
        layout.addWidget(self.countdown_label)

        codes = QHBoxLayout()
        self.qr_label, self.url_label, home = self._qr_column("On this network")
        codes.addLayout(home)
        self.internet_qr_label, self.internet_url_label, away = self._qr_column(
            "Away from home"
        )
        codes.addLayout(away)
        layout.addLayout(codes)

        self.away_label = QLabel()
        self.away_label.setWordWrap(True)
        self.away_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(self.away_label)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: bold; color: #50ff50;")
        layout.addWidget(self.status_label)
        if not web_running:
            self.status_label.setStyleSheet("font-weight: bold; color: #ff8040;")
            self.status_label.setText("Web Admin is off: tick Enable Web Admin first.")

        buttons = QHBoxLayout()
        self.new_code_btn = QPushButton("New code")
        self.new_code_btn.clicked.connect(self.new_code)
        buttons.addWidget(self.new_code_btn)
        buttons.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.new_code()

    @staticmethod
    def _qr_column(title):
        column = QVBoxLayout()
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("color: #888;")
        column.addWidget(heading)
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(qr_label)
        url_label = QLabel()
        url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_label.setWordWrap(True)
        url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(url_label)
        qr_label.heading = heading
        return qr_label, url_label, column

    @property
    def code(self):
        return self._code

    def lan_url(self):
        return lan_pair_url(self._lan, self._port, self._code) if self._lan else None

    def internet_url(self):
        return internet_pair_url(self._internet, self._code) if self._internet else None

    def new_code(self):
        """Ask the web server for a fresh code (the old one stops working)."""
        self._code, self._expires_at = self.ws.start_pairing()
        self._paired = False
        self.code_label.setText(f"{self._code[:3]} {self._code[3:]}")
        self.code_label.setStyleSheet("color: #e8c840; padding: 6px;")
        self._show_qr(self.qr_label, self.url_label, self.lan_url(),
                      "No network address found - check this PC is on Wi-Fi/LAN")
        self._show_qr(self.internet_qr_label, self.internet_url_label, self.internet_url(), None)
        # A PC whose own address is a public one (a rented server): that QR
        # works from anywhere, so there is nothing more to say.
        public = bool(self._lan) and not is_local_address(self._lan)
        self.qr_label.heading.setText("Scan with the phone" if public else "On this network")
        if self._internet or public or not getattr(self.ws, "allow_internet", False):
            self.away_label.setText("")
        else:
            self.away_label.setText(
                f"Away from home: open this PC's internet address, port {self._port}, "
                "on the phone and type the code."
            )
        if self.status_label.text().startswith("✓"):
            self.status_label.setText("")
        self._tick()

    @staticmethod
    def _show_qr(qr_label, url_label, url, missing_text):
        heading = qr_label.heading
        if url is None:
            qr_label.clear()
            qr_label.setVisible(False)
            url_label.setText(missing_text or "")
            url_label.setVisible(bool(missing_text))
            heading.setVisible(bool(missing_text))
            return
        qr_label.setPixmap(qr_pixmap(url))
        qr_label.setVisible(True)
        url_label.setText(url)
        url_label.setVisible(True)
        heading.setVisible(True)

    def _tick(self):
        if self._paired:
            return
        remaining = int(self._expires_at - self._clock() + 0.999)
        if remaining <= 0:
            self.countdown_label.setText("Code expired - press New code")
            self.code_label.setStyleSheet("color: #555; padding: 6px;")
            self.qr_label.setVisible(False)
            self.internet_qr_label.setVisible(False)
            return
        self.countdown_label.setText(f"Valid for {remaining // 60}:{remaining % 60:02d}")

    def paired(self, name):
        """The code was used: say so and stop the countdown."""
        self._paired = True
        self.countdown_label.setText("")
        self.code_label.setStyleSheet("color: #555; padding: 6px;")
        self.qr_label.setVisible(False)
        self.internet_qr_label.setVisible(False)
        self.status_label.setStyleSheet("font-weight: bold; color: #50ff50;")
        self.status_label.setText(f"✓ {name} is paired. You can close this window.")
        self.close_btn.setText("Done")

    def done(self, result):
        # An unused code stops working when the window closes.
        if not self._paired and self._code is not None:
            active = self.ws.pairing_code()
            if active is not None and active[0] == self._code:
                self.ws.cancel_pairing()
        self._timer.stop()
        super().done(result)
