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
        self.rotation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rotation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.rotation_table.horizontalHeader().resizeSection(1, 30)
        self.rotation_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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

        self.status_mode = QLabel(" Game: - ")
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
                        display_name = filename
                        for ext in ('.bms', '.npj', '.npaj'):
                            if display_name.lower().endswith(ext):
                                display_name = display_name[:-len(ext)]
                                break
                else:
                    display_name = display_name.split('<')[0].replace('(2x)', '').strip()

                row_idx = len(self._rotation_maps)
                self._rotation_maps.append(filename)
                
                is_current = '<CURRENT MISSION>' in m or '<NEXT MISSION>' in m
                play_count = 2 if '(2x)' in m else 1
                self._add_rotation_row(filename, play_count, is_current, display_name=display_name)

                # Track which row to restore
                if prev_name and filename == prev_name:
                    restored = row_idx

                # Update bottom status bar with current map
                if is_current:
                    self.status_info.setText(f" Map: {display_name} ")
                    # Push current map name to main status bar
                    if hasattr(self, '_main_window') and self._main_window:
                        self._main_window.status_map_label.setText(f"Map: {display_name}")

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
        for raw in self.server.missions:
            # Extract server index from start of string
            parts = raw.split(' - ')
            name_part = parts[0].strip()
            idx_str = ""
            if ':' in name_part[:5]:
                idx_str = name_part.split(':', 1)[0].strip()
            
            # Extract filename from end of string
            server_filename = name_part
            if len(parts) > 1:
                server_filename = parts[1].strip()
                server_filename = server_filename.split('<')[0].strip()
                if server_filename.endswith('(2x)'):
                    server_filename = server_filename.replace('(2x)', '').strip()
                    
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
            self._add_rotation_row(filename, count, False)

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
            f"Add '{name}' maps to current rotation?\n\n(Existing maps will be kept. Remove them manually after.)",
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


