"""
WolfRAT 2.4.9 - Modern Joint Operations Server Admin Tool
Replaces the original WolfRAT v0.95 (2005, MFC70)
"""

import sys
import os
import json
import time
import random
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSpinBox, QCheckBox, QGroupBox, QRadioButton, QButtonGroup,
    QScrollArea, QSplitter, QMessageBox, QStatusBar, QFrame, QListWidget, QListWidgetItem,
    QAbstractItemView, QMenu, QSlider, QPlainTextEdit, QTableView, QProgressDialog
, QDialog)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QObject, QAbstractTableModel
from PyQt6.QtGui import QFont, QColor, QIcon, QTextCursor

import math
from wolfrat.protocol import ServerManager, wire_log
from wolfrat.sounds import generate_all_sounds
from wolfrat.web_server import WolfWebServer, generate_token
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
QTableCornerButton::section {
    background-color: #050500;
    border: 1px solid #1a1a00;
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
        # Disconnect the normal clicked signal - we'll fire it after the hold
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
            # Don't call super yet - keeps the button visually pressed
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
    reconnecting_signal = pyqtSignal(int)
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
        self._server_creds = {}  # {(host, port, user): password}
        self.reconnect_timer = QTimer()
        self.reconnect_timer.timeout.connect(self._auto_reconnect_tick)
        self._manual_disconnect = False
        self._reconnect_attempts = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Connection group
        conn_group = QGroupBox("Server Connection")
        conn_layout = QGridLayout()

        conn_layout.addWidget(QLabel("Server Address:"), 0, 0)
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("e.g. 192.168.1.100 - Port is set in your game.cfg")
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
        self.pass_input.setPlaceholderText("Password")
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

        self.auto_reconnect_cb = QCheckBox("Auto-Reconnect")
        self.auto_reconnect_cb.setChecked(True)
        btn_layout.addWidget(self.auto_reconnect_cb)

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
        self._manual_disconnect = False
        self.reconnect_timer.stop()

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
            self._handle_connect_failure()
            return
        self.log(msg)

        if success:
            self._reconnect_attempts = 0
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #e8c840;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            try:
                self.signals.connected_signal.emit()
            except Exception as e:
                self.log(f"Signal error: {e}")
            # Save last server for auto-reconnect on next launch
            self._save_last_server(host, port, username, password)
            # Polling starts automatically in ServerManager.connect()
            # Don't auto-apply rotation - it wipes the server's actual maps
            # Presets are loaded on demand via the Load button
        else:
            self.connect_btn.setEnabled(True)
            self._handle_connect_failure()

    def _handle_connect_failure(self):
        if not self._manual_disconnect and self.auto_reconnect_cb.isChecked():
            self._reconnect_attempts += 1
            self.log(f"Connection failed. Retrying in 30s... (Attempt {self._reconnect_attempts})")
            self.reconnect_timer.start(30000)
            self.signals.reconnecting_signal.emit(self._reconnect_attempts)

    def handle_disconnect_ui(self):
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ff6040;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)

        if not self._manual_disconnect and self.auto_reconnect_cb.isChecked():
            self._reconnect_attempts = 1
            self.log(f"Connection lost. Auto-reconnecting in 30s... (Attempt {self._reconnect_attempts})")
            self.reconnect_timer.start(30000)
            self.signals.reconnecting_signal.emit(self._reconnect_attempts)

    def _auto_reconnect_tick(self):
        self.reconnect_timer.stop()
        if not self.server.proto.connected:
            self.log(f"Auto-reconnecting... (Attempt {self._reconnect_attempts})")
            self._do_connect()

    def _do_disconnect(self):
        self._manual_disconnect = True
        self._reconnect_attempts = 0
        self.reconnect_timer.stop()
        self.server.disconnect()
        self.server.stop_polling()
        self.handle_disconnect_ui()
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
        password = self.pass_input.text().strip()
        if host:
            item = f"{host}:{port} ({user})"
            self.server_list.addItem(item)
            self._server_creds[(host, port, user)] = password
            self._persist_servers()

    def _delete_server(self):
        row = self.server_list.currentRow()
        if row >= 0:
            item = self.server_list.item(row)
            try:
                addr, user = item.text().split(' (')
                user = user.rstrip(')')
                host, port = addr.rsplit(':', 1)
                self._server_creds.pop((host, int(port), user), None)
            except Exception:
                pass
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
            password = self._server_creds.get((host, int(port), user), '')
            self.pass_input.setText(password)
        except Exception:
            pass

    def _get_servers_path(self):
        return os.path.join(os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False)
                            else os.path.dirname(sys.executable), 'wolfrat_servers.json')

    def _persist_servers(self):
        servers = []
        for i in range(self.server_list.count()):
            servers.append(self.server_list.item(i).text())
        creds = []
        for (h, p, u), pw in self._server_creds.items():
            creds.append({'host': h, 'port': p, 'user': u, 'password': pw})
        try:
            existing = {}
            cfg_path = self._get_servers_path()
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    existing = json.load(f) or {}
            existing['servers'] = servers
            existing['creds'] = creds
            with open(cfg_path, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    def _save_last_server(self, host, port, username, password):
        """Save the last successfully connected server for auto-reconnect on next launch."""
        try:
            cfg_path = self._get_servers_path()
            existing = {}
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    existing = json.load(f) or {}
            existing['last_server'] = {
                'host': host, 'port': port,
                'user': username, 'password': password
            }
            with open(cfg_path, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    def _load_last_server(self):
        """Load the last connected server details and populate the fields."""
        try:
            cfg_path = self._get_servers_path()
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    data = json.load(f)
                last = data.get('last_server')
                if last and last.get('host'):
                    self.host_input.setText(last['host'])
                    self.port_input.setValue(last.get('port', 10086))
                    self.user_input.setText(last.get('user', ''))
                    self.pass_input.setText(last.get('password', ''))
                    return True
        except Exception:
            pass
        return False

    def _load_saved_servers(self):
        try:
            cfg_path = self._get_servers_path()
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # Old format: just a list of server strings
                        for s in data:
                            self.server_list.addItem(s)
                    else:
                        # New format: {servers: [...], creds: [...]}
                        for s in data.get('servers', []):
                            self.server_list.addItem(s)
                        for c in data.get('creds', []):
                            key = (c['host'], c['port'], c['user'])
                            self._server_creds[key] = c.get('password', '')
        except Exception:
            pass



class ConsoleTab(QWidget):
    """Console tab - full server log with command input."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self._max_lines = 5000
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Better Console vs Raw Console toggle
        filter_layout = QHBoxLayout()
        self.better_console_cb = QCheckBox("Better Console (Hide repetitive polling)")
        self.better_console_cb.setChecked(True)  # Default to on
        self.better_console_cb.setStyleSheet("font-weight: bold; color: #e8c840;")
        filter_layout.addWidget(self.better_console_cb)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # Log output - QPlainTextEdit is much faster than QTextEdit for logs
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
        if self.better_console_cb.isChecked():
            # Filter out the noisy repetitive polling commands and responses
            if msg.startswith("__QUIET__"):
                return
            if "<< MISSION LIST =" in msg or "<< CURRENT STATE =" in msg or "<< GAME SETTINGS =" in msg or "<< CHAT =" in msg or "<< PLAYER LIST =" in msg:
                return
            # Don't filter out manual 'mission available' since that's rare, but filter the auto stuff.

        # Strip internal __QUIET__ tag if it made it here (i.e. if Better Console is unchecked)
        if msg.startswith("__QUIET__"):
            msg = msg.replace("__QUIET__", "")

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
    """Model for player list - drives QTableView with zero flicker."""
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
        """Replace player data efficiently - only emit dataChanged for actual changes."""
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

        # Same row count - check each cell for changes
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

        # Player table - model/view for zero-flicker updates
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
        """Mix teams - should only be used at round start."""
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

        # Update model - view repaints automatically, zero flicker
        self.model.update_players(players)


class MissionsTab(QWidget):
    """Mission/map management - matches original WolfRAT layout.

    Layout:
      LEFT:   All Available Missions (QTableWidget: Mission Name | Filename)
      RIGHT:  Mission Cycle          (QTableWidget: Mission Name | r | Filename)
      FAR RIGHT: Action buttons column (OK, Cancel, Review logs, Auto Refresh)
      BOTTOM: Status bar (Connected | Game Mode | command | status | players | time)
    """

    def __init__(self, server: ServerManager, missions_store=None):
        super().__init__()
        self.server = server
        self._missions_store = missions_store
        self._all_maps = []  # full available list [{file, display}]
        self._rotation_maps = []  # current rotation [filename, ...]
        self._main_window = None  # set by MainWindow after creation
        self._presets = {}  # saved rotation presets
        self._load_presets()
        self._build_ui()

    @staticmethod
    def _is_dm_map(filename):
        """Check if a map is a deathmatch map (DM prefix)."""
        return filename.upper().startswith('DM-') or filename.upper().startswith('DM_')

    @staticmethod
    def _build_mission_add_cmd(filename, count=1):
        """Build mission add command. DM maps don't support the '0' parameter."""
        if filename.upper().startswith('DM-') or filename.upper().startswith('DM_'):
            return f"mission add {filename}"
        elif count == 1:
            return f"mission add {filename} 0"
        else:
            return f"mission add {filename}"

    def _send_mission_add(self, filename, count=1):
        """Send mission add command. DM maps don't support the '0' parameter."""
        self.server.send(self._build_mission_add_cmd(filename, count))

    @staticmethod
    def _send_mission_add_to_server(server, filename, count=1):
        """Send mission add command via a specific server object. For use outside MissionsTab."""
        server.send(MissionsTab._build_mission_add_cmd(filename, count))

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
        self.rotation_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.rotation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.rotation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.rotation_table.horizontalHeader().resizeSection(1, 30)
        self.rotation_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.rotation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rotation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rotation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rotation_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rotation_table.customContextMenuRequested.connect(self._rotation_context_menu)
        right_layout.addWidget(self.rotation_table, 1)

        # Rotation buttons
        rot_btns = QHBoxLayout()
        refresh_rot_btn = SatisfyingButton("Refresh")
        refresh_rot_btn.clicked.connect(lambda: self.server.send("mission list"))
        rot_btns.addWidget(refresh_rot_btn)

        setnext_btn = SatisfyingButton("Queue Next")
        setnext_btn.setToolTip("Queue selected map as next (won't cycle immediately)")
        setnext_btn.clicked.connect(self._set_next_map)
        rot_btns.addWidget(setnext_btn)

        run_btn = SatisfyingButton("Run Now")
        run_btn.setToolTip("Switch server to the selected map now")
        run_btn.clicked.connect(self._run_selected_map)
        rot_btns.addWidget(run_btn)

        remove_btn = SatisfyingButton("Remove")
        remove_btn.clicked.connect(self._remove_from_rotation)
        rot_btns.addWidget(remove_btn)


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

        action_layout.addSpacing(15)

        self.tac_fuzzy_cb = QCheckBox("TAC Match priority")
        self.tac_fuzzy_cb.setChecked(True)
        self.tac_fuzzy_cb.setToolTip("Prioritise TAC maps before default")
        self.tac_fuzzy_cb.setStyleSheet("font-size: 8pt;")
        self.tac_fuzzy_cb.stateChanged.connect(lambda s: setattr(self._missions_store, 'prefer_tac', bool(s)))
        if self._missions_store:
            self._missions_store.prefer_tac = True
        action_layout.addWidget(self.tac_fuzzy_cb)

        action_layout.addStretch()

        main_h.addWidget(action_col)

        layout.addLayout(main_h, 1)  # stretch=1 for main area

        # Auto-refresh timer
        self._auto_refresh_timer = QTimer()
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tick)
        self._auto_refresh_timer.start(15000)  # every 15s

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

    def _find_display_name(self, filename):
        """Look up the display name for a filename from the available maps list.
        e.g. 'ASY_G1A.BMS' -> 'AS - Flooded Village'
        Falls back to _parse_mission_name if not found.
        """
        fl = filename.lower()
        for m in self._all_maps:
            if m.get('file', '').lower() == fl:
                return m.get('desc', '') or self._parse_mission_name(filename)
        return self._parse_mission_name(filename)

    def _add_rotation_row(self, filename, play_count=1, is_current=False, display_name=None):
        """Add a row to the Mission Cycle table."""
        row = self.rotation_table.rowCount()
        self.rotation_table.insertRow(row)
        name = display_name if display_name else self._parse_mission_name(filename)
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
            import re
            for m in missions:
                name_part = m.strip()
                if ':' in name_part[:5]:
                    name_part = name_part.split(':', 1)[1].strip()

                if not name_part:
                    continue

                # Extract filename cleanly using regex
                filename = name_part
                match = re.search(r'(\S+\.(?:bms|npj|npaj))', name_part, re.IGNORECASE)
                if match:
                    filename = match.group(1)
                else:
                    # Fallback if no extension is found (should be rare)
                    parts = name_part.split(' - ')
                    if len(parts) > 1:
                        filename = parts[1].split('<')[0].replace('(2x)', '').strip()

                # Extract display name cleanly
                display_name = name_part
                if match and filename in name_part:
                    idx = name_part.find(filename)
                    if idx > 0:
                        display_name = name_part[:idx].strip()
                        if display_name.endswith('-'):
                            display_name = display_name[:-1].strip()
                    else:
                        display_name = self._find_display_name(filename)
                else:
                    display_name = display_name.split('<')[0].replace('(2x)', '').strip()
                    if not display_name or display_name == filename:
                        display_name = self._find_display_name(filename)

                row_idx = len(self._rotation_maps)
                self._rotation_maps.append(filename)

                is_current = '<CURRENT MISSION>' in m or '<NEXT MISSION>' in m
                play_count = 2 if '(2x)' in m else 1
                self._add_rotation_row(filename, play_count, is_current, display_name=display_name)

                # Track which row to restore
                if prev_name and filename == prev_name:
                    restored = row_idx

                # Push current map name to main status bar
                if is_current:
                    if hasattr(self, '_main_window') and self._main_window:
                        self._main_window.status_map_label.setText(f" Map: {display_name} ")

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

    def load_available_from_store(self):
        """Load available maps table from MissionsStore (on startup)."""
        try:
            self.setUpdatesEnabled(False)
            self._all_maps = []
            for m in self._missions_store._data.get('available', []):
                self._all_maps.append({'file': m.get('file', ''), 'desc': m.get('name', '')})
            self._populate_available_table()
        except Exception as e:
            print(f"[WolfRAT] load_available_from_store error: {e}")
        finally:
            self.setUpdatesEnabled(True)

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
        add1 = menu.addAction(f"Add {name} - Play Once")
        add1.triggered.connect(lambda checked=False, f=filename: self._add_map_to_rotation(f, "1x"))
        add2 = menu.addAction(f"Add {name} - Play Twice")
        add2.triggered.connect(lambda checked=False, f=filename: self._add_map_to_rotation(f, "2x"))
        menu.exec(self.available_table.mapToGlobal(pos))

    def _add_map_to_rotation(self, filename, count="1x"):
        """Add a map to rotation and send to server."""
        if filename in self._rotation_maps:
            return
        play_count = 1 if count == "1x" else 2
        self._send_mission_add(filename, play_count)
        time.sleep(0.2)
        self._rotation_maps.append(filename)
        display_name = self._find_display_name(filename)
        self._add_rotation_row(filename, play_count, False, display_name=display_name)
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

        # Capture the filename - not the row index - so it survives table rebuilds
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

    def _get_server_index(self, filename):
        """Safely lookup the true server index for a filename, ignoring UI row drifts."""
        if not getattr(self, 'server', None) or not hasattr(self.server, 'missions'):
            return -1

        import re
        for raw in self.server.missions:
            # Extract server index (e.g., "1: " or "1 - ")
            idx_str = ""
            name_part = raw.strip()
            if ':' in name_part[:5]:
                parts = name_part.split(':', 1)
                idx_str = parts[0].strip()
                name_part = parts[1].strip()

            # Extract filename using the same regex as update_missions
            server_filename = name_part
            match = re.search(r'(\S+\.(?:bms|npj|npaj))', name_part, re.IGNORECASE)
            if match:
                server_filename = match.group(1)
            else:
                # Fallback if no extension is found
                parts = name_part.split(' - ')
                if len(parts) > 1:
                    server_filename = parts[1].split('<')[0].replace('(2x)', '').strip()

            if server_filename.lower() == filename.lower() and idx_str.isdigit():
                return int(idx_str)
        return -1

    def _switch_to_map(self, row):
        """Run this map now - queue it and trigger cycle."""
        if 0 <= row < len(self._rotation_maps):
            name = self._rotation_maps[row]
            idx = self._get_server_index(name)
            idx = idx if idx >= 0 else row
            self.server._log(f"Running map: {name} (server index {idx})")
            self.server.send(f"MISSION SETNEXT {idx}")
            time.sleep(0.3)
            self.server.send("GOTO GAMESTATE")

    def _run_selected_map(self):
        """Run This Map button - switch to the selected map now."""
        row = self.rotation_table.currentRow()
        if row >= 0:
            self._switch_to_map(row)

    def _set_next_mission(self, row):
        """Set Next Mission - queue map without cycling."""
        if 0 <= row < len(self._rotation_maps):
            name = self._rotation_maps[row]
            idx = self._get_server_index(name)
            idx = idx if idx >= 0 else row
            self.server.send(f"MISSION SETNEXT {idx}")
            self.server._log(f"Next mission set to: {name} (server index {idx})")

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
        name = self._rotation_maps[row]
        idx = self._get_server_index(name)
        idx = idx if idx >= 0 else row
        self.server.send(f"mission remove {idx}")
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
        """Run a map by filename - looks up current row at execution time."""
        row = self._find_rotation_row(filename)
        if row >= 0:
            self._switch_to_map(row)

    def _set_next_mission_by_name(self, filename):
        """Set next mission by filename."""
        row = self._find_rotation_row(filename)
        if row >= 0:
            self._set_next_mission(row)

    def _remove_from_rotation_by_name(self, filename):
        """Remove a map by filename - looks up current row at execution time."""
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
        """Drag-drop reorder - rebuild _rotation_maps from table."""
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
            play_count = entry.get('count', 1) if isinstance(entry, dict) else 1
            self._send_mission_add(filename, play_count)
            time.sleep(0.2)
            self._rotation_maps.append(filename)
            display_name = self._find_display_name(filename)
            self._add_rotation_row(filename, count, False, display_name=display_name)

        self.server._log(f"Rotation applied: {len(saved)} maps")

    def _set_next_map(self):
        """Set Next Mission button - queue selected map without cycling."""
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
            f"Add '{name}' maps to current rotation?\n\nOld preset will stay unless you click Cancel.\nClick Clear at the bottom right to remove it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        preset = self._presets[name]

        # Run in background thread to avoid freezing the GUI
        import threading
        def _do_load():
            self.server._log(f"Loading preset '{name}' - adding {len(preset)} maps...")
            for i, entry in enumerate(preset):
                if isinstance(entry, dict):
                    mapname = entry['file']
                    count = entry.get('count', 1)
                else:
                    mapname = entry
                    count = 1
                play_count = entry.get('count', 1) if isinstance(entry, dict) else 1
                self._send_mission_add(mapname, play_count)
                self.server._log(f"  [{i+1}/{len(preset)}] Added {mapname}")
                if i < len(preset) - 1:
                    time.sleep(2)
            # Refresh the rotation from the server
            time.sleep(1)
            self.server.send("mission list")
            self.server._log(f"Preset '{name}' loaded. Remove unwanted maps manually.")

        threading.Thread(target=_do_load, daemon=True).start()

    def _ok_clicked(self):
        """OK button - apply rotation and show feedback."""
        self._presets['_auto'] = self._build_auto_preset()
        self._save_presets()
        self.server._log("Rotation saved and applied.")

    def _cancel_clicked(self):
        """Cancel button - discard unsaved changes."""
        self.server._log("Changes discarded.")

    def _review_event_log(self):
        """Review Event Log button."""
        self.server.send("eventlog")
        self.server._log("Requested event log.")

    def _review_chat_log(self):
        """Review Chat Log button."""
        self.server.send("chat get")
        self.server._log("Requested chat log.")



class SettingsTab(QWidget):
    """Server settings - matches original WolfRAT 0.95 layout.
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
        "gameTime": ("Game Time", 1, 240, 1, "m"),
    }

    # Standard JO weapons (editable list)
    def __init__(self, server):
        super().__init__()
        self.server = server
        self.mods_tab = None  # set by MainWindow after creation
        self._checkboxes = {}
        self._sliders = {}

        self._loading = False
        self._auto_refresh = False
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._build_ui()

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(6)

        # ============================================================
        # LEFT COLUMN - Toggles + Sliders + Action Buttons
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
        self._slider_dragging = {}  # track which sliders are being dragged
        self._game_time_remaining = 0  # seconds remaining from server
        self._map_voting_tab = None  # set by MainWindow after creation
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

            # Sync slider <-> spinbox (visual only, no server spam)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)

            # Debounce: only send to server 800ms after last change
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._send_debounced(k))
            self._debounce_timers[key] = timer

            # Track slider dragging state
            self._slider_dragging[key] = False
            slider.sliderPressed.connect(lambda k=key: self._on_slider_pressed(k))
            slider.sliderReleased.connect(lambda k=key: self._on_slider_released(k))

            # Spinbox: only debounce if slider is NOT being dragged
            spin.valueChanged.connect(lambda val, k=key: self._on_spinbox_changed(k, val))

            slider_layout.addWidget(slider, row, 1)
            slider_layout.addWidget(spin, row, 2)
            self._sliders[key] = (slider, spin)
            row += 1
        # Remaining time display for GameTime
        self.game_time_remaining_lbl = QLabel("Remaining: --:--")
        self.game_time_remaining_lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #40e040;")
        slider_layout.addWidget(self.game_time_remaining_lbl, row, 0, 1, 3)
        slider_group.setLayout(slider_layout)
        left_col.addWidget(slider_group)
        left_col.addStretch()

        # ============================================================
        # CENTER COLUMN - Rules, Voting, Ping, Time, Passwords
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

        # --- Voting ---
        vote_group = QGroupBox("🌐 Map Voting")
        vote_layout = QGridLayout()

        self.vote_enabled_cb = QCheckBox("Enable Map Voting (!vote / !yes)")
        self.vote_enabled_cb.setChecked(True)
        self.vote_enabled_cb.setStyleSheet("font-size: 10pt; color: #e8c840;")
        self.vote_enabled_cb.stateChanged.connect(self._on_vote_enabled_changed)
        vote_layout.addWidget(self.vote_enabled_cb, 0, 0, 1, 2)

        vote_layout.addWidget(QLabel("  Vote threshold [%]:"), 1, 0)
        self.vote_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.vote_threshold_slider.setRange(51, 90)
        self.vote_threshold_slider.setSingleStep(1)
        self.vote_threshold_slider.setValue(51)
        self.vote_threshold_slider.setStyleSheet("""
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
        self.vote_threshold_spin = QSpinBox()
        self.vote_threshold_spin.setRange(51, 90)
        self.vote_threshold_spin.setValue(51)
        self.vote_threshold_spin.setSuffix("%")
        self.vote_threshold_spin.setFixedWidth(70)
        self.vote_threshold_spin.setStyleSheet("""
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
        self.vote_threshold_slider.valueChanged.connect(self.vote_threshold_spin.setValue)
        self.vote_threshold_spin.valueChanged.connect(self.vote_threshold_slider.setValue)
        self.vote_threshold_spin.valueChanged.connect(self._on_vote_threshold_changed)
        vote_layout.addWidget(self.vote_threshold_slider, 1, 1)
        vote_layout.addWidget(self.vote_threshold_spin, 1, 2)

        vote_group.setLayout(vote_layout)
        center_col.addWidget(vote_group)

        # --- Skip Vote ---
        skip_group = QGroupBox("Skip Vote")
        skip_layout = QGridLayout()

        self.skip_enabled_cb = QCheckBox("Enable Skip Voting (!skip / !yes)")
        self.skip_enabled_cb.setChecked(True)
        self.skip_enabled_cb.setStyleSheet("font-size: 10pt; color: #e8c840;")
        self.skip_enabled_cb.stateChanged.connect(self._on_skip_enabled_changed)
        skip_layout.addWidget(self.skip_enabled_cb, 0, 0, 1, 2)

        skip_layout.addWidget(QLabel("  Skip threshold [%]:"), 1, 0)
        self.skip_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.skip_threshold_slider.setRange(51, 90)
        self.skip_threshold_slider.setSingleStep(1)
        self.skip_threshold_slider.setValue(51)
        self.skip_threshold_slider.setStyleSheet("""
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
        self.skip_threshold_spin = QSpinBox()
        self.skip_threshold_spin.setRange(51, 90)
        self.skip_threshold_spin.setValue(51)
        self.skip_threshold_spin.setSuffix("%")
        self.skip_threshold_spin.setFixedWidth(70)
        self.skip_threshold_spin.setStyleSheet("""
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
        self.skip_threshold_slider.valueChanged.connect(self.skip_threshold_spin.setValue)
        self.skip_threshold_spin.valueChanged.connect(self.skip_threshold_slider.setValue)
        self.skip_threshold_spin.valueChanged.connect(self._on_skip_threshold_changed)
        skip_layout.addWidget(self.skip_threshold_slider, 1, 1)
        skip_layout.addWidget(self.skip_threshold_spin, 1, 2)

        skip_group.setLayout(skip_layout)
        center_col.addWidget(skip_group)

        # --- Time Configurations ---
        time_group = QGroupBox("Time Configuration")
        time_layout = QGridLayout()

        time_layout.addWidget(QLabel("Set Time of Day:"), 0, 0)
        self.tod_combo = QComboBox()
        self.tod_combo.addItems(["Def", "0000", "0100", "0200", "0300", "0400", "0500",
                                  "0600", "0700", "0800", "0900", "1000", "1100",
                                  "1200", "1300", "1400", "1500", "1600", "1700",
                                  "1800", "1900", "2000", "2100", "2200", "2300"])
        self.tod_combo.currentTextChanged.connect(lambda v: self.server.send(f"CMD TOD {v}") if v != "Def" and hasattr(self, 'server') and self.server else None)
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
        # ============================================================
        # ASSEMBLE MAIN LAYOUT
        # ============================================================
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QHBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addLayout(left_col, 2)
        scroll_layout.addLayout(center_col, 3)

        scroll_content.setMinimumWidth(800)
        scroll_content.setMinimumHeight(650)
        scroll_area.setWidget(scroll_content)

        main_layout.addWidget(scroll_area, 1)

    # ---- Weapon table helpers ----

    # ---- Settings action helpers ----

    def _on_toggle(self, key, state):
        if self._loading:
            return
        val = "1" if state == 2 else "0"
        self.server.set_setting(key, val)
        label = self.CHECKBOX_SETTINGS.get(key, key)
        self._show_feedback(f"{label} = {'ON' if val == '1' else 'OFF'}")

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
            self._show_feedback("Settings LOCKED - controls disabled")
        else:
            self._show_feedback("Settings UNLOCKED - controls enabled")

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

    def _on_slider_pressed(self, key):
        """Slider drag started — block all sends until release."""
        self._slider_dragging[key] = True
        # Cancel any pending debounce from spinbox
        if key in self._debounce_timers:
            self._debounce_timers[key].stop()

    def _on_slider_released(self, key):
        """Slider drag ended — send the final value immediately."""
        self._slider_dragging[key] = False
        if key in self._sliders:
            slider, spin = self._sliders[key]
            val = slider.value()
            self._pending_values[key] = val
            self._send_debounced(key)

    def _on_spinbox_changed(self, key, value):
        """Spinbox value changed — only debounce if slider is NOT being dragged."""
        if self._loading: return
        if self._slider_dragging.get(key, False): return
        self._pending_values[key] = value
        self._debounce_timers[key].start()

    def _send_debounced(self, key):
        if key not in self._pending_values: return
        val = self._pending_values[key]
        # GameTime needs CamelCase to match server key
        server_key = "GameTime" if key == "gameTime" else key
        self.server.send(f"set {server_key} {val}")
        self._show_feedback(f"Set {server_key} to {val}")
        del self._pending_values[key]
        self._debounce_timers[key].stop()

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
                        raw_val = settings_lower[key.lower()]
                        # GameTime comes as "remaining/total" (e.g. "21/25")
                        if key == 'gameTime' and '/' in str(raw_val):
                            parts = str(raw_val).split('/')
                            total = int(float(parts[1]))
                            remaining = int(float(parts[0]))
                            self._game_time_remaining = remaining * 60  # store as seconds
                            # Update remaining time label
                            self.game_time_remaining_lbl.setText(f"Remaining: {remaining}m / Total: {total}m")
                            # Update slider to total value
                            val = total
                            # Push to map voting tab if connected
                            if self._map_voting_tab:
                                self._map_voting_tab.update_game_time(total, remaining)
                        else:
                            val = int(float(raw_val))
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


        finally:
            self._loading = False

    # ---- Vote settings ----

    def load_vote_settings(self):
        """Load vote settings from mods config. Called on init."""
        try:
            import os, sys, json
            base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
            path = os.path.join(base, 'wolfrat_mods.json')
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
                    self._loading = True
                    self.vote_enabled_cb.setChecked(cfg.get('vote_enabled', True))
                    self.vote_threshold_spin.setValue(cfg.get('vote_threshold', 51))
                    self._loading = False
        except Exception:
            pass

    def _save_vote_settings(self):
        """Save vote settings to mods config."""
        try:
            import os, sys, json
            base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
            path = os.path.join(base, 'wolfrat_mods.json')
            cfg = {}
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
            cfg['vote_enabled'] = self.vote_enabled_cb.isChecked()
            cfg['vote_threshold'] = self.vote_threshold_spin.value()
            with open(path, 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_vote_enabled_changed(self, state):
        if self._loading:
            return
        enabled = bool(state)
        if self.mods_tab:
            self.mods_tab._vote_enabled = enabled
            if not enabled and self.mods_tab._vote_active:
                self.mods_tab._vote_timer.stop()
                self.mods_tab.server.send_chat("Vote cancelled: voting disabled")
                self.mods_tab._vote_active = False
        self._save_vote_settings()

    def _on_vote_threshold_changed(self, val):
        if self._loading:
            return
        if self.mods_tab:
            self.mods_tab._vote_threshold = val
        self._save_vote_settings()

    def load_skip_settings(self):
        """Load skip vote settings from mods config. Called on init."""
        try:
            import os, sys, json
            base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
            path = os.path.join(base, 'wolfrat_mods.json')
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
                    self._loading = True
                    self.skip_enabled_cb.setChecked(cfg.get('skip_enabled', True))
                    self.skip_threshold_spin.setValue(cfg.get('skip_threshold', 51))
                    self._loading = False
        except Exception:
            pass

    def _save_skip_settings(self):
        """Save skip vote settings to mods config."""
        try:
            import os, sys, json
            base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
            path = os.path.join(base, 'wolfrat_mods.json')
            cfg = {}
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
            cfg['skip_enabled'] = self.skip_enabled_cb.isChecked()
            cfg['skip_threshold'] = self.skip_threshold_spin.value()
            with open(path, 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_skip_enabled_changed(self, state):
        if self._loading:
            return
        enabled = bool(state)
        if self.mods_tab:
            self.mods_tab._skip_enabled = enabled
            if not enabled and self.mods_tab._skip_active:
                self.mods_tab._skip_timer.stop()
                self.mods_tab.server.send_chat("Skip vote cancelled: voting disabled")
                self.mods_tab._skip_active = False
        self._save_skip_settings()

    def _on_skip_threshold_changed(self, val):
        if self._loading:
            return
        if self.mods_tab:
            self.mods_tab._skip_threshold = val
        self._save_skip_settings()

class ChatBotTab(QWidget):
    """Chat monitor and auto-moderation tab."""

    def __init__(self, server: ServerManager):
        super().__init__()
        self.server = server
        self.bad_words = {}  # {word: action} e.g. {"nigger": "Kick", "cunt": "Warn"}
        self.auto_swap_enabled = True
        self.swap_trigger = "!switch"
        self._seen_chat_ids = set()  # msg ids we've already processed
        self._swap_cooldowns = {}  # player_name -> timestamp of last swap
        self._chat_initialized = False  # skip first chat batch (old messages from before we connected)
        self._player_chat_times = {} # player_name -> list of timestamps
        self._chat_config = self._load_chat_config()
        self._build_ui()

    def reset_chat(self):
        """Reset chat dedup state on reconnect."""
        self._chat_initialized = False
        self._seen_chat_raw = set()
        self._player_chat_times = {}

    def _chat_config_path(self):
        import os, sys
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_chat.json')

    def _load_chat_config(self):
        import json, os
        try:
            path = self._chat_config_path()
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_chat_config(self):
        import json
        try:
            cfg = self._chat_config.copy() if hasattr(self, '_chat_config') else {}
            if hasattr(self, 'auto_swap_cb'):
                cfg['auto_swap_enabled'] = self.auto_swap_cb.isChecked()
            if hasattr(self, 'trigger_input'):
                cfg['swap_trigger'] = self.trigger_input.text()
            if hasattr(self, 'spam_cb'):
                cfg['spam_enabled'] = self.spam_cb.isChecked()
            if hasattr(self, 'spam_msg_spin'):
                cfg['spam_msg_count'] = self.spam_msg_spin.value()
            self._chat_config = cfg
            with open(self._chat_config_path(), 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

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
        self.auto_swap_cb.setChecked(self._chat_config.get('auto_swap_enabled', True))
        self.auto_swap_cb.stateChanged.connect(self._save_chat_config)
        swap_layout.addWidget(self.auto_swap_cb, 0, 0, 1, 2)

        swap_layout.addWidget(QLabel("Trigger word:"), 1, 0)
        self.trigger_input = QLineEdit(self._chat_config.get('swap_trigger', '!switch'))
        self.trigger_input.setPlaceholderText("e.g. !switch, !swap, !team")
        self.trigger_input.textChanged.connect(self._save_chat_config)
        swap_layout.addWidget(self.trigger_input, 1, 1)

        swap_group.setLayout(swap_layout)
        layout.addWidget(swap_group)

        # Bad words filter - per-word actions
        filter_group = QGroupBox("Bad Words Filter")
        filter_layout = QVBoxLayout()

        # Anti-Spam row
        spam_layout = QHBoxLayout()
        self.spam_cb = QCheckBox("Enable Anti-Spam (Kick players who send")
        self.spam_cb.setChecked(self._chat_config.get('spam_enabled', True))
        self.spam_cb.stateChanged.connect(self._save_chat_config)
        spam_layout.addWidget(self.spam_cb)

        self.spam_msg_spin = QSpinBox()
        self.spam_msg_spin.setRange(3, 20)
        self.spam_msg_spin.setValue(self._chat_config.get('spam_msg_count', 6))
        self.spam_msg_spin.valueChanged.connect(self._save_chat_config)
        spam_layout.addWidget(self.spam_msg_spin)

        spam_layout.addWidget(QLabel("msgs in"))

        self.spam_time_spin = QSpinBox()
        self.spam_time_spin.setRange(2, 20)
        self.spam_time_spin.setValue(5)
        spam_layout.addWidget(self.spam_time_spin)

        spam_layout.addWidget(QLabel("secs)"))
        spam_layout.addStretch()

        filter_layout.addLayout(spam_layout)

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
            self._save_config()

    def _remove_bad_word(self):
        row = self.bad_words_list.currentRow()
        if row >= 0:
            item = self.bad_words_list.item(row)
            word = item.text().split(' → ')[0].strip()
            self.bad_words.pop(word, None)
            self.bad_words_list.takeItem(row)
            self._save_config()

    def update_chat(self, messages: list):
        try:
            from wolfrat.protocol import wire_log
        except ImportError:
            wire_log = lambda m: None

        # Skip the first batch - those are old messages from before we connected
        if not self._chat_initialized:
            self._chat_initialized = True
            self._seen_chat_ids = {m.get('id', m.get('raw', '')) for m in messages}
            wire_log(f'CHAT: initialized with {len(self._seen_chat_ids)} existing messages')
            return

        # Only process messages we haven't seen before (by unique id)
        new_messages = []
        for msg in messages:
            msg_id = msg.get('id', msg.get('raw', ''))
            if msg_id and msg_id not in self._seen_chat_ids:
                self._seen_chat_ids.add(msg_id)
                new_messages.append(msg)

        wire_log(f'CHAT: {len(messages)} total, {len(new_messages)} new, {len(self.bad_words)} bad words registered')

        # Trim seen set to prevent memory growth
        if len(self._seen_chat_ids) > 1000:
            self._seen_chat_ids = set(list(self._seen_chat_ids)[-500:])

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
                                    f"<span style='color: #e8c840'>[SWAP] {name} requested team switch - swapping and respawning</span>")
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
                        f"<span style='color: #ff6040'>[FILTER] Bad word '{word}' from {display_name} - Action: {action}</span>")

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

            # Anti-Spam Check
            if self.spam_cb.isChecked():
                player_name = text.split(':')[0].strip() if ':' in text else text.split()[0].strip()
                if player_name and player_name != 'Server':
                    now = time.time()
                    history = self._player_chat_times.get(player_name.lower(), [])
                    window = self.spam_time_spin.value()
                    # keep only messages within the time window
                    history = [t for t in history if now - t <= window]
                    history.append(now)
                    self._player_chat_times[player_name.lower()] = history

                    if len(history) >= self.spam_msg_spin.value():
                        pid = None
                        display_name = player_name
                        for p in self.server.players:
                            if p.get('name', '').lower() == player_name.lower():
                                pid = p.get('id', '')
                                display_name = p.get('name', player_name)
                                break

                        if pid:
                            self.server.punt_player(int(pid), "Chat spam")
                            time.sleep(0.3)
                            self.server.send_chat(f"{display_name} was kicked for spamming the chat")
                            self.chat_display.append(f"<span style='color: #ff6040'>[ANTI-SPAM] {display_name} kicked for spam</span>")
                            self._player_chat_times[player_name.lower()] = [] # Reset after kick

        # Scroll to bottom
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)


class StatsStore:
    """SQLite-backed persistent player stats (kills, deaths, KD, streaks)."""

    def __init__(self):
        self._db = None
        self._init_db()

    def _db_path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_stats.db')

    def _init_db(self):
        try:
            import sqlite3
            self._db = sqlite3.connect(self._db_path())
            self._db.execute('''
                CREATE TABLE IF NOT EXISTS players (
                    name TEXT NOT NULL,
                    name_lower TEXT PRIMARY KEY,
                    kills INTEGER DEFAULT 0,
                    deaths INTEGER DEFAULT 0,
                    kd REAL DEFAULT 0.0,
                    best_streak INTEGER DEFAULT 0,
                    last_seen TEXT,
                    baseline_k INTEGER DEFAULT 0,
                    baseline_d INTEGER DEFAULT 0
                )
            ''')
            self._db.commit()
            # Add baseline columns to existing databases
            try:
                self._db.execute('ALTER TABLE players ADD COLUMN baseline_k INTEGER DEFAULT 0')
                self._db.commit()
            except Exception:
                pass  # column already exists
            try:
                self._db.execute('ALTER TABLE players ADD COLUMN baseline_d INTEGER DEFAULT 0')
                self._db.commit()
            except Exception:
                pass  # column already exists
            # Migrate: if old table exists without name_lower, rebuild
            cols = [r[1] for r in self._db.execute('PRAGMA table_info(players)').fetchall()]
            if 'name_lower' not in cols:
                self._db.execute('DROP TABLE IF EXISTS players')
                self._db.commit()
                self._init_db()  # recreate with new schema
        except Exception as e:
            print(f"[StatsStore] DB init error: {e}")

    def update_player(self, name, kills, deaths, streak=0):
        """Upsert a player's stats. Accumulates across map changes."""
        if not self._db or not name:
            return
        try:
            name_key = name.strip().lower()
            now = time.strftime('%Y-%m-%dT%H:%M:%S')

            # Check existing record
            existing = self._db.execute(
                'SELECT kills, deaths, baseline_k, baseline_d FROM players WHERE name_lower = ?',
                (name_key,)
            ).fetchone()

            if existing:
                old_k, old_d, old_bk, old_bd = existing
                if kills < old_k - old_bk:
                    # Map reset detected - old total becomes new baseline
                    wire_log(f"[KD] Map reset detected for {name}: server K={kills} < stored K={old_k}, setting baseline to {old_k}")
                    old_bk = old_k
                    old_bd = old_d
                # Accumulated = baseline + current server stats
                total_k = old_bk + kills
                total_d = old_bd + deaths
            else:
                total_k = kills
                total_d = deaths
                old_bk = 0
                old_bd = 0

            kd = round(total_k / total_d, 2) if total_d > 0 else float(total_k)
            self._db.execute('''
                INSERT INTO players (name, name_lower, kills, deaths, kd, best_streak, last_seen, baseline_k, baseline_d)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name_lower) DO UPDATE SET
                    name = ?,
                    kills = ?,
                    deaths = ?,
                    kd = ?,
                    best_streak = MAX(players.best_streak, ?),
                    last_seen = ?,
                    baseline_k = ?,
                    baseline_d = ?
            ''', (name, name_key, total_k, total_d, kd, streak, now, old_bk, old_bd,
                  name, total_k, total_d, kd, streak, now, old_bk, old_bd))
            self._db.commit()
        except Exception as e:
            print(f"[StatsStore] Update error: {e}")

    def get_player(self, name):
        """Look up a player by name (case-insensitive). Returns dict or None."""
        if not self._db or not name:
            return None
        try:
            name_key = name.strip().lower()
            row = self._db.execute(
                'SELECT name, kills, deaths, kd, best_streak, last_seen FROM players WHERE name_lower = ?',
                (name_key,)
            ).fetchone()
            if row:
                return {
                    'name': row[0], 'kills': row[1], 'deaths': row[2],
                    'kd': row[3], 'best_streak': row[4], 'last_seen': row[5]
                }
        except Exception as e:
            print(f"[StatsStore] Lookup error: {e}")
        return None

    def get_top_players(self, limit=10):
        """Return top players by kills."""
        if not self._db:
            return []
        try:
            rows = self._db.execute(
                'SELECT name, kills, deaths, kd FROM players ORDER BY kills DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [{'name': r[0], 'kills': r[1], 'deaths': r[2], 'kd': r[3]} for r in rows]
        except Exception:
            return []

    def get_player_count(self):
        """Total tracked players."""
        if not self._db:
            return 0
        try:
            return self._db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
        except Exception:
            return 0


class MessagesTab(QWidget):
    """Server messaging - direct, recurring, and welcome messages."""

    def __init__(self, server: ServerManager, stats_store: StatsStore = None):
        super().__init__()
        self.server = server
        self.stats_store = stats_store
        self._recurring_messages = []
        self._recurring_index = 0
        self._recurring_timer = QTimer()
        self._recurring_timer.timeout.connect(self._send_next_recurring)
        self._seen_players = set()
        self._welcome_enabled = True
        self._welcome_message = "Welcome to the server, {player}! Enjoy your stay."
        self._spree_enabled = True
        self._spree_thresholds = {
            3: ">>> {player} is on a KILLING SPREE! (3 Kills) <<<",
            5: ">>> {player} is on a RAMPAGE! (5 Kills) <<<",
            7: ">>> {player} is UNSTOPPABLE! (7 Kills) <<<",
            10: ">>> {player} is GODLIKE! (10 Kills) <<<"
        }
        self._spree_table_loading = False
        self._kd_enabled = True
        self._player_stats = {}
        self._recurring_interval_idx = 2
        self._recurring_running = False
        self._load_config()
        self._build_ui()
        # Auto-start recurring messages if they were running
        if self._recurring_running and self._recurring_messages:
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
                    self._spree_enabled = cfg.get('spree_enabled', True)
                    # Spree thresholds: {"3": "message", "5": "message", ...}
                    raw_thresholds = cfg.get('spree_thresholds', None)
                    if raw_thresholds is not None:
                        self._spree_thresholds = {int(k): v for k, v in raw_thresholds.items()}
                    # else keep the defaults from __init__
                    self._kd_enabled = cfg.get('kd_enabled', True)
                    self._recurring_interval_idx = cfg.get('recurring_interval_idx', 2)
                    self._recurring_running = cfg.get('recurring_running', False)
        except Exception:
            pass

    def _save_config(self):
        try:
            # Safely grab UI values if they exist yet
            interval_idx = self.interval_combo.currentIndex() if hasattr(self, 'interval_combo') else getattr(self, '_recurring_interval_idx', 2)
            is_running = self._recurring_timer.isActive() if hasattr(self, '_recurring_timer') else getattr(self, '_recurring_running', False)

            cfg = {
                'recurring': self._recurring_messages,
                'welcome_enabled': self._welcome_enabled,
                'welcome_msg': self._welcome_message,
                'seen_players': list(self._seen_players),
                'spree_enabled': self._spree_enabled,
                'spree_thresholds': {str(k): v for k, v in sorted(self._spree_thresholds.items())},
                'kd_enabled': self._kd_enabled,
                'recurring_interval_idx': interval_idx,
                'recurring_running': is_running,
            }
            with open(self._config_path(), 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(0, 0, 0, 0)

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
        self.interval_combo.addItems(["5 minutes", "10 minutes", "15 minutes", "30 minutes", "45 minutes", "60 minutes"])
        self.interval_combo.setCurrentIndex(getattr(self, '_recurring_interval_idx', 2))
        self.interval_combo.currentIndexChanged.connect(self._on_interval_changed)
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


        # --- KD Tracking ---
        kd_group = QGroupBox("Player KD Tracking")
        kd_layout = QVBoxLayout()
        self.kd_checkbox = QCheckBox("Enable KD Tracking (persistent stats database)")
        self.kd_checkbox.setChecked(self._kd_enabled)
        self.kd_checkbox.stateChanged.connect(self._toggle_kd)
        kd_layout.addWidget(self.kd_checkbox)
        kd_hint = QLabel("Players can type !kd in game chat to see their stats.\nTracked: {0} players".format(
            self.stats_store.get_player_count() if self.stats_store else 0))
        kd_hint.setStyleSheet("color: #6a6a30; font-size: 8pt;")
        kd_hint.setWordWrap(True)
        self.kd_hint_label = kd_hint
        kd_layout.addWidget(kd_hint)
        kd_group.setLayout(kd_layout)
        layout.addWidget(kd_group)

        # --- Message log ---
        log_group = QGroupBox("Message Log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
        log_group.setVisible(False)

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

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

    def _on_interval_changed(self):
        self._save_config()
        if self._recurring_timer.isActive():
            intervals = {0: 300000, 1: 600000, 2: 900000, 3: 1800000, 4: 2700000, 5: 3600000}
            ms = intervals.get(self.interval_combo.currentIndex(), 600000)
            self._recurring_timer.start(ms)
            label = self.interval_combo.currentText()
            self.recur_status.setText(f"Active - every {label}")

    def _toggle_recurring(self):
        if self._recurring_timer.isActive():
            self._recurring_timer.stop()
            self.start_recur_btn.setText("Start")
            self.recur_status.setText("Stopped")
            self.recur_status.setStyleSheet("color: #6a6a20;")
            self._save_config()
        else:
            if not self._recurring_messages:
                self.log_text.append("[{0}] No recurring messages to send".format(time.strftime('%H:%M:%S')))
                return
            intervals = {0: 300000, 1: 600000, 2: 900000, 3: 1800000, 4: 2700000, 5: 3600000}  # 5, 10, 15, 30, 45, 60 min in ms
            ms = intervals.get(self.interval_combo.currentIndex(), 600000)
            self._recurring_timer.start(ms)
            self.start_recur_btn.setText("Stop")
            label = self.interval_combo.currentText()
            self.recur_status.setText(f"Active - every {label}")
            self.recur_status.setStyleSheet("color: #e8c840; font-weight: bold;")
            self._save_config()
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

    def _toggle_spree(self, state):
        self._spree_enabled = (state == 2)
        self._save_config()

    def _toggle_kd(self, state):
        self._kd_enabled = (state == 2)
        self._save_config()

    def _populate_spree_table(self):
        """Fill the spree thresholds table from self._spree_thresholds."""
        self._spree_table_loading = True
        self.spree_table.setRowCount(0)
        for kills in sorted(self._spree_thresholds.keys()):
            row = self.spree_table.rowCount()
            self.spree_table.insertRow(row)
            kills_item = QTableWidgetItem(str(kills))
            kills_item.setTextAlignment(0x0004)  # AlignCenter
            self.spree_table.setItem(row, 0, kills_item)
            self.spree_table.setItem(row, 1, QTableWidgetItem(self._spree_thresholds[kills]))
        self._spree_table_loading = False

    def _add_spree_threshold(self):
        """Add a new blank threshold row."""
        # Find the next logical kill count (max + 5, or 3 if empty)
        if self._spree_thresholds:
            next_kill = max(self._spree_thresholds.keys()) + 5
        else:
            next_kill = 3
        self._spree_thresholds[next_kill] = f">>> {{{{player}}}} hit {next_kill} kills! <<<"
        self._populate_spree_table()
        self._save_config()

    def _remove_spree_threshold(self):
        """Remove the selected threshold row."""
        row = self.spree_table.currentRow()
        if row < 0:
            return
        kills_item = self.spree_table.item(row, 0)
        if kills_item:
            kills = int(kills_item.text())
            self._spree_thresholds.pop(kills, None)
            self._populate_spree_table()
            self._save_config()

    def _reset_spree_defaults(self):
        """Reset thresholds to 3/5/7/10 defaults."""
        self._spree_thresholds = {
            3: ">>> {player} is on a KILLING SPREE! (3 Kills) <<<",
            5: ">>> {player} is on a RAMPAGE! (5 Kills) <<<",
            7: ">>> {player} is UNSTOPPABLE! (7 Kills) <<<",
            10: ">>> {player} is GODLIKE! (10 Kills) <<<"
        }
        self._populate_spree_table()
        self._save_config()

    def _on_spree_cell_changed(self, row, col):
        """Save edits made inline in the spree table."""
        if self._spree_table_loading:
            return
        # Rebuild thresholds from the entire table
        new_thresholds = {}
        for r in range(self.spree_table.rowCount()):
            kills_item = self.spree_table.item(r, 0)
            msg_item = self.spree_table.item(r, 1)
            if kills_item and msg_item:
                try:
                    kills = int(kills_item.text().strip())
                    msg = msg_item.text().strip()
                    if kills > 0 and msg:
                        new_thresholds[kills] = msg
                except ValueError:
                    pass  # skip rows with invalid kill numbers
        if new_thresholds:
            self._spree_thresholds = new_thresholds
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

class SpreeTab(QWidget):
    """Killing Spree Announcer Tab"""

    def __init__(self, server, messages_tab):
        super().__init__()
        self.server = server
        self.messages_tab = messages_tab
        self._spree_enabled = True
        self._spree_thresholds = {
            3: ">>> {player} is on a KILLING SPREE! (3 Kills) <<<",
            5: ">>> {player} is on a RAMPAGE! (5 Kills) <<<",
            7: ">>> {player} is UNSTOPPABLE! (7 Kills) <<<",
            10: ">>> {player} is GODLIKE! (10 Kills) <<<"
        }
        self._first_blood_enabled = True
        self._first_blood_templates = [
            "{player} drew first blood",
            "First blood to {player}",
            "{player} secured first blood",
            "{player} claims first blood"
        ]
        self._first_blood_ready = True
        self._spree_table_loading = False
        self._player_stats = {}
        self._load_config()
        self._build_ui()

    def _config_path(self):
        import os, sys
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_sprees.json')

    def _load_config(self):
        import os, json
        try:
            path = self._config_path()
            if os.path.exists(path):
                with open(path) as f:
                    cfg = json.load(f)
                    self._spree_enabled = cfg.get('spree_enabled', True)
                    self._first_blood_enabled = cfg.get('first_blood_enabled', True)
                    raw_thresholds = cfg.get('spree_thresholds', None)
                    if raw_thresholds is not None:
                        self._spree_thresholds = {int(k): v for k, v in raw_thresholds.items()}
            else:
                old_path = os.path.join(os.path.dirname(path), 'wolfrat_messages.json')
                if os.path.exists(old_path):
                    with open(old_path) as f:
                        old_cfg = json.load(f)
                        if 'spree_enabled' in old_cfg:
                            self._spree_enabled = old_cfg['spree_enabled']
                        if 'spree_thresholds' in old_cfg:
                            self._spree_thresholds = {int(k): v for k, v in old_cfg['spree_thresholds'].items()}
        except Exception:
            pass

    def _save_config(self):
        import json
        try:
            cfg = {
                'spree_enabled': self._spree_enabled,
                'first_blood_enabled': self._first_blood_enabled,
                'spree_thresholds': {str(k): v for k, v in sorted(self._spree_thresholds.items())}
            }
            with open(self._config_path(), 'w') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)

        spree_group = QGroupBox("Killing Spree Announcer")
        spree_layout = QVBoxLayout()

        self.spree_checkbox = QCheckBox("Enable Announcer")
        self.spree_checkbox.setChecked(self._spree_enabled)
        self.spree_checkbox.stateChanged.connect(self._toggle_spree)
        spree_layout.addWidget(self.spree_checkbox)

        self.first_blood_checkbox = QCheckBox("Enable First Blood announcements")
        self.first_blood_checkbox.setChecked(self._first_blood_enabled)
        self.first_blood_checkbox.stateChanged.connect(self._toggle_first_blood)
        spree_layout.addWidget(self.first_blood_checkbox)

        spree_layout.addWidget(QLabel("Streak Thresholds (kill count → announcement message):"))
        self.spree_table = QTableWidget(0, 2)
        self.spree_table.setHorizontalHeaderLabels(["Kills", "Announcement Message"])
        self.spree_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.spree_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.spree_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.spree_table.setMinimumHeight(300)
        self._populate_spree_table()
        self.spree_table.cellChanged.connect(self._on_spree_cell_changed)
        spree_layout.addWidget(self.spree_table)

        hint = QLabel("Use {player} as placeholder for the player's name.")
        hint.setStyleSheet("color: #6a6a30; font-size: 8pt;")
        spree_layout.addWidget(hint)

        spree_btn_layout = QHBoxLayout()
        add_spree_btn = QPushButton("+ Add")
        add_spree_btn.clicked.connect(self._add_spree_threshold)
        remove_spree_btn = QPushButton("Remove Selected")
        remove_spree_btn.clicked.connect(self._remove_spree_threshold)
        reset_spree_btn = QPushButton("Reset to Defaults")
        reset_spree_btn.clicked.connect(self._reset_spree_defaults)
        spree_btn_layout.addWidget(add_spree_btn)
        spree_btn_layout.addWidget(remove_spree_btn)
        spree_btn_layout.addWidget(reset_spree_btn)
        spree_btn_layout.addStretch()
        spree_layout.addLayout(spree_btn_layout)

        spree_group.setLayout(spree_layout)
        layout.addWidget(spree_group)

        # Log for sprees
        log_group = QGroupBox("Spree Activity Log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

    def _toggle_spree(self, state):
        self._spree_enabled = (state == 2)
        self._save_config()

    def _toggle_first_blood(self, state):
        self._first_blood_enabled = (state == 2)
        self._save_config()

    def _populate_spree_table(self):
        self._spree_table_loading = True
        self.spree_table.setRowCount(0)
        for kills, msg in sorted(self._spree_thresholds.items()):
            row = self.spree_table.rowCount()
            self.spree_table.insertRow(row)
            k_item = QTableWidgetItem(str(kills))
            m_item = QTableWidgetItem(msg)
            self.spree_table.setItem(row, 0, k_item)
            self.spree_table.setItem(row, 1, m_item)
        self._spree_table_loading = False

    def _add_spree_threshold(self):
        row = self.spree_table.rowCount()
        self.spree_table.insertRow(row)
        self.spree_table.setItem(row, 0, QTableWidgetItem("15"))
        self.spree_table.setItem(row, 1, QTableWidgetItem(">>> {player} is LEGENDARY! <<<"))

    def _remove_spree_threshold(self):
        row = self.spree_table.currentRow()
        if row >= 0:
            self.spree_table.removeRow(row)
            self._save_config_from_table()

    def _reset_spree_defaults(self):
        self._spree_thresholds = {
            3: ">>> {player} is on a KILLING SPREE! (3 Kills) <<<",
            5: ">>> {player} is on a RAMPAGE! (5 Kills) <<<",
            7: ">>> {player} is UNSTOPPABLE! (7 Kills) <<<",
            10: ">>> {player} is GODLIKE! (10 Kills) <<<"
        }
        self._populate_spree_table()
        self._save_config()

    def _on_spree_cell_changed(self, row, col):
        if self._spree_table_loading: return
        self._save_config_from_table()

    def _save_config_from_table(self):
        new_thresh = {}
        for row in range(self.spree_table.rowCount()):
            try:
                k_item = self.spree_table.item(row, 0)
                m_item = self.spree_table.item(row, 1)
                if k_item and m_item:
                    kills = int(k_item.text().strip())
                    msg = m_item.text().strip()
                    new_thresh[kills] = msg
            except ValueError:
                pass
        self._spree_thresholds = new_thresh
        self._save_config()

    def on_missions_updated(self, missions):
        """Detect real map changes via <CURRENT MISSION> tag. Reset first blood + streaks."""
        import re
        current = None
        for m in missions:
            if '<CURRENT MISSION>' in m:
                current = m.split(' - ')[0].strip()
                if ':' in current[:5]:
                    current = current.split(':', 1)[1].strip()
                current = re.sub(r'<[^>]*>', '', current).strip()
                break

        if not current:
            return

        if current != getattr(self, '_last_map', None):
            self._last_map = current
            self._first_blood_ready = True
            for stat in self._player_stats.values():
                stat['streak'] = 0
            try:
                from wolfrat.web_server import wire_log
            except ImportError:
                wire_log = lambda m: None
            wire_log(f"[SPREE] Map changed to {current} - first blood + streaks reset")

    def check_sprees(self, players):
        try:
            from wolfrat.protocol import wire_log
        except ImportError:
            wire_log = lambda m: None

        kd_enabled = self.messages_tab._kd_enabled
        if not self._spree_enabled and not kd_enabled:
            return

        for p in players:
            name = p.get('name', '').strip()
            pid = p.get('id', '')
            if not name or not pid: continue

            try:
                k_val = p.get('kills', '0')
                d_val = p.get('deaths', '0')
                kills = int(k_val) if k_val != '-' else 0
                deaths = int(d_val) if d_val != '-' else 0
            except ValueError:
                continue

            if pid not in self._player_stats:
                self._player_stats[pid] = {'kills': kills, 'deaths': deaths, 'streak': 0, 'name': name}
                if kd_enabled and getattr(self.messages_tab, 'stats_store', None):
                    self.messages_tab.stats_store.update_player(name, kills, deaths, 0)
                continue

            prev = self._player_stats[pid]
            prev['name'] = name

            # Map change resets handled by on_missions_updated() (v2.4.9)

            if deaths > prev['deaths']:
                prev['streak'] = 0
            elif kills > prev['kills']:
                gained = kills - prev['kills']
                old_streak = prev['streak']
                new_streak = old_streak + gained
                prev['streak'] = new_streak

                # First blood check
                if self._first_blood_enabled and self._first_blood_ready and new_streak >= 1:
                    fb_msg = random.choice(self._first_blood_templates).replace('{player}', name)
                    self.server.send_chat(fb_msg)
                    self.log_text.append(f"[{time.strftime('%H:%M:%S')}] FIRST BLOOD: {fb_msg}")
                    self._first_blood_ready = False

                if self._spree_enabled:
                    thresholds = sorted(self._spree_thresholds.keys(), reverse=True)
                    for t in thresholds:
                        if old_streak < t <= new_streak:
                            template = self._spree_thresholds[t]
                            msg = template.replace('{player}', name)
                            self.server.send_chat(msg)
                            self.log_text.append(f"[{time.strftime('%H:%M:%S')}] SPREE: {msg}")
                            break

            prev['kills'] = kills
            prev['deaths'] = deaths

            if kd_enabled and getattr(self.messages_tab, 'stats_store', None):
                self.messages_tab.stats_store.update_player(name, kills, deaths, prev.get('streak', 0))



class MissionsStore:
    """Persistent store of available missions (maps) on the server.
    Fetches on connect, saves to JSON, used by Mods tab for !map lookup.
    """

    def __init__(self):
        self._data = {'rotation': [], 'available': [], 'updated': None}
        self._missions_tab = None  # set by MainWindow, called after on-connect fetch
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
        '0: TD-BattleoftheBulge.bms - (2x) () () <> <NEXT MISSION>' -> 'TD-BattleoftheBulge.bms'
        '0. DM-COD4Killhouse.npj (Description)' -> 'DM-COD4Killhouse.npj'
        '10: AS - Cool Map.bms - (2x)' -> 'AS - Cool Map.bms'
        """
        import re
        name = raw.strip()
        m = re.match(r'^\d+[.:]\s*', name)
        if m:
            name = name[m.end():]

        ext_match = re.search(r'(?i)\.(bms|npaj|npj)\b', name)
        if ext_match:
            return name[:ext_match.end()].strip()

        # Fallback if no extension
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
        import re
        for line in data_str.split('\n'):
            line = line.strip()
            if not line:
                continue

            m = re.match(r'^\d+[.:]\s*', line)
            if m:
                line = line[m.end():]

            ext_match = re.search(r'(?i)\.(bms|npaj|npj)\b', line)
            if not ext_match:
                continue

            filename = line[:ext_match.end()].strip()

            desc = line[ext_match.end():].strip()
            if desc.startswith('-'): desc = desc[1:].strip()
            if desc.startswith('('): desc = desc[1:].strip()
            if desc.endswith(')'): desc = desc[:-1].strip()

            if filename:
                # Use description as the display name if available
                name = desc if desc else self._strip_ext(filename)
                self._data['available'].append({
                    'name': name,
                    'file': filename
                })
        self._save()
        # Refresh the MissionsTab table if linked
        if self._missions_tab:
            try:
                self._missions_tab.load_available_from_store()
            except Exception:
                pass

    @property
    def prefer_tac(self):
        return getattr(self, '_prefer_tac', True)

    @prefer_tac.setter
    def prefer_tac(self, val):
        self._prefer_tac = val

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

        def _score(entry):
            name_lower = entry['name'].lower()
            if self.prefer_tac and name_lower.endswith('tac'):
                return 2
            return 1

        best_match = None
        best_score = -1

        # Search rotation first
        for entry in self._data.get('rotation', []):
            if _match(entry):
                score = _score(entry)
                if score > best_score:
                    best_match = (entry['name'], entry['file'], 'rotation')
                    best_score = score

        # If we found a TAC match in rotation, use it immediately
        if best_match and best_score == 2:
            return best_match

        # Otherwise search available maps
        best_avail_match = None
        best_avail_score = -1
        for entry in self._data.get('available', []):
            if _match(entry):
                score = _score(entry)
                if score > best_avail_score:
                    best_avail_match = (entry['name'], entry['file'], 'available')
                    best_avail_score = score
                    if best_avail_score == 2:
                        return best_avail_match

        # Prefer rotation matches (even if score=1) over available maps (score=1)
        if best_match:
            return best_match
        if best_avail_match:
            return best_avail_match

        return None, None, None

    @property
    def rotation_count(self):
        return len(self._data.get('rotation', []))

    @property
    def available_count(self):
        return len(self._data.get('available', []))


class ModsTab(QWidget):
    """Moderator management - assign mods, track their commands."""

    def __init__(self, server: ServerManager, missions_store: MissionsStore):
        super().__init__()
        self.server = server
        self.missions_store = missions_store
        self.messages_tab = None  # set by MainWindow cross-tab wiring
        self.mods = {}  # {lowercase_name: display_name}
        self._seen_chat_ids = set()  # for dedup
        self._chat_initialized = False
        # Map vote state
        self._vote_enabled = True  # controlled from Settings tab
        self._vote_threshold = 51  # controlled from Settings tab
        self._vote_active = False
        self._vote_map_name = None
        self._vote_map_file = None
        self._vote_source = None
        self._vote_voters = set()  # lowercase player names
        self._vote_total = 0
        self._vote_timer = QTimer()
        self._vote_timer.setSingleShot(True)
        self._vote_timer.timeout.connect(self._vote_expired)
        # Skip vote state
        self._skip_enabled = True  # controlled from Settings tab
        self._skip_threshold = 51  # controlled from Settings tab
        self._skip_active = False
        self._skip_cooldown_until = 0  # epoch; skip cooldown timer (15 min)
        self._skip_voters = set()
        self._skip_total = 0
        self._skip_timer = QTimer()
        self._skip_timer.setSingleShot(True)
        self._skip_timer.timeout.connect(self._skip_expired)
        self._load_config()
        self._build_ui()
        # Populate list from saved mods
        for name in sorted(self.mods.values()):
            self.mod_list.addItem(name)

    def reset_chat(self):
        """Reset chat dedup state on reconnect."""
        self._seen_chat_ids = set()  # for dedup
        self._chat_initialized = False

    def on_connect(self):
        """Called on reconnect. Reset dedup so first batch is skipped, then new messages process."""
        self._seen_chat_ids = set()
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
                    self._vote_enabled = cfg.get('vote_enabled', True)
                    self._vote_threshold = cfg.get('vote_threshold', 51)
                    self._skip_enabled = cfg.get('skip_enabled', True)
                    self._skip_threshold = cfg.get('skip_threshold', 51)
        except Exception:
            pass

    def _save_config(self):
        try:
            cfg = {'mods': sorted(self.mods.values()), 'vote_enabled': self._vote_enabled, 'vote_threshold': self._vote_threshold, 'skip_enabled': self._skip_enabled, 'skip_threshold': self._skip_threshold}
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
        perms_group = QGroupBox("Mod Commands")
        perms_layout = QVBoxLayout()
        perms_label = QLabel(
            "Mods can use these commands in game chat:\n\n"
            "  !warn <player> [reason]  - Warn a player\n"
            "  !kick <player> [reason]  - Kick a player\n"
            "  !ban <player>            - Ban a player\n"
            "  !swap <player>           - Swap to other team\n"
            "  !kill <player>           - Kill a player\n"
            "  !map <name>              - Switch to a map\n"
            "  !add <name>              - Add map to rotation\n"
            "  !remove <name>           - Remove map from rotation\n"
            "  !next                    - Skip to next map\n"
            "  !startvote               - Force early end-of-map vote\n"
            "  !mixteams                - Randomly shuffle all players\n"
            "  !balanceteams            - Balance teams from bigger team\n"
            "  !time <0000-2300>        - Set time of day (fuzzy: !time 930 -> 1000)\n"
            "  !gametime <1-240>        - Set game time in minutes\n\n"
            "Map names can be partial/fuzzy: !map treasure"
        )
        perms_label.setStyleSheet("font-size: 9pt; color: #a89830; line-height: 1.4;")
        perms_label.setWordWrap(True)
        perms_layout.addWidget(perms_label)
        perms_group.setLayout(perms_layout)
        left_col.addWidget(perms_group)

        player_cmds_group = QGroupBox("Player Commands")
        player_cmds_layout = QVBoxLayout()
        player_cmds_label = QLabel(
            "Any player can use these commands in game chat:\n\n"
            "  !kd [player]             - Check KD stats\n"
            "  !list                    - Show current map rotation\n"
            "  !vote <name>             - Start a map vote\n"
            "  !skip                    - Vote to skip current map\n"
            "  !yes                     - Vote yes on current vote\n"
            "  !switch                  - Switch own team"
        )
        player_cmds_label.setStyleSheet("font-size: 9pt; color: #a89830; line-height: 1.4;")
        player_cmds_label.setWordWrap(True)
        player_cmds_layout.addWidget(player_cmds_label)
        player_cmds_group.setLayout(player_cmds_layout)
        left_col.addWidget(player_cmds_group)

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
        Permanent dedup - same message never fires twice (prevents re-firing on reconnect)."""
        wire_log(f"[MODS] update_chat called: {len(messages)} messages, initialized={self._chat_initialized}")
        if not self._chat_initialized:
            self._chat_initialized = True
            self._seen_chat_ids = {m.get('id', m.get('raw', '')) for m in messages}
            wire_log(f"[MODS] Initialized: {len(messages)} messages in list (skipping first batch)")
            return

        processed = 0
        for msg in messages:
            msg_id = msg.get('id', msg.get('raw', ''))
            if msg_id and msg_id in self._seen_chat_ids:
                continue
            self._seen_chat_ids.add(msg_id)
            text = msg.get('text', '')
            wire_log(f"[MODS] New chat: {text[:100]}")
            self._check_mod_command(text)
            processed += 1

        if processed:
            wire_log(f"[MODS] Processed {processed} messages")

        if len(self._seen_chat_ids) > 1000:
            self._seen_chat_ids = set(list(self._seen_chat_ids)[-500:])

    def _check_mod_command(self, text):
        """Check if a chat message is a mod command and execute it."""
        wire_log(f"[MODS] _check_mod_command: text={text[:80]!r}")
        if '!' not in text:
            return

        # Ignore server error messages (e.g. "Unknown command: !killme")
        # These contain '!' and ':' and would cause infinite command loops
        if text.strip().startswith('Unknown command:'):
            return

        # Extract sender name and verify authorization
        if ':' not in text:
            # No colon = sent directly by server console (Web UI / Desktop app)
            # The Web UI appends '[ADMIN] ' to all messages.
            if text.startswith('[ADMIN] !'):
                sender = 'web_admin'
                message = text[8:].strip()  # Strip off '[ADMIN] '
            else:
                return  # Ignore other system messages
        else:
            # Has colon = sent by a player in-game
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

        # !kd - any player can check their own stats (or another player's)
        if cmd == '!kd':
            wire_log(f"[MODS] !kd triggered: sender={sender} args={args}")
            if not hasattr(self, 'messages_tab') or not self.messages_tab:
                wire_log(f"[MODS] !kd: no messages_tab")
                return
            if not self.messages_tab._kd_enabled:
                wire_log(f"[MODS] !kd: kd_enabled=False")
                return
            stats_store = self.messages_tab.stats_store
            if not stats_store:
                wire_log(f"[MODS] !kd: no stats_store")
                return
            # If args provided, look up that player; otherwise look up sender
            if args:
                target_name = ' '.join(args)
            else:
                target_name = sender
            wire_log(f"[MODS] !kd: looking up '{target_name}'")
            stats = stats_store.get_player(target_name)
            if stats:
                kd_str = f"{stats['name']}: Kills: {stats['kills']} | Deaths: {stats['deaths']} | KD: {stats['kd']}"
                self.server.send_chat(kd_str)
                wire_log(f"[MODS] !kd: sent '{kd_str}'")
            else:
                self.server.send_chat(f"No stats found for {target_name}")
                wire_log(f"[MODS] !kd: no stats for '{target_name}'")
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

        # !ping - any player can check their ping
        if cmd == '!ping':
            wire_log(f"[MODS] !ping triggered: sender={sender}")
            player = find_player(sender)
            if player and player.get('ping') and player['ping'] != '-':
                ping_str = f"{player['name']}: {player['ping']}ms"
                self.server.send_chat(ping_str)
                wire_log(f"[MODS] !ping: sent '{ping_str}'")
            else:
                self.server.send_chat(f"{sender}: ping unavailable")
                wire_log(f"[MODS] !ping: ping unavailable for '{sender}'")
            return

        # !list - available to ALL players, shows current map rotation
        if cmd == '!list':
            rotation = self.missions_store._data.get('rotation', [])
            if not rotation:
                self.server.send_chat("No maps in rotation")
                return
            # Build numbered list, send in chunks to respect 69-char limit
            names = [r['name'] for r in rotation]
            full = "Rotation: " + ", ".join(f"{i}.{n}" for i, n in enumerate(names, 1))
            # Split into 69-char chunks
            chunk = ""
            for part in full.split(", "):
                test = f"{chunk}, {part}" if chunk else part
                if len(test) > 69:
                    if chunk:
                        self.server.send_chat(chunk)
                    chunk = part
                else:
                    chunk = test
            if chunk:
                self.server.send_chat(chunk)
            return

        # !vote <map> - any player can start a map vote
        if cmd == '!vote':
            if not self._vote_enabled:
                return
            if self._vote_active:
                self.server.send_chat("Vote already in progress")
                return
            map_name = ' '.join(args) if args else ''
            if not map_name:
                self.server.send_chat("Usage: !vote <map name>")
                return
            # Need 2+ players
            player_count = len(self.server.players)
            if player_count < 2:
                self.server.send_chat("Need at least 2 players to vote")
                return
            # Find the map
            name, filename, source = find_map(map_name)
            if not name or not filename:
                self.server.send_chat(f"Map not found: {map_name}")
                return
            # Start the vote
            self._vote_active = True
            self._vote_map_name = name
            self._vote_map_file = filename
            self._vote_source = source
            self._vote_voters = set()
            self._vote_total = player_count
            self._vote_voters.add(sender)  # starter auto-votes
            self.server.send_chat(f"Map vote: {name}. Type !yes. 60 seconds.")
            self._vote_timer.start(60000)
            self._vote_id = getattr(self, '_vote_id', 0) + 1
            self._schedule_vote_milestones(self._vote_id)
            self.mod_log.addItem(f"[{now}] {sender} started vote for {name} ({player_count} players)")
            return

        # !skip - any player can start a skip vote for the current map
        if cmd == '!skip':
            if not self._skip_enabled:
                return
            if self._skip_active:
                self.server.send_chat("Skip vote already in progress")
                return
            # 15-minute cooldown check
            remaining = int(self._skip_cooldown_until - time.time())
            if remaining > 0:
                mins = remaining // 60
                secs = remaining % 60
                self.server.send_chat(f"Skip on cooldown - {mins}m {secs}s left")
                return
            player_count = len(self.server.players)
            if player_count < 1:
                self.server.send_chat("Need at least 1 player to vote")
                return
            self._skip_active = True
            self._skip_voters = set()
            self._skip_total = player_count
            self._skip_voters.add(sender)  # starter auto-votes
            # Check if auto-vote already meets threshold (solo player)
            threshold = int(self._skip_total * self._skip_threshold / 100) + 1
            if len(self._skip_voters) >= threshold:
                self.server.send_chat("Skip vote passed! Skipping map...")
                self.mod_log.addItem(f"[{now}] Skip vote passed ({len(self._skip_voters)}/{self._skip_total})")
                wire_log(f"[SKIP] Passed immediately ({len(self._skip_voters)}/{self._skip_total})")
                self.server.send('GOTO GAMESTATE')
                self._skip_active = False
                self._skip_cooldown_until = time.time() + 900  # 15 min cooldown
                return
            self.server.send_chat("Skip current map? Type !yes. 60 seconds.")
            self._skip_timer.start(60000)
            self._skip_id = getattr(self, '_skip_id', 0) + 1
            self._schedule_skip_milestones(self._skip_id)
            self.mod_log.addItem(f"[{now}] {sender} started skip vote ({player_count} players)")
            return

        # !yes handles BOTH map vote and skip vote - check skip first
        if cmd == '!yes':
            # Skip vote takes priority if active
            if self._skip_active and self._skip_enabled:
                if sender in self._skip_voters:
                    # Already voted on skip, check map vote
                    pass
                else:
                    sender_is_player = any(
                        p.get('name', '').lower() == sender for p in self.server.players
                    )
                    if sender_is_player:
                        self._skip_voters.add(sender)
                        votes = len(self._skip_voters)
                        threshold = int(self._skip_total * self._skip_threshold / 100) + 1
                        wire_log(f"[SKIP] {sender} voted yes. {votes}/{self._skip_total} (need {threshold}, {self._skip_threshold}%)")
                        if votes >= threshold:
                            self._skip_timer.stop()
                            self.server.send_chat("Skip vote passed! Skipping map...")
                            self.mod_log.addItem(f"[{now}] Skip vote passed ({votes}/{self._skip_total})")
                            self.server.send('GOTO GAMESTATE')
                            self._skip_active = False
                            self._skip_cooldown_until = time.time() + 900  # 15 min cooldown
                            return

            # Map vote
            if not self._vote_enabled:
                return
            if not self._vote_active:
                return
            if sender in self._vote_voters:
                return  # already voted
            # Check sender is a connected player
            sender_is_player = any(
                p.get('name', '').lower() == sender for p in self.server.players
            )
            if not sender_is_player:
                return
            self._vote_voters.add(sender)
            votes = len(self._vote_voters)
            threshold = int(self._vote_total * self._vote_threshold / 100) + 1
            wire_log(f"[VOTE] {sender} voted yes. {votes}/{self._vote_total} (need {threshold}, {self._vote_threshold}%)")
            # Check if we hit threshold
            if votes >= threshold:
                self._vote_timer.stop()
                self.server.send_chat(f"Vote passed! Switching to {self._vote_map_name}...")
                self.mod_log.addItem(f"[{now}] Vote passed: {self._vote_map_name} ({votes}/{self._vote_total})")
                # If map is available but not in rotation, add it first
                if self._vote_source == 'available':
                    MissionsTab._send_mission_add_to_server(self.server, self._vote_map_file, 1)
                    time.sleep(0.5)
                    self.server.send('mission list')
                    time.sleep(0.5)
                # Set the voted map as next, then cycle
                mtab = getattr(self.server, '_missions_tab', None)
                if mtab:
                    idx = mtab._get_server_index(self._vote_map_file)
                    if idx is not None:
                        self.server.send(f'MISSION SETNEXT {idx}')
                    else:
                        wire_log(f"[VOTE] Could not find index for {self._vote_map_file}, falling back to 999")
                        self.server.send('MISSION SETNEXT 999')
                else:
                    wire_log(f"[VOTE] No missions_tab found, falling back to 999")
                    self.server.send('MISSION SETNEXT 999')
                time.sleep(0.3)
                self.server.send('GOTO GAMESTATE')
                self._vote_active = False
            return

        # If it's a ! command but not recognized, tell them
        valid_commands = {'!warn', '!kick', '!ban', '!swap', '!kill', '!next', '!map', '!add', '!remove', '!1', '!2', '!3', '!startvote', '!mixteams', '!balanceteams', '!time', '!gametime'}
        if cmd not in valid_commands:
            self.server.send_chat(f"Unknown command: {cmd}")
            return

        # Map voting commands (!1, !2, !3) - any player can vote
        if cmd in ('!1', '!2', '!3'):
            try:
                map_tab = getattr(self.server, '_map_voting_tab', None)
                if map_tab:
                    map_tab.on_vote(sender, int(cmd[1:]))
                    wire_log(f"[VOTE] Forwarded {cmd} from {sender}")
                else:
                    wire_log(f"[VOTE] No map_voting_tab found")
            except Exception as e:
                wire_log(f"[VOTE] Error forwarding vote: {e}")
            return

        # All other commands require mod status
        if sender != 'web_admin' and sender not in self.mods:
            wire_log(f"[MODS] sender '{sender}' not in mods {list(self.mods.keys())} - ignoring")
            return

        if cmd == '!startvote':
            map_tab = getattr(self.server, '_map_voting_tab', None)
            if map_tab:
                if map_tab._vote_active:
                    self.server.send_chat("Vote is already running.")
                else:
                    self.server.send_chat(f"Mod {sender} forced an early end-of-match map vote.")
                    map_tab._start_vote()
                    self.mod_log.addItem(f"[{now}] {sender} triggered an early map vote")
            else:
                self.server.send_chat("Map voting tab not found.")
            return

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
            self.server.send('GOTO GAMESTATE')
            self.server.send_chat("Skipping to next map...")
            self.mod_log.addItem(f"[{now}] {sender} skipped to next map")

        elif cmd == '!mixteams':
            if sender not in self.mods and sender != 'web_admin':
                return
            results = self.server.shuffle_teams()
            for line in results:
                self.server._log(line)
            self.mod_log.addItem(f"[{now}] {sender} mixed teams")

        elif cmd == '!balanceteams':
            if sender not in self.mods and sender != 'web_admin':
                return
            results = self.server.mix_teams()
            for line in results:
                self.server._log(line)
            self.mod_log.addItem(f"[{now}] {sender} balanced teams")

        elif cmd == '!time':
            if sender not in self.mods and sender != 'web_admin':
                return
            raw = args[0] if args else ''
            # Strip colons: "09:31" -> "0931", "9:30" -> "930"
            raw = raw.replace(':', '')
            # Validate: must be all digits
            if not raw.isdigit() or not raw:
                self.server.send_chat("Usage: !time <0000-2300> e.g. !time 0100")
                return
            # Pad to 4 digits: "1" -> "0100", "13" -> "1300", "930" -> "0930"
            if len(raw) == 1:
                raw = raw + '00'
            elif len(raw) == 2:
                raw = raw + '00'
            elif len(raw) == 3:
                raw = '0' + raw
            # Now raw is 4 digits: HHMM
            hour = int(raw[:2])
            minute = int(raw[2:])
            # Clamp minutes to nearest hour (>=30 rounds up)
            if minute >= 30:
                hour += 1
            # Wrap 24 -> 0
            hour = hour % 24
            time_str = f"{hour:02d}00"
            self.server.send(f"CMD TOD {time_str}")
            self.server.send_chat(f"Time of day set to {hour:02d}:00")
            self.mod_log.addItem(f"[{now}] {sender} set time of day to {hour:02d}:00")

        elif cmd == '!gametime':
            if sender not in self.mods and sender != 'web_admin':
                return
            raw = args[0] if args else ''
            if not raw.isdigit() or not raw:
                self.server.send_chat("Usage: !gametime <1-240> minutes")
                return
            val = int(raw)
            if val < 1 or val > 240:
                self.server.send_chat("Game time must be 1-240 minutes")
                return
            self.server.set_setting("gameTime", str(val))
            self.server.send_chat(f"Game time set to {val} minutes")
            self.mod_log.addItem(f"[{now}] {sender} set game time to {val} minutes")

        elif cmd == '!map':
            map_name = ' '.join(args) if args else ''
            wire_log(f"[MODS] !map: map_name='{map_name}'")
            name, filename, source = find_map(map_name)
            wire_log(f"[MODS] !map result: name={name} file={filename} source={source}")
            if name and filename:
                if source == 'available':
                    # Map is on server but not in rotation - add it first
                    MissionsTab._send_mission_add_to_server(self.server, filename, 1)
                    time.sleep(0.5)
                    self.server.send('mission list')  # refresh rotation
                    time.sleep(0.5)
                    self.mod_log.addItem(f"[{now}] {sender} added {name} to rotation")
                    mtab = getattr(self.missions_store, '_missions_tab', None)
                    if mtab:
                        idx = len(mtab._rotation_maps)
                        self.server.send(f"MISSION SETNEXT {idx}")
                        time.sleep(0.3)
                        self.server.send('GOTO GAMESTATE')
                    else:
                        self.server.send('GOTO GAMESTATE')
                else:
                    mtab = getattr(self.missions_store, '_missions_tab', None)
                    if mtab:
                        mtab._switch_to_map_by_name(filename)
                    else:
                        self.server.send_chat(f"Could not switch to {name} - missions tab unavailable")
                        return
                self.server.send_chat(f"Switching to {name}...")
                self.mod_log.addItem(f"[{now}] {sender} switched to {name}")
            else:
                self.server.send_chat(f"Map not found: {map_name}")
                self.mod_log.addItem(f"[{now}] {sender} tried !map but '{map_name}' not found")

        elif cmd == '!add':
            map_name = ' '.join(args) if args else ''
            name, filename, source = find_map(map_name)
            if name and filename:
                MissionsTab._send_mission_add_to_server(self.server, filename, 1)
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

    def _schedule_vote_milestones(self, vote_id):
        QTimer.singleShot(20000, lambda: self._vote_milestone(vote_id, 20))
        QTimer.singleShot(40000, lambda: self._vote_milestone(vote_id, 40))

    def _vote_milestone(self, vote_id, seconds):
        if not self._vote_active or getattr(self, '_vote_id', 0) != vote_id:
            return
        votes = len(self._vote_voters)
        threshold = int(self._vote_total * self._vote_threshold / 100) + 1
        needed = max(0, threshold - votes)
        if seconds == 20:
            self.server.send_chat(f"Map Vote (20s): {votes} votes cast so far. Type !yes")
        elif seconds == 40:
            self.server.send_chat(f"Map Vote (40s): Need {needed} more votes to pass! Type !yes")

    def _schedule_skip_milestones(self, skip_id):
        QTimer.singleShot(20000, lambda: self._skip_milestone(skip_id, 20))
        QTimer.singleShot(40000, lambda: self._skip_milestone(skip_id, 40))

    def _skip_milestone(self, skip_id, seconds):
        if not self._skip_active or getattr(self, '_skip_id', 0) != skip_id:
            return
        votes = len(self._skip_voters)
        threshold = int(self._skip_total * self._skip_threshold / 100) + 1
        needed = max(0, threshold - votes)
        if seconds == 20:
            self.server.send_chat(f"Skip Vote (20s): {votes} votes cast so far. Type !yes")
        elif seconds == 40:
            self.server.send_chat(f"Skip Vote (40s): Need {needed} more votes to skip! Type !yes")

    def _vote_expired(self):
        """Called when the 60-second vote timer expires."""
        if not self._vote_active:
            return
        votes = len(self._vote_voters)
        name = self._vote_map_name
        threshold = int(self._vote_total * self._vote_threshold / 100) + 1
        if votes >= threshold:
            self.server.send_chat(f"Vote passed! Switching to {name}...")
            self.mod_log.addItem(f"Vote passed on expiry: {name} ({votes}/{self._vote_total})")
            wire_log(f"[VOTE] Passed on expiry: {name} ({votes}/{self._vote_total})")
            # Switch map
            mtab = getattr(self.server, '_missions_tab', None)
            if mtab:
                idx = mtab._get_server_index(self._vote_map_file)
                if idx is not None:
                    self.server.send(f'MISSION SETNEXT {idx}')
                else:
                    wire_log(f"[VOTE] Could not find index for {self._vote_map_file}, falling back to 999")
                    self.server.send('MISSION SETNEXT 999')
            else:
                self.server.send('MISSION SETNEXT 999')
            time.sleep(0.3)
            self.server.send('GOTO GAMESTATE')
            self._vote_cooldown_until = time.time() + 900
        else:
            self.server.send_chat(f"Vote ended: not enough votes ({votes}/{self._vote_total})")
            self.mod_log.addItem(f"Vote expired: {name} ({votes}/{self._vote_total})")
            wire_log(f"[VOTE] Expired: {name} ({votes}/{self._vote_total})")
        self._vote_active = False

    def _skip_expired(self):
        """Called when the 60-second skip vote timer expires."""
        if not self._skip_active:
            return
        votes = len(self._skip_voters)
        threshold = int(self._skip_total * self._skip_threshold / 100) + 1
        if votes >= threshold:
            self.server.send_chat("Skip vote passed! Skipping map...")
            self.mod_log.addItem(f"Skip vote passed ({votes}/{self._skip_total})")
            wire_log(f"[SKIP] Passed on expiry ({votes}/{self._skip_total})")
            self.server.send('GOTO GAMESTATE')
            self._skip_cooldown_until = time.time() + 900
        else:
            self.server.send_chat(f"Skip vote ended: not enough votes ({votes}/{self._skip_total})")
            self.mod_log.addItem(f"Skip vote expired ({votes}/{self._skip_total})")
            wire_log(f"[SKIP] Expired ({votes}/{self._skip_total})")
        self._skip_active = False


class MapVotingTab(QWidget):
    """End-of-match map voting management."""

    def __init__(self, server: ServerManager, missions_tab: MissionsTab):
        super().__init__()
        self.server = server
        self.missions_tab = missions_tab
        self.server._map_voting_tab = self  # allow mods system to forward votes

        # Register raw chat callback for vote detection
        if hasattr(self.server, 'proto') and self.server.proto:
            self.server.proto._on_raw_chat = self._on_raw_chat

        self._vote_active = False
        self._vote_stage = 'idle'
        self._votes = {}  # pid -> map_index (1,2,3)
        self._map_choices = [] # [(row_idx, filename), ...]
        self._last_progress_update = 0
        self._server_game_time_total = 0  # total minutes from server
        self._server_game_time_remaining = 0  # remaining minutes from server
        self._server_time_updated = 0  # timestamp of last server update

        import json, os, sys
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        self._recently_played_file = os.path.join(base, 'wolfrat_recently_played.json')
        try:
            if os.path.exists(self._recently_played_file):
                with open(self._recently_played_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._recently_played = data.get('recently_played', [])
                        self._current_map = data.get('current_map')
                        self._match_start_time = data.get('match_start_time', 0)
                        self._reset_pool_pct = data.get('reset_pool_pct', 50)
                        self._vote_choices = data.get('vote_choices', 3)
                        self._voting_enabled = data.get('voting_enabled', True)
                        self._match_duration = data.get('match_duration', 30)
                        self._trigger_mins = data.get('trigger_mins', 3)
                        self._vote_duration = data.get('vote_duration', 2)
                    elif isinstance(data, list):
                        self._recently_played = data
                        self._current_map = None
                        self._match_start_time = 0
                        self._reset_pool_pct = 50
                        self._vote_choices = 3
                        self._voting_enabled = True
                        self._match_duration = 30
                        self._trigger_mins = 3
                        self._vote_duration = 2
            else:
                self._recently_played = []
                self._current_map = None
                self._match_start_time = 0
                self._reset_pool_pct = 50
                self._vote_choices = 3
                self._voting_enabled = True
                self._match_duration = 30
                self._trigger_mins = 3
                self._vote_duration = 2
        except Exception:
            self._recently_played = []
            self._current_map = None
            self._match_start_time = 0
            self._reset_pool_pct = 50
            self._vote_choices = 3
            self._voting_enabled = True
            self._match_duration = 30
            self._trigger_mins = 3
            self._vote_duration = 2

        self._build_ui()

        self._tick_timer = QTimer()
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(5000)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        config_group = QGroupBox("Map Voting Configuration")
        config_layout = QGridLayout()

        self.enable_cb = QCheckBox("Enable End-of-Match Auto Voting")
        self.enable_cb.setChecked(self._voting_enabled)
        config_layout.addWidget(self.enable_cb, 0, 0, 1, 2)

        config_layout.addWidget(QLabel("Match Duration (mins):"), 1, 0)
        self.match_duration_spin = QSpinBox()
        self.match_duration_spin.setRange(1, 120)
        self.match_duration_spin.setValue(self._match_duration)
        config_layout.addWidget(self.match_duration_spin, 1, 1)

        config_layout.addWidget(QLabel("Trigger Vote X mins before end:"), 2, 0)
        self.trigger_spin = QSpinBox()
        self.trigger_spin.setRange(1, 20)
        self.trigger_spin.setValue(self._trigger_mins)
        config_layout.addWidget(self.trigger_spin, 2, 1)

        config_layout.addWidget(QLabel("Vote Duration (mins):"), 3, 0)
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 10)
        self.duration_spin.setValue(self._vote_duration)
        config_layout.addWidget(self.duration_spin, 3, 1)

        # (Row 1, Cols 2 & 3)
        config_layout.addWidget(QLabel("Reset Pool At:"), 1, 2)
        slider_layout = QHBoxLayout()
        self.reset_pool_slider = QSlider(Qt.Orientation.Horizontal)
        self.reset_pool_slider.setRange(20, 100)
        self.reset_pool_slider.setSingleStep(5)
        self.reset_pool_slider.setValue(self._reset_pool_pct)
        self.reset_pool_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #1a1a00; height: 8px; border-radius: 4px; }
            QSlider::handle:horizontal { background: #e8c840; width: 16px; height: 16px; margin: -4px 0; border-radius: 8px; }
            QSlider::handle:horizontal:hover { background: #ffd700; }
            QSlider::sub-page:horizontal { background: #807020; height: 8px; border-radius: 4px; }
        """)
        self.reset_pool_val_lbl = QLabel(f"{self._reset_pool_pct}%")
        slider_layout.addWidget(self.reset_pool_slider)
        slider_layout.addWidget(self.reset_pool_val_lbl)
        config_layout.addLayout(slider_layout, 1, 3)

        # (Row 2, Cols 2 & 3)
        config_layout.addWidget(QLabel("Vote Choices (2-5):"), 2, 2)
        self.choices_spin = QSpinBox()
        self.choices_spin.setRange(2, 5)
        self.choices_spin.setValue(self._vote_choices)
        config_layout.addWidget(self.choices_spin, 2, 3)

        # (Row 3, Cols 2 & 3)
        blacklist_title = QLabel("Blacklist:")
        blacklist_title.setToolTip("Maps are only blacklisted if players are on the server and actively voting.")
        config_layout.addWidget(blacklist_title, 3, 2)

        self.blacklist_lbl = QLabel("...")
        self.blacklist_lbl.setStyleSheet("font-weight: bold; color: #a89830;")
        self.blacklist_lbl.setToolTip("Maps are only blacklisted if players are on the server and actively voting.")
        config_layout.addWidget(self.blacklist_lbl, 3, 3)

        self.reset_pool_slider.valueChanged.connect(self._on_reset_pool_changed)
        self.choices_spin.valueChanged.connect(self._save_recently_played)
        self.enable_cb.stateChanged.connect(self._save_recently_played)
        self.match_duration_spin.valueChanged.connect(self._save_recently_played)
        self.trigger_spin.valueChanged.connect(self._save_recently_played)
        self.duration_spin.valueChanged.connect(self._save_recently_played)

        self.start_btn = SatisfyingButton("Start Vote Now")
        self.start_btn.clicked.connect(self._start_vote)
        config_layout.addWidget(self.start_btn, 4, 0, 1, 4)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        self.status_lbl = QLabel("Status: Waiting for map change...")
        self.status_lbl.setStyleSheet("font-size: 11pt; padding: 10px; color: #a89830;")
        layout.addWidget(self.status_lbl)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        layout.addWidget(self.log_text)

    def _on_reset_pool_changed(self, val):
        self.reset_pool_val_lbl.setText(f"{val}%")
        self._update_blacklist_label()
        self._save_recently_played()

    def _update_blacklist_label(self):
        mtab = getattr(self, 'missions_tab', None)
        if not mtab and hasattr(self.parent(), 'missions_tab'):
            mtab = self.parent().missions_tab

        total = len(mtab._rotation_maps) if (mtab and hasattr(mtab, '_rotation_maps') and mtab._rotation_maps) else 0
        if total > 0:
            pct = self.reset_pool_slider.value() / 100.0
            limit = max(1, int(total * pct))
            current = len(self._recently_played)
            rem = max(0, limit - current)
            self.blacklist_lbl.setText(f"{current} blacklisted ({rem} remaining before reset)")
        else:
            self.blacklist_lbl.setText(f"{len(self._recently_played)} blacklisted (No rotation set)")

    def log(self, msg):
        import time
        now = time.strftime('%H:%M:%S')
        self.log_text.appendPlainText(f"[{now}] {msg}")

    def _save_recently_played(self):
        import json
        try:
            data = {
                'recently_played': self._recently_played,
                'current_map': self._current_map,
                'match_start_time': self._match_start_time,
                'reset_pool_pct': self.reset_pool_slider.value(),
                'vote_choices': self.choices_spin.value(),
                'voting_enabled': self.enable_cb.isChecked(),
                'match_duration': self.match_duration_spin.value(),
                'trigger_mins': self.trigger_spin.value(),
                'vote_duration': self.duration_spin.value()
            }
            with open(self._recently_played_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass

    def on_missions_updated(self, missions):
        import time
        import re
        current = None
        # Pass 1: find <CURRENT MISSION> specifically
        for m in missions:
            if '<CURRENT MISSION>' in m:
                current = m.split(' - ')[0].strip()
                if ':' in current[:5]:
                    current = current.split(':', 1)[1].strip()
                # Strip any leftover tags like <CURRENT MISSION> <> etc.
                current = re.sub(r'<[^>]*>', '', current).strip()
                break
        # Pass 2: fall back to <NEXT MISSION> only if no current found
        if not current:
            for m in missions:
                if '<NEXT MISSION>' in m:
                    current = m.split(' - ')[0].strip()
                    if ':' in current[:5]:
                        current = current.split(':', 1)[1].strip()
                    current = re.sub(r'<[^>]*>', '', current).strip()
                    break

        if current and current != self._current_map:
            display = self.missions_tab._find_display_name(current)
            self.log(f"New map detected: {display}. Resetting stopwatch.")
            self._current_map = current
            self._match_start_time = time.time()
            self._vote_active = False
            self._vote_stage = 'idle'

            # --- FIX: Clear list if it hits 50% of the total rotation ---
            mtab = getattr(self, 'missions_tab', None)
            if not mtab and hasattr(self.parent(), 'missions_tab'):
                mtab = self.parent().missions_tab

            if mtab and mtab._rotation_maps:
                total_maps = len(mtab._rotation_maps)
                pct = self.reset_pool_slider.value() / 100.0
                limit = max(1, int(total_maps * pct))
                if len(self._recently_played) >= limit:
                    self.log(f"Recent maps reached {limit} ({self.reset_pool_slider.value()}% of {total_maps}). Clearing list.")
                    self._recently_played.clear()
            # ------------------------------------------------------------

            # --- FIX: Only add to blacklist if there are players ---
            has_players = len(self.server.players) > 0
            if has_players and current not in self._recently_played:
                self._recently_played.append(current)
            elif not has_players:
                self.log(f"Server is empty. {display} not added to blacklist.")
            # -------------------------------------------------------

            self._save_recently_played()
            QTimer.singleShot(500, self._update_blacklist_label)

    def update_game_time(self, total_mins, remaining_mins):
        """Called by SettingsTab when GameTime is received from server."""
        import time
        self._server_game_time_total = total_mins
        self._server_game_time_remaining = remaining_mins
        self._server_time_updated = time.time()
        # Auto-sync match duration spinbox with server value
        if total_mins > 0:
            self.match_duration_spin.setValue(total_mins)

    def _tick(self):
        import time
        if not self._match_start_time:
            return

        elapsed = time.time() - self._match_start_time
        # Use server remaining time if available (updated within last 30s)
        if self._server_time_updated and (time.time() - self._server_time_updated) < 30:
            match_secs = self._server_game_time_total * 60
        else:
            match_secs = self.match_duration_spin.value() * 60
        trigger_secs = self.trigger_spin.value() * 60

        vote_start_target = match_secs - trigger_secs

        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        if self._vote_stage == 'idle':
            rem = vote_start_target - elapsed
            # Show remaining time from server if available
            if self._server_time_updated and (time.time() - self._server_time_updated) < 30:
                time_left = self._server_game_time_remaining
                time_str = f"{time_left}m remaining"
            else:
                time_str = f"{mins:02d}:{secs:02d} elapsed"
            if rem > 0:
                self.status_lbl.setText(f"Status: Match running ({time_str}). Auto-vote in {int(rem//60):02d}:{int(rem%60):02d}")
            else:
                self.status_lbl.setText(f"Status: Match running ({time_str}). Auto-vote pending...")

            if self.enable_cb.isChecked() and elapsed >= vote_start_target:
                if len(self.server.players) > 0:
                    self._start_vote()

        elif self._vote_stage == 'voting':
            vote_elapsed = time.time() - self._vote_start_time
            vote_dur_secs = self.duration_spin.value() * 60
            rem = vote_dur_secs - vote_elapsed
            if rem > 0:
                self.status_lbl.setText(f"Status: VOTE ACTIVE! ({int(rem//60):02d}:{int(rem%60):02d} remaining)")
                third = vote_dur_secs / 3
                if vote_elapsed >= third and self._last_progress_update < 1:
                    self._send_progress_update()
                    self._last_progress_update = 1
                elif vote_elapsed >= third * 2 and self._last_progress_update < 2:
                    self._send_progress_update()
                    self._last_progress_update = 2
            else:
                self._end_vote()

    def _start_vote(self):
        import time
        import random
        if self._vote_active:
            return

        rotation = self.missions_tab._rotation_maps
        if not rotation:
            self.log("Cannot start vote: No maps in rotation.")
            return

        # --- FIX: Only blacklist current map if players are present ---
        has_players = len(self.server.players) > 0
        if has_players and self._current_map and self._current_map not in self._recently_played:
            bl_name = self.missions_tab._find_display_name(self._current_map)
            self.log(f"Players present. Blacklisting current map: {bl_name}")
            self._recently_played.append(self._current_map)
            self._save_recently_played()
        elif not has_players:
            self.log(f"Server empty. Current map not blacklisted.")
        # -------------------------------------------------------------------

        # Pick random maps, excluding recent if possible
        num_choices = self.choices_spin.value()
        pool = []
        for idx, filename in enumerate(rotation):
            if filename not in self._recently_played:
                if not (filename.lower().startswith("00tr") or "training" in filename.lower()):
                    pool.append((idx, filename))

        # If pool is too small, just use full rotation
        if len(pool) < num_choices:
            if self._recently_played:
                self.log(f"Fewer than {num_choices} non-blacklisted maps available. Clearing blacklist.")
                self._recently_played.clear()
                self._save_recently_played()
                QTimer.singleShot(500, self._update_blacklist_label)
                pool = [(idx, fname) for idx, fname in enumerate(rotation)
                        if not (fname.lower().startswith("00tr") or "training" in fname.lower())]
            else:
                pool = [(idx, fname) for idx, fname in enumerate(rotation)
                        if not (fname.lower().startswith("00tr") or "training" in fname.lower())]

        random.shuffle(pool)
        self._map_choices = pool[:num_choices]

        if not self._map_choices:
            return

        self._vote_active = True
        self._vote_stage = 'voting'
        self._vote_start_time = time.time()
        self._votes = {}
        self._last_progress_update = 0

        self.log("Map voting started.")
        options_text = ", ".join([f"!{i+1}" for i in range(num_choices)])
        self.server.send_chat(f"Map Voting Started! Type {options_text} to vote.")

        # Send options with delays to avoid truncation
        def send_opt(i):
            if i >= len(self._map_choices): return
            idx, fname = self._map_choices[i]
            display = self.missions_tab._find_display_name(fname)
            # Trim to 69-char chat limit (prefix "N: " = 3 chars)
            if len(display) > 66:
                display = display[:63] + "..."
            self.server.send_chat(f"{i+1}: {display}")

        # Dynamically queue chat messages for however many choices we have
        for i in range(num_choices):
            QTimer.singleShot((i + 1) * 1000, lambda idx=i: send_opt(idx))

        dur_mins = self.duration_spin.value()
        QTimer.singleShot((num_choices + 1) * 1000, lambda: self.server.send_chat(f"Type {options_text}. You have {dur_mins} mins!"))

    def _send_progress_update(self):
        """Send a mid-vote progress update to chat."""
        try:
            counts, _, total = self._tally_votes()
            num_choices = self.choices_spin.value()
            if total == 0:
                options = ", ".join([f"!{i+1}" for i in range(num_choices)])
                self.server.send_chat(f"No votes yet! Type {options} to vote.")
            else:
                parts = []
                for i in range(num_choices):
                    fname = self._map_choices[i][1]
                    display = self.missions_tab._find_display_name(fname)
                    parts.append(f"{i+1}:{counts.get(i+1,0)}")
                self.server.send_chat(f"Votes: {', '.join(parts)} ({total} total)")
        except Exception:
            pass

    def _tally_votes(self):
        """Tally current votes. Returns (counts_dict, winner_opt, total_votes)."""
        num_choices = self.choices_spin.value()
        counts = {i:0 for i in range(1, num_choices + 1)}
        for v in self._votes.values():
            if v in counts:
                counts[v] += 1
        winner_opt = 1
        max_v = -1
        for opt, c in counts.items():
            if c > max_v:
                max_v = c
                winner_opt = opt
        total = sum(counts.values())
        return counts, winner_opt, total

    def _end_vote(self):
        self._vote_active = False
        self._vote_stage = 'done'
        self.status_lbl.setText("Status: Vote Complete")

        try:
            if not self._map_choices:
                return

            # Tally
            counts, _, total = self._tally_votes()

            tally_parts = []
            for k in sorted(counts.keys()):
                tally_parts.append(f"{k}={counts[k]}")
            self.log(f"Vote ended. Tally: {', '.join(tally_parts)}")

            if total == 0:
                # No votes cast at all, just silently pick the first option
                winner_opt = 1
                is_draw = False
                max_v = 0
            else:
                import random
                max_v = max(counts.values())
                tied_opts = [opt for opt, c in counts.items() if c == max_v]
                winner_opt = random.choice(tied_opts)
                is_draw = len(tied_opts) > 1

            winner_idx = winner_opt - 1
            if winner_idx >= len(self._map_choices):
                winner_idx = 0

            row_idx, fname = self._map_choices[winner_idx]
            display = self.missions_tab._find_display_name(fname)

            if total > 0:
                if is_draw:
                    self.server.send_chat(f"Draw! Server coin flip selected: {display}")
                else:
                    self.server.send_chat(f"Vote Complete! {display} wins with {max_v} vote{'s' if max_v != 1 else ''}!")

            # --- FIX: Dynamically find the correct index at execution time ---
            mtab = getattr(self, 'missions_tab', None)
            if not mtab and hasattr(self.parent(), 'missions_tab'):
                mtab = self.parent().missions_tab

            if mtab:
                idx = mtab._get_server_index(fname)
                if idx >= 0:
                    self.log(f"Winner declared: {display} (Found at Server Index {idx})")
                    self.server.send(f"MISSION SETNEXT {idx}")
                elif mtab._find_rotation_row(fname) >= 0:
                    row = mtab._find_rotation_row(fname)
                    self.log(f"Winner declared: {display} (Found at Row {row})")
                    self.server.send(f"MISSION SETNEXT {row}")
                else:
                    self.log(f"Winner declared: {display} (Not in rotation, adding first)")
                    import time
                    # Map not in server rotation anymore, add it to the end
                    mtab._send_mission_add_to_server(self.server, fname, 1)
                    time.sleep(0.5)
                    # Its new index will be the current length of the rotation list
                    idx = len(mtab._rotation_maps)
                    self.server.send(f"MISSION SETNEXT {idx}")
                    # Request a refresh so the UI updates with the newly added map
                    self.server.send("mission list")
            else:
                # Fallback if we can't access MissionsTab (shouldn't happen)
                self.log(f"Winner declared: {display} (Fallback queueing)")
                # Just guess it's at the end
                self.server.send(f"MISSION SETNEXT 999")
            # ---------------------------------------------------------------

            # --- FIX: Clear list if it hits 50% of the total rotation ---
            if mtab and mtab._rotation_maps:
                total_maps = len(mtab._rotation_maps)
                pct = self.reset_pool_slider.value() / 100.0
                limit = max(1, int(total_maps * pct))
                if len(self._recently_played) >= limit:
                    self.log(f"Recent maps reached {limit} ({self.reset_pool_slider.value()}% of {total_maps}). Clearing list.")
                    self._recently_played.clear()
            # ------------------------------------------------------------

            # --- FIX: Do not blacklist the winner if no votes were cast ---
            if total > 0:
                if fname not in self._recently_played:
                    self._recently_played.append(fname)
            else:
                self.log(f"0 votes cast. {fname} not added to blacklist.")
            # --------------------------------------------------------------

            self._save_recently_played()
            QTimer.singleShot(500, self._update_blacklist_label)

        except Exception as e:
            self.log(f"Error ending vote: {e}")
            try:
                self.server.send_chat("Vote ended with an error. Map unchanged.")
            except:
                pass

    def _on_raw_chat(self, data):
        """Process raw chat payload directly for votes. Bypasses overlap detection."""
        if not self._vote_active:
            return
        try:
            import re
            for line in data.split('\n'):
                line = line.strip().rstrip('\r')
                if not line:
                    continue
                # Match: PlayerName: !1 / !2 / !3 / !4 / !5
                m = re.match(r'^(.+?):\s*(![12345])\s*$', line)
                if m:
                    sender = m.group(1).strip()
                    cmd = m.group(2)
                    if sender and sender != 'Server':
                        self.on_vote(sender, int(cmd[1:]))
        except Exception:
            pass

    def on_vote(self, sender, opt):
        """Process a single vote from a player (!1, !2, !3)."""
        if not self._vote_active:
            return
        if not sender or sender == 'Server':
            return
        if sender in self._votes:
            return  # already voted
        if opt < 1 or opt > len(self._map_choices):
            return
        self._votes[sender] = opt
        self.log(f"Vote recorded: {sender} -> {opt}")

    def on_chat(self, chat_messages):
        if not self._vote_active:
            return

        if not isinstance(chat_messages, list):
            return

        for chat_msg in chat_messages:
            try:
                sender = chat_msg.get('name', '')
                text = chat_msg.get('text', '').strip()

                if sender == 'Server' or not sender:
                    continue

                if text in ['!1', '!2', '!3', '!4', '!5']:
                    if sender not in self._votes:
                        opt = int(text[1:])
                        if opt <= len(self._map_choices):
                            self._votes[sender] = opt
                            self.log(f"Vote recorded: {sender} -> {opt}")
            except Exception:
                continue


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, dest):
        super().__init__()
        self.url = url
        self.dest = dest

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(self.url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response, open(self.dest, 'wb') as out_file:
                total_size = int(response.getheader('Content-Length', 0))
                downloaded = 0
                chunk_size = 8192
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = int((downloaded / total_size) * 100)
                        self.progress.emit(percent)
            self.finished.emit(self.dest)
        except Exception as e:
            self.error.emit(str(e))

class WeaponsTab(QWidget):
    """Weapons Availability Matrix Tab"""

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
        self._weapon_rows = {}
        self._loading = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        weapon_group = QGroupBox("Weapons Availability")
        weapon_layout = QVBoxLayout()

        override_row = QHBoxLayout()
        override_row.addWidget(QLabel("Set All:"))

        all_yes_btn = SatisfyingButton("Yes")
        all_yes_btn.setToolTip("Set all weapons to Yes (available)")
        all_yes_btn.clicked.connect(lambda: self._set_all_weapons("0"))
        override_row.addWidget(all_yes_btn)

        all_armory_btn = SatisfyingButton("Armory")
        all_armory_btn.setToolTip("Set all weapons to Armory (spawn with armory)")
        all_armory_btn.clicked.connect(lambda: self._set_all_weapons("1"))
        override_row.addWidget(all_armory_btn)

        all_no_btn = SatisfyingButton("No")
        all_no_btn.setToolTip("Set all weapons to No (disabled)")
        all_no_btn.clicked.connect(lambda: self._set_all_weapons("2"))
        override_row.addWidget(all_no_btn)

        override_row.addStretch()
        weapon_layout.addLayout(override_row)

        update_list_btn = SatisfyingButton("Update List from Server")
        update_list_btn.setToolTip("Refresh weapons list from server")
        update_list_btn.clicked.connect(lambda: self.server.send("get settings"))
        weapon_layout.addWidget(update_list_btn)

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

        self.weapon_table = QTableWidget()
        self.weapon_table.setColumnCount(4)
        self.weapon_table.setHorizontalHeaderLabels(["Weapon", "Y", "A", "N"])
        self.weapon_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.weapon_table.setColumnWidth(1, 60)
        self.weapon_table.setColumnWidth(2, 60)
        self.weapon_table.setColumnWidth(3, 60)
        self.weapon_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.weapon_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._populate_weapon_table()
        weapon_layout.addWidget(self.weapon_table)

        weapon_group.setLayout(weapon_layout)
        layout.addWidget(weapon_group)

    def _populate_weapon_table(self):
        self.weapon_table.setRowCount(0)
        for i, weapon in enumerate(self.WEAPON_LIST):
            self.weapon_table.insertRow(i)
            self._weapon_rows[weapon.lower()] = i

            w_item = QTableWidgetItem(weapon)
            self.weapon_table.setItem(i, 0, w_item)

            bg_group = QButtonGroup(self)
            bg_group.setExclusive(True)

            for col in range(1, 4):
                cb = QRadioButton()
                cb.setStyleSheet("margin-left: 10px;")
                if col == 1:
                    cb.setChecked(True)
                bg_group.addButton(cb, col)

                widget = QWidget()
                l = QHBoxLayout(widget)
                l.addWidget(cb)
                l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                l.setContentsMargins(0, 0, 0, 0)

                self.weapon_table.setCellWidget(i, col, widget)

                cb.toggled.connect(lambda checked, r=i, c=col: self._on_weapon_toggled(checked, r, c))

    def _on_weapon_toggled(self, checked, row, col):
        if self._loading or not checked: return
        weapon = self.weapon_table.item(row, 0).text()
        val = str(col - 1)
        self.server.set_setting(f"weapon_{weapon}", val)

    def _set_all_weapons(self, value):
        labels = {"0": "Yes", "1": "Armory", "2": "No"}
        for row in range(self.weapon_table.rowCount()):
            self._loading = True
            for col in range(1, 4):
                w = self.weapon_table.cellWidget(row, col)
                if w:
                    rb = w.layout().itemAt(0).widget()
                    rb.setChecked(col - 1 == int(value))
            self._loading = False
            weapon = self.weapon_table.item(row, 0).text()
            self.server.set_setting(f"weapon_{weapon}", value)

    def update_settings(self, settings: dict):
        for key, val in settings.items():
            if key.lower().startswith("weapon_"):
                weapon_name = key[7:]
                if weapon_name in self._weapon_rows:
                    row = self._weapon_rows[weapon_name]
                    try:
                        col = int(val) + 1
                        if 1 <= col <= 3:
                            w = self.weapon_table.cellWidget(row, col)
                            if w:
                                rb = w.layout().itemAt(0).widget()
                                self._loading = True
                                rb.setChecked(True)
                                self._loading = False
                    except ValueError:
                        pass


class WebAdminTab(QWidget):
    """Web Admin settings tab - configure the embedded web server for mobile access."""

    def __init__(self, web_server):
        super().__init__()
        self.ws = web_server
        self._config_path = self._get_config_path()
        self._config = self._load_config()
        self._build_ui()
        self._apply_config()

    def _get_config_path(self):
        base = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        return os.path.join(base, 'wolfrat_web.json')

    def _load_config(self):
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_config(self):
        try:
            cfg = {
                'web_enabled': self.enable_cb.isChecked(),
                'web_port': self.port_spin.value(),
                'web_username': self.user_input.text().strip() or 'admin',
                'web_token': self._config.get('web_token', ''),
                'login_ips': self._config.get('login_ips', {}),
            }
            with open(self._config_path, 'w') as f:
                json.dump(cfg, f, indent=2)
            self._config = cfg
        except Exception:
            pass

    def _apply_config(self):
        """Apply loaded config to UI widgets and start server if enabled."""
        self._loading = True
        self.enable_cb.setChecked(self._config.get('web_enabled', False))
        self.port_spin.setValue(self._config.get('web_port', 8070))
        self.user_input.setText(self._config.get('web_username', 'admin'))
        token = self._config.get('web_token', '')
        if not token:
            token = generate_token()
            self._config['web_token'] = token
            self._save_config()
        self.token_display.setText(token)
        self._loading = False
        # Apply auth to web server
        self.ws.set_auth(self._config.get('web_username', 'admin'), token)
        # Load persisted login IPs
        saved_ips = self._config.get('login_ips', {})
        self.ws.load_login_ips(saved_ips)
        # Set callback to persist new logins
        self.ws.on_login = self._on_web_login
        # Start if enabled
        if self._config.get('web_enabled', False):
            self.ws.port = self._config.get('web_port', 8070)
            self.ws.start()
            self._update_status()
        # Update status bar LED
        parent = self.window()
        if hasattr(parent, 'update_web_led'):
            parent.update_web_led(self.ws.is_running)

    def _on_web_login(self, ip, timestamp):
        """Called by web server when a new login happens. Persists to config."""
        self._config['login_ips'] = self.ws.get_login_ips_dict()
        try:
            with open(self._config_path, 'w') as f:
                json.dump(self._config, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        # Title
        title = QLabel("Web Admin")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #e8c840;")
        layout.addWidget(title)

        desc = QLabel("Enable a mobile-friendly web interface for remote server administration.")
        desc.setStyleSheet("color: #888; font-size: 10pt;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Warning
        warning = QLabel("⚠️ OFF by default. When enabled, the web server listens on your network. Ensure your firewall is configured.")
        warning.setStyleSheet("color: #ff8040; font-size: 10pt; padding: 8px; background: #1a1000; border-radius: 6px;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # Enable toggle
        self.enable_cb = QCheckBox("Enable Web Admin")
        self.enable_cb.setStyleSheet("font-size: 12pt; font-weight: bold; color: #e8c840;")
        self.enable_cb.stateChanged.connect(self._on_toggle)
        layout.addWidget(self.enable_cb)

        # Status
        status_group = QGroupBox("Status")
        status_layout = QGridLayout()
        self.status_label = QLabel("Stopped")
        self.status_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #ff4040;")
        status_layout.addWidget(QLabel("Server:"), 0, 0)
        status_layout.addWidget(self.status_label, 0, 1)
        self.url_label = QLabel("-")
        self.url_label.setStyleSheet("color: #888; font-size: 10pt;")
        status_layout.addWidget(QLabel("URL:"), 1, 0)
        status_layout.addWidget(self.url_label, 1, 1)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # Settings
        settings_group = QGroupBox("Settings")
        settings_layout = QGridLayout()

        settings_layout.addWidget(QLabel("Port:"), 0, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(8070)
        self.port_spin.wheelEvent = lambda event: None  # Disable mouse wheel
        self.port_spin.valueChanged.connect(self._on_port_change)
        settings_layout.addWidget(self.port_spin, 0, 1)

        settings_layout.addWidget(QLabel("Username:"), 1, 0)
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("admin")
        self.user_input.textChanged.connect(self._on_user_change)
        settings_layout.addWidget(self.user_input, 1, 1)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # Access Token
        token_group = QGroupBox("Access Token")
        token_layout = QVBoxLayout()

        token_desc = QLabel("Share this username and token with admins who need mobile access.")
        token_desc.setStyleSheet("color: #888; font-size: 9pt;")
        token_desc.setWordWrap(True)
        token_layout.addWidget(token_desc)

        self.token_display = QLineEdit()
        self.token_display.setReadOnly(True)
        self.token_display.setStyleSheet("font-family: monospace; font-size: 11pt; color: #e8c840; background: #0a0a00; padding: 8px;")
        self.token_display.setPlaceholderText("Click Generate to create a token")
        token_layout.addWidget(self.token_display)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy Token")
        copy_btn.clicked.connect(self._copy_token)
        btn_row.addWidget(copy_btn)
        regen_btn = QPushButton("Regenerate Token")
        regen_btn.setStyleSheet("color: #ff8040;")
        regen_btn.clicked.connect(self._regen_token)
        btn_row.addWidget(regen_btn)
        token_layout.addLayout(btn_row)

        token_group.setLayout(token_layout)
        layout.addWidget(token_group)

        # Login IP History
        ip_group = QGroupBox("Recent Logins")
        ip_layout = QVBoxLayout()
        self.ip_list = QLabel("No logins yet")
        self.ip_list.setStyleSheet("color: #888; font-size: 10pt; padding: 4px;")
        self.ip_list.setWordWrap(True)
        self.ip_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        ip_layout.addWidget(self.ip_list)
        refresh_ip_btn = QPushButton("Refresh")
        refresh_ip_btn.setFixedWidth(80)
        refresh_ip_btn.clicked.connect(self._refresh_ip_list)
        ip_layout.addWidget(refresh_ip_btn)
        ip_group.setLayout(ip_layout)
        layout.addWidget(ip_group)

        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _on_toggle(self, state):
        if self._loading:
            return
        enabled = state == 2  # Qt.CheckState.Checked
        if enabled:
            self.ws.port = self.port_spin.value()
            self.ws.start()
        else:
            self.ws.stop()
        self._update_status()
        self._save_config()

    def _on_port_change(self, port):
        if self._loading:
            return
        self._save_config()
        if self.ws.is_running:
            self.ws.port = port
            self.ws.restart()
            self._update_status()

    def _on_user_change(self, text):
        if self._loading:
            return
        self.ws.set_auth(text.strip() or 'admin', self.token_display.text())
        self._save_config()

    def _copy_token(self):
        token = self.token_display.text()
        if token:
            QApplication.clipboard().setText(token)
            self._show_msg("Token copied to clipboard!")

    def _regen_token(self):
        reply = QMessageBox.question(
            self, "Regenerate Token",
            "This will invalidate the current token. All users will need the new token. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            token = generate_token()
            self.token_display.setText(token)
            self._config['web_token'] = token
            self.ws.set_auth(self.user_input.text().strip() or 'admin', token)
            self._save_config()
            self._show_msg("New token generated!")

    def _update_status(self):
        if self.ws.is_running:
            self.status_label.setText("Running")
            self.status_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #50ff50;")
            self.url_label.setText(f"http://localhost:{self.port_spin.value()}")
        else:
            self.status_label.setText("Stopped")
            self.status_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #ff4040;")
            self.url_label.setText("-")
        # Update status bar LED
        parent = self.window()
        if hasattr(parent, 'update_web_led'):
            parent.update_web_led(self.ws.is_running)
        # Refresh IP list
        self._refresh_ip_list()

    def _show_msg(self, msg):
        """Temporary feedback via parent status bar if available."""
        parent = self.window()
        if hasattr(parent, 'show_feedback'):
            parent.show_feedback(msg)

    def _refresh_ip_list(self):
        """Refresh the login IP history display."""
        ips = self.ws.get_login_ips()
        if not ips:
            self.ip_list.setText("No logins yet")
            return
        lines = []
        for entry in ips:
            ip = entry['ip']
            ts = entry['last_login']
            time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
            ago = int(time.time() - ts)
            if ago < 60:
                ago_str = f"{ago}s ago"
            elif ago < 3600:
                ago_str = f"{ago // 60}m ago"
            elif ago < 86400:
                ago_str = f"{ago // 3600}h ago"
            else:
                ago_str = f"{ago // 86400}d ago"
            lines.append(f"● {ip}  -  {time_str} ({ago_str})")
        self.ip_list.setText("\n".join(lines))


class MainWindow(QMainWindow):
    """WolfRAT 2.4.9 Main Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WolfRAT 2.4.9 - Joint Operations Server Admin")

        # Set Window Icon
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setMinimumSize(960, 650)
        self.resize(1000, 720)

        # Server manager
        self.server = ServerManager()
        self.server._app = self  # Back-reference for store access
        self.signals = LogSignals()
        self.missions_store = MissionsStore()
        self.stats_store = StatsStore()

        # Web server for mobile access (v2.4.9) - disabled by default
        self.web_server = WolfWebServer(self.server)
        # Don't start here - WebAdminTab controls start/stop

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
            on_connect_done=lambda: (self.chatbot_tab.reset_chat(), self.mods_tab.on_connect()),
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
        self.signals.players_signal.connect(self.spree_tab.check_sprees)
        self.signals.chat_signal.connect(self.chatbot_tab.update_chat)
        self.signals.chat_signal.connect(lambda c: self.web_server.broadcast_chat())
        self.signals.chat_signal.connect(self.mods_tab.update_chat)
        self.signals.gamestate_signal.connect(self.server_tab.update_gamestate)
        self.signals.gamestate_signal.connect(lambda g: self.web_server.broadcast_state())
        self.signals.missions_signal.connect(self.missions_tab.update_missions)
        self.signals.missions_signal.connect(self.missions_store.update_rotation)
        self.signals.missions_signal.connect(lambda m: self.mods_tab._update_maps_info())
        self.signals.available_maps_signal.connect(self.missions_tab.update_available_maps)
        self.signals.available_maps_signal.connect(self.missions_store.update_available)
        self.signals.gamestate_signal.connect(self._update_status_bar)
        self.signals.settings_signal.connect(self.settings_tab.update_settings)
        self.signals.settings_signal.connect(self.weapons_tab.update_settings)
        self.signals.settings_signal.connect(self.server_tab.update_settings)
        # Link settings tab to map voting tab for GameTime sync
        self.settings_tab._map_voting_tab = self.map_voting_tab
        self.signals.settings_signal.connect(lambda s: self._update_title(s.get('servername', '')))
        self.signals.settings_signal.connect(lambda s: print(f"[WolfRAT] settings_signal received: {len(s)} keys"))
        self.signals.settings_signal.connect(lambda s: self.flash_sync_led())
        self.signals.settings_signal.connect(lambda s: self.web_server.broadcast_state())
        self.signals.gamestate_signal.connect(lambda s: self.flash_sync_led())
        self.signals.players_signal.connect(lambda s: self.flash_sync_led())
        self.signals.connected_signal.connect(lambda: self.set_connected(True))
        self.signals.connected_signal.connect(lambda: self.web_server.broadcast_state())
        self.signals.connected_signal.connect(lambda: sounds.play("connect"))
        self.signals.disconnected_signal.connect(lambda: self.set_connected(False, 'Disconnected'))
        self.signals.disconnected_signal.connect(lambda: self.setWindowTitle("WolfRAT 2.4.9 - Joint Operations Server Admin"))
        self.signals.disconnected_signal.connect(lambda: self.web_server.broadcast_state())
        self.signals.disconnected_signal.connect(lambda: self.server_tab.handle_disconnect_ui())
        self.signals.reconnecting_signal.connect(lambda attempt: self.set_connected(False, f'Reconnecting (Attempt {attempt})...'))
        self.signals.disconnected_signal.connect(lambda: sounds.play("disconnect"))

        # Status bar

    def _update_title(self, server_name=""):
        """Update window title with server name when connected."""
        if server_name:
            self.setWindowTitle(f"WolfRAT 2.4.9 \u2014 {server_name}")
        else:
            self.setWindowTitle("WolfRAT 2.4.9 - Joint Operations Server Admin")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Header
        header = QLabel("WolfRAT 2.4.9")
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
        self.missions_tab = MissionsTab(self.server, self.missions_store)
        self.settings_tab = SettingsTab(self.server)
        self.weapons_tab = WeaponsTab(self.server)
        self.chatbot_tab = ChatBotTab(self.server)
        self.messages_tab = MessagesTab(self.server, self.stats_store)
        self.spree_tab = SpreeTab(self.server, self.messages_tab)
        self.mods_tab = ModsTab(self.server, self.missions_store)
        self.map_voting_tab = MapVotingTab(self.server, self.missions_tab)
        self.web_admin_tab = WebAdminTab(self.web_server)

        # Wire up cross-tab references
        self.server_tab.missions_tab = self.missions_tab
        self.missions_tab._main_window = self
        self.missions_store._missions_tab = self.missions_tab
        self.settings_tab.mods_tab = self.mods_tab
        self.mods_tab.messages_tab = self.messages_tab
        self.settings_tab.load_vote_settings()
        self.settings_tab.load_skip_settings()

        # Connect MapVoting signals
        self.signals.missions_signal.connect(self.map_voting_tab.on_missions_updated)
        self.signals.missions_signal.connect(self.spree_tab.on_missions_updated)
        self.signals.chat_signal.connect(self.map_voting_tab.on_chat)

        self.tabs.addTab(self.server_tab, "🖥️ Server")
        self.tabs.addTab(self.console_tab, "👥 Console")
        self.tabs.addTab(self.players_tab, "👥 Players")
        self.tabs.addTab(self.missions_tab, "🗺️ Missions")
        self.tabs.addTab(self.settings_tab, "⚙️ Settings")
        self.tabs.addTab(self.weapons_tab, "🔫 Weapons")
        self.tabs.addTab(self.chatbot_tab, "💬 Chat Bot")
        self.tabs.addTab(self.messages_tab, "📢 Messages")
        self.tabs.addTab(self.spree_tab, "🔥 Sprees")
        self.tabs.addTab(self.mods_tab, "🛡️ Mods")
        self.tabs.addTab(self.map_voting_tab, "🌐 Map Voting")
        self.tabs.addTab(self.web_admin_tab, "🌐 Web Admin")

        layout.addWidget(self.tabs)

        # Load available maps from persistent store (avoids server polling conflicts)
        self.missions_tab.load_available_from_store()

        # --- Status bar ---
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(4, 2, 4, 2)
        status_bar.setSpacing(0)

        self.status_connected_label = QLabel(" ● Disconnected ")
        self.status_connected_label.setStyleSheet("font-size: 9pt; color: #ff6040; padding: 2px 8px;")
        status_bar.addWidget(self.status_connected_label)

        sep1 = QLabel("│")
        sep1.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep1)

        self.status_mode_label = QLabel(" Game: - ")
        self.status_mode_label.setStyleSheet("font-size: 9pt; color: #a89830; padding: 2px 8px;")
        status_bar.addWidget(self.status_mode_label)

        sep2 = QLabel("│")
        sep2.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep2)

        self.status_map_label = QLabel(" Map: - ")
        self.status_map_label.setStyleSheet("font-size: 10pt; color: #ffff00; font-weight: bold; padding: 2px 8px;")
        status_bar.addWidget(self.status_map_label)

        sep3 = QLabel("│")
        sep3.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep3)

        self.status_players_label = QLabel(" Players: 0 ")
        self.status_players_label.setStyleSheet("font-size: 9pt; color: #a89830; padding: 2px 8px;")
        status_bar.addWidget(self.status_players_label)

        sep4 = QLabel("│")
        sep4.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep4)

        self.feedback_label = QLabel("")
        self.feedback_label.setStyleSheet("font-size: 9pt; color: #50ff50; padding: 2px 8px;")
        status_bar.addWidget(self.feedback_label, 1)  # stretch

        self.sync_led_label = QLabel("●")
        self.sync_led_label.setStyleSheet("font-size: 10pt; color: #444444; padding: 2px 4px;")
        self.sync_led_label.setToolTip("Server Sync Activity")
        status_bar.addWidget(self.sync_led_label)

        sep5 = QLabel("│")
        sep5.setStyleSheet("color: #333; padding: 0 4px;")
        status_bar.addWidget(sep5)

        self.web_led_label = QLabel(" ● Web: Off ")
        self.web_led_label.setStyleSheet("font-size: 9pt; color: #555; padding: 2px 8px;")
        self.web_led_label.setToolTip("Web Admin Server Status")
        status_bar.addWidget(self.web_led_label)

        self.mute_cb = QCheckBox(" Mute")
        self.mute_cb.setStyleSheet("font-size: 9pt; color: #6a6a20;")
        self.mute_cb.stateChanged.connect(lambda s: sounds.set_muted(s == 2))
        status_bar.addWidget(self.mute_cb)

        status_bar.addSpacing(10)

        ver_label = QLabel("v2.4.9 · Built by BadgerLove · FMJ Squad")
        ver_label.setStyleSheet("font-size: 9pt; color: #444;")
        status_bar.addWidget(ver_label)

        status_bar.addSpacing(10)

        update_btn = QPushButton("Check for Updates")
        update_btn.setStyleSheet("font-size: 8pt; padding: 2px 8px; background: #222; border: 1px solid #444; border-radius: 3px;")
        update_btn.clicked.connect(self._check_for_updates)
        status_bar.addWidget(update_btn)

        status_widget = QWidget()
        status_widget.setLayout(status_bar)
        status_widget.setStyleSheet("background-color: #0a0a00; border-top: 1px solid #1a1a00;")
        layout.addWidget(status_widget)

        # Sync web LED with actual server state (WebAdminTab may have started server before status bar existed)
        QTimer.singleShot(100, lambda: self.update_web_led(self.web_server.is_running))

        # Auto-connect to last server on launch
        QTimer.singleShot(500, self._auto_connect_last)

    def set_connected(self, connected, text="Connected"):
        if connected:
            self.status_connected_label.setText(" ● Connected ")
            self.status_connected_label.setStyleSheet("font-size: 9pt; color: #50ff50; padding: 2px 8px;")
        else:
            self.status_connected_label.setText(" ● Disconnected ")
            self.status_connected_label.setStyleSheet("font-size: 9pt; color: #ff6040; padding: 2px 8px;")

    def _auto_connect_last(self):
        """On startup, load last server and auto-connect."""
        if self.server_tab._load_last_server():
            self.server_tab.log("Auto-connecting to last server...")
            self.server_tab._do_connect()

    def show_feedback(self, msg):
        """Show temporary feedback message in status bar."""
        self.feedback_label.setText(msg)
        QTimer.singleShot(3000, lambda: self.feedback_label.setText(""))

    def flash_sync_led(self):
        """Flash the sync LED green to indicate server data reception."""
        if hasattr(self, 'sync_led_label'):
            self.sync_led_label.setStyleSheet("font-size: 10pt; color: #50ff50; padding: 2px 4px;")
            QTimer.singleShot(300, lambda: self.sync_led_label.setStyleSheet("font-size: 10pt; color: #444444; padding: 2px 4px;"))

    def update_web_led(self, running):
        """Update the web server status LED in the status bar."""
        if hasattr(self, 'web_led_label'):
            if running:
                self.web_led_label.setText(" ● Web: On ")
                self.web_led_label.setStyleSheet("font-size: 9pt; color: #50ff50; padding: 2px 8px;")
            else:
                self.web_led_label.setText(" ● Web: Off ")
                self.web_led_label.setStyleSheet("font-size: 9pt; color: #555; padding: 2px 8px;")

    def update_status_map(self, text):
        self.status_map_label.setText(text)

    def _update_status_bar(self, state):
        mode = state.get("mode", "")
        self.status_mode_label.setText(f" Game: {mode} " if mode else " Game: - ")

        # When map changes, update the status bar directly from the mission cycle parser
        # The true name is pushed by MissionsTab, but we can safely fallback to state if needed.

    def _check_for_updates(self):
        """Check fmj-squad.com/version.json for updates."""
        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                "http://fmj-squad.com/version.json",
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())

            wolfrat_data = data.get("wolfrat", {})
            latest_version = wolfrat_data.get("version", "2.4.9")
            current = "2.4.9"

            if latest_version != current:
                # Custom dialog with scrollable changelog
                dlg = QDialog(self)
                dlg.setWindowTitle("Update Available")
                dlg.setMinimumSize(450, 350)
                dlg.setMaximumSize(600, 500)
                dlg.resize(500, 400)
                layout = QVBoxLayout(dlg)

                header = QLabel(f"A new version of WolfRAT is available!")
                header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #e8c840;")
                layout.addWidget(header)

                versions = QLabel(f"Current: v{current}    →    Latest: v{latest_version}")
                versions.setStyleSheet("color: #a89830; font-size: 10pt;")
                layout.addWidget(versions)

                changelog = QTextEdit()
                changelog.setReadOnly(True)
                changelog.setPlainText(wolfrat_data.get('notes', 'No changelog available.'))
                changelog.setStyleSheet("font-family: 'Segoe UI', Arial; font-size: 9pt; color: #e8c840; background-color: #050500; border: 1px solid #1a1a00; padding: 6px;")
                layout.addWidget(changelog, 1)

                question = QLabel("Would you like to download and install it now?")
                question.setStyleSheet("color: #e8c840; font-size: 10pt; padding-top: 6px;")
                layout.addWidget(question)

                btn_row = QHBoxLayout()
                btn_row.addStretch()
                yes_btn = QPushButton("Yes")
                yes_btn.setMinimumWidth(80)
                yes_btn.clicked.connect(dlg.accept)
                btn_row.addWidget(yes_btn)
                no_btn = QPushButton("No")
                no_btn.setMinimumWidth(80)
                no_btn.clicked.connect(dlg.reject)
                btn_row.addWidget(no_btn)
                layout.addLayout(btn_row)

                if dlg.exec() == QDialog.DialogCode.Accepted:
                    self._start_update_download(wolfrat_data.get("exe_url", "http://fmj-squad.com/downloads/WolfRAT2.exe"))
            else:
                QMessageBox.information(self, "Up to Date", "You are running the latest version of WolfRAT.")

        except Exception as e:
            QMessageBox.warning(self, "Update Check Failed", f"Could not check for updates.\n\nError: {e}")

    def _start_update_download(self, url):
        if not url:
            QMessageBox.warning(self, "Error", "No download URL provided in the update config.")
            return

        self.progress_dialog = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Updating WolfRAT")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setAutoClose(True)
        self.progress_dialog.show()

        import os, sys, tempfile
        base_dir = os.path.dirname(sys.argv[0]) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
        dest_path = os.path.join(tempfile.gettempdir(), "WolfRAT2_update.exe")

        self.dl_worker = DownloadWorker(url, dest_path)
        self.dl_worker.progress.connect(self.progress_dialog.setValue)
        self.dl_worker.finished.connect(self._on_download_finished)
        self.dl_worker.error.connect(self._on_download_error)
        self.progress_dialog.canceled.connect(self.dl_worker.terminate)
        self.dl_worker.start()

    def _on_download_error(self, err):
        self.progress_dialog.close()
        QMessageBox.warning(self, "Download Failed", f"Failed to download update:\n\n{err}")

    def _on_download_finished(self, dest_path):
        self.progress_dialog.close()
        import os, sys, subprocess, tempfile, shutil

        if not getattr(sys, 'frozen', False):
            QMessageBox.information(self, "Update", "Update downloaded! (Running from source, skipping update).")
            return

        current_exe = os.path.abspath(sys.executable)
        pid = os.getpid()

        # Extract bundled updater.exe to temp dir
        updater_dest = os.path.join(tempfile.gettempdir(), "fmj_updater.exe")

        bundled_updater = os.path.join(sys._MEIPASS, 'updater.exe')
        if not os.path.exists(bundled_updater):
            bundled_updater = os.path.join(os.path.dirname(current_exe), 'updater.exe')
        if os.path.exists(bundled_updater):
            shutil.copy2(bundled_updater, updater_dest)
        else:
            QMessageBox.critical(self, "Update Error", "Could not find updater component. Please download the update manually from fmj-squad.com")
            return

        # Launch updater: updater.exe <old_exe> <new_exe> <pid>
        subprocess.Popen(
            [updater_dest, current_exe, dest_path, str(pid)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )

        # Exit the application
        QApplication.quit()

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
        wire_log("=== WolfRAT 2.4.9 STARTED ===")
    except Exception:
        pass

    # B-Stats: anonymous usage analytics
    try:
        import bstats
        bstats.bstats_start("wolfrat", "2.4.9")
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
    app.setApplicationName("WolfRAT 2.4.9")

    window = MainWindow()
    _set_dark_title_bar(window)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
