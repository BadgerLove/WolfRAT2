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
    assert p1._settings.tune_mode == lc.TUNE_WATCH and p1._settings.extra_ms == 70
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


# ------------------------------------------------- helper version 5 (2026-11-22)
# Claymore shrapnel is never rewound (a claymore fires by itself: plain JO
# decides) and is counted apart, so it gets a line of its own under the totals.
CLAYMORE_LINE = ("Claymore shrapnel: 12,345 checks, 67 hits - plain JO, never rewound "
                 "(a claymore fires by itself)")
RULE_OFF_LINE = "On this server exe claymores cannot be told apart and are still rewound."


def _v5_state(device_steps=0, device_hits=0, device_rule=1):
    state = _v4_state()
    s = state.stats
    s.has_v5 = True
    s.device_steps, s.device_hits, s.device_rule = device_steps, device_hits, device_rule
    return state


def test_claymore_shrapnel_has_its_own_line_under_the_totals(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v5_state(device_steps=12345, device_hits=67))
    p._refresh_stats(force=True)
    text = p.totals_lbl.text()
    assert text.endswith("<br><span style='font-size:9pt'>" + CLAYMORE_LINE + "</span>")
    assert "cannot be told apart" not in text
    # The numbers above it are as they were: the pellets are not in them.
    assert "<b>1,200</b> hits on live players and bots counted with it" in text
    assert "5,000 bullet checks" in text
    # No claymore has gone off yet: no line.
    q = _panel(qtbot, tmp_path, monkeypatch, _v5_state())
    q._refresh_stats(force=True)
    assert "laymore" not in q.totals_lbl.text()


def test_the_claymore_line_shows_when_only_claymores_have_fired(qtbot, tmp_path, monkeypatch):
    """Their checks are no longer bullet checks from rewound players, so the
    totals can read 'none yet' while claymores have gone off."""
    state = _v5_state(device_steps=288, device_hits=2)
    state.stats.tests = state.stats.hits = state.stats.plain_hits = 0
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    text = p.totals_lbl.text()
    assert "No bullet checks from rewound players yet" in text
    assert ("Claymore shrapnel: 288 checks, 2 hits - plain JO, never rewound "
            "(a claymore fires by itself)") in text


def test_an_exe_where_claymores_cannot_be_told_apart_says_so(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v5_state(device_rule=0))
    p._refresh_stats(force=True)
    text = p.totals_lbl.text()
    assert text.endswith("<br><span style='font-size:9pt'>" + RULE_OFF_LINE + "</span>")
    assert "Claymore shrapnel:" not in text
    # Not said of an exe the helper does not fit at all: nothing is rewound there.
    unfit = _v5_state(device_rule=0)
    unfit.ready = False
    q = _panel(qtbot, tmp_path, monkeypatch, unfit)
    q._refresh_stats(force=True)
    assert "laymore" not in q.totals_lbl.text()


def test_an_older_helper_shows_no_claymore_lines(qtbot, tmp_path, monkeypatch):
    state = _v5_state(device_steps=288, device_hits=2, device_rule=0)
    state.stats.has_v5 = False                                 # a version 4 helper
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    p._refresh_stats(force=True)
    text = p.totals_lbl.text()
    assert "laymore" not in text
    assert text.endswith("5,000 bullet checks · server tick 16.0 ms</span>")   # as before


def test_the_help_says_a_claymore_is_never_rewound():
    assert ("<br>• Rewind anyone for a claymore: a claymore fires by itself, so plain JO "
            "decides its hits.") in panel_mod._HELP
    # The "Being shot at" tables do not hold the pellets either.
    assert "Dead bodies and claymore shrapnel don't count." in panel_mod.TARGETS_HELP


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


# ------------------------- quick first retries (2026-11-20)
# Twice a server restart left lag compensation off for about 150 s: the server
# was not ready for a few seconds ("Could not find kernel32 in the server",
# "cannot find its settings block") and every retry was 30 s apart.

class _Mem2:
    pid = 6001


def _failing_panel(qtbot, tmp_path, monkeypatch, poll_now=None):
    import json
    (tmp_path / "lc.json").write_text(json.dumps(
        {"on": True, "reapply": True, "auto_enabled_once": True}), encoding="utf-8")
    now = {"state": lc.State(lc.STOCK), "works": False}
    clock, calls, logs = [1000.0], [], []
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: now["state"])
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    monkeypatch.setattr(panel_mod.time, "monotonic", lambda: clock[0])

    def turn_on(pid, memory, data_dir, settings):
        calls.append(clock[0])
        if not now["works"]:
            raise lc.LagCompError("Could not find kernel32 in the server.")
        now["state"] = _loaded(alive=True)
        return now["state"]
    monkeypatch.setattr(lc, "turn_on", turn_on)
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json",
                               tmp_path, log=logs.append, poll_now=poll_now)
    qtbot.addWidget(p)
    return p, now, clock, calls, logs


