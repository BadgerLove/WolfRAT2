"""The main window hands the phone page its bridge (2026-11-23)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat.runtime import DesktopRuntime  # noqa: E402


def test_the_window_wires_the_bridge_and_publishes_on_a_poll(qapp, qtbot, tmp_path):
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    try:
        assert window.web_server.on_desktop == window.phone_bridge.submit
        assert window.web_server.on_command is not None
        window.signals.players_signal.emit([])
        qtbot.waitUntil(lambda: window.web_server.desktop_state is not None, timeout=3000)
        top = window.web_server.desktop_state["top"]
        assert top["manual_mode"] in ("full", "manual", "undecided")
        assert top["features"]["bans"] is True and top["features"]["warn"] is True
        assert [item["id"] for item in top["features"]["ban_expiry"]] == [
            "", "1h", "1d", "7d", "30d", "90d"]
        assert top["lag_comp"]["tune_mode"] in ("off", "watch", "on")
        state = window.web_server._build_state()
        assert state["features"]["pairing"] is True
        assert state["lag_comp"] == top["lag_comp"]
    finally:
        window.shutdown()
