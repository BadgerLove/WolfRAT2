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
