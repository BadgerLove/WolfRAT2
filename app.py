"""
WolfRAT 2.0 — Modern Joint Operations Server Admin Tool
Replaces the original WolfRAT v0.95 (2005, MFC70)
"""

import sys
import os
import json
import time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSpinBox, QCheckBox, QGroupBox,
    QSplitter, QMessageBox, QStatusBar, QFrame, QListWidget, QListWidgetItem,
    QAbstractItemView, QMenu, QSlider, QPlainTextEdit, QTableView
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QObject, QAbstractTableModel
from PyQt6.QtGui import QFont, QColor, QIcon, QTextCursor

import math
from wolfrat.protocol import ServerManager, wire_log
from wolfrat.sounds import generate_all_sounds
from wolfrat.web_server import WolfWebServer
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtCore import QUrl


# =============================================================================
# Sound Manager
# =============================================================================
class SoundManager:
    """Manages Hitchhiker's Guide style door sounds."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._muted = False
        self._effects = {}
        try:
            sound_files = generate_all_sounds()
            for name, path in sound_files.items():
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(path))
                effect.setVolume(0.4)
                self._effects[name] = effect
        except Exception as e:
            print(f"Sound init failed: {e}")

    def play(self, name):
        if not self._muted and name in self._effects:
            try:
                self._effects[name].play()
            except Exception:
                pass

    def set_muted(self, muted):
        self._muted = muted

    @property
    def muted(self):
        return self._muted


# Global instance
sounds = SoundManager()


# =============================================================================
# OLED Black + Yellow theme
# =============================================================================
DARK_STYLE = """
QMainWindow {
    background-color: #000000;
}
QWidget {
    background-color: #000000;
    color: #e8c840;
    font-family: 'Segoe UI', Arial;
    font-size: 10pt;
}
QTabWidget::pane {
    border: 1px solid #2a2a00;
    background-color: #000000;
    border-radius: 4px;
}
QTabBar::tab {
    background-color: #1a1a00;
    color: #8a7a20;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #2a2a00;
    color: #e8c840;
}
QTabBar::tab:hover {
    background-color: #333300;
}
QPushButton {
    background-color: #1a1a00;
    color: #e8c840;
    border-top: 2px solid #4a4a10;
    border-left: 2px solid #4a4a10;
    border-bottom: 2px solid #0a0a00;
    border-right: 2px solid #0a0a00;
    padding: 7px 16px 5px 16px;
    border-radius: 3px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #2a2a00;
    border-top: 2px solid #6a6a20;
    border-left: 2px solid #6a6a20;
    border-bottom: 2px solid #0a0a00;
    border-right: 2px solid #0a0a00;
}
QPushButton:pressed {
    background-color: #0f0f00;
    border-top: 2px solid #0a0a00;
    border-left: 2px solid #0a0a00;
    border-bottom: 2px solid #4a4a10;
    border-right: 2px solid #4a4a10;
    padding: 5px 16px 7px 16px;
}
QPushButton:disabled {
    background-color: #0a0a00;
    color: #2a2a00;
    border-top: 2px solid #151500;
    border-left: 2px solid #151500;
    border-bottom: 2px solid #050500;
    border-right: 2px solid #050500;
}
QPushButton#connectBtn {
    background-color: #2a2a00;
    color: #e8c840;
    border-top: 2px solid #e8c840;
    border-left: 2px solid #e8c840;
    border-bottom: 2px solid #1a1a00;
    border-right: 2px solid #1a1a00;
    font-weight: bold;
    padding: 7px 16px 5px 16px;
}
QPushButton#connectBtn:hover {
    background-color: #3a3a00;
    border-top: 2px solid #ffd700;
    border-left: 2px solid #ffd700;
    border-bottom: 2px solid #1a1a00;
    border-right: 2px solid #1a1a00;
}
QPushButton#connectBtn:pressed {
    background-color: #0f0f00;
    border-top: 2px solid #1a1a00;
    border-left: 2px solid #1a1a00;
    border-bottom: 2px solid #e8c840;
    border-right: 2px solid #e8c840;
    padding: 5px 16px 7px 16px;
}
QPushButton#disconnectBtn {
    background-color: #1a0000;
    color: #ff6040;
    border-top: 2px solid #ff6040;
    border-left: 2px solid #ff6040;
    border-bottom: 2px solid #0a0000;
    border-right: 2px solid #0a0000;
    font-weight: bold;
    padding: 7px 16px 5px 16px;
}
QPushButton#disconnectBtn:hover {
    background-color: #2a0000;
    border-top: 2px solid #ff8060;
    border-left: 2px solid #ff8060;
    border-bottom: 2px solid #0a0000;
    border-right: 2px solid #0a0000;
}
QPushButton#disconnectBtn:pressed {
    background-color: #0a0000;
    border-top: 2px solid #0a0000;
    border-left: 2px solid #0a0000;
    border-bottom: 2px solid #ff6040;
    border-right: 2px solid #ff6040;
    padding: 5px 16px 7px 16px;
}
QPushButton#warnBtn {
    background-color: #1a1a00;
    color: #e8c840;
    border-top: 2px solid #e8c840;
    border-left: 2px solid #e8c840;
    border-bottom: 2px solid #0a0a00;
    border-right: 2px solid #0a0a00;
    padding: 7px 16px 5px 16px;
}
QPushButton#warnBtn:hover {
    background-color: #2a2a00;
    border-top: 2px solid #ffd700;
    border-left: 2px solid #ffd700;
    border-bottom: 2px solid #0a0a00;
    border-right: 2px solid #0a0a00;
}
QPushButton#warnBtn:pressed {
    background-color: #0f0f00;
    border-top: 2px solid #0a0a00;
    border-left: 2px solid #0a0a00;
    border-bottom: 2px solid #e8c840;
    border-right: 2px solid #e8c840;
    padding: 5px 16px 7px 16px;
}
QPushButton#puntBtn {
    background-color: #1a0a00;
    color: #d4841a;
    border: 1px solid #d4841a;
}
QPushButton#banBtn {
    background-color: #1a0000;
    color: #ff6040;
    border: 1px solid #ff6040;
}
QPushButton#killBtn {
    background-color: #0a0a00;
    color: #8a7a20;
    border: 1px solid #8a7a20;
}
QPushButton#swapBtn {
    background-color: #001a1a;
    color: #40c0c0;
    border: 1px solid #40c0c0;
}
QLineEdit, QSpinBox {
    background-color: #0a0a00;
    color: #e8c840;
    border: 1px solid #2a2a00;
    padding: 4px 8px;
    border-radius: 4px;
}
QLineEdit:focus, QSpinBox:focus {
    border: 1px solid #e8c840;
}
QLineEdit::placeholder {
    color: #4a4a10;
}
QTextEdit {
    background-color: #050500;
    color: #a89830;
    border: 1px solid #1a1a00;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 9pt;
    border-radius: 4px;
}
QTableWidget {
    background-color: #050500;
    color: #e8c840;
    border: 1px solid #1a1a00;
    gridline-color: #1a1a00;
    selection-background-color: #2a2a00;
    selection-color: #ffd700;
    border-radius: 4px;
}
QTableWidget::item {
    padding: 4px;
}
QTableWidget::item:selected {
    background-color: #2a2a00;
}
QHeaderView::section {
    background-color: #1a1a00;
    color: #e8c840;
    padding: 4px;
    border: 1px solid #2a2a00;
    font-weight: bold;
}
QGroupBox {
    border: 1px solid #2a2a00;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: bold;
    color: #e8c840;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #e8c840;
}
QComboBox {
    background-color: #0a0a00;
    color: #e8c840;
    border: 1px solid #2a2a00;
    padding: 4px 8px;
    border-radius: 4px;
}
QComboBox::drop-down {
    border: none;
}
QComboBox QAbstractItemView {
    background-color: #0a0a00;
    color: #e8c840;
    selection-background-color: #2a2a00;
    selection-color: #ffd700;
}
QCheckBox {
    color: #e8c840;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator:unchecked {
    background-color: #0a0a00;
    border: 1px solid #3a3a00;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #e8c840;
    border: 1px solid #e8c840;
    border-radius: 3px;
}
QListWidget {
    background-color: #050500;
    color: #e8c840;
    border: 1px solid #1a1a00;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #2a2a00;
}
QStatusBar {
    background-color: #050500;
    color: #8a7a20;
    border-top: 1px solid #1a1a00;
}
QSplitter::handle {
    background-color: #2a2a00;
}
QToolTip {
    background-color: #1a1a00;
    color: #e8c840;
    border: 1px solid #3a3a00;
    padding: 4px;
}
QScrollBar:vertical {
    background-color: #000000;
    width: 10px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #2a2a00;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #3a3a00;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #000000;
    height: 10px;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: #2a2a00;
    border-radius: 5px;
    min-width: 20px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #3a3a00;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
"""


class SatisfyingButton(QPushButton):
    """A button that holds its pressed visual for a moment before firing.
    Makes clicks feel physical instead of instant."""

    def __init__(self, text="", parent=None, hold_ms=120):
        super().__init__(text, parent)
        self._hold_ms = hold_ms
        self._pending = False
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        # Disconnect the normal clicked signal — we'll fire it after the hold
        # We use a custom signal instead

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pending = True
            # Let Qt show the :pressed state visually
            super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pending:
            self._pending = False
            # Hold the pressed look for a bit, then release + fire
            self._timer.start(self._hold_ms)
            # Don't call super yet — keeps the button visually pressed
        else:
            super().mouseReleaseEvent(event)

    def _fire(self):
        # Now release the visual and emit clicked
        # Simulate a clean release
        self.setDown(False)
        self.repaint()
        sounds.play("click")
        self.clicked.emit()


class LogSignals(QObject):
    """Thread-safe signal emitter for protocol callbacks."""
    connected_signal = pyqtSignal()
    disconnected_signal = pyqtSignal()
    log_signal = pyqtSignal(str)
    players_signal = pyqtSignal(list)
    chat_signal = pyqtSignal(list)
    gamestate_signal = pyqtSignal(dict)
    missions_signal = pyqtSignal(list)
    settings_signal = pyqtSignal(dict)
    available_maps_signal = pyqtSignal(str)
    connect_signal = pyqtSignal(bool, str)


class ServerTab(QWidget):
    """Server connection tab."""

    def __init__(self, server: ServerManager, signals: LogSignals):
        super().__init__()
        self.server = server
        self.signals = signals
        self.missions_tab = None  # set by MainWindow after creation
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Connection group
        conn_group = QGroupBox("Server Connection")
        conn_layout = QGridLayout()

        conn_layout.addWidget(QLabel("Server Address:"), 0, 0)
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("e.g. 192.168.1.100 — Port is set in your game.cfg")
        conn_layout.addWidget(self.host_input, 0, 1)

        conn_layout.addWidget(QLabel("Port:"), 1, 0)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(4000)
        conn_layout.addWidget(self.port_input, 1, 1)

        conn_layout.addWidget(QLabel("Username:"), 2, 0)
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("Admin username")
        conn_layout.addWidget(self.user_input, 2, 1)

        conn_layout.addWidget(QLabel("Password:"), 3, 0)
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("Any password works")
        conn_layout.addWidget(self.pass_input, 3, 1)

        btn_layout = QHBoxLayout()
        self.connect_btn = SatisfyingButton("Connect")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.clicked.connect(self._do_connect)
        btn_layout.addWidget(self.connect_btn)

        self.disconnect_btn = SatisfyingButton("Disconnect")
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.clicked.connect(self._do_disconnect)
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.disconnect_btn)

        self.refresh_btn = SatisfyingButton("Refresh All")
        self.refresh_btn.clicked.connect(lambda: self.server.refresh_all())
        self.refresh_btn.setEnabled(False)
        btn_layout.addWidget(self.refresh_btn)

        conn_layout.addLayout(btn_layout, 4, 0, 1, 2)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        # Server info group
        info_group = QGroupBox("Server Status")
        info_layout = QGridLayout()

        self.status_label = QLabel("Not Connected")
        self.status_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ff6040;")
        info_layout.addWidget(QLabel("Status:"), 0, 0)
        info_layout.addWidget(self.status_label, 0, 1)

        self.server_name_label = QLabel("-")
        info_layout.addWidget(QLabel("Server Name:"), 1, 0)
        info_layout.addWidget(self.server_name_label, 1, 1)

        self.game_mode_label = QLabel("-")
        info_layout.addWidget(QLabel("Game Mode:"), 2, 0)
        info_layout.addWidget(self.game_mode_label, 2, 1)

        self.player_count_label = QLabel("-")
        info_layout.addWidget(QLabel("Players:"), 3, 0)
        info_layout.addWidget(self.player_count_label, 3, 1)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Saved servers
        saved_group = QGroupBox("Saved Servers")
        saved_layout = QVBoxLayout()
        self.server_list = QListWidget()
        self.server_list.itemDoubleClicked.connect(self._load_server)
        saved_layout.addWidget(self.server_list)

        save_btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save Current")
        save_btn.clicked.connect(self._save_server)
        save_btn_layout.addWidget(save_btn)
        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self._delete_server)
        save_btn_layout.addWidget(del_btn)
        saved_layout.addLayout(save_btn_layout)

        saved_group.setLayout(saved_layout)
        layout.addWidget(saved_group)

        # Load saved servers
        self._load_saved_servers()

    def _do_connect(self):
        host = self.host_input.text().strip()
        if not host:
            QMessageBox.warning(self, "Error", "Please enter a server address.")
            return

        port = self.port_input.value()
        username = self.user_input.text().strip()
        password = self.pass_input.text().strip()

        self.connect_btn.setEnabled(False)
        self.log(f"Connecting to {host}:{port} as {username}...")

        try:
            success, msg = self.server.connect(host, port, username, password)
        except Exception as e:
            self.log(f"Connect error: {e}")
            self.connect_btn.setEnabled(True)
            return
        self.log(msg)

        if success:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #e8c840;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            try:
                self.signals.connected_signal.emit()
            except Exception as e:
                self.log(f"Signal error: {e}")
            # Polling starts automatically in ServerManager.connect()
            # Don't auto-apply rotation — it wipes the server's actual maps
            # Presets are loaded on demand via the Load button
        else:
            self.connect_btn.setEnabled(True)

    def _do_disconnect(self):
        self.server.disconnect()
        self.server.stop_polling()
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ff6040;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.log("Disconnected from server.")
        self.signals.disconnected_signal.emit()

    def update_gamestate(self, state: dict):
        mode = state.get('mode', '-')
        self.game_mode_label.setText(mode)

    def update_player_count(self, players: list):
        self.player_count_label.setText(str(len(players)))

    def update_settings(self, settings: dict):
        # Extract server name from settings (keys are lowercased by _parse_settings)
        for key in ('servername', 'servertitle', 'name', 'title'):
            if key in settings and settings[key]:
                self.server_name_label.setText(settings[key])
                break

    def log(self, msg: str):
        pass  # Console tab handles logging now

    def _save_server(self):
        host = self.host_input.text().strip()
        port = self.port_input.value()
        user = self.user_input.text().strip()
        if host:
            item = f"{host}:{port} ({user})"
            self.server_list.addItem(item)
            self._persist_servers()

    def _delete_server(self):
        row = self.server_list.currentRow()
        if row >= 0:
            self.server_list.takeItem(row)
            self._persist_servers()

    def _load_server(self, item: QListWidgetItem):
        text = item.text()
        # Parse "host:port (user)"
        try:
            addr, user = text.split(' (')
            user = user.rstrip(')')
            host, port = addr.rsplit(':', 1)
            self.host_input.setText(host)
            self.port_input.setValue(int(port))
            self.user_input.setText(user)
        except Exception:
            pass

    def _persist_servers(self):
        servers = []
        for i in range(self.server_list.count()):
            servers.append(self.server_list.item(i).text())
        try:
            cfg_path = os.path.join(os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False)
                                     else os.path.dirname(sys.executable), 'wolfrat_servers.json')
            with open(cfg_path, 'w') as f:
                json.dump(servers, f)
        except Exception:
            pass

    def _load_saved_servers(self):
        try:
            cfg_path = os.path.join(os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False)
                                     else os.path.dirname(sys.executable), 'wolfrat_servers.json')
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    for s in json.load(f):
                        self.server_list.addItem(s)
        except Exception:
            pass


class ConsoleTab(QWidget):
    """Console tab — full server log with command input."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self._max_lines = 5000
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Log output — QPlainTextEdit is much faster than QTextEdit for logs
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(self._max_lines)
        self.console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0a0a00;
                color: #c0c0a0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10pt;
                border: 1px solid #3a3a00;
                border-radius: 4px;
                padding: 4px;
            }
        """)
        layout.addWidget(self.console)

        # Command input row
        cmd_layout = QHBoxLayout()
        cmd_label = QLabel(">")
        cmd_label.setStyleSheet("font-family: 'Consolas', monospace; font-size: 11pt; font-weight: bold; color: #e8c840;")
        cmd_layout.addWidget(cmd_label)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Type a command and press Enter...")
        self.cmd_input.setStyleSheet("""
            QLineEdit {
                background-color: #0a0a00;
                color: #e8c840;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11pt;
                border: 1px solid #3a3a00;
                border-radius: 4px;
                padding: 6px;
            }
            QLineEdit:focus {
                border: 1px solid #e8c840;
            }
        """)
        self.cmd_input.returnPressed.connect(self._send_command)
        cmd_layout.addWidget(self.cmd_input)

        send_btn = SatisfyingButton("Send")
        send_btn.setFixedWidth(80)
        send_btn.clicked.connect(self._send_command)
        cmd_layout.addWidget(send_btn)

        clear_btn = SatisfyingButton("Clear")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self.console.clear)
        cmd_layout.addWidget(clear_btn)

        layout.addLayout(cmd_layout)

    def log(self, msg: str):
        """Append a log line without flicker."""
        # Check if user is scrolled to bottom before adding text
        scrollbar = self.console.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2

        self.console.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

        # Auto-scroll only if user was already at the bottom
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _send_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        if not self.server.proto.connected:
            self.log("⚠ Not connected to server")
            return
        self.server.send(cmd)
        self.cmd_input.clear()


class PlayerTableModel(QAbstractTableModel):
    """Model for player list — drives QTableView with zero flicker."""
    HEADERS = ["ID", "Name", "Team", "Class", "Kills", "Deaths", "Ping"]

    CLASS_MAP = {
        "0": "Rifleman", "1": "Gunner", "2": "Sniper",
        "3": "Medic", "4": "Engineer", "5": "Medic",
        "6": "Marksman", "7": "Fire Support", "8": "Rifleman",
        "9": "Engineer",
    }

    def __init__(self):
        super().__init__()
        self._players = []

    def rowCount(self, parent=None):
        return len(self._players)

    def columnCount(self, parent=None):
        return len(self.HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        p = self._players[index.row()]
        col = index.column()
        if col == 0: return str(p.get('id', ''))
        if col == 1: return str(p.get('name', ''))
        if col == 2: return str(p.get('team_name', p.get('team', '')))
        if col == 3: return self.CLASS_MAP.get(str(p.get('class', '')).strip(), str(p.get('class', '-')))
        if col == 4: return str(p.get('kills', '0'))
        if col == 5: return str(p.get('deaths', '-'))
        if col == 6: return str(p.get('ping', '-'))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def update_players(self, players):
        """Replace player data efficiently — only emit dataChanged for actual changes."""
        old = self._players
        new = players

        # If row count changed, do a full reset (unavoidable)
        if len(old) != len(new):
            try:
                from wolfrat.protocol import wire_log
                wire_log(f"PlayerTable: FULL RESET (row count {len(old)} -> {len(new)})")
            except: pass
            self.beginResetModel()
            self._players = new
            self.endResetModel()
            return

        # Same row count — check each cell for changes
        changed = 0
        self._players = new
        for row in range(len(new)):
            for col in range(len(self.HEADERS)):
                old_val = self._cell_value(old[row], col) if row < len(old) else None
                new_val = self._cell_value(new[row], col)
                if old_val != new_val:
                    changed += 1
                    idx = self.index(row, col)
                    self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
        if changed:
            try:
                from wolfrat.protocol import wire_log
                wire_log(f"PlayerTable: {changed} cells changed (no reset)")
            except: pass

    def _cell_value(self, p, col):
        """Get display value for a player dict at a given column."""
        if col == 0: return str(p.get('id', ''))
        if col == 1: return str(p.get('name', ''))
        if col == 2: return str(p.get('team_name', p.get('team', '')))
        if col == 3: return self.CLASS_MAP.get(str(p.get('class', '')).strip(), str(p.get('class', '-')))
        if col == 4: return str(p.get('kills', '0'))
        if col == 5: return str(p.get('deaths', '-'))
        if col == 6: return str(p.get('ping', '-'))
        return None

    def get_player_at(self, row):
        """Get player dict at given row."""
        if 0 <= row < len(self._players):
            return self._players[row]
        return None


class PlayersTab(QWidget):
    """Player list with admin actions."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Player table — model/view for zero-flicker updates
        self.model = PlayerTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table)

        # Admin actions
        actions_group = QGroupBox("Admin Actions")
        actions_layout = QGridLayout()

        # Row 1: Warning messages
        actions_layout.addWidget(QLabel("Message:"), 0, 0)
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("Optional reason / message")
        actions_layout.addWidget(self.msg_input, 0, 1, 1, 3)

        # Row 2: Action buttons
        self.warn_btn = SatisfyingButton("Warn")
        self.warn_btn.setObjectName("warnBtn")
        self.warn_btn.clicked.connect(lambda: self._admin_action("warn"))
        actions_layout.addWidget(self.warn_btn, 1, 0)

        self.punt_btn = SatisfyingButton("Punt (Kick)")
        self.punt_btn.setObjectName("puntBtn")
        self.punt_btn.clicked.connect(lambda: self._admin_action("punt"))
        actions_layout.addWidget(self.punt_btn, 1, 1)

        self.ban_btn = SatisfyingButton("Ban")
        self.ban_btn.setObjectName("banBtn")
        self.ban_btn.clicked.connect(lambda: self._admin_action("ban"))
        actions_layout.addWidget(self.ban_btn, 1, 2)

        self.kill_btn = SatisfyingButton("Kill")
        self.kill_btn.setObjectName("killBtn")
        self.kill_btn.clicked.connect(lambda: self._admin_action("kill"))
        actions_layout.addWidget(self.kill_btn, 2, 0)

        self.swap_btn = SatisfyingButton("Swap Team")
        self.swap_btn.setObjectName("swapBtn")
        self.swap_btn.setToolTip("Switch player to the other team")
        self.swap_btn.clicked.connect(lambda: self._admin_action("swap"))
        actions_layout.addWidget(self.swap_btn, 2, 1)

        self.zero_btn = SatisfyingButton("Zero Score")
        self.zero_btn.clicked.connect(lambda: self._admin_action("zero"))
        actions_layout.addWidget(self.zero_btn, 2, 2)

        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        # Team balance section
        balance_group = QGroupBox("Team Balance")
        balance_layout = QVBoxLayout()

        self.balance_label = QLabel("No players")
        self.balance_label.setStyleSheet("font-size: 11pt; color: #a89830;")
        balance_layout.addWidget(self.balance_label)

        balance_btn_layout = QHBoxLayout()

        self.mix_btn = SatisfyingButton("Balance Teams")
        self.mix_btn.setStyleSheet("font-size: 11pt; padding: 8px; background-color: #1a1a00; color: #e8c840; border: 1px solid #e8c840;")
        self.mix_btn.setToolTip("Move players from the bigger team to balance team sizes")
        self.mix_btn.clicked.connect(self._mix_teams)
        balance_btn_layout.addWidget(self.mix_btn)

        self.shuffle_btn = SatisfyingButton("Mix Teams")
        self.shuffle_btn.setStyleSheet("font-size: 11pt; padding: 8px; background-color: #1a1a00; color: #e8c840; border: 1px solid #e8c840;")
        self.shuffle_btn.setToolTip("Randomly shuffle all players across both teams")
        self.shuffle_btn.clicked.connect(self._shuffle_teams)
        balance_btn_layout.addWidget(self.shuffle_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda: self.server.send("player list"))
        balance_btn_layout.addWidget(refresh_btn)

        balance_layout.addLayout(balance_btn_layout)
        balance_group.setLayout(balance_layout)
        layout.addWidget(balance_group)

    def _get_selected_player_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select Player", "Please select a player from the list first.")
            return None
        row = rows[0].row()
        p = self.model.get_player_at(row)
        if p:
            return p.get('id', '')
        return None

    def _admin_action(self, action: str):
        player_id = self._get_selected_player_id()
        if not player_id:
            return

        # Get player name for announcements
        player_name = ""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            p = self.model.get_player_at(rows[0].row())
            if p:
                player_name = p.get('name', '')

        msg = self.msg_input.text().strip()
        display = player_name or player_id

        if action == "warn":
            # Send warning with custom message to player
            self.server.warn_player(int(player_id), msg or "You have been warned!")
            time.sleep(0.3)
            # Announce to server with the message
            self.server.send_chat(f"WARNING to {display}: {msg or 'Behave!'}")

        elif action == "punt":
            self.server.punt_player(int(player_id), msg or "Kicked by admin")
            time.sleep(0.3)
            self.server.send_chat(f"{display} has been kicked")

        elif action == "ban":
            self.server.ban_player(int(player_id), msg or "Banned by admin")
            time.sleep(0.3)
            self.server.send_chat(f"{display} has been banned")

        elif action == "kill":
            self.server.kill_player(int(player_id))

        elif action == "swap":
            # PLAYER SWAPTEAM handles the team change directly
            self.server.swap_player(int(player_id))
            time.sleep(0.3)
            self.server.send_chat(f"{display} swapped to the other team")

        elif action == "zero":
            self.server.zero_player(int(player_id))

    def _mix_teams(self):
        """Mix teams — should only be used at round start."""
        reply = QMessageBox.question(
            self, "Balance Teams",
            "This will move players from the bigger team to even things up.\n\n"
            "This will:\n"
            "• Randomly select players from the bigger team\n"
            "• Swap them to the other team\n"
            "• Kill them so they respawn at the correct base\n"
            "• Announce each swap to the server\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        results = self.server.mix_teams()
        for line in results:
            self.server._log(line)

    def _shuffle_teams(self):
        """Randomly shuffle all players across both teams."""
        players = self.server.players
        if len(players) < 2:
            self.server.send_chat("Need at least 2 players to mix!")
            return

        reply = QMessageBox.question(
            self, "Mix Teams",
            f"This will randomly shuffle all {len(players)} players across both teams.\n\n"
            "Players who change team will be killed to respawn at their new base.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        results = self.server.shuffle_teams()
        for line in results:
            self.server._log(line)

    def update_players(self, players: list):
        # Update team balance display
        try:
            team_a = [p for p in players if p.get('team') == '1']
            team_b = [p for p in players if p.get('team') == '2']
            score_a = sum(int(p.get('kills', '0')) for p in team_a)
            score_b = sum(int(p.get('kills', '0')) for p in team_b)
            diff = abs(score_a - score_b)
            count_diff = abs(len(team_a) - len(team_b))

            balance_text = f"Joint Ops: {len(team_a)} players ({score_a} kills)  |  Rebels: {len(team_b)} players ({score_b} kills)"
            if count_diff > 1 or diff > 10:
                balance_text += f"  ⚠ UNBALANCED"
                self.balance_label.setStyleSheet("font-size: 11pt; color: #ff6040; font-weight: bold;")
            else:
                self.balance_label.setStyleSheet("font-size: 11pt; color: #a89830;")
            self.balance_label.setText(balance_text)
        except Exception:
            self.balance_label.setText(f"Players: {len(players)}")

        # Update model — view repaints automatically, zero flicker
        self.model.update_players(players)


class MissionsTab(QWidget):
    """Mission/map management — matches original WolfRAT layout.

    Layout:
      LEFT:   All Available Missions (QTableWidget: Mission Name | Filename)
      RIGHT:  Mission Cycle          (QTableWidget: Mission Name | r | Filename)
      FAR RIGHT: Action buttons column (OK, Cancel, Review logs, Auto Refresh)
      BOTTOM: Status bar (Connected | Game Mode | command | status | players | time)
    """

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self._all_maps = []  # full available list [{file, display}]
        self._rotation_maps = []  # current rotation [filename, ...]
        self._main_window = None  # set by MainWindow after creation
        self._presets = {}  # saved rotation presets
        self._load_presets()
        self._build_ui()

    def _preset_path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_rotations.json')

    def _load_presets(self):
        try:
            path = self._preset_path()
            if os.path.exists(path):
                with open(path) as f:
                    self._presets = json.load(f)
        except Exception:
            pass

    def _save_presets(self):
        try:
            with open(self._preset_path(), 'w') as f:
                json.dump(self._presets, f, indent=2)
        except Exception:
            pass

    def _build_auto_preset(self):
        """Build preset data from current rotation table (filenames + play counts)."""
        maps = []
        for row in range(self.rotation_table.rowCount()):
            file_item = self.rotation_table.item(row, 2)
            r_item = self.rotation_table.item(row, 1)
            if file_item:
                filename = file_item.text()
                count = int(r_item.text()) if r_item else 1
                maps.append({"file": filename, "count": count})
        return maps

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # =========================================================
        # MAIN AREA: 3-column layout
        #   LEFT: Available Missions | RIGHT: Mission Cycle | FAR RIGHT: Buttons
        # =========================================================
        main_h = QHBoxLayout()
        main_h.setSpacing(6)

        # ---- LEFT: All Available Missions ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        left_header = QLabel("All Available Missions")
        left_header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #e8c840;")
        left_layout.addWidget(left_header)

        # Search + mode filter
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search maps...")
        self.search_input.textChanged.connect(self._filter_maps)
        search_row.addWidget(self.search_input, 1)

        self.mode_filter = QComboBox()
        self.mode_filter.addItem("All Modes")
        self.mode_filter.addItems(["AS", "TD", "TK", "DM", "FB", "CP", "CTF",
                                    "TDY", "TDH", "TKH", "TDR", "TKR",
                                    "TDX", "TKX", "TDP", "TKP", "TDB"])
        self.mode_filter.currentTextChanged.connect(self._filter_maps)
        search_row.addWidget(self.mode_filter)
        left_layout.addLayout(search_row)

        # Available maps table: Mission Name | Filename
        self.available_table = QTableWidget(0, 2)
        self.available_table.setHorizontalHeaderLabels(["Mission Name", "Filename"])
        self.available_table.horizontalHeader().setStretchLastSection(True)
        self.available_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.available_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.available_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.available_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.available_table.customContextMenuRequested.connect(self._available_context_menu)
        self.available_table.doubleClicked.connect(self._double_click_add)
        left_layout.addWidget(self.available_table, 1)

        # Bottom buttons
        avail_btns = QHBoxLayout()
        refresh_avail_btn = SatisfyingButton("Refresh")
        refresh_avail_btn.clicked.connect(lambda: self.server.send("mission available"))
        avail_btns.addWidget(refresh_avail_btn)

        add_btn = SatisfyingButton("Add to Rotation (1x)")
        add_btn.clicked.connect(lambda: self._add_to_rotation("1x"))
        avail_btns.addWidget(add_btn)

        add_btn2 = SatisfyingButton("Add to Rotation (2x)")
        add_btn2.clicked.connect(lambda: self._add_to_rotation("2x"))
        avail_btns.addWidget(add_btn2)

        add_all_btn = SatisfyingButton("Add All")
        add_all_btn.clicked.connect(lambda: self._add_all_visible("1x"))
        avail_btns.addWidget(add_all_btn)
        left_layout.addLayout(avail_btns)

        main_h.addWidget(left, 1)  # stretch=1

        # ---- RIGHT: Mission Cycle (rotation) ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        right_header = QLabel("Mission Cycle")
        right_header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #e8c840;")
        right_layout.addWidget(right_header)

        # Rotation table: Mission Name | r | Filename
        self.rotation_table = QTableWidget(0, 3)
        self.rotation_table.setHorizontalHeaderLabels(["Mission Name", "r", "Filename"])
        self.rotation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rotation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.rotation_table.horizontalHeader().resizeSection(1, 30)
        self.rotation_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.rotation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rotation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rotation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rotation_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rotation_table.customContextMenuRequested.connect(self._rotation_context_menu)
        self.rotation_table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.rotation_table.model().rowsMoved.connect(self._on_rotation_reorder)
        right_layout.addWidget(self.rotation_table, 1)

        # Rotation buttons
        rot_btns = QHBoxLayout()
        refresh_rot_btn = SatisfyingButton("Refresh")
        refresh_rot_btn.clicked.connect(lambda: self.server.send("mission list"))
        rot_btns.addWidget(refresh_rot_btn)

        setnext_btn = SatisfyingButton("Set Next Mission")
        setnext_btn.setToolTip("Queue selected map as next (won't cycle immediately)")
        setnext_btn.clicked.connect(self._set_next_map)
        rot_btns.addWidget(setnext_btn)

        run_btn = SatisfyingButton("Run This Map")
        run_btn.setToolTip("Switch server to the selected map now")
        run_btn.clicked.connect(self._run_selected_map)
        rot_btns.addWidget(run_btn)

        remove_btn = SatisfyingButton("Remove")
        remove_btn.clicked.connect(self._remove_from_rotation)
        rot_btns.addWidget(remove_btn)

        up_btn = SatisfyingButton("▲")
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(self._move_up)
        rot_btns.addWidget(up_btn)

        down_btn = SatisfyingButton("▼")
        down_btn.setToolTip("Move down")
        down_btn.clicked.connect(self._move_down)
        rot_btns.addWidget(down_btn)

        right_layout.addLayout(rot_btns)

        # Presets row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(none)")
        for name in self._presets:
            self.preset_combo.addItem(name)
        preset_row.addWidget(self.preset_combo, 1)
        load_preset_btn = QPushButton("Load")
        load_preset_btn.clicked.connect(self._load_preset)
        preset_row.addWidget(load_preset_btn)
        save_preset_btn = QPushButton("Save")
        save_preset_btn.clicked.connect(self._save_preset)
        preset_row.addWidget(save_preset_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_rotation)
        preset_row.addWidget(clear_btn)
        right_layout.addLayout(preset_row)

        main_h.addWidget(right, 1)  # stretch=1

        # ---- FAR RIGHT: Action buttons column ----
        action_col = QWidget()
        action_col.setFixedWidth(140)
        action_layout = QVBoxLayout(action_col)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)

        action_layout.addWidget(QLabel(""))  # spacer for header alignment
        action_layout.addSpacing(20)

        ok_btn = SatisfyingButton("OK")
        ok_btn.setToolTip("Apply current rotation and close")
        ok_btn.clicked.connect(self._ok_clicked)
        action_layout.addWidget(ok_btn)

        cancel_btn = SatisfyingButton("Cancel")
        cancel_btn.setToolTip("Discard changes")
        cancel_btn.clicked.connect(self._cancel_clicked)
        action_layout.addWidget(cancel_btn)

        action_layout.addSpacing(10)

        review_event_btn = SatisfyingButton("Review\nEvent Log")
        review_event_btn.clicked.connect(self._review_event_log)
        action_layout.addWidget(review_event_btn)

        review_chat_btn = SatisfyingButton("Review\nChat Log")
        review_chat_btn.clicked.connect(self._review_chat_log)
        action_layout.addWidget(review_chat_btn)

        action_layout.addSpacing(10)

        self.auto_refresh_check = QCheckBox("Auto Refresh")
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.toggled.connect(self._toggle_auto_refresh)
        action_layout.addWidget(self.auto_refresh_check)

        action_layout.addStretch()

        main_h.addWidget(action_col)

        layout.addLayout(main_h, 1)  # stretch=1 for main area

        # =========================================================
        # BOTTOM: Status bar
        # =========================================================
        status_bar = QHBoxLayout()
        status_bar.setSpacing(0)
        status_bar.setContentsMargins(4, 2, 4, 2)

        self.status_connected = QLabel(" ● Disconnected ")
        self.status_connected.setStyleSheet("font-size: 9pt; color: #ff6040; padding: 2px 8px;")
        status_bar.addWidget(self.status_connected)

        sep1 = QLabel("│")
        sep1.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep1)

        self.status_mode = QLabel(" Game: — ")
        self.status_mode.setStyleSheet("font-size: 9pt; color: #a89830; padding: 2px 8px;")
        status_bar.addWidget(self.status_mode)

        sep2 = QLabel("│")
        sep2.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep2)

        self.status_command = QLabel(" ")
        self.status_command.setStyleSheet("font-size: 9pt; color: #6a6a20; padding: 2px 8px;")
        status_bar.addWidget(self.status_command)

        sep3 = QLabel("│")
        sep3.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep3)

        self.status_info = QLabel(" ")
        self.status_info.setStyleSheet("font-size: 9pt; color: #6a6a20; padding: 2px 8px;")
        status_bar.addWidget(self.status_info, 1)  # stretch

        self.status_players = QLabel(" Players: 0 ")
        self.status_players.setStyleSheet("font-size: 9pt; color: #a89830; padding: 2px 8px;")
        status_bar.addWidget(self.status_players)

        sep4 = QLabel("│")
        sep4.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep4)

        self.status_time = QLabel(" ")
        self.status_time.setStyleSheet("font-size: 9pt; color: #6a6a20; padding: 2px 8px;")
        status_bar.addWidget(self.status_time)

        status_widget = QWidget()
        status_widget.setFixedHeight(28)
        status_widget.setLayout(status_bar)
        status_widget.setStyleSheet("background-color: #0a0a00; border-top: 1px solid #1a1a00;")
        layout.addWidget(status_widget)

        # Auto-refresh timer
        self._auto_refresh_timer = QTimer()
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tick)
        self._auto_refresh_timer.start(15000)  # every 15s

        # Status bar time update
        self._time_timer = QTimer()
        self._time_timer.timeout.connect(self._update_time)
        self._time_timer.start(1000)
        self._update_time()

    def _refresh_all(self):
        """Refresh everything: rotation, available maps, gamestate."""
        self.server.send("mission list")
        time.sleep(0.3)
        self.server.send("mission available")
        time.sleep(0.3)
        self.server.send("get gamestate")

    def _auto_refresh_tick(self):
        """Auto-refresh rotation and gamestate periodically."""
        if not self.auto_refresh_check.isChecked():
            return
        if not self.server.proto.connected:
            return
        self.server.send("mission list", quiet=True)
        time.sleep(0.2)
        self.server.send("get gamestate", quiet=True)

    def _update_time(self):
        """Update the time display in the status bar."""
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_time.setText(f" {now} ")

    def _toggle_auto_refresh(self, checked):
        """Toggle auto refresh on/off."""
        if checked:
            self._auto_refresh_timer.start(15000)
        else:
            self._auto_refresh_timer.stop()

    def _parse_mission_name(self, filename):
        """Extract a human-readable mission name from a filename.
        e.g. 'AS-Teotihuacan.bms' -> 'AS-Teotihuacan'
        """
        name = filename
        for ext in ('.bms', '.npaj', '.npj'):
            if name.lower().endswith(ext):
                name = name[:-len(ext)]
        return name

    def _add_rotation_row(self, filename, play_count=1, is_current=False):
        """Add a row to the Mission Cycle table."""
        row = self.rotation_table.rowCount()
        self.rotation_table.insertRow(row)
        name = self._parse_mission_name(filename)
        name_item = QTableWidgetItem(name)
        self.rotation_table.setItem(row, 0, name_item)

        r_item = QTableWidgetItem(str(play_count))
        r_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rotation_table.setItem(row, 1, r_item)

        file_item = QTableWidgetItem(filename)
        self.rotation_table.setItem(row, 2, file_item)

        if is_current:
            for col in range(3):
                item = self.rotation_table.item(row, col)
                if item:
                    item.setBackground(QColor("#2a2a00"))
                    item.setForeground(QColor("#ffd700"))

    def update_missions(self, missions: list):
        """Update the current rotation from mission list response."""
        try:
            # Don't wipe rotation if server returns empty (happens during map transitions)
            if not missions:
                return

            # Skip rebuild if data hasn't changed (prevents flicker from polling)
            if missions == getattr(self, '_last_missions_raw', None):
                return
            self._last_missions_raw = list(missions)

            # Suppress repaints to prevent flicker
            self.setUpdatesEnabled(False)

            # Remember which map was selected
            prev_row = self.rotation_table.currentRow()
            prev_name = None
            if prev_row >= 0 and prev_row < len(self._rotation_maps):
                prev_name = self._rotation_maps[prev_row]

            self._rotation_maps.clear()
            self.rotation_table.setRowCount(0)

            restored = -1
            for m in missions:
                name = m.split(' - ')[0].strip() if ' - ' in m else m.strip()
                if ':' in name[:5]:
                    name = name.split(':', 1)[1].strip()
                if not name:
                    continue
                row_idx = len(self._rotation_maps)
                self._rotation_maps.append(name)
                is_current = '<CURRENT MISSION>' in m or '<NEXT MISSION>' in m
                play_count = 2 if '(2x)' in m else 1
                self._add_rotation_row(name, play_count, is_current)

                # Track which row to restore
                if prev_name and name == prev_name:
                    restored = row_idx

                # Update bottom status bar with current map
                if is_current:
                    self.status_info.setText(f" Map: {name} ")
                    # Push current map name to main status bar
                    if hasattr(self, '_main_window') and self._main_window:
                        self._main_window.status_map_label.setText(f"Map: {name}")

            # Restore selection
            if restored >= 0:
                self.rotation_table.selectRow(restored)
            elif prev_name is None and self.rotation_table.rowCount() > 0:
                pass  # Don't auto-select if nothing was selected before
        except Exception as e:
            print(f"[WolfRAT] update_missions error: {e}")
        finally:
            self.setUpdatesEnabled(True)

    def update_available_maps(self, data: str):
        """Update the available maps table from mission available response."""
        try:
            self.setUpdatesEnabled(False)
            self._all_maps = []
            self.available_table.setRowCount(0)

            for line in data.split('\n'):
                line = line.strip()
                if not line:
                    continue
                # Format: "0. MAPNAME.bms (Description)"
                if '. ' in line[:6]:
                    parts = line.split('. ', 1)
                    if len(parts) >= 2:
                        rest = parts[1].strip()
                        if ' (' in rest:
                            filename = rest.split(' (')[0].strip()
                            desc = rest.split(' (')[1].rstrip(')').strip()
                        else:
                            filename = rest
                            desc = ""
                        self._all_maps.append({'file': filename, 'desc': desc})

            self._populate_available_table()
        except Exception as e:
            print(f"[WolfRAT] update_available_maps error: {e}")
        finally:
            self.setUpdatesEnabled(True)

    def _populate_available_table(self):
        """Fill the available maps table respecting current filter."""
        search = self.search_input.text().strip().lower()
        mode = self.mode_filter.currentText()
        self.available_table.setRowCount(0)

        for m in self._all_maps:
            filename = m['file']
            desc = m['desc']
            # Use the server's mission name if available, otherwise strip extension from filename
            name = desc if desc else self._parse_mission_name(filename)
            # Filter
            if search:
                combined = f"{name} {filename} {desc}".lower()
                if search not in combined:
                    continue
            if mode != 'All Modes' and not filename.upper().startswith(mode.upper()):
                continue

            row = self.available_table.rowCount()
            self.available_table.insertRow(row)
            self.available_table.setItem(row, 0, QTableWidgetItem(name))
            self.available_table.setItem(row, 1, QTableWidgetItem(filename))

    def _filter_maps(self):
        self._populate_available_table()

    def _add_to_rotation(self, count="1x"):
        """Add selected available maps to rotation."""
        selected = self.available_table.selectionModel().selectedRows()
        rows = sorted(set(idx.row() for idx in selected))
        for row in rows:
            file_item = self.available_table.item(row, 1)
            if file_item:
                self._add_map_to_rotation(file_item.text(), count)

    def _double_click_add(self, index):
        """Double-click on available map -> add to rotation (1x)."""
        row = index.row()
        file_item = self.available_table.item(row, 1)
        if file_item:
            self._add_map_to_rotation(file_item.text(), "1x")

    def _available_context_menu(self, pos):
        """Right-click on available maps table."""
        row = self.available_table.indexAt(pos).row()
        if row < 0:
            return
        file_item = self.available_table.item(row, 1)
        if not file_item:
            return
        filename = file_item.text()
        name = self._parse_mission_name(filename)

        menu = QMenu(self)
        add1 = menu.addAction(f"Add {name} — Play Once")
        add1.triggered.connect(lambda checked=False, f=filename: self._add_map_to_rotation(f, "1x"))
        add2 = menu.addAction(f"Add {name} — Play Twice")
        add2.triggered.connect(lambda checked=False, f=filename: self._add_map_to_rotation(f, "2x"))
        menu.exec(self.available_table.mapToGlobal(pos))

    def _add_map_to_rotation(self, filename, count="1x"):
        """Add a map to rotation and send to server."""
        if filename in self._rotation_maps:
            return
        if count == "1x":
            self.server.send(f"mission add {filename} 0")
            play_count = 1
        else:
            self.server.send(f"mission add {filename}")
            play_count = 2
        time.sleep(0.2)
        self._rotation_maps.append(filename)
        self._add_rotation_row(filename, play_count, False)
        self._presets['_auto'] = self._build_auto_preset()
        self._save_presets()
        self.server._log(f"Added {filename} to rotation ({count})")

    def _add_all_visible(self, count="1x"):
        """Add all visible (filtered) available maps to rotation."""
        for row in range(self.available_table.rowCount()):
            file_item = self.available_table.item(row, 1)
            if file_item:
                self._add_map_to_rotation(file_item.text(), count)

    def _rotation_context_menu(self, pos):
        """Right-click context menu for Mission Cycle table."""
        row = self.rotation_table.indexAt(pos).row()
        if row < 0:
            row = self.rotation_table.currentRow()
        if row < 0 or row >= len(self._rotation_maps):
            return

        # Capture the filename — not the row index — so it survives table rebuilds
        filename = self._rotation_maps[row]
        name = self._parse_mission_name(filename)
        menu = QMenu(self)

        run_action = menu.addAction(f"Run {name} Now")
        run_action.triggered.connect(lambda checked=False, f=filename: self._switch_to_map_by_name(f))

        setnext_action = menu.addAction("Set as Next Mission")
        setnext_action.triggered.connect(lambda checked=False, f=filename: self._set_next_mission_by_name(f))

        menu.addSeparator()

        remove_action = menu.addAction("Remove from Rotation")
        remove_action.triggered.connect(lambda checked=False, f=filename: self._remove_from_rotation_by_name(f))

        menu.exec(self.rotation_table.mapToGlobal(pos))

    def _switch_to_map(self, row):
        """Run this map now — queue it and trigger cycle."""
        if 0 <= row < len(self._rotation_maps):
            name = self._rotation_maps[row]
            self.server._log(f"Running map: {name} (index {row})")
            self.server.send(f"MISSION SETNEXT {row}")
            time.sleep(0.3)
            self.server.send("GOTO GAMESTATE")

    def _run_selected_map(self):
        """Run This Map button — switch to the selected map now."""
        row = self.rotation_table.currentRow()
        if row >= 0:
            self._switch_to_map(row)

    def _set_next_mission(self, row):
        """Set Next Mission — queue map without cycling."""
        if 0 <= row < len(self._rotation_maps):
            name = self._rotation_maps[row]
            self.server.send(f"MISSION SETNEXT {row}")
            self.server._log(f"Next mission set to: {name} (index {row})")

    def _set_play_count(self, row, count):
        """Set how many times a map plays (1x or 2x). Updates 'r' column."""
        if row >= self.rotation_table.rowCount():
            return
        r_item = self.rotation_table.item(row, 1)
        if r_item:
            r_item.setText(str(count))

    def _remove_from_rotation_at(self, row):
        """Remove a specific map by row index."""
        if row >= len(self._rotation_maps):
            return
        self.server.send(f"mission remove {row}")
        time.sleep(0.3)
        self._rotation_maps.pop(row)
        self.rotation_table.removeRow(row)
        self._presets['_auto'] = self._build_auto_preset()
        self._save_presets()

    def _remove_from_rotation(self):
        """Remove selected map from rotation (button click)."""
        row = self.rotation_table.currentRow()
        if row >= 0 and self.rotation_table.selectionModel().hasSelection():
            self._remove_from_rotation_at(row)

    def _find_rotation_row(self, filename):
        """Find the current row index for a filename in the rotation table."""
        for i in range(self.rotation_table.rowCount()):
            item = self.rotation_table.item(i, 2)
            if item and item.text() == filename:
                return i
        return -1

    def _switch_to_map_by_name(self, filename):
        """Run a map by filename — looks up current row at execution time."""
        row = self._find_rotation_row(filename)
        if row >= 0:
            self._switch_to_map(row)

    def _set_next_mission_by_name(self, filename):
        """Set next mission by filename."""
        row = self._find_rotation_row(filename)
        if row >= 0:
            self._set_next_mission(row)

    def _remove_from_rotation_by_name(self, filename):
        """Remove a map by filename — looks up current row at execution time."""
        row = self._find_rotation_row(filename)
        if row >= 0:
            self._remove_from_rotation_at(row)

    def _clear_rotation(self):
        reply = QMessageBox.question(
            self, "Clear Rotation",
            "Remove all maps from the rotation?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.server.send("mission clear")
            self._rotation_maps.clear()
            self.rotation_table.setRowCount(0)

    def _move_up(self):
        row = self.rotation_table.currentRow()
        if row > 0:
            self._swap_rotation_rows(row, row - 1)
            self.rotation_table.setCurrentCell(row - 1, 0)

    def _move_down(self):
        row = self.rotation_table.currentRow()
        if row < self.rotation_table.rowCount() - 1:
            self._swap_rotation_rows(row, row + 1)
            self.rotation_table.setCurrentCell(row + 1, 0)

    def _swap_rotation_rows(self, r1, r2):
        """Swap two rows in the rotation table and backing list."""
        # Swap backing list
        self._rotation_maps[r1], self._rotation_maps[r2] = self._rotation_maps[r2], self._rotation_maps[r1]

        # Swap table cell contents
        for col in range(self.rotation_table.columnCount()):
            item1 = self.rotation_table.takeItem(r1, col)
            item2 = self.rotation_table.takeItem(r2, col)
            self.rotation_table.setItem(r1, col, item2)
            self.rotation_table.setItem(r2, col, item1)

    def _on_rotation_reorder(self):
        """Drag-drop reorder — rebuild _rotation_maps from table."""
        self._rotation_maps = []
        for i in range(self.rotation_table.rowCount()):
            file_item = self.rotation_table.item(i, 2)
            if file_item:
                self._rotation_maps.append(file_item.text())

    def auto_apply_rotation(self):
        """Called on connect. Applies saved rotation automatically."""
        saved = self._presets.get('_auto', [])
        if not saved:
            return

        self.server._log(f"Auto-applying saved rotation ({len(saved)} maps)...")
        time.sleep(1.0)

        self.server.send("mission clear")
        time.sleep(0.5)

        self._rotation_maps = []
        self.rotation_table.setRowCount(0)
        for entry in saved:
            if isinstance(entry, dict):
                filename = entry['file']
                count = entry.get('count', 1)
            else:
                filename = entry
                count = 1
            if count == 1:
                self.server.send(f"mission add {filename} 0")
            else:
                self.server.send(f"mission add {filename}")
            time.sleep(0.2)
            self._rotation_maps.append(filename)
            self._add_rotation_row(filename, count, False)

        self.server._log(f"Rotation applied: {len(saved)} maps")

    def _set_next_map(self):
        """Set Next Mission button — queue selected map without cycling."""
        row = self.rotation_table.currentRow()
        if 0 <= row < len(self._rotation_maps):
            name = self._rotation_maps[row]
            self.server.send(f"MISSION SETNEXT {row}")
            self.server._log(f"Next mission set to: {name} (index {row})")

    def _save_preset(self):
        try:
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "Save Rotation", "Preset name:")
            if ok and name.strip():
                name = name.strip()
                # Read filename + play count from the table
                maps = []
                for row in range(self.rotation_table.rowCount()):
                    file_item = self.rotation_table.item(row, 2)
                    r_item = self.rotation_table.item(row, 1)
                    if file_item:
                        filename = file_item.text()
                        count = int(r_item.text()) if r_item else 1
                        maps.append({"file": filename, "count": count})
                self._presets[name] = maps
                self._save_presets()
                if self.preset_combo.findText(name) < 0:
                    self.preset_combo.addItem(name)
                self.preset_combo.setCurrentText(name)
        except Exception as e:
            self.server._log(f"Save preset error: {e}")

    def _load_preset(self):
        name = self.preset_combo.currentText()
        if name == "(none)" or name not in self._presets:
            return

        reply = QMessageBox.question(
            self, "Load Preset",
            f"Replace current rotation with '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.server.send("mission clear")
        time.sleep(0.5)

        self._rotation_maps = []
        self.rotation_table.setRowCount(0)
        for entry in self._presets[name]:
            # Handle both old format (string) and new format (dict with file + count)
            if isinstance(entry, dict):
                mapname = entry['file']
                count = entry.get('count', 1)
            else:
                mapname = entry
                count = 1
            if count == 1:
                self.server.send(f"mission add {mapname} 0")
            else:
                self.server.send(f"mission add {mapname}")
            time.sleep(0.2)
            self._rotation_maps.append(mapname)
            self._add_rotation_row(mapname, count, False)

    def _ok_clicked(self):
        """OK button — apply rotation and show feedback."""
        self._presets['_auto'] = self._build_auto_preset()
        self._save_presets()
        self.server._log("Rotation saved and applied.")

    def _cancel_clicked(self):
        """Cancel button — discard unsaved changes."""
        self.server._log("Changes discarded.")

    def _review_event_log(self):
        """Review Event Log button."""
        self.server.send("eventlog")
        self.server._log("Requested event log.")

    def _review_chat_log(self):
        """Review Chat Log button."""
        self.server.send("chat get")
        self.server._log("Requested chat log.")

    def update_gamestate(self, state: dict):
        """Update gamestate in the status bar and current map info."""
        try:
            mode = state.get('mode', '')
            if mode:
                self.status_mode.setText(f" Game: {mode} ")
        except Exception as e:
            print(f"[WolfRAT] update_gamestate error: {e}")

    def update_connected(self, connected):
        """Update the connection status in the bottom bar."""
        try:
            if connected:
                self.status_connected.setText(" ● Connected ")
                self.status_connected.setStyleSheet("font-size: 9pt; color: #50ff50; padding: 2px 8px;")
            else:
                self.status_connected.setText(" ● Disconnected ")
                self.status_connected.setStyleSheet("font-size: 9pt; color: #ff6040; padding: 2px 8px;")
        except Exception as e:
            print(f"[WolfRAT] update_connected error: {e}")

    def update_player_count(self, players):
        """Update player count in the bottom status bar."""
        try:
            count = len(players) if players else 0
            self.status_players.setText(f" Players: {count} ")
        except Exception as e:
            print(f"[WolfRAT] update_player_count error: {e}")

    def show_command_feedback(self, text):
        """Show a command in the status bar briefly."""
        self.status_command.setText(f" {text} ")
        QTimer.singleShot(3000, lambda: self.status_command.setText(" "))


class SettingsTab(QWidget):
    """Server settings — matches original WolfRAT 0.95 layout.
    LEFT: Toggles + Sliders | CENTER: Rules/Voting/Ping/Time/Passwords
    RIGHT: Weapons Matrix | FAR RIGHT: OK/Cancel + Auto Refresh"""

    CHECKBOX_SETTINGS = {
        "autoBalanceOnRecycle": "AutoBalance on Recycle",
        "friendlyFire": "Friendly Fire",
        "friendlyTags": "Friendly Tags",
        "tracers": "Tracers",
        "fatBullets": "Fat Bullets",
        "oneShotKill": "One Shot Kills",
    }

    SLIDER_SETTINGS = {
        "startDelay": ("Start Delay", 0, 120, 1, "s"),
        "kothLimit": ("KOTH Limit", 0, 600, 1, "n"),
        "killLimit": ("Kill Limit", 0, 500, 1, "n"),
        "armoryTimer": ("Armory Timer", 0, 300, 1, "s"),
        "maxFriendlyKills": ("Max TKills", 0, 999, 1, "n"),
        "maxScore": ("Max Score", 0, 999, 1, "n"),
    }

    # Standard JO weapons (editable list)
    WEAPON_LIST = [
        "M4A1", "M16A2", "AK-47", "AKS-74U", "G36C", "MP5", "MP5-SD",
        "P90", "UMP45", "Spas12", "USAS12", "M249", "RPK", "Dragunov",
        "Barrett", "M40", "PSG1", "SOCOM", "Glock18", "Beretta", "DEagle",
        "Colt", "M60", "FN FAL", "Steyr", "LR300", "G3A3", "SG552",
        "Binoculars", "C4", "Claymore", "Grenade", "Flashbang", "Smoke",
        "Knife", "MedKit", "Binocular", "RPG", "M203", "GP25",
    ]

    def __init__(self, server):
        super().__init__()
        self.server = server
        self._checkboxes = {}
        self._sliders = {}
        self._weapon_rows = {}
        self._loading = False
        self._auto_refresh = False
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._build_ui()

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(6)

        # ============================================================
        # LEFT COLUMN — Toggles + Sliders + Action Buttons
        # ============================================================
        left_col = QVBoxLayout()

        # --- Checkboxes ---
        toggle_group = QGroupBox("Server Settings")
        toggle_layout = QGridLayout()
        row = 0
        for key, label in self.CHECKBOX_SETTINGS.items():
            cb = QCheckBox(label)
            cb.setToolTip(f"Toggle {label}")
            cb.stateChanged.connect(lambda state, k=key: self._on_toggle(k, state))
            toggle_layout.addWidget(cb, row, 0, 1, 2)
            self._checkboxes[key] = cb
            row += 1
        toggle_group.setLayout(toggle_layout)
        left_col.addWidget(toggle_group)

        # --- Sliders (proper sliders with synced spinbox, debounced) ---
        slider_group = QGroupBox("Limits & Timers")
        slider_layout = QGridLayout()
        self._debounce_timers = {}
        self._pending_values = {}
        row = 0
        for key, (label, min_val, max_val, step, unit) in self.SLIDER_SETTINGS.items():
            slider_layout.addWidget(QLabel(f"{label} [{unit}]:"), row, 0)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(min_val, max_val)
            slider.setSingleStep(step)
            slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    background: #1a1a00;
                    height: 8px;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #e8c840;
                    width: 16px;
                    height: 16px;
                    margin: -4px 0;
                    border-radius: 8px;
                }
                QSlider::handle:horizontal:hover {
                    background: #ffd700;
                }
                QSlider::sub-page:horizontal {
                    background: #3a3a00;
                    border-radius: 4px;
                }
            """)

            spin = QSpinBox()
            spin.setRange(min_val, max_val)
            spin.setSingleStep(step)
            spin.setFixedWidth(70)
            spin.setStyleSheet("""
                QSpinBox {
                    background-color: #0a0a00;
                    color: #e8c840;
                    border: 1px solid #3a3a00;
                    padding: 4px;
                    border-radius: 4px;
                    font-size: 11pt;
                    font-weight: bold;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    width: 0;
                    height: 0;
                    border: none;
                }
                QSpinBox:focus {
                    border: 1px solid #e8c840;
                    background-color: #1a1a00;
                }
            """)

            # Sync slider <-> spinbox (no server spam)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)

            # Debounce: only send to server 800ms after last change
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._send_debounced(k))
            self._debounce_timers[key] = timer

            # When spinbox changes (user typed or slider moved), start debounce
            spin.valueChanged.connect(lambda val, k=key: self._on_slider_changed(k, val))

            # When slider is released, send immediately
            slider.sliderReleased.connect(lambda k=key: self._send_debounced(k))

            slider_layout.addWidget(slider, row, 1)
            slider_layout.addWidget(spin, row, 2)
            self._sliders[key] = (slider, spin)
            row += 1
        slider_group.setLayout(slider_layout)
        left_col.addWidget(slider_group)

        # --- Bottom action buttons ---
        btn_row = QHBoxLayout()
        lock_btn = SatisfyingButton("Lock")
        lock_btn.setToolTip("Lock/unlock the server (set password to lock)")
        lock_btn.clicked.connect(self._lock_server)
        btn_row.addWidget(lock_btn)

        refresh_btn = SatisfyingButton("Refresh")
        refresh_btn.setToolTip("Refresh all settings from server")
        refresh_btn.clicked.connect(self._do_refresh)
        btn_row.addWidget(refresh_btn)

        load_btn = SatisfyingButton("Load")
        load_btn.setToolTip("Load settings from server")
        load_btn.clicked.connect(self._do_refresh)
        btn_row.addWidget(load_btn)

        save_btn = SatisfyingButton("Save")
        save_btn.setToolTip("Save current settings to local file")
        save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(save_btn)

        left_col.addLayout(btn_row)
        left_col.addStretch()

        # ============================================================
        # CENTER COLUMN — Rules, Voting, Ping, Time, Passwords
        # ============================================================
        center_col = QVBoxLayout()

        # --- Ping Restrictions ---
        ping_group = QGroupBox("Ping Restrictions")
        ping_layout = QGridLayout()

        self.ping_min_cb = QCheckBox("Do Minimum Ping Check")
        self.ping_min_cb.stateChanged.connect(lambda s: self._on_toggle("pingMinCheck", s))
        ping_layout.addWidget(self.ping_min_cb, 0, 0, 1, 2)
        self._checkboxes["pingMinCheck"] = self.ping_min_cb

        ping_layout.addWidget(QLabel("  Min ping [ms]:"), 1, 0)
        self.ping_min_val = QSpinBox()
        self.ping_min_val.setRange(0, 999)
        self.ping_min_val.setFixedWidth(80)
        self.ping_min_val.valueChanged.connect(lambda v: self._on_slider("pingMin", v))
        ping_layout.addWidget(self.ping_min_val, 1, 1)
        self._sliders["pingMin"] = self.ping_min_val

        self.ping_max_cb = QCheckBox("Do Maximum Ping Check")
        self.ping_max_cb.stateChanged.connect(lambda s: self._on_toggle("pingMaxCheck", s))
        ping_layout.addWidget(self.ping_max_cb, 2, 0, 1, 2)
        self._checkboxes["pingMaxCheck"] = self.ping_max_cb

        ping_layout.addWidget(QLabel("  Max ping [ms]:"), 3, 0)
        self.ping_max_val = QSpinBox()
        self.ping_max_val.setRange(0, 999)
        self.ping_max_val.setFixedWidth(80)
        self.ping_max_val.valueChanged.connect(lambda v: self._on_slider("pingMax", v))
        ping_layout.addWidget(self.ping_max_val, 3, 1)
        self._sliders["pingMax"] = self.ping_max_val

        ping_group.setLayout(ping_layout)
        center_col.addWidget(ping_group)

        # --- Time Configurations ---
        time_group = QGroupBox("Time Configuration")
        time_layout = QGridLayout()

        time_layout.addWidget(QLabel("Set Time of Day:"), 0, 0)
        self.tod_combo = QComboBox()
        self.tod_combo.addItems(["Def", "0000", "0100", "0200", "0300", "0400", "0500",
                                  "0600", "0700", "0800", "0900", "1000", "1100",
                                  "1200", "1300", "1400", "1500", "1600", "1700",
                                  "1800", "1900", "2000", "2100", "2200", "2300"])
        self.tod_combo.currentTextChanged.connect(lambda v: self._on_combo("timeOfDay", v) if v != "Def" else None)
        time_layout.addWidget(self.tod_combo, 0, 1)

        time_layout.addWidget(QLabel("24hr passes in (min):"), 1, 0)
        self.game_pass_combo = QComboBox()
        self.game_pass_combo.addItems(["Def", "5", "10", "15", "20", "30", "45", "60", "90", "120"])
        self.game_pass_combo.currentTextChanged.connect(lambda v: self._on_combo("gamePass", v) if v != "Def" else None)
        time_layout.addWidget(self.game_pass_combo, 1, 1)

        time_group.setLayout(time_layout)
        center_col.addWidget(time_group)

        # --- Passwords & Title ---
        pw_group = QGroupBox("Passwords && Title")
        pw_layout = QGridLayout()

        pw_layout.addWidget(QLabel("Server Password:"), 0, 0)
        self.pw_server = QLineEdit()
        self.pw_server.setPlaceholderText("Server password...")
        pw_layout.addWidget(self.pw_server, 0, 1)
        pw_set1 = SatisfyingButton("Set")
        pw_set1.clicked.connect(lambda: self._set_password("serverPassword", self.pw_server.text()))
        pw_layout.addWidget(pw_set1, 0, 2)
        pw_clr1 = SatisfyingButton("Clear")
        pw_clr1.clicked.connect(lambda: self._clear_password("serverPassword", self.pw_server))
        pw_layout.addWidget(pw_clr1, 0, 3)

        pw_layout.addWidget(QLabel("Side A Password:"), 1, 0)
        self.pw_sideA = QLineEdit()
        self.pw_sideA.setPlaceholderText("Side A password...")
        pw_layout.addWidget(self.pw_sideA, 1, 1)
        pw_set2 = SatisfyingButton("Set")
        pw_set2.clicked.connect(lambda: self._set_password("sideAPassword", self.pw_sideA.text()))
        pw_layout.addWidget(pw_set2, 1, 2)
        pw_clr2 = SatisfyingButton("Clear")
        pw_clr2.clicked.connect(lambda: self._clear_password("sideAPassword", self.pw_sideA))
        pw_layout.addWidget(pw_clr2, 1, 3)

        pw_layout.addWidget(QLabel("Side B Password:"), 2, 0)
        self.pw_sideB = QLineEdit()
        self.pw_sideB.setPlaceholderText("Side B password...")
        pw_layout.addWidget(self.pw_sideB, 2, 1)
        pw_set3 = SatisfyingButton("Set")
        pw_set3.clicked.connect(lambda: self._set_password("sideBPassword", self.pw_sideB.text()))
        pw_layout.addWidget(pw_set3, 2, 2)
        pw_clr3 = SatisfyingButton("Clear")
        pw_clr3.clicked.connect(lambda: self._clear_password("sideBPassword", self.pw_sideB))
        pw_layout.addWidget(pw_clr3, 2, 3)

        pw_layout.addWidget(QLabel("Server Title:"), 3, 0)
        self.pw_title = QLineEdit()
        self.pw_title.setPlaceholderText("Server name...")
        pw_layout.addWidget(self.pw_title, 3, 1)
        pw_set4 = SatisfyingButton("Set")
        pw_set4.clicked.connect(lambda: self._set_password("serverName", self.pw_title.text()))
        pw_layout.addWidget(pw_set4, 3, 2)
        pw_clr4 = SatisfyingButton("Clear")
        pw_clr4.clicked.connect(lambda: self._clear_password("serverName", self.pw_title))
        pw_layout.addWidget(pw_clr4, 3, 3)

        pw_group.setLayout(pw_layout)
        center_col.addWidget(pw_group)

        center_col.addStretch()

        # ============================================================
        # RIGHT COLUMN — Weapons Availability Matrix
        # ============================================================
        right_col = QVBoxLayout()

        weapon_group = QGroupBox("Weapons Availability")
        weapon_layout = QVBoxLayout()

        # Global override buttons
        override_row = QHBoxLayout()
        override_row.addWidget(QLabel("Set All:"))
        all_yes_btn = SatisfyingButton("Yes")
        all_yes_btn.setToolTip("Set all weapons to Yes (available)")
        all_yes_btn.clicked.connect(lambda: self._set_all_weapons("1"))
        override_row.addWidget(all_yes_btn)
        all_armory_btn = SatisfyingButton("Armory")
        all_armory_btn.setToolTip("Set all weapons to Armory (spawn with armory)")
        all_armory_btn.clicked.connect(lambda: self._set_all_weapons("2"))
        override_row.addWidget(all_armory_btn)
        all_no_btn = SatisfyingButton("No")
        all_no_btn.setToolTip("Set all weapons to No (disabled)")
        all_no_btn.clicked.connect(lambda: self._set_all_weapons("0"))
        override_row.addWidget(all_no_btn)
        override_row.addStretch()
        weapon_layout.addLayout(override_row)

        update_list_btn = SatisfyingButton("Update List")
        update_list_btn.setToolTip("Refresh weapons list from server")
        update_list_btn.clicked.connect(self._do_refresh)
        weapon_layout.addWidget(update_list_btn)

        # --- WARNING ---
        warn_label = QLabel(
            "⚠️ Warning: Changing weapon/armoury settings live can crash your server.\n"
            "Game corrupts memory pointers when weapon configs change at runtime."
        )
        warn_label.setWordWrap(True)
        warn_label.setStyleSheet(
            "background-color: #1a0000; color: #ff6040; padding: 8px; "
            "border: 1px solid #ff4040; border-radius: 4px; font-size: 9pt; font-weight: bold;"
        )
        weapon_layout.addWidget(warn_label)

        # Weapons table: [Weapon] [Y] [A] [N]
        self.weapon_table = QTableWidget()
        self.weapon_table.setColumnCount(4)
        self.weapon_table.setHorizontalHeaderLabels(["Weapon", "Y", "A", "N"])
        self.weapon_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.weapon_table.setColumnWidth(1, 40)
        self.weapon_table.setColumnWidth(2, 40)
        self.weapon_table.setColumnWidth(3, 40)
        self.weapon_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.weapon_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._populate_weapon_table()
        weapon_layout.addWidget(self.weapon_table)

        weapon_group.setLayout(weapon_layout)
        right_col.addWidget(weapon_group)

        # ============================================================
        # FAR RIGHT — OK / Cancel / Auto Refresh
        # ============================================================
        far_right_col = QVBoxLayout()

        ok_btn = SatisfyingButton("OK")
        ok_btn.setToolTip("Apply and close")
        ok_btn.clicked.connect(lambda: self._show_feedback("Settings applied"))
        far_right_col.addWidget(ok_btn)

        cancel_btn = SatisfyingButton("Cancel")
        cancel_btn.setToolTip("Discard changes")
        cancel_btn.clicked.connect(self._do_refresh)
        far_right_col.addWidget(cancel_btn)

        far_right_col.addSpacing(20)

        self.auto_refresh_cb = QCheckBox("Auto Refresh")
        self.auto_refresh_cb.setToolTip("Poll settings from server every 15s")
        self.auto_refresh_cb.stateChanged.connect(self._toggle_auto_refresh)
        far_right_col.addWidget(self.auto_refresh_cb)

        far_right_col.addStretch()

        # ============================================================
        # ASSEMBLE MAIN LAYOUT
        # ============================================================
        main_layout.addLayout(left_col, 2)
        main_layout.addLayout(center_col, 3)
        main_layout.addLayout(right_col, 3)
        main_layout.addLayout(far_right_col, 0)

    # ---- Weapon table helpers ----

    def _populate_weapon_table(self):
        """Fill the weapons table from WEAPON_LIST."""
        self.weapon_table.setRowCount(len(self.WEAPON_LIST))
        self._weapon_rows = {}
        for i, name in enumerate(self.WEAPON_LIST):
            # Weapon name
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor("#e8c840"))
            self.weapon_table.setItem(i, 0, name_item)

            # Radio-style buttons in columns Y/A/N
            # We use QPushButton styled as radio selectors
            for col, (label, val) in enumerate([("Y", "0"), ("A", "1"), ("N", "2")], start=1):
                btn = QPushButton(label)
                btn.setFixedSize(36, 24)
                btn.setCheckable(True)
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #0a0a00;
                        color: #8a7a20;
                        border: 1px solid #3a3a00;
                        border-radius: 3px;
                        font-size: 9pt;
                        font-weight: bold;
                    }
                    QPushButton:checked {
                        background-color: #2a2a00;
                        color: #e8c840;
                        border: 1px solid #e8c840;
                    }
                """)
                btn.clicked.connect(lambda checked, r=i, v=val, c=col: self._on_weapon_click(r, v, c))
                self.weapon_table.setCellWidget(i, col, btn)

            self._weapon_rows[name] = i

    def _on_weapon_click(self, row, value, clicked_col):
        """Handle weapon Y/A/N button click — ensure only one selected per row."""
        self._loading = True
        for col in range(1, 4):
            w = self.weapon_table.cellWidget(row, col)
            if w:
                w.setChecked(col == clicked_col)
        self._loading = False
        weapon = self.weapon_table.item(row, 0).text()
        key = f"weapon_{weapon}"
        self.server.set_setting(key, value)
        labels = {"0": "Yes", "1": "Armory", "2": "No"}
        self._show_feedback(f"{weapon} set to {labels.get(value, value)}")

    def _set_all_weapons(self, value):
        """Set all weapons to the same availability."""
        labels = {"0": "Yes", "1": "Armory", "2": "No"}
        for row in range(self.weapon_table.rowCount()):
            self._loading = True
            for col in range(1, 4):
                w = self.weapon_table.cellWidget(row, col)
                if w:
                    w.setChecked(col - 1 == int(value))
            self._loading = False
            weapon = self.weapon_table.item(row, 0).text()
            self.server.set_setting(f"weapon_{weapon}", value)
        self._show_feedback(f"All weapons set to {labels.get(value, value)}")

    def _update_weapon_from_settings(self, settings):
        """Update weapon table buttons from server settings."""
        for key, val in settings.items():
            if key.lower().startswith("weapon_"):
                weapon_name = key[7:]  # strip "weapon_"
                if weapon_name in self._weapon_rows:
                    row = self._weapon_rows[weapon_name]
                    self._loading = True
                    for col in range(1, 4):
                        w = self.weapon_table.cellWidget(row, col)
                        if w:
                            w.setChecked(str(col - 1) == str(val))
                    self._loading = False

    # ---- Settings action helpers ----

    def _on_toggle(self, key, state):
        if self._loading:
            return
        val = "1" if state == 2 else "0"
        self.server.set_setting(key, val)
        label = self.CHECKBOX_SETTINGS.get(key, key)
        self._show_feedback(f"{label} = {'ON' if val == '1' else 'OFF'}")

    def _on_slider_changed(self, key, val):
        """Called on every value change — stores pending value and starts debounce timer."""
        if self._loading:
            return
        self._pending_values[key] = val
        self._debounce_timers[key].start(800)  # 800ms debounce

    def _send_debounced(self, key):
        """Send the pending value to server (called by debounce timer or slider release)."""
        if key in self._pending_values:
            val = self._pending_values.pop(key)
            self._debounce_timers[key].stop()
            self.server.set_setting(key, str(val))
            label = self.SLIDER_SETTINGS.get(key, (key,))[0]
            self._show_feedback(f"{label} = {val}")

    def _on_slider(self, key, val):
        """Direct send (for non-slider spinboxes like ping limits)."""
        if self._loading:
            return
        self.server.set_setting(key, str(val))
        label = self.SLIDER_SETTINGS.get(key, (key,))[0]
        self._show_feedback(f"{label} = {val}")

    def _on_combo(self, key, val):
        if self._loading or val == "Def":
            return
        self.server.set_setting(key, val)
        self._show_feedback(f"{key} = {val}")

    def _set_password(self, key, value):
        self.server.set_setting(key, value)
        self._show_feedback(f"{key} = '{value}'" if value else f"{key} cleared")

    def _clear_password(self, key, field):
        self.server.set_setting(key, "")
        field.clear()
        self._show_feedback(f"{key} cleared")

    def _lock_server(self):
        """Lock/unlock all settings controls to prevent accidental changes."""
        locked = not getattr(self, '_controls_locked', False)
        self._controls_locked = locked

        # Disable/enable all checkboxes
        for cb in self._checkboxes.values():
            cb.setEnabled(not locked)

        # Disable/enable all sliders
        for spin in self._sliders.values():
            if isinstance(spin, tuple):
                spin[0].setEnabled(not locked)  # slider
                spin[1].setEnabled(not locked)  # spinbox
            else:
                spin.setEnabled(not locked)

        # Disable/enable weapon table
        self.weapon_table.setEnabled(not locked)

        # Disable/enable password fields
        for field in [self.pw_server, self.pw_sideA, self.pw_sideB, self.pw_title]:
            field.setEnabled(not locked)

        # Disable/enable combos
        self.tod_combo.setEnabled(not locked)
        self.game_pass_combo.setEnabled(not locked)

        # Update lock button appearance
        if locked:
            self._show_feedback("Settings LOCKED — controls disabled")
        else:
            self._show_feedback("Settings UNLOCKED — controls enabled")

    def _toggle_auto_refresh(self, state):
        self._auto_refresh = state == 2
        if self._auto_refresh:
            self._refresh_timer.start(15000)
            self._show_feedback("Auto-refresh ON (every 15s)")
        else:
            self._refresh_timer.stop()
            self._show_feedback("Auto-refresh OFF")

    def _do_refresh(self):
        """Re-fetch settings from server."""
        self.server.send("get gamesettings")
        self._show_feedback("Settings refreshed")

    def _save_settings(self):
        """Save current settings to a local JSON file."""
        import json
        settings = {}
        for key, cb in self._checkboxes.items():
            settings[key] = "1" if cb.isChecked() else "0"
        for key, spin in self._sliders.items():
            if isinstance(spin, tuple):
                settings[key] = str(spin[1].value())  # spin is index 1
            else:
                settings[key] = str(spin.value())
        try:
            path = os.path.join(os.path.dirname(__file__), "settings_preset.json")
            with open(path, "w") as f:
                json.dump(settings, f, indent=2)
            self._show_feedback(f"Settings saved to {os.path.basename(path)}")
        except Exception as e:
            self._show_feedback(f"Save failed: {e}")

    def _show_feedback(self, msg):
        try:
            window = self.window()
            if hasattr(window, 'show_feedback'):
                window.show_feedback(msg)
        except Exception:
            pass

    # ---- Update from server ----

    def update_settings(self, settings: dict):
        """Update all UI controls from server settings dict."""
        all_keys = sorted(settings.keys())
        print(f"[WolfRAT] SettingsTab.update_settings: {len(settings)} keys")
        print(f"[WolfRAT] ALL SERVER KEYS: {all_keys}")
        # Build case-insensitive lookup (server sends CamelCase, we use camelCase)
        settings_lower = {k.lower(): v for k, v in settings.items()}
        self._loading = True
        try:
            # Checkboxes
            for key, cb in self._checkboxes.items():
                if key.lower() in settings_lower:
                    val = settings_lower[key.lower()]
                    cb.setChecked(val.lower() in ("1", "true", "on"))
                    print(f"[WolfRAT] Checkbox {key} = {val}")

            # Sliders
            for key, spin in self._sliders.items():
                if key.lower() in settings_lower:
                    try:
                        val = int(float(settings_lower[key.lower()]))
                        if isinstance(spin, tuple):
                            spin[1].setValue(val)  # spinbox is index 1, slider auto-syncs
                        else:
                            spin.setValue(val)
                        print(f"[WolfRAT] Slider {key} = {val}")
                    except (ValueError, TypeError) as e:
                        print(f"[WolfRAT] Slider {key} ERROR: {e}")
                else:
                    print(f"[WolfRAT] Slider {key} NOT FOUND (looking for {key.lower()})")

            # Passwords & Title
            pw_keys = {
                "serverPassword": self.pw_server,
                "sideAPassword": self.pw_sideA,
                "sideBPassword": self.pw_sideB,
                "serverName": self.pw_title,
            }
            for key, field in pw_keys.items():
                if key.lower() in settings_lower:
                    field.setText(settings_lower[key.lower()])

            # Weapons
            self._update_weapon_from_settings(settings)

        finally:
            self._loading = False