def test_first_retries_are_5_s_apart_for_a_minute_then_30_s(qtbot, tmp_path, monkeypatch):
    p, now, clock, calls, logs = _failing_panel(qtbot, tmp_path, monkeypatch)
    p.server_tick(_Mem(), writable=True)                       # new server: first try fails
    assert calls == [1000.0]
    assert "tries again every 5 s" in p.state_lbl.text() and "NOT running" in p.state_lbl.text()
    clock[0] = 1004.9
    p.server_tick(_Mem(), writable=True)                       # not yet
    assert len(calls) == 1
    for second in range(1005, 1061, 5):                        # every 5 s through the first minute
        clock[0] = float(second)
        p.server_tick(_Mem(), writable=True)
    assert calls == [float(t) for t in range(1000, 1061, 5)]   # 13 tries in the first 60 s
    assert "tries again every 30 s" in p.state_lbl.text()      # from now on, as before
    clock[0] = 1065.0
    p.server_tick(_Mem(), writable=True)
    clock[0] = 1089.9
    p.server_tick(_Mem(), writable=True)
    assert len(calls) == 13
    clock[0] = 1090.0
    p.server_tick(_Mem(), writable=True)
    assert len(calls) == 14
    # Never unticked, and the reason is logged once with both speeds.
    assert p.on_cb.isChecked() and _saved(tmp_path)["on"] is True
    told = [line for line in logs if "kernel32" in line]
    assert told == ["Lag compensation: Could not find kernel32 in the server. Still ticked - "
                    "WolfRAT tries again every 5 s for a minute, then every 30 s."]


def test_the_quick_tries_start_again_after_it_worked_or_on_a_new_server(
        qtbot, tmp_path, monkeypatch):
    p, now, clock, calls, logs = _failing_panel(qtbot, tmp_path, monkeypatch)
    p.server_tick(_Mem(), writable=True)
    clock[0] = 1200.0                                          # failing for over a minute
    p.server_tick(_Mem(), writable=True)
    assert "every 30 s" in p.state_lbl.text()
    now["works"] = True
    clock[0] = 1230.0
    p.server_tick(_Mem(), writable=True)                       # works
    assert p.state_lbl.text().startswith("\U0001F7E2 ON") and not p.wants_write()
    # The server restarts (a new process) and is not ready: quick tries again.
    now["works"], now["state"] = False, lc.State(lc.STOCK)
    clock[0] = 1300.0
    p.server_tick(_Mem2(), writable=True)
    assert "tries again every 5 s" in p.state_lbl.text()
    # A run of failures that has gone slow, then yet another new server: quick again.
    clock[0] = 1400.0
    p.server_tick(_Mem2(), writable=True)
    assert "every 30 s" in p.state_lbl.text()
    clock[0] = 1401.0
    p.server_tick(_Mem(), writable=True)
    assert "tries again every 5 s" in p.state_lbl.text()


def test_a_quick_retry_asks_for_a_poll_when_it_is_due(qtbot, tmp_path, monkeypatch):
    """The Weather tab polls every 5 s: without this a 5 s retry would just miss
    the next poll and really be 10 s."""
    polls = []
    p, now, clock, calls, logs = _failing_panel(qtbot, tmp_path, monkeypatch,
                                                poll_now=lambda: polls.append(clock[0]))
    p.server_tick(_Mem(), writable=True)
    assert p._retry_timer.isActive() and p._retry_timer.interval() == 5050
    p._retry_poke()                                            # too early: nothing
    assert polls == []
    clock[0] = 1005.0
    p._retry_poke()
    assert polls == [1005.0]
    p._retry_timer.stop()
    clock[0] = 1100.0                                          # slow retries: the normal poll does it
    p.server_tick(_Mem(), writable=True)
    assert "every 30 s" in p.state_lbl.text() and not p._retry_timer.isActive()


