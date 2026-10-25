"""Web Admin must remember being enabled. A failed/slow start at launch must never
untick the box or write web_enabled=false (2026-10-25: it used to, so Dale kept finding
Web Admin off when he went to use it, and the setting looked 'not saved')."""
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wolfrat.app import WebAdminTab


def _fake_tab(retries=0):
    """A stand-in with just the attributes the failure handler touches, so we can test
    the logic without a Qt event loop."""
    f = types.SimpleNamespace()
    f._web_autostart_retries = retries
    f._config = {"web_enabled": True, "web_port": 8070}
    f.enable_cb = mock.Mock()
    f._save_config = mock.Mock()
    f._update_status = mock.Mock()
    f.ws = mock.Mock()
    f.status_label = mock.Mock()
    return f


def test_failure_keeps_preference_and_tick():
    f = _fake_tab(retries=0)
    WebAdminTab._handle_web_start_failure(f, "boom", autostart=True)
    # Preference preserved: still enabled, nothing unticked, nothing re-saved off.
    assert f._config["web_enabled"] is True
    f.enable_cb.setChecked.assert_not_called()
    f._save_config.assert_not_called()
    # The user is shown the failure.
    f._update_status.assert_called_once()
    assert f._update_status.call_args.kwargs.get("error") == "boom"


def test_autostart_retries_before_giving_up():
    f = _fake_tab(retries=2)
    with mock.patch("wolfrat.app.QTimer") as qt:
        WebAdminTab._handle_web_start_failure(f, "not ready", autostart=True)
    # It scheduled a retry instead of surfacing a failure, and used up one retry.
    assert f._web_autostart_retries == 1
    qt.singleShot.assert_called_once()
    f._update_status.assert_not_called()


def test_manual_failure_also_keeps_preference():
    f = _fake_tab(retries=0)
    WebAdminTab._handle_web_start_failure(f, "port busy", autostart=False)
    assert f._config["web_enabled"] is True
    f.enable_cb.setChecked.assert_not_called()
    f._save_config.assert_not_called()
    f._update_status.assert_called_once()
