"""'Start counting again' on the Lag compensation page (2026-10-19): Dale found
it did nothing he could see - with the helper not loaded it returned silently,
and on an empty server every number was already 0.  Now the button is only
live while the helper is in the server, and a click always says what happened."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import lag_comp as lc  # noqa: E402
from wolfrat import lag_comp_panel as panel_mod  # noqa: E402


def _panel(qtbot, tmp_path, monkeypatch, state):
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: state)
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json",
                               tmp_path, log=lambda text: None)
    qtbot.addWidget(p)
    p._pid = 1234
    return p


def _loaded(alive):
    return lc.State(lc.ON, base=0x10000000, shared=0x10001000, ready=True,
                    settings=lc.Settings(False, 0, 300, 80),
                    stats=lc.Stats(seconds=600), alive=alive)


def test_button_is_off_until_the_helper_is_in_the_server(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    assert not p.reset_btn.isEnabled()
    p._refresh_stats(force=True)
    assert not p.reset_btn.isEnabled()
    assert "switch" in p.reset_btn.toolTip().lower()


def test_click_says_counting_started_again(qtbot, tmp_path, monkeypatch):
    state = _loaded(alive=True)
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert p.reset_btn.isEnabled()
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    p._reset_clicked()
    assert "Counting started again at" in p.totals_lbl.text()
    assert "Nobody is on yet" not in p.totals_lbl.text()
    assert p.reset_btn.text().startswith("✔")


def test_click_on_an_empty_server_says_when_it_counts(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _loaded(alive=False))
    p._refresh_stats(force=True)
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    p._reset_clicked()
    assert "Counting started again at" in p.totals_lbl.text()
    assert "Nobody is on yet" in p.totals_lbl.text()


def test_helper_gone_says_so(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _loaded(alive=True))
    p._refresh_stats(force=True)
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: False)
    p._reset_clicked()
    assert "Switch it on first" in p.totals_lbl.text()
    assert not p.reset_btn.isEnabled()


def test_write_refused_is_shown(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _loaded(alive=True))
    p._refresh_stats(force=True)

    def refuse(pid, name=None):
        raise lc.LagCompError("Windows would not let WolfRAT open the server process.")
    monkeypatch.setattr(lc, "reset_stats", refuse)
    p._reset_clicked()
    assert "Could not start counting again" in p.totals_lbl.text()


# ------------------------------------------- the box stays ticked (2026-10-29)
# 24 Oct: a temp clean-up had deleted the helper out of the running WolfRAT's own
# temp folder; at the next server restart switching it back on failed, and the
# failure unticked the box and saved it unticked.  The server ran four days
# without lag compensation.

class _Mem:
    pid = 5460


def _saved(tmp_path):
    import json
    return json.loads((tmp_path / "lc.json").read_text(encoding="utf-8"))


def test_a_failed_switch_on_after_a_restart_keeps_the_box_ticked_and_retries(
        qtbot, tmp_path, monkeypatch):
    import json
    (tmp_path / "lc.json").write_text(json.dumps({"on": True, "reapply": True}), encoding="utf-8")
    state = {"now": lc.State(lc.STOCK)}
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: state["now"])
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    clock = [1000.0]
    monkeypatch.setattr(panel_mod.time, "monotonic", lambda: clock[0])
    calls = []

    def turn_on(pid, memory, data_dir, settings):
        calls.append(pid)
        if len(calls) < 3:
            raise lc.LagCompError("WolfRAT's lag compensation helper is missing from this build.")
        state["now"] = _loaded(alive=True)
        return state["now"]
    monkeypatch.setattr(lc, "turn_on", turn_on)
    logs = []
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json",
                               tmp_path, log=logs.append)
    qtbot.addWidget(p)

    p.server_tick(_Mem(), writable=True)                     # a new server: put it back
    assert calls == [5460]
    assert p.on_cb.isChecked() and _saved(tmp_path)["on"] is True
    assert "NOT running" in p.state_lbl.text() and "stays ticked" in p.state_lbl.text()
    assert sum("missing" in line for line in logs) == 1

    p.server_tick(_Mem(), writable=True)                     # too soon: no retry yet
    assert calls == [5460]
    clock[0] += panel_mod.RETRY_SECONDS
    p.server_tick(_Mem(), writable=True)                     # fails again: not logged twice
    assert len(calls) == 2 and sum("missing" in line for line in logs) == 1
    assert p.on_cb.isChecked() and _saved(tmp_path)["on"] is True

    clock[0] += panel_mod.RETRY_SECONDS
    p.server_tick(_Mem(), writable=True)                     # works
    assert len(calls) == 3 and "NOT running" not in p.state_lbl.text()
    assert p.on_cb.isChecked() and not p.wants_write()


def test_being_shot_at_tables(qtbot, tmp_path, monkeypatch):
    """Helper version 2: the target-ping bands and per-target rows (2026-10-31,
    players said high-ping players felt harder to hit)."""
    state = _loaded(alive=True)
    s = state.stats
    s.has_targets = True
    s.bands = [lc.BandStats(label) for label in lc.BAND_LABELS]
    s.bands[2].players, s.bands[2].close_low, s.bands[2].hits_low = 2, 400, 100
    s.bands[2].cal_median = 72
    s.targets = [lc.TargetStats(4, "Far", 210, 200, 50, 10, 4, 10, 3, 2, 1)]
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert p.bands_table.rowCount() == 3
    assert p.bands_table.item(2, 0).text() == "150+"
    assert p.bands_table.item(2, 3).text() == "25%"
    assert p.bands_table.item(2, 9).text() == "+72 ms"
    assert p.bands_table.item(0, 3).text() == "-"            # nothing counted
    assert p.targets_table.item(0, 0).text() == "Far"
    assert p.targets_table.item(0, 5).text() == "40%"


def test_older_helper_says_tick_again(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _loaded(alive=True))
    p._refresh_stats(force=True)
    assert p.bands_table.rowCount() == 0
    assert "older helper" in p.targets_note.text()


def test_autotune_status_and_live_log_on_panel(qtbot, tmp_path, monkeypatch):
    state = _loaded(alive=True)
    state.settings = lc.Settings(False, 40, 400, 80, lc.TUNE_APPLY)
    s = state.stats
    s.tests, s.hits, s.plain_hits = 500, 120, 100
    s.has_targets = True
    s.bands = [lc.BandStats(label) for label in lc.BAND_LABELS]
    s.has_tuner = True
    s.tune_mode = lc.TUNE_APPLY
    s.tune_value_ms = 65
    s.tune_median_ms = 68
    s.tune_samples = 1140
    s.tune_window_mins = 24
    s.tune_decisions = 5
    s.tune_log = [lc.TuneStep(5, 10000, 63, 65, 68, 1140, 24)]
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert "+65 ms" in p.autotune_lbl.text()
    assert "rolling best" in p.autotune_lbl.text()
    assert "Auto-tune min #5" in p.event_log_edit.toPlainText()


def test_one_time_auto_enable_switches_everyone_on_once_then_respects_user_choice(
        qtbot, tmp_path, monkeypatch):
    import json
    cfg = tmp_path / "lc.json"
    # Existing install that had lag comp off and old +40 default, before migration:
    cfg.write_text(json.dumps({"on": False, "reapply": False, "extra_ms": 40, "tune_mode": 0}),
                   encoding="utf-8")
    p1 = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    assert p1.on_cb.isChecked() and p1.reapply_cb.isChecked()
    assert p1._settings.tune_mode == lc.TUNE_APPLY and p1._settings.extra_ms == 70
    saved1 = _saved(tmp_path)
    assert saved1["on"] is True and saved1["auto_enabled_once"] is True

    # User now unticks On: next launch must respect their choice and keep it off!
    p1.on_cb.setChecked(False)
    assert _saved(tmp_path)["on"] is False
    p2 = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    assert not p2.on_cb.isChecked()


def test_server_tick_records_history_and_events_even_when_tab_is_hidden(
        qtbot, tmp_path, monkeypatch):
    state = _loaded(alive=True)
    state.settings = lc.Settings(False, 70, 400, 80, lc.TUNE_APPLY)
    s = state.stats
    s.tests, s.hits, s.plain_hits = 500, 120, 100
    s.has_targets = True
    s.bands = [lc.BandStats(label) for label in lc.BAND_LABELS]
    s.targets = [lc.TargetStats(2, "Leaver", 150, 20, 5, 10, 4, 10, 3, 1, 0)]
    s.has_tuner = True
    s.tune_mode = lc.TUNE_APPLY
    s.tune_value_ms = 70
    s.tune_log = [lc.TuneStep(1, 50000, 70, 70, None, 120, 1)]
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p.hide()  # tab is NOT visible
    assert not p.isVisible()
    p.server_tick(_Mem(), writable=False)
    assert (tmp_path / "lc_history.json").exists()
    assert (tmp_path / "wolfrat_lag_comp_events.log").exists()
    assert "Auto-tune min #1" in (tmp_path / "wolfrat_lag_comp_events.log").read_text(encoding="utf-8")


# ------------------------------------------------- helper version 4 (2026-11-20)
def _v4_state(what_if=None, dead_hits=0, min_ping=80):
    state = _loaded(alive=True)
    state.settings = lc.Settings(False, 70, 400, min_ping)
    s = state.stats
    s.tests, s.hits, s.plain_hits, s.gained, s.lost = 5000, 1200, 1000, 300, 100
    s.has_targets = True
    s.bands = [lc.BandStats(label) for label in lc.BAND_LABELS]
    s.has_v4 = True
    s.bot_totals = lc.BandStats(lc.BOTS_LABEL, players=40, close_lag=300, hits_lag=120,
                                close_lag_plain=300, plain_lag=90, gained=40, lost=10)
    s.dead_hits = dead_hits
    s.what_if_offsets = list(lc.WHATIF_OFFSETS)
    labels = lc.what_if_band_labels(lc.WHATIF_BAND_MIN_PING)
    s.what_if = [lc.WhatIfRow(label, low, 0, [0] * 8, [0] * 8)
                 for label, low in zip(labels, lc.WHATIF_BAND_MIN_PING)]
    for band, (steps, hits) in (what_if or {}).items():
        s.what_if[band].steps, s.what_if[band].hits = steps, list(hits)
    return state


def _header(table):
    return [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]


def test_version_4_totals_say_live_players_and_bots_and_show_dead_bodies(
        qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v4_state(dead_hits=1234))
    p._refresh_stats(force=True)
    text = p.totals_lbl.text()
    assert "<b>1,200</b> hits on live players and bots counted with it" in text
    assert "Dead bodies hit: 1,234 (not counted)" in text
    q = _panel(qtbot, tmp_path, monkeypatch, _v4_state(dead_hits=0))
    q._refresh_stats(force=True)
    assert "Dead bodies" not in q.totals_lbl.text()


def test_version_4_being_shot_at_has_a_bots_row(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v4_state())
    p._refresh_stats(force=True)
    assert p.bands_table.rowCount() == 4
    row = [p.bands_table.item(3, c).text() for c in range(p.bands_table.columnCount())]
    assert row == ["Bots (AI)", "40", "0", "-", "300", "40%", "30%", "40", "10", "-"]
    assert p.bands_table.minimumHeight() == 134                # room for the fourth row


def test_what_if_table_bold_best_and_most_hits_overall(qtbot, tmp_path, monkeypatch):
    state = _v4_state({0: (5000, [900, 0, 0, 0, 0, 0, 0, 0]),
                       2: (200, [40, 30, 35, 50, 70, 60, 45, 20]),
                       4: (100, [20, 10, 15, 25, 30, 41, 22, 10])})
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert not p.whatif_group.isHidden() and p.whatif_group.title() == "What if - hits at other rewinds"
    t = p.whatif_table
    assert _header(t) == ["Shooter ping", "Plain JO", "ping -80", "ping -40", "ping +0",
                          "ping +40", "ping +80", "ping +120", "ping +160", "Bullets"]
    assert t.rowCount() == 3                                   # only bands with bullets
    assert [t.item(r, 0).text() for r in range(3)] == ["under 50", "80-99", "150-199"]
    assert [t.item(1, c).text() for c in range(1, 10)] == [
        "40", "30", "35", "50", "70", "60", "45", "20", "200"]
    bold = [[c for c in range(t.columnCount()) if t.item(r, c).font().bold()] for r in range(3)]
    assert bold == [[1], [5], [6]]                             # each row's best rewind
    # 80 and over only (the minimum ping): ping +80 has 60 + 41, ping +40 has 70 + 30.
    assert p.whatif_best_lbl.text() == "Most hits overall: <b>ping +80</b>"


def test_what_if_says_not_enough_yet_under_300_bullets(qtbot, tmp_path, monkeypatch):
    state = _v4_state({2: (299, [40, 30, 35, 50, 70, 60, 45, 20])})
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert p.whatif_best_lbl.text() == "Most hits overall: not enough yet (299 of 300 bullets)"
    assert p.whatif_table.rowCount() == 1


def test_an_older_helper_shows_no_what_if_no_bots_row_and_the_old_totals(
        qtbot, tmp_path, monkeypatch):
    state = _v4_state({2: (500, [40, 30, 35, 50, 70, 60, 45, 20])}, dead_hits=9)
    state.stats.has_v4 = False                                 # a version 3 helper
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    assert p.whatif_group.isHidden()
    assert p.bands_table.rowCount() == 3 and p.bands_table.minimumHeight() == 110
    assert "<b>1,200</b> hits counted with it" in p.totals_lbl.text()
    assert "Dead bodies" not in p.totals_lbl.text()


def test_page_with_the_what_if_box_fits_1024x768(qtbot, tmp_path, monkeypatch):
    """Same rule as the other Server tab pages (test_server_patcher): a sub-page
    gets about 1004 x 540 at 1024x768 and scrolls rather than squashing a box."""
    from PyQt6.QtWidgets import QApplication, QGroupBox, QScrollArea, QTabWidget
    state = _v4_state({i: (400 + i, [40, 30, 35, 50, 70, 60, 45, 20]) for i in range(6)},
                      dead_hits=12)
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    tabs.addTab(p, "Lag compensation")
    tabs.resize(1004, 540)
    tabs.show()
    p._refresh_stats(force=True)
    for _ in range(20):
        QApplication.processEvents()
    assert p.whatif_group.isVisible() and p.whatif_table.rowCount() == 6
    columns = [a for a in p.findChildren(QScrollArea) if a.widgetResizable()]
    assert len(columns) == 2                                   # still two scrolling columns
    for group in p.findChildren(QGroupBox):
        assert group.height() >= group.minimumSizeHint().height(), group.title()
    # The new box asks for no more width than the widest box already in its
    # column, so it cannot make the column any wider than it was.
    others = [g.minimumSizeHint().width() for g in p.findChildren(QGroupBox)
              if g is not p.whatif_group and g.parent() is p.whatif_group.parent()]
    assert len(others) == 3 and p.whatif_group.minimumSizeHint().width() <= max(others)
    assert p.whatif_table.height() >= (p.whatif_table.horizontalHeader().height()
                                       + 6 * 24)               # all six rows show
    tabs.hide()


# ------------------------- another WolfRAT version's helper is on (review B3)
def _other_version_on():
    return lc.State(lc.OLD, base=0x20000000, shared=0x20008000, ready=True,
                    settings=lc.Settings(False, 55, 400, 80),
                    stats=lc.Stats(seconds=600, tests=900, hits=200, plain_hits=150), alive=True)


def _switchable(qtbot, tmp_path, monkeypatch, saved, state, elsewhere=False):
    """A panel over a fake server whose state the test can change; records the
    turn_on / turn_off calls."""
    import json
    (tmp_path / "lc.json").write_text(json.dumps(dict(saved, auto_enabled_once=True)),
                                      encoding="utf-8")
    now = {"state": state, "elsewhere": elsewhere}
    calls, logs = [], []
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: now["state"])
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    monkeypatch.setattr(lc, "managed_elsewhere", lambda st, **kw: now["elsewhere"],
                        raising=False)

    def turn_on(pid, memory, data_dir, settings):
        calls.append("on")
        now["state"] = _loaded(alive=True)
        return now["state"]

    def turn_off(pid, memory):
        calls.append("off")
        now["state"] = lc.State(lc.STOCK)
        return now["state"]
    monkeypatch.setattr(lc, "turn_on", turn_on)
    monkeypatch.setattr(lc, "turn_off", turn_off)
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json",
                               tmp_path, log=logs.append)
    qtbot.addWidget(p)
    return p, now, calls, logs


def test_other_versions_helper_shows_as_on_and_the_page_shows_its_numbers(
        qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": False},
                                      _other_version_on())
    p.server_tick(_Mem(), writable=True)
    assert calls == []                                         # unticked: left as it is
    assert p.state_lbl.text() == ("\U0001F7E1 On, from another WolfRAT version. Tick the box "
                                  "to run it from this one (the numbers start again).")
    p._refresh_stats(force=True)
    assert "<b>200</b> hits" in p.totals_lbl.text()            # that helper's live numbers
    assert p.reset_btn.isEnabled()


def test_unticking_switches_another_versions_helper_off(qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": True}, _loaded(alive=True))
    p.server_tick(_Mem(), writable=True)                       # ours is on
    now["state"] = _other_version_on()                         # another build took the calls
    p.server_tick(_Mem(), writable=True)
    assert calls == []                                         # same server: no tug of war
    assert p.state_lbl.text() == ("\U0001F7E1 On, from another WolfRAT version. Untick the "
                                  "box to switch it off.")
    p.on_cb.setChecked(False)
    p.server_tick(_Mem(), writable=True)
    assert calls == ["off"]
    assert "Off - normal JO hits" in p.state_lbl.text()
    assert any("OFF" in line for line in logs)


def test_after_a_wolfrat_update_the_ticked_box_carries_on_with_this_version(
        qtbot, tmp_path, monkeypatch):
    """WolfRAT restarted (an update), the server did not: the old version's
    helper is still on.  Before, with 'put it back' unticked, the page unticked
    itself and said the server had restarted and it was off - while it was on."""
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": False}, _other_version_on())
    p.server_tick(_Mem(), writable=True)
    assert calls == ["on"] and p.on_cb.isChecked() and _saved(tmp_path)["on"] is True
    assert any("moving it to this one" in line for line in logs)
    assert not any("restarted and it is off" in line for line in logs)
    assert p.state_lbl.text().startswith("\U0001F7E2 ON")


# ------------------------- two WolfRATs on one server (review P1, 2026-11-20)
class _Writes(_Mem):
    """A server handle that records what is written through it."""

    def __init__(self):
        self.writes = []

    def write(self, address, data):
        self.writes.append((address, bytes(data)))


def test_it_never_takes_over_by_itself_from_a_wolfrat_that_is_running_it(
        qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": True}, _other_version_on(),
                                      elsewhere=True)
    for _ in range(3):                                         # a new server, then two polls
        p.server_tick(_Mem(), writable=True)
    assert calls == []                                         # left alone
    assert p.on_cb.isChecked() and _saved(tmp_path)["on"] is True      # never unticked
    assert p.state_lbl.text() == (
        "\U0001F7E1 On, run by another WolfRAT that is open now, so this one leaves it alone. "
        "Untick the box to switch it off, then tick it to run it from here.")
    assert sum("leaves it alone" in line for line in logs) == 1        # said once
    assert not any("moving it to this one" in line for line in logs)

    now["elsewhere"] = False                                   # that WolfRAT has closed
    p.server_tick(_Mem(), writable=True)
    assert calls == ["on"] and p.state_lbl.text().startswith("\U0001F7E2 ON")


def test_ticking_the_box_takes_over_even_from_a_running_wolfrat(qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": False},
                                      _other_version_on(), elsewhere=True)
    p.server_tick(_Mem(), writable=True)
    assert calls == []
    assert p.state_lbl.text() == (
        "\U0001F7E1 On, run by another WolfRAT that is open now, so this one leaves it alone. "
        "Tick the box to run it from here.")
    p.on_cb.setChecked(True)                                   # a deliberate click
    p.server_tick(_Mem(), writable=True)
    assert calls == ["on"] and p.state_lbl.text().startswith("\U0001F7E2 ON")
    # Afterwards it is back to never taking over by itself.
    now["state"], now["elsewhere"] = _other_version_on(), True
    p._dirty = True                                            # e.g. a setting changed
    p.server_tick(_Mem(), writable=True)
    assert calls == ["on"]


def test_unticking_still_switches_a_running_wolfrats_helper_off(qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": True}, _other_version_on(),
                                      elsewhere=True)
    p.server_tick(_Mem(), writable=True)
    p.on_cb.setChecked(False)
    p.server_tick(_Mem(), writable=True)
    assert calls == ["off"]
    now["elsewhere"] = False
    p.on_cb.setChecked(True)                                   # and ticking runs it from here
    p.server_tick(_Mem(), writable=True)
    assert calls == ["off", "on"]


def test_the_wolfrat_running_it_says_so_on_every_poll_with_write_access(
        qtbot, tmp_path, monkeypatch):
    state = _loaded(alive=True)
    state.has_v4 = True
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": True}, state)
    monkeypatch.setattr(lc, "_tick_count", lambda: 987654)
    mem = _Writes()
    p.server_tick(mem, writable=True)
    p.server_tick(mem, writable=True)
    import struct
    stamp = (state.shared + lc._OFF["owner_pid"], struct.pack("<II", os.getpid(), 987654))
    assert mem.writes == [stamp, stamp]                        # one 8-byte write a poll
    p.server_tick(mem, writable=False)                         # read-only handle: nothing
    assert len(mem.writes) == 2
    state.has_v4 = False                                       # an older helper: no room for it
    p.server_tick(mem, writable=True)
    assert len(mem.writes) == 2
    state.has_v4 = True
    p.on_cb.setChecked(False)                                  # not this WolfRAT's to run
    now["state"] = state
    monkeypatch.setattr(lc, "turn_off", lambda pid, memory: state)
    p.server_tick(mem, writable=True)
    assert len(mem.writes) == 2


# ------------------------- a switch that did not finish (review P2)
def _half_switched():
    return lc.State(lc.PARTIAL, base=0x10000000, shared=0x10001000, ready=True,
                    settings=lc.Settings(), stats=lc.Stats(seconds=5), alive=True)


def test_a_half_done_switch_is_finished_when_the_box_is_ticked(qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch,
                                      {"on": True, "reapply": True}, _loaded(alive=True))
    p.server_tick(_Mem(), writable=True)
    now["state"] = _half_switched()
    assert p.wants_write() is False
    p.server_tick(_Mem(), writable=False)                      # no write access yet
    assert calls == [] and p.wants_write() is True             # asks for it
    p.server_tick(_Mem(), writable=True)
    assert calls == ["on"] and p.state_lbl.text().startswith("\U0001F7E2 ON")
    assert sum("did not finish" in line for line in logs) == 1


def test_a_half_done_switch_is_undone_when_the_box_is_unticked(qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": False},
                                      _half_switched())
    p.server_tick(_Mem(), writable=True)
    assert calls == ["off"] and "Off - normal JO hits" in p.state_lbl.text()
    assert not p.on_cb.isChecked()


def test_a_half_done_switch_that_cannot_be_repaired_says_so_and_is_not_hammered(
        qtbot, tmp_path, monkeypatch):
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": False},
                                      _half_switched())
    clock = [1000.0]
    monkeypatch.setattr(panel_mod.time, "monotonic", lambda: clock[0])
    tries = []

    def turn_off(pid, memory):
        tries.append(1)
        raise lc.LagCompError("Could not write the server's code. The change is half done; "
                              "WolfRAT finishes it on the next try.")
    monkeypatch.setattr(lc, "turn_off", turn_off)
    p.server_tick(_Mem(), writable=True)
    assert len(tries) == 1 and "half done" in p.state_lbl.text()
    p.server_tick(_Mem(), writable=True)                       # 5 s later: not again yet
    assert len(tries) == 1
    p._failed_text = ""
    p._show_state(now["state"])
    assert p.state_lbl.text() == ("⚠ Half switched: a change to the game's hit code did "
                                  "not finish. WolfRAT is putting it right.")
    clock[0] += panel_mod.RETRY_SECONDS
    p.server_tick(_Mem(), writable=True)
    assert len(tries) == 2
    assert sum("did not finish" in line for line in logs) == 1 and not p.on_cb.isChecked()
