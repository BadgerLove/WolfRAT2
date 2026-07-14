import re

FILE_PATH = r"C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py"

with open(FILE_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Define WeaponsTab
weapons_class = """class WeaponsTab(QWidget):
    \"\"\"Weapons Availability Matrix Tab\"\"\"

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
            "⚠️ Warning: Changing weapon/armoury settings live can crash your server.\\n"
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
"""

# Insert WeaponsTab before MainWindow
if "class WeaponsTab(QWidget):" not in content:
    content = content.replace("class MainWindow(QMainWindow):", weapons_class + "\n\nclass MainWindow(QMainWindow):")

# Remove Right Column from SettingsTab
def remove_between(text, start_marker, end_marker):
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    return pattern.sub("", text)

start = "        # ============================================================\n        # RIGHT COLUMN"
end = "        # ============================================================\n        # FAR RIGHT"
content = remove_between(content, start, end)

# Change main_layout.addLayout(right_col, 2) inside SettingsTab
content = re.sub(r'main_layout\.addLayout\(right_col.*?\)[\r\n]*', '', content)

# Modify SettingsTab.__init__ to remove WEAPON_LIST and weapon logic
content = re.sub(r'    WEAPON_LIST = \[.*?\]\n\n', '', content, flags=re.DOTALL)
content = content.replace("self._weapon_rows = {}", "")

# Remove weapon methods from SettingsTab
def remove_method(text, method_start, next_method):
    idx1 = text.find(method_start)
    if idx1 == -1: return text
    idx2 = text.find(next_method, idx1 + 10)
    if idx2 == -1: return text
    return text[:idx1] + text[idx2:]

content = remove_method(content, "    def _populate_weapon_table(self):", "    def _on_slider(self, key, val):")
content = remove_method(content, "    def _on_weapon_toggled(self, checked, row, col):", "    def _on_slider(self, key, val):")
content = remove_method(content, "    def _set_all_weapons(self, value):", "    def _on_slider(self, key, val):")
content = remove_method(content, "    def _update_weapon_from_settings(self, settings):", "    def _show_feedback(self, msg):")

# Remove self._update_weapon_from_settings(settings) from update_settings
content = content.replace("self._update_weapon_from_settings(settings)", "")

# Add WeaponsTab to MainWindow
if "self.weapons_tab = WeaponsTab(self.server)" not in content:
    content = content.replace(
        "self.settings_tab = SettingsTab(self.server)",
        "self.settings_tab = SettingsTab(self.server)\n        self.weapons_tab = WeaponsTab(self.server)"
    )
    content = content.replace(
        "self.tabs.addTab(self.settings_tab, \"⚙ Settings\")",
        "self.tabs.addTab(self.settings_tab, \"⚙ Settings\")\n        self.tabs.addTab(self.weapons_tab, \"🔫 Weapons\")"
    )
    content = content.replace(
        "self.signals.settings_signal.connect(self.settings_tab.update_settings)",
        "self.signals.settings_signal.connect(self.settings_tab.update_settings)\n        self.signals.settings_signal.connect(self.weapons_tab.update_settings)"
    )

with open(FILE_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch 2 (Weapons split) applied.")