def test_the_page_gives_the_check_its_own_data_folder(qtbot, tmp_path, monkeypatch):
    """So an older helper this WolfRAT loaded before it was updated is not taken
    for another WolfRAT's just because a second WolfRAT is open on the PC."""
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": False},
                                      _other_version_on())
    asked = []
    monkeypatch.setattr(lc, "managed_elsewhere", lambda st, **kw: asked.append(kw) or False)
    p.server_tick(_Mem(), writable=True)
    assert asked and all(kw == {"own_dir": str(tmp_path)} for kw in asked)


# ------------------------------------- recommended settings, by level (2026-11-21)
# Dale, 2026-11-20: the release must put the right lag comp settings on every
# server, new or updating, even for admins who skip releases - and never undo a
# value an admin chose.  lc.SETTINGS_STEPS, saved per install as defaults_level.

def test_settings_steps_move_only_values_still_on_the_old_default():
    old = {"on": True, "extra_ms": 70, "max_ms": 400, "min_ping_ms": 80, "tune_mode": lc.TUNE_APPLY,
           "reapply": True, "auto_enabled_once": True}
    out, moved = lc.apply_settings_steps(old)
    assert (out["max_ms"], out["min_ping_ms"], out["tune_mode"]) == (300, 0, lc.TUNE_WATCH)
    assert out["extra_ms"] == 70 and out["on"] is True and out["defaults_level"] == lc.SETTINGS_LEVEL
    assert len(moved) == 3 and any("Minimum ping 80 ms -> 0 (everyone)" in m for m in moved)
    assert old["max_ms"] == 400                                  # the caller's dict is not changed

    chosen = dict(old, max_ms=500, min_ping_ms=120, tune_mode=lc.TUNE_OFF)
    out, moved = lc.apply_settings_steps(chosen)
    assert (out["max_ms"], out["min_ping_ms"], out["tune_mode"]) == (500, 120, lc.TUNE_OFF)
    assert moved == [] and out["defaults_level"] == lc.SETTINGS_LEVEL

    again, moved = lc.apply_settings_steps(dict(old, defaults_level=lc.SETTINGS_LEVEL))
    assert (again["max_ms"], again["min_ping_ms"], again["tune_mode"]) == (400, 80, lc.TUNE_APPLY)
    assert moved == []                                           # put back by the admin: stays

    new, moved = lc.apply_settings_steps({})
    assert (new["max_ms"], new["min_ping_ms"], new["tune_mode"]) == (300, 0, lc.TUNE_WATCH)
    assert moved == []                                           # a new install: nothing "moved"

    newer, moved = lc.apply_settings_steps(dict(old, defaults_level=lc.SETTINGS_LEVEL + 5))
    assert newer["defaults_level"] == lc.SETTINGS_LEVEL + 5 and moved == []


def test_a_new_install_starts_on_the_recommended_settings(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    s = p._settings
    assert (s.extra_ms, s.max_ms, s.min_ping_ms, s.tune_mode) == (70, 300, 0, lc.TUNE_WATCH)
    assert p.on_cb.isChecked() and p.reapply_cb.isChecked()
    assert _saved(tmp_path)["defaults_level"] == lc.SETTINGS_LEVEL


def test_an_updating_server_moves_once_and_writes_it_into_the_running_server(
        qtbot, tmp_path, monkeypatch):
    import json
    cfg = tmp_path / "lc.json"
    cfg.write_text(json.dumps({"on": True, "observe": False, "extra_ms": 70, "max_ms": 400,
                               "min_ping_ms": 80, "tune_mode": lc.TUNE_APPLY, "reapply": True,
                               "auto_enabled_once": True}), encoding="utf-8")
    running = lc.State(lc.ON, base=0x10000000, shared=0x10001000, ready=True,
                       settings=lc.Settings(False, 70, 400, 80, lc.TUNE_APPLY),
                       stats=lc.Stats(seconds=600), alive=True)
    state = {"now": running}
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: state["now"])
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: True)
    monkeypatch.setattr(lc, "stamp_owner", lambda memory, st: None)
    written = []

    def turn_on(pid, memory, data_dir, settings):
        written.append(settings)
        state["now"] = lc.State(lc.ON, base=0x10000000, shared=0x10001000, ready=True,
                                settings=settings, stats=lc.Stats(seconds=600), alive=True)
        return state["now"]
    monkeypatch.setattr(lc, "turn_on", turn_on)
    logs = []
    p = panel_mod.LagCompPanel(cfg, tmp_path / "lc_history.json", tmp_path, log=logs.append)
    qtbot.addWidget(p)
    assert (p.max_spin.value(), p.min_spin.value()) == (300, 0)
    assert p.tune_combo.currentData() == lc.TUNE_WATCH and p.extra_spin.value() == 70
    assert sum("Recommended settings applied once" in line for line in logs) == 1
    saved = _saved(tmp_path)
    assert (saved["max_ms"], saved["min_ping_ms"], saved["tune_mode"]) == (300, 0, lc.TUNE_WATCH)
    assert saved["defaults_level"] == lc.SETTINGS_LEVEL

    p.server_tick(_Mem(), writable=True)                 # the server still had 400 / 80 / apply
    assert written == [lc.Settings(False, 70, 300, 0, lc.TUNE_WATCH)]
    p.server_tick(_Mem(), writable=True)
    assert len(written) == 1                             # once

    # The admin puts 80 back: the next start leaves it alone and says nothing.
    p.min_spin.setValue(80)
    p._settings_settled()
    assert _saved(tmp_path)["min_ping_ms"] == 80
    logs2 = []
    p2 = panel_mod.LagCompPanel(cfg, tmp_path / "lc_history.json", tmp_path, log=logs2.append)
    qtbot.addWidget(p2)
    assert p2.min_spin.value() == 80 and p2.max_spin.value() == 300
    assert not any("Recommended settings" in line for line in logs2)


