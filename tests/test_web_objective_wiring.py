"""The main window wires the score card, the sky and the time left (2026-11-24)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import web_timeleft  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402


def test_the_window_wires_the_bell_and_publishes_the_new_state(qapp, qtbot, tmp_path):
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    try:
        # A zone, a flag or the hill changing hands goes from the Bans tab's
        # poll to the page's bell.
        assert window.bans_tab.on_objective_event == window.web_server.add_event
        window.bans_tab.on_objective_event("zone", "Rebels took zone C (3 of 5)")
        newest = window.web_server._events.recent(1)[0]
        assert (newest["kind"], newest["text"]) == ("zone", "Rebels took zone C (3 of 5)")

        # The round length to put back is kept next to the other settings.
        assert window.phone_bridge._time._path == str(tmp_path.resolve() / web_timeleft.FILE_NAME)

        window.signals.players_signal.emit([])
        qtbot.waitUntil(lambda: window.web_server.desktop_state is not None, timeout=3000)
        top = window.web_server.desktop_state["top"]
        # Not connected, no game server on this PC: nothing to show, nothing offered.
        assert top["objective"] is None and top["sky"] is None and top["time_restore"] is None
        features = top["features"]
        assert features["sky"] is False and features["sky_scenes"] == []
        assert features["time_left"] is False
        state = window.web_server._build_state()
        for key in ("objective", "sky", "time_restore"):
            assert key in state and state[key] is None
        assert state["features"]["sky"] is False and state["features"]["time_left"] is False

        # The real Bans tab and Sky page answer what the bridge asks them.
        bans, page = window.bans_tab, window.weather_tab.sky_page
        assert bans.zones_now() == [] and bans.hill_now() is None
        assert bans.cap_goals_now() is None and bans.team_caps_now() is None
        assert len(page.phone_scenes()) >= 27 and page.phone_state() is None
        assert page.phone_show("toxic", None, "Web Admin")[0] is False
    finally:
        window.shutdown()
