"""The "Wildlife" page of the Weather tab: hunting sharks for the TAC mod."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from wolfrat import wildlife


def scroll_column() -> tuple:
    """Same shape as weather_tab.scroll_column (kept here: the tab imports this page)."""
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 6, 0)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(host)
    return area, layout

BANNER = (
    "<b>TAC mod only.</b> The sharks were made by <b>OscarMike247</b> for the TAC mod "
    "(Revx02). On any other mod there are no sharks and this page does nothing."
)

_HELP = (
    "<b>What this does.</b> The TAC mod's sharks are placed as a neutral team with a "
    "100 m attack distance, so they have nobody to hunt and, when poked, lunge from far "
    "away and miss. Tick <b>Hunting sharks</b> and WolfRAT makes every shark on the server "
    "<b>berserk</b> (attacks anyone, the same as a map's Berserk tick) with a short attack "
    "distance, so they circle, charge and bite swimmers. They keep their own team, so "
    "players who join later see them swim normally. Nobody downloads anything; it is a "
    "change inside the running server."
    "<br><br><b>Needs:</b> WolfRAT linked to the game server on this PC (Server tab). Over a "
    "remote connection the page stays greyed out."
    "<br><br><b>Good to know</b>"
    "<br>• A map load and every shark respawn put the map's own numbers back. WolfRAT "
    "re-applies yours within a few seconds for as long as the switch is on; the line at the "
    "top counts how often it had to."
    "<br>• <b>Attack distance 10 m</b> is the tested sweet spot: the bite lands a few metres "
    "into the lunge. At 25 m sharks lunge and miss; at 4-6 m you have to be right on top "
    "of them."
    "<br>• <b>Chasing swimmers</b> also needs the shark fix in the server: jo-server-patches "
    "patch 16, or onHook with the water-creature chase. With it, sharks turn and come for "
    "you in deep water and never follow you onto land; without it they patrol and only act "
    "once you are inside the attack distance. WolfRAT also gives them a 4 m 'start "
    "chasing' distance where the map left none, which the game needs before any chase."
    "<br>• <b>Sharks ignore each other.</b> A berserk creature normally attacks its own "
    "kind too, so before the first shark goes berserk WolfRAT adds a small fix to the "
    "running server: berserk sharks hunt players, never each other. It stays until the "
    "server restarts (a map load keeps it). If the server already has it (onHook's "
    "BerserkIgnoresOwnKind, or the same exe patch) WolfRAT leaves it alone; on a server "
    "exe it does not recognise, the sharks are not set to hunt at all."
    "<br>• Untick the box and the sharks keep hunting until the next map load; WolfRAT "
    "does not put them back."
)


class WildlifePage(QWidget):
    changed = pyqtSignal()

    def __init__(self, config: dict | None = None):
        super().__init__()
        config = config or {}
        self._loading = True
        self._build(bool(config.get("enabled", False)),
                    int(config.get("attack_m", wildlife.DEFAULT_ATTACK_M)))
        self._loading = False

    # ------------------------------------------------------------------ UI
    def _build(self, enabled: bool, attack_m: int):
        root = QHBoxLayout(self)
        left_scroll, left = scroll_column()
        right_scroll, right = scroll_column()
        root.addWidget(left_scroll, 3)
        root.addWidget(right_scroll, 2)

        self.banner_lbl = QLabel(BANNER)
        self.banner_lbl.setWordWrap(True)
        self.banner_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.banner_lbl.setStyleSheet(
            "font-size: 10pt; color: #ffd700; background: #1a1a00; border: 1px solid #4a4a10; "
            "border-radius: 4px; padding: 6px;"
        )
        left.addWidget(self.banner_lbl)

        self.status_lbl = QLabel("Looking for sharks...")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px;")
        left.addWidget(self.status_lbl)

        group = QGroupBox("🦈 Sharks")
        box = QVBoxLayout()
        self.enabled_cb = QCheckBox("Hunting sharks - berserk (attack anyone), short attack distance")
        self.enabled_cb.setChecked(enabled)
        box.addWidget(self.enabled_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Attack distance:"))
        self.attack_slider = QSlider(Qt.Orientation.Horizontal)
        self.attack_slider.setRange(wildlife.MIN_ATTACK_M, wildlife.MAX_ATTACK_M)
        self.attack_slider.setValue(max(wildlife.MIN_ATTACK_M, min(wildlife.MAX_ATTACK_M, attack_m)))
        self.attack_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.attack_slider.setTickInterval(1)
        row.addWidget(self.attack_slider, 1)
        self.attack_lbl = QLabel()
        self.attack_lbl.setMinimumWidth(150)
        row.addWidget(self.attack_lbl)
        box.addLayout(row)

        hint = QLabel("10 m tested: circles, charges, bites.  25 m: lunges and misses.  "
                      "4-6 m: only bites if you are on top of it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 9pt; color: #a89830;")
        box.addWidget(hint)
        group.setLayout(box)
        left.addWidget(group)
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

        self._action_widgets = (self.enabled_cb, self.attack_slider)
        self._attack_text(self.attack_slider.value())
        self.attack_slider.valueChanged.connect(self._attack_text)
        self.enabled_cb.toggled.connect(self._emit)
        self.attack_slider.valueChanged.connect(self._emit)

    def _attack_text(self, value: int):
        word = "tested" if value == wildlife.DEFAULT_ATTACK_M else (
            "misses" if value >= 20 else ("very close" if value <= 6 else ""))
        self.attack_lbl.setText(f"{value} m" + (f"  ({word})" if word else ""))

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    # ------------------------------------------------------------- state
    def config(self) -> dict:
        return {"enabled": self.enabled_cb.isChecked(), "attack_m": self.attack_slider.value()}

    def enabled(self) -> bool:
        return self.enabled_cb.isChecked()

    def attack_m(self) -> int:
        return self.attack_slider.value()

    def set_available(self, available: bool):
        for widget in self._action_widgets:
            widget.setEnabled(available)

    def set_status(self, text: str):
        self.status_lbl.setText(text)