def test_values_an_admin_chose_are_not_moved_and_a_stock_server_is_not_switched_on(
        qtbot, tmp_path, monkeypatch):
    import json
    cfg = tmp_path / "lc.json"
    cfg.write_text(json.dumps({"on": True, "observe": False, "extra_ms": 55, "max_ms": 500,
                               "min_ping_ms": 80, "tune_mode": lc.TUNE_OFF, "reapply": False,
                               "auto_enabled_once": True}), encoding="utf-8")
    monkeypatch.setattr(lc, "turn_on", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no write")))
    logs = []
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: lc.State(lc.STOCK))
    p = panel_mod.LagCompPanel(cfg, tmp_path / "lc_history.json", tmp_path, log=logs.append)
    qtbot.addWidget(p)
    s = p._settings
    assert (s.extra_ms, s.max_ms, s.min_ping_ms, s.tune_mode) == (55, 500, 0, lc.TUNE_OFF)
    assert sum("Minimum ping 80 ms -> 0 (everyone)" in line for line in logs) == 1
    # Restarted server, "put it back" unticked: the level step must not switch it on.
    p.server_tick(_Mem(), writable=True)
    assert not p.on_cb.isChecked()


# ------------------------------------------- auto-tune per player (2026-11-23)
# tmp/autotune2/SPEC.md: each player's own Extra, learned by WolfRAT from the
# version 6 helper's tune rows and written back into the server.
import dataclasses  # noqa: E402
import struct  # noqa: E402

from wolfrat import player_tune as pt  # noqa: E402

EZ, A99, NEW, LOCKY = lc.name_hash("ez"), lc.name_hash("A-99"), lc.name_hash("Newbie"), \
    lc.name_hash("Locky")


def _tune_row(slot, name, ping, center=70, **kw):
    return lc.PlayerTuneRow(slot, name, ping, 0x100 + slot, center_ms=center, **kw)


def _v6_state(rows, pt_apply=0):
    state = _v5_state()
    state.settings = lc.Settings(False, 70, 300, 0)      # the helper's own auto-tune: Off
    s = state.stats
    s.has_v6, s.pt_apply, s.pt_spread_ms = True, pt_apply, 40
    s.players = [lc.PlayerStats(r.slot, r.name, r.ping, r.ping + 70, 900, 300, 250, 60, 10)
                 for r in rows if r.present]
    s.player_tune = list(rows)
    return state


def _four():
    return [_tune_row(3, "ez", 45), _tune_row(9, "A-99", 120), _tune_row(5, "Newbie", 80),
            _tune_row(7, "Locky", 60)]


def _learned(p):
    p._tuner.players["ez"] = pt.PlayerEntry("ez", offset=-20)
    p._tuner.players["A-99"] = pt.PlayerEntry("A-99", offset=40)
    p._tuner.players["Locky"] = pt.PlayerEntry("Locky", offset=20, locked=True)


def _own(p):
    t = p.players_table
    return {t.item(r, 0).text(): t.item(r, panel_mod.OWN_COLUMN).text() for r in range(t.rowCount())}


def _mode(p, mode):
    p._settings = dataclasses.replace(p._settings, tune_mode=mode)


