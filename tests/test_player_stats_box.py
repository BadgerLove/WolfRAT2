"""Player Stats (Dale, 2026-11-21): the "Player KD Tracking" box moved from
under the Messages pages to the top of Sprees > Killing sprees, renamed, with
a line on what it needs."""

import json

from PyQt6.QtWidgets import QGroupBox, QLabel, QScrollArea

from wolfrat.app import MessagesTab, SpreeTab
from wolfrat.runtime import DesktopRuntime


class Server:
    players = []

    def _log(self, _msg):
        pass

    def send_chat(self, _msg):
        pass

    def announce(self, _msg):
        pass

    def refresh_chat(self):
        pass


class Stats:
    def get_player_count(self):
        return 3

    def get_player(self, _name):
        return None


def build(qtbot, tmp_path):
    runtime = DesktopRuntime.isolated(tmp_path)
    messages = MessagesTab(Server(), Stats(), runtime)
    qtbot.addWidget(messages)
    sprees = SpreeTab(Server(), messages, runtime)
    qtbot.addWidget(sprees)
    return messages, sprees, runtime


def test_player_stats_is_the_first_box_on_the_killing_sprees_page(qtbot, tmp_path):
    _messages, sprees, _runtime = build(qtbot, tmp_path)
    assert sprees.pages.tabText(0) == "Killing sprees"
    page = sprees.pages.widget(0)
    assert isinstance(page, QScrollArea)            # scrolls on a 720-high screen
    titles = [box.title() for box in page.widget().findChildren(QGroupBox)]
    assert titles[:2] == ["Player Stats", "Killing Spree Announcer"]
    assert sprees.stats_checkbox.text() == "Enable stats tracking"
    about = " ".join(label.text() for label in
                     sprees.stats_checkbox.parentWidget().findChildren(QLabel))
    assert "!kd" in about and "works from any PC" in about
    assert "Tracked: 3 players" in sprees.stats_hint_label.text()


def test_the_messages_tab_has_no_stats_box_any_more(qtbot, tmp_path):
    messages, _sprees, _runtime = build(qtbot, tmp_path)
    titles = [box.title() for box in messages.findChildren(QGroupBox)]
    assert not [title for title in titles if "KD" in title or "Stats" in title]
    assert "Message Log" in titles                  # the log stays (Dale)


def test_the_box_switches_tracking_and_keeps_the_saved_choice(qtbot, tmp_path):
    messages, sprees, runtime = build(qtbot, tmp_path)
    assert messages._kd_enabled is True and sprees.stats_checkbox.isChecked()
    sprees.stats_checkbox.setChecked(False)
    assert messages._kd_enabled is False
    with open(messages._config_path(), encoding="utf-8") as f:
        assert json.load(f)["kd_enabled"] is False  # same key as before: nothing lost
    again_messages = MessagesTab(Server(), Stats(), runtime)
    qtbot.addWidget(again_messages)
    again = SpreeTab(Server(), again_messages, runtime)
    qtbot.addWidget(again)
    assert not again.stats_checkbox.isChecked()
