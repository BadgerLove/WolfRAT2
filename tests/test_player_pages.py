"""Players tab pages (Dale 2026-11-16): JustAPlayer on a small screen saw the player
list squashed to one row once the two stats boxes sat under it (2.10.18/2.10.19).
The tab now has three pages: Players (list, Admin Actions), Team Balance, and Stats
(Restore disconnected stats, Stats XP and ranks).  At 1024x768 the main window
leaves the tab about 560 px; nothing on any page may be squashed.  Measured with
WolfRAT's own stylesheet: the old single page gave the list 1 row, two pages
(balance still under the list) 4 rows."""

import os
import sys

import pytest

keep_alive = []


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    keep_alive.append(app)
    return app


class FakeServer:
    def _log(self, _text):
        pass

    def refresh_players(self):
        pass


def settle(app):
    for _ in range(20):
        app.processEvents()


@pytest.fixture
def players_tab(qapp, tmp_path):
    from wolfrat.app import DARK_STYLE, PlayersTab
    from wolfrat.auto_balance_panel import AutoBalancePanel
    from wolfrat.career_panel import CareerPanel
    from wolfrat.keep_stats_panel import KeepStatsPanel
    tab = PlayersTab(FakeServer())
    # sizes as the real window has them; on the tab only (app-wide would restyle
    # every window the other tests left open, and time out)
    tab.setStyleSheet(DARK_STYLE)
    # the main window's wiring (MainWindow: auto balance row, then the two stats boxes)
    balance = AutoBalancePanel(str(tmp_path), say=lambda _t: None, move=lambda _p, _t: None,
                               set_jo_balance=lambda _on: None)
    tab.balance_layout.addWidget(balance)
    tab.stats_layout.addWidget(KeepStatsPanel(str(tmp_path)))
    career = CareerPanel(str(tmp_path), log=lambda _t: None, announce=lambda _t: None,
                         chat_limit=lambda: 59, colour=lambda: False)
    tab.stats_layout.addWidget(career)
    tab.stats_layout.addStretch(1)
    yield tab
    tab.hide()
    career.shutdown()


def test_the_players_tab_has_players_team_balance_and_stats_pages(players_tab):
    from PyQt6.QtWidgets import QGroupBox
    pages = players_tab.pages
    assert [pages.tabText(i) for i in range(pages.count())] == ["Players", "Team Balance", "Stats"]
    titles = [[g.title() for g in pages.widget(i).findChildren(QGroupBox)] for i in range(pages.count())]
    assert titles[0] == ["Admin Actions"]
    assert titles[1] == ["Team Balance"]
    assert "Restore disconnected stats" in titles[2] and "Stats, XP and ranks" in titles[2]
    assert players_tab.balance_label in pages.widget(1).findChildren(type(players_tab.balance_label))
    assert players_tab.table in pages.widget(0).findChildren(type(players_tab.table))


def test_both_pages_fit_1024x768_and_the_list_keeps_its_rows(qapp, players_tab):
    from PyQt6.QtWidgets import QGroupBox
    players_tab.resize(1004, 560)
    players_tab.show()
    for page in range(players_tab.pages.count()):
        players_tab.pages.setCurrentIndex(page)
        settle(qapp)
        for group in players_tab.pages.widget(page).findChildren(QGroupBox):
            assert group.height() >= group.minimumSizeHint().height(), (page, group.title(), group.height())
    players_tab.pages.setCurrentIndex(0)
    settle(qapp)
    row = players_tab.table.verticalHeader().defaultSectionSize()
    rows = players_tab.table.viewport().height() // row
    assert rows >= 8, rows      # 2.10.19 at this size: one row