def test_the_auto_tune_combo_and_help_say_it_is_per_player(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    items = [(p.tune_combo.itemText(i), p.tune_combo.itemData(i)) for i in range(p.tune_combo.count())]
    assert items == [
        ("Off - every player uses the Extra above", lc.TUNE_OFF),
        ("Watch - learn each player's best Extra and show it, change nothing", lc.TUNE_WATCH),
        ("On - each player gets their own learned Extra (within limits)", lc.TUNE_APPLY)]
    assert p.tune_combo.currentData() == lc.TUNE_WATCH                 # a new install watches
    assert "<b>Auto-tune per player</b> learns each player's own Extra" in panel_mod._HELP
    assert "at most once per 150 such shots" in panel_mod._HELP
    assert "2 ms a minute" not in panel_mod._HELP                     # the retired one
    # The combo does not size the column by its longest item (1024x768), and its
    # list still shows every item in full.
    longest = max(p.tune_combo.fontMetrics().horizontalAdvance(t) for t, _ in panel_mod.TUNE_ITEMS)
    assert p.tune_combo.minimumSizeHint().width() < longest
    assert p.tune_combo.sizeHint().width() < longest
    assert p.tune_combo.view().minimumWidth() > longest
    assert [p.tune_combo.itemData(i, panel_mod.Qt.ItemDataRole.ToolTipRole)
            for i in range(3)] == [t for t, _ in panel_mod.TUNE_ITEMS]


def test_settings_step_2_turns_the_old_on_into_watch_once(qtbot, tmp_path, monkeypatch):
    level1 = {"on": True, "extra_ms": 70, "max_ms": 300, "min_ping_ms": 0, "reapply": True,
              "auto_enabled_once": True, "defaults_level": 1}
    out, moved = lc.apply_settings_steps(dict(level1, tune_mode=lc.TUNE_APPLY))
    assert out["tune_mode"] == lc.TUNE_WATCH and out["defaults_level"] == 2 == lc.SETTINGS_LEVEL
    assert moved == ["Auto-tune per player On -> Watch"]
    for mode in (lc.TUNE_OFF, lc.TUNE_WATCH):                         # an admin's choice stays
        out, moved = lc.apply_settings_steps(dict(level1, tune_mode=mode))
        assert out["tune_mode"] == mode and moved == []
    out, moved = lc.apply_settings_steps(dict(level1, tune_mode=lc.TUNE_APPLY, defaults_level=2))
    assert out["tune_mode"] == lc.TUNE_APPLY and moved == []          # On again after it: stays
    # On the page: once, said once, saved.
    import json
    (tmp_path / "lc.json").write_text(json.dumps(dict(level1, tune_mode=lc.TUNE_APPLY)),
                                      encoding="utf-8")
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    logs = []
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json", tmp_path,
                               log=logs.append)
    qtbot.addWidget(p)
    assert p.tune_combo.currentData() == lc.TUNE_WATCH
    assert _saved(tmp_path)["tune_mode"] == lc.TUNE_WATCH and _saved(tmp_path)["defaults_level"] == 2
    assert [line for line in logs if "Recommended" in line] == [
        "Lag compensation: Recommended settings applied once: Auto-tune per player On -> Watch. "
        "Change them back on this page if you prefer."]


def test_the_own_extra_column(qtbot, tmp_path, monkeypatch):
    state = _v6_state(_four())
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    _learned(p)
    p._refresh_stats(force=True)
    assert _header(p.players_table) == ["Player", "Ping", "Rewind", "Own Extra", "Checks", "Hits",
                                        "Without", "Gain"]
    assert p.players_table.horizontalHeaderItem(panel_mod.OWN_COLUMN).toolTip() == \
        panel_mod.OWN_EXTRA_TIP
    assert _own(p) == {"ez": "+50 (watch)", "A-99": "+110 (watch)", "Newbie": "server",
                       "Locky": "locked"}
    row = next(r for r in range(4) if p.players_table.item(r, 0).text() == "ez")
    tip = p.players_table.item(row, panel_mod.OWN_COLUMN).toolTip()
    assert tip.startswith("ez: learned +50 (-20 on the server's +70).")
    assert "Since the last move: -40 disagreed with the current answer 0 times" in tip
    # On: a value the helper has not taken on yet says so.
    _mode(p, lc.TUNE_APPLY)
    p._refresh_stats(force=True)
    assert _own(p)["ez"] == "+50 (waiting)" and _own(p)["Newbie"] == "server (waiting)"
    ez = state.stats.player_tune[0]
    ez.want_hash, ez.want_extra_ms, ez.want_seq = EZ, 50, 2
    ez.use_extra_ms, ez.use_seq, ez.use_on, ez.center_ms = 50, 2, 1, 50
    p._refresh_stats(force=True)
    assert _own(p)["ez"] == "+50"
    _mode(p, lc.TUNE_OFF)
    p._refresh_stats(force=True)
    assert _own(p) == {"ez": "server", "A-99": "server", "Newbie": "server", "Locky": "locked"}


