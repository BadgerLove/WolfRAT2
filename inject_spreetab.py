import sys

FILE_PATH = r"C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py"

spree_class = """class SpreeTab(QWidget):
    \"\"\"Killing Spree Announcer Tab\"\"\"

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

            if kills < prev['kills']:
                for stat in self._player_stats.values():
                    stat['streak'] = 0

            if deaths > prev['deaths']:
                prev['streak'] = 0
            elif kills > prev['kills']:
                gained = kills - prev['kills']
                old_streak = prev['streak']
                new_streak = old_streak + gained
                prev['streak'] = new_streak

                if self._spree_enabled:
                    thresholds = sorted(self._spree_thresholds.keys(), reverse=True)
                    for t in thresholds:
                        if old_streak < t <= new_streak:
                            template = self._spree_thresholds[t]
                            import time
                            msg = template.replace('{player}', name)
                            self.server.send_chat(msg)
                            self.log_text.append(f"[{time.strftime('%H:%M:%S')}] SPREE: {msg}")
                            break

            prev['kills'] = kills
            prev['deaths'] = deaths

            if kd_enabled and getattr(self.messages_tab, 'stats_store', None):
                self.messages_tab.stats_store.update_player(name, kills, deaths, prev.get('streak', 0))

"""

with open(FILE_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

if "class SpreeTab(QWidget):" not in content:
    content = content.replace("class MissionsStore:", spree_class + "\n\nclass MissionsStore:")
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Injected SpreeTab.")
else:
    print("SpreeTab already exists.")