class ChatBotTab(QWidget):
    """Chat monitor and auto-moderation tab."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self.bad_words = {}  # {word: action} e.g. {"nigger": "Kick", "cunt": "Warn"}
        self.auto_swap_enabled = True
        self.swap_trigger = "!switch"
        self._seen_chat_raw = set()  # raw text of messages we've already processed
        self._swap_cooldowns = {}  # player_name -> timestamp of last swap
        self._chat_initialized = False  # skip first chat batch (old messages from before we connected)
        self._build_ui()

    def reset_chat(self):
        """Reset chat dedup state on reconnect."""
        self._chat_initialized = False
        self._seen_chat_raw = set()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Chat display
        chat_group = QGroupBox("Live Chat")
        chat_layout = QVBoxLayout()
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        chat_layout.addWidget(self.chat_display)

        # Send chat
        send_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type a message (max 69 chars)...")
        self.chat_input.setMaxLength(69)
        self.chat_input.textChanged.connect(lambda t: self.char_count.setText(f"{len(t)}/69"))
        self.chat_input.returnPressed.connect(self._send_chat)
        send_layout.addWidget(self.chat_input)
        self.char_count = QLabel("0/69")
        self.char_count.setStyleSheet("color: #666;")
        send_layout.addWidget(self.char_count)

        send_btn = SatisfyingButton("Send")
        send_btn.clicked.connect(self._send_chat)
        send_layout.addWidget(send_btn)
        chat_layout.addLayout(send_layout)

        chat_group.setLayout(chat_layout)
        layout.addWidget(chat_group)

        # Auto-swap feature
        swap_group = QGroupBox("Auto Team Swap (Chat Trigger)")
        swap_layout = QGridLayout()

        self.auto_swap_cb = QCheckBox("Enable auto-swap on chat trigger")
        self.auto_swap_cb.setToolTip("When a player types the trigger word in chat, they get auto-swapped")
        self.auto_swap_cb.setChecked(True)
        swap_layout.addWidget(self.auto_swap_cb, 0, 0, 1, 2)

        swap_layout.addWidget(QLabel("Trigger word:"), 1, 0)
        self.trigger_input = QLineEdit("!switch")
        self.trigger_input.setPlaceholderText("e.g. !switch, !swap, !team")
        swap_layout.addWidget(self.trigger_input, 1, 1)

        swap_group.setLayout(swap_layout)
        layout.addWidget(swap_group)

        # Bad words filter — per-word actions
        filter_group = QGroupBox("Bad Words Filter")
        filter_layout = QVBoxLayout()

        self.bad_words_list = QListWidget()
        filter_layout.addWidget(self.bad_words_list)

        add_layout = QHBoxLayout()
        self.bad_word_input = QLineEdit()
        self.bad_word_input.setPlaceholderText("Add a bad word...")
        add_layout.addWidget(self.bad_word_input)

        self.bad_word_action = QComboBox()
        self.bad_word_action.addItems(["Warn", "Kick", "Ban"])
        self.bad_word_action.setFixedWidth(80)
        add_layout.addWidget(self.bad_word_action)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_bad_word)
        add_layout.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_bad_word)
        add_layout.addWidget(remove_btn)

        filter_layout.addLayout(add_layout)

        hint = QLabel("Each word can have its own action: Warn, Kick, or Ban")
        hint.setStyleSheet("font-size: 9pt; color: #6a6a20;")
        filter_layout.addWidget(hint)

        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)

    def _send_chat(self):
        msg = self.chat_input.text().strip()
        if msg:
            self.server.send_chat(msg)
            self.chat_display.append(f"<span style='color: #e8c840'>[ADMIN] {msg}</span>")
            self.chat_input.clear()

    def _add_bad_word(self):
        word = self.bad_word_input.text().strip().lower()
        action = self.bad_word_action.currentText()
        if word and word not in self.bad_words:
            self.bad_words[word] = action
            self.bad_words_list.addItem(f"{word} → {action}")
            self.bad_word_input.clear()

    def _remove_bad_word(self):
        row = self.bad_words_list.currentRow()
        if row >= 0:
            item = self.bad_words_list.item(row)
            word = item.text().split(' → ')[0].strip()
            self.bad_words.pop(word, None)
            self.bad_words_list.takeItem(row)

    def update_chat(self, messages: list):
        try:
            from wolfrat.protocol import wire_log
        except ImportError:
            wire_log = lambda m: None

        # Skip the first batch — those are old messages from before we connected
        if not self._chat_initialized:
            self._chat_initialized = True
            self._seen_chat_raw = {m.get('raw', '') for m in messages}
            wire_log(f'CHAT: initialized with {len(self._seen_chat_raw)} existing messages')
            return

        # Only process messages we haven't seen before (by raw text)
        new_messages = []
        for msg in messages:
            raw = msg.get('raw', '')
            if raw and raw not in self._seen_chat_raw:
                self._seen_chat_raw.add(raw)
                new_messages.append(msg)

        wire_log(f'CHAT: {len(messages)} total, {len(new_messages)} new, {len(self.bad_words)} bad words registered')

        # Trim seen set to prevent memory growth
        if len(self._seen_chat_raw) > 1000:
            self._seen_chat_raw = set(list(self._seen_chat_raw)[-500:])

        for msg in new_messages:
            text = msg.get('text', '')
            time_str = msg.get('time', '')
            formatted = f"[{time_str}] {text}"
            self.chat_display.append(f"<span style='color: #a89830'>{formatted}</span>")

            # Check for auto-swap trigger
            if self.auto_swap_cb.isChecked():
                trigger = self.trigger_input.text().strip().lower()
                if trigger and trigger in text.lower():
                    # Try to extract player name from chat message
                    # Format is usually: "PlayerName: message" or "PlayerName message"
                    player_name = text.split(':')[0].strip() if ':' in text else text.split()[0].strip()
                    # Find player in current player list
                    found = False
                    for p in self.server.players:
                        if p.get('name', '').lower() == player_name.lower():
                            pid = p.get('id', '')
                            name = p.get('name', player_name)
                            # Cooldown: ignore if this player triggered within last 2 minutes
                            now = time.time()
                            last_swap = self._swap_cooldowns.get(name.lower(), 0)
                            if now - last_swap < 120:
                                remaining = int(120 - (now - last_swap))
                                self.chat_display.append(
                                    f"<span style='color: #a89830'>[SWAP] {name} on cooldown ({remaining}s remaining)</span>")
                            else:
                                self._swap_cooldowns[name.lower()] = now
                                self.chat_display.append(
                                    f"<span style='color: #e8c840'>[SWAP] {name} requested team switch — swapping and respawning</span>")
                                # Swap + kill in background thread so UI doesn't freeze
                                import threading
                                threading.Thread(
                                    target=self.server.swap_and_kill,
                                    args=(pid, name),
                                    daemon=True
                                ).start()
                            found = True
                            break
                    if not found:
                        self.chat_display.append(
                            f"<span style='color: #a89830'>[SWAP] Trigger detected but player '{player_name}' not found in player list</span>")

            # Check for bad words (per-word action)
            for word, action in self.bad_words.items():
                if word in text.lower():
                    # Extract player name from chat message
                    player_name = text.split(':')[0].strip() if ':' in text else text.split()[0].strip()
                    # Find player in player list
                    pid = None
                    display_name = player_name
                    for p in self.server.players:
                        if p.get('name', '').lower() == player_name.lower():
                            pid = p.get('id', '')
                            display_name = p.get('name', player_name)
                            break

                    self.chat_display.append(
                        f"<span style='color: #ff6040'>[FILTER] Bad word '{word}' from {display_name} — Action: {action}</span>")

                    if pid:
                        if action == 'Warn':
                            self.server.warn_player(int(pid), "Watch your language!")
                            time.sleep(0.3)
                            self.server.send_chat(f"{display_name}: watch your language!")
                        elif action == 'Kick':
                            self.server.punt_player(int(pid), "Bad language")
                            time.sleep(0.3)
                            self.server.send_chat(f"{display_name} was kicked for bad language")
                        elif action == 'Ban':
                            self.server.ban_player(int(pid), "Bad language")
                            time.sleep(0.3)
                            self.server.send_chat(f"{display_name} was banned for bad language")

        # Scroll to bottom
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)


class MessagesTab(QWidget):
    """Server messaging — direct, recurring, and welcome messages."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self._recurring_messages = []
        self._recurring_index = 0
        self._recurring_timer = QTimer()
        self._recurring_timer.timeout.connect(self._send_next_recurring)
        self._seen_players = set()
        self._welcome_enabled = True
        self._welcome_message = "Welcome to the server, {player}! Enjoy your stay."
        self._load_config()
        self._build_ui()
        # Auto-start recurring messages if there are any
        if self._recurring_messages:
            self._toggle_recurring()

    def _config_path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_messages.json')

    def _load_config(self):
        try:
            path = self._config_path()
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
                    self._recurring_messages = cfg.get('recurring', [])
                    self._welcome_enabled = cfg.get('welcome_enabled', False)
                    self._welcome_message = cfg.get('welcome_msg', self._welcome_message)
                    self._seen_players = set(cfg.get('seen_players', []))
        except Exception:
            pass

    def _save_config(self):
        try:
            cfg = {
                'recurring': self._recurring_messages,
                'welcome_enabled': self._welcome_enabled,
                'welcome_msg': self._welcome_message,
                'seen_players': list(self._seen_players),
            }
            with open(self._config_path(), 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Recurring messages ---
        recur_group = QGroupBox("Recurring Messages (rotates through list)")
        recur_layout = QVBoxLayout()

        self.recur_list = QListWidget()
        for msg in self._recurring_messages:
            self.recur_list.addItem(msg)
        recur_layout.addWidget(self.recur_list)

        # Add/remove
        add_layout = QHBoxLayout()
        self.recur_input = QLineEdit()
        self.recur_input.setPlaceholderText("Add a recurring message (max 69 chars)...")
        self.recur_input.setMaxLength(69)
        add_layout.addWidget(self.recur_input)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_recurring)
        add_layout.addWidget(add_btn)
        del_btn = QPushButton("Remove")
        del_btn.clicked.connect(self._remove_recurring)
        add_layout.addWidget(del_btn)
        recur_layout.addLayout(add_layout)

        # Timer controls
        timer_layout = QHBoxLayout()
        timer_layout.addWidget(QLabel("Send every:"))
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["5 minutes", "10 minutes", "15 minutes"])
        self.interval_combo.setCurrentIndex(1)  # default 10 min
        timer_layout.addWidget(self.interval_combo)

        self.start_recur_btn = QPushButton("Start")
        self.start_recur_btn.clicked.connect(self._toggle_recurring)
        timer_layout.addWidget(self.start_recur_btn)

        self.recur_status = QLabel("Stopped")
        self.recur_status.setStyleSheet("color: #6a6a20;")
        timer_layout.addWidget(self.recur_status)
        recur_layout.addLayout(timer_layout)

        recur_group.setLayout(recur_layout)
        layout.addWidget(recur_group)

        # --- Welcome messages ---
        welcome_group = QGroupBox("Welcome Messages (first-time joiners)")
        welcome_layout = QVBoxLayout()

        self.welcome_cb = QCheckBox("Enable welcome messages")
        self.welcome_cb.setChecked(self._welcome_enabled)
        self.welcome_cb.toggled.connect(self._toggle_welcome)
        welcome_layout.addWidget(self.welcome_cb)

        welcome_layout.addWidget(QLabel("Welcome message ({player} = player name):"))
        self.welcome_input = QLineEdit(self._welcome_message)
        self.welcome_input.setMaxLength(69)
        self.welcome_input.textChanged.connect(self._update_welcome_msg)
        welcome_layout.addWidget(self.welcome_input)

        welcome_layout.addWidget(QLabel("Recently welcomed:"))
        self.welcome_log = QListWidget()
        self.welcome_log.setMaximumHeight(100)
        welcome_layout.addWidget(self.welcome_log)

        welcome_group.setLayout(welcome_layout)
        layout.addWidget(welcome_group)

        # --- Message log ---
        log_group = QGroupBox("Message Log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

    def _send_message(self):
        msg = self.msg_input.text().strip()
        if msg:
            self.server.announce(msg)
            self.log_text.append(f"[{time.strftime('%H:%M:%S')}] SENT: {msg}")
            self.msg_input.clear()

    def _add_recurring(self):
        msg = self.recur_input.text().strip()
        if msg and msg not in self._recurring_messages:
            self._recurring_messages.append(msg)
            self.recur_list.addItem(msg)
            self.recur_input.clear()
            self._save_config()

    def _remove_recurring(self):
        row = self.recur_list.currentRow()
        if row >= 0:
            self._recurring_messages.pop(row)
            self.recur_list.takeItem(row)
            self._save_config()

    def _toggle_recurring(self):
        if self._recurring_timer.isActive():
            self._recurring_timer.stop()
            self.start_recur_btn.setText("Start")
            self.recur_status.setText("Stopped")
            self.recur_status.setStyleSheet("color: #6a6a20;")
        else:
            if not self._recurring_messages:
                self.log_text.append("[{0}] No recurring messages to send".format(time.strftime('%H:%M:%S')))
                return
            intervals = {0: 300000, 1: 600000, 2: 900000}  # 5, 10, 15 min in ms
            ms = intervals.get(self.interval_combo.currentIndex(), 600000)
            self._recurring_timer.start(ms)
            self.start_recur_btn.setText("Stop")
            label = self.interval_combo.currentText()
            self.recur_status.setText(f"Active — every {label}")
            self.recur_status.setStyleSheet("color: #e8c840; font-weight: bold;")
            self._send_next_recurring()  # send first one immediately

    def _send_next_recurring(self):
        if not self._recurring_messages:
            return
        msg = self._recurring_messages[self._recurring_index % len(self._recurring_messages)]
        self.server.announce(msg)
        self.log_text.append(f"[{time.strftime('%H:%M:%S')}] RECURRING: {msg}")
        self._recurring_index += 1

    def _toggle_welcome(self, checked):
        self._welcome_enabled = checked
        self._save_config()

    def _update_welcome_msg(self, text):
        self._welcome_message = text
        self._save_config()

    def check_new_players(self, players):
        """Called when player list updates. Detects first-time joiners."""
        if not self._welcome_enabled:
            return
        for p in players:
            name = p.get('name', '').strip()
            if name and name not in self._seen_players:
                self._seen_players.add(name)
                msg = self._welcome_message.replace('{player}', name)
                self.welcome_log.addItem(f"[{time.strftime('%H:%M:%S')}] {name} (sending in 40s)")
                self.log_text.append(f"[{time.strftime('%H:%M:%S')}] WELCOME QUEUED: {name}")
                self._save_config()
                # Delay 15 seconds so player finishes loading before they see the message
                QTimer.singleShot(40000, lambda m=msg, n=name: self._send_welcome(m, n))

    def _send_welcome(self, msg, name):
        """Actually send the welcome message after the delay."""
        self.server.announce(msg)
        self.welcome_log.addItem(f"[{time.strftime('%H:%M:%S')}] {name} (sent)")
        self.log_text.append(f"[{time.strftime('%H:%M:%S')}] WELCOME SENT: {name}")


class MissionsStore:
    """Persistent store of available missions (maps) on the server.
    Fetches on connect, saves to JSON, used by Mods tab for !map lookup.
    """

    def __init__(self):
        self._data = {'rotation': [], 'available': [], 'updated': None}
        self._load()

    def _path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_missions.json')

    def _load(self):
        try:
            p = self._path()
            if os.path.exists(p):
                with open(p) as f:
                    self._data = json.load(f)
        except Exception:
            pass

    def _save(self):
        try:
            self._data['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            with open(self._path(), 'w') as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def _clean(raw):
        """Extract clean map name from a raw mission line.
        '0: TD-BattleoftheBulge.bms - (2x) () () <> <NEXT MISSION>' -> 'TD-BattleoftheBulge'
        '0. DM-COD4Killhouse.npj (Description)' -> 'DM-COD4Killhouse'
        '10: TD-BattleoftheBulge.bms - (2x)' -> 'TD-BattleoftheBulge'
        'DM-COD4Killhouse.npj' -> 'DM-COD4Killhouse'
        """
        import re
        name = raw.strip()
        m = re.match(r'^\d+[.:]\s*', name)
        if m:
            name = name[m.end():]
        if ' - ' in name:
            name = name.split(' - ', 1)[0].strip()
        elif ' (' in name:
            name = name.split(' (', 1)[0].strip()
        return name

    @staticmethod
    def _strip_ext(filename):
        """Remove .bms/.npaj/.npj extension."""
        for ext in ('.bms', '.npaj', '.npj'):
            if filename.lower().endswith(ext):
                return filename[:-len(ext)]
        return filename

    def update_rotation(self, missions_list):
        """Update rotation from 'mission list' response (list of raw lines)."""
        if not missions_list:
            return
        self._data['rotation'] = []
        for raw in missions_list:
            full = self._clean(raw)
            if full:
                self._data['rotation'].append({'name': self._strip_ext(full), 'file': full})
        self._save()

    def update_available(self, data_str):
        """Update available maps from 'mission available' response (raw text)."""
        if not data_str:
            return
        self._data['available'] = []
        for line in data_str.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Two formats:
            #   "0. DM-COD4Killhouse.npj (Description)"  (numbered with '. ')
            #   "0: DM-COD4Killhouse.npj - (Description)" (colon)
            filename = None
            desc = ""
            if '. ' in line[:6]:
                parts = line.split('. ', 1)
                if len(parts) >= 2:
                    rest = parts[1].strip()
                    if ' (' in rest:
                        filename = rest.split(' (')[0].strip()
                        desc = rest.split(' (', 1)[1].rstrip(')').strip()
                    else:
                        filename = rest
            elif ':' in line and '.' in line.lower():
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    rest = parts[1].strip()
                    if ' (' in rest:
                        filename = rest.split(' (')[0].strip()
                        desc = rest.split(' (', 1)[1].rstrip(')').strip()
                    elif ' - ' in rest:
                        filename = rest.split(' - ')[0].strip()
                        desc = rest.split(' - ', 1)[1].strip().rstrip(')').strip()
                    else:
                        filename = rest
            if filename:
                # Use description as the display name if available
                name = desc if desc else self._strip_ext(filename)
                self._data['available'].append({
                    'name': name,
                    'file': filename
                })
        self._save()

    def find(self, query):
        """Search for a map by partial name. Returns (name, file, source) or (None, None, None).
        source is 'rotation' or 'available'.
        """
        if not query:
            return None, None, None
        q = query.lower().strip()
        # Strip extension if user typed it
        for ext in ('.bms', '.npaj', '.npj'):
            if q.endswith(ext):
                q = q[:-len(ext)]
                break

        def _match(entry):
            name_lower = entry['name'].lower()
            if q == name_lower:
                return True
            if q in name_lower:
                return True
            # Without prefix (e.g. 'villa' matches 'AS-Villa')
            no_pfx = name_lower
            for pfx in ('as-', 'dm-', 'td-', 'ad-', 'tk-', 'ctf-'):
                if no_pfx.startswith(pfx):
                    no_pfx = no_pfx[len(pfx):]
            return q == no_pfx or q in no_pfx

        # Exact match in rotation first
        for entry in self._data.get('rotation', []):
            if q == entry['name'].lower():
                return entry['name'], entry['file'], 'rotation'
        # Partial match in rotation
        for entry in self._data.get('rotation', []):
            if _match(entry):
                return entry['name'], entry['file'], 'rotation'
        # Exact match in available
        for entry in self._data.get('available', []):
            if q == entry['name'].lower():
                return entry['name'], entry['file'], 'available'
        # Partial match in available
        for entry in self._data.get('available', []):
            if _match(entry):
                return entry['name'], entry['file'], 'available'

        return None, None, None

    @property
    def rotation_count(self):
        return len(self._data.get('rotation', []))

    @property
    def available_count(self):
        return len(self._data.get('available', []))


class ModsTab(QWidget):
    """Moderator management — assign mods, track their commands."""

    def __init__(self, server: ServerManager, missions_store: MissionsStore):
        super().__init__()
        self.server = server
        self.missions_store = missions_store
        self.mods = {}  # {lowercase_name: display_name}
        self._seen_chat_raw = set()  # for dedup
        self._chat_initialized = False
        self._load_config()
        self._build_ui()
        # Populate list from saved mods
        for name in sorted(self.mods.values()):
            self.mod_list.addItem(name)

    def reset_chat(self):
        """Reset chat dedup state on reconnect."""
        self._seen_chat_raw = set()  # for dedup
        self._chat_initialized = False

    def _config_path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_mods.json')

    def _load_config(self):
        try:
            path = self._config_path()
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
                    saved = cfg.get('mods', [])
                    self.mods = {n.lower(): n for n in saved}
        except Exception:
            pass

    def _save_config(self):
        try:
            cfg = {'mods': sorted(self.mods.values())}
            with open(self._config_path(), 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # LEFT: Mod list
        left_col = QVBoxLayout()

        mod_group = QGroupBox("Moderators")
        mod_layout = QVBoxLayout()

        self.mod_list = QListWidget()
        self.mod_list.setStyleSheet("""
            QListWidget {
                background-color: #0a0a00;
                color: #e8c840;
                border: 1px solid #3a3a00;
                font-size: 11pt;
            }
        """)
        mod_layout.addWidget(self.mod_list)

        add_layout = QHBoxLayout()
        self.mod_input = QLineEdit()
        self.mod_input.setPlaceholderText("Player name (exact, case-insensitive)")
        self.mod_input.returnPressed.connect(self._add_mod)
        add_layout.addWidget(self.mod_input)

        add_btn = SatisfyingButton("Add Mod")
        add_btn.clicked.connect(self._add_mod)
        add_layout.addWidget(add_btn)

        remove_btn = SatisfyingButton("Remove")
        remove_btn.clicked.connect(self._remove_mod)
        add_layout.addWidget(remove_btn)

        mod_layout.addLayout(add_layout)

        # Quick-add from current players
        quick_layout = QHBoxLayout()
        quick_layout.addWidget(QLabel("Quick add:"))
        self.player_combo = QComboBox()
        self.player_combo.setPlaceholderText("Select online player...")
        quick_layout.addWidget(self.player_combo)

        quick_add_btn = SatisfyingButton("+Mod")
        quick_add_btn.setFixedWidth(60)
        quick_add_btn.clicked.connect(self._quick_add_mod)
        quick_layout.addWidget(quick_add_btn)

        mod_layout.addLayout(quick_layout)
        mod_group.setLayout(mod_layout)
        left_col.addWidget(mod_group)

        # Permissions info
        perms_group = QGroupBox("Mod Permissions")
        perms_layout = QVBoxLayout()
        perms_label = QLabel(
            "Mods can use these commands in game chat:\n\n"
            "  !warn <player> [reason]  — Warn a player\n"
            "  !kick <player> [reason]  — Kick a player\n"
            "  !ban <player>            — Ban a player\n"
            "  !swap <player>           — Swap to other team\n"
            "  !kill <player>           — Kill a player\n"
            "  !next                    — Skip to next map\n"
            "  !map <name>              — Switch to a map\n"
            "  !add <name>              — Add map to rotation\n"
            "  !remove <name>           — Remove map from rotation\n"
            "  !switch                  — Switch own team (any player)\n\n"
            "Map names can be partial/fuzzy: !map treasure\n"
            "Mods CANNOT use admin console or send raw commands."
        )
        perms_label.setStyleSheet("font-size: 9pt; color: #a89830; line-height: 1.4;")
        perms_label.setWordWrap(True)
        perms_layout.addWidget(perms_label)
        perms_group.setLayout(perms_layout)
        left_col.addWidget(perms_group)

        left_col.addStretch()
        layout.addLayout(left_col, 1)

        # RIGHT: Map database + Mod activity log
        right_col = QVBoxLayout()

        # Map database info
        maps_group = QGroupBox("Map Database")
        maps_layout = QVBoxLayout()
        self.maps_info = QLabel(
            f"Rotation: {self.missions_store.rotation_count} maps | "
            f"Available: {self.missions_store.available_count} maps"
        )
        self.maps_info.setStyleSheet("font-size: 10pt; color: #a89830;")
        maps_layout.addWidget(self.maps_info)

        refresh_maps_btn = SatisfyingButton("Refresh Maps from Server")
        refresh_maps_btn.clicked.connect(self._refresh_maps)
        maps_layout.addWidget(refresh_maps_btn)

        maps_group.setLayout(maps_layout)
        right_col.addWidget(maps_group)

        # Mod activity log
        log_group = QGroupBox("Mod Activity")
        log_layout = QVBoxLayout()
        self.mod_log = QListWidget()
        self.mod_log.setStyleSheet("""
            QListWidget {
                background-color: #0a0a00;
                color: #a89830;
                border: 1px solid #3a3a00;
                font-size: 10pt;
            }
        """)
        log_layout.addWidget(self.mod_log)

        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.mod_log.clear)
        log_layout.addWidget(clear_btn)

        log_group.setLayout(log_layout)
        right_col.addWidget(log_group)

        layout.addLayout(right_col, 1)

    def _add_mod(self):
        name = self.mod_input.text().strip()
        if name and name.lower() not in self.mods:
            self.mods[name.lower()] = name
            self.mod_list.addItem(name)
            self.mod_input.clear()
            self.mod_log.addItem(f"[{time.strftime('%H:%M:%S')}] Mod added: {name}")
            self._save_config()

    def _remove_mod(self):
        row = self.mod_list.currentRow()
        if row >= 0:
            item = self.mod_list.item(row)
            name = item.text()
            self.mods.pop(name.lower(), None)
            self.mod_list.takeItem(row)
            self.mod_log.addItem(f"[{time.strftime('%H:%M:%S')}] Mod removed: {name}")
            self._save_config()

    def _quick_add_mod(self):
        name = self.player_combo.currentText().strip()
        if name and name.lower() not in self.mods:
            self.mods[name.lower()] = name
            self.mod_list.addItem(name)
            self.mod_log.addItem(f"[{time.strftime('%H:%M:%S')}] Mod added: {name}")
            self._save_config()

    def _refresh_maps(self):
        """Re-fetch missions from server to update the store."""
        self.server.send('mission list')
        time.sleep(0.3)
        self.server.send('mission available')
        self.mod_log.addItem(f"[{time.strftime('%H:%M:%S')}] Refreshing maps from server...")
        # Update info label after a short delay (responses arrive async)
        QTimer.singleShot(2000, self._update_maps_info)

    def _update_maps_info(self):
        """Update the maps info label from store."""
        self.maps_info.setText(
            f"Rotation: {self.missions_store.rotation_count} maps | "
            f"Available: {self.missions_store.available_count} maps"
        )

    def update_players(self, players):
        """Update the quick-add player dropdown."""
        current = self.player_combo.currentText()
        self.player_combo.clear()
        for p in players:
            name = p.get('name', '').strip()
            if name:
                self.player_combo.addItem(name)
        # Restore selection if still available
        idx = self.player_combo.findText(current)
        if idx >= 0:
            self.player_combo.setCurrentIndex(idx)


    def update_chat(self, messages):
        """Monitor chat for mod commands. Skips first batch, then processes new messages.
        Permanent dedup — same message never fires twice (prevents re-firing on reconnect)."""
        if not self._chat_initialized:
            self._chat_initialized = True
            self._seen_chat_raw = set()
            wire_log(f"[MODS] Initialized: {len(messages)} messages in list (skipping first batch)")
            return

        processed = 0
        for msg in messages:
            raw = msg.get('raw', '')
            if raw in self._seen_chat_raw:
                continue
            self._seen_chat_raw.add(raw)
            text = msg.get('text', '')
            wire_log(f"[MODS] New chat: {text[:100]}")
            self._check_mod_command(text)
            processed += 1

        if processed:
            wire_log(f"[MODS] Processed {processed} messages")

        if len(self._seen_chat_raw) > 1000:
            self._seen_chat_raw = set(list(self._seen_chat_raw)[-500:])

    def _check_mod_command(self, text):
        """Check if a chat message is a mod command and execute it."""
        if '!' not in text:
            return

        # Extract sender name
        if ':' not in text:
            return
        sender = text.split(':')[0].strip().lower()
        message = text.split(':', 1)[1].strip()

        if not message.startswith('!'):
            return

        # Parse command
        parts = message.split(None, 2)
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        wire_log(f"[MODS] Command: cmd={cmd} sender={sender} args={args} mods={list(self.mods.keys())}")

        # !switch is handled by ChatBot for ALL players
        if cmd == '!switch':
            return

        # If it's a ! command but not recognized, tell them
        valid_commands = {'!warn', '!kick', '!ban', '!swap', '!kill', '!next', '!map', '!add', '!remove'}
        if cmd not in valid_commands:
            self.server.send_chat(f"Unknown command: {cmd}")
            return

        # All other commands require mod status
        if sender not in self.mods:
            wire_log(f"[MODS] sender '{sender}' not in mods {list(self.mods.keys())} — ignoring")
            return

        # Find player by name (for commands that target a player)
        def find_player(name):
            if not name:
                return None
            name_lower = name.lower()
            for p in self.server.players:
                if p.get('name', '').lower() == name_lower:
                    return p
            # Fuzzy: partial match
            for p in self.server.players:
                if name_lower in p.get('name', '').lower():
                    return p
            return None

        def find_map(name):
            """Find map by name. Uses missions store, falls back to live server data."""
            # Try the persistent store first
            result = self.missions_store.find(name)
            if result[0]:
                return result
            # Fallback: search live server missions data
            if not name:
                return None, None, None
            q = name.lower().strip()
            for ext in ('.bms', '.npaj', '.npj'):
                if q.endswith(ext):
                    q = q[:-len(ext)]
                    break
            for raw in self.server.missions:
                clean = MissionsStore._clean(raw)
                bare = MissionsStore._strip_ext(clean)
                if q == bare.lower() or q in bare.lower():
                    return bare, clean, 'rotation'
            # Try without prefix
            for raw in self.server.missions:
                clean = MissionsStore._clean(raw)
                bare = MissionsStore._strip_ext(clean)
                no_pfx = bare.lower()
                for pfx in ('as-', 'dm-', 'td-', 'ad-', 'tk-', 'ctf-'):
                    if no_pfx.startswith(pfx):
                        no_pfx = no_pfx[len(pfx):]
                if q == no_pfx or q in no_pfx:
                    return bare, clean, 'rotation'
            return None, None, None

        now = time.strftime('%H:%M:%S')

        if cmd == '!warn':
            target = find_player(args[0]) if args else None
            wire_log(f"[MODS] !warn: target={target.get('name') if target else None} args={args}")
            if target:
                reason = args[1] if len(args) > 1 else "You have been warned"
                self.server.warn_player(int(target['id']), reason)
                time.sleep(0.3)
                self.server.send_chat(f"{target['name']} was warned by a mod")
                self.mod_log.addItem(f"[{now}] {sender} warned {target['name']}: {reason}")
            else:
                self.mod_log.addItem(f"[{now}] {sender} tried to warn but player not found")

        elif cmd == '!kick':
            target = find_player(args[0]) if args else None
            if target:
                reason = args[1] if len(args) > 1 else "Kicked by mod"
                self.server.punt_player(int(target['id']), reason)
                time.sleep(0.3)
                self.server.send_chat(f"{target['name']} was kicked by a mod")
                self.mod_log.addItem(f"[{now}] {sender} kicked {target['name']}: {reason}")
            else:
                self.mod_log.addItem(f"[{now}] {sender} tried to kick but player not found")

        elif cmd == '!ban':
            target = find_player(args[0]) if args else None
            if target:
                self.server.ban_player(int(target['id']), "Banned by mod")
                time.sleep(0.3)
                self.server.send_chat(f"{target['name']} was banned by a mod")
                self.mod_log.addItem(f"[{now}] {sender} banned {target['name']}")
            else:
                self.mod_log.addItem(f"[{now}] {sender} tried to ban but player not found")

        elif cmd == '!swap':
            target = find_player(args[0]) if args else None
            if target:
                self.server.swap_player(int(target['id']))
                time.sleep(0.3)
                self.server.kill_player(int(target['id']))
                time.sleep(0.3)
                self.server.send_chat(f"{target['name']} was swapped by a mod")
                self.mod_log.addItem(f"[{now}] {sender} swapped {target['name']}")
            else:
                self.mod_log.addItem(f"[{now}] {sender} tried to swap but player not found")

        elif cmd == '!kill':
            target = find_player(args[0]) if args else None
            if target:
                self.server.kill_player(int(target['id']))
                time.sleep(0.3)
                self.server.send_chat(f"{target['name']} was killed by a mod")
                self.mod_log.addItem(f"[{now}] {sender} killed {target['name']}")
            else:
                self.mod_log.addItem(f"[{now}] {sender} tried to kill but player not found")

        elif cmd == '!next':
            self.server.send('mission cycle')
            self.server.send_chat("Skipping to next map...")
            self.mod_log.addItem(f"[{now}] {sender} skipped to next map")

        elif cmd == '!map':
            map_name = ' '.join(args) if args else ''
            wire_log(f"[MODS] !map: map_name='{map_name}'")
            name, filename, source = find_map(map_name)
            wire_log(f"[MODS] !map result: name={name} file={filename} source={source}")
            if name and filename:
                if source == 'available':
                    # Map is on server but not in rotation — add it first
                    self.server.send(f'mission add {filename} 0')
                    time.sleep(0.5)
                    self.server.send('mission list')  # refresh rotation
                    time.sleep(0.5)
                    self.mod_log.addItem(f"[{now}] {sender} added {name} to rotation")
                # Cycle to the next map
                self.server.send('mission cycle')
                self.server.send_chat(f"Switching to {name}...")
                self.mod_log.addItem(f"[{now}] {sender} switched to {name}")
            else:
                self.server.send_chat(f"Map not found: {map_name}")
                self.mod_log.addItem(f"[{now}] {sender} tried !map but '{map_name}' not found")

        elif cmd == '!add':
            map_name = ' '.join(args) if args else ''
            name, filename, source = find_map(map_name)
            if name and filename:
                self.server.send(f'mission add {filename} 0')
                time.sleep(0.3)
                self.server.send_chat(f"Added {name} to rotation")
                self.server.send('mission list')  # refresh
                self.mod_log.addItem(f"[{now}] {sender} added {name} to rotation")
            else:
                self.server.send_chat(f"Map not found: {map_name}")
                self.mod_log.addItem(f"[{now}] {sender} tried !add but '{map_name}' not found")

        elif cmd == '!remove':
            map_name = ' '.join(args) if args else ''
            name, filename, source = find_map(map_name)
            if name and filename and source == 'rotation':
                # Find the index in the rotation
                idx = None
                for i, m in enumerate(self.server.missions):
                    if name.lower() in m.lower():
                        idx = i
                        break
                if idx is not None:
                    self.server.send(f'mission remove {idx}')
                    time.sleep(0.3)
                    self.server.send_chat(f"Removed {name} from rotation")
                    self.server.send('mission list')  # refresh
                    self.mod_log.addItem(f"[{now}] {sender} removed {name} from rotation")
                else:
                    self.server.send_chat(f"Could not find {name} index")
                    self.mod_log.addItem(f"[{now}] {sender} tried !remove but index not found")
            elif name and filename and source == 'available':
                self.server.send_chat(f"{name} is not in the rotation")
                self.mod_log.addItem(f"[{now}] {sender} tried !remove but {name} not in rotation")
            else:
                self.server.send_chat(f"Map not found: {map_name}")
                self.mod_log.addItem(f"[{now}] {sender} tried !remove but '{map_name}' not found")


class MainWindow(QMainWindow):
    """WolfRAT 2.0 Main Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WolfRAT 2.0 — Joint Operations Server Admin")
        self.setMinimumSize(1100, 750)
        self.resize(1300, 850)

        # Server manager
        self.server = ServerManager()
        self.signals = LogSignals()
        self.missions_store = MissionsStore()

        # Web server for mobile access (v2.4)
        self.web_server = WolfWebServer(self.server)
        self.web_server.start()

        # Wire up callbacks
        self.server.set_callbacks(
            on_players=lambda p: self.signals.players_signal.emit(p),
            on_chat=lambda c: self.signals.chat_signal.emit(c),
            on_gamestate=lambda g: self.signals.gamestate_signal.emit(g),
            on_missions=lambda m: self.signals.missions_signal.emit(m),
            on_settings=lambda s: self.signals.settings_signal.emit(s),
            on_available_maps=lambda d: self.signals.available_maps_signal.emit(d),
            on_log=lambda msg: self.signals.log_signal.emit(msg),
            on_disconnect_ui=lambda: self.signals.disconnected_signal.emit(),
            on_connect_done=lambda: (self.chatbot_tab.reset_chat(), self.mods_tab.reset_chat()),
        )

        # Build UI
        self._build_ui()

        # Connect signals
        self.signals.log_signal.connect(self.server_tab.log)
        self.signals.log_signal.connect(self.console_tab.log)
        self.signals.players_signal.connect(self.players_tab.update_players)
        self.signals.players_signal.connect(lambda p: self.server_tab.update_player_count(p))
        self.signals.players_signal.connect(lambda p: self.status_players_label.setText(f"Players: {len(p)}"))
        self.signals.players_signal.connect(lambda p: self.web_server.broadcast_state())
        self.signals.players_signal.connect(self.mods_tab.update_players)
        self.signals.players_signal.connect(self.messages_tab.check_new_players)
        self.signals.chat_signal.connect(self.chatbot_tab.update_chat)
        self.signals.chat_signal.connect(lambda c: self.web_server.broadcast_state())
        self.signals.chat_signal.connect(self.mods_tab.update_chat)
        self.signals.gamestate_signal.connect(self.server_tab.update_gamestate)
        self.signals.gamestate_signal.connect(lambda g: self.web_server.broadcast_state())
        self.signals.missions_signal.connect(self.missions_tab.update_missions)
        self.signals.missions_signal.connect(self.missions_store.update_rotation)
        self.signals.missions_signal.connect(lambda m: self.mods_tab._update_maps_info())
        self.signals.available_maps_signal.connect(self.missions_tab.update_available_maps)
        self.signals.available_maps_signal.connect(self.missions_store.update_available)
        self.signals.gamestate_signal.connect(self.missions_tab.update_gamestate)
        self.signals.gamestate_signal.connect(self._update_status_bar)
        self.signals.settings_signal.connect(self.settings_tab.update_settings)
        self.signals.settings_signal.connect(self.server_tab.update_settings)
        self.signals.settings_signal.connect(lambda s: print(f"[WolfRAT] settings_signal received: {len(s)} keys"))
        self.signals.settings_signal.connect(lambda s: self.show_feedback("Settings updated from server"))
        self.signals.settings_signal.connect(lambda s: self.web_server.broadcast_state())
        self.signals.connected_signal.connect(lambda: self.set_connected(True))
        self.signals.connected_signal.connect(lambda: self.web_server.broadcast_state())
        self.signals.connected_signal.connect(lambda: self.missions_tab.update_connected(True))
        self.signals.connected_signal.connect(lambda: sounds.play("connect"))
        self.signals.disconnected_signal.connect(lambda: self.set_connected(False, 'Disconnected'))
        self.signals.disconnected_signal.connect(lambda: self.web_server.broadcast_state())
        self.signals.disconnected_signal.connect(lambda: self.missions_tab.update_connected(False))
        self.signals.disconnected_signal.connect(lambda: sounds.play("disconnect"))
        self.signals.players_signal.connect(lambda p: self.missions_tab.update_player_count(p))

        # Status bar

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Header
        header = QLabel("WolfRAT 2.0")
        header.setStyleSheet("font-size: 22pt; font-weight: bold; color: #e8c840; padding: 12px; letter-spacing: 4px;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        subheader = QLabel("Joint Operations Server Admin Tool")
        subheader.setStyleSheet("font-size: 10pt; color: #6a6a20; padding-bottom: 8px; letter-spacing: 2px;")
        subheader.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subheader)

        # Tabs
        self.tabs = QTabWidget()

        self.server_tab = ServerTab(self.server, self.signals)
        self.console_tab = ConsoleTab(self.server)
        self.players_tab = PlayersTab(self.server)
        self.missions_tab = MissionsTab(self.server)
        self.settings_tab = SettingsTab(self.server)
        self.chatbot_tab = ChatBotTab(self.server)
        self.messages_tab = MessagesTab(self.server)
        self.mods_tab = ModsTab(self.server, self.missions_store)

        # Wire up cross-tab references
        self.server_tab.missions_tab = self.missions_tab
        self.missions_tab._main_window = self

        self.tabs.addTab(self.server_tab, "🖥 Server")
        self.tabs.addTab(self.console_tab, "💻 Console")
        self.tabs.addTab(self.players_tab, "👥 Players")
        self.tabs.addTab(self.missions_tab, "🗺 Missions")
        self.tabs.addTab(self.settings_tab, "⚙ Settings")
        self.tabs.addTab(self.chatbot_tab, "💬 Chat Bot")
        self.tabs.addTab(self.messages_tab, "📢 Messages")
        self.tabs.addTab(self.mods_tab, "🛡 Mods")

        layout.addWidget(self.tabs)

        # --- Status bar ---
        status_bar = QHBoxLayout()

        # Connection status bar
        self.led_bar = QLabel("")
        self.led_bar.setFixedHeight(6)
        self.led_bar.setMinimumWidth(200)
        self.led_bar.setStyleSheet("background-color: #ff4040; border-radius: 3px;")
        status_bar.addWidget(self.led_bar)

        self.connection_label = QLabel("Disconnected")
        self.connection_label.setStyleSheet("font-size: 10pt; color: #ff6040; font-weight: bold;")
        status_bar.addWidget(self.connection_label)

        status_bar.addSpacing(20)

        # Map + Game mode
        self.status_map_label = QLabel("")
        self.status_map_label.setStyleSheet("font-size: 10pt; color: #a89830;")
        status_bar.addWidget(self.status_map_label)

        status_bar.addSpacing(20)

        # Player count
        self.status_players_label = QLabel("Players: 0")
        self.status_players_label.setStyleSheet("font-size: 10pt; color: #e8c840; font-weight: bold;")
        status_bar.addWidget(self.status_players_label)

        status_bar.addStretch()

        # Command feedback
        self.feedback_label = QLabel("")
        self.feedback_label.setStyleSheet("font-size: 9pt; color: #50ff50;")
        status_bar.addWidget(self.feedback_label)

        # Sound mute toggle
        self.mute_cb = QCheckBox("🔇 Mute")
        self.mute_cb.setStyleSheet("font-size: 9pt; color: #6a6a20;")
        self.mute_cb.stateChanged.connect(lambda s: sounds.set_muted(s == 2))
        status_bar.addWidget(self.mute_cb)

        status_bar.addSpacing(10)

        # Version
        ver_label = QLabel("v2.0")
        ver_label.setStyleSheet("font-size: 9pt; color: #444;")
        status_bar.addWidget(ver_label)

        status_widget = QWidget()
        status_widget.setLayout(status_bar)
        status_widget.setStyleSheet("background-color: #0a0a00; padding: 4px; border-top: 1px solid #333;")
        layout.addWidget(status_widget)

        # Pulsing LED animation
        self._led_timer = QTimer()
        self._led_timer.timeout.connect(self._pulse_led)
        self._led_phase = 0
        self._led_connected = False

    def _pulse_led(self):
        if not self._led_connected:
            return
        self._led_phase = (self._led_phase + 1) % 20
        alpha = int(155 + 100 * (0.5 + 0.5 * __import__('math').sin(self._led_phase * 0.314)))
        self.led_bar.setStyleSheet(f"background-color: rgba(80, 255, 80, {alpha}); border-radius: 3px;")

    def set_connected(self, connected, text="Connected"):
        self._led_connected = connected
        if connected:
            self.led_bar.setStyleSheet("background-color: #50ff50; border-radius: 3px;")
            self.connection_label.setText(text)
            self.connection_label.setStyleSheet("font-size: 10pt; color: #50ff50; font-weight: bold;")
            self._led_timer.start(50)
        else:
            self._led_timer.stop()
            self.led_bar.setStyleSheet("background-color: #ff4040; border-radius: 3px;")
            self.connection_label.setText(text)
            self.connection_label.setStyleSheet("font-size: 10pt; color: #ff6040; font-weight: bold;")

    def show_feedback(self, msg):
        """Show temporary feedback message in status bar."""
        self.feedback_label.setText(msg)
        QTimer.singleShot(3000, lambda: self.feedback_label.setText(""))

    def update_status_map(self, text):
        self.status_map_label.setText(text)

    def _update_status_bar(self, state):
        mode = state.get("mode", "")
        self.status_map_label.setText(mode if mode else "")

    def closeEvent(self, event):
        """Clean shutdown."""
        self.web_server.stop()
        self.server.stop_polling()
        self.server.disconnect()
        event.accept()


def _set_dark_title_bar(window):
    """Enable dark title bar on Windows 10/11."""
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(window.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass  # Not on Windows or API not available


def main():
    # Log startup
    try:
        from protocol import wire_log
        wire_log("=== WolfRAT 2.0 STARTED ===")
    except Exception:
        pass

    # B-Stats: anonymous usage analytics
    try:
        import bstats
        bstats.bstats_start("wolfrat", "2.2")
    except Exception:
        pass

    # Catch-all exception handler for debugging
    import traceback
    def excepthook(exc_type, exc_value, exc_tb):
        tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"\n=== CRASH ===\n{tb}\n============")
        try:
            with open('wolfrat_crash.log', 'w') as f:
                f.write(tb)
        except Exception:
            pass
        # Show error dialog if possible
        try:
            QMessageBox.critical(None, "WolfRAT Crash", f"An error occurred:\n\n{tb[-1000:]}")
        except Exception:
            pass
    sys.excepthook = excepthook

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    app.setApplicationName("WolfRAT 2.0")

    window = MainWindow()
    _set_dark_title_bar(window)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