def test_an_older_helper_gives_everyone_the_server_value_and_says_why(qtbot, tmp_path, monkeypatch):
    state = _v6_state(_four())
    state.stats.has_v6 = False                                  # a version 5 helper
    state.stats.player_tune = []
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    _learned(p)
    p._refresh_stats(force=True)
    assert set(_own(p).values()) == {"server"}
    assert p.players_table.item(0, panel_mod.OWN_COLUMN).toolTip() == panel_mod.OLDER_HELPER_TIP
    assert "needs lag compensation helper version 6" in p.autotune_lbl.text()


def test_the_status_line(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v6_state(_four()))
    _learned(p)
    p._tuner.players["A-99"].top_seconds = 31 * 60
    p._tuner.players["Gone<b>"] = pt.PlayerEntry("Gone<b>", offset=-40)   # not on now
    p._refresh_stats(force=True)
    assert p.autotune_lbl.text().split("<br>") == [
        "Auto-tune per player (watch): 3 players differ from the server value: A-99 +110, "
        "ez +50, Gone&lt;b&gt; +30.",
        "At the top limit for 30+ min: A-99 +110.",
        "Locked to the server value: Locky."]
    _mode(p, lc.TUNE_OFF)
    p._refresh_stats(force=True)
    assert p.autotune_lbl.text() == "Auto-tune per player: off - every player uses the Extra above."


def test_the_right_click_menu_locks_learns_and_forgets(qtbot, tmp_path, monkeypatch):
    import json
    state = _v6_state(_four())
    monkeypatch.setattr(lc, "loaded_name", lambda data_dir: "wolfrat_lagcomp_test.dll")
    monkeypatch.setattr(lc, "server_state", lambda pid, name=None: state)
    logs, polls = [], []
    p = panel_mod.LagCompPanel(tmp_path / "lc.json", tmp_path / "lc_history.json", tmp_path,
                               log=logs.append, poll_now=lambda: polls.append(1))
    qtbot.addWidget(p)
    p._pid = 1234
    _learned(p)
    p._refresh_stats(force=True)

    def menu(name):
        return [(a.text(), a.isEnabled()) for a in p.player_menu(name).actions()]

    assert menu("ez") == [("Use the server value for ez", True), ("Let auto-tune learn ez", False),
                          ("Forget ez's learned Extra", True)]
    assert menu("Newbie") == [("Use the server value for Newbie", True),
                              ("Let auto-tune learn Newbie", False),
                              ("Forget Newbie's learned Extra", True)]   # seen: on the server value
    assert menu("Never")[2] == ("Forget Never's learned Extra", False)
    assert menu("A&B")[0][0] == "Use the server value for A&&B"     # not a shortcut key
    p.player_menu("ez").actions()[0].trigger()
    assert p._tuner.entry("ez").locked and _own(p)["ez"] == "locked"
    saved = json.loads((tmp_path / pt.FILE_NAME).read_text(encoding="utf-8"))
    assert saved["players"]["ez"]["locked"] is True                   # saved at once
    assert ("Lag compensation: Auto-tune: ez uses the server value now, set by the admin "
            "(learned -20 ms is kept).") in logs
    assert "ez uses the server value now" in p.event_log_edit.toPlainText()
    assert polls and p.wants_write()                                    # written on the poll it asks for
    assert menu("ez")[:2] == [("Use the server value for ez", False), ("Let auto-tune learn ez", True)]
    p.player_menu("ez").actions()[1].trigger()
    assert not p._tuner.entry("ez").locked and _own(p)["ez"] == "+50 (watch)"
    p.player_menu("ez").actions()[2].trigger()
    assert p._tuner.entry("ez") is None and _own(p)["ez"] == "server"
    assert any("forgot ez's learned Extra (-20 ms)" in line for line in logs)
    rows = [json.loads(line) for line in (tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [(r["kind"], r["action"]) for r in rows if r.get("action")] == [
        ("player_tune", "lock"), ("player_tune", "learn"), ("player_tune", "forget")]


def test_right_click_on_a_row_opens_the_menu_for_that_player(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, _v6_state(_four()))
    p.resize(1004, 700)
    p.show()
    p._refresh_stats(force=True)
    shown = []
    monkeypatch.setattr(panel_mod.QMenu, "exec",
                        lambda menu, *a: shown.append(menu.actions()[0].text()))
    t = p.players_table
    row = next(r for r in range(t.rowCount()) if t.item(r, 0).text() == "A-99")
    p._players_menu_requested(t.visualItemRect(t.item(row, 2)).center())
    assert shown == ["Use the server value for A-99"]
    p._players_menu_requested(t.viewport().rect().bottomRight())     # below the rows: nothing
    assert len(shown) == 1


def test_each_poll_writes_every_players_own_extra_into_the_server(qtbot, tmp_path, monkeypatch):
    rows = _four()
    rows[3].want_hash, rows[3].want_extra_ms, rows[3].want_seq = LOCKY, 90, 3   # locked since
    left = lc.PlayerTuneRow(20, "", 0, 0, want_hash=777, want_extra_ms=30, want_seq=9)
    state = _v6_state(rows + [left])
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": True, "reapply": True},
                                      state)
    _learned(p)
    mem = _Writes()
    p.server_tick(mem, writable=True)
    base = state.shared + 28980

    def slot(n, extra, h, seq):
        row = base + n * 88
        return [(row + 4, struct.pack("<i", extra)), (row, struct.pack("<I", h)),
                (row + 8, struct.pack("<I", seq))]

    assert mem.writes == (slot(3, 50, EZ, 1) + slot(5, 70, NEW, 1) + slot(7, 90, 0, 4)
                          + slot(9, 110, A99, 1) + slot(20, 30, 0, 10))
    assert calls == [] and not p.wants_write()
    p.server_tick(mem, writable=True)                                    # nothing new: no writes
    assert len(mem.writes) == 15
    _mode(p, lc.TUNE_APPLY)                                             # On: ptApply 1
    p.server_tick(mem, writable=True)
    assert mem.writes[15:] == [(state.shared + 28964, b"\x01\0\0\0")]
    _mode(p, lc.TUNE_OFF)                                               # Off: nobody has one
    p.server_tick(mem, writable=False)                                  # read-only: asks to write
    assert len(mem.writes) == 16 and p.wants_write()
    p.server_tick(mem, writable=True)
    assert mem.writes[16] == (state.shared + 28964, b"\0\0\0\0")
    assert sorted(a for a, d in mem.writes[17:] if (a - base) % 88 == 0) == [
        base + n * 88 for n in (3, 5, 9)]
    assert all(r.want_hash == 0 for r in state.stats.player_tune)
    assert not p.wants_write()


def test_a_move_goes_to_the_console_and_the_event_log_with_its_evidence(
        qtbot, tmp_path, monkeypatch):
    import json
    ez = _tune_row(3, "ez", 45)
    state = _v6_state([ez])
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": True, "reapply": True},
                                      state)
    p.server_tick(_Writes(), writable=True)                             # ez arrives
    ez.up_losses, ez.down_wins = 132, 18
    p.server_tick(_Writes(), writable=True)
    line = ("Auto-tune (watch): ez Extra +70 -> +50 - of 18 shots where -40 and the current "
            "answer disagreed, -40 hit 18")
    assert "Lag compensation: " + line in logs
    assert line in p.event_log_edit.toPlainText()
    rows = [json.loads(x) for x in (tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    move = next(r for r in rows if r["kind"] == "player_tune")
    assert (move["message"], move["name"], move["why"], move["from"], move["to"]) == (
        line, "ez", "down", 70, 50)
    assert (move["shots"], move["wins"], move["ping"], move["pid"]) == (18, 18, 45, 5460)
    assert p._tuner.entry("ez").offset == -20 and ez.want_extra_ms == 50       # written too
    # The history row keeps the tuner's state.
    p._last_record = 0
    state.stats.tests = 10
    p.server_tick(_Writes(), writable=True)
    hist = json.loads((tmp_path / "lc_history.json").read_text(encoding="utf-8"))[-1]
    assert hist["auto_tune"]["differ"] == {"ez": 50} and hist["auto_tune"]["mode"] == "watch"
    assert [m["to"] for m in hist["auto_tune"]["moves"]] == [50]
    assert hist["setting"] == "ping +70 ms (auto-tune per player: watch), none under 0, max 300"


def test_a_helper_still_running_the_old_server_wide_auto_tune_gets_it_switched_off(
        qtbot, tmp_path, monkeypatch):
    state = _v6_state([])
    s = state.stats
    s.has_tuner, s.tune_mode, s.tune_value_ms, s.tune_median_ms = True, lc.TUNE_APPLY, 65, 68
    p, now, calls, logs = _switchable(qtbot, tmp_path, monkeypatch, {"on": True, "reapply": True},
                                      state)
    resets = []
    monkeypatch.setattr(lc, "reset_stats", lambda pid, name=None: resets.append(pid) or True)

    def turn_on(pid, memory, data_dir, settings):
        calls.append("on")
        s.tune_mode = lc.TUNE_OFF                       # _write_settings writes it Off
        return state
    monkeypatch.setattr(lc, "turn_on", turn_on)
    p._pid = _Mem.pid
    p._refresh_stats(force=True)
    assert "Server-wide auto-tune (retired, still on in this helper), applied live: Extra " \
        "<b>+65 ms</b>" in p.autotune_lbl.text()
    p.server_tick(_Mem(), writable=False)               # no write access: nothing, no hold-up
    assert calls == []
    assert "ping +65 ms (server-wide auto from +70) (auto-tune per player: watch)" in \
        p.state_lbl.text()
    p.server_tick(_Writes(), writable=True)
    assert calls == ["on"] and resets == []             # the counters carry on
    p.server_tick(_Writes(), writable=True)
    assert calls == ["on"]
    p._refresh_stats(force=True)
    assert "Server-wide" not in p.autotune_lbl.text()


def test_closing_wolfrat_saves_what_was_learned(qtbot, tmp_path, monkeypatch):
    p = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    p._tuner.players["ez"] = pt.PlayerEntry("ez", offset=-20)
    p._tuner._dirty = True
    p.shutdown()
    import json
    saved = json.loads((tmp_path / pt.FILE_NAME).read_text(encoding="utf-8"))
    assert saved["players"]["ez"]["offset"] == -20
    # And the next start reads it back.
    q = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    assert q._tuner.entry("ez").offset == -20


def test_a_broken_tune_file_is_said_once_and_the_page_starts(qtbot, tmp_path, monkeypatch):
    (tmp_path / pt.FILE_NAME).write_text("{oops", encoding="utf-8")
    p = _panel(qtbot, tmp_path, monkeypatch, lc.State(lc.STOCK))
    assert p._tuner.players == {} and (tmp_path / (pt.FILE_NAME + ".bad")).exists()
    assert "could not be read" in p.event_log_edit.toPlainText()


def test_page_with_the_own_extra_column_still_fits_1024x768(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication, QGroupBox, QScrollArea, QTabWidget
    names = ["MutantEnder", "AngelExalted", "OscarMike247", "elk road", "A-99", "ez",
             "BadgerLove", "Somebody Long Name"]
    rows = [_tune_row(i, n, 40 + 20 * i) for i, n in enumerate(names)]
    state = _v6_state(rows)
    state.stats.what_if = [dataclasses.replace(r, steps=400 + i, hits=[40, 30, 35, 50, 70, 60, 45, 20])
                           for i, r in enumerate(state.stats.what_if)]
    p = _panel(qtbot, tmp_path, monkeypatch, state)
    for n, off in zip(names, (-40, -60, -20, 40, 40, -80)):
        p._tuner.players[n] = pt.PlayerEntry(n, offset=off, top_seconds=4000 if n == "A-99" else 0)
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    tabs.addTab(p, "Lag compensation")
    tabs.resize(1004, 540)
    tabs.show()
    p._refresh_stats(force=True)
    for _ in range(20):
        QApplication.processEvents()
    columns = [a for a in p.findChildren(QScrollArea) if a.widgetResizable()]
    assert len(columns) == 2
    for group in p.findChildren(QGroupBox):
        assert group.height() >= group.minimumSizeHint().height(), group.title()
    assert p.players_table.rowCount() == 8 and p.players_table.columnCount() == 8
    # The new column and the status line ask for no more width than the boxes
    # already in their columns.
    players = p.players_table.parent()
    right = [g.minimumSizeHint().width() for g in p.findChildren(QGroupBox)
             if g.parent() is players.parent() and g is not players]
    assert players.minimumSizeHint().width() <= max(right)
    session = p.autotune_lbl.parent()
    left = [g.minimumSizeHint().width() for g in p.findChildren(QGroupBox)
            if g.parent() is session.parent() and g is not session]
    assert session.minimumSizeHint().width() <= max(left)
    tabs.hide()
